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
from pydantic import BaseModel

from .db import connection
from .operator import require_operator_user
from .recipes import recipe_problems, store_recipe_version

router = APIRouter(prefix="/api/v1", tags=["tasks"])

_INSERT_WORKFLOW = """
INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins, created_by)
VALUES (%s, %s, %s, %s, %s)
RETURNING id
"""


class CreateTaskRequest(BaseModel):
    """What the popup sends: the name the person chose, and the recipe that compiled."""

    name: str
    recipe: dict[str, Any]


class CreateTaskResponse(BaseModel):
    """Where the saved task lives, so the popup can link straight to it."""

    workflow_id: uuid.UUID
    recipe_version_id: uuid.UUID


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
    problems, nothing written). A task name the tenant already uses is a 409, not a 500.
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


__all__ = ["CreateTaskRequest", "CreateTaskResponse", "destination_key_of", "router"]
