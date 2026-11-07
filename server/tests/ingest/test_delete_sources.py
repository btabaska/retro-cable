"""Originals of Live TV downloads are deleted once their encode is whole and checked (M4).

Only under one configured root (RC_DELETE_SOURCES_UNDER; unset means never), only after
normalize recorded a whole encode of an unchanged source, and never through a symlink. The
item, its encode and its manifest entry stay, and it is not queued for encoding again.
"""

import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from retro_cable.catalog import load_catalog
from retro_cable.ingest.manifest import build_manifest
from retro_cable.ingest.normalize import Item, Probe, Stream, deletable, no_crop, normalize, plan
from retro_cable.ingest.scan import scan_movies, scan_tv
from retro_cable.scheduler import load_library
from tests.conftest import MakeCatalog
from tests.ingest.test_normalize import Recorder, fake_probe

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
VIDEO = Stream(0, 640, 480, Fraction(1))


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# --- the root check ----------------------------------------------------------------------


@pytest.fixture
def layout(tmp_path: Path) -> Path:
    """media/sources (deletable) beside media/tv (encodes) and a Plex tree outside."""
    touch(tmp_path / "media/sources/tv/Show (1990)/Season 01/Show - S01E01.mkv")
    touch(tmp_path / "media/sources-old/Show - S01E01.mkv")
    touch(tmp_path / "media/tv/show/s01/show-s01e01.mp4")
    touch(tmp_path / "plex/tv/Show (1990)/Season 01/Show - S01E01.mkv")
    (tmp_path / "media/sources/tv/link-out.mkv").symlink_to(
        tmp_path / "plex/tv/Show (1990)/Season 01/Show - S01E01.mkv"
    )
    (tmp_path / "media/sources/tv/link-in.mkv").symlink_to(
        tmp_path / "media/sources/tv/Show (1990)/Season 01/Show - S01E01.mkv"
    )
    (tmp_path / "media/sources/plex").symlink_to(tmp_path / "plex/tv")
    return tmp_path


@pytest.mark.parametrize(
    ("path", "allowed", "why"),
    [
        ("media/sources/tv/Show (1990)/Season 01/Show - S01E01.mkv", True, "a download"),
        ("plex/tv/Show (1990)/Season 01/Show - S01E01.mkv", False, "Plex's tree"),
        ("media/tv/show/s01/show-s01e01.mp4", False, "an encode, beside the root"),
        ("media/sources-old/Show - S01E01.mkv", False, "a sibling sharing the prefix"),
        ("media/sources/../tv/show/s01/show-s01e01.mp4", False, "'..' out of the root"),
        ("media/sources/tv/link-out.mkv", False, "a symlink to Plex's tree"),
        ("media/sources/tv/link-in.mkv", False, "a symlink, even to a file inside"),
        ("media/sources/plex/Show (1990)/Season 01/Show - S01E01.mkv", False,
         "a symlinked folder out of the root"),
        ("media/sources", False, "the root itself"),
        ("media/sources/tv", False, "a folder"),
        ("media/sources/tv/nothing.mkv", False, "a file that isn't there"),
    ],
)  # fmt: skip
def test_only_regular_files_really_under_the_root_are_deletable(
    layout: Path, path: str, allowed: bool, why: str
) -> None:
    found = deletable(layout / path, layout / "media/sources")
    assert (found is not None) == allowed, why
    if found is not None:
        assert found == (layout / path).resolve()


def test_a_symlinked_root_is_resolved_too(layout: Path) -> None:
    (layout / "alias").symlink_to(layout / "media/sources")
    src = layout / "media/sources/tv/Show (1990)/Season 01/Show - S01E01.mkv"
    assert deletable(src, layout / "alias") == src.resolve()
    assert deletable(layout / "plex/tv/Show (1990)/Season 01/Show - S01E01.mkv",
                     layout / "alias") is None  # fmt: skip


SEGMENT = st.sampled_from(["..", ".", "sources", "tv", "plex", "out", "a.mkv", "link"])


@settings(max_examples=200, deadline=None)
@given(st.lists(SEGMENT, min_size=1, max_size=6))
def test_whatever_the_path_a_deletable_file_is_inside_the_root(parts: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve()
        root = base / "sources"
        for d in ("sources/tv", "plex", "out"):
            (base / d).mkdir(parents=True, exist_ok=True)
        for d in ("sources", "sources/tv", "plex", "out", ""):
            touch(base / d / "a.mkv")
        (base / "sources/tv/link").symlink_to(base / "out")
        (base / "sources/link").symlink_to(base / "plex/a.mkv")
        found = deletable(root.joinpath(*parts), root)
        if found is not None:
            real = Path(os.path.realpath(found))
            assert real.is_relative_to(root) and real != root and real.is_file()
            assert not root.joinpath(*parts).is_symlink()


# --- in the normalize job ----------------------------------------------------------------


@pytest.fixture
def downloads(db: sqlite3.Connection, tmp_path: Path) -> Path:
    """Show 's' with three sources: two downloads under media/sources and one Plex file."""
    db.execute("INSERT INTO shows (id, title) VALUES ('s', 'S')")
    paths = [
        tmp_path / "media/sources/tv/S/Season 01/S - S01E01.mkv",
        tmp_path / "media/sources/tv/S/Season 01/S - S01E02.mkv",
        tmp_path / "plex/tv/S/Season 01/S - S01E03.mkv",
    ]
    for ep, path in enumerate(paths, start=1):
        touch(path, b"x" * ep)
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, runtime_seconds, file_path)"
            " VALUES ('s', 1, ?, 1300, ?)",
            (ep, str(path)),
        )
    return tmp_path


