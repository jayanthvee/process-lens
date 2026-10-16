"""The sidebar's extension status: whether a session is attached right now.

The hub is an in-memory relay that never touches the database, so these tests need no
``DATABASE_URL`` and start from an empty registry. The route only reads the registry under its own
lock; the sockets are covered by ``tests/runner/test_hub.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from starlette.websockets import WebSocket

from services.api import hub
from services.api.hub import Peer, Session, reset_sessions

SESSION_STATE_PATH = "/api/v1/executor/session"
EXTENSION_CLIENT = {"x-processlens-client": "extension"}


@pytest.fixture(autouse=True)
def _empty_registry() -> Iterator[None]:
    """Every test starts from nothing, and leaves nothing behind."""
    reset_sessions()
    yield
    reset_sessions()


def _session(session_id: str = "session-1", expires_at: float = 1e12) -> Session:
    """One registry row, built the way the popup's Connect call builds it."""
    return Session(
        session_id=session_id,
        session_token="session-token",
        resume_token=None,
        tab_url="https://example.test/leads/new",
        tab_title="New lead",
        expires_at=expires_at,
    )


def test_no_session_at_all_means_not_attached(client: TestClient) -> None:
    """With nothing open, the sidebar says Chrome is not connected."""
    response = client.get(SESSION_STATE_PATH)

    assert response.status_code == 200, response.text
    assert response.json() == {"attached": False}


def test_an_open_session_that_has_not_dialled_in_is_not_attached(
    monkeypatch: MonkeyPatch, client: TestClient
) -> None:
    """A session the popup opened is not a connected extension: the socket is what connects it."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", "abcdefghijklmnopabcdefghijklmnop")
    created = client.post(
        "/api/v1/executor/sessions",
        json={"tab_url": "https://example.test/leads/new", "tab_title": "New lead"},
        headers=EXTENSION_CLIENT,
    )
    assert created.status_code == 201, created.text

    assert client.get(SESSION_STATE_PATH).json() == {"attached": False}


def test_a_session_with_the_extension_socket_attached_is_attached(client: TestClient) -> None:
    """The extension's socket is the one thing that makes the status read connected."""
    row = _session()
    row.extension = Peer(role=hub.ROLE_EXTENSION, websocket=cast(WebSocket, None))
    hub._sessions[row.session_id] = row

    response = client.get(SESSION_STATE_PATH)

    assert response.status_code == 200, response.text
    assert response.json() == {"attached": True}


def test_an_expired_session_is_not_reported_as_attached(client: TestClient) -> None:
    """The same expiry purge the rest of the hub runs decides it: a dead row is not shown."""
    row = _session(expires_at=0.0)
    hub._sessions[row.session_id] = row

    assert client.get(SESSION_STATE_PATH).json() == {"attached": False}
