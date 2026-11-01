import json
from pathlib import Path

from retro_cable.app import create_app
from retro_cable.openapi import render_openapi

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "openapi.json"


def test_committed_openapi_matches_app() -> None:
    """server/openapi.json is generated; run `make api` if this fails."""
    assert OPENAPI_PATH.read_text() == render_openapi(create_app())


def test_openapi_names_operations_for_the_generated_client() -> None:
    spec = json.loads(render_openapi(create_app()))
    assert spec["paths"]["/health"]["get"]["operationId"] == "getHealth"
    assert spec["paths"]["/version"]["get"]["operationId"] == "getVersion"
