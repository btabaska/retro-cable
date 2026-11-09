"""`ingest acquire`: ask sonarr-livetv and radarr-livetv for carried titles with no files (M4).

Everything here talks to fakes; no test touches the network.
"""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from retro_cable.catalog import load_catalog
from retro_cable.db import connect, migrate
from retro_cable.ingest import cli
from retro_cable.ingest.acquire import Wanted, acquire, wanted
from tests.conftest import MakeCatalog


class FakeArr:
    """A Sonarr or Radarr v3 API: canned GETs, and POSTs recorded (and echoed with an id)."""

    def __init__(
        self,
        kind: str,
        *,
        roots: list[str] | None = None,
        profiles: list[dict[str, Any]] | None = None,
        existing: list[dict[str, Any]] | None = None,
        lookups: Mapping[str, Any] | None = None,
        fail_post: bool = False,
        episodes: Mapping[int, list[dict[str, Any]]] | None = None,
        queue: list[dict[str, Any]] | None = None,
    ) -> None:
        self.kind = kind
        self.roots = roots if roots is not None else (["/tv"] if kind == "series" else ["/movies"])
        self.profiles = profiles if profiles is not None else [
            {"id": 1, "name": "Any"}, {"id": 9, "name": "LiveTV SD"},
        ]  # fmt: skip
        self.existing = existing or []
        self.lookups = dict(lookups or {})
        self.fail_post = fail_post
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.posted: list[dict[str, Any]] = []
        self.put: list[dict[str, Any]] = []
        self.commands: list[dict[str, Any]] = []
        self.episodes = dict(episodes or {})  # series id -> its episodes
        self.queue = list(queue or [])
        self.deleted: list[tuple[str, dict[str, str]]] = []
        self.health: list[dict[str, Any]] = []

    def __call__(
        self, method: str, path: str, params: Mapping[str, str] | None = None, body: Any = None
    ) -> Any:
        q = dict(params or {})
        self.calls.append((method, path, q))
        if method == "POST" and path == "/api/v3/command":
            self.commands.append(body)
            return {**body, "id": 1}
        if method == "PUT":
            self.put.append(body)
            return body
        if method == "DELETE":
            self.deleted.append((path, q))
            return None
        if method == "POST":
            if self.fail_post:
                raise OSError("HTTP Error 400: Bad Request")
            self.posted.append(body)
            return {**body, "id": 100 + len(self.posted)}
        if path == "/api/v3/rootfolder":
            return [{"id": n, "path": p} for n, p in enumerate(self.roots, 1)]
        if path == "/api/v3/qualityprofile":
            return self.profiles
        if path == f"/api/v3/{self.kind}":
            return self.existing
        if path == "/api/v3/health":
            return self.health
        if path == "/api/v3/indexer":
            return [{"name": n, "enableAutomaticSearch": True} for n in ("Bitmagnet", "IPT")]
        if path == "/api/v3/episode":
            return self.episodes.get(int(q["seriesId"]), [])
        if path == "/api/v3/queue":
            page, size = int(q.get("page", "1")), int(q.get("pageSize", "10"))
            return {"totalRecords": len(self.queue),
                    "records": self.queue[(page - 1) * size : page * size]}  # fmt: skip
        if path == "/api/v3/series/lookup":
            return self.lookups.get(q["term"], [])
        if path == "/api/v3/movie/lookup":
            return self.lookups.get(q["term"], [])
        if path == "/api/v3/movie/lookup/tmdb":
            found = self.lookups.get(f"tmdb:{q['tmdbId']}")
            if found is None:
                raise OSError("HTTP Error 404: Not Found")
            return found
        raise AssertionError(f"unexpected {method} {path}")

    @property
    def gets(self) -> list[str]:
        return [path for method, path, _ in self.calls if method == "GET"]


def series(
    title: str, year: int, tvdb: int, tmdb: int = 0, seasons: dict[int, bool] | None = None
) -> dict[str, Any]:
    return {"title": title, "year": year, "tvdbId": tvdb, "tmdbId": tmdb,
            "titleSlug": title.lower().replace(" ", "-"), "images": [],
            "seasons": [{"seasonNumber": n, "monitored": m} for n, m in (seasons or {}).items()]}  # fmt: skip


def movie(title: str, year: int, tmdb: int) -> dict[str, Any]:
    return {"title": title, "year": year, "tmdbId": tmdb, "titleSlug": f"{tmdb}", "images": []}


@pytest.fixture
def library(db: sqlite3.Connection, tmp_path: Path) -> sqlite3.Connection:
    """The carried titles of the test catalog, as the rig's database knows them:
    - the-simpsons: files on the Plex tree;
    - dexters-laboratory: no files, but a converted episode whose download was deleted;
    - x-men: no files, year known (1992); gravity-falls: no files, TMDB id known;
    - dragon-ball-z: no files, year from overrides.yaml's plex_title `Dragon Ball Z (1989)`;
    - halloweentown (a movie): no file, year 1998."""
    db.executemany(
        "INSERT INTO shows (id, title, year, tmdb_id) VALUES (?, ?, ?, ?)",
        [("the-simpsons", "The Simpsons", 1989, None),
         ("dexters-laboratory", "Dexter's Laboratory", 1996, None),
         ("x-men", "X-Men", 1992, None),
         ("gravity-falls", "Gravity Falls", None, 40075)],
    )  # fmt: skip
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, file_path)"
        " VALUES ('the-simpsons', 1, 1, '/sources/tv/The Simpsons/S01E01.mkv')"
    )
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, media_path, media_seconds,"
        " source_deleted_at) VALUES ('dexters-laboratory', 1, 1, 'tv/d/s01/d-s01e01.mp4', 1300,"
        " '2026-11-07T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO movies (id, title, year) VALUES ('halloweentown', 'Halloweentown', 1998)"
    )
    return db


