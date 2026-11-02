"""templates.yaml: loading, pool semantics, and the cross-file checks the schema can't express."""

import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from retro_cable.catalog import load_catalog, validate
from retro_cable.catalog.load import CatalogError

MakeCatalog = Callable[..., Path]

BASE = """
templates:
  - id: ktvu-base
    channel: ktvu
    from: 1990-01-01
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: the-simpsons }] }
      - { weekday: friday, start: "16:00", block: fox-kids, pool: [{ show: x-men }] }
"""


def test_templates_load(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog(templates=BASE))
    assert [t.id for t in catalog.templates.templates] == ["ktvu-base"]
    part = catalog.templates.templates[0].parts[1]
    assert part.block == "fox-kids"
    assert part.pool[0].show == "x-men" and part.pool[0].weight == 1.0


def test_templates_file_is_optional(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog(templates=None))
    assert catalog.templates.templates == []


def test_pool_needs_exactly_one_of_show_or_tag() -> None:
    with pytest.raises(CatalogError, match="exactly one of show or tag"):
        load_catalog(
            _write("""
templates:
  - id: t
    channel: ktvu
    from: 1990-01-01
    parts: [{ weekday: friday, start: "00:00", pool: [{ show: a, tag: b }] }]
""")
        )
    with pytest.raises(CatalogError, match="exactly one of show or tag"):
        load_catalog(_write("""
templates:
  - id: t
    channel: ktvu
    from: 1990-01-01
    parts: [{ weekday: friday, start: "00:00", pool: [{}] }]
"""))  # fmt: skip


def test_start_must_be_a_clock_time() -> None:
    with pytest.raises(CatalogError, match=r"templates\.yaml is invalid"):
        load_catalog(_write("""
templates:
  - id: t
    channel: ktvu
    from: 1990-01-01
    parts: [{ weekday: friday, start: "9am", pool: [] }]
"""))  # fmt: skip


def _write(templates: str) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "channels.yaml").write_text(
        "timezone: America/New_York\nyear_range: [1990, 2010]\n"
        "channels:\n  - id: ktvu\n    name: KTVU\n    type: network\n"
        "    identity: [{ from: 1990-01-01, number: 2, call_sign: KTVU }]\n"
        "    blocks: [{ id: fox-kids, name: Fox Kids }]\n"
    )
    (root / "holidays.yaml").write_text("holidays: []\n")
    (root / "templates.yaml").write_text(templates)
    return root


def _validate(make_catalog: MakeCatalog, **replace: str) -> list[str]:
    return validate(load_catalog(make_catalog(**replace)))


def test_valid_template_has_no_errors(make_catalog: MakeCatalog) -> None:
    assert _validate(make_catalog, templates=BASE) == []


def test_unknown_channel_is_reported(make_catalog: MakeCatalog) -> None:
    errors = _validate(make_catalog, templates=BASE.replace("channel: ktvu", "channel: nope"))
    assert "templates: 'ktvu-base' has unknown channel 'nope'" in errors


def test_movie_channels_have_no_day_parts(make_catalog: MakeCatalog) -> None:
    errors = _validate(
        make_catalog, templates=BASE.replace("channel: ktvu", "channel: disney-movies")
    )
    assert "templates: 'ktvu-base' targets movie channel 'disney-movies'" in errors  # fmt: skip


def test_duplicate_template_ids(make_catalog: MakeCatalog) -> None:
    dup = """
templates:
  - id: dup
    channel: ktvu
    from: 1990-01-01
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: the-simpsons }] }
  - id: dup
    channel: ktvu
    from: 2000-01-01
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: x-men }] }
"""  # fmt: skip
    errors = _validate(make_catalog, templates=dup)
    assert "templates: duplicate template id 'dup'" in errors


def test_era_periods_must_not_overlap(make_catalog: MakeCatalog) -> None:
    two = BASE + """  - id: ktvu-later
    channel: ktvu
    from: 1995-01-01
    parts:
      - { weekday: friday, start: "00:00", pool: [{ show: x-men }] }
"""  # fmt: skip
    errors = _validate(make_catalog, templates=two)
    assert "ktvu: template era periods overlap" in errors


def test_weekday_must_have_a_midnight_part(make_catalog: MakeCatalog) -> None:
    errors = _validate(make_catalog, templates=BASE.replace('start: "00:00"', 'start: "06:00"'))
    assert "ktvu-base: friday must have a part starting at 00:00" in errors


def test_two_parts_cannot_start_at_the_same_time(make_catalog: MakeCatalog) -> None:
    errors = _validate(make_catalog, templates=BASE.replace('start: "16:00"', 'start: "00:00"'))
    assert "ktvu-base: friday has two parts starting at the same time" in errors


def test_block_must_exist_on_the_channel(make_catalog: MakeCatalog) -> None:
    errors = _validate(make_catalog, templates=BASE.replace("block: fox-kids", "block: ytv"))
    assert "ktvu-base: unknown block 'ytv' on ktvu" in errors


def test_pool_show_must_be_carried_on_the_channel(make_catalog: MakeCatalog) -> None:
    errors = _validate(make_catalog, templates=BASE.replace("show: x-men", "show: arthur"))
    assert "ktvu-base: pool show 'arthur' is not carried on ktvu" in errors


def test_pool_tag_must_appear_in_carriage(make_catalog: MakeCatalog) -> None:
    carriage = """
carriage:
  - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 1990-01-01 }
"""
    templates = BASE.replace("{ show: x-men }", "{ tag: fox-toons }")
    errors = _validate(make_catalog, carriage=carriage, templates=templates)
    assert "ktvu-base: pool tag 'fox-toons' unused in ktvu carriage" in errors


def test_pool_tag_used_in_carriage_is_valid(make_catalog: MakeCatalog) -> None:
    carriage = """
carriage:
  - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 1990-01-01 }
  - { show: x-men, title: X-Men, channel: ktvu, block: fox-kids, from: 1992-10-31, tags: [fox-toons] }
"""  # fmt: skip
    templates = BASE.replace("{ show: x-men }", "{ tag: fox-toons }")
    errors = _validate(make_catalog, carriage=carriage, templates=templates)
    assert "ktvu-base: pool tag 'fox-toons' unused in ktvu carriage" not in errors
