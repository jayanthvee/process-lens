"""Recipe storage and ingestion endpoints.

A recipe is stored as one immutable row in ``recipe_versions``, under its
workflow — that is the table the schema defines for recipes, and its trigger
rejects UPDATE and DELETE so a stored version can never be rewritten. The
"recipes" a caller lists and fetches are therefore recipe versions: the latest
version of each non-archived workflow, or one pinned version by id.

Ingest is fail-closed. A document that does not match the recipe schema, or a
commit step that does not carry an ``assert_after``, is rejected with every
problem listed and nothing is written.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from packages.compiler.validation import validation_errors

from .db import connection

router = APIRouter(prefix="/api/v1", tags=["recipes"])

# The step fields that can hold a nested step list. A commit step inside a branch
# or a loop carries an assertion just like a top-level one.
NESTED_STEP_KEYS = ("then", "else", "steps")

DEFAULT_CHANGE_REASON = "compiled from recording"


class RecipeVersionSummary(BaseModel):
    """A stored recipe version, without the recipe document."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_name: str
    version: int
    format_version: int
    name: str
    record_key: str
    idempotency_key: str
    change_reason: str
    created_at: datetime


class RecipeVersionDetail(RecipeVersionSummary):
    """A stored recipe version, including the recipe document itself."""

    recipe: dict[str, Any]


def commit_assertion_problems(document: Any) -> list[str]:
    """Every commit step that is missing an ``assert_after``.

    The schema already forbids a commit without an assertion. This check states
    the same rule with a precise message, and it walks into branches and loops,
    which is where a missing assertion is easiest to miss.
    """

    problems: list[str] = []

    def walk(steps: Any, path: str) -> None:
        if not isinstance(steps, list):
            return
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            where = f"{path}[{index}]"
            if step.get("commit") is True and not step.get("assert_after"):
                problems.append(
                    f"{where} ({step.get('id', '?')}): a commit step must carry an assert_after"
                )
            for key in NESTED_STEP_KEYS:
                walk(step.get(key), f"{where}.{key}")

    if isinstance(document, dict):
        walk(document.get("steps"), "steps")
    return problems


def recipe_problems(document: Any) -> list[str]:
    """Every reason to refuse this document, most specific first."""
    problems = list(validation_errors(document))
    problems.extend(commit_assertion_problems(document))
    return problems


def _meta_of(document: dict[str, Any]) -> dict[str, Any]:
    meta = document.get("meta")
    return meta if isinstance(meta, dict) else {}


def _summary(row: dict[str, Any], document: dict[str, Any]) -> RecipeVersionSummary:
    meta = _meta_of(document)
    return RecipeVersionSummary(
        id=row["id"],
        tenant_id=row["tenant_id"],
        workflow_id=row["workflow_id"],
        workflow_name=row["workflow_name"],
        version=row["version"],
        format_version=row["format_version"],
        name=str(meta.get("name", "")),
        record_key=str(meta.get("record_key", "")),
        idempotency_key=str(meta.get("idempotency_key", "")),
        change_reason=row["change_reason"],
        created_at=row["created_at"],
    )


# One statement: lock the workflow, then take the next version number for it.
# The lock serializes two ingests of the same workflow, so they cannot both claim
# version n; the UNIQUE (workflow_id, version) constraint is the backstop. An
# archived or missing workflow matches nothing, so nothing is inserted.
_INSERT_RECIPE_VERSION = """
WITH locked AS (
  SELECT id, tenant_id, name
  FROM workflows
  WHERE id = %s AND archived_at IS NULL
  FOR UPDATE
)
INSERT INTO recipe_versions
  (tenant_id, workflow_id, version, format_version, recipe, change_reason)
SELECT
  locked.tenant_id,
  locked.id,
  COALESCE((SELECT max(version) FROM recipe_versions WHERE workflow_id = locked.id), 0) + 1,
  %s,
  %s,
  %s
FROM locked
RETURNING
  id,
  tenant_id,
  workflow_id,
  (SELECT name FROM workflows WHERE id = workflow_id) AS workflow_name,
  version,
  format_version,
  change_reason,
  created_at
"""

