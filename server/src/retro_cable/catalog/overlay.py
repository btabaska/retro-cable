"""Admin edits as an overlay on the YAML catalog (Phase 10).

The admin never writes schedules or the YAML files. It writes an Overlay, which holds channels,
carriage, synthetic ("pretend") premieres and, for channels the channel builder made, day-part
templates, in the same models as the YAML, and the effective catalog is the YAML catalog with
the overlay merged in. A premiere may name any channel, YAML or
custom, and replaces a synthetic.yaml entry for the same (show, channel). The catalog version
covers both, so a schedule stays a pure function of (channel, date, catalog version, seed).
Overlay channel ids start with `custom-`, so they can never collide with YAML ids.
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
    Synthetic,
    SyntheticFile,
    Template,
    TemplatesFile,
)

__all__ = [
    "CUSTOM_PREFIX",
    "Overlay",
    "apply_overlay",
    "canonical",
    "overlay_version",
    "parse",
    "with_channel",
    "with_premiere",
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

    @property
    def empty(self) -> bool:
        return not (self.channels or self.carriage or self.premieres or self.templates)


def canonical(overlay: Overlay) -> str:
    """The overlay as canonical JSON: stable key order, no whitespace, dates as ISO text.
    Premieres are sorted, and left out when there are none, so overlays saved before they
    existed keep their text and their catalog version. Templates are left out the same way."""
    data = overlay.model_dump(mode="json", by_alias=True)
    if overlay.premieres:
        data["premieres"] = sorted(data["premieres"], key=lambda p: (p["channel"], p["show"]))
    else:
        del data["premieres"]
    if not overlay.templates:
        del data["templates"]  # likewise for overlays saved before templates (M3)
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
    return replace(
        catalog,
        channels=channels,
        carriage=carriage,
        synthetic=synthetic,
        templates=templates,
        version=overlay_version(catalog.version, overlay),
    )


def without_channel(overlay: Overlay, channel_id: str) -> Overlay:
    """The overlay without a custom channel, its carriage, premieres and templates. Premieres
    on a YAML channel of that id (there are none: custom ids never collide) would also go."""
    return Overlay(
        channels=[c for c in overlay.channels if c.id != channel_id],
        carriage=[c for c in overlay.carriage if c.channel != channel_id],
        premieres=[p for p in overlay.premieres if p.channel != channel_id],
        templates=[t for t in overlay.templates if t.channel != channel_id],
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
    )


def with_premiere(overlay: Overlay, show: str, channel_id: str, premiere: date | None) -> Overlay:
    """Set (or, with None, clear) the pretend premiere of one show on one channel. Clearing
    removes only the overlay's entry; a synthetic.yaml entry would apply again."""
    rest = [p for p in overlay.premieres if (p.show, p.channel) != (show, channel_id)]
    if premiere is not None:
        rest.append(Synthetic(show=show, channel=channel_id, premiere=premiere))
    return overlay.model_copy(update={"premieres": rest})
