"""Run an ingest job: `python -m retro_cable.ingest plex|tmdb|scan|normalize [options]`.

  plex       import carried shows and movies from Plex (PLEX_URL, PLEX_TOKEN)
  tmdb       fill air dates and runtimes from TMDB (TMDB_TOKEN)
  scan       read Plex-named folders off the disk, without Plex (--tv DIR, --movies DIR;
             each may be given more than once: the rig adds the LiveTV downloads)
  acquire    add carried titles with no files to sonarr-livetv and radarr-livetv
             (RC_SONARR_LIVETV_URL/_KEY, RC_RADARR_LIVETV_URL/_KEY); see ingest/acquire.py
  normalize  encode playable MP4s into the media root (RC_MEDIA_DIR); see ingest/normalize.py
             (RC_DELETE_SOURCES_UNDER: delete originals there once their encode is whole)
  assemble   join the episodes of TV movies TheTVDB files as episodes (overrides.yaml
             `episodes_of`) into those movies; see ingest/assemble.py
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from retro_cable.admin import store
from retro_cable.catalog import Catalog, CatalogError, load_catalog
from retro_cable.catalog.overlay import apply_overlay
from retro_cable.db import connect, migrate
from retro_cable.ingest import manifest
from retro_cable.ingest.acquire import acquire
from retro_cable.ingest.assemble import AssembleStats, assemble
from retro_cable.ingest.gpu import choose_encoder, comfy_busy
from retro_cable.ingest.http import Request, json_request
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
from retro_cable.ingest.tmdb import TmdbClient, fill_gaps, lineup_ids
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
        "job",
        choices=[
            "plex",
            "tmdb",
            "scan",
            "acquire",
            "normalize",
            "assemble",
            "manifest",
            "index-media",
        ],
    )
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument(
        "--catalog", type=Path, default=Path(os.environ.get("RC_CATALOG_DIR", "catalog"))
    )
    scan = parser.add_argument_group("scan")
    scan.add_argument("--tv", type=Path, action="append",
                      help="root of a Plex-named TV tree; repeat: earlier wins")  # fmt: skip
    scan.add_argument("--movies", type=Path, action="append",
                      help="root of a Plex-named movie tree; repeat for more")  # fmt: skip
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
    norm.add_argument("--codec", choices=["h264", "hevc"],
                      default=os.environ.get("RC_CODEC", "h264"),
                      help="hevc: about 40%% smaller, for Safari/tvOS/Chrome (rig)")  # fmt: skip
    norm.add_argument("--order", choices=["catalog", "priority"],
                      default=os.environ.get("RC_ORDER", "catalog"),
                      help="priority: carried shows first, by earliest year")  # fmt: skip
    norm.add_argument("--priority-url", default=os.environ.get("RC_PRIORITY_URL", ""),
                      help="the mini's /api/admin/library (/admin channels)")  # fmt: skip
    norm.add_argument("--manifest", action="store_true",
                      help="write JSON sidecars and manifest.json (the rig)")  # fmt: skip
    acq = parser.add_argument_group("acquire")
    acq.add_argument("--livetv-tv", type=Path, default=None,
                     help="sonarr-livetv's /tv here: release finished downloads whose "
                          "imports are all encoded and deleted (the rig)")  # fmt: skip
    idx = parser.add_argument_group("index-media")
    idx.add_argument("--manifest-file", type=Path, default=None,
                     help="default <media dir>/manifest.json")  # fmt: skip
    idx.add_argument("--prune", action="store_true",
                     help="clear encodes the manifest no longer lists")  # fmt: skip
    idx.add_argument("--watch", type=float, default=None, metavar="SECONDS",
                     help="keep running: index-media re-indexes whenever the manifest"
                          " changes; tmdb fills again every SECONDS")  # fmt: skip
    norm.add_argument("--limit", type=int, default=None, help="encode at most N items")
    norm.add_argument("--for-minutes", type=float, default=None, metavar="N",
                      help="start no item after N minutes (the rig rescans then)")  # fmt: skip
    norm.add_argument("--max-seconds", type=float, default=None,
                      help="trials only: keep the first N seconds of each item")  # fmt: skip
    norm.add_argument("--force", action="store_true", help="re-encode up-to-date items")
    norm.add_argument("--crop", choices=["auto", "off"], default="auto",
                      help="auto: remove black bars baked into the picture (default)")  # fmt: skip
    norm.add_argument("--dry-run", action="store_true",
                      help="normalize: print the ffmpeg commands; acquire: add nothing, "
                           "print what would be added")  # fmt: skip
    norm.add_argument("--delete-sources-under", type=Path,
                      default=os.environ.get("RC_DELETE_SOURCES_UNDER") or None,
                      help="delete originals under this folder once their encode is whole "
                           "(default RC_DELETE_SOURCES_UNDER; unset: never)")  # fmt: skip
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
    needs_catalog = ("plex", "tmdb", "scan", "acquire", "assemble")
    if args.job in needs_catalog or (args.job == "normalize" and not args.all):
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
        assert catalog is not None
        return _tmdb(db, catalog, TmdbClient(tmdb_http_fetch(token)), args.watch)
    elif args.job == "scan":
        assert catalog is not None
        out: dict[str, object] = {}
        measure = None if args.no_probe else source_seconds
        if args.tv:
            out["tv"] = asdict(
                scan_tv(db, args.tv, catalog, now, carried_only=args.carried_only, probe=measure)
            )
        if args.movies:
            # The mini's /admin channels carry movies too (the channel builder's).
            found = scan_movies(db, args.movies, catalog, now, probe=measure,
                                extra=_mini_items(args))  # fmt: skip
            out["movies"] = asdict(found)
        stats = out
    elif args.job == "acquire":
        assert catalog is not None
        return _acquire(db, catalog, args)
    elif args.job == "assemble":
        assert catalog is not None
        stats = _assemble(db, catalog, args)
    elif args.job == "manifest":
        path = manifest.write_manifest(db, _media_dir(args), now)
        stats = {"manifest": str(path)}
    elif args.job == "index-media":
        return _index_media(db, args)
    else:
        shows = None
        if not args.all and catalog is not None:
            # What a channel carries here, plus the shows on channels made in the mini's /admin,
            # plus the series whose seasons they are or whose episodes make their movies
            # (overrides.yaml part_of, episodes_of).
            shows = carried_shows(db, catalog) | frozenset(
                priority_years(db, catalog, args.priority_url)
            )
        stats = _normalize(db, args, shows, catalog)
    print(json.dumps(stats, indent=2))
    return 0


def _tmdb(db: sqlite3.Connection, catalog: Catalog, source: TmdbClient, every: float | None) -> int:
    """Fill gaps from TMDB by the lineup's ids (the admin overlay's too, read again each
    time); with `every`, again every that many seconds, as new encodes arrive."""
    while True:
        lineup = lineup_ids(apply_overlay(catalog, store.head(db).overlay))
        stats = fill_gaps(db, source, datetime.now(UTC), lineup=lineup)
        print(json.dumps(asdict(stats), indent=2), flush=True)
        if every is None:
            return 0
        time.sleep(every)


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


def _arr(name: str) -> Request | None:
    """A client for sonarr-livetv or radarr-livetv from RC_<NAME>_LIVETV_URL and _KEY, or
    None when either is unset. The key goes only into the request header."""
    url = os.environ.get(f"RC_{name}_LIVETV_URL", "")
    key = os.environ.get(f"RC_{name}_LIVETV_KEY", "")
    # The twins answer slowly for hours while they import season packs (Nov 9 2026).
    timeout = float(os.environ.get("RC_ARR_TIMEOUT", "120"))
    return json_request(url, {"X-Api-Key": key}, timeout=timeout) if url and key else None


def _mini_items(args: argparse.Namespace) -> list[dict[str, object]]:
    """The mini's /api/admin/library items (--priority-url), or none if it can't be read."""
    if not args.priority_url:
        return []
    try:
        data = _get_json(args.priority_url)
    except (OSError, ValueError) as err:
        print(f"warning: can't read {args.priority_url}: {err}", file=sys.stderr)
        return []
    found = data.get("items", []) if isinstance(data, dict) else []
    return [i for i in found if isinstance(i, dict)]


def _acquire(db: sqlite3.Connection, catalog: Catalog, args: argparse.Namespace) -> int:
    # The mini's /admin channels count as carried.
    report = acquire(db, catalog, sonarr=_arr("SONARR"), radarr=_arr("RADARR"),
                     dry_run=args.dry_run, extra=_mini_items(args),
                     now=datetime.now(UTC), livetv_tv=args.livetv_tv)  # fmt: skip
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.ok else 1


def _assemble(db: sqlite3.Connection, catalog: Catalog, args: argparse.Namespace) -> object:
    media = _media_dir(args)
    rig = args.manifest and not args.dry_run

    def written(movie: str, target: str) -> None:
        try:
            manifest.write_sidecar(db, media, "movies", movie, target)
        except OSError as err:  # the manifest below still lists it
            print(f"warning: sidecar write failed: {err}", file=sys.stderr)

    result: AssembleStats = assemble(
        db, catalog, media, extra=_mini_items(args), dry_run=args.dry_run,
        on_assembled=written if rig else None,
    )  # fmt: skip
    if rig and result.assembled:
        manifest.write_manifest(db, media, datetime.now(UTC))
    for cmd in result.commands:
        print(" ".join(cmd), file=sys.stderr)
    stats = asdict(result)
    stats.pop("commands")
    return stats


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
    shows on channels made in the mini's /admin. A series some of whose seasons a carried
    show is (overrides.yaml part_of), or whose episodes a carried movie is made of
    (episodes_of), ranks with it. A failed fetch is a warning."""
    years: dict[str, int] = {}
    effective = None
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
    if effective is not None:
        for o in effective.overrides.shows:
            of = o.part_of or o.episodes_of
            if of is not None and o.show in years:
                years[of.show] = min(years.get(of.show, 9999), years[o.show])
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
        codec=args.codec,
        max_seconds=args.max_seconds,
        limit=args.limit,
        until=(
            datetime.now(UTC) + timedelta(minutes=args.for_minutes)
            if args.for_minutes is not None
            else None
        ),
        force=args.force,
        dry_run=args.dry_run,
        crop=detect_crop if args.crop == "auto" else no_crop,
        progress=lambda label: print(f"encoding {label}", file=sys.stderr, flush=True),
        shows=shows,
        order=order,
        on_encoded=hook,
        watch=watch,
        delete_sources_under=args.delete_sources_under,
        now=lambda: datetime.now(UTC),
        log=lambda message: print(message, file=sys.stderr, flush=True),
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
