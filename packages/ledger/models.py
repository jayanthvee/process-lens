"""Frozen rows the ledger returns.

Every ledger function returns one of these instead of a raw tuple, so a caller cannot read a column
by a stale index and the type is checkable. ``_row`` maps a ``dict_row`` into the dataclass,
ignoring columns the dataclass does not declare.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _row[T](cls: type[T], row: dict[str, Any]) -> T:
    names = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    return cls(**{key: row[key] for key in names})  # type: ignore[call-arg]


@dataclass(frozen=True)
class RunClaim:
    """A run row, as the worker needs it."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    workflow_id: uuid.UUID
    recipe_version_id: uuid.UUID
    input_batch_id: uuid.UUID | None
    mode: str
    status: str
    pause_reason: str | None
    approval_policy: dict[str, Any]
    destination_key: str
    worker_id: str | None
    lease_epoch: int
    cancel_requested_at: datetime | None
    # The two clocks the results screen needs for a finished run's time saved. They arrive with the
    # rest of the row (``get_run`` reads ``SELECT *``), and they default so a test that builds a run
    # by hand does not have to invent them.
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class RecordClaim:
    """A ``run_records`` row, as the worker needs it."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    input_record_id: uuid.UUID
    record_key: str
    idempotency_key: str
    state: str
    attempts: int
    max_attempts: int
    commit_attempted: bool
    reason: str | None
    destination_ref: str | None
    claimed_by: str | None


@dataclass(frozen=True)
class VerifyResult:
    """The outcome of a verify transaction."""

    key_inserted: bool
    destination_ref: str | None


@dataclass(frozen=True)
class RecoveryCounts:
    """What a recovery pass moved."""

    reconciled: int = 0
    released: int = 0


@dataclass(frozen=True)
class ApprovalRequestRow:
    """One row of ``approval_requests``."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    run_record_id: uuid.UUID
    step_id: str
    kind: str
    shown: dict[str, Any]
    created_at: datetime
    resolved_by_approval_id: uuid.UUID | None


@dataclass(frozen=True)
class ApprovalDecision:
    """A decision, with the values it answered."""

    shown: dict[str, Any]
    decision: str
    reason: str | None
    decided_at: datetime


@dataclass(frozen=True)
class JobClaim:
    """A claimed background job."""

    id: int
    tenant_id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


@dataclass(frozen=True)
class RunSummaryRow:
    """One row of the ``run_summary`` view."""

    run_id: uuid.UUID
    total: int
    verified: int
    skipped: int
    parked: int
    failed: int
    in_progress: int


@dataclass(frozen=True)
class RunListRow:
    """One row of the runs list."""

    id: uuid.UUID
    status: str
    pause_reason: str | None
    created_at: datetime
    recipe_name: str
    recipe_version: int
    counts: dict[str, int]


@dataclass(frozen=True)
class TransitionRow:
    """One row of ``run_record_transitions``: the audit feed."""

    id: int
    run_record_id: uuid.UUID
    from_state: str
    to_state: str
    reason: str | None
    actor: str | None
    at: datetime


@dataclass(frozen=True)
class ResultsRow:
    """One row of the results export."""

    kind: str  # 'ledger' or 'excluded'
    record_key: str
    state: str
    reason: str | None
    attempts: int | None
    destination_ref: str | None
    issues: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class WeekCountsRow:
    """The week's finished rows, counted from the append-only audit table."""

    done: int
    already_there: int


@dataclass(frozen=True)
class NeedsHelpRow:
    """One row that needs a person, with the task it came from and its plain reason."""

    record_id: uuid.UUID
    run_id: uuid.UUID
    task_name: str
    row: str
    state: str
    reason: str | None
    commit_attempted: bool


@dataclass(frozen=True)
class RunningRunRow:
    """One run that is still open, with the progress the page shows."""

    run_id: uuid.UUID
    task_name: str
    done: int
    total: int
    already_there: int


@dataclass(frozen=True)
class TaskListRow:
    """One saved task: its latest version, the site it may use, and its newest run."""

    workflow_id: uuid.UUID
    recipe_version_id: uuid.UUID
    name: str
    app: str | None
    last_run_id: uuid.UUID | None
    last_run_status: str | None
    last_run_started_at: datetime | None
    last_run_counts: dict[str, int]
