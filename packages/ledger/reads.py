"""Read-only shapes the API needs.

The separation rule (all ledger SQL lives here) applies to reads too, so the API never writes SQL
that names ``runs`` or ``run_records``. Nothing here writes, so nothing needs a sql-pattern.
"""

from __future__ import annotations

import uuid

import psycopg
from psycopg.rows import dict_row

from .models import (
    ApprovalRequestRow,
    NeedsHelpRow,
    RecordClaim,
    ResultsRow,
    RunClaim,
    RunListRow,
    RunningRunRow,
    RunSummaryRow,
    TaskListRow,
    TransitionRow,
    WeekCountsRow,
    _row,
)

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

# Most recent runs first. The recipe name comes from the pinned version's own JSON, so a run stays
# named as it was pinned; the workflow name is only the fallback when that JSON carries none. The
# per-state counts come from a lateral aggregate, so a run with no records counts as {} not NULL.
_LIST_RUNS = """
SELECT ru.id, ru.status::text AS status, ru.pause_reason, ru.created_at,
       coalesce(rv.recipe -> 'meta' ->> 'name', w.name) AS recipe_name,
       rv.version AS recipe_version,
       coalesce(counts.by_state, '{}'::jsonb) AS counts
FROM runs ru
JOIN recipe_versions rv ON rv.id = ru.recipe_version_id
JOIN workflows w ON w.id = ru.workflow_id
LEFT JOIN LATERAL (
  SELECT jsonb_object_agg(per_state.state::text, per_state.n) AS by_state
  FROM (
    SELECT state, count(*) AS n FROM run_records WHERE run_id = ru.id GROUP BY state
  ) per_state
) counts ON true
ORDER BY ru.created_at DESC, ru.id
LIMIT %(limit)s
"""

# The requests of a run that nobody has answered yet, oldest first.
_LIST_OPEN_APPROVALS = """
SELECT * FROM approval_requests
WHERE run_id = %(run)s AND resolved_by_approval_id IS NULL
ORDER BY created_at, id
"""


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


def list_runs(conn: psycopg.Connection, *, limit: int = 50) -> list[RunListRow]:
    """The most recent runs first, at most ``limit`` of them (1..50)."""
    capped = max(1, min(int(limit), 50))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_RUNS, {"limit": capped})
        return [_row(RunListRow, row) for row in cur.fetchall()]


def list_open_approvals(conn: psycopg.Connection, run_id: uuid.UUID) -> list[ApprovalRequestRow]:
    """The approval requests of a run that have no decision yet, oldest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_OPEN_APPROVALS, {"run": run_id})
        return [_row(ApprovalRequestRow, row) for row in cur.fetchall()]


# The week's finished rows come from ``run_record_transitions``: one row per state change, written
# by the database trigger, so a row is counted by when it actually reached ``verified`` or
# ``skipped`` and not by the run's own timestamps. The join to ``run_records`` is only for the
# tenant filter.
_WEEK_COUNTS = """
SELECT
  count(*) FILTER (WHERE t.to_state = 'verified') AS done,
  count(*) FILTER (WHERE t.to_state = 'skipped') AS already_there
FROM run_record_transitions t
JOIN run_records rr ON rr.id = t.run_record_id
WHERE t.at >= now() - make_interval(days => %(days)s)
  AND (%(tenant)s::uuid IS NULL OR rr.tenant_id = %(tenant)s)
"""

# The wall clock of the runs that finished inside the window, from start to finish. An open run
# (queued, running or paused) is left out until it finishes, so a run paused over a weekend for an
# approval does not wipe out the week's time saved while it waits.
_WINDOW_RUN_SECONDS = """
SELECT coalesce(
         sum(extract(epoch FROM (finished_at - started_at))),
         0
       )::double precision AS seconds
FROM runs
WHERE finished_at IS NOT NULL
  AND started_at IS NOT NULL
  AND finished_at >= now() - make_interval(days => %(days)s)
  AND (%(tenant)s::uuid IS NULL OR tenant_id = %(tenant)s)
"""

# Every row across all runs that needs a person, newest first: by when it entered the state it is
# in now, so a problem that just appeared is at the top. The task's name is the recipe's own, with
# the workflow name as the fallback, exactly as the runs list does.
_LIST_NEEDS_HELP = """
SELECT rr.id AS record_id, rr.run_id,
       coalesce(rv.recipe -> 'meta' ->> 'name', w.name) AS task_name,
       rr.record_key AS "row", rr.state::text AS state, rr.reason, rr.commit_attempted,
       coalesce(entered.at, rr.created_at) AS entered_at
FROM run_records rr
JOIN runs ru ON ru.id = rr.run_id
JOIN recipe_versions rv ON rv.id = ru.recipe_version_id
JOIN workflows w ON w.id = ru.workflow_id
LEFT JOIN LATERAL (
  SELECT max(t.at) AS at
  FROM run_record_transitions t
  WHERE t.run_record_id = rr.id AND t.to_state = rr.state
) entered ON true
WHERE rr.state IN ('parked', 'failed')
  AND (%(tenant)s::uuid IS NULL OR rr.tenant_id = %(tenant)s)
