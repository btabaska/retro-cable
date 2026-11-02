"""Scheduler invariants (CLAUDE.md) as Hypothesis properties."""

import itertools
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from retro_cable.catalog import Catalog
from retro_cable.catalog.holidays import in_season, in_window
from retro_cable.scheduler import Library, Program, Scheduler
from retro_cable.scheduler.engine import OFF_AIR, PAID_PROGRAMMING
from tests.conftest import MakeCatalog
from tests.scheduler.conftest import make_library
from tests.scheduler.holiday_fixtures import holiday_catalog, holiday_library

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


# --- holidays (Phase 7) -------------------------------------------------------------------

HOLIDAY_CHANNELS = ["ktvu", "disney", "disney-movies", "nick"]
HOLIDAY_ITEMS = {
    "halloweentown",
    "under-wraps",
    "the-luck-of-the-irish",
    "peanuts-holiday-specials",
}
holiday_days = st.dates(min_value=date(1997, 9, 1), max_value=date(2002, 12, 31)) | st.sampled_from(
    [date(1998, 10, 31), date(1998, 10, 25), date(2001, 3, 17), date(1999, 12, 24)]
)


@pytest.fixture
def hcat(make_catalog: MakeCatalog) -> Catalog:
    return holiday_catalog(make_catalog)


@SETTINGS
@given(day=holiday_days, channel=st.sampled_from(HOLIDAY_CHANNELS), seed=st.integers(0, 1000))
def test_holiday_windows_pull_only_tagged_items(
    hcat: Catalog, day: date, channel: str, seed: int
) -> None:
    sched = Scheduler(hcat, holiday_library(), seed=seed)
    holidays = {h.id: h for h in hcat.holidays.holidays}
    slots = sched.day(channel, day)
    assert slots[0].start == datetime.combine(day, time(5), NY)
    for a, b in itertools.pairwise(slots):
        assert a.end == b.start
    for s in slots:
        local = s.start.astimezone(NY).date()
        if s.kind in ("episode", "movie"):
            assert s.air_date is not None and s.air_date <= local
        # A holiday label names a holiday in force on that date.
        if s.holiday:
            h = holidays[s.holiday]
            assert in_window(h, local) or in_season(h, local)
        # Holiday movies and specials air only tagged for a holiday in season.
        if s.show in HOLIDAY_ITEMS:
            assert s.holiday is not None and in_season(holidays[s.holiday], local)


# --- Programs always air in full (Brandon, M1 review) ---------------------------------------

runtimes = st.integers(min_value=60, max_value=3 * 3600)


def _by_path(library: Library) -> dict[str, Program]:
    found = {p.path: p for eps in library.episodes.values() for p in eps}
    found.update({m.path: m for m in library.movies.values()})
    return found


def _odd_library(episode_seconds: list[int], movie_seconds: int) -> Library:
    """The test library with runtimes that line up with nothing: 7 min, 23:17, 2 h 41 ..."""
    base = make_library()
    episodes = {
        show: [
            replace(p, seconds=episode_seconds[(i + n) % len(episode_seconds)])
            for i, p in enumerate(eps)
        ]
        for n, (show, eps) in enumerate(base.episodes.items())
    }
    movies = {k: replace(m, seconds=movie_seconds) for k, m in base.movies.items()}
    return Library.build(episodes, movies, dict(base.bumpers))


def _assert_full_runtime(sched: Scheduler, library: Library, channel: str, day: date) -> None:
    programs = _by_path(library)
    bumper_cap = sched.catalog.channel(channel).bumper_seconds
    for s in sched.day(channel, day):
        if s.kind in ("episode", "movie"):
            assert s.path is not None
            program = programs[s.path]
            assert s.seconds == program.seconds, (
                f"{s.title} cut to {s.seconds}s of {program.seconds}s"
            )
        elif s.kind == "bumper":
            assert s.seconds <= bumper_cap
        else:
            assert s.kind == "filler" and s.title in (PAID_PROGRAMMING, OFF_AIR)


@SETTINGS
@given(day=days | dst_days, channel=st.sampled_from(CHANNELS), seed=st.integers(0, 1000))
def test_programs_always_air_in_full(catalog: Catalog, day: date, channel: str, seed: int) -> None:
    library = make_library()
    sched = Scheduler(catalog, library, seed=seed)
    if not _on_air(sched, channel, day):
        return
    _assert_full_runtime(sched, library, channel, day)


@SETTINGS
@given(
    day=days | dst_days,
    channel=st.sampled_from(CHANNELS),
    seed=st.integers(0, 1000),
    episode_seconds=st.lists(runtimes, min_size=1, max_size=6),
    movie_seconds=runtimes,
)
def test_odd_runtimes_still_air_in_full(
    catalog: Catalog,
    day: date,
    channel: str,
    seed: int,
    episode_seconds: list[int],
    movie_seconds: int,
) -> None:
    """Day parts, template blocks and the 5 a.m. day end flex around a program; they never
    truncate it. Only filler and bumpers absorb leftover time."""
    library = _odd_library(episode_seconds, movie_seconds)
    sched = Scheduler(catalog, library, seed=seed)
    if not _on_air(sched, channel, day):
        return
    _assert_full_runtime(sched, library, channel, day)


def test_a_program_in_a_block_runs_past_the_block_end(catalog: Catalog) -> None:
    """Day parts and blocks are soft: X-Men started in Fox Kids finishes after 8 p.m."""
    runtime = 97 * 60 + 13  # every episode 1:37:13
    library = _odd_library([runtime], 84 * 60)
    sched = Scheduler(catalog, library, seed=3)
    friday = date(1995, 3, 3)
    eight = datetime.combine(friday, time(20), NY)
    straddling = [s for s in sched.day("ktvu", friday) if s.start < eight < s.end]
    assert len(straddling) == 1
    (slot,) = straddling
    assert slot.kind == "episode" and slot.seconds == runtime
    _assert_full_runtime(sched, library, "ktvu", friday)
