"""The Seerr-backed metadata source: TMDB data through Brandon's LAN Seerr (v3), no network."""

import io
import json
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, date, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from retro_cable.db import connect, migrate
from retro_cable.ingest import cli, http
from retro_cable.ingest.http import FetchError
from retro_cable.ingest.tmdb import (
    Candidate,
    EpisodeFacts,
    MovieFacts,
    SeerrClient,
    TmdbClient,
    fill_gaps,
    pick_source,
    seerr_http_fetch,
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
KEY = "seerr-key-0123456789abcdef"


class FakeFetch:
    """Answers API paths from a dict; a value that is an exception is raised instead."""

    def __init__(self, routes: Mapping[str, Any]) -> None:
        self.routes = dict(routes)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        self.calls.append((path, dict(params or {})))
        if path not in self.routes:
            raise FetchError(f"HTTP 500 for {path}")
        value = self.routes[path]
        if isinstance(value, Exception):
            raise value
        return value


# Shapes from seerr v3 (server/models/Tv.ts, Movie.ts, Search.ts; routes/search.ts).
SIMPSONS_SHOW = {
    "id": 456,
    "name": "The Simpsons",
    "firstAirDate": "1989-12-17",
    "numberOfEpisodes": 4,
    "externalIds": {"tvdbId": 71663, "imdbId": "tt0096697"},
    "seasons": [
        {"id": 1, "seasonNumber": 0, "episodeCount": 1, "name": "Specials", "airDate": None},
        {"id": 2, "seasonNumber": 1, "episodeCount": 2, "name": "Season 1", "airDate": "1989-12-17"},
        {"id": 3, "seasonNumber": 2, "episodeCount": 1, "name": "Season 2", "airDate": "1990-10-11"},
    ],
}  # fmt: skip
SIMPSONS_S1 = {
    "id": 2,
    "seasonNumber": 1,
    "name": "Season 1",
    "episodes": [
        {"id": 11, "episodeNumber": 1, "seasonNumber": 1, "name": "Simpsons Roasting on an Open Fire", "airDate": "1989-12-17"},
        {"id": 12, "episodeNumber": 2, "seasonNumber": 1, "name": "Bart the Genius", "airDate": "1990-01-14"},
        {"id": 13, "episodeNumber": 3, "seasonNumber": 1, "name": "Homer's Odyssey", "airDate": None},
    ],
}  # fmt: skip
SIMPSONS_S0 = {"id": 1, "seasonNumber": 0, "name": "Specials", "episodes": []}
SEARCH = {
    "page": 1,
    "totalPages": 1,
    "totalResults": 4,
    "results": [
        {"id": 99, "mediaType": "person", "name": "The Simpsons"},
        {"id": 7, "mediaType": "tv", "name": "The Simpsons", "firstAirDate": "2031-01-01"},
        {"id": 456, "mediaType": "tv", "name": "The Simpsons", "firstAirDate": "1989-12-17"},
        {"id": 862, "mediaType": "movie", "title": "Toy Story", "releaseDate": "1995-11-22"},
    ],
}


def seerr_routes() -> dict[str, Any]:
    return {
        "/api/v1/search": SEARCH,
        "/api/v1/tv/456": SIMPSONS_SHOW,
        "/api/v1/tv/456/season/0": SIMPSONS_S0,
        "/api/v1/tv/456/season/1": SIMPSONS_S1,
        # season 2 is listed but Seerr answers 500 (TMDB has no such season any more)
        "/api/v1/movie/862": {"id": 862, "title": "Toy Story", "releaseDate": "1995-11-22",
                              "runtime": 81},
    }  # fmt: skip


def seed(db: sqlite3.Connection) -> None:
    """The mini's state: shows and movies from the disk scan, so no TMDB ids and no dates."""
    db.execute("INSERT INTO shows (id, title, year) VALUES ('the-simpsons', 'The Simpsons', 1989)")
    db.execute("INSERT INTO shows (id, title, year) VALUES ('nowhere', 'Nowhere Show', 1994)")
    db.execute(
        "INSERT INTO episodes (show_id, season, episode, title, file_path) VALUES"
        " ('the-simpsons', 1, 1, '', '/a.mkv'),"
        " ('the-simpsons', 1, 3, 'Homers Odyssey', '/c.mkv'),"
        " ('the-simpsons', 1, 9, 'Not on TMDB', '/z.mkv'),"
        " ('the-simpsons', 2, 1, '', '/d.mkv')"
    )
    db.execute("INSERT INTO movies (id, title, year) VALUES ('toy-story', 'Toy Story', 1995)")


def episodes(db: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(r)
        for r in db.execute(
            "SELECT season, episode, title, air_date, air_date_source, file_path FROM episodes"
            " WHERE show_id = 'the-simpsons' ORDER BY season, episode"
        )
    ]


def test_client_reads_seasons_and_episodes() -> None:
    client = SeerrClient(FakeFetch(seerr_routes()))
    assert client.seasons(456) == [0, 1, 2]
    assert client.season(456, 1) == [
        EpisodeFacts(1, 1, "Simpsons Roasting on an Open Fire", date(1989, 12, 17), None),
        EpisodeFacts(1, 2, "Bart the Genius", date(1990, 1, 14), None),
        EpisodeFacts(1, 3, "Homer's Odyssey", None, None),  # Seerr sends null for unknown
    ]


def test_client_search_keeps_only_the_asked_media_type() -> None:
    fetch = FakeFetch(seerr_routes())
    client = SeerrClient(fetch)
    assert client.search_tv("The Simpsons") == [
        Candidate(7, "The Simpsons", 2031),
        Candidate(456, "The Simpsons", 1989),
    ]
    assert client.search_movies("Toy Story") == [Candidate(862, "Toy Story", 1995)]
    assert fetch.calls[0] == ("/api/v1/search", {"query": "The Simpsons", "page": "1",
                                                 "language": "en"})  # fmt: skip
    assert client.movie(862) == MovieFacts(date(1995, 11, 22), 81 * 60)


def test_fill_via_seerr_finds_ids_fills_dates_and_handles_missing_seasons_and_episodes(
    db: sqlite3.Connection,
) -> None:
    seed(db)
    stats = fill_gaps(db, SeerrClient(FakeFetch(seerr_routes())), now=NOW)

    assert episodes(db) == [
        (1, 1, "Simpsons Roasting on an Open Fire", "1989-12-17", "tmdb", "/a.mkv"),
        (1, 2, "Bart the Genius", "1990-01-14", "tmdb", None),  # known, not on disk
        (1, 3, "Homers Odyssey", None, None, "/c.mkv"),  # no date anywhere: left empty
        (1, 9, "Not on TMDB", None, None, "/z.mkv"),  # not in TMDB: untouched
        (2, 1, "", None, None, "/d.mkv"),  # its season failed: untouched
    ]
    # The search picked the 1989 show, not the 2031 one with the same title, and kept the id.
    assert db.execute("SELECT tmdb_id FROM shows WHERE id = 'the-simpsons'").fetchone()[0] == 456
    assert stats.matched_by_search == ["the-simpsons", "movie:toy-story"]
    assert stats.shows_without_tmdb_id == ["nowhere"]
    assert stats.failed_seasons == ["the-simpsons S2"]
    assert stats.failed == []
    assert (stats.shows, stats.filled_air_dates, stats.added_episodes) == (1, 1, 1)
    movie = db.execute("SELECT premiere_date, runtime_seconds, tmdb_id FROM movies").fetchone()
    assert tuple(movie) == ("1995-11-22", 4860, 862)
    assert stats.filled_premieres == 1
    run = db.execute("SELECT source, stats FROM import_runs").fetchone()
    assert run["source"] == "tmdb"
    assert json.loads(run["stats"])["provider"] == "seerr"


def test_fill_via_seerr_is_idempotent(db: sqlite3.Connection) -> None:
    seed(db)
    fill_gaps(db, SeerrClient(FakeFetch(seerr_routes())), now=NOW)
    first = episodes(db)
    stats = fill_gaps(db, SeerrClient(FakeFetch(seerr_routes())), now=NOW)
    assert episodes(db) == first
    assert (stats.added_episodes, stats.filled_air_dates, stats.filled_premieres) == (0, 0, 0)
    assert stats.matched_by_search == []  # the ids found last time are reused


def test_a_show_whose_details_fail_is_listed_and_skipped(db: sqlite3.Connection) -> None:
    seed(db)
    routes = seerr_routes()
    routes["/api/v1/tv/456"] = FetchError("HTTP 500 for /api/v1/tv/456")
    stats = fill_gaps(db, SeerrClient(FakeFetch(routes)), now=NOW)
    assert stats.failed == ["the-simpsons"]
    assert stats.filled_premieres == 1  # the movie still filled


def test_tmdb_client_searches_too() -> None:
    fetch = FakeFetch(
        {
            "/3/search/tv": {"results": [{"id": 456, "name": "The Simpsons",
                                          "first_air_date": "1989-12-17"}]},
            "/3/search/movie": {"results": [{"id": 862, "title": "Toy Story",
                                             "release_date": ""}]},
            "/3/movie/862": {"id": 862, "release_date": "1995-11-22", "runtime": 81},
        }
    )  # fmt: skip
    client = TmdbClient(fetch)
    assert client.search_tv("The Simpsons") == [Candidate(456, "The Simpsons", 1989)]
    assert client.search_movies("Toy Story") == [Candidate(862, "Toy Story", None)]
    assert client.movie(862) == MovieFacts(date(1995, 11, 22), 4860)


# Provider selection


def test_tmdb_wins_when_its_token_is_set() -> None:
    env = {"TMDB_TOKEN": "t", "SEERR_URL": "http://seerr:5055", "SEERR_API_KEY": KEY}
    choice = pick_source(env)
    assert choice.provider == "tmdb"
    assert isinstance(choice.source, TmdbClient)


def test_seerr_when_both_of_its_vars_are_set() -> None:
    choice = pick_source({"SEERR_URL": "http://seerr:5055", "SEERR_API_KEY": KEY})
    assert choice.provider == "seerr"
    assert isinstance(choice.source, SeerrClient)


@pytest.mark.parametrize(
    "env", [{}, {"SEERR_URL": "http://seerr:5055"}, {"SEERR_API_KEY": KEY}, {"TMDB_TOKEN": ""}]
)
def test_nothing_usable_is_a_clear_skip(env: dict[str, str]) -> None:
    choice = pick_source(env)
    assert choice.source is None
    assert "TMDB_TOKEN" in choice.message and "SEERR_URL and SEERR_API_KEY" in choice.message
    assert KEY not in choice.message


def test_cli_skips_with_the_message_when_nothing_is_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    for name in ("TMDB_TOKEN", "SEERR_URL", "SEERR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["tmdb", "--db", str(tmp_path / "x.db")]) == 2
    err = capsys.readouterr().err
    assert "TMDB_TOKEN" in err and "SEERR_URL and SEERR_API_KEY" in err


def test_seerr_fetch_sends_the_key_as_a_header_and_accepts_an_api_suffix() -> None:
    seen: dict[str, object] = {}

    def make_fetch(base_url: str, headers: Mapping[str, str], timeout: float = 30) -> Any:
        seen.update(base=base_url, headers=dict(headers))
        return lambda path, params=None: {}

    seerr_http_fetch("http://seerr:5055/api/v1/", KEY, make_fetch=make_fetch)
    assert seen == {"base": "http://seerr:5055", "headers": {"X-Api-Key": KEY}}


# Key redaction, through a fake HTTP transport


class FakeTransport:
    """Stands in for urllib's urlopen: records each request and answers or fails."""

    def __init__(self, answer: object = None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float = 30) -> io.BytesIO:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return io.BytesIO(json.dumps(self.answer).encode())


def test_the_key_travels_only_in_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeTransport(answer=SEARCH)
    monkeypatch.setattr(http, "urlopen", transport)
    fetch = seerr_http_fetch("http://seerr.invalid:5055", KEY)
    SeerrClient(fetch).search_tv("Ed, Edd n Eddy (1999)!")
    (request,) = transport.requests
    assert request.get_header("X-api-key") == KEY
    assert KEY not in request.full_url
    # Seerr wants the query percent-encoded (spaces as %20, no bare reserved characters)
    assert "query=Ed%2C%20Edd%20n%20Eddy%20%281999%29%21" in request.full_url


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError(
            f"http://seerr.invalid/?k={KEY}", 401, f"bad key {KEY}", Message(), None
        ),
        urllib.error.URLError(f"connection refused, header X-Api-Key: {KEY}"),
        TimeoutError(f"timed out sending {KEY}"),
    ],
)
def test_errors_never_carry_the_key(error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "urlopen", FakeTransport(error=error))
    fetch = seerr_http_fetch("http://seerr.invalid:5055", KEY)
    with pytest.raises(FetchError) as caught:
        fetch("/api/v1/tv/456", None)
    err = caught.value
    assert isinstance(err, OSError)  # fill_gaps lists the show and carries on
    for text in (str(err), repr(err), repr(err.args)):
        assert KEY not in text
    assert err.__cause__ is None and err.__suppress_context__  # no chained original
    assert "/api/v1/tv/456" in str(err)


def test_a_failing_seerr_run_prints_no_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.delenv("TMDB_TOKEN", raising=False)
    monkeypatch.setenv("SEERR_URL", "http://seerr.invalid:5055")
    monkeypatch.setenv("SEERR_API_KEY", KEY)
    error = urllib.error.HTTPError("http://seerr.invalid", 401, f"no {KEY}", Message(), None)
    monkeypatch.setattr(http, "urlopen", FakeTransport(error=error))
    db_path = tmp_path / "retro-cable.db"
    seeded = connect(db_path)
    migrate(seeded)
    seed(seeded)
    seeded.close()

    assert cli.main(["tmdb", "--db", str(db_path)]) == 0
    captured = capsys.readouterr()
    assert KEY not in captured.out and KEY not in captured.err
    stats = json.loads(captured.out)
    assert stats["provider"] == "seerr"
    assert stats["failed"] == ["nowhere", "the-simpsons", "movie:toy-story"]  # searches failed
    assert "using Seerr" in captured.err
