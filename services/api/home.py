"""The Home screen's own reads: the week's counts, the time saved, the rows that need a person,
the runs that are still going, and the saved tasks.

Every query lives in ``packages.ledger.reads`` (the separation rule,
``tests/runner/test_sql_separation.py``); this module only calls those functions and shapes the
JSON, so it writes no ledger statement of its own.

The week's counts and the minutes saved come from the append-only audit table
``run_record_transitions`` -- one row per state change, written by the database trigger, not by
this API. A row is "done" when it entered ``verified`` inside the rolling last seven days and
"already there" when it entered ``skipped``; neither is inferred from a run's own status.

Minutes saved used to be three minutes for every done row, minus the wall clock the window's runs
took, clamped at zero. PL-061 makes the manual side real: every done row is worth the manual
seconds its own task learned while it was taught (``meta.manual_seconds_per_row``), with the old
three minutes (180 seconds) only as the fallback for a task that never measured itself. The
fallback rows are counted in ``estimated_rows`` so the screen can say so.

Three routes are frozen by PL-023: the whole week, the rows that need a person, and the saved
tasks. All three take the optional ``tenant_id`` filter ``/api/v1/recipes`` takes, so a test can
scope itself in a shared session database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from packages.ledger import (
    NeedsHelpRow,
    TaskListRow,
    keeps_failing_tasks,
    list_needs_help,
    list_running_runs,
    list_tasks,
    week_counts,
    week_manual_seconds,
    window_run_seconds,
)

from .db import connection
from .task_columns import examples_for_versions, inputs_for_versions

router = APIRouter(prefix="/api/v1", tags=["home"])

# The week is the rolling last seven days.
HOME_WINDOW_DAYS = 7
# The Home panels are a glance, not an export: three rows that need a person, five tasks.
NEEDS_HELP_ON_HOME = 3
TASKS_ON_HOME = 5


class HomeCounts(BaseModel):
    """The week's counts, in the words the page uses."""

    done: int
    already_there: int
    needs_help: int


class NeedsHelpEntry(BaseModel):
    """One row that needs a person."""

    record_id: uuid.UUID
    run_id: uuid.UUID
    task_name: str
    row: str
    state: str
    reason: str | None
    commit_attempted: bool
    # PL-117: the task's site, for the destination tick's words, and whether the task is a sign-in
    # (a sign-in row has no destination tick and no Dismiss at all).
    app: str | None = None
    sign_in: bool = False


class RunningEntry(BaseModel):
    """One run that is still going, with its progress."""

    run_id: uuid.UUID
    task_name: str
    done: int
    total: int
    already_there: int


class TaskLastRun(BaseModel):
    """A task's newest run, or nothing at all when it has never run.

    PL-112: ``pause_reason``/``pause_detail`` let the Tasks row say why a sign-in run could not
    read its code, in the same words the run page uses.
    """

    run_id: uuid.UUID
    status: str
    started_at: datetime | None
    counts: dict[str, int]
    pause_reason: str | None = None
    pause_detail: str | None = None


class TaskEntry(BaseModel):
    """One saved task: its name, the site it may use, and its newest run."""

    workflow_id: uuid.UUID
    recipe_version_id: uuid.UUID
    name: str
    app: str | None
    last_run: TaskLastRun | None


class KeepsFailing(BaseModel):
    """The reason a task's last two finished runs both stopped for, in the runner's words."""

    reason: str


class TaskListEntry(TaskEntry):
    """One row of ``GET /api/v1/tasks``: the task, whether it stopped working (PL-041), and the
    columns a spreadsheet must supply.

    ``inputs`` is empty for a task taught with no recorded inputs; such a task is run once from
    the side panel, with no spreadsheet at all (PL-105).

    PL-112: ``sign_in`` is true for a task taught through a page that asked for an emailed code.
    ``sign_in_column`` is the column its email was typed into, and ``sign_in_email`` is the value
    used while teaching (the recipe's own example). The list of emails typed later is kept in the
    extension's storage, never here.
    """

    keeps_failing: KeepsFailing | None = None
    inputs: list[str] = Field(default_factory=list)
    sign_in: bool = False
    sign_in_column: str | None = None
    sign_in_email: str | None = None


class HomeSummary(BaseModel):
    """`GET /api/v1/home`: the whole week at a glance."""

    window_days: int
    counts: HomeCounts
    # PL-061: the real comparison, in seconds. `manual_seconds` is what the week's done rows would
    # have cost by hand; `automated_seconds` is what the window's finished runs actually took;
    # `saved_seconds` is the difference, never below zero. `estimated_rows` counts the rows whose
    # task had no measurement, so the screen can say the figure is partly an estimate.
    manual_seconds: int
    automated_seconds: int
    saved_seconds: int
    estimated_rows: int
    # The pre-PL-061 field, kept working until every screen reads the new ones.
    minutes_saved: int
    needs_help: list[NeedsHelpEntry]
    running: list[RunningEntry]
    tasks: list[TaskEntry]


