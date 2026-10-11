"""Run creation: validate, pin, and lay down the ledger.

Creation is fail-closed. It refuses a run when the recipe does not match the schema, when a step
action is outside the supported set, when an ``end_record`` outcome is impossible on its path,
when the recipe version, the input batch and the workflow do not all belong together, or when
any row's key cannot be rendered. Every problem is listed; nothing is written when there is one.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg.rows import dict_row

from packages.compiler.validation import validation_errors
from packages.ledger import create as ledger_create

from .templates import TemplateError, render

# The actions this runner understands. Executor actions are sent to the browser; the rest are
# runner steps, evaluated here. Anything else is refused at creation (fail closed).
EXECUTOR_ACTIONS = frozenset({"navigate", "fill", "select", "click", "wait_for", "assert"})
RUNNER_ACTIONS = frozenset({"branch", "end_record", "approval_gate"})
SUPPORTED_ACTIONS = EXECUTOR_ACTIONS | RUNNER_ACTIONS

_MODES = frozenset({"preview", "batch"})
_NESTED_KEYS = ("then", "else", "steps")

_SELECT_WORKFLOW = """
SELECT id, tenant_id, destination_key, archived_at FROM workflows WHERE id = %(id)s
"""
_SELECT_RECIPE_VERSION = """
SELECT id, tenant_id, workflow_id, recipe FROM recipe_versions WHERE id = %(id)s
"""
_SELECT_BATCH = """
SELECT id, tenant_id, workflow_id FROM input_batches WHERE id = %(id)s
"""
_SELECT_READY_ROWS = """
SELECT id, raw, clean FROM input_records
WHERE batch_id = %(batch)s AND status = 'ready'
ORDER BY row_index
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


def _check_consistency(
    workflow: Mapping[str, Any],
    recipe_version: Mapping[str, Any],
    batch: Mapping[str, Any],
) -> list[str]:
    """S5: the version, the batch and the workflow must belong together, under one tenant."""
    problems: list[str] = []
    if recipe_version["workflow_id"] != workflow["id"]:
        problems.append(
            f"recipe version {recipe_version['id']} belongs to workflow "
            f"{recipe_version['workflow_id']}, not {workflow['id']}"
        )
    if batch["workflow_id"] != workflow["id"]:
        problems.append(
            f"input batch {batch['id']} belongs to workflow "
            f"{batch['workflow_id']}, not {workflow['id']}"
        )
    tenants = {workflow["tenant_id"], recipe_version["tenant_id"], batch["tenant_id"]}
    if len(tenants) != 1:
        problems.append("the workflow, recipe version and input batch are not all one tenant")
    return problems


def _render_keys(
    meta: Mapping[str, Any], rows: list[dict[str, Any]]
) -> tuple[list[tuple[Any, str, str]], list[str]]:
    """Render ``record_key`` and ``idempotency_key`` for every row; collect every failure."""
    record_key_template = str(meta.get("record_key", ""))
    idempotency_template = str(meta.get("idempotency_key", ""))
    rendered: list[tuple[Any, str, str]] = []
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
        rendered.append((row["id"], record_key, idempotency_key))

    return rendered, problems


def create_run(
    conn: psycopg.Connection,
    *,
    workflow_id: uuid.UUID,
    recipe_version_id: uuid.UUID,
    input_batch_id: uuid.UUID,
    mode: str = "batch",
) -> uuid.UUID:
    """Create a run pinned to one recipe version and one input batch; return its id.

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
        cur.execute(_SELECT_BATCH, {"id": input_batch_id})
        batch = cur.fetchone()

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

        cur.execute(_SELECT_READY_ROWS, {"batch": input_batch_id})
        rows = cur.fetchall()

    meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
    rendered, key_problems = _render_keys(meta, rows)
    if key_problems:
        raise CreationError(key_problems)

    approval_policy = meta.get("approval_policy") or {"commit": "first_record"}
    tenant_id = workflow["tenant_id"]

    with conn.transaction():
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
        for input_record_id, record_key, idempotency_key in rendered:
            ledger_create.insert_run_record(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                input_record_id=input_record_id,
                record_key=record_key,
                idempotency_key=idempotency_key,
            )

    return run_id
