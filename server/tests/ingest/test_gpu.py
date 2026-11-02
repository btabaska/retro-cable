"""Picking an encoder per item: NVENC only while the shared GPU is idle, x264 otherwise."""

import subprocess
from collections.abc import Callable

import pytest

from retro_cable.ingest.gpu import GpuState, choose_encoder, comfy_busy, parse_nvidia_smi


def test_parse_nvidia_smi_reads_utilization_and_memory() -> None:
    assert parse_nvidia_smi("3, 11571, 24564\n") == GpuState(3, 11571, 24564)
    assert parse_nvidia_smi("[N/A], 100, 24564") is None
    assert parse_nvidia_smi("") is None


def smi(*readings: str) -> Callable[[], GpuState | None]:
    """A fake nvidia-smi that returns each reading in turn (the last one repeats)."""
    calls = list(readings)

    def read() -> GpuState | None:
        text = calls.pop(0) if len(calls) > 1 else calls[0]
        return parse_nvidia_smi(text)

    return read


def test_idle_gpu_with_room_gets_nvenc() -> None:
    pick = choose_encoder(read=smi("0, 11571, 24564"), queue_busy=lambda: False, pause=lambda: None)
    assert pick() == "nvenc"


@pytest.mark.parametrize(
    "readings",
    [
        ("0, 1000, 24564", "85, 1000, 24564", "0, 1000, 24564"),  # busy in one of the samples
        ("0, 24000, 24564",),  # an LLM fills the card: no room for an encoder session
        ("garbage",),  # nvidia-smi unreadable
    ],
)
def test_busy_full_or_unknown_gpu_falls_back_to_x264(readings: tuple[str, ...]) -> None:
    pick = choose_encoder(read=smi(*readings), queue_busy=lambda: False, pause=lambda: None)
    assert pick() == "x264"


def test_a_running_comfyui_queue_means_x264() -> None:
    pick = choose_encoder(read=smi("0, 1000, 24564"), queue_busy=lambda: True, pause=lambda: None)
    assert pick() == "x264"


def test_missing_nvidia_smi_means_x264() -> None:
    def read() -> GpuState | None:
        raise FileNotFoundError("nvidia-smi")

    assert choose_encoder(read=read, queue_busy=lambda: False, pause=lambda: None)() == "x264"


def test_comfy_busy_reads_the_queue_and_treats_errors_as_idle() -> None:
    assert comfy_busy("http://arbiter", lambda url: {"queue_running": [["x"]], "queue_pending": []})
    assert not comfy_busy("http://arbiter", lambda url: {"queue_running": [], "queue_pending": []})

    def down(url: str) -> object:
        raise OSError("connection refused")

    assert not comfy_busy("http://arbiter", down)
    assert not comfy_busy("", down), "no URL: nothing to ask"


def test_run_nvidia_smi_failure_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from retro_cable.ingest import gpu

    def fail(*args: object, **kwargs: object) -> object:
        raise subprocess.CalledProcessError(9, "nvidia-smi")

    monkeypatch.setattr(subprocess, "run", fail)
    assert gpu.read_nvidia_smi() is None
