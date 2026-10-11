"""Break-it cases against migration 0002's ledger guards.

Each test asserts the safe behaviour. A failing test is a confirmed way around a guard.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.db.seed import Ledger

COMMIT_TO_FAILED = [
    "claimed",
    "prechecked",
    "filling",
    "submitting",
    "submitted_unverified",
    "failed",
]
BACK_TO_COMMIT = [
    "claimed",
    "prechecked",
    "filling",
    "submitting",
    "submitted_unverified",
    "failed",
]


def _user(conn: psycopg.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    return conn.execute(
        "INSERT INTO users (tenant_id, email, name, role) "
        "VALUES (%s, %s, 'A Person', 'reviewer') RETURNING id",
        (tenant_id, f"p-{uuid.uuid4().hex[:6]}@example.com"),
    ).fetchone()[0]


def _walk(conn: psycopg.Connection, record_id: uuid.UUID, states: list[str]) -> None:
    for state in states:
        conn.execute("UPDATE run_records SET state = %s WHERE id = %s", (state, record_id))


def _commit_failed_record(ledger: Ledger) -> uuid.UUID:
    record_id = ledger.add_record(ledger.new_run())
    _walk(ledger.conn, record_id, COMMIT_TO_FAILED)
    return record_id


def test_commit_attempted_cannot_be_cleared_by_a_standalone_update(ledger: Ledger) -> None:
    record_id = _commit_failed_record(ledger)
    with pytest.raises(psycopg.errors.RaiseException):
        ledger.conn.execute(
            "UPDATE run_records SET commit_attempted = false WHERE id = %s", (record_id,)
        )


def test_clearing_commit_attempted_does_not_open_a_blind_requeue(ledger: Ledger) -> None:
    record_id = _commit_failed_record(ledger)
    try:
        ledger.conn.execute(
            "UPDATE run_records SET commit_attempted = false WHERE id = %s", (record_id,)
        )
    except psycopg.errors.RaiseException:
        return  # the flag is protected, so the bypass is closed
    with pytest.raises(psycopg.errors.RaiseException):
        ledger.conn.execute("UPDATE run_records SET state = 'pending' WHERE id = %s", (record_id,))


def test_a_destination_check_does_not_carry_over_to_the_next_commit(ledger: Ledger) -> None:
    conn = ledger.conn
    person = _user(conn, ledger.tenant_id)
    record_id = _commit_failed_record(ledger)
    conn.execute(
        "UPDATE run_records SET state = 'pending', destination_checked_by = %s, "
        "destination_checked_at = now() WHERE id = %s",
        (person, record_id),
    )
    # The record is clicked again and fails again; nobody checks the destination this time.
    _walk(conn, record_id, BACK_TO_COMMIT)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE run_records SET state = 'pending' WHERE id = %s", (record_id,))


def test_a_destination_check_must_be_recorded_in_the_requeue_itself(ledger: Ledger) -> None:
    conn = ledger.conn
    person = _user(conn, ledger.tenant_id)
    record_id = _commit_failed_record(ledger)
    try:
        conn.execute(
            "UPDATE run_records SET destination_checked_by = %s WHERE id = %s",
            (person, record_id),
        )
    except psycopg.errors.RaiseException:
        return  # the fix rejects a standalone check, so it cannot be staged in an earlier UPDATE
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE run_records SET state = 'pending' WHERE id = %s", (record_id,))


def test_transition_audit_rows_cannot_be_edited(ledger: Ledger) -> None:
    record_id = ledger.add_record(ledger.new_run(), state="claimed")
    with pytest.raises(psycopg.errors.RaiseException):
        ledger.conn.execute(
            "UPDATE run_record_transitions SET to_state = 'verified' WHERE run_record_id = %s",
            (record_id,),
        )


def test_transition_audit_rows_cannot_be_deleted(ledger: Ledger) -> None:
    record_id = ledger.add_record(ledger.new_run(), state="claimed")
    with pytest.raises(psycopg.errors.RaiseException):
        ledger.conn.execute(
            "DELETE FROM run_record_transitions WHERE run_record_id = %s", (record_id,)
        )


def test_an_empty_idempotency_key_is_rejected(ledger: Ledger) -> None:
    with pytest.raises(psycopg.errors.RaiseException):
        ledger.add_record(ledger.new_run(), idempotency_key="")


def test_an_active_run_cannot_move_to_another_destination(ledger: Ledger) -> None:
    run = ledger.new_run(status="running")
    with pytest.raises(psycopg.errors.RaiseException):
        ledger.conn.execute(
            "UPDATE runs SET destination_key = 'somewhere-else' WHERE id = %s", (run.id,)
        )


def test_a_stale_lease_write_does_not_succeed_while_a_takeover_is_in_flight(
    ledger: Ledger, database_url: str
) -> None:
    """The documented state-change pattern, raced against the documented takeover."""
    conn = ledger.conn
    run = ledger.new_run(status="running")
    record_id = ledger.add_record(run, state="filling")
    conn.execute("UPDATE run_records SET claimed_by = 'worker-old' WHERE id = %s", (record_id,))
    epoch = conn.execute("SELECT lease_epoch FROM runs WHERE id = %s", (run.id,)).fetchone()[0]

    with (
        psycopg.connect(database_url) as takeover,
        psycopg.connect(database_url, autocommit=True) as stale,
    ):
        takeover.execute(
            "UPDATE runs SET worker_id = 'worker-new', lease_epoch = lease_epoch + 1 WHERE id = %s",
            (run.id,),
        )  # not committed yet: the takeover is in flight
        stale.execute("SET lock_timeout = '2s'")
        try:
            moved = stale.execute(
                "UPDATE run_records SET state = 'submitting' "
                "WHERE id = %s AND state = 'filling' AND claimed_by = 'worker-old' "
                "AND EXISTS (SELECT 1 FROM runs "
                "WHERE id = run_records.run_id AND lease_epoch = %s FOR SHARE)",
                (record_id, epoch),
            ).rowcount
        except psycopg.errors.LockNotAvailable:
            moved = 0  # blocked behind the takeover: the stale write did not land
        takeover.commit()

    assert moved == 0, "a worker whose lease was being taken over still wrote 'submitting'"


def test_recovery_leaves_reconciling_records_movable_by_the_new_worker(ledger: Ledger) -> None:
    """After the documented recovery, the documented state-change pattern must still apply."""
    conn = ledger.conn
    run = ledger.new_run(status="running")
    record_id = ledger.add_record(run, state="submitting")
    conn.execute("UPDATE run_records SET claimed_by = 'worker-old' WHERE id = %s", (record_id,))
    conn.execute(
        "UPDATE runs SET worker_id = 'worker-new', lease_epoch = lease_epoch + 1 WHERE id = %s",
        (run.id,),
    )
    epoch = conn.execute("SELECT lease_epoch FROM runs WHERE id = %s", (run.id,)).fetchone()[0]

    # Recovery, exactly as sql-patterns.md writes it: it reassigns the record to the new worker.
    conn.execute(
        "UPDATE run_records SET state = 'reconciling', claimed_by = 'worker-new', "
        "claimed_at = now() "
        "WHERE run_id = %s AND state IN ('submitting', 'submitted_unverified')",
        (run.id,),
    )
    # The new worker reconciles: found in the destination -> verified.
    moved = conn.execute(
        "UPDATE run_records SET state = 'verified' "
        "WHERE id = %s AND state = 'reconciling' AND claimed_by = 'worker-new' "
        "AND EXISTS (SELECT 1 FROM runs WHERE id = run_records.run_id AND lease_epoch = %s)",
        (record_id, epoch),
    ).rowcount
    assert moved == 1, "the new worker cannot move a reconciling record it inherited"


def test_claiming_a_queued_run_skips_a_busy_destination(ledger: Ledger) -> None:
    """The documented run-claim query must not crash when the destination is already active."""
    ledger.new_run(status="running")
    queued = ledger.new_run(status="queued")
    # The documented claim query, scoped to this test's tenant so other tests' runs don't interfere.
    # It skips a destination that already has an active run, so it does not hit the unique index.
    ledger.conn.execute(
        "UPDATE runs SET status = 'running', worker_id = 'w2', heartbeat_at = now() "
        "WHERE id = (SELECT id FROM runs WHERE status = 'queued' AND tenant_id = %s "
        "AND NOT EXISTS (SELECT 1 FROM runs active "
        "WHERE active.tenant_id = runs.tenant_id "
        "AND active.destination_key = runs.destination_key "
        "AND active.status IN ('running', 'paused')) "
        "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)",
        (ledger.tenant_id,),
    )
    row = ledger.conn.execute("SELECT status FROM runs WHERE id = %s", (queued.id,)).fetchone()
    status = row[0]
    assert status == "queued"
