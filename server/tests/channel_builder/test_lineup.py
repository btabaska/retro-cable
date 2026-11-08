"""Pruning the YAML catalog to Brandon's lineup (M4): what is kept, what goes, every fuzzy
match reported, and a catalog that still validates afterwards."""

from pathlib import Path
from textwrap import dedent

import pytest

from retro_cable.catalog import load_catalog, validate
from retro_cable.channel_builder.lineup import decide, main, prune, read_list
from tests.conftest import MakeCatalog

LIST = """﻿action,review,category,title,kind,first_aired,after_2010,original_network,on_nas,already_carried,tmdb_id,aka,notes,source
keep,ok,family,The Simpsons,show,1989-12-17,,FOX,yes,yes,tv:456,,,
remove,ok,kids,Dexter's Laboratory,show,1996-04-28,,Cartoon Network,yes,yes,tv:4229,,,
keep,ok,anime,Dragon Ball Z: The Saga,show,1989,,Fuji TV,,,tv:12971,,,
keep,ok,anime,Dragon Ball Z Kai,show,2009,,Fuji TV,,,tv:61709,,,
keep,ok,teen,Gravity Falls,show,2012-06-15,yes,Disney Channel,,,tv:40075,Gravity Falls (reruns),,
keep,ok,family,Halloweentown,movie,1998-10-17,,Disney Channel,,,movie:10719,,,
add,new,kids,Johnny Bravo,show,1997-07-07,,Cartoon Network,,,tv:2,,,
"""

# X-Men is not in the list at all; Dexter's Laboratory is a `remove` row.
CARRIAGE = """\
# yaml-language-server: $schema=schema/carriage.schema.json
# A header comment stays.

carriage:
  # KTVU
  - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 1990-01-01 }
  - { show: x-men, title: X-Men, channel: ktvu, block: fox-kids, from: 1992-10-31, until: 1997-09-20 }
  - { show: gravity-falls, title: Gravity Falls, channel: ktvu, from: 2004-06-15 }

  # Cartoon Network: everything here goes
  - { show: dexters-laboratory, title: Dexter's Laboratory, channel: cartoon-network, from: 1996-04-28 }

  # Cartoon Network, later
  - { show: dragon-ball-z, title: Dragon Ball Z, channel: cartoon-network, from: 1998-03-16 }
  - { show: gravity-falls, title: Gravity Falls, channel: cartoon-network, from: 2004-06-15 }
  - { show: halloweentown, title: Halloweentown, channel: disney-movies, from: 1990-01-01, kind: movie }
"""

OVERRIDES = """\
# yaml-language-server: $schema=schema/overrides.schema.json
shows:
  - show: dragon-ball-z
    us_debut: 1996-09-13
  - { show: x-men, us_debut: 1992-10-31 }
  - { show: gravity-falls, tmdb_id: "tv:1" }
"""

SYNTHETIC = """\
synthetic:
  - { show: gravity-falls, channel: cartoon-network, premiere: 2004-06-15 }
  - { show: dexters-laboratory, channel: cartoon-network, premiere: 1996-04-28 }
"""

TEMPLATES = """\
templates:
  - id: ktvu-base
    channel: ktvu
    from: 1990-01-01
    until: 1999-12-31
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: the-simpsons }] }
      - { weekday: friday, start: "16:00", block: fox-kids, pool: [{ show: x-men }] }
      - { weekday: friday, start: "20:00", primetime: true, pool: [{ show: the-simpsons, weight: 2 }, { show: x-men }] }
      - { weekday: friday, start: "23:00", pool: [{ show: x-men }, { show: gravity-falls }] }
  - id: ktvu-later
    channel: ktvu
    from: 2000-01-01
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: the-simpsons }] }
      - { weekday: friday, start: "22:00", pool: [{ show: gravity-falls }, { show: x-men }] }

  # Cartoon Network
  - id: cn-classics
    channel: cartoon-network
    from: 1992-10-01
    until: 1997-12-31
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: dexters-laboratory, weight: 2 }] }
  - id: cn-later
    channel: cartoon-network
    from: 1998-01-01
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: dexters-laboratory, weight: 2 }] }
      - { weekday: friday, start: "20:00", primetime: true, pool: [{ show: dragon-ball-z }, { show: gravity-falls, weight: 2 }] }
"""


@pytest.fixture
def catalog_dir(make_catalog: MakeCatalog) -> Path:
    return make_catalog(
        carriage=CARRIAGE, overrides=OVERRIDES, synthetic=SYNTHETIC, templates=TEMPLATES
    )


