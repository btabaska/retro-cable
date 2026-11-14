"""Admin edits as an overlay on the YAML catalog (Phase 10).

The admin never writes schedules or the YAML files. It writes an Overlay, which holds channels,
carriage, synthetic ("pretend") premieres and, for channels the channel builder made, day-part
templates, in the same models as the YAML, and the effective catalog is the YAML catalog with
the overlay merged in. It also keeps what the lineup says about the shows the builder placed
(TMDB id, first-aired year), merged into the effective overrides so `ingest acquire` can look
them up. A premiere may name any channel, YAML or
custom, and replaces a synthetic.yaml entry for the same (show, channel). The catalog version
covers both, so a schedule stays a pure function of (channel, date, catalog version, seed).
Overlay channel ids start with `custom-`, so they can never collide with YAML ids.

The weekly station manager (M5) writes only the overlay's `tuning`: weight multipliers on
carriage and template pool entries (YAML or custom) and power hours. Tuning never adds or
removes carriage and never moves a date; entries that no longer match anything are ignored.
"""

import hashlib
import json
from dataclasses import replace
from datetime import date

from pydantic import Field

from retro_cable.catalog.load import Catalog
from retro_cable.catalog.models import (
    CUSTOM_PREFIX,
    Carriage,
    CarriageFile,
    Channel,
    ChannelsFile,
    Model,
    OverridesFile,
    PowerHour,
    ShowIds,
    ShowOverride,
    Synthetic,
    SyntheticFile,
    Template,
    TemplatesFile,
    Tuning,
)

__all__ = [
    "CUSTOM_PREFIX",
    "Overlay",
    "apply_overlay",
    "canonical",
    "overlay_version",
    "parse",
    "with_channel",
    "with_ids",
    "with_premiere",
    "with_tuning",
    "without_channel",
]


class Overlay(Model):
    """Inputs added in /admin. Empty means the catalog is exactly the YAML."""

    channels: list[Channel] = Field(default_factory=list)
    carriage: list[Carriage] = Field(default_factory=list)
    premieres: list[Synthetic] = Field(
        default_factory=list, description="Pretend premieres, one per (show, channel)"
    )
    templates: list[Template] = Field(
        default_factory=list, description="Day-part templates of custom channels (M3)"
    )
    ids: list[ShowIds] = Field(
        default_factory=list,
        description="Lineup TMDB ids and years of shows the channel builder placed (M4)",
    )
    tuning: Tuning = Field(
        default_factory=Tuning, description="The weekly station manager's weights (M5)"
    )

    @property
    def empty(self) -> bool:
        return not (
            self.channels
            or self.carriage
            or self.premieres
            or self.templates
            or self.ids
            or not self.tuning.empty
        )


def canonical(overlay: Overlay) -> str:
    """The overlay as canonical JSON: stable key order, no whitespace, dates as ISO text.
    Premieres are sorted, and left out when there are none, so overlays saved before they
    existed keep their text and their catalog version. Templates and ids are left out the
    same way; ids are sorted by show and drop their empty fields."""
    data = overlay.model_dump(mode="json", by_alias=True)
    if overlay.premieres:
        data["premieres"] = sorted(data["premieres"], key=lambda p: (p["channel"], p["show"]))
    else:
        del data["premieres"]
    if not overlay.templates:
        del data["templates"]  # likewise for overlays saved before templates (M3)
    if overlay.ids:
        data["ids"] = sorted(
            ({k: v for k, v in i.items() if v is not None} for i in data["ids"]),
            key=lambda i: i["show"],
        )
    else:
        del data["ids"]  # and before lineup ids (M4)
    if overlay.tuning.empty:
        del data["tuning"]  # and before the station manager (M5)
    else:
        tuning = data["tuning"]
        tuning["carriage"] = sorted(tuning["carriage"], key=lambda t: (t["channel"], t["show"]))
        tuning["pools"] = sorted(
            tuning["pools"], key=lambda t: (t["template"], t["start"], t["show"])
        )
        tuning["power_hours"] = sorted(tuning["power_hours"], key=lambda p: p["channel"])
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse(text: str) -> Overlay:
    return Overlay.model_validate(json.loads(text) if text else {})


def overlay_version(base_version: str, overlay: Overlay) -> str:
    """The YAML version when the overlay is empty, else a hash over both."""
    if overlay.empty:
        return base_version
    digest = hashlib.sha256(f"{base_version}\0{canonical(overlay)}".encode())
    return digest.hexdigest()[:12]


def apply_overlay(catalog: Catalog, overlay: Overlay) -> Catalog:
    """The effective catalog. Validate the result with `validate()`."""
    if overlay.empty:
        return catalog
    channels = ChannelsFile.model_validate(
        {
            **catalog.channels.model_dump(by_alias=True),
            "channels": [
                *(c.model_dump(by_alias=True) for c in catalog.channels.channels),
                *(c.model_dump(by_alias=True) for c in overlay.channels),
            ],
        }
    )
    carriage = CarriageFile(carriage=[*catalog.carriage.carriage, *overlay.carriage])
    replaced = {(p.show, p.channel) for p in overlay.premieres}
    synthetic = SyntheticFile(
        synthetic=[
            *(s for s in catalog.synthetic.synthetic if (s.show, s.channel) not in replaced),
            *overlay.premieres,
        ]
    )
    templates = catalog.templates
    if overlay.templates:
        templates = TemplatesFile(templates=[*catalog.templates.templates, *overlay.templates])
    power_hours: tuple[PowerHour, ...] = ()
    if not overlay.tuning.empty:
        carriage, templates = _tuned(carriage, templates, overlay.tuning)
        power_hours = _power_hours(channels, carriage, overlay.tuning.power_hours)
    return replace(
        catalog,
        channels=channels,
        carriage=carriage,
        synthetic=synthetic,
        templates=templates,
        overrides=_with_ids(catalog.overrides, overlay.ids, {c.show for c in carriage.carriage}),
        version=overlay_version(catalog.version, overlay),
        power_hours=power_hours,
    )


