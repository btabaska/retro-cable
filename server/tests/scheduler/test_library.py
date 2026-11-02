"""load_library: what reaches the schedule, and from which file."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from retro_cable.catalog import Catalog
from retro_cable.db import connect, migrate
from retro_cable.scheduler import Scheduler, load_library


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "rc.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO shows (id, title, year) VALUES ('the-simpsons', 'The Simpsons', 1989)"
    )
    return conn


def add_episode(db: sqlite3.Connection, ep: int, file_path: str | None, **media: object) -> None:
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, air_date, runtime_seconds,"
        " file_path, media_path, media_seconds) VALUES ('the-simpsons', 1, ?, '', ?, 1320, ?, ?, ?)",
        (ep, media.get("air_date"), file_path, media.get("media_path"), media.get("seconds")),
    )


def test_normalized_files_win_with_their_measured_length(db: sqlite3.Connection) -> None:
    add_episode(db, 1, "/volume3/tv/The Simpsons/S01E01.mkv", media_path="tv/a.mp4", seconds=1381)
    add_episode(db, 2, "demo/half-hour.mp4")  # relative: already under the media root
    add_episode(db, 3, "/volume3/tv/The Simpsons/S01E03.mkv")  # raw source, not normalized
    add_episode(db, 4, None)  # known, not on disk
    eps = load_library(db).episodes["the-simpsons"]
    assert [(p.episode, p.path, p.seconds) for p in eps] == [
        (1, "tv/a.mp4", 1381),
        (2, "demo/half-hour.mp4", 1320),
    ]


def test_bumpers_and_movies_prefer_normalized_files(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO bumpers (channel_id, file_path, runtime_seconds, media_path, media_seconds)"
        " VALUES ('ktvu', '/src/_bumpers/ktvu/id.avi', 10, 'bumpers/ktvu/id.mp4', 9)"
    )
    db.execute(
        "INSERT INTO bumpers (channel_id, file_path, runtime_seconds)"
        " VALUES ('ktvu', '/src/_bumpers/ktvu/raw.avi', 10)"
    )
    db.execute(
        "INSERT INTO movies (id, title, year, runtime_seconds, file_path, media_path, media_seconds)"
        " VALUES ('halloweentown', 'Halloweentown', 1998, 5040, '/m/h.mkv', 'movies/h.mp4', 5003)"
    )
    lib = load_library(db)
    assert [(b.path, b.seconds) for b in lib.bumpers["ktvu"]] == [("bumpers/ktvu/id.mp4", 9)]
    movie = lib.movies["halloweentown"]
    assert (movie.path, movie.seconds) == ("movies/h.mp4", 5003)
    # Known only by its year: it premieres no earlier than Dec 31 of that year.
    assert movie.air_date == date(1998, 12, 31)


def test_scanned_episodes_without_dates_air_only_after_a_safe_synthesized_date(
    db: sqlite3.Connection, catalog: Catalog
) -> None:
    for ep in range(1, 23):
        add_episode(db, ep, f"tv/{ep}.mp4", media_path=f"tv/{ep}.mp4", seconds=1320)
    lib = load_library(db)
    assert lib.show_years == {"the-simpsons": 1989}
    sched = Scheduler(catalog, lib, seed=1)
    dates = [p.air_date for p in sched.programs("the-simpsons")]
    assert dates[0] == date(1989, 12, 31)
    assert all(d is not None and d >= date(1989, 12, 31) for d in dates)

    # Friday Jan 5 1990: only the first episode is eligible (the next is Jan 16).
    day = date(1990, 1, 5)
    aired = {s.episode for s in sched.day("ktvu", day) if s.show == "the-simpsons"}
    assert aired == {1}
