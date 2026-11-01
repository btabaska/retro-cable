"""The checked-in catalog/ must always load and validate, and its schemas must be current."""

from pathlib import Path

from retro_cable.catalog import load_catalog, validate
from retro_cable.catalog.schema import render_schemas

CATALOG = Path(__file__).resolve().parents[3] / "catalog"


def test_repo_catalog_is_valid() -> None:
    assert validate(load_catalog(CATALOG)) == []


def test_committed_schemas_match_the_models() -> None:
    """catalog/schema/ is generated; run `make catalog-schema` if this fails."""
    for name, text in render_schemas().items():
        assert (CATALOG / "schema" / f"{name}.schema.json").read_text() == text, name


def test_yaml_files_point_editors_at_their_schema() -> None:
    for name in render_schemas():
        path = CATALOG / f"{name}.yaml"
        if path.exists():
            first = path.read_text().splitlines()[0]
            assert first == f"# yaml-language-server: $schema=schema/{name}.schema.json"
