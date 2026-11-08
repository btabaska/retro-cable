"""GET /admin/station/runs: what the weekly station manager did, and why (M5).

Read-only. The newest run first, each with its signals summary and its changes; an applied
run that is still the latest /admin revision can be undone with the usual POST /admin/undo.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from retro_cable.admin import store
from retro_cable.db import connect, migrate
from retro_cable.station_manager import runs
from retro_cable.station_manager.manager import auto_apply
from retro_cable.station_manager.runs import StationRun


class StationRunRow(StationRun):
    can_undo: bool = Field(description="Applied and still the latest /admin revision")


class StationRuns(BaseModel):
    auto_apply: bool = Field(description="RC_STATION_AUTO_APPLY on this server")
    runs: list[StationRunRow]


def router(db_path: Path) -> APIRouter:
    api = APIRouter(prefix="/admin", tags=["admin"])
    migrated: set[tuple[int, int]] = set()

    @contextmanager
    def database() -> Iterator[sqlite3.Connection]:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = connect(db_path)
        try:
            st = db_path.stat()
            if (st.st_dev, st.st_ino) not in migrated:
                migrate(db)
                migrated.add((st.st_dev, st.st_ino))
            yield db
        finally:
            db.close()

    @api.get("/station/runs", name="adminStationRuns")
    def station_runs(
        limit: Annotated[int, Query(ge=1, le=100, description="Newest first")] = 20,
    ) -> StationRuns:
        """Recent station manager runs: applied, proposed, dry, skipped and failed."""
        with database() as db:
            head = store.head(db)
            found = runs.recent(db, limit)
        return StationRuns(
            auto_apply=auto_apply(),
            runs=[
                StationRunRow(
                    **r.model_dump(),
                    can_undo=r.status == "applied" and r.revision_id == head.id and head.can_undo,
                )
                for r in found
            ],
        )

    return api
