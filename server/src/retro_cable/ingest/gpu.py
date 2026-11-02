"""Share the rig's GPU politely: `normalize --encoder auto` asks before each item.

The rig's RTX card also runs Brandon's local models (llama-swap behind LiteLLM) and ComfyUI
(behind a gpu-arbiter on :8189, which only proxies ComfyUI and has no reservation API). So
before each encode the worker samples `nvidia-smi` a few times and checks ComfyUI's queue
through the arbiter; it uses NVENC only when every sample is idle, there is room for an
encoder session, and no image job is queued. Anything else, including an unreadable GPU,
means libx264 on the CPU (the worker runs at nice 19 / idle I/O class).

NVENC runs on the card's separate encoder block and needs a few hundred MiB, so an encode
that has started doesn't slow a model that loads later; it is not interrupted.
"""

import json
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from retro_cable.ingest.normalize import Encoder

QUERY = "utilization.gpu,memory.used,memory.total"
MAX_UTIL = 10  # percent, in every sample
MIN_FREE_MIB = 1536  # room for an NVENC session plus headroom
SAMPLES = 3
SAMPLE_GAP = 1.0  # seconds between samples


@dataclass(frozen=True)
class GpuState:
    util: int
    mem_used: int
    mem_total: int

    @property
    def free(self) -> int:
        return self.mem_total - self.mem_used


def parse_nvidia_smi(text: str) -> GpuState | None:
    """The first GPU from `--format=csv,noheader,nounits`, or None if it can't be read."""
    line = text.strip().splitlines()[0] if text.strip() else ""
    try:
        util, used, total = (int(x.strip()) for x in line.split(","))
    except ValueError:
        return None
    return GpuState(util, used, total)


def read_nvidia_smi(nvidia_smi: str = "nvidia-smi") -> GpuState | None:
    try:
        out = subprocess.run(
            [nvidia_smi, f"--query-gpu={QUERY}", "--format=csv,noheader,nounits"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return parse_nvidia_smi(out)


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def comfy_busy(base_url: str, get: Callable[[str], Any] = _get_json) -> bool:
    """True while ComfyUI (through the gpu-arbiter) has a job running or pending. An
    unreachable arbiter counts as idle: nvidia-smi still has the last word."""
    if not base_url:
        return False
    try:
        queue = get(base_url.rstrip("/") + "/queue")
    except (OSError, ValueError):
        return False
    return isinstance(queue, dict) and bool(
        queue.get("queue_running") or queue.get("queue_pending")
    )


def choose_encoder(
    *,
    read: Callable[[], GpuState | None] = read_nvidia_smi,
    queue_busy: Callable[[], bool] = lambda: False,
    pause: Callable[[], None] = lambda: time.sleep(SAMPLE_GAP),
    max_util: int = MAX_UTIL,
    min_free_mib: int = MIN_FREE_MIB,
    samples: int = SAMPLES,
) -> Callable[[], Encoder]:
    """A chooser for `normalize(encoder=...)`, asked once per item."""

    def idle() -> bool:
        for n in range(samples):
            if n:
                pause()
            try:
                state = read()
            except OSError:
                return False
            if state is None or state.util > max_util or state.free < min_free_mib:
                return False
        return not queue_busy()

    def pick() -> Encoder:
        return "nvenc" if idle() else "x264"

    return pick
