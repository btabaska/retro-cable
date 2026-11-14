"""load_library: what reaches the schedule, and from which file."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from retro_cable.catalog import Catalog, load_catalog
from retro_cable.db import connect, migrate
from retro_cable.scheduler import Scheduler, load_library
from tests.conftest import MakeCatalog


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


def test_slots_last_the_measured_length_of_the_file(
    db: sqlite3.Connection, catalog: Catalog
) -> None:
    """Programs always air in full: the slot is the normalized file's measured length
    (media_seconds), not the catalog runtime, even when it lines up with no half hour."""
    for ep in range(1, 23):
        add_episode(
            db, ep, f"tv/{ep}.mp4", media_path=f"tv/{ep}.mp4", seconds=1381 + ep,
            air_date="1989-12-17",
        )  # fmt: skip
    lib = load_library(db)
    measured = {p.path: p.seconds for p in lib.episodes["the-simpsons"]}
    sched = Scheduler(catalog, lib, seed=1)
    slots = [s for s in sched.day("ktvu", date(1995, 3, 3)) if s.show == "the-simpsons"]
    assert slots
    for s in slots:
        assert s.path is not None and s.seconds == measured[s.path] != 1320


def test_a_sample_or_truncated_file_is_never_scheduled_as_an_episode(
    db: sqlite3.Connection,
) -> None:
    """M1 review: a 2-minute sample of a 22-minute episode aired as the episode. A program whose
    playable file is shorter than half its expected runtime (the catalog runtime, or the
    source's own measured length) stays off the schedule."""
    add_episode(db, 1, "/src/S01E01.mkv", media_path="tv/1.mp4", seconds=120)  # a sample
    add_episode(db, 2, "/src/S01E02.mkv", media_path="tv/2.mp4", seconds=1381)
    add_episode(db, 3, "/src/S01E03.mkv", media_path="tv/3.mp4", seconds=660)  # exactly half
    db.execute(
        "INSERT INTO movies (id, title, year, runtime_seconds, file_path, media_path, media_seconds)"
        " VALUES ('halloweentown', 'Halloweentown', 1998, 5040, '/m/h.mkv', 'movies/h.mp4', 300)"
    )
    lib = load_library(db)
    assert [p.episode for p in lib.episodes["the-simpsons"]] == [2, 3]
    assert "halloweentown" not in lib.movies


def test_an_encode_cut_short_of_its_source_is_never_scheduled(db: sqlite3.Connection) -> None:
    # No catalog runtime to compare with, but the source measured 1380 s and the encode 120 s.
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, file_path, source_seconds,"
        " media_path, media_seconds) VALUES ('the-simpsons', 1, 1, '', '/s.mkv', 1380,"
        " 'tv/1.mp4', 120)"
    )
    assert "the-simpsons" not in load_library(db).episodes


def test_a_clip_far_shorter_than_the_rest_of_its_show_is_never_scheduled(
    db: sqlite3.Connection,
) -> None:
    """With no runtime to compare with, a truncated download measures whole against itself
    (Leverage Redemption S01E13: 62 s). An episode under a quarter of its show's median
    length stays off, once the show has five episodes to take a median from."""
    db.execute("INSERT INTO shows (id, title) VALUES ('leverage', 'Leverage')")
    for ep, seconds in [(1, 2600), (2, 2580), (3, 2610), (4, 650), (5, 62), (6, 2590)]:
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, title, file_path, source_seconds,"
            " media_path, media_seconds) VALUES ('leverage', 1, ?, '', '/s.mkv', ?, ?, ?)",
            (ep, seconds, f"tv/{ep}.mp4", seconds),
        )
    # Just over a quarter of the median (2585 s) stays; the 62 s clip doesn't.
    assert [p.episode for p in load_library(db).episodes["leverage"]] == [1, 2, 3, 4, 6]
    assert len(load_library(db, allow_short=True).episodes["leverage"]) == 6
    # Four episodes are too few to judge by: a short one stays.
    db.execute("DELETE FROM episodes WHERE episode IN (1, 2)")
    assert [p.episode for p in load_library(db).episodes["leverage"]] == [3, 4, 5, 6]


def test_short_media_can_be_allowed_explicitly(db: sqlite3.Connection) -> None:
    # Trials (`normalize --max-seconds`) and demos may opt in: RC_ALLOW_SHORT_MEDIA=1.
    add_episode(db, 1, "/src/S01E01.mkv", media_path="tv/1.mp4", seconds=120)
    eps = load_library(db, allow_short=True).episodes["the-simpsons"]
    assert [(p.path, p.seconds) for p in eps] == [("tv/1.mp4", 120)]


def test_a_file_under_the_media_root_plays_at_its_measured_length(
    db: sqlite3.Connection,
) -> None:
    """Scheduling uses the real length: the normalized file's when there is one, else the
    source's ffprobe length from `ingest scan`, and only else the catalog runtime."""
    add_episode(db, 1, "demo/ep1.mp4")  # runtime 1320, never measured
    db.execute("UPDATE episodes SET source_seconds = 1402 WHERE episode = 1")
    add_episode(db, 2, "demo/ep2.mp4")
    add_episode(db, 3, "/src/S01E03.mkv", media_path="tv/3.mp4", seconds=1377)
    db.execute("UPDATE episodes SET source_seconds = 1379 WHERE episode = 3")
    eps = load_library(db).episodes["the-simpsons"]
    assert [(p.episode, p.seconds) for p in eps] == [(1, 1402), (2, 1320), (3, 1377)]


def test_seasons_another_show_claims_air_as_that_show(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """overrides.yaml `part_of`: TheTVDB files Justice League Unlimited as Justice League
    seasons 3-5, so its files sit under the series' id. The scheduler reads them as the other
    show, with its title and year; the series keeps its other seasons."""
    for season in (1, 2):
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, title, runtime_seconds, media_path,"
            " media_seconds) VALUES ('the-simpsons', ?, 1, '', 1320, ?, 1320)",
            (season, f"tv/s{season}.mp4"),
        )
    overrides = (
        "shows:\n"
        "  - { show: x-men, year: 1992, part_of: { show: the-simpsons, tvdb_id: 71663,"
        " seasons: [2] } }\n"
    )
    lib = Scheduler(load_catalog(make_catalog(overrides=overrides)), load_library(db)).library
    assert [(p.season, p.path) for p in lib.episodes["the-simpsons"]] == [(1, "tv/s1.mp4")]
    assert [(p.show, p.title, p.season, p.path) for p in lib.episodes["x-men"]] == [
        ("x-men", "X-Men", 2, "tv/s2.mp4")
    ]
    assert lib.show_years["x-men"] == 1992
