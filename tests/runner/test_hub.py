"""Tests for the WebSocket hub.

``TestClient`` is used as a context manager on purpose: inside the ``with``, every HTTP request and
every WebSocket runs in the same event loop, so both sockets of one session can be attached at once
and the relay under test is the real one. Every request carries a ``127.0.0.1`` peer address and an
explicit local ``Host`` header, because the hub's own front door refuses anything else.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from services.api import hub
from services.api.hub import PROTOCOL_SUBPROTOCOL, is_loopback, reset_sessions
from services.api.main import app

HOST_HEADERS = {"host": "127.0.0.1:8000"}
LOCAL_CLIENT = ("127.0.0.1", 51234)
RUNNER_SECRET = "runner-secret-value"
EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
PAGE_ORIGIN = "http://localhost:5173"


@pytest.fixture(autouse=True)
def _retire_leftover_runs() -> Iterator[None]:
    """Shadow the runner package's database fixture.

    ``tests/runner/conftest.py`` retires leftover runs in a live database before every test in this
    directory. The hub is an in-memory relay that never touches the database, so this module
    overrides that fixture with a no-op and needs no ``DATABASE_URL``.
    """
    yield


@pytest.fixture(autouse=True)
def _hub_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The secrets the hub needs, and an empty session registry for each test."""
    monkeypatch.setenv("PROCESSLENS_RUNNER_SECRET", RUNNER_SECRET)
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)
    reset_sessions()
    yield
    reset_sessions()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose peer address is loopback and whose sockets share one event loop."""
    with TestClient(app, client=LOCAL_CLIENT) as test_client:
        yield test_client


def _create_session(client: TestClient, **extra: str) -> dict[str, Any]:
    """The popup's Connect call, and the session token it is told exactly once."""
    response = client.post(
        "/api/v1/executor/sessions",
        json={"tab_url": "https://example.test/leads/new", "tab_title": "New lead"},
        headers={**HOST_HEADERS, "x-processlens-client": "extension", **extra},
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def _extension_url(session_id: str) -> str:
    return f"/api/v1/executor/ws?session={session_id}"


def _runner_url(session_id: str) -> str:
    return f"/api/v1/runner/ws?session={session_id}"


def _extension_subprotocols(token: str) -> list[str]:
    """What the extension offers: the protocol, and its token in a second subprotocol."""
    return [PROTOCOL_SUBPROTOCOL, f"pl.token.{token}"]


def _extension_headers(**extra: str) -> dict[str, str]:
    return {**HOST_HEADERS, "origin": EXTENSION_ORIGIN, **extra}


def _runner_headers(secret: str | None = RUNNER_SECRET) -> dict[str, str]:
    headers = dict(HOST_HEADERS)
    if secret is not None:
        headers["authorization"] = f"Bearer {secret}"
    return headers


def _detach_headers() -> dict[str, str]:
    return {**HOST_HEADERS, "authorization": f"Bearer {RUNNER_SECRET}"}


def _close_code(socket: WebSocketTestSession) -> int:
    """The close code the hub sends, read from the frame the client sees."""
    with pytest.raises(WebSocketDisconnect) as refused:
        socket.receive_text()
    return refused.value.code


def _pending_frames(session_id: str, role: str) -> int:
    """Frames the hub still holds for one attached socket.

    "The hub sent nothing else" cannot wait on the socket, because ``TestClient.receive`` blocks
    until a frame arrives; it reads the relay's own queue instead.
    """
    row = hub._sessions[session_id]
    peer = row.extension if role == "extension" else row.runner
    return 0 if peer is None else peer.queue.qsize()


def test_runner_channel_without_the_secret_is_refused_before_reading(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: an unset secret closes the runner channel to everyone, and reads nothing."""
    created = _create_session(client)
    session_id = created["session_id"]

    monkeypatch.delenv("PROCESSLENS_RUNNER_SECRET", raising=False)
    with client.websocket_connect(_runner_url(session_id), headers=_runner_headers()) as socket:
        assert _close_code(socket) == 4401

    monkeypatch.setenv("PROCESSLENS_RUNNER_SECRET", RUNNER_SECRET)
    with client.websocket_connect(_runner_url(session_id), headers=_runner_headers(None)) as socket:
        assert _close_code(socket) == 4401

    assert hub._sessions[session_id].runner is None


def test_extension_channel_with_a_page_origin_is_refused(client: TestClient) -> None:
    """A page, a near-miss extension id, and a missing Origin are all refused."""
    created = _create_session(client)
    session_id = created["session_id"]
    token = created["session_token"]

    refused = [PAGE_ORIGIN, "https://evil.test", f"chrome-extension://{EXTENSION_ID}x"]
    for origin in refused:
        with client.websocket_connect(
            _extension_url(session_id),
            subprotocols=_extension_subprotocols(token),
            headers=_extension_headers(origin=origin),
        ) as socket:
            assert _close_code(socket) == 4401

    with client.websocket_connect(
        _extension_url(session_id),
        subprotocols=_extension_subprotocols(token),
        headers=dict(HOST_HEADERS),
    ) as socket:
        assert _close_code(socket) == 4401

    assert hub._sessions[session_id].extension is None


def test_extension_channel_with_a_wrong_token_is_refused(client: TestClient) -> None:
    """A wrong token, no token, a missing protocol subprotocol, and an unknown session."""
    created = _create_session(client)
    session_id = created["session_id"]
    token = created["session_token"]

    with client.websocket_connect(
        _extension_url(session_id),
        subprotocols=[PROTOCOL_SUBPROTOCOL, "pl.token.wrong"],
        headers=_extension_headers(),
    ) as socket:
        assert _close_code(socket) == 4401

    with client.websocket_connect(
        _extension_url(session_id),
        subprotocols=[PROTOCOL_SUBPROTOCOL],
        headers=_extension_headers(),
    ) as socket:
        assert _close_code(socket) == 4401

    with client.websocket_connect(
        _extension_url(session_id),
        subprotocols=[f"pl.token.{token}"],
        headers=_extension_headers(),
    ) as socket:
        assert _close_code(socket) == 4400

    with client.websocket_connect(
        _extension_url(str(uuid.uuid4())),
        subprotocols=_extension_subprotocols(token),
        headers=_extension_headers(),
    ) as socket:
        assert _close_code(socket) == 4401

    assert hub._sessions[session_id].extension is None
    assert hub._sessions[session_id].claimed is False


def test_the_loopback_check_refuses_a_public_address() -> None:
    """A real non-loopback peer is not reproducible in-process, so the predicate is asserted."""
    assert is_loopback("127.0.0.1")
    assert is_loopback("::1")
    assert is_loopback("localhost")
    assert is_loopback("::ffff:127.0.0.1")
    assert not is_loopback("203.0.113.10")
    assert not is_loopback("10.0.0.7")
    assert not is_loopback("localhost.attacker.test")
    assert not is_loopback("")
    assert not is_loopback(None)


def test_a_dns_rebinding_host_header_is_refused(client: TestClient) -> None:
    """S4: a name that resolves here does not reach the executor API or the extension socket."""
    rebinding = {"host": "attacker.example.com", "x-processlens-client": "extension"}
    response = client.post("/api/v1/executor/sessions", json={}, headers=rebinding)
    assert response.status_code == 403

    created = _create_session(client)
    with client.websocket_connect(
        _extension_url(created["session_id"]),
        subprotocols=_extension_subprotocols(created["session_token"]),
        headers={**rebinding, "origin": EXTENSION_ORIGIN},
    ) as socket:
        assert _close_code(socket) == 4401


def test_the_hub_selects_the_protocol_subprotocol_and_never_echoes_the_token(
    client: TestClient,
) -> None:
    """S3: the accept names the protocol subprotocol, and the token never comes back."""
    created = _create_session(client)
    token = created["session_token"]
    with client.websocket_connect(
        _extension_url(created["session_id"]),
        subprotocols=_extension_subprotocols(token),
        headers=_extension_headers(),
    ) as socket:
        assert socket.accepted_subprotocol == PROTOCOL_SUBPROTOCOL
        for name, value in socket.extra_headers or []:
            assert token.encode() not in value, name

        first = socket.receive_text()
        frame = json.loads(first)
        assert frame["type"] == "session"
        assert sorted(frame) == ["protocol", "resume_token", "type"]
        assert frame["protocol"] == 1
        assert frame["resume_token"] != token
        assert token not in first


def test_a_resume_token_is_single_use(client: TestClient) -> None:
    """R5: the resume token re-attaches once, and the socket that used it is the only one."""
    created = _create_session(client)
    session_id = created["session_id"]
    session_token = created["session_token"]

    with client.websocket_connect(
        _extension_url(session_id),
        subprotocols=_extension_subprotocols(session_token),
        headers=_extension_headers(),
    ) as extension:
        resume = json.loads(extension.receive_text())["resume_token"]
        assert json.loads(extension.receive_text())["type"] == "peer_ready"

        with client.websocket_connect(_runner_url(session_id), headers=_runner_headers()) as runner:
            assert json.loads(runner.receive_text())["type"] == "peer_ready"
            assert json.loads(extension.receive_text())["type"] == "peer_ready"

            # The extension drops, and the runner learning about it proves the hub saw it go.
            extension.close()
            assert json.loads(runner.receive_text())["type"] == "peer_gone"

            with client.websocket_connect(
                _extension_url(session_id),
                subprotocols=_extension_subprotocols(resume),
                headers=_extension_headers(),
            ) as reattached:
                second = json.loads(reattached.receive_text())
                assert second["type"] == "session"
                assert second["resume_token"] != resume
                assert json.loads(reattached.receive_text())["type"] == "peer_ready"

                with client.websocket_connect(
                    _extension_url(session_id),
                    subprotocols=_extension_subprotocols(resume),
                    headers=_extension_headers(),
                ) as third:
                    assert _close_code(third) == 4401

                with client.websocket_connect(
                    _extension_url(session_id),
                    subprotocols=_extension_subprotocols(session_token),
                    headers=_extension_headers(),
                ) as fourth:
                    assert _close_code(fourth) == 4401


def test_close_detaches_the_session_and_the_extension_re_attaches(client: TestClient) -> None:
    """S6: the runner stays attached, the extension returns, and the relay carries a command."""
    created = _create_session(client)
    session_id = created["session_id"]

    with client.websocket_connect(
        _extension_url(session_id),
        subprotocols=_extension_subprotocols(created["session_token"]),
        headers=_extension_headers(),
    ) as extension:
        resume = json.loads(extension.receive_text())["resume_token"]
        assert json.loads(extension.receive_text())["type"] == "peer_ready"

        with client.websocket_connect(_runner_url(session_id), headers=_runner_headers()) as runner:
            assert json.loads(runner.receive_text())["type"] == "peer_ready"
            assert json.loads(extension.receive_text())["type"] == "peer_ready"

            detached = client.post(
                f"/api/v1/executor/sessions/{session_id}/close",
                headers=_detach_headers(),
            )
            assert detached.status_code == 200
            assert detached.json() == {"detached": True}
            assert _close_code(extension) == 4409
            assert json.loads(runner.receive_text())["type"] == "peer_gone"
            assert hub._sessions[session_id].runner is not None
            assert hub._sessions[session_id].extension is None

            with client.websocket_connect(
                _extension_url(session_id),
                subprotocols=_extension_subprotocols(resume),
                headers=_extension_headers(),
            ) as reattached:
                assert json.loads(reattached.receive_text())["type"] == "session"
                assert json.loads(reattached.receive_text())["type"] == "peer_ready"
                assert json.loads(runner.receive_text())["type"] == "peer_ready"

                command = '{"protocol": 1, "type": "step_command", "command_id": "after-detach"}'
                reattached.send_text(command)
                assert runner.receive_text() == command


def test_the_hub_never_queues_a_command_for_an_absent_peer(client: TestClient) -> None:
    """A command with no extension attached is answered, not held, and no `peer_ready` is sent."""
    created = _create_session(client)
    session_id = created["session_id"]
    command = '{"protocol": 1, "type": "step_command", "command_id": "absent"}'

    with client.websocket_connect(_runner_url(session_id), headers=_runner_headers()) as runner:
        assert _pending_frames(session_id, "runner") == 0
        runner.send_text(command)
        error = json.loads(runner.receive_text())
        assert error == {
            "protocol": 1,
            "type": "hub_error",
            "code": "peer_unavailable",
            "command_id": "absent",
        }

        with client.websocket_connect(
            _extension_url(session_id),
            subprotocols=_extension_subprotocols(created["session_token"]),
            headers=_extension_headers(),
        ) as extension:
            received = [extension.receive_text() for _ in range(2)]
            assert [json.loads(text)["type"] for text in received] == ["session", "peer_ready"]
            assert all("absent" not in text for text in received)
            assert _pending_frames(session_id, "extension") == 0
            assert json.loads(runner.receive_text())["type"] == "peer_ready"


def test_the_hub_relays_a_command_verbatim(client: TestClient) -> None:
    """A byte-for-byte round trip in both directions: the hub moves text, it does not rewrite it."""
    created = _create_session(client)
    session_id = created["session_id"]
    command = (
        '{"protocol": 1, "type": "step_command", "command_id": "cmd-1",'
        ' "step": {"id": "fill-email", "value": "Ada  Lovelace\\t/x"}, "note": "h\\u00e9llo"}'
    )
    answer = '{"protocol": 1, "type": "step_result", "command_id": "cmd-1", "ok": true}'

    with client.websocket_connect(
        _extension_url(session_id),
        subprotocols=_extension_subprotocols(created["session_token"]),
        headers=_extension_headers(),
    ) as extension:
        assert json.loads(extension.receive_text())["type"] == "session"
        assert json.loads(extension.receive_text())["type"] == "peer_ready"

        with client.websocket_connect(_runner_url(session_id), headers=_runner_headers()) as runner:
            assert json.loads(runner.receive_text())["type"] == "peer_ready"
            assert json.loads(extension.receive_text())["type"] == "peer_ready"

            extension.send_text(command)
            assert runner.receive_text() == command

            runner.send_text(answer)
            assert extension.receive_text() == answer


def test_peer_gone_reaches_the_still_connected_side(client: TestClient) -> None:
    """Whichever side drops, the side still attached is told."""
    first = _create_session(client)
    with client.websocket_connect(
        _extension_url(first["session_id"]),
        subprotocols=_extension_subprotocols(first["session_token"]),
        headers=_extension_headers(),
    ) as extension:
        assert json.loads(extension.receive_text())["type"] == "session"
        assert json.loads(extension.receive_text())["type"] == "peer_ready"

        with client.websocket_connect(
            _runner_url(first["session_id"]), headers=_runner_headers()
        ) as runner:
            assert json.loads(runner.receive_text())["type"] == "peer_ready"
            assert json.loads(extension.receive_text())["type"] == "peer_ready"

            extension.close()
            assert json.loads(runner.receive_text()) == {"protocol": 1, "type": "peer_gone"}

    second = _create_session(client)
    with client.websocket_connect(
        _extension_url(second["session_id"]),
        subprotocols=_extension_subprotocols(second["session_token"]),
        headers=_extension_headers(),
    ) as extension:
        assert json.loads(extension.receive_text())["type"] == "session"
        assert json.loads(extension.receive_text())["type"] == "peer_ready"

        with client.websocket_connect(
            _runner_url(second["session_id"]), headers=_runner_headers()
        ) as runner:
            assert json.loads(runner.receive_text())["type"] == "peer_ready"
            assert json.loads(extension.receive_text())["type"] == "peer_ready"

            runner.close()
            assert json.loads(extension.receive_text()) == {"protocol": 1, "type": "peer_gone"}


def test_the_completed_session_returns_the_resume_token_exactly_once(client: TestClient) -> None:
    """The session frame is the first and only one, and it rotates the token on every attach."""
    created = _create_session(client)
    session_id = created["session_id"]
    session_token = created["session_token"]
    assert created["protocol"] == 1
    assert created["expires_at"] > time.time()

    with client.websocket_connect(
        _extension_url(session_id),
        subprotocols=_extension_subprotocols(session_token),
        headers=_extension_headers(),
    ) as extension:
        first = json.loads(extension.receive_text())
        assert first["type"] == "session"
        resume = first["resume_token"]
        assert resume != session_token
        assert json.loads(extension.receive_text())["type"] == "peer_ready"
        assert _pending_frames(session_id, "extension") == 0

        detached = client.post(
            f"/api/v1/executor/sessions/{session_id}/close",
            headers=_detach_headers(),
        )
        assert detached.json() == {"detached": True}
        assert _close_code(extension) == 4409
        assert _pending_frames(session_id, "extension") == 0

    with client.websocket_connect(
        _extension_url(session_id),
        subprotocols=_extension_subprotocols(resume),
        headers=_extension_headers(),
    ) as reattached:
        resumed = json.loads(reattached.receive_text())
        assert resumed["type"] == "session"
        assert resumed["resume_token"] not in {session_token, resume}
        assert json.loads(reattached.receive_text())["type"] == "peer_ready"
        assert _pending_frames(session_id, "extension") == 0


def test_two_concurrent_attaches_with_one_token_leave_exactly_one(client: TestClient) -> None:
    """B5: the token match and the slot reservation are one critical section.

    Two extension sockets that present the same session token at the same instant must not both
    attach: the loser would overwrite the winner's slot, inject frames, and announce a false
    ``peer_gone`` when it retires.
    """
    created = _create_session(client)
    session_id = created["session_id"]
    token = created["session_token"]
    start = threading.Barrier(2)
    collected = threading.Lock()
    results: list[tuple[str, int | None]] = []

    def attach() -> None:
        start.wait()
        with client.websocket_connect(
            _extension_url(session_id),
            subprotocols=_extension_subprotocols(token),
            headers=_extension_headers(),
        ) as socket:
            try:
                first = json.loads(socket.receive_text())["type"]
            except WebSocketDisconnect as refused:
                with collected:
                    results.append(("refused", refused.code))
                return
            with collected:
                results.append((first, None))

    threads = [threading.Thread(target=attach) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    winners = [kind for kind, _code in results if kind == "session"]
    refused = [code for kind, code in results if kind == "refused"]
    assert winners == ["session"]
    assert len(refused) == 1
    assert refused[0] in (4401, 4403)
    assert hub._sessions[session_id].claimed is True
