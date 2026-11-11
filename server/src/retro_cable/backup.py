"""Nightly database backups and their restore test (Phase 8):
`python -m retro_cable.backup JOB [options]`.

  take     copy the live database with SQLite's backup API (consistent while the api writes,
           WAL included), gzip it to --dest as retro-cable-<UTC time>.sqlite.gz, prune --dest
           by the retention rule, and print the file's name
  expired  read backup names on stdin and print the ones the retention rule drops (the host
           prunes the NAS copies this way: the container can't see them)
  verify   the restore test, on a backup file or `-` for stdin: unzip it to a scratch
           directory, check its integrity, migrate it, and build a day on every channel from
           the catalog and its /admin overlay with the scheduler checks. Exit 1 if it fails.

`take` and `verify` record their result in the live database's import_runs (sources `backup`
and `restore-test`); the restored copy is thrown away. On the mini ops/backup.sh runs them in
the api container every night and keeps the copies on the NAS (docs/specs/ops.md).
"""

import argparse
import gzip
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from zoneinfo import ZoneInfo

from retro_cable.admin import store
from retro_cable.admin.drafts import smoke_test
from retro_cable.catalog import Catalog, CatalogError, load_catalog, validate
from retro_cable.catalog.overlay import apply_overlay
from retro_cable.channel_builder.__main__ import default_db
from retro_cable.channel_builder.proposal import safe_day
from retro_cable.db import connect, migrate
from retro_cable.scheduler import Scheduler, load_library

NAME = re.compile(r"retro-cable-(\d{8}T\d{6}Z)\.sqlite\.gz")
KEEP_DAYS = 14  # every backup this recent
KEEP_WEEKS = 8  # then the newest of each ISO week
KEEP_MONTHS = 12  # then the newest of each month
TEST_YEARS = (1995, 2000, 2005)  # the restore test builds today's date in these years
COUNTED = ("shows", "episodes", "movies", "catalog_revisions", "watch_sessions", "station_runs")