@pytest.fixture
def list_csv(tmp_path: Path) -> Path:
    path = tmp_path / "list.csv"
    path.write_text(LIST, encoding="utf-8")
    return path


def test_decisions_match_titles_aka_and_variants(catalog_dir: Path, list_csv: Path) -> None:
    decisions = {d.show: d for d in decide(load_catalog(catalog_dir), read_list(list_csv))}
    assert {s: (d.keep, d.how) for s, d in decisions.items()} == {
        "the-simpsons": (True, "title"),
        "x-men": (False, "not in the list"),
        "dexters-laboratory": (False, "remove row"),
        # Two kept rows start with "Dragon Ball Z": kept, but which ids is a judgment call.
        "dragon-ball-z": (True, "ambiguous variant"),
        "gravity-falls": (True, "title"),
        "halloweentown": (True, "title"),
    }
    assert decisions["dragon-ball-z"].row is None
    assert "Dragon Ball Z Kai" in decisions["dragon-ball-z"].note
    assert decisions["dragon-ball-z"].fuzzy and not decisions["the-simpsons"].fuzzy


def test_a_judgment_picks_the_row_or_keeps_by_hand(catalog_dir: Path, list_csv: Path) -> None:
    catalog, rows = load_catalog(catalog_dir), read_list(list_csv)
    same = {"dragon-ball-z": "Dragon Ball Z: The Saga", "x-men": "Johnny Bravo"}
    decisions = {d.show: d for d in decide(catalog, rows, same=same)}
    assert (decisions["dragon-ball-z"].keep, decisions["dragon-ball-z"].how) == (True, "same as")
    assert decisions["dragon-ball-z"].row is not None
    assert decisions["dragon-ball-z"].row.tmdb_id == "tv:12971"
    by_hand = {d.show: d for d in decide(catalog, rows, keep={"x-men"})}
    assert (by_hand["x-men"].keep, by_hand["x-men"].how, by_hand["x-men"].row) == (
        True,
        "kept by hand",
        None,
    )
    with pytest.raises(ValueError, match="Nope"):
        decide(catalog, rows, same={"x-men": "Nope"})


def test_a_one_word_variant_and_spacing_are_fuzzy_matches(make_catalog: MakeCatalog) -> None:
    carriage = """\
carriage:
  - { show: digimon, title: Digimon, channel: ktvu, from: 1999-01-01 }
  - { show: yu-yu-hakusho, title: Yu Yu Hakusho, channel: ktvu, from: 2002-01-01 }
"""
    rows = read_list_text(
        "action,title,kind,first_aired,tmdb_id,aka\n"
        "keep,Digimon: Digital Monsters,show,1999-08-14,tv:31654,Digimon Adventure\n"
        "keep,Digimon: The Movie,movie,2000,movie:1,\n"
        "remove,YuYu Hakusho,show,1992,tv:30669,\n"
    )
    catalog = load_catalog(
        make_catalog(carriage=carriage, overrides="shows: []\n", synthetic="synthetic: []\n",
                     templates="templates: []\n")
    )  # fmt: skip
    decisions = {d.show: d for d in decide(catalog, rows)}
    digimon = decisions["digimon"]
    assert (digimon.keep, digimon.how, digimon.fuzzy) == (True, "variant", True)
    assert digimon.row is not None and digimon.row.tmdb_id == "tv:31654"  # not the movie
    yyh = decisions["yu-yu-hakusho"]
    assert (yyh.keep, yyh.how, yyh.fuzzy) == (False, "remove row (spacing)", True)


def read_list_text(text: str) -> list:  # type: ignore[type-arg]
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "l.csv"
        path.write_text(text)
        return read_list(path)


