"""The columns a saved task's pinned recipe version says it needs.

``GET /api/v1/tasks`` lists each task's ``inputs`` and run-once refuses a task that has any, so
both read the same place: ``meta.input_schema.fields`` of the newest recipe version. The
``run_once_id`` placeholder a no-input task is compiled with is not a spreadsheet column, so it is
left out.

The recipe version is read here rather than in ``packages.ledger``: this is a projection of the
task list, not a ledger read, and the ledger's own tables are untouched (the ticket's boundary).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row

# The fresh value a Run once batch carries; the compiled record key repeats it, so every Run once
# is new work rather than the same key being skipped as already done.
RUN_ONCE_FIELD = "run_once_id"

_SELECT_RECIPES = """
SELECT id, recipe FROM recipe_versions WHERE id = ANY(%(ids)s)
"""

_SELECT_LATEST_TASK = """
SELECT rv.id, rv.tenant_id, rv.recipe
FROM workflows w
JOIN recipe_versions rv ON rv.workflow_id = w.id
WHERE w.id = %(workflow)s AND w.archived_at IS NULL
ORDER BY rv.version DESC, rv.id DESC
LIMIT 1
"""


def input_fields(recipe: Any) -> list[str]:
    """The input schema's field names, in recipe order, without the run-once placeholder."""
    if not isinstance(recipe, dict):
        return []
    meta = recipe.get("meta")
    if not isinstance(meta, dict):
        return []
    schema = meta.get("input_schema")
    if not isinstance(schema, dict):
        return []
    fields = schema.get("fields")
    if not isinstance(fields, dict):
        return []
    return [str(name) for name in fields if str(name) != RUN_ONCE_FIELD]


def inputs_for_versions(
    conn: psycopg.Connection, recipe_version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Each pinned recipe version's input columns, keyed by the version's id."""
    if not recipe_version_ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_RECIPES, {"ids": recipe_version_ids})
        return {row["id"]: input_fields(row["recipe"]) for row in cur.fetchall()}


def latest_task_version(conn: psycopg.Connection, workflow_id: uuid.UUID) -> dict[str, Any] | None:
    """A non-archived task's newest recipe version: ``id``, ``tenant_id`` and ``recipe``."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_LATEST_TASK, {"workflow": workflow_id})
        return cur.fetchone()


__all__ = [
    "RUN_ONCE_FIELD",
    "input_fields",
    "inputs_for_versions",
    "latest_task_version",
]
