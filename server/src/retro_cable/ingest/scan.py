"""Read Plex-named folders straight off the disk: `python -m retro_cable.ingest scan`.

A stopgap while there is no Plex token: it finds episode files by name, without air dates, for
carried shows and (stored as uncarried, so /admin can put them on new channels) every other
show folder; and carried movies. Undated episodes get conservative dates when the schedule
is read (catalog.air_dates.synthesize_undated). A later Plex import or TMDB fill replaces them.

TV layout: `<root>/<Show (Year)>/Season NN/<Show (Year)> - SxxEyy - Title.ext`, plus the
variants found on the NAS: `Show.S01E02.Title`, `1x02`, `102 - Title` and `02. Title` inside a
`Season N` folder. Movies: `<root>/<Title (Year)>/<Title (Year)>.ext`.

Several roots read as one library (the rig: Plex's trees, then the LiveTV downloads from
sonarr-livetv and radarr-livetv). A show's folders in every root merge; when two roots have
the same episode, the earlier root's copy wins, so a rescan never flips between them. A root
that isn't there is skipped and detaches nothing; an empty one really is empty.
"""

import json
import math
import os
import re
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

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
_SEASON_DIR = re.compile(r"^(?:season|series)[ ._-]*(\d{1,4})(.*)$", re.I)
_LEADING_SE = re.compile(r"^(\d{1,2})(\d{2})(?=[ ._-])")
_LEADING_E = re.compile(r"^(\d{1,3})(?:\.|\s*-|\s)\s*(?=\D)")
# `1.01 Welcome to the Hellmouth`, `5.1 Buffy vs. Dracula` (one digit only before a space)
_LEADING_DOT = re.compile(r"^(\d{1,2})\.(\d{2}(?=[ ._-])|\d(?= ))")
_WORDS = re.compile(  # `Season 1 Episode 12`, `S1Episode 1`
    r"(?<![A-Za-z0-9])(?:season|s)[ ._-]*(\d{1,2})[ ._-]*episode[ ._-]*(\d{1,3})(?!\d)", re.I
)
_SHOW_NUMBER = re.compile(r"^(?:.*? - )?(\d{1,3})(?: - |[ ._])\s*(.*)$")  # `Show - 101 - Title`
_JUNK = re.compile(
    r"\b(?:480p|576p|720p|1080p|2160p|WEB[- ]?DL|WEBRip|WEB|DVDRip|DVD|BDRip|BluRay|HDTV|"
    r"x264|x265|h\.?264|h\.?265|HEVC|XviD|DivX|AAC2?\.?0?|AC3|DDP?5\.1|EAC3)\b.*$",
    re.I,
)
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_DIGITS = re.compile(r"(\d+)")
_NAMES_SEASON = re.compile(r"(?:season|series)[ ._-]*\d|(?<![A-Za-z0-9])s\d{1,2}(?!\d)", re.I)
_YEAR = re.compile(r"\((19\d{2}|20\d{2})\)")

# Measures a source file: its length in seconds from ffprobe, or None when it can't be read.
# The CLI passes normalize.source_seconds; tests pass a fake, or nothing to skip measuring.
SourceProbe = Callable[[Path], float | None]
# One root, or several in order of preference.
Roots = Path | Sequence[Path]


def _present(roots: Roots, missing: list[str]) -> list[Path]:
    """The roots that are directories, in order; the others are noted in `missing`."""
    found = []
    for root in [roots] if isinstance(roots, Path) else roots:
        if root.is_dir():
            found.append(root)
        else:
            missing.append(str(root))
    return found


def _measure(
    probe: SourceProbe | None,
    path: Path,
    size: int,
    known: tuple[str | None, int | None, int | None] | None,
) -> int | None:
    """A source's measured length, rounded down: probed when the file is new, changed or not
    measured yet, else the length recorded for it last time."""
    if known is not None and known[0] == str(path) and known[1] == size and known[2]:
        return known[2]
    if probe is None:
        return None
    seconds = probe(path)
    return math.floor(seconds) if seconds and seconds > 0 else None


