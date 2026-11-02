"""The media manifest: what the rig encoded, readable by the mini without probing any file.

The rig's worker (ops/rig/) scans the NAS, encodes into the media root and, after each item,
writes a JSON sidecar next to the MP4 (`tv/<show>/s01/<show>-s01e01.json`); every few minutes
and at the end of a run it rewrites `manifest.json` at the media root. Both are written to a
temporary name and renamed, so a reader sees the old file or the new one, never half of one.

The mini serves the same folder read-only. `ingest index-media` reads `manifest.json` (one
file over SMB, instead of ffprobing 400 GB) and fills its own database: the shows and
episodes the rig found, their source lengths, and each encode's `media_path` and
`media_seconds`. Metadata the mini already has (a Plex or TMDB title, air date or runtime)
wins; the manifest only fills what is missing and keeps the file facts current. An unchanged
manifest writes nothing, so the api's schedule cache stays warm.
"""

import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from retro_cable.db import transaction

FORMAT = "retro-cable-media/1"
MANIFEST = "manifest.json"

SHOW_FIELDS = ("id", "title", "year")
MEDIA_FIELDS = ("media_path", "media_seconds", "media_source_size", "media_source_mtime_ns")
EPISODE_FIELDS = (
    "show_id", "season", "episode", "title", "air_date", "air_date_source", "runtime_seconds",
    "file_path", "file_size", "source_seconds", *MEDIA_FIELDS,
)  # fmt: skip
MOVIE_FIELDS = (
    "id", "title", "year", "premiere_date", "runtime_seconds", "file_path", "file_size",
    "source_seconds", *MEDIA_FIELDS,
)  # fmt: skip
BUMPER_FIELDS = ("channel_id", "file_path", "runtime_seconds", *MEDIA_FIELDS)

Manifest = dict[str, Any]


class ManifestError(Exception):
    """The manifest is missing, half written or in a format this code doesn't read."""


def write_json_atomic(path: Path, data: object) -> None:
    """Write JSON to a temporary file in the same folder, flush it, then rename it over
    `path` (atomic on a local disk and on SMB 3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def sidecar_path(media_dir: Path, media_path: str) -> Path:
    return (media_dir / media_path).with_suffix(".json")


def _rows(db: sqlite3.Connection, sql: str, fields: Iterable[str]) -> list[dict[str, Any]]:
    names = list(fields)
    return [{k: r[k] for k in names} for r in db.execute(sql)]


def _episodes_sql(where: str = "") -> str:
    return (
        f"SELECT {', '.join(EPISODE_FIELDS)} FROM episodes"
        f" WHERE (file_path IS NOT NULL OR media_path IS NOT NULL){where}"
        " ORDER BY show_id, season, episode"
    )


def build_manifest(db: sqlite3.Connection, now: datetime) -> Manifest:
    """Every show, episode, movie and bumper the rig knows a file for, with its encode."""
    return {
        "format": FORMAT,
        "generated_at": now.isoformat(),
        "shows": _rows(
            db,
            "SELECT id, title, year FROM shows WHERE id IN (SELECT show_id FROM episodes"
            " WHERE file_path IS NOT NULL OR media_path IS NOT NULL) ORDER BY id",
            SHOW_FIELDS,
        ),
        "episodes": _rows(db, _episodes_sql(), EPISODE_FIELDS),
        "movies": _rows(
            db,
            f"SELECT {', '.join(MOVIE_FIELDS)} FROM movies"
            " WHERE file_path IS NOT NULL OR media_path IS NOT NULL ORDER BY id",
            MOVIE_FIELDS,
        ),
        "bumpers": _rows(
            db,
            f"SELECT {', '.join(BUMPER_FIELDS)} FROM bumpers ORDER BY channel_id, file_path",
            BUMPER_FIELDS,
        ),
    }


def write_manifest(db: sqlite3.Connection, media_dir: Path, now: datetime) -> Path:
    path = media_dir / MANIFEST
    write_json_atomic(path, build_manifest(db, now))
    return path


def sidecar(db: sqlite3.Connection, table: str, rowid: int | str) -> dict[str, Any] | None:
    """One item's manifest entry, for the JSON next to its MP4."""
    if table == "episodes":
        rows = _rows(db, _episodes_sql(f" AND id = {int(rowid)}"), EPISODE_FIELDS)
    elif table == "movies":
        cols = ", ".join(MOVIE_FIELDS)
        rows = [
            {k: r[k] for k in MOVIE_FIELDS}
            for r in db.execute(f"SELECT {cols} FROM movies WHERE id = ?", (rowid,))
        ]
    else:
        cols = ", ".join(BUMPER_FIELDS)
        rows = [
            {k: r[k] for k in BUMPER_FIELDS}
            for r in db.execute(f"SELECT {cols} FROM bumpers WHERE id = ?", (rowid,))
        ]
    if not rows:
        return None
    return {"format": FORMAT, "kind": table.removesuffix("s"), **rows[0]}


