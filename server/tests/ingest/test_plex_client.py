import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from retro_cable.ingest.plex import PlexClient, http_fetch
from tests.ingest.conftest import RecordedFetch


def test_sections_keep_only_shows_and_movies(plex_fetch: RecordedFetch) -> None:
    sections = PlexClient(plex_fetch).sections()
    assert [(s.key, s.type, s.title) for s in sections] == [
        ("1", "show", "TV Shows"),
        ("2", "movie", "Movies"),
    ]


def test_shows_parse_external_ids(plex_fetch: RecordedFetch) -> None:
    shows = PlexClient(plex_fetch).shows("1")
    simpsons = shows[0]
    assert simpsons.rating_key == "101"
    assert simpsons.title == "The Simpsons"
    assert simpsons.year == 1989
    assert (simpsons.tmdb_id, simpsons.tvdb_id, simpsons.imdb_id) == (456, 71663, "tt0000101")
    assert shows[2].tmdb_id is None
    path, params = plex_fetch.calls[-1]
    assert path == "/library/sections/1/all"
    assert params == {"type": "2", "includeGuids": "1"}


def test_episodes_parse_dates_runtimes_and_files(plex_fetch: RecordedFetch) -> None:
    episodes = PlexClient(plex_fetch).episodes("101")
    treehouse = episodes[0]
    assert (treehouse.season, treehouse.episode) == (10, 4)
    assert treehouse.air_date == date(1998, 10, 25)
    assert treehouse.runtime_seconds == 1320
    assert treehouse.file_path == "/data/tv/The Simpsons (1989)/Season 10/The Simpsons - S10E04.mkv"
    assert treehouse.file_size == 234567890
    special = episodes[2]
    assert (special.season, special.episode, special.air_date) == (0, 1, None)


def test_episodes_without_a_number_are_skipped(plex_fetch: RecordedFetch) -> None:
    titles = [e.title for e in PlexClient(plex_fetch).episodes("101")]
    assert "Unnumbered Extra" not in titles
    assert len(titles) == 3


def test_movies_parse_premiere_and_file(plex_fetch: RecordedFetch) -> None:
    movie = PlexClient(plex_fetch).movies("2")[0]
    assert movie.title == "Halloweentown"
    assert movie.premiere_date == date(1998, 10, 17)
    assert movie.runtime_seconds == 5040
    assert movie.tmdb_id == 16784
    assert movie.file_path == "/data/movies/Halloweentown (1998)/Halloweentown (1998).mkv"


def test_http_fetch_sends_the_token_as_a_header_not_in_the_url() -> None:
    seen: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen["path"] = self.path
            seen["token"] = self.headers.get("X-Plex-Token", "")
            seen["accept"] = self.headers.get("Accept", "")
            body = b'{"MediaContainer": {"Directory": []}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        fetch = http_fetch(f"http://127.0.0.1:{server.server_address[1]}", "s3cret")
        assert fetch("/library/sections", {"a": "1"}) == {"MediaContainer": {"Directory": []}}
    finally:
        server.shutdown()
    assert seen == {
        "path": "/library/sections?a=1",
        "token": "s3cret",
        "accept": "application/json",
    }
