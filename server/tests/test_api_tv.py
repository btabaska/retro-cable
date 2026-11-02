"""The TV endpoints: /channels, /guide, /tune and signed /media."""

import itertools
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from retro_cable.app import Settings, create_app
from retro_cable.db import connect, migrate
from tests.conftest import MakeCatalog

# 8:14 PM on Oct 31 in New York (EDT in 2026).
REAL_NOW = datetime(2026, 11, 1, 0, 14, tzinfo=UTC)


def seed_db(path: Path, media: Path) -> None:
    db = connect(path)
    migrate(db)
    db.execute("INSERT INTO shows (id, title) VALUES ('the-simpsons', 'The Simpsons')")
    db.execute(
        "INSERT INTO shows (id, title) VALUES ('dexters-laboratory', 'Dexter''s Laboratory')"
    )
    first = datetime(1989, 12, 17)
    for i in range(200):
        aired = (first + timedelta(weeks=i)).date().isoformat()
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, title, air_date, runtime_seconds,"
            " file_path) VALUES ('the-simpsons', ?, ?, ?, ?, 1320, 'simpsons.mp4')",
            (1 + i // 22, 1 + i % 22, f"Episode {i + 1}", aired),
        )
    for i in range(50):
        aired = (datetime(1996, 4, 28) + timedelta(weeks=i)).date().isoformat()
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, title, air_date, runtime_seconds,"
            " file_path) VALUES ('dexters-laboratory', 1, ?, ?, ?, 1320, 'dexter.mp4')",
            (i + 1, f"Episode {i + 1}", aired),
        )
    db.execute(
        "INSERT INTO bumpers (channel_id, file_path, runtime_seconds) VALUES ('ktvu', 'b.mp4', 10)"
    )
    db.close()
    (media / "simpsons.mp4").write_bytes(b"0123456789" * 100)


@pytest.fixture
def client(make_catalog: MakeCatalog, tmp_path: Path) -> TestClient:
    media = tmp_path / "media"
    media.mkdir()
    db = tmp_path / "rc.db"
    seed_db(db, media)
    settings = Settings(
        catalog_dir=make_catalog(),
        db_path=db,
        media_dir=media,
        media_secret="test-secret",
        seed=7,
    )
    return TestClient(create_app(settings, clock=lambda: REAL_NOW))


def test_channels_lists_the_dial_for_a_year(client: TestClient) -> None:
    body = client.get("/channels", params={"year": 1991}).json()
    assert body["date"] == "1991-10-31"
    assert body["weekday"] == "Thursday"
    by_id = {c["id"]: c for c in body["channels"]}
    assert by_id["ktvu"]["on_air"] is True
    assert by_id["ktvu"]["number"] == 2
    assert by_id["cartoon-network"]["on_air"] is False
    assert by_id["cartoon-network"]["launch"] == "1992-10-01"
    assert by_id["disney-movies"]["on_air"] is True


def test_channels_rejects_years_outside_the_range(client: TestClient) -> None:
    assert client.get("/channels", params={"year": 1989}).status_code == 422
    assert client.get("/channels", params={"year": 2011}).status_code == 422


def test_guide_returns_a_full_back_to_back_day(client: TestClient) -> None:
    body = client.get("/guide", params={"year": 1998, "date": "10-31"}).json()
    assert body["start"] == "1998-10-31T00:00:00-05:00"
    ids = [row["channel"]["id"] for row in body["channels"]]
    assert ids == ["ktvu", "cartoon-network", "disney-movies"]  # on the dial, by number
    for channel in body["channels"]:
        slots = channel["slots"]
        assert slots, channel["channel"]["id"]
        assert datetime.fromisoformat(slots[0]["start"]) <= datetime.fromisoformat(body["start"])
        assert datetime.fromisoformat(slots[-1]["end"]) >= datetime.fromisoformat(body["end"])
        for a, b in itertools.pairwise(slots):
            assert a["end"] == b["start"]
        assert all("path" not in s and "media_url" not in s for s in slots)


def test_guide_window(client: TestClient) -> None:
    body = client.get(
        "/guide", params={"year": 1998, "date": "10-31", "start": "20:00", "hours": 1.5}
    ).json()
    start = datetime.fromisoformat(body["start"])
    end = datetime.fromisoformat(body["end"])
    assert (start.hour, start.minute) == (20, 0)
    assert end - start == timedelta(minutes=90)


def test_tune_returns_current_offset_and_next(client: TestClient) -> None:
    body = client.get("/tune/ktvu", params={"year": 1998}).json()
    now = datetime.fromisoformat(body["now"])
    assert (now.year, now.month, now.day, now.hour, now.minute) == (1998, 10, 31, 20, 14)
    current = body["current"]
    start = datetime.fromisoformat(current["start"])
    assert body["offset_seconds"] == pytest.approx((now - start).total_seconds())
    assert 0 <= body["offset_seconds"] < current["seconds"]
    assert len(body["next"]) == 3
    assert body["next"][0]["start"] == current["end"]
    assert body["channel"]["call_sign"] == "KTVU"


