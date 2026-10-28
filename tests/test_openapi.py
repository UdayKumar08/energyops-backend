"""The web app's types are generated from web/openapi.json. If the API changes and the file is
not regenerated, the web app would be built against a description that no longer matches."""

from pathlib import Path

from energyops.service.openapi import render

COMMITTED = Path(__file__).parent.parent / "web" / "openapi.json"


def test_the_committed_api_description_is_current() -> None:
    assert COMMITTED.read_text() == render(), (
        "web/openapi.json is out of date. Regenerate it with: "
        "uv run python -m energyops.service.openapi web/openapi.json, then npm run gen:api in web/"
    )
