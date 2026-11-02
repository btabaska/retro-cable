"""Generate fake media for tests and local dev: `make fixtures`.

Each clip shows its show, episode and a running timecode burned into the picture, so a
screenshot tells you what is playing and from which second. Frames are drawn with Pillow
and piped to ffmpeg, so ffmpeg needs no drawtext/freetype support.
"""

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

WIDTH, HEIGHT = 640, 480  # 480p, 4:3
DRAW_FPS = 10  # timecode resolution; output is resampled to OUT_FPS
OUT_FPS = 30

# (name, premiere year, background RGB, tone Hz)
SHOWS: list[tuple[str, int, tuple[int, int, int], int]] = [
    ("Fixture Friends", 1995, (28, 58, 122), 440),
    ("Test Pattern Toons", 1997, (122, 40, 28), 523),
    ("Sample Sitcom", 1999, (28, 104, 60), 659),
]
EPISODES_PER_SHOW = 3

# (channel id, label)
BUMPERS: list[tuple[str, str]] = [
    ("cartoon-network", "Bumper One"),
    ("nick", "Bumper Two"),
]


# Demo feeds: one long clip per runtime class, reused by every show in the demo database
# (`make demo`). The picture is color bars and a timecode; the app overlays what's airing.
DEMO_FEEDS: dict[str, int] = {
    "quarter-hour": 11 * 60,
    "half-hour": 22 * 60,
    "hour": 44 * 60,
    "movie": 90 * 60,
    "bumper": 10,
}
DEMO_DRAW_FPS = 2  # long clips: fewer drawn frames, still a keyframe every second
BARS = [(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192), (192, 0, 0),
        (0, 0, 192)]  # fmt: skip


@dataclass(frozen=True)
class Clip:
    kind: str  # "episode", "bumper" or "demo"
    show: str
    season: int
    episode: int
    seconds: int
    background: tuple[int, int, int]
    tone: int
    relpath: Path
    fps: int = DRAW_FPS

    def manifest_entry(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "show": self.show,
            "season": self.season,
            "episode": self.episode,
            "seconds": self.seconds,
            "path": self.relpath.as_posix(),
        }


def plan_clips(episode_seconds: int, bumper_seconds: int) -> list[Clip]:
    clips: list[Clip] = []
    for show, year, background, tone in SHOWS:
        for ep in range(1, EPISODES_PER_SHOW + 1):
            relpath = Path(f"{show} ({year})/Season 01/{show} - S01E{ep:02d}.mp4")
            clips.append(Clip("episode", show, 1, ep, episode_seconds, background, tone, relpath))
    for i, (channel, label) in enumerate(BUMPERS, start=1):
        relpath = Path(f"_bumpers/{channel}/fixture/{label.lower().replace(' ', '-')}.mp4")
        clips.append(Clip("bumper", label, 0, i, bumper_seconds, (20, 20, 20), 880, relpath))
    return clips


def plan_demo(lengths: dict[str, int] | None = None) -> list[Clip]:
    lengths = lengths or {}
    return [
        Clip("demo", name, 0, 0, lengths.get(name, seconds), (16, 16, 16), 330,
             Path("_demo") / f"{name}.mp4", DEMO_DRAW_FPS)
        for name, seconds in DEMO_FEEDS.items()
    ]  # fmt: skip


def timecode(frame: int, fps: int = DRAW_FPS) -> str:
    tenths = frame * 10 // fps
    seconds, tenth = divmod(tenths, 10)
    minutes, sec = divmod(seconds, 60)
    return f"{minutes:02d}:{sec:02d}.{tenth}"


