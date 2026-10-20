"""PL-062: the workflow editor's save, through the route that already stores versions.

An edit never changes a stored version. The page posts the edited document to
``POST /api/v1/recipes`` with the workflow and the change reason the person's diff produced; that
route stores it as the workflow's next immutable version. This file proves the three things the
editor depends on: the new version is N+1 with the reason, the old version is untouched, and
``GET /api/v1/tasks`` names the new version, so the next run uses the edited steps.
"""

from __future__ import annotations

import copy
import uuid

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from tests.db.seed import Ledger

ENV_VAR = "PROCESSLENS_OPERATOR_ID"
ORIGIN = "http://127.0.0.1:8123"


def recipe_document() -> dict:
    """A minimal valid v1 recipe: open the form, type the email, save with a check."""
    return {
        "format_version": 1,
        "meta": {
            "name": "Add inquiries",
            "when_to_use": "A spreadsheet of CRM inquiries, one row per lead.",
            "input_schema": {
                "fields": {
                    "email": {"type": "string", "required": True},
                    "full_name": {"type": "string"},
                }
            },
            "record_key": "{{row.email | lower}}",
            "idempotency_key": "{{row.email | lower}}",
            "approval_policy": {"commit": "first_record"},
            "allowed_origins": [ORIGIN],
        },
        "steps": [
            {"id": "open-form", "action": "navigate", "url": f"{ORIGIN}/new-lead.html"},
            {
                "id": "type-email",
                "action": "fill",
                "name": "Email",
                "targets": [{"by": "label", "text": "Email"}],
                "value": {"kind": "input", "column": "email"},
            },
            {
                "id": "save-lead",
                "action": "click",
                "name": "Save",
                "commit": True,
                "targets": [{"by": "role", "role": "button", "name": "Save"}],
                "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
            },
            {"id": "done", "action": "end_record", "outcome": "verified", "reason": "Added."},
        ],
    }


def edited_document() -> dict:
    """The same document after an edit: a renamed step, a new column, a new save check."""
    document = copy.deepcopy(recipe_document())
    document["steps"][1]["name"] = "Work email"
    document["steps"][1]["value"] = {"kind": "input", "column": "full_name"}
    document["steps"][2]["assert_after"] = {"url_matches": "/leads/9$", "timeout_ms": 8000}
    return document


CHANGE_REASON = (
    "Renamed step 2; step 2 now comes from the full_name column; "
    "the save is now checked by the address."
)


@pytest.fixture
def operator(connected: TestClient, ledger: Ledger, monkeypatch: MonkeyPatch) -> str:
    """The configured operator: one owner of this test's tenant."""
    user_id = ledger.add_user(role="owner", name="Editor owner", email="owner@editor.test")
    monkeypatch.setenv(ENV_VAR, str(user_id))
    return str(user_id)


def save_task(connected: TestClient) -> dict:
    created = connected.post(
        "/api/v1/tasks", json={"name": "Add inquiries", "recipe": recipe_document()}
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_an_edited_document_becomes_the_next_version_with_the_change_reason(
    connected: TestClient, conn: object, operator: str
) -> None:
    del operator
    first = save_task(connected)

    response = connected.post(
        f"/api/v1/recipes?workflow_id={first['workflow_id']}&change_reason={CHANGE_REASON}",
        json=edited_document(),
    )

    assert response.status_code == 201, response.text
    stored = response.json()
    assert stored["workflow_id"] == first["workflow_id"]
    assert stored["version"] == 2
    assert stored["change_reason"] == CHANGE_REASON
    assert stored["recipe"] == edited_document()

    versions = conn.execute(  # type: ignore[attr-defined]
        "SELECT version, change_reason FROM recipe_versions WHERE workflow_id = %s "
        "ORDER BY version",
        (uuid.UUID(first["workflow_id"]),),
    ).fetchall()
    assert [(row[0], row[1]) for row in versions] == [
        (1, "compiled from recording"),
        (2, CHANGE_REASON),
    ]


def test_the_previous_version_is_untouched(
    connected: TestClient, conn: object, operator: str
) -> None:
    del operator
    first = save_task(connected)
    connected.post(
        f"/api/v1/recipes?workflow_id={first['workflow_id']}&change_reason=edited",
        json=edited_document(),
    )

    # The version the task was already running is exactly as it was stored.
    old = connected.get(f"/api/v1/recipes/{first['recipe_version_id']}")
    assert old.status_code == 200, old.text
    assert old.json()["version"] == 1
    assert old.json()["recipe"] == recipe_document()

    # And the table itself was never rewritten: the stored document still shows the old value.
    row = conn.execute(  # type: ignore[attr-defined]
        "SELECT recipe FROM recipe_versions WHERE id = %s",
        (uuid.UUID(first["recipe_version_id"]),),
    ).fetchone()
    assert row[0] == recipe_document()


def test_the_task_points_at_the_newest_version_after_an_edit(
    connected: TestClient, ledger: Ledger, operator: str
) -> None:
    """Part 3: a task runs its latest version, so the edited steps are what the next run uses."""
    del operator
    first = save_task(connected)
    edited = connected.post(
        f"/api/v1/recipes?workflow_id={first['workflow_id']}&change_reason=edited",
        json=edited_document(),
    )
    assert edited.status_code == 201, edited.text

    listed = connected.get(f"/api/v1/tasks?tenant_id={ledger.tenant_id}")
    assert listed.status_code == 200, listed.text
    tasks = [task for task in listed.json() if task["workflow_id"] == first["workflow_id"]]
    assert len(tasks) == 1
    assert tasks[0]["recipe_version_id"] == edited.json()["id"]
    assert tasks[0]["recipe_version_id"] != first["recipe_version_id"]
