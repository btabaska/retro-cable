"""Pretend (synthetic) premieres set in /admin: one (show, channel) pair, inputs only (M2 A)."""

from datetime import date
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests import test_api_admin
from tests.test_api_admin import toons, version

# The admin API fixtures: a fixture catalog and a seeded database behind a test client.
catalog_dir = test_api_admin.catalog_dir
client = test_api_admin.client

GUIDE: dict[str, Any] = {
    "year": 1996,
    "date": "05-03",
    "hours": 24,
}  # a Friday, Dexter's first carried week


def guide_slots(client: TestClient, channel: str, **params: Any) -> list[dict[str, Any]]:
    body = client.get("/guide", params={**GUIDE, **params}).json()
    row = next(r for r in body["channels"] if r["channel"]["id"] == channel)
    slots: list[dict[str, Any]] = row["slots"]
    return slots


def dexter(client: TestClient, channel: str) -> dict[str, Any]:
    shows = client.get(f"/admin/channels/{channel}").json()["shows"]
    found: dict[str, Any] = next(s for s in shows if s["show"] == "dexters-laboratory")
    return found


def test_channel_view_lists_each_show_with_its_dates(client: TestClient) -> None:
    row = dexter(client, "cartoon-network")
    assert row == {
        "show": "dexters-laboratory",
        "kind": "show",
        "debut": "1996-04-28",
        "premiere": None,
        "premiere_source": None,
        "first_air": "1996-04-28",
        "last_air": "1997-04-06",
        "on_air_until": None,
        "airs": True,
    }


