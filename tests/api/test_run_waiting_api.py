"""PL-041 part 1: a queued run says what it is waiting for.

The database allows one running-or-paused run per tenant and destination
(``runs_one_active_per_destination``), so a queued run can be stuck behind another task's run on
the same app. ``GET /api/v1/runs/{id}`` names that run in ``waiting_behind``, and names nothing
when there is none to name.

The style is ``tests/api/test_runs_reads.py``'s: the ``ledger`` fixture builds a fresh tenant, so
every assertion is scoped to this test's own data in the shared session database.
"""

from __future__ import annotations

import json
import uuid

import psycopg
from fastapi.testclient import TestClient

from tests.db.seed import DESTINATION_KEY, Ledger


def add_task(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    name: str,
    destination_key: str = DESTINATION_KEY,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A saved task: one workflow and its one recipe version, named by the recipe's own meta."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (tenant_id, name, destination_key, ["http://crm.example.com"]),
        )
        workflow_id = cur.fetchone()[0]
        document = {"meta": {"name": name}, "steps": []}
        cur.execute(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'compiled from recording') RETURNING id",
            (tenant_id, workflow_id, json.dumps(document)),
        )
        return workflow_id, cur.fetchone()[0]


def add_run(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    recipe_version_id: uuid.UUID,
    status: str,
    destination_key: str = DESTINATION_KEY,
) -> uuid.UUID:
    """A run in the state a test needs it in, with no records: this test is about the wait only."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO runs "
            "(tenant_id, workflow_id, recipe_version_id, mode, status, destination_key) "
            "VALUES (%s, %s, %s, 'batch', %s, %s) RETURNING id",
            (tenant_id, workflow_id, recipe_version_id, status, destination_key),
        )
        return cur.fetchone()[0]


def waiting_of(connected: TestClient, run_id: uuid.UUID) -> dict | None:
    """``waiting_behind`` of one run, from the run page's own read."""
    response = connected.get(f"/api/v1/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()["waiting_behind"]


def test_a_queued_run_behind_a_paused_run_of_another_task_names_it(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The paused run ahead is named, with its task's own name and its state."""
    queued, _ = ledger.seed_records(1, state="pending")
    workflow_id, version_id = add_task(conn, tenant_id=ledger.tenant_id, name="Send invoices")
    paused_id = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=workflow_id,
        recipe_version_id=version_id,
        status="paused",
    )

    assert waiting_of(connected, queued.id) == {
        "run_id": str(paused_id),
        "task_name": "Send invoices",
        "status": "paused",
    }


def test_a_queued_run_behind_a_running_run_names_that_one_too(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A running run blocks the app the same way, so it is named the same way."""
    queued, _ = ledger.seed_records(1, state="pending")
    workflow_id, version_id = add_task(conn, tenant_id=ledger.tenant_id, name="Send invoices")
    running_id = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=workflow_id,
        recipe_version_id=version_id,
        status="running",
    )

    assert waiting_of(connected, queued.id) == {
        "run_id": str(running_id),
        "task_name": "Send invoices",
        "status": "running",
    }


def test_a_queued_run_with_nothing_ahead_waits_for_nothing(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A finished run on the same app has let go of it, so there is nothing to wait behind."""
    queued, _ = ledger.seed_records(1, state="pending")
    workflow_id, version_id = add_task(conn, tenant_id=ledger.tenant_id, name="Send invoices")
    add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=workflow_id,
        recipe_version_id=version_id,
        status="completed",
    )

    assert waiting_of(connected, queued.id) is None


def test_a_run_that_is_not_queued_has_nothing_to_wait_behind(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Only a queued run waits. A finished one on the same app is not blocked by what follows."""
    finished, _ = ledger.seed_records(1, state="verified")
    conn.execute("UPDATE runs SET status = 'completed' WHERE id = %s", (finished.id,))
    workflow_id, version_id = add_task(conn, tenant_id=ledger.tenant_id, name="Send invoices")
    add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=workflow_id,
        recipe_version_id=version_id,
        status="paused",
    )

    assert waiting_of(connected, finished.id) is None


def test_another_tenants_run_on_the_same_app_never_shows(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The wait is scoped by the tenant of the run itself, never by a client-supplied id."""
    queued, _ = ledger.seed_records(1, state="pending")
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (name) VALUES ('Another tenant') RETURNING id")
        other_tenant = cur.fetchone()[0]
    workflow_id, version_id = add_task(conn, tenant_id=other_tenant, name="Someone else's task")
    add_run(
        conn,
        tenant_id=other_tenant,
        workflow_id=workflow_id,
        recipe_version_id=version_id,
        status="paused",
    )

    assert waiting_of(connected, queued.id) is None
