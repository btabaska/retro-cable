import json
import sqlite3
from datetime import UTC, datetime

from retro_cable.catalog import load_catalog
from retro_cable.ingest.plex import PlexClient, import_plex
from tests.conftest import OVERRIDES, MakeCatalog
from tests.ingest.conftest import RecordedFetch

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def run(db: sqlite3.Connection, fetch: RecordedFetch, make_catalog: MakeCatalog, **files: str):  # type: ignore[no-untyped-def]
    return import_plex(db, PlexClient(fetch), load_catalog(make_catalog(**files)), now=NOW)


def rows(db: sqlite3.Connection, sql: str) -> list[tuple[object, ...]]:
    return [tuple(r) for r in db.execute(sql)]


def test_imports_carried_shows_by_title(
    db: sqlite3.Connection, plex_fetch: RecordedFetch, make_catalog: MakeCatalog
) -> None:
    run(db, plex_fetch, make_catalog)
    assert rows(db, "SELECT id, title, plex_rating_key, tmdb_id FROM shows ORDER BY id") == [
        ("dexters-laboratory", "Dexter\u2019s Laboratory", "102", 4229),
        ("dragon-ball-z", "Dragon Ball Z (1989)", "103", None),
        ("the-simpsons", "The Simpsons", "101", 456),
    ]


def test_imports_episodes_with_air_dates_and_files(
    db: sqlite3.Connection, plex_fetch: RecordedFetch, make_catalog: MakeCatalog
) -> None:
    run(db, plex_fetch, make_catalog)
    assert rows(
        db,
        "SELECT season, episode, title, air_date, air_date_source, runtime_seconds, file_path IS NOT NULL"
        " FROM episodes WHERE show_id = 'the-simpsons' ORDER BY season, episode",
    ) == [
        (0, 1, "Special Feature", None, None, 600, 1),
        (10, 1, "Lard of the Dance", "1998-08-23", "plex", 1310, 1),
        (10, 4, "Treehouse of Horror IX", "1998-10-25", "plex", 1320, 1),
    ]


def test_imports_carried_movies(
    db: sqlite3.Connection, plex_fetch: RecordedFetch, make_catalog: MakeCatalog
) -> None:
    run(db, plex_fetch, make_catalog)
    assert rows(db, "SELECT id, premiere_date, runtime_seconds, tmdb_id FROM movies") == [
        ("halloweentown", "1998-10-17", 5040, 16784)
    ]


def test_reports_what_it_could_not_match(
    db: sqlite3.Connection, plex_fetch: RecordedFetch, make_catalog: MakeCatalog
) -> None:
    stats = run(db, plex_fetch, make_catalog)
    assert stats.unmatched_shows == ["Uncarried Show (2015)"]
    assert stats.unmatched_movies == ["Uncarried Movie (2014)"]
    assert stats.shows == 3
    assert stats.episodes == 5
    assert stats.movies == 1
    assert stats.episodes_without_air_date == 1


def test_records_the_run(
    db: sqlite3.Connection, plex_fetch: RecordedFetch, make_catalog: MakeCatalog
) -> None:
    run(db, plex_fetch, make_catalog)
    source, started, finished, stats = db.execute(
        "SELECT source, started_at, finished_at, stats FROM import_runs"
    ).fetchone()
    assert (source, started, finished) == ("plex", NOW.isoformat(), NOW.isoformat())
    assert json.loads(stats)["episodes"] == 5


def test_excluded_shows_are_skipped(
    db: sqlite3.Connection, plex_fetch: RecordedFetch, make_catalog: MakeCatalog
) -> None:
    overrides = OVERRIDES + "  - show: dexters-laboratory\n    exclude: true\n"
    run(db, plex_fetch, make_catalog, overrides=overrides)
    assert ("dexters-laboratory",) not in rows(db, "SELECT id FROM shows")
    assert "/library/metadata/102/allLeaves" not in [p for p, _ in plex_fetch.calls]


def test_reimport_is_idempotent(
    db: sqlite3.Connection, plex_fetch: RecordedFetch, make_catalog: MakeCatalog
) -> None:
    run(db, plex_fetch, make_catalog)
    before = rows(db, "SELECT * FROM episodes ORDER BY id")
    run(db, plex_fetch, make_catalog)
    assert rows(db, "SELECT * FROM episodes ORDER BY id") == before


def test_a_file_removed_from_plex_is_marked_missing_but_the_episode_stays(
    db: sqlite3.Connection, plex_fetch: RecordedFetch, make_catalog: MakeCatalog
) -> None:
    run(db, plex_fetch, make_catalog)
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, air_date, air_date_source,"
        " plex_rating_key, file_path) VALUES ('the-simpsons', 10, 9, 'Gone', '1998-12-20', 'plex', '1999', '/x.mkv')"
    )
    run(db, plex_fetch, make_catalog)
    assert rows(
        db, "SELECT air_date, plex_rating_key, file_path FROM episodes WHERE episode = 9"
    ) == [("1998-12-20", None, None)]


def test_plex_never_erases_a_known_air_date(
    db: sqlite3.Connection, plex_fetch: RecordedFetch, make_catalog: MakeCatalog
) -> None:
    run(db, plex_fetch, make_catalog)
    db.execute(
        "UPDATE episodes SET air_date = '1999-01-01', air_date_source = 'tmdb' WHERE season = 0"
    )
    run(db, plex_fetch, make_catalog)
    assert rows(db, "SELECT air_date, air_date_source FROM episodes WHERE season = 0") == [
        ("1999-01-01", "tmdb")
    ]
