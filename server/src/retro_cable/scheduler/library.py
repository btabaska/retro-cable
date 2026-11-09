"""What can be played: programs on disk with their runtimes, read from the catalog database."""

import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from statistics import median
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from retro_cable.catalog.parts import Part


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

    def split(self, parts: Mapping[tuple[str, int], "Part"]) -> "Library":
        """Seasons another show claims (catalog.parts) as that show, with its title and year;
        the series keeps the rest."""
        if not parts:
            return self
        episodes: dict[str, list[Program]] = defaultdict(list)
        years = dict(self.show_years)
        for show, eps in self.episodes.items():
            for p in eps:
                part = parts.get((show, p.season)) if p.season is not None else None
                if part is None:
                    episodes[show].append(p)
                    continue
                episodes[part.show].append(replace(p, show=part.show, title=part.title))
                if part.year is not None:
                    years[part.show] = part.year
        return Library.build(episodes, self.movies, self.bumpers, years)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _year_end(year: int | None) -> date | None:
    """A movie known only by its year premieres no earlier than Dec 31 of it (the safe side)."""
    return date(year, 12, 31) if year else None


# What the app can play. A normalized file (media_path, with its measured length) wins. A
# relative file_path is already under the media root (demo feeds, fixtures). An absolute
# file_path is a raw source as Plex or `ingest scan` saw it, so it waits for `normalize`.
_PLAY_PATH = (
    "COALESCE(media_path, CASE WHEN file_path NOT LIKE '/%' THEN file_path END) AS play_path"
)
# Its length, always the real one when it is known: the normalized file's ffprobe length,
# else the source's (from `ingest scan`), and only else the catalog runtime (Plex, TMDB, or
# the demo seed's runtime class).
PLAY_SECONDS = (
    "CASE WHEN media_path IS NOT NULL THEN media_seconds"
    " ELSE COALESCE(source_seconds, runtime_seconds) END"
)
# What a whole program should last: the catalog runtime or the source's measured length,
# whichever is longer. 0 when neither is known.
EXPECTED_SECONDS = "MAX(COALESCE(runtime_seconds, 0), COALESCE(source_seconds, 0))"
# A file shorter than half its expected runtime is a sample or a truncated encode (the M1
# review aired 2-minute samples as 22-minute episodes). It never reaches the schedule unless
# short media is allowed explicitly (RC_ALLOW_SHORT_MEDIA, for trials and demos).
SHORT_MEDIA_RATIO = 0.5
# With no runtime known, a truncated download measures whole against itself. An episode
# under a quarter of its show's median length is held back too, once the show has
# SHOW_MEDIAN_MIN episodes to judge by (Leverage Redemption S01E13 was a 62 s clip).
SHOW_MEDIAN_RATIO = 0.25
SHOW_MEDIAN_MIN = 5
_PLAYABLE = f"{_PLAY_PATH}, {PLAY_SECONDS} AS play_seconds, {EXPECTED_SECONDS} AS expected_seconds"
_BUMPER_PLAYABLE = (
    f"{_PLAY_PATH}, CASE WHEN media_path IS NOT NULL THEN media_seconds ELSE runtime_seconds END"
    " AS play_seconds"
)
_READY = " WHERE play_path IS NOT NULL AND play_seconds > 0"


def is_short(seconds: float, expected: float) -> bool:
    """True when a file of `seconds` is too short to air as a program expected to run
    `expected` seconds (0 or less: nothing to compare with)."""
    return expected > 0 and seconds < expected * SHORT_MEDIA_RATIO


def _ready(allow_short: bool) -> str:
    if allow_short:
        return _READY
    return _READY + f" AND play_seconds >= expected_seconds * {SHORT_MEDIA_RATIO}"


def _typical(programs: list[Program]) -> list[Program]:
    """A show's episodes without those far shorter than its median (see SHOW_MEDIAN_RATIO)."""
    if len(programs) < SHOW_MEDIAN_MIN:
        return programs
    floor = median(p.seconds for p in programs) * SHOW_MEDIAN_RATIO
    return [p for p in programs if p.seconds >= floor]


def load_library(db: sqlite3.Connection, *, allow_short: bool = False) -> Library:
    """Playable programs with a known runtime. Missing or raw files never reach the schedule,
    nor (unless `allow_short`) files shorter than half their expected runtime or than a
    quarter of their show's median episode."""
    ready = _ready(allow_short)
    episodes: dict[str, list[Program]] = defaultdict(list)
    for r in db.execute(
        "SELECT * FROM (SELECT e.show_id, s.title AS show_title, e.season, e.episode, e.title,"
        f" e.air_date, {_PLAYABLE} FROM episodes e JOIN shows s ON s.id = e.show_id)" + ready
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
    if not allow_short:
        episodes = defaultdict(list, {k: _typical(v) for k, v in episodes.items()})
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
            + ready
        )
    }
    bumpers: dict[str, list[Bumper]] = defaultdict(list)
    for r in db.execute(
        f"SELECT * FROM (SELECT channel_id, file_path, {_BUMPER_PLAYABLE} FROM bumpers)"
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
