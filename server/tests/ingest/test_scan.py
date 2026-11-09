"""`ingest scan`: read a Plex-named TV tree straight off the disk (no Plex token needed)."""

import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from retro_cable.catalog import load_catalog
from retro_cable.ingest.scan import (
    movie_title,
    parse_episode,
    scan_movies,
    scan_tv,
    show_title,
    show_year,
)
from tests.conftest import CARRIAGE, MakeCatalog


@pytest.mark.parametrize(
    ("folder", "name", "expected"),
    [
        ("Season 01", "The Simpsons (1989) - S01E02 - Bart the Genius [DSNP][WEBDL-1080p].mkv",
         (1, 2, "Bart the Genius")),
        ("Season 01", "Courage the Cowardly Dog (1999) - S01E01-E02 - A Night at the Katz Motel"
         " and Cajun Granny Stew [HMAX][WEBDL-1080p][AC3 2.0][h264]-ViSiON.mkv",
         (1, 1, "A Night at the Katz Motel and Cajun Granny Stew")),
        ("Season 7", "Futurama.S07E01.The.Bots.and.the.Bees.WEB-DL.x264.AAC.mp4",
         (7, 1, "The Bots and the Bees")),
        ("Season 1", "The OC.S1E11The Homecoming.mkv", (1, 11, "The Homecoming")),
        ("Teen Titans", "Teen Titans - 1x03 - #03 - Divide and Conquer - s1e3.avi",
         (1, 3, "#03 - Divide and Conquer")),
        ("Season 2", "203 - My Case Study.avi", (2, 3, "My Case Study")),
        ("Season 1", "01. On Leather Wings.xvid", (1, 1, "On Leather Wings")),
        # A release tag or a stray space after the season number (Young Justice, Adventure Time)
        ("Season 1.AAC", "01 Independence Day (HD).m4v", (1, 1, "Independence Day (HD)")),
        ("Season 2 ", "203 - My Case Study.avi", (2, 3, "My Case Study")),
        ("Season 2 [DVDRip]", "05 - Pilot.avi", (2, 5, "Pilot")),
        # `S.EE` and spelled-out numbers, in any folder (Buffy, Scooby-Doo, Always Sunny)
        ("Buffy the Vampire Slayer", "1.01 Welcome to the Hellmouth.avi",
         (1, 1, "Welcome to the Hellmouth")),
        ("BtVS Season 5", "5.1 Buffy vs. Dracula.avi", (5, 1, "Buffy vs. Dracula")),
        ("Season 1", "14.5. Heart of Ice Commentary.xvid", None),  # not 14x5
        ("Scooby Doo Where Are You Season 1", "S1Episode 1 What a Night for a Knight.avi",
         (1, 1, "What a Night for a Knight")),
        ("Season 8", "Its Always Sunny in Philadelphia Season 08 Episode 01 - Pop-Pop.mkv",
         (8, 1, "Pop-Pop")),
        ("Season 1", "Community Season 1 Episode 12 \u2013 Comparative Religion.mp4",
         (1, 12, "Comparative Religion")),
        ("Extras", "00.00 - Adventure Time Pilot.mp4", None),  # no episode 0
        ("Season 1 - 2", "01 Pilot.mkv", None),  # two seasons: the number isn't the season
        ("Season 3", "Episode 4.mkv", None),  # no number we can trust
        ("Specials", "Show - S00E01 - Pilot Promo.mkv", (0, 1, "Pilot Promo")),
        ("Season 1", "1920x1080 sample.mkv", None),
    ],
)  # fmt: skip
def test_parse_episode(folder: str, name: str, expected: tuple[int, int, str] | None) -> None:
    assert parse_episode(folder, name) == expected


def test_show_year_from_the_folder_or_the_file_names() -> None:
    assert show_year("Archer (2009)", []) == 2009
    assert show_year("The Simpsons", ["The Simpsons (1989) - S01E01 - x.mkv"] * 3) == 1989
    assert show_year("Scrubs", ["101 - My First Day.avi"]) is None


