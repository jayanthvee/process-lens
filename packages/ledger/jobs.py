"""Background jobs. Postgres is the queue (D-007)."""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from .models import JobClaim, _row

# sql-patterns.md § Claim a background job.
_CLAIM_JOB = """
UPDATE jobs
SET status = 'running', locked_by = %(worker)s, locked_at = now(), attempts = attempts + 1
WHERE id = (
  SELECT id FROM jobs
  WHERE status = 'queued' AND run_after <= now() AND attempts < max_attempts
  ORDER BY run_after
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *
"""


def claim_job(conn: psycopg.Connection, worker_id: str) -> JobClaim | None:
    """Claim the oldest ready job, or ``None`` when the queue is empty."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_CLAIM_JOB, {"worker": worker_id})
        row = cur.fetchone()
    return _row(JobClaim, row) if row is not None else None
