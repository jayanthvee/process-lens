"""The commit window: write-ahead, acknowledgement, verify, and the pre-batch skip.

The write-ahead rule is the reason these are separate calls: :func:`begin_commit` must be
**committed** before the destination can change, and :func:`verify_commit` must write ``verified``
and its ``committed_keys`` row in **one** transaction.
"""

from __future__ import annotations

import uuid

import psycopg

from .errors import guard_refusal_is_typed
from .models import VerifyResult
from .records import _classify_miss

_BEGIN_COMMIT = """
UPDATE run_records
SET state = 'submitting', reason = %(reason)s
WHERE id = %(record)s
  AND state = 'filling'
  AND claimed_by = %(worker)s
  AND EXISTS (
    SELECT 1 FROM runs
    WHERE id = run_records.run_id AND lease_epoch = %(epoch)s
    FOR SHARE
  )
"""

_ACK_COMMIT = """
UPDATE run_records
SET state = 'submitted_unverified', reason = %(reason)s
WHERE id = %(record)s
  AND state = 'submitting'
  AND claimed_by = %(worker)s
  AND EXISTS (
    SELECT 1 FROM runs
    WHERE id = run_records.run_id AND lease_epoch = %(epoch)s
    FOR SHARE
  )
"""

_VERIFY_COMMIT = """
UPDATE run_records
SET state = 'verified', destination_ref = %(ref)s, reason = %(reason)s
WHERE id = %(record)s
  AND state IN ('submitted_unverified', 'reconciling')
  AND claimed_by = %(worker)s
  AND EXISTS (
    SELECT 1 FROM runs
    WHERE id = run_records.run_id AND lease_epoch = %(epoch)s
    FOR SHARE
  )
"""

# The L5 check: a 0-row insert means another record already committed this key.
_INSERT_COMMITTED_KEY = """
INSERT INTO committed_keys
  (tenant_id, destination_key, idempotency_key, workflow_id, run_record_id, destination_ref)
SELECT r.tenant_id, runs.destination_key, r.idempotency_key, runs.workflow_id, r.id, %(ref)s
FROM run_records r
JOIN runs ON runs.id = r.run_id
WHERE r.id = %(record)s
ON CONFLICT (tenant_id, destination_key, idempotency_key) DO NOTHING
RETURNING id
"""

# sql-patterns.md § Skip keys already committed by earlier runs.
_SKIP_ALREADY_COMMITTED = """
UPDATE run_records r
SET state = 'skipped', reason = 'already committed in an earlier run'
FROM committed_keys k
WHERE r.run_id = %(run)s AND r.state = 'pending'
  AND k.tenant_id = r.tenant_id AND k.destination_key = %(destination)s
  AND k.idempotency_key = r.idempotency_key
"""


def begin_commit(
    conn: psycopg.Connection,
    record_id: uuid.UUID,
    *,
    worker: str,
    epoch: int,
    reason: str = "saving it now",
) -> None:
    """``filling -> submitting``. The caller must commit before sending the command."""
    with conn.cursor() as cur, guard_refusal_is_typed():
        cur.execute(
            _BEGIN_COMMIT,
            {"reason": reason, "record": record_id, "worker": worker, "epoch": epoch},
        )
        if cur.rowcount == 0:
            _classify_miss(conn, record_id, worker=worker, epoch=epoch)


def mark_submitted_unverified(
    conn: psycopg.Connection,
    record_id: uuid.UUID,
    *,
    worker: str,
    epoch: int,
    reason: str = "sent it; checking it arrived",
) -> None:
    """``submitting -> submitted_unverified``. Called from the dispatch acknowledgement."""
    with conn.cursor() as cur, guard_refusal_is_typed():
        cur.execute(
            _ACK_COMMIT,
            {"reason": reason, "record": record_id, "worker": worker, "epoch": epoch},
        )
        if cur.rowcount == 0:
            _classify_miss(conn, record_id, worker=worker, epoch=epoch)


def verify_commit(
    conn: psycopg.Connection,
    record_id: uuid.UUID,
    *,
    worker: str,
    epoch: int,
    destination_ref: str | None,
    reason: str = "added and checked",
) -> VerifyResult:
    """One transaction: ``-> verified`` and the ``committed_keys`` insert.

    The caller owns the transaction boundary, so this must run inside one transaction; the runner
    writes the step event in the same block. ``key_inserted`` is False when another record already
    committed the key: the L5 duplicate incident.
    """
    with conn.cursor() as cur, guard_refusal_is_typed():
        cur.execute(
            _VERIFY_COMMIT,
            {
                "ref": destination_ref,
                "reason": reason,
                "record": record_id,
                "worker": worker,
                "epoch": epoch,
            },
        )
        if cur.rowcount == 0:
            _classify_miss(conn, record_id, worker=worker, epoch=epoch)

        cur.execute(_INSERT_COMMITTED_KEY, {"ref": destination_ref, "record": record_id})
        key_inserted = cur.fetchone() is not None

    return VerifyResult(key_inserted=key_inserted, destination_ref=destination_ref)


def skip_already_committed(
    conn: psycopg.Connection, run_id: uuid.UUID, destination_key: str
) -> int:
    """End every pending record whose idempotency key is already in ``committed_keys``.

    A matched record ends ``skipped``; it is never ``verified``.
    """
    with conn.cursor() as cur:
        cur.execute(_SKIP_ALREADY_COMMITTED, {"run": run_id, "destination": destination_key})
        return int(cur.rowcount)
