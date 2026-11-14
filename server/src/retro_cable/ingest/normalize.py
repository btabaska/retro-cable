"""Normalize source files into the MP4s the app plays: `python -m retro_cable.ingest normalize`.

Every episode, movie and bumper with a source file gets one H.264 (or HEVC) + AAC stereo MP4
under the media root, at most 480 lines with its display aspect kept (4:3 stays 4:3, 16:9
stays 16:9), the index at the front (faststart) and a keyframe every second, so playback can
start at any offset `/tune` returns. The output path is a slug of the item, never the source name.

The job is idempotent and resumable: an item whose output exists, whose source size and
mtime match what was recorded and whose recorded length is whole is skipped, each finished
item is committed on its own, and an encode writes to `<name>.partial` and is renamed only
once ffprobe reads it back whole: a video stream, an audio stream when the source has one,
and a length within 2% of the source's (or of `--max-seconds` for a trial). Anything else
is a failure, leaves no file and is tried again on the next run.

Encoders: libx264 on the CPU (default) or h264_nvenc on an NVIDIA GPU (`--encoder nvenc`).
With `--codec hevc` (the rig) they are libx265 and hevc_nvenc, tagged hvc1 for Safari and
tvOS: about 40% smaller at nearly the same VMAF, measured on NAS episodes (docs/specs/ops.md).
"""

import json
import math
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, Protocol

from retro_cable.db import transaction
from retro_cable.scheduler.library import is_short

Encoder = Literal["x264", "nvenc"]
ENCODERS: dict[str, Encoder] = {
    "x264": "x264",
    "libx264": "x264",
    "nvenc": "nvenc",
    "h264_nvenc": "nvenc",
}
Codec = Literal["h264", "hevc"]
Table = Literal["episodes", "movies", "bumpers"]

MAX_LINES = 480
KEYFRAME_SECONDS = 1
VIDEO_MAXRATE = "1500k"
VIDEO_BUFSIZE = "3000k"
HEVC_MAXRATE = "1000k"
HEVC_BUFSIZE = "2000k"
LENGTH_TOLERANCE = 0.02  # an encode within 2% of its source's length is whole
LENGTH_SLACK = 1.0  # seconds; container rounding on very short items such as bumpers


@dataclass(frozen=True)
class Stream:
    index: int
    width: int
    height: int
    sar: Fraction
    codec: str = ""  # ffprobe's codec_name: h264, hevc


@dataclass(frozen=True)
class Probe:
    duration: float
    video: Stream | None
    audio_index: int | None
    # The first audio language when every track names one and none is English (a dub-only
    # rip): build_command refuses it rather than air it.
    foreign_audio: str | None = None


ENGLISH = ("eng", "en")
# Language tags that say nothing about the language: such a track may well be English.
UNKNOWN_LANGUAGE = ("", "und", "unk", "mis", "mul", "zxx")


def _fraction(value: Any) -> Fraction:
    try:
        num, den = (int(x) for x in str(value).split(":"))
        return Fraction(num, den) if num > 0 and den > 0 else Fraction(1)
    except ValueError:
        return Fraction(1)


def _stream_seconds(stream: dict[str, Any]) -> float | None:
    """A stream's own length: `duration` (MP4) or a `DURATION` tag (MKV, `HH:MM:SS.ns`)."""
    tags = stream.get("tags") or {}
    tagged = [v for k, v in tags.items() if k.upper().startswith("DURATION")]
    for value in [stream.get("duration"), *tagged]:
        try:
            *hm, sec = str(value).split(":")
            minutes = 0
            for x in hm:
                minutes = minutes * 60 + int(x)
            seconds = minutes * 60 + float(sec)
        except ValueError:
            continue
        if seconds > 0:
            return seconds
    return None


