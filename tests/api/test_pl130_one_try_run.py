"""PL-130: Try it twice for the same task and file gives back the try that is still open.

The owner pressed Try it on "huh" twice, 16 seconds apart, and two one-row runs were queued behind
each other. The panel had lost the first run (its screen was opened again), so the button showed
again. The server is where both clicks meet: while a try of the same task, version and file is
still open, a second one returns it instead of queuing another.
"""

from __future__ import annotations

import psycopg
from fastapi.testclient import TestClient

from tests.api.test_batches_api import add_task
from tests.db.seed import Ledger


def _try_body(connected: TestClient, conn: psycopg.Connection, ledger: Ledger) -> dict:
    workflow_id, recipe_version_id = add_task(conn, ledger.tenant_id, name="Change prices")
    uploaded = connected.post(
        "/api/v1/batches",
        data={"workflow_id": str(workflow_id)},
        files={"file": ("prices.csv", b"email\na@example.com\nb@example.com\n", "text/csv")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return {
        "workflow_id": str(workflow_id),
        "recipe_version_id": str(recipe_version_id),
        "input_batch_id": uploaded.json()["batch_id"],
        "mode": "preview",
        "limit": 1,
    }


def test_a_second_try_returns_the_open_one(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    body = _try_body(connected, conn, ledger)

    first = connected.post("/api/v1/runs", json=body)
    second = connected.post("/api/v1/runs", json=body)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["run_id"] == first.json()["run_id"]


def test_a_try_after_the_last_one_finished_is_new(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    body = _try_body(connected, conn, ledger)
    first = connected.post("/api/v1/runs", json=body).json()["run_id"]
    conn.execute(
        "UPDATE runs SET status = 'cancelled', finished_at = now() WHERE id = %s", (first,)
    )

    second = connected.post("/api/v1/runs", json=body)

    assert second.status_code == 201, second.text
    assert second.json()["run_id"] != first


def test_a_batch_run_is_never_folded_into_another(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    body = {**_try_body(connected, conn, ledger), "mode": "batch"}
    body.pop("limit")

    first = connected.post("/api/v1/runs", json=body).json()["run_id"]
    second = connected.post("/api/v1/runs", json=body).json()["run_id"]

    assert second != first
