"""Ask for carried titles that have no files: `python -m retro_cable.ingest acquire` (M4).

The rig runs it after the scan and before normalize, only with RC_ACQUIRE=1. A show some
channel carries (YAML carriage, the /admin overlay here, and with RC_PRIORITY_URL the shows
on the mini's /admin channels) that has no source file in any scanned root and no encode is
added to sonarr-livetv; such a movie to radarr-livetv. Those two are the NAS's Live TV twins
of the Plex instances: they download into the LiveTV share (`sources/tv`, `sources/movies`),
which Plex never sees, and they are not Seerr servers. Once an encode is whole, normalize
deletes the download (see normalize.delete_sources_under), and "unmonitor deleted episodes"
keeps them from fetching it again.

Rules:
- A title is identified by an id: the TMDB id the catalog has from Brandon's lineup
  (overrides.yaml `tmdb_id`, the admin overlay's ids, or the mini's library item; `tv:N` for
  a show, `movie:N` for a movie, the other kind is ignored), else one the library has
  (TheTVDB or TMDB, from a Plex import). Failing that, by a lookup on its title and
  first-aired year (the lineup's `year` first, then the library's) that has exactly one
  exact match. Without a year, or with none or several matches, it is reported as
  unresolved: never a guess.
- A show whose title an uncarried show with files has, or has plus a qualifier (`Justice
  League (Unlimited)` or `Justice League 2001` for `Justice League`), is probably on the NAS
  in a folder the scan couldn't match: it is reported, not requested. More words make another
  show (`House Of The Dragon` is not `House`).
- A TV movie made of a series' episodes (overrides.yaml `episodes_of`) is never asked of
  Radarr: it comes down with the series, and `ingest assemble` joins it. With the series'
  `tvdb_id` (no channel carries it), the seasons holding those episodes are asked of Sonarr
  as for `part_of`, until any of them has files.
- Titles already in the instance (by tvdbId or tmdbId) are skipped, so a rerun adds nothing.
- An instance whose root folders are not exactly `/tv` (Sonarr) or `/movies` (Radarr) is
  refused before anything is looked up: that is how a Plex instance would look.
- The quality profile is found by name (`LiveTV SD`), never by id.
- Seasons of carried shows that can never air are unmonitored in sonarr-livetv: they began
  after the last simulated day (Dec 31 2010) and the show is on no moved dates (a pretend
  premiere anywhere, or a US debut). Seasons a part_of or episodes_of show needs are kept.
  Sonarr then adds no new seasons of that show, and unfinished downloads holding only those
  seasons are cancelled (see trim_seasons).
- radarr-livetv and sonarr-livetv are asked to search their missing movies and episodes
  again about daily: they search a title only when it is added, and their RSS sync sees
  only new posts.
- `dry_run` only reads (GET); nothing is added, changed, cancelled or searched.

The API keys travel in a header (ingest/http.py) and never appear in output or a repr.
"""

import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Literal

from retro_cable.admin import store
from retro_cable.catalog import Catalog
from retro_cable.catalog.models import TMDB_REF
from retro_cable.catalog.overlay import apply_overlay
from retro_cable.ingest.http import Request
from retro_cable.ingest.plex import normalize_title
from retro_cable.ingest.scan import show_title

SONARR, RADARR = "sonarr-livetv", "radarr-livetv"
PROFILE = "LiveTV SD"
SONARR_ROOT, RADARR_ROOT = "/tv", "/movies"
_YEAR = re.compile(r"\(((?:19|20)\d{2})\)\s*$")
_TMDB = re.compile(TMDB_REF)
# A folder's trailing qualifier: `(Unlimited)`, `[DVD]`, `2018`. More words are another show.
_QUALIFIER = re.compile(r"\s*(\([^()]*\)|\[[^\[\]]*\]|\b(?:19|20)\d{2})\s*$")
_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError)


def _key(title: str) -> str:
    return normalize_title(_YEAR.sub("", title))


def _year_in(title: str | None) -> int | None:
    m = _YEAR.search(title or "")
    return int(m[1]) if m else None


