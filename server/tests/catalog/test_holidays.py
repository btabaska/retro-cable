from datetime import date
from pathlib import Path

import pytest

from retro_cable.catalog import load_catalog
from retro_cable.catalog.holidays import Tagger, holiday_date, mode_for, season, window
from retro_cable.catalog.models import Holiday

REPO_CATALOG = Path(__file__).resolve().parents[3] / "catalog"


@pytest.fixture(scope="module")
def tagger() -> Tagger:
    return Tagger(load_catalog(REPO_CATALOG))


def holiday(tagger: Tagger, hid: str) -> Holiday:
    return tagger.holidays[hid]


def test_fixed_and_nth_weekday_dates(tagger: Tagger) -> None:
    assert holiday_date(holiday(tagger, "halloween"), 1998) == date(1998, 10, 31)
    # Fourth Thursday of November.
    assert holiday_date(holiday(tagger, "thanksgiving"), 1998) == date(1998, 11, 26)
    assert holiday_date(holiday(tagger, "thanksgiving"), 2001) == date(2001, 11, 22)


def test_last_weekday_rule() -> None:
    memorial = Holiday.model_validate(
        {
            "id": "memorial-day",
            "name": "Memorial Day",
            "rule": {"type": "nth_weekday", "n": -1, "weekday": "monday", "month": 5},
            "window_days": 1,
            "strength": "low",
            "default_mode": "off",
        }
    )
    assert holiday_date(memorial, 1998) == date(1998, 5, 25)
    assert holiday_date(memorial, 2004) == date(2004, 5, 31)


def test_halloween_window_is_the_seven_days_up_to_it(tagger: Tagger) -> None:
    # Oct 31 1998 was a Saturday; window_days 7 gives Oct 25-31.
    assert window(holiday(tagger, "halloween"), 1998) == (date(1998, 10, 25), date(1998, 10, 31))
    # The season is the whole month up to the holiday.
    assert season(holiday(tagger, "halloween"), 1998) == (date(1998, 10, 1), date(1998, 10, 31))
    # Christmas' 25-day window already starts on the 1st.
    assert season(holiday(tagger, "christmas"), 2003) == (date(2003, 12, 1), date(2003, 12, 25))


def test_calendar_on_a_date(tagger: Tagger) -> None:
    oct31 = tagger.on(date(1998, 10, 31))
    assert [h.id for h in oct31.window] == ["halloween"]
    assert tagger.on(date(1998, 10, 10)).window == ()
    assert "halloween" in tagger.on(date(1998, 10, 10)).season
    assert tagger.on(date(1998, 11, 5)).window == ()
    assert "halloween" not in tagger.on(date(1998, 11, 5)).season


def test_channel_modes(tagger: Tagger) -> None:
    stpat = holiday(tagger, "st-patricks")
    assert mode_for(stpat, "disney") == "on"
    assert mode_for(stpat, "ktvu") == "specials_only"
    assert mode_for(holiday(tagger, "halloween"), "ktvu") == "on"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Treehouse of Horror IX", {"halloween"}),
        ("Halloween Spook-tacular", {"halloween"}),
        ("It's the Great Pumpkin, Charlie Brown", {"halloween"}),
        ("A Charlie Brown Christmas", {"christmas"}),
        ("Marge Be Not Proud", set()),
        ("Santa Barbara Blues", set()),
        ("A Charlie Brown Thanksgiving", {"thanksgiving"}),
        ("Be My Valentine, Charlie Brown", {"valentines"}),
        ("Leprechaun's Gold", {"st-patricks"}),
    ],
)
def test_keyword_tags(tagger: Tagger, title: str, expected: set[str]) -> None:
    assert tagger.keyword_tags(title) == expected


def test_movies_tag_from_their_title_and_tagged_items(tagger: Tagger) -> None:
    assert tagger.tags("halloweentown", "movie", "Halloweentown") == {"halloween"}
    # Under Wraps has no keyword; holidays.yaml lists it.
    assert tagger.tags("under-wraps", "movie", "Under Wraps") == {"halloween"}
    assert tagger.tags("the-luck-of-the-irish", "movie", "The Luck of the Irish") == {"st-patricks"}
    assert tagger.tags("brink", "movie", "Brink!") == frozenset()


def test_overrides_replace_the_tagger(make_catalog: object) -> None:
    from collections.abc import Callable

    make: Callable[..., Path] = make_catalog  # type: ignore[assignment]
    catalog = load_catalog(
        make(
            overrides="""
            shows:
              - show: the-simpsons
                holiday_tags:
                  S10E04: [halloween]
                  S8E1: []
            """
        )
    )
    t = Tagger(catalog)
    assert t.tags("the-simpsons", "episode", "The Simpsons", "Episode 4", 10, 4) == {"halloween"}
    # An empty list untags an episode the keywords would tag.
    assert t.tags("the-simpsons", "episode", "The Simpsons", "Treehouse of Horror VII", 8, 1) == (
        frozenset()
    )


def test_seasonal_items(tagger: Tagger) -> None:
    assert tagger.is_seasonal("halloweentown", "movie", "Halloweentown", {"halloween"})
    assert not tagger.is_seasonal("brink", "movie", "Brink!", set())
    # Peanuts specials are marked seasonal in overrides.yaml; a regular show's holiday episode
    # is not seasonal (Treehouse of Horror reruns aired all year).
    assert tagger.is_seasonal(
        "peanuts-holiday-specials", "episode", "Peanuts holiday specials", {"christmas"}
    )
    assert not tagger.is_seasonal("the-simpsons", "episode", "The Simpsons", {"halloween"})