def test_prune_rewrites_the_yaml_and_keeps_it_valid(catalog_dir: Path, list_csv: Path) -> None:
    same = {"dragon-ball-z": "Dragon Ball Z: The Saga"}
    report = prune(catalog_dir, read_list(list_csv), same=same)
    carriage = (catalog_dir / "carriage.yaml").read_text()
    assert carriage == dedent("""\
        # yaml-language-server: $schema=schema/carriage.schema.json
        # A header comment stays.

        carriage:
          # KTVU
          - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 1990-01-01 }
          - { show: gravity-falls, title: Gravity Falls, channel: ktvu, from: 2004-06-15 }

          # Cartoon Network, later
          - { show: dragon-ball-z, title: Dragon Ball Z, channel: cartoon-network, from: 1998-03-16 }
          - { show: gravity-falls, title: Gravity Falls, channel: cartoon-network, from: 2004-06-15 }
          - { show: halloweentown, title: Halloweentown, channel: disney-movies, from: 1990-01-01, kind: movie }
        """)
    overrides = (catalog_dir / "overrides.yaml").read_text()
    assert overrides == dedent("""\
        # yaml-language-server: $schema=schema/overrides.schema.json
        shows:
          - show: dragon-ball-z
            us_debut: 1996-09-13
            tmdb_id: "tv:12971"
            year: 1989
          - { show: gravity-falls, tmdb_id: "tv:1", year: 2012 }

          # TMDB ids and first-aired years from Brandon's lineup (channel_builder.lineup prune)
          - { show: halloweentown, tmdb_id: "movie:10719", year: 1998 }
          - { show: the-simpsons, tmdb_id: "tv:456", year: 1989 }
        """)
    assert (catalog_dir / "synthetic.yaml").read_text() == (
        "synthetic:\n  - { show: gravity-falls, channel: cartoon-network, premiere: 2004-06-15 }\n"
    )
    templates = (catalog_dir / "templates.yaml").read_text()
    # X-Men leaves the pools; the Fox Kids part keeps its block (the-simpsons isn't in it, so
    # its emptied pool takes the template's remaining shows and drops the block).
    assert '{ weekday: friday, start: "16:00", pool: [{ show: the-simpsons }] }' in templates
    assert (
        '{ weekday: friday, start: "20:00", primetime: true, pool: [{ show: the-simpsons,'
        " weight: 2 }] }" in templates
    )
    # Gravity Falls is left at 23:00, but KTVU carries it only from 2004, after this template:
    # the part plays what the template has in its years instead.
    assert '{ weekday: friday, start: "23:00", pool: [{ show: the-simpsons }] }' in templates
    # ktvu-later at 22:00 keeps Gravity Falls (2004 on), but X-Men leaving opens 2000-2003:
    # the template's shows carried then join the pool.
    assert (
        '{ weekday: friday, start: "22:00", pool: [{ show: gravity-falls }, { show:'
        " the-simpsons }] }" in templates
    )
    assert "ktvu-later fri 22:00: nothing in its pool airs in 2000-2003; the-simpsons" in (
        "\n".join(report.notes)
    )
    # cn-classics had only Dexter: dropped. cn-later's midnight part takes what is left.
    assert "cn-classics" not in templates and "# Cartoon Network" in templates
    assert (
        '{ weekday: friday, start: "00:00", pool: [{ show: dragon-ball-z }, { show:'
        " gravity-falls }] }" in templates
    )
    catalog = load_catalog(catalog_dir)
    assert validate(catalog) == []
    assert {c.show for c in catalog.carriage.carriage} == {
        "the-simpsons", "dragon-ball-z", "gravity-falls", "halloweentown"
    }  # fmt: skip
    assert report.kept == 4 and len(report.removed) == 2
    assert report.dropped_templates == ["cn-classics"]
    assert "kept overrides.yaml's tmdb_id tv:1 for gravity-falls (the list says tv:40075)" in (
        report.notes
    )
    # Running it again changes nothing.
    before = {p.name: p.read_text() for p in catalog_dir.glob("*.yaml")}
    again = prune(catalog_dir, read_list(list_csv), same=same)
    assert {p.name: p.read_text() for p in catalog_dir.glob("*.yaml")} == before
    assert again.removed == [] and again.kept == 4


def test_cli_dry_run_writes_nothing_and_reports_every_judgment(
    catalog_dir: Path, list_csv: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = {p.name: p.read_text() for p in catalog_dir.glob("*.yaml")}
    args = ["prune", "--shows", str(list_csv), "--catalog", str(catalog_dir), "--dry-run"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert {p.name: p.read_text() for p in catalog_dir.glob("*.yaml")} == before
    assert "kept 4 carried titles, removed 2" in out
    assert "dragon-ball-z (Dragon Ball Z): kept, ambiguous variant" in out
    assert "x-men (X-Men): removed, not in the list" in out
    assert "dry run: nothing written" in out
    assert main([*args[:-1], "--same", "dragon-ball-z=Dragon Ball Z: The Saga"]) == 0
    assert "catalog ok" in capsys.readouterr().out
    assert main([*args[:-1], "--same", "no-equals-sign"]) == 2