def tmdb_number(ref: object, kind: str) -> int | None:
    """The number of a lineup TMDB id (`tv:N` / `movie:N`) of this kind, else None."""
    if not isinstance(ref, str) or not _TMDB.match(ref):
        return None
    prefix, number = ref.split(":")
    return int(number) if prefix == ("tv" if kind == "show" else "movie") else None


@dataclass(frozen=True)
class Wanted:
    id: str
    title: str
    kind: Literal["show", "movie"]
    year: int | None = None  # first aired (shows) or released (movies)
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    titles: tuple[str, ...] = ()  # other names that count as exact (overrides.yaml plex_title)
    # An uncarried show with files whose title is this one or starts with it (`Justice
    # League (Unlimited)` for `Justice League`): likely this show in a folder the scan
    # couldn't match, so it is not requested.
    on_nas_as: str | None = None
    # overrides.yaml part_of: (the series' TheTVDB id, its seasons this show is)
    part_of: tuple[int, tuple[int, ...]] | None = None

    def keys(self) -> set[str]:
        return {_key(t) for t in (self.title, *self.titles)}


def _on_nas_as(keys: set[str], others: Mapping[str, str]) -> str | None:
    for _, title in sorted(others.items()):
        if _key(_QUALIFIER.sub("", title)) in keys:
            return title
    return None


def wanted(
    db: sqlite3.Connection, catalog: Catalog, extra: Iterable[Mapping[str, Any]] = ()
) -> tuple[list[Wanted], int, list[str]]:
    """(carried titles with no source file and no encode, by id; how many carried titles
    have files or encodes; carried ids overrides.yaml excludes). `extra` is the mini's
    /api/admin/library items; those on a channel count as carried."""
    effective = apply_overlay(catalog, store.head(db).overlay)
    excluded_ids = {o.show for o in effective.overrides.shows if o.exclude}
    plex_titles = {o.show: o.plex_title for o in effective.overrides.shows if o.plex_title}
    lineup = {o.show: (o.tmdb_id, o.year) for o in effective.overrides.shows}
    part_of = {o.show: o.part_of for o in effective.overrides.shows if o.part_of}
    from_episodes = {o.show: o.episodes_of for o in effective.overrides.shows if o.episodes_of}
    carried: dict[str, tuple[str, Literal["show", "movie"]]] = {}
    for c in effective.carriage.carriage:
        carried.setdefault(c.show, (c.title, c.kind))
    mini_years: dict[str, int] = {}
    mini_files: set[str] = set()
    for item in extra:
        ident = str(item.get("id") or "")
        if not ident or not item.get("channels"):
            continue
        kind: Literal["show", "movie"] = "movie" if item.get("kind") == "movie" else "show"
        carried.setdefault(ident, (str(item.get("title") or ident), kind))
        if isinstance(item.get("first_year"), int):
            mini_years[ident] = item["first_year"]
        if ident not in lineup:  # the mini's effective overrides: the builder's ids too
            year = item.get("year")
            lineup[ident] = (item.get("tmdb_id"), year if isinstance(year, int) else None)
        if isinstance(item.get("episodes"), int) and item["episodes"] > 0:
            mini_files.add(ident)

    has = "file_path IS NOT NULL OR media_path IS NOT NULL"
    seasons_with = {
        (r[0], r[1])
        for r in db.execute(f"SELECT DISTINCT show_id, season FROM episodes WHERE {has}")
    }
    shows_with = {show for show, _ in seasons_with}
    movies_with = {r[0] for r in db.execute(f"SELECT id FROM movies WHERE {has}")}
    shows = {r["id"]: r for r in db.execute("SELECT id, title, year, tmdb_id, tvdb_id FROM shows")}
    movies = {r["id"]: r for r in db.execute("SELECT id, year, tmdb_id FROM movies")}
    uncarried = {
        _key(shows[i]["title"]): shows[i]["title"]
        for i in shows_with
        if i not in carried and i in shows
    }

    found: list[Wanted] = []
    with_files = 0
    excluded: list[str] = []
    for ident in sorted(carried):
        title, kind = carried[ident]
        if ident in excluded_ids:
            excluded.append(ident)
            continue
        part = part_of.get(ident) if kind == "show" else None
        on_disk = (
            any((part.show, s) in seasons_with for s in part.seasons)
            if part is not None
            else ident in (shows_with if kind == "show" else movies_with)
        )
        if ident in mini_files or on_disk:
            with_files += 1
            continue
        if kind == "movie" and ident in from_episodes:  # comes with its series (assemble)
            of = from_episodes[ident]
            seasons = tuple(sorted({int(e[1:].split("E")[0]) for e in of.episodes}))
            if of.tvdb_id is not None and not any((of.show, s) in seasons_with for s in seasons):
                # A series no channel carries: its seasons are asked of Sonarr, like part_of.
                found.append(Wanted(ident, title, "show", part_of=(of.tvdb_id, seasons)))
            continue
        if part is not None:  # asked for by season from the series, never by its own title
            found.append(Wanted(ident, title, kind, part_of=(part.tvdb_id, tuple(part.seasons))))
            continue
        row = (shows if kind == "show" else movies).get(ident)
        plex_title = plex_titles.get(ident)
        ref, lineup_year = lineup.get(ident, (None, None))
        year = (
            lineup_year
            or (row["year"] if row is not None else None)
            or _year_in(plex_title)
            or _year_in(title)
            or mini_years.get(ident)
        )
        w = Wanted(
            ident,
            title,
            kind,
            year,
            tmdb_id=tmdb_number(ref, kind) or (row["tmdb_id"] if row is not None else None),
            tvdb_id=row["tvdb_id"] if row is not None and kind == "show" else None,
            titles=(plex_title,) if plex_title else (),
        )
        if kind == "show":
            w = replace(w, on_nas_as=_on_nas_as(w.keys(), uncarried))
        found.append(w)
    return found, with_files, excluded