def _run(
    db: sqlite3.Connection,
    root: Path,
    delete: Path | None,
    **kw: object,
) -> tuple[Recorder, list[str]]:
    rec = Recorder()
    stats = normalize(
        db, root / "media", run=rec, probe=kw.pop("probe", fake_probe), crop=no_crop,  # type: ignore[arg-type]
        delete_sources_under=delete, now=lambda: NOW, **kw,  # type: ignore[arg-type]
    )  # fmt: skip
    return rec, stats.deleted


def test_a_download_is_deleted_after_its_whole_encode(
    db: sqlite3.Connection, downloads: Path
) -> None:
    root = downloads / "media/sources"
    _, deleted = _run(db, downloads, root)
    # Relative paths only, so the log names no host or share.
    assert deleted == ["tv/S/Season 01/S - S01E01.mkv", "tv/S/Season 01/S - S01E02.mkv"]
    assert not list(root.rglob("*.mkv"))
    assert (downloads / "plex/tv/S/Season 01/S - S01E03.mkv").is_file(), "never Plex's files"
    rows = db.execute(
        "SELECT episode, file_path IS NULL, source_deleted_at, media_path, media_seconds"
        " FROM episodes ORDER BY episode"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        (1, 1, NOW.isoformat(), "tv/s/s01/s-s01e01.mp4", 1320),
        (2, 1, NOW.isoformat(), "tv/s/s01/s-s01e02.mp4", 1320),
        (3, 0, None, "tv/s/s01/s-s01e03.mp4", 1320),
    ]


def test_nothing_is_deleted_by_default(db: sqlite3.Connection, downloads: Path) -> None:
    _, deleted = _run(db, downloads, None)
    assert deleted == []
    assert len(list((downloads / "media/sources").rglob("*.mkv"))) == 2


def _probe_partial(partial: Probe) -> object:
    def probe(path: Path) -> Probe:
        return partial if path.name.endswith(".partial") else fake_probe(path)

    return probe


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"probe": _probe_partial(Probe(1200.0, VIDEO, 1))}, "the encode is 9% short"),
        ({"probe": _probe_partial(Probe(1320.9, VIDEO, None))}, "the encode lost its audio"),
        ({"max_seconds": 60, "probe": _probe_partial(Probe(60.0, VIDEO, 1))}, "a trial"),
        ({"probe": lambda p: Probe(100.0, VIDEO, 1)}, "a sample shorter than half its runtime"),
        (
            {"probe": lambda p: Probe(1320.9 if p.name.endswith(".partial") else 0.0, VIDEO, 1)},
            "the source's length is unknown, so the encode's can't be checked",
        ),
        ({"dry_run": True}, "a dry run"),
    ],
)
def test_no_original_is_deleted_without_a_whole_checked_encode(
    db: sqlite3.Connection, downloads: Path, kw: dict[str, object], why: str
) -> None:
    _, deleted = _run(db, downloads, downloads / "media/sources", **kw)
    assert deleted == [], why
    assert len(list((downloads / "media/sources").rglob("*.mkv"))) == 2, why


def test_a_source_that_changed_during_the_encode_is_kept(
    db: sqlite3.Connection, downloads: Path
) -> None:
    """Sonarr replaced the file (an upgrade) while ffmpeg read the old one."""
    src = downloads / "media/sources/tv/S/Season 01/S - S01E01.mkv"

    class Upgrade(Recorder):
        def __call__(self, cmd: list[str]) -> None:
            super().__call__(cmd)
            if str(src) in cmd:
                src.write_bytes(b"a bigger, better file")

    rec = Upgrade()
    stats = normalize(db, downloads / "media", run=rec, probe=fake_probe, crop=no_crop,
                      delete_sources_under=downloads / "media/sources")  # fmt: skip
    assert stats.deleted == ["tv/S/Season 01/S - S01E02.mkv"]
    assert src.is_file()


def test_an_existing_whole_encode_lets_its_download_go_on_a_later_pass(
    db: sqlite3.Connection, downloads: Path
) -> None:
    """Encoded before deletion was on (or a stop between encode and delete): the next pass
    deletes the original without encoding it again."""
    _run(db, downloads, None)
    rec, deleted = _run(db, downloads, downloads / "media/sources")
    assert rec.calls == []
    assert len(deleted) == 2


