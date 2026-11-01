"""Static and behavioral checks for ops/: compose files, the Caddy snippet and scripts."""

import os
import shutil
import socket
import subprocess
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

OPS = Path(__file__).resolve().parents[1]
SCRIPTS = ["deploy.sh", "rollback.sh", "smoke.sh"]


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((OPS / name).read_text())
    return data


# --- scripts -----------------------------------------------------------------


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_is_strict_and_executable(name: str) -> None:
    path = OPS / name
    lines = path.read_text().splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert "set -euo pipefail" in lines[:5]
    assert os.access(path, os.X_OK)
    subprocess.run(["bash", "-n", str(path)], check=True)


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_scripts_pass_shellcheck() -> None:
    subprocess.run(["shellcheck", *(str(OPS / s) for s in SCRIPTS)], check=True)


# --- compose -----------------------------------------------------------------


def test_compose_runs_api_and_web() -> None:
    services = load("compose.yml")["services"]
    assert set(services) == {"api", "web"}
    assert services["api"]["image"].startswith("ghcr.io/btabaska/retro-cable-api:")
    assert services["web"]["image"].startswith("ghcr.io/btabaska/retro-cable-web:")


def test_compose_publishes_only_web_and_only_on_loopback_by_default() -> None:
    services = load("compose.yml")["services"]
    assert "ports" not in services["api"]
    (port,) = services["web"]["ports"]
    assert port.startswith("${RC_WEB_BIND:-127.0.0.1}:")


def test_web_joins_the_edge_network_under_a_stable_name() -> None:
    compose = load("compose.yml")
    web = compose["services"]["web"]
    assert web["networks"]["edge"]["aliases"] == ["retro-cable-web"]
    assert compose["networks"]["edge"] == {"external": True, "name": "${RC_EDGE_NETWORK:-edge}"}


def test_web_serves_media_read_only() -> None:
    web = load("compose.yml")["services"]["web"]
    assert "${RC_MEDIA_DIR:-./media}:/srv/media:ro" in web["volumes"]


def test_test_compose_creates_its_own_edge_network() -> None:
    assert load("compose.test.yml")["networks"]["edge"]["external"] is False


def test_build_compose_builds_both_images_from_source() -> None:
    services = load("compose.build.yml")["services"]
    assert services["api"]["build"]["context"] == "../server"
    assert services["web"]["build"] == {"context": "..", "dockerfile": "web/Dockerfile"}


@pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0
    if shutil.which("docker")
    else True,
    reason="docker not available",
)
@pytest.mark.parametrize("overlay", ["compose.test.yml", "compose.build.yml"])
def test_compose_files_are_valid(overlay: str) -> None:
    subprocess.run(
        ["docker", "compose", "-f", str(OPS / "compose.yml"), "-f", str(OPS / overlay), "config"],
        check=True,
        capture_output=True,
    )


def test_test_compose_builds_from_source_and_mounts_fixtures_read_only() -> None:
    services = load("compose.test.yml")["services"]
    assert "build" in services["api"]
    assert "build" in services["web"]
    assert "../.fixtures:/srv/media:ro" in services["web"]["volumes"]


# --- caddy -------------------------------------------------------------------


def test_caddy_snippet_rejects_non_private_clients() -> None:
    text = (OPS / "caddy" / "retro-cable.caddy").read_text()
    assert "tv.home.tabaska.us" in text
    assert "@not_lan not remote_ip private_ranges" in text
    assert "abort @not_lan" in text


def test_caddy_snippet_proxies_to_web_by_container_name_with_homelab_tls() -> None:
    text = (OPS / "caddy" / "retro-cable.caddy").read_text()
    assert "import local_tls" in text
    assert "reverse_proxy retro-cable-web:80" in text


# --- smoke / deploy / rollback ------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A static stand-in for the running stack: index.html plus /api/health and /api/version."""
    root = tmp_path / "www"
    (root / "api").mkdir(parents=True)
    (root / "index.html").write_text('<div id="root"></div>')
    (root / "api" / "health").write_text('{"status":"ok"}')
    (root / "api" / "version").write_text('{"version":"1.2.3","commit":"abc"}')
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", root
    server.shutdown()


def run(script: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(OPS / script), *args],
        env={**os.environ, "RC_SMOKE_RETRIES": "1", "RC_SMOKE_DELAY": "0", **env},
        capture_output=True,
        text=True,
    )


def test_smoke_passes_against_healthy_stack(stack: tuple[str, Path]) -> None:
    url, _ = stack
    result = run("smoke.sh", env={"RC_SMOKE_URL": url, "RC_EXPECT_VERSION": "1.2.3"})
    assert result.returncode == 0, result.stderr


def test_smoke_fails_on_wrong_version(stack: tuple[str, Path]) -> None:
    url, _ = stack
    result = run("smoke.sh", env={"RC_SMOKE_URL": url, "RC_EXPECT_VERSION": "9.9.9"})
    assert result.returncode != 0