def write_sidecar(
    db: sqlite3.Connection, media_dir: Path, table: str, rowid: int | str, target: str
) -> None:
    entry = sidecar(db, table, rowid)
    if entry is not None:
        write_json_atomic(sidecar_path(media_dir, target), entry)


def load_manifest(path: Path) -> Manifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ManifestError(f"{path}: {err}") from err
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ManifestError(f"{path}: not a {FORMAT} manifest")
    return data


# --- the mini's side ---------------------------------------------------------------------


def safe_media_path(value: object) -> bool:
    """A relative `.mp4` path that stays inside the media root."""
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    p = PurePosixPath(value)
    return not p.is_absolute() and ".." not in p.parts and p.suffix == ".mp4"


@dataclass
class IndexStats:
    shows: int = 0  # added
    episodes: int = 0  # added
    movies: int = 0  # added
    bumpers: int = 0  # added
    updated: int = 0  # existing rows whose file facts changed
    media: int = 0  # rows that gained or changed an encode
    pruned: int = 0
    missing_files: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.shows + self.episodes + self.movies + self.bumpers + self.updated + self.pruned


def _clean_media(
    entry: dict[str, Any],
    existing: sqlite3.Row | None,
    media_dir: Path | None,
    stats: IndexStats,
) -> dict[str, Any]:
    """The entry's media fields, or all None when the path is unsafe or (for a new or
    changed encode) the file isn't in the media root."""
    media = {k: entry.get(k) for k in MEDIA_FIELDS}
    path = media["media_path"]
    if path is None:
        return media
    none = dict.fromkeys(MEDIA_FIELDS)
    if not safe_media_path(path):
        stats.rejected.append(str(path))
        return none
    seconds = media["media_seconds"]
    if not isinstance(seconds, int) or seconds <= 0:
        stats.rejected.append(str(path))
        return none
    known = existing is not None and all(existing[k] == media[k] for k in MEDIA_FIELDS)
    if not known and media_dir is not None and not (media_dir / path).is_file():
        stats.missing_files.append(str(path))
        return none
    return media


def _merge(
    existing: sqlite3.Row,
    entry: dict[str, Any],
    media: dict[str, Any],
    fill: Iterable[str],
    replace: Iterable[str],
) -> dict[str, Any]:
    """The columns to update: `fill` ones only where the mini has nothing, `replace` ones
    (file facts) whenever the manifest knows them, and the encode when it has one."""
    out: dict[str, Any] = {}
    for k in fill:
        if existing[k] in (None, "") and entry.get(k) not in (None, ""):
            out[k] = entry[k]
    for k in replace:
        if entry.get(k) is not None and existing[k] != entry[k]:
            out[k] = entry[k]
    if media["media_path"] is not None:
        out.update({k: v for k, v in media.items() if existing[k] != v})
    return out


Write = tuple[str, tuple[object, ...]]  # SQL and its parameters


def _update(table: str, rowid: object, cols: dict[str, Any]) -> Write:
    sets = ", ".join(f"{k} = ?" for k in cols)
    return f"UPDATE {table} SET {sets} WHERE id = ?", (*cols.values(), rowid)


def _insert(table: str, row: dict[str, Any]) -> Write:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    return f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values())


