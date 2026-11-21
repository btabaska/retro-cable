"""All Years (Nov 10 2026): every channel on the dial, every episode on disk, whatever its date."""

import itertools
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from retro_cable.catalog import Catalog, load_catalog
from retro_cable.scheduler import Library, Scheduler, Slot
from tests.conftest import MakeCatalog
from tests.scheduler.conftest import HALF, make_library, weekly

NY = ZoneInfo("America/New_York")
EARLY = date(1991, 6, 7)  # a Friday before Cartoon Network launched and Dexter debuted
LATE = date(2026, 11, 6)  # a Friday long after X-Men left KTVU's Fox Kids block


def at(d: date, hh: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, tzinfo=NY)


def week(s: Scheduler, channel: str, first: date) -> list[Slot]:
    return [x for i in range(7) for x in s.day(channel, first + timedelta(days=i))]


def test_every_channel_is_on_the_dial(catalog: Catalog) -> None:
    s = Scheduler(catalog, make_library(), seed=7, all_years=True)
    assert {c.id for c in s.channels_on(EARLY)} == {c.id for c in catalog.channels.channels}
    assert s.on_air(catalog.channel("cartoon-network"), EARLY)
    assert any(x.kind == "episode" for x in s.day("cartoon-network", EARLY))


def test_episodes_air_whatever_their_date(catalog: Catalog) -> None:
    s = Scheduler(catalog, make_library(), seed=7, all_years=True)
    cn = week(s, "cartoon-network", EARLY)
    assert {"dexters-laboratory", "dragon-ball-z", "gravity-falls"} <= {x.show for x in cn}
    later = [x for x in week(s, "ktvu", EARLY) if x.show == "the-simpsons"]
    assert any(x.air_date is not None and x.air_date > EARLY for x in later)


def test_carriage_dates_blocks_and_templates_are_lifted(catalog: Catalog) -> None:
    """X-Men was carried only in Fox Kids, 1992-1997. In All Years it airs on KTVU any time
    of day, as part of one all-day rotation of everything the channel ever carried."""
    normal = Scheduler(catalog, make_library(), seed=7)
    assert not any(x.show == "x-men" for x in week(normal, "ktvu", LATE))
    s = Scheduler(catalog, make_library(), seed=7, all_years=True)
    xmen = [x for x in week(s, "ktvu", LATE) if x.show == "x-men"]
    assert any(x.start.astimezone(NY).hour < 16 for x in xmen)


def test_a_channel_keeps_its_latest_name_and_number(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(
        make_catalog(
            channels="""
        timezone: America/New_York
        channels:
          - id: nbc
            name: NBC
            type: network
            identity:
              - { from: 1990-01-01, until: 2001-12-31, number: 4, call_sign: KRON }
              - { from: 2002-01-01, number: 11, call_sign: KNTV }
        """,
            carriage="carriage: []",
            templates="templates: []",
            synthetic="synthetic: []",
            overrides="shows: []",
            holidays="holidays: []",
        )
    )
    s = Scheduler(catalog, make_library(), seed=0, all_years=True)
    assert s.identity("nbc", EARLY).call_sign == "KNTV"
    assert s.identity("nbc", EARLY).number == 11


def test_nothing_is_new(catalog: Catalog) -> None:
    base = make_library()
    episodes = dict(base.episodes)
    episodes["the-simpsons"] = tuple(
        weekly("the-simpsons", "The Simpsons", date(1997, 10, 3), 10, HALF)
    )
    library = Library.build(episodes, base.movies, base.bumpers)
    s = Scheduler(catalog, library, seed=3, all_years=True)
    assert not any(x.new for x in s.day("ktvu", date(1997, 10, 31)))


def test_days_run_back_to_back_and_are_deterministic(catalog: Catalog) -> None:
    a = Scheduler(catalog, make_library(), seed=7, all_years=True)
    b = Scheduler(catalog, make_library(), seed=7, all_years=True)
    for channel in ("ktvu", "cartoon-network", "disney-movies"):
        slots = a.between(channel, at(LATE, 22), at(LATE + timedelta(days=1), 7))
        assert slots == b.between(channel, at(LATE, 22), at(LATE + timedelta(days=1), 7))
        for x, y in itertools.pairwise(slots):
            assert x.end == y.start and x.end > x.start
