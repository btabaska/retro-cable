"""The media manifest: the rig writes it next to the MP4s; the mini indexes it without
probing a single file over SMB."""

import json
import sqlite3
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import pytest

from retro_cable.db import connect, migrate
from retro_cable.ingest.manifest import (
    FORMAT,
    ManifestError,
    build_manifest,
    index_media,
    load_manifest,
    sidecar_path,
    watch,
    write_json_atomic,
    write_manifest,
)
from retro_cable.ingest.normalize import Probe, Stream, no_crop, normalize
from retro_cable.scheduler import load_library

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def fake_probe(path: Path) -> Probe:
    return Probe(duration=1320.9, video=Stream(0, 640, 480, Fraction(1)), audio_index=1)


def write_mp4(cmd: list[str]) -> None:
    Path(cmd[-1]).write_bytes(b"mp4")


@pytest.fixture
def rig(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """The rig's own database after a scan: two episodes on disk, one movie, one uncarried
    show; then a normalize run that encoded all of them into the media root."""
    db = connect(tmp_path / "rig.db")
    migrate(db)
    src = tmp_path / "sources"
    for name in ("S01E01.mkv", "S01E02.mkv", "Movie.mkv", "Other.S01E01.mkv"):
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(b"x" * 10)
    db.execute("INSERT INTO shows (id, title, year) VALUES ('the-simpsons', 'The Simpsons', 1989)")
    db.execute("INSERT INTO shows (id, title, year) VALUES ('other-show', 'Other Show', 2001)")
    for ep in (1, 2):
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, title, file_path, file_size)"
            " VALUES ('the-simpsons', 1, ?, ?, ?, 10)",
            (ep, f"Ep {ep}", str(src / f"S01E0{ep}.mkv")),
        )
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, file_path, file_size)"
        " VALUES ('other-show', 1, 1, 'Pilot', ?, 10)",
        (str(src / "Other.S01E01.mkv"),),
    )
    db.execute(
        "INSERT INTO movies (id, title, year, file_path, file_size)"
        " VALUES ('halloweentown', 'Halloweentown', 1998, ?, 10)",
        (str(src / "Movie.mkv"),),
    )
    media = tmp_path / "media"
    normalize(db, media, run=write_mp4, probe=fake_probe, crop=no_crop)
    return db, media


def mini_db(tmp_path: Path) -> sqlite3.Connection:
    db = connect(tmp_path / "mini.db")
    migrate(db)
    return db


def test_write_json_atomic_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "a" / "manifest.json"
    write_json_atomic(target, {"x": 1})
    write_json_atomic(target, {"x": 2})
    assert json.loads(target.read_text()) == {"x": 2}
    assert [p.name for p in target.parent.iterdir()] == ["manifest.json"]


def test_build_manifest_lists_the_library_and_its_encodes(
    rig: tuple[sqlite3.Connection, Path],
) -> None:
    db, _ = rig
    data = build_manifest(db, NOW)
    assert data["format"] == FORMAT
    assert data["generated_at"] == NOW.isoformat()
    assert [s["id"] for s in data["shows"]] == ["other-show", "the-simpsons"]
    first = data["episodes"][1]
    assert first["show_id"] == "the-simpsons"
    assert (first["season"], first["episode"]) == (1, 1)
    assert first["media_path"] == "tv/the-simpsons/s01/the-simpsons-s01e01.mp4"
    assert first["media_seconds"] == 1320
    assert first["source_seconds"] == 1320
    assert data["movies"][0]["media_path"] == "movies/halloweentown.mp4"
    assert data["bumpers"] == []


def test_write_manifest_puts_it_at_the_media_root(
    rig: tuple[sqlite3.Connection, Path],
) -> None:
    db, media = rig
    path = write_manifest(db, media, NOW)
    assert path == media / "manifest.json"
    assert load_manifest(path)["episodes"]


def test_sidecar_sits_next_to_its_mp4() -> None:
    assert sidecar_path(Path("/m"), "tv/a/s01/a-s01e01.mp4") == Path("/m/tv/a/s01/a-s01e01.json")


def test_load_manifest_rejects_other_formats_and_partial_files(tmp_path: Path) -> None:
    bad = tmp_path / "manifest.json"
    bad.write_text('{"format": "something-else/9"}')
    with pytest.raises(ManifestError):
        load_manifest(bad)
    bad.write_text('{"format": "retro-cable-me')
    with pytest.raises(ManifestError):
        load_manifest(bad)
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "missing.json")


def test_index_media_fills_an_empty_db_so_programs_play(
    rig: tuple[sqlite3.Connection, Path], tmp_path: Path
) -> None:
    db, media = rig
    manifest = build_manifest(db, NOW)
    mini = mini_db(tmp_path)

    stats = index_media(mini, manifest, media, NOW)

    assert (stats.shows, stats.episodes, stats.movies) == (2, 3, 1)
    assert stats.media == 4
    assert stats.missing_files == []
    lib = load_library(mini)
    assert [p.episode for p in lib.episodes["the-simpsons"]] == [1, 2]
    assert lib.episodes["the-simpsons"][0].seconds == 1320
    assert lib.episodes["the-simpsons"][0].path == "tv/the-simpsons/s01/the-simpsons-s01e01.mp4"
    assert lib.movies["halloweentown"].path == "movies/halloweentown.mp4"
    # The uncarried show reaches the library too, for /admin's channel builder.
    assert "other-show" in lib.episodes