def test_a_deleted_original_is_not_queued_again_and_still_counts_as_done(
    db: sqlite3.Connection, downloads: Path
) -> None:
    _run(db, downloads, downloads / "media/sources")
    items = plan(db)
    assert [(i.label, i.source_deleted, i.up_to_date_in_db()) for i in items] == [
        ("s S01E01", True, True),
        ("s S01E02", True, True),
        ("s S01E03", False, True),
    ]
    rec = Recorder()
    stats = normalize(db, downloads / "media", run=rec, probe=fake_probe, crop=no_crop)
    assert rec.calls == []
    assert (stats.skipped, stats.missing, stats.encoded) == (3, [], 0)
    # If its encode is lost too, there is nothing to convert from: it is reported missing.
    (downloads / "media/tv/s/s01/s-s01e01.mp4").unlink()
    stats = normalize(db, downloads / "media", run=Recorder(), probe=fake_probe, crop=no_crop)
    assert stats.missing == ["s S01E01"] and stats.encoded == 0


def test_movies_lose_their_download_too_but_bumpers_never(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    movie = touch(tmp_path / "media/sources/movies/M (1998)/M (1998).mkv")
    bumper = touch(tmp_path / "media/sources/bumpers/ktvu/id.mp4")
    db.execute(
        "INSERT INTO movies (id, title, runtime_seconds, file_path) VALUES ('m', 'M', 1300, ?)",
        (str(movie),),
    )
    db.execute(
        "INSERT INTO bumpers (channel_id, file_path, runtime_seconds) VALUES ('ktvu', ?, 5)",
        (str(bumper),),
    )
    _, deleted = _run(db, tmp_path, tmp_path / "media/sources")
    assert deleted == ["movies/M (1998)/M (1998).mkv"]
    assert bumper.is_file()
    row = db.execute("SELECT file_path, source_deleted_at, media_path FROM movies").fetchone()
    assert tuple(row) == (None, NOW.isoformat(), "movies/m.mp4")


# --- the whole sequence: scan, normalize, delete, rescan ---------------------------------


def test_scan_normalize_delete_rescan_keeps_the_episode_on_the_air(
    db: sqlite3.Connection, tmp_path: Path, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    plex = tmp_path / "plex/tv"
    sources = tmp_path / "media/sources"
    touch(plex / "The Simpsons (1989)/Season 01/The Simpsons (1989) - S01E01 - Roasting.mkv")
    touch(sources / "tv/X-Men (1992)/Season 01/X-Men (1992) - S01E01 - Night of the Sentinels.mkv")
    touch(sources / "movies/Halloweentown (1998)/Halloweentown (1998).mkv")
    roots = [plex, sources / "tv"]
    movie_roots = [tmp_path / "plex/movies", sources / "movies"]

    scan_tv(db, roots, catalog, NOW)
    scan_movies(db, movie_roots, catalog, NOW)
    first = normalize(db, tmp_path / "media", run=Recorder(), probe=fake_probe, crop=no_crop,
                      delete_sources_under=sources, now=lambda: NOW)  # fmt: skip
    assert first.encoded == 3
    assert sorted(first.deleted) == [
        "movies/Halloweentown (1998)/Halloweentown (1998).mkv",
        "tv/X-Men (1992)/Season 01/X-Men (1992) - S01E01 - Night of the Sentinels.mkv",
    ]
    assert not list(sources.rglob("*.mkv"))

    stats = scan_tv(db, roots, catalog, NOW)
    scan_movies(db, movie_roots, catalog, NOW)
    assert stats.detached == 0

    manifest = build_manifest(db, NOW)
    assert {(e["show_id"], e["media_path"]) for e in manifest["episodes"]} == {
        ("the-simpsons", "tv/the-simpsons/s01/the-simpsons-s01e01.mp4"),
        ("x-men", "tv/x-men/s01/x-men-s01e01.mp4"),
    }
    assert [m["media_path"] for m in manifest["movies"]] == ["movies/halloweentown.mp4"]
    assert "x-men" in {s["id"] for s in manifest["shows"]}

    rec = Recorder()
    again = normalize(db, tmp_path / "media", run=rec, probe=fake_probe, crop=no_crop,
                      delete_sources_under=sources, now=lambda: NOW)  # fmt: skip
    assert rec.calls == [], "nothing is queued for encoding again"
    assert (again.encoded, again.missing, again.skipped) == (0, [], 3)
    assert [p.episode for p in load_library(db).episodes["x-men"]] == [1]

    # A new download of the same episode (Sonarr was told to fetch it again) is a new source.
    touch(sources / "tv/X-Men (1992)/Season 01/X-Men (1992) - S01E01 - Night of the Sentinels.mkv",
          b"new")  # fmt: skip
    scan_tv(db, roots, catalog, NOW)
    row = db.execute(
        "SELECT file_path IS NOT NULL, source_deleted_at FROM episodes WHERE show_id = 'x-men'"
    ).fetchone()
    assert tuple(row) == (1, None)


def test_items_carry_whether_their_original_was_deleted() -> None:
    item = Item("episodes", 1, "s S01E01", "", "tv/s/s01/s-s01e01.mp4", None, None, None)
    assert item.source_deleted is False
