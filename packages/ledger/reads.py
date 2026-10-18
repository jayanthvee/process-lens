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
    AppRow,
    AppTaskRow,
    BlockingRunRow,
    KeepsFailingRow,
    NeedsHelpRow,
    OpenRunRow,
    RecordClaim,
    ResultsRow,
    RunClaim,
    RunListRow,
    RunningRunRow,
    RunSummaryRow,
    SpreadsheetRow,
    TaskListRow,
    TransitionRow,
    WeekCountsRow,
    _row,
)
from .runs import DEFAULT_HEARTBEAT_TIMEOUT

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


# PL-041: the run a queued one is waiting behind. The database allows one running-or-paused run per
# tenant and destination (``runs_one_active_per_destination``), so a queued run can be stuck behind
# another task's run on the same app, and the page has to be able to say which one. The task's name
# is the recipe's own, with the workflow name as the fallback, exactly as the runs list names it.
# The run's own row supplies the tenant and the destination, so a client never names either.
_RUN_BLOCKING = """
SELECT r.id AS run_id,
       coalesce(rv.recipe -> 'meta' ->> 'name', w.name) AS task_name,
       r.status::text AS status
FROM runs me
JOIN runs r ON r.tenant_id = me.tenant_id
  AND r.destination_key = me.destination_key
  AND r.id <> me.id
JOIN recipe_versions rv ON rv.id = r.recipe_version_id
JOIN workflows w ON w.id = r.workflow_id
WHERE me.id = %(run)s
  AND me.status = 'queued'
  AND r.status IN ('running', 'paused')
ORDER BY r.created_at, r.id
LIMIT 1
"""


def run_blocking(conn: psycopg.Connection, run_id: uuid.UUID) -> BlockingRunRow | None:
    """The run a queued run is waiting behind, or ``None`` when nothing is ahead of it.

    A run that is not queued is never waiting: it is the one holding the app.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_RUN_BLOCKING, {"run": run_id})
        row = cur.fetchone()
    return _row(BlockingRunRow, row) if row is not None else None


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

# The runs that are going: ``running``, or ``paused`` -- a run waiting for a person's approval is
# paused, and it must stay in view. A queued run has not started, so it is not shown. ``done``
# counts both the rows that were added and the rows that were already there; ``already_there``
# isolates the latter.
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
WHERE ru.status IN ('running', 'paused')
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
    """The runs that are running or paused, newest first, with their progress."""
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


# PL-041: the tasks that stopped working. A saved task never changes, so one recorded before a fix
# keeps failing the same way; the task list has to be able to say so. The two most recent finished
# runs of a task are read, each counted for the rows it added and the rows that need a person, and
# a task is flagged when both added nothing, both have at least one such row, and the two runs
# share a reason. A run that is still going has not finished, so it neither earns nor clears the
# warning. ``min`` picks one reason when two runs have several in common, so the answer is stable.
_KEEPS_FAILING = """
WITH finished AS (
  SELECT ru.id AS run_id, ru.workflow_id,
         row_number() OVER (
           PARTITION BY ru.workflow_id ORDER BY ru.created_at DESC, ru.id DESC
         ) AS place
  FROM runs ru
  JOIN workflows w ON w.id = ru.workflow_id
  WHERE ru.status NOT IN ('queued', 'running', 'paused')
    AND w.archived_at IS NULL
    AND (%(tenant)s::uuid IS NULL OR ru.tenant_id = %(tenant)s)
),
per_run AS (
  SELECT finished.workflow_id, finished.run_id,
         count(*) FILTER (WHERE rr.state = 'verified') AS added,
         count(*) FILTER (WHERE rr.state IN ('parked', 'failed')) AS troubled,
         array_agg(DISTINCT rr.reason) FILTER (
           WHERE rr.state IN ('parked', 'failed') AND rr.reason IS NOT NULL
         ) AS reasons
  FROM finished
  LEFT JOIN run_records rr ON rr.run_id = finished.run_id
  WHERE finished.place <= 2
  GROUP BY finished.workflow_id, finished.run_id
)
SELECT newest.workflow_id, min(shared.reason) AS reason
FROM per_run newest
JOIN per_run older ON older.workflow_id = newest.workflow_id
  AND older.run_id <> newest.run_id
CROSS JOIN LATERAL unnest(newest.reasons) AS shared(reason)
WHERE newest.added = 0 AND newest.troubled > 0
  AND older.added = 0 AND older.troubled > 0
  AND shared.reason = ANY (older.reasons)