def _season_of(folder: str) -> int | None:
    if folder.casefold() in ("specials", "season 0", "season 00"):
        return 0
    m = _SEASON_DIR.match(folder)
    if m is None:
        return None
    # `Season 1.AAC`, `Season 2 [DVDRip]`: a release tag may follow, but not another number
    tail = m[2].strip(" ._-")
    return int(m[1]) if not tail or tail[0] in "[(" or _JUNK.match(tail) else None


def _clean_title(rest: str) -> str:
    rest = rest.split("[", 1)[0]
    rest = _SXE.sub(" ", _NXM.sub(" ", rest))
    if " " not in rest.strip(" ._-"):
        rest = rest.replace(".", " ").replace("_", " ")
    rest = _JUNK.sub("", rest)
    return rest.strip(" ._-\u2013\u2014")  # en and em dashes too


def parse_episode(folder: str, name: str) -> tuple[int, int, str] | None:
    """(season, episode, title) from a file name and its folder, or None if it has no number."""
    stem = Path(name).stem
    if EXTRA.search(stem):
        return None
    tokens = [m for m in (_SXE.search(stem), _NXM.search(stem), _WORDS.search(stem)) if m]
    if tokens:
        first = min(tokens, key=lambda m: m.start())
        return int(first[1]), int(first[2]), _clean_title(stem[first.end() :])
    m = _LEADING_DOT.match(stem)
    if m and int(m[2]) > 0:
        return int(m[1]), int(m[2]), _clean_title(stem[m.end() :])
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
    def __init__(self, catalog: Catalog, kind: str, more: Mapping[str, str] = {}) -> None:
        # `more`: id -> title of others carried (the mini's /admin channels); the YAML wins.
        self.exact = {normalize_title(t): i for i, t in more.items()} | _title_index(catalog, kind)
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
    shows: int = 0  # carried
    episodes: int = 0  # of carried shows
    duplicates: int = 0
    detached: int = 0
    uncarried: list[str] = field(default_factory=list)  # folders stored as uncarried shows
    uncarried_episodes: int = 0
    unmatched_folders: list[str] = field(default_factory=list)  # stored nowhere
    unparsed: list[str] = field(default_factory=list)
    missing_roots: list[str] = field(default_factory=list)  # skipped: nothing detached


@dataclass(frozen=True)
class _Found:
    season: int
    episode: int
    title: str
    path: Path
    size: int
    canonical: bool  # in a `Season NN` / `Specials` folder
    rank: int = 0  # the root's place in the scan's list: earlier roots win


def _pick(found: list[_Found]) -> dict[tuple[int, int], _Found]:
    """One file per episode: an earlier root's copy first, then a `Season NN` copy, then the
    bigger file, then by path."""
    best: dict[tuple[int, int], _Found] = {}
    for f in sorted(found, key=lambda f: (f.rank, not f.canonical, -f.size, str(f.path))):
        best.setdefault((f.season, f.episode), f)
    return best


_RELEASE = re.compile(
    r"[ ._-](?:S\d{1,2}(?:E\d{1,3})?|Season[ ._]?\d+|Complete|\d{3,4}p|WEB(?:-?DL|Rip)?|HDTV|"
    r"DVDRip|BluRay|BDRip|x26[45]|HEVC)(?=$|[ ._-])",
    re.I,
)


def show_title(folder: str) -> str:
    """A show's title from its folder name: `Office, The (2005)` -> `The Office`,
    `Russian.Doll.S01E01.720p.HEVC.x265-MeGusta` -> `Russian Doll`. Drops the `(year)`,
    release tags and private-use characters (Synology's stand-ins for a trailing dot or space),
    and moves a trailing article to the front."""
    name = "".join(ch for ch in folder if unicodedata.category(ch) != "Co").strip()
    if " " not in name:
        name = name.replace(".", " ").replace("_", " ")
    m = _RELEASE.search(name)
    if m and m.start() > 0:
        name = name[: m.start()]
    name = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", name).strip(" -._")
    m = re.match(r"^(.*),\s*(the|a|an)$", name, re.I)
    return (f"{m[2]} {m[1]}" if m else name) or folder


