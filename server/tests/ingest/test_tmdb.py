import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime

from retro_cable.catalog.models import PartOf, ShowOverride
from retro_cable.ingest.tmdb import (
    EpisodeFacts,
    MovieFacts,
    TmdbClient,
    fill_gaps,
    fill_movies,
    lineup_movie_ids,
)
from tests.conftest import MakeCatalog
from tests.ingest.conftest import RecordedFetch

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def test_client_lists_seasons(tmdb_fetch: RecordedFetch) -> None:
    assert TmdbClient(tmdb_fetch).seasons(456) == [0, 10]


def test_client_parses_episodes(tmdb_fetch: RecordedFetch) -> None:
    episodes = TmdbClient(tmdb_fetch).season(456, 10)
    assert episodes[0] == EpisodeFacts(
        10, 1, "Lard of the Dance", date(1998, 8, 23), 1320,
        "Lisa worries about growing up when a new girl arrives at school.",
    )  # fmt: skip
    assert episodes[1].overview == ""  # none on TMDB
    assert episodes[3].air_date is None  # TMDB sends "" for unknown dates


def test_client_reads_a_movie(tmdb_fetch: RecordedFetch) -> None:
    assert TmdbClient(tmdb_fetch).movie(11508) == MovieFacts(
        title="Addams Family Reunion", release_date=date(1998, 9, 22), runtime_seconds=88 * 60,
        overview="The Addams family go to a reunion that turns out to be for another family.",
    )  # fmt: skip


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
        8: None,  # no title to check, in a season where one number is off
    }
    assert stats.mismatched_titles == ["arthur S02E07", "arthur S02E08"]
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


def test_untitled_episodes_are_left_alone_in_a_season_numbered_differently(
    db: sqlite3.Connection,
) -> None:
    """Firefly on the NAS is in DVD order, TMDB in broadcast order, and some files have no
    title. One titled episode out of place marks the season, so the untitled ones beside it
    get neither TMDB's title nor its date: file 1 is the pilot, not TMDB's The Train Job."""
    db.execute("INSERT INTO shows (id, title) VALUES ('firefly', 'Firefly')")
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, media_path) VALUES"
        " ('firefly', 1, 1, '', 'f1.mp4'), ('firefly', 1, 2, 'The Train Job', 'f2.mp4'),"
        " ('firefly', 2, 1, '', 'g1.mp4')"
    )
    firefly = {
        5: {
            1: [
                EpisodeFacts(1, 1, "The Train Job", date(2002, 9, 20), 2580),
                EpisodeFacts(1, 2, "Bushwhacked", date(2002, 9, 27), 2580),
            ],
            2: [EpisodeFacts(2, 1, "Another Season", date(2003, 9, 1), 2580)],
        }
    }
    stats = fill_gaps(db, FakeSource(firefly), now=NOW, lineup={"firefly": 5})
    got = db.execute(
        "SELECT season, episode, title, air_date, runtime_seconds FROM episodes"
        " ORDER BY season, episode"
    ).fetchall()
    assert [tuple(r) for r in got] == [
        (1, 1, "", None, None),
        (1, 2, "The Train Job", "2002-09-20", 2580),  # its title finds TMDB's E1
        (2, 1, "Another Season", "2003-09-01", 2580),  # nothing out of place in season 2
    ]
    assert stats.mismatched_titles == ["firefly S01E01"]


def test_a_title_under_another_number_takes_that_episodes_date(db: sqlite3.Connection) -> None:
    """The Muppet Show's files run in another order than TMDB's: the guest named in the
    title finds its episode wherever TMDB numbers it. Only an exact title, found once; a
    generic `Episode 29` or a title TMDB has twice tells nothing."""
    db.execute("INSERT INTO shows (id, title) VALUES ('muppets', 'The Muppet Show')")
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, media_path) VALUES"
        " ('muppets', 1, 1, 'Sandy Duncan', 'm1.mp4'),"
        " ('muppets', 1, 2, 'Joel Grey', 'm2.mp4'),"
        " ('muppets', 1, 3, '3D Struction', 'm3.mp4'),"  # punctuation aside
        " ('muppets', 1, 4, 'Twice Told', 'm4.mp4'),"
        " ('muppets', 2, 29, 'Episode 29', 'm5.mp4'),"
        " ('muppets', 2, 30, 'Not on TMDB', 'm6.mp4')"
    )
    muppets = {
        7: {
            1: [
                EpisodeFacts(1, 1, "Joel Grey", date(1976, 9, 13), 1500),
                EpisodeFacts(1, 2, "3-D Struction", date(1976, 9, 20), 1500),
                EpisodeFacts(1, 3, "Twice Told", date(1976, 9, 27), 1500),
                EpisodeFacts(1, 4, "Sandy Duncan", date(1976, 10, 4), None),
                EpisodeFacts(1, 5, "Twice Told", date(1976, 10, 11), 1500),
            ],
            2: [EpisodeFacts(2, 1, "Episode 29", date(1977, 9, 1), 1500)],
        }
    }
    stats = fill_gaps(db, FakeSource(muppets), now=NOW, lineup={"muppets": 7})
    got = db.execute(
        "SELECT episode, air_date, air_date_source, runtime_seconds FROM episodes"
        " ORDER BY season, episode"
    ).fetchall()
    assert [tuple(r) for r in got] == [
        (1, "1976-10-04", "tmdb", None),  # Sandy Duncan is TMDB's E4
        (2, "1976-09-13", "tmdb", 1500),  # Joel Grey is TMDB's E1
        (3, "1976-09-20", "tmdb", 1500),
        (4, None, None, None),  # TMDB has two Twice Told: can't tell which
        (29, None, None, None),  # a generic title names no episode
        (30, None, None, None),
    ]
    assert stats.matched_by_title == 3
    assert stats.filled_air_dates == 3
    assert stats.mismatched_titles == ["muppets S01E04"]  # no TMDB S02E29 to disagree with


