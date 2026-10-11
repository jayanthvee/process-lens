"""Write one ``step_events`` row for every executor step (S4).

``step_events`` is the append-only audit of what the browser did; the results screen reads timings
and what the page showed from it. The row is written in the same transaction as any state change
that accompanies it, so a state change and its evidence commit together.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import RecordClaim

_INSERT_STEP_EVENT = """
INSERT INTO step_events
  (tenant_id, run_record_id, step_id, attempt, started_at, ended_at, outcome, locator_rung,
   observed, error)
VALUES
  (%(tenant)s, %(record)s, %(step)s, %(attempt)s,
   coalesce(%(started)s, now()), coalesce(%(ended)s, now()),
   %(outcome)s, %(rung)s, %(observed)s, %(error)s)
RETURNING id
"""


def record_step_event(
    conn: psycopg.Connection,
    *,
    record: RecordClaim,
    step_id: str,
    outcome: str,
    observed: dict[str, Any] | None = None,
    error: Any = None,
    locator_rung: int | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    attempt: int | None = None,
) -> int:
    """Append one step event and return its id.

    ``outcome`` is one of the ``step_outcome`` values: ``ok``, ``retried``, ``failed``, ``skipped``.
    """
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_STEP_EVENT,
            {
                "tenant": record.tenant_id,
                "record": record.id,
                "step": step_id,
                "attempt": attempt if attempt is not None else record.attempts,
                "started": started_at,
                "ended": ended_at,
                "outcome": outcome,
                "rung": locator_rung,
                "observed": Jsonb(observed) if observed is not None else None,
                "error": Jsonb(error) if error is not None else None,
            },
        )
        return int(cur.fetchone()[0])
