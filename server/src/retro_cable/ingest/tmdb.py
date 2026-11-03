"""Fill catalog gaps from TMDB: missing air dates, runtimes and titles, episodes not on disk,
and movie premiere dates.

TMDB is the app's only external metadata source at runtime, used by the worker. It is reached
directly (`TMDB_TOKEN`) or through Brandon's LAN Seerr, which proxies TMDB (`SEERR_URL` and
`SEERR_API_KEY`); `pick_source` chooses. Plex values always win; TMDB only fills what is empty.
"""

import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from retro_cable.db import transaction
from retro_cable.ingest.http import Fetch, json_fetch
from retro_cable.ingest.plex import normalize_title

TMDB_API = "https://api.themoviedb.org"

# What a show or movie fetch can raise: FetchError (an OSError) from the transport, or a
# response shaped differently from what the client expects.
_FAILURES = (OSError, ValueError, KeyError, TypeError)


@dataclass(frozen=True)
class EpisodeFacts:
    season: int
    episode: int
    title: str
    air_date: date | None
    runtime_seconds: int | None


@dataclass(frozen=True)
class MovieFacts:
    release_date: date | None
    runtime_seconds: int | None


@dataclass(frozen=True)
class Candidate:
    """One search hit: its TMDB id, title and first-air or release year."""

    id: int
    title: str
    year: int | None


class MetadataSource(Protocol):
    name: str

    def seasons(self, tmdb_id: int) -> list[int]: ...

    def season(self, tmdb_id: int, number: int) -> list[EpisodeFacts]: ...

    def search_tv(self, title: str) -> list[Candidate]: ...

    def search_movies(self, title: str) -> list[Candidate]: ...

    def movie(self, tmdb_id: int) -> MovieFacts: ...


def _date(value: object) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


def _year(value: object) -> int | None:
    d = _date(value)
    return d.year if d else None


def _minutes(value: object) -> int | None:
    return int(value) * 60 if isinstance(value, int | float) and value else None


class TmdbClient:
    """TMDB's v3 API, with a v4 read access token."""

    name = "tmdb"

    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch

    def seasons(self, tmdb_id: int) -> list[int]:
        show: dict[str, Any] = self._fetch(f"/3/tv/{tmdb_id}", None)
        return sorted(int(s["season_number"]) for s in show.get("seasons", []))

    def season(self, tmdb_id: int, number: int) -> list[EpisodeFacts]:
        data: dict[str, Any] = self._fetch(f"/3/tv/{tmdb_id}/season/{number}", None)
        return [
            EpisodeFacts(
                season=int(e.get("season_number", number)),
                episode=int(e["episode_number"]),
                title=e.get("name") or "",
                air_date=_date(e.get("air_date")),
                runtime_seconds=_minutes(e.get("runtime")),
            )
            for e in data.get("episodes", [])
        ]

    def search_tv(self, title: str) -> list[Candidate]:
        data: dict[str, Any] = self._fetch("/3/search/tv", {"query": title})
        return [
            Candidate(int(r["id"]), r.get("name") or "", _year(r.get("first_air_date")))
            for r in data.get("results", [])
        ]

    def search_movies(self, title: str) -> list[Candidate]:
        data: dict[str, Any] = self._fetch("/3/search/movie", {"query": title})
        return [
            Candidate(int(r["id"]), r.get("title") or "", _year(r.get("release_date")))
            for r in data.get("results", [])
        ]

    def movie(self, tmdb_id: int) -> MovieFacts:
        data: dict[str, Any] = self._fetch(f"/3/movie/{tmdb_id}", None)
        return MovieFacts(_date(data.get("release_date")), _minutes(data.get("runtime")))


