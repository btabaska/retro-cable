"""Import shows, episodes and movies from Plex into the catalog database.

Plex is a metadata source only: titles, external ids, original air dates, runtimes and file
paths. Only titles carried in catalog/carriage.yaml are imported; the rest are reported.
"""

import json
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from retro_cable.catalog import Catalog
from retro_cable.db import transaction
from retro_cable.ingest.http import Fetch, json_fetch


@dataclass(frozen=True)
class Section:
    key: str
    type: str
    title: str


@dataclass(frozen=True)
class Ids:
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None


@dataclass(frozen=True)
class PlexShow(Ids):
    rating_key: str = ""
    title: str = ""
    year: int | None = None
    summary: str | None = None


@dataclass(frozen=True)
class PlexEpisode:
    rating_key: str
    season: int
    episode: int
    title: str
    air_date: date | None
    runtime_seconds: int | None
    summary: str | None
    file_path: str | None
    file_size: int | None


@dataclass(frozen=True)
class PlexMovie(Ids):
    rating_key: str = ""
    title: str = ""
    year: int | None = None
    premiere_date: date | None = None
    runtime_seconds: int | None = None
    summary: str | None = None
    file_path: str | None = None
    file_size: int | None = None


def _ids(item: dict[str, Any]) -> Ids:
    found: dict[str, str] = {}
    for guid in item.get("Guid", []):
        scheme, _, value = str(guid.get("id", "")).partition("://")
        found[scheme] = value
    tmdb, tvdb = found.get("tmdb"), found.get("tvdb")
    return Ids(
        tmdb_id=int(tmdb) if tmdb and tmdb.isdigit() else None,
        tvdb_id=int(tvdb) if tvdb and tvdb.isdigit() else None,
        imdb_id=found.get("imdb"),
    )


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _seconds(ms: Any) -> int | None:
    return round(ms / 1000) if isinstance(ms, int | float) and ms > 0 else None


def _part(item: dict[str, Any]) -> tuple[str | None, int | None]:
    for media in item.get("Media", []):
        for part in media.get("Part", []):
            if part.get("file"):
                return part["file"], part.get("size")
    return None, None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


