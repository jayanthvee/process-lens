"""PL-112 item 1: ``POST /compile`` tells "sent []" from "not sent".

The panel's "Nothing gets saved" sends an empty list on purpose. An older caller that predates the
field sends nothing at all. The route has to keep the two apart before the compiler sees them.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from packages.compiler.testing import EMAIL_TARGETS, SAVE_TARGETS, action_event

NOT_NEW_URL = "https://app.example.com/records"


def sign_in_recording() -> list[dict[str, Any]]:
    """Fill the email, click Continue, and the same form submits -- a recorded sign-in."""
    return [
        action_event(
            1,
            "fill",
            "fill",
            EMAIL_TARGETS,
            value="ravi@example.com",
            url=NOT_NEW_URL,
        ),
        action_event(
            2,
            "click",
            "click",
            SAVE_TARGETS,
            name="Click Continue",
            url=NOT_NEW_URL,
        ),
        action_event(3, "submit", "click", SAVE_TARGETS, url=NOT_NEW_URL),
    ]


def body(recording: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "recording": recording,
        "name": "Sign in to the app",
        "when_to_use": "The account needs a code from its email before it shows its pages.",
        "sample_row": {"email": "ravi@example.com"},
        "record_key_column": "email",
    }
    request.update(overrides)
    return request


def test_an_empty_commit_list_compiles_with_no_commit_step(client: TestClient) -> None:
    """The panel chose "Nothing gets saved", so the submit must not become a commit."""
    response = client.post(
        "/api/v1/recipes/compile",
        json=body(sign_in_recording(), commit_step_ids=[]),
    )

    assert response.status_code == 200, response.text
    steps = response.json()["recipe"]["steps"]
    assert [step for step in steps if step.get("commit")] == []


def test_an_omitted_commit_list_keeps_the_older_behaviour(client: TestClient) -> None:
    """An older caller sends no field; the submit still makes Continue a commit, so it is refused
    for carrying no assertion -- exactly what the route did before the field existed.
    """
    payload = body(sign_in_recording())
    assert "commit_step_ids" not in payload

    response = client.post("/api/v1/recipes/compile", json=payload)

    assert response.status_code == 422, response.text
    problems = response.json()["detail"]["problems"]
    assert any("assert_after" in problem for problem in problems), problems
