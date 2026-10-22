"""Hosted mode (PL-086): the public address and the team's access code replace "local only".

Every request here comes from a proxy-like peer (``10.0.0.5``), never loopback, the way the
platform delivers it. No database is needed: the routes used are the access routes, the hub's
session read and the page shell.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.api import access
from services.api.hub import PROTOCOL_SUBPROTOCOL, reset_sessions
from services.api.main import app

PUBLIC_URL = "https://processlens-api.onrender.com"
PUBLIC_HOST = "processlens-api.onrender.com"
PROXY_PEER = ("10.0.0.5", 41000)
ACCESS_CODE = "team-access-code-value"
RUNNER_SECRET = "runner-secret-value"
EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
SESSION_READ = "/api/v1/executor/session"


@pytest.fixture(autouse=True)
def _hosted(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PROCESSLENS_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("PROCESSLENS_ACCESS_CODE", ACCESS_CODE)
    monkeypatch.setenv("PROCESSLENS_RUNNER_SECRET", RUNNER_SECRET)
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)
    reset_sessions()
    yield
    reset_sessions()


@pytest.fixture
def hosted() -> Iterator[TestClient]:
    """A browser or tool reaching the public address through the platform's proxy."""
    with TestClient(app, base_url=PUBLIC_URL, client=PROXY_PEER) as test_client:
        yield test_client


def _bearer(value: str) -> dict[str, str]:
    return {"authorization": f"Bearer {value}"}


def test_the_health_check_answers_the_platform_whatever_host_it_names(
    hosted: TestClient,
) -> None:
    response = hosted.get("/health", headers={"host": "10.0.0.9:10000"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


def test_an_api_route_without_a_credential_asks_for_the_access_code(hosted: TestClient) -> None:
    response = hosted.get(SESSION_READ)

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == access.ACCESS_DETAIL


def test_a_loopback_peer_is_not_a_credential_when_hosted() -> None:
    with TestClient(app, base_url=PUBLIC_URL, client=("127.0.0.1", 1)) as local:
        response = local.get(SESSION_READ)

    assert response.status_code == 401, response.text


def test_a_host_other_than_the_public_address_is_refused(hosted: TestClient) -> None:
    response = hosted.get(SESSION_READ, headers={"host": "evil.example", **_bearer(ACCESS_CODE)})

    assert response.status_code == 403, response.text
    assert "host" in response.json()["detail"]


def test_the_access_code_and_the_runner_secret_are_each_a_credential(hosted: TestClient) -> None:
    for secret in (ACCESS_CODE, RUNNER_SECRET):
        response = hosted.get(SESSION_READ, headers=_bearer(secret))
        assert response.status_code == 200, response.text
        assert response.json() == {"attached": False, "session_id": None}

    wrong = hosted.get(SESSION_READ, headers=_bearer("not-the-code"))
    assert wrong.status_code == 401, wrong.text


def test_a_wrong_code_earns_no_cookie(hosted: TestClient) -> None:
    response = hosted.post("/api/v1/access", json={"code": "guess"})

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "That access code is not right."
    assert access.ACCESS_COOKIE not in response.cookies


def test_the_right_code_signs_this_browser_in_with_a_strict_cookie(hosted: TestClient) -> None:
    assert hosted.get("/api/v1/access").json() == {"required": True, "granted": False}

    response = hosted.post("/api/v1/access", json={"code": f"  {ACCESS_CODE} "})

    assert response.status_code == 200, response.text
    assert response.json() == {"required": True, "granted": True}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert ACCESS_CODE not in cookie, "the cookie carries an HMAC of the code, not the code"
    # The client keeps the cookie, so the next request is signed in.
    assert hosted.get("/api/v1/access").json() == {"required": True, "granted": True}
    assert hosted.get(SESSION_READ).status_code == 200


def test_a_signed_in_page_may_post_from_the_public_origin_but_not_from_another(
    hosted: TestClient,
) -> None:
    hosted.post("/api/v1/access", json={"code": ACCESS_CODE})

    own = hosted.post("/api/v1/access", json={"code": ACCESS_CODE}, headers={"origin": PUBLIC_URL})
    foreign = hosted.post(
        "/api/v1/access", json={"code": ACCESS_CODE}, headers={"origin": "https://evil.example"}
    )

    assert own.status_code == 200, own.text
    assert foreign.status_code == 403, foreign.text
    assert "origin" in foreign.json()["detail"]


def test_the_page_shell_loads_before_sign_in(hosted: TestClient) -> None:
    response = hosted.get("/")

    assert response.status_code != 401, "the page must load to ask for the code"


def test_a_hosted_api_with_no_access_code_lets_nobody_in(
    hosted: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PROCESSLENS_ACCESS_CODE")

    assert hosted.get(SESSION_READ, headers=_bearer("")).status_code == 401
    assert hosted.post("/api/v1/access", json={"code": ""}).status_code == 401
    assert hosted.get("/health").status_code == 200


def test_the_extension_connects_through_the_public_address(hosted: TestClient) -> None:
    """Session over HTTP with the access code, then the socket with the token it was given."""
    created = hosted.post(
        "/api/v1/executor/sessions",
        json={"tab_url": "https://example.test/", "tab_title": "Example"},
        headers={
            "origin": EXTENSION_ORIGIN,
            "x-processlens-client": "extension",
            **_bearer(ACCESS_CODE),
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()

    with hosted.websocket_connect(
        f"/api/v1/executor/ws?session={body['session_id']}",
        headers={"host": PUBLIC_HOST, "origin": EXTENSION_ORIGIN},
        subprotocols=[PROTOCOL_SUBPROTOCOL, f"pl.token.{body['session_token']}"],
    ) as socket:
        first = json.loads(socket.receive_text())

    assert first["type"] == "session"


def test_a_socket_naming_another_host_is_refused_when_hosted(hosted: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        hosted.websocket_connect(
            "/api/v1/runner/ws?session=x",
            headers={"host": "evil.example", **_bearer(RUNNER_SECRET)},
        ) as socket,
    ):
        socket.receive_text()

    assert refused.value.code == 4401


def test_local_mode_has_no_door(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROCESSLENS_PUBLIC_URL")
    with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 1)) as local:
        assert local.get("/api/v1/access").json() == {"required": False, "granted": True}
        assert local.get(SESSION_READ).status_code == 200