def test_index_media_is_idempotent_and_writes_nothing_when_unchanged(
    rig: tuple[sqlite3.Connection, Path], tmp_path: Path
) -> None:
    db, media = rig
    manifest = build_manifest(db, NOW)
    mini = mini_db(tmp_path)
    index_media(mini, manifest, media, NOW)
    runs = mini.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0]
    api = connect(tmp_path / "mini.db")  # stands in for the api's watch connection
    (before,) = api.execute("PRAGMA data_version").fetchone()

    stats = index_media(mini, manifest, media, NOW)

    assert stats.changed == 0
    assert mini.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0] == runs
    # No write at all, so the api's schedule cache (keyed on data_version) stays warm.
    assert api.execute("PRAGMA data_version").fetchone()[0] == before


def test_index_media_keeps_metadata_the_mini_already_has(
    rig: tuple[sqlite3.Connection, Path], tmp_path: Path
) -> None:
    """A Plex or TMDB fill on the mini wins for titles, air dates and runtimes; the rig only
    contributes files and their measured lengths."""
    db, media = rig
    mini = mini_db(tmp_path)
    mini.execute("INSERT INTO shows (id, title, year) VALUES ('the-simpsons', 'Simpsons!', 1989)")
    mini.execute(
        "INSERT INTO episodes (show_id, season, episode, title, air_date, air_date_source,"
        " runtime_seconds) VALUES ('the-simpsons', 1, 1, 'Simpsons Roasting', '1989-12-17',"
        " 'tmdb', 1380)"
    )
    index_media(mini, build_manifest(db, NOW), media, NOW)
    row = mini.execute(
        "SELECT e.title, air_date, runtime_seconds, media_path, media_seconds, s.title AS show"
        " FROM episodes e JOIN shows s ON s.id = e.show_id WHERE episode = 1"
        " AND show_id = 'the-simpsons'"
    ).fetchone()
    assert tuple(row) == (
        "Simpsons Roasting", "1989-12-17", 1380,
        "tv/the-simpsons/s01/the-simpsons-s01e01.mp4", 1320, "Simpsons!",
    )  # fmt: skip


def test_index_media_skips_files_that_are_not_there(
    rig: tuple[sqlite3.Connection, Path], tmp_path: Path
) -> None:
    db, media = rig
    (media / "tv/the-simpsons/s01/the-simpsons-s01e02.mp4").unlink()
    mini = mini_db(tmp_path)
    stats = index_media(mini, build_manifest(db, NOW), media, NOW)
    assert stats.missing_files == ["tv/the-simpsons/s01/the-simpsons-s01e02.mp4"]
    assert [p.episode for p in load_library(mini).episodes["the-simpsons"]] == [1]


@pytest.mark.parametrize(
    "path", ["/etc/passwd.mp4", "../outside.mp4", "tv/../../x.mp4", "tv/a/notes.txt"]
)
def test_index_media_refuses_unsafe_media_paths(
    rig: tuple[sqlite3.Connection, Path], tmp_path: Path, path: str
) -> None:
    db, media = rig
    manifest = build_manifest(db, NOW)
    manifest["episodes"][0]["media_path"] = path
    mini = mini_db(tmp_path)
    stats = index_media(mini, manifest, media, NOW)
    assert stats.rejected == [path]
    assert (
        mini.execute("SELECT COUNT(*) FROM episodes WHERE media_path = ?", (path,)).fetchone()[0]
        == 0
    )


def test_prune_clears_media_the_manifest_no_longer_lists(
    rig: tuple[sqlite3.Connection, Path], tmp_path: Path
) -> None:
    db, media = rig
    mini = mini_db(tmp_path)
    index_media(mini, build_manifest(db, NOW), media, NOW)
    manifest = build_manifest(db, NOW)
    manifest["movies"] = []

    kept = index_media(mini, manifest, media, NOW)
    assert kept.pruned == 0
    assert "halloweentown" in load_library(mini).movies

    pruned = index_media(mini, manifest, media, NOW, prune=True)
    assert pruned.pruned == 1
    assert "halloweentown" not in load_library(mini).movies


def test_new_encodes_show_up_on_the_next_index(
    rig: tuple[sqlite3.Connection, Path], tmp_path: Path
) -> None:
    db, media = rig
    mini = mini_db(tmp_path)
    db.execute("UPDATE episodes SET media_path = NULL, media_seconds = NULL WHERE episode = 2")
    index_media(mini, build_manifest(db, NOW), media, NOW)
    assert [p.episode for p in load_library(mini).episodes["the-simpsons"]] == [1]

    normalize(db, media, run=write_mp4, probe=fake_probe, crop=no_crop)
    stats = index_media(mini, build_manifest(db, NOW), media, NOW)
    assert stats.media == 1
    assert [p.episode for p in load_library(mini).episodes["the-simpsons"]] == [1, 2]


def test_watch_indexes_only_when_the_manifest_changes(
    rig: tuple[sqlite3.Connection, Path], tmp_path: Path
) -> None:
    db, media = rig
    mini = mini_db(tmp_path)
    seen: list[int] = []

    def tick(n: int) -> None:
        if n == 1:
            write_manifest(db, media, NOW)  # appears after the first look
        if n == 3:
            (media / "manifest.json").write_text("{ half written")

    runs = watch(
        mini, media, interval=0, sleep=lambda s: None, ticks=5, on_tick=tick,
        report=lambda stats: seen.append(stats.changed), now=lambda: NOW,
    )  # fmt: skip
    assert runs == 1, "indexed once: absent, then new, then unchanged, then broken"
    assert seen and seen[0] > 0