def test_set_a_premiere_on_a_yaml_channel(client: TestClient, catalog_dir: Path) -> None:
    snapshot = {p.name: p.read_bytes() for p in catalog_dir.glob("*.yaml")}
    before_version = version(client)
    before = [s for s in guide_slots(client, "cartoon-network") if s["show"]]
    assert before and not any(s["transplant"] for s in before)

    saved = client.put(
        "/admin/channels/cartoon-network/premieres/dexters-laboratory",
        json={"premiere": "1996-01-05"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["message"] == "Set the pretend premiere of Dexter's Laboratory"
    assert body["show"]["premiere"] == "1996-01-05"
    assert body["show"]["premiere_source"] == "admin"
    # S1E1 moves to the premiere; the last episode keeps its spacing (49 weeks later).
    assert body["show"]["first_air"] == "1996-01-05"
    assert (
        body["show"]["last_air"]
        == (date(1996, 1, 5) + (date(1997, 4, 6) - date(1996, 4, 28))).isoformat()
    )
    assert body["show"]["debut"] == "1996-04-28"  # the real debut is unchanged
    assert body["state"]["can_undo"] is True
    assert body["state"]["catalog_version"] != before_version

    # The guide follows on the next read, with no re-import: shifted dates, transplant mark.
    after = [s for s in guide_slots(client, "cartoon-network") if s["show"] == "dexters-laboratory"]
    assert after and all(s["transplant"] for s in after)
    assert all(date.fromisoformat(s["air_date"]) <= date(1996, 5, 4) for s in after)
    assert min(date.fromisoformat(s["air_date"]) for s in after) < date(1996, 4, 28)
    # Inputs only: the YAML files are untouched.
    assert {p.name: p.read_bytes() for p in catalog_dir.glob("*.yaml")} == snapshot

    # Undo restores the old version and the old guide.
    assert client.post("/admin/undo").json()["state"]["catalog_version"] == before_version
    assert [s for s in guide_slots(client, "cartoon-network") if s["show"]] == before


def test_a_premiere_changes_only_its_own_channel(client: TestClient) -> None:
    # Dexter also on a custom library channel: a premiere on Cartoon Network leaves it alone.
    shows = [{"show": "dexters-laboratory"}, {"show": "johnny-bravo"}]
    channel_id = client.post("/admin/channels", json={"draft": toons(shows=shows)}).json()[
        "channel_id"
    ]
    custom_before = dexter(client, channel_id)
    put = client.put(
        "/admin/channels/cartoon-network/premieres/dexters-laboratory",
        json={"premiere": "1993-01-04"},
    )
    assert put.status_code == 200, put.text
    assert dexter(client, channel_id) == custom_before
    assert dexter(client, channel_id)["first_air"] == "1996-04-28"
    assert dexter(client, "cartoon-network")["first_air"] == "1993-01-04"
    # The custom channel's guide keeps the real dates and no mark.
    custom = [s for s in guide_slots(client, channel_id) if s["show"] == "dexters-laboratory"]
    assert all(not s["transplant"] for s in custom)
    assert all(date.fromisoformat(s["air_date"]) >= date(1996, 4, 28) for s in custom)


def test_clear_a_premiere(client: TestClient) -> None:
    url = "/admin/channels/cartoon-network/premieres/dexters-laboratory"
    client.put(url, json={"premiere": "1996-01-05"})
    cleared = client.put(url, json={"premiere": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["message"] == "Cleared the pretend premiere of Dexter's Laboratory"
    assert cleared.json()["show"]["premiere"] is None
    assert cleared.json()["show"]["first_air"] == "1996-04-28"


def test_the_yaml_premiere_shows_but_cannot_be_cleared_here(client: TestClient) -> None:
    # The fixture catalog's synthetic.yaml gives Gravity Falls a 2004 premiere on CN; the test
    # database has no Gravity Falls episodes, but the channel view still names the premiere.
    shows = client.get("/admin/channels/cartoon-network").json()["shows"]
    gf = next(s for s in shows if s["show"] == "gravity-falls")
    assert gf["premiere"] == "2004-06-15" and gf["premiere_source"] == "catalog"
    refused = client.put(
        "/admin/channels/cartoon-network/premieres/gravity-falls", json={"premiere": None}
    )
    assert refused.status_code == 409
    assert "catalog files" in refused.json()["errors"][0]["message"]


def test_premiere_must_fall_in_1990_to_2010(client: TestClient) -> None:
    before = version(client)
    url = "/admin/channels/cartoon-network/premieres/dexters-laboratory"
    for bad in ("1989-12-31", "2011-01-01"):
        refused = client.put(url, json={"premiere": bad})
        assert refused.status_code == 409
        assert refused.json()["errors"] == [
            {"field": "premiere", "message": "Pick a date from 1990 to 2010."}
        ]
    assert version(client) == before


def test_premiere_needs_the_show_carried_on_that_channel(client: TestClient) -> None:
    refused = client.put(
        "/admin/channels/ktvu/premieres/dexters-laboratory", json={"premiere": "1996-01-05"}
    )
    assert refused.status_code == 404
    assert (
        client.put(
            "/admin/channels/nope/premieres/dexters-laboratory", json={"premiere": "1996-01-05"}
        ).status_code
        == 404
    )


def test_premiere_warns_when_later_than_the_real_debut(client: TestClient) -> None:
    saved = client.put(
        "/admin/channels/ktvu/premieres/the-simpsons", json={"premiere": "1991-01-04"}
    )
    assert saved.status_code == 200
    assert any("later than its real premiere" in w["message"] for w in saved.json()["warnings"])


def test_custom_channel_drafts_carry_premieres(client: TestClient) -> None:
    draft = toons(
        library=False,
        launch_year=1995,
        shows=[{"show": "courage-the-cowardly-dog", "premiere": "1996-09-06"}],
    )
    saved = client.post("/admin/channels", json={"draft": draft})
    assert saved.status_code == 200, saved.text
    channel_id = saved.json()["channel_id"]
    got = client.get(f"/admin/channels/{channel_id}").json()
    assert got["draft"]["shows"][0]["premiere"] == "1996-09-06"
    assert got["shows"][0]["first_air"] == "1996-09-06"
    assert got["shows"][0]["debut"] == "1999-11-12"
    tuned = client.get(f"/tune/{channel_id}", params={"year": 1997}).json()
    assert tuned["current"]["show"] == "courage-the-cowardly-dog"
    assert tuned["current"]["transplant"] is True
    # Deleting the channel drops its premieres with it.
    client.delete(f"/admin/channels/{channel_id}")
    courage = next(
        i
        for i in client.get("/admin/library").json()["items"]
        if i["id"] == "courage-the-cowardly-dog"
    )
    assert courage["debut"] == "1999-11-12"


def test_draft_premiere_checks(client: TestClient) -> None:
    out_of_range = toons(shows=[{"show": "johnny-bravo", "premiere": "2012-06-15"}])
    result = client.post("/admin/check", json={"draft": out_of_range}).json()
    assert {"field": "shows.0.premiere", "message": "Pick a date from 1990 to 2010."} in result[
        "errors"
    ]
    after_until = toons(
        shows=[{"show": "johnny-bravo", "until_year": 1995, "premiere": "1996-01-01"}]
    )
    result = client.post("/admin/check", json={"draft": after_until}).json()
    assert any(
        e["field"] == "shows.0.premiere" and "after the show leaves the channel" in e["message"]
        for e in result["errors"]
    )
    early = toons(
        library=False,
        launch_year=1998,
        shows=[{"show": "johnny-bravo", "premiere": "1995-01-01"}],
    )
    result = client.post("/admin/check", json={"draft": early}).json()
    assert result["ok"], result
    assert any("isn't on the dial until 1998" in w["message"] for w in result["warnings"])


def test_a_show_debuting_after_the_channel_ends_is_flagged(client: TestClient) -> None:
    # Courage (1999) on a channel that carries it only until 1997: it would never air.
    draft = toons(shows=[{"show": "courage-the-cowardly-dog", "until_year": 1997}])
    warnings = client.post("/admin/check", json={"draft": draft}).json()["warnings"]
    assert any(
        w["field"] == "shows.0" and "Set a pretend premiere" in w["message"] for w in warnings
    )
