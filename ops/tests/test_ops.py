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
SCRIPTS = [
    "deploy.sh",
    "rollback.sh",
    "smoke.sh",
    "station-manager.sh",
    "systemd/install.sh",
    "rig/worker.sh",
    "rig/up.sh",
]


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
    assert set(services) == {"api", "web", "worker", "media-index"}
    assert services["api"]["image"].startswith("ghcr.io/btabaska/retro-cable-api:")
    assert services["web"]["image"].startswith("ghcr.io/btabaska/retro-cable-web:")
    # The worker shares the api image (it has ffmpeg) and only runs when asked:
    #   docker compose --profile jobs run --rm worker python -m retro_cable.ingest normalize
    worker = services["worker"]
    assert worker["image"] == services["api"]["image"]
    assert worker["profiles"] == ["jobs"]
    assert "ports" not in worker


def volumes(service: dict[str, Any]) -> dict[str, str]:
    """Container path -> mount mode ('ro' or 'rw'), ignoring propagation flags (rslave)."""
    out = {}
    for v in service.get("volumes", []):
        parts = re.sub(r"\$\{[^}]*\}", "SRC", v).split(":")
        flags = parts[-1].split(",") if len(parts) > 2 else []
        out[parts[1]] = "ro" if "ro" in flags else "rw"
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


def test_media_index_records_the_rigs_encodes_without_writing_media() -> None:
    services = load("compose.yml")["services"]
    index = services["media-index"]
    # Off unless ops/.env turns it on (COMPOSE_PROFILES=media-index on the mini).
    assert index["profiles"] == ["media-index"]
    assert index["image"] == services["api"]["image"]
    assert index["restart"] == "unless-stopped"
    assert "ports" not in index
    assert index["command"][:5] == ["python", "-m", "retro_cable.ingest", "index-media", "--watch"]
    assert volumes(index) == {"/data": "rw", "/media": "ro"}
    assert index["environment"]["RC_MEDIA_DIR"] == "/media"
    assert index["depends_on"]["api"]["condition"] == "service_healthy"


def test_media_index_is_unhealthy_until_it_can_write_the_database() -> None:
    """A data dir the api user can't write (it was uid 1000 on the mini) made media-index
    crash-loop while `up --wait` and the smoke test passed. With a healthcheck, `up --wait`
    fails and deploy.sh rolls back."""
    index = load("compose.yml")["services"]["media-index"]
    check = " ".join(index["healthcheck"]["test"])
    assert "/data/retro-cable.db" in check and "W_OK" in check
    assert index["healthcheck"]["start_period"]


# --- the rig worker ----------------------------------------------------------


def test_rig_worker_reads_sources_read_only_and_writes_only_the_media_share() -> None:
    worker = load("rig/compose.yml")["services"]["worker"]
    mounts = volumes(worker)
    assert mounts["/sources/tv"] == "ro"
    assert mounts["/sources/movies"] == "ro"
    assert mounts["/catalog"] == "ro"
    assert mounts["/rig/worker.sh"] == "ro"
    assert {t for t, m in mounts.items() if m == "rw"} == {"/media", "/data"}
    text = (OPS / "rig/compose.yml").read_text()
    assert "${RC_RIG_MEDIA:-/mnt/nas-livetv}:/media:rw" in text
    assert worker["environment"]["RC_CODEC"] == "${RC_CODEC:-hevc}", "the rig saves space with HEVC"
    assert "${RC_RIG_TV:-/mnt/nas-tv-ro}:/sources/tv:ro" in text
    assert "${RC_RIG_MOVIES:-/mnt/nas-movies-ro}:/sources/movies:ro" in text
    assert "ports" not in worker
    assert worker["restart"] == "unless-stopped"
    assert worker["user"].startswith("${RC_RIG_UID:-1000}")


