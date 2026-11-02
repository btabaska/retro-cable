"""Golden day: Oct 31 1998 on the checked-in catalog with the demo seed.

The snapshot in golden/ is the whole broadcast day for a few channels. When a catalog edit
changes it on purpose, regenerate it with `RC_UPDATE_GOLDEN=1 make test-server` and review the
diff in the PR.
"""

import itertools
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from retro_cable.catalog import load_catalog
from retro_cable.db import connect
from retro_cable.demo import seed_demo
from retro_cable.scheduler import Scheduler, Slot, load_library
from retro_cable.scheduler.engine import OFF_AIR
from tests.test_demo import write_manifest

REPO_CATALOG = Path(__file__).resolve().parents[3] / "catalog"
GOLDEN = Path(__file__).parent / "golden"
NY = ZoneInfo("America/New_York")
CHANNELS = ("ktvu", "disney", "disney-movies", "cartoon-network", "kpix")
OCT31 = date(1998, 10, 31)


@pytest.fixture(scope="module")
def sched(tmp_path_factory: pytest.TempPathFactory) -> Scheduler:
    tmp = tmp_path_factory.mktemp("golden")
    media = tmp / "media"
    write_manifest(media)
    seed_demo(tmp / "demo.db", REPO_CATALOG, media)
    library = load_library(connect(tmp / "demo.db"))
    return Scheduler(load_catalog(REPO_CATALOG), library, seed=0)


def line(s: Slot) -> str:
    start = s.start.astimezone(NY).strftime("%H:%M:%S")
    marks = " ".join(m for m in (s.kind, "NEW" if s.new else "", s.holiday or "") if m)
    episode = f" S{s.season}E{s.episode} {s.episode_title}" if s.season is not None else ""
    return f"{start} {s.seconds:>5}s [{marks}] {s.title}{episode}"


def render(sched: Scheduler, day: date) -> str:
    out = [f"# {day.isoformat()} ({day.strftime('%A')})"]
    for channel in CHANNELS:
        out.append(f"\n## {channel}")
        out.extend(line(s) for s in sched.day(channel, day))
    return "\n".join(out) + "\n"


def test_oct_31_1998_snapshot(sched: Scheduler) -> None:
    path = GOLDEN / "1998-10-31.txt"
    text = render(sched, OCT31)
    if os.environ.get("RC_UPDATE_GOLDEN") or not path.exists():
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(text)
    assert text == path.read_text()


def at(hh: int) -> datetime:
    return datetime.combine(OCT31, time(hh), NY)


def test_oct_31_1998_plays_halloweentown_in_prime_on_disney(sched: Scheduler) -> None:
    disney = sched.day("disney", OCT31)
    halloweentown = [s for s in disney if s.show == "halloweentown"]
    assert len(halloweentown) == 1
    assert at(19) <= halloweentown[0].start < at(22)
    assert halloweentown[0].holiday == "halloween"


def test_oct_31_1998_airs_halloween_episodes(sched: Scheduler) -> None:
    prime = sched.between("ktvu", at(20), at(23))
    episodes = [s for s in prime if s.kind == "episode"]
    themed = {s.show for s in episodes if s.holiday == "halloween"}
    assert "the-simpsons" in themed
    # A show with Halloween episodes airs only those in prime; shows that premiered in 1998
    # have none yet and air as usual.
    assert all(s.holiday == "halloween" for s in episodes if s.show in themed)
    # No Christmas or Thanksgiving specials on Halloween (the broadcast day runs into Nov 1,
    # when Thanksgiving's season begins).
    for channel in CHANNELS:
        for s in sched.day(channel, OCT31):
            if s.start.astimezone(NY).date() == OCT31:
                assert s.holiday in (None, "halloween"), s


# --- Holiday movies are seasonal everywhere; the holiday itself is a marathon (M1 review) ---

HALLOWEEN_MOVIES = {"halloweentown", "under-wraps", "halloweentown-ii-kalabars-revenge", "twitches"}


