import json
import sqlite3
from pathlib import Path

import pytest

from retro_cable.catalog import load_catalog
from retro_cable.db import connect, migrate
from retro_cable.inventory import build_inventory, main, render_markdown
from tests.conftest import MakeCatalog


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "retro-cable.db")
    migrate(conn)
    conn.executescript(
        """
        INSERT INTO shows (id, title, tmdb_id) VALUES
          ('the-simpsons', 'The Simpsons', 456), ('dragon-ball-z', 'Dragon Ball Z', NULL),
          ('gravity-falls', 'Gravity Falls', 40075), ('dexters-laboratory', 'Dexter''s Laboratory', 4229);
        INSERT INTO episodes (show_id, season, episode, air_date, file_path) VALUES
          ('the-simpsons', 10, 1, '1998-08-23', '/s1001.mkv'),
          ('the-simpsons', 10, 2, '1998-09-20', NULL),
          ('the-simpsons', 10, 4, '1998-10-25', '/s1004.mkv'),
          ('the-simpsons', 11, 1, '1999-09-26', '/s1101.mkv'),
          ('the-simpsons', 0, 1, NULL, '/special.mkv'),
          ('dragon-ball-z', 1, 1, '1989-04-26', '/dbz1.mkv'),
          ('dragon-ball-z', 1, 2, '1989-05-03', '/dbz2.mkv'),
          ('gravity-falls', 1, 1, '2012-06-15', '/gf1.mkv'),
          ('gravity-falls', 1, 2, '2012-06-29', NULL),
          ('dexters-laboratory', 1, 1, '1996-04-28', NULL);
        INSERT INTO movies (id, title, premiere_date, file_path) VALUES
          ('halloweentown', 'Halloweentown', '1998-10-17', '/ht.mkv');
        INSERT INTO import_runs (source, started_at, finished_at, stats) VALUES
          ('plex', '2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00',
           '{"unmatched_shows": ["Uncarried Show (2015)"], "unmatched_movies": []}');
        """
    )
    return conn


def row(inv, channel: str, year: int):  # type: ignore[no-untyped-def]
    return next((r for r in inv.rows if r.channel == channel and r.year == year), None)


def test_counts_eligible_episodes_per_channel_and_year(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    inv = build_inventory(load_catalog(make_catalog()), db)
    ktvu98 = row(inv, "ktvu", 1998)
    assert (ktvu98.expected, ktvu98.on_disk, ktvu98.missing) == (3, 2, 1)
    ktvu99 = row(inv, "ktvu", 1999)
    assert (ktvu99.expected, ktvu99.on_disk) == (4, 3)
    assert row(inv, "ktvu", 1997).expected == 0


def test_hidden_channel_years_are_left_out(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    inv = build_inventory(load_catalog(make_catalog()), db)
    assert row(inv, "cartoon-network", 1991) is None
    assert row(inv, "cartoon-network", 1992) is not None
    assert row(inv, "disney-movies", 1990) is not None  # library channels are always on


def test_us_debut_and_synthetic_premieres_shift_eligibility(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    inv = build_inventory(load_catalog(make_catalog()), db)
    # Cartoon Network carries Dexter (1996-), DBZ (1998-) and Gravity Falls (2004-).
    assert (
        row(inv, "cartoon-network", 1997).expected,
        row(inv, "cartoon-network", 1997).on_disk,
    ) == (1, 0)
    assert row(inv, "cartoon-network", 1998).expected == 3  # DBZ counts from its 1996 US debut
    cn04 = row(inv, "cartoon-network", 2004)
    assert (cn04.expected, cn04.on_disk) == (5, 3)  # Gravity Falls from its synthetic 2004 premiere


def test_movies_count_from_their_premiere(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    inv = build_inventory(load_catalog(make_catalog()), db)
    assert (
        row(inv, "disney-movies", 1997).movies_expected,
        row(inv, "disney-movies", 1998).movies_on_disk,
    ) == (0, 1)


def test_reports_gaps(db: sqlite3.Connection, make_catalog: MakeCatalog) -> None:
    gaps = build_inventory(load_catalog(make_catalog()), db).gaps
    assert gaps.missing_shows == ["x-men"]  # carried, nothing in Plex
    assert gaps.shows_without_files == ["dexters-laboratory"]
    assert gaps.undated_on_disk == ["the-simpsons S00E01"]
    assert gaps.shows_without_tmdb_id == ["dragon-ball-z"]
    assert gaps.missing_movies == []
    assert gaps.in_plex_not_carried == ["Uncarried Show (2015)"]


def test_markdown_has_a_table_per_channel_and_the_gaps(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    text = render_markdown(build_inventory(load_catalog(make_catalog()), db))
    assert "## ktvu — KTVU (Fox)" in text
    assert "| 1998 | 1 | 3 | 2 | 1 | 0 | 0 |" in text
    assert "### Carried but not in Plex" in text
    assert "- x-men" in text


def test_cli_writes_markdown_and_json(
    db: sqlite3.Connection, make_catalog: MakeCatalog, tmp_path: Path
) -> None:
    db_path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    out = tmp_path / "reports"
    assert main(["--db", str(db_path), "--catalog", str(make_catalog()), "--out", str(out)]) == 0
    assert (out / "inventory.md").read_text().startswith("# Inventory")
    data = json.loads((out / "inventory.json").read_text())
    assert data["gaps"]["missing_shows"] == ["x-men"]
    assert {"channel", "year", "expected", "on_disk"} <= set(data["rows"][0])


def test_cli_works_before_any_import(make_catalog: MakeCatalog, tmp_path: Path) -> None:
    db_path = tmp_path / "not-yet" / "retro-cable.db"
    assert main(["--db", str(db_path), "--catalog", str(make_catalog()), "--out", str(tmp_path / "r")]) == 0
    assert "x-men" in (tmp_path / "r" / "inventory.md").read_text()
