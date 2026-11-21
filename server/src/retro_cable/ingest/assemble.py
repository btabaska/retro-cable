"""TV movies made of a series' episodes: `python -m retro_cable.ingest assemble`.

TheTVDB files some TV movies as episodes of their series (Drake & Josh: Really Big Shrimp is
Drake & Josh S04E17-E18; Avatar: Sozin's Comet is S03E18-E21), so Radarr can't find them and
they come down with the series instead. overrides.yaml `episodes_of` names those episodes;
the rig runs this after normalize and joins their encodes, in the listed order, into the
movie's MP4 (`movies/<id>.mp4`), recorded as a movie with no source file. normalize leaves
it alone; the manifest carries it to the mini like any other encoded movie.

Rules:
- Only whole encodes join (their recorded length is their source's): a movie waits until
  every listed episode is converted, and is reported as waiting with the missing ones.
- Encodes alike in codec, picture size and audio are joined without re-encoding (ffmpeg's
  concat demuxer; normalize already gives them a keyframe every second). Otherwise they are
  re-encoded on the CPU, scaled to the first episode's size, with normalize's settings.
- The join must last as long as its episodes together (within 2%), or it is discarded.
- A rerun whose episode encodes are unchanged (sizes and mtimes) does nothing.
- A movie that has its own source file (Radarr or Plex found it after all) is normalize's.
"""

import math
import os
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retro_cable.admin import store
from retro_cable.catalog import Catalog
from retro_cable.catalog.models import ShowOverride
from retro_cable.catalog.overlay import apply_overlay
from retro_cable.db import transaction
from retro_cable.ingest.normalize import (
    Probe,
    Prober,
    Runner,
    codec_args,
    length_ok,
    probe,
    recorded_whole,
    run_ffmpeg,
    target_path,
)


@dataclass
class AssembleStats:
    assembled: list[str] = field(default_factory=list)  # in a dry run: would be
    current: list[str] = field(default_factory=list)  # already joined from these encodes
    waiting: dict[str, list[str]] = field(default_factory=dict)  # id -> episodes not ready
    has_file: list[str] = field(default_factory=list)  # its own source: normalize's
    failed: dict[str, str] = field(default_factory=dict)  # id -> why
    commands: list[list[str]] = field(default_factory=list)  # dry run only


@dataclass(frozen=True)
class _Part:
    key: str  # S04E17
    path: Path
    st: os.stat_result
    air_date: str | None


