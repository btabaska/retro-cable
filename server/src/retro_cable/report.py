"""The Sunday report (Phase 8): `python -m retro_cable.report run [--no-push]`.

What happened in the week before it runs: viewing against the week before, the station
manager's run and anything waiting for Brandon's approval, what is on the air and which
carried titles have nothing yet, the rig's conversion, and whether last night's backup and
its restore test passed. Every report is kept (migration 0009) for /admin's Report tab and
so the next one can say what changed. With RC_NTFY_URL set it is also pushed to Brandon's
phone through the homelab's ntfy (on the LAN; RC_NTFY_TOKEN when the topic needs one, never
logged), linking to RC_REPORT_LINK. On the mini a systemd timer runs it Sundays at 8 AM in the
api container (ops/weekly-report.sh).
"""

import argparse
import os
import sqlite3
import sys
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from retro_cable import backup
from retro_cable.admin import store
from retro_cable.catalog import Catalog, CatalogError, load_catalog
from retro_cable.catalog.overlay import apply_overlay
from retro_cable.channel_builder.__main__ import default_db
from retro_cable.db import connect, migrate
from retro_cable.ingest.progress import read_progress
from retro_cable.scheduler import load_library
from retro_cable.station_manager import runs
from retro_cable.station_manager.manager import week_of
from retro_cable.station_manager.signals import compute, load_sessions

SCHEDULE = "Sundays at 8 AM"
TOP = 5  # shows and channels listed
EMPTY_LIST = 30  # carried titles with nothing yet, listed by name
STALE_BACKUP = timedelta(hours=36)  # a nightly backup older than this missed a night


class ShowRow(BaseModel):
    show: str
    title: str
    channel: str = Field(description="The channel's name")
    seconds: int
    completion: float


class ChannelRow(BaseModel):
    id: str
    name: str
    seconds: int


class Viewing(BaseModel):
    seconds: int
    sessions: int
    last_week_seconds: int
    top_shows: list[ShowRow]
    top_channels: list[ChannelRow]
    busiest_hour: str | None = Field(description="Local HH:MM of the busiest hour")


class StationLine(BaseModel):
    id: int
    status: str
    summary: str
    week_of: str


class LibraryLine(BaseModel):
    episodes: int = Field(description="Episodes that can air")
    shows: int = Field(description="Shows with at least one episode that can air")
    movies: int
    episodes_added: int | None = Field(description="Since the last report; None for the first")
    empty_count: int = Field(description="Carried titles with nothing that can air yet")
    empty: list[str] = Field(description=f"Their titles, A to Z (up to {EMPTY_LIST})")


class ConversionLine(BaseModel):
    done: int
    total: int
    left: int
    failed: int
    hours_left: float | None = Field(description="At the median of recent files")


class BackupLine(BaseModel):
    ok: bool = Field(description="A backup in the last 36 hours whose restore test passed")
    last_backup_at: datetime | None
    last_backup_bytes: int | None
    restore_ok: bool | None
    restore_at: datetime | None
    restore_problems: list[str]
    schedule_warnings: list[str] = Field(
        default_factory=list,
        description="What the restore test's scheduler checks found in the data (not failures)",
    )


class WeeklyReport(BaseModel):
    week_of: str = Field(description="YYYY-MM-DD, the Sunday the reported week starts")
    since: datetime
    until: datetime
    viewing: Viewing
    station: StationLine | None
    pending: list[str] = Field(description="What waits for Brandon")
    library: LibraryLine
    conversion: ConversionLine | None
    backups: BackupLine
    headline: list[str] = Field(description="The report in a few lines, as pushed")


class StoredReport(BaseModel):
    id: int
    created_at: datetime
    week_of: str
    report: WeeklyReport


def _duration(seconds: int) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def _ago(when: datetime, now: datetime) -> str:
    hours = (now - when) / timedelta(hours=1)
    return f"{round(hours)} h ago" if hours < 48 else f"{int(hours // 24)} days ago"


def _viewing(
    db: sqlite3.Connection,
    base: Catalog,
    since: datetime,
    until: datetime,
    tz: ZoneInfo,
    titles: Mapping[str, str],
) -> Viewing:
    signals = compute(load_sessions(db, since, until), tz)
    before = compute(load_sessions(db, since - timedelta(days=7), since), tz)
    names = {c.id: c.name for c in base.channels.channels}
    return Viewing(
        seconds=signals.total_seconds,
        sessions=signals.sessions,
        last_week_seconds=before.total_seconds,
        top_shows=[
            ShowRow(
                show=s.show,
                title=titles.get(s.show, s.show),
                channel=names.get(s.channel, s.channel),
                seconds=s.seconds,
                completion=s.completion,
            )
            for s in signals.shows[:TOP]
        ],
        top_channels=[
            ChannelRow(id=c.channel, name=names.get(c.channel, c.channel), seconds=c.seconds)
            for c in signals.channels[:TOP]
        ],
        busiest_hour=f"{signals.busiest_hours[0]:02d}:00" if signals.busiest_hours else None,
    )


