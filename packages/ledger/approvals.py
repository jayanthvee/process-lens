"""Approval requests and decisions.

An approval request records the **exact** values a person was shown; a decision points at the
request it answered. That pair is what makes a re-run idempotent: the runner looks up a decision by
(record, step, shown) and never re-asks for values it already answered (R3).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import StateConflict
from .models import ApprovalDecision, ApprovalRequestRow, _row

_OPEN_REQUEST = """
INSERT INTO approval_requests (tenant_id, run_id, run_record_id, step_id, kind, shown)
VALUES (%(tenant)s, %(run)s, %(record)s, %(step)s, %(kind)s, %(shown)s)
RETURNING id
"""

# The decision is copied from the request, so it carries the same tenant, run, record, step, kind
# and the exact values, and points back at the request it answered.
_RECORD_DECISION = """
INSERT INTO approvals
  (tenant_id, run_id, run_record_id, step_id, kind, shown, decision, decided_by, request_id, reason)
SELECT tenant_id, run_id, run_record_id, step_id, kind, shown,
       %(decision)s, %(decided_by)s, id, %(reason)s
FROM approval_requests
WHERE id = %(request)s
RETURNING id
"""

_RESOLVE_REQUEST = """
UPDATE approval_requests SET resolved_by_approval_id = %(approval)s
WHERE id = %(request)s AND resolved_by_approval_id IS NULL
"""

_OPEN_FOR_RUN = """
SELECT * FROM approval_requests
WHERE run_id = %(run)s AND resolved_by_approval_id IS NULL
ORDER BY created_at DESC
LIMIT 1
"""

_SELECT_REQUEST = "SELECT * FROM approval_requests WHERE id = %(id)s"

# A decision, joined to the values its request showed. Newest first, so a later decision wins.
_DECISIONS_FOR_STEP = """
SELECT ar.shown, a.decision, a.reason, a.decided_at
FROM approval_requests ar
JOIN approvals a ON a.request_id = ar.id
WHERE ar.run_record_id = %(record)s AND ar.step_id = %(step)s
ORDER BY a.decided_at DESC, a.id DESC
"""


def open_approval_request(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    run_record_id: uuid.UUID,
    step_id: str,
    kind: str,
    shown: dict[str, Any],
) -> uuid.UUID:
    """Record the exact values a person must approve; return the request id."""
    with conn.cursor() as cur:
        cur.execute(
            _OPEN_REQUEST,
            {
                "tenant": tenant_id,
                "run": run_id,
                "record": run_record_id,
                "step": step_id,
                "kind": kind,
                "shown": Jsonb(shown),
            },
        )
        return cur.fetchone()[0]


def record_approval(
    conn: psycopg.Connection,
    *,
    request_id: uuid.UUID,
    decision: str,
    decided_by: uuid.UUID,
    reason: str | None = None,
) -> uuid.UUID:
    """Record a decision and resolve its request, in the caller's transaction.

    The insert and the resolve must commit together; the caller wraps them in one transaction.
    """
    with conn.cursor() as cur:
        cur.execute(
            _RECORD_DECISION,
            {
                "decision": decision,
                "decided_by": decided_by,
                "request": request_id,
                "reason": reason,
            },
        )
        row = cur.fetchone()
        if row is None:
            raise StateConflict(f"approval request {request_id} does not exist")
        approval_id = row[0]

        cur.execute(_RESOLVE_REQUEST, {"approval": approval_id, "request": request_id})
        if cur.rowcount == 0:
            raise StateConflict(f"approval request {request_id} is already resolved")
    return approval_id


def open_approval_for_run(conn: psycopg.Connection, run_id: uuid.UUID) -> ApprovalRequestRow | None:
    """The open approval request of a run, if any."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_OPEN_FOR_RUN, {"run": run_id})
        row = cur.fetchone()
    return _row(ApprovalRequestRow, row) if row is not None else None


def get_request(conn: psycopg.Connection, request_id: uuid.UUID) -> ApprovalRequestRow | None:
    """Read one approval request by id."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_REQUEST, {"id": request_id})
        row = cur.fetchone()
    return _row(ApprovalRequestRow, row) if row is not None else None


def decisions_for_step(
    conn: psycopg.Connection, run_record_id: uuid.UUID, step_id: str
) -> list[ApprovalDecision]:
    """Every decision recorded for one step of one record, newest first (R3)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_DECISIONS_FOR_STEP, {"record": run_record_id, "step": step_id})
        return [_row(ApprovalDecision, row) for row in cur.fetchall()]
