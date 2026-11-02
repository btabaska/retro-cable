"""`ingest scan`: read a Plex-named TV tree straight off the disk (no Plex token needed)."""

import sqlite3
from pathlib import Path

import pytest

from retro_cable.catalog import load_catalog
from retro_cable.ingest.scan import parse_episode, scan_movies, scan_tv, show_year
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


def test_scan_imports_carried_shows_and_reports_the_rest(
    db: sqlite3.Connection, tree: Path, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    stats = scan_tv(db, tree, catalog)
    assert stats.shows == 2
    assert stats.episodes == 3
    assert stats.unmatched_folders == ["Not Carried (2001)"]
    assert stats.unparsed == ["Dexters Laboratory/Season 1/Dexter - extras.avi"]
    assert stats.duplicates == 1
    assert rows(db, "SELECT id, title, year FROM shows ORDER BY id") == [
        ("dexters-laboratory", "Dexter's Laboratory", None),
        ("the-simpsons", "The Simpsons", 1989),
    ]
    assert rows(
        db,
        "SELECT show_id, season, episode, title, air_date, file_path FROM episodes"
        " ORDER BY show_id, season, episode",
    ) == [
        ("dexters-laboratory", 1, 1, "Changes", None,
         str(tree / "Dexters Laboratory/Season 1/101 - Changes.avi")),
        ("the-simpsons", 1, 1, "Simpsons Roasting", None,
         str(tree / "The Simpsons/Season 01/The Simpsons (1989) - S01E01 - Simpsons Roasting [WEBDL].mkv")),
        ("the-simpsons", 1, 2, "Bart the Genius", None,
         str(tree / "The Simpsons/Season 01/The Simpsons (1989) - S01E02 - Bart the Genius [WEBDL].mkv")),
    ]  # fmt: skip


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
