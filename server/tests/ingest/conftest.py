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
