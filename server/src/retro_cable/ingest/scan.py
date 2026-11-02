"""Read Plex-named folders straight off the disk: `python -m retro_cable.ingest scan`.

A stopgap while there is no Plex token: it finds carried shows' episode files (and carried
movies) by name, without air dates. Undated episodes get conservative dates when the schedule
is read (catalog.air_dates.synthesize_undated). A later Plex import or TMDB fill replaces them.

TV layout: `<root>/<Show (Year)>/Season NN/<Show (Year)> - SxxEyy - Title.ext`, plus the
variants found on the NAS: `Show.S01E02.Title`, `1x02`, `102 - Title` and `02. Title` inside a
`Season N` folder. Movies: `<root>/<Title (Year)>/<Title (Year)>.ext`.
"""

import json
import os
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from retro_cable.catalog import Catalog
from retro_cable.db import transaction
from retro_cable.ingest.plex import _title_index, normalize_title

VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".mpg", ".mpeg", ".xvid",
         ".divx", ".flv", ".webm"}  # fmt: skip
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot", ".AppleDouble"}
EXTRA = re.compile(r"(?:^|[-_. ])(?:sample|trailer|featurette|behindthescenes|extras?)(?:$|[-_. ])",
                   re.I)  # fmt: skip

_SXE = re.compile(r"(?<![A-Za-z0-9])[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})(?:-?[Ee]\d{1,3})*")
_NXM = re.compile(r"(?<![0-9A-Za-z])(\d{1,2})x(\d{2,3})(?![0-9])")
_SEASON_DIR = re.compile(r"^(?:season|series)[ ._-]*(\d{1,4})$", re.I)
_LEADING_SE = re.compile(r"^(\d{1,2})(\d{2})(?=[ ._-])")
_LEADING_E = re.compile(r"^(\d{1,3})(?:\.|\s*-|\s)\s*(?=\D)")
_JUNK = re.compile(
    r"\b(?:480p|576p|720p|1080p|2160p|WEB[- ]?DL|WEBRip|WEB|DVDRip|DVD|BDRip|BluRay|HDTV|"
    r"x264|x265|h\.?264|h\.?265|HEVC|XviD|DivX|AAC2?\.?0?|AC3|DDP?5\.1|EAC3)\b.*$",
    re.I,
)
_YEAR = re.compile(r"\((19\d{2}|20\d{2})\)")


def _season_of(folder: str) -> int | None:
    if folder.casefold() in ("specials", "season 0", "season 00"):
        return 0
    m = _SEASON_DIR.match(folder)
    return int(m[1]) if m else None


def _clean_title(rest: str) -> str:
    rest = rest.split("[", 1)[0]
    rest = _SXE.sub(" ", _NXM.sub(" ", rest))
    if " " not in rest.strip(" ._-"):
        rest = rest.replace(".", " ").replace("_", " ")
    rest = _JUNK.sub("", rest)
    return rest.strip(" ._-")


def parse_episode(folder: str, name: str) -> tuple[int, int, str] | None:
    """(season, episode, title) from a file name and its folder, or None if it has no number."""
    stem = Path(name).stem
    if EXTRA.search(stem):
        return None
    tokens = [m for m in (_SXE.search(stem), _NXM.search(stem)) if m]
    if tokens:
        first = min(tokens, key=lambda m: m.start())
        return int(first[1]), int(first[2]), _clean_title(stem[first.end() :])
    season = _season_of(folder)
    if season is None:
        return None
    m = _LEADING_SE.match(stem)
    if m and int(m[1]) == season:
        return season, int(m[2]), _clean_title(stem[m.end() :])
    m = _LEADING_E.match(stem)
    if m:
        return season, int(m[1]), _clean_title(stem[m.end() :])
    return None


def show_year(folder: str, names: list[str]) -> int | None:
    """The premiere year from `Show (Year)`, or the commonest `Show (Year) - Sxx` file name."""
    m = _YEAR.search(folder)
    if m:
        return int(m[1])
    years = Counter(
        int(m[1]) for n in names if (m := re.search(r"\((\d{4})\)\s*-\s*[Ss]\d", n)) is not None
    )
    return years.most_common(1)[0][0] if years else None


def _keys(folder: str) -> list[str]:
    """Normalized names to try for a folder: as is, without a year, `X, The` as `The X`."""
    base = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", folder).rstrip(" -")
    names = [folder, base]
    m = re.match(r"^(.*),\s*(the|a|an)$", base, re.I)
    if m:
        names.append(f"{m[2]} {m[1]}")
    return [normalize_title(n) for n in names]


class _Index:
    def __init__(self, catalog: Catalog, kind: str) -> None:
        self.exact = _title_index(catalog, kind)
        self.compact = {k.replace(" ", ""): v for k, v in self.exact.items()}

    def match(self, folder: str) -> str | None:
        for key in _keys(folder):
            slug = self.exact.get(key) or self.compact.get(key.replace(" ", ""))
            if slug:
                return slug
        return None


def _titles(catalog: Catalog) -> dict[str, str]:
    titles: dict[str, str] = {}
    for c in catalog.carriage.carriage:
        titles.setdefault(c.show, c.title)
    return titles


