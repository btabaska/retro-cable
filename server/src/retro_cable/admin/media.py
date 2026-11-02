"""GET /admin/media/progress: how far the rig's library conversion has got (the Media tab).

Read-only. It reads the `progress.json` the rig's worker keeps at the media root
(ingest/progress.py) through the mini's read-only mount; 404 until there is one.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from retro_cable.ingest.progress import read_progress


class MediaShowProgress(BaseModel):
    title: str
    done: int
    total: int
    failed: int = Field(description="Failed in the current or last pass; retried on the next")
    state: Literal["converting", "queued", "done"]


class MediaProgress(BaseModel):
    done: int
    total: int
    failed: int
    encoder: Literal["gpu", "cpu", "paused"] = Field(
        description="paused: the worker is between passes or hasn't reported for 30 minutes"
    )
    gpu_busy: bool = Field(description="The CPU was chosen because the GPU was in use")
    recent_seconds: list[float] = Field(
        description="Wall-clock seconds of the most recent finished files, newest last (<= 20)"
    )
    shows: list[MediaShowProgress] = Field(description="In conversion order")


def router(media_dir: Path, clock: Callable[[], datetime]) -> APIRouter:
    api = APIRouter(prefix="/admin", tags=["admin"])

    @api.get(
        "/media/progress",
        name="adminMediaProgress",
        responses={404: {"description": "The rig hasn't reported any progress"}},
    )
    def media_progress() -> MediaProgress:
        found = read_progress(media_dir, clock())
        if found is None:
            raise HTTPException(404, "no conversion progress yet")
        return MediaProgress.model_validate(found)

    return api
