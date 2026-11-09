"""Fill catalog gaps from TMDB: missing air dates, runtimes and titles, and episodes not on disk.

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
                )
            )
        return out


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
    shows_without_tmdb_id: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # Episodes TMDB has another title for under the same number (`show SxxEyy`): its
    # numbering differs there, so their empty date and runtime are left alone.
    mismatched_titles: list[str] = field(default_factory=list)


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
            "SELECT season, episode, title, air_date, runtime_seconds FROM episodes"
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
    with transaction(db):
        for f in facts:
            air = f.air_date.isoformat() if f.air_date else None
            row = rows.get((f.season, f.episode))
            if row is None:
                if not add_missing:
                    continue
                db.execute(
                    "INSERT INTO episodes (show_id, season, episode, title, air_date,"
                    " air_date_source, runtime_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (show_id, f.season, f.episode, f.title, air, "tmdb" if air else None,
                     f.runtime_seconds),
                )  # fmt: skip
                stats.added_episodes += 1
                continue
            untitled_off = not row["title"] and f.season in off
            if untitled_off or not same_episode(row["title"] or "", f.title):
                if row["air_date"] is None or row["runtime_seconds"] is None:
                    stats.mismatched_titles.append(f"{show_id} S{f.season:02d}E{f.episode:02d}")
                continue
            if row["air_date"] is None and air:
                stats.filled_air_dates += 1
            if row["runtime_seconds"] is None and f.runtime_seconds:
                stats.filled_runtimes += 1
            db.execute(
                """UPDATE episodes SET
                     title = CASE WHEN title = '' THEN ? ELSE title END,
                     air_date_source = CASE WHEN air_date IS NULL AND ? IS NOT NULL
                       THEN 'tmdb' ELSE air_date_source END,
                     air_date = COALESCE(air_date, ?),
                     runtime_seconds = COALESCE(runtime_seconds, ?)
                   WHERE show_id = ? AND season = ? AND episode = ?""",
                (f.title, air, air, f.runtime_seconds, show_id, f.season, f.episode),
            )


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