def _previous(db: sqlite3.Connection, now: datetime) -> WeeklyReport | None:
    r = db.execute(
        "SELECT report FROM weekly_reports WHERE created_at < ? ORDER BY created_at DESC, id DESC"
        " LIMIT 1",
        (now.isoformat(),),
    ).fetchone()
    return WeeklyReport.model_validate_json(r["report"]) if r else None


def _backups(db: sqlite3.Connection, now: datetime) -> BackupLine:
    found = backup.status(db)
    last, test = found.last_backup, found.last_restore_test
    problems = test.stats.get("problems") if test else None
    warnings = test.stats.get("warnings") if test else None
    size = last.stats.get("bytes") if last else None
    fresh = last is not None and now - last.at <= STALE_BACKUP
    return BackupLine(
        ok=fresh and test is not None and test.ok,
        last_backup_at=last.at if last else None,
        last_backup_bytes=size if isinstance(size, int) else None,
        restore_ok=test.ok if test else None,
        restore_at=test.at if test else None,
        restore_problems=[str(p) for p in problems] if isinstance(problems, list) else [],
        schedule_warnings=[str(w) for w in warnings] if isinstance(warnings, list) else [],
    )


def build(db: sqlite3.Connection, base: Catalog, media_dir: Path, now: datetime) -> WeeklyReport:
    tz = ZoneInfo(base.channels.timezone)
    since = now - timedelta(days=7)
    effective = apply_overlay(base, store.head(db).overlay)
    library = load_library(db, allow_short=os.environ.get("RC_ALLOW_SHORT_MEDIA") == "1")
    titles = {p[0].show: p[0].title for p in library.episodes.values() if p}
    titles |= {c.show: c.title for c in effective.carriage.carriage if c.title}

    station = None
    for run in runs.recent(db, 10):
        if since < run.created_at <= now:
            station = StationLine(
                id=run.id, status=run.status, summary=run.summary, week_of=run.week_of
            )
            break
    pending = []
    if station is not None and station.status == "proposed":
        pending.append("The station manager's proposal waits for you in /admin, Station.")

    playable = {s for s, eps in library.episodes.items() if eps}
    empty = {
        c.show: titles.get(c.show, c.show)
        for c in effective.carriage.carriage
        if c.show not in (library.movies if c.kind == "movie" else playable)
    }
    episodes = sum(len(eps) for eps in library.episodes.values())
    previous = _previous(db, now)
    lib = LibraryLine(
        episodes=episodes,
        shows=len(playable),
        movies=len(library.movies),
        episodes_added=episodes - previous.library.episodes if previous else None,
        empty_count=len(empty),
        empty=sorted(empty.values(), key=str.casefold)[:EMPTY_LIST],
    )

    conversion = None
    found = read_progress(media_dir, now)
    if found is not None:
        left = found["total"] - found["done"]
        per = median(found["recent_seconds"]) if found["recent_seconds"] else None
        conversion = ConversionLine(
            done=found["done"],
            total=found["total"],
            left=left,
            failed=found["failed"],
            hours_left=round(left * per / 3600, 1) if per else None,
        )

    report = WeeklyReport(
        week_of=week_of(since, tz).isoformat(),
        since=since,
        until=now,
        viewing=_viewing(db, base, since, now, tz, titles),
        station=station,
        pending=pending,
        library=lib,
        conversion=conversion,
        backups=_backups(db, now),
        headline=[],
    )
    report.headline = headline(report, now)
    return report


