"""Inventory report: episodes per channel per year, expected vs on disk, plus gaps.

`python -m retro_cable.inventory [--db PATH] [--catalog DIR] [--out DIR]` writes
inventory.md and inventory.json. "Expected" means eligible by Dec 31 of that year (effective
air date on or before it) for a show the channel carries that year; "on disk" means it also
has a file. Episodes known only from TMDB make up the difference.
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from retro_cable.catalog import (
    Catalog,
    CatalogError,
    effective_dates,
    is_library_show,
    load_catalog,
)
from retro_cable.catalog.holidays import Tagger
from retro_cable.catalog.models import Carriage, Channel
from retro_cable.db import connect, migrate
from retro_cable.ingest.cli import default_db


@dataclass(frozen=True)
class Row:
    channel: str
    year: int
    shows_carried: int
    expected: int
    on_disk: int
    movies_expected: int
    movies_on_disk: int

    @property
    def missing(self) -> int:
        return self.expected - self.on_disk


@dataclass
class Gaps:
    missing_shows: list[str] = field(default_factory=list)
    shows_without_files: list[str] = field(default_factory=list)
    undated_on_disk: list[str] = field(default_factory=list)
    shows_without_tmdb_id: list[str] = field(default_factory=list)
    missing_movies: list[str] = field(default_factory=list)
    in_plex_not_carried: list[str] = field(default_factory=list)
    untagged_specials: list[str] = field(default_factory=list)


@dataclass
class Inventory:
    catalog_version: str
    channels: list[tuple[str, str]]  # (id, name) in catalog order
    rows: list[Row]
    gaps: Gaps


@dataclass(frozen=True)
class _Episode:
    effective: date | None
    on_disk: bool


def _on_air(channel: Channel, year: int) -> bool:
    return channel.library or channel.launch is None or channel.launch.year <= year


def _carried_in(c: Carriage, year: int) -> bool:
    return c.from_ <= date(year, 12, 31) and (c.until is None or c.until >= date(year, 1, 1))


def _episodes(catalog: Catalog, db: sqlite3.Connection) -> dict[str, list[_Episode]]:
    by_show: dict[str, list[tuple[int, int, date | None, bool]]] = defaultdict(list)
    for r in db.execute(
        "SELECT show_id, season, episode, air_date, file_path IS NOT NULL AS on_disk"
        " FROM episodes ORDER BY show_id, season, episode"
    ):
        air = date.fromisoformat(r["air_date"]) if r["air_date"] else None
        by_show[r["show_id"]].append((r["season"], r["episode"], air, bool(r["on_disk"])))
    out: dict[str, list[_Episode]] = {}
    for show, rows in by_show.items():
        dates = effective_dates(catalog, show, [(s, e, d) for s, e, d, _ in rows])
        out[show] = [_Episode(d, row[3]) for d, row in zip(dates, rows, strict=True)]
    return out


def _gaps(catalog: Catalog, db: sqlite3.Connection, episodes: dict[str, list[_Episode]]) -> Gaps:
    gaps = Gaps()
    shows = {c.show for c in catalog.carriage.carriage if c.kind == "show"}
    movies = {c.show for c in catalog.carriage.carriage if c.kind == "movie"}
    in_db = {r["id"]: r["tmdb_id"] for r in db.execute("SELECT id, tmdb_id FROM shows")}
    movie_files = {r["id"]: r["file_path"] for r in db.execute("SELECT id, file_path FROM movies")}
    gaps.missing_shows = sorted(shows - in_db.keys())
    gaps.shows_without_files = sorted(
        s for s in shows & in_db.keys() if not any(e.on_disk for e in episodes.get(s, []))
    )
    gaps.shows_without_tmdb_id = sorted(s for s in shows & in_db.keys() if in_db[s] is None)
    gaps.missing_movies = sorted(m for m in movies if not movie_files.get(m))
    for r in db.execute(
        "SELECT show_id, season, episode FROM episodes"
        " WHERE air_date IS NULL AND file_path IS NOT NULL ORDER BY show_id, season, episode"
    ):
        gaps.undated_on_disk.append(f"{r['show_id']} S{r['season']:02d}E{r['episode']:02d}")
    last = db.execute(
        "SELECT stats FROM import_runs WHERE source = 'plex' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last:
        stats = json.loads(last["stats"])
        gaps.in_plex_not_carried = sorted(
            stats.get("unmatched_shows", []) + stats.get("unmatched_movies", [])
        )
    gaps.untagged_specials = _untagged_specials(catalog, db)
    return gaps


SPECIAL_MAX_SECONDS = 60 * 60  # a "movie" this short is a TV special (Rudolph, Frosty)


def _untagged_specials(catalog: Catalog, db: sqlite3.Connection) -> list[str]:
    """Specials the holiday tagger couldn't place: episodes of seasonal shows (which never
    air untagged) and short movies. Fix them with holiday_tags or holidays.yaml `tagged`."""
    tagger = Tagger(catalog)
    found = []
    for r in db.execute(
        "SELECT e.show_id, e.season, e.episode, e.title, s.title AS show_title FROM episodes e"
        " JOIN shows s ON s.id = e.show_id ORDER BY e.show_id, e.season, e.episode"
    ):
        if r["show_id"] not in tagger.seasonal_shows:
            continue
        tags = tagger.tags(
            r["show_id"], "episode", r["show_title"], r["title"], r["season"], r["episode"]
        )
        if not tags:
            found.append(f"{r['show_id']} S{r['season']:02d}E{r['episode']:02d}")
    for r in db.execute(
        "SELECT id, title FROM movies WHERE runtime_seconds > 0 AND runtime_seconds <= ?",
        (SPECIAL_MAX_SECONDS,),
    ):
        if not tagger.tags(r["id"], "movie", r["title"]):
            found.append(f"{r['id']} (movie)")
    return sorted(found)


def build_inventory(catalog: Catalog, db: sqlite3.Connection) -> Inventory:
    episodes = _episodes(catalog, db)
    premieres = {
        r["id"]: (date.fromisoformat(r["premiere_date"]) if r["premiere_date"] else None,
                  r["file_path"] is not None)
        for r in db.execute("SELECT id, premiere_date, file_path FROM movies")
    }  # fmt: skip
    low, high = catalog.channels.year_range
    rows = []
    for channel in catalog.channels.channels:
        carriage = [c for c in catalog.carriage.carriage if c.channel == channel.id]
        for year in range(low, high + 1):
            if not _on_air(channel, year):
                continue
            end = date(year, 12, 31)
            carried = [c for c in carriage if _carried_in(c, year)]
            shows = {c.show for c in carried if c.kind == "show"}
            eligible = [
                e
                for s in shows
                for e in episodes.get(s, [])
                if is_library_show(catalog, s) or (e.effective is not None and e.effective <= end)
            ]
            movies_expected = movies_on_disk = 0
            for c in carried:
                if c.kind != "movie":
                    continue
                premiere, has_file = premieres.get(c.show, (None, False))
                if premiere is not None and premiere > end:
                    continue  # carried, but not out yet (Halloweentown before Oct 1998)
                movies_expected += 1
                movies_on_disk += has_file
            rows.append(
                Row(
                    channel=channel.id,
                    year=year,
                    shows_carried=len(shows),
                    expected=len(eligible),
                    on_disk=sum(1 for e in eligible if e.on_disk),
                    movies_expected=movies_expected,
                    movies_on_disk=movies_on_disk,
                )
            )
    return Inventory(
        catalog_version=catalog.version,
        channels=[(c.id, c.name) for c in catalog.channels.channels],
        rows=rows,
        gaps=_gaps(catalog, db, episodes),
    )


_GAP_TITLES = {
    "missing_shows": "Carried but not in Plex",
    "shows_without_files": "In Plex but no episode files",
    "undated_on_disk": "On disk without an air date (can't air)",
    "shows_without_tmdb_id": "No TMDB id (expected counts are Plex-only)",
    "missing_movies": "Carried movies not on disk",
    "in_plex_not_carried": "In Plex but not carried (prune or add carriage)",
    "untagged_specials": "Specials without a holiday tag (seasonal shows' episodes never air)",
}


def render_markdown(inv: Inventory) -> str:
    lines = [f"# Inventory (catalog {inv.catalog_version})", ""]
    lines.append(
        "Expected = episodes eligible by Dec 31 of the year for shows the channel carries "
        "that year; missing = expected minus on disk."
    )
    for channel_id, name in inv.channels:
        rows = [r for r in inv.rows if r.channel == channel_id]
        lines += ["", f"## {channel_id} — {name}", ""]
        lines.append("| Year | Shows | Expected | On disk | Missing | Movies | Movies on disk |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        lines += [
            f"| {r.year} | {r.shows_carried} | {r.expected} | {r.on_disk} | {r.missing}"
            f" | {r.movies_expected} | {r.movies_on_disk} |"
            for r in rows
        ]
    lines += ["", "## Gaps"]
    for key, title in _GAP_TITLES.items():
        items: list[str] = getattr(inv.gaps, key)
        lines += ["", f"### {title} ({len(items)})", ""]
        lines += [f"- {i}" for i in items] or ["None."]
    return "\n".join(lines) + "\n"


def to_json(inv: Inventory) -> str:
    data = {
        "catalog_version": inv.catalog_version,
        "rows": [{**asdict(r), "missing": r.missing} for r in inv.rows],
        "gaps": asdict(inv.gaps),
    }
    return json.dumps(data, indent=2) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m retro_cable.inventory")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--catalog", type=Path, default=Path("catalog"))
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
    except CatalogError as err:
        print(err, file=sys.stderr)
        return 1
    db_path = args.db or default_db()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = connect(db_path)
    migrate(db)
    inv = build_inventory(catalog, db)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "inventory.md").write_text(render_markdown(inv))
    (args.out / "inventory.json").write_text(to_json(inv))
    print(f"wrote {args.out / 'inventory.md'} and inventory.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
