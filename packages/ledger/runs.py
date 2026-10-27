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
from .lease import require_lease
from .models import RunClaim, _row

# The claim pattern (sql-patterns.md § Claim the next queued run). It skips a run whose destination
# already has an active run, so it does not collide with runs_one_active_per_destination.
# The claim also increments `lease_epoch` (B1): every new holder fences out any previous one, so a
# worker that lost a race can never write under a lease it no longer owns.
# PL-087: a worker for one Chrome (`browser`) claims that browser's runs and unbound ones; a worker
# with no browser (the harness, a laptop's single worker) claims as before.
_CLAIM_RUN = """
UPDATE runs
SET status = 'running', worker_id = %(worker)s, lease_epoch = lease_epoch + 1,
    started_at = coalesce(started_at, now()), heartbeat_at = now()
WHERE id = (
  SELECT id FROM runs
  WHERE status = 'queued'
    AND (%(browser)s::text IS NULL OR runs.browser_id IS NULL OR runs.browser_id = %(browser)s)
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
  AND (%(browser)s::text IS NULL OR browser_id IS NULL OR browser_id = %(browser)s)
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

# B1: a worker that stops on a pause hands the run back, so a later resume does not leave the run
# held by a worker that has already stopped. A run resumed in the meantime matches 0 rows.
_RELEASE_RUN = """
UPDATE runs SET worker_id = NULL
WHERE id = %(run)s AND worker_id = %(worker)s AND status = 'paused'
RETURNING id
"""

# B1: resume moves a paused run back to `queued` only when no live worker holds it (released, or its
# heartbeat lapsed). Otherwise the current holder simply carries on: the run goes back to `running`.
_RESUME_RUN = """
UPDATE runs
SET status = (CASE
      WHEN worker_id IS NULL OR heartbeat_at < now() - %(timeout)s::interval THEN 'queued'
      ELSE 'running'
    END)::run_status,
    pause_reason = NULL,
    pause_detail = NULL
WHERE id = %(run)s AND status = 'paused'
RETURNING id, status
"""

# B1: the same rule for a cancel. A released or stale run goes back to `queued` for a worker to
# finalise; a run a live worker still holds goes to `running`, so that holder notices the request.
_REQUEST_CANCEL = """
UPDATE runs
SET cancel_requested_at = now(),
    status = (CASE
      WHEN status = 'paused'
           AND (worker_id IS NULL OR heartbeat_at < now() - %(timeout)s::interval) THEN 'queued'
      WHEN status = 'paused' THEN 'running'
      ELSE status
    END)::run_status