def parse_probe(data: dict[str, Any]) -> Probe:
    """The first real video stream (not cover art) and the audio to keep (English if tagged;
    `foreign_audio` when every track is tagged with another language).
    The length is the longer of those two streams, so a subtitle that runs past the end
    doesn't count; the container's length when they don't say, and never more than it."""
    video = None
    lengths: dict[int, float | None] = {}
    audio: list[tuple[int, str]] = []
    for s in data.get("streams", []):
        lengths[int(s.get("index", -1))] = _stream_seconds(s)
        kind = s.get("codec_type")
        if kind == "video" and video is None and not s.get("disposition", {}).get("attached_pic"):
            video = Stream(
                int(s["index"]),
                int(s["width"]),
                int(s["height"]),
                _fraction(s.get("sample_aspect_ratio")),
                str(s.get("codec_name", "")),
            )
        elif kind == "audio":
            audio.append((int(s["index"]), str(s.get("tags", {}).get("language", ""))))
    english = [i for i, lang in audio if lang.lower() in ENGLISH]
    pick = english[0] if english else (audio[0][0] if audio else None)
    named = audio and all(lang.lower() not in UNKNOWN_LANGUAGE for _, lang in audio)
    foreign = audio[0][1] if named and not english else None
    duration = float(data.get("format", {}).get("duration") or 0)
    kept = [lengths.get(i) for i in (video.index if video else None, pick) if i is not None]
    if kept and all(kept):
        longest = max(x for x in kept if x)
        duration = min(duration, longest) if duration > 0 else longest
    return Probe(duration=duration, video=video, audio_index=pick, foreign_audio=foreign)


# How far before the container's end to look for the last picture and sound packets.
PACKET_WINDOW = 600.0


