"""Conversion progress: the rig's progress.json and the mini's view of it (M2 B13)."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path

from retro_cable.ingest.normalize import Item, Probe, Stream, no_crop, normalize
from retro_cable.ingest.progress import PROGRESS, ProgressFile, read_progress
from tests.ingest.test_normalize import Recorder, fake_probe

NOW = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _item(key: str, n: int, *, done: bool = False, table: str = "episodes") -> Item:
    target = f"tv/{key}/{n}.mp4"
    return Item(
        table, n, f"{key} {n}", f"/src/{key}/{n}.mkv", target,  # type: ignore[arg-type]
        target if done else None, 1, 1, 1320, key, 1320 if done else None, 1320,
    )  # fmt: skip


def test_the_rig_writes_counts_in_conversion_order_and_timings(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / PROGRESS
    p = ProgressFile(path, {"dexters-laboratory": "Dexter's Laboratory"}, auto=True,
                     clock=clock, now=lambda: NOW)  # fmt: skip
    items = [
        _item("dexters-laboratory", 1, done=True),
        _item("dexters-laboratory", 2),
        _item("the-simpsons", 1),
        _item("film", 1, table="movies"),
        _item("ktvu", 1, table="bumpers"),
    ]
    p.planned(items)
    data = json.loads(path.read_text())
    assert data["state"] == "running"
    assert [(g["title"], g["done"], g["total"]) for g in data["groups"]] == [
        ("Dexter's Laboratory", 1, 2), ("the-simpsons", 0, 1), ("Movies", 0, 1),
        ("Bumpers", 0, 1),
    ]  # fmt: skip

    p.started(items[1], "x264")
    clock.t = 52.04
    p.finished(items[1], "encoded")
    p.started(items[2], "nvenc")
    clock.t = 80.0
    p.finished(items[2], "failed")
    p.started(items[3], "x264")  # still running when we look
    data = json.loads(path.read_text())
    assert data["recent_seconds"] == [52.0]
    assert data["encoder"] == "cpu" and data["gpu_busy"] is True
    assert data["current"] == "_movies"
    groups = {g["key"]: g for g in data["groups"]}
    assert (groups["dexters-laboratory"]["done"], groups["the-simpsons"]["failed"]) == (2, 1)
    assert "/src" not in path.read_text(), "no paths in the progress file"

    p.finished(items[4], "missing")
    p.close()
    data = json.loads(path.read_text())
    assert data["state"] == "idle" and data["current"] is None
    assert {g["key"]: g["total"] for g in data["groups"]}["_bumpers"] == 0


def test_fixed_x264_is_not_reported_as_a_busy_gpu(tmp_path: Path) -> None:
    p = ProgressFile(tmp_path / PROGRESS, {}, auto=False, now=lambda: NOW)
    item = _item("s", 1)
    p.planned([item])
    p.started(item, "x264")
    data = read_progress(tmp_path, NOW)
    assert data is not None
    assert (data["encoder"], data["gpu_busy"]) == ("cpu", False)


def test_timings_survive_a_worker_restart(tmp_path: Path) -> None:
    (tmp_path / PROGRESS).write_text(json.dumps({"recent_seconds": [40.0, 41.5, "x", -1]}))
    p = ProgressFile(tmp_path / PROGRESS, {}, auto=True, now=lambda: NOW)
    assert p.recent == [40.0, 41.5]


def test_a_failed_write_is_a_warning_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "ro").write_text("a file, so the folder can't be made")
    logged: list[str] = []
    p = ProgressFile(tmp_path / "ro" / PROGRESS, {}, auto=True, log=logged.append)
    p.planned([_item("s", 1)])
    assert logged and "progress write failed" in logged[0]


def test_skipping_through_done_items_writes_at_most_every_few_seconds(tmp_path: Path) -> None:
    clock = Clock()
    p = ProgressFile(tmp_path / PROGRESS, {}, auto=True, clock=clock, now=lambda: NOW)
    items = [_item("s", n, done=True) for n in range(50)]
    p.planned(items)
    writes = 0
    real = p.snapshot

    def counting() -> dict[str, object]:
        nonlocal writes
        writes += 1
        return real()

    p.snapshot = counting  # type: ignore[method-assign]
    for item in items:
        clock.t += 0.01
        p.finished(item, "skipped")
    assert writes == 0


# --- the mini's view ----------------------------------------------------------------------


def _write(tmp_path: Path, **over: object) -> None:
    data = {
        "format": "retro-cable-progress/1",
        "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
        "state": "running", "encoder": "gpu", "gpu_busy": False, "current": "b",
        "recent_seconds": [31.2, 29.8],
        "groups": [
            {"key": "a", "title": "A", "done": 3, "total": 3, "failed": 0},
            {"key": "b", "title": "B", "done": 1, "total": 4, "failed": 2},
            {"key": "c", "title": "C", "done": 0, "total": 5, "failed": 0},
        ],
    } | over  # fmt: skip
    (tmp_path / PROGRESS).write_text(json.dumps(data))


def test_read_progress_is_the_media_tab_shape(tmp_path: Path) -> None:
    _write(tmp_path)
    assert read_progress(tmp_path, NOW) == {
        "done": 4, "total": 12, "failed": 2, "encoder": "gpu", "gpu_busy": False,
        "recent_seconds": [31.2, 29.8],
        "shows": [
            {"title": "A", "done": 3, "total": 3, "failed": 0, "state": "done"},
            {"title": "B", "done": 1, "total": 4, "failed": 2, "state": "converting"},
            {"title": "C", "done": 0, "total": 5, "failed": 0, "state": "queued"},
        ],
    }  # fmt: skip


def test_an_idle_or_silent_worker_reads_as_paused(tmp_path: Path) -> None:
    _write(tmp_path, state="idle", current=None, encoder=None)
    data = read_progress(tmp_path, NOW)
    assert data is not None and data["encoder"] == "paused"
    _write(tmp_path, updated_at=(NOW - timedelta(hours=2)).isoformat(), gpu_busy=True)
    data = read_progress(tmp_path, NOW)
    assert data is not None
    assert (data["encoder"], data["gpu_busy"]) == ("paused", False)
    assert all(s["state"] != "converting" for s in data["shows"])


def test_no_file_or_a_bad_one_reads_as_none(tmp_path: Path) -> None:
    assert read_progress(tmp_path, NOW) is None
    (tmp_path / PROGRESS).write_text("{half")
    assert read_progress(tmp_path, NOW) is None
    _write(tmp_path, format="something-else/9")
    assert read_progress(tmp_path, NOW) is None


def test_bad_numbers_are_clamped(tmp_path: Path) -> None:
    _write(tmp_path, groups=[{"key": "a", "title": 5, "done": 9, "total": 3, "failed": -1}],
           recent_seconds=[True, 10, "x"])  # fmt: skip
    data = read_progress(tmp_path, NOW)
    assert data is not None
    assert data["shows"] == [{"title": "", "done": 3, "total": 3, "failed": 0, "state": "done"}]
    assert data["recent_seconds"] == [10.0]


# --- normalize drives it ------------------------------------------------------------------


def test_normalize_reports_each_item_to_the_progress_file(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    path = media / PROGRESS
    clock = Clock()

    def probe(p: Path) -> Probe:
        clock.t += 10  # each probe "takes" 10 s, so encodes have a length
        if p.name == "s-s01e02.mp4.partial":
            return Probe(duration=100.0, video=Stream(0, 640, 480, Fraction(1)), audio_index=1)
        return fake_probe(p)

    watch = ProgressFile(path, {"s": "S"}, auto=True, clock=clock, now=lambda: NOW)
    stats = normalize(db, media, run=Recorder(), probe=probe, crop=no_crop,
                      encoder="x264", watch=watch)  # fmt: skip
    assert (stats.encoded, stats.failed) == (2, ["s S01E02"])
    view = read_progress(media, NOW)
    assert view is not None
    assert view["shows"] == [{"title": "S", "done": 2, "total": 3, "failed": 1, "state": "queued"}]
    assert view["encoder"] == "paused", "the pass is over"
    assert len(view["recent_seconds"]) == 2
