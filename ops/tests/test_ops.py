"""Static and behavioral checks for ops/: compose files, the Caddy snippet and scripts."""

import os
import re
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


def test_compose_runs_api_and_web_and_a_worker_on_demand() -> None:
    services = load("compose.yml")["services"]
    assert set(services) == {"api", "web", "worker"}
    assert services["api"]["image"].startswith("ghcr.io/btabaska/retro-cable-api:")
    assert services["web"]["image"].startswith("ghcr.io/btabaska/retro-cable-web:")
    # The worker shares the api image (it has ffmpeg) and only runs when asked:
    #   docker compose --profile jobs run --rm worker python -m retro_cable.ingest normalize
    worker = services["worker"]
    assert worker["image"] == services["api"]["image"]
    assert worker["profiles"] == ["jobs"]
    assert "ports" not in worker


def volumes(service: dict[str, Any]) -> dict[str, str]:
    """Container path -> mount mode ('ro' or 'rw')."""
    out = {}
    for v in service.get("volumes", []):
        parts = re.sub(r"\$\{[^}]*\}", "SRC", v).split(":")
        mode = parts[-1] if parts[-1] in ("ro", "rw") else "rw"
        target = parts[-2] if mode == parts[-1] and len(parts) > 2 else parts[1]
        out[target] = mode
    return out


def test_compose_mounts_catalog_and_media_and_only_the_worker_writes_media() -> None:
    services = load("compose.yml")["services"]
    api, web, worker = (volumes(services[s]) for s in ("api", "web", "worker"))
    assert api["/catalog"] == "ro" and worker["/catalog"] == "ro"
    assert api["/media"] == "ro"
    assert web["/srv/media"] == "ro"
    assert worker["/media"] == "rw"
    assert worker["/sources"] == "ro"
    for name in ("api", "worker"):
        env = services[name]["environment"]
        assert env["RC_CATALOG_DIR"] == "/catalog"
        assert env["RC_MEDIA_DIR"] == "/media"
    text = (OPS / "compose.yml").read_text()
    assert "${RC_MEDIA_DIR:-" in text


def test_env_example_documents_media_settings() -> None:
    text = (OPS / ".env.example").read_text()
    for key in ("RC_MEDIA_DIR", "RC_MEDIA_SECRET", "RC_SOURCE_DIR", "RC_SOURCE_MAP", "RC_ENCODER"):
        assert key in text


def test_server_image_has_ffmpeg_for_the_worker() -> None:
    text = (OPS.parent / "server" / "Dockerfile").read_text()
    final_stage = text.rsplit("FROM ", 1)[1]
    assert "ffmpeg" in final_stage


def test_compose_publishes_only_web_and_only_on_loopback_by_default() -> None:
    services = load("compose.yml")["services"]
    assert "ports" not in services["api"]
    (port,) = services["web"]["ports"]
    assert port.startswith("${RC_WEB_BIND:-127.0.0.1}:")


def test_test_compose_builds_from_source_and_mounts_fixtures_read_only() -> None:
    services = load("compose.test.yml")["services"]
    assert "build" in services["api"]
    assert "build" in services["web"]
    assert "../.fixtures:/srv/media:ro" in services["web"]["volumes"]


def test_test_compose_api_runs_on_the_demo_catalog() -> None:
    api = load("compose.test.yml")["services"]["api"]
    assert "../catalog:/catalog:ro" in api["volumes"]
    assert "../.fixtures:/media:ro" in api["volumes"]
    assert api["environment"]["RC_DB"] == "/data/demo.db"
    assert "retro_cable.demo" in api["command"][-1]


def test_api_image_owns_data_dir_so_named_volumes_are_writable() -> None:
    # A fresh named volume copies the image's /data ownership. Without this the
    # non-root api user cannot create /data/demo.db and the container exits at once.
    text = (OPS.parent / "server" / "Dockerfile").read_text()
    final_stage = text.rsplit("FROM ", 1)[1]
    user_at = final_stage.index("USER app")
    chown = [line for line in final_stage.splitlines() if "/data" in line and "chown" in line]
    assert chown, "final stage must create /data owned by the app user"
    assert "app" in chown[0]
    assert final_stage.index(chown[0]) < user_at


def test_local_test_workflow_collects_logs_even_when_stack_fails() -> None:
    wf = yaml.safe_load((OPS.parent / ".github" / "workflows" / "local-test.yml").read_text())
    steps = wf["jobs"]["local-test"]["steps"]
    (step,) = [s for s in steps if s.get("name") == "Collect stack logs"]
    assert "mkdir -p web/test-results" in step["run"]


# --- caddy -------------------------------------------------------------------


def test_caddy_snippet_rejects_non_private_clients() -> None:
    text = (OPS / "caddy" / "retro-cable.caddy").read_text()
    assert "tv.home.tabaska.us" in text
    assert "@not_lan not remote_ip private_ranges" in text
    assert "abort @not_lan" in text


def _block(text: str, opener: str) -> str:
    """The body of the first `opener {` block, braces balanced."""
    start = text.index(opener)
    # The block's brace ends the line; `{$VAR}` placeholders don't.
    match = re.compile(r"\{[ \t]*\n").search(text, start)
    assert match, f"no block after {opener}"
    depth, at = 0, match.start()
    for i in range(at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[at + 1 : i]
    raise AssertionError(f"unbalanced block {opener}")


def test_web_caddy_checks_media_signatures_before_serving_files() -> None:
    # The web container's Caddy serves /media/*; the host snippet only proxies to it.
    text = (OPS.parent / "web" / "Caddyfile").read_text()
    assert "handle_path /media/*" not in text, "stripping first would hide the signed path"
    media = _block(text, "handle /media/*")
    route = _block(media, "route")
    auth = _block(route, "forward_auth")
    assert "uri /media-auth" in auth
    lines = [line.strip() for line in route.splitlines() if line.strip()]
    order = [next(i for i, line in enumerate(lines) if line.startswith(d))
             for d in ("forward_auth", "uri strip_prefix /media", "file_server")]  # fmt: skip
    assert order == sorted(order), "auth, then strip /media, then serve"
    assert "browse" not in media
    assert "log" not in text.split(), "access logs would record signed URLs"


@pytest.mark.skipif(shutil.which("caddy") is None, reason="caddy not installed")
def test_web_caddyfile_is_valid() -> None:
    config = str(OPS.parent / "web" / "Caddyfile")
    subprocess.run(
        ["caddy", "validate", "--adapter", "caddyfile", "--config", config],
        check=True,
        env={**os.environ, "RC_API_UPSTREAM": "127.0.0.1:8000"},
    )


def test_local_test_fails_if_logs_carry_signed_media_urls() -> None:
    wf = yaml.safe_load((OPS.parent / ".github" / "workflows" / "local-test.yml").read_text())
    steps = wf["jobs"]["local-test"]["steps"]
    names = [s.get("name") for s in steps]
    (check,) = [s for s in steps if s.get("name") == "No signed media URLs in the logs"]
    assert names.index("Collect stack logs") < names.index(check["name"])
    assert "sig=" in check["run"] and "stack.log" in check["run"]


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
