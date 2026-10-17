"""PL-030: ``POST /api/v1/tasks`` -- the route the popup's "Save as task" calls.

Today the popup compiles a recipe and keeps it in extension storage; no route
creates the task the dashboard lists, so a person cannot get from "Save as
task" to a run without a test harness. This route takes ``{name, recipe}`` and
writes one workflow and its version 1, under the configured operator's tenant.

The operator is configuration (``PROCESSLENS_OPERATOR_ID``), so each test pins
it to a person of its own tenant via the ``operator`` fixture; monkeypatch
restores the environment afterwards.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from tests.db.seed import Ledger

ENV_VAR = "PROCESSLENS_OPERATOR_ID"
ORIGIN = "http://127.0.0.1:8123"
EXTENSION_ID = "a" * 32


def recipe_document(*, with_assertion: bool = True) -> dict:
    """A minimal valid v1 recipe, or the same one missing its commit's assertion."""
    commit: dict = {
        "id": "save-lead",
        "name": "Save the lead",
        "action": "click",
        "commit": True,
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
    }
    if with_assertion:
        commit["assert_after"] = {"text_visible": "Lead created", "timeout_ms": 8000}
    return {
        "format_version": 1,
        "meta": {
            "name": "Add inquiries",
            "when_to_use": "A spreadsheet of CRM inquiries, one row per lead.",
            "input_schema": {"fields": {"email": {"type": "string", "required": True}}},
            "record_key": "{{row.email | lower}}",
            "idempotency_key": "{{row.email | lower}}",
            "approval_policy": {"commit": "first_record"},
            "allowed_origins": [ORIGIN],
        },
        "steps": [
            {"id": "open-form", "action": "navigate", "url": f"{ORIGIN}/new-lead.html"},
            commit,
        ],
    }


@pytest.fixture
def operator(connected: TestClient, ledger: Ledger, monkeypatch: MonkeyPatch) -> str:
    """The configured operator: one owner of this test's tenant."""
    user_id = ledger.add_user(role="owner", name="Task owner", email="owner@tasks.test")
    monkeypatch.setenv(ENV_VAR, str(user_id))
    return str(user_id)


def test_saving_a_task_creates_one_workflow_and_one_version(
    connected: TestClient, conn: object, ledger: Ledger, operator: str
) -> None:
    """201, one workflow and one version under the operator's tenant, and GET /tasks lists it."""
    body = {"name": "Add inquiries", "recipe": recipe_document()}
    response = connected.post("/api/v1/tasks", json=body)

    assert response.status_code == 201, response.text
    created = response.json()
    assert set(created) == {"workflow_id", "recipe_version_id"}

    listed = connected.get(f"/api/v1/tasks?tenant_id={ledger.tenant_id}")
    assert listed.status_code == 200, listed.text
    tasks = [task for task in listed.json() if task["workflow_id"] == created["workflow_id"]]
    assert len(tasks) == 1
    assert tasks[0]["name"] == "Add inquiries"
    assert tasks[0]["recipe_version_id"] == created["recipe_version_id"]
    # The dashboard's own derivation: the origin without its scheme, port included. The stored
    # destination_key is the bare host, asserted below on the workflow row itself.
    assert tasks[0]["app"] == "127.0.0.1:8123"

    row = conn.execute(  # type: ignore[attr-defined]
        "SELECT tenant_id, name, destination_key, allowed_origins, created_by "
        "FROM workflows WHERE id = %s",
        (uuid.UUID(created["workflow_id"]),),
    ).fetchone()
    assert row == (
        ledger.tenant_id,
        "Add inquiries",
        "127.0.0.1",
        [ORIGIN],
        uuid.UUID(operator),
    )
    mine = conn.execute(  # type: ignore[attr-defined]
        "SELECT count(*) FROM workflows WHERE created_by = %s", (uuid.UUID(operator),)
    ).fetchone()[0]
    assert mine == 1, "exactly one workflow was created for this task"

    version = conn.execute(  # type: ignore[attr-defined]
        "SELECT tenant_id, workflow_id, version, recipe, change_reason "
        "FROM recipe_versions WHERE id = %s",
        (uuid.UUID(created["recipe_version_id"]),),
    ).fetchone()
    assert version == (
        ledger.tenant_id,
        uuid.UUID(created["workflow_id"]),
        1,
        recipe_document(),
        "compiled from recording",
    )


