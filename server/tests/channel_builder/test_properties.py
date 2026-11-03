"""Any proposal that passes the checks keeps the scheduler invariants once applied: days run
back to back from 5 a.m. to 5 a.m., nothing airs before its (pretend) date, programs air in
full, no channel airs before its launch, and the same inputs give the same day."""

import itertools
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from retro_cable.admin.drafts import LibraryItem
from retro_cable.catalog import Catalog, load_catalog
from retro_cable.catalog.overlay import Overlay, apply_overlay
from retro_cable.channel_builder.proposal import (
    ChannelType,
    DayPartPlan,
    Proposal,
    ProposedChannel,
    ProposedShow,
    check_proposal,
)
from retro_cable.scheduler import Library, Scheduler
from tests.conftest import MakeCatalog
from tests.scheduler.conftest import make_library

NY = ZoneInfo("America/New_York")
SHOWS = ["the-simpsons", "x-men", "dexters-laboratory", "dragon-ball-z", "gravity-falls"]
SETTINGS = settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


@pytest.fixture
def catalog(make_catalog: MakeCatalog) -> Catalog:
    return load_catalog(make_catalog())


def index_of(library: Library, catalog: Catalog) -> dict[str, LibraryItem]:
    sched = Scheduler(catalog, library)
    out = {}
    for show in library.episodes:
        dated = [p.air_date for p in sched.programs(show) if p.air_date]
        out[show] = LibraryItem(
            show, show.title(), "show", min(dated), max(dated), len(dated), len(dated)
        )
    for movie, p in library.movies.items():
        out[movie] = LibraryItem(movie, p.title, "movie", p.air_date, p.air_date, 1, 1)
    return out


starts = st.sampled_from(["00:00", "06:00", "15:00", "20:00", "23:30"])


@st.composite
def channels(draw: st.DrawFn, n: int) -> ProposedChannel:
    kind: ChannelType = draw(st.sampled_from(["network", "marathon", "movie"]))
    launch = draw(st.integers(1990, 2010))
    pool = ["halloweentown"] if kind == "movie" else SHOWS
    picked = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=len(pool), unique=True))
    shows = []
    for s in picked:
        from_year = draw(st.integers(launch, 2010))
        premiere = None
        if s == "gravity-falls" or (draw(st.booleans()) and kind != "movie"):
            premiere = draw(st.dates(date(from_year, 1, 1), date(2010, 12, 31)))
        shows.append(
            ProposedShow(
                show=s, title=s.title(), kind="movie" if kind == "movie" else "show",
                category="kids", debut=None, from_year=from_year, premiere=premiere,
            )
        )  # fmt: skip
    plans = []
    if kind == "network":
        for _ in range(draw(st.integers(0, 4))):
            plans.append(
                DayPartPlan(
                    name="part",
                    days=draw(st.sampled_from(["weekdays", "weekends", "all"])),
                    start=draw(starts),
                    shows=draw(st.lists(st.sampled_from(picked), min_size=1, unique=True)),
                    primetime=draw(st.booleans()),
                )
            )
    return ProposedChannel(
        id=f"custom-built-{n}", reuse=False, name=f"Built {n}", call_sign=f"BLT{n}",
        number=draw(st.sampled_from([2, 27, 40 + n, 41 + n])), type=kind,
        launch_year=launch, audience="kids", identity="", shows=shows, dayparts=plans,
    )  # fmt: skip


@st.composite
def proposals(draw: st.DrawFn) -> Proposal:
    built = [draw(channels(i)) for i in range(draw(st.integers(1, 3)))]
    if draw(st.booleans()):  # an existing channel picks up a show
        built.append(
            ProposedChannel(
                id="cartoon-network", reuse=True, name="Cartoon Network", call_sign="TOON",
                number=27, type="network", launch_year=1992, audience="kids", identity="",
                shows=[ProposedShow(show="x-men", title="X-Men", kind="show", category="kids",
                                    debut=None, from_year=draw(st.integers(1992, 2010)))],
            )
        )  # fmt: skip
    return Proposal(created_at="t", model="m", shows_file="f", base_version="v", channels=built)


@SETTINGS
@given(
    proposal=proposals(),
    day=st.dates(date(1990, 1, 1), date(2010, 12, 31)),
    seed=st.integers(0, 50),
)
def test_applying_a_valid_proposal_keeps_the_invariants(
    catalog: Catalog, proposal: Proposal, day: date, seed: int
) -> None:
    library = make_library()
    checked = check_proposal(
        proposal, catalog, Overlay(), index_of(library, catalog), library, seed
    )
    event(f"channels applied: {len(checked.applied)}")
    merged = apply_overlay(catalog, checked.overlay)
    sched = Scheduler(merged, library, seed=seed)
    again = Scheduler(merged, make_library(), seed=seed)
    lengths = {
        (p.show, p.season, p.episode): p.seconds for eps in library.episodes.values() for p in eps
    }
    lengths |= {(p.show, None, None): p.seconds for p in library.movies.values()}
    for channel_id in checked.applied:
        channel = merged.channel(channel_id)
        on_air = sched.on_air(channel, day)
        if channel.launch and not channel.library:
            assert on_air == (day >= channel.launch)  # no channel before its launch
        if not on_air:
            continue
        slots = sched.day(channel_id, day)
        assert slots[0].start == datetime.combine(day, time(5), NY)
        assert slots[-1].end == datetime.combine(day + timedelta(days=1), time(5), NY)
        for a, b in itertools.pairwise(slots):
            assert a.end == b.start  # back to back: no gaps, no overlaps
        for s in slots:
            if s.kind in ("episode", "movie"):
                assert s.air_date is not None and s.air_date <= s.start.astimezone(NY).date()
                assert (s.end - s.start).total_seconds() == lengths[
                    (s.show or "", s.season, s.episode)
                ]
        assert again.day(channel_id, day) == slots  # deterministic


@SETTINGS
@given(proposal=proposals())
def test_checks_never_apply_a_channel_with_errors(catalog: Catalog, proposal: Proposal) -> None:
    library = make_library()
    checked = check_proposal(proposal, catalog, Overlay(), index_of(library, catalog), library, 0)
    failed = {i.channel for i in checked.issues if i.severity == "error"}
    assert failed == set(checked.skipped)
    merged = apply_overlay(catalog, checked.overlay)
    ids = {c.id for c in merged.channels.channels}
    assert not (failed - {"cartoon-network"}) & ids
    # Numbers stay unique across every channel that made it in.
    numbers = [i.number for c in merged.channels.channels for i in c.identity]
    assert len(numbers) == len(set(numbers))
