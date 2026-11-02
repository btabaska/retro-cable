"""Tests for the fixture media generator. Uses short clips to stay fast."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from generate import BUMPERS, SHOWS, Clip, generate, plan_clips


def probe(path: Path) -> dict[str, object]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result: dict[str, object] = json.loads(out)
    return result


def test_plan_has_three_shows_of_three_episodes_and_two_bumpers() -> None:
    clips = plan_clips(episode_seconds=60, bumper_seconds=5)
    episodes = [c for c in clips if c.kind == "episode"]
    bumpers = [c for c in clips if c.kind == "bumper"]
    assert len(SHOWS) == 3
    assert len(episodes) == 9
    assert len(bumpers) == len(BUMPERS) == 2
    assert all(c.seconds == 60 for c in episodes)


def test_episode_paths_use_plex_naming() -> None:
    clip = plan_clips(episode_seconds=60, bumper_seconds=5)[0]
    show = clip.show
    assert clip.relpath == Path(f"{show} (1995)/Season 01/{show} - S01E01.mp4")


def test_bumper_paths_live_under_bumpers_dir() -> None:
    bumpers = [c for c in plan_clips(episode_seconds=60, bumper_seconds=5) if c.kind == "bumper"]
    assert all(c.relpath.parts[0] == "_bumpers" for c in bumpers)


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("fixtures")
    generate(out, episode_seconds=2, bumper_seconds=1)
    return out


def test_generate_writes_every_clip_as_h264_aac(generated: Path) -> None:
    clips = plan_clips(episode_seconds=2, bumper_seconds=1)
    for clip in clips:
        info = probe(generated / clip.relpath)
        streams = info["streams"]
        assert isinstance(streams, list)
        codecs = {s["codec_type"]: s["codec_name"] for s in streams}
        assert codecs == {"video": "h264", "audio": "aac"}
        fmt = info["format"]
        assert isinstance(fmt, dict)
        assert float(fmt["duration"]) == pytest.approx(clip.seconds, abs=0.2)


def test_generate_writes_manifest(generated: Path) -> None:
    manifest = json.loads((generated / "manifest.json").read_text())
    assert len(manifest["clips"]) == 11
    first = manifest["clips"][0]
    assert set(first) == {"kind", "show", "season", "episode", "seconds", "path"}


def test_generate_is_idempotent(generated: Path) -> None:
    clip: Clip = plan_clips(episode_seconds=2, bumper_seconds=1)[0]
    before = (generated / clip.relpath).stat().st_mtime_ns
    generate(generated, episode_seconds=2, bumper_seconds=1)
    assert (generated / clip.relpath).stat().st_mtime_ns == before


def test_demo_feeds_cover_every_runtime_class() -> None:
    from generate import DEMO_FEEDS, plan_demo

    clips = plan_demo({"half-hour": 3})
    names = {c.relpath.stem for c in clips}
    assert names == set(DEMO_FEEDS)
    assert all(c.relpath.parts[0] == "_demo" for c in clips)
    assert next(c for c in clips if c.relpath.stem == "half-hour").seconds == 3
    assert next(c for c in clips if c.relpath.stem == "hour").seconds == DEMO_FEEDS["hour"]


def test_generate_demo_writes_clips_and_manifest(tmp_path: Path) -> None:
    from generate import DEMO_FEEDS, generate_demo

    lengths = dict.fromkeys(DEMO_FEEDS, 2)
    generate_demo(tmp_path, lengths)
    manifest = json.loads((tmp_path / "_demo" / "manifest.json").read_text())
    assert {c["feed"]: c["seconds"] for c in manifest["clips"]} == lengths
    info = probe(tmp_path / "_demo" / "movie.mp4")
    fmt = info["format"]
    assert isinstance(fmt, dict)
    assert float(fmt["duration"]) == pytest.approx(2, abs=0.3)


def test_cli_has_no_demo_scale_flag() -> None:
    # Shrunken demo feeds made unrealistic schedules (see server/tests/test_demo.py).
    import generate

    out = subprocess.run(
        [sys.executable, generate.__file__, "--help"], check=True, capture_output=True, text=True
    ).stdout
    assert "--demo-scale" not in out