def _key(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


def _parts(
    db: sqlite3.Connection, media_dir: Path, o: ShowOverride
) -> tuple[list[_Part], list[str]]:
    """The listed episodes' whole encodes, in order, and the keys that aren't ready."""
    assert o.episodes_of is not None
    rows = {
        _key(r["season"], r["episode"]): r
        for r in db.execute(
            "SELECT season, episode, air_date, media_path, media_seconds, source_seconds"
            " FROM episodes WHERE show_id = ? AND media_path IS NOT NULL",
            (o.episodes_of.show,),
        )
    }
    parts: list[_Part] = []
    missing: list[str] = []
    for listed in o.episodes_of.episodes:
        season, episode = (int(n) for n in listed[1:].split("E"))
        key = _key(season, episode)
        r = rows.get(key)
        path = media_dir / r["media_path"] if r is not None else None
        if (
            r is None
            or path is None
            or r["media_seconds"] is None
            or not recorded_whole(r["media_seconds"], r["source_seconds"])
            or not path.is_file()
        ):
            missing.append(listed)
            continue
        parts.append(_Part(listed, path, path.stat(), r["air_date"]))
    return parts, missing


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


def _alike(probes: list[Probe]) -> bool:
    def shape(p: Probe) -> object:
        v = p.video
        return (v.codec, v.width, v.height, v.sar) if v else None, p.audio_index is not None

    return len({shape(p) for p in probes}) == 1


def join_command(
    paths: list[Path], probes: list[Probe], dst: Path, listing: Path, ffmpeg: str = "ffmpeg"
) -> list[str]:
    """The ffmpeg command joining `paths` into `dst`: a stream copy through the concat list
    `listing` (written here) when they are alike, else a re-encode to the first one's size."""
    first = probes[0].video
    if first is None or any(p.video is None for p in probes):
        raise ValueError("an episode encode has no video stream")
    audio = probes[0].audio_index is not None
    if any((p.audio_index is not None) != audio for p in probes):
        raise ValueError("some episode encodes have audio and some don't")
    hevc = first.codec == "hevc"
    head = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    tail = ["-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart",
            "-f", "mp4", str(dst)]  # fmt: skip
    if _alike(probes):
        listing.write_text("".join(f"file {_quote(p)}\n" for p in paths))
        cmd = [*head, "-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0:v:0"]
        cmd += ["-map", "0:a:0"] if audio else []
        cmd += ["-c", "copy"] + (["-tag:v", "hvc1"] if hevc else [])
        return cmd + tail
    cmd = list(head)
    for p in paths:
        cmd += ["-i", str(p)]
    chains, inputs = [], []
    for i in range(len(paths)):
        chains.append(f"[{i}:v:0]scale={first.width}:{first.height}:flags=lanczos,setsar=1[v{i}]")
        inputs.append(f"[v{i}]")
        if audio:
            chains.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
            inputs.append(f"[a{i}]")
    out = "[v][a]" if audio else "[v]"
    chains.append(f"{''.join(inputs)}concat=n={len(paths)}:v=1:a={int(audio)}{out}")
    cmd += ["-filter_complex", ";".join(chains), "-map", "[v]"]
    cmd += ["-map", "[a]"] if audio else []
    cmd += codec_args("hevc" if hevc else "h264", "x264", audio=audio)
    return cmd + tail


def _titles(catalog: Catalog, extra: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for c in catalog.carriage.carriage:
        titles.setdefault(c.show, c.title)
    for item in extra:  # the mini's /admin channels (the channel builder's movies)
        if item.get("id") and item.get("title") and item.get("channels"):
            titles.setdefault(str(item["id"]), str(item["title"]))
    return titles


def assemble(
    db: sqlite3.Connection,
    catalog: Catalog,
    media_dir: Path,
    *,
    extra: Iterable[Mapping[str, Any]] = (),
    dry_run: bool = False,
    run: Runner = run_ffmpeg,
    probe: Prober = probe,
    on_assembled: Callable[[str, str], None] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> AssembleStats:
    """Join every `episodes_of` movie whose episodes are all converted (see the module
    docstring). `extra` is the mini's /api/admin/library items, for the titles of movies on
    its /admin channels; `on_assembled(id, target)` runs after each is recorded (the rig
    writes the movie's sidecar there)."""
    stats = AssembleStats()
    effective = apply_overlay(catalog, store.head(db).overlay)
    titles = _titles(effective, extra)
    for o in effective.overrides.shows:
        if o.episodes_of is None or o.exclude:
            continue
        row = db.execute(
            "SELECT file_path, media_path, media_source_size, media_source_mtime_ns"
            " FROM movies WHERE id = ?",
            (o.show,),
        ).fetchone()
        if row is not None and row["file_path"] is not None:
            stats.has_file.append(o.show)
            continue
        parts, missing = _parts(db, media_dir, o)
        if missing:
            stats.waiting[o.show] = missing
            continue
        target = target_path("movies", o.show, None, None, "")
        size = sum(p.st.st_size for p in parts)
        mtime = max(p.st.st_mtime_ns for p in parts)
        if (
            row is not None
            and row["media_path"] == target
            and (row["media_source_size"], row["media_source_mtime_ns"]) == (size, mtime)
            and (media_dir / target).is_file()
        ):
            stats.current.append(o.show)
            continue
        dst = media_dir / target
        tmp = dst.with_name(dst.name + ".partial")
        try:
            with tempfile.TemporaryDirectory(prefix="rc-assemble-") as scratch:
                probes = [probe(p.path) for p in parts]
                cmd = join_command([p.path for p in parts], probes, tmp,
                                   Path(scratch) / "episodes.txt")  # fmt: skip
                if dry_run:
                    stats.commands.append(cmd)
                    stats.assembled.append(o.show)
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                run(cmd)
            out = probe(tmp)
            expected = sum(p.duration for p in probes)
            if out.video is None or out.duration <= 0:
                raise ValueError("the joined file has no video")
            if probes[0].audio_index is not None and out.audio_index is None:
                raise ValueError("the joined file lost the audio")
            if not length_ok(out.duration, expected):
                raise ValueError(f"joined {out.duration:.1f} s of {expected:.1f} s of episodes")
            os.replace(tmp, dst)
        except (subprocess.CalledProcessError, ValueError, KeyError, OSError) as err:
            tmp.unlink(missing_ok=True)
            stats.failed[o.show] = str(err)
            continue
        seconds = math.floor(out.duration)
        premiere = parts[0].air_date
        year = o.year or (int(premiere[:4]) if premiere else None)
        with transaction(db):
            db.execute(
                """INSERT INTO movies (id, title, year, premiere_date, runtime_seconds,
                     media_path, media_seconds, media_source_size, media_source_mtime_ns,
                     source_seconds, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET title = excluded.title,
                     year = COALESCE(movies.year, excluded.year),
                     premiere_date = COALESCE(movies.premiere_date, excluded.premiere_date),
                     runtime_seconds = excluded.runtime_seconds,
                     media_path = excluded.media_path, media_seconds = excluded.media_seconds,
                     media_source_size = excluded.media_source_size,
                     media_source_mtime_ns = excluded.media_source_mtime_ns,
                     source_seconds = excluded.source_seconds, updated_at = excluded.updated_at""",
                (o.show, titles.get(o.show, o.show), year, premiere, seconds, target, seconds,
                 size, mtime, math.floor(expected), now().isoformat()),
            )  # fmt: skip
        stats.assembled.append(o.show)
        if on_assembled is not None:
            on_assembled(o.show, target)
    return stats