class SeerrClient:
    """The same TMDB data through Seerr (v3; the Overseerr/Jellyseerr successor), which
    proxies and caches TMDB. Seerr camel-cases TMDB's fields and sends no per-episode
    runtime, so runtimes come only from Plex, the files or TMDB direct."""

    name = "seerr"

    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch

    def seasons(self, tmdb_id: int) -> list[int]:
        show: dict[str, Any] = self._fetch(f"/api/v1/tv/{tmdb_id}", None)
        return sorted(int(s["seasonNumber"]) for s in show.get("seasons", []))

    def season(self, tmdb_id: int, number: int) -> list[EpisodeFacts]:
        data: dict[str, Any] = self._fetch(f"/api/v1/tv/{tmdb_id}/season/{number}", None)
        return [
            EpisodeFacts(
                season=int(e.get("seasonNumber", number)),
                episode=int(e["episodeNumber"]),
                title=e.get("name") or "",
                air_date=_date(e.get("airDate")),
                runtime_seconds=None,
            )
            for e in data.get("episodes", [])
        ]

    def _search(self, title: str, media_type: str) -> list[dict[str, Any]]:
        # A multi search (tv, movie, person, collection); only the first page, by popularity.
        params = {"query": title, "page": "1", "language": "en"}
        data: dict[str, Any] = self._fetch("/api/v1/search", params)
        return [r for r in data.get("results", []) if r.get("mediaType") == media_type]

    def search_tv(self, title: str) -> list[Candidate]:
        return [
            Candidate(int(r["id"]), r.get("name") or "", _year(r.get("firstAirDate")))
            for r in self._search(title, "tv")
        ]

    def search_movies(self, title: str) -> list[Candidate]:
        return [
            Candidate(int(r["id"]), r.get("title") or "", _year(r.get("releaseDate")))
            for r in self._search(title, "movie")
        ]

    def movie(self, tmdb_id: int) -> MovieFacts:
        data: dict[str, Any] = self._fetch(f"/api/v1/movie/{tmdb_id}", None)
        return MovieFacts(_date(data.get("releaseDate")), _minutes(data.get("runtime")))


MakeFetch = Callable[[str, Mapping[str, str], float], Fetch]


def http_fetch(token: str, make_fetch: MakeFetch = json_fetch, timeout: float = 30) -> Fetch:
    """TMDB v4 read-access token, sent as a bearer header."""
    return make_fetch(TMDB_API, {"Authorization": f"Bearer {token}"}, timeout)


def seerr_http_fetch(
    url: str, api_key: str, make_fetch: MakeFetch = json_fetch, timeout: float = 30
) -> Fetch:
    """Seerr's API key, sent as its X-Api-Key header. `url` is Seerr's base URL; a trailing
    /api/v1 is accepted and dropped."""
    base = re.sub(r"/api/v1/?$", "", url.rstrip("/"))
    return make_fetch(base, {"X-Api-Key": api_key}, timeout)


@dataclass(frozen=True)
class SourceChoice:
    provider: str
    source: MetadataSource | None
    message: str


def pick_source(env: Mapping[str, str]) -> SourceChoice:
    """TMDB direct when TMDB_TOKEN is set, else Seerr when SEERR_URL and SEERR_API_KEY are
    both set, else nothing (with a message that names the variables, never their values)."""
    token = env.get("TMDB_TOKEN", "")
    url, key = env.get("SEERR_URL", ""), env.get("SEERR_API_KEY", "")
    if token:
        return SourceChoice("tmdb", TmdbClient(http_fetch(token)), "using TMDB (TMDB_TOKEN)")
    if url and key:
        source = SeerrClient(seerr_http_fetch(url, key))
        return SourceChoice("seerr", source, "using Seerr as a TMDB proxy (SEERR_URL)")
    return SourceChoice(
        "",
        None,
        "skipping the TMDB fill: set TMDB_TOKEN (a TMDB v4 read access token), or"
        " SEERR_URL and SEERR_API_KEY (a Seerr on the LAN)",
    )


def _without_year(title: str) -> str:
    return re.sub(r"\s*\(\d{4}\)\s*$", "", title)


def match(candidates: list[Candidate], title: str, year: int | None) -> int | None:
    """The TMDB id for a title: candidates whose title is the same ignoring case, accents,
    punctuation and a trailing (year). With a year, the hit from that year, else one a year
    either side (a pilot or a late listing), else none; without one, the most popular hit."""
    key = normalize_title(_without_year(title))
    same = [c for c in candidates if normalize_title(_without_year(c.title)) == key]
    if year is None:
        return same[0].id if same else None
    for c in same:
        if c.year == year:
            return c.id
    for c in same:
        if c.year is not None and abs(c.year - year) == 1:
            return c.id
    return None


@dataclass
class TmdbStats:
    provider: str = "tmdb"
    shows: int = 0
    added_episodes: int = 0
    filled_air_dates: int = 0
    filled_runtimes: int = 0
    filled_premieres: int = 0
    matched_by_search: list[str] = field(default_factory=list)
    shows_without_tmdb_id: list[str] = field(default_factory=list)
    movies_without_tmdb_id: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    failed_seasons: list[str] = field(default_factory=list)