def draw_demo_frame(clip: Clip, frame: int, fonts: dict[str, Font]) -> bytes:
    image = Image.new("RGB", (WIDTH, HEIGHT), clip.background)
    draw = ImageDraw.Draw(image)
    bar = WIDTH / len(BARS)
    for i, color in enumerate(BARS):
        draw.rectangle((round(i * bar), 0, round((i + 1) * bar), 300), fill=color)
    draw.text((WIDTH // 2, 340), f"RETRO CABLE DEMO FEED · {clip.show}", font=fonts["label"],
              fill="white", anchor="mm")  # fmt: skip
    seconds = frame // clip.fps
    hours, rest = divmod(seconds, 3600)
    stamp = f"{hours}:{rest // 60:02d}:{rest % 60:02d}"
    draw.text((WIDTH // 2, 410), stamp, font=fonts["timecode"], fill="#F2C230", anchor="mm")
    return image.tobytes()


def draw_frame(clip: Clip, frame: int, fonts: dict[str, Font]) -> bytes:
    if clip.kind == "demo":
        return draw_demo_frame(clip, frame, fonts)
    image = Image.new("RGB", (WIDTH, HEIGHT), clip.background)
    draw = ImageDraw.Draw(image)
    title = clip.show if clip.kind == "episode" else f"BUMPER · {clip.show}"
    label = f"S{clip.season:02d}E{clip.episode:02d}" if clip.kind == "episode" else "fixture"
    draw.text((WIDTH // 2, 120), title, font=fonts["title"], fill="white", anchor="mm")
    draw.text((WIDTH // 2, 190), label, font=fonts["label"], fill="white", anchor="mm")
    stamp = timecode(frame, clip.fps)
    draw.text((WIDTH // 2, 300), stamp, font=fonts["timecode"], fill="#F2C230", anchor="mm")
    progress = frame / max(1, clip.seconds * clip.fps)
    draw.rectangle((40, 420, 40 + int((WIDTH - 80) * progress), 440), fill="white")
    return image.tobytes()


def encode(clip: Clip, dest: Path) -> None:
    fonts: dict[str, Font] = {
        "title": ImageFont.load_default(size=40),
        "label": ImageFont.load_default(size=24 if clip.kind == "demo" else 32),
        "timecode": ImageFont.load_default(size=72),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.mp4")
    out_fps = OUT_FPS if clip.kind != "demo" else 10
    # Demo feeds run for hours of viewing, so their tone is kept very quiet.
    volume = "0.03" if clip.kind == "demo" else "1"
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(clip.fps), "-i", "-",
        "-f", "lavfi", "-i", f"sine=frequency={clip.tone}:sample_rate=48000",
        "-t", str(clip.seconds),
        "-vf", f"fps={out_fps}", "-af", f"volume={volume}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-g", str(out_fps),  # a keyframe every second so playback can start at any offset
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(tmp),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for frame in range(clip.seconds * clip.fps):
        proc.stdin.write(draw_frame(clip, frame, fonts))
    proc.stdin.close()
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed for {clip.relpath}")
    tmp.rename(dest)


def generate(out: Path, episode_seconds: int = 60, bumper_seconds: int = 5) -> list[Clip]:
    clips = plan_clips(episode_seconds, bumper_seconds)
    manifest_path = out / "manifest.json"
    previous: dict[str, int] = {}
    if manifest_path.exists():
        for entry in json.loads(manifest_path.read_text())["clips"]:
            previous[entry["path"]] = entry["seconds"]
    for clip in clips:
        dest = out / clip.relpath
        if dest.exists() and previous.get(clip.relpath.as_posix()) == clip.seconds:
            continue
        print(f"writing {clip.relpath}")
        encode(clip, dest)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"clips": [c.manifest_entry() for c in clips]}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return clips


def generate_demo(out: Path, lengths: dict[str, int] | None = None) -> list[Clip]:
    """Write the demo feeds into out/_demo/ with their own manifest (feed name -> seconds)."""
    clips = plan_demo(lengths)
    manifest_path = out / "_demo" / "manifest.json"
    previous: dict[str, int] = {}
    if manifest_path.exists():
        previous = {e["feed"]: e["seconds"] for e in json.loads(manifest_path.read_text())["clips"]}
    for clip in clips:
        dest = out / clip.relpath
        if dest.exists() and previous.get(clip.show) == clip.seconds:
            continue
        print(f"writing {clip.relpath} ({clip.seconds} s)")
        encode(clip, dest)
    entries = [{"feed": c.show, "seconds": c.seconds, "path": c.relpath.as_posix()} for c in clips]
    manifest_path.write_text(json.dumps({"clips": entries}, indent=2) + "\n")
    return clips


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(".fixtures"))
    parser.add_argument("--episode-seconds", type=int, default=60)
    parser.add_argument("--bumper-seconds", type=int, default=5)
    parser.add_argument(
        "--demo", action="store_true", help="also write the long demo feeds into _demo/"
    )
    parser.add_argument(
        "--demo-scale", type=float, default=1.0, help="shrink demo feed lengths (tests)"
    )
    args = parser.parse_args()
    generate(args.out, args.episode_seconds, args.bumper_seconds)
    if args.demo:
        lengths = {k: max(1, round(v * args.demo_scale)) for k, v in DEMO_FEEDS.items()}
        generate_demo(args.out, lengths)


if __name__ == "__main__":
    main()
