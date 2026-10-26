"""PL-105: ``GET /api/v1/tasks`` names a task's spreadsheet columns, and a task with none can be
run once without one.

The style is ``tests/api/test_batches_api.py``'s: the ``ledger`` fixture builds a fresh tenant, so
every assertion is scoped to this test's own data in the shared session database.
"""

from __future__ import annotations

import uuid

import psycopg
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from tests.api.test_batches_api import add_task, recipe_document
from tests.db.seed import Ledger

EXTENSION_ID = "a" * 32
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"


def no_input_document() -> dict:
    """The recipe the panel compiles for a recording with no typed values."""
    return recipe_document(
        fields={"run_once_id": {"type": "string", "example": "x"}},
        record_key="{{row.run_once_id}}",
    )


def test_tasks_list_the_columns_a_spreadsheet_must_supply(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A task with columns lists them; a task taught with none lists nothing."""
    once_id, _ = add_task(
        conn, ledger.tenant_id, document=no_input_document(), name="Close the day"
    )
    sheet_id, _ = add_task(conn, ledger.tenant_id, name="Add inquiries")

    response = connected.get(f"/api/v1/tasks?tenant_id={ledger.tenant_id}")

    assert response.status_code == 200, response.text
    by_id = {row["workflow_id"]: row for row in response.json()}
    assert by_id[str(once_id)]["inputs"] == []
    assert by_id[str(sheet_id)]["inputs"] == ["email"]


def test_run_once_refuses_a_task_that_needs_a_spreadsheet(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """422 in plain words, and nothing is written."""
    workflow_id, _ = add_task(conn, ledger.tenant_id, name="Add inquiries")

    response = connected.post(f"/api/v1/tasks/{workflow_id}/run-once", json={})

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "This task needs a spreadsheet."
    written = conn.execute(
        "SELECT count(*) FROM input_batches WHERE workflow_id = %s", (workflow_id,)
    ).fetchone()[0]
    assert written == 0


def test_two_run_onces_make_two_runs_with_different_keys(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Each Run once is new work: one row, one run, and a key of its own every time."""
    workflow_id, recipe_version_id = add_task(
        conn, ledger.tenant_id, document=no_input_document(), name="Close the day"
    )

    first = connected.post(f"/api/v1/tasks/{workflow_id}/run-once", json={})
    second = connected.post(f"/api/v1/tasks/{workflow_id}/run-once", json={})

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["status"] == "queued"
    assert first.json()["run_id"] != second.json()["run_id"]

    keys: set[str] = set()
    for answer in (first.json(), second.json()):
        run_id = uuid.UUID(answer["run_id"])
        run = conn.execute(
            "SELECT workflow_id, recipe_version_id, input_batch_id, mode FROM runs WHERE id = %s",
            (run_id,),
        ).fetchone()
        assert run[:4] == (workflow_id, recipe_version_id, run[2], "batch")
        records = connected.get(f"/api/v1/runs/{run_id}/records")
        assert records.status_code == 200, records.text
        rows = records.json()
        assert len(rows) == 1
        assert rows[0]["state"] == "pending"
        keys.add(rows[0]["record_key"])
        batch_rows = conn.execute(
            "SELECT count(*), min(raw ->> 'run_once_id') FROM input_records WHERE batch_id = %s",
            (run[2],),
        ).fetchone()
        assert batch_rows[0] == 1
        assert batch_rows[1] == rows[0]["record_key"]

    assert len(keys) == 2, "two Run onces must not share a key"


def test_the_side_panel_may_start_a_run_from_the_pinned_origin(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, monkeypatch: MonkeyPatch
) -> None:
    """A panel ``POST /api/v1/runs`` passes the front door; another extension id does not."""
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", EXTENSION_ID)
    workflow_id, recipe_version_id = add_task(conn, ledger.tenant_id, name="Add inquiries")
    uploaded = connected.post(
        "/api/v1/batches",
        data={"workflow_id": str(workflow_id)},
        files={"file": ("inquiries.csv", b"email\na@example.com\n", "text/csv")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = {
        "workflow_id": str(workflow_id),
        "recipe_version_id": str(recipe_version_id),
        "input_batch_id": uploaded.json()["batch_id"],
        "mode": "batch",
    }

    accepted = connected.post("/api/v1/runs", json=body, headers={"origin": EXTENSION_ORIGIN})
    refused = connected.post(
        "/api/v1/runs",
        json=body,
        headers={"origin": f"chrome-extension://{'b' * 32}"},
    )

    assert accepted.status_code == 201, accepted.text
    assert refused.status_code == 403, refused.text
    assert "origin" in refused.json()["detail"]


def test_run_once_of_a_missing_task_is_404(connected: TestClient) -> None:
    response = connected.post(f"/api/v1/tasks/{uuid.uuid4()}/run-once", json={})

    assert response.status_code == 404, response.text