_MOVIE_TAG = (
    r"\d{3,4}p|WEB(?:-?DL|Rip)?|HDRip|HDTV|DVDRip|DVD|BluRay|BrRip|BDRip|REMUX|x26[45]|"
    r"H\.?26[45]|HEVC|XviD|AC3|AAC|DTS|YTS(?:\.\w+)?|HD-?TS|Directors?\.?\s?Cut"
)
# Where a release-named movie folder's title ends: a bracketed year, a bare year (to 2029,
# so `Blade Runner 2049` stays whole) before a tag or the end, or the first release tag.
_MOVIE_END = re.compile(
    r"\s*[(\[](?:19|20)\d{2}[)\]]"
    rf"|\s(?:19\d{{2}}|20[0-2]\d)(?=\s+(?:{_MOVIE_TAG})\b|\s*$)"
    rf"|[\s\[(-](?:{_MOVIE_TAG})(?=$|[\s\])._-])",
    re.I,
)


def movie_title(folder: str) -> str:
    """A movie's title from its folder name: `Psych.The.Movie.2017.HDRip.XviD.AC3-EVO` ->
    `Psych The Movie`, `Beauty.Shop[2005]DvDRip-Rdgrnnr` -> `Beauty Shop`, `Amelie (2001)`
    -> `Amelie`. A trailing article moves to the front, as for shows."""
    name = "".join(ch for ch in folder if unicodedata.category(ch) != "Co").strip()
    if " " not in name:
        name = name.replace(".", " ").replace("_", " ")
    m = _MOVIE_END.search(name, 1)
    if m:
        name = name[: m.start()]
    name = name.strip(" -._")
    m2 = re.match(r"^(.*),\s*(the|a|an)$", name, re.I)
    return (f"{m2[2]} {m2[1]}" if m2 else name) or folder


def _slug(title: str) -> str:
    return "-".join(normalize_title(title).split()) or "show"


def _excluded(catalog: Catalog) -> set[str]:
    """Normalized titles of carried shows that overrides.yaml excludes from import."""
    gone = {o.show for o in catalog.overrides.shows if o.exclude}
    keys = {normalize_title(c.title) for c in catalog.carriage.carriage if c.show in gone}
    keys |= {normalize_title(o.plex_title) for o in catalog.overrides.shows
             if o.show in gone and o.plex_title}  # fmt: skip
    return keys | {k.replace(" ", "") for k in keys}


def _numbered(files: list[Path]) -> dict[Path, tuple[int, int, str]]:
    """`101 - Title` files in a folder that isn't a season folder, as S01E01, when every
    numbered file there has a season digit and an episode from 01 to 49. A folder with lower
    numbers too (`099`, `100`) is absolute numbering, and one file alone proves nothing."""
    hits = {f: m for f in files if not EXTRA.search(f.stem) and (m := _SHOW_NUMBER.match(f.stem))}
    if len(hits) < 2 or not all(
        len(m[1]) == 3 and m[1][0] != "0" and 1 <= int(m[1][1:]) <= 49 for m in hits.values()
    ):
        return {}
    return {f: (int(m[1][0]), int(m[1][1:]), _clean_title(m[2])) for f, m in hits.items()}


