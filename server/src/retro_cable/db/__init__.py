"""SQLite access and schema migrations (numbered .sql files in db/migrations/)."""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def _load_migrations() -> list[Migration]:
    found = []
    for entry in files("retro_cable.db").joinpath("migrations").iterdir():
        match = re.fullmatch(r"(\d{4})_(\w+)\.sql", entry.name)
        if match:
            found.append(Migration(int(match[1]), match[2], entry.read_text()))
    return sorted(found, key=lambda m: m.version)


MIGRATIONS = _load_migrations()


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database in autocommit mode; callers group writes with `with transaction(db)`."""
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db


@contextmanager
def transaction(db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run the block in one transaction: commit on success, roll back on any exception."""
    db.execute("BEGIN")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def migrate(db: sqlite3.Connection, migrations: list[Migration] = MIGRATIONS) -> list[int]:
    """Apply pending migrations in order, each atomically. Returns the versions applied."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    done = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
    applied = []
    for m in migrations:
        if m.version in done:
            continue
        try:
            db.executescript(
                f"BEGIN;\n{m.sql}\n;"
                f"INSERT INTO schema_migrations (version, name) VALUES ({m.version}, '{m.name}');"
                "\nCOMMIT;"
            )
        except sqlite3.Error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        applied.append(m.version)
    return applied
