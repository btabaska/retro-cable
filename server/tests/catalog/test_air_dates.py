from datetime import date

from retro_cable.catalog import effective_dates, is_library_show, load_catalog, shift_air_dates
from tests.conftest import OVERRIDES, SYNTHETIC, MakeCatalog


def test_no_anchor_keeps_original_dates() -> None:
    dates = [date(1999, 1, 1), date(1999, 1, 8)]
    assert shift_air_dates(dates, None) == dates


def test_anchor_moves_the_first_episode_and_keeps_spacing() -> None:
    original = [date(2012, 6, 15), date(2012, 6, 29), None, date(2012, 7, 13)]
    assert shift_air_dates(original, date(2004, 6, 15)) == [
        date(2004, 6, 15),
        date(2004, 6, 29),
        None,
        date(2004, 7, 13),
    ]


def test_all_missing_dates_stay_missing() -> None:
    assert shift_air_dates([None, None], date(2004, 1, 1)) == [None, None]


def test_effective_dates_apply_episode_overrides_then_the_anchor(make_catalog: MakeCatalog) -> None:
    overrides = OVERRIDES + (
        "  - show: dexters-laboratory\n    episodes:\n      S1E2: 1996-05-05\n"
    )
    catalog = load_catalog(make_catalog(overrides=overrides))
    episodes = [(1, 1, date(1996, 4, 28)), (1, 2, None), (1, 3, date(1996, 5, 12))]
    assert effective_dates(catalog, "dexters-laboratory", episodes) == [
        date(1996, 4, 28),
        date(1996, 5, 5),
        date(1996, 5, 12),
    ]


def test_effective_dates_use_us_debut_and_synthetic_premiere(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    dbz = [(1, 1, date(1989, 4, 26)), (1, 2, date(1989, 5, 3))]
    assert effective_dates(catalog, "dragon-ball-z", dbz) == [date(1996, 9, 13), date(1996, 9, 20)]
    gf = [(1, 1, date(2012, 6, 15))]
    assert effective_dates(catalog, "gravity-falls", gf) == [date(2004, 6, 15)]


def test_library_shows(make_catalog: MakeCatalog) -> None:
    synthetic = SYNTHETIC + "  - { show: dexters-laboratory, library: true }\n"
    catalog = load_catalog(make_catalog(synthetic=synthetic))
    assert is_library_show(catalog, "dexters-laboratory")
    assert not is_library_show(catalog, "gravity-falls")
