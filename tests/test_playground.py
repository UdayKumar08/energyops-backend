import re

import pytest
from fastapi.testclient import TestClient

from energyops.api.app import STATIC_DIR, ApiSettings, create_app
from energyops.config import Environment
from energyops.models.credentials import ProviderKeys

APP = create_app(
    ApiSettings(environment=Environment.LOCAL),
    keys=ProviderKeys(gemini_api_key=None, nebius_api_key=None),
)
CLIENT = TestClient(APP, "http://127.0.0.1")
PAGE_FILES = ("playground.html", "playground.js", "playground.css")


def test_the_root_sends_you_to_the_playground() -> None:
    response = CLIENT.get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/playground"


def test_the_page_is_served_with_a_strict_content_security_policy() -> None:
    response = CLIENT.get("/playground")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    policy = response.headers["content-security-policy"]
    for rule in (
        "default-src 'none'",
        "script-src 'self'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
    ):
        assert rule in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "<title>EnergyOps playground</title>" in response.text


def test_the_script_and_stylesheet_are_served_with_the_right_types() -> None:
    script, style = CLIENT.get("/playground/app.js"), CLIENT.get("/playground/style.css")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    assert style.status_code == 200 and style.headers["content-type"].startswith("text/css")
    assert "content-security-policy" in script.headers


@pytest.mark.parametrize(
    "name", ["playground.html", "playground.js", "../app.py", "..%2Fapp.py", "__init__.py", "x"]
)
def test_only_the_two_listed_assets_can_be_fetched(name: str) -> None:
    assert CLIENT.get(f"/playground/{name}").status_code == 404


def test_the_page_answers_only_to_localhost_host_names() -> None:
    assert TestClient(APP, "http://evil.example").get("/playground").status_code == 400


def test_the_page_loads_nothing_from_outside() -> None:
    text = "\n".join((STATIC_DIR / name).read_text(encoding="utf-8") for name in PAGE_FILES)
    assert not re.search(r"https?://", text)
    html = (STATIC_DIR / "playground.html").read_text(encoding="utf-8")
    sources = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert sorted(sources) == ["/playground/app.js", "/playground/style.css"]
    assert "<style" not in html and not re.search(r"<script(?![^>]*src=)", html)
    assert " style=" not in html and " onclick=" not in html


def test_the_script_never_turns_model_text_into_markup() -> None:
    script = (STATIC_DIR / "playground.js").read_text(encoding="utf-8")
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert banned not in script, banned


def test_every_element_the_script_looks_up_exists_in_the_page() -> None:
    html = (STATIC_DIR / "playground.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "playground.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    wanted = set(re.findall(r'\$\("([^"]+)"\)', script))
    assert wanted <= ids, wanted - ids