def _tuned(
    carriage: CarriageFile, templates: TemplatesFile, tuning: Tuning
) -> tuple[CarriageFile, TemplatesFile]:
    """Carriage and templates with the tuning's multipliers applied to their weights. Only
    weights change: the same rows, windows, parts and pools."""
    by_pair = {(t.channel, t.show): t.factor for t in tuning.carriage}
    rows = [
        c.model_copy(update={"weight": c.weight * by_pair[(c.channel, c.show)]})
        if (c.channel, c.show) in by_pair
        else c
        for c in carriage.carriage
    ]
    by_entry = {(t.template, t.start, t.show): t.factor for t in tuning.pools}
    if not by_entry:
        return CarriageFile(carriage=rows), templates
    tuned: list[Template] = []
    for t in templates.templates:
        parts = [
            p.model_copy(
                update={
                    "pool": [
                        e.model_copy(update={"weight": e.weight * by_entry[key]})
                        if (key := (t.id, p.start, e.show or "")) in by_entry
                        else e
                        for e in p.pool
                    ]
                }
            )
            for p in t.parts
        ]
        tuned.append(t.model_copy(update={"parts": parts}))
    return CarriageFile(carriage=rows), TemplatesFile(templates=tuned)


def _power_hours(
    channels: ChannelsFile, carriage: CarriageFile, wanted: list[PowerHour]
) -> tuple[PowerHour, ...]:
    """Power hours on channels that exist and aren't movie channels, pooling only shows the
    channel carries; one that is left with no show is dropped."""
    kinds = {c.id: c.type for c in channels.channels}
    shows = {(c.channel, c.show) for c in carriage.carriage if c.kind == "show"}
    out: list[PowerHour] = []
    for ph in sorted(wanted, key=lambda p: p.channel):
        if kinds.get(ph.channel) in (None, "movie") or any(p.channel == ph.channel for p in out):
            continue
        pool = [e for e in ph.pool if (ph.channel, e.show) in shows]
        if pool:
            out.append(ph.model_copy(update={"pool": pool}))
    return tuple(out)


def _with_ids(overrides: OverridesFile, ids: list[ShowIds], carried: set[str]) -> OverridesFile:
    """The overrides with the overlay's ids filled in: a YAML value wins, and ids of shows
    nothing carries any more are ignored (overrides must name a carried show)."""
    if not ids:
        return overrides
    wanted = {i.show: i for i in ids if i.show in carried}
    shows: list[ShowOverride] = []
    for o in overrides.shows:
        i = wanted.pop(o.show, None)
        if i is not None:
            o = o.model_copy(update={"tmdb_id": o.tmdb_id or i.tmdb_id, "year": o.year or i.year})
        shows.append(o)
    shows += [ShowOverride(show=i.show, tmdb_id=i.tmdb_id, year=i.year) for i in wanted.values()]
    return OverridesFile(shows=shows)


def without_channel(overlay: Overlay, channel_id: str) -> Overlay:
    """The overlay without a custom channel, its carriage, premieres and templates. Premieres
    on a YAML channel of that id (there are none: custom ids never collide) would also go."""
    return Overlay(
        channels=[c for c in overlay.channels if c.id != channel_id],
        carriage=[c for c in overlay.carriage if c.channel != channel_id],
        premieres=[p for p in overlay.premieres if p.channel != channel_id],
        templates=[t for t in overlay.templates if t.channel != channel_id],
        ids=overlay.ids,  # per show, not per channel; unused ones are ignored when applied
        tuning=overlay.tuning,  # stale entries are ignored when applied
    )


def with_channel(
    overlay: Overlay,
    channel: Channel,
    carriage: list[Carriage],
    premieres: list[Synthetic] | None = None,
    templates: list[Template] | None = None,
) -> Overlay:
    """Insert or replace one custom channel with all of its carriage, premieres and
    day-part templates (none: the default all-day rotation)."""
    rest = without_channel(overlay, channel.id)
    return Overlay(
        channels=[*rest.channels, channel],
        carriage=[*rest.carriage, *carriage],
        premieres=[*rest.premieres, *(premieres or [])],
        templates=[*rest.templates, *(templates or [])],
        ids=rest.ids,
        tuning=rest.tuning,
    )


def with_ids(overlay: Overlay, ids: list[ShowIds]) -> Overlay:
    """Set the lineup ids of some shows, replacing what the overlay had for them."""
    if not ids:
        return overlay
    new = {i.show: i for i in ids}
    rest = [i for i in overlay.ids if i.show not in new]
    return overlay.model_copy(update={"ids": [*rest, *new.values()]})


def with_premiere(overlay: Overlay, show: str, channel_id: str, premiere: date | None) -> Overlay:
    """Set (or, with None, clear) the pretend premiere of one show on one channel. Clearing
    removes only the overlay's entry; a synthetic.yaml entry would apply again."""
    rest = [p for p in overlay.premieres if (p.show, p.channel) != (show, channel_id)]
    if premiere is not None:
        rest.append(Synthetic(show=show, channel=channel_id, premiere=premiere))
    return overlay.model_copy(update={"premieres": rest})


def with_tuning(overlay: Overlay, tuning: Tuning) -> Overlay:
    """The overlay with the station manager's tuning replaced; everything else as it was."""
    return overlay.model_copy(update={"tuning": tuning})