def _fill_show(
    db: sqlite3.Connection, show_id: str, facts: list[EpisodeFacts], stats: TmdbStats
) -> None:
    with transaction(db):
        for f in facts:
            air = f.air_date.isoformat() if f.air_date else None
            row = db.execute(
                "SELECT title, air_date, runtime_seconds FROM episodes"
                " WHERE show_id = ? AND season = ? AND episode = ?",
                (show_id, f.season, f.episode),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO episodes (show_id, season, episode, title, air_date,"
                    " air_date_source, runtime_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (show_id, f.season, f.episode, f.title, air, "tmdb" if air else None,
                     f.runtime_seconds),
                )  # fmt: skip
                stats.added_episodes += 1
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


def _find_id(
    db: sqlite3.Connection,
    table: str,
    row: sqlite3.Row,
    search: Callable[[str], list[Candidate]],
    label: str,
    stats: TmdbStats,
) -> int | None:
    """A row's TMDB id: the stored one, else a search match, which is stored for next time."""
    if row["tmdb_id"] is not None:
        return int(row["tmdb_id"])
    found = match(search(_without_year(row["title"])), row["title"], row["year"])
    if found is not None:
        with transaction(db):
            db.execute(f"UPDATE {table} SET tmdb_id = ? WHERE id = ?", (found, row["id"]))
        stats.matched_by_search.append(label)
    return found


def _fill_shows(db: sqlite3.Connection, source: MetadataSource, stats: TmdbStats) -> None:
    for show in db.execute("SELECT id, title, year, tmdb_id FROM shows ORDER BY id").fetchall():
        try:
            tmdb_id = _find_id(db, "shows", show, source.search_tv, show["id"], stats)
            if tmdb_id is None:
                stats.shows_without_tmdb_id.append(show["id"])
                continue
            numbers = source.seasons(tmdb_id)
        except _FAILURES:
            stats.failed.append(show["id"])
            continue
        facts: list[EpisodeFacts] = []
        for n in numbers:
            try:  # a season TMDB lists but can't serve is skipped, not the whole show
                facts.extend(source.season(tmdb_id, n))
            except _FAILURES:
                stats.failed_seasons.append(f"{show['id']} S{n}")
        _fill_show(db, show["id"], facts, stats)
        stats.shows += 1


def _fill_movies(db: sqlite3.Connection, source: MetadataSource, stats: TmdbStats) -> None:
    rows = db.execute(
        "SELECT id, title, year, tmdb_id FROM movies"
        " WHERE premiere_date IS NULL OR runtime_seconds IS NULL ORDER BY id"
    ).fetchall()
    for movie in rows:
        label = f"movie:{movie['id']}"
        try:
            tmdb_id = _find_id(db, "movies", movie, source.search_movies, label, stats)
            if tmdb_id is None:
                stats.movies_without_tmdb_id.append(movie["id"])
                continue
            facts = source.movie(tmdb_id)
        except _FAILURES:
            stats.failed.append(label)
            continue
        premiere = facts.release_date.isoformat() if facts.release_date else None
        with transaction(db):
            filled = db.execute(
                "UPDATE movies SET premiere_date = ? WHERE id = ? AND premiere_date IS NULL"
                " AND ? IS NOT NULL",
                (premiere, movie["id"], premiere),
            ).rowcount
            db.execute(
                "UPDATE movies SET runtime_seconds = COALESCE(runtime_seconds, ?) WHERE id = ?",
                (facts.runtime_seconds, movie["id"]),
            )
        stats.filled_premieres += filled


def fill_gaps(db: sqlite3.Connection, source: MetadataSource, now: datetime) -> TmdbStats:
    """Fill every show and movie. One without a TMDB id is searched for by title and year
    first. A failing show or movie is recorded and skipped; so is a failing season."""
    stats = TmdbStats(provider=source.name)
    _fill_shows(db, source, stats)
    _fill_movies(db, source, stats)
    stamp = now.isoformat()
    db.execute(
        "INSERT INTO import_runs (source, started_at, finished_at, stats) VALUES (?, ?, ?, ?)",
        ("tmdb", stamp, stamp, json.dumps(asdict(stats))),
    )
    return stats
