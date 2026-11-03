from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from retro_cable.admin import store
from retro_cable.catalog import load_catalog, validate
from retro_cable.catalog.models import Carriage, Channel, Synthetic, Template
from retro_cable.catalog.overlay import (
    Overlay,
    apply_overlay,
    canonical,
    parse,
    with_channel,
    with_premiere,
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


# --- pretend premieres in the overlay (M2 A) -------------------------------------------


def test_an_overlay_without_premieres_keeps_its_text_and_version(make_catalog: MakeCatalog) -> None:
    # Overlays saved before premieres existed must hash the same, so their schedules stay put.
    overlay = with_channel(Overlay(), *toons())
    assert "premieres" not in canonical(overlay)
    catalog = load_catalog(make_catalog())
    old = apply_overlay(catalog, parse(canonical(overlay)))
    assert old.version == apply_overlay(catalog, overlay).version


def test_premieres_change_the_version_and_replace_a_yaml_entry(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    overlay = with_premiere(Overlay(), "gravity-falls", "cartoon-network", date(2005, 1, 7))
    merged = apply_overlay(catalog, overlay)
    assert merged.version != catalog.version
    assert [(s.show, s.channel, s.premiere) for s in merged.synthetic.synthetic] == [
        ("gravity-falls", "cartoon-network", date(2005, 1, 7))
    ]
    assert validate(merged) == []
    # Canonical text sorts premieres, so the order they were set in doesn't matter.
    a = with_premiere(with_premiere(Overlay(), "x-men", "ktvu", date(1993, 1, 1)),
                      "the-simpsons", "ktvu", date(1990, 2, 2))  # fmt: skip
    b = with_premiere(with_premiere(Overlay(), "the-simpsons", "ktvu", date(1990, 2, 2)),
                      "x-men", "ktvu", date(1993, 1, 1))  # fmt: skip
    assert canonical(a) == canonical(b)
    # Clearing removes the overlay entry only.
    assert with_premiere(overlay, "gravity-falls", "cartoon-network", None).empty


def test_deleting_a_custom_channel_drops_its_premieres() -> None:
    channel, carriage = toons()
    premiere = Synthetic(show="the-simpsons", channel="custom-toons", premiere=date(1991, 1, 1))
    overlay = with_channel(Overlay(), channel, carriage, [premiere])
    overlay = with_premiere(overlay, "x-men", "ktvu", date(1993, 1, 1))
    rest = without_channel(overlay, "custom-toons")
    assert [(p.show, p.channel) for p in rest.premieres] == [("x-men", "ktvu")]


def test_a_premiere_for_a_show_the_channel_does_not_carry_fails_validation(
    make_catalog: MakeCatalog,
) -> None:
    catalog = load_catalog(make_catalog())
    overlay = with_premiere(Overlay(), "x-men", "cartoon-network", date(1995, 1, 1))
    errors = validate(apply_overlay(catalog, overlay))
    assert any("'x-men' is not carried on cartoon-network" in e for e in errors)


# --- day-part templates in the overlay (M3, channel builder) ----------------------------


def toons_template(show: str = "the-simpsons") -> Template:
    return Template.model_validate(
        {
            "id": "custom-toons-1",
            "channel": "custom-toons",
            "from": "1990-01-01",
            "parts": [
                {"weekday": "monday", "start": "00:00", "pool": [{"show": show}]},
                {"weekday": "monday", "start": "20:00", "primetime": True,
                 "pool": [{"show": show, "weight": 2}]},
            ],
        }
    )  # fmt: skip


def test_an_overlay_without_templates_keeps_its_text_and_version(
    make_catalog: MakeCatalog,
) -> None:
    # Overlays saved before templates existed must hash the same, so their schedules stay put.
    overlay = with_channel(Overlay(), *toons())
    assert "templates" not in canonical(overlay)
    catalog = load_catalog(make_catalog())
    with_t = with_channel(Overlay(), *toons(), templates=[toons_template()])
    assert '"templates"' in canonical(with_t)
    assert apply_overlay(catalog, with_t).version != apply_overlay(catalog, overlay).version
    assert parse(canonical(with_t)) == with_t


def test_overlay_templates_merge_and_validate(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    merged = apply_overlay(catalog, with_channel(Overlay(), *toons(), templates=[toons_template()]))
    assert [t.id for t in merged.templates.templates if t.channel == "custom-toons"] == [
        "custom-toons-1"
    ]
    assert validate(merged) == []
    # A pool naming a show the channel doesn't carry is caught by the same checks as YAML.
    bad = apply_overlay(
        catalog, with_channel(Overlay(), *toons(), templates=[toons_template("x-men")])
    )
    assert any("pool show 'x-men' is not carried on custom-toons" in e for e in validate(bad))


def test_removing_a_channel_drops_its_templates() -> None:
    overlay = with_channel(Overlay(), *toons(), templates=[toons_template()])
    assert without_channel(overlay, "custom-toons").empty
    # with_channel replaces everything of the channel, templates included.
    assert with_channel(overlay, *toons()).templates == []
    # Setting a premiere leaves the templates alone.
    kept = with_premiere(overlay, "the-simpsons", "custom-toons", date(1991, 1, 1))
    assert kept.templates == [toons_template()]
