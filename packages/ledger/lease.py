"""The lease fence.

Requirement 1 of the ticket: **every** ledger write that a worker makes is conditional on the worker
still holding the run's live ``lease_epoch``. The reference statement is
``sql-patterns.md § Change state``, which folds the check into the ``UPDATE`` with
``EXISTS (... FOR SHARE)``.

The bulk writes (recovery, close-out, skip, release) and the record claim read no single state to
match against, so they cannot carry the check in their own ``WHERE`` clause the same way. They
call :func:`require_lease` first, in the same transaction: the ``FOR SHARE`` lock makes a write
whose lease is being taken over wait for the takeover to commit and then fail the
``lease_epoch`` check (H7), exactly as the reference statement does. Zero rows is a typed
:class:`StaleLease`, never silence.
"""

from __future__ import annotations

import uuid

import psycopg

from .errors import StaleLease

_LEASE_HELD = """
SELECT 1 FROM runs
WHERE id = %(run)s AND worker_id = %(worker)s AND lease_epoch = %(epoch)s
FOR SHARE
"""


def require_lease(conn: psycopg.Connection, run_id: uuid.UUID, worker_id: str, epoch: int) -> None:
    """Raise :class:`StaleLease` unless this worker holds ``lease_epoch`` on the run.

    Must run inside the caller's transaction, so the ``FOR SHARE`` lock is held until it commits.
    """
    with conn.cursor() as cur:
        cur.execute(_LEASE_HELD, {"run": run_id, "worker": worker_id, "epoch": epoch})
        if cur.fetchone() is None:
            raise StaleLease(
                f"run {run_id}: worker {worker_id!r} does not hold lease epoch {epoch}"
            )
