"""A station manager run end to end, on the fake model and a test database (M5)."""

from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from retro_cable.admin import store
from retro_cable.app import Settings, create_app
from retro_cable.catalog import load_catalog
from retro_cable.catalog.models import Tuning
from retro_cable.catalog.overlay import Overlay, apply_overlay
from retro_cable.channel_builder.llm import LLMError
from retro_cable.db import connect, migrate
from retro_cable.scheduler import load_library
from retro_cable.station_manager import manager, runs
from retro_cable.station_manager.__main__ import main
from retro_cable.station_manager.manager import Options, apply_run, prompt, run, week_of
from retro_cable.station_manager.signals import compute, load_sessions
from retro_cable.station_manager.tuning import drift_cap, inputs_only, knobs
from tests.conftest import MakeCatalog
from tests.station_manager.conftest import EVENING, NOW, FakeManager, watch


def go(catalog_dir: Path, db_path: Path, llm: FakeManager, **opts: object) -> runs.StationRun:
    db = connect(db_path)
    migrate(db)
    try:
        return run(db, load_catalog(catalog_dir), load_library(db), llm, NOW, Options(**opts))  # type: ignore[arg-type]
    finally:
        db.close()


def head(db_path: Path) -> store.Revision:
    db = connect(db_path)
    try:
        return store.head(db)
    finally:
        db.close()


def test_not_enough_viewing_records_a_skipped_run_and_asks_nothing(
    catalog_dir: Path, db_path: Path
) -> None:
    db = connect(db_path)
    migrate(db)
    watch(db, "ktvu", "the-simpsons", 1320, EVENING)
    db.close()
    llm = FakeManager()
    done = go(catalog_dir, db_path, llm)
    assert done.status == "skipped"
    assert "Not enough viewing yet: 22 min in 4 weeks" in done.summary
    assert llm.prompts == []
    assert head(db_path).id == 0
    assert done.week_of == "2026-11-08"


def test_an_applied_run_is_one_undoable_revision_within_the_guardrails(
    catalog_dir: Path, watched: Path
) -> None:
    done = go(catalog_dir, watched, FakeManager())
    assert done.status == "applied", done.summary
    rev = head(watched)
    assert rev.id == done.revision_id and rev.can_undo
    assert rev.note.startswith("station manager: week of 2026-11-08, ")
    tuning = rev.overlay.tuning
    cap = drift_cap(30)
    # Nothing moves past the authenticity cap, and at most a fifth of KTVU's 6 weights.
    factors = [t.factor for t in tuning.carriage] + [t.factor for t in tuning.pools]
    assert factors and all(1 / cap - 0.01 <= f <= cap + 0.01 for f in factors)
    on_ktvu = [c for c in done.report.changes if c.channel == "ktvu"]
    assert len(on_ktvu) == 2
    assert any("a fifth" in d.reason for d in done.report.dropped)
    # The power hour is Brandon's 8 PM, on KTVU, from what he finishes there.
    assert done.report.power_window is not None
    assert (done.report.power_window.start, done.report.power_window.hours) == ("20:00", 1)
    [ph] = tuning.power_hours
    assert (ph.channel, ph.start, [p.show for p in ph.pool]) == ("ktvu", "20:00", ["the-simpsons"])
    # Inputs only: same carriage, dates, channels and holidays.
    base = load_catalog(catalog_dir)
    assert inputs_only(base, apply_overlay(base, rev.overlay)) == []
    # Undo brings back the exact catalog it started from.
    db = connect(watched)
    _, restored = store.undo(db, NOW)
    db.close()
    assert apply_overlay(base, restored.overlay).version == base.version


