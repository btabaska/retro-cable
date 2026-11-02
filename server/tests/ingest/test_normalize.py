"""The normalize job: ffmpeg H.264 + AAC 480p MP4s, idempotent, recorded in SQLite.

Sources are made here with ffmpeg's test generators (no real media). Tests that run ffmpeg
are skipped when it is missing; CI and `make bootstrap` install it.
"""

import itertools
import json
import os
import shutil
import sqlite3
import struct
import subprocess
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

import pytest

from retro_cable.ingest.normalize import (
    Crop,
    Encoder,
    Item,
    NormalizeStats,
    Probe,
    Stream,
    Table,
    build_command,
    merge_crops,
    no_crop,
    normalize,
    output_size,
    parse_cropdetect,
    parse_probe,
    plan,
    prioritize,
    resolve_source,
    target_path,
)
from retro_cable.scheduler import load_library

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg"
)


# --- pure parts --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "height", "sar", "expected"),
    [
        (640, 480, Fraction(1), (640, 480)),  # 4:3 stays 4:3
        (1920, 1080, Fraction(1), (854, 480)),  # 16:9 stays 16:9
        (1280, 720, Fraction(1), (854, 480)),
        (720, 480, Fraction(8, 9), (640, 480)),  # anamorphic DVD, 4:3
        (720, 480, Fraction(32, 27), (854, 480)),  # anamorphic DVD, 16:9
        (1440, 1080, Fraction(4, 3), (854, 480)),  # HDV
        (320, 240, Fraction(1), (320, 240)),  # never upscaled
        (513, 385, Fraction(1), (512, 384)),  # even dimensions for yuv420p
        (1422, 1072, Fraction(1), (640, 480)),  # within 3% of 4:3: drawn as 4:3
        (1510, 1080, Fraction(1), (672, 480)),  # an odd shape stays that shape
        (576, 432, Fraction(1), (576, 432)),
    ],
)
def test_output_size_keeps_the_display_aspect_at_480_lines(
    width: int, height: int, sar: Fraction, expected: tuple[int, int]
) -> None:
    assert output_size(width, height, sar) == expected


CROPDETECT_LOG = """
[Parsed_cropdetect_0 @ 0x1] x1:246 x2:1673 y1:8 y2:1079 w:1424 h:1072 x:248 y:8 pts:1 t:0.04 crop=1424:1072:248:8
[Parsed_cropdetect_0 @ 0x1] x1:240 x2:1679 y1:0 y2:1079 w:1440 h:1080 x:240 y:0 pts:2 t:0.08 crop=1440:1080:240:0
"""


def test_parse_cropdetect_takes_the_last_box() -> None:
    assert parse_cropdetect(CROPDETECT_LOG) == Crop(1440, 1080, 240, 0)
    assert (
        parse_cropdetect("no crop lines, e.g. a black sample: crop=-1904:-1072:1910:1076") is None
    )


def test_merge_crops_keeps_every_sampled_picture() -> None:
    pillar = [Crop(1440, 1080, 240, 0), Crop(1424, 1072, 248, 8), Crop(1440, 1072, 240, 4)]
    assert merge_crops(1920, 1080, pillar) == Crop(1440, 1080, 240, 0)
    # Any sample without a detected box (unreadable, all black) means: don't crop.
    assert merge_crops(1920, 1080, [*pillar[:2], None]) is None
    # Thin edges (overscan, a few dark columns) are left alone.
    assert merge_crops(640, 480, [Crop(632, 480, 6, 0)] * 3) is None
    assert merge_crops(1920, 1080, [Crop(1920, 1080, 0, 0)] * 3) is None


def test_crop_runs_before_scaling_and_sets_the_shape() -> None:
    hd = Probe(duration=60.0, video=Stream(0, 1920, 1080, Fraction(1)), audio_index=1)
    cmd = build_command(Path("a.mkv"), Path("a.mp4"), hd, "x264", crop=Crop(1440, 1080, 240, 0))
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.index("crop=1440:1080:240:0") < vf.index("scale=640:480")


