"""The one local operator identity, and the 503 the page shows when there is none."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

ENV_VAR = "PROCESSLENS_OPERATOR_ID"


def test_the_operator_endpoint_is_503_when_the_variable_is_unset(
    monkeypatch: MonkeyPatch, client: TestClient
) -> None:
    """No id is invented: the page is told there is no operator, and disables its actions."""
    monkeypatch.delenv(ENV_VAR, raising=False)

    response = client.get("/api/v1/operator")

    assert response.status_code == 503, response.text
    assert ENV_VAR in response.json()["detail"]


def test_the_operator_endpoint_is_503_when_blank_or_not_a_uuid(
    monkeypatch: MonkeyPatch, client: TestClient
) -> None:
    """A blank or malformed value is the same condition as an unset one, not a crash."""
    for value in ("", "   ", "not-a-uuid", "12345"):
        monkeypatch.setenv(ENV_VAR, value)
        response = client.get("/api/v1/operator")
        assert response.status_code == 503, f"{value!r}: {response.text}"
        assert ENV_VAR in response.json()["detail"]


def test_the_operator_endpoint_returns_the_configured_id(
    monkeypatch: MonkeyPatch, client: TestClient
) -> None:
    """The id the page sends as ``decided_by`` is exactly the configured one."""
    operator_id = uuid.uuid4()
    monkeypatch.setenv(ENV_VAR, str(operator_id))

    response = client.get("/api/v1/operator")

    assert response.status_code == 200, response.text
    assert response.json() == {"operator_id": str(operator_id)}