@dataclass
class InstanceReport:
    instance: str
    error: str | None = None  # nothing was added: unset, refused or unreachable
    added: list[dict[str, Any]] = field(default_factory=list)  # in a dry run: would add
    present: list[str] = field(default_factory=list)  # already in the instance
    unresolved: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)  # the add was refused
    # Seasons unmonitored because they can never air (sonarr-livetv; see trim_seasons): id,
    # title, seasons and (not in a dry run) how many unfinished downloads were cancelled.
    trimmed: list[dict[str, Any]] = field(default_factory=list)
    searched_missing: bool = False  # asked to search its missing movies or episodes again


@dataclass
class AcquireReport:
    dry_run: bool
    with_files: int  # carried titles that already have files or encodes
    excluded: list[str]
    shows: InstanceReport
    movies: InstanceReport

    @property
    def ok(self) -> bool:
        return self.shows.error is None and self.movies.error is None


Resolved = tuple[dict[str, Any], str] | str  # (the instance's record, how) or why not


def _unique(hits: list[dict[str, Any]], how: str, w: Wanted) -> Resolved:
    if len(hits) == 1:
        return hits[0], how
    if not hits:
        return f"no exact match for {w.title} ({w.year})"
    return f"{len(hits)} exact matches for {w.title} ({w.year})"


def _by_title(request: Request, path: str, id_field: str, w: Wanted) -> Resolved:
    if w.year is None:
        return "no first-aired year known"
    results = request("GET", path, {"term": w.title}, None) or []
    keys = w.keys()
    hits = [
        r
        for r in results
        if isinstance(r, dict)
        and r.get(id_field)
        and _key(str(r.get("title", ""))) in keys
        and r.get("year") == w.year
    ]
    return _unique(hits, "title+year", w)


