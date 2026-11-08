"""The station manager's tuning in the admin overlay (M5): weight multipliers and power hours.
Inputs only: the same carriage rows, windows, templates and dates, with other weights."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from retro_cable.catalog import load_catalog, validate
from retro_cable.catalog.models import CarriageTune, PoolTune, PowerHour, Tuning
from retro_cable.catalog.overlay import (
    Overlay,
    apply_overlay,
    canonical,
    parse,
    with_channel,
    with_tuning,
    without_channel,
)
from tests.catalog.test_overlay import toons
from tests.conftest import MakeCatalog


def tuning() -> Tuning:
    return Tuning(
        carriage=[CarriageTune(channel="cartoon-network", show="dexters-laboratory", factor=1.5)],
        pools=[PoolTune(template="ktvu-base", start="20:00", show="the-simpsons", factor=0.5)],
        power_hours=[
            PowerHour.model_validate(
                {"channel": "ktvu", "start": "20:00", "pool": [{"show": "the-simpsons"}]}
            )
        ],
    )


def test_an_overlay_without_tuning_keeps_its_text_and_version(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    overlay = with_channel(Overlay(), *toons())
    assert "tuning" not in canonical(overlay)
    assert parse(canonical(overlay)) == overlay
    tuned = with_tuning(overlay, tuning())
    assert apply_overlay(catalog, tuned).version != apply_overlay(catalog, overlay).version
    assert with_tuning(Overlay(), Tuning()).empty
    assert not with_tuning(Overlay(), tuning()).empty


def test_tuning_multiplies_weights_and_changes_nothing_else(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    merged = apply_overlay(catalog, with_tuning(Overlay(), tuning()))
    assert validate(merged) == []
    weights = {(c.channel, c.show): c.weight for c in merged.carriage.carriage}
    assert weights[("cartoon-network", "dexters-laboratory")] == 1.5
    assert weights[("ktvu", "the-simpsons")] == 1.0
    ktvu = next(t for t in merged.templates.templates if t.id == "ktvu-base")
    prime = next(p for p in ktvu.parts if p.start == "20:00")
    assert {e.show: e.weight for e in prime.pool} == {"the-simpsons": 1.0, "x-men": 1.0}
    late = next(p for p in ktvu.parts if p.start == "00:00")
    assert late.pool[0].weight == 1.0  # other parts keep their weight
    # Same rows, windows and dates: only weights differ.
    strip = [c.model_copy(update={"weight": 1}) for c in merged.carriage.carriage]
    assert strip == [c.model_copy(update={"weight": 1}) for c in catalog.carriage.carriage]
    assert merged.synthetic == catalog.synthetic
    assert merged.channels == catalog.channels
    assert merged.power_hours[0].channel == "ktvu"


def test_stale_tuning_is_ignored(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    stale = Tuning(
        carriage=[CarriageTune(channel="custom-gone", show="the-simpsons", factor=2)],
        pools=[PoolTune(template="gone", start="20:00", show="x", factor=2)],
        power_hours=[
            PowerHour.model_validate(
                {"channel": "custom-gone", "start": "20:00", "pool": [{"show": "the-simpsons"}]}
            ),
            PowerHour.model_validate(
                {"channel": "disney-movies", "start": "20:00", "pool": [{"show": "halloweentown"}]}
            ),
            PowerHour.model_validate(
                {"channel": "ktvu", "start": "20:00", "pool": [{"show": "dexters-laboratory"}]}
            ),
        ],
    )
    merged = apply_overlay(catalog, with_tuning(Overlay(), stale))
    assert validate(merged) == []
    assert merged.power_hours == ()


def test_tuning_survives_channel_edits() -> None:
    overlay = with_tuning(with_channel(Overlay(), *toons()), tuning())
    assert with_channel(overlay, *toons(51)).tuning == overlay.tuning
    assert without_channel(overlay, "custom-toons").tuning == overlay.tuning


def test_canonical_tuning_is_sorted() -> None:
    a = Tuning(
        carriage=[
            CarriageTune(channel="ktvu", show="the-simpsons", factor=2),
            CarriageTune(channel="cartoon-network", show="dragon-ball-z", factor=2),
        ]
    )
    b = Tuning(carriage=list(reversed(a.carriage)))
    assert canonical(with_tuning(Overlay(), a)) == canonical(with_tuning(Overlay(), b))


def test_factors_and_power_hours_have_hard_limits() -> None:
    with pytest.raises(ValidationError):
        CarriageTune(channel="ktvu", show="the-simpsons", factor=6)
    with pytest.raises(ValidationError):
        CarriageTune(channel="ktvu", show="the-simpsons", factor=0.1)
    with pytest.raises(ValidationError):
        PowerHour.model_validate(
            {"channel": "ktvu", "start": "23:00", "hours": 2, "pool": [{"show": "a"}]}
        )
    with pytest.raises(ValidationError):
        PowerHour.model_validate({"channel": "ktvu", "start": "20:00", "pool": [{"tag": "a"}]})
    with pytest.raises(ValidationError):
        PowerHour.model_validate({"channel": "ktvu", "start": "20:00", "pool": []})


def test_validation_names_a_bad_power_hour(make_catalog: MakeCatalog) -> None:
    catalog = load_catalog(make_catalog())
    bad = PowerHour.model_validate(
        {"channel": "ktvu", "start": "20:00", "pool": [{"show": "dexters-laboratory"}]}
    )
    problems = validate(replace(catalog, power_hours=(bad, bad)))
    assert "power hour: 'dexters-laboratory' is not carried on ktvu" in problems
    assert "power hour: 'ktvu' has two" in problems
