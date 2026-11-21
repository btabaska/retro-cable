"""`ingest assemble`: TV movies TheTVDB files as episodes of a series (overrides.yaml
`episodes_of`), joined from those episodes' encodes into one movie the mini plays."""

import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import pytest

from retro_cable.catalog import Catalog, load_catalog
from retro_cable.ingest import manifest
from retro_cable.ingest.assemble import assemble
from retro_cable.ingest.normalize import Probe, Stream, probe
from tests.conftest import MakeCatalog

SHRIMP = """
shows:
  - show: really-big-shrimp
    year: 2007
    episodes_of: { show: drake-and-josh, episodes: [S04E17, S04E18] }
"""

CARRIAGE = """
carriage:
  - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 1990-01-01 }
  - { show: halloweentown, title: Halloweentown, channel: disney-movies, from: 1990-01-01, kind: movie }
  - { show: really-big-shrimp, title: "Drake & Josh: Really Big Shrimp", channel: disney-movies, from: 1990-01-01, kind: movie }
"""


class Ffmpeg:
    """Stands in for ffmpeg: writes the output (the last argument) and keeps each command."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.lists: list[str] = []  # the concat lists, read while they exist

    def __call__(self, cmd: list[str]) -> None:
        self.calls.append(cmd)
        if "concat" in cmd:
            self.lists.append(Path(cmd[cmd.index("-i") + 1]).read_text())
        Path(cmd[-1]).write_bytes(b"joined")


def prober(
    sizes: dict[str, tuple[int, int]] | None = None, joined: float = 2700.4
) -> Callable[[Path], Probe]:
    """ffprobe: each episode encode is 1350.2 s (`sizes` by file name, default 854x480 HEVC);
    the joined file is `joined` seconds."""

    def probe(path: Path) -> Probe:
        if path.name.startswith("really-big-shrimp"):
            return Probe(joined, Stream(0, 854, 480, Fraction(1), "hevc"), 1)
        w, h = (sizes or {}).get(path.name, (854, 480))
        return Probe(1350.2, Stream(0, w, h, Fraction(1), "hevc"), 1)

    return probe


@pytest.fixture
def media(tmp_path: Path) -> Path:
    return tmp_path / "media"


@pytest.fixture
def shrimp(db: sqlite3.Connection, media: Path) -> sqlite3.Connection:
    """Drake & Josh S04E16-E18 converted (the movie is E17-E18)."""
    db.execute("INSERT INTO shows (id, title) VALUES ('drake-and-josh', 'Drake & Josh')")
    for ep in (16, 17, 18):
        target = f"tv/drake-and-josh/s04/drake-and-josh-s04e{ep}.mp4"
        (media / target).parent.mkdir(parents=True, exist_ok=True)
        (media / target).write_bytes(b"x" * ep)
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, title, air_date, runtime_seconds,"
            " media_path, media_seconds, source_seconds)"
            " VALUES ('drake-and-josh', 4, ?, ?, '2007-08-03', 1320, ?, 1350, 1351)",
            (ep, f"Episode {ep}", target),
        )
    return db


def catalog(make_catalog: MakeCatalog, overrides: str = SHRIMP) -> Catalog:
    return load_catalog(make_catalog(overrides=overrides, carriage=CARRIAGE))


def test_joins_the_episodes_encodes_into_the_movie(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog
) -> None:
    ffmpeg = Ffmpeg()
    stats = assemble(shrimp, catalog(make_catalog), media, run=ffmpeg, probe=prober())
    assert stats.assembled == ["really-big-shrimp"]
    (cmd,) = ffmpeg.calls
    # Alike encodes (codec, size, audio) are joined without re-encoding, in the listed order.
    assert cmd[cmd.index("-c") + 1] == "copy" and "hvc1" in cmd
    (listing,) = ffmpeg.lists
    assert [line.split("/")[-1] for line in listing.splitlines()] == [
        "drake-and-josh-s04e17.mp4'",
        "drake-and-josh-s04e18.mp4'",
    ]
    assert (media / "movies/really-big-shrimp.mp4").read_bytes() == b"joined"
    row = shrimp.execute("SELECT * FROM movies WHERE id = 'really-big-shrimp'").fetchone()
    assert row["title"] == "Drake & Josh: Really Big Shrimp"
    assert (row["year"], row["premiere_date"]) == (2007, "2007-08-03")
    assert (row["media_path"], row["media_seconds"]) == ("movies/really-big-shrimp.mp4", 2700)
    assert row["source_seconds"] == 2700 and row["runtime_seconds"] == 2700
    assert row["file_path"] is None  # normalize leaves it alone: there is no source
    # The mini finds it in the manifest like any other encoded movie.
    movies = manifest.build_manifest(shrimp, datetime(2026, 11, 8))["movies"]
    assert [m["id"] for m in movies] == ["really-big-shrimp"]


def test_a_rerun_with_the_same_encodes_does_nothing(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog
) -> None:
    cat = catalog(make_catalog)
    assemble(shrimp, cat, media, run=Ffmpeg(), probe=prober())
    again = Ffmpeg()
    stats = assemble(shrimp, cat, media, run=again, probe=prober())
    assert (stats.assembled, stats.current, again.calls) == ([], ["really-big-shrimp"], [])
    # A re-encoded episode makes it join again.
    (media / "tv/drake-and-josh/s04/drake-and-josh-s04e18.mp4").write_bytes(b"new encode")
    stats = assemble(shrimp, cat, media, run=again, probe=prober())
    assert stats.assembled == ["really-big-shrimp"]


def test_waits_for_every_episode_to_be_converted(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog
) -> None:
    shrimp.execute("UPDATE episodes SET media_path = NULL WHERE episode = 18")
    ffmpeg = Ffmpeg()
    stats = assemble(shrimp, catalog(make_catalog), media, run=ffmpeg, probe=prober())
    assert stats.waiting == {"really-big-shrimp": ["S04E18"]}
    assert ffmpeg.calls == [] and not (media / "movies").exists()


def test_an_episode_whose_encode_is_cut_short_is_not_ready(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog
) -> None:
    shrimp.execute("UPDATE episodes SET media_seconds = 600 WHERE episode = 17")
    stats = assemble(shrimp, catalog(make_catalog), media, run=Ffmpeg(), probe=prober())
    assert stats.waiting == {"really-big-shrimp": ["S04E17"]}


def test_episodes_of_different_sizes_are_re_encoded_to_the_first_ones(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog
) -> None:
    ffmpeg = Ffmpeg()
    sizes = {"drake-and-josh-s04e18.mp4": (640, 480)}
    stats = assemble(shrimp, catalog(make_catalog), media, run=ffmpeg, probe=prober(sizes))
    assert stats.assembled == ["really-big-shrimp"]
    (cmd,) = ffmpeg.calls
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=854:480" in graph and "concat=n=2:v=1:a=1" in graph
    assert "libx265" in cmd and "copy" not in cmd


def test_a_join_of_the_wrong_length_leaves_nothing(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog
) -> None:
    stats = assemble(
        shrimp, catalog(make_catalog), media, run=Ffmpeg(), probe=prober(joined=1350.0)
    )
    assert list(stats.failed) == ["really-big-shrimp"]
    assert not list((media / "movies").iterdir())
    assert shrimp.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 0


def test_a_dry_run_joins_nothing(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog
) -> None:
    ffmpeg = Ffmpeg()
    stats = assemble(shrimp, catalog(make_catalog), media, run=ffmpeg, probe=prober(), dry_run=True)
    assert stats.assembled == ["really-big-shrimp"] and len(stats.commands) == 1
    assert ffmpeg.calls == [] and shrimp.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 0


def test_a_movie_with_its_own_file_is_left_to_normalize(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog
) -> None:
    shrimp.execute(
        "INSERT INTO movies (id, title, file_path) VALUES ('really-big-shrimp', 'RBS', '/m.mkv')"
    )
    ffmpeg = Ffmpeg()
    stats = assemble(shrimp, catalog(make_catalog), media, run=ffmpeg, probe=prober())
    assert stats.has_file == ["really-big-shrimp"] and ffmpeg.calls == []


def test_titles_come_from_the_minis_library_for_admin_channels(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog
) -> None:
    """Builder-placed movies are carried on the mini's /admin channels, not the YAML."""
    mini = [{"id": "really-big-shrimp", "title": "Really Big Shrimp", "kind": "movie",
             "channels": [{"id": "custom-teen-screen"}]}]  # fmt: skip
    cat = load_catalog(make_catalog(overrides=SHRIMP))
    assemble(shrimp, cat, media, run=Ffmpeg(), probe=prober(), extra=mini)
    row = shrimp.execute("SELECT title FROM movies WHERE id = 'really-big-shrimp'").fetchone()
    assert row["title"] == "Really Big Shrimp"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