def headline(r: WeeklyReport, now: datetime) -> list[str]:
    v = r.viewing
    if v.seconds == 0:
        lines = ["Nothing watched this week."]
    else:
        last = (
            f"last week {_duration(v.last_week_seconds)}"
            if v.last_week_seconds
            else ("nothing last week")
        )
        top = v.top_shows[0]
        lines = [f"Watched {_duration(v.seconds)} ({last}), most {top.title} on {top.channel}."]
    if r.station is None:
        lines.append("Station manager: didn't run this week.")
    else:
        lines.append(f"Station manager: {r.station.status.replace('_', ' ')}, {r.station.summary}")
    lib = r.library
    added = lib.episodes_added
    change = f" ({added:+d})" if added else ""
    lines.append(
        f"On the air: {lib.episodes:,} episodes{change} of {lib.shows} shows, {lib.movies} movies;"
        f" {lib.empty_count} carried titles have nothing yet."
    )
    c = r.conversion
    if c is not None:
        if c.left <= 0:
            lines.append(f"Conversion: all {c.total:,} files done.")
        else:
            eta = f", about {c.hours_left:g} h" if c.hours_left is not None else ""
            failed = f"; {c.failed} failed" if c.failed else ""
            lines.append(f"Conversion: {c.left:,} files left{eta}{failed}.")
    lines += r.pending
    b = r.backups
    if b.last_backup_at is None:
        lines.append("No backup yet.")
    elif now - b.last_backup_at > STALE_BACKUP:
        lines.append(f"Backups: last {_ago(b.last_backup_at, now)}: none since!")
    elif b.restore_ok:
        lines.append(f"Backups: last {_ago(b.last_backup_at, now)}, restore test passed.")
    else:
        why = "; ".join(b.restore_problems[:2]) or "not run"
        lines.append(f"Backups: last {_ago(b.last_backup_at, now)}, restore test FAILED: {why}")
    if b.schedule_warnings:
        n = len(b.schedule_warnings)
        lines.append(
            f"Schedule checks: {n} warning{'s' if n != 1 else ''}, such as {b.schedule_warnings[0]}"
        )
    return lines


def save(db: sqlite3.Connection, report: WeeklyReport, now: datetime) -> int:
    cur = db.execute(
        "INSERT INTO weekly_reports (created_at, week_of, report) VALUES (?, ?, ?)",
        (now.isoformat(), report.week_of, report.model_dump_json()),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def recent(db: sqlite3.Connection, limit: int = 10) -> list[StoredReport]:
    rows = db.execute(
        "SELECT * FROM weekly_reports ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
    )
    return [
        StoredReport(
            id=r["id"],
            created_at=datetime.fromisoformat(r["created_at"]),
            week_of=r["week_of"],
            report=WeeklyReport.model_validate_json(r["report"]),
        )
        for r in rows
    ]


Post = Callable[[str, dict[str, str], bytes], None]


def _post(url: str, headers: dict[str, str], body: bytes) -> None:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15):  # a LAN URL from ops/.env
        pass


def push(report: WeeklyReport, env: Mapping[str, str], post: Post = _post) -> bool:
    """Send the headline to ntfy; False when RC_NTFY_URL isn't set. Raises OSError."""
    url = env.get("RC_NTFY_URL", "")
    if not url:
        return False
    sunday = datetime.fromisoformat(report.week_of)
    headers = {
        "Title": f"Retro Cable, week of {sunday:%b} {sunday.day}",
        "Tags": "tv",
        "Priority": "3" if report.backups.ok else "4",
    }
    if env.get("RC_REPORT_LINK"):
        headers["Click"] = env["RC_REPORT_LINK"]
    if env.get("RC_NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {env['RC_NTFY_TOKEN']}"
    post(url, headers, "\n".join(report.headline).encode())
    return True


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m retro_cable.report")
    p.add_argument("job", choices=["run"])
    p.add_argument("--db", type=Path, default=None, help="default RC_DB or RC_DATA_DIR")
    p.add_argument(
        "--catalog", type=Path, default=Path(os.environ.get("RC_CATALOG_DIR", "catalog"))
    )
    p.add_argument("--media", type=Path, default=Path(os.environ.get("RC_MEDIA_DIR", "media")))
    p.add_argument("--no-push", action="store_true", help="save it only; don't send it")
    return p


def main(
    argv: list[str] | None = None,
    now: datetime | None = None,
    post: Post = _post,
) -> int:
    args = _parser().parse_args(argv)
    when = now or datetime.now(UTC)
    try:
        base = load_catalog(args.catalog)
    except CatalogError as e:
        print(e, file=sys.stderr)
        return 1
    db = connect(args.db or default_db())
    try:
        migrate(db)
        report = build(db, base, args.media, when)
        save(db, report, when)
    finally:
        db.close()
    print(f"report for the week of {report.week_of}:")
    for line in report.headline:
        print(f"  {line}")
    if args.no_push:
        return 0
    try:
        sent = push(report, os.environ, post)
    except OSError as e:
        print(f"couldn't push the report to ntfy: {type(e).__name__}", file=sys.stderr)
        return 1
    print("pushed to ntfy" if sent else "RC_NTFY_URL isn't set; not pushed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