def _absolute(files: list[Path]) -> dict[Path, tuple[int, int, str]]:
    """Files whose names are one name with a different number in each (`[a-s] Samurai
    Champloo 01`, `Princess.Jellyfish.-.02.-.1080p`) as season 1, episode N: absolute
    numbering. Tags in brackets don't count; it takes three such files, none of them extras,
    in a folder that doesn't name a season (`Series 5 + Bonus/Episode 01`, `Dog.S01/cd101`)."""
    groups: dict[tuple[str, ...], list[tuple[Path, list[str]]]] = {}
    for f in files:
        if not EXTRA.search(f.stem):
            parts = _DIGITS.split(_BRACKETS.sub("", f.stem).strip())
            groups.setdefault(tuple(parts[0::2]), []).append((f, parts[1::2]))
    found = {}
    for members in groups.values():
        columns = list(zip(*(numbers for _, numbers in members), strict=True))
        varying = [i for i, column in enumerate(columns) if len(set(column)) > 1]
        if len(members) < 3 or len(varying) != 1:
            continue
        column = columns[varying[0]]
        if len(set(column)) == len(column) and all(len(n) <= 3 and int(n) > 0 for n in column):
            found.update({f: (1, int(numbers[varying[0]]), "") for f, numbers in members})
    return found


def _collect(
    folder: Path, root: Path, stats: ScanStats, rank: int = 0
) -> tuple[list[Path], list[_Found]]:
    files = _video_files(folder)
    episodes = {f: parse_episode(f.parent.name, f.name) for f in files}
    loose: dict[Path, list[Path]] = {}
    for f, parsed in episodes.items():
        if parsed is not None or _season_of(f.parent.name) is not None:
            continue
        # `Season 1/Gravity Falls 720p/01 Tourist Trapped.mp4`: a release folder in a season
        if f.parent != folder and not EXTRA.search(f.parent.name):
            season_dir = f.parent.parent.name
            if _season_of(season_dir) is not None:
                episodes[f] = parse_episode(season_dir, f.name)
                continue
        loose.setdefault(f.parent, []).append(f)
    for parent, group in loose.items():
        episodes.update(_numbered(group))
        if not EXTRA.search(parent.name) and not _NAMES_SEASON.search(parent.name):
            episodes.update(_absolute([f for f in group if episodes[f] is None]))
    found = []
    for f in files:
        parsed = episodes[f]
        if parsed is None:
            stats.unparsed.append(f.relative_to(root).as_posix())
            continue
        season, episode, title = parsed
        canonical = _season_of(f.parent.name) is not None
        found.append(_Found(season, episode, title, f, f.stat().st_size, canonical, rank))
    return files, found


def _best(found: list[_Found], stats: ScanStats) -> dict[tuple[int, int], _Found]:
    best = _pick(found)
    stats.duplicates += len(found) - len(best)
    return best


@dataclass
class _Uncarried:
    """Folders that hold one uncarried show: same title and same `(year)` in the name."""

    title: str
    year: int | None
    folders: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    found: list[_Found] = field(default_factory=list)


@dataclass
class _Carried:
    """A carried show's folders, in every root."""

    folders: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    found: list[_Found] = field(default_factory=list)

    def year(self) -> int | None:
        for folder in self.folders:
            year = show_year(folder.name, [])
            if year is not None:
                return year
        return show_year("", [f.name for f in self.files])


def _store(
    db: sqlite3.Connection,
    show: str,
    title: str,
    year: int | None,
    best: dict[tuple[int, int], _Found],
    now: datetime | None,
    probe: SourceProbe | None = None,
) -> None:
    known = {
        (r["season"], r["episode"]): (r["file_path"], r["file_size"], r["source_seconds"])
        for r in db.execute(
            "SELECT season, episode, file_path, file_size, source_seconds FROM episodes"
            " WHERE show_id = ?",
            (show,),
        )
    }
    measured = {
        key: _measure(probe, pick.path, pick.size, known.get(key)) for key, pick in best.items()
    }
    with transaction(db):
        db.execute(
            "INSERT INTO shows (id, title, year, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (id) DO UPDATE SET year = COALESCE(shows.year, excluded.year)",
            (show, title, year, now.isoformat() if now else None),
        )
        for (season, episode), pick in sorted(best.items()):
            db.execute(
                """INSERT INTO episodes (show_id, season, episode, title, file_path, file_size,
                     source_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (show_id, season, episode) DO UPDATE SET
                     title = CASE WHEN episodes.title = '' THEN excluded.title
                             ELSE episodes.title END,
                     file_path = excluded.file_path, file_size = excluded.file_size,
                     source_seconds = excluded.source_seconds, source_deleted_at = NULL""",
                (show, season, episode, pick.title, str(pick.path), pick.size,
                 measured[(season, episode)]),
            )  # fmt: skip