def resolve_series(request: Request, w: Wanted) -> Resolved:
    for value, name in ((w.tvdb_id, "tvdb"), (w.tmdb_id, "tmdb")):
        if not value:
            continue
        results = request("GET", "/api/v3/series/lookup", {"term": f"{name}:{value}"}, None)
        # Only a result with the same id: Sonarr answers a `tmdb:N` it can't map with the
        # series whose TheTVDB id is N (and TMDB id 0).
        own = "tvdbId" if name == "tvdb" else "tmdbId"
        hits = [
            r
            for r in results or []
            if isinstance(r, dict) and r.get("tvdbId") and r.get(own) == value
        ]
        if len(hits) == 1:
            return hits[0], name
    return _by_title(request, "/api/v3/series/lookup", "tvdbId", w)


def resolve_movie(request: Request, w: Wanted) -> Resolved:
    if w.tmdb_id:
        try:
            hit = request("GET", "/api/v3/movie/lookup/tmdb", {"tmdbId": str(w.tmdb_id)}, None)
        except OSError:  # 404: not a TMDB movie id after all
            hit = None
        if isinstance(hit, dict) and hit.get("tmdbId") == w.tmdb_id:
            return hit, "tmdb"
    return _by_title(request, "/api/v3/movie/lookup", "tmdbId", w)


def series_body(record: Mapping[str, Any], profile: int) -> dict[str, Any]:
    body = {k: v for k, v in record.items() if k != "id"}
    return body | {
        "qualityProfileId": profile,
        "rootFolderPath": SONARR_ROOT,
        "monitored": True,
        "seasonFolder": True,
        "addOptions": {
            "monitor": "all",
            "searchForMissingEpisodes": True,
            "searchForCutoffUnmetEpisodes": False,
        },
    }


def movie_body(record: Mapping[str, Any], profile: int) -> dict[str, Any]:
    body = {k: v for k, v in record.items() if k != "id"}
    return body | {
        "qualityProfileId": profile,
        "rootFolderPath": RADARR_ROOT,
        "monitored": True,
        "minimumAvailability": "released",
        "addOptions": {"searchForMovie": True, "monitor": "movieOnly"},
    }


@dataclass(frozen=True)
class _Instance:
    name: str
    env: str
    root: str
    resource: str  # /api/v3/<resource>: list and add
    id_field: str
    report_id: str
    resolve: Callable[[Request, Wanted], Resolved]
    body: Callable[[Mapping[str, Any], int], dict[str, Any]]


SONARR_API = _Instance(SONARR, "RC_SONARR_LIVETV", SONARR_ROOT, "series", "tvdbId", "tvdb_id",
                       resolve_series, series_body)  # fmt: skip
RADARR_API = _Instance(RADARR, "RC_RADARR_LIVETV", RADARR_ROOT, "movie", "tmdbId", "tmdb_id",
                       resolve_movie, movie_body)  # fmt: skip


def _add(
    api: _Instance,
    request: Request | None,
    items: list[Wanted],
    report: InstanceReport,
    dry_run: bool,
) -> None:
    if not items:
        return  # nothing wanted: the instance isn't asked anything
    if request is None:
        report.error = f"set {api.env}_URL and {api.env}_KEY"
        return
    try:
        roots = sorted(
            str(r.get("path", "")).rstrip("/") or "/"
            for r in request("GET", "/api/v3/rootfolder", None, None) or []
        )
        if roots != [api.root]:
            report.error = (
                f"{api.name}'s root folders are {roots}, not exactly ['{api.root}']: refusing"
                " to add anything (a Plex instance?)"
            )
            return
        profiles = request("GET", "/api/v3/qualityprofile", None, None) or []
        profile = next((int(p["id"]) for p in profiles if p.get("name") == PROFILE), None)
        if profile is None:
            report.error = f"{api.name} has no quality profile named '{PROFILE}'"
            return
        existing = request("GET", f"/api/v3/{api.resource}", None, None) or []
        have = {int(x.get(api.id_field) or 0) for x in existing} - {0}
        records = {int(x.get(api.id_field) or 0): x for x in existing}
    except _ERRORS as err:
        report.error = f"{api.name}: {err}"
        return

    for w in items:
        if w.part_of is not None:
            _add_seasons(request, w, report, dry_run, profile, records)
            continue
        if w.on_nas_as is not None:
            report.unresolved.append({"id": w.id, "title": w.title, "reason": (
                f"may be on the NAS already as '{w.on_nas_as}': give it a plex_title in"
                f" overrides.yaml, or add it to {api.name} by hand")})  # fmt: skip
            continue
        try:
            resolved = api.resolve(request, w)
        except _ERRORS as err:
            report.unresolved.append({"id": w.id, "title": w.title, "reason": f"lookup: {err}"})
            continue
        if isinstance(resolved, str):
            report.unresolved.append({"id": w.id, "title": w.title, "reason": resolved})
            continue
        record, how = resolved
        ident = int(record[api.id_field])
        if ident in have:
            report.present.append(w.id)
            continue
        entry = {
            "id": w.id,
            "title": w.title,
            "found": f"{record.get('title')} ({record.get('year')})",
            api.report_id: ident,
            "matched_on": how,
        }
        if not dry_run:
            try:
                made = request("POST", f"/api/v3/{api.resource}", None, api.body(record, profile))
            except _ERRORS as err:
                report.failed.append({"id": w.id, "title": w.title, "reason": str(err)})
                continue
            if isinstance(made, dict):
                records[ident] = made
        report.added.append(entry)
        have.add(ident)