def probe_json(*streams: dict[str, object], duration: str = "1320.5") -> dict[str, object]:
    return {"format": {"duration": duration}, "streams": list(streams)}


def test_parse_probe_picks_the_first_video_and_prefers_english_audio() -> None:
    data = probe_json(
        {"index": 0, "codec_type": "video", "width": 720, "height": 480,
         "sample_aspect_ratio": "8:9", "disposition": {"attached_pic": 0}},
        {"index": 1, "codec_type": "audio", "channels": 2, "tags": {"language": "jpn"}},
        {"index": 2, "codec_type": "audio", "channels": 6, "tags": {"language": "eng"}},
        {"index": 3, "codec_type": "subtitle"},
    )  # fmt: skip
    p = parse_probe(data)
    assert p.duration == 1320.5
    assert p.video == Stream(index=0, width=720, height=480, sar=Fraction(8, 9))
    assert p.audio_index == 2


def test_parse_probe_skips_cover_art_and_handles_missing_audio_and_sar() -> None:
    data = probe_json(
        {"index": 0, "codec_type": "video", "width": 600, "height": 600,
         "disposition": {"attached_pic": 1}},
        {"index": 1, "codec_type": "video", "width": 640, "height": 480,
         "sample_aspect_ratio": "0:1"},
    )  # fmt: skip
    p = parse_probe(data)
    assert p.video == Stream(index=1, width=640, height=480, sar=Fraction(1))
    assert p.audio_index is None


SD = Probe(duration=60.0, video=Stream(0, 720, 480, Fraction(8, 9)), audio_index=1)


def test_x264_command_is_the_default_shape() -> None:
    cmd = build_command(Path("/src/a.mkv"), Path("/out/a.mp4.partial"), SD, "x264")
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-i") : cmd.index("-i") + 2] == ["-i", "/src/a.mkv"]
    assert "-map 0:0 -map 0:1" in joined
    assert "scale=640:480" in joined
    assert "setsar=1" in joined
    assert "-c:v libx264" in joined
    assert "-crf" in cmd
    assert "-pix_fmt yuv420p" in joined
    assert "-force_key_frames expr:gte(t,n_forced*1)" in joined
    assert "-c:a aac -b:a 128k -ac 2 -ar 48000" in joined
    assert "-movflags +faststart" in joined
    assert cmd[-3:] == ["-f", "mp4", "/out/a.mp4.partial"]
    assert "-t" not in cmd


def test_nvenc_command_uses_the_gpu_encoder_with_forced_idr_keyframes() -> None:
    cmd = build_command(Path("/src/a.mkv"), Path("/out/a.mp4"), SD, "nvenc")
    joined = " ".join(cmd)
    assert "-c:v h264_nvenc" in joined
    assert "-forced-idr 1" in joined
    assert "libx264" not in joined
    assert "-crf" not in cmd
    assert "-force_key_frames expr:gte(t,n_forced*1)" in joined
    assert "scale=640:480" in joined


def test_command_without_audio_and_with_a_trial_limit() -> None:
    silent = Probe(duration=60.0, video=Stream(0, 1920, 1080, Fraction(1)), audio_index=None)
    cmd = build_command(Path("a.mkv"), Path("a.mp4"), silent, "x264", max_seconds=120)
    assert "-an" in cmd
    assert "-c:a" not in cmd
    assert cmd[cmd.index("-t") + 1] == "120"
    assert "scale=854:480" in " ".join(cmd)


def test_resolve_source_maps_plex_paths_and_roots_relative_ones(tmp_path: Path) -> None:
    maps = [("/data/tv", str(tmp_path / "tv")), ("/data/movies", "/mnt/movies")]
    got = resolve_source("/data/tv/Show (1999)/Season 01/a.mkv", None, maps)
    assert got == tmp_path / "tv" / "Show (1999)" / "Season 01" / "a.mkv"
    assert resolve_source("/data/tvx/a.mkv", None, maps) == Path("/data/tvx/a.mkv")
    assert resolve_source("_bumpers/ktvu/a.mkv", tmp_path, []) == tmp_path / "_bumpers/ktvu/a.mkv"
    assert resolve_source("_bumpers/ktvu/a.mkv", None, []) is None