def _stamp(name: str) -> datetime | None:
    m = NAME.fullmatch(name)
    return datetime.strptime(m[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) if m else None


def expired(names: Iterable[str], now: datetime) -> list[str]:
    """The backups the retention rule drops: everything from the last KEEP_DAYS days stays,
    then the newest of each week for KEEP_WEEKS weeks and of each month for KEEP_MONTHS
    months. The newest backup always stays, and names it didn't make are never listed."""
    dated = sorted(((t, n) for n in names if (t := _stamp(n))), reverse=True)
    keep = {dated[0][1]} if dated else set()
    weeks: set[tuple[int, int]] = set()
    months: set[tuple[int, int]] = set()
    for when, name in dated:  # newest first, so the first of a week or month is its newest
        age = now - when
        week, month = when.isocalendar()[:2], (when.year, when.month)
        if age < timedelta(days=KEEP_DAYS):
            keep.add(name)
        if age < timedelta(weeks=KEEP_WEEKS) and week not in weeks:
            keep.add(name)
        if age < timedelta(days=31 * KEEP_MONTHS) and month not in months:
            keep.add(name)
        weeks.add(week)
        months.add(month)
    return [n for _, n in sorted(dated) if n not in keep]


def take(db_path: Path, dest: Path, now: datetime, *, create: bool = True) -> Path:
    """Back up the database to dest (written as .part, then renamed) and prune dest."""
    if create:
        dest.mkdir(parents=True, exist_ok=True)
    elif not dest.is_dir():
        raise FileNotFoundError(f"{dest} doesn't exist (is the share mounted?)")
    out = dest / f"retro-cable-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}.sqlite.gz"
    with tempfile.TemporaryDirectory(prefix="rc-backup-") as scratch:
        copy = Path(scratch) / "copy.db"
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        part = out.with_name(out.name + ".part")
        with copy.open("rb") as f, gzip.open(part, "wb") as z:
            shutil.copyfileobj(f, z)
    part.replace(out)
    for gone in expired((p.name for p in dest.iterdir()), now):
        (dest / gone).unlink(missing_ok=True)
    return out


@dataclass
class RestoreTest:
    ok: bool
    problems: list[str] = field(default_factory=list)
    schema: int | None = None
    revision: int | None = None
    channels: int = 0
    counts: dict[str, int] = field(default_factory=dict)


def _unzip(data: BinaryIO, out: Path) -> str | None:
    """Write the backup's database to out; a problem, or None."""
    try:
        with gzip.open(data, "rb") as z, out.open("wb") as f:
            shutil.copyfileobj(z, f)
    except (OSError, EOFError, zlib.error) as e:
        return f"the backup isn't a gzipped backup, or is cut short ({e})"
    if out.stat().st_size == 0:
        return "the backup is empty"
    return None


def restore_test(data: BinaryIO, base: Catalog, today: date, seed: int) -> RestoreTest:
    """Restore a backup into a scratch directory and check that the app could run on it."""
    with tempfile.TemporaryDirectory(prefix="rc-restore-") as scratch:
        path = Path(scratch) / "restored.db"
        problem = _unzip(data, path)
        if problem:
            return RestoreTest(ok=False, problems=[problem])
        try:
            db = connect(path)
        except sqlite3.DatabaseError as e:
            return RestoreTest(ok=False, problems=[f"the backup isn't a readable database ({e})"])
        try:
            return _check(db, base, today, seed)
        except sqlite3.DatabaseError as e:
            return RestoreTest(ok=False, problems=[f"the backup isn't a readable database ({e})"])
        finally:
            db.close()


def _check(db: sqlite3.Connection, base: Catalog, today: date, seed: int) -> RestoreTest:
    integrity = [r[0] for r in db.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        return RestoreTest(ok=False, problems=[f"integrity check: {'; '.join(integrity[:5])}"])
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    schema = (
        db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        if "schema_migrations" in tables
        else None
    )
    migrate(db)  # a backup from before the running code's migrations must still open
    test = RestoreTest(ok=True, schema=schema)
    test.counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in COUNTED}
    head = store.head(db)
    test.revision = head.id
    merged = apply_overlay(base, head.overlay)
    test.problems = [f"catalog: {p}" for p in validate(merged)]
    if test.problems:
        test.ok = False
        return test
    sched = Scheduler(merged, load_library(db), seed=seed)
    days = [safe_day(y, today.month, today.day) for y in TEST_YEARS]
    for channel in merged.channels.channels:
        test.problems += [f"{channel.id}: {p}" for p in smoke_test(sched, channel, days)]
        test.channels += 1
    test.ok = not test.problems
    return test


@dataclass(frozen=True)
class Event:
    at: datetime
    ok: bool
    stats: dict[str, object]


@dataclass(frozen=True)
class Status:
    last_backup: Event | None
    last_restore_test: Event | None


def _record(db: sqlite3.Connection, source: str, now: datetime, stats: dict[str, object]) -> None:
    db.execute(
        "INSERT INTO import_runs (source, started_at, finished_at, stats) VALUES (?, ?, ?, ?)",
        (source, now.isoformat(), now.isoformat(), json.dumps(stats)),
    )


def _last(db: sqlite3.Connection, source: str) -> Event | None:
    r = db.execute(
        "SELECT started_at, stats FROM import_runs WHERE source = ? ORDER BY id DESC LIMIT 1",
        (source,),
    ).fetchone()
    if r is None:
        return None
    stats = json.loads(r["stats"] or "{}")
    return Event(datetime.fromisoformat(r["started_at"]), bool(stats.get("ok", True)), stats)


def status(db: sqlite3.Connection) -> Status:
    """The last backup and the last restore test, as recorded in the live database."""
    return Status(_last(db, "backup"), _last(db, "restore-test"))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m retro_cable.backup")
    p.add_argument("job", choices=["take", "expired", "verify"])
    p.add_argument("file", nargs="?", help="verify: the backup to test, or - for stdin")
    p.add_argument("--db", type=Path, default=None, help="default RC_DB or RC_DATA_DIR")
    p.add_argument("--dest", type=Path, help="take: the directory backups go to")
    p.add_argument("--name", help="verify: the backup's name to record (for stdin)")
    p.add_argument(
        "--catalog", type=Path, default=Path(os.environ.get("RC_CATALOG_DIR", "catalog"))
    )
    p.add_argument("--seed", type=int, default=int(os.environ.get("RC_SEED", "0")))
    return p


def main(
    argv: list[str] | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = _parser().parse_args(argv)
    when = now or clock()
    if args.job == "expired":
        print("\n".join(expired(sys.stdin.read().split(), when)))
        return 0
    db_path = args.db or default_db()
    if args.job == "take":
        if args.dest is None:
            print("take needs --dest", file=sys.stderr)
            return 2
        made = take(db_path, args.dest, when)
        db = connect(db_path)
        try:
            _record(db, "backup", when, {"file": made.name, "bytes": made.stat().st_size})
        finally:
            db.close()
        print(made.name)
        return 0
    if not args.file:
        print("give the backup to verify, or - for stdin", file=sys.stderr)
        return 2
    try:
        base = load_catalog(args.catalog)
    except CatalogError as e:
        print(e, file=sys.stderr)
        return 1
    today = when.astimezone(ZoneInfo(base.channels.timezone)).date()
    if args.file == "-":
        result = restore_test(sys.stdin.buffer, base, today, args.seed)
    else:
        with open(args.file, "rb") as f:
            result = restore_test(f, base, today, args.seed)
    db = connect(db_path)
    try:
        migrate(db)
        _record(db, "restore-test", when, {**result.__dict__, "file": args.name or args.file})
    finally:
        db.close()
    verdict = "passed" if result.ok else "FAILED"
    print(f"restore test {verdict}: schema {result.schema}, /admin revision {result.revision},"
          f" {result.channels} channels, {result.counts}")  # fmt: skip
    for p in result.problems:
        print(f"  {p}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