def _add_seasons(
    request: Request,
    w: Wanted,
    report: InstanceReport,
    dry_run: bool,
    profile: int,
    records: dict[int, Any],
) -> None:
    """A show that is seasons of another series (overrides.yaml part_of): add the series if
    Sonarr hasn't got it, with nothing monitored and no search, then monitor and search just
    the missing seasons. Already monitored: present."""
    assert w.part_of is not None
    tvdb, seasons = w.part_of
    record = records.get(tvdb)
    new = record is None
    try:
        if record is None:
            hits = [
                r
                for r in request("GET", "/api/v3/series/lookup", {"term": f"tvdb:{tvdb}"}, None)
                or []
                if isinstance(r, dict) and r.get("tvdbId") == tvdb
            ]
            if len(hits) != 1:
                report.unresolved.append({"id": w.id, "title": w.title,
                                          "reason": f"no TheTVDB series {tvdb}"})  # fmt: skip
                return
            record = hits[0]
    except _ERRORS as err:
        report.unresolved.append({"id": w.id, "title": w.title, "reason": f"lookup: {err}"})
        return
    monitored = {s.get("seasonNumber") for s in record.get("seasons", []) if s.get("monitored")}
    missing = list(seasons) if new else [s for s in seasons if s not in monitored]
    if not new and not missing and record.get("monitored"):
        report.present.append(w.id)
        return
    entry = {
        "id": w.id,
        "title": w.title,
        "found": f"{record.get('title')} ({record.get('year')}) seasons"
        f" {', '.join(map(str, seasons))}",
        "tvdb_id": tvdb,
        "matched_on": "part_of",
    }
    if not dry_run:
        try:
            if new:
                body = series_body(record, profile)
                body["addOptions"] = body["addOptions"] | {
                    "monitor": "none",
                    "searchForMissingEpisodes": False,
                }
                record = request("POST", "/api/v3/series", None, body)
            assert isinstance(record, dict)
            listed = {s.get("seasonNumber") for s in record.get("seasons", [])}
            record = record | {
                "monitored": True,
                "seasons": [
                    # A new series starts from nothing: Sonarr's own add marks every season.
                    s
                    | {
                        "monitored": (not new and bool(s.get("monitored")))
                        or s.get("seasonNumber") in seasons
                    }
                    for s in record.get("seasons", [])
                ]
                + [{"seasonNumber": s, "monitored": True} for s in seasons if s not in listed],
            }
            record = request("PUT", f"/api/v3/series/{record['id']}", None, record) or record
            for season in missing:
                search = {"name": "SeasonSearch", "seriesId": record["id"], "seasonNumber": season}
                request("POST", "/api/v3/command", None, search)
        except (*_ERRORS, AssertionError) as err:
            report.failed.append({"id": w.id, "title": w.title, "reason": str(err)})
            return
        records[tvdb] = record
    report.added.append(entry)


