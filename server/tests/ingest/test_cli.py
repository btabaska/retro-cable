import json
from pathlib import Path

import pytest

from retro_cable.ingest import cli
from tests.conftest import MakeCatalog
from tests.ingest.conftest import RecordedFetch


def test_plex_requires_url_and_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PLEX_URL", raising=False)
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    assert cli.main(["plex"]) == 2
    assert "PLEX_URL and PLEX_TOKEN" in capsys.readouterr().err


def test_plex_imports_into_a_migrated_db_and_prints_stats(
    tmp_path: Path,
    make_catalog: MakeCatalog,
    plex_fetch: RecordedFetch,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PLEX_URL", "http://plex.invalid:32400")
    monkeypatch.setenv("PLEX_TOKEN", "s3cret")
    monkeypatch.setattr(cli, "plex_http_fetch", lambda url, token: plex_fetch)
    db_path = tmp_path / "data" / "retro-cable.db"

    code = cli.main(["plex", "--db", str(db_path), "--catalog", str(make_catalog())])

    assert code == 0
    out = capsys.readouterr().out
    assert json.loads(out)["shows"] == 3
    assert "s3cret" not in out
    assert db_path.exists()


def test_db_defaults_to_the_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RC_DATA_DIR", str(tmp_path))
    assert cli.default_db() == tmp_path / "retro-cable.db"


def test_scan_needs_a_root(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["scan"]) == 2
    assert "--tv" in capsys.readouterr().err


def test_scan_then_normalize_dry_run(
    tmp_path: Path,
    make_catalog: MakeCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tv = tmp_path / "tv" / "The Simpsons (1989)" / "Season 01"
    tv.mkdir(parents=True)
    (tv / "The Simpsons (1989) - S01E01 - Simpsons Roasting.mkv").write_bytes(b"x")
    db = str(tmp_path / "rc.db")
    catalog = str(make_catalog())

    assert cli.main(["scan", "--db", db, "--catalog", catalog, "--tv", str(tmp_path / "tv")]) == 0
    assert json.loads(capsys.readouterr().out)["tv"]["episodes"] == 1

    # The worker sees the NAS share somewhere else: map the scanned prefix to it.
    monkeypatch.setenv("RC_SOURCE_MAP", f"{tmp_path / 'tv'}={tmp_path / 'mnt' / 'tv'}")
    monkeypatch.setenv("RC_ENCODER", "nvenc")
    monkeypatch.setattr(
        cli,
        "normalize",
        lambda db, media, **kw: _capture(kw),
    )
    media = ["--media-dir", str(tmp_path / "media")]
    assert cli.main(["normalize", "--db", db, "--catalog", catalog, *media]) == 0
    assert CAPTURED["encoder"] == "nvenc"
    assert CAPTURED["source_map"] == [(str(tmp_path / "tv"), str(tmp_path / "mnt" / "tv"))]
    # By default only shows a channel carries are encoded; --all takes the whole library.
    shows = CAPTURED["shows"]
    assert isinstance(shows, frozenset) and "the-simpsons" in shows
    assert cli.main(["normalize", "--db", db, "--catalog", catalog, "--all", *media]) == 0
    assert CAPTURED["shows"] is None


def test_normalize_includes_shows_on_custom_channels(
    tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from retro_cable.admin import store
    from retro_cable.catalog.models import Carriage, Channel
    from retro_cable.catalog.overlay import Overlay
    from retro_cable.db import connect, migrate

    db_path = tmp_path / "rc.db"
    db = connect(db_path)
    migrate(db)
    custom = Channel.model_validate(
        {"id": "custom-toons", "name": "Toons", "type": "network", "library": True,
         "identity": [{"from": "1990-01-01", "number": 50, "call_sign": "TOONS"}]}
    )  # fmt: skip
    bravo = Carriage.model_validate(
        {"show": "johnny-bravo", "title": "Johnny Bravo", "channel": "custom-toons",
         "from": "1990-01-01"}
    )  # fmt: skip
    store.save(db, Overlay(channels=[custom], carriage=[bravo]), "add", datetime.now(UTC))
    db.close()
    monkeypatch.setattr(cli, "normalize", lambda db, media, **kw: _capture(kw))
    args = ["normalize", "--db", str(db_path), "--catalog", str(make_catalog())]
    assert cli.main([*args, "--media-dir", str(tmp_path / "media")]) == 0
    shows = CAPTURED["shows"]
    assert isinstance(shows, frozenset) and {"johnny-bravo", "the-simpsons"} <= shows


def test_normalize_needs_a_catalog_unless_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["normalize", "--db", str(tmp_path / "rc.db"), "--catalog", str(tmp_path / "nope")]
    assert cli.main([*args, "--media-dir", str(tmp_path / "m"), "--dry-run"]) == 1
    assert cli.main([*args, "--media-dir", str(tmp_path / "m"), "--dry-run", "--all"]) == 0


CAPTURED: dict[str, object] = {}


def _capture(kw: dict[str, object]) -> object:
    from retro_cable.ingest.normalize import NormalizeStats

    CAPTURED.clear()
    CAPTURED.update(kw)
    return NormalizeStats()


def test_source_maps_parse_flags_or_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cli.source_maps(["/data/tv=/volume3/tv"]) == [("/data/tv", "/volume3/tv")]
    monkeypatch.setenv("RC_SOURCE_MAP", "/a=/b, /c=/d")
    assert cli.source_maps([]) == [("/a", "/b"), ("/c", "/d")]
    with pytest.raises(SystemExit):
        cli.source_maps(["nope"])
