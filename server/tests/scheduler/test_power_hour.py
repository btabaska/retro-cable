"""Power hours (M5): the station manager's day-part on Brandon's busiest hour, every day."""

from datetime import date, datetime, time

from retro_cable.catalog import Catalog
from retro_cable.catalog.models import PowerHour, Tuning
from retro_cable.catalog.overlay import Overlay, apply_overlay, with_tuning
from retro_cable.scheduler import Scheduler
from tests.scheduler.conftest import make_library


def with_power(catalog: Catalog, channel: str, start: str, *shows: str, hours: int = 1) -> Catalog:
    ph = PowerHour.model_validate(
        {"channel": channel, "start": start, "hours": hours, "pool": [{"show": s} for s in shows]}
    )
    return apply_overlay(catalog, with_tuning(Overlay(), Tuning(power_hours=[ph])))


def local_start(sched: Scheduler, slot_start: datetime) -> time:
    return slot_start.astimezone(sched.tz).time()


def test_programs_starting_in_the_power_hour_come_from_its_pool(catalog: Catalog) -> None:
    sched = Scheduler(with_power(catalog, "cartoon-network", "10:00", "dragon-ball-z"),
                      make_library(), seed=3)  # fmt: skip
    day = date(1999, 6, 4)
    inside = [
        s
        for s in sched.day("cartoon-network", day)
        if s.kind == "episode" and time(10) <= local_start(sched, s.start) < time(11)
    ]
    assert inside and all(s.show == "dragon-ball-z" for s in inside)
    plain = Scheduler(catalog, make_library(), seed=3).day("cartoon-network", day)
    assert sched.day("cartoon-network", day) != plain
    # Outside the power hour, the morning is the same as without it.
    cut = datetime.combine(day, time(9, 30), sched.tz)
    before = [s for s in plain if s.start < cut]
    assert list(sched.day("cartoon-network", day))[: len(before)] == before


def test_a_power_hour_with_nothing_to_air_yet_leaves_the_day_alone(catalog: Catalog) -> None:
    # X-Men is carried on KTVU only until 1997, so in 1999 the usual part plays.
    tuned = Scheduler(with_power(catalog, "ktvu", "20:00", "x-men"), make_library(), seed=3)
    plain = Scheduler(catalog, make_library(), seed=3)
    for day in (date(1999, 10, 1), date(1999, 10, 2)):
        assert tuned.day("ktvu", day) == plain.day("ktvu", day)


def test_a_power_hour_in_prime_keeps_prime(catalog: Catalog) -> None:
    # KTVU's Friday 20:00 part is prime time, so the power hour over it is too.
    sched = Scheduler(with_power(catalog, "ktvu", "20:00", "the-simpsons", hours=2),
                      make_library(), seed=1)  # fmt: skip
    day = date(1995, 10, 6)  # a Friday
    local = sched.tz
    part, _ = sched._power_hour("ktvu", datetime.combine(day, time(20, 30), local), None)
    assert part is not None and not part.primetime
    under = sched._active_part("ktvu", datetime.combine(day, time(20, 30), local))[0]
    part, end = sched._power_hour("ktvu", datetime.combine(day, time(20, 30), local), under)
    assert part is not None and part.primetime
    assert end is not None and end.astimezone(local).time() == time(22)
    assert sched._power_hour("ktvu", datetime.combine(day, time(22), local), under) == (None, None)
