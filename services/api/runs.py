"""The runs API: create a run, watch it, pause/resume/cancel it, and export its results.

Every read and write goes through :mod:`packages.ledger`, so no SQL that names ``runs`` or
``run_records`` lives here. Every query is scoped by the tenant taken from the run or the record
itself; a client-supplied tenant id is never trusted.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from packages.ledger import (
    IllegalTransition,
    StateConflict,
    dismiss_record,
    get_run,
    list_open_approvals,
    list_runs,
    open_approval_for_run,
    reads,
    request_cancel,
    requeue_record,
    resume_run,
    set_run_status,
)
from services.runner.creation import CreationError, create_run, workflow_tenant

from . import hub
from .admin import require_runs_allowed
from .db import connection
from .operator import require_operator_user

router = APIRouter(prefix="/api/v1", tags=["runs"])

_OPEN_STATUSES = frozenset({"queued", "running", "paused"})

# The runs list is a page, not an export: fifty rows is what a person can take in, and the ledger
# caps the query at the same number.
RUN_LIST_LIMIT = 50


class CreateRunRequest(BaseModel):
    """The three pins a run needs, the mode, and how many rows it may take."""

    workflow_id: uuid.UUID
    recipe_version_id: uuid.UUID
    input_batch_id: uuid.UUID
    mode: Literal["preview", "batch"] = "batch"
    # "Try one row" is a preview of exactly one row: the first row that is ready.
    limit: Annotated[int, Field(ge=1)] | None = None


class CreateRunResponse(BaseModel):
    """The new run's identity."""

    run_id: uuid.UUID
    status: str


class RunCounts(BaseModel):
    total: int = 0
    verified: int = 0
    skipped: int = 0
    parked: int = 0
    failed: int = 0
    in_progress: int = 0


class OpenApproval(BaseModel):
    id: uuid.UUID
    run_record_id: uuid.UUID
    step_id: str
    kind: str
    shown: dict[str, Any]
    created_at: datetime


class RunListEntry(BaseModel):
    """One row of the runs list: what the page shows for a run, newest first."""

    id: uuid.UUID
    status: str
    pause_reason: str | None
    created_at: datetime
    recipe_name: str
    recipe_version: int
    counts: dict[str, int]


class WaitingBehind(BaseModel):
    """PL-041: the run ahead of a queued one on the same app.

    The database allows one running-or-paused run per tenant and destination, so a queued run can
    wait for another task's run. Naming it is the difference between "Queued" forever and a person
    knowing what to do next.
    """

    run_id: uuid.UUID
    task_name: str
    status: str


