"""Every station manager run, kept in SQLite (migration 0008) so Brandon can read what changed
and why: applied, proposed (auto-apply off), dry run, skipped and failed runs alike."""

import sqlite3
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from retro_cable.catalog.models import Tuning
from retro_cable.station_manager.signals import Signals
from retro_cable.station_manager.tuning import Change, Dropped, PowerHourPlan

Status = Literal["applied", "proposed", "dry_run", "skipped", "failed"]
REPORT_SHOWS = 30  # the report keeps the most watched shows only


class PowerWindow(BaseModel):
    start: str = Field(description="Local HH:MM")
    hours: int


class RunReport(BaseModel):
    weeks: int = Field(description="Weeks of viewing read")
    since: datetime
    until: datetime
    signals: Signals
    power_window: PowerWindow | None = None
    changes: list[Change] = Field(default_factory=list)
    power_hours: list[PowerHourPlan] = Field(default_factory=list)
    dropped: list[Dropped] = Field(default_factory=list)
    model_summary: str = Field(default="", description="The model's own one-line summary")
    notes: list[str] = Field(default_factory=list)
    before: Tuning = Field(default_factory=Tuning, description="The tuning the run started from")
    after: Tuning | None = Field(default=None, description="The tuning it applied or proposes")


class StationRun(BaseModel):
    id: int
    created_at: datetime
    week_of: str = Field(description="YYYY-MM-DD, the Sunday the run's week starts")
    status: Status
    model: str
    revision_id: int | None = Field(description="The /admin revision an applied run saved")
    summary: str
    report: RunReport


def record(
    db: sqlite3.Connection,
    now: datetime,
    week_of: str,
    status: Status,
    model: str,
    summary: str,
    report: RunReport,
    revision_id: int | None = None,
) -> StationRun:
    signals = report.signals.model_copy(update={"shows": report.signals.shows[:REPORT_SHOWS]})
    report = report.model_copy(update={"signals": signals})
    cur = db.execute(
        "INSERT INTO station_runs (created_at, week_of, status, model, revision_id, summary,"
        " report) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now.isoformat(), week_of, status, model, revision_id, summary,
         report.model_dump_json()),
    )  # fmt: skip
    assert cur.lastrowid is not None
    return get(db, cur.lastrowid)


def _row(r: sqlite3.Row) -> StationRun:
    return StationRun(
        id=r["id"],
        created_at=datetime.fromisoformat(r["created_at"]),
        week_of=r["week_of"],
        status=r["status"],
        model=r["model"],
        revision_id=r["revision_id"],
        summary=r["summary"],
        report=RunReport.model_validate_json(r["report"]),
    )


def get(db: sqlite3.Connection, run_id: int) -> StationRun:
    r = db.execute("SELECT * FROM station_runs WHERE id = ?", (run_id,)).fetchone()
    if r is None:
        raise KeyError(run_id)
    return _row(r)


def recent(db: sqlite3.Connection, limit: int = 20) -> list[StationRun]:
    rows = db.execute("SELECT * FROM station_runs ORDER BY id DESC LIMIT ?", (limit,))
    return [_row(r) for r in rows]
