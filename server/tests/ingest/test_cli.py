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