@dataclass(frozen=True)
class Airable:
    """What decides which seasons of a sonarr-livetv series can ever air (`trim_seasons`)."""

    last_day: date  # the last simulated day (channels.yaml year_range)
    # Title key -> carried show ids and first-aired years (carriage, plex_title, the mini):
    # a remake shares its title (DuckTales 1987 and 2017), so the year tells them apart.
    shows: Mapping[str, tuple[tuple[str, int | None], ...]]
    shifted: frozenset[str]  # show ids on moved dates somewhere: a pretend premiere, a US debut
    claimed: Mapping[str, frozenset[int]]  # series id -> seasons part_of/episodes_of need
    claimed_tvdb: Mapping[int, frozenset[int]]  # the same, by the series' TheTVDB id

    def show(self, keys: Iterable[str], year: int | None) -> str | None:
        """The carried show a series is: by title, then by year (within one). A lone show
        with no known year matches by title alone; otherwise none, never a guess."""
        for key in keys:
            found = self.shows.get(key, ())
            for slack in (0, 1):
                near = [i for i, y in found if y is not None and year and abs(y - year) <= slack]
                if near:
                    return near[0]
            if len(found) == 1 and (found[0][1] is None or not year):
                return found[0][0]
        return None


def airable(
    db: sqlite3.Connection, catalog: Catalog, extra: Iterable[Mapping[str, Any]] = ()
) -> Airable:
    effective = apply_overlay(catalog, store.head(db).overlay)
    years = {o.show: o.year for o in effective.overrides.shows if o.year}
    shows: dict[str, list[tuple[str, int | None]]] = {}

    def carried(title: str, ident: str, year: int | None) -> None:
        found = shows.setdefault(_key(title), [])
        if all(i != ident for i, _ in found):
            found.append((ident, years.get(ident) or _year_in(title) or year))

    shifted = {p.show for p in effective.synthetic.synthetic}
    for c in effective.carriage.carriage:
        if c.kind == "show":
            carried(c.title, c.show, None)
    for item in extra:  # the mini's /admin channels and its pretend premieres
        ident = str(item.get("id") or "")
        if ident and item.get("channels") and item.get("kind", "show") == "show":
            year = item.get("year") or item.get("first_year")
            carried(str(item.get("title") or ident), ident, year if isinstance(year, int) else None)
            if item.get("shifted"):
                shifted.add(ident)
    claimed: dict[str, set[int]] = {}
    claimed_tvdb: dict[int, set[int]] = {}
    for o in effective.overrides.shows:
        if o.plex_title:
            carried(o.plex_title, o.show, None)
        if o.us_debut:
            shifted.add(o.show)
        if o.part_of is not None:
            claimed.setdefault(o.part_of.show, set()).update(o.part_of.seasons)
            claimed_tvdb.setdefault(o.part_of.tvdb_id, set()).update(o.part_of.seasons)
        if o.episodes_of is not None:
            seasons = {int(e[1:].split("E")[0]) for e in o.episodes_of.episodes}
            claimed.setdefault(o.episodes_of.show, set()).update(seasons)
            if o.episodes_of.tvdb_id is not None:
                claimed_tvdb.setdefault(o.episodes_of.tvdb_id, set()).update(seasons)
    return Airable(
        date(effective.channels.year_range[1], 12, 31),
        {k: tuple(v) for k, v in shows.items()},
        frozenset(shifted),
        {k: frozenset(v) for k, v in claimed.items()},
        {k: frozenset(v) for k, v in claimed_tvdb.items()},
    )


def _queue(request: Request) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page = 1
    while True:
        params = {"page": str(page), "pageSize": "500", "includeEpisode": "true"}
        got = request("GET", "/api/v3/queue", params, None)
        batch = (got or {}).get("records") or []
        records += batch
        if not batch or len(records) >= int((got or {}).get("totalRecords") or 0):
            return records
        page += 1


