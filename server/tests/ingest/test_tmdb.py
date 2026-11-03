import json
import sqlite3
from datetime import UTC, date, datetime

from retro_cable.ingest.tmdb import Candidate, EpisodeFacts, MovieFacts, TmdbClient, fill_gaps
from tests.ingest.conftest import RecordedFetch

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def test_client_lists_seasons(tmdb_fetch: RecordedFetch) -> None:
    assert TmdbClient(tmdb_fetch).seasons(456) == [0, 10]


def test_client_parses_episodes(tmdb_fetch: RecordedFetch) -> None:
    episodes = TmdbClient(tmdb_fetch).season(456, 10)
    assert episodes[0] == EpisodeFacts(10, 1, "Lard of the Dance", date(1998, 8, 23), 1320)
    assert episodes[3].air_date is None  # TMDB sends "" for unknown dates


class FakeSource:
    name = "tmdb"

    def __init__(
        self, data: dict[int, dict[int, list[EpisodeFacts]]], broken: frozenset[int] = frozenset()
    ) -> None:
        self.data = data
        self.broken = broken

    def seasons(self, tmdb_id: int) -> list[int]:
        if tmdb_id in self.broken:
            raise OSError("tmdb down")
        return sorted(self.data[tmdb_id])

    def season(self, tmdb_id: int, number: int) -> list[EpisodeFacts]:
        return self.data[tmdb_id][number]

    def search_tv(self, title: str) -> list[Candidate]:
        return []

    def search_movies(self, title: str) -> list[Candidate]:
        return []

    def movie(self, tmdb_id: int) -> MovieFacts:
        return MovieFacts(None, None)


def seed(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO shows (id, title, tmdb_id) VALUES ('the-simpsons', 'The Simpsons', 456)"
    )
    db.execute("INSERT INTO shows (id, title) VALUES ('no-ids', 'No Ids')")
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, air_date, air_date_source, runtime_seconds, file_path)"
        " VALUES ('the-simpsons', 10, 1, 'Lard of the Dance', '1998-08-23', 'plex', 1310, '/a.mkv'),"
        "        ('the-simpsons', 10, 4, '', NULL, NULL, NULL, '/b.mkv')"
    )


SIMPSONS = {
    456: {
        10: [
            EpisodeFacts(10, 1, "Lard of the Dance", date(1998, 8, 30), 1320),
            EpisodeFacts(10, 2, "The Wizard of Evergreen Terrace", date(1998, 9, 20), 1320),
            EpisodeFacts(10, 4, "Treehouse of Horror IX", date(1998, 10, 25), 1320),
        ]
    }
}


def rows(db: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(r)
        for r in db.execute(
            "SELECT episode, title, air_date, air_date_source, runtime_seconds, file_path"
            " FROM episodes ORDER BY season, episode"
        )
    ]


def test_fills_only_missing_fields_and_adds_episodes_not_on_disk(db: sqlite3.Connection) -> None:
    seed(db)
    stats = fill_gaps(db, FakeSource(SIMPSONS), now=NOW)
    assert rows(db) == [
        (1, "Lard of the Dance", "1998-08-23", "plex", 1310, "/a.mkv"),  # Plex values kept
        (
            2,
            "The Wizard of Evergreen Terrace",
            "1998-09-20",
            "tmdb",
            1320,
            None,
        ),  # known, not on disk
        (4, "Treehouse of Horror IX", "1998-10-25", "tmdb", 1320, "/b.mkv"),  # gaps filled
    ]
    assert (stats.shows, stats.added_episodes, stats.filled_air_dates, stats.filled_runtimes) == (
        1,
        1,
        1,
        1,
    )
    assert stats.shows_without_tmdb_id == ["no-ids"]


def test_is_idempotent(db: sqlite3.Connection) -> None:
    seed(db)
    fill_gaps(db, FakeSource(SIMPSONS), now=NOW)
    first = rows(db)
    stats = fill_gaps(db, FakeSource(SIMPSONS), now=NOW)
    assert rows(db) == first
    assert (stats.added_episodes, stats.filled_air_dates) == (0, 0)


def test_one_failing_show_does_not_stop_the_rest(db: sqlite3.Connection) -> None:
    seed(db)
    db.execute("INSERT INTO shows (id, title, tmdb_id) VALUES ('broken', 'Broken', 999)")
    stats = fill_gaps(db, FakeSource(SIMPSONS, broken=frozenset({999})), now=NOW)
    assert stats.failed == ["broken"]
    assert stats.added_episodes == 1


def test_records_the_run(db: sqlite3.Connection) -> None:
    seed(db)
    fill_gaps(db, FakeSource(SIMPSONS), now=NOW)
    source, stats = db.execute("SELECT source, stats FROM import_runs").fetchone()
    assert source == "tmdb"
    assert json.loads(stats)["added_episodes"] == 1


def test_client_sends_bearer_token_header() -> None:
    from retro_cable.ingest.tmdb import http_fetch

    seen: dict[str, str] = {}

    class Recorder:
        def __call__(self, base_url: str, headers: dict[str, str], timeout: float = 30) -> object:
            seen.update(headers, base=base_url)
            return lambda path, params=None: {}

    http_fetch("tok", make_fetch=Recorder())  # type: ignore[arg-type]
    assert seen == {"Authorization": "Bearer tok", "base": "https://api.themoviedb.org"}
