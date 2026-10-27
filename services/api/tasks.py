"""Create a task from a compiled recipe: the route the popup's "Save as task" calls.

Compiling (``POST /api/v1/recipes/compile``) stores nothing; this route is the
explicit keep. The popup posts the recipe it just compiled together with the
name the person chose, and the task then shows up on the dashboard -- no test
harness, no SQL by hand.

The task belongs to the configured operator (``PROCESSLENS_OPERATOR_ID``): their
tenant owns the workflow and they are recorded as its creator. Validation and
storage are the code ``POST /api/v1/recipes`` uses, reused rather than copied:
the same problems list on a refusal, the same immutable version row on success.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from urllib.parse import urlsplit

import psycopg
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from packages.ledger import BatchRow, insert_batch
from services.runner.creation import CreationError, create_run

from .admin import require_runs_allowed
from .db import connection
from .operator import require_operator_user
from .recipes import recipe_problems, store_recipe_version
from .task_columns import RUN_ONCE_FIELD, input_fields, latest_task_version

router = APIRouter(prefix="/api/v1", tags=["tasks"])

_INSERT_WORKFLOW = """
INSERT INTO workflows
  (tenant_id, name, destination_key, allowed_origins, created_by, sign_in, sign_in_column)
VALUES (%s, %s, %s, %s, %s, %s, %s)
RETURNING id
"""

_UPDATE_WORKFLOW_SIGN_IN = """
UPDATE workflows SET sign_in = %s, sign_in_column = %s WHERE id = %s
"""


class CreateTaskRequest(BaseModel):
    """What the popup sends: the name the person chose, and the recipe that compiled.

    PL-112: a recording that reached a page asking for an emailed code is a sign-in task. The
    marker and the column its email was typed into ride with the save; the taught email itself is
    already the recipe's own example, so the server keeps no second copy.
    """

    name: str
    recipe: dict[str, Any]
    sign_in: bool = False
    sign_in_column: str | None = None


class CreateTaskResponse(BaseModel):
    """Where the saved task lives, so the popup can link straight to it."""

    workflow_id: uuid.UUID
    recipe_version_id: uuid.UUID


class RunOnceRequest(BaseModel):
    """The Chrome that should drive this one-row run, and optionally the row itself.

    PL-112: a sign-in task's row is one picked email, placed in the column the recording typed it
    into. A task with inputs still needs a spreadsheet when no row is supplied.
    """

    browser_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")] | None = None
    values: dict[str, Any] | None = None


class RunOnceResponse(BaseModel):
    """The new run's identity, the same shape ``POST /api/v1/runs`` answers."""

    run_id: uuid.UUID
    status: str


NEEDS_A_SPREADSHEET = "This task needs a spreadsheet."
# PL-112, T1 review: a run-once row's record key is a fresh id, so it can never repeat and the
# committed-keys guard has nothing to compare. Only a task that saves nothing (a sign-in) may be
# run that way, and then only with the one address its own column holds.
VALUES_NOT_FOR_THIS_TASK = "This task runs from a spreadsheet, not from one row."
ONE_EMAIL_ONLY = "Run this task with one email address, in its own field."
EMAIL_REQUIRED = "Type the email this run should use."
EMAIL_TOO_LONG = "That email is too long to use here."
# The recipe schema's own limit on an input field's example, and far more than an address needs.
EMAIL_MAX_LENGTH = 320


def destination_key_of(origins: list[str]) -> str:
    """The host of the first origin: the system this task writes to.

    The schema only admits origins of the form ``scheme://host[:port]`` with no path, so the host
    is always there; the fallback keeps a hand-made document honest rather than guessing.
    """
    host = urlsplit(origins[0]).hostname
    return host if host else origins[0]


@router.post("/tasks", response_model=CreateTaskResponse, status_code=201)
def create_task(payload: Annotated[CreateTaskRequest, Body()]) -> CreateTaskResponse:
    """Save a compiled recipe as a new task: one workflow and its version 1, in one transaction.

    The operator is who is asking (503 when none is configured, 403 when the id names nobody), and
    the recipe is checked with the same code ``POST /api/v1/recipes`` uses (422 with the same
    problems, nothing written). Saving under a name the tenant already uses is teaching that task
    again: the recipe becomes its next version, and earlier versions stay as they were.
    """
    document = payload.recipe
    with connection() as conn:
        person = require_operator_user(conn)
        problems = recipe_problems(document)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "the recipe does not match the recipe schema",
                    "problems": problems,
                },
            )
        # Validation passed, so the schema guarantees a non-empty list of scheme://host origins.
        origins = [str(origin) for origin in document["meta"]["allowed_origins"]]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM workflows WHERE tenant_id = %s AND name = %s "
                "AND archived_at IS NULL",
                (person.tenant_id, payload.name),
            )
            existing = cur.fetchone()
        if existing is not None:
            workflow_id = uuid.UUID(str(existing[0]))
            stored = store_recipe_version(conn, workflow_id=workflow_id, document=document)
            if stored is None:  # pragma: no cover - the row was read in this same connection
                raise HTTPException(status_code=404, detail=f"workflow {workflow_id} is gone")
            # A re-teach is the task's current shape: a task taught again without a code page is
            # no longer a sign-in task.
            with conn.cursor() as cur:
                cur.execute(
                    _UPDATE_WORKFLOW_SIGN_IN,
                    (payload.sign_in, payload.sign_in_column, workflow_id),
                )
            conn.commit()
            return CreateTaskResponse(workflow_id=workflow_id, recipe_version_id=stored["id"])
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        _INSERT_WORKFLOW,
                        (
                            person.tenant_id,
                            payload.name,
                            destination_key_of(origins),
                            origins,
                            person.id,
                            payload.sign_in,
                            payload.sign_in_column,
                        ),
                    )
                    workflow_id = uuid.UUID(str(cur.fetchone()[0]))
                stored = store_recipe_version(conn, workflow_id=workflow_id, document=document)
        except psycopg.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(
                status_code=409, detail=f"a task named {payload.name!r} already exists"
            ) from exc
        conn.commit()

    if stored is None:  # pragma: no cover - the workflow was created in this same transaction
        raise HTTPException(status_code=404, detail=f"workflow {workflow_id} does not exist")
    return CreateTaskResponse(workflow_id=workflow_id, recipe_version_id=stored["id"])