def test_saving_under_an_existing_name_adds_the_next_version(
    connected: TestClient, conn: object, operator: str
) -> None:
    """Teaching a task again keeps its workflow and adds version 2; version 1 is untouched."""
    body = {"name": "Add inquiries", "recipe": recipe_document()}
    first = connected.post("/api/v1/tasks", json=body)
    second = connected.post("/api/v1/tasks", json=body)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["workflow_id"] == first.json()["workflow_id"]
    assert second.json()["recipe_version_id"] != first.json()["recipe_version_id"]
    versions = conn.execute(  # type: ignore[attr-defined]
        "SELECT version FROM recipe_versions WHERE workflow_id = %s ORDER BY version",
        (uuid.UUID(first.json()["workflow_id"]),),
    ).fetchall()
    assert [row[0] for row in versions] == [1, 2]


def test_a_recipe_without_a_commit_assertion_is_refused_like_recipes_refuses(
    connected: TestClient, conn: object, ledger: Ledger, operator: str
) -> None:
    """422 with the same problems list ``POST /recipes`` gives, and nothing written."""
    document = recipe_document(with_assertion=False)
    response = connected.post("/api/v1/tasks", json={"name": "Broken", "recipe": document})

    assert response.status_code == 422, response.text
    problems = response.json()["detail"]["problems"]
    assert any("assert_after" in problem for problem in problems)

    # The same document through the storage route the dashboard already uses: same refusal.
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, 'for the comparison', '127.0.0.1', %s) RETURNING id",
            (ledger.tenant_id, [ORIGIN]),
        )
        workflow_id = cur.fetchone()[0]
    via_recipes = connected.post(f"/api/v1/recipes?workflow_id={workflow_id}", json=document)
    assert via_recipes.status_code == 422, via_recipes.text
    assert via_recipes.json()["detail"]["problems"] == problems

    written = conn.execute(  # type: ignore[attr-defined]
        "SELECT count(*) FROM workflows WHERE created_by = %s", (uuid.UUID(operator),)
    ).fetchone()[0]
    assert written == 0, "a refused recipe writes nothing"


def test_saving_a_task_with_no_operator_configured_is_a_503(
    connected: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """There is nobody to own the task, so the API refuses rather than inventing an owner."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    response = connected.post(
        "/api/v1/tasks", json={"name": "Add inquiries", "recipe": recipe_document()}
    )

    assert response.status_code == 503, response.text
    assert "PROCESSLENS_OPERATOR_ID" in response.json()["detail"]


def test_the_popups_origin_may_save_a_task(
    connected: TestClient, ledger: Ledger, operator: str, monkeypatch: MonkeyPatch
) -> None:
    """The extension's own origin passes the front door on this route, as on compile."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)
    response = connected.post(
        "/api/v1/tasks",
        json={"name": "Add inquiries", "recipe": recipe_document()},
        headers={"Origin": f"chrome-extension://{EXTENSION_ID}"},
    )

    assert response.status_code == 201, response.text


def test_another_extensions_origin_is_refused(
    connected: TestClient, ledger: Ledger, operator: str, monkeypatch: MonkeyPatch
) -> None:
    """Only the pinned id is the popup: every other origin still fails the front door."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)
    response = connected.post(
        "/api/v1/tasks",
        json={"name": "Add inquiries", "recipe": recipe_document()},
        headers={"Origin": f"chrome-extension://{'b' * 32}"},
    )

    assert response.status_code == 403, response.text
