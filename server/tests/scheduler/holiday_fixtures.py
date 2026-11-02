"""A catalog and library with holiday-tagged episodes, Disney movies and a seasonal specials show."""

from datetime import date, timedelta
from pathlib import Path

from retro_cable.catalog import Catalog, load_catalog
from retro_cable.scheduler import Bumper, Library, Program
from tests.conftest import MakeCatalog
from tests.scheduler.conftest import HALF, weekly

CHANNELS = """
timezone: America/New_York
channels:
  - id: ktvu
    name: KTVU (Fox)
    type: network
    identity:
      - { from: 1990-01-01, number: 2, call_sign: KTVU, network: FOX }
  - id: disney
    name: Disney Channel
    type: network
    identity:
      - { from: 1990-01-01, number: 30, call_sign: DSNY }
  - id: disney-movies
    name: Disney Movies
    type: movie
    library: true
    playlist: ordered
    identity:
      - { from: 1990-01-01, number: 31, call_sign: DMOV }
  - id: nick
    name: Nickelodeon
    type: network
    identity:
      - { from: 1990-01-01, number: 24, call_sign: NICK }
"""

HOLIDAYS = """
holidays:
  - id: halloween
    name: Halloween
    rule: { type: fixed, month: 10, day: 31 }
    window_days: 7
    strength: strong
    default_mode: "on"
    channels: { nick: "off" }
    tagged:
      - { title: "Under Wraps", kind: movie, channel: disney, premiere: 1997 }
  - id: christmas
    name: Christmas
    rule: { type: fixed, month: 12, day: 25 }
    window_days: 25
    strength: strong
    default_mode: "on"
  - id: st-patricks
    name: St. Patrick's Day
    rule: { type: fixed, month: 3, day: 17 }
    window_days: 3
    strength: medium
    default_mode: specials_only
    channels: { disney: "on", ktvu: "off" }
"""

CARRIAGE = """
carriage:
  - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 1990-01-01 }
  - { show: peanuts-holiday-specials, title: Peanuts holiday specials, channel: ktvu, from: 1990-01-01, weight: 0.5 }
  - { show: doug, title: Doug, channel: disney, from: 1990-01-01 }
  - { show: halloweentown, title: Halloweentown, channel: disney, from: 1998-01-01, kind: movie }
  - { show: under-wraps, title: Under Wraps, channel: disney, from: 1997-01-01, kind: movie }
  - { show: brink, title: Brink!, channel: disney, from: 1998-01-01, kind: movie }
  - { show: the-luck-of-the-irish, title: The Luck of the Irish, channel: disney, from: 2001-01-01, kind: movie }
  - { show: halloweentown, title: Halloweentown, channel: disney-movies, from: 1990-01-01, kind: movie }
  - { show: brink, title: Brink!, channel: disney-movies, from: 1990-01-01, kind: movie }
  - { show: the-simpsons, title: The Simpsons, channel: nick, from: 1990-01-01 }
  - { show: halloweentown, title: Halloweentown, channel: nick, from: 1998-01-01, kind: movie }
"""  # fmt: skip

OVERRIDES = """
shows:
  - { show: peanuts-holiday-specials, seasonal: true }
"""


def holiday_catalog(make_catalog: MakeCatalog, **replace: str) -> Catalog:
    files = {
        "channels": CHANNELS,
        "holidays": HOLIDAYS,
        "carriage": CARRIAGE,
        "overrides": OVERRIDES,
        "synthetic": "synthetic: []",
        "templates": "templates: []",
        **replace,
    }
    root: Path = make_catalog(**files)
    return load_catalog(root)


def _movie(show: str, title: str, premiere: date, minutes: int) -> Program:
    return Program(
        show=show,
        kind="movie",
        title=title,
        season=None,
        episode=None,
        episode_title="",
        air_date=premiere,
        seconds=minutes * 60,
        path=f"movies/{show}.mp4",
    )


def holiday_library() -> Library:
    simpsons = weekly("the-simpsons", "The Simpsons", date(1989, 12, 17), 300, HALF)
    # One Treehouse of Horror and one Christmas episode a season.
    for i, p in enumerate(simpsons):
        if p.episode == 5:
            simpsons[i] = Program(**{**p.__dict__, "episode_title": f"Treehouse of Horror {i}"})
        elif p.episode == 10:
            simpsons[i] = Program(**{**p.__dict__, "episode_title": f"Christmas Story {i}"})
    peanuts = [
        Program(
            show="peanuts-holiday-specials",
            kind="episode",
            title="Peanuts holiday specials",
            season=1,
            episode=n + 1,
            episode_title=title,
            air_date=aired,
            seconds=HALF,
            path=f"peanuts/{n}.mp4",
        )
        for n, (title, aired) in enumerate(
            [
                ("A Charlie Brown Christmas", date(1965, 12, 9)),
                ("It's the Great Pumpkin, Charlie Brown", date(1966, 10, 27)),
                ("Be My Valentine, Charlie Brown", date(1975, 1, 28)),
            ]
        )
    ]
    doug = weekly("doug", "Doug", date(1991, 8, 11), 100, HALF)
    movies = {
        "halloweentown": _movie("halloweentown", "Halloweentown", date(1998, 10, 17), 84),
        "under-wraps": _movie("under-wraps", "Under Wraps", date(1997, 10, 25), 90),
        "brink": _movie("brink", "Brink!", date(1998, 8, 29), 99),
        "the-luck-of-the-irish": _movie(
            "the-luck-of-the-irish", "The Luck of the Irish", date(2001, 3, 9), 86
        ),
    }
    bumpers = {
        c: (Bumper(channel=c, path=f"_bumpers/{c}/a.mp4", seconds=10),)
        for c in ("ktvu", "disney", "disney-movies", "nick")
    }
    return Library.build(
        {"the-simpsons": simpsons, "peanuts-holiday-specials": peanuts, "doug": doug},
        movies,
        bumpers,
    )


def days(start: date, count: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(count)]
