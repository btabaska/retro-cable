"""Drafting, checking and applying a proposal end to end, with the fake model and a test
database. The proposal is applied only by the explicit apply step, as one undoable revision."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from retro_cable.admin import store
from retro_cable.app import Settings, create_app
from retro_cable.catalog import load_catalog, validate
from retro_cable.catalog.overlay import apply_overlay
from retro_cable.channel_builder.__main__ import load_context, main
from retro_cable.channel_builder.build import build_proposal
from retro_cable.channel_builder.proposal import (
    NotApplied,
    Proposal,
    apply_proposal,
    check_proposal,
)
from retro_cable.channel_builder.shows import known_ids, read_shows
from retro_cable.db import connect, migrate
from retro_cable.ingest.cli import carried_shows
from tests.channel_builder.conftest import FakeLLM, lines_after
from tests.conftest import MakeCatalog
from tests.test_api_admin import add_uncarried
from tests.test_api_tv import seed_db

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


@pytest.fixture
def catalog_dir(make_catalog: MakeCatalog) -> Path:
    return make_catalog()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    media = tmp_path / "media"
    media.mkdir()
    path = tmp_path / "rc.db"
    seed_db(path, media)
    add_uncarried(path)  # Courage and Johnny Bravo, in the library, on no channel
    return path


def draft(catalog_dir: Path, db_path: Path, shows_csv: Path, llm: FakeLLM) -> Proposal:
    db = connect(db_path)
    ctx = load_context(db, catalog_dir, seed=7)
    db.close()
    titles = {c.show: c.title for c in ctx.effective.carriage.carriage}
    rows = read_shows(shows_csv, known_ids(titles, {i.id: i.title for i in ctx.index.values()}))
    return build_proposal(rows, ctx.effective, ctx.index, llm, NOW, shows_csv.name)


def test_every_kept_show_is_placed_carried_or_unplaced_with_a_reason(
    catalog_dir: Path, db_path: Path, shows_csv: Path
) -> None:
    llm = FakeLLM()
    proposal = draft(catalog_dir, db_path, shows_csv, llm)
    placed = {s.show for c in proposal.channels for s in c.shows}
    carried = {a.show for a in proposal.already_carried}
    unplaced = {u.show: u.reason for u in proposal.unplaced}
    kept = {r.id for r in read_shows(shows_csv, known_ids({}, {}))}
    assert placed | carried | set(unplaced) >= kept - {"the-simpsons", "gravity-falls"}
    # Already on a channel: left alone, never sent to the model.
    assert carried == {"the-simpsons", "gravity-falls", "halloweentown"}
    assert "barney-and-friends" not in placed | carried | set(unplaced)  # removed in the list
    assert "holiday calendar" in unplaced["a-charlie-brown-christmas"]
    assert placed == {
        "courage-the-cowardly-dog", "johnny-bravo", "psych", "pixel-patrol", "snow-day-rescue"
    }  # fmt: skip
    assert llm.calls.count("concepts") == 3  # one per group: kids, adult, movies
    assert proposal.model == "fake-local"


def test_the_code_sets_dates_numbers_and_ids(
    catalog_dir: Path, db_path: Path, shows_csv: Path
) -> None:
    proposal = draft(catalog_dir, db_path, shows_csv, FakeLLM())
    by_id = {c.id: c for c in proposal.channels}
    assert all(c.id.startswith("custom-") and not c.reuse for c in proposal.channels)
    kids = by_id["custom-kids-tv-1"]
    # Courage (1999), Johnny Bravo (1997) and Pixel Patrol (2014, pretend 1994): the model
    # said 1995, but Pixel Patrol can air from 1994, so the channel launches then.
    assert kids.launch_year == 1994
    shows = {s.show: s for s in kids.shows}
    assert shows["pixel-patrol"].premiere == date(1994, 3, 2)
    assert shows["pixel-patrol"].from_year == 1994
    assert shows["johnny-bravo"].from_year == 1997 and shows["johnny-bravo"].premiere is None
    assert shows["johnny-bravo"].in_library and shows["johnny-bravo"].on_nas
    assert not shows["pixel-patrol"].in_library
    assert kids.number == 20 and kids.call_sign == "KID1"  # the kids band; cleaned sign
    movies = next(c for c in proposal.channels if c.type == "movie")
    assert movies.number == 90 and [s.kind for s in movies.shows] == ["movie"]
    assert kids.dayparts and kids.dayparts[0].start == "00:00"
    assert by_id["custom-adult-tv-1"].number == 60


def test_check_and_apply_go_through_the_admin_overlay(
    catalog_dir: Path, db_path: Path, shows_csv: Path
) -> None:
    def to_cartoon_network(user: str, n: int) -> str:
        # Johnny Bravo onto the existing Cartoon Network, the rest onto the first channel.
        shows = [line.split(":")[0] for line in lines_after(user, "Shows:\n")]
        first = lines_after(user, "Channels:\n")[0].split(":")[0]
        titles = dict(line.split(": ", 1) for line in lines_after(user, "Shows:\n"))
        return json.dumps(
            {
                "assignments": [
                    {
                        "show": s,
                        "channels": ["cartoon-network"]
                        if titles[s].startswith("Johnny Bravo")
                        else [first],
                    }
                    for s in shows
                ]
            }
        )

    proposal = draft(catalog_dir, db_path, shows_csv, FakeLLM(hooks={"assign": to_cartoon_network}))
    cn = next(c for c in proposal.channels if c.id == "cartoon-network")
    assert cn.reuse and [s.show for s in cn.shows] == ["johnny-bravo"]

    db = connect(db_path)
    ctx = load_context(db, catalog_dir, seed=7)
    checked = check_proposal(proposal, ctx.base, ctx.head, ctx.index, ctx.library, 7)
    assert checked.ok, checked.issues
    assert any("no playable files" in i.message for i in checked.issues)  # Pixel Patrol
    assert store.head(db).id == 0  # checking saves nothing

    rev, _ = apply_proposal(db, proposal, ctx.base, ctx.index, ctx.library, 7, NOW)
    assert rev.note.startswith("channel builder:") and rev.can_undo
    merged = apply_overlay(ctx.base, store.head(db).overlay)
    assert validate(merged) == []
    ids = {c.id for c in merged.channels.channels}
    assert {c.id for c in proposal.channels} <= ids
    assert any(c.show == "johnny-bravo" and c.channel == "cartoon-network"
               for c in merged.carriage.carriage)  # fmt: skip
    assert any(t.channel.startswith("custom-kids") for t in merged.templates.templates)
    assert any(p.show == "pixel-patrol" for p in merged.synthetic.synthetic)
    # What the channels carry is what the rig converts.
    assert {"johnny-bravo", "courage-the-cowardly-dog", "pixel-patrol"} <= carried_shows(
        db, ctx.base
    )
    # One undo reverts the whole proposal.
    store.undo(db, NOW)
    assert apply_overlay(ctx.base, store.head(db).overlay).version == ctx.base.version
    db.close()


def test_invalid_channels_are_reported_and_not_applied(
    catalog_dir: Path, db_path: Path, shows_csv: Path
) -> None:
    proposal = draft(catalog_dir, db_path, shows_csv, FakeLLM())
    bad = proposal.channels[0].model_copy(update={"number": 2})  # KTVU's number
    proposal = proposal.model_copy(update={"channels": [bad, *proposal.channels[1:]]})
    db = connect(db_path)
    ctx = load_context(db, catalog_dir, seed=7)
    checked = check_proposal(proposal, ctx.base, ctx.head, ctx.index, ctx.library, 7)
    assert checked.skipped == [bad.id]
    assert any(i.channel == bad.id and "Number 2 is already KTVU" in i.message
               for i in checked.issues)  # fmt: skip
    with pytest.raises(NotApplied):
        apply_proposal(db, proposal, ctx.base, ctx.index, ctx.library, 7, NOW)
    assert store.head(db).id == 0
    _, partial = apply_proposal(
        db, proposal, ctx.base, ctx.index, ctx.library, 7, NOW, skip_invalid=True
    )
    merged = apply_overlay(ctx.base, store.head(db).overlay)
    assert bad.id not in {c.id for c in merged.channels.channels}
    assert set(partial.applied) <= {c.id for c in merged.channels.channels}
    db.close()


def test_a_model_that_keeps_getting_it_wrong_leaves_shows_unplaced(
    catalog_dir: Path, db_path: Path, shows_csv: Path
) -> None:
    llm = FakeLLM(
        hooks={"assign": lambda u, n: '{"assignments": [{"show": "s1", "channels": ["nope"]}]}'}
    )
    proposal = draft(catalog_dir, db_path, shows_csv, llm)
    assert proposal.channels == []
    reasons = {u.show: u.reason for u in proposal.unplaced}
    assert reasons["johnny-bravo"] == "the model gave no valid channel for it"
    assert llm.counts["assign"] == 3 * 3  # three groups, each tried three times
    assert any("gave up" in n for n in proposal.notes)


def test_one_bad_reply_is_retried(catalog_dir: Path, db_path: Path, shows_csv: Path) -> None:
    fake = FakeLLM()

    def forgetful(user: str, n: int) -> str:
        reply = json.loads(fake.assign(user))
        if n == 0:
            reply["assignments"] = reply["assignments"][1:]  # forgets a show the first time
        return json.dumps(reply)

    fake.hooks["assign"] = forgetful
    proposal = draft(catalog_dir, db_path, shows_csv, fake)
    assert not [u for u in proposal.unplaced if "model" in u.reason]
    assert "retries: 1" in proposal.notes[0]


def test_concepts_fall_back_to_a_plain_channel(
    catalog_dir: Path, db_path: Path, shows_csv: Path
) -> None:
    llm = FakeLLM(hooks={"concepts": lambda u, n: "I can't do JSON today"})
    proposal = draft(catalog_dir, db_path, shows_csv, llm)
    assert "Kids Channel" in {c.name for c in proposal.channels}


def test_cli_proposes_checks_and_applies(
    catalog_dir: Path, db_path: Path, shows_csv: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    common = ["--db", str(db_path), "--catalog", str(catalog_dir), "--seed", "7"]
    assert main(["propose", "--shows", str(shows_csv), "--out", str(out), *common],
                llm=FakeLLM(), clock=lambda: NOW) == 0  # fmt: skip
    text = (out / "proposal.md").read_text()
    assert "# Channel builder proposal" in text and "Kids TV 1" in text and "Pixel Patrol" in text
    assert "1994-03-02" in text  # the pretend premiere
    rows = (out / "proposal.csv").read_text().splitlines()
    assert rows[0].startswith("number,call_sign,channel") and any("unplaced" in r for r in rows)
    proposal = out / "proposal.json"
    assert main(["check", str(proposal), *common]) == 0
    db = connect(db_path)
    assert store.head(db).id == 0  # propose and check save nothing
    db.close()
    assert main(["apply", str(proposal), *common], clock=lambda: NOW) == 0
    db = connect(db_path)
    assert store.head(db).note.startswith("channel builder:")
    db.close()


def test_editing_a_built_channel_in_admin_keeps_its_day_parts_while_they_fit(
    catalog_dir: Path, db_path: Path, shows_csv: Path, tmp_path: Path
) -> None:
    proposal = draft(catalog_dir, db_path, shows_csv, FakeLLM())
    db = connect(db_path)
    migrate(db)
    ctx = load_context(db, catalog_dir, seed=7)
    apply_proposal(db, proposal, ctx.base, ctx.index, ctx.library, 7, NOW)
    db.close()
    media = tmp_path / "media"
    settings = Settings(
        catalog_dir=catalog_dir, db_path=db_path, media_dir=media, media_secret="s", seed=7
    )
    client = TestClient(create_app(settings, clock=lambda: NOW))
    kids = next(c for c in proposal.channels if c.id == "custom-kids-tv-1")
    body = client.get(f"/admin/channels/{kids.id}").json()["draft"]

    def templates() -> list[str]:
        db = connect(db_path)
        try:
            return [t.id for t in store.head(db).overlay.templates if t.channel == kids.id]
        finally:
            db.close()

    renamed = client.put(f"/admin/channels/{kids.id}", json={"draft": {**body, "name": "Kids!"}})
    assert renamed.status_code == 200, renamed.json()
    assert templates() == [f"{kids.id}-week"]
    fewer = {**body, "shows": [s for s in body["shows"] if s["show"] != "johnny-bravo"]}
    dropped = client.put(f"/admin/channels/{kids.id}", json={"draft": fewer})
    assert dropped.status_code == 200
    assert any("day-parts" in w["message"] for w in dropped.json()["check"]["warnings"])
    assert templates() == []


def test_the_yaml_catalog_is_never_written(
    catalog_dir: Path, db_path: Path, shows_csv: Path
) -> None:
    before = {p.name: p.read_text() for p in catalog_dir.glob("*.yaml")}
    proposal = draft(catalog_dir, db_path, shows_csv, FakeLLM())
    db = connect(db_path)
    ctx = load_context(db, catalog_dir, seed=7)
    apply_proposal(db, proposal, ctx.base, ctx.index, ctx.library, 7, NOW)
    db.close()
    assert {p.name: p.read_text() for p in catalog_dir.glob("*.yaml")} == before
    assert load_catalog(catalog_dir).version == ctx.base.version
