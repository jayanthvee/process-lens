"""The Home screen's own reads: the week's counts, the time saved, the rows that need a person,
the runs that are still going, and the saved tasks.

Every query lives in ``packages.ledger.reads`` (the separation rule,
``tests/runner/test_sql_separation.py``); this module only calls those functions and shapes the
JSON, so it writes no ledger statement of its own.

The week's counts and the minutes saved come from the append-only audit table
``run_record_transitions`` -- one row per state change, written by the database trigger, not by
this API. A row is "done" when it entered ``verified`` inside the rolling last seven days and
"already there" when it entered ``skipped``; neither is inferred from a run's own status.

Minutes saved is three minutes for every done row, minus the wall clock the window's runs took,
clamped at zero and reported in whole minutes.

Three routes are frozen by PL-023: the whole week, the rows that need a person, and the saved
tasks. All three take the optional ``tenant_id`` filter ``/api/v1/recipes`` takes, so a test can
scope itself in a shared session database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from packages.ledger import (
    NeedsHelpRow,
    TaskListRow,
    list_needs_help,
    list_running_runs,
    list_tasks,
    week_counts,
    window_run_seconds,
)

from .db import connection

router = APIRouter(prefix="/api/v1", tags=["home"])

# The week is the rolling last seven days.
HOME_WINDOW_DAYS = 7
# What one row costs a person by hand.
MINUTES_PER_ROW = 3
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


class RunningEntry(BaseModel):
    """One run that is still going, with its progress."""

    run_id: uuid.UUID
    task_name: str
    done: int
    total: int
    already_there: int


class TaskLastRun(BaseModel):
    """A task's newest run, or nothing at all when it has never run."""

    run_id: uuid.UUID
    status: str
    started_at: datetime | None
    counts: dict[str, int]


class TaskEntry(BaseModel):
    """One saved task: its name, the site it may use, and its newest run."""

    workflow_id: uuid.UUID
    recipe_version_id: uuid.UUID
    name: str
    app: str | None
    last_run: TaskLastRun | None


class HomeSummary(BaseModel):
    """`GET /api/v1/home`: the whole week at a glance."""

    window_days: int
    counts: HomeCounts
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
        seconds = window_run_seconds(conn, days=HOME_WINDOW_DAYS, tenant_id=tenant_id)
        needing = list_needs_help(conn, tenant_id=tenant_id)
        running = list_running_runs(conn, tenant_id=tenant_id)
        tasks = list_tasks(conn, tenant_id=tenant_id, limit=TASKS_ON_HOME)

    minutes_saved = max(0, round(counts.done * MINUTES_PER_ROW - seconds / 60))
    return HomeSummary(
        window_days=HOME_WINDOW_DAYS,
        counts=HomeCounts(
            done=counts.done,
            already_there=counts.already_there,
            # The count is the whole set, not the three the panel shows: the sidebar and the
            # needs-help page read the same set.
            needs_help=len(needing),
        ),
        minutes_saved=minutes_saved,
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


@router.get("/tasks", response_model=list[TaskEntry])
def tasks(tenant_id: Annotated[uuid.UUID | None, Query()] = None) -> list[TaskEntry]:
    """Every saved task, newest first, with the site it may use and its newest run."""
    with connection() as conn:
        rows = list_tasks(conn, tenant_id=tenant_id, limit=None)
    return _tasks(rows)


__all__ = ["router"]
