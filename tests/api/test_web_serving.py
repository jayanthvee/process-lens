"""The built page is served at `/`, and a client-side route is not a missing file.

The tests skip themselves when ``apps/web/dist`` has not been built, exactly as the mount itself
answers one line instead of crashing when the directory is not there.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api.main import WEB_DIST

pytestmark = pytest.mark.skipif(not WEB_DIST.is_dir(), reason="the page has not been built yet")

ROOT_ELEMENT = '<div id="root">'


def test_the_built_page_is_served_at_the_root(client: TestClient) -> None:
    """`/` answers the built page, so the operator can open the app."""
    response = client.get("/")

    assert response.status_code == 200, response.text
    assert ROOT_ELEMENT in response.text


def test_a_client_side_route_serves_the_page_instead_of_a_404(client: TestClient) -> None:
    """A refresh on `/runs/abc` loads the app, and the router inside it decides what to draw."""
    response = client.get("/runs/abc")

    assert response.status_code == 200, response.text
    assert ROOT_ELEMENT in response.text


def test_a_route_with_a_dot_serves_the_page(client: TestClient) -> None:
    """A refresh on `/runs/abc.def` loads the app: an unknown suffix is a route, not an asset."""
    response = client.get("/runs/abc.def")

    assert response.status_code == 200, response.text
    assert ROOT_ELEMENT in response.text


def test_a_missing_asset_still_404s(client: TestClient) -> None:
    """A missing `.js` is a missing file, not a route: a broken build must be visible."""
    response = client.get("/assets/missing.js")

    assert response.status_code == 404, response.text


def test_an_unknown_json_api_path_still_404s_as_json(client: TestClient) -> None:
    """A miss under `/api/` keeps the API's 404 even when the path looks like an asset."""
    response = client.get("/api/v1/nope.json")

    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert ROOT_ELEMENT not in response.text


def test_an_unknown_api_path_still_404s_as_json(client: TestClient) -> None:
    """A miss under `/api/` is the API's own 404, never the page: a caller gets JSON, not HTML.

    Otherwise a mistyped API path would answer 200 with the app shell, and a page that parses JSON
    would sit on "Loading…" for a request that actually failed.
    """
    response = client.get("/api/v1/nope")

    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert ROOT_ELEMENT not in response.text


def test_a_bare_api_path_is_not_the_page(client: TestClient) -> None:
    """`/api` with no trailing slash is an API miss too, never the page."""
    response = client.get("/api")

    assert response.status_code == 404, response.text
    assert ROOT_ELEMENT not in response.text
