"""The compile route: a recording in, a recipe and its warnings out, nothing stored.

Every test here but the run-cancel one uses the plain ``client`` fixture, so no ``DATABASE_URL``
is involved at all: that is the point of the route -- it compiles and stores nothing, so it needs
no database. The origin tests pin ``PROCESSLENS_EXTENSION_ID`` and send
``Origin: chrome-extension://<id>``, the header the popup's own fetch carries, and assert that the
extension origin is accepted on this route and still refused everywhere else.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.compiler.testing import EMAIL_TARGETS, INTEREST_TARGETS, SAVE_TARGETS, action_event
from services.api.recipes import recipe_problems
from tests.db.seed import Ledger

EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
OTHER_EXTENSION_ORIGIN = "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

SAMPLE_ROW = {"email": "ravi@example.com", "interest": "Data course"}


def request_body(recording: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    """The popup's "Make recipe" body around one recording."""
    body: dict[str, Any] = {
        "recording": recording,
        "name": "Create a record from a sheet",
        "when_to_use": "Each row has to exist in the application exactly once.",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "email",
    }
    body.update(overrides)
    return body


def a_demonstration() -> list[dict[str, Any]]:
    """Type, choose, save, and the confirmation the save showed."""
    return [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com", name="Fill Email"),
        action_event(2, "select", "select", INTEREST_TARGETS, value="Data course"),
        action_event(3, "click", "click", SAVE_TARGETS, name="Click Save"),
        action_event(
            4,
            "submit",
            "click",
            SAVE_TARGETS,
            observation={"text_visible": "Record created"},
        ),
    ]


# PL-059: a save on a ``/new`` page is checked by the address it lands on, so the unobserved-save
# tests below save on a page with no ``/new`` -- they are about the fallbacks after that one.
NOT_NEW_URL = "https://app.example.com/records"


def an_unobserved_save() -> list[dict[str, Any]]:
    """The same save, but nothing observed what it did: no assertion can be inferred."""
    return [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com", url=NOT_NEW_URL),
        action_event(2, "click", "click", SAVE_TARGETS, name="Click Save", url=NOT_NEW_URL),
        action_event(3, "submit", "click", SAVE_TARGETS, url=NOT_NEW_URL),
    ]


