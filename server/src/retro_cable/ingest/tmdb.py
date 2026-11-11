"""Fill catalog gaps from TMDB: missing air dates, runtimes, titles and descriptions, episodes
not on disk, and the lineup's movies (description, release date, runtime).

This is the app's only external call at runtime, made by the worker. Plex values always win;
TMDB only fills what is empty.
"""

import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Protocol

from retro_cable.db import transaction
from retro_cable.ingest.http import Fetch, json_fetch

if TYPE_CHECKING:
    from retro_cable.catalog import Catalog

TMDB_API = "https://api.themoviedb.org"


@dataclass(frozen=True)
class EpisodeFacts:
    season: int
    episode: int
    title: str
    air_date: date | None
    runtime_seconds: int | None
    overview: str = ""


@dataclass(frozen=True)
class MovieFacts:
    title: str
    release_date: date | None
    runtime_seconds: int | None
    overview: str


class MetadataSource(Protocol):
    def seasons(self, tmdb_id: int) -> list[int]: ...

    def season(self, tmdb_id: int, number: int) -> list[EpisodeFacts]: ...


class TmdbClient:
    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch

    def seasons(self, tmdb_id: int) -> list[int]:
        show: dict[str, Any] = self._fetch(f"/3/tv/{tmdb_id}", None)
        return sorted(int(s["season_number"]) for s in show.get("seasons", []))

    def season(self, tmdb_id: int, number: int) -> list[EpisodeFacts]:
        data: dict[str, Any] = self._fetch(f"/3/tv/{tmdb_id}/season/{number}", None)
        out = []
        for e in data.get("episodes", []):
            air = e.get("air_date")
            runtime = e.get("runtime")
            out.append(
                EpisodeFacts(
                    season=int(e.get("season_number", number)),
                    episode=int(e["episode_number"]),
                    title=e.get("name") or "",
                    air_date=date.fromisoformat(air) if air else None,
                    runtime_seconds=int(runtime) * 60 if runtime else None,
                    overview=(e.get("overview") or "").strip(),
                )
            )
        return out

    def movie(self, tmdb_id: int) -> MovieFacts:
        data: dict[str, Any] = self._fetch(f"/3/movie/{tmdb_id}", None)
        released, runtime = data.get("release_date"), data.get("runtime")
        return MovieFacts(
            title=data.get("title") or "",
            release_date=date.fromisoformat(released) if released else None,
            runtime_seconds=int(runtime) * 60 if runtime else None,
            overview=(data.get("overview") or "").strip(),
        )


def http_fetch(
    token: str,
    make_fetch: Callable[[str, Mapping[str, str], float], Fetch] = json_fetch,
    timeout: float = 30,
) -> Fetch:
    """TMDB v4 read-access token, sent as a bearer header."""
    return make_fetch(TMDB_API, {"Authorization": f"Bearer {token}"}, timeout)


@dataclass
class TmdbStats:
    shows: int = 0
    added_episodes: int = 0
    filled_air_dates: int = 0
    filled_runtimes: int = 0
    filled_summaries: int = 0
    shows_without_tmdb_id: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # Episodes TMDB has another title for under the same number (`show SxxEyy`): its
    # numbering differs there, so their empty date and runtime are left alone.
    mismatched_titles: list[str] = field(default_factory=list)
    # Episodes dated from the TMDB episode with their exact title under another number.
    matched_by_title: int = 0


_WORDS = re.compile(r"[^a-z0-9]+")


def _words(title: str) -> str:
    return _WORDS.sub(" ", title.replace("'", "").lower()).strip()


def same_episode(ours: str, theirs: str) -> bool:
    """Whether two titles name one episode: alike, or ours starts with TMDB's (a file
    holding two segments, `Lucky Stone and The Mefirst Wizard` for TMDB's `Lucky Stone`).
    An empty title can't tell, so it counts as the same."""
    a, b = _words(ours), _words(theirs)
    if not a or not b:
        return True
    return a.startswith(b) or b.startswith(a) or SequenceMatcher(None, a, b).ratio() >= 0.6


_GENERIC = re.compile(r"(episode|ep|chapter|part) ?\d+|\d+")


def _title_key(title: str) -> str | None:
    """A title's exact identity (`3-D Struction` is `3D Struction`), or None for one that
    names no particular episode (`Episode 29`)."""
    words = _words(title)
    if not words or _GENERIC.fullmatch(words):
        return None
    return words.replace(" ", "")


def _by_title(facts: list[EpisodeFacts]) -> dict[str, EpisodeFacts]:
    """TMDB's episodes by exact title, for the titles TMDB uses once."""
    seen: dict[str, list[EpisodeFacts]] = {}
    for f in facts:
        if (key := _title_key(f.title)) is not None:
            seen.setdefault(key, []).append(f)
    return {key: found[0] for key, found in seen.items() if len(found) == 1}


