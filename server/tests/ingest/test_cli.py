import json
from pathlib import Path

import pytest

from retro_cable.ingest import cli
from retro_cable.ingest.assemble import AssembleStats
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


def test_normalize_includes_shows_on_the_minis_admin_channels(
    tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The rig converts only what some channel carries, including channels made on the mini.
    library = {
        "items": [
            {
                "id": "over-the-garden-wall",
                "first_year": 2004,
                "channels": [{"id": "custom-toons"}],
            },
            {"id": "nobody-airs-me", "first_year": 1995, "channels": []},
        ]
    }
    monkeypatch.setattr(cli, "_get_json", lambda url: library)
    monkeypatch.setattr(cli, "normalize", lambda db, media, **kw: _capture(kw))
    args = ["normalize", "--db", str(tmp_path / "rig.db"), "--catalog", str(make_catalog()),
            "--media-dir", str(tmp_path / "media"), "--order", "priority",
            "--priority-url", "http://mini/api/admin/library"]  # fmt: skip
    assert cli.main(args) == 0
    shows = CAPTURED["shows"]
    assert isinstance(shows, frozenset)
    assert {"over-the-garden-wall", "the-simpsons"} <= shows and "nobody-airs-me" not in shows


def test_normalize_codec_comes_from_the_flag_or_rc_codec(
    tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "normalize", lambda db, media, **kw: _capture(kw))
    args = ["normalize", "--db", str(tmp_path / "rc.db"), "--catalog", str(make_catalog()),
            "--media-dir", str(tmp_path / "media")]  # fmt: skip

    def codec(argv: list[str]) -> object:
        assert cli.main(argv) == 0
        return CAPTURED["codec"]

    monkeypatch.delenv("RC_CODEC", raising=False)
    assert codec(args) == "h264"
    monkeypatch.setenv("RC_CODEC", "hevc")
    assert codec(args) == "hevc"
    assert codec([*args, "--codec", "h264"]) == "h264"


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


# --- the rig worker and the mini's index -------------------------------------------------


def _rig_db(tmp_path: Path) -> Path:
    from retro_cable.db import connect, migrate

    path = tmp_path / "rig.db"
    db = connect(path)
    migrate(db)
    db.execute("INSERT INTO shows (id, title, year) VALUES ('the-simpsons', 'The Simpsons', 1989)")
    (tmp_path / "media/tv/the-simpsons/s01").mkdir(parents=True)
    (tmp_path / "media/tv/the-simpsons/s01/the-simpsons-s01e01.mp4").write_bytes(b"mp4")
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, file_path, file_size,"
        " source_seconds, media_path, media_seconds) VALUES ('the-simpsons', 1, 1, 'Pilot',"
        " '/sources/tv/x.mkv', 10, 1320, 'tv/the-simpsons/s01/the-simpsons-s01e01.mp4', 1320)"
    )
    db.close()
    return path


