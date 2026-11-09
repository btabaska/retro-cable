import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime

from retro_cable.catalog.models import PartOf, ShowOverride
from retro_cable.ingest.tmdb import EpisodeFacts, TmdbClient, fill_gaps
from tests.conftest import MakeCatalog
from tests.ingest.conftest import RecordedFetch

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def test_client_lists_seasons(tmdb_fetch: RecordedFetch) -> None:
    assert TmdbClient(tmdb_fetch).seasons(456) == [0, 10]


def test_client_parses_episodes(tmdb_fetch: RecordedFetch) -> None:
    episodes = TmdbClient(tmdb_fetch).season(456, 10)
    assert episodes[0] == EpisodeFacts(10, 1, "Lard of the Dance", date(1998, 8, 23), 1320)
    assert episodes[3].air_date is None  # TMDB sends "" for unknown dates


class FakeSource:
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


# --- the lineup's ids, and episodes numbered differently (Nov 9 2026) ---------------------

ARTHUR = {
    789: {
        2: [
            EpisodeFacts(2, 5, "D.W., the Picky Eater", date(1997, 10, 22), 1560),
            EpisodeFacts(2, 6, "Arthur's Underwear", date(1997, 10, 23), 1560),
            EpisodeFacts(2, 7, "Big Red Chicken", date(1997, 10, 24), 1560),
            EpisodeFacts(2, 8, "Lost!", date(1997, 10, 27), 1560),
        ]
    }
}


def seed_downloads(db: sqlite3.Connection) -> None:
    """A show known only from its downloaded files: TheTVDB's numbers and titles, no ids."""
    db.execute("INSERT INTO shows (id, title) VALUES ('arthur', 'Arthur')")
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, media_path) VALUES"
        " ('arthur', 2, 5, 'D.W. the Picky Eater and Buster and the Daredevils', 'a5.mp4'),"
        " ('arthur', 2, 6, 'Arthurs Underwear', 'a6.mp4'),"
        " ('arthur', 2, 7, 'Dora Saves the Game', 'a7.mp4'),"
        " ('arthur', 2, 8, '', 'a8.mp4')"
    )


def dates(db: sqlite3.Connection, show: str) -> dict[int, str | None]:
    return dict(
        db.execute("SELECT episode, air_date FROM episodes WHERE show_id = ?", (show,)).fetchall()
    )


def test_the_lineups_id_fills_a_show_with_no_id_of_its_own(db: sqlite3.Connection) -> None:
    seed_downloads(db)
    stats = fill_gaps(db, FakeSource(ARTHUR), now=NOW, lineup={"arthur": 789})
    assert dates(db, "arthur") == {
        5: "1997-10-22",  # a double-length file holds TMDB's first half: same episode
        6: "1997-10-23",  # punctuation aside, the same title
        7: None,  # another episode under this number on TMDB: left for a better source
        8: "1997-10-27",  # no title to check
    }
    assert stats.mismatched_titles == ["arthur S02E07"]
    assert stats.shows_without_tmdb_id == []
    # Its numbering may not be TMDB's, so TMDB adds no file-less episodes to it: a later
    # file under one of those numbers would take that episode's date.
    assert stats.added_episodes == 0
    assert db.execute("SELECT count(*) FROM episodes WHERE show_id = 'arthur'").fetchone()[0] == 4


def test_the_lineups_id_wins_over_the_one_plex_gave(db: sqlite3.Connection) -> None:
    seed(db)
    stats = fill_gaps(db, FakeSource(SIMPSONS | ARTHUR), now=NOW, lineup={"the-simpsons": 789})
    assert stats.shows == 1
    assert dates(db, "the-simpsons") == {1: "1998-08-23", 4: None}


def test_a_plex_row_with_another_title_keeps_its_empty_date(db: sqlite3.Connection) -> None:
    seed(db)
    db.execute(
        "UPDATE episodes SET title = 'Wild Barts Cant Be Broken' WHERE show_id = 'the-simpsons'"
        " AND episode = 4"
    )
    stats = fill_gaps(db, FakeSource(SIMPSONS), now=NOW)
    assert dates(db, "the-simpsons")[4] is None
    assert stats.mismatched_titles == ["the-simpsons S10E04"]
    assert stats.filled_runtimes == 0


def test_lineup_ids_come_from_the_effective_overrides(make_catalog: MakeCatalog) -> None:
    from retro_cable.catalog import load_catalog
    from retro_cable.ingest.tmdb import lineup_ids

    catalog = load_catalog(make_catalog())
    shows = [
        ShowOverride(show="arthur", tmdb_id="tv:789"),
        ShowOverride(show="a-movie", tmdb_id="movie:12"),  # not a series
        ShowOverride(
            show="horizons",
            tmdb_id="tv:220150",  # its episodes live under the series it is part of
            part_of=PartOf(show="pokemon", tvdb_id=76703, seasons=[20]),
        ),
    ]
    catalog = replace(catalog, overrides=catalog.overrides.model_copy(update={"shows": shows}))
    assert lineup_ids(catalog) == {"arthur": 789}
