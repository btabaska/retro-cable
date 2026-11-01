"""Export JSON Schemas for catalog/*.yaml: `python -m retro_cable.catalog.schema <dir>`.

Editors use them through the yaml-language-server comment at the top of each file, and the
Phase 11 channel builder validates model drafts against them.
"""

import json
import sys
from pathlib import Path

from retro_cable.catalog.models import FILE_MODELS


def render_schemas() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, model in FILE_MODELS.items():
        schema = model.model_json_schema(by_alias=True)
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = f"catalog/{name}.yaml"
        out[name] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return out


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "catalog/schema")
    out.mkdir(parents=True, exist_ok=True)
    for name, text in render_schemas().items():
        (out / f"{name}.schema.json").write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
