import itertools
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from retro_cable.catalog import Catalog
from retro_cable.catalog.models import Carriage, Channel
from retro_cable.catalog.overlay import Overlay, apply_overlay, with_channel
from retro_cable.scheduler import ChannelOffAir, Library, Program, Scheduler, Slot
from tests.conftest import MakeCatalog
from tests.scheduler.conftest import make_library

NY = ZoneInfo("America/New_York")


def at(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=NY)


def assert_back_to_back(slots: list[Slot], start: datetime, end: datetime) -> None:
    assert slots, "empty schedule"
    assert slots[0].start <= start
    assert slots[-1].end >= end
    for a, b in itertools.pairwise(slots):
        assert a.end == b.start, f"gap or overlap between {a} and {b}"
        assert a.end > a.start


def test_broadcast_day_runs_five_am_to_five_am(scheduler: Scheduler) -> None:
    slots = scheduler.day("ktvu", date(1997, 10, 31))
    assert slots[0].start == at(1997, 10, 31, 5)
    assert slots[-1].end == at(1997, 11, 1, 5)
    assert_back_to_back(list(slots), at(1997, 10, 31, 5), at(1997, 11, 1, 5))


def test_between_spans_midnight_without_gaps(scheduler: Scheduler) -> None:
    start, end = at(1997, 10, 31, 22), at(1997, 11, 1, 7)
    slots = scheduler.between("ktvu", start, end)
    assert_back_to_back(slots, start, end)


def test_is_deterministic(catalog: Catalog) -> None:
    a = Scheduler(catalog, make_library(), seed=7).day("ktvu", date(1997, 10, 31))
    b = Scheduler(catalog, make_library(), seed=7).day("ktvu", date(1997, 10, 31))
    c = Scheduler(catalog, make_library(), seed=8).day("ktvu", date(1997, 10, 31))
    assert a == b
    assert a != c


def test_programs_have_bumpers_between_them(scheduler: Scheduler) -> None:
    slots = scheduler.day("ktvu", date(1997, 10, 31))
    kinds = [s.kind for s in slots]
    assert "bumper" in kinds
    for a, b in itertools.pairwise(slots):
        assert not (a.kind == "bumper" and b.kind == "bumper")
    bumper = next(s for s in slots if s.kind == "bumper")
    assert bumper.end - bumper.start == timedelta(seconds=10)


def test_day_parts_anchor_on_the_running_program(scheduler: Scheduler) -> None:
    # Friday 1995-10-27: Fox Kids (X-Men only) from 16:00, then primetime at 20:00.
    slots = scheduler.between("ktvu", at(1995, 10, 27, 16), at(1995, 10, 27, 20))
    after_four = [s for s in slots if s.start >= at(1995, 10, 27, 16, 0) and s.kind == "episode"]
    first = after_four[0]
    assert first.show == "x-men"
    assert first.start < at(1995, 10, 27, 16, 30)
    # The program running at 16:00 started before it and was allowed to finish.
    running = next(s for s in slots if s.start < at(1995, 10, 27, 16) < s.end)
    assert running.end <= first.start


def test_no_episode_before_its_air_date(scheduler: Scheduler) -> None:
    slots = scheduler.day("ktvu", date(1993, 1, 8))
    for s in slots:
        if s.kind == "episode":
            assert s.air_date is not None and s.air_date <= date(1993, 1, 8)


def test_imports_count_from_their_us_debut(catalog: Catalog) -> None:
    sched = Scheduler(catalog, make_library(), seed=1)
    dbz = sched.programs("dragon-ball-z")
    assert dbz[0].air_date == date(1996, 9, 13)
    # Carried on Cartoon Network from 1998-03-16; by then 79 weekly episodes had aired.
    slots = sched.day("cartoon-network", date(1998, 3, 20))
    for s in slots:
        if s.show == "dragon-ball-z":
            assert s.air_date is not None and s.air_date <= date(1998, 3, 20)


def test_transplants_are_marked(catalog: Catalog) -> None:
    sched = Scheduler(catalog, make_library(), seed=1)
    assert sched.programs("gravity-falls", "cartoon-network")[0].air_date == date(2004, 6, 15)
    # The premiere belongs to Cartoon Network; anywhere else Gravity Falls keeps 2012.
    assert sched.programs("gravity-falls")[0].air_date == date(2012, 6, 15)
    assert sched.programs("gravity-falls", "ktvu")[0].air_date == date(2012, 6, 15)
    slots = sched.between("cartoon-network", at(2005, 1, 7, 5), at(2005, 1, 9, 5))
    gf = [s for s in slots if s.show == "gravity-falls"]
    assert gf and all(s.transplant for s in gf)
    assert not any(s.transplant for s in slots if s.show == "dexters-laboratory")