GROUP BY newest.workflow_id
"""


def keeps_failing_tasks(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID | None = None
) -> list[KeepsFailingRow]:
    """The tasks whose last two finished runs both added nothing and stopped for one reason.

    ``reason`` is the runner's own sentence, the same one the rows themselves carry, so the page
    can put it into words with ``plainReason`` rather than inventing a second vocabulary.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_KEEPS_FAILING, {"tenant": tenant_id})
        return [_row(KeepsFailingRow, row) for row in cur.fetchall()]


# One row per site a saved task may use. The tasks are the same ones ``_LIST_TASKS`` names -- the
# latest version of each non-archived workflow, named as its recipe is -- but grouped by origin
# instead of by task, and sorted by name inside each site. ``last_run`` is the newest run of *any*
# task that may use the site, so a site says when it was last worked on, not when one task was.
_LIST_APPS = """
WITH saved AS (
  SELECT w.id AS workflow_id, w.tenant_id,
         coalesce(rv.recipe -> 'meta' ->> 'name', w.name) AS name,
         w.allowed_origins
  FROM workflows w
  JOIN recipe_versions rv ON rv.workflow_id = w.id
  WHERE w.archived_at IS NULL
    AND rv.version = (
      SELECT max(v.version) FROM recipe_versions v WHERE v.workflow_id = w.id
    )
    AND (%(tenant)s::uuid IS NULL OR w.tenant_id = %(tenant)s)
),
per_origin AS (
  SELECT DISTINCT s.tenant_id, site.origin, s.workflow_id, s.name
  FROM saved s
  CROSS JOIN LATERAL unnest(s.allowed_origins) AS site(origin)
  WHERE site.origin IS NOT NULL AND site.origin <> ''
),
last_run AS (
  SELECT per_origin.origin AS site, ru.created_at, ru.status,
         row_number() OVER (
           PARTITION BY per_origin.origin ORDER BY ru.created_at DESC, ru.id
         ) AS place
  FROM runs ru
  JOIN per_origin ON per_origin.workflow_id = ru.workflow_id
    AND per_origin.tenant_id = ru.tenant_id
)
SELECT per_origin.origin AS origin,
       jsonb_agg(
         jsonb_build_object('id', per_origin.workflow_id, 'name', per_origin.name)
         ORDER BY per_origin.name, per_origin.workflow_id
       ) AS tasks,
       last_run.created_at AS last_run_at,
       last_run.status::text AS last_run_status
FROM per_origin
LEFT JOIN last_run ON last_run.site = per_origin.origin AND last_run.place = 1
GROUP BY per_origin.origin, last_run.created_at, last_run.status
ORDER BY per_origin.origin
"""


def list_apps(conn: psycopg.Connection, *, tenant_id: uuid.UUID | None = None) -> list[AppRow]:
    """One row per site the tenant's saved tasks may use, with its tasks and its newest run.

    An archived workflow is not a task any more, so its sites are left out with it. A task that may
    use two sites appears on both, because the site is what the Apps page lists.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_APPS, {"tenant": tenant_id})
        rows = cur.fetchall()
    return [
        AppRow(
            origin=row["origin"],
            tasks=[
                AppTaskRow(id=uuid.UUID(task["id"]), name=task["name"]) for task in row["tasks"]
            ],
            last_run_at=row["last_run_at"],
            last_run_status=row["last_run_status"],
        )
        for row in rows
    ]


# One row per uploaded spreadsheet: a csv, xlsx or manual batch -- never a documents batch, which
# belongs to the Documents page. A batch with no file is rows a person pasted, so it says so. The
# task is named the way ``_LIST_TASKS`` names it (the latest recipe's own name, with the workflow
# name as the fallback), and the runs are counted and the newest kept so the page can link the
# batch to the run that used it. No ``input_records`` is read: the page lists the upload, not its
# rows.
_LIST_SPREADSHEETS = """
SELECT b.id,
       coalesce(f.original_name, 'Pasted rows') AS file_name,
       b.workflow_id AS task_id,
       coalesce(latest.recipe -> 'meta' ->> 'name', w.name) AS task_name,
       b.row_count,
       b.created_at AS uploaded_at,
       u.name AS uploaded_by,
       rc.runs,
       rc.last_run_id
