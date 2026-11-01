import pytest
from fastapi.testclient import TestClient

from retro_cable import __version__
from retro_cable.app import create_app


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
