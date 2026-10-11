"""The recipe storage and ingestion endpoints.

These run against the real schema and the real database, like the rest of the
database suite: a recipient tenant and workflow are created, then recipes are
ingested, listed, and fetched through the API.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from services.api.main import app

client = TestClient(app)

ORIGIN = "http://localhost:5173"

VALID_RECIPE: dict[str, Any] = {
    "format_version": 1,
    "meta": {
        "name": "Create a lead from an inquiry sheet",
        "when_to_use": "Each inquiry row must exist as one lead.",
        "input_schema": {"fields": {"email": {"type": "string", "required": True}}},
        "record_key": "{{row.email | lower}}",
        "idempotency_key": "{{row.email | lower}}",
        "approval_policy": {"commit": "first_record"},
        "allowed_origins": [ORIGIN],
    },
    "steps": [
        {"id": "open-1", "action": "navigate", "url": f"{ORIGIN}/leads/new"},
        {
            "id": "fill-1",
            "action": "fill",
            "targets": [{"by": "label", "text": "Email"}],
            "value": {"kind": "input", "column": "email"},
        },
        {
            "id": "save-1",
            "action": "click",
            "commit": True,
            "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
            "targets": [{"by": "role", "role": "button", "name": "Save"}],
        },
    ],
}


@pytest.fixture
def connected(monkeypatch: MonkeyPatch, database_url: str) -> TestClient:
    """A client whose requests reach the migrated test database."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    return client


