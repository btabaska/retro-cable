"""The /admin API: custom channels are catalog inputs, checked before save, with undo."""

import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from retro_cable.app import Settings, create_app
from retro_cable.db import connect
from tests.conftest import MakeCatalog
from tests.test_api_tv import seed_db

REAL_NOW = datetime(2026, 11, 1, 0, 14, tzinfo=UTC)  # 8:14 PM Oct 31 in New York


def add_uncarried(path: Path) -> None:
    """Two shows in the library that no channel carries."""
    db = connect(path)
    for show, title, first in (
        ("courage-the-cowardly-dog", "Courage the Cowardly Dog", datetime(1999, 11, 12)),
        ("johnny-bravo", "Johnny Bravo", datetime(1997, 7, 7)),
    ):
        db.execute("INSERT INTO shows (id, title) VALUES (?, ?)", (show, title))
        for i in range(30):
            db.execute(
                "INSERT INTO episodes (show_id, season, episode, title, air_date,"
                " runtime_seconds, file_path) VALUES (?, 1, ?, ?, ?, 1320, ?)",
                (show, i + 1, f"Episode {i + 1}",
                 (first + timedelta(weeks=i)).date().isoformat(), f"{show}.mp4"),
            )  # fmt: skip
    db.close()


@pytest.fixture
def catalog_dir(make_catalog: MakeCatalog) -> Path:
    return make_catalog()


@pytest.fixture
def client(catalog_dir: Path, tmp_path: Path) -> TestClient:
    media = tmp_path / "media"
    media.mkdir()
    db = tmp_path / "rc.db"
    seed_db(db, media)
    add_uncarried(db)
    settings = Settings(
        catalog_dir=catalog_dir, db_path=db, media_dir=media, media_secret="s", seed=7
    )
    return TestClient(create_app(settings, clock=lambda: REAL_NOW))


def toons(**over: Any) -> dict[str, Any]:
    draft = {
        "number": 50,
        "call_sign": "toons",
        "name": "Brandon's Toons",
        "type": "network",
        "library": True,
        "launch_year": None,
        "shows": [{"show": "courage-the-cowardly-dog"}, {"show": "johnny-bravo"}],
    }
    return {**draft, **over}


def version(client: TestClient) -> str:
    state: str = client.get("/admin/state").json()["catalog_version"]
    return state


def test_library_lists_uncarried_shows_first(client: TestClient) -> None:
    items = client.get("/admin/library").json()["items"]
    ids = [i["id"] for i in items]
    assert set(ids) >= {"courage-the-cowardly-dog", "johnny-bravo", "the-simpsons"}
    uncarried = [i for i in items if not i["channels"]]
    assert ids[: len(uncarried)] == [i["id"] for i in uncarried]
    simpsons = next(i for i in items if i["id"] == "the-simpsons")
    assert simpsons["channels"] == [{"id": "ktvu", "name": "KTVU (Fox)", "number": 2}]
    courage = next(i for i in items if i["id"] == "courage-the-cowardly-dog")
    assert courage["first_year"] == 1999 and courage["episodes"] == 30
    assert [
        i["id"] for i in client.get("/admin/library", params={"q": "brav"}).json()["items"]
    ] == ["johnny-bravo"]


def test_list_channels_marks_yaml_channels_read_only(client: TestClient) -> None:
    body = client.get("/admin/channels").json()
    assert body["state"] == {
        "revision": 0,
        "catalog_version": body["state"]["catalog_version"],
        "can_undo": False,
    }
    assert [c["number"] for c in body["channels"]] == sorted(c["number"] for c in body["channels"])
    assert all(not c["custom"] for c in body["channels"])
    ktvu = client.get("/admin/channels/ktvu").json()
    assert ktvu["custom"] is False and ktvu["draft"]["call_sign"] == "KTVU"


@pytest.mark.parametrize(
    ("draft", "field", "message"),
    [
        (toons(number=2), "number", "Number 2 is already KTVU (Fox)."),
        (toons(library=False, launch_year=1983), "launch_year", "Channel launches 1983"),
        (toons(library=False, launch_year=None), "launch_year", "Pick the year"),
        (toons(shows=[]), "shows", "Add at least one show."),
        (toons(shows=[{"show": "not-a-show"}]), "shows.0", "isn't in the library"),
        (toons(call_sign="TOOLONGX"), "call_sign", "up to 6"),
    ],
)
def test_check_rejects_bad_drafts_inline(
    client: TestClient, draft: dict[str, Any], field: str, message: str
) -> None:
    result = client.post("/admin/check", json={"draft": draft}).json()
    assert result["ok"] is False
    assert any(e["field"] == field and message in e["message"] for e in result["errors"]), result
    # And saving refuses with the same problems, changing nothing.
    before = version(client)
    response = client.post("/admin/channels", json={"draft": draft})
    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert version(client) == before


