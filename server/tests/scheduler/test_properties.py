"""Scheduler invariants (CLAUDE.md) as Hypothesis properties."""

import itertools
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from retro_cable.catalog import Catalog
from retro_cable.scheduler import Scheduler
from tests.scheduler.conftest import make_library

NY = ZoneInfo("America/New_York")
CHANNELS = ["ktvu", "cartoon-network", "disney-movies"]
days = st.dates(min_value=date(1990, 1, 1), max_value=date(2010, 12, 31))
# DST changes in the 1990-2010 rules: first Sunday of April / last Sunday of October until
# 2006, second Sunday of March / first Sunday of November after.
dst_days = st.sampled_from(
    [date(1998, 4, 5), date(1998, 10, 25), date(2007, 3, 11), date(2007, 11, 4), date(2006, 10, 29)]
)
SETTINGS = settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


def _on_air(sched: Scheduler, channel: str, day: date) -> bool:
    return channel in {c.id for c in sched.channels_on(day)}


@SETTINGS
@given(day=days | dst_days, channel=st.sampled_from(CHANNELS), seed=st.integers(0, 1000))
def test_days_are_back_to_back(catalog: Catalog, day: date, channel: str, seed: int) -> None:
    sched = Scheduler(catalog, make_library(), seed=seed)
    if not _on_air(sched, channel, day):
        return
    slots = sched.day(channel, day)
    start = datetime.combine(day, time(5), NY)
    end = datetime.combine(day + timedelta(days=1), time(5), NY)
    assert slots[0].start == start and slots[-1].end == end
    total = timedelta()
    for a, b in itertools.pairwise(slots):
        assert a.end == b.start
    for s in slots:
        assert s.end > s.start
        total += s.end - s.start
    assert total == end.astimezone(UTC) - start.astimezone(
        UTC
    )  # real elapsed time, so 23 or 25 hours on DST days


@SETTINGS
@given(day=days, channel=st.sampled_from(CHANNELS), seed=st.integers(0, 1000))
def test_nothing_airs_before_its_date(catalog: Catalog, day: date, channel: str, seed: int) -> None:
    sched = Scheduler(catalog, make_library(), seed=seed)
    if not _on_air(sched, channel, day):
        return
    for s in sched.day(channel, day):
        if s.kind in ("episode", "movie"):
            aired_by = s.start.astimezone(NY).date()
            assert s.air_date is not None and s.air_date <= aired_by


@SETTINGS
@given(day=days, channel=st.sampled_from(CHANNELS), seed=st.integers(0, 1000))
def test_deterministic(catalog: Catalog, day: date, channel: str, seed: int) -> None:
    a = Scheduler(catalog, make_library(), seed=seed)
    b = Scheduler(catalog, make_library(), seed=seed)
    if not _on_air(a, channel, day):
        return
    assert a.day(channel, day) == b.day(channel, day)


@SETTINGS
@given(day=days, seed=st.integers(0, 1000))
def test_hidden_channels_never_scheduled(catalog: Catalog, day: date, seed: int) -> None:
    sched = Scheduler(catalog, make_library(), seed=seed)
    launch = catalog.channel("cartoon-network").launch
    assert launch is not None
    assert _on_air(sched, "cartoon-network", day) == (day >= launch)


@SETTINGS
@given(day=days, seed=st.integers(0, 1000))
def test_midnight_is_continuous(catalog: Catalog, day: date, seed: int) -> None:
    sched = Scheduler(catalog, make_library(), seed=seed)
    midnight = datetime.combine(day + timedelta(days=1), time(0), NY)
    before = sched.between("ktvu", midnight - timedelta(hours=1), midnight)
    after = sched.between("ktvu", midnight, midnight + timedelta(hours=1))
    # The program running at midnight is the same slot seen from either side.
    assert before[-1] == after[0] or before[-1].end == after[0].start
