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

from packages.ledger import claim_run
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


def test_try_again_keeps_the_row_original_reason(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """PL-107 item 15: pressing Try again must not erase why the row stopped."""
    run = ledger.new_run(status="completed")
    record_id = ledger.add_record(run, state="parked")
    original = "Couldn't click Continue: something was in front of it"
    conn.execute("UPDATE run_records SET reason = %s WHERE id = %s", (original, record_id))

    response = connected.post(f"/api/v1/records/{record_id}/requeue", json={})

    assert response.status_code == 200, response.text
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE id = %s", (record_id,))
        reason = cur.fetchone()[0]
    assert reason is not None
    assert original in reason
    assert "Trying again" in reason


def test_try_again_twice_does_not_nest_the_reason_prefix(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """PL-107 review: a row already carrying the requeue note must not gain it a second time."""
    run = ledger.new_run(status="completed")
    record_id = ledger.add_record(run, state="parked")
    once = "Trying again — last time: Couldn't click Continue: something was in front of it"
    conn.execute("UPDATE run_records SET reason = %s WHERE id = %s", (once, record_id))

    response = connected.post(f"/api/v1/records/{record_id}/requeue", json={})

    assert response.status_code == 200, response.text
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE id = %s", (record_id,))
        reason = cur.fetchone()[0]
    assert reason is not None
    assert reason.count("Trying again") == 1, reason
    assert reason == once


def test_try_again_never_lowers_the_lease_epoch(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    """PL-107 review: lease_epoch is the fence and may only go up (B1)."""
    run = ledger.new_run(status="completed")
    record_id = ledger.add_record(run, state="parked")
    # A run that has already been taken over more than once.
    conn.execute("UPDATE runs SET lease_epoch = 7 WHERE id = %s", (run.id,))

    response = connected.post(f"/api/v1/records/{record_id}/requeue", json={})

    assert response.status_code == 200, response.text
    with conn.cursor() as cur:
        cur.execute("SELECT lease_epoch FROM runs WHERE id = %s", (run.id,))
        after_reopen = cur.fetchone()[0]
    # The reopen leaves the fence where it was; it does not reset it.
    assert after_reopen == 7

    # This session's database is shared by the file, and `claim_run` picks the oldest queued run in
    # the whole database. Park any run an earlier test left queued, so the claim is unambiguously
    # this one.
    conn.execute(
        "UPDATE runs SET status = 'completed' WHERE status = 'queued' AND id <> %s", (run.id,)
    )
    claimed = claim_run(conn, "worker-after-reopen")
    assert claimed is not None
    assert claimed.id == run.id
    assert claimed.lease_epoch > after_reopen