def test_number_collision_only_counts_dates_the_channel_exists(client: TestClient) -> None:
    # Cartoon Network is 27 from its 1992 launch, so a 27 that left the dial is a collision only
    # for a channel that overlaps it; a library channel is on every date, so it collides.
    result = client.post("/admin/check", json={"draft": toons(number=27)}).json()
    assert any("Number 27 is already Cartoon Network" in e["message"] for e in result["errors"])


def test_warns_when_a_show_starts_after_launch(client: TestClient) -> None:
    draft = toons(library=False, launch_year=1995)
    result = client.post("/admin/check", json={"draft": draft, "year": 2000}).json()
    assert result["ok"] is True
    assert any(
        w["field"] == "shows.0"
        and "Courage the Cowardly Dog won't air before its 1999 debut" in w["message"]
        for w in result["warnings"]
    )


def test_preview_week_shows_both_shows_back_to_back(client: TestClient) -> None:
    before = version(client)
    body = client.post(
        "/admin/preview", json={"draft": toons(), "year": 2000, "date": "10-31"}
    ).json()
    assert body["check"]["ok"] is True
    assert body["channel"]["number"] == 50
    days = body["days"]
    assert next(d["weekday"] for d in days) == "Sunday" and len(days) == 7
    saturday = next(d for d in days if d["date"] == "2000-10-31")
    assert saturday["weekday"] == "Tuesday"  # Oct 31 2000 was a Tuesday
    shows = {s["show"] for d in days for s in d["slots"] if s["kind"] == "episode"}
    assert shows == {"courage-the-cowardly-dog", "johnny-bravo"}
    for d in days:
        slots = d["slots"]
        for a, b in itertools.pairwise(slots):
            assert a["end"] == b["start"]
    # Preview never saves.
    assert version(client) == before
    assert "custom-draft" not in {
        c["id"] for c in client.get("/channels", params={"year": 2000}).json()["channels"]
    }


def test_create_tune_and_undo(client: TestClient) -> None:
    before_version = version(client)
    before_guide = client.get("/guide", params={"year": 2000, "date": "10-31", "hours": 6}).text

    saved = client.post("/admin/channels", json={"draft": toons()})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    channel_id = body["channel_id"]
    assert channel_id == "custom-brandon-s-toons"
    assert body["message"] == "Created Brandon's Toons"
    assert body["state"]["can_undo"] is True
    assert body["state"]["catalog_version"] != before_version

    # On the dial and in the guide right away, with no restart.
    lineup = client.get("/channels", params={"year": 2000}).json()["channels"]
    toon = next(c for c in lineup if c["id"] == channel_id)
    assert toon["number"] == 50 and toon["call_sign"] == "TOONS" and toon["on_air"]
    guide = client.get("/guide", params={"year": 2000, "date": "10-31", "hours": 6}).json()
    assert channel_id in [r["channel"]["id"] for r in guide["channels"]]
    tuned = client.get(f"/tune/{channel_id}", params={"year": 2000}).json()
    assert tuned["current"]["show"] in {"courage-the-cowardly-dog", "johnny-bravo"}
    assert tuned["current"]["media_url"]

    # The catalog YAML is untouched.
    edited = client.get(f"/admin/channels/{channel_id}").json()
    assert edited["custom"] is True and edited["draft"]["number"] == 50

    undone = client.post("/admin/undo").json()
    assert undone["message"] == "Restored"
    assert undone["state"]["catalog_version"] == before_version
    assert undone["state"]["can_undo"] is False
    assert channel_id not in {
        c["id"] for c in client.get("/channels", params={"year": 2000}).json()["channels"]
    }
    after_guide = client.get("/guide", params={"year": 2000, "date": "10-31", "hours": 6}).text
    assert after_guide == before_guide  # byte-identical
    assert client.post("/admin/undo").status_code == 409


def test_catalog_files_are_never_written(client: TestClient, catalog_dir: Path) -> None:
    snapshot = {p.name: p.read_bytes() for p in catalog_dir.glob("*.yaml")}
    client.post("/admin/channels", json={"draft": toons()})
    channel_id = "custom-brandon-s-toons"
    client.delete(f"/admin/channels/{channel_id}")
    assert {p.name: p.read_bytes() for p in catalog_dir.glob("*.yaml")} == snapshot


