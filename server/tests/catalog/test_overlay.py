from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from retro_cable.admin import store
from retro_cable.catalog import load_catalog, validate
from retro_cable.catalog.models import Carriage, Channel
from retro_cable.catalog.overlay import (
    Overlay,
    apply_overlay,
    canonical,
    parse,
    with_channel,
    without_channel,
)
from retro_cable.db import connect, migrate
from tests.conftest import MakeCatalog

NOW = datetime(2026, 11, 2, tzinfo=UTC)


def toons(number: int = 50) -> tuple[Channel, list[Carriage]]:
    channel = Channel.model_validate(
        {
            "id": "custom-toons",
            "name": "Toons",
            "type": "network",
            "library": True,
            "identity": [{"from": "1990-01-01", "number": number, "call_sign": "TOONS"}],
        }
    )
    carriage = [
        Carriage.model_validate(
            {"show": "the-simpsons", "title": "The Simpsons", "channel": "custom-toons",
             "from": "1990-01-01"}
        )
    ]  # fmt: skip
    return channel, carriage


def test_empty_overlay_is_the_yaml_catalog(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    assert apply_overlay(catalog, Overlay()) is catalog


def test_overlay_adds_channels_and_changes_the_version(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    overlay = with_channel(Overlay(), *toons())
    merged = apply_overlay(catalog, overlay)
    assert merged.channel("custom-toons").name == "Toons"
    assert any(c.channel == "custom-toons" for c in merged.carriage.carriage)
    assert merged.version != catalog.version
    assert validate(merged) == []
    # Same inputs, same version; a different overlay, a different version.
    assert apply_overlay(catalog, parse(canonical(overlay))).version == merged.version
    assert apply_overlay(catalog, with_channel(Overlay(), *toons(51))).version != merged.version
    # The YAML is untouched.
    assert "custom-toons" not in {c.id for c in catalog.channels.channels}


def test_canonical_json_is_stable() -> None:
    overlay = with_channel(Overlay(), *toons())
    text = canonical(overlay)
    assert text == canonical(parse(text))
    assert '"from":"1990-01-01"' in text and " " not in text.replace("The Simpsons", "")


def test_replacing_and_removing_a_channel() -> None:
    channel, carriage = toons()
    overlay = with_channel(with_channel(Overlay(), channel, carriage), channel, carriage)
    assert len(overlay.channels) == 1 and len(overlay.carriage) == 1
    assert without_channel(overlay, "custom-toons").empty


def test_validation_catches_a_bad_overlay(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    channel, carriage = toons()
    bad = Carriage.model_validate({**carriage[0].model_dump(by_alias=True), "channel": "nope"})
    merged = apply_overlay(catalog, Overlay(channels=[channel], carriage=[bad]))
    assert any("nope" in e for e in validate(merged))


@pytest.fixture
def db(tmp_path: Path):  # type: ignore[no-untyped-def]
    conn = connect(tmp_path / "rc.db")
    migrate(conn)
    return conn


def test_store_appends_and_undo_never_rewrites(db) -> None:  # type: ignore[no-untyped-def]
    assert store.head(db) == store.EMPTY and not store.head(db).can_undo
    one = with_channel(Overlay(), *toons())
    two = with_channel(Overlay(), *toons(51))
    first = store.save(db, one, "create Toons", NOW)
    second = store.save(db, two, "update Toons", NOW)
    assert store.head(db).id == second.id and second.parent_id == first.id
    undone, restored = store.undo(db, NOW)
    assert undone.id == second.id
    assert restored.overlay == one and restored.id > second.id
    _, back = store.undo(db, NOW)
    assert back.overlay.empty and not back.can_undo
    with pytest.raises(store.NothingToUndo):
        store.undo(db, NOW)
    ids = [r[0] for r in db.execute("SELECT id FROM catalog_revisions ORDER BY id")]
    assert ids == sorted(ids) and len(ids) == 5  # start, two saves, two undos
    assert store.get(db, first.id).overlay == one  # history is intact
    assert date.fromisoformat(store.head(db).created_at[:10]) == NOW.date()
