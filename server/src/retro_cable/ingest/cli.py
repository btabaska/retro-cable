"""Run an ingest job: `python -m retro_cable.ingest plex|tmdb|scan|normalize [options]`.

  plex       import carried shows and movies from Plex (PLEX_URL, PLEX_TOKEN)
  tmdb       fill air dates and runtimes from TMDB (TMDB_TOKEN)
  scan       read Plex-named folders off the disk, without Plex (--tv DIR, --movies DIR)
  normalize  encode playable MP4s into the media root (RC_MEDIA_DIR); see ingest/normalize.py

Credentials come from the environment and are never printed.
"""

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from retro_cable.catalog import CatalogError, load_catalog
from retro_cable.db import connect, migrate
from retro_cable.ingest.normalize import ENCODERS, detect_crop, no_crop, normalize
from retro_cable.ingest.plex import PlexClient, import_plex
from retro_cable.ingest.plex import http_fetch as plex_http_fetch
from retro_cable.ingest.scan import scan_movies, scan_tv
from retro_cable.ingest.tmdb import TmdbClient, fill_gaps
from retro_cable.ingest.tmdb import http_fetch as tmdb_http_fetch


def default_db() -> Path:
    return Path(os.environ.get("RC_DATA_DIR", "data")) / "retro-cable.db"


def source_maps(values: list[str]) -> list[tuple[str, str]]:
    """`FROM=TO` pairs from --source-map flags, or RC_SOURCE_MAP (comma-separated)."""
    if not values:
        values = [v for v in os.environ.get("RC_SOURCE_MAP", "").split(",") if v.strip()]
    pairs = []
    for v in values:
        src, sep, dst = v.partition("=")
        if not sep or not src or not dst:
            raise SystemExit(f"bad source map {v!r}: use FROM=TO")
        pairs.append((src.strip(), dst.strip()))
    return pairs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m retro_cable.ingest")
    parser.add_argument("job", choices=["plex", "tmdb", "scan", "normalize"])
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument(
        "--catalog", type=Path, default=Path(os.environ.get("RC_CATALOG_DIR", "catalog"))
    )
    scan = parser.add_argument_group("scan")
    scan.add_argument("--tv", type=Path, help="root of a Plex-named TV tree")
    scan.add_argument("--movies", type=Path, help="root of a Plex-named movie tree")
    norm = parser.add_argument_group("normalize")
    norm.add_argument("--media-dir", type=Path, default=None, help="default RC_MEDIA_DIR")
    norm.add_argument("--source-root", type=Path, default=None,
                      help="where relative source paths live (default RC_SOURCE_ROOT)")  # fmt: skip
    norm.add_argument("--source-map", action="append", default=[], metavar="FROM=TO",
                      help="rewrite a source path prefix, e.g. /data/tv=/volume3/tv")  # fmt: skip
    norm.add_argument("--encoder", choices=sorted(ENCODERS),
                      default=os.environ.get("RC_ENCODER", "x264"))  # fmt: skip
    norm.add_argument("--limit", type=int, default=None, help="encode at most N items")
    norm.add_argument("--max-seconds", type=float, default=None,
                      help="trials only: keep the first N seconds of each item")  # fmt: skip
    norm.add_argument("--force", action="store_true", help="re-encode up-to-date items")
    norm.add_argument("--crop", choices=["auto", "off"], default="auto",
                      help="auto: remove black bars baked into the picture (default)")  # fmt: skip
    norm.add_argument("--dry-run", action="store_true", help="print the ffmpeg commands")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    now = datetime.now(UTC)  # the worker stamps runs with wall time; the scheduler never does

    token = ""
    catalog = None
    if args.job == "plex":
        url, token = os.environ.get("PLEX_URL", ""), os.environ.get("PLEX_TOKEN", "")
        if not url or not token:
            print("set PLEX_URL and PLEX_TOKEN", file=sys.stderr)
            return 2
    elif args.job == "tmdb":
        token = os.environ.get("TMDB_TOKEN", "")
        if not token:
            print("set TMDB_TOKEN (a TMDB v4 read access token)", file=sys.stderr)
            return 2
    elif args.job == "scan" and not (args.tv or args.movies):
        print("scan needs --tv DIR and/or --movies DIR", file=sys.stderr)
        return 2
    if args.job in ("plex", "scan"):
        try:
            catalog = load_catalog(args.catalog)
        except CatalogError as err:
            print(err, file=sys.stderr)
            return 1

    db_path = args.db or default_db()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(db_path)
    migrate(db)
    stats: object
    if args.job == "plex":
        assert catalog is not None
        fetch = plex_http_fetch(os.environ["PLEX_URL"], token)
        stats = asdict(import_plex(db, PlexClient(fetch), catalog, now))
    elif args.job == "tmdb":
        stats = asdict(fill_gaps(db, TmdbClient(tmdb_http_fetch(token)), now))
    elif args.job == "scan":
        assert catalog is not None
        out: dict[str, object] = {}
        if args.tv:
            out["tv"] = asdict(scan_tv(db, args.tv, catalog, now))
        if args.movies:
            out["movies"] = asdict(scan_movies(db, args.movies, catalog, now))
        stats = out
    else:
        stats = _normalize(db, args)
    print(json.dumps(stats, indent=2))
    return 0


def _normalize(db: sqlite3.Connection, args: argparse.Namespace) -> dict[str, object]:

    media = args.media_dir or Path(os.environ.get("RC_MEDIA_DIR", "data/media"))
    root = args.source_root or (
        Path(os.environ["RC_SOURCE_ROOT"]) if os.environ.get("RC_SOURCE_ROOT") else None
    )
    result = normalize(
        db,
        media,
        source_root=root,
        source_map=source_maps(args.source_map),
        encoder=ENCODERS[args.encoder],
        max_seconds=args.max_seconds,
        limit=args.limit,
        force=args.force,
        dry_run=args.dry_run,
        crop=detect_crop if args.crop == "auto" else no_crop,
        progress=lambda label: print(f"encoding {label}", file=sys.stderr, flush=True),
    )
    if args.dry_run:
        for cmd in result.commands:
            print(" ".join(cmd), file=sys.stderr)
    stats = asdict(result)
    stats.pop("commands")
    return stats
