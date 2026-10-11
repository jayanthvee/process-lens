"""Recovery, close-out, and the counts the run loop needs.

Recovery runs after **every** claim or takeover (R4): on a fresh run it is a no-op, and on a resumed
run it releases the records a stopped worker left behind. Records that may have been submitted go to
``reconciling`` and are reassigned to the new worker, so the new worker can act on them under the
worker-scoped state change.
"""

from __future__ import annotations

import uuid

import psycopg
from psycopg.rows import dict_row

from .lease import require_lease
from .models import RecordClaim, RecoveryCounts, _row

# sql-patterns.md § Recovery. Commit-window records go to reconciling and are reassigned to the
# new worker, including records already reconciling under the dead worker's claim, so the new
# worker can reconcile every one of them before it claims any pending record.
_RECONCILE_COMMIT_WINDOW = """
UPDATE run_records
SET state = 'reconciling', reason = %(reason)s, claimed_by = %(worker)s, claimed_at = now()
WHERE run_id = %(run)s AND state IN ('submitting', 'submitted_unverified', 'reconciling')
"""

_RELEASE_WORKING = """
UPDATE run_records
SET state = 'pending', reason = %(reason)s, claimed_by = NULL, claimed_at = NULL
WHERE run_id = %(run)s AND state IN ('claimed', 'prechecked', 'filling')
"""

_CLOSE_OUT_EXHAUSTED = """
UPDATE run_records
SET state = 'failed', reason = 'attempts exhausted'
WHERE run_id = %(run)s AND state = 'pending' AND attempts >= max_attempts
"""

_COUNT_NOT_EXIT = """
SELECT count(*) FROM run_records
WHERE run_id = %(run)s AND state NOT IN ('verified', 'skipped', 'parked', 'failed')
"""

_COUNT_COMMITS_ATTEMPTED = """
SELECT count(*) FROM run_records WHERE run_id = %(run)s AND commit_attempted
"""

_SELECT_RECONCILING = """
SELECT * FROM run_records WHERE run_id = %(run)s AND state = 'reconciling'
ORDER BY created_at, id
"""


def recover_run(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    worker_id: str,
    epoch: int,
    *,
    reason: str = "released on restart",
) -> RecoveryCounts:
    """Move the commit window to ``reconciling`` and the working records back to ``pending``."""
    require_lease(conn, run_id, worker_id, epoch)
    with conn.cursor() as cur:
        cur.execute(
            _RECONCILE_COMMIT_WINDOW,
            {"run": run_id, "worker": worker_id, "reason": reason},
        )
        reconciled = int(cur.rowcount)
        cur.execute(_RELEASE_WORKING, {"run": run_id, "reason": reason})
        released = int(cur.rowcount)
    return RecoveryCounts(reconciled=reconciled, released=released)


def close_out_exhausted(
    conn: psycopg.Connection, run_id: uuid.UUID, worker_id: str, epoch: int
) -> int:
    """End every pending record that used all its attempts ``failed`` (R5)."""
    require_lease(conn, run_id, worker_id, epoch)
    with conn.cursor() as cur:
        cur.execute(_CLOSE_OUT_EXHAUSTED, {"run": run_id})
        return int(cur.rowcount)


def count_not_exit(conn: psycopg.Connection, run_id: uuid.UUID) -> int:
    """How many records are still not in an exit state."""
    with conn.cursor() as cur:
        cur.execute(_COUNT_NOT_EXIT, {"run": run_id})
        return int(cur.fetchone()[0])


def all_records_in_exit_state(conn: psycopg.Connection, run_id: uuid.UUID) -> bool:
    """True when every record of the run is in an exit state."""
    return count_not_exit(conn, run_id) == 0


def commits_attempted(conn: psycopg.Connection, run_id: uuid.UUID) -> int:
    """How many records of the run have entered the commit window (the approval-policy counter)."""
    with conn.cursor() as cur:
        cur.execute(_COUNT_COMMITS_ATTEMPTED, {"run": run_id})
        return int(cur.fetchone()[0])


def reconciling_records(conn: psycopg.Connection, run_id: uuid.UUID) -> list[RecordClaim]:
    """Every record of the run in ``reconciling``, oldest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_RECONCILING, {"run": run_id})
        return [_row(RecordClaim, row) for row in cur.fetchall()]