def _previous_id(db: sqlite3.Connection, folders: list[Path], skip: set[str]) -> str | None:
    """The uncarried id an earlier scan gave these folders, so ids stay put across re-scans."""
    for folder in folders:
        prefix = str(folder) + "/"
        for r in db.execute(
            "SELECT show_id, COUNT(*) AS n FROM episodes WHERE substr(file_path, 1, ?) = ?"
            " GROUP BY show_id ORDER BY n DESC, show_id",
            (len(prefix), prefix),
        ):
            if r["show_id"] not in skip:
                return str(r["show_id"])
    return None


def _new_id(title: str, year: int | None, taken: set[str]) -> tuple[str, str]:
    """(id, title) for a show seen for the first time: the title's slug, else with its year
    (`doctor-who-1963`, titled `Doctor Who (1963)`), else numbered."""
    base = _slug(title)
    if base not in taken:
        return base, title
    if year is not None and f"{base}-{year}" not in taken:
        return f"{base}-{year}", f"{title} ({year})"
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}", f"{title} ({n})"


def scan_tv(
    db: sqlite3.Connection,
    root: Roots,
    catalog: Catalog,
    now: datetime | None = None,
    *,
    carried_only: bool = False,
    probe: SourceProbe | None = None,
) -> ScanStats:
    """Attach every show's episode files under `root` (one root, or several read as one
    library: see the module docstring). Air dates are left alone.

    Folders that match a carried show attach to its catalog id. The rest are stored as
    uncarried shows (in the library, on no channel) so the admin can build channels from them,
    unless `carried_only` is set or overrides.yaml excludes the title. Their ids are slugs of
    the folder title and stay the same across re-scans.

    With `probe`, each new or changed file's length is measured (source_seconds), so the
    scheduler never has to fall back on a runtime class default.
    """
    stats = ScanStats()
    index, titles = _Index(catalog, "show"), _titles(catalog)
    excluded = _excluded(catalog)
    carried = {c.show for c in catalog.carriage.carriage}
    seen: set[tuple[str, str]] = set()
    groups: dict[tuple[str, int | None], _Uncarried] = {}
    shows: dict[str, _Carried] = {}
    roots = _present(root, stats.missing_roots)
    for rank, top in enumerate(roots):
        for folder in _folders(top):
            slug = index.match(folder.name)
            if slug is None and (
                carried_only
                or any(k in excluded or k.replace(" ", "") in excluded for k in _keys(folder.name))
            ):
                stats.unmatched_folders.append(folder.name)
                continue
            files, found = _collect(folder, top, stats, rank)
            if slug is None:
                if not found:
                    stats.unmatched_folders.append(folder.name)
                    continue
                # Release folders of one show (`Russian Doll`, `Russian.Doll.S01E01.720p...`)
                # merge; a `(year)` in the name keeps remakes apart (`Doctor Who (1963)`).
                title, folder_year = show_title(folder.name), show_year(folder.name, [])
                key = (_slug(title), folder_year)
                group = groups.setdefault(key, _Uncarried(title, folder_year))
                group.folders.append(folder)
                group.files += files
                group.found += found
                continue
            show = shows.setdefault(slug, _Carried())
            show.folders.append(folder)
            show.files += files
            show.found += found

    for slug, show in shows.items():
        best = _best(show.found, stats)
        _store(db, slug, titles.get(slug, show.folders[0].name), show.year(), best, now, probe)
        seen |= {(slug, str(f.path)) for f in best.values()}
        stats.shows += 1
        stats.episodes += len(best)

    # Uncarried shows: reuse the id an earlier scan gave their folders, then name the new ones.
    pending = list(groups.values())
    ids: dict[int, str] = {}
    for i, group in enumerate(pending):
        previous = _previous_id(db, group.folders, carried | set(ids.values()))
        if previous is not None:
            ids[i] = previous
    taken = carried | {r["id"] for r in db.execute("SELECT id FROM shows")} | set(ids.values())
    for i, group in enumerate(pending):
        year = group.year or show_year("", [f.name for f in group.files])
        if i in ids:
            row = db.execute("SELECT title FROM shows WHERE id = ?", (ids[i],)).fetchone()
            title = row["title"] if row else group.title
        else:
            ids[i], title = _new_id(group.title, group.year, taken)
            taken.add(ids[i])
        best = _best(group.found, stats)
        _store(db, ids[i], title, year, best, now, probe)
        seen |= {(ids[i], str(f.path)) for f in best.values()}
        stats.uncarried += [f.name for f in group.folders]
        stats.uncarried_episodes += len(best)

    # Files under a scanned root that weren't found again for the same show are detached (a
    # file that moved to a carried show leaves its old uncarried row); the rows themselves
    # stay. Only roots that are there: a missing one says nothing about its files.
    prefixes = tuple(str(top).rstrip("/") + "/" for top in roots)
    with transaction(db):
        for r in db.execute(
            "SELECT id, show_id, file_path FROM episodes WHERE file_path IS NOT NULL"
        ).fetchall():
            if r["file_path"].startswith(prefixes) and (r["show_id"], r["file_path"]) not in seen:
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
    missing_roots: list[str] = field(default_factory=list)


