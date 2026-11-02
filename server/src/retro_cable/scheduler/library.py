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
    # A show's premiere year (from Plex, or the folder and file names for `ingest scan`). It
    # bounds the synthesized dates of undated episodes (catalog.air_dates.effective_dates).
    show_years: Mapping[str, int] = field(default_factory=dict)

    @staticmethod
    def build(
        episodes: Mapping[str, list[Program] | tuple[Program, ...]],
        movies: Mapping[str, Program],
        bumpers: Mapping[str, tuple[Bumper, ...]],
        show_years: Mapping[str, int] | None = None,
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
            show_years=MappingProxyType(dict(show_years or {})),
        )


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _year_end(year: int | None) -> date | None:
    """A movie known only by its year premieres no earlier than Dec 31 of it (the safe side)."""
    return date(year, 12, 31) if year else None


# What the app can play. A normalized file (media_path, with its measured length) wins. A
# relative file_path is already under the media root (demo feeds, fixtures). An absolute
# file_path is a raw source as Plex or `ingest scan` saw it, so it waits for `normalize`.
_PLAYABLE = (
    "COALESCE(media_path, CASE WHEN file_path NOT LIKE '/%' THEN file_path END) AS play_path,"
    " CASE WHEN media_path IS NOT NULL THEN media_seconds ELSE runtime_seconds END"
    " AS play_seconds"
)
_READY = " WHERE play_path IS NOT NULL AND play_seconds > 0"


def load_library(db: sqlite3.Connection) -> Library:
    """Playable programs with a known runtime. Missing or raw files never reach the schedule."""
    episodes: dict[str, list[Program]] = defaultdict(list)
    for r in db.execute(
        "SELECT * FROM (SELECT e.show_id, s.title AS show_title, e.season, e.episode, e.title,"
        f" e.air_date, {_PLAYABLE} FROM episodes e JOIN shows s ON s.id = e.show_id)" + _READY
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
                seconds=r["play_seconds"],
                path=r["play_path"],
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
            air_date=_date(r["premiere_date"]) or _year_end(r["year"]),
            seconds=r["play_seconds"],
            path=r["play_path"],
        )
        for r in db.execute(
            f"SELECT * FROM (SELECT id, title, premiere_date, year, {_PLAYABLE} FROM movies)"
            + _READY
        )
    }
    bumpers: dict[str, list[Bumper]] = defaultdict(list)
    for r in db.execute(
        f"SELECT * FROM (SELECT channel_id, file_path, {_PLAYABLE} FROM bumpers)"
        + _READY
        + " ORDER BY file_path"
    ):
        bumpers[r["channel_id"]].append(
            Bumper(channel=r["channel_id"], path=r["play_path"], seconds=r["play_seconds"])
        )
    years = {
        r["id"]: r["year"] for r in db.execute("SELECT id, year FROM shows WHERE year IS NOT NULL")
    }
    return Library.build(episodes, movies, {k: tuple(v) for k, v in bumpers.items()}, years)
