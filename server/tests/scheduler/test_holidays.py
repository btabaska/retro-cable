"""Phase 7 "Feel": holiday windows, specials in prime, seasonal items, bumpers and NEW."""

import itertools
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from retro_cable.catalog import Catalog
from retro_cable.scheduler import Scheduler, Slot
from tests.conftest import MakeCatalog
from tests.scheduler.holiday_fixtures import days, holiday_catalog, holiday_library

NY = ZoneInfo("America/New_York")


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), NY)


@pytest.fixture
def hcat(make_catalog: MakeCatalog) -> Catalog:
    return holiday_catalog(make_catalog)


@pytest.fixture
def sched(hcat: Catalog) -> Scheduler:
    return Scheduler(hcat, holiday_library(), seed=11)


def tagged(s: Slot, holiday: str = "halloween") -> bool:
    return s.holiday == holiday


OCT31 = date(1998, 10, 31)


def test_halloweentown_airs_once_in_prime_on_oct_31_1998(sched: Scheduler) -> None:
    day = sched.day("disney", OCT31)
    airings = [s for s in day if s.show == "halloweentown"]
    assert len(airings) == 1
    movie = airings[0]
    assert at(OCT31, 19) <= movie.start.astimezone(NY) < at(OCT31, 22)
    assert movie.seconds == 84 * 60  # full length
    assert movie.holiday == "halloween"


@pytest.mark.parametrize("channel", ["disney", "disney-movies"])
def test_halloweentown_never_airs_before_its_premiere(sched: Scheduler, channel: str) -> None:
    # Premiered 1998-10-17; Disney Movies is a library channel but movies keep their dates.
    # The broadcast day of Oct 16 runs into the early hours of Oct 17, the premiere date.
    premiere = date(1998, 10, 17)
    for d in days(date(1998, 10, 1), 16):
        for s in sched.day(channel, d):
            if s.show == "halloweentown":
                assert s.start.astimezone(NY).date() >= premiere
    assert not any(s.show == "halloweentown" for s in sched.day(channel, date(1998, 10, 10)))


def test_pooled_show_airs_its_tagged_episodes_in_the_window(sched: Scheduler) -> None:
    slots = sched.between("ktvu", at(OCT31, 20), at(OCT31, 23))
    simpsons = [s for s in slots if s.show == "the-simpsons"]
    assert simpsons
    assert all(tagged(s) for s in simpsons), [s.episode_title for s in simpsons]


def test_tagged_episodes_get_no_preference_after_the_window(make_catalog: MakeCatalog) -> None:
    # Outside every window a day is exactly what it would be with no holidays at all.
    simpsons_only = """
    carriage:
      - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 1990-01-01 }
    """
    with_holidays = holiday_catalog(make_catalog, carriage=simpsons_only)
    without = holiday_catalog(make_catalog, carriage=simpsons_only, holidays="holidays: []")
    a = Scheduler(with_holidays, holiday_library(), seed=3)
    b = Scheduler(without, holiday_library(), seed=3)
    for d in (date(1998, 11, 5), date(1999, 6, 2)):
        a_eps = [(s.start, s.show, s.episode) for s in a.day("ktvu", d)]
        b_eps = [(s.start, s.show, s.episode) for s in b.day("ktvu", d)]
        assert a_eps == b_eps
    # Inside the window they differ: tagged episodes are preferred.
    a_oct = [s.episode for s in a.day("ktvu", OCT31)]
    b_oct = [s.episode for s in b.day("ktvu", OCT31)]
    assert a_oct != b_oct
    assert not any(s.holiday for s in a.day("ktvu", date(1998, 11, 5)))


def test_mode_off_injects_nothing(sched: Scheduler) -> None:
    # Nick has Halloween off: no preference for tagged Simpsons, no special injection.
    slots = sched.between("nick", at(OCT31, 19), at(OCT31, 23))
    simpsons = [s for s in slots if s.show == "the-simpsons"]
    assert simpsons and not all("Treehouse" in s.episode_title for s in simpsons)


def test_specials_only_injects_movies_but_does_not_prefer_episodes(sched: Scheduler) -> None:
    # St. Patrick's Day: disney "on" injects The Luck of the Irish into prime; ktvu is off.
    stpat = date(2002, 3, 17)
    disney = sched.between("disney", at(stpat, 19), at(stpat, 23))
    assert any(s.show == "the-luck-of-the-irish" for s in disney)
    assert sum(1 for s in sched.day("disney", stpat) if s.show == "the-luck-of-the-irish") == 1


def test_specials_only_mode(make_catalog: MakeCatalog) -> None:
    catalog = holiday_catalog(
        make_catalog,
        holidays="""
        holidays:
          - id: halloween
            name: Halloween
            rule: { type: fixed, month: 10, day: 31 }
            window_days: 7
            strength: strong
            default_mode: specials_only
        """,
    )
    sched = Scheduler(catalog, holiday_library(), seed=11)
    disney = sched.between("disney", at(OCT31, 19), at(OCT31, 22))
    assert any(s.show == "halloweentown" for s in disney)
    ktvu = [s for s in sched.between("ktvu", at(OCT31, 19), at(OCT31, 23)) if s.show]
    simpsons = [s for s in ktvu if s.show == "the-simpsons"]
    assert simpsons and not all("Treehouse" in s.episode_title for s in simpsons)


def test_seasonal_specials_stay_in_their_season(sched: Scheduler) -> None:
    for d in days(date(1998, 7, 1), 14):
        assert not any(s.show == "peanuts-holiday-specials" for s in sched.day("ktvu", d))
    october = [
        s
        for d in days(date(1998, 10, 1), 31)
        for s in sched.day("ktvu", d)
        if s.show == "peanuts-holiday-specials"
    ]
    assert october
    assert all(s.episode_title == "It's the Great Pumpkin, Charlie Brown" for s in october)


def test_holiday_movies_stay_in_season(sched: Scheduler) -> None:
    # Halloweentown is a seasonal movie: never on Disney Channel in July.
    for d in days(date(1999, 7, 1), 10):
        assert not any(s.show == "halloweentown" for s in sched.day("disney", d))


def test_exactly_one_bumper_between_programs(sched: Scheduler) -> None:
    slots = sched.day("disney", OCT31)
    programs = [s for s in slots if s.kind in ("episode", "movie")]
    assert programs
    for a, b in itertools.pairwise(slots):
        assert not (a.kind == "bumper" and b.kind == "bumper")
        if a.kind in ("episode", "movie") and b.kind in ("episode", "movie"):
            # Only allowed when a bumper would not fit before 5 a.m.
            assert b.end >= at(OCT31 + timedelta(days=1), 5) - timedelta(seconds=10)
    five = at(OCT31 + timedelta(days=1), 5)
    for s in slots:
        if s.kind == "bumper":
            assert s.end <= five


def test_no_bumpers_without_a_pool(make_catalog: MakeCatalog, hcat: Catalog) -> None:
    from retro_cable.scheduler import Library

    lib = holiday_library()
    bare = Library.build(dict(lib.episodes), dict(lib.movies), {})
    sched = Scheduler(hcat, bare, seed=11)
    assert not any(s.kind == "bumper" for s in sched.day("disney", OCT31))


def test_slots_are_back_to_back_through_the_window(sched: Scheduler) -> None:
    for channel in ("ktvu", "disney", "disney-movies", "nick"):
        for d in days(date(1998, 10, 24), 9):
            slots = sched.day(channel, d)
            assert slots[0].start == at(d, 5)
            assert slots[-1].end == at(d + timedelta(days=1), 5)
            for a, b in itertools.pairwise(slots):
                assert a.end == b.start
