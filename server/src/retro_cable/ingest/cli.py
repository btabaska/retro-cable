"""Run an ingest job: `python -m retro_cable.ingest plex|tmdb|scan|normalize [options]`.

  plex       import carried shows and movies from Plex (PLEX_URL, PLEX_TOKEN)
  tmdb       fill air dates and runtimes from TMDB (TMDB_TOKEN)
  scan       read Plex-named folders off the disk, without Plex (--tv DIR, --movies DIR)
  normalize  encode playable MP4s into the media root (RC_MEDIA_DIR); see ingest/normalize.py
  manifest   write manifest.json at the media root from this database (ingest/manifest.py)
  index-media  record a manifest's encodes in this database, without probing (the mini)

Credentials come from the environment and are never printed.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from retro_cable.admin import store
from retro_cable.catalog import Catalog, CatalogError, load_catalog
from retro_cable.catalog.overlay import apply_overlay
from retro_cable.db import connect, migrate
from retro_cable.ingest import manifest
from retro_cable.ingest.gpu import choose_encoder, comfy_busy
from retro_cable.ingest.normalize import (
    ENCODERS,
    Item,
    detect_crop,
    no_crop,
    normalize,
    prioritize,
    source_seconds,
)
from retro_cable.ingest.plex import PlexClient, import_plex
from retro_cable.ingest.plex import http_fetch as plex_http_fetch
from retro_cable.ingest.progress import PROGRESS, ProgressFile
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
    parser.add_argument(
        "job", choices=["plex", "tmdb", "scan", "normalize", "manifest", "index-media"]
    )
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
    norm.add_argument("--encoder", choices=[*sorted(ENCODERS), "auto"],
                      default=os.environ.get("RC_ENCODER", "x264"),
                      help="auto: NVENC while the GPU is idle, else x264")  # fmt: skip
    norm.add_argument("--gpu-queue-url", default=os.environ.get("RC_GPU_QUEUE_URL", ""),
                      help="ComfyUI or its gpu-arbiter, for queued jobs")  # fmt: skip
    norm.add_argument("--order", choices=["catalog", "priority"],
                      default=os.environ.get("RC_ORDER", "catalog"),
                      help="priority: carried shows first, by earliest year")  # fmt: skip
    norm.add_argument("--priority-url", default=os.environ.get("RC_PRIORITY_URL", ""),
                      help="the mini's /api/admin/library (/admin channels)")  # fmt: skip
    norm.add_argument("--manifest", action="store_true",
                      help="write JSON sidecars and manifest.json (the rig)")  # fmt: skip
    idx = parser.add_argument_group("index-media")
    idx.add_argument("--manifest-file", type=Path, default=None,
                     help="default <media dir>/manifest.json")  # fmt: skip
    idx.add_argument("--prune", action="store_true",
                     help="clear encodes the manifest no longer lists")  # fmt: skip
    idx.add_argument("--watch", type=float, default=None, metavar="SECONDS",
                     help="keep running: re-index whenever the manifest changes")  # fmt: skip
    norm.add_argument("--limit", type=int, default=None, help="encode at most N items")
    norm.add_argument("--max-seconds", type=float, default=None,
                      help="trials only: keep the first N seconds of each item")  # fmt: skip
    norm.add_argument("--force", action="store_true", help="re-encode up-to-date items")
    norm.add_argument("--crop", choices=["auto", "off"], default="auto",
                      help="auto: remove black bars baked into the picture (default)")  # fmt: skip
    norm.add_argument("--dry-run", action="store_true", help="print the ffmpeg commands")
    norm.add_argument("--all", action="store_true",
                      help="also what no channel carries (default: carried only, with "
                           "/admin channels; --priority-url adds the mini's)")  # fmt: skip
    scan.add_argument("--carried-only", action="store_true",
                      help="skip show folders no channel carries (default: keep)")  # fmt: skip
    scan.add_argument("--no-probe", action="store_true",
                      help="don't measure new or changed files with ffprobe")  # fmt: skip
    return parser


def carried_shows(db: sqlite3.Connection, catalog: Catalog) -> frozenset[str]:
    """Shows some channel carries: the YAML catalog plus the /admin overlay's channels."""
    effective = apply_overlay(catalog, store.head(db).overlay)
    return frozenset(c.show for c in effective.carriage.carriage)


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
    if args.job in ("plex", "scan") or (args.job == "normalize" and not args.all):
        try:
            catalog = load_catalog(args.catalog)
        except CatalogError as err:
            print(err, file=sys.stderr)
            return 1
    elif args.job == "normalize" and args.order == "priority":
        try:  # --all: the catalog only ranks what goes first
            catalog = load_catalog(args.catalog)
        except CatalogError as err:
            print(f"warning: no catalog for --order priority: {err}", file=sys.stderr)

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
        measure = None if args.no_probe else source_seconds
        if args.tv:
            out["tv"] = asdict(
                scan_tv(db, args.tv, catalog, now, carried_only=args.carried_only, probe=measure)
            )
        if args.movies:
            out["movies"] = asdict(scan_movies(db, args.movies, catalog, now, probe=measure))
        stats = out
    elif args.job == "manifest":
        path = manifest.write_manifest(db, _media_dir(args), now)
        stats = {"manifest": str(path)}
    elif args.job == "index-media":
        return _index_media(db, args)
    else:
        shows = None
        if not args.all and catalog is not None:
            # What a channel carries here, plus the shows on channels made in the mini's /admin.
            shows = carried_shows(db, catalog) | frozenset(
                priority_years(db, None, args.priority_url)
            )
        stats = _normalize(db, args, shows, catalog)
    print(json.dumps(stats, indent=2))
    return 0


