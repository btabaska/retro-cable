"""Golden day: Oct 31 1998 on the checked-in catalog with the demo seed.

The snapshot in golden/ is the whole broadcast day for a few channels. When a catalog edit
changes it on purpose, regenerate it with `RC_UPDATE_GOLDEN=1 make test-server` and review the
diff in the PR.
"""

import os
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from retro_cable.catalog import load_catalog
from retro_cable.db import connect
from retro_cable.demo import seed_demo
from retro_cable.scheduler import Scheduler, Slot, load_library
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
