import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from retro_cable import __version__
from retro_cable.app import Settings, create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_reports_package_version_and_commit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RC_GIT_SHA", "abc1234")
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": __version__, "commit": "abc1234"}


def test_version_commit_defaults_to_dev(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RC_GIT_SHA", raising=False)
    assert client.get("/version").json()["commit"] == "dev"


def _settings(data: Path) -> Settings:
    return Settings(catalog_dir=data, db_path=data / "retro-cable.db", media_dir=data / "media",
                    media_secret="s")  # fmt: skip


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_health_fails_when_the_data_dir_is_not_writable(tmp_path: Path) -> None:
    """The mini's data dir once belonged to uid 1000 while the api runs as 10001: no database
    was ever written, media-index crash-looped and the smoke test still passed. Now the api
    is unhealthy, so `up --wait` and the smoke test fail and deploy.sh rolls back."""
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o555)
    try:
        response = TestClient(create_app(_settings(data))).get("/health")
    finally:
        data.chmod(0o755)
    assert response.status_code == 503
    assert response.json() == {"status": "data dir not writable"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_health_fails_when_the_database_is_read_only(tmp_path: Path) -> None:
    (tmp_path / "retro-cable.db").write_bytes(b"")
    (tmp_path / "retro-cable.db").chmod(0o444)
    response = TestClient(create_app(_settings(tmp_path))).get("/health")
    assert response.status_code == 503


def test_health_is_ok_with_a_writable_or_not_yet_made_data_dir(tmp_path: Path) -> None:
    assert TestClient(create_app(_settings(tmp_path))).get("/health").status_code == 200
    missing = tmp_path / "not-yet"
    assert TestClient(create_app(_settings(missing))).get("/health").status_code == 200
