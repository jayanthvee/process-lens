"""The Spreadsheets read: the uploaded batches and how a run used them.

The style is ``tests/api/test_apps_api.py``'s: the ``ledger`` fixture builds a fresh tenant, so
every assertion here is scoped to this test's own data in the shared session database (the route
takes the ``tenant_id`` filter for exactly that reason).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import psycopg
from fastapi.testclient import TestClient

from tests.db.seed import Ledger


def spreadsheets_body(connected: TestClient, ledger: Ledger) -> list[dict]:
    """`GET /api/v1/spreadsheets`, scoped to this test's tenant."""
    response = connected.get(f"/api/v1/spreadsheets?tenant_id={ledger.tenant_id}")
    assert response.status_code == 200, response.text
    return response.json()


def add_batch(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    kind: str = "csv",
    row_count: int = 20,
    file_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    age_hours: int = 0,
) -> uuid.UUID:
    """One uploaded batch, created ``age_hours`` before now."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO input_batches "
            "(tenant_id, workflow_id, file_id, kind, row_count, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, now() - make_interval(hours => %s)) "
            "RETURNING id",
            (tenant_id, workflow_id, file_id, kind, row_count, created_by, age_hours),
        )
        return cur.fetchone()[0]


def add_file(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    original_name: str,
    uploaded_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """One stored file, named as the person uploaded it."""
    key = uuid.uuid4().hex
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO files "
            "(tenant_id, storage_key, original_name, mime_type, size_bytes, sha256, uploaded_by) "
            "VALUES (%s, %s, %s, 'text/csv', 10, %s, %s) RETURNING id",
            (tenant_id, key, original_name, key, uploaded_by),
        )
        return cur.fetchone()[0]


def add_run(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    recipe_version_id: uuid.UUID,
    batch_id: uuid.UUID,
    status: str = "completed",
    age_hours: int = 0,
) -> uuid.UUID:
    """One run of a batch, created ``age_hours`` before now."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO runs "
            "(tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status, "
            " destination_key, created_at) "
            "VALUES (%s, %s, %s, %s, 'batch', %s, 'demo-crm', now() - make_interval(hours => %s)) "
            "RETURNING id",
            (tenant_id, workflow_id, recipe_version_id, batch_id, status, age_hours),
        )
        return cur.fetchone()[0]


def add_workflow(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID, name: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """One workflow and its single recipe version, for another tenant's batch."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, %s, 'demo-crm', '{http://localhost:5173}') RETURNING id",
            (tenant_id, name),
        )
        workflow_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO recipe_versions "
            "(tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, '{\"steps\": []}', 'compiled from recording') RETURNING id",
            (tenant_id, workflow_id),
        )
        return workflow_id, cur.fetchone()[0]


def test_the_newest_batch_is_first(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The page lists the newest upload first, and the order is the API's own."""
    oldest = add_batch(
        conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id, age_hours=5
    )
    newest = add_batch(
        conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id, age_hours=1
    )

    body = spreadsheets_body(connected, ledger)

    assert [row["id"] for row in body] == [str(newest), str(oldest)]


def test_another_tenants_batches_never_appear(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The read is scoped to one tenant: another tenant's batch is not this page's row."""
    other_tenant = uuid.uuid4()
    conn.execute("INSERT INTO tenants (id, name) VALUES (%s, 'Other tenant')", (other_tenant,))
    other_workflow, _ = add_workflow(conn, tenant_id=other_tenant, name="Their task")
    theirs = add_batch(conn, tenant_id=other_tenant, workflow_id=other_workflow)
    mine = add_batch(conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id)

    body = spreadsheets_body(connected, ledger)

    ids = {row["id"] for row in body}
    assert str(theirs) not in ids
    assert str(mine) in ids


def test_a_batch_with_no_file_is_pasted_rows(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A pasted batch has no file to name, so it says exactly that."""
    pasted = add_batch(
        conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id, kind="manual"
    )

    body = spreadsheets_body(connected, ledger)

    row = next(row for row in body if row["id"] == str(pasted))
    assert row["file_name"] == "Pasted rows"


def test_the_file_name_is_the_original_name(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """An uploaded file is shown by the name the person gave it."""
    file_id = add_file(conn, tenant_id=ledger.tenant_id, original_name="spring leads.csv")
    batch = add_batch(
        conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id, file_id=file_id
    )

    body = spreadsheets_body(connected, ledger)

    row = next(row for row in body if row["id"] == str(batch))
    assert row["file_name"] == "spring leads.csv"


def test_the_run_count_and_the_newest_run_are_right(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Every run of the batch is counted, and the newest of them is the one linked."""
    batch = add_batch(conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id)
    add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=ledger.workflow_id,
        recipe_version_id=ledger.recipe_version_id,
        batch_id=batch,
        age_hours=5,
    )
    newest = add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=ledger.workflow_id,
        recipe_version_id=ledger.recipe_version_id,
        batch_id=batch,
        status="running",
        age_hours=1,
    )
    add_run(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=ledger.workflow_id,
        recipe_version_id=ledger.recipe_version_id,
        batch_id=batch,
        status="failed",
        age_hours=3,
    )

    body = spreadsheets_body(connected, ledger)

    row = next(row for row in body if row["id"] == str(batch))
    assert row["runs"] == 3
    assert row["last_run_id"] == str(newest)


def test_a_batch_with_no_runs_is_not_used_yet(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A batch nothing has run on has no runs and no newest run, not a made-up answer."""
    batch = add_batch(conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id)

    body = spreadsheets_body(connected, ledger)

    row = next(row for row in body if row["id"] == str(batch))
    assert row["runs"] == 0
    assert row["last_run_id"] is None


def test_the_uploader_and_the_upload_time_are_the_batch_s_own(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Who uploaded the batch and when come from the batch row itself."""
    person = ledger.add_user(name="Jay")
    batch = add_batch(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=ledger.workflow_id,
        created_by=person,
        age_hours=2,
    )

    body = spreadsheets_body(connected, ledger)

    row = next(row for row in body if row["id"] == str(batch))
    assert row["uploaded_by"] == "Jay"
    stored = conn.execute("SELECT created_at FROM input_batches WHERE id = %s", (batch,)).fetchone()
    assert datetime.fromisoformat(row["uploaded_at"]) == stored[0]


def test_documents_are_not_spreadsheets(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Only csv, xlsx and manual batches are spreadsheets; a documents batch is not one."""
    documents = add_batch(
        conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id, kind="documents"
    )
    manual = add_batch(
        conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id, kind="manual"
    )
    xlsx = add_batch(conn, tenant_id=ledger.tenant_id, workflow_id=ledger.workflow_id, kind="xlsx")

    body = spreadsheets_body(connected, ledger)

    ids = {row["id"] for row in body}
    assert str(documents) not in ids
    assert {str(manual), str(xlsx)} <= ids


def test_the_response_keys_are_exactly_the_frozen_shape(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """No row contents, no extra fields: a spreadsheet carries exactly what was frozen."""
    batch = add_batch(
        conn,
        tenant_id=ledger.tenant_id,
        workflow_id=ledger.workflow_id,
        row_count=20,
    )

    body = spreadsheets_body(connected, ledger)

    row = next(row for row in body if row["id"] == str(batch))
    assert set(row) == {
        "id",
        "file_name",
        "task_id",
        "task_name",
        "row_count",
        "uploaded_at",
        "uploaded_by",
        "runs",
        "last_run_id",
    }
    assert row["task_id"] == str(ledger.workflow_id)
    assert row["task_name"] == "Inquiries"
    assert row["row_count"] == 20
    assert row["uploaded_by"] is None
