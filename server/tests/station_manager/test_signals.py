"""Viewing signals (M5): deterministic summaries of the watch sessions in a window."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from retro_cable.station_manager.signals import (
    EARLY_SECONDS,
    Session,
    compute,
    power_window,
)

NY = ZoneInfo("America/New_York")
# Sunday Nov 1 2026, 8:00 PM in New York (EST).
EVENING = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


def session(
    channel: str = "ktvu",
    show: str = "the-simpsons",
    seconds: int = 1320,
    start: datetime = EVENING,
    runtime: int = 1320,
    offset: int = 0,
    ended: str | None = "program_end",
    year: int = 1995,
) -> Session:
    return Session(
        channel=channel, item=show, kind="episode", sim_date=date(year, 11, 1),
        started_at=start, seconds=seconds, offset=offset, runtime=runtime, ended=ended,
    )  # fmt: skip


def test_per_show_watch_time_completion_and_early_tune_aways() -> None:
    sessions = [
        session(seconds=1320),
        session(seconds=660, offset=660),  # tuned in halfway and stayed: complete
        session(show="x-men", seconds=100, ended="tune_away"),
        session(show="x-men", seconds=150, ended="tune_away"),
        session(show="x-men", seconds=1320),
        session(show="x-men", seconds=5, ended="tune_away"),  # surfing past: ignored
    ]
    sig = compute(sessions, NY)
    by = {(s.channel, s.show): s for s in sig.shows}
    simpsons, xmen = by[("ktvu", "the-simpsons")], by[("ktvu", "x-men")]
    assert simpsons.seconds == 1980 and simpsons.sessions == 2
    assert simpsons.completion == 1.0 and simpsons.early_tune_aways == 0
    assert xmen.sessions == 3 and xmen.early_tune_aways == 2
    assert xmen.completion == round((100 + 150 + 1320) / (3 * 1320), 3)
    assert sig.total_seconds == 1980 + 1570
    assert sig.shows[0].show == "the-simpsons"  # most watched first
    [ktvu] = sig.channels
    assert ktvu.seconds == 3550 and ktvu.sessions == 5 and ktvu.mean_seconds == 710
    assert sig.year == 1995


def test_heatmap_is_hour_of_week_in_the_configured_zone_and_splits_across_hours() -> None:
    # 7:50 PM to 8:12 PM on a Sunday in New York.
    sig = compute([session(seconds=1320, start=EVENING - timedelta(minutes=10))], NY)
    sunday = sig.heatmap[6]
    assert sunday[19] == 600 and sunday[20] == 720
    assert sum(map(sum, sig.heatmap)) == 1320
    assert sig.busiest_hours[0] == 20


def test_power_window_is_the_busiest_hour_widened_to_a_busy_neighbor() -> None:
    nights = [session(start=EVENING + timedelta(days=d)) for d in range(4)]  # 8:00-8:22
    sig = compute(nights, NY)
    assert power_window(sig) == ("20:00", 1)
    late = [session(start=EVENING + timedelta(days=d, minutes=40)) for d in range(4)]
    sig = compute(nights + late, NY)  # 8:40-9:02 spills into nine o'clock a little
    assert power_window(sig) == ("20:00", 1)
    later = [session(start=EVENING + timedelta(days=d, hours=1)) for d in range(4)]
    assert power_window(compute(nights + later, NY)) == ("20:00", 2)
    assert power_window(compute([], NY)) is None


def test_most_watched_year_breaks_ties_toward_the_latest() -> None:
    sig = compute([session(year=1995), session(year=1999)], NY)
    assert sig.year == 1999


@settings(max_examples=50, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(0, 4 * 7 * 24 * 3600), st.integers(0, 3 * 3600), st.integers(1, 3 * 3600)
        ),
        max_size=30,
    )
)
def test_signals_are_deterministic_and_add_up(rows: list[tuple[int, int, int]]) -> None:
    sessions = [
        session(start=EVENING + timedelta(seconds=t), seconds=min(s, r), runtime=r)
        for t, s, r in rows
    ]
    a, b = compute(sessions, NY), compute(list(reversed(sessions)), NY)
    assert a == b
    assert sum(map(sum, a.heatmap)) == a.total_seconds
    assert a.total_seconds == sum(s.seconds for s in a.shows)
    assert all(0 <= s.completion <= 1 for s in a.shows)
    assert all(s.early_tune_aways <= s.sessions for s in a.shows)
    assert EARLY_SECONDS == 180