def test_smoke_fails_when_health_is_down(stack: tuple[str, Path]) -> None:
    url, root = stack
    (root / "api" / "health").unlink()
    assert run("smoke.sh", env={"RC_SMOKE_URL": url}).returncode != 0


@pytest.fixture
def fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    """A `docker` on PATH that records each call with the RC_VERSION it saw."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(f'#!/usr/bin/env bash\necho "${{RC_VERSION:-}} $*" >> {log}\n')
    docker.chmod(0o755)
    return bin_dir, log


def deploy_env(tmp_path: Path, bin_dir: Path, smoke_url: str) -> dict[str, str]:
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RC_STATE_DIR": str(tmp_path / "state"),
        "RC_DATA_DIR": str(tmp_path / "data"),
        "RC_SMOKE_URL": smoke_url,
    }


def test_deploy_rejects_bad_version(tmp_path: Path, fake_docker: tuple[Path, Path]) -> None:
    bin_dir, log = fake_docker
    result = run("deploy.sh", "latest; rm -rf /", env=deploy_env(tmp_path, bin_dir, "x"))
    assert result.returncode != 0
    assert not log.exists()


def test_deploy_pulls_starts_and_records_version(
    tmp_path: Path, fake_docker: tuple[Path, Path], stack: tuple[str, Path]
) -> None:
    bin_dir, log = fake_docker
    url, _ = stack
    env = deploy_env(tmp_path, bin_dir, url)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "current").write_text("1.2.2\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "retro-cable.db").write_text("db")

    result = run("deploy.sh", "1.2.3", env=env)

    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert any(c.startswith("1.2.3 compose") and " pull" in c for c in calls)
    assert any(c.startswith("1.2.3 compose") and " up -d --wait" in c for c in calls)
    assert (tmp_path / "state" / "current").read_text().strip() == "1.2.3"
    assert (tmp_path / "state" / "previous").read_text().strip() == "1.2.2"
    assert (tmp_path / "state" / "db-1.2.2.sqlite").read_text() == "db"


def test_deploy_rolls_back_when_smoke_fails(tmp_path: Path, fake_docker: tuple[Path, Path]) -> None:
    bin_dir, log = fake_docker
    env = deploy_env(tmp_path, bin_dir, f"http://127.0.0.1:{free_port()}")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "current").write_text("1.2.2\n")

    result = run("deploy.sh", "1.2.3", env=env)

    assert result.returncode != 0
    calls = log.read_text().splitlines()
    assert any(c.startswith("1.2.2 compose") and " up -d --wait" in c for c in calls)
    assert (tmp_path / "state" / "current").read_text().strip() == "1.2.2"


def test_rollback_restores_previous_version_and_db(
    tmp_path: Path, fake_docker: tuple[Path, Path], stack: tuple[str, Path]
) -> None:
    bin_dir, log = fake_docker
    url, root = stack
    (root / "api" / "version").write_text('{"version":"1.2.2","commit":"old"}')
    env = deploy_env(tmp_path, bin_dir, url)
    state, data = tmp_path / "state", tmp_path / "data"
    state.mkdir()
    data.mkdir()
    (state / "current").write_text("1.2.3\n")
    (state / "previous").write_text("1.2.2\n")
    (state / "db-1.2.2.sqlite").write_text("old db")
    (data / "retro-cable.db").write_text("new db")

    result = run("rollback.sh", env=env)

    assert result.returncode == 0, result.stderr
    assert any(c.startswith("1.2.2 compose") for c in log.read_text().splitlines())
    assert (data / "retro-cable.db").read_text() == "old db"
    assert (state / "current").read_text().strip() == "1.2.2"


def test_rollback_without_previous_fails(tmp_path: Path, fake_docker: tuple[Path, Path]) -> None:
    bin_dir, _ = fake_docker
    assert run("rollback.sh", env=deploy_env(tmp_path, bin_dir, "x")).returncode != 0


def head_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(OPS), "rev-parse", "--short=7", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_deploy_build_mode_builds_instead_of_pulling(
    tmp_path: Path, fake_docker: tuple[Path, Path], stack: tuple[str, Path]
) -> None:
    bin_dir, log = fake_docker
    url, root = stack
    version = f"sha-{head_sha()}"
    (root / "api" / "version").write_text(f'{{"version":"0.0.0","commit":"{head_sha()}"}}')
    env = {**deploy_env(tmp_path, bin_dir, url), "RC_IMAGE_SOURCE": "build"}

    result = run("deploy.sh", version, env=env)

    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert not any(" pull" in c for c in calls)
    assert any("compose.build.yml" in c and c.endswith(" build") for c in calls)


def test_deploy_build_mode_refuses_a_checkout_that_does_not_match(
    tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    bin_dir, log = fake_docker
    env = {**deploy_env(tmp_path, bin_dir, "x"), "RC_IMAGE_SOURCE": "build"}
    result = run("deploy.sh", "sha-0000000", env=env)
    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert not log.exists()