def scan_movies(
    db: sqlite3.Connection,
    root: Roots,
    catalog: Catalog,
    now: datetime | None = None,
    *,
    probe: SourceProbe | None = None,
    extra: Iterable[Mapping[str, Any]] = (),
) -> MovieScanStats:
    """Attach carried movies from `<root>/<Title (Year)>/` (or a release-named folder, see
    `movie_title`): the biggest video file that isn't a trailer or extra, from the earliest
    root that has one. `extra` is the mini's /api/admin/library items: movies on its /admin
    channels are carried too, under its ids. The premiere date is left alone (the year
    bounds it, see load_library)."""
    stats = MovieScanStats()
    more = {
        str(i["id"]): str(i.get("title") or i["id"])
        for i in extra
        if i.get("id") and i.get("channels") and i.get("kind") == "movie"
    }
    index, titles = _Index(catalog, "movie", more), more | _titles(catalog)
    picks: dict[str, tuple[tuple[int, int, str], Path, Path]] = {}
    for rank, top in enumerate(_present(root, stats.missing_roots)):
        for folder in _folders(top):
            slug = index.match(folder.name) or index.match(movie_title(folder.name))
            if slug is None:
                stats.unmatched_folders.append(folder.name)
                continue
            for f in _video_files(folder):
                if not EXTRA.search(f.stem):
                    key = (rank, -f.stat().st_size, str(f))
                    if slug not in picks or key < picks[slug][0]:
                        picks[slug] = (key, f, folder)
    for slug, (_, best, folder) in picks.items():
        year = show_year(folder.name, [])
        size = best.stat().st_size
        row = db.execute(
            "SELECT file_path, file_size, source_seconds FROM movies WHERE id = ?", (slug,)
        ).fetchone()
        known = (row["file_path"], row["file_size"], row["source_seconds"]) if row else None
        db.execute(
            """INSERT INTO movies (id, title, year, file_path, file_size, source_seconds,
                 updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET year = COALESCE(movies.year, excluded.year),
                 file_path = excluded.file_path, file_size = excluded.file_size,
                 source_seconds = excluded.source_seconds, source_deleted_at = NULL""",
            (slug, titles.get(slug, folder.name), year, str(best), size,
             _measure(probe, best, size, known), now.isoformat() if now else None),
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