def test_oct_31_1998_disney_movies_is_a_halloween_marathon(sched: Scheduler) -> None:
    """On Halloween the movie channel runs every eligible Halloween movie back to back,
    repeating, through the whole broadcast day (5 a.m. to 5 a.m.)."""
    slots = sched.day("disney-movies", OCT31)
    movies = [s for s in slots if s.kind == "movie"]
    shows = {s.show for s in movies}
    # Every Halloween movie in the demo seed that had premiered by Oct 31 1998.
    assert shows == {"halloweentown", "under-wraps"}
    assert all(s.holiday == "halloween" for s in movies)
    # Repeating as needed: each airs several times, never twice in a row.
    assert all(sum(s.show == show for s in movies) >= 3 for show in shows)
    assert all(a.show != b.show for a, b in itertools.pairwise(movies))
    # Back to back from 5 a.m., with only bumpers between and filler at the very end.
    assert slots[0].kind == "movie" and slots[0].start == at(5)
    assert all(s.kind in ("movie", "bumper") for s in slots[:-1])


def test_nov_7_1998_disney_movies_airs_no_halloween_movies(sched: Scheduler) -> None:
    slots = sched.day("disney-movies", date(1998, 11, 7))
    assert any(s.kind == "movie" for s in slots)
    for s in slots:
        assert s.show not in HALLOWEEN_MOVIES, s
        assert s.holiday is None, s


def test_halloween_movies_stay_in_season_on_every_channel(sched: Scheduler) -> None:
    for day in (date(1998, 7, 4), date(1998, 11, 7), date(1999, 3, 17), date(1998, 12, 24)):
        for channel in CHANNELS:
            for s in sched.day(channel, day):
                assert s.show not in HALLOWEEN_MOVIES, (channel, s)


def test_oct_24_1998_in_season_but_not_the_holiday_is_no_marathon(sched: Scheduler) -> None:
    """Before the day itself, Halloween movies only get prime time; the rest of the day
    runs the usual rotation."""
    movies = [s for s in sched.day("disney-movies", date(1998, 10, 24)) if s.kind == "movie"]
    assert any(s.show not in HALLOWEEN_MOVIES for s in movies)


# --- M1 rough edges (M2 D21, D22) -------------------------------------------------------


def evening(sched: Scheduler, channel: str, day: date) -> list[Slot]:
    start = datetime.combine(day, time(18), NY)
    return sched.between(channel, start, start + timedelta(hours=6))


def test_cartoon_network_1998_prime_time_never_goes_off_air(sched: Scheduler) -> None:
    """18:00-24:00 every day of a 1998 week, and Halloween (a Saturday), runs real programs."""
    days = [date(1998, 6, 1) + timedelta(days=i) for i in range(7)] + [OCT31]
    for day in days:
        slots = evening(sched, "cartoon-network", day)
        assert not [s for s in slots if s.title == OFF_AIR], day
        assert all(s.kind in ("episode", "movie", "bumper") for s in slots), day


@pytest.mark.parametrize("year", range(1993, 2011))
def test_cartoon_network_evenings_stay_on_the_air_in_every_era(sched: Scheduler, year: int) -> None:
    for day in (date(year, 3, 2), date(year, 6, 6), date(year, 9, 12)):
        for offset in range(0, 7, 3):  # three weekdays a week apart in the day-part sense
            d = day + timedelta(days=offset)
            assert not [s for s in evening(sched, "cartoon-network", d) if s.title == OFF_AIR], d


def test_disney_movies_cycles_through_every_title_before_repeating(sched: Scheduler) -> None:
    """The ordered rotation airs each eligible title once before any repeats, and never the
    same movie twice in a row (the demo seed has fewer titles than a day holds)."""
    for day in (date(2006, 6, 3), date(2008, 6, 3), date(2010, 6, 3), date(2004, 3, 3)):
        movies = [s.show for s in sched.day("disney-movies", day) if s.kind == "movie"]
        titles = len(set(movies))
        assert len(set(movies[:titles])) == titles, (day, movies)
        assert all(a != b for a, b in itertools.pairwise(movies)), (day, movies)
