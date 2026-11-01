from datetime import date

from retro_cable.catalog import shift_air_dates


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