def test_wanted_is_what_a_channel_carries_with_no_file_and_no_encode(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog())
    found, with_files, excluded = wanted(library, catalog)
    assert found == [
        Wanted("dragon-ball-z", "Dragon Ball Z", "show", 1989, titles=("Dragon Ball Z (1989)",)),
        Wanted("gravity-falls", "Gravity Falls", "show", None, tmdb_id=40075),
        Wanted("halloweentown", "Halloweentown", "movie", 1998),
        Wanted("x-men", "X-Men", "show", 1992),
    ]
    assert with_files == 2  # the-simpsons, and dexters-laboratory's kept encode
    assert excluded == []


def test_wanted_skips_excluded_titles_and_adds_the_minis_admin_channels(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    overrides = "shows:\n  - { show: x-men, exclude: true }\n"
    catalog = load_catalog(make_catalog(overrides=overrides))
    mini = [
        {"id": "johnny-bravo", "title": "Johnny Bravo", "kind": "show", "first_year": 1997,
         "episodes": 0, "channels": [{"id": "custom-toons"}]},
        {"id": "on-the-mini", "title": "On the Mini", "kind": "show", "first_year": 1995,
         "episodes": 12, "channels": [{"id": "custom-toons"}]},  # files the mini knows
        {"id": "uncarried", "title": "Uncarried", "kind": "show", "episodes": 0, "channels": []},
    ]  # fmt: skip
    found, _, excluded = wanted(library, catalog, mini)
    ids = [w.id for w in found]
    assert "x-men" not in ids and excluded == ["x-men"]
    assert Wanted("johnny-bravo", "Johnny Bravo", "show", 1997) in found
    assert "on-the-mini" not in ids and "uncarried" not in ids


def _run(
    db: sqlite3.Connection,
    make_catalog: MakeCatalog,
    sonarr: FakeArr | None,
    radarr: FakeArr | None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    report = acquire(db, load_catalog(make_catalog()), sonarr=sonarr, radarr=radarr,
                     dry_run=dry_run)  # fmt: skip
    out: dict[str, Any] = asdict(report)
    return out


SONARR_LOOKUPS = {
    "X-Men": [series("X-Men", 1992, 76115), series("X-Men: Evolution", 2000, 70582),
              series("X-Men", 2011, 248470)],
    "tmdb:40075": [series("Gravity Falls", 2012, 259972, 40075)],
    "Dragon Ball Z": [series("Dragon Ball Z", 1989, 81472)],
}  # fmt: skip
RADARR_LOOKUPS = {
    "Halloweentown": [movie("Halloweentown", 1998, 10719), movie("Halloweentown II", 2001, 10720)],
}


def test_adds_series_and_movies_with_the_live_tv_settings(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    sonarr = FakeArr("series", lookups=SONARR_LOOKUPS)
    radarr = FakeArr("movie", lookups=RADARR_LOOKUPS)
    out = _run(library, make_catalog, sonarr, radarr)

    assert [(a["id"], a["tvdb_id"], a["matched_on"]) for a in out["shows"]["added"]] == [
        ("dragon-ball-z", 81472, "title+year"),
        ("gravity-falls", 259972, "tmdb"),
        ("x-men", 76115, "title+year"),
    ]
    assert out["shows"]["unresolved"] == []
    for body in sonarr.posted:
        assert body["rootFolderPath"] == "/tv"
        assert body["qualityProfileId"] == 9, "the profile is found by name, not id"
        assert body["monitored"] is True and body["seasonFolder"] is True
        assert body["addOptions"] == {"monitor": "all", "searchForMissingEpisodes": True,
                                      "searchForCutoffUnmetEpisodes": False}  # fmt: skip
    assert [b["tvdbId"] for b in sonarr.posted] == [81472, 259972, 76115]
    assert ("POST", "/api/v3/series", {}) in sonarr.calls

    assert [(a["id"], a["tmdb_id"]) for a in out["movies"]["added"]] == [("halloweentown", 10719)]
    (body,) = radarr.posted
    assert body["rootFolderPath"] == "/movies" and body["qualityProfileId"] == 9
    assert body["monitored"] is True
    assert body["addOptions"] == {"searchForMovie": True, "monitor": "movieOnly"}
    assert out["with_files"] == 2 and out["dry_run"] is False


def test_a_dry_run_posts_nothing(library: sqlite3.Connection, make_catalog: MakeCatalog) -> None:
    sonarr = FakeArr("series", lookups=SONARR_LOOKUPS)
    radarr = FakeArr("movie", lookups=RADARR_LOOKUPS)
    out = _run(library, make_catalog, sonarr, radarr, dry_run=True)
    assert all(method == "GET" for method, _, _ in sonarr.calls + radarr.calls)
    assert [a["title"] for a in out["shows"]["added"]] == [
        "Dragon Ball Z",
        "Gravity Falls",
        "X-Men",
    ]
    assert [a["title"] for a in out["movies"]["added"]] == ["Halloweentown"]
    assert out["dry_run"] is True


def test_titles_already_in_the_instance_are_skipped(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    sonarr = FakeArr("series", lookups=SONARR_LOOKUPS,
                     existing=[{"id": 4, "title": "X-Men", "tvdbId": 76115, "tmdbId": 0}])  # fmt: skip
    radarr = FakeArr("movie", lookups=RADARR_LOOKUPS,
                     existing=[{"id": 2, "title": "Halloweentown", "tmdbId": 10719}])  # fmt: skip
    out = _run(library, make_catalog, sonarr, radarr)
    assert out["shows"]["present"] == ["x-men"]
    assert out["movies"]["present"] == ["halloweentown"] and radarr.posted == []
    assert [b["tvdbId"] for b in sonarr.posted] == [81472, 259972]

    # And a second run adds nothing: the first one's additions are there now.
    sonarr.existing += [{**b, "id": 7} for b in sonarr.posted]
    sonarr.posted.clear()
    again = _run(library, make_catalog, sonarr, radarr)
    assert sonarr.posted == [] and again["shows"]["added"] == []
    assert sorted(again["shows"]["present"]) == ["dragon-ball-z", "gravity-falls", "x-men"]


def test_without_a_first_aired_year_a_title_is_never_guessed(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """Dragon Ball Z without its plex_title: the lookup has one exact title, but no year to
    check it against, so it is reported, not added."""
    catalog = load_catalog(make_catalog(overrides="shows: []\n"))
    sonarr = FakeArr("series", lookups=SONARR_LOOKUPS)
    report = acquire(library, catalog, sonarr=sonarr, radarr=None, dry_run=False)
    assert [u for u in report.shows.unresolved if u["id"] == "dragon-ball-z"] == [
        {"id": "dragon-ball-z", "title": "Dragon Ball Z", "reason": "no first-aired year known"}
    ]
    assert 81472 not in [b["tvdbId"] for b in sonarr.posted]


def test_a_show_that_may_be_on_the_nas_under_another_folder_name_is_not_requested(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """The NAS's `Justice League (Unlimited)` folder matches no carried title, so it is stored
    as an uncarried show; carried `justice-league` then looks file-less. Downloading it again
    would duplicate the NAS: it is reported instead, until a plex_title override matches it."""
    library.execute(
        "INSERT INTO shows (id, title) VALUES ('x-men-tas', 'X-Men (The Animated Series)')"
    )
    library.execute(
        "INSERT INTO episodes (show_id, season, episode, file_path)"
        " VALUES ('x-men-tas', 1, 1, '/sources/tv/X-Men (The Animated Series)/S01E01.mkv')"
    )
    library.execute("INSERT INTO shows (id, title) VALUES ('x-mental', 'X-Mental')")  # not it
    library.execute(
        "INSERT INTO episodes (show_id, season, episode, file_path)"
        " VALUES ('x-mental', 1, 1, '/sources/tv/X-Mental/S01E01.mkv')"
    )
    sonarr = FakeArr("series", lookups=SONARR_LOOKUPS)
    out = _run(library, make_catalog, sonarr, None)
    assert {
        "id": "x-men",
        "title": "X-Men",
        "reason": "may be on the NAS already as 'X-Men (The Animated Series)': give it a"
        " plex_title in overrides.yaml, or add it to sonarr-livetv by hand",
    } in out["shows"]["unresolved"]
    assert 76115 not in [b["tvdbId"] for b in sonarr.posted]
    assert ("X-Men", 1992) not in [(c[2].get("term"), 1992) for c in sonarr.calls]


@pytest.mark.parametrize(
    ("folder", "held"),
    [
        ("X-Men (The Animated Series)", True),
        ("X-Men 1992", True),  # `Bluey 2018`
        ("X-Men [DVD]", True),
        ("X-Men Evolution", False),  # `House Of The Dragon` is not House
        ("X-Men Evolution (2000)", False),  # `Digimon Beatbreak (2025)` is not Digimon
    ],
)
def test_only_a_qualified_folder_name_holds_a_show_back(
    library: sqlite3.Connection, make_catalog: MakeCatalog, folder: str, held: bool
) -> None:
    """A longer title is another show (House Of The Dragon, Malcolm in the Middle Life's Still
    Unfair); only the same title plus a qualifier in brackets or a year is held back."""
    library.execute("INSERT INTO shows (id, title) VALUES ('nas', ?)", (folder,))
    library.execute(
        "INSERT INTO episodes (show_id, season, episode, file_path)"
        " VALUES ('nas', 1, 1, '/sources/tv/nas/S01E01.mkv')"
    )
    sonarr = FakeArr("series", lookups=SONARR_LOOKUPS)
    out = _run(library, make_catalog, sonarr, None)
    assert (76115 in [b["tvdbId"] for b in sonarr.posted]) is not held
    assert ("x-men" in [u["id"] for u in out["shows"]["unresolved"]]) is held


@pytest.mark.parametrize(
    ("lookup", "reason"),
    [
        ([], "no exact match for X-Men (1992)"),
        ([series("X-Men: The Animated Series", 1992, 1)], "no exact match for X-Men (1992)"),
        ([series("X-Men", 1992, 1), series("X Men", 1992, 2)], "2 exact matches for X-Men (1992)"),
        ([series("X-Men", 2011, 248470)], "no exact match for X-Men (1992)"),
    ],
)
def test_a_title_lookup_must_be_exact_and_unambiguous(
    library: sqlite3.Connection, make_catalog: MakeCatalog, lookup: list[Any], reason: str
) -> None:
    sonarr = FakeArr("series", lookups={**SONARR_LOOKUPS, "X-Men": lookup})
    out = _run(library, make_catalog, sonarr, None)
    assert {"id": "x-men", "title": "X-Men", "reason": reason} in out["shows"]["unresolved"]
    assert 76115 not in [b["tvdbId"] for b in sonarr.posted]


def test_a_year_in_the_instances_title_still_matches(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """TheTVDB names remakes `X-Men (1992)`; that is still an exact title."""
    sonarr = FakeArr("series", lookups={**SONARR_LOOKUPS, "X-Men": [
        series("X-Men (1992)", 1992, 76115), series("X-Men (2011)", 2011, 248470)]})  # fmt: skip
    out = _run(library, make_catalog, sonarr, None)
    assert ("x-men", 76115) in [(a["id"], a["tvdb_id"]) for a in out["shows"]["added"]]


@pytest.mark.parametrize(
    "roots",
    [["/data/tv"], ["/tv", "/data/tv"], [], ["/movies"]],
)
def test_refuses_an_instance_whose_root_folders_are_not_exactly_live_tvs(
    library: sqlite3.Connection, make_catalog: MakeCatalog, roots: list[str]
) -> None:
    """The Plex instances have other roots: pointing acquire at one adds nothing."""
    sonarr = FakeArr("series", roots=roots, lookups=SONARR_LOOKUPS)
    radarr = FakeArr("movie", roots=["/tv"], lookups=RADARR_LOOKUPS)
    out = _run(library, make_catalog, sonarr, radarr)
    assert sonarr.posted == [] and radarr.posted == []
    assert "root folders" in out["shows"]["error"] and "/tv" in out["shows"]["error"]
    assert "root folders" in out["movies"]["error"]
    assert sonarr.gets == ["/api/v3/rootfolder"], "nothing else is asked of it"


def test_a_trailing_slash_on_the_root_folder_is_fine(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    sonarr = FakeArr("series", roots=["/tv/"], lookups=SONARR_LOOKUPS)
    out = _run(library, make_catalog, sonarr, None)
    assert out["shows"]["error"] is None and len(sonarr.posted) == 3


def test_refuses_without_the_live_tv_profile(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    sonarr = FakeArr("series", profiles=[{"id": 7, "name": "HD-1080p"}], lookups=SONARR_LOOKUPS)
    out = _run(library, make_catalog, sonarr, None)
    assert sonarr.posted == []
    assert out["shows"]["error"] == "sonarr-livetv has no quality profile named 'LiveTV SD'"


def test_unconfigured_or_failing_instances_are_reported(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    out = _run(library, make_catalog, None, FakeArr("movie", lookups=RADARR_LOOKUPS,
                                                    fail_post=True))  # fmt: skip
    assert out["shows"]["error"] == "set RC_SONARR_LIVETV_URL and RC_SONARR_LIVETV_KEY"
    assert out["movies"]["failed"] == [
        {"id": "halloweentown", "title": "Halloweentown", "reason": "HTTP Error 400: Bad Request"}
    ]


def test_nothing_wanted_means_nothing_added(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """Nothing is added and Radarr isn't asked anything; sonarr-livetv is only read, for
    seasons that can never air (trim_seasons, which here finds none)."""
    carriage = "carriage:\n  - { show: the-simpsons, title: The Simpsons, channel: ktvu, from: 1990-01-01 }\n"
    catalog = load_catalog(make_catalog(carriage=carriage, templates="templates: []\n",
                                        synthetic="synthetic: []\n", overrides="shows: []\n"))  # fmt: skip
    db.execute("INSERT INTO shows (id, title) VALUES ('the-simpsons', 'The Simpsons')")
    db.execute("INSERT INTO episodes (show_id, season, episode, file_path)"
               " VALUES ('the-simpsons', 1, 1, '/x.mkv')")  # fmt: skip
    sonarr, radarr = FakeArr("series"), FakeArr("movie")
    report = acquire(db, catalog, sonarr=sonarr, radarr=radarr, dry_run=False)
    assert radarr.calls == []
    assert {m for m, _, _ in sonarr.calls} == {"GET"}
    assert sonarr.posted == sonarr.put == [] and sonarr.deleted == []
    report = acquire(db, catalog, sonarr=None, radarr=None, dry_run=False)
    assert report.shows.error is None and report.movies.error is None, "nothing to ask for"


# --- the lineup's TMDB ids and years (overrides.yaml, the admin overlay, the mini) --------

LINEUP_OVERRIDES = """
shows:
  - { show: x-men, tmdb_id: "tv:4500", year: 1992 }
  - { show: dragon-ball-z, plex_title: "Dragon Ball Z (1989)", year: 1989 }
  - { show: halloweentown, tmdb_id: "movie:10719" }
  - { show: gravity-falls, tmdb_id: "movie:5" }
"""


def test_wanted_takes_the_lineups_tmdb_id_and_year_from_overrides(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """Nothing in the database: the catalog's ids and years are all acquire has. A TMDB id of
    the other kind (a show with a movie id) is not used."""
    catalog = load_catalog(make_catalog(overrides=LINEUP_OVERRIDES))
    found, _, _ = wanted(db, catalog)
    by_id = {w.id: w for w in found}
    assert (by_id["x-men"].tmdb_id, by_id["x-men"].year) == (4500, 1992)
    assert by_id["halloweentown"].tmdb_id == 10719
    assert by_id["gravity-falls"].tmdb_id is None
    assert by_id["dragon-ball-z"].year == 1989


def test_the_catalogs_tmdb_id_wins_over_the_databases(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    overrides = 'shows:\n  - { show: gravity-falls, tmdb_id: "tv:999", year: 2012 }\n'
    found, _, _ = wanted(library, load_catalog(make_catalog(overrides=overrides)))
    gravity = next(w for w in found if w.id == "gravity-falls")
    assert (gravity.tmdb_id, gravity.year) == (999, 2012)


def test_lineup_ids_resolve_by_tmdb_then_fall_back_to_title_and_year(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog(overrides=LINEUP_OVERRIDES))
    sonarr = FakeArr("series", lookups={
        "tmdb:4500": [series("X-Men", 1992, 76115, 4500)],
        # gravity-falls has no usable id and no year: unresolved, never guessed
        "Dragon Ball Z": [series("Dragon Ball Z", 1989, 81472)],
    })  # fmt: skip
    radarr = FakeArr("movie", lookups={"tmdb:10719": movie("Halloweentown", 1998, 10719)})
    report = acquire(db, catalog, sonarr=sonarr, radarr=radarr, dry_run=True)
    assert [(a["id"], a["matched_on"]) for a in report.shows.added] == [
        ("dragon-ball-z", "title+year"),
        ("x-men", "tmdb"),
    ]
    assert {
        "id": "gravity-falls",
        "title": "Gravity Falls",
        "reason": "no first-aired year known",
    } in report.shows.unresolved
    assert [(a["id"], a["matched_on"]) for a in report.movies.added] == [("halloweentown", "tmdb")]
    assert ("GET", "/api/v3/series/lookup", {"term": "tmdb:4500"}) in sonarr.calls
    assert ("GET", "/api/v3/movie/lookup/tmdb", {"tmdbId": "10719"}) in radarr.calls


def test_an_unknown_tmdb_id_falls_back_to_the_lineups_year(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    catalog = load_catalog(make_catalog(overrides=LINEUP_OVERRIDES))
    sonarr = FakeArr("series", lookups={"X-Men": [series("X-Men", 1992, 76115)]})
    report = acquire(db, catalog, sonarr=sonarr, radarr=None, dry_run=True)
    assert ("x-men", "title+year") in [(a["id"], a["matched_on"]) for a in report.shows.added]


def test_the_minis_library_items_bring_their_lineup_ids(
    db: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """Shows the channel builder placed on the mini, with nothing on disk anywhere: the
    mini's /api/admin/library lists them with the lineup's TMDB id and year."""
    catalog = load_catalog(make_catalog(overrides="shows: []\n"))
    mini: list[dict[str, Any]] = [
        {"id": "psych", "title": "Psych", "kind": "show", "first_year": None, "year": 2006,
         "tmdb_id": "tv:1447", "episodes": 0, "channels": [{"id": "custom-tv"}]},
        {"id": "snow-day", "title": "Snow Day", "kind": "movie", "first_year": None,
         "year": 2000, "tmdb_id": "movie:10561", "episodes": 0,
         "channels": [{"id": "custom-movies"}]},
        {"id": "odd", "title": "Odd", "kind": "show", "tmdb_id": "tv:x", "year": "1999",
         "episodes": 0, "channels": [{"id": "custom-tv"}]},
    ]  # fmt: skip
    found, _, _ = wanted(db, catalog, mini)
    by_id = {w.id: w for w in found}
    assert (by_id["psych"].tmdb_id, by_id["psych"].year) == (1447, 2006)
    assert (by_id["snow-day"].tmdb_id, by_id["snow-day"].year) == (10561, 2000)
    assert (by_id["odd"].tmdb_id, by_id["odd"].year) == (None, None)


# --- the CLI and the HTTP helper ---------------------------------------------------------


def test_acquire_cli_prints_a_json_summary_and_never_the_keys(
    tmp_path: Path,
    make_catalog: MakeCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fakes = {"http://sonarr:8990": FakeArr("series", lookups=SONARR_LOOKUPS),
             "http://radarr:7879": FakeArr("movie", lookups=RADARR_LOOKUPS)}  # fmt: skip
    seen_keys: list[str] = []

    def make(base_url: str, headers: Mapping[str, str], timeout: float = 30) -> FakeArr:
        seen_keys.append(headers["X-Api-Key"])
        return fakes[base_url]

    monkeypatch.setattr(cli, "json_request", make)
    monkeypatch.delenv("RC_PRIORITY_URL", raising=False)
    monkeypatch.setenv("RC_SONARR_LIVETV_URL", "http://sonarr:8990")
    monkeypatch.setenv("RC_SONARR_LIVETV_KEY", "sonarr-secret-key")
    monkeypatch.setenv("RC_RADARR_LIVETV_URL", "http://radarr:7879")
    monkeypatch.setenv("RC_RADARR_LIVETV_KEY", "radarr-secret-key")
    db = tmp_path / "rig.db"
    conn = connect(db)
    migrate(conn)
    conn.execute("INSERT INTO shows (id, title, year) VALUES ('x-men', 'X-Men', 1992)")
    conn.execute("INSERT INTO movies (id, title, year) VALUES ('halloweentown', 'H', 1998)")
    conn.close()

    args = ["acquire", "--db", str(db), "--catalog", str(make_catalog()), "--dry-run"]
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["dry_run"] is True
    assert "X-Men" in [a["title"] for a in out["shows"]["added"]]
    assert all(m == "GET" for f in fakes.values() for m, _, _ in f.calls)
    assert sorted(seen_keys) == ["radarr-secret-key", "sonarr-secret-key"]
    assert "secret" not in captured.out + captured.err


def test_acquire_cli_fails_when_an_instance_refuses(
    tmp_path: Path,
    make_catalog: MakeCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("SONARR", "RADARR"):
        monkeypatch.delenv(f"RC_{name}_LIVETV_URL", raising=False)
        monkeypatch.delenv(f"RC_{name}_LIVETV_KEY", raising=False)
    monkeypatch.delenv("RC_PRIORITY_URL", raising=False)
    args = ["acquire", "--db", str(tmp_path / "rig.db"), "--catalog", str(make_catalog())]
    assert cli.main(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["shows"]["error"].startswith("set RC_SONARR_LIVETV_URL")


def test_json_request_sends_the_key_in_a_header_and_json_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io
    import urllib.request

    from retro_cable.ingest.http import json_request

    sent: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        sent.append(request)
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    request = json_request("http://sonarr:8990/", {"X-Api-Key": "k3y"})
    assert request("GET", "/api/v3/series/lookup", {"term": "tmdb:1396"}, None) == {"ok": True}
    assert request("POST", "/api/v3/series", None, {"title": "X"}) == {"ok": True}
    get, post = sent
    assert get.full_url == "http://sonarr:8990/api/v3/series/lookup?term=tmdb%3A1396"
    assert get.get_method() == "GET" and get.data is None
    assert post.get_method() == "POST" and isinstance(post.data, bytes)
    assert json.loads(post.data) == {"title": "X"}
    assert post.get_header("Content-type") == "application/json"
    assert all(r.get_header("X-api-key") == "k3y" for r in sent)
    assert "k3y" not in repr(request) and all("k3y" not in r.full_url for r in sent)


def test_a_tmdb_lookup_answered_by_a_tvdb_id_is_not_a_match(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """Sonarr answers `tmdb:125211` it can't map with the series whose TheTVDB id is 125211
    (Hello Kitty and Friends for Lupin the 3rd Part II, Nov 8 2026). Only a result that
    carries the same TMDB id counts."""
    collision = {"tmdb:40075": [series("Hello Kitty and Friends", 1989, 40075, 0)]}
    sonarr = FakeArr("series", lookups={**SONARR_LOOKUPS, **collision})
    out = _run(library, make_catalog, sonarr, None)
    assert 40075 not in [b["tvdbId"] for b in sonarr.posted]
    assert {
        "id": "gravity-falls",
        "title": "Gravity Falls",
        "reason": "no first-aired year known",
    } in out["shows"]["unresolved"]


JLU = (
    "shows:\n  - { show: x-men, year: 2004,"
    " part_of: { show: justice-league, tvdb_id: 76320, seasons: [3, 4] } }\n"
)
# A lookup marks every season monitored; a new series must keep only the asked-for ones.
JL = series("Justice League", 2001, 76320, 1618, {1: True, 2: True, 3: True, 4: True})


def _part(
    library: sqlite3.Connection, make_catalog: MakeCatalog, sonarr: FakeArr, **kw: Any
) -> dict[str, Any]:
    catalog = load_catalog(make_catalog(overrides=JLU))
    out: dict[str, Any] = asdict(acquire(library, catalog, sonarr=sonarr, radarr=None, **kw))
    return out


def test_seasons_of_a_series_are_asked_for_by_season(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """overrides.yaml `part_of`: X-Men here stands for Justice League Unlimited, which
    TheTVDB files as Justice League seasons 3-4. The series is added with nothing monitored
    and no search, then just those seasons are monitored and searched."""
    sonarr = FakeArr("series", lookups={**SONARR_LOOKUPS, "tvdb:76320": [JL]})
    out = _part(library, make_catalog, sonarr, dry_run=False)
    added = next(a for a in out["shows"]["added"] if a["id"] == "x-men")
    assert (added["tvdb_id"], added["matched_on"]) == (76320, "part_of")
    assert added["found"] == "Justice League (2001) seasons 3, 4"
    body = next(b for b in sonarr.posted if b["tvdbId"] == 76320)
    assert body["addOptions"]["monitor"] == "none"
    assert body["addOptions"]["searchForMissingEpisodes"] is False
    assert body["rootFolderPath"] == "/tv" and body["qualityProfileId"] == 9
    (put,) = sonarr.put
    assert put["monitored"] is True
    assert {s["seasonNumber"]: s["monitored"] for s in put["seasons"]} == {
        1: False, 2: False, 3: True, 4: True,
    }  # fmt: skip
    assert sonarr.commands == [
        {"name": "SeasonSearch", "seriesId": put["id"], "seasonNumber": n} for n in (3, 4)
    ]
    assert ("GET", "/api/v3/series/lookup", {"term": "X-Men"}) not in sonarr.calls


def test_a_series_already_there_gets_only_the_missing_seasons(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    there = dict(JL, id=7, monitored=True,
                 seasons=[{"seasonNumber": n, "monitored": n == 3} for n in (1, 2, 3, 4)])  # fmt: skip
    sonarr = FakeArr("series", lookups=SONARR_LOOKUPS, existing=[there])
    _part(library, make_catalog, sonarr, dry_run=False)
    assert not [b for b in sonarr.posted if b.get("tvdbId") == 76320]
    assert [s["seasonNumber"] for s in sonarr.put[0]["seasons"] if s["monitored"]] == [3, 4]
    assert sonarr.commands == [{"name": "SeasonSearch", "seriesId": 7, "seasonNumber": 4}]

    done = dict(there, seasons=[{"seasonNumber": n, "monitored": n >= 3} for n in (1, 2, 3, 4)])
    again = FakeArr("series", lookups=SONARR_LOOKUPS, existing=[done])
    out = _part(library, make_catalog, again, dry_run=False)
    assert "x-men" in out["shows"]["present"]
    assert again.put == [] and again.commands == []


def test_seasons_on_disk_under_the_series_count_as_files(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    library.execute("INSERT INTO shows (id, title) VALUES ('justice-league', 'Justice League')")
    library.execute(
        "INSERT INTO episodes (show_id, season, episode, file_path)"
        " VALUES ('justice-league', 4, 1, '/sources/tv/Justice League/S04E01.mkv')"
    )
    found, with_files, _ = wanted(library, load_catalog(make_catalog(overrides=JLU)))
    assert "x-men" not in [w.id for w in found]
    assert with_files == 3


def test_a_dry_run_asks_for_no_seasons(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    sonarr = FakeArr("series", lookups={**SONARR_LOOKUPS, "tvdb:76320": [JL]})
    out = _part(library, make_catalog, sonarr, dry_run=True)
    assert "x-men" in [a["id"] for a in out["shows"]["added"]]
    assert sonarr.posted == [] and sonarr.put == [] and sonarr.commands == []


def test_a_tv_movie_made_of_episodes_is_never_asked_of_radarr(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """overrides.yaml `episodes_of`: it comes down with its series and `ingest assemble`
    joins it, so it is neither wanted nor counted until then."""
    overrides = (
        "shows:\n  - { show: halloweentown, year: 1998,"
        " episodes_of: { show: the-simpsons, episodes: [S10E04] } }\n"
    )
    found, with_files, _ = wanted(library, load_catalog(make_catalog(overrides=overrides)))
    assert "halloweentown" not in [w.id for w in found]
    assert with_files == 2  # the-simpsons and dexters-laboratory, as before


def test_a_tv_movie_whose_series_nobody_carries_is_asked_for_by_season(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """`episodes_of` with a `tvdb_id` (Heidi (1993) is a two-part Disney Channel miniseries
    on TheTVDB): the series' seasons holding those episodes are asked of Sonarr, like
    `part_of`, until any of them is on disk."""
    overrides = (
        "shows:\n  - { show: halloweentown, year: 1998, episodes_of:"
        " { show: justice-league, tvdb_id: 76320, episodes: [S03E01, S04E02] } }\n"
    )
    catalog = load_catalog(make_catalog(overrides=overrides))
    found, _, _ = wanted(library, catalog)
    w = next(w for w in found if w.id == "halloweentown")
    assert (w.kind, w.part_of) == ("show", (76320, (3, 4)))  # Sonarr's, not Radarr's
    sonarr = FakeArr("series", lookups={**SONARR_LOOKUPS, "tvdb:76320": [JL]})
    radarr = FakeArr("movie", lookups=RADARR_LOOKUPS)
    out = asdict(acquire(library, catalog, sonarr=sonarr, radarr=radarr, dry_run=False))
    assert "halloweentown" in [a["id"] for a in out["shows"]["added"]]
    assert not [b for b in radarr.posted if b.get("title") == "Halloweentown"]

    library.execute("INSERT INTO shows (id, title) VALUES ('justice-league', 'Justice League')")
    library.execute(
        "INSERT INTO episodes (show_id, season, episode, file_path)"
        " VALUES ('justice-league', 3, 1, '/media/sources/tv/Justice League/S03E01.mkv')"
    )
    found, _, _ = wanted(library, catalog)
    assert "halloweentown" not in [w.id for w in found]


# --- seasons that can never air (Nov 9 2026) --------------------------------------------------


def aired(series_id: int, *seasons: tuple[int, str]) -> list[dict[str, Any]]:
    """Two episodes per season, the first on the given date."""
    return [{"seriesId": series_id, "seasonNumber": n, "episodeNumber": e, "airDate": d}
            for n, d in seasons for e in (1, 2)]  # fmt: skip


def live(title: str, ident: int, tvdb: int, year: int, seasons: list[int]) -> dict[str, Any]:
    return series(title, year, tvdb, seasons={n: True for n in seasons}) | {
        "id": ident, "path": f"/tv/{title} ({year})", "monitored": True,
        "monitorNewItems": "all",
    }  # fmt: skip


SIMPSONS = live("The Simpsons", 1, 71663, 1989, [1, 22, 23])
SIMPSONS_EPS = aired(1, (1, "1989-12-17"), (22, "2010-09-26"), (23, "2011-09-25"))


def _trim(
    library: sqlite3.Connection, make_catalog: MakeCatalog, sonarr: FakeArr, **kw: Any
) -> dict[str, Any]:
    catalog = load_catalog(make_catalog(**kw.pop("files", {})))
    dry_run = kw.pop("dry_run", False)
    out: dict[str, Any] = asdict(
        acquire(library, catalog, sonarr=sonarr, radarr=None, dry_run=dry_run, **kw)
    )
    return out


def test_seasons_first_aired_after_2010_are_unmonitored(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """Simulated years end in 2010, so a season that began later never airs (unless the show
    airs on moved dates: next test). It is unmonitored, Sonarr adds no new seasons, and its
    unfinished downloads are cancelled; a season that began by 2010 stays."""
    queue = [
        # A queued pack of season 23 only: cancelled.
        {"id": 50, "seriesId": 1, "downloadId": "a", "seasonNumber": 23, "status": "queued",
         "trackedDownloadState": "downloading"},
        {"id": 51, "seriesId": 1, "downloadId": "a", "seasonNumber": 23, "status": "queued",
         "trackedDownloadState": "downloading"},
        # Finished, or also holding season 22: left alone.
        {"id": 52, "seriesId": 1, "downloadId": "b", "seasonNumber": 23, "status": "completed",
         "trackedDownloadState": "importPending"},
        {"id": 53, "seriesId": 1, "downloadId": "c", "seasonNumber": 22, "status": "queued",
         "trackedDownloadState": "downloading"},
        {"id": 54, "seriesId": 1, "downloadId": "c", "seasonNumber": 23, "status": "queued",
         "trackedDownloadState": "downloading"},
    ]  # fmt: skip
    sonarr = FakeArr("series", existing=[SIMPSONS], episodes={1: SIMPSONS_EPS}, queue=queue,
                     lookups=SONARR_LOOKUPS)  # fmt: skip
    out = _trim(library, make_catalog, sonarr)
    assert out["shows"]["trimmed"] == [
        {"id": "the-simpsons", "title": "The Simpsons", "seasons": [23], "cancelled": 1}
    ]
    (put,) = [p for p in sonarr.put if p.get("id") == 1]
    assert {x["seasonNumber"]: x["monitored"] for x in put["seasons"]} == {
        1: True, 22: True, 23: False,
    }  # fmt: skip
    assert put["monitorNewItems"] == "none"
    assert sonarr.deleted == [
        ("/api/v3/queue/50", {"removeFromClient": "true", "blocklist": "false"})
    ]
    # Run again: nothing left to trim.
    done = live("The Simpsons", 1, 71663, 1989, [1, 22]) | {"seasons": put["seasons"]}
    again = FakeArr("series", existing=[done], episodes={1: SIMPSONS_EPS}, lookups=SONARR_LOOKUPS)
    assert _trim(library, make_catalog, again)["shows"]["trimmed"] == []
    assert again.put == []


def test_shows_on_moved_dates_keep_every_season(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """A pretend premiere (synthetic.yaml, or the mini's /admin: `shifted`) or a US debut
    moves the dates, so a later season may air after all."""
    falls = live("Gravity Falls", 2, 259972, 2012, [1, 2])  # synthetic.yaml premiere
    simpsons = SIMPSONS
    sonarr = FakeArr("series", existing=[falls, simpsons], lookups=SONARR_LOOKUPS,
                     episodes={1: SIMPSONS_EPS, 2: aired(2, (1, "2012-06-15"), (2, "2014-08-01"))})  # fmt: skip
    mini = [{"id": "the-simpsons", "title": "The Simpsons", "kind": "show",
             "channels": [{"id": "ktvu"}], "episodes": 3, "shifted": True}]  # fmt: skip
    out = _trim(library, make_catalog, sonarr, extra=mini)
    assert out["shows"]["trimmed"] == [] and sonarr.put == [] and sonarr.deleted == []


def test_seasons_another_show_airs_as_are_kept(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """overrides.yaml part_of (and episodes_of) seasons belong to a show of their own, which
    may have a pretend premiere: never trimmed."""
    overrides = (
        "shows:\n  - { show: x-men, year: 2011, part_of:"
        " { show: the-simpsons, tvdb_id: 71663, seasons: [23] } }\n"
    )
    sonarr = FakeArr("series", existing=[SIMPSONS], episodes={1: SIMPSONS_EPS},
                     lookups=SONARR_LOOKUPS)  # fmt: skip
    out = _trim(library, make_catalog, sonarr, files={"overrides": overrides})
    assert out["shows"]["trimmed"] == []


def test_series_no_channel_carries_are_left_alone(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    other = live("Sesame Street", 3, 79591, 1969, [41, 42])
    sonarr = FakeArr("series", existing=[other], lookups=SONARR_LOOKUPS,
                     episodes={3: aired(3, (41, "2010-09-27"), (42, "2011-09-26"))})  # fmt: skip
    assert _trim(library, make_catalog, sonarr)["shows"]["trimmed"] == []
    assert not any(path == "/api/v3/episode" for path in sonarr.gets)


def test_a_dry_run_trims_nothing(library: sqlite3.Connection, make_catalog: MakeCatalog) -> None:
    sonarr = FakeArr("series", existing=[SIMPSONS], episodes={1: SIMPSONS_EPS},
                     lookups=SONARR_LOOKUPS)  # fmt: skip
    out = _trim(library, make_catalog, sonarr, dry_run=True)
    assert out["shows"]["trimmed"] == [{"id": "the-simpsons", "title": "The Simpsons",
                                        "seasons": [23]}]  # fmt: skip
    assert sonarr.put == [] and sonarr.deleted == []


def test_trims_nothing_on_an_instance_with_other_root_folders(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    sonarr = FakeArr("series", roots=["/data/tv"], existing=[SIMPSONS],
                     episodes={1: SIMPSONS_EPS})  # fmt: skip
    out = _trim(library, make_catalog, sonarr)
    assert out["shows"]["error"] and out["shows"]["trimmed"] == []
    assert sonarr.put == [] and sonarr.deleted == []


def test_a_remake_with_the_same_title_is_told_apart_by_year(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """DuckTales (1987) and DuckTales (2017) share a title: a series matches the carried
    show with its first-aired year (within a year), and none when no year fits."""
    reboot = live("The Simpsons", 5, 999, 2021, [1, 2])
    eps = {5: aired(5, (1, "2021-05-01"), (2, "2022-05-01"))}
    mini = [{"id": "the-simpsons-2021", "title": "The Simpsons (2021)", "kind": "show",
             "year": 2021, "channels": [{"id": "custom-x"}], "shifted": True}]  # fmt: skip
    years = "shows:\n  - { show: the-simpsons, year: 1989 }\n"
    sonarr = FakeArr("series", existing=[reboot], episodes=eps, lookups=SONARR_LOOKUPS)
    out = _trim(library, make_catalog, sonarr, extra=mini, files={"overrides": years})
    assert out["shows"]["trimmed"] == [], "the 2021 show is shifted"
    alone = FakeArr("series", existing=[reboot], episodes=eps, lookups=SONARR_LOOKUPS)
    out = _trim(library, make_catalog, alone, files={"overrides": years})
    assert out["shows"]["trimmed"] == [], "only the 1989 show is carried: not this series"
    assert not any(path == "/api/v3/episode" for path in alone.gets)


# --- searching again for missing movies (Nov 9 2026) -------------------------------------


def test_missing_movies_are_searched_again_once_a_day(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """Radarr searches a movie when it is added and never again; its RSS feed only sees new
    posts. So acquire asks radarr-livetv to search its missing movies again, at most daily."""
    from datetime import UTC, datetime, timedelta

    catalog = load_catalog(make_catalog())
    first = datetime(2026, 11, 9, 5, 0, tzinfo=UTC)

    def run(now: datetime, **kw: Any) -> tuple[FakeArr, dict[str, Any]]:
        radarr = FakeArr("movie", lookups=RADARR_LOOKUPS)
        report = acquire(library, catalog, sonarr=None, radarr=radarr, now=now, **kw)
        out: dict[str, Any] = asdict(report)
        return radarr, out

    radarr, out = run(first, dry_run=False)
    assert {"name": "MissingMoviesSearch", "filterKey": "monitored",
            "filterValue": "true"} in radarr.commands  # fmt: skip
    assert out["movies"]["searched_missing"] is True
    radarr, out = run(first + timedelta(hours=6), dry_run=False)
    assert radarr.commands == [] and out["movies"]["searched_missing"] is False
    radarr, _ = run(first + timedelta(days=1, minutes=1), dry_run=True)
    assert radarr.commands == [], "a dry run searches nothing"
    radarr, _ = run(first + timedelta(days=1, minutes=1), dry_run=False)
    assert [c["name"] for c in radarr.commands] == ["MissingMoviesSearch"]


def test_no_search_while_every_indexer_is_down(
    library: sqlite3.Connection, make_catalog: MakeCatalog
) -> None:
    """Radarr backs off from failing indexers; a search then finds nothing (Nov 9: "0 active
    indexers"). It is skipped, not recorded, so the next pass tries again."""
    from datetime import UTC, datetime

    catalog = load_catalog(make_catalog())
    radarr = FakeArr("movie", lookups=RADARR_LOOKUPS)
    radarr.health = [{"type": "error", "source": "IndexerStatusCheck",
                      "message": "All indexers are unavailable due to failures"}]  # fmt: skip
    now = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
    out = asdict(acquire(library, catalog, sonarr=None, radarr=radarr, dry_run=False, now=now))
    assert out["movies"]["searched_missing"] is False and radarr.commands == []
    radarr.health = [{"message": "Indexers unavailable due to failures: Bitmagnet, IPT"}]
    out = asdict(acquire(library, catalog, sonarr=None, radarr=radarr, dry_run=False, now=now))
    assert out["movies"]["searched_missing"] is False, "every search indexer is named"
    radarr.health = [{"message": "Indexers unavailable due to failures: IPT"}]
    out = asdict(acquire(library, catalog, sonarr=None, radarr=radarr, dry_run=False, now=now))
    assert out["movies"]["searched_missing"] is True, "Bitmagnet still searches"