def test_rig_worker_is_low_priority_and_can_use_the_gpu() -> None:
    worker = load("rig/compose.yml")["services"]["worker"]
    assert "nvidia.com/gpu=all" in worker["devices"]
    assert worker["cpu_shares"] <= 256
    assert worker["environment"]["RC_ENCODER"] == "${RC_ENCODER:-auto}"
    script = (OPS / "rig/worker.sh").read_text()
    assert "nice -n 19 ionice -c 3 python -m retro_cable.ingest" in script


def _fake_bin(tmp_path: Path) -> tuple[Path, Path]:
    """nice, ionice and python stand-ins that log their arguments."""
    bin_dir, calls = tmp_path / "bin", tmp_path / "calls.log"
    bin_dir.mkdir()
    for name, body in (
        ("nice", 'echo "nice $*" >>"$CALLS"; shift 2; exec "$@"'),
        ("ionice", 'echo "ionice $*" >>"$CALLS"; shift 2; exec "$@"'),
        ("python", 'echo "python $*" >>"$CALLS"; echo "{}"'),
    ):
        path = bin_dir / name
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)
    return bin_dir, calls


def _run_worker(tmp_path: Path, media: Path, tv: Path, **extra: str) -> tuple[int, str, str]:
    bin_dir, calls = _fake_bin(tmp_path)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    env = {
        **{k: v for k, v in os.environ.items() if k != "RC_ACQUIRE"},
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls),
        "RC_WORKER_ONCE": "1", "RC_TV_ROOT": str(tv), "RC_MOVIES_ROOT": str(tmp_path / "none"),
        "RC_MEDIA_DIR": str(media), "RC_DATA_DIR": str(data), **extra,
    }  # fmt: skip
    result = subprocess.run(
        ["bash", str(OPS / "rig/worker.sh")], env=env, capture_output=True, text=True
    )
    log = calls.read_text() if calls.exists() else ""
    return result.returncode, log, result.stderr


def test_rig_worker_scans_then_normalizes_in_priority_order(tmp_path: Path) -> None:
    media, tv = tmp_path / "media", tmp_path / "tv"
    media.mkdir()
    (media / ".plexignore").write_text("*\n")
    (tv / "Show (1990)").mkdir(parents=True)
    code, log, _ = _run_worker(tmp_path, media, tv)
    assert code == 0
    pythons = [line for line in log.splitlines() if line.startswith("python")]
    assert pythons[0] == f"python -m retro_cable.ingest scan --tv {tv} --no-probe"
    assert pythons[1] == (
        "python -m retro_cable.ingest normalize --order priority --encoder auto --manifest"
    ), "only what a channel carries is converted, never the whole library (--all)"
    lines = log.splitlines()
    assert sum(line.startswith("nice -n 19 ionice -c 3 python") for line in lines) == 2
    assert sum(line.startswith("ionice -c 3 python") for line in lines) == 2


def test_rig_worker_writes_nothing_without_the_nas_share(tmp_path: Path) -> None:
    media, tv = tmp_path / "media", tmp_path / "tv"
    media.mkdir()
    (tv / "Show (1990)").mkdir(parents=True)
    code, log, err = _run_worker(tmp_path, media, tv)
    assert code != 0
    assert "python" not in log
    assert ".plexignore" in err


def test_rig_worker_skips_an_unmounted_source_tree(tmp_path: Path) -> None:
    media, tv = tmp_path / "media", tmp_path / "tv"
    media.mkdir()
    (media / ".plexignore").write_text("*\n")
    tv.mkdir()
    code, log, err = _run_worker(tmp_path, media, tv)
    assert code != 0
    assert "python" not in log, "an empty tree would detach every file"
    assert "not mounted" in err


def _pythons(log: str) -> list[str]:
    return [line for line in log.splitlines() if line.startswith("python")]