def test_auto_apply_off_records_a_proposal_that_apply_can_land_later(
    catalog_dir: Path, watched: Path
) -> None:
    done = go(catalog_dir, watched, FakeManager(), auto_apply=False)
    assert done.status == "proposed" and done.revision_id is None
    assert head(watched).id == 0
    assert done.report.after is not None and not done.report.after.empty
    db = connect(watched)
    try:
        rev = apply_run(db, load_catalog(catalog_dir), load_library(db), done.id, NOW, 0)
        assert rev.overlay.tuning == done.report.after
        with pytest.raises(manager.NotApplied):  # the weights moved since: not again
            apply_run(db, load_catalog(catalog_dir), load_library(db), done.id, NOW, 0)
    finally:
        db.close()


def test_a_dry_run_changes_nothing(catalog_dir: Path, watched: Path) -> None:
    done = go(catalog_dir, watched, FakeManager(), dry_run=True)
    assert done.status == "dry_run" and head(watched).id == 0
    assert done.report.changes


def test_an_unreachable_model_records_a_failed_run(catalog_dir: Path, watched: Path) -> None:
    def down(user: str, n: int) -> str:
        raise LLMError("model endpoint unreachable at http://rig:4000", True)

    done = go(catalog_dir, watched, FakeManager(hook=down))
    assert done.status == "failed" and "couldn't be reached" in done.summary
    assert head(watched).id == 0


def test_replies_still_invalid_after_retries_record_a_failed_run(
    catalog_dir: Path, watched: Path
) -> None:
    llm = FakeManager(
        hook=lambda user, n: '{"changes": [{"knob": "carriage:nope:x", "factor": 2}]}'
    )
    done = go(catalog_dir, watched, llm)
    assert done.status == "failed" and "invalid after 2 retries" in done.summary
    assert len(llm.prompts) == 3
    assert "unknown knob carriage:nope:x" in llm.prompts[1]
    assert head(watched).id == 0


def test_a_bad_reply_is_sent_back_and_the_corrected_one_used(
    catalog_dir: Path, watched: Path
) -> None:
    good = FakeManager()

    def once_wrong(user: str, n: int) -> str:
        return "not json" if n == 0 else good.reply(user)

    done = go(catalog_dir, watched, FakeManager(hook=once_wrong))
    assert done.status == "applied"


