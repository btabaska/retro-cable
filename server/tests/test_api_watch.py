"""POST /watch: the web app reports what was watched (M5). Small, validated, privacy-boring."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from retro_cable.app import Settings, create_app
from retro_cable.db import connect
from tests.conftest import MakeCatalog
from tests.test_api_tv import seed_db

START = datetime(2026, 11, 1, 1, 0, tzinfo=UTC)  # 9:00 PM Oct 31 in New York


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    media = tmp_path / "media"
    media.mkdir()
    path = tmp_path / "rc.db"
    seed_db(path, media)
    return path


@pytest.fixture
def client(make_catalog: MakeCatalog, db_path: Path, clock: Clock) -> TestClient:
    settings = Settings(
        catalog_dir=make_catalog(), db_path=db_path, media_dir=db_path.parent / "media",
        media_secret="s", seed=7,
    )  # fmt: skip
    return TestClient(create_app(settings, clock=clock))


def report(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "session": "abcd1234efgh",
        "channel": "ktvu",
        "item": "the-simpsons",
        "kind": "episode",
        "season": 4,
        "episode": 12,
        "year": 1995,
        "offset": 30,
        "seconds": 60,
        "runtime": 1320,
        "ended": None,
    }
    body.update(over)
    return body


def rows(db_path: Path) -> list[dict[str, Any]]:
    db = connect(db_path)
    try:
        return [dict(r) for r in db.execute("SELECT * FROM watch_sessions ORDER BY id")]
    finally:
        db.close()


def test_a_session_is_recorded_and_heartbeats_update_it(
    client: TestClient, db_path: Path, clock: Clock
) -> None:
    assert client.post("/watch", json=report()).status_code == 204
    [row] = rows(db_path)
    assert row["channel"] == "ktvu" and row["item"] == "the-simpsons"
    assert (row["season"], row["episode"]) == (4, 12)
    assert row["seconds"] == 60 and row["runtime_seconds"] == 1320 and row["offset_seconds"] == 30
    # The server stamps the real start (now minus what was watched) and the simulated date.
    assert row["started_at"] == (START - timedelta(seconds=60)).isoformat()
    assert row["sim_date"] == "1995-10-31"
    assert row["ended"] is None and row["device"] is None

    clock.now += timedelta(minutes=10)
    client.post("/watch", json=report(seconds=660, ended="tune_away"))
    [row] = rows(db_path)
    assert row["seconds"] == 660 and row["ended"] == "tune_away"
    assert row["started_at"] == (START - timedelta(seconds=60)).isoformat()
    # A late heartbeat never shrinks the count or reopens it.
    client.post("/watch", json=report(seconds=30))
    [row] = rows(db_path)
    assert row["seconds"] == 660 and row["ended"] == "tune_away"


def test_seconds_are_capped_by_runtime_and_real_time(
    client: TestClient, db_path: Path, clock: Clock
) -> None:
    client.post("/watch", json=report(seconds=5000, runtime=1320))
    assert rows(db_path)[0]["seconds"] == 1320
    client.post("/watch", json=report(session="second-session", seconds=60))
    clock.now += timedelta(minutes=2)
    # Only about two more minutes have passed: it can't have watched twenty.
    client.post("/watch", json=report(session="second-session", seconds=1200))
    assert rows(db_path)[1]["seconds"] <= 60 + 120 + 60


@pytest.mark.parametrize(
    "bad",
    [
        {"session": "short"},
        {"session": "has spaces in it"},
        {"channel": "KTVU!"},
        {"channel": "not-a-channel"},
        {"item": "../etc/passwd"},
        {"kind": "bumper"},
        {"year": 1989},
        {"year": 1},
        {"seconds": -1},
        {"seconds": 6 * 3600 + 1},
        {"runtime": 0},
        {"ended": "exploded"},
        {"extra": "field"},
    ],
)
def test_bad_reports_are_refused(client: TestClient, db_path: Path, bad: dict[str, Any]) -> None:
    assert client.post("/watch", json=report(**bad)).status_code == 422
    assert rows(db_path) == []


def test_an_all_years_report_dates_the_session_today(client: TestClient, db_path: Path) -> None:
    assert client.post("/watch", json=report(year=0)).status_code == 204
    assert rows(db_path)[0]["sim_date"] == "2026-10-31"


def test_a_session_id_cannot_move_to_another_program(client: TestClient, db_path: Path) -> None:
    client.post("/watch", json=report())
    assert client.post("/watch", json=report(item="x-men")).status_code == 409
    assert rows(db_path)[0]["item"] == "the-simpsons"


def test_watch_is_in_the_openapi_spec(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert spec["paths"]["/watch"]["post"]["operationId"] == "postWatch"
