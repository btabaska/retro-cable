"""The append-only history of admin overlays in SQLite (migration 0003)."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from retro_cable.catalog.overlay import Overlay, canonical, parse
from retro_cable.db import transaction


@dataclass(frozen=True)
class Revision:
    id: int  # 0 is the implicit empty overlay before any admin edit
    parent_id: int | None
    overlay: Overlay
    note: str
    created_at: str

    @property
    def can_undo(self) -> bool:
        return self.parent_id is not None


EMPTY = Revision(id=0, parent_id=None, overlay=Overlay(), note="", created_at="")


def _row(r: sqlite3.Row) -> Revision:
    return Revision(
        id=r["id"],
        parent_id=r["parent_id"],
        overlay=parse(r["overlay"]),
        note=r["note"],
        created_at=r["created_at"],
    )


def head(db: sqlite3.Connection) -> Revision:
    r = db.execute("SELECT * FROM catalog_revisions ORDER BY id DESC LIMIT 1").fetchone()
    return _row(r) if r else EMPTY


def get(db: sqlite3.Connection, revision_id: int) -> Revision:
    if revision_id == 0:
        return EMPTY
    r = db.execute("SELECT * FROM catalog_revisions WHERE id = ?", (revision_id,)).fetchone()
    if r is None:
        raise KeyError(revision_id)
    return _row(r)


def _insert(
    db: sqlite3.Connection, parent: int | None, overlay: Overlay, note: str, now: datetime
) -> Revision:
    cur = db.execute(
        "INSERT INTO catalog_revisions (created_at, parent_id, overlay, note) VALUES (?, ?, ?, ?)",
        (now.isoformat(), parent, canonical(overlay), note),
    )
    assert cur.lastrowid is not None
    return get(db, cur.lastrowid)


def save(db: sqlite3.Connection, overlay: Overlay, note: str, now: datetime) -> Revision:
    """Append `overlay` as the new head, its parent the current head."""
    with transaction(db):
        current = head(db)
        if current.id == 0:
            # Record the empty starting point, so the first save can be undone like any other.
            current = _insert(db, None, Overlay(), "start", now)
        return _insert(db, current.id, overlay, note, now)


class NothingToUndo(Exception):
    pass


def undo(db: sqlite3.Connection, now: datetime) -> tuple[Revision, Revision]:
    """Append a copy of the head's parent. Returns (undone head, new head)."""
    with transaction(db):
        current = head(db)
        if current.parent_id is None:
            raise NothingToUndo
        parent = get(db, current.parent_id)
        restored = _insert(db, parent.parent_id, parent.overlay, f"undo: {current.note}", now)
        return current, restored
