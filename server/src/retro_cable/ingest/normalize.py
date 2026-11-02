"""Normalize source files into the MP4s the app plays: `python -m retro_cable.ingest normalize`.

Every episode, movie and bumper with a source file gets one H.264 + AAC stereo MP4 under the
media root, at most 480 lines with its display aspect kept (4:3 stays 4:3, 16:9 stays 16:9),
the index at the front (faststart) and a keyframe every second, so playback can start at any
offset `/tune` returns. The output path is a slug of the item, never the source name.

The job is idempotent and resumable: an item whose output exists and whose source size and
mtime match what was recorded is skipped, each finished item is committed on its own, and an
encode writes to `<name>.partial` and is renamed only once ffprobe reads it back.

Encoders: libx264 on the CPU (default) or h264_nvenc on an NVIDIA GPU (`--encoder nvenc`).
"""

import json
import math
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from retro_cable.db import transaction

Encoder = Literal["x264", "nvenc"]
ENCODERS: dict[str, Encoder] = {
    "x264": "x264",
    "libx264": "x264",
    "nvenc": "nvenc",
    "h264_nvenc": "nvenc",
}
Table = Literal["episodes", "movies", "bumpers"]

MAX_LINES = 480
KEYFRAME_SECONDS = 1
VIDEO_MAXRATE = "1500k"
VIDEO_BUFSIZE = "3000k"


@dataclass(frozen=True)
class Stream:
    index: int
    width: int
    height: int
    sar: Fraction


@dataclass(frozen=True)
class Probe:
    duration: float
    video: Stream | None
    audio_index: int | None


def _fraction(value: Any) -> Fraction:
    try:
        num, den = (int(x) for x in str(value).split(":"))
        return Fraction(num, den) if num > 0 and den > 0 else Fraction(1)
    except ValueError:
        return Fraction(1)


def parse_probe(data: dict[str, Any]) -> Probe:
    """The first real video stream (not cover art) and the audio to keep (English if tagged)."""
    video = None
    audio: list[tuple[int, str]] = []
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        if kind == "video" and video is None and not s.get("disposition", {}).get("attached_pic"):
            video = Stream(
                int(s["index"]),
                int(s["width"]),
                int(s["height"]),
                _fraction(s.get("sample_aspect_ratio")),
            )
        elif kind == "audio":
            audio.append((int(s["index"]), str(s.get("tags", {}).get("language", ""))))
    english = [i for i, lang in audio if lang.lower() in ("eng", "en")]
    pick = english[0] if english else (audio[0][0] if audio else None)
    duration = float(data.get("format", {}).get("duration") or 0)
    return Probe(duration=duration, video=video, audio_index=pick)


def probe(path: Path, ffprobe: str = "ffprobe") -> Probe:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-of", "json", "-show_format", "-show_streams", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return parse_probe(json.loads(out))


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


