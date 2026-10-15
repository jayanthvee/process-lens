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


def test_a_missing_asset_still_404s(client: TestClient) -> None:
    """A missing `.js` is a missing file, not a route: a broken build must be visible."""
    response = client.get("/assets/does-not-exist-0000.js")

    assert response.status_code == 404, response.text
