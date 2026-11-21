"""Whatever the model proposes, the station manager's result keeps the scheduler invariants
once applied and stays inside its guardrails: inputs only, the authenticity cap, at most a
fifth of a channel's weights per run (M5)."""

import itertools
import math
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from retro_cable.admin.drafts import preview_week
from retro_cable.catalog import Catalog, load_catalog
from retro_cable.catalog.models import Tuning
from retro_cable.catalog.overlay import Overlay, apply_overlay, with_tuning
from retro_cable.scheduler import Scheduler
from retro_cable.station_manager.manager import check_channels
from retro_cable.station_manager.signals import Session, compute, power_window
from retro_cable.station_manager.tuning import (
    CHANGE_SHARE,
    Proposed,
    drift_cap,
    guard,
    inputs_only,
    knobs,
)
from tests.conftest import MakeCatalog
from tests.scheduler.conftest import make_library

NY = ZoneInfo("America/New_York")
START = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
PAIRS = [
    ("ktvu", "the-simpsons"),
    ("ktvu", "x-men"),
    ("cartoon-network", "dexters-laboratory"),
    ("cartoon-network", "dragon-ball-z"),
    ("cartoon-network", "gravity-falls"),
    ("disney-movies", "halloweentown"),
]


@pytest.fixture
def catalog(make_catalog: MakeCatalog) -> Catalog:
    return load_catalog(make_catalog())


sessions = st.lists(
    st.builds(
        lambda pair, at, secs, ended, year: Session(
            channel=pair[0],
            item=pair[1],
            kind="episode",
            sim_date=date(year, 6, 1),
            started_at=START + timedelta(minutes=at),
            seconds=secs,
            offset=0,
            runtime=1320,
            ended=ended,
        ),
        st.sampled_from(PAIRS),
        st.integers(0, 4 * 7 * 24 * 60),
        st.integers(0, 1320),
        st.sampled_from([None, "tune_away", "power_off", "program_end"]),
        st.integers(1990, 2010),
    ),
    min_size=1,
    max_size=40,
)


@st.composite
def replies(draw: st.DrawFn, catalog: Catalog) -> Proposed:
    table = sorted(knobs(catalog, Tuning()))
    picked = draw(st.lists(st.sampled_from(table), unique=True, max_size=len(table)))
    factors = {k: draw(st.floats(0.01, 50, allow_nan=False)) for k in picked}
    channels = sorted({c for c, _ in PAIRS})
    power = {
        c: [(s, draw(st.floats(0.01, 10))) for s in draw(st.lists(st.sampled_from(
            [s for ch, s in PAIRS if ch == c] + ["not-carried"]), max_size=5))]
        for c in draw(st.lists(st.sampled_from(channels), unique=True))
    }  # fmt: skip
    return Proposed(factors=factors, power=power)


@SETTINGS
@given(data=st.data(), watched=sessions, day=st.dates(date(1990, 1, 1), date(2010, 12, 31)))
def test_an_applied_run_keeps_the_invariants_and_its_guardrails(
    catalog: Catalog, data: st.DataObject, watched: list[Session], day: date
) -> None:
    proposed = data.draw(replies(catalog))
    seed = data.draw(st.integers(0, 20))
    signals = compute(watched, NY)
    guarded = guard(proposed, catalog, Tuning(), signals, power_window(signals))
    library = make_library()
    week = preview_week(date(signals.year or 2000, 6, 1))
    final, failed = check_channels(
        catalog, Overlay(), Tuning(), guarded.tuning, guarded.channels(Tuning()), library,
        seed, week,
    )  # fmt: skip
    event(f"changed channels: {len(guarded.channels(Tuning()))}, failed: {len(failed)}")
    merged = apply_overlay(catalog, with_tuning(Overlay(), final))

    # Guardrails: inputs only; the authenticity cap; at most a fifth of each channel's knobs.
    assert inputs_only(catalog, merged) == []
    table = knobs(catalog, Tuning())
    dials = {c.id: c.authenticity for c in catalog.channels.channels}
    tuned = knobs(catalog, final)
    for k in tuned.values():
        cap = drift_cap(dials[k.channel])
        assert 1 / cap - 0.01 <= k.factor <= cap + 0.01
    for channel_id in dials:
        mine = [kid for kid, k in table.items() if k.channel == channel_id]
        moved = [kid for kid in mine if tuned[kid].factor != 1]
        assert len(moved) <= math.ceil(len(mine) * CHANGE_SHARE)
    for ph in merged.power_hours:
        assert dials[ph.channel] > 0 and len(ph.pool) <= 4
        assert all(1 <= e.weight <= 3 for e in ph.pool)

    # The scheduler invariants, on every channel, on any day.
    sched = Scheduler(merged, library, seed=seed)
    again = Scheduler(merged, make_library(), seed=seed)
    lengths = {
        (p.show, p.season, p.episode): p.seconds for eps in library.episodes.values() for p in eps
    }
    lengths |= {(p.show, None, None): p.seconds for p in library.movies.values()}
    for channel in merged.channels.channels:
        on_air = sched.on_air(channel, day)
        if channel.launch and not channel.library:
            assert on_air == (day >= channel.launch)
        if not on_air:
            continue
        slots = sched.day(channel.id, day)
        assert slots[0].start == datetime.combine(day, time(5), NY)
        assert slots[-1].end == datetime.combine(day + timedelta(days=1), time(5), NY)
        for a, b in itertools.pairwise(slots):
            assert a.end == b.start
        for s in slots:
            if s.kind in ("episode", "movie"):
                assert s.air_date is not None and s.air_date <= s.start.astimezone(NY).date()
                assert s.seconds == lengths[(s.show or "", s.season, s.episode)]
        assert again.day(channel.id, day) == slots