class RunDetail(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    workflow_id: uuid.UUID
    recipe_version_id: uuid.UUID
    input_batch_id: uuid.UUID | None
    mode: str
    status: str
    pause_reason: str | None
    cancel_requested_at: datetime | None
    # The wall clock, for the results screen's "about N minutes saved". ``started_at`` is null until
    # a worker claims the run; ``finished_at`` is null until it ends.
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # PL-061: the real comparison for one row of this run. All three are null while the run is open
    # or has not finished a row. ``manual_seconds_per_row`` is what the task's own demonstration
    # measured, so it is null for a task that never measured itself; ``automated_seconds_per_row``
    # is the run's wall clock shared across its done rows; ``saved_seconds`` is the whole run's
    # manual time less its wall clock, never below zero.
    manual_seconds_per_row: float | None = None
    automated_seconds_per_row: float | None = None
    saved_seconds: int | None = None
    counts: RunCounts
    open_approval: OpenApproval | None = None
    # Set only while this run is queued and another run on the same app is running or paused.
    waiting_behind: WaitingBehind | None = None


class RecordDetail(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    input_record_id: uuid.UUID
    record_key: str
    idempotency_key: str
    state: str
    reason: str | None
    attempts: int
    max_attempts: int
    commit_attempted: bool
    destination_ref: str | None


class TransitionDetail(BaseModel):
    id: int
    run_record_id: uuid.UUID
    from_state: str
    to_state: str
    reason: str | None
    actor: str | None
    at: datetime


class EnterCodeRequest(BaseModel):
    """PL-068: the one-time code a person read from their email or phone.

    The shape is the whole check: 4-10 letters or digits. The value is never stored, logged or
    recorded, and the answer never echoes it.
    """

    code: Annotated[str, Field(pattern=r"^[0-9A-Za-z]{4,10}$")]


class EnterCodeResult(BaseModel):
    """What the extension did with the code: typed it, or could not find the field."""

    entered: bool
    reason: str


class RequeueRequest(BaseModel):
    """A person re-queues a record. ``destination_checked_by`` records that they checked."""

    reason: str | None = None
    destination_checked_by: uuid.UUID | None = None


class RequeueResponse(BaseModel):
    record_id: uuid.UUID
    state: str


class DismissRequest(BaseModel):
    """A person dismisses a parked record. ``destination_checked_by`` records that they checked."""

    reason: str | None = None
    destination_checked_by: uuid.UUID | None = None


def _fetch_run(conn: Any, run_id: uuid.UUID) -> Any:
    run = get_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} does not exist")
    return run


@router.post("/runs", response_model=CreateRunResponse, status_code=201)
def create_run_endpoint(payload: Annotated[CreateRunRequest, Body()]) -> CreateRunResponse:
    """Create a run pinned to one recipe version and one input batch.

    Fail closed: an unsupported step, an unknown filter, a missing column, a mismatched workflow, or
    a cross-tenant pin is refused with every problem and nothing is written.

    PL-026: before anything is written, the workspace is asked whether a run may start at all -- the
    person's access, an admin's freeze, and the tenant's Chrome extension floor. An unknown workflow
    has no tenant to ask about, so it falls through to the creation's own 422, which names it.
    """
    with connection() as conn:
        tenant_id = workflow_tenant(conn, payload.workflow_id)
        if tenant_id is not None:
            require_runs_allowed(conn, tenant_id)
        try:
            run_id = create_run(
                conn,
                workflow_id=payload.workflow_id,
                recipe_version_id=payload.recipe_version_id,
                input_batch_id=payload.input_batch_id,
                mode=payload.mode,
                limit=payload.limit,
            )
        except CreationError as exc:
            conn.rollback()
            raise HTTPException(
                status_code=422,
                detail={"message": "the run could not be created", "problems": exc.problems},
            ) from exc
        conn.commit()
    return CreateRunResponse(run_id=run_id, status="queued")


@router.get("/runs", response_model=list[RunListEntry])
def list_runs_endpoint() -> list[RunListEntry]:
    """The most recent runs first, capped at 50, each with its recipe and its per-state counts."""
    with connection() as conn:
        rows = list_runs(conn, limit=RUN_LIST_LIMIT)
    return [
        RunListEntry(
            id=row.id,
            status=row.status,
            pause_reason=row.pause_reason,
            created_at=row.created_at,
            recipe_name=row.recipe_name,
            recipe_version=row.recipe_version,
            counts=row.counts,
        )
        for row in rows
    ]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run_endpoint(run_id: uuid.UUID) -> RunDetail:
    """A run's status, pause reason, counts, any open approval request, and what it waits behind."""
    with connection() as conn:
        run = _fetch_run(conn, run_id)
        summary = reads.run_summary(conn, run_id)
        run_time = reads.run_time(conn, run_id)
        approval = open_approval_for_run(conn, run_id)
        blocking = reads.run_blocking(conn, run_id)

    counts = RunCounts(
        total=summary.total if summary else 0,
        verified=summary.verified if summary else 0,
        skipped=summary.skipped if summary else 0,
        parked=summary.parked if summary else 0,
        failed=summary.failed if summary else 0,
        in_progress=summary.in_progress if summary else 0,
    )
    manual_per_row: float | None = None
    automated_per_row: float | None = None
    saved_seconds: int | None = None
    if run_time is not None and run.status not in _OPEN_STATUSES and run_time.done > 0:
        manual_per_row = run_time.manual_seconds_per_row
        if run_time.automated_seconds is not None:
            automated_per_row = round(run_time.automated_seconds / run_time.done, 1)
            if manual_per_row is not None:
                saved_seconds = max(
                    0, round(run_time.done * manual_per_row - run_time.automated_seconds)
                )
    return RunDetail(
        id=run.id,
        tenant_id=run.tenant_id,
        workflow_id=run.workflow_id,
        recipe_version_id=run.recipe_version_id,
        input_batch_id=run.input_batch_id,
        mode=run.mode,
        status=run.status,
        pause_reason=run.pause_reason,
        cancel_requested_at=run.cancel_requested_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        manual_seconds_per_row=manual_per_row,
        automated_seconds_per_row=automated_per_row,
        saved_seconds=saved_seconds,
        counts=counts,
        open_approval=(
            OpenApproval(
                id=approval.id,
                run_record_id=approval.run_record_id,
                step_id=approval.step_id,
                kind=approval.kind,
                shown=approval.shown,
                created_at=approval.created_at,
            )
            if approval
            else None
        ),
        waiting_behind=(
            WaitingBehind(
                run_id=blocking.run_id,
                task_name=blocking.task_name,
                status=blocking.status,
            )
            if blocking
            else None
        ),
    )


@router.get("/runs/{run_id}/records", response_model=list[RecordDetail])
def list_records_endpoint(run_id: uuid.UUID) -> list[RecordDetail]:
    """Per-record state, reason, attempts, and whether the record entered the commit window."""
    with connection() as conn:
        _fetch_run(conn, run_id)
        records = reads.list_run_records(conn, run_id)
    return [
        RecordDetail(
            id=record.id,
            run_id=record.run_id,
            input_record_id=record.input_record_id,
            record_key=record.record_key,
            idempotency_key=record.idempotency_key,
            state=record.state,
            reason=record.reason,
            attempts=record.attempts,
            max_attempts=record.max_attempts,
            commit_attempted=record.commit_attempted,
            destination_ref=record.destination_ref,
        )
        for record in records
    ]


@router.get("/runs/{run_id}/approvals", response_model=list[OpenApproval])
def list_approvals_endpoint(run_id: uuid.UUID) -> list[OpenApproval]:
    """The run's approval requests that no one has decided yet, with what each one asks."""
    with connection() as conn:
        _fetch_run(conn, run_id)
        requests = list_open_approvals(conn, run_id)
    return [
        OpenApproval(
            id=row.id,
            run_record_id=row.run_record_id,
            step_id=row.step_id,
            kind=row.kind,
            shown=row.shown,
            created_at=row.created_at,
        )
        for row in requests
    ]


@router.get("/runs/{run_id}/transitions", response_model=list[TransitionDetail])
def list_transitions_endpoint(
    run_id: uuid.UUID, after: Annotated[int, Query(ge=0)] = 0
) -> list[TransitionDetail]:
    """The audit feed the dashboard polls, after a cursor id."""
    with connection() as conn:
        _fetch_run(conn, run_id)
        transitions = reads.list_transitions(conn, run_id, after_id=after)
    return [
        TransitionDetail(
            id=row.id,
            run_record_id=row.run_record_id,
            from_state=row.from_state,
            to_state=row.to_state,
            reason=row.reason,
            actor=row.actor,
            at=row.at,
        )
        for row in transitions
    ]


@router.post("/runs/{run_id}/pause", response_model=RunDetail)
def pause_run_endpoint(run_id: uuid.UUID) -> RunDetail:
    """Ask a run to pause at its next step boundary.

    PL-039: ``runs_one_active_per_destination`` covers ``running`` and ``paused``, so pausing a
    queued run while another run of a task on this app is already paused violates the index. That
    is a conflict the person can resolve, not a server error, so it is a 409 in plain words.
    """
    with connection() as conn:
        run = _fetch_run(conn, run_id)
        if run.status not in _OPEN_STATUSES:
            raise HTTPException(status_code=409, detail=f"run {run_id} is {run.status}")
        try:
            set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")
        except psycopg.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail=(
                    "Another run of a task on this app is already paused. "
                    "Finish or cancel that one first."
                ),
            ) from exc
        conn.commit()
    return get_run_endpoint(run_id)