def index_media(
    db: sqlite3.Connection,
    manifest: Manifest,
    media_dir: Path | None,
    now: datetime,
    *,
    prune: bool = False,
) -> IndexStats:
    """Bring the database in line with a manifest. With `media_dir`, a new or changed encode
    is recorded only if its file is there (one stat, no probe). With `prune`, encodes the
    manifest no longer lists are cleared."""
    stats = IndexStats()
    shows = {r["id"]: r for r in db.execute("SELECT * FROM shows")}
    episodes = {
        (r["show_id"], r["season"], r["episode"]): r for r in db.execute("SELECT * FROM episodes")
    }
    movies = {r["id"]: r for r in db.execute("SELECT * FROM movies")}
    bumpers = {(r["channel_id"], r["file_path"]): r for r in db.execute("SELECT * FROM bumpers")}
    writes: list[Write] = []
    listed: set[str] = set()

    def media_for(entry: dict[str, Any], existing: sqlite3.Row | None) -> dict[str, Any]:
        media = _clean_media(entry, existing, media_dir, stats)
        if media["media_path"] is not None:
            listed.add(media["media_path"])
            if (
                existing is None
                or existing["media_path"] != media["media_path"]
                or (existing["media_seconds"] != media["media_seconds"])
            ):
                stats.media += 1
        return media

    for s in manifest.get("shows", []):
        found = shows.get(s["id"])
        if found is None:
            row = {k: s.get(k) for k in SHOW_FIELDS} | {"updated_at": now.isoformat()}
            writes.append(_insert("shows", row))
            stats.shows += 1
        elif found["year"] is None and s.get("year") is not None:
            year = s["year"]
            writes.append(_update("shows", s["id"], {"year": year}))
            stats.updated += 1

    known_shows = set(shows) | {s["id"] for s in manifest.get("shows", [])}
    for e in manifest.get("episodes", []):
        if e.get("show_id") not in known_shows:
            continue
        key = (e["show_id"], e["season"], e["episode"])
        found = episodes.get(key)
        media = media_for(e, found)
        if found is None:
            row = {k: e.get(k) for k in EPISODE_FIELDS} | media
            row["title"] = row["title"] or ""
            writes.append(_insert("episodes", row))
            stats.episodes += 1
            continue
        cols = _merge(
            found, e, media,
            fill=("title", "air_date", "air_date_source", "runtime_seconds"),
            replace=("file_path", "file_size", "source_seconds"),
        )  # fmt: skip
        if cols:
            writes.append(_update("episodes", found["id"], cols))
            stats.updated += 1

    for m in manifest.get("movies", []):
        found = movies.get(m["id"])
        media = media_for(m, found)
        if found is None:
            row = {k: m.get(k) for k in MOVIE_FIELDS} | media | {"updated_at": now.isoformat()}
            writes.append(_insert("movies", row))
            stats.movies += 1
            continue
        cols = _merge(
            found, m, media,
            fill=("year", "premiere_date", "runtime_seconds"),
            replace=("file_path", "file_size", "source_seconds"),
        )  # fmt: skip
        if cols:
            writes.append(_update("movies", found["id"], cols))
            stats.updated += 1

    for b in manifest.get("bumpers", []):
        found = bumpers.get((b["channel_id"], b["file_path"]))
        media = media_for(b, found)
        if found is None:
            if not b.get("runtime_seconds"):
                continue
            row = {k: b.get(k) for k in BUMPER_FIELDS} | media
            writes.append(_insert("bumpers", row))
            stats.bumpers += 1
            continue
        cols = _merge(found, b, media, fill=(), replace=())
        if cols:
            writes.append(_update("bumpers", found["id"], cols))
            stats.updated += 1

    if prune:
        clear = dict.fromkeys(MEDIA_FIELDS)
        for table, rows in (("episodes", episodes), ("movies", movies), ("bumpers", bumpers)):
            for r in rows.values():
                if r["media_path"] is not None and r["media_path"] not in listed:
                    writes.append(_update(table, r["id"], clear))
                    stats.pruned += 1

    if writes:
        with transaction(db):
            for sql, params in writes:
                db.execute(sql, params)
            db.execute(
                "INSERT INTO import_runs (source, started_at, finished_at, stats)"
                " VALUES ('index-media', ?, ?, ?)",
                (now.isoformat(), now.isoformat(), json.dumps(asdict(stats))),
            )
    return stats


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def watch(
    db: sqlite3.Connection,
    media_dir: Path,
    *,
    interval: float,
    now: Callable[[], datetime],
    sleep: Callable[[float], None] = time.sleep,
    ticks: int | None = None,
    on_tick: Callable[[int], None] | None = None,
    report: Callable[[IndexStats], None] | None = None,
    prune: bool = False,
    log: Callable[[str], None] = _log,
) -> int:
    """Index `manifest.json` whenever it changes, checking every `interval` seconds; forever
    unless `ticks` is given. Returns how many times it indexed."""
    path = media_dir / MANIFEST
    last: tuple[int, int] | None = None
    runs = 0
    n = 0
    while ticks is None or n < ticks:
        if on_tick:
            on_tick(n)
        stamp = _stamp(path)
        if stamp is not None and stamp != last:
            last = stamp
            try:
                manifest = load_manifest(path)
            except ManifestError as err:
                log(f"index-media: skipped, {err}")
            else:
                stats = index_media(db, manifest, media_dir, now(), prune=prune)
                runs += 1
                if report:
                    report(stats)
                if stats.changed:
                    log(f"index-media: {json.dumps(asdict(stats))}")
        n += 1
        if ticks is None or n < ticks:
            sleep(interval)
    return runs
