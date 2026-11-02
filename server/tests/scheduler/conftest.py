"""A small library of on-disk programs to schedule against the shared test catalog."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from retro_cable.catalog import Catalog, load_catalog
from retro_cable.scheduler import Bumper, Library, Program, Scheduler
from tests.conftest import MakeCatalog

HALF = 22 * 60
HOUR = 44 * 60


def weekly(show: str, title: str, first: date, count: int, seconds: int) -> list[Program]:
    return [
        Program(
            show=show,
            kind="episode",
            title=title,
            season=1 + i // 22,
            episode=1 + i % 22,
            episode_title=f"Episode {i + 1}",
            air_date=first + timedelta(weeks=i),
            seconds=seconds,
            path=f"{show}/{i}.mp4",
        )
        for i in range(count)
    ]


def make_library() -> Library:
    episodes = {
        "the-simpsons": weekly("the-simpsons", "The Simpsons", date(1989, 12, 17), 300, HALF),
        "x-men": weekly("x-men", "X-Men", date(1992, 10, 31), 76, HALF),
        "dexters-laboratory": weekly(
            "dexters-laboratory", "Dexter's Laboratory", date(1996, 4, 28), 78, HALF
        ),
        # Imported: original dates in 1989, shifted to the 1996 US debut by overrides.yaml.
        "dragon-ball-z": weekly("dragon-ball-z", "Dragon Ball Z", date(1989, 4, 26), 100, HALF),
        # Transplant: 2012 dates, shifted to a synthetic 2004 premiere.
        "gravity-falls": weekly("gravity-falls", "Gravity Falls", date(2012, 6, 15), 40, HALF),
    }
    movies = {
        "halloweentown": Program(
            show="halloweentown",
            kind="movie",
            title="Halloweentown",
            season=None,
            episode=None,
            episode_title="",
            air_date=date(1998, 10, 17),
            seconds=84 * 60,
            path="movies/halloweentown.mp4",
        )
    }
    bumpers = {
        "ktvu": (Bumper(channel="ktvu", path="_bumpers/ktvu/a.mp4", seconds=10),),
        "cartoon-network": (
            Bumper(channel="cartoon-network", path="_bumpers/cn/a.mp4", seconds=20),
        ),
    }
    return Library.build(episodes, movies, bumpers)


@pytest.fixture
def catalog(make_catalog: MakeCatalog) -> Catalog:
    root: Path = make_catalog()
    return load_catalog(root)


@pytest.fixture
def scheduler(catalog: Catalog) -> Scheduler:
    return Scheduler(catalog, make_library(), seed=7)
