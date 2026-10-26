"""Record-level ledger operations: claim, change state, requeue, bulk release.

Every conditional state change is scoped to the expected current state, the claiming worker, and the
run's live ``lease_epoch`` (locked ``FOR SHARE``), exactly as ``sql-patterns.md`` requires. A change
that matches no row raises a typed error and is never silent.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import StaleLease, StateConflict, guard_refusal_is_typed
from .lease import require_lease
from .models import RecordClaim, _row
from .runs import get_run, reopen_completed_run

# sql-patterns.md § Claim the next record in a run. attempts is raised by the database trigger.
# The order is the input row's row_index, not created_at: a batch's records are inserted together,
# so created_at ties to the microsecond and the random uuid breaks the tie, which made the canary
# -- the first record to reach a commit -- non-deterministic. row_index is the person's row order.
_CLAIM_RECORD = """
WITH next AS (
  SELECT rr.id FROM run_records rr
  JOIN input_records ir ON ir.id = rr.input_record_id
  WHERE rr.run_id = %(run)s AND rr.state = 'pending' AND rr.attempts < rr.max_attempts
  ORDER BY ir.row_index, rr.id
  FOR UPDATE OF rr SKIP LOCKED
  LIMIT 1
)
UPDATE run_records r
SET state = 'claimed', claimed_by = %(worker)s, claimed_at = now()
FROM next
WHERE r.id = next.id
RETURNING r.*
"""

# sql-patterns.md § Change state. The lease check locks the runs row FOR SHARE, so a stale worker's
# write waits for a takeover to commit and then fails the lease_epoch check (H7).
_CHANGE_STATE = """
UPDATE run_records
SET state = %(new)s, reason = %(reason)s, last_error = %(error)s
WHERE id = %(record)s
  AND state = %(expected)s
  AND claimed_by = %(worker)s
  AND EXISTS (
    SELECT 1 FROM runs
    WHERE id = run_records.run_id AND lease_epoch = %(epoch)s
    FOR SHARE
  )
"""

_READ_OWNERSHIP = """
SELECT r.state, r.claimed_by, runs.lease_epoch
FROM run_records r JOIN runs ON runs.id = r.run_id
WHERE r.id = %(record)s
"""

_RELEASE_WORKING = """
UPDATE run_records
SET state = 'pending', reason = %(reason)s, claimed_by = NULL, claimed_at = NULL
WHERE run_id = %(run)s AND claimed_by = %(worker)s
  AND state IN ('claimed', 'prechecked', 'filling')
"""

_SKIP_PENDING = """
UPDATE run_records
SET state = 'skipped', reason = %(reason)s
WHERE run_id = %(run)s AND state = 'pending'
"""

_SELECT_RECORD = "SELECT * FROM run_records WHERE id = %(record)s"


def _classify_miss(
    conn: psycopg.Connection, record_id: uuid.UUID, *, worker: str, epoch: int
) -> None:
    """Raise the right typed error when a conditional change matched no row."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_READ_OWNERSHIP, {"record": record_id})
        row = cur.fetchone()
    if row is None:
        raise StateConflict(f"run_record {record_id} does not exist")
    if row["claimed_by"] != worker or int(row["lease_epoch"]) != epoch:
        raise StaleLease(
            f"run_record {record_id}: claimed_by={row['claimed_by']!r}"
            f" lease_epoch={row['lease_epoch']}"
            f" (worker {worker!r}, epoch {epoch})"
        )
    raise StateConflict(f"run_record {record_id} is {row['state']!r}, not the expected state")


