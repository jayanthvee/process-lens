"""Run-level ledger operations: claim, take over, heartbeat, status.

Every statement here is one of the approved shapes in ``internal/docs/sql-patterns.md``. Functions
take a connection and never open a transaction of their own; the caller owns the boundary, because
the write-ahead pattern depends on committing at the right moment.
"""

from __future__ import annotations

import uuid

import psycopg
from psycopg.rows import dict_row

from .errors import StaleLease
from .models import RunClaim, _row

# The claim pattern (sql-patterns.md § Claim the next queued run). It skips a run whose destination
# already has an active run, so it does not collide with runs_one_active_per_destination.
_CLAIM_RUN = """
UPDATE runs
SET status = 'running', worker_id = %(worker)s,
    started_at = coalesce(started_at, now()), heartbeat_at = now()
WHERE id = (
  SELECT id FROM runs
  WHERE status = 'queued'
    AND NOT EXISTS (
      SELECT 1 FROM runs active
      WHERE active.tenant_id = runs.tenant_id
        AND active.destination_key = runs.destination_key
        AND active.status IN ('running', 'paused')
    )
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *
"""

_FIND_STALE_RUN = """
SELECT * FROM runs
WHERE status IN ('running', 'paused')
  AND heartbeat_at < now() - %(timeout)s::interval
ORDER BY heartbeat_at
LIMIT 1
"""

_TAKE_OVER_RUN = """
UPDATE runs
SET worker_id = %(worker)s,
    lease_epoch = lease_epoch + 1,
    heartbeat_at = now()
WHERE id = %(run)s
  AND status IN ('running', 'paused')
  AND heartbeat_at < now() - %(timeout)s::interval
RETURNING lease_epoch
"""

_HEARTBEAT = """
UPDATE runs
SET heartbeat_at = now()
WHERE id = %(run)s AND worker_id = %(worker)s AND lease_epoch = %(epoch)s
"""

_GET_RUN = "SELECT * FROM runs WHERE id = %(run)s"

_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})


def claim_run(conn: psycopg.Connection, worker_id: str) -> RunClaim | None:
    """Claim the oldest queued run whose destination has no active run.

    A ``23505`` on ``runs_one_active_per_destination`` is a lost race: another worker took the
    destination between the subquery and the update. It means "nothing to claim", not an error.
    """
    try:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_CLAIM_RUN, {"worker": worker_id})
            row = cur.fetchone()
    except psycopg.errors.UniqueViolation:
        return None
    return _row(RunClaim, row) if row is not None else None


def find_stale_run(conn: psycopg.Connection, *, heartbeat_timeout: str) -> RunClaim | None:
    """The oldest running or paused run whose heartbeat lapsed, if any."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_FIND_STALE_RUN, {"timeout": heartbeat_timeout})
        row = cur.fetchone()
    return _row(RunClaim, row) if row is not None else None


def take_over_run(
    conn: psycopg.Connection, run_id: uuid.UUID, worker_id: str, *, heartbeat_timeout: str
) -> int | None:
    """Take over a run whose heartbeat is stale; return the new ``lease_epoch`` or ``None``."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            _TAKE_OVER_RUN,
            {"run": run_id, "worker": worker_id, "timeout": heartbeat_timeout},
        )
        row = cur.fetchone()
    return int(row["lease_epoch"]) if row is not None else None


def heartbeat(conn: psycopg.Connection, run_id: uuid.UUID, worker_id: str, epoch: int) -> None:
    """Refresh the lease. Raises :class:`StaleLease` when another worker owns the run now."""
    with conn.cursor() as cur:
        cur.execute(_HEARTBEAT, {"run": run_id, "worker": worker_id, "epoch": epoch})
        if cur.rowcount == 0:
            raise StaleLease(
                f"run {run_id}: worker {worker_id!r} no longer holds lease epoch {epoch}"
            )


def get_run(conn: psycopg.Connection, run_id: uuid.UUID) -> RunClaim | None:
    """Re-read a run. The worker calls this at every step boundary (R4)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_GET_RUN, {"run": run_id})
        row = cur.fetchone()
    return _row(RunClaim, row) if row is not None else None


def set_run_status(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    status: str,
    *,
    pause_reason: str | None = None,
) -> None:
    """Move ``runs.status`` and record the reason when there is one."""
    terminal = ", finished_at = now()" if status in _TERMINAL_STATUSES else ""
    sql = (
        f"UPDATE runs SET status = %(status)s, pause_reason = %(reason)s{terminal}"
        " WHERE id = %(run)s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, {"status": status, "reason": pause_reason, "run": run_id})


def request_cancel(conn: psycopg.Connection, run_id: uuid.UUID) -> bool:
    """Ask a run to stop (R4). Returns True when the run was there to ask.

    A paused run has no worker acting on it, so it also moves back to ``queued`` for a worker to
    pick up and finalise; a running run is left alone and its worker notices the cancel request.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET cancel_requested_at = now(),"
            " status = CASE WHEN status = 'paused' THEN 'queued' ELSE status END"
            " WHERE id = %(run)s AND status IN ('queued', 'running', 'paused') RETURNING id",
            {"run": run_id},
        )
        return cur.fetchone() is not None


def resume_run(conn: psycopg.Connection, run_id: uuid.UUID) -> bool:
    """Resume a paused run: ``paused -> queued`` so a worker claims it again (R4)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET status = 'queued', pause_reason = NULL"
            " WHERE id = %(run)s AND status = 'paused' RETURNING id",
            {"run": run_id},
        )
        return cur.fetchone() is not None