@pytest.mark.parametrize("sizes", [((320, 240), (320, 240)), ((320, 240), (426, 240))])
def test_real_ffmpeg_joins_generated_episodes(
    shrimp: sqlite3.Connection, media: Path, make_catalog: MakeCatalog,
    sizes: tuple[tuple[int, int], tuple[int, int]],
) -> None:  # fmt: skip
    """Stand-in encodes made by ffmpeg's test generators, like normalize's: alike ones are
    stream-copied, different sizes re-encoded; either way the movie lasts as long as both."""
    for ep, (w, h) in zip((17, 18), sizes, strict=True):
        out = media / f"tv/drake-and-josh/s04/drake-and-josh-s04e{ep}.mp4"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=24:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-force_key_frames", "expr:gte(t,n_forced*1)", "-c:a", "aac", "-ac", "2",
             "-shortest", str(out)],
            check=True,
        )  # fmt: skip
    shrimp.execute("UPDATE episodes SET media_seconds = 2, source_seconds = 2")
    stats = assemble(shrimp, catalog(make_catalog), media)
    assert stats.assembled == ["really-big-shrimp"], stats
    joined = probe(media / "movies/really-big-shrimp.mp4")
    assert joined.video is not None and joined.audio_index is not None
    assert (joined.video.width, joined.video.height) == sizes[0]
    assert abs(joined.duration - 4) < 0.2
