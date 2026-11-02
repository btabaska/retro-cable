import json
from datetime import date
from pathlib import Path

from retro_cable.catalog import load_catalog
from retro_cable.db import connect
from retro_cable.demo import seed_demo
from retro_cable.scheduler import Scheduler, load_library
from tests.conftest import MakeCatalog

FEEDS = {"quarter-hour": 660, "half-hour": 1320, "hour": 2640, "movie": 5400, "bumper": 10}


def write_manifest(media: Path) -> None:
    (media / "_demo").mkdir(parents=True)
    clips = [{"feed": k, "seconds": v, "path": f"_demo/{k}.mp4"} for k, v in FEEDS.items()]
    (media / "_demo" / "manifest.json").write_text(json.dumps({"clips": clips}))


def test_seed_demo_fills_every_carried_show(make_catalog: MakeCatalog, tmp_path: Path) -> None:
    media = tmp_path / "media"
    write_manifest(media)
    root = make_catalog()
    db_path = tmp_path / "demo.db"
    stats = seed_demo(db_path, root, media)
    assert stats["shows"] == 5 and stats["movies"] == 1

    db = connect(db_path)
    library = load_library(db)
    assert set(library.episodes) == {
        "the-simpsons", "x-men", "dexters-laboratory", "dragon-ball-z", "gravity-falls"
    }  # fmt: skip
    simpsons = library.episodes["the-simpsons"]
    assert simpsons[0].air_date is not None and simpsons[0].air_date < date(1990, 1, 1)
    assert all(p.path == "_demo/half-hour.mp4" and p.seconds == 1320 for p in simpsons)
    assert library.movies["halloweentown"].seconds == 5400
    assert {b.channel for bs in library.bumpers.values() for b in bs} == {
        "ktvu", "cartoon-network", "disney-movies"
    }  # fmt: skip

    # Seeding twice replaces the demo data rather than duplicating it.
    seed_demo(db_path, root, media)
    assert len(load_library(connect(db_path)).episodes["the-simpsons"]) == len(simpsons)

    sched = Scheduler(load_catalog(root), library, seed=0)
    slots = sched.day("cartoon-network", date(1998, 10, 31))
    assert any(s.kind == "episode" for s in slots)
