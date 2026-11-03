"""The api is ready before it answers anything.

Startup migrates the database and builds the schedule, so the first request after a deploy
(the info banner's /channels and /tune) is never the one that migrates or pays for the
cold build, and `/health` answering (which the compose healthcheck, `up --wait` and
media-index's depends_on wait for) means the TV endpoints are ready.
"""

import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from retro_cable.app import Settings, create_app
from retro_cable.db import MIGRATIONS, connect, migrate
from tests.conftest import MakeCatalog
from tests.test_api_tv import seed_db
from tests.test_schedule_cache import REAL_NOW, Builds
from tests.test_schedule_cache import builds as builds  # the fixture, re-exported


def old_schema_db(path: Path, media: Path) -> None:
    """A database as the previous release left it: seeded, without the last migration."""
    seed_db(path, media)
    db = connect(path)
    db.execute("ALTER TABLE episodes DROP COLUMN source_seconds")
    db.execute("ALTER TABLE movies DROP COLUMN source_seconds")
    db.execute("DELETE FROM schema_migrations WHERE version = ?", (MIGRATIONS[-1].version,))
    db.close()


def versions(path: Path) -> list[int]:
    db = connect(path)
    try:
        return [r[0] for r in db.execute("SELECT version FROM schema_migrations")]
    finally:
        db.close()


def settings(catalog: Path, tmp_path: Path) -> Settings:
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    return Settings(
        catalog_dir=catalog,
        db_path=tmp_path / "rc.db",
        media_dir=media,
        media_secret="s",
        seed=7,
    )


def test_startup_migrates_and_builds_the_schedule_before_serving(
    make_catalog: MakeCatalog, tmp_path: Path, builds: Builds
) -> None:
    config = settings(make_catalog(), tmp_path)
    old_schema_db(config.db_path, config.media_dir)
    assert MIGRATIONS[-1].version not in versions(config.db_path)

    with TestClient(create_app(config, clock=lambda: REAL_NOW)) as client:
        # Nothing has been requested yet.
        assert versions(config.db_path) == [m.version for m in MIGRATIONS]
        assert builds.count == 1
        assert client.get("/channels", params={"year": 1998}).status_code == 200
        assert client.get("/tune/ktvu", params={"year": 1998}).status_code == 200
        assert builds.count == 1


def test_first_banner_request_survives_media_index_migrating_at_the_same_time(
    make_catalog: MakeCatalog, tmp_path: Path
) -> None:
    # deploy.sh: `up --wait` returns once the api is healthy, media-index (which waits for
    # a healthy api) starts and migrates, and the browser's first /channels lands now.
    config = settings(make_catalog(), tmp_path)
    old_schema_db(config.db_path, config.media_dir)

    with TestClient(create_app(config, clock=lambda: REAL_NOW)) as client:
        start = threading.Barrier(2)
        errors: list[sqlite3.Error] = []

        def media_index() -> None:
            db = connect(config.db_path)
            start.wait()
            try:
                migrate(db)
            except sqlite3.Error as e:
                errors.append(e)
            finally:
                db.close()

        other = threading.Thread(target=media_index)
        other.start()
        start.wait()
        lineup = client.get("/channels", params={"year": 1998})
        other.join()

    assert errors == []
    assert lineup.status_code == 200, lineup.text
    assert lineup.json()["channels"]


def test_startup_fails_on_a_broken_catalog(make_catalog: MakeCatalog, tmp_path: Path) -> None:
    # Fail the container (so `up --wait` fails and deploy.sh rolls back) rather than come
    # up healthy and fail every TV request.
    config = settings(make_catalog(channels="channels: [nope"), tmp_path)
    app = create_app(config, clock=lambda: REAL_NOW)
    with pytest.raises(Exception), TestClient(app):  # noqa: B017 - any load error
        pass


def test_startup_without_a_database_still_serves(make_catalog: MakeCatalog, tmp_path: Path) -> None:
    # A fresh host: no database until the first ingest or admin write. Startup doesn't
    # create one, and the TV answers from the catalog alone.
    config = settings(make_catalog(), tmp_path)
    with TestClient(create_app(config, clock=lambda: REAL_NOW)) as client:
        assert not config.db_path.exists()
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/channels", params={"year": 1998}).status_code == 200