def test_rig_worker_also_scans_the_live_tv_downloads(tmp_path: Path) -> None:
    """sonarr-livetv and radarr-livetv download into the LiveTV share's sources/ folders; an
    empty one is normal (everything converted and its originals deleted)."""
    media, tv = tmp_path / "media", tmp_path / "tv"
    media.mkdir()
    (media / ".plexignore").write_text("*\n")
    (media / "sources/tv").mkdir(parents=True)  # empty: still scanned
    (media / "sources/movies/Movie (1998)").mkdir(parents=True)
    (tv / "Show (1990)").mkdir(parents=True)
    code, log, _ = _run_worker(tmp_path, media, tv)
    assert code == 0
    assert _pythons(log)[0] == (
        f"python -m retro_cable.ingest scan --tv {tv} --no-probe --tv {media}/sources/tv"
        f" --movies {media}/sources/movies"
    )


def test_rig_worker_still_skips_when_only_the_plex_tree_is_empty(tmp_path: Path) -> None:
    media, tv = tmp_path / "media", tmp_path / "tv"
    media.mkdir()
    (media / ".plexignore").write_text("*\n")
    (media / "sources/tv/X-Men (1992)").mkdir(parents=True)
    tv.mkdir()
    code, log, err = _run_worker(tmp_path, media, tv)
    assert code != 0
    assert "python" not in log and "not mounted" in err


def test_rig_worker_acquires_only_when_asked(tmp_path: Path) -> None:
    media, tv = tmp_path / "media", tmp_path / "tv"
    media.mkdir()
    (media / ".plexignore").write_text("*\n")
    (tv / "Show (1990)").mkdir(parents=True)
    _, log, _ = _run_worker(tmp_path, media, tv)
    assert not any(" acquire" in p for p in _pythons(log)), "off unless RC_ACQUIRE=1"

    (tmp_path / "calls.log").unlink()
    shutil.rmtree(tmp_path / "bin")
    code, log, _ = _run_worker(tmp_path, media, tv, RC_ACQUIRE="1")
    assert code == 0
    jobs = [p.split()[3] for p in _pythons(log)]
    assert jobs == ["scan", "acquire", "normalize"], "after the scan, before normalize"
    assert (tmp_path / "data/last-acquire.json").is_file()


def test_rig_worker_deletes_only_live_tv_originals_and_never_acquires_by_default() -> None:
    env = load("rig/compose.yml")["services"]["worker"]["environment"]
    # Unset in .env: /media/sources (the downloads). Set to empty in .env: off.
    assert env["RC_DELETE_SOURCES_UNDER"] == "${RC_DELETE_SOURCES_UNDER-/media/sources}"
    assert env["RC_ACQUIRE"] == "${RC_ACQUIRE:-0}"
    assert env["RC_SONARR_LIVETV_URL"] == "${RC_SONARR_LIVETV_URL:-http://192.168.10.4:8990}"
    assert env["RC_RADARR_LIVETV_URL"] == "${RC_RADARR_LIVETV_URL:-http://192.168.10.4:7879}"
    # The keys come only from ops/rig/.env (env_file), never from the compose file.
    assert not any("KEY" in k for k in env)
    example = (OPS / "rig/.env.example").read_text()
    for name in ("RC_SONARR_LIVETV_KEY", "RC_RADARR_LIVETV_KEY", "RC_ACQUIRE",
                 "RC_DELETE_SOURCES_UNDER"):  # fmt: skip
        assert f"# {name}=" in example


