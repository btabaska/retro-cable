"""Read catalog/*.yaml into typed models."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from retro_cable.catalog.models import (
    FILE_MODELS,
    CarriageFile,
    Channel,
    ChannelsFile,
    HolidaysFile,
    Model,
    OverridesFile,
    SyntheticFile,
    TemplatesFile,
)

OPTIONAL = {"carriage", "synthetic", "overrides", "templates"}


class CatalogError(Exception):
    pass


class _Loader(yaml.SafeLoader):
    """SafeLoader that leaves dates and YAML 1.1 sexagesimal numbers ("1:30") as strings.

    Pydantic parses dates itself, with clear errors, and titles keep their exact text.
    """


_TIMESTAMP = "tag:yaml.org,2002:timestamp"
_INT = "tag:yaml.org,2002:int"
_Loader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag not in (_TIMESTAMP, _INT)]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for _first in "-+0123456789":
    _Loader.add_implicit_resolver(_INT, re.compile(r"^[-+]?[0-9]+$"), [_first])


@dataclass(frozen=True)
class Catalog:
    channels: ChannelsFile
    holidays: HolidaysFile
    carriage: CarriageFile
    synthetic: SyntheticFile
    overrides: OverridesFile
    templates: TemplatesFile
    version: str  # first 12 hex chars of a sha256 over the input files

    def channel(self, channel_id: str) -> Channel:
        for channel in self.channels.channels:
            if channel.id == channel_id:
                return channel
        raise KeyError(channel_id)


def _describe(name: str, err: ValidationError) -> str:
    lines = [f"{name}.yaml is invalid:"]
    for e in err.errors():
        where = ".".join(str(p) for p in e["loc"])
        lines.append(f"  {where}: {e['msg']}")
    return "\n".join(lines)


def _read(root: Path, name: str, digest: Any) -> Model:
    path = root / f"{name}.yaml"
    model = FILE_MODELS[name]
    if not path.exists():
        if name in OPTIONAL:
            return model()
        raise CatalogError(f"{name}.yaml is missing from {root}")
    raw = path.read_bytes()
    digest.update(name.encode() + b"\0" + raw + b"\0")
    try:
        data = yaml.load(raw, Loader=_Loader) or {}
    except yaml.YAMLError as e:
        raise CatalogError(f"{name}.yaml is not valid YAML: {e}") from e
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise CatalogError(_describe(name, e)) from e


def load_catalog(root: Path) -> Catalog:
    digest = hashlib.sha256()
    files = {name: _read(root, name, digest) for name in FILE_MODELS}
    return Catalog(
        channels=files["channels"],  # type: ignore[arg-type]
        holidays=files["holidays"],  # type: ignore[arg-type]
        carriage=files["carriage"],  # type: ignore[arg-type]
        synthetic=files["synthetic"],  # type: ignore[arg-type]
        overrides=files["overrides"],  # type: ignore[arg-type]
        templates=files["templates"],  # type: ignore[arg-type]
        version=digest.hexdigest()[:12],
    )