def _video_files(top: Path) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(filenames):
            if not f.startswith(".") and Path(f).suffix.lower() in VIDEO:
                found.append(Path(dirpath) / f)
    return found


def _folders(root: Path) -> list[Path]:
    return sorted(
        p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS and p.name[0] != "."
    )


@dataclass
class ScanStats:
    shows: int = 0
    episodes: int = 0
    duplicates: int = 0
    detached: int = 0
    unmatched_folders: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Found:
    season: int
    episode: int
    title: str
    path: Path
    size: int
    canonical: bool  # in a `Season NN` / `Specials` folder


def _pick(found: list[_Found]) -> dict[tuple[int, int], _Found]:
    """One file per episode: a `Season NN` copy first, then the bigger file, then by path."""
    best: dict[tuple[int, int], _Found] = {}
    for f in sorted(found, key=lambda f: (not f.canonical, -f.size, str(f.path))):
        best.setdefault((f.season, f.episode), f)
    return best


def scan_tv(
    db: sqlite3.Connection, root: Path, catalog: Catalog, now: datetime | None = None
) -> ScanStats:
    """Attach every carried show's episode files under `root`. Air dates are left alone."""
    stats = ScanStats()
    index, titles = _Index(catalog, "show"), _titles(catalog)
    seen: set[str] = set()
    for folder in _folders(root):
        slug = index.match(folder.name)
        if slug is None:
            stats.unmatched_folders.append(folder.name)
            continue
        files = _video_files(folder)
        found = []
        for f in files:
            parsed = parse_episode(f.parent.name, f.name)
            if parsed is None:
                stats.unparsed.append(f.relative_to(root).as_posix())
                continue
            season, episode, title = parsed
            canonical = _season_of(f.parent.name) is not None
            found.append(_Found(season, episode, title, f, f.stat().st_size, canonical))
        best = _pick(found)
        stats.duplicates += len(found) - len(best)
        year = show_year(folder.name, [f.name for f in files])
        with transaction(db):
            db.execute(
                "INSERT INTO shows (id, title, year, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET year = COALESCE(shows.year, excluded.year)",
                (slug, titles.get(slug, folder.name), year, now.isoformat() if now else None),
            )
            for (season, episode), pick in sorted(best.items()):
                db.execute(
                    """INSERT INTO episodes (show_id, season, episode, title, file_path, file_size)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT (show_id, season, episode) DO UPDATE SET
                         title = CASE WHEN episodes.title = '' THEN excluded.title
                                 ELSE episodes.title END,
                         file_path = excluded.file_path, file_size = excluded.file_size""",
                    (slug, season, episode, pick.title, str(pick.path), pick.size),
                )
                seen.add(str(pick.path))
        stats.shows += 1
        stats.episodes += len(best)
    # Files under this root that weren't found again are detached; their rows stay.
    prefix = str(root).rstrip("/") + "/"
    with transaction(db):
        for r in db.execute("SELECT id, file_path FROM episodes WHERE file_path IS NOT NULL"):
            if r["file_path"].startswith(prefix) and r["file_path"] not in seen:
                db.execute(
                    "UPDATE episodes SET file_path = NULL, file_size = NULL WHERE id = ?",
                    (r["id"],),
                )
                stats.detached += 1
    _record_run(db, "scan", now, asdict(stats))
    return stats


@dataclass
class MovieScanStats:
    movies: int = 0
    unmatched_folders: list[str] = field(default_factory=list)


def scan_movies(
    db: sqlite3.Connection, root: Path, catalog: Catalog, now: datetime | None = None
) -> MovieScanStats:
    """Attach carried movies from `<root>/<Title (Year)>/`: the biggest video file that isn't
    a trailer or extra. The premiere date is left alone (the year bounds it, see load_library)."""
    stats = MovieScanStats()
    index, titles = _Index(catalog, "movie"), _titles(catalog)
    for folder in _folders(root):
        slug = index.match(folder.name)
        if slug is None:
            stats.unmatched_folders.append(folder.name)
            continue
        files = [f for f in _video_files(folder) if not EXTRA.search(f.stem)]
        if not files:
            continue
        best = max(files, key=lambda f: (f.stat().st_size, str(f)))
        year = show_year(folder.name, [])
        db.execute(
            """INSERT INTO movies (id, title, year, file_path, file_size, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET year = COALESCE(movies.year, excluded.year),
                 file_path = excluded.file_path, file_size = excluded.file_size""",
            (slug, titles.get(slug, folder.name), year, str(best), best.stat().st_size,
             now.isoformat() if now else None),
        )  # fmt: skip
        stats.movies += 1
    _record_run(db, "scan-movies", now, asdict(stats))
    return stats


def _record_run(
    db: sqlite3.Connection, source: str, now: datetime | None, stats: dict[str, object]
) -> None:
    stamp = now.isoformat() if now else ""
    db.execute(
        "INSERT INTO import_runs (source, started_at, finished_at, stats) VALUES (?, ?, ?, ?)",
        (source, stamp, stamp, json.dumps(stats)),
    )
