"""The Sunday report (Phase 8): what happened this week, on a test database. No network."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from retro_cable import backup, report
from retro_cable.app import Settings, create_app
from retro_cable.catalog import load_catalog
from retro_cable.db import connect, migrate
from retro_cable.station_manager import runs
from retro_cable.station_manager.runs import RunReport
from retro_cable.station_manager.signals import compute
from tests.conftest import MakeCatalog
from tests.station_manager.conftest import watch
from tests.test_api_tv import seed_db

# Sunday Nov 15 2026, 8:00 AM in New York: when the timer fires.
NOW = datetime(2026, 11, 15, 13, 0, tzinfo=UTC)
TUESDAY_EVENING = datetime(2026, 11, 11, 1, 0, tzinfo=UTC)  # Tue Nov 10, 8 PM in New York


@pytest.fixture
def setup(tmp_path: Path, make_catalog: MakeCatalog) -> tuple[Path, Path, Path]:
    media = tmp_path / "media"
    media.mkdir()
    db_path = tmp_path / "rc.db"
    seed_db(db_path, media)
    return db_path, make_catalog(), media


def build(setup: tuple[Path, Path, Path], now: datetime = NOW) -> report.WeeklyReport:
    db_path, catalog, media = setup
    db = connect(db_path)
    try:
        return report.build(db, load_catalog(catalog), media, now)
    finally:
        db.close()


def progress(media: Path, **over: Any) -> None:
    data = {
        "format": "retro-cable-progress/1",
        "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
        "state": "running", "encoder": "gpu", "gpu_busy": False, "current": "b",
        "recent_seconds": [50.0, 70.0, 60.0],
        "groups": [
            {"key": "a", "title": "A", "done": 30, "total": 30, "failed": 0},
            {"key": "b", "title": "B", "done": 10, "total": 130, "failed": 3},
        ],
    } | over  # fmt: skip
    (media / "progress.json").write_text(json.dumps(data))


# --- what it says -----------------------------------------------------------------------------


def test_an_empty_week_says_so(setup: tuple[Path, Path, Path]) -> None:
    got = build(setup)
    assert got.week_of == "2026-11-08"
    assert got.viewing.seconds == 0 and got.viewing.top_shows == []
    assert got.station is None
    assert got.conversion is None
    assert got.backups.last_backup_at is None and not got.backups.ok
    assert "Nothing watched this week." in got.headline
    assert "No backup yet." in got.headline


def test_viewing_this_week_against_last_week(setup: tuple[Path, Path, Path]) -> None:
    db = connect(setup[0])
    watch(db, "ktvu", "the-simpsons", 1320, TUESDAY_EVENING)
    watch(db, "ktvu", "the-simpsons", 1320, TUESDAY_EVENING + timedelta(days=1))
    watch(db, "cartoon-network", "dexters-laboratory", 600, TUESDAY_EVENING, ended="tune_away")
    watch(db, "ktvu", "the-simpsons", 900, TUESDAY_EVENING - timedelta(days=7))  # last week
    watch(db, "ktvu", "the-simpsons", 5000, NOW + timedelta(minutes=5))  # after the report
    db.close()
    got = build(setup)
    assert got.viewing.seconds == 1320 * 2 + 600
    assert got.viewing.last_week_seconds == 900
    top = got.viewing.top_shows[0]
    assert (top.title, top.channel, top.seconds) == ("The Simpsons", "KTVU (Fox)", 2640)
    assert got.viewing.top_channels[0].name == "KTVU (Fox)"
    assert got.viewing.busiest_hour == "20:00"
    assert "Watched 54 min (last week 15 min), most The Simpsons on KTVU (Fox)." in got.headline


def test_the_week_s_station_manager_run_and_what_waits_for_approval(
    setup: tuple[Path, Path, Path],
) -> None:
    db = connect(setup[0])
    signals = compute([], ZoneInfo("America/New_York"))
    empty = RunReport(weeks=4, since=NOW, until=NOW, signals=signals)
    runs.record(db, NOW - timedelta(days=9), "2026-11-01", "applied", "m", "old run", empty)
    runs.record(db, NOW - timedelta(hours=6), "2026-11-15", "proposed", "m",
                "3 changes proposed", empty)  # fmt: skip
    db.close()
    got = build(setup)
    assert got.station is not None
    assert (got.station.status, got.station.summary) == ("proposed", "3 changes proposed")
    assert got.pending == ["The station manager's proposal waits for you in /admin, Station."]
    assert "Station manager: proposed, 3 changes proposed" in got.headline


def test_what_is_on_the_air_and_the_carried_titles_with_nothing_yet(
    setup: tuple[Path, Path, Path],
) -> None:
    got = build(setup)
    assert got.library.episodes == 250
    assert got.library.shows == 2
    assert got.library.movies == 0
    assert got.library.episodes_added is None  # no report to compare with
    assert got.library.empty_count == 4
    assert got.library.empty == ["Dragon Ball Z", "Gravity Falls", "Halloweentown", "X-Men"]
    assert "On the air: 250 episodes of 2 shows, 0 movies; 4 carried titles have nothing yet." in (
        got.headline
    )


def test_episodes_added_since_the_last_report(setup: tuple[Path, Path, Path]) -> None:
    db_path = setup[0]
    first = build(setup, NOW - timedelta(days=7))
    db = connect(db_path)
    report.save(db, first, NOW - timedelta(days=7))
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, air_date, runtime_seconds,"
        " file_path) VALUES ('the-simpsons', 30, 1, 'New', '2000-01-01', 1320, 'n.mp4')"
    )
    db.close()
    got = build(setup)
    assert got.library.episodes_added == 1
    assert "On the air: 251 episodes (+1)" in got.headline[2]


def test_conversion_progress_and_how_long_is_left(setup: tuple[Path, Path, Path]) -> None:
    progress(setup[2])
    got = build(setup)
    assert got.conversion is not None
    assert (got.conversion.done, got.conversion.total, got.conversion.left) == (40, 160, 120)
    assert got.conversion.failed == 3
    assert got.conversion.hours_left == 2.0  # 120 files at a median of 60 s
    assert "Conversion: 120 files left, about 2 h; 3 failed." in got.headline


def test_backups_are_good_only_when_recent_and_restore_tested(
    setup: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = setup[0]
    when = NOW - timedelta(hours=12)
    made = backup.take(db_path, tmp_path / "b", when)
    db = connect(db_path)
    backup._record(db, "backup", when, {"file": made.name, "bytes": 2048})
    backup._record(db, "restore-test", when, {"ok": True, "problems": [], "warnings": [
        "kqed: Sat Nov 11 2000: Some Movie would air before its first air date.",
        "kqed: Fri Nov 11 2005: Some Movie would air before its first air date.",
    ]})  # fmt: skip
    db.close()
    got = build(setup)
    assert got.backups.ok
    assert got.backups.last_backup_bytes == 2048
    assert len(got.backups.schedule_warnings) == 2
    assert "Backups: last 12 h ago, restore test passed." in got.headline
    assert (
        "Schedule checks: 2 warnings, such as kqed: Sat Nov 11 2000: Some Movie would air before"
        " its first air date." in got.headline
    )

    later = build(setup, NOW + timedelta(days=2))  # nothing since
    assert not later.backups.ok
    assert "Backups: last 2 days ago: none since!" in later.headline

    db = connect(db_path)
    backup._record(db, "restore-test", NOW, {"ok": False, "problems": ["integrity check"]})
    db.close()
    failed = build(setup)
    assert not failed.backups.ok
    assert failed.backups.restore_problems == ["integrity check"]


# --- kept and sent ----------------------------------------------------------------------------


def test_a_saved_report_is_listed_newest_first_in_admin(
    setup: tuple[Path, Path, Path],
) -> None:
    db_path, catalog, media = setup
    db = connect(db_path)
    report.save(db, build(setup, NOW - timedelta(days=7)), NOW - timedelta(days=7))
    report.save(db, build(setup), NOW)
    db.close()
    settings = Settings(catalog_dir=catalog, db_path=db_path, media_dir=media,
                        media_secret="test-secret", seed=7)  # fmt: skip
    with TestClient(create_app(settings)) as client:
        got = client.get("/admin/reports", params={"limit": 5}).json()
    assert [r["week_of"] for r in got["reports"]] == ["2026-11-08", "2026-11-01"]
    assert got["reports"][0]["report"]["library"]["episodes"] == 250
    assert got["schedule"] == "Sundays at 8 AM"


class FakeNtfy:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, dict[str, str], str]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> None:
        if self.fail:
            raise OSError("connection refused")
        self.sent.append((url, headers, body.decode()))


def test_the_report_is_pushed_to_ntfy_with_a_link_to_admin(
    setup: tuple[Path, Path, Path],
) -> None:
    got = build(setup)
    post = FakeNtfy()
    env = {"RC_NTFY_URL": "http://ntfy.lan/retro-cable", "RC_NTFY_TOKEN": "tk_secret",
           "RC_REPORT_LINK": "https://tv.home.tabaska.us/admin"}  # fmt: skip
    assert report.push(got, env, post)
    [(url, headers, body)] = post.sent
    assert url == "http://ntfy.lan/retro-cable"
    assert headers["Title"] == "Retro Cable, week of Nov 8"
    assert headers["Click"] == "https://tv.home.tabaska.us/admin"
    assert headers["Authorization"] == "Bearer tk_secret"
    assert headers["Priority"] == "4"  # no backup yet: worth a look
    assert body == "\n".join(got.headline)


def test_no_ntfy_settings_means_no_push(setup: tuple[Path, Path, Path]) -> None:
    post = FakeNtfy()
    assert report.push(build(setup), {}, post) is False
    assert post.sent == []


def test_the_job_saves_then_pushes_and_fails_if_the_push_does(
    setup: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, catalog, media = setup
    monkeypatch.setenv("RC_NTFY_URL", "http://ntfy.lan/retro-cable")
    monkeypatch.setenv("RC_NTFY_TOKEN", "tk_secret")
    argv = ["run", "--db", str(db_path), "--catalog", str(catalog), "--media", str(media)]
    assert report.main(argv, NOW, FakeNtfy(fail=True)) == 1
    out = capsys.readouterr()
    assert "tk_secret" not in out.out + out.err
    assert "couldn't push" in out.err
    db = connect(db_path)
    migrate(db)
    assert len(report.recent(db)) == 1  # saved before the push
    db.close()
    assert report.main([*argv, "--no-push"], NOW, FakeNtfy(fail=True)) == 0