def _needs_help(rows: list[NeedsHelpRow]) -> list[NeedsHelpEntry]:
    return [
        NeedsHelpEntry(
            record_id=row.record_id,
            run_id=row.run_id,
            task_name=row.task_name,
            row=row.row,
            state=row.state,
            reason=row.reason,
            commit_attempted=row.commit_attempted,
            app=row.app,
            sign_in=row.sign_in,
        )
        for row in rows
    ]


def _tasks(rows: list[TaskListRow]) -> list[TaskEntry]:
    entries: list[TaskEntry] = []
    for row in rows:
        last_run: TaskLastRun | None = None
        if row.last_run_id is not None:
            last_run = TaskLastRun(
                run_id=row.last_run_id,
                status=row.last_run_status or "queued",
                started_at=row.last_run_started_at,
                counts=row.last_run_counts,
                pause_reason=row.last_run_pause_reason,
                pause_detail=row.last_run_pause_detail,
            )
        entries.append(
            TaskEntry(
                workflow_id=row.workflow_id,
                recipe_version_id=row.recipe_version_id,
                name=row.name,
                app=row.app,
                last_run=last_run,
            )
        )
    return entries


@router.get("/home", response_model=HomeSummary)
def home_summary(tenant_id: Annotated[uuid.UUID | None, Query()] = None) -> HomeSummary:
    """The week at a glance: the counts, the time saved, help needed, live runs and tasks."""
    with connection() as conn:
        counts = week_counts(conn, days=HOME_WINDOW_DAYS, tenant_id=tenant_id)
        week_time = week_manual_seconds(conn, days=HOME_WINDOW_DAYS, tenant_id=tenant_id)
        seconds = window_run_seconds(conn, days=HOME_WINDOW_DAYS, tenant_id=tenant_id)
        needing = list_needs_help(conn, tenant_id=tenant_id)
        running = list_running_runs(conn, tenant_id=tenant_id)
        tasks = list_tasks(conn, tenant_id=tenant_id, limit=TASKS_ON_HOME)

    manual_seconds = round(week_time.manual_seconds)
    automated_seconds = round(seconds)
    saved_seconds = max(0, manual_seconds - automated_seconds)
    return HomeSummary(
        window_days=HOME_WINDOW_DAYS,
        counts=HomeCounts(
            done=counts.done,
            already_there=counts.already_there,
            # The count is the whole set, not the three the panel shows: the sidebar and the
            # needs-help page read the same set.
            needs_help=len(needing),
        ),
        manual_seconds=manual_seconds,
        automated_seconds=automated_seconds,
        saved_seconds=saved_seconds,
        estimated_rows=week_time.estimated_rows,
        minutes_saved=round(saved_seconds / 60),
        needs_help=_needs_help(needing[:NEEDS_HELP_ON_HOME]),
        running=[
            RunningEntry(
                run_id=row.run_id,
                task_name=row.task_name,
                done=row.done,
                total=row.total,
                already_there=row.already_there,
            )
            for row in running
        ],
        tasks=_tasks(tasks),
    )


@router.get("/needs-help", response_model=list[NeedsHelpEntry])
def needs_help(tenant_id: Annotated[uuid.UUID | None, Query()] = None) -> list[NeedsHelpEntry]:
    """Every row across all runs that needs a person, newest first."""
    with connection() as conn:
        rows = list_needs_help(conn, tenant_id=tenant_id)
    return _needs_help(rows)


@router.get("/tasks", response_model=list[TaskListEntry])
def tasks(tenant_id: Annotated[uuid.UUID | None, Query()] = None) -> list[TaskListEntry]:
    """Every saved task, newest first, with the site it may use and its newest run.

    PL-041: each row also says whether the task's two most recent finished runs both added nothing
    and stopped for the same reason -- a saved task never changes, so a task recorded before a fix
    keeps failing the same way and the list is where a person notices. The Home glance keeps its
    own five-task shape, so this field is added here and not to ``TaskEntry``.
    """
    with connection() as conn:
        rows = list_tasks(conn, tenant_id=tenant_id, limit=None)
        inputs = inputs_for_versions(conn, [row.recipe_version_id for row in rows])
        examples = examples_for_versions(conn, [row.recipe_version_id for row in rows])
        failing = {
            row.workflow_id: row.reason for row in keeps_failing_tasks(conn, tenant_id=tenant_id)
        }
    by_id = {row.workflow_id: row for row in rows}
    entries: list[TaskListEntry] = []
    for entry in _tasks(rows):
        source = by_id[entry.workflow_id]
        example = (
            examples.get(entry.recipe_version_id, {}).get(source.sign_in_column or "")
            if source.sign_in
            else None
        )
        entries.append(
            TaskListEntry(
                **entry.model_dump(),
                inputs=inputs.get(entry.recipe_version_id, []),
                sign_in=source.sign_in,
                sign_in_column=source.sign_in_column,
                sign_in_email=example,
                keeps_failing=(
                    KeepsFailing(reason=failing[entry.workflow_id])
                    if entry.workflow_id in failing
                    else None
                ),
            )
        )
    return entries


__all__ = ["router"]