def test_manifest_then_index_media(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rig = _rig_db(tmp_path)
    media = str(tmp_path / "media")
    assert cli.main(["manifest", "--db", str(rig), "--media-dir", media]) == 0
    assert (tmp_path / "media/manifest.json").is_file()
    capsys.readouterr()

    mini = tmp_path / "mini.db"
    assert cli.main(["index-media", "--db", str(mini), "--media-dir", media]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert (stats["episodes"], stats["media"]) == (1, 1)

    from retro_cable.db import connect
    from retro_cable.scheduler import load_library

    assert [p.episode for p in load_library(connect(mini)).episodes["the-simpsons"]] == [1]


def test_index_media_reports_a_missing_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["index-media", "--db", str(tmp_path / "m.db"), "--media-dir", str(tmp_path)]
    assert cli.main(args) == 1
    assert "manifest.json" in capsys.readouterr().err


def test_priority_years_merge_the_catalog_and_the_minis_channels(
    tmp_path: Path, make_catalog: MakeCatalog, capsys: pytest.CaptureFixture[str]
) -> None:
    from retro_cable.catalog import load_catalog
    from retro_cable.db import connect, migrate

    db = connect(tmp_path / "rig.db")
    migrate(db)
    catalog = load_catalog(make_catalog())
    library = {
        "items": [
            {"id": "johnny-bravo", "first_year": 1997, "channels": [{"id": "custom-toons"}]},
            {"id": "the-simpsons", "first_year": 1989, "channels": [{"id": "ktvu"}]},
            {"id": "nobody-airs-me", "first_year": 1970, "channels": []},
        ]
    }
    years = cli.priority_years(db, catalog, "http://mini/api/admin/library", lambda u: library)
    assert years["johnny-bravo"] == 1997  # only on an /admin channel on the mini
    assert years["the-simpsons"] == 1990  # the catalog's carriage start wins
    assert years["dexters-laboratory"] == 1996
    assert "nobody-airs-me" not in years

    def down(url: str) -> object:
        raise OSError("no route to host")

    years = cli.priority_years(db, catalog, "http://mini/api/admin/library", down)
    assert "johnny-bravo" not in years and years["the-simpsons"] == 1990
    assert "warning" in capsys.readouterr().err


def test_a_series_converts_with_the_show_its_seasons_air_as(
    tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    """overrides.yaml part_of: Lupin III's files convert when a carried show is some of its
    seasons, ranked by that show's earliest year (here only on the mini's /admin)."""
    from retro_cable.catalog import load_catalog
    from retro_cable.db import connect, migrate

    db = connect(tmp_path / "rig.db")
    migrate(db)
    overrides = (
        "shows:\n  - { show: lupin-the-3rd-part-ii,"
        " part_of: { show: lupin-iii, tvdb_id: 71863, seasons: [2] } }\n"
    )
    catalog = load_catalog(make_catalog(overrides=overrides))
    library = {"items": [{"id": "lupin-the-3rd-part-ii", "first_year": 2003,
                          "channels": [{"id": "adult-swim"}]}]}  # fmt: skip
    years = cli.priority_years(db, catalog, "http://mini/api/admin/library", lambda u: library)
    assert years["lupin-iii"] == 2003
    assert "lupin-iii" not in cli.priority_years(db, catalog)  # nothing carries part II


def test_a_series_converts_with_the_tv_movie_made_of_its_episodes(
    tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    """overrides.yaml episodes_of: Heidi (1993) is a miniseries no channel carries; its
    episodes convert because the movie made of them is carried (here on the mini's /admin)."""
    from retro_cable.catalog import load_catalog
    from retro_cable.db import connect, migrate

    db = connect(tmp_path / "rig.db")
    migrate(db)
    overrides = (
        "shows:\n  - { show: heidi-1993, episodes_of:"
        " { show: heidi, tvdb_id: 338932, episodes: [S01E01, S01E02] } }\n"
    )
    catalog = load_catalog(make_catalog(overrides=overrides))
    library = {"items": [{"id": "heidi-1993", "first_year": None, "kind": "movie",
                          "channels": [{"id": "disney-movies"}]}]}  # fmt: skip
    years = cli.priority_years(db, catalog, "http://mini/api/admin/library", lambda u: library)
    assert years["heidi"] == years["heidi-1993"]
    assert "heidi" not in cli.priority_years(db, catalog)  # nothing carries the movie


def test_normalize_rig_flags_pass_a_chooser_an_order_and_a_manifest_hook(
    tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "normalize", lambda db, media, **kw: _capture(kw))
    args = ["normalize", "--db", str(tmp_path / "rig.db"), "--catalog", str(make_catalog()),
            "--media-dir", str(tmp_path / "media"), "--all", "--encoder", "auto",
            "--order", "priority", "--manifest"]  # fmt: skip
    assert cli.main(args) == 0
    assert callable(CAPTURED["encoder"])
    assert callable(CAPTURED["order"])
    assert callable(CAPTURED["on_encoded"])
    from retro_cable.ingest.progress import ProgressFile

    watch = CAPTURED["watch"]
    assert isinstance(watch, ProgressFile), "the rig reports progress for the Media tab"
    assert watch.path == tmp_path / "media/progress.json" and watch.auto
    assert CAPTURED["shows"] is None
    assert (tmp_path / "media/manifest.json").is_file(), "the final manifest is written"


def test_assemble_reads_the_minis_titles_and_writes_the_manifest(
    tmp_path: Path, make_catalog: MakeCatalog, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    library = {"items": [{"id": "rbs", "title": "Really Big Shrimp", "channels": [{"id": "c"}]}]}
    monkeypatch.setattr(cli, "_get_json", lambda url: library)
    seen: dict[str, object] = {}

    def fake(db, catalog, media, **kw):  # type: ignore[no-untyped-def]
        seen.update(kw)
        db.execute(
            "INSERT INTO movies (id, title, media_path) VALUES ('rbs', 'RBS', 'movies/rbs.mp4')"
        )
        kw["on_assembled"]("rbs", "movies/rbs.mp4")
        return AssembleStats(assembled=["rbs"])

    monkeypatch.setattr(cli, "assemble", fake)
    media = tmp_path / "media"
    args = ["assemble", "--db", str(tmp_path / "rig.db"), "--catalog", str(make_catalog()),
            "--media-dir", str(media), "--manifest",
            "--priority-url", "http://mini/api/admin/library"]  # fmt: skip
    assert cli.main(args) == 0
    assert seen["extra"] == library["items"] and seen["dry_run"] is False
    assert json.loads(capsys.readouterr().out)["assembled"] == ["rbs"]
    assert (media / "movies/rbs.json").is_file()
    movies = json.loads((media / "manifest.json").read_text())["movies"]
    assert [m["id"] for m in movies] == ["rbs"]