def test_target_paths_are_deterministic_slugs() -> None:
    assert target_path("episodes", "the-simpsons", 10, 4, "x.mkv") == (
        "tv/the-simpsons/s10/the-simpsons-s10e04.mp4"
    )
    assert target_path("movies", "halloweentown", None, None, "y.mkv") == (
        "movies/halloweentown.mp4"
    )
    assert target_path("bumpers", "ktvu", None, None, "_bumpers/ktvu/1998/Fox Kids ID!.avi") == (
        "bumpers/ktvu/1998-fox-kids-id.mp4"
    )


# --- the job against a database ---------------------------------------------------------


def make_source(path: Path, size: str, sar: str = "1", seconds: int = 3) -> Path:
    """A test-pattern clip with sparse keyframes and 5.1 audio, like an old DVD rip."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
         "-vf", f"setsar={sar}", "-c:v", "mpeg4", "-g", "300", "-q:v", "5",
         "-af", "pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0", "-c:a", "ac3",
         str(path)],
        check=True,
    )  # fmt: skip
    return path


def ffprobe(path: Path, *args: str) -> dict[str, object]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", *args, str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data: dict[str, object] = json.loads(out)
    return data


def top_level_boxes(path: Path) -> list[str]:
    boxes, data, at = [], path.read_bytes(), 0
    while at + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[at : at + 8])
        if size == 1:
            size = struct.unpack(">Q", data[at + 8 : at + 16])[0]
        boxes.append(kind.decode())
        if size == 0:
            break
        at += size
    return boxes


def keyframe_times(path: Path) -> list[float]:
    data = ffprobe(path, "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags")
    packets = data["packets"]
    assert isinstance(packets, list)
    return sorted(float(p["pts_time"]) for p in packets if "K" in p["flags"])


@pytest.fixture
def library(db: sqlite3.Connection, tmp_path: Path) -> Path:
    """Three sources: a 4:3 episode, a 16:9 episode and a bumper; plus one missing file."""
    src = tmp_path / "src"
    if shutil.which("ffmpeg"):
        make_source(
            src / "Fixture Friends (1995)/Season 01/Fixture Friends - S01E01.avi", "640x480"
        )
        make_source(
            src / "Fixture Friends (1995)/Season 01/Fixture Friends - S01E02.mkv", "1280x720"
        )
        make_source(src / "_bumpers/ktvu/1998/id.avi", "720x480", sar="8/9", seconds=2)
    db.execute("INSERT INTO shows (id, title) VALUES ('fixture-friends', 'Fixture Friends')")
    for ep, name in [(1, "S01E01.avi"), (2, "S01E02.mkv"), (3, "S01E03.mkv")]:
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, title, air_date, runtime_seconds,"
            " file_path) VALUES ('fixture-friends', 1, ?, ?, '1995-09-0' || ?, 1320, ?)",
            (ep, f"Ep {ep}", ep, f"/plex/tv/Fixture Friends (1995)/Season 01/Fixture Friends - {name}"),
        )  # fmt: skip
    db.execute(
        "INSERT INTO bumpers (channel_id, file_path, runtime_seconds)"
        " VALUES ('ktvu', '_bumpers/ktvu/1998/id.avi', 10)"
    )
    return src


def run_job(db: sqlite3.Connection, src: Path, media: Path) -> NormalizeStats:
    return normalize(db, media, source_root=src, source_map=[("/plex/tv", str(src))])


def test_plan_lists_every_item_with_a_source_file(db: sqlite3.Connection, library: Path) -> None:
    items = plan(db)
    assert [(i.table, i.target) for i in items] == [
        ("episodes", "tv/fixture-friends/s01/fixture-friends-s01e01.mp4"),
        ("episodes", "tv/fixture-friends/s01/fixture-friends-s01e02.mp4"),
        ("episodes", "tv/fixture-friends/s01/fixture-friends-s01e03.mp4"),
        ("bumpers", "bumpers/ktvu/1998-id.mp4"),
    ]


def test_plan_can_keep_to_some_shows(db: sqlite3.Connection, library: Path) -> None:
    # Uncarried shows and movies wait until a channel carries them; bumpers always go.
    db.execute(
        "INSERT INTO movies (id, title, file_path) VALUES ('home-alone', 'Home Alone', '/m/ha.mkv')"
    )
    db.execute("INSERT INTO movies (id, title, file_path) VALUES ('heat', 'Heat', '/m/heat.mkv')")
    carried = {"fixture-friends", "home-alone"}
    assert [i.key for i in plan(db, shows=carried)] == ["fixture-friends"] * 3 + [
        "home-alone",
        "ktvu",
    ]
    assert [i.table for i in plan(db, shows=set())] == ["bumpers"]
    assert {i.key for i in plan(db) if i.table == "movies"} == {"heat", "home-alone"}


@needs_ffmpeg
def test_normalize_writes_playable_480p_mp4s_and_records_them(
    db: sqlite3.Connection, library: Path, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    stats = run_job(db, library, media)
    assert stats.encoded == 3
    assert stats.missing == ["fixture-friends S01E03"]
    assert stats.failed == []

    four_three = media / "tv/fixture-friends/s01/fixture-friends-s01e01.mp4"
    wide = media / "tv/fixture-friends/s01/fixture-friends-s01e02.mp4"
    bumper = media / "bumpers/ktvu/1998-id.mp4"
    for path, size in [(four_three, (640, 480)), (wide, (854, 480)), (bumper, (640, 480))]:
        info = ffprobe(path, "-show_streams")
        streams = info["streams"]
        assert isinstance(streams, list)
        video = next(s for s in streams if s["codec_type"] == "video")
        audio = next(s for s in streams if s["codec_type"] == "audio")
        assert video["codec_name"] == "h264"
        assert (video["width"], video["height"]) == size
        assert video["pix_fmt"] == "yuv420p"
        assert audio["codec_name"] == "aac"
        assert audio["channels"] == 2
        boxes = top_level_boxes(path)
        assert boxes.index("moov") < boxes.index("mdat"), "faststart: index before the data"
        keys = keyframe_times(path)
        gaps = [b - a for a, b in itertools.pairwise(keys)]
        assert keys[0] == pytest.approx(0, abs=0.05)
        assert gaps and max(gaps) <= 1.1
    assert not list(media.rglob("*.partial"))

    rows = db.execute(
        "SELECT episode, media_path, media_seconds, media_source_size FROM episodes ORDER BY episode"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        (1, "tv/fixture-friends/s01/fixture-friends-s01e01.mp4", 3,
         (library / "Fixture Friends (1995)/Season 01/Fixture Friends - S01E01.avi").stat().st_size),
        (2, "tv/fixture-friends/s01/fixture-friends-s01e02.mp4", 3,
         (library / "Fixture Friends (1995)/Season 01/Fixture Friends - S01E02.mkv").stat().st_size),
        (3, None, None, None),
    ]  # fmt: skip
    (bumper_row,) = db.execute("SELECT media_path, media_seconds FROM bumpers").fetchall()
    assert tuple(bumper_row) == ("bumpers/ktvu/1998-id.mp4", 2)

    # The 3-second fixtures are far shorter than the 1320 s the catalog expects: the job flags
    # them and the scheduler leaves them off unless short media is allowed explicitly.
    assert stats.short == [
        "fixture-friends S01E01 (3 s of 1320 s)",
        "fixture-friends S01E02 (3 s of 1320 s)",
    ]
    assert "fixture-friends" not in load_library(db).episodes
    # Allowed, the scheduler plays the normalized files at their measured length.
    lib = load_library(db, allow_short=True)
    eps = lib.episodes["fixture-friends"]
    assert [(p.path, p.seconds) for p in eps] == [
        ("tv/fixture-friends/s01/fixture-friends-s01e01.mp4", 3),
        ("tv/fixture-friends/s01/fixture-friends-s01e02.mp4", 3),
    ]
    assert [(b.path, b.seconds) for b in lib.bumpers["ktvu"]] == [("bumpers/ktvu/1998-id.mp4", 2)]


@needs_ffmpeg
def test_a_4_3_show_pillarboxed_in_a_16_9_file_comes_out_4_3(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    # Web releases of 4:3 shows often bake black side bars into a 16:9 frame.
    src = tmp_path / "pillarboxed.mkv"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=960x720:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=duration=6",
         "-vf", "pad=1280:720:160:0:black", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", str(src)],
        check=True,
    )  # fmt: skip
    db.execute("INSERT INTO shows (id, title) VALUES ('s', 'S')")
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, file_path) VALUES ('s', 1, 1, ?)",
        (str(src),),
    )
    media = tmp_path / "media"
    assert normalize(db, media).encoded == 1
    info = ffprobe(media / "tv/s/s01/s-s01e01.mp4", "-show_streams", "-select_streams", "v:0")
    streams = info["streams"]
    assert isinstance(streams, list)
    assert (streams[0]["width"], streams[0]["height"]) == (640, 480)


class Recorder:
    """Stands in for ffmpeg: writes the output file and remembers each command."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, cmd: list[str]) -> None:
        self.calls.append(cmd)
        if self.fail_on and any(self.fail_on in part for part in cmd):
            Path(cmd[-1]).write_bytes(b"half")
            raise subprocess.CalledProcessError(1, cmd[0])
        Path(cmd[-1]).write_bytes(b"mp4")


