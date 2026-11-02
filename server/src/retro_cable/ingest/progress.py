"""Conversion progress: the rig writes `progress.json`, the mini's /admin Media tab reads it.

The rig's `normalize --manifest` keeps a small JSON file at the media root (the NAS
`.retro-cable` folder) up to date while it works: per show (in conversion order) how many
files are done, how many there are and how many failed this pass; which encoder the current
file uses and whether the GPU was busy; and how long the last 20 files took. It is written
to a temporary name and renamed, like the manifest, so a reader never sees half of it.

The mini mounts that folder read-only; `GET /api/admin/media/progress` turns the file into
the shape in docs/specs/api.md. A file the rig hasn't touched for `STALE` (it stopped, or
is between passes) reads as paused. The file holds counts, titles and show ids only: no
paths, hosts, URLs or tokens.
"""

import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypedDict

from retro_cable.ingest.manifest import write_json_atomic
from retro_cable.ingest.normalize import Encoder, Item, Outcome

FORMAT = "retro-cable-progress/1"
PROGRESS = "progress.json"
RECENT = 20  # finished files whose wall-clock time is kept for the ETA
STALE = timedelta(minutes=30)  # no update for this long: the worker is stopped or idle
WRITE_EVERY = 2.0  # seconds between writes while skipping through done items
MOVIES, BUMPERS = "_movies", "_bumpers"


@dataclass
class _Group:
    key: str
    title: str
    done: int = 0
    total: int = 0
    failed: int = 0


def _group(item: Item) -> str:
    if item.table == "movies":
        return MOVIES
    if item.table == "bumpers":
        return BUMPERS
    return item.key


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class ProgressFile:
    """The rig's side: `normalize(watch=ProgressFile(...))` calls these as it goes."""

    def __init__(
        self,
        path: Path,
        titles: Mapping[str, str],
        *,
        auto: bool,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        log: Callable[[str], None] = _log,
    ) -> None:
        self.path = path
        self.titles = titles
        self.auto = auto
        self.clock, self.now, self.log = clock, now, log
        self.groups: dict[str, _Group] = {}
        self.state: Literal["running", "idle"] = "idle"
        self.encoder: Literal["gpu", "cpu"] | None = None
        self.gpu_busy = False
        self.current: str | None = None
        self.recent: list[float] = _previous_recent(path)
        self._started: float | None = None
        self._last_write: float | None = None

    def _title(self, key: str) -> str:
        if key == MOVIES:
            return "Movies"
        if key == BUMPERS:
            return "Bumpers"
        return self.titles.get(key) or key

    def planned(self, items: Sequence[Item]) -> None:
        """The whole pass, in conversion order. Items already recorded count as done."""
        self.groups = {}
        for item in items:
            key = _group(item)
            group = self.groups.setdefault(key, _Group(key, self._title(key)))
            group.total += 1
            if item.up_to_date_in_db():
                group.done += 1
        self.state = "running"
        self.write(force=True)

    def started(self, item: Item, encoder: Encoder) -> None:
        self.current = _group(item)
        self.encoder = "gpu" if encoder == "nvenc" else "cpu"
        self.gpu_busy = self.auto and encoder == "x264"
        self._started = self.clock()
        self.write(force=True)

    def finished(self, item: Item, outcome: Outcome) -> None:
        group = self.groups.get(_group(item))
        if group is None:
            return
        was_done = item.up_to_date_in_db()
        if outcome == "encoded":
            if not was_done:
                group.done += 1
            if self._started is not None:
                self.recent = [*self.recent, round(self.clock() - self._started, 1)][-RECENT:]
        elif outcome == "failed":
            group.failed += 1
        elif outcome == "missing":  # no source to convert: it isn't part of the job
            group.total -= 1
            if was_done:
                group.done -= 1
        self._started = None
        self.write(force=outcome in ("encoded", "failed"))

    def close(self) -> None:
        """The pass is over: the worker sleeps until it looks for new files."""
        self.state, self.current, self.encoder = "idle", None, None
        self.write(force=True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "updated_at": self.now().isoformat(),
            "state": self.state,
            "encoder": self.encoder,
            "gpu_busy": self.gpu_busy,
            "current": self.current,
            "recent_seconds": self.recent,
            "groups": [vars(g).copy() for g in self.groups.values()],
        }

    def write(self, *, force: bool = False) -> None:
        at = self.clock()
        if not force and self._last_write is not None and at - self._last_write < WRITE_EVERY:
            return
        self._last_write = at
        try:
            write_json_atomic(self.path, self.snapshot())
        except OSError as err:  # an SMB blip: the next item writes again
            self.log(f"warning: progress write failed: {err}")


def _previous_recent(path: Path) -> list[float]:
    """The last pass's timings, so the ETA survives a worker restart."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        recent = data.get("recent_seconds", []) if isinstance(data, dict) else []
        return [float(x) for x in recent if isinstance(x, int | float) and x > 0][-RECENT:]
    except (OSError, ValueError):
        return []


# --- the mini's side ---------------------------------------------------------------------


class ShowProgress(TypedDict):
    title: str
    done: int
    total: int
    failed: int
    state: Literal["converting", "queued", "done"]


class MediaProgress(TypedDict):
    done: int
    total: int
    failed: int
    encoder: Literal["gpu", "cpu", "paused"]
    gpu_busy: bool
    recent_seconds: list[float]
    shows: list[ShowProgress]


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def read_progress(media_dir: Path, now: datetime) -> MediaProgress | None:
    """The Media tab's view of `progress.json`, or None when there is none (or it is not
    one this code reads)."""
    try:
        data = json.loads((media_dir / PROGRESS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None
    try:
        updated = datetime.fromisoformat(str(data.get("updated_at")))
    except ValueError:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    running = data.get("state") == "running" and now - updated <= STALE
    encoder = data.get("encoder") if running else None
    current = data.get("current") if running else None
    shows: list[ShowProgress] = []
    for g in data.get("groups", []):
        if not isinstance(g, dict):
            continue
        total = _count(g.get("total"))
        done = min(_count(g.get("done")), total)
        state: Literal["converting", "queued", "done"] = (
            "converting" if current is not None and g.get("key") == current
            else "done" if done >= total
            else "queued"
        )  # fmt: skip
        title = g.get("title")
        shows.append(
            ShowProgress(
                title=title if isinstance(title, str) else "",
                done=done,
                total=total,
                failed=_count(g.get("failed")),
                state=state,
            )
        )
    recent = [
        float(x)
        for x in data.get("recent_seconds", [])
        if isinstance(x, int | float) and not isinstance(x, bool) and x > 0
    ][-RECENT:]
    return MediaProgress(
        done=sum(s["done"] for s in shows),
        total=sum(s["total"] for s in shows),
        failed=sum(s["failed"] for s in shows),
        encoder=encoder if encoder in ("gpu", "cpu") else "paused",
        gpu_busy=running and data.get("gpu_busy") is True,
        recent_seconds=recent,
        shows=shows,
    )
