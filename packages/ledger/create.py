"""Run creation: the run row and one ledger row per ready input record.

A ledger row is born ``pending`` with an ``idempotency_key`` (the insert guard requires it), so the
runner can only create it through here. This is the one new pattern the ticket adds to
``sql-patterns.md``: creating a run and its records.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

_INSERT_RUN = """
INSERT INTO runs
  (tenant_id, workflow_id, recipe_version_id, input_batch_id, mode, status,
   destination_key, approval_policy, min_seconds_between_rows, min_seconds_between_steps)
VALUES
  (%(tenant)s, %(workflow)s, %(recipe)s, %(batch)s, %(mode)s, 'queued',
   %(destination)s, %(policy)s, %(rows_pace)s, %(steps_pace)s)
RETURNING id
"""

_INSERT_RUN_RECORD = """
INSERT INTO run_records
  (tenant_id, run_id, input_record_id, record_key, idempotency_key, state, max_attempts)
VALUES
  (%(tenant)s, %(run)s, %(input)s, %(record_key)s, %(idempotency)s, 'pending', %(max_attempts)s)
RETURNING id
"""


def insert_run(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    recipe_version_id: uuid.UUID,
    input_batch_id: uuid.UUID | None,
    mode: str,
    destination_key: str,
    approval_policy: dict[str, Any],
    min_seconds_between_rows: int | None = None,
    min_seconds_between_steps: int | None = None,
) -> uuid.UUID:
    """Insert a ``queued`` run and return its id.

    The two pacing values are the person's choice on the run's start screen (PL-080); ``None`` is
    unset, which the runner reads as 0 -- the pace the product had before pacing existed.
    """
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_RUN,
            {
                "tenant": tenant_id,
                "workflow": workflow_id,
                "recipe": recipe_version_id,
                "batch": input_batch_id,
                "mode": mode,
                "destination": destination_key,
                "policy": Jsonb(approval_policy),
                "rows_pace": min_seconds_between_rows,
                "steps_pace": min_seconds_between_steps,
            },
        )
        return cur.fetchone()[0]


def insert_run_record(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    input_record_id: uuid.UUID,
    record_key: str,
    idempotency_key: str,
    max_attempts: int = 3,
) -> uuid.UUID:
    """Insert one ``pending`` ledger row and return its id."""
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_RUN_RECORD,
            {
                "tenant": tenant_id,
                "run": run_id,
                "input": input_record_id,
                "record_key": record_key,
                "idempotency": idempotency_key,
                "max_attempts": max_attempts,
            },
        )
        return cur.fetchone()[0]