_SELECT_LATEST_PER_WORKFLOW = """
SELECT DISTINCT ON (rv.workflow_id)
  rv.id,
  rv.tenant_id,
  rv.workflow_id,
  rv.version,
  rv.format_version,
  rv.recipe,
  rv.change_reason,
  rv.created_at,
  w.name AS workflow_name
FROM recipe_versions rv
JOIN workflows w ON w.id = rv.workflow_id
WHERE w.archived_at IS NULL
  AND (%s::uuid IS NULL OR rv.tenant_id = %s::uuid)
  AND (%s::uuid IS NULL OR rv.workflow_id = %s::uuid)
ORDER BY rv.workflow_id, rv.version DESC
"""

_SELECT_BY_ID = """
SELECT
  rv.id,
  rv.tenant_id,
  rv.workflow_id,
  rv.version,
  rv.format_version,
  rv.recipe,
  rv.change_reason,
  rv.created_at,
  w.name AS workflow_name
FROM recipe_versions rv
JOIN workflows w ON w.id = rv.workflow_id
WHERE rv.id = %s
"""


@router.post("/recipes", response_model=RecipeVersionDetail, status_code=201)
def ingest_recipe(
    workflow_id: Annotated[uuid.UUID, Query(description="The workflow this recipe belongs to.")],
    document: Annotated[dict[str, Any], Body(description="The compiled recipe document.")],
    change_reason: Annotated[str, Query(max_length=500)] = DEFAULT_CHANGE_REASON,
) -> RecipeVersionDetail:
    """Store a compiled recipe as the next immutable version of its workflow.

    Fail-closed: a document that does not match the recipe schema, or a commit
    step without an ``assert_after``, is rejected with every problem and nothing
    is written.
    """
    problems = recipe_problems(document)
    if problems:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "the recipe does not match the recipe schema",
                "problems": problems,
            },
        )

    format_version = document.get("format_version", 1)
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _INSERT_RECIPE_VERSION,
                (workflow_id, format_version, Jsonb(document), change_reason),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"workflow {workflow_id} does not exist or is archived",
        )

    columns = (
        "id",
        "tenant_id",
        "workflow_id",
        "workflow_name",
        "version",
        "format_version",
        "change_reason",
        "created_at",
    )
    stored = dict(zip(columns, row, strict=True))
    return RecipeVersionDetail(**_summary(stored, document).model_dump(), recipe=document)


@router.get("/recipes", response_model=list[RecipeVersionSummary])
def list_recipes(
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    workflow_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[RecipeVersionSummary]:
    """List the active recipes: the latest version of every non-archived workflow."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            _SELECT_LATEST_PER_WORKFLOW,
            (tenant_id, tenant_id, workflow_id, workflow_id),
        )
        rows = cur.fetchall()

    summaries: list[RecipeVersionSummary] = []
    for row in rows:
        document = row[5] if isinstance(row[5], dict) else {}
        stored = {
            "id": row[0],
            "tenant_id": row[1],
            "workflow_id": row[2],
            "workflow_name": row[8],
            "version": row[3],
            "format_version": row[4],
            "change_reason": row[6],
            "created_at": row[7],
        }
        summaries.append(_summary(stored, document))
    return summaries


@router.get("/recipes/{recipe_id}", response_model=RecipeVersionDetail)
def get_recipe(recipe_id: uuid.UUID) -> RecipeVersionDetail:
    """Retrieve one recipe version by id, with its pinned version number."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(_SELECT_BY_ID, (recipe_id,))
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"recipe {recipe_id} does not exist")

    document = row[5] if isinstance(row[5], dict) else {}
    stored = {
        "id": row[0],
        "tenant_id": row[1],
        "workflow_id": row[2],
        "workflow_name": row[8],
        "version": row[3],
        "format_version": row[4],
        "change_reason": row[6],
        "created_at": row[7],
    }
    return RecipeVersionDetail(**_summary(stored, document).model_dump(), recipe=document)