def test_a_recorded_demonstration_compiles_to_a_problem_free_recipe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinned extension origin compiles here, and the recipe passes ``recipe_problems``.

    Nothing needs the database: the route compiles in memory and returns.
    """
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)

    response = client.post(
        "/api/v1/recipes/compile",
        json=request_body(a_demonstration()),
        headers={"origin": EXTENSION_ORIGIN},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"recipe", "warnings", "search_kind"}
    # No search in this demonstration, so there is no searched shape to report.
    assert body["search_kind"] is None
    assert recipe_problems(body["recipe"]) == []


def test_an_unobserved_save_infers_text_from_the_record_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit commit with no observation can be inferred from a previous fill of the record
    key, so the recipe compiles and names the inferred assertion.
    """
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)

    response = client.post(
        "/api/v1/recipes/compile",
        json=request_body(an_unobserved_save(), commit_step_ids=["click-2"]),
        headers={"origin": EXTENSION_ORIGIN},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    commit = next(step for step in body["recipe"]["steps"] if step.get("commit"))
    assert commit["assert_after"] == {"text_count_increases": "{{row.email}}", "timeout_ms": 8000}
    assert any(
        "checked by the new record's name appearing" in warning for warning in body["warnings"]
    )


def test_an_unobserved_save_with_no_inferrable_fill_is_still_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit step with nothing to assert and no fill of the record key or a required column
    before it is still a 422 naming assert_after.
    """
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)

    recording = [
        action_event(
            1,
            "fill",
            "fill",
            INTEREST_TARGETS,
            value="Data course",
            name="Fill Interest",
            url=NOT_NEW_URL,
        ),
        action_event(2, "click", "click", SAVE_TARGETS, name="Click Save", url=NOT_NEW_URL),
        action_event(3, "submit", "click", SAVE_TARGETS, url=NOT_NEW_URL),
    ]
    response = client.post(
        "/api/v1/recipes/compile",
        json=request_body(recording, commit_step_ids=["click-2"]),
        headers={"origin": EXTENSION_ORIGIN},
    )

    assert response.status_code == 422, response.text
    problems = response.json()["detail"]["problems"]
    assert any("assert_after" in problem for problem in problems), problems


def test_a_recorded_navigate_outside_pinned_origins_is_a_422(client: TestClient) -> None:
    """A navigate to an origin the caller did not pin is refused, and the problem names the url."""
    recording = [
        *a_demonstration(),
        {"seq": 5, "kind": "navigate", "url": "https://evil.example.com/records/new"},
    ]

    response = client.post(
        "/api/v1/recipes/compile",
        json=request_body(recording, allowed_origins=["https://app.example.com"]),
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["message"] == "a recorded navigation goes outside allowed_origins"
    problems = detail["problems"]
    assert any("https://evil.example.com/records/new" in problem for problem in problems), problems


def test_the_extension_origin_compiles_and_may_cancel_a_run(
    connected: TestClient, ledger: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PL-105: the side panel drives every state-changing API path from its own origin."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)
    run = ledger.new_run()

    compiled = connected.post(
        "/api/v1/recipes/compile",
        json=request_body(a_demonstration()),
        headers={"origin": EXTENSION_ORIGIN},
    )
    cancelled = connected.post(
        f"/api/v1/runs/{run.id}/cancel", headers={"origin": EXTENSION_ORIGIN}
    )

    assert compiled.status_code == 200, compiled.text
    assert cancelled.status_code == 200, cancelled.text


def test_a_foreign_origin_cannot_compile(client: TestClient) -> None:
    """A page on another origin cannot spend the local compiler, and needs no database to fail."""
    response = client.post(
        "/api/v1/recipes/compile",
        json=request_body(a_demonstration()),
        headers={"origin": "http://evil.example"},
    )

    assert response.status_code == 403, response.text
    assert "origin" in response.json()["detail"]


def test_another_extension_id_cannot_compile(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the pinned id passes: every other extension origin is still a stranger."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)

    response = client.post(
        "/api/v1/recipes/compile",
        json=request_body(a_demonstration()),
        headers={"origin": OTHER_EXTENSION_ORIGIN},
    )

    assert response.status_code == 403, response.text
    assert "origin" in response.json()["detail"]


def test_a_local_client_with_no_origin_compiles(client: TestClient) -> None:
    """A CLI, a worker and a test client send no ``Origin``, and that is allowed."""
    response = client.post("/api/v1/recipes/compile", json=request_body(a_demonstration()))

    assert response.status_code == 200, response.text
    assert response.json()["recipe"]["format_version"] == 1


def test_the_compile_route_stores_nothing_and_needs_no_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route is a compiler call over HTTP: with ``DATABASE_URL`` unset it still answers."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = client.post("/api/v1/recipes/compile", json=request_body(a_demonstration()))

    assert response.status_code == 200, response.text
    assert recipe_problems(response.json()["recipe"]) == []


# ------------------------------------------------------------------ PL-077


def a_find_then_update() -> list[dict[str, Any]]:
    """Search for the SKU, open the result, change a value, save: an update, not a create."""
    search = [{"by": "role", "role": "searchbox", "name": "Search products"}]
    row = [{"by": "role", "role": "cell", "name": "SKU-1"}]
    price = [{"by": "label", "text": "Sales Price"}]
    list_url = "https://app.example.com/products"
    record_url = "https://app.example.com/products/17"
    events = [
        action_event(1, "fill", "fill", search, value="SKU-1", url=list_url),
        action_event(2, "press", "press", search, url=list_url),
        action_event(3, "click", "click", row, url=list_url),
        action_event(4, "fill", "fill", price, value="19.99", url=record_url),
        action_event(
            5,
            "click",
            "click",
            SAVE_TARGETS,
            url=record_url,
            observation={"text_visible": "Saved"},
        ),
    ]
    events[1]["step"]["key"] = "Enter"
    return events


def test_the_route_says_which_searched_shape_it_compiled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PL-077: the popup words its rule from this answer, so the route has to carry the shape."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)

    response = client.post(
        "/api/v1/recipes/compile",
        json=request_body(
            a_find_then_update(),
            sample_row={"sku": "SKU-1", "price": "19.99"},
            record_key_column="sku",
            commit_step_ids=["click-5"],
        ),
        headers={"origin": EXTENSION_ORIGIN},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["search_kind"] == "update"
    branch = next(step for step in body["recipe"]["steps"] if step["action"] == "branch")
    # An update parks a row whose search found nothing, in plain words (PL-113 item 6, restored to
    # a park by T1's review: the run page's Skip it button is the person's decision).
    assert branch["when"]["element_absent"] is not None
    assert branch["then"][0]["outcome"] == "parked"
    assert branch["then"][0]["reason"].startswith("No ")


def test_a_task_with_no_recorded_inputs_compiles_with_the_run_once_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PL-105: the panel saves a recording with no typed values under a fresh-key template."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)

    response = client.post(
        "/api/v1/recipes/compile",
        json=request_body(
            a_demonstration(),
            sample_row={"run_once_id": "x"},
            record_key_column=None,
            record_key="{{row.run_once_id}}",
        ),
        headers={"origin": EXTENSION_ORIGIN},
    )

    assert response.status_code == 200, response.text
    meta = response.json()["recipe"]["meta"]
    assert meta["record_key"] == "{{row.run_once_id}}"
    assert meta["idempotency_key"] == "{{row.run_once_id}}"
    assert meta["input_schema"]["fields"] == {"run_once_id": {"type": "string", "example": "x"}}


def a_panel_save() -> list[dict[str, Any]]:
    """What the panel records when Save is a plain button: no form submit follows the click.

    A ``submit`` event makes the compiler mark its click a commit by itself (``collapse_submits``),
    so a recording carrying one cannot show what ``commit_step_ids`` does. The panel's save does
    not depend on that: an application whose Save button posts from its own handler records no
    submit at all.
    """
    return [
        action_event(
            1,
            "fill",
            "fill",
            EMAIL_TARGETS,
            value="ravi@example.com",
            name="Fill Email",
            url=NOT_NEW_URL,
        ),
        action_event(
            2,
            "click",
            "click",
            SAVE_TARGETS,
            name="Click Save",
            url=NOT_NEW_URL,
            step_id="save-click",
        ),
        {
            "seq": 3,
            "kind": "observation",
            "url": NOT_NEW_URL,
            "step": None,
            "observation": {"text_visible": "Record created"},
        },
    ]


def test_a_panel_saved_recording_has_a_commit_step(client: TestClient) -> None:
    """PL-105 review: the panel names the recording's last click, so the saved task marks it.

    With no commit step the save compiles as a plain click: no write-ahead record and no save
    check, which is how a crash between the click and its confirmation could create the same
    record twice. The empty list the panel used to send is asserted beside it, so this test fails
    for the bug and not only for the fix.
    """
    fixed = client.post(
        "/api/v1/recipes/compile",
        json=request_body(
            a_panel_save(),
            sample_row={"email": "ravi@example.com"},
            record_key_column="email",
            commit_step_ids=["save-click"],
        ),
    )

    assert fixed.status_code == 200, fixed.text
    commits = [step for step in fixed.json()["recipe"]["steps"] if step.get("commit")]
    assert len(commits) == 1, fixed.json()["recipe"]["steps"]
    assert commits[0]["name"] == "Click Save"
    assert commits[0]["assert_after"] == {"text_visible": "Record created", "timeout_ms": 8000}

    unchecked = client.post(
        "/api/v1/recipes/compile",
        json=request_body(
            a_panel_save(),
            sample_row={"email": "ravi@example.com"},
            record_key_column="email",
            commit_step_ids=[],
        ),
    )

    assert unchecked.status_code == 200, unchecked.text
    assert not any(step.get("commit") for step in unchecked.json()["recipe"]["steps"])
