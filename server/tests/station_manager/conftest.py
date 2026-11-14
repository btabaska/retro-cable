"""A fake local model and a test database with viewing for the station manager. No network."""

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from retro_cable.channel_builder.llm import Message
from retro_cable.db import connect
from tests.conftest import MakeCatalog
from tests.test_api_tv import seed_db

# Sunday Nov 8 2026, 2:00 AM in New York: when the timer fires.
NOW = datetime(2026, 11, 8, 7, 0, tzinfo=UTC)
EVENING = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)  # Sunday Nov 1, 8:00 PM in New York


@dataclass
class FakeManager:
    """Answers the station manager's prompt like a cooperative model: every knob of a show in
    `up` goes to `factor_up`, every knob of a show in `down` to `factor_down`, and each power
    hour gets its first two choices. `hook` replaces the reply (with the call number)."""

    model: str = "fake-local"
    up: tuple[str, ...] = ("the-simpsons",)
    down: tuple[str, ...] = ("x-men",)
    factor_up: float = 3.0
    factor_down: float = 0.1
    hook: Callable[[str, int], str] | None = None
    prompts: list[str] = field(default_factory=list)

    def complete(self, messages: list[Message]) -> str:
        self.prompts.append(messages[-1]["content"])
        if self.hook is not None:
            return self.hook(messages[1]["content"], len(self.prompts) - 1)
        return self.reply(messages[1]["content"])

    def reply(self, user: str) -> str:
        changes = []
        for knob in re.findall(r"^  ((?:carriage|pool):\S+)", user, flags=re.M):
            show = knob.rsplit(":", 1)[1]
            if show in self.up:
                changes.append({"knob": knob, "factor": self.factor_up, "why": "finished"})
            elif show in self.down:
                changes.append({"knob": knob, "factor": self.factor_down, "why": "skipped"})
        power = [
            {"channel": ch, "shows": [{"show": s, "weight": 2} for s in shows.split(", ")[:2]]}
            for ch, shows in re.findall(r"^Power hour choices on (\S+): (.+)$", user, flags=re.M)
        ]
        return json.dumps({"summary": "more of what you finish", "changes": changes,
                           "power_hour": power})  # fmt: skip


def watch(
    db: sqlite3.Connection,
    channel: str,
    item: str,
    seconds: int,
    started: datetime,
    ended: str | None = "program_end",
    runtime: int = 1320,
    year: int = 1995,
) -> None:
    n = db.execute("SELECT COUNT(*) FROM watch_sessions").fetchone()[0]
    db.execute(
        "INSERT INTO watch_sessions (session, channel, item, kind, sim_date, started_at,"
        " updated_at, seconds, runtime_seconds, ended) VALUES (?, ?, ?, 'episode', ?, ?, ?, ?,"
        " ?, ?)",
        (f"session-{n:04d}", channel, item, f"{year}-11-01", started.isoformat(),
         started.isoformat(), seconds, runtime, ended),
    )  # fmt: skip


def evenings(db: sqlite3.Connection, nights: int = 8) -> None:
    """Brandon at 8 PM: The Simpsons on KTVU to the end, X-Men flipped away from early."""
    for d in range(nights):
        day = date(2026, 11, 1) - timedelta(days=d)  # across the end of DST: 8 PM local
        start = datetime.combine(day, time(20), ZoneInfo("America/New_York")).astimezone(UTC)
        watch(db, "ktvu", "the-simpsons", 1320, start)
        watch(db, "ktvu", "x-men", 90, start + timedelta(minutes=23), ended="tune_away")
        watch(db, "cartoon-network", "dexters-laboratory", 600, start - timedelta(minutes=30),
              ended="tune_away")  # fmt: skip


@pytest.fixture
def catalog_dir(make_catalog: MakeCatalog) -> Path:
    return make_catalog()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    media = tmp_path / "media"
    media.mkdir()
    path = tmp_path / "rc.db"
    seed_db(path, media)
    return path


@pytest.fixture
def watched(db_path: Path) -> Path:
    db = connect(db_path)
    evenings(db)
    db.close()
    return db_path