def sign_in_row(task: dict[str, Any], values: dict[str, Any] | None) -> dict[str, str]:
    """The one cell a sign-in task's run-once row may carry; empty when no values were sent.

    A run-once row is keyed by a fresh ``run_once_id``, so its key never repeats. That is right
    only for a task that saves nothing: for a task that commits it would bypass the
    ``committed_keys`` duplicate guard, and a spreadsheet row's own key would never be consulted.
    So ``values`` is refused for every task the person did not teach through a code page, and a
    sign-in task accepts exactly its own column and one non-empty address.
    """
    if not values:
        return {}
    if task.get("sign_in") is not True:
        raise HTTPException(status_code=422, detail=VALUES_NOT_FOR_THIS_TASK)
    column = task.get("sign_in_column")
    if not isinstance(column, str) or column == "" or set(values) != {column}:
        raise HTTPException(status_code=422, detail=ONE_EMAIL_ONLY)
    email = values[column]
    if not isinstance(email, str) or email.strip() == "":
        raise HTTPException(status_code=422, detail=EMAIL_REQUIRED)
    trimmed = email.strip()
    if len(trimmed) > EMAIL_MAX_LENGTH:
        raise HTTPException(status_code=422, detail=EMAIL_TOO_LONG)
    return {column: trimmed}


@router.post("/tasks/{workflow_id}/run-once", response_model=RunOnceResponse, status_code=201)
def run_task_once(
    workflow_id: uuid.UUID, payload: Annotated[RunOnceRequest, Body()] | None = None
) -> RunOnceResponse:
    """Run a task that needs no spreadsheet, once, on a fresh one-row batch.

    A run needs an input batch and a batch needs at least one row, but a task with no recorded
    inputs has no columns to fill. The row carries one fresh ``run_once_id``, and the task's own
    record key repeats it, so two Run onces are two pieces of work rather than the same key
    skipped as already done.

    A task whose pinned recipe version asks for spreadsheet columns is refused rather than handed
    an empty row: it belongs in the normal run flow (PL-105). The one exception is a sign-in task
    (PL-112), which may be given the single address it signs in with; see :func:`sign_in_row`.
    """
    body = payload or RunOnceRequest()
    with connection() as conn:
        task = latest_task_version(conn, workflow_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"task {workflow_id} does not exist")
        fields = input_fields(task["recipe"])
        row_values = sign_in_row(task, body.values)
        if fields and not row_values:
            raise HTTPException(status_code=422, detail=NEEDS_A_SPREADSHEET)

        require_runs_allowed(conn, task["tenant_id"])
        run_once_id = uuid.uuid4()
        row = {**row_values, RUN_ONCE_FIELD: str(run_once_id)}
        try:
            with conn.transaction():
                batch_id = insert_batch(
                    conn,
                    tenant_id=task["tenant_id"],
                    workflow_id=workflow_id,
                    kind="manual",
                    rows=[
                        BatchRow(
                            row_index=1,
                            raw=row,
                            clean=row,
                            record_key=str(run_once_id),
                        )
                    ],
                )
                run_id = create_run(
                    conn,
                    workflow_id=workflow_id,
                    recipe_version_id=task["id"],
                    input_batch_id=batch_id,
                    mode="batch",
                    limit=1,
                    browser_id=body.browser_id,
                )
        except CreationError as exc:
            conn.rollback()
            raise HTTPException(
                status_code=422,
                detail={"message": "the run could not be created", "problems": exc.problems},
            ) from exc
        conn.commit()
    return RunOnceResponse(run_id=run_id, status="queued")


__all__ = [
    "CreateTaskRequest",
    "CreateTaskResponse",
    "EMAIL_MAX_LENGTH",
    "EMAIL_REQUIRED",
    "EMAIL_TOO_LONG",
    "NEEDS_A_SPREADSHEET",
    "ONE_EMAIL_ONLY",
    "RunOnceRequest",
    "RunOnceResponse",
    "VALUES_NOT_FOR_THIS_TASK",
    "destination_key_of",
    "router",
    "sign_in_row",
]
