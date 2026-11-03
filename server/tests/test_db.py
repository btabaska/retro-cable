import sqlite3
import threading
from pathlib import Path

import pytest

from retro_cable.db import MIGRATIONS, connect, migrate


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "retro-cable.db")


def tables(db: sqlite3.Connection) -> set[str]:
    rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def test_migrate_creates_the_catalog_tables(db: sqlite3.Connection) -> None:
    applied = migrate(db)
    assert applied == [m.version for m in MIGRATIONS]
    assert {"schema_migrations", "shows", "episodes", "movies", "import_runs"} <= tables(db)


def test_migrate_is_idempotent(db: sqlite3.Connection) -> None:
    migrate(db)
    assert migrate(db) == []
    versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [m.version for m in MIGRATIONS]


def test_migrations_are_numbered_in_order() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))


def test_foreign_keys_are_enforced(db: sqlite3.Connection) -> None:
    migrate(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, title) VALUES ('nope', 1, 1, 'x')"
        )


def test_an_episode_is_unique_per_show_season_and_number(db: sqlite3.Connection) -> None:
    migrate(db)
    db.execute("INSERT INTO shows (id, title) VALUES ('s', 'S')")
    db.execute("INSERT INTO episodes (show_id, season, episode, title) VALUES ('s', 1, 1, 'a')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO episodes (show_id, season, episode, title) VALUES ('s', 1, 1, 'b')")


def test_a_failed_migration_leaves_no_partial_state(tmp_path: Path) -> None:
    from retro_cable.db import Migration

    db = connect(tmp_path / "x.db")
    broken = [
        Migration(1, "ok", "CREATE TABLE a (x)"),
        Migration(2, "bad", "CREATE TABLE b (; nope"),
    ]
    with pytest.raises(sqlite3.OperationalError):
        migrate(db, broken)
    assert "b" not in tables(db)
    assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [1]


def test_transaction_commits_or_rolls_back(db: sqlite3.Connection) -> None:
    from retro_cable.db import transaction

    migrate(db)
    with transaction(db):
        db.execute("INSERT INTO shows (id, title) VALUES ('a', 'A')")
    with pytest.raises(RuntimeError), transaction(db):
        db.execute("INSERT INTO shows (id, title) VALUES ('b', 'B')")
        raise RuntimeError("boom")
    assert [r[0] for r in db.execute("SELECT id FROM shows")] == ["a"]


def test_two_processes_migrating_at_once_both_succeed(tmp_path: Path) -> None:
    # Right after a deploy the api and media-index can both open the database and migrate
    # it. Each pending migration must apply once; the loser must see it done, not re-run it
    # ("duplicate column name") and fail its caller.
    path = tmp_path / "rc.db"

    def run(start: threading.Barrier, errors: list[sqlite3.Error]) -> None:
        db = connect(path)
        start.wait()
        try:
            migrate(db)
        except sqlite3.Error as e:
            errors.append(e)
        finally:
            db.close()

    for trial in range(20):
        path.unlink(missing_ok=True)
        old = connect(path)
        migrate(old, MIGRATIONS[:3])
        old.close()
        start = threading.Barrier(2)
        errors: list[sqlite3.Error] = []
        threads = [threading.Thread(target=run, args=(start, errors)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], f"trial {trial}"
        db = connect(path)
        done = [r[0] for r in db.execute("SELECT version FROM schema_migrations")]
        db.close()
        assert done == [m.version for m in MIGRATIONS]