ORDER BY entered_at DESC, rr.id DESC
"""

# The runs that are going right now: only ``running``. A queued run has not started, and a paused
# run is stopped, so neither is shown as "Running now" on Home. ``done`` counts both the rows that
# were added and the rows that were already there; ``already_there`` isolates the latter.
_LIST_RUNNING_RUNS = """
SELECT ru.id AS run_id,
       coalesce(rv.recipe -> 'meta' ->> 'name', w.name) AS task_name,
       coalesce(c.verified, 0) + coalesce(c.skipped, 0) AS done,
       coalesce(c.total, 0) AS total,
       coalesce(c.skipped, 0) AS already_there
FROM runs ru
JOIN recipe_versions rv ON rv.id = ru.recipe_version_id
JOIN workflows w ON w.id = ru.workflow_id
LEFT JOIN LATERAL (
  SELECT count(*) AS total,
         count(*) FILTER (WHERE rr.state = 'verified') AS verified,
         count(*) FILTER (WHERE rr.state = 'skipped') AS skipped
  FROM run_records rr
  WHERE rr.run_id = ru.id
) c ON true
WHERE ru.status = 'running'
  AND (%(tenant)s::uuid IS NULL OR ru.tenant_id = %(tenant)s)
ORDER BY ru.created_at DESC, ru.id
"""

# Every saved task: the latest version of each non-archived workflow, newest first. ``app`` is the
# host of the first site the task may use -- the recipe document's own ``meta.allowed_origins``,
# with the workflow's configured origins as the fallback for a version that predates that field.
# ``last_run`` is the newest run of the workflow, or all-NULL when it has never run.
_LIST_TASKS = """
SELECT workflow_id, recipe_version_id, name,
       CASE
         WHEN origin IS NULL OR origin = '' THEN NULL
         ELSE split_part(
           regexp_replace(origin, '^[a-zA-Z][a-zA-Z0-9+.-]*://', ''),
           '/', 1
         )
       END AS app,
       last_run_id, last_run_status, last_run_started_at, last_run_counts
FROM (
  SELECT rv.workflow_id, rv.id AS recipe_version_id,
         coalesce(rv.recipe -> 'meta' ->> 'name', w.name) AS name,
         coalesce(rv.recipe -> 'meta' -> 'allowed_origins' ->> 0, w.allowed_origins[1]) AS origin,
         last.id AS last_run_id, last.status::text AS last_run_status,
         last.started_at AS last_run_started_at,
         coalesce(last.counts, '{}'::jsonb) AS last_run_counts,
         rv.created_at AS created_at
  FROM recipe_versions rv
  JOIN workflows w ON w.id = rv.workflow_id
  LEFT JOIN LATERAL (
    SELECT ru.id, ru.status, ru.started_at,
           (SELECT coalesce(jsonb_object_agg(s.state::text, s.n), '{}'::jsonb)
            FROM (
              SELECT state, count(*) AS n FROM run_records WHERE run_id = ru.id GROUP BY state
            ) s) AS counts
    FROM runs ru
    WHERE ru.workflow_id = rv.workflow_id AND ru.tenant_id = rv.tenant_id
    ORDER BY ru.created_at DESC, ru.id
    LIMIT 1
  ) last ON true
  WHERE w.archived_at IS NULL
    AND rv.version = (
      SELECT max(v.version) FROM recipe_versions v WHERE v.workflow_id = rv.workflow_id
    )
    AND (%(tenant)s::uuid IS NULL OR rv.tenant_id = %(tenant)s)
) ranked
ORDER BY created_at DESC, recipe_version_id
LIMIT %(limit)s
"""


def week_counts(
    conn: psycopg.Connection, *, days: int, tenant_id: uuid.UUID | None = None
) -> WeekCountsRow:
    """How many rows entered ``verified`` and ``skipped`` in the last ``days`` days."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_WEEK_COUNTS, {"days": days, "tenant": tenant_id})
        return _row(WeekCountsRow, cur.fetchone())


def window_run_seconds(
    conn: psycopg.Connection, *, days: int, tenant_id: uuid.UUID | None = None
) -> float:
    """The wall clock, in seconds, that the runs started in the last ``days`` days took."""
    with conn.cursor() as cur:
        cur.execute(_WINDOW_RUN_SECONDS, {"days": days, "tenant": tenant_id})
        row = cur.fetchone()
    return float(row[0]) if row is not None else 0.0


def list_needs_help(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID | None = None
) -> list[NeedsHelpRow]:
    """Every row in state ``parked`` or ``failed``, newest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_NEEDS_HELP, {"tenant": tenant_id})
        return [_row(NeedsHelpRow, row) for row in cur.fetchall()]


def list_running_runs(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID | None = None
) -> list[RunningRunRow]:
    """The runs that are running right now, newest first, with their progress."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_RUNNING_RUNS, {"tenant": tenant_id})
        return [_row(RunningRunRow, row) for row in cur.fetchall()]


def list_tasks(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID | None = None, limit: int | None = None
) -> list[TaskListRow]:
    """The latest version of every non-archived workflow, newest first.

    ``limit`` of ``None`` is every task (the SQL ``LIMIT NULL`` is no limit).
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_TASKS, {"tenant": tenant_id, "limit": limit})
        return [_row(TaskListRow, row) for row in cur.fetchall()]


__all__ = [
    "RunClaim",
    "RunListRow",
    "TaskListRow",
    "WeekCountsRow",
    "list_needs_help",
    "list_open_approvals",
    "list_run_records",
    "list_running_runs",
    "list_runs",
    "list_tasks",
    "list_transitions",
    "results_rows",
    "run_of_record",
    "run_summary",
    "week_counts",
    "window_run_seconds",
]
