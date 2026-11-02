"""The app's schedule cache: rebuilt only when its inputs change, once per change.

Admin reads must not rebuild the Scheduler (each rebuild stalls the next TV request), and
concurrent requests after a change must share one rebuild.
"""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import retro_cable.app as app_module
from retro_cable.app import Settings, create_app
from retro_cable.db import connect
from retro_cable.scheduler import Scheduler
from tests.conftest import MakeCatalog
from tests.test_api_admin import add_uncarried, toons
from tests.test_api_tv import seed_db

REAL_NOW = datetime(2026, 11, 1, 0, 14, tzinfo=UTC)


class Builds:
    """Counts Scheduler constructions made by the app's cache."""

    def __init__(self) -> None:
        self.count = 0
        self.delay = 0.0


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> Iterator[Builds]:
    counter = Builds()
    real = Scheduler

    def counting(*args: Any, **kwargs: Any) -> Any:
        counter.count += 1
        if counter.delay:
            time.sleep(counter.delay)
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, "Scheduler", counting)
    yield counter


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    media = tmp_path / "media"
    media.mkdir()
    db = tmp_path / "rc.db"
    seed_db(db, media)
    add_uncarried(db)
    return db


@pytest.fixture
def client(make_catalog: MakeCatalog, db_path: Path, tmp_path: Path) -> TestClient:
    settings = Settings(
        catalog_dir=make_catalog(),
        db_path=db_path,
        media_dir=tmp_path / "media",
        media_secret="s",
        seed=7,
    )
    return TestClient(create_app(settings, clock=lambda: REAL_NOW))


def test_admin_reads_do_not_rebuild_the_scheduler(client: TestClient, builds: Builds) -> None:
    assert client.get("/channels", params={"year": 1998}).status_code == 200
    warm = builds.count
    assert warm == 1
    draft = {"draft": toons(), "year": 2000}
    for _ in range(2):
        assert client.get("/admin/channels").status_code == 200
        assert client.get("/admin/channels/ktvu").status_code == 200
        assert client.get("/admin/state").status_code == 200
        assert client.get("/admin/library").status_code == 200
        assert client.post("/admin/check", json=draft).status_code == 200
        assert client.post("/admin/preview", json=draft).status_code == 200
        assert client.get("/channels", params={"year": 1998}).status_code == 200
        assert client.get("/guide", params={"year": 1998}).status_code == 200
    assert builds.count == warm


def test_an_admin_write_rebuilds_once_and_shows_up(client: TestClient, builds: Builds) -> None:
    client.get("/channels", params={"year": 2000})
    before = builds.count
    saved = client.post("/admin/channels", json={"draft": toons(), "year": 2000})
    assert saved.status_code == 200, saved.text
    ids = [c["id"] for c in client.get("/channels", params={"year": 2000}).json()["channels"]]
    assert saved.json()["channel_id"] in ids
    client.get("/admin/channels")
    client.get("/channels", params={"year": 2000})
    assert builds.count == before + 1


def test_a_write_from_another_process_is_picked_up(
    client: TestClient, db_path: Path, builds: Builds
) -> None:
    client.get("/admin/library")
    before = builds.count
    db = connect(db_path)
    db.execute("INSERT INTO shows (id, title) VALUES ('recess', 'Recess')")
    db.close()
    titles = [i["title"] for i in client.get("/admin/library").json()["items"]]
    assert "Recess" in titles
    assert builds.count == before + 1


def test_a_replaced_database_file_is_picked_up(
    client: TestClient, db_path: Path, tmp_path: Path
) -> None:
    client.get("/admin/library")
    fresh = tmp_path / "fresh.db"
    seed_db(fresh, tmp_path / "media")
    fresh.replace(db_path)
    for leftover in (db_path.with_name(db_path.name + s) for s in ("-wal", "-shm")):
        leftover.unlink(missing_ok=True)
    titles = [i["title"] for i in client.get("/admin/library").json()["items"]]
    assert "Courage the Cowardly Dog" not in titles
    assert "The Simpsons" in titles


def test_concurrent_requests_share_one_rebuild(
    client: TestClient, db_path: Path, builds: Builds
) -> None:
    client.get("/channels", params={"year": 1998})
    before = builds.count
    db = connect(db_path)
    db.execute("INSERT INTO shows (id, title) VALUES ('recess', 'Recess')")
    db.close()
    builds.delay = 0.3
    results: list[int] = []

    def tune() -> None:
        results.append(client.get("/channels", params={"year": 1998}).status_code)

    threads = [threading.Thread(target=tune) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 6
    assert builds.count == before + 1


def test_a_commit_still_in_the_wal_is_picked_up(client: TestClient, db_path: Path) -> None:
    """A long ingest keeps its connection open and its commits sit in the -wal file; no
    file mtime needs to move for the change to show."""
    client.get("/admin/library")
    writer = connect(db_path)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("INSERT INTO shows (id, title) VALUES ('recess', 'Recess')")
    titles = [i["title"] for i in client.get("/admin/library").json()["items"]]
    writer.close()
    assert "Recess" in titles
