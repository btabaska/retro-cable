"""Fill catalog gaps from TMDB: missing air dates, runtimes and titles, and episodes not on disk.

This is the app's only external call at runtime, made by the worker. Plex values always win;
TMDB only fills what is empty.
"""

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from retro_cable.db import transaction
from retro_cable.ingest.http import Fetch, json_fetch

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


def fill_gaps(db: sqlite3.Connection, source: MetadataSource, now: datetime) -> TmdbStats:
    """Fill every show with a TMDB id. A failing show is recorded and skipped."""
    stats = TmdbStats()
    for show in db.execute("SELECT id, tmdb_id FROM shows ORDER BY id").fetchall():
        if show["tmdb_id"] is None:
            stats.shows_without_tmdb_id.append(show["id"])
            continue
        try:
            facts = [
                f
                for n in source.seasons(show["tmdb_id"])
                for f in source.season(show["tmdb_id"], n)
            ]
        except (OSError, ValueError, KeyError):
            stats.failed.append(show["id"])
            continue
        _fill_show(db, show["id"], facts, stats)
        stats.shows += 1
    stamp = now.isoformat()
    db.execute(
        "INSERT INTO import_runs (source, started_at, finished_at, stats) VALUES (?, ?, ?, ?)",
        ("tmdb", stamp, stamp, json.dumps(asdict(stats))),
    )
    return stats