@router.post("/runs/{run_id}/resume", response_model=RunDetail)
def resume_run_endpoint(run_id: uuid.UUID) -> RunDetail:
    """Resume a paused run.

    PL-026: the same gate as starting one -- a person whose access is off, a frozen workspace, or an
    extension below the floor all refuse here, and unfreezing never resumes a run by itself.
    """
    with connection() as conn:
        run = _fetch_run(conn, run_id)
        require_runs_allowed(conn, run.tenant_id)
        if not resume_run(conn, run_id):
            raise HTTPException(status_code=409, detail=f"run {run_id} is {run.status}, not paused")
        conn.commit()
    return get_run_endpoint(run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunDetail)
def cancel_run_endpoint(run_id: uuid.UUID) -> RunDetail:
    """Ask a run to stop. A worker reconciles the commit window before the run ends."""
    with connection() as conn:
        run = _fetch_run(conn, run_id)
        if run.status not in _OPEN_STATUSES:
            raise HTTPException(status_code=409, detail=f"run {run_id} is {run.status}")
        request_cancel(conn, run_id)
        conn.commit()
    return get_run_endpoint(run_id)


@router.post("/runs/{run_id}/code", response_model=EnterCodeResult)
async def enter_code_endpoint(run_id: uuid.UUID, payload: EnterCodeRequest) -> EnterCodeResult:
    """PL-068: hand a person's verification code to the paused run's work window, once.

    Only a run paused waiting for a code accepts one, and only a person in that run's tenant may
    send it. The code goes over the session's socket and nowhere else: no database write, no log
    line, and no echo in the answer.
    """
    with connection() as conn:
        run = _fetch_run(conn, run_id)
        person = require_operator_user(conn)
        if person is None or person.tenant_id != run.tenant_id:
            raise HTTPException(status_code=404, detail=f"run {run_id} does not exist")
        if run.status != "paused" or run.pause_reason != "code_required":
            raise HTTPException(
                status_code=409,
                detail=f"run {run_id} is {run.status}, not waiting for a verification code",
            )

    # The run's own lease epoch: the extension refuses a command older than the newest it has seen
    # for this run, so a code sent with a made-up epoch would be refused as stale.
    outcome = await hub.send_enter_code(payload.code, run_id=run_id, lease_epoch=run.lease_epoch)
    if outcome.get("reason") == "no_extension":
        raise HTTPException(status_code=503, detail="the work window is not connected")
    return EnterCodeResult(
        entered=bool(outcome.get("entered")),
        reason=str(outcome.get("reason") or "not_found"),
    )


