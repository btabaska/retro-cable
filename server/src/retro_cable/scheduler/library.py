"""What can be played: programs on disk with their runtimes, read from the catalog database."""

import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Literal


@dataclass(frozen=True)
class Program:
    """One playable episode or movie. `air_date` is the imported original date; the scheduler
    applies overrides.yaml and synthetic.yaml to it."""

    show: str  # catalog slug
    kind: Literal["episode", "movie"]
    title: str
    season: int | None
    episode: int | None
    episode_title: str
    air_date: date | None
    seconds: int
    path: str  # relative to the media root


@dataclass(frozen=True)
class Bumper:
    channel: str
    path: str
    seconds: int


@dataclass(frozen=True)
class Library:
    episodes: Mapping[str, tuple[Program, ...]] = field(default_factory=dict)
    movies: Mapping[str, Program] = field(default_factory=dict)
    bumpers: Mapping[str, tuple[Bumper, ...]] = field(default_factory=dict)

    @staticmethod
    def build(
        episodes: Mapping[str, list[Program] | tuple[Program, ...]],
        movies: Mapping[str, Program],
        bumpers: Mapping[str, tuple[Bumper, ...]],
    ) -> "Library":
        """Episodes are sorted into air order (season, episode)."""
        ordered = {
            show: tuple(sorted(eps, key=lambda p: (p.season or 0, p.episode or 0)))
            for show, eps in episodes.items()
        }
        return Library(
            episodes=MappingProxyType(ordered),
            movies=MappingProxyType(dict(movies)),
            bumpers=MappingProxyType(dict(bumpers)),
        )


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def load_library(db: sqlite3.Connection) -> Library:
    """Programs that are on disk with a known runtime. Missing files never reach the schedule."""
    episodes: dict[str, list[Program]] = defaultdict(list)
    for r in db.execute(
        "SELECT e.show_id, s.title AS show_title, e.season, e.episode, e.title, e.air_date,"
        " e.runtime_seconds, e.file_path FROM episodes e JOIN shows s ON s.id = e.show_id"
        " WHERE e.file_path IS NOT NULL AND e.runtime_seconds > 0"
    ):
        episodes[r["show_id"]].append(
            Program(
                show=r["show_id"],
                kind="episode",
                title=r["show_title"],
                season=r["season"],
                episode=r["episode"],
                episode_title=r["title"],
                air_date=_date(r["air_date"]),
                seconds=r["runtime_seconds"],
                path=r["file_path"],
            )
        )
    movies = {
        r["id"]: Program(
            show=r["id"],
            kind="movie",
            title=r["title"],
            season=None,
            episode=None,
            episode_title="",
            air_date=_date(r["premiere_date"]),
            seconds=r["runtime_seconds"],
            path=r["file_path"],
        )
        for r in db.execute(
            "SELECT id, title, premiere_date, runtime_seconds, file_path FROM movies"
            " WHERE file_path IS NOT NULL AND runtime_seconds > 0"
        )
    }
    bumpers: dict[str, list[Bumper]] = defaultdict(list)
    for r in db.execute(
        "SELECT channel_id, file_path, runtime_seconds FROM bumpers ORDER BY file_path"
    ):
        bumpers[r["channel_id"]].append(
            Bumper(channel=r["channel_id"], path=r["file_path"], seconds=r["runtime_seconds"])
        )
    return Library.build(episodes, movies, {k: tuple(v) for k, v in bumpers.items()})
