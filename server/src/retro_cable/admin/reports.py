"""GET /admin/reports: the Sunday reports, newest first (Phase 8, the Report tab).

Read-only. Each is what `python -m retro_cable.report run` saved: viewing, the station
manager's run, what is on the air, the conversion and the backups, plus the headline it
pushed.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from retro_cable import report
from retro_cable.db import connect, migrate
from retro_cable.report import StoredReport


class Reports(BaseModel):
    schedule: str = Field(description="When the report runs, in words")
    reports: list[StoredReport]


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

    @api.get("/reports", name="adminReports")
    def reports(
        limit: Annotated[int, Query(ge=1, le=52, description="Newest first")] = 8,
    ) -> Reports:
        """The latest Sunday reports."""
        with database() as db:
            return Reports(schedule=report.SCHEDULE, reports=report.recent(db, limit))

    return api