def claim_record(
    conn: psycopg.Connection, run_id: uuid.UUID, worker_id: str, epoch: int
) -> RecordClaim | None:
    """Claim the oldest pending record of a run that still has attempts left.

    The order is the input row's ``row_index`` (PL-017): a batch's records are created together,
    so ``created_at`` ties and the random uuid tie-break made the claim order -- and therefore
    the canary -- non-deterministic. ``row_index`` is the person's row order.

    Fenced (Requirement 1): a worker whose lease has been taken over claims nothing and sees the
    typed :class:`StaleLease`, rather than claiming a record it cannot act on or mistaking a
    stolen run for an empty one.
    """
    require_lease(conn, run_id, worker_id, epoch)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_CLAIM_RECORD, {"run": run_id, "worker": worker_id})
        row = cur.fetchone()
    return _row(RecordClaim, row) if row is not None else None


def get_record(conn: psycopg.Connection, record_id: uuid.UUID) -> RecordClaim | None:
    """Read one record by id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_RECORD, {"record": record_id})
        row = cur.fetchone()
    return _row(RecordClaim, row) if row is not None else None


def change_state(
    conn: psycopg.Connection,
    record_id: uuid.UUID,
    *,
    worker: str,
    epoch: int,
    expected: str,
    new: str,
    reason: str,
    error: Any = None,
    refund_attempt: bool = False,
) -> None:
    """Move a record to ``new`` when it is in ``expected`` and this worker's lease is live.

    ``refund_attempt`` marks a release to ``pending`` caused by a pause, not a failure: the guard
    (migration 0009) gives the claim's attempt back. Call it inside the caller's transaction, since
    the mark is transaction-local.
    """
    with conn.cursor() as cur, guard_refusal_is_typed():
        if refund_attempt:
            cur.execute("SELECT set_config('processlens.refund_attempt', 'on', true)")
        cur.execute(
            _CHANGE_STATE,
            {
                "new": new,
                "reason": reason,
                "error": Jsonb(error) if error is not None else None,
                "record": record_id,
                "expected": expected,
                "worker": worker,
                "epoch": epoch,
            },
        )
        moved = cur.rowcount
        if refund_attempt:
            # The mark covers this one update, not whatever else the transaction goes on to do.
            cur.execute("SELECT set_config('processlens.refund_attempt', 'off', true)")
        if moved == 0:
            _classify_miss(conn, record_id, worker=worker, epoch=epoch)


def release_working_records(
    conn: psycopg.Connection, run_id: uuid.UUID, worker_id: str, epoch: int, *, reason: str
) -> int:
    """Release this worker's claimed/prechecked/filling records back to ``pending`` (recovery)."""
    require_lease(conn, run_id, worker_id, epoch)
    with conn.cursor() as cur:
        cur.execute(_RELEASE_WORKING, {"run": run_id, "worker": worker_id, "reason": reason})
        return int(cur.rowcount)


def skip_pending_records(
    conn: psycopg.Connection, run_id: uuid.UUID, worker_id: str, epoch: int, *, reason: str
) -> int:
    """End every pending record of a run ``skipped`` (a cancelled run never processes them)."""
    require_lease(conn, run_id, worker_id, epoch)
    with conn.cursor() as cur:
        cur.execute(_SKIP_PENDING, {"run": run_id, "reason": reason})
        return int(cur.rowcount)