def test_rig_up_refuses_mounts_that_are_not_what_it_expects(tmp_path: Path) -> None:
    """up.sh checks findmnt before starting anything: read-only sources, and a read-write
    media root that is the NAS LiveTV share with its .plexignore."""
    tv, movies, media = (tmp_path / n for n in ("tv", "movies", "media"))
    for d in (tv, movies, media):
        d.mkdir()
    (media / ".plexignore").write_text("*\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    (bin_dir / "docker").write_text(f'#!/usr/bin/env bash\necho "$*" >>{docker_log}\n')
    (bin_dir / "docker").chmod(0o755)

    def run(table: dict[str, tuple[str, str]]) -> subprocess.CompletedProcess[str]:
        findmnt = tmp_path / "findmnt"
        cases = "\n".join(
            f'  {p}) [[ $col == SOURCE ]] && echo "{src}" || echo "{opts}" ;;'
            for p, (src, opts) in table.items()
        )
        findmnt.write_text(
            f"#!/usr/bin/env bash\ncol=$3; target=$5\ncase $target in\n{cases}\n"
            "  *) exit 1 ;;\nesac\n"
        )
        findmnt.chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FINDMNT": str(findmnt),
               "RC_RIG_TV": str(tv), "RC_RIG_MOVIES": str(movies), "RC_RIG_MEDIA": str(media),
               "RC_RIG_DATA": str(tmp_path / "data")}  # fmt: skip
        return subprocess.run(
            ["bash", str(OPS / "rig/up.sh")], env=env, capture_output=True, text=True
        )

    good = {
        str(tv): ("//nas/tv", "ro,relatime"),
        str(movies): ("//nas/movies", "ro,relatime"),
        str(media): ("//nas/LiveTV", "rw,relatime"),
    }
    writable_tv = {**good, str(tv): ("//nas/tv", "rw,relatime")}
    wrong_media = {**good, str(media): ("//nas/tv", "rw,relatime")}
    for bad in (writable_tv, wrong_media):
        result = run(bad)
        assert result.returncode != 0
        assert not docker_log.exists(), result.stderr
    result = run(good)
    assert result.returncode == 0, result.stderr
    assert "compose" in docker_log.read_text()


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


def test_web_caddy_also_rejects_non_private_clients() -> None:
    """The web port is loopback by default, but RC_WEB_BIND can put it on the LAN (mini:8095
    when the host Caddy can't be used). The container then needs its own LAN-only check,
    before any route."""
    text = (OPS.parent / "web" / "Caddyfile").read_text()
    site = _block(text, ":80")
    lines = [
        ln.strip() for ln in site.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]
    assert lines[0] == "@not_lan not remote_ip private_ranges"
    assert lines[1] == "abort @not_lan"


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


def test_deploy_reads_host_settings_from_env_file(
    tmp_path: Path, fake_docker: tuple[Path, Path], stack: tuple[str, Path]
) -> None:
    bin_dir, log = fake_docker
    url, root = stack
    port = url.rsplit(":", 1)[1]
    (root / "api" / "version").write_text(f'{{"version":"0.0.0","commit":"{head_sha()}"}}')
    env_file = tmp_path / "host.env"
    env_file.write_text(f"RC_IMAGE_SOURCE=build\nRC_WEB_PORT={port}\n")
    env = deploy_env(tmp_path, bin_dir, url)
    del env["RC_SMOKE_URL"]  # smoke must find the stack through RC_WEB_PORT
    env["RC_ENV_FILE"] = str(env_file)

    result = run("deploy.sh", f"sha-{head_sha()}", env=env)

    assert result.returncode == 0, result.stderr
    assert any(c.endswith(" build") for c in log.read_text().splitlines())


# --- the weekly station manager (M5) ------------------------------------------


def unit(name: str) -> dict[str, dict[str, str]]:
    """A systemd unit file as {section: {key: value}} (last value wins)."""
    out: dict[str, dict[str, str]] = {}
    section = ""
    for raw in (OPS / "systemd" / name).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            out[section] = {}
        else:
            key, value = line.split("=", 1)
            out[section][key] = value
    return out


def test_station_manager_timer_runs_sundays_at_2am_new_york_and_catches_up() -> None:
    timer = unit("retro-cable-station-manager.timer")
    assert timer["Timer"]["OnCalendar"] == "Sun *-*-* 02:00:00 America/New_York"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Timer"]["Unit"] == "retro-cable-station-manager.service"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_station_manager_service_runs_the_job_script_after_docker() -> None:
    service = unit("retro-cable-station-manager.service")
    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["ExecStart"] == "/opt/retro-cable/ops/station-manager.sh"
    assert "docker.service" in service["Unit"]["After"]
    assert service["Unit"]["Requires"] == "docker.service"
    assert service["Service"]["User"] == "CHECKOUT_OWNER"  # filled in by install.sh