def touch(path: Path, size: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tv"
    simpsons = root / "The Simpsons"
    touch(simpsons / "Season 01" / "The Simpsons (1989) - S01E01 - Simpsons Roasting [WEBDL].mkv")
    touch(simpsons / "Season 01" / "The Simpsons (1989) - S01E02 - Bart the Genius [WEBDL].mkv")
    touch(simpsons / "Season 01" / "The Simpsons (1989) - S01E02 - Bart the Genius [WEBDL].en.srt")
    # A duplicate in a release folder: the "Season NN" copy wins.
    touch(simpsons / "The.Simpsons.S01.DVDRip" / "The.Simpsons.S01E02.DVDRip.avi", size=50)
    touch(simpsons / "@eaDir" / "The Simpsons - S01E03.mkv")  # Synology thumbnails
    touch(simpsons / "Season 01" / "._The Simpsons - S01E04.mkv")  # macOS resource fork
    touch(simpsons / "Season 01" / "notes.txt")
    touch(root / "Dexters Laboratory" / "Season 1" / "101 - Changes.avi")
    touch(root / "Dexters Laboratory" / "Season 1" / "Dexter - extras.avi")  # unparsed
    touch(root / "Not Carried (2001)" / "Season 1" / "Not Carried - S01E01.mkv")
    touch(root / "#recycle" / "The Simpsons - S09E09.mkv")
    return root


def rows(db: sqlite3.Connection, sql: str) -> list[tuple[object, ...]]:
    return [tuple(r) for r in db.execute(sql).fetchall()]


def test_scan_imports_carried_shows_and_keeps_the_rest_as_uncarried(
    db: sqlite3.Connection, tree: Path, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    touch(tree / "Only Extras" / "Season 1" / "Making Of.mkv")  # nothing numbered: not stored
    stats = scan_tv(db, tree, catalog)
    assert stats.shows == 2
    assert stats.episodes == 3
    # A show no channel carries is stored too, so the admin can put it on a new channel.
    assert stats.uncarried == ["Not Carried (2001)"]
    assert stats.uncarried_episodes == 1
    assert stats.unmatched_folders == ["Only Extras"]
    assert stats.unparsed == [
        "Dexters Laboratory/Season 1/Dexter - extras.avi",
        "Only Extras/Season 1/Making Of.mkv",
    ]
    assert stats.duplicates == 1
    assert rows(db, "SELECT id, title, year FROM shows ORDER BY id") == [
        ("dexters-laboratory", "Dexter's Laboratory", None),
        ("not-carried", "Not Carried", 2001),
        ("the-simpsons", "The Simpsons", 1989),
    ]
    assert rows(
        db,
        "SELECT show_id, season, episode, title, air_date, file_path FROM episodes"
        " ORDER BY show_id, season, episode",
    ) == [
        ("dexters-laboratory", 1, 1, "Changes", None,
         str(tree / "Dexters Laboratory/Season 1/101 - Changes.avi")),
        ("not-carried", 1, 1, "", None,
         str(tree / "Not Carried (2001)/Season 1/Not Carried - S01E01.mkv")),
        ("the-simpsons", 1, 1, "Simpsons Roasting", None,
         str(tree / "The Simpsons/Season 01/The Simpsons (1989) - S01E01 - Simpsons Roasting [WEBDL].mkv")),
        ("the-simpsons", 1, 2, "Bart the Genius", None,
         str(tree / "The Simpsons/Season 01/The Simpsons (1989) - S01E02 - Bart the Genius [WEBDL].mkv")),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("folder", "title"),
    [
        ("Office, The (2005)", "The Office"),
        ("Grim Adventures of Billy and Mandy, The -", "The Grim Adventures of Billy and Mandy"),
        ("Gossip.Girl", "Gossip Girl"),
        ("Russian.Doll.S01E01.720p.HEVC.x265-MeGusta", "Russian Doll"),
        ("Scooby Doo Mystery Incorporated Complete Season 1 720p x264 Salman Sk SilverRG",
         "Scooby Doo Mystery Incorporated"),
        ("The Serpent Queen S01 web hevc-d3g", "The Serpent Queen"),
        ("Why.Didnt.They.Ask.Evans.S01.1080p.WEB-DL.DDP5.1.H.264-squalor",
         "Why Didnt They Ask Evans"),
        ("Reign (2013)", "Reign"),  # Synology's stand-in for a trailing dot or space
        ("Clone High 2023", "Clone High 2023"),  # a bare year can be part of the title
        ("Schmigadoon!", "Schmigadoon!"),
    ],
)  # fmt: skip
def test_show_title_cleans_release_folder_names(folder: str, title: str) -> None:
    assert show_title(folder) == title


def test_folders_of_one_uncarried_show_merge(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    root = tmp_path / "tv"
    touch(root / "Russian Doll" / "Season 1" / "Russian Doll - S01E02.mkv")
    touch(root / "Russian.Doll.S01E01.720p.HEVC.x265-MeGusta" / "Russian.Doll.S01E01.mkv")
    touch(root / "Roseanne" / "Season 1" / "S01E01.mkv")
    touch(root / "Roseanne" / "Season 2" / "S02E01.mkv")
    stats = scan_tv(db, root, load_catalog(make_catalog()))
    assert stats.uncarried_episodes == 4
    assert rows(
        db, "SELECT show_id, season, episode FROM episodes ORDER BY show_id, season, episode"
    ) == [("roseanne", 1, 1), ("roseanne", 2, 1), ("russian-doll", 1, 1), ("russian-doll", 1, 2)]
    assert rows(db, "SELECT id, title FROM shows ORDER BY id") == [
        ("roseanne", "Roseanne"),
        ("russian-doll", "Russian Doll"),
    ]


def test_carried_only_skips_uncarried_folders(
    db: sqlite3.Connection, tree: Path, make_catalog: MakeCatalog
) -> None:
    stats = scan_tv(db, tree, load_catalog(make_catalog()), carried_only=True)
    assert stats.uncarried == []
    assert stats.unmatched_folders == ["Not Carried (2001)"]
    assert rows(db, "SELECT id FROM shows ORDER BY id") == [
        ("dexters-laboratory",),
        ("the-simpsons",),
    ]


def test_uncarried_ids_are_slugs_that_stay_stable_across_rescans(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    root = tmp_path / "tv"
    touch(root / "Doctor Who (2005)" / "Season 01" / "Doctor Who (2005) - S01E01 - Rose.mkv")
    touch(root / "Office, The (2005)" / "Season 1" / "S01E01.mkv")
    # Halloweentown is a carried movie: a TV folder with that name can't take its id.
    touch(root / "Halloweentown (2004)" / "Season 1" / "S01E01.mkv")
    scan_tv(db, root, catalog)
    first = rows(db, "SELECT id, title FROM shows ORDER BY id")
    assert first == [
        ("doctor-who", "Doctor Who"),
        ("halloweentown-2004", "Halloweentown (2004)"),
        ("the-office", "The Office"),
    ]

    # A second Doctor Who arrives: the first keeps its id, the new one takes its year.
    touch(root / "Doctor Who (1963)" / "Season 01" / "Doctor Who - S01E01.mkv")
    stats = scan_tv(db, root, catalog)
    assert stats.uncarried_episodes == 4
    assert rows(db, "SELECT id, title FROM shows ORDER BY id") == [
        ("doctor-who", "Doctor Who"),
        ("doctor-who-1963", "Doctor Who (1963)"),
        ("halloweentown-2004", "Halloweentown (2004)"),
        ("the-office", "The Office"),
    ]
    assert rows(
        db,
        "SELECT show_id, file_path LIKE '%(1963)%' FROM episodes WHERE show_id LIKE 'doctor%'"
        " ORDER BY show_id",
    ) == [("doctor-who", 0), ("doctor-who-1963", 1)]


def test_an_uncarried_show_that_becomes_carried_moves_its_files(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    root = tmp_path / "tv"
    touch(root / "Johnny Bravo (1997)" / "Season 1" / "S01E01.mkv")
    scan_tv(db, root, load_catalog(make_catalog()))
    assert rows(db, "SELECT show_id FROM episodes WHERE file_path IS NOT NULL") == [
        ("johnny-bravo",)
    ]

    carriage = CARRIAGE + (
        '  - { show: bravo, title: "Johnny Bravo", channel: cartoon-network, from: 1997-07-14 }\n'
    )
    stats = scan_tv(db, root, load_catalog(make_catalog(carriage=carriage)))
    assert stats.shows == 1 and stats.uncarried == []
    # The carried id now has the file; the old uncarried row keeps no file, so nothing airs twice.
    assert rows(db, "SELECT show_id FROM episodes WHERE file_path IS NOT NULL") == [("bravo",)]


def test_rescan_keeps_plex_and_tmdb_data_and_detaches_files_that_are_gone(
    db: sqlite3.Connection, tree: Path, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    scan_tv(db, tree, catalog)
    db.execute(
        "UPDATE episodes SET air_date = '1989-12-17', air_date_source = 'tmdb', title = 'Real'"
        " WHERE show_id = 'the-simpsons' AND episode = 1"
    )
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, file_path)"
        " VALUES ('the-simpsons', 2, 1, '/elsewhere/plex/S02E01.mkv')"
    )
    (
        tree / "The Simpsons/Season 01/The Simpsons (1989) - S01E02 - Bart the Genius [WEBDL].mkv"
    ).unlink()
    (tree / "The Simpsons/The.Simpsons.S01.DVDRip/The.Simpsons.S01E02.DVDRip.avi").unlink()

    scan_tv(db, tree, catalog)
    assert rows(
        db,
        "SELECT season, episode, title, air_date, file_path IS NOT NULL FROM episodes"
        " WHERE show_id = 'the-simpsons' ORDER BY season, episode",
    ) == [
        (1, 1, "Real", "1989-12-17", 1),  # dates and titles from Plex/TMDB are kept
        (1, 2, "Bart the Genius", None, 0),  # gone from disk: detached, row kept
        (2, 1, "", None, 1),  # outside the scanned root: untouched
    ]


def test_scan_respects_plex_title_overrides_and_exclusions(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    overrides = """
    shows:
      - { show: x-men, exclude: true }
      - { show: the-simpsons, plex_title: "Los Simpson" }
    """
    catalog = load_catalog(make_catalog(overrides=overrides))
    root = tmp_path / "tv"
    touch(root / "Los Simpson" / "Season 1" / "S01E01.mkv")
    touch(root / "X-Men (1992)" / "Season 1" / "X-Men - S01E01.mkv")
    stats = scan_tv(db, root, catalog)
    assert stats.shows == 1
    assert rows(db, "SELECT id FROM shows") == [("the-simpsons",)]
    assert stats.unmatched_folders == ["X-Men (1992)"]
    assert stats.uncarried == []  # excluded means not imported at all


def test_titles_match_without_spaces_or_a_trailing_article(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    carriage = CARRIAGE + (
        '  - { show: the-o-c, title: "The O.C.", channel: ktvu, from: 2003-01-01 }\n'
        '  - { show: grim, title: "The Grim Adventures of Billy & Mandy",'
        " channel: cartoon-network, from: 2003-01-01 }\n"
    )
    catalog = load_catalog(make_catalog(carriage=carriage))
    root = tmp_path / "tv"
    touch(root / "The OC" / "Season 1" / "The OC.S1E1. The Pilot.mkv")
    touch(root / "Grim Adventures of Billy and Mandy, The -" / "Season 1" / "S01E01.mkv")
    scan_tv(db, root, catalog)
    assert rows(db, "SELECT id FROM shows ORDER BY id") == [("grim",), ("the-o-c",)]


def test_scan_movies_reads_plex_movie_folders(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    root = tmp_path / "movies"
    touch(root / "Halloweentown (1998)" / "Halloweentown (1998).mkv", size=20)
    touch(root / "Halloweentown (1998)" / "Halloweentown (1998)-trailer.mkv", size=5)
    touch(root / "Some Other Movie (2004)" / "Some Other Movie (2004).mp4")
    stats = scan_movies(db, root, catalog)
    assert stats.movies == 1
    assert stats.unmatched_folders == ["Some Other Movie (2004)"]
    assert rows(db, "SELECT id, title, year, premiere_date, file_path, file_size FROM movies") == [
        ("halloweentown", "Halloweentown", 1998, None,
         str(root / "Halloweentown (1998)" / "Halloweentown (1998).mkv"), 20),
    ]  # fmt: skip


class FakeProbe:
    """Stands in for ffprobe: a length per file name, and a record of what was probed."""

    def __init__(self, seconds: dict[str, float | None]) -> None:
        self.seconds = seconds
        self.calls: list[str] = []

    def __call__(self, path: Path) -> float | None:
        self.calls.append(path.name)
        return self.seconds.get(path.name)


def test_scan_records_each_source_files_measured_length(
    db: sqlite3.Connection, tree: Path, make_catalog: MakeCatalog
) -> None:
    """The source's ffprobe length, not a runtime class default, is what the scheduler falls
    back on and what a normalized file is checked against."""
    catalog = load_catalog(make_catalog())
    ep1 = "The Simpsons (1989) - S01E01 - Simpsons Roasting [WEBDL].mkv"
    ep2 = "The Simpsons (1989) - S01E02 - Bart the Genius [WEBDL].mkv"
    db.execute("INSERT INTO shows (id, title) VALUES ('the-simpsons', 'The Simpsons')")
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, runtime_seconds)"
        " VALUES ('the-simpsons', 1, 1, '', 1320)"
    )
    probe = FakeProbe({ep1: 1381.7, ep2: None})  # ffprobe could not read episode 2
    scan_tv(db, tree, catalog, probe=probe)
    lengths = (
        "SELECT episode, runtime_seconds, source_seconds FROM episodes"
        " WHERE show_id = 'the-simpsons' ORDER BY episode"
    )
    assert rows(db, lengths) == [(1, 1320, 1381), (2, None, None)]

    # A rescan probes only files that are new, changed or not measured yet.
    again = FakeProbe({ep1: 9999.0, ep2: 1290.0})
    scan_tv(db, tree, catalog, probe=again)
    assert ep1 not in again.calls
    assert ep2 in again.calls
    (tree / "The Simpsons" / "Season 01" / ep1).write_bytes(b"a longer file")
    scan_tv(db, tree, catalog, probe=FakeProbe({ep1: 1402.2}))
    assert rows(db, lengths) == [(1, 1320, 1402), (2, None, 1290)]

    # Without a prober (tests, or --no-probe) an unchanged file keeps its measured length.
    scan_tv(db, tree, catalog)
    assert rows(db, lengths) == [(1, 1320, 1402), (2, None, 1290)]


def test_scan_movies_records_the_measured_length(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    root = tmp_path / "movies"
    touch(root / "Halloweentown (1998)" / "Halloweentown (1998).mkv", size=20)
    probe = FakeProbe({"Halloweentown (1998).mkv": 5003.5})
    scan_movies(db, root, load_catalog(make_catalog()), probe=probe)
    assert rows(db, "SELECT id, source_seconds FROM movies") == [("halloweentown", 5003)]


# --- more than one root: Plex's trees plus the LiveTV downloads (M4) ---------------------


@pytest.fixture
def livetv(tmp_path: Path) -> Path:
    """sonarr-livetv's tree: Plex-style names, a show Plex doesn't have and a later season of
    one it does."""
    root = tmp_path / "livetv" / "tv"
    touch(
        root / "X-Men (1992)" / "Season 01" / "X-Men (1992) - S01E01 - Night of the Sentinels.mkv"
    )
    touch(
        root
        / "The Simpsons (1989)"
        / "Season 02"
        / "The Simpsons (1989) - S02E01 - Bart Gets an F.mkv"
    )
    # A copy of an episode Plex already has: Plex's copy wins (it is never deleted).
    touch(
        root
        / "The Simpsons (1989)"
        / "Season 01"
        / "The Simpsons (1989) - S01E01 - Simpsons Roasting.mkv",
        size=500,
    )
    return root  # fmt: skip


def test_scan_reads_several_roots_as_one_library(
    db: sqlite3.Connection, tree: Path, livetv: Path, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    stats = scan_tv(db, [tree, livetv], catalog)
    assert stats.shows == 3
    assert rows(
        db,
        "SELECT show_id, season, episode, file_path FROM episodes WHERE show_id IN"
        " ('the-simpsons', 'x-men') ORDER BY show_id, season, episode",
    ) == [
        ("the-simpsons", 1, 1,
         str(tree / "The Simpsons/Season 01/The Simpsons (1989) - S01E01 - Simpsons Roasting [WEBDL].mkv")),
        ("the-simpsons", 1, 2,
         str(tree / "The Simpsons/Season 01/The Simpsons (1989) - S01E02 - Bart the Genius [WEBDL].mkv")),
        ("the-simpsons", 2, 1,
         str(livetv / "The Simpsons (1989)/Season 02/The Simpsons (1989) - S02E01 - Bart Gets an F.mkv")),
        ("x-men", 1, 1,
         str(livetv / "X-Men (1992)/Season 01/X-Men (1992) - S01E01 - Night of the Sentinels.mkv")),
    ]  # fmt: skip
    assert stats.duplicates == 2  # the DVDRip copy, and LiveTV's copy of S01E01

    # The same scan again changes nothing: no root's copy flips with the other's.
    before = rows(db, "SELECT id, file_path FROM episodes ORDER BY id")
    again = scan_tv(db, [tree, livetv], catalog)
    assert again.detached == 0
    assert rows(db, "SELECT id, file_path FROM episodes ORDER BY id") == before


@pytest.mark.parametrize("state", ["empty", "missing"])
def test_an_empty_or_missing_root_detaches_nothing_from_another_root(
    db: sqlite3.Connection, tree: Path, livetv: Path, make_catalog: MakeCatalog, state: str
) -> None:
    catalog = load_catalog(make_catalog())
    scan_tv(db, [tree, livetv], catalog)
    plex_files = rows(db, f"SELECT id FROM episodes WHERE file_path LIKE '{tree}/%'")
    # The LiveTV tree empties (every download converted and deleted) or isn't there.
    shutil.rmtree(livetv)
    if state == "empty":
        livetv.mkdir()
    stats = scan_tv(db, [tree, livetv], catalog)
    assert rows(db, f"SELECT id FROM episodes WHERE file_path LIKE '{tree}/%'") == plex_files
    # An empty root really is empty: its files are detached. A missing one is left alone.
    gone = rows(db, f"SELECT COUNT(*) FROM episodes WHERE file_path LIKE '{livetv}/%'")[0][0]
    assert (stats.detached, gone) == ((2, 0) if state == "empty" else (0, 2))
    assert stats.missing_roots == ([] if state == "empty" else [str(livetv)])


def test_scan_with_one_root_still_takes_a_path(
    db: sqlite3.Connection, tree: Path, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    assert scan_tv(db, tree, catalog).shows == scan_tv(db, [tree], catalog).shows == 2


def test_scan_movies_reads_several_roots_and_prefers_the_first(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    plex, livetv = tmp_path / "movies", tmp_path / "livetv" / "movies"
    touch(livetv / "Halloweentown (1998)" / "Halloweentown (1998).mkv", size=90)
    stats = scan_movies(db, [plex, livetv], catalog)  # Plex's movie tree isn't there
    assert stats.movies == 1 and stats.missing_roots == [str(plex)]
    assert rows(db, "SELECT file_path FROM movies") == [
        (str(livetv / "Halloweentown (1998)" / "Halloweentown (1998).mkv"),)
    ]
    touch(plex / "Halloweentown (1998)" / "Halloweentown (1998).mkv", size=20)
    scan_movies(db, [plex, livetv], catalog)
    scan_movies(db, [plex, livetv], catalog)
    assert rows(db, "SELECT file_path FROM movies") == [
        (str(plex / "Halloweentown (1998)" / "Halloweentown (1998).mkv"),)
    ]


def test_scan_movies_reads_release_named_folders(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    """Plex's movie tree has folders named like releases: the title ends at a year or a
    release tag. A year inside a title doesn't cut it unless a tag follows."""
    root = tmp_path / "movies"
    touch(root / "Halloweentown.1998.1080p.WEB-DL.DD5.1.H264" / "h.mkv")
    stats = scan_movies(db, root, load_catalog(make_catalog()))
    assert stats.movies == 1 and rows(db, "SELECT id FROM movies") == [("halloweentown",)]


@pytest.mark.parametrize(
    ("folder", "title"),
    [
        ("Psych.The.Movie.2017.HDRip.XviD.AC3-EVO", "Psych The Movie"),
        (
            "Hey Arnold The Jungle Movie (2017) 720p Webrip X264 Solar",
            "Hey Arnold The Jungle Movie",
        ),
        (
            "Rugrats in Paris The Movie 2000 1080p WEB-DL DD5 1 H264-SA89",
            "Rugrats in Paris The Movie",
        ),
        ("Addams_Family_Reunion_DVDRip_XviD-KIDSROCK", "Addams Family Reunion"),
        ("Beauty.Shop[2005]DvDRip-Rdgrnnr", "Beauty Shop"),
        ("Aloha Scooby Doo (2005) [1080p]", "Aloha Scooby Doo"),
        ("Blade Runner 2049 (2017)", "Blade Runner 2049"),
        ("2001 A Space Odyssey (1968)", "2001 A Space Odyssey"),
        ("Amelie (2001)", "Amelie"),
    ],
)
def test_movie_title_from_a_release_name(folder: str, title: str) -> None:
    assert movie_title(folder) == title


def test_scan_movies_matches_the_minis_admin_movies(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    """Movies the channel builder placed are carried on the mini's /admin channels, not in
    the YAML: with the mini's library items they match too, under the mini's id."""
    root = tmp_path / "movies"
    touch(root / "The Rugrats Movie (1998)" / "The Rugrats Movie (1998).mkv")
    touch(root / "Nobody Carries Me (1999)" / "n.mkv")
    mini: list[dict[str, Any]] = [
        {"id": "the-rugrats-movie", "title": "The Rugrats Movie", "kind": "movie",
         "channels": [{"id": "custom-nick-flicks"}]},
        {"id": "nobody-carries-me", "title": "Nobody Carries Me", "kind": "movie",
         "channels": []},
    ]  # fmt: skip
    stats = scan_movies(db, root, load_catalog(make_catalog()), extra=mini)
    assert rows(db, "SELECT id, title FROM movies") == [("the-rugrats-movie", "The Rugrats Movie")]
    assert stats.unmatched_folders == ["Nobody Carries Me (1999)"]


def test_scan_reads_season_and_episode_numbers_from_a_whole_show_folder(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    # `101 - Title` outside a season folder is S01E01 when every numbered file in the folder
    # reads that way; a folder with low numbers too is absolute numbering and stays unparsed.
    root = tmp_path / "tv"
    for name in (
        "Freakazoid - 101 - Dance Of Doom.avi",
        "Freakazoid - 102 - Candle Jack.avi",
        "Freakazoid - 201 - The Chip.avi",
    ):
        touch(root / "Freakazoid" / name)
    for name in ("101 Theres No Creature Like Snow Creature.avi", "102 3-D Struction.avi"):
        touch(root / "Whats New Scooby Doo" / name)
    for name in ("Anime - 099 - A.mkv", "Anime - 100 - B.mkv", "Anime - 101 - C.mkv"):
        touch(root / "Anime" / name)
    touch(root / "Lone" / "Lone - 101 - Only One.avi")
    stats = scan_tv(db, root, load_catalog(make_catalog()))
    assert rows(db, "SELECT show_id, season, episode, title FROM episodes"
                    " ORDER BY show_id, season, episode") == [
        ("freakazoid", 1, 1, "Dance Of Doom"),
        ("freakazoid", 1, 2, "Candle Jack"),
        ("freakazoid", 2, 1, "The Chip"),
        ("whats-new-scooby-doo", 1, 1, "Theres No Creature Like Snow Creature"),
        ("whats-new-scooby-doo", 1, 2, "3-D Struction"),
    ]  # fmt: skip
    assert sorted(stats.unparsed) == [
        "Anime/Anime - 099 - A.mkv", "Anime/Anime - 100 - B.mkv", "Anime/Anime - 101 - C.mkv",
        "Lone/Lone - 101 - Only One.avi",
    ]  # fmt: skip


def test_scan_reads_absolute_numbers_and_release_folders_in_a_season(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    # Names that differ only by one number are absolute numbering: season 1, episode N.
    # Files in a release folder inside a season folder take that season.
    root = tmp_path / "tv"
    for n in ("01", "02", "26"):
        touch(root / "Samurai Champloo" / f"[a-s] Samurai Champloo {n}.mkv")
        touch(root / "Princess Jellyfish" / f"Princess.Jellyfish.-.{n}.-.1080p.BluRay.x264.DHD.mkv")
        touch(root / "Show" / "Extras" / f"Making Of {n}.mkv")
        touch(root / "Midwife" / "Midwife Series 5 + Bonus" / f"Episode {n}.mp4")
        touch(root / "Dog" / "Dog.S01" / f"cd1{n}-wpi.avi")
    touch(root / "Pair" / "Pair 01.mkv")
    touch(root / "Pair" / "Pair 02.mkv")
    for name in ("01 Tourist Trapped.mp4", "02 The Legend of the Gobblewonker.mp4"):
        touch(root / "Gravity Falls" / "Season 1" / "Gravity Falls 720p" / name)
    stats = scan_tv(db, root, load_catalog(make_catalog()))
    assert rows(db, "SELECT show_id, season, episode, title FROM episodes"
                    " ORDER BY show_id, season, episode") == [
        ("gravity-falls", 1, 1, "Tourist Trapped"),
        ("gravity-falls", 1, 2, "The Legend of the Gobblewonker"),
        ("princess-jellyfish", 1, 1, ""),
        ("princess-jellyfish", 1, 2, ""),
        ("princess-jellyfish", 1, 26, ""),
        ("samurai-champloo", 1, 1, ""),
        ("samurai-champloo", 1, 2, ""),
        ("samurai-champloo", 1, 26, ""),
    ]  # fmt: skip
    # A folder that names a season isn't absolute numbering; two files prove nothing.
    assert sorted(stats.unparsed) == [
        "Dog/Dog.S01/cd101-wpi.avi", "Dog/Dog.S01/cd102-wpi.avi", "Dog/Dog.S01/cd126-wpi.avi",
        "Midwife/Midwife Series 5 + Bonus/Episode 01.mp4",
        "Midwife/Midwife Series 5 + Bonus/Episode 02.mp4",
        "Midwife/Midwife Series 5 + Bonus/Episode 26.mp4",
        "Pair/Pair 01.mkv", "Pair/Pair 02.mkv",
        "Show/Extras/Making Of 01.mkv", "Show/Extras/Making Of 02.mkv",
        "Show/Extras/Making Of 26.mkv",
    ]  # fmt: skip
