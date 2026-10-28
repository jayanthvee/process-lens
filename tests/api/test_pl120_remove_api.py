"""PL-120: removing a task from the list, and putting it back.

A removed task is hidden from the task lists (the web page, the panel and Home), but everything it
made stays: its recipe versions, runs, records and committed keys. Restore is the same switch back.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.db.seed import Ledger


def task_list_ids(client: TestClient, tenant_id: uuid.UUID) -> set[str]:
    """The task ids ``GET /api/v1/tasks`` answers for this tenant."""
    response = client.get(f"/api/v1/tasks?tenant_id={tenant_id}")
    assert response.status_code == 200, response.text
    return {row["workflow_id"] for row in response.json()}


def home_task_ids(client: TestClient, tenant_id: uuid.UUID) -> set[str]:
    """The task ids Home's five-row glance answers for this tenant."""
    response = client.get(f"/api/v1/home?tenant_id={tenant_id}")
    assert response.status_code == 200, response.text
    return {row["workflow_id"] for row in response.json()["tasks"]}


def removed_at(conn: psycopg.Connection, workflow_id: uuid.UUID) -> object:
    """The workflow's removal stamp, or ``None`` while it is listed."""
    return conn.execute(
        "SELECT removed_at FROM workflows WHERE id = %s", (workflow_id,)
    ).fetchone()[0]


def test_remove_hides_the_task_from_the_lists_and_keeps_everything_it_made(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """The task leaves Tasks and Home; its run, batch and recipe stay, and the run still lists."""
    run = ledger.new_run(status="completed")
    workflow_id = ledger.workflow_id
    assert str(workflow_id) in task_list_ids(connected, ledger.tenant_id)
    assert str(workflow_id) in home_task_ids(connected, ledger.tenant_id)

    response = connected.post(f"/api/v1/tasks/{workflow_id}/remove")

    assert response.status_code == 200, response.text
    assert removed_at(conn, workflow_id) is not None
    assert str(workflow_id) not in task_list_ids(connected, ledger.tenant_id)
    assert str(workflow_id) not in home_task_ids(connected, ledger.tenant_id)

    # Nothing else was deleted or changed.
    assert conn.execute("SELECT name FROM workflows WHERE id = %s", (workflow_id,)).fetchone() == (
        "Inquiries",
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM recipe_versions WHERE workflow_id = %s", (workflow_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM input_batches WHERE workflow_id = %s", (workflow_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute("SELECT count(*) FROM runs WHERE workflow_id = %s", (workflow_id,)).fetchone()[
            0
        ]
        == 1
    )

    # The run list still names the run after the task behind it. The recipe has no meta name, so
    # the workflow's own name is the fallback, exactly as it is for a listed task.
    listed = connected.get("/api/v1/runs")
    assert listed.status_code == 200, listed.text
    run_row = next(row for row in listed.json() if row["id"] == str(run.id))
    assert run_row["recipe_name"] == "Inquiries"

    detail = connected.get(f"/api/v1/runs/{run.id}")
    assert detail.status_code == 200, detail.text


def test_restore_puts_the_task_back(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Restore clears the stamp, and the task is listed again."""
    workflow_id = ledger.workflow_id
    assert connected.post(f"/api/v1/tasks/{workflow_id}/remove").status_code == 200
    assert str(workflow_id) not in task_list_ids(connected, ledger.tenant_id)

    response = connected.post(f"/api/v1/tasks/{workflow_id}/restore")

    assert response.status_code == 200, response.text
    assert removed_at(conn, workflow_id) is None
    assert str(workflow_id) in task_list_ids(connected, ledger.tenant_id)
    assert str(workflow_id) in home_task_ids(connected, ledger.tenant_id)

    # Restoring a task that is not removed is a no-op, not an error.
    again = connected.post(f"/api/v1/tasks/{workflow_id}/restore")
    assert again.status_code == 200, again.text
    assert removed_at(conn, workflow_id) is None


def test_a_second_remove_is_a_no_op(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """Removing twice answers 200 both times and leaves the first stamp alone."""
    workflow_id = ledger.workflow_id
    first = connected.post(f"/api/v1/tasks/{workflow_id}/remove")
    assert first.status_code == 200, first.text
    stamp = removed_at(conn, workflow_id)
    assert stamp is not None

    second = connected.post(f"/api/v1/tasks/{workflow_id}/remove")

    assert second.status_code == 200, second.text
    assert removed_at(conn, workflow_id) == stamp
    assert str(workflow_id) not in task_list_ids(connected, ledger.tenant_id)


@pytest.mark.parametrize("status", ["queued", "running", "paused"])
def test_a_task_with_an_open_run_is_refused(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, status: str
) -> None:
    """409 with the sentence a person needs, and the task stays listed."""
    ledger.new_run(status=status)

    response = connected.post(f"/api/v1/tasks/{ledger.workflow_id}/remove")

    assert response.status_code == 409, response.text
    assert "Stop its run first." in response.json()["detail"]
    assert removed_at(conn, ledger.workflow_id) is None
    assert str(ledger.workflow_id) in task_list_ids(connected, ledger.tenant_id)


@pytest.mark.parametrize("action", ["remove", "restore"])
def test_an_unknown_task_is_a_404(connected: TestClient, action: str) -> None:
    """An id that names no task is refused by the route itself, not by a missing path."""
    unknown = uuid.uuid4()

    response = connected.post(f"/api/v1/tasks/{unknown}/{action}")

    assert response.status_code == 404, response.text
    # The route's own sentence, so this cannot pass merely because no route matched.
    assert response.json()["detail"] == f"task {unknown} does not exist"
