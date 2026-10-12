"""The API's front door: one test per check, each asserting the refusal names its check.

A refusal is 403 with ``{"detail": "..."}``, so every assertion reads the check's own name out of
the body rather than settling for "not 200". Each test would fail if its check were removed: the
foreign-origin and text/plain cases are otherwise a perfectly good request (the last test proves a
local caller reaches the route), and the loopback and host cases would reach a live route.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from services.api.main import app
from tests.db.seed import Ledger

LOCAL_BASE_URL = "http://127.0.0.1:8000"
EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"


def test_a_non_loopback_client_is_refused(client: TestClient) -> None:
    """A peer that is not this machine is refused before any route sees the request."""
    with TestClient(app, base_url=LOCAL_BASE_URL, client=("203.0.113.10", 1)) as remote:
        response = remote.get("/health")

    assert response.status_code == 403, response.text
    assert "loopback" in response.json()["detail"]


def test_a_rebound_host_header_is_refused(client: TestClient) -> None:
    """S4 for every route: a DNS name that resolves here is refused (DNS rebinding)."""
    response = client.get("/health", headers={"host": "evil.example"})

    assert response.status_code == 403, response.text
    assert "host" in response.json()["detail"]


def test_a_foreign_origin_on_a_state_changing_request_is_refused(
    connected: TestClient, ledger: Ledger
) -> None:
    """A page on another origin cannot pause the run: its ``Origin`` is not this API's own."""
    run = ledger.new_run()

    response = connected.post(
        f"/api/v1/runs/{run.id}/pause", headers={"origin": "http://evil.example"}
    )

    assert response.status_code == 403, response.text
    assert "origin" in response.json()["detail"]


def test_a_text_plain_body_is_refused(
    connected: TestClient, ledger: Ledger, conn: psycopg.Connection
) -> None:
    """A body that is not JSON is refused, so a browser must preflight (and the preflight fails)."""
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="parked")

    response = connected.post(
        f"/api/v1/records/{record_id}/requeue",
        content=b"{}",
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 403, response.text
    assert "content-type" in response.json()["detail"]
    with conn.cursor() as cur:
        cur.execute("SELECT state FROM run_records WHERE id = %s", (record_id,))
        assert cur.fetchone()[0] == "parked", "the refused request changed nothing"


def test_an_absent_origin_from_a_local_client_is_accepted(
    connected: TestClient, ledger: Ledger
) -> None:
    """A CLI, a worker and a test client send no ``Origin``, and that is allowed."""
    run = ledger.new_run()

    response = connected.post(f"/api/v1/runs/{run.id}/pause")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paused"


def test_the_pinned_extension_origin_may_open_a_session(
    connected: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extension's service worker opens its session from ``chrome-extension://<id>``."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)

    response = connected.post(
        "/api/v1/executor/sessions",
        json={},
        headers={"origin": EXTENSION_ORIGIN, "x-processlens-client": "extension"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["session_token"]


def test_another_extension_id_may_not_open_a_session(
    connected: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the pinned id passes: a different extension origin is still a stranger."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)

    response = connected.post(
        "/api/v1/executor/sessions",
        json={},
        headers={
            "origin": "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "x-processlens-client": "extension",
        },
    )

    assert response.status_code == 403, response.text
    assert "origin" in response.json()["detail"]


def test_the_extension_origin_opens_no_other_route(
    connected: TestClient, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exception is the session endpoint alone: a run cancel still wants this API's origin."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)
    run = ledger.new_run()

    response = connected.post(f"/api/v1/runs/{run.id}/cancel", headers={"origin": EXTENSION_ORIGIN})

    assert response.status_code == 403, response.text
    assert "origin" in response.json()["detail"]


def test_an_unset_extension_id_accepts_no_extension_origin(
    connected: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No pinned id means no extension origin at all, not every extension origin."""
    monkeypatch.delenv("PROCESSLENS_EXTENSION_ID", raising=False)

    response = connected.post(
        "/api/v1/executor/sessions",
        json={},
        headers={"origin": EXTENSION_ORIGIN, "x-processlens-client": "extension"},
    )

    assert response.status_code == 403, response.text
    assert "origin" in response.json()["detail"]