def test_a_change_that_fails_the_checks_is_dropped_and_reported(
    catalog_dir: Path, watched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = manager.smoke_test  # type: ignore[attr-defined]

    def fails_on_ktvu(sched, channel, days):  # type: ignore[no-untyped-def]
        return ["Sun Nov 1 1995: gap"] if channel.id == "ktvu" else real(sched, channel, days)

    monkeypatch.setattr(manager, "smoke_test", fails_on_ktvu)
    done = go(catalog_dir, watched, FakeManager(up=("the-simpsons", "dexters-laboratory")))
    assert done.status == "applied"
    assert {c.channel for c in done.report.changes} == {"cartoon-network"}
    assert not done.report.power_hours
    assert any(d.channel == "ktvu" and "failed the checks" in d.reason
               for d in done.report.dropped)  # fmt: skip
    assert all(t.channel != "ktvu" for t in head(watched).overlay.tuning.carriage)


def test_an_edit_in_admin_during_the_run_wins(catalog_dir: Path, watched: Path) -> None:
    good = FakeManager()

    def edit_meanwhile(user: str, n: int) -> str:
        db = connect(watched)
        store.save(db, Overlay(), "someone else", NOW)
        db.close()
        return good.reply(user)

    done = go(catalog_dir, watched, FakeManager(hook=edit_meanwhile))
    assert done.status == "failed" and "changed while it ran" in done.summary
    assert head(watched).note == "someone else"


def test_authenticity_zero_changes_nothing_on_that_channel(
    make_catalog: MakeCatalog, watched: Path
) -> None:
    from tests.conftest import CHANNELS

    era = CHANNELS.replace(
        "      - { from: 1990-01-01, number: 2, call_sign: KTVU, network: FOX }",
        "      - { from: 1990-01-01, number: 2, call_sign: KTVU, network: FOX }\n"
        "    authenticity: 0",
    )
    done = go(make_catalog(channels=era), watched, FakeManager())
    assert all(c.channel != "ktvu" for c in done.report.changes)
    assert all(p.channel != "ktvu" for p in done.report.power_hours)


def test_the_prompt_lists_signals_limits_and_choices(catalog_dir: Path, watched: Path) -> None:
    db = connect(watched)
    base = load_catalog(catalog_dir)
    signals = compute(
        load_sessions(db, NOW - timedelta(weeks=4), NOW), ZoneInfo("America/New_York")
    )
    db.close()
    table = knobs(base, Tuning())
    text = prompt(signals, base, table, ("20:00", 1), {"ktvu": ["the-simpsons"]},
                  week_of(NOW, ZoneInfo("America/New_York")), 4)  # fmt: skip
    assert "Week of 2026-11-08" in text
    assert "ktvu (KTVU (Fox); authenticity 30, factors 0.62-1.62, at most 2 changes)" in text
    assert '  the-simpsons "The Simpsons": 2 h 56 min, 8 programs, 100% finished' in text
    assert "  pool:ktvu-base:20:00:the-simpsons  The Simpsons, part from 20:00" in text
    assert "Power hour choices on ktvu: the-simpsons" in text
    assert "8 PM to 9 PM every day" in text
    assert prompt(signals, base, table, ("20:00", 1), {"ktvu": ["the-simpsons"]},
                  week_of(NOW, ZoneInfo("America/New_York")), 4) == text  # fmt: skip


def test_the_cli_runs_and_records(
    catalog_dir: Path, watched: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", "--db", str(watched), "--catalog", str(catalog_dir), "--dry-run"],
                llm=FakeManager(), clock=lambda: NOW)  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and "(dry_run)" in out and "power hour 20:00 (1 h) on ktvu" in out
    assert main(["run", "--db", str(watched), "--catalog", str(catalog_dir), "--weeks", "0"],
                llm=FakeManager(), clock=lambda: NOW) == 2  # fmt: skip


def test_the_cli_reads_the_auto_apply_setting(
    catalog_dir: Path, watched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RC_STATION_AUTO_APPLY", "0")
    main(["run", "--db", str(watched), "--catalog", str(catalog_dir)],
         llm=FakeManager(), clock=lambda: NOW)  # fmt: skip
    assert head(watched).id == 0
    monkeypatch.setenv("RC_STATION_AUTO_APPLY", "1")
    assert main(["run", "--db", str(watched), "--catalog", str(catalog_dir)],
                llm=FakeManager(), clock=lambda: NOW) == 0  # fmt: skip
    assert head(watched).note.startswith("station manager")


def test_admin_lists_runs_and_offers_undo_for_the_latest(catalog_dir: Path, watched: Path) -> None:
    go(catalog_dir, watched, FakeManager(), dry_run=True)
    done = go(catalog_dir, watched, FakeManager())
    settings = Settings(catalog_dir=catalog_dir, db_path=watched, media_dir=watched.parent,
                        media_secret="s", seed=0)  # fmt: skip
    client = TestClient(create_app(settings, clock=lambda: NOW))
    body = client.get("/admin/station/runs").json()
    assert body["auto_apply"] is True
    latest, older = body["runs"]
    assert latest["id"] == done.id and latest["status"] == "applied" and latest["can_undo"]
    assert older["status"] == "dry_run" and not older["can_undo"]
    assert latest["report"]["changes"] and latest["report"]["signals"]["heatmap"][6][20] > 0
    # Undo it like any /admin save; the run then can't be undone again.
    assert client.post("/admin/undo").status_code == 200
    assert not client.get("/admin/station/runs").json()["runs"][0]["can_undo"]
    # The tuning is gone with the undo, so the schedule is the YAML's again.
    assert client.get("/admin/state").json()["catalog_version"] == load_catalog(catalog_dir).version