@pytest.fixture
def workflow(conn: psycopg.Connection) -> uuid.UUID:
    """A synthetic tenant and workflow the recipes are ingested under."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (name) VALUES ('Synthetic tenant') RETURNING id")
        tenant_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, 'Inquiries', 'demo-crm', %s) RETURNING id",
            (tenant_id, [ORIGIN]),
        )
        return cur.fetchone()[0]


def count_versions(conn: psycopg.Connection, workflow_id: uuid.UUID) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM recipe_versions WHERE workflow_id = %s", (workflow_id,)
        )
        return int(cur.fetchone()[0])


# --- ingest ------------------------------------------------------------------


def test_ingest_stores_the_recipe_and_returns_its_metadata(
    connected: TestClient, conn: psycopg.Connection, workflow: uuid.UUID
) -> None:
    response = connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=VALID_RECIPE)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["workflow_id"] == str(workflow)
    assert body["version"] == 1
    assert body["format_version"] == 1
    assert body["name"] == "Create a lead from an inquiry sheet"
    assert body["record_key"] == "{{row.email | lower}}"
    assert body["idempotency_key"] == "{{row.email | lower}}"
    assert body["recipe"] == VALID_RECIPE
    assert count_versions(conn, workflow) == 1


def test_ingest_twice_creates_two_immutable_versions(
    connected: TestClient, conn: psycopg.Connection, workflow: uuid.UUID
) -> None:
    first = connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=VALID_RECIPE)
    second = connected.post(
        f"/api/v1/recipes?workflow_id={workflow}&change_reason=approved+repair",
        json=VALID_RECIPE,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2
    assert second.json()["change_reason"] == "approved repair"
    assert count_versions(conn, workflow) == 2


def test_ingest_rejects_an_unknown_workflow(
    connected: TestClient, conn: psycopg.Connection
) -> None:
    missing = uuid.uuid4()
    response = connected.post(f"/api/v1/recipes?workflow_id={missing}", json=VALID_RECIPE)

    assert response.status_code == 404
    assert count_versions(conn, missing) == 0


def test_ingest_rejects_an_archived_workflow(
    connected: TestClient, conn: psycopg.Connection, workflow: uuid.UUID
) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE workflows SET archived_at = now() WHERE id = %s", (workflow,))

    response = connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=VALID_RECIPE)

    assert response.status_code == 404
    assert count_versions(conn, workflow) == 0


# --- fail-closed validation --------------------------------------------------


def test_ingest_rejects_a_schema_violation(
    connected: TestClient, conn: psycopg.Connection, workflow: uuid.UUID
) -> None:
    broken = copy.deepcopy(VALID_RECIPE)
    broken["steps"][0].pop("url")  # a navigate step without a url

    response = connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=broken)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["problems"], "the response must list what is wrong"
    # The schema reports the offending location; the oneOf shape makes the leaf
    # message generic, so the path is what identifies the step.
    assert any(problem.startswith("steps/0") for problem in detail["problems"])
    assert count_versions(conn, workflow) == 0


def test_ingest_rejects_a_commit_step_without_an_assertion(
    connected: TestClient, conn: psycopg.Connection, workflow: uuid.UUID
) -> None:
    broken = copy.deepcopy(VALID_RECIPE)
    broken["steps"][2].pop("assert_after")

    response = connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=broken)

    assert response.status_code == 422
    problems = response.json()["detail"]["problems"]
    assert any("assert_after" in problem for problem in problems)
    assert count_versions(conn, workflow) == 0


def test_ingest_finds_an_unasserted_commit_inside_a_branch(
    connected: TestClient, conn: psycopg.Connection, workflow: uuid.UUID
) -> None:
    nested = copy.deepcopy(VALID_RECIPE)
    nested["steps"].append(
        {
            "id": "branch-1",
            "action": "branch",
            "when": {"text_visible": "Existing"},
            "then": [
                {
                    "id": "nested-save",
                    "action": "click",
                    "commit": True,
                    "targets": [{"by": "role", "role": "button", "name": "Save"}],
                }
            ],
        }
    )

    response = connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=nested)

    assert response.status_code == 422
    problems = response.json()["detail"]["problems"]
    assert any("nested-save" in problem and "assert_after" in problem for problem in problems)


def test_ingest_rejects_a_document_missing_meta(
    connected: TestClient, conn: psycopg.Connection, workflow: uuid.UUID
) -> None:
    response = connected.post(
        f"/api/v1/recipes?workflow_id={workflow}", json={"format_version": 1, "steps": []}
    )

    assert response.status_code == 422
    assert count_versions(conn, workflow) == 0


# --- retrieval ---------------------------------------------------------------


def test_list_returns_the_latest_version_of_each_active_workflow(
    connected: TestClient, conn: psycopg.Connection, workflow: uuid.UUID
) -> None:
    connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=VALID_RECIPE)
    connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=VALID_RECIPE)

    response = connected.get(f"/api/v1/recipes?workflow_id={workflow}")

    assert response.status_code == 200
    listed = response.json()
    assert len(listed) == 1
    assert listed[0]["workflow_id"] == str(workflow)
    assert listed[0]["version"] == 2
    assert "recipe" not in listed[0]  # the list is a summary


def test_list_excludes_archived_workflows(
    connected: TestClient, conn: psycopg.Connection, workflow: uuid.UUID
) -> None:
    connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=VALID_RECIPE)
    with conn.cursor() as cur:
        cur.execute("UPDATE workflows SET archived_at = now() WHERE id = %s", (workflow,))

    response = connected.get(f"/api/v1/recipes?workflow_id={workflow}")

    assert response.status_code == 200
    assert response.json() == []


def test_get_returns_the_recipe_with_its_version_pinned(
    connected: TestClient, workflow: uuid.UUID
) -> None:
    created = connected.post(f"/api/v1/recipes?workflow_id={workflow}", json=VALID_RECIPE)
    recipe_id = created.json()["id"]

    response = connected.get(f"/api/v1/recipes/{recipe_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == recipe_id
    assert body["version"] == 1
    assert body["recipe"]["meta"]["name"] == "Create a lead from an inquiry sheet"


def test_get_a_missing_recipe_is_404(connected: TestClient) -> None:
    response = connected.get(f"/api/v1/recipes/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_a_malformed_recipe_id_is_422(connected: TestClient) -> None:
    response = connected.get("/api/v1/recipes/not-a-uuid")
    assert response.status_code == 422


# --- dependency conditions ---------------------------------------------------


def test_ingest_reports_a_missing_database(monkeypatch: MonkeyPatch) -> None:
    from services.api import db

    monkeypatch.setattr(db, "database_url", lambda: "")
    response = client.post(f"/api/v1/recipes?workflow_id={uuid.uuid4()}", json=VALID_RECIPE)
    assert response.status_code == 503


def test_list_reports_an_unreachable_database(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://processlens@127.0.0.1:1/processlens")
    response = client.get("/api/v1/recipes")
    assert response.status_code == 503