def _cancel(request: Request, queue: list[dict[str, Any]], series: int, late: set[int]) -> int:
    """Cancel this series' unfinished downloads that hold only `late` seasons."""
    downloads: dict[str, list[dict[str, Any]]] = {}
    for r in queue:
        if r.get("seriesId") == series:
            downloads.setdefault(str(r.get("downloadId") or r.get("id")), []).append(r)
    cancelled = 0
    for records in downloads.values():
        seasons = {r.get("seasonNumber", (r.get("episode") or {}).get("seasonNumber"))
                   for r in records}  # fmt: skip
        unfinished = all(r.get("trackedDownloadState") in (None, "downloading")
                         and r.get("status") != "completed" for r in records)  # fmt: skip
        if seasons <= late and unfinished:
            request("DELETE", f"/api/v3/queue/{records[0]['id']}",
                    {"removeFromClient": "true", "blocklist": "false"}, None)  # fmt: skip
            cancelled += 1
    return cancelled


def trim_seasons(request: Request, rules: Airable, report: InstanceReport, dry_run: bool) -> None:
    """Unmonitor the seasons of carried shows in sonarr-livetv that can never air: they
    began after the last simulated day and the show isn't on moved dates anywhere (a pretend
    premiere or a US debut could bring them into range). Seasons a part_of or episodes_of
    show needs are kept. Sonarr then adds no new seasons of that show either, and unfinished
    downloads holding only those seasons are cancelled. Series no channel carries are left
    alone. A season with no air dates is kept."""
    try:
        roots = sorted(
            str(r.get("path", "")).rstrip("/") or "/"
            for r in request("GET", "/api/v3/rootfolder", None, None) or []
        )
        if roots != [SONARR_ROOT]:
            report.error = report.error or f"{SONARR}'s root folders are {roots}: not trimming"
            return
        listed = request("GET", "/api/v3/series", None, None) or []
    except _ERRORS as err:
        report.error = report.error or f"{SONARR}: {err}"
        return
    last = rules.last_day.isoformat()
    queue: list[dict[str, Any]] | None = None
    for record in listed:
        folder = PurePosixPath(str(record.get("path") or "")).name
        keys = (
            [_key(str(record.get("title") or "")), _key(show_title(folder))]
            if folder
            else [_key(str(record.get("title") or ""))]
        )
        year = record.get("year")
        ident = rules.show(keys, year if isinstance(year, int) else None)
        if ident is None or ident in rules.shifted:
            continue
        keep = rules.claimed.get(ident, frozenset()) | rules.claimed_tvdb.get(
            int(record.get("tvdbId") or 0), frozenset()
        )
        watched = [
            x.get("seasonNumber")
            for x in record.get("seasons", [])
            if x.get("monitored") and x.get("seasonNumber") not in keep
        ]
        if not watched:
            continue
        try:
            episodes = request("GET", "/api/v3/episode",
                               {"seriesId": str(record["id"])}, None) or []  # fmt: skip
            began: dict[int, str] = {}
            for e in episodes:
                day, n = e.get("airDate"), e.get("seasonNumber")
                if day and isinstance(n, int):
                    began[n] = min(began.get(n, day), day)
            late = sorted(n for n in watched if isinstance(n, int) and began.get(n, "") > last)
            if not late:
                continue
            entry: dict[str, Any] = {"id": ident, "title": record.get("title"), "seasons": late}
            if not dry_run:
                body = record | {
                    "monitorNewItems": "none",
                    "seasons": [
                        x | {"monitored": False} if x.get("seasonNumber") in late else x
                        for x in record.get("seasons", [])
                    ],
                }
                request("PUT", f"/api/v3/series/{record['id']}", None, body)
                if queue is None:
                    queue = _queue(request)
                entry["cancelled"] = _cancel(request, queue, int(record["id"]), set(late))
        except (*_ERRORS, AssertionError) as err:
            report.failed.append({"id": ident, "title": str(record.get("title")),
                                  "reason": f"trimming seasons: {err}"})  # fmt: skip
            continue
        report.trimmed.append(entry)