def probe(path: Path, ffprobe: str = "ffprobe") -> Probe:
    """ffprobe's view of a file. When the kept streams don't say how long they are and another
    stream (a subtitle) could have set the container's length, the length is where their last
    packets end, read from the final ten minutes."""
    out = subprocess.run(
        [ffprobe, "-v", "error", "-of", "json", "-show_format", "-show_streams", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data = json.loads(out)
    info = parse_probe(data)
    streams = {int(s.get("index", -1)): s for s in data.get("streams", [])}
    picture = info.video.index if info.video else None
    kept = {i for i in (picture, info.audio_index) if i is not None}
    if (
        info.duration <= 0
        or not kept
        or all(_stream_seconds(streams[i]) for i in kept)
        or set(streams) <= kept
    ):
        return info
    start = max(info.duration - PACKET_WINDOW, 0.0)
    out = subprocess.run(
        [ffprobe, "-v", "error", "-read_intervals", f"{start:.3f}%",
         "-show_entries", "packet=stream_index,pts_time,duration_time", "-of", "csv=p=0",
         str(path)],
        capture_output=True, text=True, check=False,
    ).stdout  # fmt: skip
    ends: dict[int, float] = {}
    for line in out.splitlines():
        try:
            index, pts, length = line.split(",")[:3]
            stream = int(index)
            end = float(pts) + (float(length) if length not in ("", "N/A") else 0.0)
        except ValueError:
            continue
        if stream in kept:
            ends[stream] = max(end, ends.get(stream, 0.0))
    if set(ends) != kept:
        return info
    return replace(info, duration=min(info.duration, max(ends.values())))


def source_seconds(path: Path) -> float | None:
    """A file's length from ffprobe, or None when ffprobe can't read it (`ingest scan`)."""
    try:
        seconds = probe(path).duration
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError):
        return None
    return seconds if seconds > 0 else None


def _even(x: float) -> int:
    return max(2, math.floor(x / 2 + 0.5) * 2)  # nearest even, halves up


TV_SHAPES = (Fraction(4, 3), Fraction(16, 9))
SNAP = Fraction(3, 100)  # within 3% of 4:3 or 16:9 counts as that shape


def output_size(width: int, height: int, sar: Fraction) -> tuple[int, int]:
    """Square-pixel size at most MAX_LINES tall with the source's display aspect; no upscaling.
    A picture within 3% of 4:3 or 16:9 is drawn at exactly that shape."""
    lines = min(MAX_LINES, height)
    lines -= lines % 2
    aspect = Fraction(width) * sar / height
    for shape in TV_SHAPES:
        if abs(aspect - shape) <= shape * SNAP:
            aspect = shape
    return _even(float(lines * aspect)), lines


@dataclass(frozen=True)
class Crop:
    width: int
    height: int
    x: int
    y: int


CROPDETECT = "cropdetect=limit=24:round=2:reset=0"
_CROP = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
MIN_BAR = Fraction(25, 1000)  # ignore bars under 2.5% of the frame a side (overscan, noise)


def parse_cropdetect(stderr: str) -> Crop | None:
    """The last crop ffmpeg's cropdetect printed (it only grows with reset=0)."""
    found = _CROP.findall(stderr)
    return Crop(*(int(x) for x in found[-1])) if found else None


def merge_crops(width: int, height: int, crops: Sequence[Crop | None]) -> Crop | None:
    """The smallest crop that keeps every sampled picture: the union of the boxes. None when
    any sample saw no bars, or the bars are too thin to matter."""
    if not crops or any(c is None for c in crops):
        return None
    boxes = [c for c in crops if c is not None]
    x, y = min(c.x for c in boxes), min(c.y for c in boxes)
    right = max(c.x + c.width for c in boxes)
    bottom = max(c.y + c.height for c in boxes)
    crop = Crop(right - x, bottom - y, x, y)
    cut_w, cut_h = Fraction(width - crop.width, width), Fraction(height - crop.height, height)
    if cut_w < 2 * MIN_BAR and cut_h < 2 * MIN_BAR:
        return None
    return crop


CropDetector = Callable[[Path, Probe, float | None], Crop | None]


def detect_crop(src: Path, info: Probe, max_seconds: float | None) -> Crop | None:
    """Pillarbox or letterbox bars baked into the picture (a 4:3 show in a 16:9 web release),
    from cropdetect on three 4-second samples at 20%, 50% and 80% of the running time."""
    if info.video is None:
        return None
    length = min(info.duration, max_seconds) if max_seconds else info.duration
    crops = []
    for at in (0.2, 0.5, 0.8):
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-ss", f"{length * at:.1f}", "-i", str(src),
             "-t", "4", "-map", f"0:{info.video.index}", "-vf", CROPDETECT, "-an", "-f", "null",
             "-"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        crops.append(parse_cropdetect(result.stderr))
    return merge_crops(info.video.width, info.video.height, crops)


def no_crop(src: Path, info: Probe, max_seconds: float | None) -> Crop | None:
    return None


def codec_args(codec: Codec, encoder: Encoder, *, audio: bool) -> list[str]:
    """The video and audio encoder settings every encode shares (normalize, assemble)."""
    args: list[str] = []
    if codec == "hevc" and encoder == "nvenc":
        args += ["-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "26",
                "-b:v", "0", "-spatial-aq", "1", "-temporal-aq", "1", "-forced-idr", "1",
                "-profile:v", "main"]  # fmt: skip
    elif codec == "hevc":
        args += ["-c:v", "libx265", "-preset", "fast", "-crf", "22",
                "-x265-params", "log-level=error:forced-idr=1"]  # fmt: skip
    elif encoder == "nvenc":
        args += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                "-forced-idr", "1"]  # fmt: skip
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    if codec == "hevc":
        args += ["-maxrate", HEVC_MAXRATE, "-bufsize", HEVC_BUFSIZE, "-tag:v", "hvc1"]
    else:
        args += ["-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE, "-profile:v", "high"]
    args += ["-pix_fmt", "yuv420p",
            "-force_key_frames", f"expr:gte(t,n_forced*{KEYFRAME_SECONDS})"]  # fmt: skip
    if audio:
        audio_rate = "96k" if codec == "hevc" else "128k"
        args += ["-c:a", "aac", "-b:a", audio_rate, "-ac", "2", "-ar", "48000"]
    else:
        args += ["-an"]
    return args


def build_command(
    src: Path,
    dst: Path,
    info: Probe,
    encoder: Encoder,
    max_seconds: float | None = None,
    ffmpeg: str = "ffmpeg",
    crop: Crop | None = None,
    codec: Codec = "h264",
) -> list[str]:
    if info.video is None:
        raise ValueError(f"{src.name}: no video stream")
    if info.foreign_audio is not None:
        raise ValueError(f"{src.name}: no English audio (only {info.foreign_audio})")
    width, height = info.video.width, info.video.height
    # bwdif only touches frames flagged interlaced (DVD and broadcast rips); progressive passes.
    filters = ["bwdif=mode=send_frame:deint=interlaced"]
    if crop is not None:
        filters.append(f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}")
        width, height = crop.width, crop.height
    out_w, out_h = output_size(width, height, info.video.sar)
    filters += [f"scale={out_w}:{out_h}:flags=lanczos", "setsar=1"]
    vf = ",".join(filters)
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    if max_seconds is not None:
        cmd += ["-t", f"{max_seconds:g}"]
    cmd += ["-map", f"0:{info.video.index}"]
    if info.audio_index is not None:
        cmd += ["-map", f"0:{info.audio_index}"]
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn", "-vf", vf]
    cmd += codec_args(codec, encoder, audio=info.audio_index is not None)
    cmd += ["-max_muxing_queue_size", "1024", "-movflags", "+faststart", "-f", "mp4", str(dst)]
    return cmd


# --- what to encode ----------------------------------------------------------------------


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.casefold())) or "item"