def lineup_ids(catalog: "Catalog") -> dict[str, int]:
    """Show id -> TMDB series number from the lineup (`tmdb_id: tv:N` in the effective
    overrides). Shows that are seasons or episodes of another series (`part_of`,
    `episodes_of`) are left out: their episodes live under that series."""
    out: dict[str, int] = {}
    for o in catalog.overrides.shows:
        if o.part_of is not None or o.episodes_of is not None or not o.tmdb_id:
            continue
        kind, _, number = o.tmdb_id.partition(":")
        if kind == "tv" and number.isdigit():
            out[o.show] = int(number)
    return out


def _fill_show(
    db: sqlite3.Connection,
    show_id: str,
    facts: list[EpisodeFacts],
    stats: TmdbStats,
    add_missing: bool = True,
) -> None:
    rows = {
        (r["season"], r["episode"]): r
        for r in db.execute(
            "SELECT season, episode, title, air_date, runtime_seconds, summary FROM episodes"
            " WHERE show_id = ?",
            (show_id,),
        )
    }
    # A season where a titled episode sits under another number on TMDB is numbered
    # differently (Firefly's DVD order): its untitled episodes can't be matched either.
    off = {
        f.season
        for f in facts
        if (row := rows.get((f.season, f.episode))) is not None
        and row["title"]
        and not same_episode(row["title"], f.title)
    }
    numbered = {(f.season, f.episode): f for f in facts}
    titles = _by_title(facts)
    with transaction(db):
        moved = _fill_by_title(db, show_id, rows, numbered, titles, stats)
        for f in facts:
            air = f.air_date.isoformat() if f.air_date else None
            row = rows.get((f.season, f.episode))
            if row is None:
                if not add_missing:
                    continue
                db.execute(
                    "INSERT INTO episodes (show_id, season, episode, title, air_date,"
                    " air_date_source, runtime_seconds, summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (show_id, f.season, f.episode, f.title, air, "tmdb" if air else None,
                     f.runtime_seconds, f.overview or None),
                )  # fmt: skip
                stats.added_episodes += 1
                continue
            untitled_off = not row["title"] and f.season in off
            if untitled_off or not same_episode(row["title"] or "", f.title):
                if (f.season, f.episode) in moved:
                    continue
                if row["air_date"] is None or row["runtime_seconds"] is None:
                    stats.mismatched_titles.append(f"{show_id} S{f.season:02d}E{f.episode:02d}")
                continue
            if row["air_date"] is None and air:
                stats.filled_air_dates += 1
            if row["runtime_seconds"] is None and f.runtime_seconds:
                stats.filled_runtimes += 1
            if not row["summary"] and f.overview:
                stats.filled_summaries += 1
            db.execute(
                """UPDATE episodes SET
                     title = CASE WHEN title = '' THEN ? ELSE title END,
                     air_date_source = CASE WHEN air_date IS NULL AND ? IS NOT NULL
                       THEN 'tmdb' ELSE air_date_source END,
                     air_date = COALESCE(air_date, ?),
                     runtime_seconds = COALESCE(runtime_seconds, ?),
                     summary = COALESCE(NULLIF(summary, ''), ?)
                   WHERE show_id = ? AND season = ? AND episode = ?""",
                (f.title, air, air, f.runtime_seconds, f.overview or None, show_id, f.season,
                 f.episode),
            )  # fmt: skip


def _fill_by_title(
    db: sqlite3.Connection,
    show_id: str,
    rows: Mapping[tuple[int, int], sqlite3.Row],
    numbered: Mapping[tuple[int, int], EpisodeFacts],
    titles: Mapping[str, EpisodeFacts],
    stats: TmdbStats,
) -> set[tuple[int, int]]:
    """Date the titled episodes whose number holds another title on TMDB (or none) from
    the one TMDB episode with exactly their title: the files of some shows run in another
    order (The Muppet Show, What's New, Scooby-Doo?). Not where that TMDB episode's own
    number holds it too (a second file of one episode). Returns the episodes dated so."""
    moved: set[tuple[int, int]] = set()
    for key, row in rows.items():
        if row["air_date"] is not None and row["runtime_seconds"] is not None:
            continue
        here = numbered.get(key)
        if not row["title"] or (here is not None and same_episode(row["title"], here.title)):
            continue
        title = _title_key(row["title"])
        found = titles.get(title) if title is not None else None
        if found is None:
            continue
        there = rows.get((found.season, found.episode))
        if there is not None and there["title"] and _title_key(there["title"]) == title:
            continue
        air = found.air_date.isoformat() if found.air_date else None
        if row["air_date"] is None and air:
            stats.filled_air_dates += 1
        if row["runtime_seconds"] is None and found.runtime_seconds:
            stats.filled_runtimes += 1
        db.execute(
            """UPDATE episodes SET
                 air_date_source = CASE WHEN air_date IS NULL AND ? IS NOT NULL
                   THEN 'tmdb' ELSE air_date_source END,
                 air_date = COALESCE(air_date, ?),
                 runtime_seconds = COALESCE(runtime_seconds, ?)
               WHERE show_id = ? AND season = ? AND episode = ?""",
            (air, air, found.runtime_seconds, show_id, *key),
        )
        stats.matched_by_title += 1
        moved.add(key)
    return moved