SEARCH_EVERY = timedelta(hours=23)  # about daily, with the worker's 6-hour passes
_NO_INDEXERS = re.compile(
    r"^(All (search-capable )?indexers are (temporarily )?unavailable|No indexers available)",
    re.I,
)
_FAILING = re.compile(r"^Indexers unavailable due to failures(?: for more than \d+ hours)?: (.+)$")


@dataclass(frozen=True)
class _Search:
    root: str
    command: dict[str, Any]
    source: str  # import_runs source: when it last searched


_SEARCH = {
    RADARR: _Search(
        RADARR_ROOT,
        {"name": "MissingMoviesSearch", "filterKey": "monitored", "filterValue": "true"},
        "acquire-search-movies",
    ),
    SONARR: _Search(
        SONARR_ROOT, {"name": "MissingEpisodeSearch", "monitored": True}, "acquire-search-episodes"
    ),
}


def search_missing(
    db: sqlite3.Connection, request: Request, report: InstanceReport, now: datetime
) -> None:
    """Ask the instance to search all its missing monitored movies or episodes again, at
    most daily. Radarr and Sonarr search a title once, when it is added, and their RSS sync
    only sees new posts, so a release that was missing then (or had no seeders, or came
    while every indexer was failing) is otherwise never found."""
    search = _SEARCH[report.instance]
    last = db.execute(
        "SELECT MAX(started_at) FROM import_runs WHERE source = ?", (search.source,)
    ).fetchone()[0]
    if last is not None and now - datetime.fromisoformat(last) < SEARCH_EVERY:
        return
    try:
        roots = [str(r.get("path", "")).rstrip("/") or "/"
                 for r in request("GET", "/api/v3/rootfolder", None, None) or []]  # fmt: skip
        if roots != [search.root]:
            report.error = (
                report.error or f"{report.instance}'s root folders are {roots}: not searching"
            )
            return
        # The arrs back off from failing indexers; with none left a search finds nothing.
        health = [
            str(h.get("message", "")) for h in request("GET", "/api/v3/health", None, None) or []
        ]
        if any(_NO_INDEXERS.search(m) for m in health):
            return
        down = {n.strip() for m in health if (f := _FAILING.match(m)) for n in f[1].split(",")}
        if down:
            indexers = request("GET", "/api/v3/indexer", None, None) or []
            searching = {str(i.get("name")) for i in indexers if i.get("enableAutomaticSearch")}
            if searching <= down:
                return
        request("POST", "/api/v3/command", None, search.command)
    except _ERRORS as err:
        report.failed.append({"id": "missing", "title": "", "reason": f"search: {err}"})
        return
    stamp = now.isoformat()
    db.execute(
        "INSERT INTO import_runs (source, started_at, finished_at, stats) VALUES (?, ?, ?, '{}')",
        (search.source, stamp, stamp),
    )
    db.commit()
    report.searched_missing = True


def acquire(
    db: sqlite3.Connection,
    catalog: Catalog,
    *,
    sonarr: Request | None,
    radarr: Request | None,
    dry_run: bool,
    extra: Iterable[Mapping[str, Any]] = (),
    now: datetime | None = None,
) -> AcquireReport:
    """Add every carried title with no files to sonarr-livetv or radarr-livetv (see the
    module docstring). An instance that is None is unset (RC_*_LIVETV_URL and _KEY)."""
    found, with_files, excluded = wanted(db, catalog, extra)
    report = AcquireReport(dry_run, with_files, excluded, InstanceReport(SONARR),
                           InstanceReport(RADARR))  # fmt: skip
    _add(SONARR_API, sonarr, [w for w in found if w.kind == "show"], report.shows, dry_run)
    _add(RADARR_API, radarr, [w for w in found if w.kind == "movie"], report.movies, dry_run)
    if sonarr is not None and report.shows.error is None:
        trim_seasons(sonarr, airable(db, catalog, extra), report.shows, dry_run)
    for request, instance in ((sonarr, report.shows), (radarr, report.movies)):
        if request is not None and instance.error is None and now is not None and not dry_run:
            search_missing(db, request, instance, now)
    return report