# --- descriptions (Nov 11 2026) ---------------------------------------------------------------


def test_episode_descriptions_fill_only_empty_summaries(db: sqlite3.Connection) -> None:
    seed(db)
    db.execute("UPDATE episodes SET summary = 'From Plex.' WHERE episode = 1")
    described = {
        456: {
            10: [
                replace(SIMPSONS[456][10][0], overview="TMDB's words."),
                replace(SIMPSONS[456][10][1], overview="Homer dreams big."),
                replace(SIMPSONS[456][10][2], overview="Three scary tales."),
            ]
        }
    }
    stats = fill_gaps(db, FakeSource(described), now=NOW)
    got = dict(db.execute("SELECT episode, summary FROM episodes").fetchall())
    assert got == {1: "From Plex.", 2: "Homer dreams big.", 4: "Three scary tales."}
    assert stats.filled_summaries == 1  # episode 4; episode 2 was added with its description


class FakeMovies:
    def __init__(self, data: dict[int, MovieFacts], broken: frozenset[int] = frozenset()) -> None:
        self.data, self.broken = data, broken
        self.calls: list[int] = []

    def movie(self, tmdb_id: int) -> MovieFacts:
        self.calls.append(tmdb_id)
        if tmdb_id in self.broken:
            raise OSError("tmdb down")
        return self.data[tmdb_id]


REUNION = MovieFacts("Addams Family Reunion", date(1998, 9, 22), 5280, "A family reunion.")


def test_lineup_movies_get_a_description_a_release_date_and_a_runtime(
    db: sqlite3.Connection,
) -> None:
    """TMDB filled shows only, so a movie whose folder had no year stayed undated and aired in
    any year. A lineup movie missing any of these is looked up once by its TMDB id."""
    db.execute(
        "INSERT INTO movies (id, title, year, premiere_date, runtime_seconds, summary) VALUES"
        " ('addams-family-reunion', 'Addams Family Reunion', 1998, NULL, NULL, NULL),"
        " ('halloweentown', 'Halloweentown', 1998, '1998-10-17', 5040, 'From Plex.'),"
        " ('broken', 'Broken', NULL, NULL, NULL, NULL),"
        " ('no-id', 'No Id', NULL, NULL, NULL, NULL)"
    )
    source = FakeMovies({11508: REUNION, 1: REUNION}, broken=frozenset({999}))
    lineup = {"addams-family-reunion": 11508, "halloweentown": 1, "broken": 999}
    stats = fill_movies(db, source, NOW, lineup)
    assert source.calls == [11508, 999]  # Halloweentown lacks nothing
    got = db.execute(
        "SELECT premiere_date, runtime_seconds, summary FROM movies"
        " WHERE id = 'addams-family-reunion'"
    ).fetchone()
    assert tuple(got) == ("1998-09-22", 5280, "A family reunion.")
    assert (stats.movies, stats.filled_premieres, stats.filled_summaries) == (1, 1, 1)
    assert stats.failed == ["broken"]
    source.calls.clear()
    fill_movies(db, source, NOW, lineup)
    assert source.calls == [999]  # only the one still missing everything


def test_lineup_movie_ids_come_from_the_effective_overrides(make_catalog: MakeCatalog) -> None:
    from retro_cable.catalog import load_catalog

    overrides = (
        "shows:\n"
        "  - { show: addams-family-reunion, tmdb_id: 'movie:11508', year: 1998 }\n"
        "  - { show: the-simpsons, tmdb_id: 'tv:456' }\n"
    )
    catalog = load_catalog(make_catalog(overrides=overrides))
    assert lineup_movie_ids(catalog) == {"addams-family-reunion": 11508}
