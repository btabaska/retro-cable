"""Run an ingest job: `python -m retro_cable.ingest plex|tmdb [--db PATH] [--catalog DIR]`.

Credentials come from the environment (PLEX_URL, PLEX_TOKEN, TMDB_TOKEN) and are never printed.
"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from retro_cable.catalog import CatalogError, load_catalog
from retro_cable.db import connect, migrate
from retro_cable.ingest.plex import PlexClient, import_plex
from retro_cable.ingest.plex import http_fetch as plex_http_fetch
from retro_cable.ingest.tmdb import TmdbClient, fill_gaps
from retro_cable.ingest.tmdb import http_fetch as tmdb_http_fetch


def default_db() -> Path:
    return Path(os.environ.get("RC_DATA_DIR", "data")) / "retro-cable.db"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m retro_cable.ingest")
    parser.add_argument("job", choices=["plex", "tmdb"])
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--catalog", type=Path, default=Path("catalog"))
    args = parser.parse_args(argv)
    now = datetime.now(UTC)  # the worker stamps runs with wall time; the scheduler never does

    url = os.environ.get("PLEX_URL", "")
    token = os.environ.get("PLEX_TOKEN" if args.job == "plex" else "TMDB_TOKEN", "")
    if args.job == "plex":
        if not url or not token:
            print("set PLEX_URL and PLEX_TOKEN", file=sys.stderr)
            return 2
        try:
            catalog = load_catalog(args.catalog)
        except CatalogError as err:
            print(err, file=sys.stderr)
            return 1
    elif not token:
        print("set TMDB_TOKEN (a TMDB v4 read access token)", file=sys.stderr)
        return 2

    db_path = args.db or default_db()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(db_path)
    migrate(db)
    if args.job == "plex":
        stats: object = import_plex(db, PlexClient(plex_http_fetch(url, token)), catalog, now)
    else:
        stats = fill_gaps(db, TmdbClient(tmdb_http_fetch(token)), now)
    print(json.dumps(asdict(stats), indent=2))  # type: ignore[call-overload]
    return 0
