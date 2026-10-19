"""Migration 0009: releasing a record because the run paused does not spend one of its attempts.

A record released by an event (a pause, a lost Chrome connection, a hidden window, an admin freeze)
never failed: it goes back to ``pending`` and is claimed again, and that claim would count as a new
attempt. Three such pauses exhausted a three-attempt row that never ran once. The runner now marks
those releases with the transaction-local ``processlens.refund_attempt``, and the guard gives
the attempt back on that one update. Crash recovery does not set it, so a row that keeps crashing a
worker still runs out of attempts.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.db.seed import Ledger


def _attempts(conn: psycopg.Connection, record_id: uuid.UUID) -> int:
    row = conn.execute("SELECT attempts FROM run_records WHERE id = %s", (record_id,)).fetchone()
    assert row is not None
    return int(row[0])


def _move(conn: psycopg.Connection, record_id: uuid.UUID, new: str, *, refund: bool) -> None:
    with conn.transaction():
        if refund:
            conn.execute("SELECT set_config('processlens.refund_attempt', 'on', true)")
        conn.execute(
            "UPDATE run_records SET state = %s, reason = 'test' WHERE id = %s", (new, record_id)
        )


@pytest.mark.parametrize("state", ["claimed", "prechecked", "filling"])
def test_a_marked_release_gives_the_attempt_back(
    conn: psycopg.Connection, ledger: Ledger, state: str
) -> None:
    record_id = ledger.add_record(ledger.new_run(), state=state)
    before = _attempts(conn, record_id)

    _move(conn, record_id, "pending", refund=True)

    assert _attempts(conn, record_id) == before - 1


def test_an_unmarked_release_still_counts(conn: psycopg.Connection, ledger: Ledger) -> None:
    record_id = ledger.add_record(ledger.new_run(), state="filling")
    before = _attempts(conn, record_id)

    _move(conn, record_id, "pending", refund=False)

    assert _attempts(conn, record_id) == before


def test_three_paused_claims_leave_the_row_runnable(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    record_id = ledger.add_record(ledger.new_run(), max_attempts=3)
    for _ in range(3):
        _move(conn, record_id, "claimed", refund=False)
        _move(conn, record_id, "pending", refund=True)

    assert _attempts(conn, record_id) == 0
    _move(conn, record_id, "claimed", refund=False)
    assert _attempts(conn, record_id) == 1


def test_the_mark_does_not_touch_other_transitions(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    claimed = ledger.add_record(ledger.new_run(), state="pending")
    _move(conn, claimed, "claimed", refund=True)
    assert _attempts(conn, claimed) == 1

    parked = ledger.add_record(ledger.new_run(), state="filling")
    before = _attempts(conn, parked)
    _move(conn, parked, "parked", refund=True)
    assert _attempts(conn, parked) == before


def test_the_mark_does_not_let_application_code_write_attempts(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    record_id = ledger.add_record(ledger.new_run(), state="pending", attempts=2)

    with (
        pytest.raises(
            psycopg.errors.RaiseException, match="attempts is written only by the trigger"
        ),
        conn.transaction(),
    ):
        conn.execute("SELECT set_config('processlens.refund_attempt', 'on', true)")
        conn.execute("UPDATE run_records SET attempts = 0 WHERE id = %s", (record_id,))


def test_the_mark_ends_with_its_transaction(conn: psycopg.Connection, ledger: Ledger) -> None:
    with conn.transaction():
        conn.execute("SELECT set_config('processlens.refund_attempt', 'on', true)")
    record_id = ledger.add_record(ledger.new_run(), state="filling")
    before = _attempts(conn, record_id)

    _move(conn, record_id, "pending", refund=False)

    assert _attempts(conn, record_id) == before