def test_tune_hidden_channel_is_404(client: TestClient) -> None:
    assert client.get("/tune/cartoon-network", params={"year": 1991}).status_code == 404
    assert client.get("/tune/nope", params={"year": 1998}).status_code == 404


def test_media_urls_are_signed_and_expire(client: TestClient) -> None:
    body = client.get("/tune/ktvu", params={"year": 1998}).json()
    url = next(s["media_url"] for s in [body["current"], *body["next"]] if s["media_url"])
    simpsons = next(
        s["media_url"] for s in [body["current"], *body["next"]] if s["kind"] == "episode"
    )
    assert simpsons is not None
    parsed = urlparse(simpsons)
    assert parsed.path == "/media/simpsons.mp4"
    query = parse_qs(parsed.query)
    assert set(query) == {"exp", "sig"}
    assert url

    ok = client.get(simpsons.removeprefix("/api"), headers={"Range": "bytes=0-9"})
    assert ok.status_code == 206
    assert ok.content == b"0123456789"

    tampered = simpsons.replace(query["sig"][0], "0" * len(query["sig"][0]))
    assert client.get(tampered).status_code == 403
    expired = simpsons.replace(f"exp={query['exp'][0]}", "exp=1")
    assert client.get(expired).status_code == 403
    assert client.get("/media/../rc.db", params={"exp": "9999999999", "sig": "x"}).status_code in (
        403,
        404,
    )


def test_media_auth_checks_the_signature_caddy_forwards(client: TestClient) -> None:
    """Caddy's forward_auth sends the original URI in X-Forwarded-Uri; a 2xx lets file_server
    answer, anything else goes back to the client as is."""
    body = client.get("/tune/ktvu", params={"year": 1998}).json()
    signed = next(
        s["media_url"] for s in [body["current"], *body["next"]] if s["kind"] == "episode"
    )
    query = parse_qs(urlparse(signed).query)

    def auth(uri: str, method: str = "GET") -> int:
        response = client.request(method, "/media-auth", headers={"X-Forwarded-Uri": uri})
        return response.status_code

    assert auth(signed) == 204
    assert auth(signed, "HEAD") == 204
    assert auth(signed.replace(query["sig"][0], "f" * 64)) == 403  # tampered signature
    assert auth(signed.replace("simpsons.mp4", "dexter.mp4")) == 403  # tampered path
    assert auth(signed.replace(f"exp={query['exp'][0]}", "exp=1")) == 403  # expired
    assert auth(signed.split("?")[0]) == 403  # no signature
    assert auth("/elsewhere/simpsons.mp4?" + urlparse(signed).query) == 403
    assert client.get("/media-auth").status_code == 403  # no header at all


def test_schedules_pick_up_writes_still_in_the_wal(make_catalog: MakeCatalog, tmp_path: Path) -> None:
    """A long ingest run keeps its connection open, so its commits sit in the -wal file and
    the main database file's mtime doesn't move until a checkpoint."""
    media = tmp_path / "media"
    media.mkdir()
    db_path = tmp_path / "rc.db"
    seed_db(db_path, media)
    settings = Settings(make_catalog(), db_path, media, media_secret="k", seed=7)
    app = TestClient(create_app(settings, clock=lambda: REAL_NOW))

    def media_paths() -> set[str]:
        body = app.get("/tune/ktvu", params={"year": 1998}).json()
        slots = [body["current"], *body["next"]]
        return {urlparse(s["media_url"]).path for s in slots if s["kind"] == "episode"}

    assert media_paths() == {"/media/simpsons.mp4"}
    writer = connect(db_path)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("UPDATE episodes SET media_path = 'tv/n.mp4', media_seconds = 1320")
    wal = db_path.with_name(db_path.name + "-wal")
    assert wal.exists()
    stamp = wal.stat().st_mtime + 5
    os.utime(wal, (stamp, stamp))  # coarse filesystem clocks: make the change visible
    assert media_paths() == {"/media/tv/n.mp4"}
    writer.close()


def test_tune_plays_the_normalized_file_when_there_is_one(
    make_catalog: MakeCatalog, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    db_path = tmp_path / "rc.db"
    seed_db(db_path, media)
    db = connect(db_path)
    db.execute(
        "UPDATE episodes SET file_path = '/volume3/tv/The Simpsons/S01E01.mkv',"
        " media_path = 'tv/the-simpsons/s01/the-simpsons-s01e01.mp4', media_seconds = 1320"
        " WHERE show_id = 'the-simpsons'"
    )
    db.close()
    settings = Settings(make_catalog(), db_path, media, media_secret="k", seed=7)
    app = TestClient(create_app(settings, clock=lambda: REAL_NOW))
    body = app.get("/tune/ktvu", params={"year": 1998}).json()
    slots = [body["current"], *body["next"]]
    urls = [s["media_url"] for s in slots if s["kind"] == "episode"]
    assert urls
    paths = {urlparse(u).path for u in urls}
    assert paths == {"/media/tv/the-simpsons/s01/the-simpsons-s01e01.mp4"}
