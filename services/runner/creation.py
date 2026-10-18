"""Run creation: validate, pin, and lay down the ledger.

Creation is fail-closed. It refuses a run when the recipe does not match the schema, when a step
action is outside the supported set, when an ``end_record`` outcome is impossible on its path,
when the recipe version, the input batch and the workflow do not all belong together, when any
row's key cannot be rendered, or when two ready rows render the same ``record_key``. Every problem
is listed; nothing is written when there is one.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg.rows import dict_row

from packages.compiler.validation import validation_errors
from packages.ledger import batches as ledger_batches
from packages.ledger import create as ledger_create
from packages.ledger.reads import verified_preview_run

from .templates import TemplateError, render

# The actions this runner understands. Executor actions are sent to the browser; the rest are
# runner steps, evaluated here. Anything else is refused at creation (fail closed).
EXECUTOR_ACTIONS = frozenset({"navigate", "fill", "select", "click", "press", "wait_for", "assert"})
RUNNER_ACTIONS = frozenset({"branch", "end_record", "approval_gate"})
SUPPORTED_ACTIONS = EXECUTOR_ACTIONS | RUNNER_ACTIONS

_MODES = frozenset({"preview", "batch"})
_NESTED_KEYS = ("then", "else", "steps")

_SELECT_WORKFLOW = """
SELECT id, tenant_id, destination_key, archived_at FROM workflows WHERE id = %(id)s
"""
_SELECT_WORKFLOW_TENANT = "SELECT tenant_id FROM workflows WHERE id = %(id)s"
_SELECT_RECIPE_VERSION = """
SELECT id, tenant_id, workflow_id, recipe FROM recipe_versions WHERE id = %(id)s
"""


class CreationError(ValueError):
    """A run could not be created. ``problems`` lists every reason, most specific first."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems) if problems else "the run could not be created")
        self.problems = problems


def _walk_steps(
    steps: Any,
    path: str,
    *,
    ran_executor: bool,
    problems: list[str],
) -> bool:
    """Collect problems in a step list; return whether an executor step has run on this path."""
    if not isinstance(steps, list):
        return ran_executor

    for index, step in enumerate(steps):
        where = f"{path}[{index}]"
        if not isinstance(step, dict):
            problems.append(f"{where}: a step must be an object")
            continue
        step_id = step.get("id", "?")
        action = step.get("action")

        if action not in SUPPORTED_ACTIONS:
            problems.append(f"{where} ({step_id}): unsupported step action {action!r}")
            continue

        if step.get("commit") is True and not step.get("assert_after"):
            problems.append(f"{where} ({step_id}): a commit step must carry an assert_after")

        if action == "end_record":
            outcome = step.get("outcome")
            if outcome == "failed":
                problems.append(
                    f"{where} ({step_id}): end_record may not end a record failed; "
                    "failed belongs to the runner (D-017)"
                )
            elif outcome == "verified" and not ran_executor:
                problems.append(f"{where} ({step_id}): end_record verified before any step has run")

        if action == "branch":
            _walk_steps(
                step.get("then", []), f"{where}.then", ran_executor=ran_executor, problems=problems
            )
            _walk_steps(
                step.get("else", []), f"{where}.else", ran_executor=ran_executor, problems=problems
            )
            ran_executor = True  # a branch checks the page
            continue

        ran_executor = True  # every other supported action is a step on the record

    return ran_executor


def step_problems(document: Mapping[str, Any]) -> list[str]:
    """Every reason the recipe's steps are not runnable by this runner."""
    problems: list[str] = []
    _walk_steps(document.get("steps"), "steps", ran_executor=False, problems=problems)
    return problems