class PlexClient:
    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch

    def _metadata(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        container = self._fetch(path, params).get("MediaContainer", {})
        items: list[dict[str, Any]] = container.get("Metadata", [])
        return items

    def sections(self) -> list[Section]:
        container = self._fetch("/library/sections", None).get("MediaContainer", {})
        return [
            Section(str(d["key"]), d["type"], d.get("title", ""))
            for d in container.get("Directory", [])
            if d.get("type") in ("show", "movie")
        ]

    def shows(self, section_key: str) -> list[PlexShow]:
        items = self._metadata(
            f"/library/sections/{section_key}/all", {"type": "2", "includeGuids": "1"}
        )
        return [
            PlexShow(
                **asdict(_ids(i)),
                rating_key=str(i["ratingKey"]),
                title=i.get("title", ""),
                year=i.get("year"),
                summary=_text(i.get("summary")),
            )
            for i in items
        ]

    def episodes(self, show_rating_key: str) -> list[PlexEpisode]:
        episodes = []
        for i in self._metadata(f"/library/metadata/{show_rating_key}/allLeaves"):
            if "index" not in i or "parentIndex" not in i:
                continue  # extras and unmatched files have no episode number
            path, size = _part(i)
            episodes.append(
                PlexEpisode(
                    rating_key=str(i["ratingKey"]),
                    season=int(i["parentIndex"]),
                    episode=int(i["index"]),
                    title=i.get("title", ""),
                    air_date=_date(i.get("originallyAvailableAt")),
                    runtime_seconds=_seconds(i.get("duration")),
                    summary=_text(i.get("summary")),
                    file_path=path,
                    file_size=size,
                )
            )
        return episodes

    def movies(self, section_key: str) -> list[PlexMovie]:
        movies = []
        for i in self._metadata(f"/library/sections/{section_key}/all", {"includeGuids": "1"}):
            path, size = _part(i)
            movies.append(
                PlexMovie(
                    **asdict(_ids(i)),
                    rating_key=str(i["ratingKey"]),
                    title=i.get("title", ""),
                    year=i.get("year"),
                    premiere_date=_date(i.get("originallyAvailableAt")),
                    runtime_seconds=_seconds(i.get("duration")),
                    summary=_text(i.get("summary")),
                    file_path=path,
                    file_size=size,
                )
            )
        return movies


def http_fetch(base_url: str, token: str, timeout: float = 30) -> Fetch:
    return json_fetch(base_url, {"X-Plex-Token": token}, timeout)


# --- matching ----------------------------------------------------------------


def normalize_title(title: str) -> str:
    """Case-, accent- and punctuation-insensitive key: "Dexter\u2019s Lab!" -> "dexters lab"."""
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().casefold()
    t = t.replace("&", " and ").replace("'", "")
    return " ".join(re.findall(r"[a-z0-9]+", t))


def _without_year(title: str) -> str:
    return re.sub(r"\s*\(\d{4}\)\s*$", "", title)


def _title_index(catalog: Catalog, kind: str) -> dict[str, str]:
    """Normalized title -> catalog slug, for carried items of one kind that aren't excluded."""
    excluded = {o.show for o in catalog.overrides.shows if o.exclude}
    index: dict[str, str] = {}
    for c in catalog.carriage.carriage:
        if c.kind == kind and c.show not in excluded:
            index.setdefault(normalize_title(c.title), c.show)
    for o in catalog.overrides.shows:
        if o.plex_title and not o.exclude:
            index[normalize_title(o.plex_title)] = o.show
    return index


def _match(index: dict[str, str], title: str) -> str | None:
    return index.get(normalize_title(title)) or index.get(normalize_title(_without_year(title)))


# --- import ------------------------------------------------------------------


@dataclass
class PlexImportStats:
    shows: int = 0
    episodes: int = 0
    episodes_without_air_date: int = 0
    movies: int = 0
    unmatched_shows: list[str] = field(default_factory=list)
    unmatched_movies: list[str] = field(default_factory=list)


def _label(title: str, year: int | None) -> str:
    return f"{title} ({year})" if year else title


def _import_show(
    db: sqlite3.Connection, slug: str, show: PlexShow, episodes: list[PlexEpisode], now: str
) -> None:
    with transaction(db):
        db.execute(
            """INSERT INTO shows (id, title, year, plex_rating_key, tmdb_id, tvdb_id, imdb_id,
                 summary, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET
                 title = excluded.title, year = excluded.year,
                 plex_rating_key = excluded.plex_rating_key,
                 tmdb_id = COALESCE(excluded.tmdb_id, shows.tmdb_id),
                 tvdb_id = COALESCE(excluded.tvdb_id, shows.tvdb_id),
                 imdb_id = COALESCE(excluded.imdb_id, shows.imdb_id),
                 summary = COALESCE(excluded.summary, shows.summary),
                 updated_at = excluded.updated_at""",
            (slug, show.title, show.year, show.rating_key, show.tmdb_id, show.tvdb_id,
             show.imdb_id, show.summary, now),
        )  # fmt: skip
        # Files are re-attached below; anything Plex no longer has stays as a known episode.
        db.execute(
            "UPDATE episodes SET plex_rating_key = NULL, file_path = NULL, file_size = NULL"
            " WHERE show_id = ?",
            (slug,),
        )
        for e in episodes:
            db.execute(
                """INSERT INTO episodes (show_id, season, episode, title, air_date, air_date_source,
                     runtime_seconds, summary, plex_rating_key, file_path, file_size)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (show_id, season, episode) DO UPDATE SET
                     title = excluded.title,
                     air_date = COALESCE(excluded.air_date, episodes.air_date),
                     air_date_source = CASE WHEN excluded.air_date IS NULL
                       THEN episodes.air_date_source ELSE 'plex' END,
                     runtime_seconds = COALESCE(excluded.runtime_seconds, episodes.runtime_seconds),
                     summary = COALESCE(excluded.summary, episodes.summary),
                     plex_rating_key = excluded.plex_rating_key,
                     file_path = excluded.file_path, file_size = excluded.file_size""",
                (slug, e.season, e.episode, e.title,
                 e.air_date.isoformat() if e.air_date else None,
                 "plex" if e.air_date else None,
                 e.runtime_seconds, e.summary, e.rating_key, e.file_path, e.file_size),
            )  # fmt: skip


def _import_movie(db: sqlite3.Connection, slug: str, m: PlexMovie, now: str) -> None:
    db.execute(
        """INSERT INTO movies (id, title, year, premiere_date, runtime_seconds, plex_rating_key,
             tmdb_id, imdb_id, summary, file_path, file_size, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET
             title = excluded.title, year = excluded.year,
             premiere_date = COALESCE(excluded.premiere_date, movies.premiere_date),
             runtime_seconds = COALESCE(excluded.runtime_seconds, movies.runtime_seconds),
             plex_rating_key = excluded.plex_rating_key,
             tmdb_id = COALESCE(excluded.tmdb_id, movies.tmdb_id),
             imdb_id = COALESCE(excluded.imdb_id, movies.imdb_id),
             summary = COALESCE(excluded.summary, movies.summary),
             file_path = excluded.file_path, file_size = excluded.file_size,
             updated_at = excluded.updated_at""",
        (slug, m.title, m.year, m.premiere_date.isoformat() if m.premiere_date else None,
         m.runtime_seconds, m.rating_key, m.tmdb_id, m.imdb_id, m.summary, m.file_path,
         m.file_size, now),
    )  # fmt: skip


def import_plex(
    db: sqlite3.Connection, client: PlexClient, catalog: Catalog, now: datetime
) -> PlexImportStats:
    """Import every carried show and movie Plex knows about. `now` stamps the run."""
    stamp = now.isoformat()
    stats = PlexImportStats()
    shows, movies = _title_index(catalog, "show"), _title_index(catalog, "movie")
    for section in client.sections():
        if section.type == "show":
            for show in client.shows(section.key):
                slug = _match(shows, show.title)
                if slug is None:
                    stats.unmatched_shows.append(_label(show.title, show.year))
                    continue
                episodes = client.episodes(show.rating_key)
                _import_show(db, slug, show, episodes, stamp)
                stats.shows += 1
                stats.episodes += len({(e.season, e.episode) for e in episodes})
                stats.episodes_without_air_date += sum(1 for e in episodes if e.air_date is None)
        else:
            for movie in client.movies(section.key):
                slug = _match(movies, movie.title)
                if slug is None:
                    stats.unmatched_movies.append(_label(movie.title, movie.year))
                    continue
                _import_movie(db, slug, movie, stamp)
                stats.movies += 1
    db.execute(
        "INSERT INTO import_runs (source, started_at, finished_at, stats) VALUES (?, ?, ?, ?)",
        ("plex", stamp, stamp, json.dumps(asdict(stats))),
    )
    return stats