def build_command(
    src: Path,
    dst: Path,
    info: Probe,
    encoder: Encoder,
    max_seconds: float | None = None,
    ffmpeg: str = "ffmpeg",
    crop: Crop | None = None,
) -> list[str]:
    if info.video is None:
        raise ValueError(f"{src.name}: no video stream")
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
    if encoder == "nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                "-forced-idr", "1"]  # fmt: skip
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    cmd += ["-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE, "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-force_key_frames", f"expr:gte(t,n_forced*{KEYFRAME_SECONDS})"]  # fmt: skip
    if info.audio_index is not None:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
    else:
        cmd += ["-an"]
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


def plan(db: sqlite3.Connection, shows: Collection[str] | None = None) -> list[Item]:
    """Every episode, movie and bumper with a source file, in a stable order. With `shows`,
    only those shows' episodes (the CLI passes the shows some channel carries)."""
    items = [
        Item("episodes", r["id"], f"{r['show_id']} S{r['season']:02d}E{r['episode']:02d}",
             r["file_path"],
             target_path("episodes", r["show_id"], r["season"], r["episode"], r["file_path"]),
             r["media_path"], r["media_source_size"], r["media_source_mtime_ns"])
        for r in db.execute(
            "SELECT id, show_id, season, episode, file_path, media_path, media_source_size,"
            " media_source_mtime_ns FROM episodes WHERE file_path IS NOT NULL"
            " ORDER BY show_id, season, episode"
        )
        if shows is None or r["show_id"] in shows
    ]  # fmt: skip
    items += [
        Item("movies", r["id"], r["id"], r["file_path"],
             target_path("movies", r["id"], None, None, r["file_path"]),
             r["media_path"], r["media_source_size"], r["media_source_mtime_ns"])
        for r in db.execute(
            "SELECT id, file_path, media_path, media_source_size, media_source_mtime_ns"
            " FROM movies WHERE file_path IS NOT NULL ORDER BY id"
        )
    ]  # fmt: skip
    items += [
        Item("bumpers", r["id"], f"{r['channel_id']} {Path(r['file_path']).name}",
             r["file_path"],
             target_path("bumpers", r["channel_id"], None, None, r["file_path"]),
             r["media_path"], r["media_source_size"], r["media_source_mtime_ns"])
        for r in db.execute(
            "SELECT id, channel_id, file_path, media_path, media_source_size,"
            " media_source_mtime_ns FROM bumpers ORDER BY channel_id, file_path"
        )
    ]  # fmt: skip
    return items


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


def run_ffmpeg(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)


@dataclass
class NormalizeStats:
    encoded: int = 0
    skipped: int = 0
    missing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)  # dry run only


def _record(db: sqlite3.Connection, item: Item, seconds: int, st: os.stat_result) -> None:
    with transaction(db):
        db.execute(
            f"UPDATE {item.table} SET media_path = ?, media_seconds = ?, media_source_size = ?,"
            " media_source_mtime_ns = ? WHERE id = ?",
            (item.target, seconds, st.st_size, st.st_mtime_ns, item.rowid),
        )


def normalize(
    db: sqlite3.Connection,
    media_dir: Path,
    *,
    source_root: Path | None = None,
    source_map: Sequence[tuple[str, str]] = (),
    encoder: Encoder = "x264",
    max_seconds: float | None = None,
    limit: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    run: Runner = run_ffmpeg,
    probe: Prober = probe,
    crop: CropDetector = detect_crop,
    progress: Callable[[str], None] | None = None,
    shows: Collection[str] | None = None,
) -> NormalizeStats:
    """Encode every item that has no up-to-date output. `limit` caps the encodes in this run."""
    stats = NormalizeStats()
    for item in plan(db, shows):
        if limit is not None and stats.encoded >= limit:
            break
        src = resolve_source(item.file_path, source_root, source_map)
        if src is None or not src.is_file():
            stats.missing.append(item.label)
            continue
        st = src.stat()
        dst = media_dir / item.target
        if (
            not force
            and item.media_path == item.target
            and item.source_size == st.st_size
            and item.source_mtime_ns == st.st_mtime_ns
            and dst.is_file()
        ):
            stats.skipped += 1
            continue
        tmp = dst.with_name(dst.name + ".partial")
        try:
            info = probe(src)
            bars = crop(src, info, max_seconds)
            cmd = build_command(src, tmp, info, encoder, max_seconds, crop=bars)
            if dry_run:
                stats.commands.append(cmd)
                continue
            if progress:
                progress(item.label)
            dst.parent.mkdir(parents=True, exist_ok=True)
            run(cmd)
            seconds = math.floor(probe(tmp).duration)
            if seconds <= 0:
                raise ValueError("encoded file has no duration")
            os.replace(tmp, dst)
        except (subprocess.CalledProcessError, ValueError, KeyError, OSError):
            tmp.unlink(missing_ok=True)
            stats.failed.append(item.label)
            continue
        _record(db, item, seconds, st)
        stats.encoded += 1
    return stats