def test_update_and_delete(client: TestClient) -> None:
    channel_id = client.post("/admin/channels", json={"draft": toons()}).json()["channel_id"]
    v1 = version(client)
    renamed = client.put(
        f"/admin/channels/{channel_id}", json={"draft": toons(name="Toon Town", number=51)}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["message"] == "Saved Toon Town"
    assert renamed.json()["channel_id"] == channel_id  # editing keeps the id
    lineup = client.get("/channels", params={"year": 2000}).json()["channels"]
    assert next(c for c in lineup if c["id"] == channel_id)["number"] == 51
    assert version(client) != v1

    deleted = client.delete(f"/admin/channels/{channel_id}").json()
    assert deleted["message"] == "Deleted 51 Toon Town"
    assert channel_id not in {
        c["id"] for c in client.get("/channels", params={"year": 2000}).json()["channels"]
    }
    # Undo the delete: back to the renamed channel and its version.
    assert client.post("/admin/undo").json()["state"]["catalog_version"] == version(client)
    assert version(client) != v1
    lineup = client.get("/channels", params={"year": 2000}).json()["channels"]
    assert next(c for c in lineup if c["id"] == channel_id)["number"] == 51


def test_yaml_channels_cannot_be_edited_or_deleted(client: TestClient) -> None:
    assert client.delete("/admin/channels/ktvu").status_code == 403
    assert client.put("/admin/channels/ktvu", json={"draft": toons()}).status_code == 403
    assert client.delete("/admin/channels/custom-nope").status_code == 404


def test_launched_channel_hidden_before_launch(client: TestClient) -> None:
    draft = toons(library=False, launch_year=2001, number=52)
    channel_id = client.post("/admin/channels", json={"draft": draft}).json()["channel_id"]
    on = {
        c["id"]: c["on_air"]
        for c in client.get("/channels", params={"year": 2000}).json()["channels"]
    }
    assert on[channel_id] is False
    assert client.get(f"/tune/{channel_id}", params={"year": 2000}).status_code == 404
    on = {
        c["id"]: c["on_air"]
        for c in client.get("/channels", params={"year": 2002}).json()["channels"]
    }
    assert on[channel_id] is True


def add_scanned(path: Path, *, normalized: bool, year: int | None) -> None:
    """An uncarried show as `ingest scan` stores it: raw absolute paths, no dates, no runtime."""
    db = connect(path)
    db.execute(
        "INSERT INTO shows (id, title, year) VALUES ('ed-edd-n-eddy', 'Ed, Edd n Eddy', ?)",
        (year,),
    )
    for i in range(3):
        db.execute(
            "INSERT INTO episodes (show_id, season, episode, file_path, media_path, media_seconds)"
            " VALUES ('ed-edd-n-eddy', 1, ?, ?, ?, ?)",
            (i + 1, f"/volume3/tv/Ed, Edd n Eddy/Season 1/S01E0{i + 1}.mkv",
             f"tv/ed-edd-n-eddy/s01/ed-edd-n-eddy-s01e0{i + 1}.mp4" if normalized else None,
             1320 if normalized else None),
        )  # fmt: skip
    db.close()


def scanned_client(
    catalog_dir: Path, tmp_path: Path, *, normalized: bool, year: int | None
) -> TestClient:
    media = tmp_path / "media"
    media.mkdir(parents=True)
    db = tmp_path / "rc.db"
    seed_db(db, media)
    add_scanned(db, normalized=normalized, year=year)
    settings = Settings(
        catalog_dir=catalog_dir, db_path=db, media_dir=media, media_secret="s", seed=7
    )
    return TestClient(create_app(settings, clock=lambda: REAL_NOW))


def eddy(client: TestClient) -> dict[str, Any]:
    items = client.get("/admin/library", params={"q": "eddy"}).json()["items"]
    item: dict[str, Any] = items[0]
    return item


def test_a_scanned_show_counts_episodes_once_normalized(catalog_dir: Path, tmp_path: Path) -> None:
    raw = scanned_client(catalog_dir, tmp_path / "raw", normalized=False, year=1999)
    assert eddy(raw)["episodes"] == 0  # raw sources can't play yet
    draft = toons(shows=[{"show": "ed-edd-n-eddy"}])
    warnings = raw.post("/admin/check", json={"draft": draft}).json()["warnings"]
    assert any("Ed, Edd n Eddy has no playable files yet" in w["message"] for w in warnings)

    done = scanned_client(catalog_dir, tmp_path / "done", normalized=True, year=1999)
    item = eddy(done)
    assert item["episodes"] == 3 and item["first_year"] == 1999  # Dec 31 of the folder year
    saved = done.post("/admin/channels", json={"draft": draft})
    assert saved.status_code == 200, saved.text
    tuned = done.get(f"/tune/{saved.json()['channel_id']}", params={"year": 2003}).json()
    assert tuned["current"]["show"] == "ed-edd-n-eddy"


def test_short_samples_dont_count_as_playable_episodes(catalog_dir: Path, tmp_path: Path) -> None:
    """The picker counts what the schedule can air: a 2-minute encode of a 23-minute source is
    a sample, and stays off the air (load_library) unless RC_ALLOW_SHORT_MEDIA is set."""
    media = tmp_path / "media"
    media.mkdir(parents=True)
    db_path = tmp_path / "rc.db"
    seed_db(db_path, media)
    add_scanned(db_path, normalized=True, year=1999)
    db = connect(db_path)
    db.execute("UPDATE episodes SET source_seconds = 1380, media_seconds = 120 WHERE episode = 2")
    db.close()

    def count(allow: bool) -> int:
        settings = Settings(
            catalog_dir=catalog_dir, db_path=db_path, media_dir=media, media_secret="s",
            seed=7, allow_short_media=allow,
        )  # fmt: skip
        return int(eddy(TestClient(create_app(settings, clock=lambda: REAL_NOW)))["episodes"])

    assert count(allow=False) == 2
    assert count(allow=True) == 3


def test_an_undated_show_warns_on_every_channel_until_it_has_a_pretend_premiere(
    catalog_dir: Path, tmp_path: Path
) -> None:
    client = scanned_client(catalog_dir, tmp_path, normalized=True, year=None)
    draft = toons(library=False, launch_year=1995, shows=[{"show": "ed-edd-n-eddy"}])
    warnings = client.post("/admin/check", json={"draft": draft, "year": 2000}).json()["warnings"]
    assert any(w["field"] == "shows.0" and "no air dates" in w["message"] for w in warnings), (
        warnings
    )
    # Library channels no longer lift air dates (M2), so it warns there too...
    library = toons(shows=[{"show": "ed-edd-n-eddy"}])
    warnings = client.post("/admin/check", json={"draft": library}).json()["warnings"]
    assert any(w["field"] == "shows.0" and "no air dates" in w["message"] for w in warnings)
    # ...until a pretend premiere dates it on this channel.
    dated = toons(shows=[{"show": "ed-edd-n-eddy", "premiere": "1999-01-04"}])
    assert client.post("/admin/check", json={"draft": dated}).json()["warnings"] == []


# --- conversion progress (M2 B13) ---------------------------------------------------------


def test_media_progress_is_404_until_the_rig_writes_it(client: TestClient) -> None:
    assert client.get("/admin/media/progress").status_code == 404


def test_media_progress_reads_the_rigs_file(catalog_dir: Path, tmp_path: Path) -> None:
    import json

    media = tmp_path / "media"
    media.mkdir()
    (media / "progress.json").write_text(json.dumps({
        "format": "retro-cable-progress/1",
        "updated_at": (REAL_NOW - timedelta(minutes=2)).isoformat(),
        "state": "running", "encoder": "cpu", "gpu_busy": True, "current": "dexters-laboratory",
        "recent_seconds": [52.1, 49.0],
        "groups": [
            {"key": "dexters-laboratory", "title": "Dexter's Laboratory", "done": 38,
             "total": 52, "failed": 2},
            {"key": "_movies", "title": "Movies", "done": 0, "total": 4, "failed": 0},
        ],
    }))  # fmt: skip
    settings = Settings(catalog_dir=catalog_dir, db_path=tmp_path / "rc.db", media_dir=media,
                        media_secret="s")  # fmt: skip
    client = TestClient(create_app(settings, clock=lambda: REAL_NOW))
    response = client.get("/admin/media/progress")
    assert response.status_code == 200
    assert response.json() == {
        "done": 38, "total": 56, "failed": 2, "encoder": "cpu", "gpu_busy": True,
        "recent_seconds": [52.1, 49.0],
        "shows": [
            {"title": "Dexter's Laboratory", "done": 38, "total": 52, "failed": 2,
             "state": "converting"},
            {"title": "Movies", "done": 0, "total": 4, "failed": 0, "state": "queued"},
        ],
    }  # fmt: skip