def _media_dir(args: argparse.Namespace) -> Path:
    return Path(args.media_dir or os.environ.get("RC_MEDIA_DIR", "data/media"))


def _index_media(db: sqlite3.Connection, args: argparse.Namespace) -> int:
    media = _media_dir(args)
    if args.watch is not None:
        manifest.watch(db, media, interval=args.watch, now=lambda: datetime.now(UTC),
                       prune=args.prune)  # fmt: skip
        return 0
    path = args.manifest_file or media / manifest.MANIFEST
    try:
        data = manifest.load_manifest(path)
    except manifest.ManifestError as err:
        print(err, file=sys.stderr)
        return 1
    stats = manifest.index_media(db, data, media, datetime.now(UTC), prune=args.prune)
    print(json.dumps(asdict(stats), indent=2))
    return 0


def _get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.load(response)


def priority_years(
    db: sqlite3.Connection,
    catalog: Catalog | None,
    url: str = "",
    fetch: Callable[[str], object] | None = None,
) -> dict[str, int]:
    """Show and movie id -> the earliest year a channel carries it: the YAML catalog plus
    this database's /admin overlay, plus (with `url`, the mini's /api/admin/library) the
    shows on channels made in the mini's /admin. A failed fetch is a warning."""
    years: dict[str, int] = {}
    if catalog is not None:
        effective = apply_overlay(catalog, store.head(db).overlay)
        for c in effective.carriage.carriage:
            years[c.show] = min(years.get(c.show, 9999), c.from_.year)
    if url:
        try:
            data = (fetch or _get_json)(url)
            items = data.get("items", []) if isinstance(data, dict) else []
        except (OSError, ValueError) as err:
            print(f"warning: can't read {url} for priorities: {err}", file=sys.stderr)
            items = []
        for item in items:
            if isinstance(item, dict) and item.get("channels") and item.get("id"):
                years.setdefault(str(item["id"]), int(item.get("first_year") or 9999))
    return years


def _rig_hooks(
    db: sqlite3.Connection, args: argparse.Namespace, media: Path
) -> Callable[[Item], None]:
    """After each encode: the item's sidecar, and manifest.json at most every few minutes."""
    every = float(os.environ.get("RC_MANIFEST_EVERY", "300"))
    last = [time.monotonic()]

    def after(item: Item) -> None:
        try:
            manifest.write_sidecar(db, media, item.table, item.rowid, item.target)
            if time.monotonic() - last[0] >= every:
                manifest.write_manifest(db, media, datetime.now(UTC))
                last[0] = time.monotonic()
        except OSError as err:  # an SMB blip: the next item or the final write catches up
            print(f"warning: manifest write failed: {err}", file=sys.stderr)

    return after


def _normalize(
    db: sqlite3.Connection,
    args: argparse.Namespace,
    shows: frozenset[str] | None,
    catalog: Catalog | None = None,
) -> dict[str, object]:
    media = _media_dir(args)
    queue_url = args.gpu_queue_url
    encoder = (
        choose_encoder(queue_busy=lambda: comfy_busy(queue_url))
        if args.encoder == "auto"
        else ENCODERS[args.encoder]
    )
    order = None
    if args.order == "priority":
        carried = priority_years(db, catalog, args.priority_url)
        years = {r["id"]: r["year"] for r in db.execute("SELECT id, year FROM shows")} | {
            r["id"]: r["year"] for r in db.execute("SELECT id, year FROM movies")
        }

        def order(items: list[Item]) -> list[Item]:
            return prioritize(items, carried, years)

    hook = _rig_hooks(db, args, media) if args.manifest and not args.dry_run else None
    # The rig also keeps progress.json for the mini's /admin Media tab (ingest/progress.py).
    watch = None
    if hook is not None:
        titles = {r["id"]: r["title"] for r in db.execute("SELECT id, title FROM shows")}
        watch = ProgressFile(media / PROGRESS, titles, auto=args.encoder == "auto")
    root = args.source_root or (
        Path(os.environ["RC_SOURCE_ROOT"]) if os.environ.get("RC_SOURCE_ROOT") else None
    )
    result = normalize(
        db,
        media,
        source_root=root,
        source_map=source_maps(args.source_map),
        encoder=encoder,
        max_seconds=args.max_seconds,
        limit=args.limit,
        force=args.force,
        dry_run=args.dry_run,
        crop=detect_crop if args.crop == "auto" else no_crop,
        progress=lambda label: print(f"encoding {label}", file=sys.stderr, flush=True),
        shows=shows,
        order=order,
        on_encoded=hook,
        watch=watch,
    )
    if hook is not None:
        manifest.write_manifest(db, media, datetime.now(UTC))
    if args.dry_run:
        for cmd in result.commands:
            print(" ".join(cmd), file=sys.stderr)
    for label in result.short:
        print(
            f"warning: {label} is shorter than half its runtime (a sample or truncated file);"
            " it won't be scheduled unless RC_ALLOW_SHORT_MEDIA=1",
            file=sys.stderr,
        )
    stats = asdict(result)
    stats.pop("commands")
    return stats
