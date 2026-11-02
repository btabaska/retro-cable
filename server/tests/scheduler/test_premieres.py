"""The debut rule on every channel type, and synthetic premieres scoped to one channel (M2 A).

Library channels used to make every episode eligible; now `library: true` only keeps a channel
on the dial before its launch. To air a show early on purpose, Brandon sets a synthetic
("pretend") premiere for that show on that channel only.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from retro_cable.catalog import Catalog
from retro_cable.catalog.models import Carriage, Channel, Synthetic
from retro_cable.catalog.overlay import Overlay, apply_overlay, with_channel, with_premiere
from retro_cable.scheduler import Scheduler
from tests.scheduler.conftest import make_library

NY = ZoneInfo("America/New_York")
SETTINGS = settings(
    max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


def channel(cid: str, number: int, *, kind: str = "network", library: bool, launch: str | None,
            mark: bool = True) -> Channel:  # fmt: skip
    return Channel.model_validate(
        {
            "id": cid,
            "name": cid,
            "type": kind,
            "library": library,
            "launch": launch,
            "playlist": "shuffled" if kind == "movie" else None,
            "transplant_mark": mark,
            "identity": [{"from": launch or "1990-01-01", "number": number, "call_sign": "X"}],
        }
    )


def carry(cid: str, *shows: str, kind: str = "show") -> list[Carriage]:
    return [
        Carriage.model_validate(
            {"show": s, "title": s, "channel": cid, "from": "1990-01-01", "kind": kind}
        )
        for s in shows
    ]


SHOWS = ("the-simpsons", "x-men", "dexters-laboratory", "dragon-ball-z", "gravity-falls")


def every_type(catalog: Catalog) -> Catalog:
    """The fixture catalog (network, movie+library) plus a custom library channel, a custom
    launched channel, a custom marathon channel and a custom library movie channel. The
    custom launched channel gives Gravity Falls (really 2012) a 1995 pretend premiere."""
    overlay = Overlay()
    overlay = with_channel(overlay, channel("custom-lib", 60, library=True, launch=None),
                           carry("custom-lib", *SHOWS))  # fmt: skip
    overlay = with_channel(
        overlay,
        channel("custom-toons", 61, library=False, launch="1990-01-01"),
        carry("custom-toons", *SHOWS),
        [Synthetic(show="gravity-falls", channel="custom-toons", premiere=date(1995, 3, 3))],
    )
    overlay = with_channel(overlay, channel("custom-mara", 62, kind="marathon", library=True,
                                            launch=None), carry("custom-mara", "dexters-laboratory",
                                                                "gravity-falls"))  # fmt: skip
    overlay = with_channel(overlay, channel("custom-flix", 63, kind="movie", library=True,
                                            launch=None), carry("custom-flix", "halloweentown",
                                                                kind="movie"))  # fmt: skip
    return apply_overlay(catalog, overlay)


EVERY = ["ktvu", "cartoon-network", "disney-movies", "custom-lib", "custom-toons",
         "custom-mara", "custom-flix"]  # fmt: skip


@SETTINGS
@given(
    day=st.dates(min_value=date(1990, 1, 1), max_value=date(2010, 12, 31)),
    cid=st.sampled_from(EVERY),
    seed=st.integers(0, 1000),
)
def test_no_episode_airs_before_its_effective_date_on_any_channel(
    catalog: Catalog, day: date, cid: str, seed: int
) -> None:
    sched = Scheduler(every_type(catalog), make_library(), seed=seed)
    if cid not in {c.id for c in sched.channels_on(day)}:
        return
    for s in sched.day(cid, day):
        if s.kind not in ("episode", "movie"):
            continue
        aired_on = s.start.astimezone(NY).date()
        assert s.air_date is not None and s.air_date <= aired_on, (cid, s)
        # The date on the slot is the one this channel uses: its own premiere or the real one.
        if s.kind == "episode" and s.show:
            mine = {(p.season, p.episode): p.air_date for p in sched.programs(s.show, cid)}
            assert mine[(s.season, s.episode)] == s.air_date


def test_library_and_custom_channels_in_1995_air_nothing_early(catalog: Catalog) -> None:
    sched = Scheduler(every_type(catalog), make_library(), seed=3)
    seen: set[str] = set()
    for cid in ("custom-lib", "custom-toons", "custom-mara", "disney-movies"):
        for offset in range(7):
            day = date(1995, 6, 5) + timedelta(days=offset)
            for s in sched.day(cid, day):
                if s.kind in ("episode", "movie"):
                    seen.add(f"{cid}:{s.show}")
                    assert s.air_date is not None
                    assert s.air_date <= s.start.astimezone(NY).date(), (cid, s)
    # Library channels no longer lift debuts: no Gravity Falls (2012) or Dexter (1996) there...
    assert not any(k.startswith("custom-lib:gravity") or k.startswith("custom-lib:dex")
                   for k in seen)  # fmt: skip
    assert "custom-lib:the-simpsons" in seen
    # ...but the channel with a 1995 pretend premiere airs Gravity Falls.
    assert "custom-toons:gravity-falls" in seen


def test_a_premiere_on_one_channel_leaves_the_others_alone(catalog: Catalog) -> None:
    sched = Scheduler(every_type(catalog), make_library(), seed=3)
    assert sched.programs("gravity-falls", "custom-toons")[0].air_date == date(1995, 3, 3)
    assert sched.programs("gravity-falls", "custom-lib")[0].air_date == date(2012, 6, 15)
    assert sched.programs("gravity-falls", "cartoon-network")[0].air_date == date(2004, 6, 15)
    days = [date(1996, 1, 5) + timedelta(days=i) for i in range(5)]
    toons = [s for d in days for s in sched.day("custom-toons", d) if s.show == "gravity-falls"]
    assert toons and all(s.transplant for s in toons)
    lib = [s for d in days for s in sched.day("custom-lib", d) if s.show]
    assert lib and not any(s.transplant for s in lib)


def test_the_transplant_mark_follows_the_channel_setting(catalog: Catalog) -> None:
    overlay = with_channel(
        Overlay(),
        channel("custom-quiet", 64, library=False, launch="1990-01-01", mark=False),
        carry("custom-quiet", "gravity-falls"),
        [Synthetic(show="gravity-falls", channel="custom-quiet", premiere=date(1995, 3, 3))],
    )
    sched = Scheduler(apply_overlay(catalog, overlay), make_library(), seed=1)
    gf = [s for s in sched.day("custom-quiet", date(1996, 1, 5)) if s.show == "gravity-falls"]
    assert gf and not any(s.transplant for s in gf)


def test_new_follows_the_shifted_dates(catalog: Catalog) -> None:
    # A pretend premiere for X-Men on KTVU on a Friday (KTVU's fixture prime is Fridays 20:00):
    # S1E1 airs NEW that night, a week before S1E2 does.
    friday = date(1995, 3, 3)
    assert friday.weekday() == 4
    overlay = with_premiere(Overlay(), "x-men", "ktvu", friday)
    sched = Scheduler(apply_overlay(catalog, overlay), make_library(), seed=1)
    start = datetime.combine(friday, time(20), NY)
    prime = sched.between("ktvu", start, start + timedelta(hours=3))
    new = [s for s in prime if s.new]
    assert [(s.show, s.season, s.episode) for s in new] == [("x-men", 1, 1)]
    assert new[0].transplant and new[0].air_date == friday


def test_an_unrelated_premiere_never_changes_another_channel(catalog: Catalog) -> None:
    """Determinism (M2 A8): same (channel, date, seed) before and after a premiere is added on
    another channel, even for the same show."""
    before = Scheduler(every_type(catalog), make_library(), seed=11)
    extra = with_premiere(Overlay(), "x-men", "ktvu", date(1993, 1, 4))
    extra = with_premiere(extra, "gravity-falls", "disney-movies", date(1992, 2, 2))
    changed = apply_overlay(catalog, extra)
    after = Scheduler(every_type(changed), make_library(), seed=11)
    for cid in ("cartoon-network", "custom-lib", "custom-toons"):
        for day in (date(1997, 10, 31), date(2004, 7, 2), date(1999, 12, 24)):
            assert before.day(cid, day) == after.day(cid, day), (cid, day)