FROM input_batches b
JOIN workflows w ON w.id = b.workflow_id
LEFT JOIN files f ON f.id = b.file_id
LEFT JOIN users u ON u.id = b.created_by
LEFT JOIN LATERAL (
  SELECT rv.recipe
  FROM recipe_versions rv
  WHERE rv.workflow_id = b.workflow_id
  ORDER BY rv.version DESC
  LIMIT 1
) latest ON true
LEFT JOIN LATERAL (
  SELECT count(ru.id) AS runs,
         (SELECT ru2.id FROM runs ru2
          WHERE ru2.input_batch_id = b.id
          ORDER BY ru2.created_at DESC, ru2.id
          LIMIT 1) AS last_run_id
  FROM runs ru
  WHERE ru.input_batch_id = b.id
) rc ON true
WHERE b.kind IN ('csv', 'xlsx', 'manual')
  AND (%(tenant)s::uuid IS NULL OR b.tenant_id = %(tenant)s)
ORDER BY b.created_at DESC, b.id
"""


def list_spreadsheets(
    conn: psycopg.Connection, *, tenant_id: uuid.UUID | None = None
) -> list[SpreadsheetRow]:
    """Every uploaded spreadsheet, newest first, with its task and the runs that used it.

    Only ``csv``, ``xlsx`` and ``manual`` batches are spreadsheets; ``documents`` belong to the
    Documents page. A batch with no file is named "Pasted rows", and a batch nothing has run on
    has ``runs = 0`` and ``last_run_id = None``.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_SPREADSHEETS, {"tenant": tenant_id})
        return [_row(SpreadsheetRow, row) for row in cur.fetchall()]


# The runs a worker could act on, with whether a live worker holds each: a queued run has none,
# and a running run's lease is live only while its heartbeat is fresh. The lease rule is the one
# ``find_stale_run`` applies, so a takeover and this read can never disagree about "live".
_LIST_OPEN_RUNS = """
SELECT id AS run_id, status::text AS status,
       (worker_id IS NOT NULL AND heartbeat_at IS NOT NULL
        AND heartbeat_at >= now() - (%(timeout)s)::interval) AS worker_live
FROM runs
WHERE status IN ('queued', 'running')
ORDER BY created_at, id
"""


def list_open_runs(
    conn: psycopg.Connection, *, heartbeat_timeout: str = DEFAULT_HEARTBEAT_TIMEOUT
) -> list[OpenRunRow]:
    """Every queued or running run, with whether a live worker holds it right now.

    The demo supervisor reads this every couple of seconds and decides from it, as a pure function,
    whether to start its one worker. A run whose worker died is still ``running`` until the lease
    lapses -- this read is what lets the supervisor see that and step in.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LIST_OPEN_RUNS, {"timeout": heartbeat_timeout})
        return [_row(OpenRunRow, row) for row in cur.fetchall()]


# PL-040: the newest preview ("Try one row") of a batch on one recipe version that verified a row.
# Creating the batch run that follows it waives the first-record approval the preview already got.
_VERIFIED_PREVIEW = """
SELECT r.id
FROM runs r
WHERE r.input_batch_id = %(batch)s
  AND r.recipe_version_id = %(version)s
  AND r.mode = 'preview'
  AND EXISTS (SELECT 1 FROM run_records rr WHERE rr.run_id = r.id AND rr.state = 'verified')
ORDER BY r.created_at DESC, r.id
LIMIT 1
"""


def verified_preview_run(
    conn: psycopg.Connection, input_batch_id: uuid.UUID, recipe_version_id: uuid.UUID
) -> uuid.UUID | None:
    """The newest preview of this batch and recipe version that verified a row, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(_VERIFIED_PREVIEW, {"batch": input_batch_id, "version": recipe_version_id})
        row = cur.fetchone()
    return row[0] if row else None


__all__ = [
    "AppRow",
    "AppTaskRow",
    "BlockingRunRow",
    "RunClaim",
    "RunListRow",
    "SpreadsheetRow",
    "TaskListRow",
    "WeekCountsRow",
    "keeps_failing_tasks",
    "list_apps",
    "list_needs_help",
    "list_open_approvals",
    "list_open_runs",
    "list_run_records",
    "list_running_runs",
    "list_runs",
    "list_spreadsheets",
    "list_tasks",
    "list_transitions",
    "verified_preview_run",
    "results_rows",
    "run_blocking",
    "run_of_record",
    "run_summary",
    "week_counts",
    "window_run_seconds",
]
