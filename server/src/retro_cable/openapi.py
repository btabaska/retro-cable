"""Export the OpenAPI spec: `python -m retro_cable.openapi <path>`."""

import json
import sys
from pathlib import Path

from fastapi import FastAPI

from retro_cable.app import create_app


def render_openapi(app: FastAPI) -> str:
    spec = app.openapi()
    # The version changes on every release; keep it out so the spec only diffs on API changes.
    spec["info"].pop("version", None)
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m retro_cable.openapi <output.json>", file=sys.stderr)
        return 2
    Path(argv[1]).write_text(render_openapi(create_app()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