def target_path(
    table: Table, key: str, season: int | None, episode: int | None, file_path: str
) -> str:
    """Where an item's MP4 goes under the media root. `key` is the show, movie or channel id."""
    if table == "episodes":
        s, e = season or 0, episode or 0
        return f"tv/{key}/s{s:02d}/{key}-s{s:02d}e{e:02d}.mp4"
    if table == "movies":
        return f"movies/{key}.mp4"
    parts = Path(file_path).with_suffix("").parts
    if parts and parts[0] == "_bumpers":
        parts = parts[1:]
    if parts and parts[0] == key:
        parts = parts[1:]
    return f"bumpers/{key}/{_slug('-'.join(parts))}.mp4"


@dataclass(frozen=True)
class Item:
    table: Table
    rowid: int | str
    label: str
    file_path: str
    target: str
    media_path: str | None
    source_size: int | None
    source_mtime_ns: int | None
    runtime_seconds: int | None = None  # the catalog runtime (Plex, TMDB); None for bumpers
    key: str = ""  # the show, movie or channel id
    media_seconds: int | None = None  # the recorded encode's length
    source_seconds: int | None = None  # the source's length when it was encoded
    # The source was deleted after a whole, checked encode (`delete_sources_under`): the
    # encode is all there is, so the item counts as done and is never encoded again.
    source_deleted: bool = False

    def up_to_date_in_db(self, max_seconds: float | None = None) -> bool:
        """The database says this item's encode is in place and whole (no file checks)."""
        return (
            self.media_path is not None
            and self.media_path == self.target
            and recorded_whole(self.media_seconds, self.source_seconds, max_seconds)
        )


def length_ok(encoded: float, source: float) -> bool:
    """`encoded` is within 2% (and at least a second) of `source`."""
    return abs(encoded - source) <= max(source * LENGTH_TOLERANCE, LENGTH_SLACK)


def recorded_whole(
    media_seconds: int | None, source_seconds: int | None, max_seconds: float | None = None
) -> bool:
    """A recorded encode's length against its source's, both floored to whole seconds (so
    one more second of slack). Unknown lengths can't be checked and pass."""
    if media_seconds is None or source_seconds is None or source_seconds <= 0:
        return media_seconds is None or media_seconds > 0
    expected = min(source_seconds, max_seconds) if max_seconds else source_seconds
    return abs(media_seconds - expected) <= max(expected * LENGTH_TOLERANCE, LENGTH_SLACK) + 1