def test_channel_hidden_before_launch(scheduler: Scheduler) -> None:
    on = {c.id for c in scheduler.channels_on(date(1991, 6, 1))}
    assert "cartoon-network" not in on
    assert "disney-movies" in on  # library channels are always on the dial
    with pytest.raises(ChannelOffAir):
        scheduler.day("cartoon-network", date(1991, 6, 1))


def test_movies_respect_their_premiere(scheduler: Scheduler) -> None:
    before = scheduler.day("disney-movies", date(1998, 10, 1))
    assert all(s.kind == "filler" for s in before)
    after = scheduler.day("disney-movies", date(1998, 10, 31))
    movies = [s for s in after if s.kind == "movie"]
    assert movies and movies[0].title == "Halloweentown"
    assert movies[0].end - movies[0].start == timedelta(minutes=84)


def test_primetime_airs_new_episode_on_its_debut_day(catalog: Catalog) -> None:
    from retro_cable.scheduler import Library
    from tests.scheduler.conftest import HALF, weekly

    base = make_library()
    episodes = dict(base.episodes)
    # Friday debuts: 1997-10-31 is the fifth.
    episodes["the-simpsons"] = tuple(
        weekly("the-simpsons", "The Simpsons", date(1997, 10, 3), 10, HALF)
    )
    sched = Scheduler(catalog, Library.build(episodes, base.movies, base.bumpers), seed=3)
    slots = sched.between("ktvu", at(1997, 10, 31, 20), at(1997, 10, 31, 23))
    new = [s for s in slots if s.new]
    assert len(new) == 1
    assert new[0].air_date == date(1997, 10, 31) and new[0].start >= at(1997, 10, 31, 20)
    assert not any(s.new for s in sched.between("ktvu", at(1997, 10, 31, 5), at(1997, 10, 31, 19)))


def test_now_playing_returns_offset_and_next(scheduler: Scheduler) -> None:
    t = at(1997, 10, 31, 20, 14)
    current, upcoming = scheduler.now_playing("ktvu", t, count=3)
    assert current.start <= t < current.end
    assert len(upcoming) == 3
    assert upcoming[0].start == current.end


def test_identity_follows_the_date(make_catalog: MakeCatalog) -> None:
    from retro_cable.catalog import load_catalog

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
    sched = Scheduler(catalog, make_library(), seed=0)
    assert sched.identity("nbc", date(2001, 12, 31)).call_sign == "KRON"
    assert sched.identity("nbc", date(2002, 1, 1)).number == 11
    # Nothing carried: the channel shows filler all day, still back to back.
    slots = sched.day("nbc", date(2002, 1, 1))
    assert all(s.kind == "filler" for s in slots)
    assert_back_to_back(list(slots), at(2002, 1, 1, 5), at(2002, 1, 2, 5))


def test_an_ordered_movie_channel_repeats_nothing_within_a_day_given_enough_titles(
    catalog: Catalog,
) -> None:
    """M2 D22: with more titles than a day holds, no movie airs twice in a broadcast day, and
    the next day picks up further along the list. Lengths vary, so the fitting titles change
    near 5 a.m."""
    titles = [f"movie-{i:02d}" for i in range(24)]
    flix = Channel.model_validate(
        {"id": "custom-flix", "name": "Flix", "type": "movie", "playlist": "ordered",
         "library": True, "identity": [{"from": "1990-01-01", "number": 70, "call_sign": "FLIX"}]}
    )  # fmt: skip
    carriage = [
        Carriage.model_validate(
            {"show": t, "title": t, "channel": "custom-flix", "from": "1990-01-01", "kind": "movie"}
        )
        for t in titles
    ]
    base = make_library()
    movies = {
        t: Program(show=t, kind="movie", title=t, season=None, episode=None, episode_title="",
                   air_date=date(1985, 1, 1), seconds=(80 + 7 * (i % 5)) * 60, path=f"{t}.mp4")
        for i, t in enumerate(titles)
    }  # fmt: skip
    library = replace(base, movies={**base.movies, **movies})
    assert isinstance(library, Library)
    sched = Scheduler(apply_overlay(catalog, with_channel(Overlay(), flix, carriage)), library)
    firsts = []
    for offset in range(10):
        day = date(1999, 3, 1) + timedelta(days=offset)
        aired = [s.show for s in sched.day("custom-flix", day) if s.kind == "movie"]
        assert len(aired) == len(set(aired)) > 10, (day, aired)
        firsts.append(aired[0])
    assert len(set(firsts)) > 1  # each day starts somewhere new