WHERE id = %(run)s AND status IN ('queued', 'running', 'paused')
RETURNING id
"""

# A paused run whose heartbeat has not been refreshed for this long is presumed abandoned: no worker
# is acting on it, so a resume may hand it to a fresh worker. It matches the runner's default
# heartbeat timeout (3x the schema's longest step timeout plus dispatch), so a live worker is never
# mistaken for a dead one.
DEFAULT_HEARTBEAT_TIMEOUT = "365 seconds"

_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})


def claim_run(
    conn: psycopg.Connection, worker_id: str, *, browser_id: str | None = None
) -> RunClaim | None:
    """Claim the oldest queued run whose destination has no active run.

    With ``browser_id``, only that Chrome's runs and unbound ones are candidates (PL-087).

    A ``23505`` on ``runs_one_active_per_destination`` is a lost race: another worker took the
    destination between the subquery and the update. It means "nothing to claim", not an error.
    """
    try:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_CLAIM_RUN, {"worker": worker_id, "browser": browser_id})
            row = cur.fetchone()
    except psycopg.errors.UniqueViolation:
        return None
    return _row(RunClaim, row) if row is not None else None


def find_stale_run(
    conn: psycopg.Connection, *, heartbeat_timeout: str, browser_id: str | None = None
) -> RunClaim | None:
    """The oldest running or paused run whose heartbeat lapsed, if any (of ``browser_id``'s)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_FIND_STALE_RUN, {"timeout": heartbeat_timeout, "browser": browser_id})
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


# PL-089: a cancel no live worker holds (no holder, or a lapsed heartbeat: the takeover rule).
# The supervisor takes such a run under a lease of its own and finishes the cancel as a worker does.
_UNHELD_CANCELS = """
SELECT id FROM runs
WHERE cancel_requested_at IS NOT NULL
  AND status IN ('queued', 'running', 'paused')
  AND (worker_id IS NULL OR heartbeat_at IS NULL OR heartbeat_at < now() - %(timeout)s::interval)
ORDER BY created_at, id
"""

_TAKE_CANCELLED_RUN = """
UPDATE runs
SET worker_id = %(worker)s, lease_epoch = lease_epoch + 1, heartbeat_at = now()
WHERE id = %(run)s
  AND cancel_requested_at IS NOT NULL
  AND status IN ('queued', 'running', 'paused')
  AND (worker_id IS NULL OR heartbeat_at IS NULL OR heartbeat_at < now() - %(timeout)s::interval)
RETURNING lease_epoch
"""


def unheld_cancelled_runs(
    conn: psycopg.Connection, *, heartbeat_timeout: str = DEFAULT_HEARTBEAT_TIMEOUT
) -> list[uuid.UUID]:
    """Open runs with a cancel request that no live worker holds, oldest first (PL-089)."""
    with conn.cursor() as cur:
        cur.execute(_UNHELD_CANCELS, {"timeout": heartbeat_timeout})
        return [row[0] for row in cur.fetchall()]


def take_cancelled_run(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    worker_id: str,
    *,
    heartbeat_timeout: str = DEFAULT_HEARTBEAT_TIMEOUT,
) -> int | None:
    """Take a cancelled, unheld run's lease to finish the cancel; the new epoch, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            _TAKE_CANCELLED_RUN,
            {"run": run_id, "worker": worker_id, "timeout": heartbeat_timeout},
        )
        row = cur.fetchone()
    return int(row[0]) if row is not None else None


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


# PL-107: a finished run whose row is put back in the queue must be put back to work. The
# supervisor only starts `queued` runs, so a `completed` run stays completed and the re-queued row
# waits forever. Reopening clears the worker/lease fields so the run is claimed exactly like a
# fresh one. `started_at` is kept: it is when this work first started.
_REOPEN_COMPLETED_RUN = """
UPDATE runs
SET status = 'queued',
    worker_id = NULL,
    heartbeat_at = NULL,
    cancel_requested_at = NULL,
    pause_reason = NULL,
    pause_detail = NULL,
    finished_at = NULL
WHERE id = %(run)s AND status = 'completed'
"""


def reopen_completed_run(conn: psycopg.Connection, run_id: uuid.UUID) -> bool:
    """Put a ``completed`` run back to ``queued``, clearing its worker/lease fields (PL-107).

    Returns whether the run was ``completed`` and is now queued. A ``running``, ``paused`` or
    already re-queued run is left exactly as it is: its own worker keeps going.

    ``lease_epoch`` is deliberately left alone. It is the fence (B1) and may only ever go up: a
    worker from before the reopen that still holds an old epoch must never match again after the
    next claim. The claim increments it.
    """
    with conn.cursor() as cur:
        cur.execute(_REOPEN_COMPLETED_RUN, {"run": run_id})
        return cur.rowcount == 1


def set_run_status(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    status: str,
    *,
    pause_reason: str | None = None,
    pause_detail: str | None = None,
    worker: str | None = None,
    epoch: int | None = None,
) -> None:
    """Move ``runs.status`` and record the reason when there is one.

    A worker's call passes ``worker`` and ``epoch`` and is fenced (Requirement 1): the worker
    must still hold the lease, or it raises :class:`StaleLease`. An operator call (the pause
    endpoint) omits both and is not fenced, because it is not acting under a lease.
    """
    if worker is not None and epoch is not None:
        require_lease(conn, run_id, worker, epoch)

    terminal = ", finished_at = now()" if status in _TERMINAL_STATUSES else ""
    sql = (
        f"UPDATE runs SET status = %(status)s, pause_reason = %(reason)s, "
        f"pause_detail = %(detail)s{terminal}"
        " WHERE id = %(run)s"
    )
    with conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "status": status,
                "reason": pause_reason,
                "detail": pause_detail,
                "run": run_id,
            },
        )


def release_run(conn: psycopg.Connection, run_id: uuid.UUID, worker_id: str) -> bool:
    """Hand a paused run back: clear the holder so a later resume can queue it afresh (B1).

    Returns ``False`` when the run was resumed in the meantime (0 rows), which tells the caller to
    keep going rather than leave a run it no longer owns.
    """
    with conn.cursor() as cur:
        cur.execute(_RELEASE_RUN, {"run": run_id, "worker": worker_id})
        return cur.fetchone() is not None


def request_cancel(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    *,
    heartbeat_timeout: str = DEFAULT_HEARTBEAT_TIMEOUT,
) -> bool:
    """Ask a run to stop (R4, B1). Returns True when the run was there to ask.

    A paused run no live worker holds (released, or its heartbeat lapsed) goes back to ``queued``
    for a worker to pick up and finalise. A paused run a live worker still holds goes to
    ``running``, so that holder notices the cancel request. A running run is left alone.
    """
    with conn.cursor() as cur:
        cur.execute(_REQUEST_CANCEL, {"run": run_id, "timeout": heartbeat_timeout})
        return cur.fetchone() is not None


def resume_run(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    *,
    heartbeat_timeout: str = DEFAULT_HEARTBEAT_TIMEOUT,
) -> bool:
    """Resume a paused run (R4, B1).

    ``paused -> queued`` only when no live worker holds it (``worker_id IS NULL``, or its heartbeat
    lapsed), so a worker that has already stopped cannot leave the run unclaimed. Otherwise the run
    goes back to ``running`` and its current holder carries on. Returns True when the run was
    paused.
    """
    with conn.cursor() as cur:
        cur.execute(_RESUME_RUN, {"run": run_id, "timeout": heartbeat_timeout})
        return cur.fetchone() is not None