@router.post("/records/{record_id}/requeue", response_model=RequeueResponse)
def requeue_record_endpoint(
    record_id: uuid.UUID, payload: Annotated[RequeueRequest, Body()] | None = None
) -> RequeueResponse:
    """Re-queue a parked or failed record, choosing the state from ``commit_attempted``."""
    body = payload or RequeueRequest()
    with connection() as conn:
        if reads.run_of_record(conn, record_id) is None:
            raise HTTPException(status_code=404, detail=f"record {record_id} does not exist")
        try:
            state = requeue_record(
                conn,
                record_id,
                reason=body.reason or "re-queued by a person",
                destination_checked_by=body.destination_checked_by,
            )
        except StateConflict as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        conn.commit()
    return RequeueResponse(record_id=record_id, state=state)


@router.post("/records/{record_id}/dismiss", response_model=RequeueResponse)
def dismiss_record_endpoint(
    record_id: uuid.UUID, payload: Annotated[DismissRequest, Body()] | None = None
) -> RequeueResponse:
    """Dismiss a parked record as ``skipped``.

    A record that never reached a commit goes straight to ``skipped``. A commit-attempted record
    needs ``destination_checked_by``, and the same update records the fresh destination check; the
    database trigger (I26) is the authority, and its refusal is the 409 here.
    """
    body = payload or DismissRequest()
    with connection() as conn:
        if reads.run_of_record(conn, record_id) is None:
            raise HTTPException(status_code=404, detail=f"record {record_id} does not exist")
        try:
            state = dismiss_record(
                conn,
                record_id,
                reason=body.reason or "dismissed by a person",
                destination_checked_by=body.destination_checked_by,
            )
        except (StateConflict, IllegalTransition) as exc:
            # The trigger is the authority on a commit-attempted record, and it refuses with a
            # guard exception (``IllegalTransition``) rather than a missing row; a record that is
            # not parked at all raises ``StateConflict``. Both are a 409: the request is well-formed
            # but the ledger will not make this move.
            conn.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        conn.commit()
    return RequeueResponse(record_id=record_id, state=state)


@router.get("/runs/{run_id}/results.csv", response_class=PlainTextResponse)
def results_csv_endpoint(run_id: uuid.UUID) -> PlainTextResponse:
    """One row per ledger record, plus the excluded input rows with their issues."""
    with connection() as conn:
        _fetch_run(conn, run_id)
        rows = reads.results_rows(conn, run_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["row_kind", "record_key", "state", "reason", "attempts", "destination_ref", "issues"]
    )
    for row in rows:
        writer.writerow(
            [
                row.kind,
                row.record_key if row.record_key is not None else "",
                row.state,
                row.reason if row.reason is not None else "",
                row.attempts if row.attempts is not None else "",
                row.destination_ref if row.destination_ref is not None else "",
                "; ".join(str(item) for item in row.issues),
            ]
        )
    return PlainTextResponse(buffer.getvalue(), media_type="text/csv")


__all__ = ["router"]
