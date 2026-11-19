"""Nightly database backups and the restore test (Phase 8). Test databases only."""

import gzip
import io
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from retro_cable import backup
from retro_cable.admin import store
from retro_cable.catalog.overlay import Overlay
from retro_cable.db import MIGRATIONS, connect, migrate
from tests.conftest import CARRIAGE, MakeCatalog
from tests.test_api_tv import seed_db

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)  # 4:00 AM in New York, when the timer fires


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    media = tmp_path / "media"
    media.mkdir()
    path = tmp_path / "data" / "retro-cable.db"
    path.parent.mkdir()
    seed_db(path, media)
    return path


def runs(db_path: Path, source: str) -> list[dict[str, object]]:
    db = connect(db_path)
    try:
        rows = db.execute(
            "SELECT stats FROM import_runs WHERE source = ? ORDER BY id", (source,)
        ).fetchall()
        return [json.loads(r[0]) for r in rows]
    finally:
        db.close()


def restored(path: Path, tmp_path: Path) -> sqlite3.Connection:
    out = tmp_path / "restored.db"
    out.write_bytes(gzip.decompress(path.read_bytes()))
    return sqlite3.connect(out)


# --- taking a backup --------------------------------------------------------------------------


def test_a_backup_is_a_gzipped_copy_named_by_its_utc_time(db_path: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backups"
    made = backup.take(db_path, dest, NOW)
    assert made == dest / "retro-cable-20261112T090000Z.sqlite.gz"
    copy = restored(made, tmp_path)
    assert copy.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 250
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not list(dest.glob("*.part"))


def test_a_backup_holds_writes_still_in_the_wal(db_path: Path, tmp_path: Path) -> None:
    """The api keeps the database open in WAL mode; a file copy would miss what is only in the
    -wal file. SQLite's backup API copies the database as the api sees it."""
    live = connect(db_path)
    live.execute("PRAGMA wal_autocheckpoint = 0")
    live.execute("INSERT INTO shows (id, title) VALUES ('x-men', 'X-Men')")
    try:
        copy = restored(backup.take(db_path, tmp_path / "b", NOW), tmp_path)
        assert copy.execute("SELECT title FROM shows WHERE id = 'x-men'").fetchone() == ("X-Men",)
    finally:
        live.close()


def test_a_backup_is_recorded_in_the_live_database(db_path: Path, tmp_path: Path) -> None:
    assert backup.main(["take", "--db", str(db_path), "--dest", str(tmp_path / "b")], NOW) == 0
    [stats] = runs(db_path, "backup")
    assert stats["file"] == "retro-cable-20261112T090000Z.sqlite.gz"
    assert isinstance(stats["bytes"], int) and stats["bytes"] > 0


def test_take_refuses_a_destination_that_does_not_exist_when_asked(
    db_path: Path, tmp_path: Path
) -> None:
    missing = tmp_path / "nas" / "retro-cable"
    with pytest.raises(FileNotFoundError):
        backup.take(db_path, missing, NOW, create=False)
    assert not missing.exists()


# --- keeping backups --------------------------------------------------------------------------


def name(when: datetime) -> str:
    return f"retro-cable-{when:%Y%m%dT%H%M%SZ}.sqlite.gz"


def test_retention_keeps_two_weeks_of_nights_then_weeks_then_months() -> None:
    nightly = [NOW - timedelta(days=d) for d in range(400)]
    names = [name(t) for t in nightly]
    gone = set(backup.expired(names, NOW))
    kept = [t for t in nightly if name(t) not in gone]
    # Every night of the last 14 days.
    assert all(name(NOW - timedelta(days=d)) not in gone for d in range(14))
    # Beyond that, one a week for 8 weeks and one a month for 12 months.
    recent_weeks = {t.isocalendar()[:2] for t in nightly[14 : 7 * 8]}
    assert recent_weeks <= {t.isocalendar()[:2] for t in kept}
    assert all(t > NOW - timedelta(days=366) for t in kept)
    assert len(kept) <= 14 + 8 + 12
    assert len({(t.year, t.month) for t in kept if t <= NOW - timedelta(weeks=8)}) >= 9


def test_retention_never_removes_the_newest_or_files_it_did_not_make() -> None:
    ancient = name(NOW - timedelta(days=900))
    assert backup.expired([ancient], NOW) == []  # the only backup there is
    others = ["notes.txt", "retro-cable.db", "retro-cable-latest.sqlite.gz", ancient]
    assert backup.expired([*others, name(NOW)], NOW) == [ancient]


def test_take_prunes_its_destination(db_path: Path, tmp_path: Path) -> None:
    dest = tmp_path / "b"
    dest.mkdir()
    old = dest / name(NOW - timedelta(days=500))
    old.write_bytes(b"old")
    (dest / "keep-me.txt").write_text("not a backup")
    backup.take(db_path, dest, NOW)
    assert not old.exists()
    assert (dest / "keep-me.txt").exists()


def test_expired_reads_names_and_prints_the_ones_to_remove(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host prunes the NAS copies by the same rule: it pipes their names in."""
    old, new = name(NOW - timedelta(days=500)), name(NOW)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{old}\n{new}\n"))
    assert backup.main(["expired"], NOW) == 0
    assert capsys.readouterr().out.split() == [old]


# --- the restore test -------------------------------------------------------------------------


def verify(
    db_path: Path, catalog: Path, data: bytes, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, list[dict[str, object]]]:
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(data)))
    code = backup.main(["verify", "-", "--db", str(db_path), "--catalog", str(catalog)], NOW)
    return code, runs(db_path, "restore-test")


def test_a_good_backup_restores_and_schedules_every_channel(
    db_path: Path, tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = connect(db_path)
    rev = store.save(db, Overlay(), "an admin edit", NOW, expect_head=0)
    db.close()
    made = backup.take(db_path, tmp_path / "b", NOW)
    code, [stats] = verify(db_path, make_catalog(), made.read_bytes(), monkeypatch)
    assert code == 0
    assert stats["ok"] is True and stats["problems"] == []
    assert stats["revision"] == rev.id
    assert stats["schema"] == MIGRATIONS[-1].version
    counts = stats["counts"]
    assert isinstance(counts, dict) and counts["episodes"] == 250
    assert isinstance(stats["channels"], int) and stats["channels"] >= 2


def test_an_older_backup_is_migrated_before_the_test(
    tmp_path: Path, make_catalog: MakeCatalog, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "old.db"
    db = connect(old)
    migrate(db, MIGRATIONS[:-1])
    db.close()
    made = backup.take(old, tmp_path / "b", NOW)
    code, [stats] = verify(db_path, make_catalog(), made.read_bytes(), monkeypatch)
    assert code == 0, stats
    assert stats["schema"] == MIGRATIONS[-2].version


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"not gzip at all", "isn't a gzipped backup"),
        (gzip.compress(b"SQLite format 3\x00" + b"\x00" * 200), "isn't a readable database"),
        (b"", "is empty"),
    ],
)
def test_a_broken_backup_fails_the_restore_test(
    data: bytes,
    problem: str,
    db_path: Path,
    make_catalog: MakeCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, [stats] = verify(db_path, make_catalog(), data, monkeypatch)
    assert code == 1
    assert stats["ok"] is False
    problems = stats["problems"]
    assert isinstance(problems, list) and problem in problems[0]


def test_a_truncated_backup_fails_the_restore_test(
    db_path: Path, tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = backup.take(db_path, tmp_path / "b", NOW).read_bytes()
    code, [stats] = verify(db_path, make_catalog(), made[: len(made) // 2], monkeypatch)
    assert code == 1 and stats["ok"] is False


def test_schedule_problems_in_the_data_are_warnings_not_a_failed_restore(
    db_path: Path, tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A movie with no date at all airs in any year (the scheduler allows it), which the /admin smoke test flags. That is
    the data's problem, not the backup's: recorded as a warning, and the job still passes."""
    db = connect(db_path)
    db.execute(
        "INSERT INTO movies (id, title, runtime_seconds, file_path)"
        " VALUES ('the-lion-king', 'The Lion King', 5340, 'k.mp4')"
    )
    db.close()
    carriage = CARRIAGE + "  - { show: the-lion-king, title: The Lion King, channel: disney-movies,"
    carriage += " from: 1990-01-01, kind: movie }\n"
    made = backup.take(db_path, tmp_path / "b", NOW)
    code, [stats] = verify(db_path, make_catalog(carriage=carriage), made.read_bytes(), monkeypatch)
    assert code == 0
    assert stats["ok"] is True and stats["problems"] == []
    warnings = stats["warnings"]
    assert isinstance(warnings, list) and warnings
    assert all("The Lion King would air before its first air date" in w for w in warnings)


def test_the_restore_test_never_touches_the_live_database_beyond_its_record(
    db_path: Path, tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = backup.take(db_path, tmp_path / "b", NOW)
    other = tmp_path / "other.db"
    seed_db(other, tmp_path / "media")
    db = connect(other)
    db.execute("DELETE FROM episodes")
    db.close()
    code, _ = verify(other, make_catalog(), made.read_bytes(), monkeypatch)
    assert code == 0
    db = connect(other)
    assert db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0
    db.close()


def test_status_reports_the_last_backup_and_restore_test(
    db_path: Path, tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = connect(db_path)
    assert backup.status(db) == backup.Status(last_backup=None, last_restore_test=None)
    db.close()
    made = backup.take(db_path, tmp_path / "b", NOW)
    backup.main(["take", "--db", str(db_path), "--dest", str(tmp_path / "b")], NOW)
    verify(db_path, make_catalog(), made.read_bytes(), monkeypatch)
    db = connect(db_path)
    got = backup.status(db)
    db.close()
    assert got.last_backup is not None and got.last_backup.at == NOW
    assert got.last_restore_test is not None and got.last_restore_test.ok
