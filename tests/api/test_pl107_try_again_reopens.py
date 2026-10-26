"""PL-107: Try again on a row of a finished run puts the run back to work.

On the owner's first real-site test both runs ended ``completed`` with their one row parked. Try
again moved the row to ``pending`` and the page said "Waiting" forever: no worker claims a row of a
completed run, because the supervisor only starts runs that are ``queued`` (or ``running`` with a
lapsed lease). The requeue must reopen the run in the same transaction.
"""

from __future__ import annotations

import uuid

import psycopg
from fastapi.testclient import TestClient

from tests.db.seed import Ledger


def _run_status(conn: psycopg.Connection, run_id: uuid.UUID) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
        return cur.fetchone()[0]


def test_try_again_on_a_finished_run_queues_the_run_again(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run(status="completed")
    record_id = ledger.add_record(run, state="parked")

    response = connected.post(f"/api/v1/records/{record_id}/requeue", json={})

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pending"
    assert _run_status(conn, run.id) == "queued"


def test_try_again_on_a_running_run_leaves_it_running(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run(status="running")
    record_id = ledger.add_record(run, state="parked")

    response = connected.post(f"/api/v1/records/{record_id}/requeue", json={})

    assert response.status_code == 200, response.text
    assert _run_status(conn, run.id) == "running"


def test_try_again_on_a_stopped_run_says_so(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """A run the person stopped stays stopped; the answer tells them to start a new one."""
    run = ledger.new_run(status="cancelled")
    record_id = ledger.add_record(run, state="parked")

    response = connected.post(f"/api/v1/records/{record_id}/requeue", json={})

    assert response.status_code == 409, response.text
    assert "stopped" in response.text
    assert _run_status(conn, run.id) == "cancelled"
