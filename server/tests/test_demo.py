import json
from datetime import date
from pathlib import Path

import pytest

from retro_cable.catalog import load_catalog
from retro_cable.db import connect
from retro_cable.demo import seed_demo
from retro_cable.scheduler import Scheduler, load_library
from tests.conftest import MakeCatalog

FEEDS = {"quarter-hour": 660, "half-hour": 1320, "hour": 2640, "movie": 5400, "bumper": 10}


def write_manifest(media: Path, feeds: dict[str, int] = FEEDS) -> None:
    (media / "_demo").mkdir(parents=True)
    clips = [{"feed": k, "seconds": v, "path": f"_demo/{k}.mp4"} for k, v in feeds.items()]
    (media / "_demo" / "manifest.json").write_text(json.dumps({"clips": clips}))


def test_seed_demo_fills_every_carried_show(make_catalog: MakeCatalog, tmp_path: Path) -> None:
    media = tmp_path / "media"
    write_manifest(media)
    root = make_catalog()
    db_path = tmp_path / "demo.db"
    stats = seed_demo(db_path, root, media)
    # Five carried shows, plus two uncarried library shows for the admin show picker
    # (Gravity Falls is one of the demo's uncarried shows, but this catalog carries it).
    assert stats["shows"] == 7 and stats["movies"] == 1

    db = connect(db_path)
    library = load_library(db)
    assert set(library.episodes) == {
        "the-simpsons", "x-men", "dexters-laboratory", "dragon-ball-z", "gravity-falls",
        "recess", "over-the-garden-wall",
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


def test_seed_demo_refuses_shrunken_feeds(make_catalog: MakeCatalog, tmp_path: Path) -> None:
    # Feeds cut to 2% made 26-second "episodes": a 12-hour guide of 32,000 slots that the
    # browser could not load in time. Every runtime class must be full length.
    media = tmp_path / "media"
    write_manifest(media, {**FEEDS, "half-hour": 26})
    with pytest.raises(ValueError, match="half-hour"):
        seed_demo(tmp_path / "demo.db", make_catalog(), media)
    assert not (tmp_path / "demo.db").exists()


def test_demo_has_holiday_episodes_and_real_dcom_premieres(
    make_catalog: MakeCatalog, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    write_manifest(media)
    root = make_catalog()
    db_path = tmp_path / "demo.db"
    seed_demo(db_path, root, media)
    library = load_library(connect(db_path))
    # Halloweentown premiered on Disney Channel on 1998-10-17, not on its carriage date.
    assert library.movies["halloweentown"].air_date == date(1998, 10, 17)
    # An episode airing in Halloween week is a Halloween episode, so the tagger finds it.
    simpsons = library.episodes["the-simpsons"]
    halloween = [p for p in simpsons if "Halloween" in p.episode_title]
    assert halloween
    for p in halloween:
        assert p.air_date is not None and (p.air_date.month, p.air_date.day) >= (10, 25)
    assert any("Thanksgiving" in p.episode_title for p in simpsons)