HAS_SOURCE = "(file_path IS NOT NULL OR (source_deleted_at IS NOT NULL AND media_path IS NOT NULL))"


def plan(db: sqlite3.Connection, shows: Collection[str] | None = None) -> list[Item]:
    """Every episode, movie and bumper with a source file (or whose source was deleted after
    its encode), in a stable order. With `shows`, only those shows' episodes and those movies
    (the CLI passes what some channel carries)."""
    items = [
        Item("episodes", r["id"], f"{r['show_id']} S{r['season']:02d}E{r['episode']:02d}",
             r["file_path"] or "",
             target_path("episodes", r["show_id"], r["season"], r["episode"], ""),
             r["media_path"], r["media_source_size"], r["media_source_mtime_ns"],
             r["runtime_seconds"], r["show_id"], r["media_seconds"], r["source_seconds"],
             r["file_path"] is None)
        for r in db.execute(
            "SELECT id, show_id, season, episode, file_path, media_path, media_source_size,"
            " media_source_mtime_ns, runtime_seconds, media_seconds, source_seconds"
            f" FROM episodes WHERE {HAS_SOURCE} ORDER BY show_id, season, episode"
        )
        if shows is None or r["show_id"] in shows
    ]  # fmt: skip
    items += [
        Item("movies", r["id"], r["id"], r["file_path"] or "",
             target_path("movies", r["id"], None, None, ""),
             r["media_path"], r["media_source_size"], r["media_source_mtime_ns"],
             r["runtime_seconds"], r["id"], r["media_seconds"], r["source_seconds"],
             r["file_path"] is None)
        for r in db.execute(
            "SELECT id, file_path, media_path, media_source_size, media_source_mtime_ns,"
            f" runtime_seconds, media_seconds, source_seconds FROM movies WHERE {HAS_SOURCE}"
            " ORDER BY id"
        )
        if shows is None or r["id"] in shows
    ]  # fmt: skip
    items += [
        Item("bumpers", r["id"], f"{r['channel_id']} {Path(r['file_path']).name}",
             r["file_path"],
             target_path("bumpers", r["channel_id"], None, None, r["file_path"]),
             r["media_path"], r["media_source_size"], r["media_source_mtime_ns"],
             key=r["channel_id"], media_seconds=r["media_seconds"])
        for r in db.execute(
            "SELECT id, channel_id, file_path, media_path, media_source_size,"
            " media_source_mtime_ns, media_seconds FROM bumpers ORDER BY channel_id, file_path"
        )
    ]  # fmt: skip
    return items


WAVE = 13  # episodes per show per pass in priority order (about a season)


