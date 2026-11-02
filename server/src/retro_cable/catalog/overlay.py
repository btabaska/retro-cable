"""Admin edits as an overlay on the YAML catalog (Phase 10).

The admin never writes schedules or the YAML files. It writes an Overlay, which holds channels
and carriage in the same models as the YAML, and the effective catalog is the YAML catalog with
the overlay merged in. The catalog version covers both, so a schedule stays a pure function of
(channel, date, catalog version, seed). Overlay channel ids start with `custom-`, so they can
never collide with YAML ids.
"""

import hashlib
import json
from dataclasses import replace

from pydantic import Field

from retro_cable.catalog.load import Catalog
from retro_cable.catalog.models import (
    CUSTOM_PREFIX,
    Carriage,
    CarriageFile,
    Channel,
    ChannelsFile,
    Model,
)

__all__ = [
    "CUSTOM_PREFIX",
    "Overlay",
    "apply_overlay",
    "canonical",
    "overlay_version",
    "parse",
    "with_channel",
    "without_channel",
]


class Overlay(Model):
    """Inputs added in /admin. Empty means the catalog is exactly the YAML."""

    channels: list[Channel] = Field(default_factory=list)
    carriage: list[Carriage] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.channels and not self.carriage


def canonical(overlay: Overlay) -> str:
    """The overlay as canonical JSON: stable key order, no whitespace, dates as ISO text."""
    data = overlay.model_dump(mode="json", by_alias=True)
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
    return replace(
        catalog,
        channels=channels,
        carriage=carriage,
        version=overlay_version(catalog.version, overlay),
    )


def without_channel(overlay: Overlay, channel_id: str) -> Overlay:
    return Overlay(
        channels=[c for c in overlay.channels if c.id != channel_id],
        carriage=[c for c in overlay.carriage if c.channel != channel_id],
    )


def with_channel(overlay: Overlay, channel: Channel, carriage: list[Carriage]) -> Overlay:
    """Insert or replace one custom channel and all of its carriage."""
    rest = without_channel(overlay, channel.id)
    return Overlay(channels=[*rest.channels, channel], carriage=[*rest.carriage, *carriage])
