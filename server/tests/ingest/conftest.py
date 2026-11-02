import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from retro_cable.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class RecordedFetch:
    """Serves recorded JSON responses by path ('/library/sections' -> library_sections.json)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.missing: set[str] = set()

    def __call__(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        self.calls.append((path, dict(params or {})))
        name = path.strip("/").replace("/", "_") + ".json"
        if path in self.missing:
            raise FileNotFoundError(path)
        return json.loads((self.root / name).read_text())


@pytest.fixture
def plex_fetch() -> RecordedFetch:
    return RecordedFetch(FIXTURES / "plex")


@pytest.fixture
def tmdb_fetch() -> RecordedFetch:
    return RecordedFetch(FIXTURES / "tmdb")


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "retro-cable.db")
    migrate(conn)
    return conn


@pytest.fixture
def fake_library(db: sqlite3.Connection, tmp_path: Path) -> Path:
    """Three tiny stand-in sources for show 's' (1300 s runtimes), for normalize with a
    fake ffmpeg and ffprobe."""
    src = tmp_path / "src"
    db.execute("INSERT INTO shows (id, title) VALUES ('s', 'S')")
    for ep in (1, 2, 3):
        f = src / f"S01E0{ep}.mkv"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * ep)
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, runtime_seconds, file_path)"
            " VALUES ('s', 1, ?, 1300, ?)",
            (ep, str(f)),
        )
    return src