def workflow_tenant(conn: psycopg.Connection, workflow_id: uuid.UUID) -> uuid.UUID | None:
    """The tenant a workflow belongs to, or ``None`` when there is no such workflow.

    The API's run gate needs this before creation: an admin's freeze and a tenant's Chrome extension
    floor are facts about the workflow's tenant, and a run is pinned to a workflow. An unknown
    workflow has no tenant to ask about, so the caller falls through to creation, which refuses it
    with the problem it actually has.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_WORKFLOW_TENANT, {"id": workflow_id})
        row = cur.fetchone()
    return row["tenant_id"] if row is not None else None


def _check_consistency(
    workflow: Mapping[str, Any],
    recipe_version: Mapping[str, Any],
    batch: ledger_batches.BatchRowInfo,
) -> list[str]:
    """S5: the version, the batch and the workflow must belong together, under one tenant."""
    problems: list[str] = []
    if recipe_version["workflow_id"] != workflow["id"]:
        problems.append(
            f"recipe version {recipe_version['id']} belongs to workflow "
            f"{recipe_version['workflow_id']}, not {workflow['id']}"
        )
    if batch.workflow_id != workflow["id"]:
        problems.append(
            f"input batch {batch.id} belongs to workflow {batch.workflow_id}, not {workflow['id']}"
        )
    tenants = {workflow["tenant_id"], recipe_version["tenant_id"], batch.tenant_id}
    if len(tenants) != 1:
        problems.append("the workflow, recipe version and input batch are not all one tenant")
    return problems


def _render_keys(
    meta: Mapping[str, Any], rows: list[dict[str, Any]]
) -> tuple[list[tuple[Any, int, str, str]], list[str]]:
    """Render ``record_key`` and ``idempotency_key`` for every row; collect every failure."""
    record_key_template = str(meta.get("record_key", ""))
    idempotency_template = str(meta.get("idempotency_key", ""))
    rendered: list[tuple[Any, int, str, str]] = []
    problems: list[str] = []

    for row in rows:
        clean = row["clean"] if isinstance(row["clean"], dict) else {}
        try:
            record_key = render(record_key_template, row=clean, variables={})
            idempotency_key = render(idempotency_template, row=clean, variables={})
        except TemplateError as exc:
            problems.append(f"input record {row['id']}: {exc}")
            continue
        if not record_key or not idempotency_key:
            problems.append(f"input record {row['id']}: a rendered key is empty")
            continue
        rendered.append((row["id"], row["row_index"], record_key, idempotency_key))

    return rendered, problems


def _duplicate_key_problems(rendered: list[tuple[Any, int, str, str]]) -> list[str]:
    """One problem per ``record_key`` more than one row rendered, naming the key and its rows.

    ``run_records`` is unique on ``(run_id, record_key)``, so inserting these rows would make the
    insert raise ``UniqueViolation`` instead of naming the rows. The check runs before the insert,
    so no run is created.
    """
    rows_by_key: dict[str, list[int]] = {}
    for _input_record_id, row_index, record_key, _idempotency_key in rendered:
        rows_by_key.setdefault(record_key, []).append(row_index)

    return [
        f"duplicate record_key {record_key!r} on rows {', '.join(str(row) for row in indices)}"
        for record_key, indices in rows_by_key.items()
        if len(indices) > 1
    ]


def _values_as_the_recipe_sees_them(
    mapping: dict[str, Any], raw: Any, clean: Any
) -> dict[str, Any]:
    """One row's values as the recipe sees them: the saved mapping applied to the uploaded row.

    With no mapping the values are the row's ``clean`` values, exactly as before. The mapping is
    applied from ``raw``, so applying it twice is the same as applying it once.
    """
    if not mapping:
        return clean if isinstance(clean, dict) else {}
    uploaded = raw if isinstance(raw, dict) else {}
    values: dict[str, Any] = {}
    for recipe_column, spreadsheet_column in mapping.items():
        if not spreadsheet_column:
            continue
        value = uploaded.get(spreadsheet_column)
        values[str(recipe_column)] = "" if value is None else value
    return values


def create_run(
    conn: psycopg.Connection,
    *,
    workflow_id: uuid.UUID,
    recipe_version_id: uuid.UUID,
    input_batch_id: uuid.UUID,
    mode: str = "batch",
    limit: int | None = None,
) -> uuid.UUID:
    """Create a run pinned to one recipe version and one input batch; return its id.

    ``limit`` caps the ready rows the run takes, in the person's row order, so a preview of one row
    (``mode='preview', limit=1``) runs exactly the first row that is ready.

    When the batch carries the mapping a person confirmed, each ready row's values are the recipe's
    own columns, so the keys are rendered from them and the runner -- which reads ``clean`` -- sees
    the recipe's column names. ``raw`` stays exactly as it was uploaded.

    Raises :class:`CreationError` with every problem when the run cannot be created.
    """
    problems: list[str] = []
    if mode not in _MODES:
        problems.append(f"unknown run mode {mode!r}")
        raise CreationError(problems)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_WORKFLOW, {"id": workflow_id})
        workflow = cur.fetchone()
        cur.execute(_SELECT_RECIPE_VERSION, {"id": recipe_version_id})
        recipe_version = cur.fetchone()
    batch = ledger_batches.get_batch(conn, input_batch_id)

    if workflow is None:
        problems.append(f"workflow {workflow_id} does not exist")
    if recipe_version is None:
        problems.append(f"recipe version {recipe_version_id} does not exist")
    if batch is None:
        problems.append(f"input batch {input_batch_id} does not exist")
    if problems:
        raise CreationError(problems)

    assert workflow is not None and recipe_version is not None and batch is not None
    problems.extend(_check_consistency(workflow, recipe_version, batch))
    if problems:
        raise CreationError(problems)

    document = recipe_version["recipe"] if isinstance(recipe_version["recipe"], dict) else {}
    problems.extend(validation_errors(document))
    problems.extend(step_problems(document))
    if problems:
        raise CreationError(problems)

    rows = [
        row for row in ledger_batches.list_batch_rows(conn, input_batch_id) if row.status == "ready"
    ]
    if limit is not None:
        rows = rows[:limit]

    mapping = batch.column_mapping or {}
    values_by_row: dict[uuid.UUID, dict[str, Any]] = {}
    keyed_rows: list[dict[str, Any]] = []
    for row in rows:
        values = _values_as_the_recipe_sees_them(mapping, row.raw, row.clean)
        values_by_row[row.id] = values
        keyed_rows.append({"id": row.id, "row_index": row.row_index, "clean": values})

    meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
    rendered, key_problems = _render_keys(meta, keyed_rows)
    if key_problems:
        raise CreationError(key_problems)

    duplicate_problems = _duplicate_key_problems(rendered)
    if duplicate_problems:
        raise CreationError(duplicate_problems)

    approval_policy = meta.get("approval_policy") or {"commit": "first_record"}
    tenant_id = workflow["tenant_id"]
    # PL-040: "Try one row" is a preview, and a preview always stops for approval. The person who
    # just approved that row and saw it verified then presses "Run the other rows", and asking them
    # to approve the first of those again is the same question twice. The first-record gate is
    # waived only when a preview of this batch, on this exact recipe version, has a verified row.
    if (
        mode == "batch"
        and approval_policy.get("commit") == "first_record"
        and (preview := verified_preview_run(conn, input_batch_id, recipe_version_id)) is not None
    ):
        approval_policy = {"commit": "none", "approved_in_preview": str(preview)}

    with conn.transaction():
        if mapping:
            # The runner reads ``clean``, so the recipe's own column names have to land there
            # inside the same transaction that inserts the run and its records.
            for row in rows:
                ledger_batches.set_row_clean(conn, row.id, values_by_row[row.id])
        run_id = ledger_create.insert_run(
            conn,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            recipe_version_id=recipe_version_id,
            input_batch_id=input_batch_id,
            mode=mode,
            destination_key=workflow["destination_key"],
            approval_policy=approval_policy,
        )
        for input_record_id, _row_index, record_key, idempotency_key in rendered:
            ledger_create.insert_run_record(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                input_record_id=input_record_id,
                record_key=record_key,
                idempotency_key=idempotency_key,
            )

    return run_id