def test_station_manager_and_deploy_share_one_lock() -> None:
    for script in ("deploy.sh", "station-manager.sh"):
        text = (OPS / script).read_text()
        assert 'exec 9>"$state/deploy.lock"' in text and "flock -w" in text
    assert '"$here/systemd/install.sh" --check || true' in (OPS / "deploy.sh").read_text()


def test_station_manager_runs_the_job_in_the_api_container(
    tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    bin_dir, log = fake_docker
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RC_STATE_DIR": str(tmp_path / "state"),
        "RC_API_WAIT_DELAY": "0",
    }
    result = run("station-manager.sh", "--dry-run", env=env)
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    compose = f"compose -f {OPS / 'compose.yml'}"
    assert calls[0].endswith(f"{compose} exec -T api true")
    assert calls[-1].endswith(
        f"{compose} exec -T api python -m retro_cable.station_manager run --dry-run"
    )


def test_station_manager_gives_up_when_the_api_is_not_running(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/usr/bin/env bash\nexit 1\n")
    (bin_dir / "docker").chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RC_STATE_DIR": str(tmp_path / "state"),
        "RC_API_WAIT": "2",
        "RC_API_WAIT_DELAY": "0",
    }
    result = run("station-manager.sh", env=env)
    assert result.returncode == 1
    assert "isn't running" in result.stderr


def test_install_renders_the_units_once_and_enables_the_timer(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "systemctl.log"
    (bin_dir / "systemctl").write_text(f'#!/usr/bin/env bash\necho "$*" >> {calls}\n')
    (bin_dir / "systemctl").chmod(0o755)
    dest = tmp_path / "systemd"
    dest.mkdir()
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RC_SYSTEMD_DIR": str(dest),
        "RC_SERVICE_USER": "deployer",
    }
    assert run("systemd/install.sh", "--check", env=env).returncode == 1  # not installed yet

    result = run("systemd/install.sh", env=env)
    assert result.returncode == 0, result.stderr
    service = (dest / "retro-cable-station-manager.service").read_text()
    repo = str(OPS.parent)
    assert "User=deployer" in service
    assert f"ExecStart={repo}/ops/station-manager.sh" in service
    timer = (OPS / "systemd" / "retro-cable-station-manager.timer").read_text()
    assert (dest / "retro-cable-station-manager.timer").read_text() == timer.replace(
        "/opt/retro-cable", repo
    )
    assert calls.read_text().splitlines() == [
        "daemon-reload",
        "enable --now retro-cable-station-manager.timer",
        "list-timers retro-cable-station-manager.timer --no-pager",
    ]

    # Again: nothing to rewrite, so no reload; the check passes.
    calls.write_text("")
    assert run("systemd/install.sh", env=env).returncode == 0
    assert "daemon-reload" not in calls.read_text()
    assert run("systemd/install.sh", "--check", env=env).returncode == 0


def test_install_refuses_without_write_access(tmp_path: Path) -> None:
    dest = tmp_path / "ro"
    dest.mkdir(mode=0o555)
    result = run("systemd/install.sh", env={"RC_SYSTEMD_DIR": str(dest)})
    assert result.returncode == 1 and "sudo" in result.stderr


def test_api_gets_the_local_model_settings_and_the_example_names_them() -> None:
    env = load("compose.yml")["services"]["api"]["environment"]
    assert env["RC_LLM_URL"] == "${RC_LLM_URL:-http://rig:4000}"
    assert env["RC_LLM_KEY"] == "${RC_LLM_KEY:-}"
    assert env["RC_LLM_MODEL"] == "${RC_LLM_MODEL:-chat}"
    assert env["RC_STATION_AUTO_APPLY"] == "${RC_STATION_AUTO_APPLY:-1}"
    text = (OPS / ".env.example").read_text()
    for key in ("RC_LLM_URL", "RC_LLM_KEY", "RC_LLM_MODEL", "RC_STATION_AUTO_APPLY"):
        assert f"# {key}=" in text
    assert re.search(r"^# RC_LLM_KEY=\s", text, flags=re.M), "names only, never a key"
