"""The TV endpoints: /channels, /guide, /tune and signed /media."""

import itertools
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


def test_guide_labels_holiday_episodes(client: TestClient, tmp_path: Path) -> None:
    db = connect(tmp_path / "rc.db")
    db.execute(
        "UPDATE episodes SET title = 'Treehouse of Horror ' || id"
        " WHERE show_id = 'the-simpsons' AND episode = 5"
    )
    db.close()
    body = client.get(
        "/guide", params={"year": 1998, "date": "10-31", "start": "20:00", "hours": 3}
    ).json()
    ktvu = next(r for r in body["channels"] if r["channel"]["id"] == "ktvu")
    themed = [s for s in ktvu["slots"] if s["holiday"] == "halloween"]
    assert themed and all(s["holiday_name"] == "Halloween" for s in themed)
    assert all("Treehouse" in s["episode_title"] for s in themed)
    plain = [s for s in ktvu["slots"] if s["kind"] == "bumper"]
    assert plain and all(s["holiday"] is None and s["holiday_name"] is None for s in plain)
    # Outside the window nothing is labelled.
    nov = client.get("/guide", params={"year": 1998, "date": "11-05", "hours": 6}).json()
    assert not any(s["holiday"] for r in nov["channels"] for s in r["slots"])


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