def fake_probe(path: Path) -> Probe:
    return Probe(duration=1320.9, video=Stream(0, 640, 480, Fraction(1)), audio_index=1)


# `fake_library` (three 1300 s episodes of show 's') is in conftest.py.


def test_rerun_skips_up_to_date_outputs_and_reencodes_changed_sources(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    first = Recorder()
    stats = normalize(db, media, run=first, probe=fake_probe, crop=no_crop)
    assert (stats.encoded, stats.skipped) == (3, 0)
    assert db.execute("SELECT media_seconds FROM episodes WHERE episode = 1").fetchone()[0] == 1320

    again = Recorder()
    stats = normalize(db, media, run=again, probe=fake_probe, crop=no_crop)
    assert (stats.encoded, stats.skipped) == (0, 3)
    assert again.calls == []

    # A changed source (size or mtime) and a deleted output are encoded again.
    (fake_library / "S01E02.mkv").write_bytes(b"changed!")
    src3 = fake_library / "S01E03.mkv"
    os.utime(src3, ns=(src3.stat().st_atime_ns, src3.stat().st_mtime_ns + 10**9))
    (media / "tv/s/s01/s-s01e01.mp4").unlink()
    third = Recorder()
    stats = normalize(db, media, run=third, probe=fake_probe, crop=no_crop)
    assert stats.encoded == 3
    assert stats.skipped == 0


def test_the_job_records_the_source_length_and_flags_short_encodes(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    """A 2-minute sample (or an encode cut short) must never pass for a whole episode: the
    job records the source's ffprobe length next to the encode's and lists every output
    shorter than half the expected runtime."""

    def probe(path: Path) -> Probe:
        # Episode 2's source is a 2-minute sample; episode 3's encode stopped at 100 s.
        seconds = {
            "S01E02.mkv": 120.4, "s-s01e02.mp4.partial": 120.4, "s-s01e03.mp4.partial": 100.0
        }.get(path.name, 1320.9)  # fmt: skip
        return Probe(duration=seconds, video=Stream(0, 640, 480, Fraction(1)), audio_index=1)

    stats = normalize(db, tmp_path / "media", run=Recorder(), probe=probe, crop=no_crop)
    assert stats.encoded == 2
    assert stats.short == ["s S01E02 (120 s of 1300 s)"]
    # A truncated encode is no longer recorded at all (see the length check below).
    assert stats.failed == ["s S01E03"]
    lengths = "SELECT episode, runtime_seconds, source_seconds, media_seconds FROM episodes"
    assert [tuple(r) for r in db.execute(lengths + " ORDER BY episode")] == [
        (1, 1300, 1320, 1320),
        (2, 1300, 120, 120),
        (3, 1300, None, None),
    ]
    assert [p.episode for p in load_library(db).episodes["s"]] == [1]


# --- an encode counts as done only when it is whole (M2 B11) ------------------------------


def _probe_with(partial: Probe) -> Callable[[Path], Probe]:
    def probe(path: Path) -> Probe:
        return partial if path.name.endswith(".partial") else fake_probe(path)

    return probe


VIDEO = Stream(0, 640, 480, Fraction(1))


@pytest.mark.parametrize(
    ("partial", "why"),
    [
        (Probe(duration=1280.0, video=VIDEO, audio_index=1), "3% short of the source"),
        (Probe(duration=1360.0, video=VIDEO, audio_index=1), "3% longer than the source"),
        (Probe(duration=1320.9, video=None, audio_index=1), "no video stream"),
        (Probe(duration=1320.9, video=VIDEO, audio_index=None), "the source had audio"),
        (Probe(duration=0.0, video=VIDEO, audio_index=1), "no length"),
    ],
)
def test_an_encode_that_is_not_whole_is_a_failure_and_leaves_nothing(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path, partial: Probe, why: str
) -> None:
    media = tmp_path / "media"
    stats = normalize(db, media, run=Recorder(), probe=_probe_with(partial), crop=no_crop)
    assert (stats.encoded, len(stats.failed)) == (0, 3), why
    assert not list(media.rglob("*.mp4*")), "no output and no partial file"
    assert db.execute("SELECT COUNT(media_path) FROM episodes").fetchone()[0] == 0


def test_an_encode_within_2_percent_of_the_source_is_whole(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    partial = Probe(duration=1300.0, video=VIDEO, audio_index=1)  # 1.6% short (AAC, VFR)
    stats = normalize(db, tmp_path / "media", run=Recorder(), probe=_probe_with(partial),
                      crop=no_crop)  # fmt: skip
    assert (stats.encoded, stats.failed) == (3, [])


def test_a_source_without_audio_needs_no_audio_in_the_encode(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    silent = Probe(duration=1320.9, video=VIDEO, audio_index=None)
    stats = normalize(db, tmp_path / "media", run=Recorder(), probe=lambda p: silent,
                      crop=no_crop)  # fmt: skip
    assert stats.encoded == 3


def test_a_trial_is_measured_against_its_limit_and_redone_by_a_full_run(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    trial = Probe(duration=60.0, video=VIDEO, audio_index=1)
    probe = _probe_with(trial)
    stats = normalize(db, media, run=Recorder(), probe=probe, crop=no_crop, max_seconds=60)
    assert stats.encoded == 3
    again = normalize(db, media, run=Recorder(), probe=probe, crop=no_crop, max_seconds=60)
    assert (again.encoded, again.skipped) == (0, 3)
    full = normalize(db, media, run=Recorder(), probe=fake_probe, crop=no_crop)
    assert (full.encoded, full.skipped) == (3, 0), "a trial is not a whole episode"


def test_a_recorded_truncated_encode_is_converted_again(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    """An encode recorded before the length check (or cut short since) is not skipped just
    because its file exists and the source is unchanged."""
    media = tmp_path / "media"
    assert normalize(db, media, run=Recorder(), probe=fake_probe, crop=no_crop).encoded == 3
    db.execute("UPDATE episodes SET media_seconds = 900 WHERE episode = 2")
    rec = Recorder()
    stats = normalize(db, media, run=rec, probe=fake_probe, crop=no_crop)
    assert (stats.encoded, stats.skipped) == (1, 2)
    assert any("S01E02" in part for part in rec.calls[0])
    assert db.execute("SELECT media_seconds FROM episodes WHERE episode = 2").fetchone()[0] == 1320


def test_an_interrupted_run_resumes_where_it_stopped(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    broken = Recorder(fail_on="S01E02")
    stats = normalize(db, media, run=broken, probe=fake_probe, crop=no_crop)
    assert stats.encoded == 2
    assert stats.failed == ["s S01E02"]
    assert not (media / "tv/s/s01/s-s01e02.mp4").exists()
    assert not list(media.rglob("*.partial")), "a failed encode leaves no partial file"
    assert db.execute("SELECT media_path FROM episodes WHERE episode = 2").fetchone()[0] is None

    resumed = Recorder()
    stats = normalize(db, media, run=resumed, probe=fake_probe, crop=no_crop)
    assert (stats.encoded, stats.skipped) == (1, 2)
    assert any("S01E02" in part for part in resumed.calls[0])


def test_limit_and_dry_run(db: sqlite3.Connection, fake_library: Path, tmp_path: Path) -> None:
    media = tmp_path / "media"
    dry = Recorder()
    stats = normalize(db, media, run=dry, probe=fake_probe, crop=no_crop, dry_run=True)
    assert dry.calls == []
    assert len(stats.commands) == 3
    assert db.execute("SELECT COUNT(media_path) FROM episodes").fetchone()[0] == 0

    some = Recorder()
    stats = normalize(db, media, run=some, probe=fake_probe, crop=no_crop, limit=2)
    assert stats.encoded == 2


def test_nvenc_is_selectable(db: sqlite3.Connection, fake_library: Path, tmp_path: Path) -> None:
    rec = Recorder()
    normalize(db, tmp_path / "media", run=rec, probe=fake_probe, crop=no_crop, encoder="nvenc")
    assert all("h264_nvenc" in cmd for cmd in rec.calls)


# --- the rig worker: per-item encoder, priority order, hooks ----------------------------


def test_encoder_can_be_chosen_per_item(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    picks: list[Encoder] = ["nvenc", "x264", "nvenc"]
    rec = Recorder()
    stats = normalize(
        db, tmp_path / "media", run=rec, probe=fake_probe, crop=no_crop,
        encoder=lambda: picks.pop(0),
    )  # fmt: skip
    assert ["h264_nvenc" in cmd for cmd in rec.calls] == [True, False, True]
    assert stats.encoders == {"nvenc": 2, "x264": 1}


def test_after_each_encode_the_hook_sees_the_recorded_item(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    seen: list[tuple[str, object]] = []

    def hook(item: Item) -> None:
        row = db.execute("SELECT media_path FROM episodes WHERE id = ?", (item.rowid,)).fetchone()
        seen.append((item.target, row["media_path"]))

    normalize(
        db, tmp_path / "media", run=Recorder(), probe=fake_probe, crop=no_crop, on_encoded=hook
    )
    assert seen == [(f"tv/s/s01/s-s01e0{n}.mp4", f"tv/s/s01/s-s01e0{n}.mp4") for n in (1, 2, 3)]


def _item(table: Table, key: str, n: int) -> Item:
    return Item(table, n, f"{key} {n}", f"/src/{key}/{n}.mkv", f"{key}/{n}.mp4", None, None,
                None, key=key)  # fmt: skip


def test_priority_order_carried_first_by_earliest_year_in_waves() -> None:
    """Carried shows (including /admin channels') first, earliest carriage year first, a
    wave of episodes per show at a time so every channel gets something to air early; then
    shows no channel carries, by their own year. Bumpers lead: they are tiny."""
    items = (
        [_item("episodes", "early", n) for n in (1, 2, 3)]
        + [_item("episodes", "late", n) for n in (1, 2, 3)]
        + [_item("episodes", "loose", n) for n in (1, 2)]
        + [_item("movies", "film", 1), _item("bumpers", "ktvu", 1)]
    )
    ordered = prioritize(
        items, carried={"late": 2004, "early": 1990, "film": 1998}, years={"loose": 1985}, wave=2
    )
    assert [i.label for i in ordered] == [
        "ktvu 1",
        "early 1", "early 2", "film 1", "late 1", "late 2",
        "early 3", "late 3",
        "loose 1", "loose 2",
    ]  # fmt: skip


def test_normalize_follows_a_given_order(
    db: sqlite3.Connection, fake_library: Path, tmp_path: Path
) -> None:
    rec = Recorder()
    normalize(
        db, tmp_path / "media", run=rec, probe=fake_probe, crop=no_crop,
        order=lambda items: list(reversed(items)),
    )  # fmt: skip
    assert ["S01E03" in " ".join(c) for c in rec.calls] == [True, False, False]