def prioritize(
    items: Sequence[Item],
    carried: Mapping[str, int],
    years: Mapping[str, int | None],
    wave: int = WAVE,
) -> list[Item]:
    """The rig's order for a long first conversion. Bumpers first (they are tiny), then what
    some channel carries (`carried`: id -> earliest carriage year, /admin channels included),
    then the rest by their own year (`years`). Within a tier, shows take turns a `wave` of
    episodes at a time, earliest year first, so every channel has something to air early."""
    seen: dict[str, int] = {}

    def rank(pair: tuple[int, Item]) -> tuple[int, int, int, str, int]:
        n, item = pair
        if item.table == "bumpers":
            return (0, 0, 0, item.key, n)
        index = seen.get(item.key, 0)
        seen[item.key] = index + 1
        if item.key in carried:
            return (1, index // wave, carried[item.key], item.key, n)
        return (2, index // wave, years.get(item.key) or 9999, item.key, n)

    ranked = [(rank(pair), pair[1]) for pair in enumerate(items)]
    return [item for _, item in sorted(ranked, key=lambda r: r[0])]


def resolve_source(
    file_path: str, source_root: Path | None, source_map: Sequence[tuple[str, str]]
) -> Path | None:
    """Where the worker reads a source. Absolute paths (as Plex or `scan` saw them) go through
    the first matching `FROM=TO` prefix map; relative ones are under `source_root`."""
    if file_path.startswith("/"):
        for prefix, local in source_map:
            head = prefix.rstrip("/")
            if file_path == head or file_path.startswith(head + "/"):
                return Path(local) / file_path[len(head) :].lstrip("/")
        return Path(file_path)
    return source_root / file_path if source_root is not None else None


# --- the job -----------------------------------------------------------------------------


Runner = Callable[[list[str]], None]
Prober = Callable[[Path], Probe]
Outcome = Literal["encoded", "failed", "missing", "skipped"]


class Watcher(Protocol):
    """Told about the pass as it goes (the rig's progress.json, ingest/progress.py)."""

    def planned(self, items: Sequence[Item]) -> None: ...
    def started(self, item: Item, encoder: Encoder) -> None: ...
    def finished(self, item: Item, outcome: Outcome) -> None: ...
    def close(self) -> None: ...


def run_ffmpeg(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)


@dataclass
class NormalizeStats:
    encoded: int = 0
    skipped: int = 0
    missing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # Encoded, but shorter than half the expected runtime (a sample source, a --max-seconds
    # trial, a truncated encode): `label (N s of M s)`. They are recorded, and the scheduler
    # leaves them off the air unless RC_ALLOW_SHORT_MEDIA is set.
    short: list[str] = field(default_factory=list)
    encoders: dict[str, int] = field(default_factory=dict)  # encodes per encoder
    # Originals deleted after a whole, checked encode (`delete_sources_under`), relative to
    # that root, so the stats and the log name no host or share.
    deleted: list[str] = field(default_factory=list)
    # Stopped at `limit` or `until` with items still to look at: the rig's worker scans again
    # soon instead of sleeping (ops/rig/worker.sh).
    unfinished: bool = False
    commands: list[list[str]] = field(default_factory=list)  # dry run only


def _record(
    db: sqlite3.Connection, item: Item, seconds: int, source: int | None, st: os.stat_result
) -> None:
    measured = ", source_seconds = ?" if item.table != "bumpers" else ""
    values: tuple[object, ...] = (item.target, seconds, st.st_size, st.st_mtime_ns)
    if measured:
        values += (source,)
    with transaction(db):
        db.execute(
            f"UPDATE {item.table} SET media_path = ?, media_seconds = ?, media_source_size = ?,"
            f" media_source_mtime_ns = ?{measured} WHERE id = ?",
            (*values, item.rowid),
        )


def check_encode(out: Probe, info: Probe, max_seconds: float | None) -> None:
    """Raise ValueError unless the encode `out` of source `info` is whole: playable video,
    audio when the source had some, and the source's length (or the trial's) within 2%."""
    if out.video is None:
        raise ValueError("encoded file has no video stream")
    if info.audio_index is not None and out.audio_index is None:
        raise ValueError("encoded file lost the audio")
    if out.duration <= 0:
        raise ValueError("encoded file has no duration")
    if info.duration > 0:
        expected = min(info.duration, max_seconds) if max_seconds else info.duration
        if not length_ok(out.duration, expected):
            raise ValueError(f"encoded {out.duration:.1f} s of a {expected:.1f} s source")


def expected_seconds(item: Item, source: int | None) -> int:
    """What the whole program should last: its catalog runtime or its source's measured
    length, whichever is longer (bumpers have no expectation)."""
    if item.table == "bumpers":
        return 0
    return max(item.runtime_seconds or 0, source or 0)


# --- deleting originals (the LiveTV downloads) -------------------------------------------


def deletable(path: Path, root: Path) -> Path | None:
    """The file to delete for source `path`, or None unless it may go: `path` is not itself a
    symlink, and its real path is a regular file strictly inside the real path of `root`
    (so neither `..` nor a symlinked folder can reach outside it)."""
    try:
        if path.is_symlink():
            return None
        real = Path(os.path.realpath(path))
        base = Path(os.path.realpath(root))
        if real == base or not real.is_relative_to(base) or not real.is_file():
            return None
    except (OSError, ValueError):
        return None
    return real


def _delete_source(
    db: sqlite3.Connection,
    media_dir: Path,
    item: Item,
    src: Path,
    st: os.stat_result,
    root: Path,
    stats: NormalizeStats,
    now: Callable[[], datetime],
    log: Callable[[str], None] | None,
) -> None:
    """Delete `item`'s source if its recorded encode is whole and checked (both lengths
    known and within 2%, not short, its MP4 in place), the source is unchanged since it was
    encoded, and it lies under `root`. Then the row keeps its encode and loses its file_path."""
    if item.table == "bumpers":
        return
    row = db.execute(
        f"SELECT media_path, media_seconds, source_seconds, runtime_seconds, media_source_size,"
        f" media_source_mtime_ns FROM {item.table} WHERE id = ?",
        (item.rowid,),
    ).fetchone()
    media, source = row["media_seconds"], row["source_seconds"]
    if (
        row["media_path"] != item.target
        or media is None
        or source is None
        or not recorded_whole(media, source)
        or is_short(media, max(row["runtime_seconds"] or 0, source))
        or not (media_dir / item.target).is_file()
    ):
        return
    real = deletable(src, root)
    if real is None:
        return
    try:
        now_st = real.stat()
        if (now_st.st_size, now_st.st_mtime_ns) != (st.st_size, st.st_mtime_ns) or (
            row["media_source_size"],
            row["media_source_mtime_ns"],
        ) != (st.st_size, st.st_mtime_ns):
            return  # replaced since it was encoded (an upgrade): the next pass encodes it
        real.unlink()
    except OSError as err:
        if log is not None:
            log(f"{item.label}: could not delete its original: {err.strerror or err}")
        return
    with transaction(db):
        db.execute(
            f"UPDATE {item.table} SET file_path = NULL, file_size = NULL, source_deleted_at = ?"
            " WHERE id = ?",
            (now().isoformat(), item.rowid),
        )
    relative = real.relative_to(Path(os.path.realpath(root))).as_posix()
    stats.deleted.append(relative)
    if log is not None:
        log(f"deleted original {relative}")


def normalize(
    db: sqlite3.Connection,
    media_dir: Path,
    *,
    source_root: Path | None = None,
    source_map: Sequence[tuple[str, str]] = (),
    encoder: Encoder | Callable[[], Encoder] = "x264",
    codec: Codec = "h264",
    max_seconds: float | None = None,
    limit: int | None = None,
    until: datetime | None = None,
    force: bool = False,
    dry_run: bool = False,
    run: Runner = run_ffmpeg,
    probe: Prober = probe,
    crop: CropDetector = detect_crop,
    progress: Callable[[str], None] | None = None,
    shows: Collection[str] | None = None,
    order: Callable[[list[Item]], list[Item]] | None = None,
    on_encoded: Callable[[Item], None] | None = None,
    watch: Watcher | None = None,
    delete_sources_under: Path | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    log: Callable[[str], None] | None = None,
) -> NormalizeStats:
    """Encode every item that has no up-to-date output. `limit` caps the encodes in this run,
    and no item starts once `now()` reaches `until` (the one running then finishes).

    `encoder` is fixed, or asked once per item (the rig's GPU check, see ingest/gpu.py).
    `order` reorders the plan (see `prioritize`); `on_encoded` runs after each item is
    recorded (the rig writes its manifest sidecar there); `watch` hears about every item
    (the rig's progress file, ingest/progress.py).

    With `delete_sources_under` (the rig: the LiveTV downloads), an episode's or movie's
    source under that folder is deleted once its encode is recorded whole and checked, also
    for an item encoded on an earlier pass; never in a dry run or a `max_seconds` trial (see
    `_delete_source`). `now` stamps `source_deleted_at`; `log` hears what was deleted (by
    relative path) or couldn't be. Such an item then counts as done."""
    stats = NormalizeStats()
    items = plan(db, shows)
    if order is not None:
        items = order(items)
    if dry_run:
        watch = None
    if watch is not None:
        watch.planned(items)

    def done(item: Item, outcome: Outcome) -> None:
        if watch is not None:
            watch.finished(item, outcome)

    may_delete = delete_sources_under is not None and not dry_run and max_seconds is None

    def delete(item: Item, src: Path, st: os.stat_result) -> None:
        if may_delete and delete_sources_under is not None:
            _delete_source(db, media_dir, item, src, st, delete_sources_under, stats, now,
                           log)  # fmt: skip

    try:
        for item in items:
            if (limit is not None and stats.encoded >= limit) or (
                until is not None and now() >= until
            ):
                stats.unfinished = True
                break
            if item.source_deleted:  # the encode is all there is: never encoded again
                if item.up_to_date_in_db() and (media_dir / item.target).is_file():
                    stats.skipped += 1
                    done(item, "skipped")
                else:  # its encode is gone too: nothing to convert from
                    stats.missing.append(item.label)
                    done(item, "missing")
                continue
            src = resolve_source(item.file_path, source_root, source_map)
            if src is None or not src.is_file():
                stats.missing.append(item.label)
                done(item, "missing")
                continue
            st = src.stat()
            if (
                not force
                and item.up_to_date_in_db(max_seconds)
                and item.source_size == st.st_size
                and item.source_mtime_ns == st.st_mtime_ns
                and (media_dir / item.target).is_file()
            ):
                stats.skipped += 1
                delete(item, src, st)
                done(item, "skipped")
                continue
            outcome = _encode(
                db, media_dir, item, src, st, stats, encoder=encoder, codec=codec,
                max_seconds=max_seconds, dry_run=dry_run, run=run, probe=probe, crop=crop,
                progress=progress, watch=watch,
            )  # fmt: skip
            if outcome == "encoded":
                delete(item, src, st)
            if outcome == "encoded" and on_encoded is not None:
                on_encoded(item)
            if outcome is not None:
                done(item, outcome)
    finally:
        if watch is not None:
            watch.close()
    return stats


def _encode(
    db: sqlite3.Connection,
    media_dir: Path,
    item: Item,
    src: Path,
    st: os.stat_result,
    stats: NormalizeStats,
    *,
    encoder: Encoder | Callable[[], Encoder],
    codec: Codec,
    max_seconds: float | None,
    dry_run: bool,
    run: Runner,
    probe: Prober,
    crop: CropDetector,
    progress: Callable[[str], None] | None,
    watch: Watcher | None,
) -> Outcome | None:
    """Encode one item, check it and record it. None for a dry run."""
    dst = media_dir / item.target
    tmp = dst.with_name(dst.name + ".partial")
    try:
        info = probe(src)
        source = math.floor(info.duration) if info.duration > 0 else None
        bars = crop(src, info, max_seconds)
        chosen = encoder if isinstance(encoder, str) else encoder()
        cmd = build_command(src, tmp, info, chosen, max_seconds, crop=bars, codec=codec)
        if dry_run:
            stats.commands.append(cmd)
            return None
        if progress:
            progress(f"{item.label} [{chosen}]")
        if watch is not None:
            watch.started(item, chosen)
        dst.parent.mkdir(parents=True, exist_ok=True)
        run(cmd)
        out = probe(tmp)
        check_encode(out, info, max_seconds)
        seconds = math.floor(out.duration)
        os.replace(tmp, dst)
    except (subprocess.CalledProcessError, ValueError, KeyError, OSError) as err:
        tmp.unlink(missing_ok=True)
        stats.failed.append(item.label)
        if progress is not None and isinstance(err, ValueError):
            progress(f"{item.label} failed: {err}")
        return "failed"
    _record(db, item, seconds, source, st)
    stats.encoded += 1
    stats.encoders[chosen] = stats.encoders.get(chosen, 0) + 1
    expected = expected_seconds(item, source)
    if is_short(seconds, expected):
        stats.short.append(f"{item.label} ({seconds} s of {expected} s)")
    return "encoded"