def fill_gaps(
    db: sqlite3.Connection,
    source: MetadataSource,
    now: datetime,
    lineup: Mapping[str, int] | None = None,
) -> TmdbStats:
    """Fill every show with a TMDB id: the lineup's (`lineup_ids`) first, else the one Plex
    gave. A failing show is recorded and skipped. Episodes TMDB knows but the database
    doesn't are added (no file) only under an id from Plex, whose numbering is TMDB's;
    a show known from its files alone may number them TheTVDB's way."""
    stats = TmdbStats()
    lineup = lineup or {}
    for show in db.execute("SELECT id, tmdb_id FROM shows ORDER BY id").fetchall():
        tmdb_id = lineup.get(show["id"]) or show["tmdb_id"]
        if tmdb_id is None:
            stats.shows_without_tmdb_id.append(show["id"])
            continue
        try:
            facts = [f for n in source.seasons(tmdb_id) for f in source.season(tmdb_id, n)]
        except (OSError, ValueError, KeyError):
            stats.failed.append(show["id"])
            continue
        _fill_show(db, show["id"], facts, stats, add_missing=show["id"] not in lineup)
        stats.shows += 1
    stamp = now.isoformat()
    db.execute(
        "INSERT INTO import_runs (source, started_at, finished_at, stats) VALUES (?, ?, ?, ?)",
        ("tmdb", stamp, stamp, json.dumps(asdict(stats))),
    )
    return stats


def lineup_movie_ids(catalog: "Catalog") -> dict[str, int]:
    """Movie id -> TMDB movie number from the lineup (`tmdb_id: movie:N` in the effective
    overrides)."""
    out: dict[str, int] = {}
    for o in catalog.overrides.shows:
        kind, _, number = (o.tmdb_id or "").partition(":")
        if kind == "movie" and number.isdigit():
            out[o.show] = int(number)
    return out


class MovieSource(Protocol):
    def movie(self, tmdb_id: int) -> MovieFacts: ...


@dataclass
class TmdbMovieStats:
    movies: int = 0
    filled_premieres: int = 0
    filled_runtimes: int = 0
    filled_summaries: int = 0
    failed: list[str] = field(default_factory=list)


def fill_movies(
    db: sqlite3.Connection, source: MovieSource, now: datetime, lineup: Mapping[str, int]
) -> TmdbMovieStats:
    """Fill the lineup's movies from TMDB by their ids: an empty description, release date
    (the premiere date the scheduler keeps a movie to) or runtime. Only movies missing one
    are looked up, so a filled library costs no calls; values already there are kept."""
    stats = TmdbMovieStats()
    for m in db.execute(
        "SELECT id, premiere_date, runtime_seconds, summary FROM movies ORDER BY id"
    ).fetchall():
        tmdb_id = lineup.get(m["id"])
        if tmdb_id is None or (m["premiere_date"] and m["runtime_seconds"] and m["summary"]):
            continue
        try:
            facts = source.movie(tmdb_id)
        except (OSError, ValueError, KeyError):
            stats.failed.append(m["id"])
            continue
        released = facts.release_date.isoformat() if facts.release_date else None
        stats.filled_premieres += bool(not m["premiere_date"] and released)
        stats.filled_runtimes += bool(not m["runtime_seconds"] and facts.runtime_seconds)
        stats.filled_summaries += bool(not m["summary"] and facts.overview)
        db.execute(
            """UPDATE movies SET premiere_date = COALESCE(premiere_date, ?),
                 runtime_seconds = COALESCE(runtime_seconds, ?),
                 summary = COALESCE(NULLIF(summary, ''), ?)
               WHERE id = ?""",
            (released, facts.runtime_seconds, facts.overview or None, m["id"]),
        )
        stats.movies += 1
    stamp = now.isoformat()
    db.execute(
        "INSERT INTO import_runs (source, started_at, finished_at, stats) VALUES (?, ?, ?, ?)",
        ("tmdb-movies", stamp, stamp, json.dumps(asdict(stats))),
    )
    return stats
