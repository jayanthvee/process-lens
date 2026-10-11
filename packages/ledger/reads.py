"""Read-only shapes the API needs.

The separation rule (all ledger SQL lives here) applies to reads too, so the API never writes SQL
that names ``runs`` or ``run_records``. Nothing here writes, so nothing needs a sql-pattern.
"""

from __future__ import annotations

import uuid

import psycopg
from psycopg.rows import dict_row

from .models import RecordClaim, ResultsRow, RunClaim, RunSummaryRow, TransitionRow, _row

_RUN_SUMMARY = """
SELECT run_id, total, verified, skipped, parked, failed, in_progress
FROM run_summary WHERE run_id = %(run)s
"""

_LIST_RECORDS = """
SELECT * FROM run_records WHERE run_id = %(run)s ORDER BY created_at, id
"""

_LIST_TRANSITIONS = """
SELECT t.id, t.run_record_id, t.from_state, t.to_state, t.reason, t.actor, t.at
FROM run_record_transitions t
JOIN run_records r ON r.id = t.run_record_id
WHERE r.run_id = %(run)s AND t.id > %(after)s
ORDER BY t.id
"""

# One row per ledger record, plus the excluded input rows of the run's batch (they never became a
# ledger row, so they are reported from input_records).
_RESULTS = """
SELECT 'ledger' AS kind, r.record_key, r.state::text AS state, r.reason, r.attempts,
       r.destination_ref, '[]'::jsonb AS issues
FROM run_records r
WHERE r.run_id = %(run)s
UNION ALL
SELECT 'excluded', i.record_key, 'excluded', NULL, NULL, NULL, i.issues
FROM input_records i
JOIN runs ru ON ru.id = %(run)s
WHERE i.batch_id = ru.input_batch_id AND i.status = 'excluded'
ORDER BY kind DESC, record_key
"""

_RUN_OF_RECORD = "SELECT run_id FROM run_records WHERE id = %(record)s"


def run_summary(conn: psycopg.Connection, run_id: uuid.UUID) -> RunSummaryRow | None:
    """The counts of a run, from the ``run_summary`` view."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_RUN_SUMMARY, {"run": run_id})
        row = cur.fetchone()
    return _row(RunSummaryRow, row) if row is not None else None


def list_run_records(conn: psycopg.Connection, run_id: uuid.UUID) -> list[RecordClaim]:
    """Every ledger record of a run, oldest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_RECORDS, {"run": run_id})
        return [_row(RecordClaim, row) for row in cur.fetchall()]


def list_transitions(
    conn: psycopg.Connection, run_id: uuid.UUID, *, after_id: int = 0
) -> list[TransitionRow]:
    """The audit feed of a run, after a cursor id (the dashboard polls this)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_TRANSITIONS, {"run": run_id, "after": after_id})
        return [_row(TransitionRow, row) for row in cur.fetchall()]


def results_rows(conn: psycopg.Connection, run_id: uuid.UUID) -> list[ResultsRow]:
    """One row per ledger record plus the excluded input rows with their issues."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_RESULTS, {"run": run_id})
        return [_row(ResultsRow, row) for row in cur.fetchall()]


def run_of_record(conn: psycopg.Connection, record_id: uuid.UUID) -> uuid.UUID | None:
    """The run a record belongs to, so a record endpoint can scope itself by the run's tenant."""
    with conn.cursor() as cur:
        cur.execute(_RUN_OF_RECORD, {"record": record_id})
        row = cur.fetchone()
    return row[0] if row is not None else None


__all__ = [
    "RunClaim",
    "list_run_records",
    "list_transitions",
    "results_rows",
    "run_of_record",
    "run_summary",
]