def requeue_record(
    conn: psycopg.Connection,
    record_id: uuid.UUID,
    *,
    reason: str,
    destination_checked_by: uuid.UUID | None = None,
) -> str:
    """Re-queue a parked or failed record; return the state it moved to.

    A record that never reached a commit goes straight to ``pending``. A commit-attempted record
    goes to ``reconciling`` unless the caller records who checked the destination, in which case the
    same update sets both check columns and it reaches ``pending`` directly (the guard rejects
    anything else).

    PL-107: a row of a ``completed`` run puts that run back to ``queued`` in the same transaction,
    so the supervisor starts it again. A ``cancelled`` run is refused: it was stopped on purpose,
    and a new run is the way to try the row again. A ``running`` or ``paused`` run is left to its
    own worker.

    PL-107 item 15: the row's own reason is kept visible; the requeue note never replaces it.
    """
    record = get_record(conn, record_id)
    if record is None:
        raise StateConflict(f"run_record {record_id} does not exist")
    run = get_run(conn, record.run_id)
    if run is None:
        raise StateConflict(f"run {record.run_id} does not exist")
    if run.status == "cancelled":
        raise StateConflict("This run was stopped. Start a new run to try this row again.")

    # Keep why the row stopped, so the person can still see it after pressing Try again.
    previous = (record.reason or "").strip()
    requeue_reason = f"Trying again — last time: {previous}" if previous else reason

    if record.commit_attempted and destination_checked_by is None:
        with conn.cursor() as cur, guard_refusal_is_typed():
            cur.execute(
                "UPDATE run_records SET state = 'reconciling', reason = %(reason)s"
                " WHERE id = %(record)s AND state IN ('parked', 'failed') AND commit_attempted",
                {"reason": requeue_reason, "record": record_id},
            )
            if cur.rowcount == 0:
                raise StateConflict(f"run_record {record_id} is not parked or failed")
        state = "reconciling"
    elif record.commit_attempted:
        with conn.cursor() as cur, guard_refusal_is_typed():
            cur.execute(
                "UPDATE run_records SET state = 'pending', reason = %(reason)s,"
                " claimed_by = NULL, claimed_at = NULL,"
                " destination_checked_by = %(checked_by)s, destination_checked_at = now()"
                " WHERE id = %(record)s AND state IN ('parked', 'failed') AND commit_attempted",
                {
                    "reason": requeue_reason,
                    "record": record_id,
                    "checked_by": destination_checked_by,
                },
            )
            if cur.rowcount == 0:
                raise StateConflict(f"run_record {record_id} is not parked or failed")
        state = "pending"
    else:
        with conn.cursor() as cur, guard_refusal_is_typed():
            cur.execute(
                "UPDATE run_records SET state = 'pending', reason = %(reason)s,"
                " claimed_by = NULL, claimed_at = NULL"
                " WHERE id = %(record)s AND state IN ('parked', 'failed') AND NOT commit_attempted",
                {"reason": requeue_reason, "record": record_id},
            )
            if cur.rowcount == 0:
                raise StateConflict(f"run_record {record_id} is not parked or failed")
        state = "pending"

    # PL-107: a finished run whose row is back in the queue is put back to work. A running or
    # paused run is a no-op here; the reopen's own WHERE keeps it to `completed`.
    reopen_completed_run(conn, record.run_id)
    return state


def dismiss_record(
    conn: psycopg.Connection,
    record_id: uuid.UUID,
    *,
    reason: str,
    destination_checked_by: uuid.UUID | None = None,
) -> str:
    """Dismiss a parked record as ``skipped``; return the state it moved to.

    A record that never reached a commit goes straight to ``skipped``. A commit-attempted record is
    only dismissed when the same update records a fresh destination check, and the guard is the
    authority: the check is passed through as-is, so a commit-attempted record with no check reaches
    the database and is refused there rather than by a rule of this function's own.
    """
    record = get_record(conn, record_id)
    if record is None:
        raise StateConflict(f"run_record {record_id} does not exist")

    if record.commit_attempted:
        with conn.cursor() as cur, guard_refusal_is_typed():
            cur.execute(
                "UPDATE run_records SET state = 'skipped', reason = %(reason)s,"
                " destination_checked_by = %(checked_by)s, destination_checked_at = now()"
                " WHERE id = %(record)s AND state = 'parked'"
                " AND commit_attempted = true",
                {"reason": reason, "record": record_id, "checked_by": destination_checked_by},
            )
            if cur.rowcount == 0:
                raise StateConflict(f"run_record {record_id} is not parked")
        return "skipped"

    with conn.cursor() as cur, guard_refusal_is_typed():
        cur.execute(
            "UPDATE run_records SET state = 'skipped', reason = %(reason)s"
            " WHERE id = %(record)s AND state = 'parked'"
            " AND commit_attempted = false",
            {"reason": reason, "record": record_id},
        )
        if cur.rowcount == 0:
            raise StateConflict(f"run_record {record_id} is not parked")
    return "skipped"
