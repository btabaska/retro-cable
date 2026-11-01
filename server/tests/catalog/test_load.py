from datetime import date
from pathlib import Path

import pytest

from retro_cable.catalog import CatalogError, load_catalog
from tests.catalog.conftest import CARRIAGE, CHANNELS, MakeCatalog


def test_loads_every_file(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    assert [c.id for c in catalog.channels.channels] == ["ktvu", "cartoon-network", "disney-movies"]
    toon = catalog.channel("cartoon-network")
    assert toon.launch == date(1992, 10, 1)
    assert toon.identity[0].call_sign == "TOON"
    assert catalog.holidays.holidays[1].rule.type == "nth_weekday"
    assert catalog.carriage.carriage[1].block == "fox-kids"
    assert catalog.synthetic.synthetic[0].premiere == date(2004, 6, 15)
    assert catalog.overrides.shows[0].us_debut == date(1996, 9, 13)


def test_optional_files_default_to_empty(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog(carriage=None, synthetic=None, overrides=None))
    assert catalog.carriage.carriage == []
    assert catalog.synthetic.synthetic == []
    assert catalog.overrides.shows == []


def test_channels_file_is_required(make_catalog: MakeCatalog) -> None:
    with pytest.raises(CatalogError, match=r"channels\.yaml"):
        load_catalog(make_catalog(channels=None))


def test_unknown_keys_are_rejected_with_the_file_and_path(make_catalog: MakeCatalog) -> None:
    bad = CHANNELS.replace("    authenticity: 100", "    authenticty: 100")
    with pytest.raises(CatalogError) as err:
        load_catalog(make_catalog(channels=bad))
    assert "channels.yaml" in str(err.value)
    assert "authenticty" in str(err.value)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "type: network\n    identity:\n      - { from: 1990",
            "type: cable\n    identity:\n      - { from: 1990",
        ),
        ("number: 27", "number: 0"),
        ("authenticity: 100", "authenticity: 101"),
        ("launch: 1992-10-01", "launch: 1992-13-01"),
    ],
)
def test_schema_violations_are_rejected(make_catalog: MakeCatalog, old: str, new: str) -> None:
    assert old in CHANNELS
    with pytest.raises(CatalogError):
        load_catalog(make_catalog(channels=CHANNELS.replace(old, new, 1)))


def test_unquoted_on_is_explained(make_catalog: MakeCatalog) -> None:
    holidays = "holidays:\n  - { id: x, name: X, rule: { type: fixed, month: 1, day: 1 }, window_days: 1, strength: low, default_mode: on }\n"
    with pytest.raises(CatalogError, match="default_mode"):
        load_catalog(make_catalog(holidays=holidays))


def test_dates_stay_strings_until_validated(make_catalog: MakeCatalog) -> None:
    # YAML would turn 2001-09-02 into a date and 1:30 into a sexagesimal int; we read both as text.
    carriage = CARRIAGE + "  - { show: futurama, title: '1:30', channel: ktvu, from: 1999-03-28 }\n"
    catalog = load_catalog(make_catalog(carriage=carriage))
    assert catalog.carriage.carriage[-1].title == "1:30"


def test_version_is_a_stable_hash_of_the_inputs(make_catalog: MakeCatalog, tmp_path: Path) -> None:
    first = load_catalog(make_catalog()).version
    assert len(first) == 12
    assert load_catalog(make_catalog()).version == first
    changed = load_catalog(make_catalog(carriage=CARRIAGE.replace("1996-04-28", "1996-04-29")))
    assert changed.version != first
