"""The record loop: one run, record by record, with every ledger transition named.

This module holds the logic the plan's pseudocode describes, amended for T1's review:

* R2  a commit step is ``prepare`` (resolve + hit-test, no input) → ``begin_commit`` → ``dispatch``.
* R3  a gate looks for an approved request for exactly these values before it opens a new one; the
      policy gate sits immediately before ``begin_commit``.
* R4  ``runs.status`` is re-read from the database at every step boundary; a pause releases the
      record outside the commit window; a cancel is finalised by the worker that holds it.
* R5  exhausted records are closed out before the run is completed, and the run completes only when
      every record is in an exit state.
* S1  the acknowledgement is checked before a rejection.
* S2  a reconcile error parks the record, it never fails it.
* S4  every executor step writes a ``step_events`` row.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from packages.ledger import (
    LedgerError,
    RecordClaim,
    RunClaim,
    StaleLease,
    begin_commit,
    change_state,
    claim_record,
    close_out_exhausted,
    commits_attempted,
    decisions_for_step,
    get_run,
    mark_submitted_unverified,
    open_approval_request,
    reconciling_records,
    record_step_event,
    recover_run,
    set_run_status,
    skip_already_committed,
    skip_pending_records,
    verify_commit,
)

from .approvals import evaluate_policy
from .clock import Clock
from .conditions import condition_timeout
from .executor import Executor, StepCommand
from .reconcile import Reconciler, outcome_of
from .templates import TemplateError, is_template, render

# The outcome of a record that ends a failure the canary watches for.
_FAILURE_STATES = frozenset({"parked", "failed"})

_PRE_EXIT = {"skip": "skipped", "park": "parked", "fail": "failed"}

_SELECT_INPUT_ROW = "SELECT raw, clean FROM input_records WHERE id = %(id)s"
_SELECT_RECIPE = "SELECT recipe FROM recipe_versions WHERE id = %(id)s"

# value sources the runtime can resolve today. The rest are pre-batch/staging operations (D-017).
_RESOLVABLE_SOURCES = frozenset({"input", "constant"})


@dataclass
class _Box:
    """A mutable holder for the record's ledger state as the step list runs."""

    state: str


class RunLoop:
    """Processes one claimed run, record by record."""

    def __init__(
        self,
        *,
        worker_id: str,
        executor: Executor,
        reconciler: Reconciler,
        clock: Clock,
        crash_hooks: Callable[[str], None] | None = None,
        fallback_timeout_ms: int = 10_000,
        lease_check: Callable[[], None] | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.executor = executor
        self.reconciler = reconciler
        self.clock = clock
        self.crash_hooks = crash_hooks
        self.fallback_timeout_ms = fallback_timeout_ms
        self.lease_check = lease_check or (lambda: None)
        self._epoch = 0
        self._canary: uuid.UUID | None = None

    # -- the run -----------------------------------------------------------

    def process_run(self, conn: psycopg.Connection, run: RunClaim, epoch: int) -> None:
        """Run the batch: claim records, process them, then close out and complete."""
        self._epoch = epoch
        recipe = self._recipe(conn, run)
        meta = recipe.get("meta") if isinstance(recipe.get("meta"), dict) else {}
        rules = _rules(meta)

        skip_already_committed(conn, run.id, run.destination_key)

        # The ticket: after a takeover, reconcile EVERY commit-window record before claiming any
        # pending one. On a fresh run this is a no-op; after a crash or a resume it clears the
        # records a stopped worker left behind.
        for stranded in reconciling_records(conn, run.id):
            self._reconcile(conn, stranded, run, epoch)

        while True:
            self.lease_check()
            current = get_run(conn, run.id)
            if current is None:
                return
            if current.cancel_requested_at is not None:
                self._finalise_cancel(conn, run, epoch)
                return
            if current.status != "running":
                return

            with conn.transaction():
                record = claim_record(conn, run.id, self.worker_id)
            if record is None:
                break

            outcome = self._process_record(conn, record, run, epoch, recipe, meta, rules)
            if self._canary == record.id and outcome in _FAILURE_STATES:
                set_run_status(conn, run.id, "paused", pause_reason="canary_failed")
                return

        # R5: close out exhausted records, then complete only if every record is out.
        close_out_exhausted(conn, run.id)
        remaining = get_run(conn, run.id)
        if remaining is None:
            return
        from packages.ledger import all_records_in_exit_state

        if all_records_in_exit_state(conn, run.id):
            set_run_status(conn, run.id, "completed")
        else:
            set_run_status(conn, run.id, "paused", pause_reason="records_not_exit")

    def _finalise_cancel(self, conn: psycopg.Connection, run: RunClaim, epoch: int) -> None:
        """R4: reconcile the commit window, release the working records, skip the rest, stop."""
        recover_run(conn, run.id, self.worker_id, reason="run cancelled")
        for record in reconciling_records(conn, run.id):
            self._reconcile(conn, record, run, epoch)
        skip_pending_records(conn, run.id, reason="run cancelled")
        set_run_status(conn, run.id, "cancelled")

    # -- one record --------------------------------------------------------

    def _process_record(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        recipe: Mapping[str, Any],
        meta: Mapping[str, Any],
        rules: list[Any],
    ) -> str:
        row = self._input_row(conn, record)
        variables = _resolve_variables(recipe, row)

        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if _predicate_holds(rule.get("when"), row):
                target = _PRE_EXIT.get(rule.get("then"))
                if target is None:
                    continue
                self._transition(
                    conn,
                    record,
                    "claimed",
                    target,
                    _safe_text(rule.get("reason", ""), row, variables),
                )
                return target

        self._transition(conn, record, "claimed", "prechecked", "precheck passed")
        box = _Box(state="prechecked")
        terminal = self._run_steps(
            conn, record, run, epoch, recipe.get("steps", []), row, variables, box
        )
        if terminal is not None:
            return terminal

        if box.state == "prechecked":
            self._transition(conn, record, "prechecked", "parked", "no terminal step")
            return "parked"
        self._transition(conn, record, "filling", "verified", "steps completed")
        return "verified"

    def _run_steps(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        steps: Any,
        row: Mapping[str, Any],
        variables: Mapping[str, Any],
        box: _Box,
    ) -> str | None:
        if not isinstance(steps, list):
            return None

        for step in steps:
            if not isinstance(step, dict):
                continue

            # R4: re-read runs.status from the database at every step boundary.
            self.lease_check()
            current = get_run(conn, run.id)
            if current is None:
                return "paused"
            if current.cancel_requested_at is not None:
                return "cancelled"
            if current.status != "running":
                if box.state == "filling":
                    self._transition(conn, record, "filling", "pending", "paused")
                return "paused"

            action = step.get("action")

            if action == "branch":
                condition = step.get("when", {})
                passed = self._check(
                    condition, step_id=step.get("id"), record_key=record.record_key
                )
                branch = step.get("then", []) if passed else step.get("else", [])
                terminal = self._run_steps(conn, record, run, epoch, branch, row, variables, box)
                if terminal is not None:
                    return terminal
                continue

            if action == "approval_gate":
                when = step.get("when")
                if when and not self._check(
                    when, step_id=step.get("id"), record_key=record.record_key
                ):
                    continue
                shown = _gate_values(step, row, variables)
                verdict = self._gate(conn, record, run, box.state, step_id=step["id"], shown=shown)
                if verdict == "approved":
                    continue
                if verdict == "rejected":
                    return "parked"
                return "awaiting_approval"

            if action == "end_record":
                outcome = step.get("outcome")
                reason = _safe_text(step.get("reason", ""), row, variables)
                self._transition(conn, record, box.state, outcome, reason)
                return outcome

            # An executor step. Enter `filling` on the first one.
            if box.state == "prechecked":
                self._transition(conn, record, "prechecked", "filling", "steps begin")
                box.state = "filling"

            if step.get("commit") is True:
                terminal = self._commit_step(conn, record, run, epoch, step, row, variables)
            else:
                terminal = self._simple_step(conn, record, run, step, row, variables)
            if terminal is not None:
                return terminal

        return None

    def _commit_step(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        step: Mapping[str, Any],
        row: Mapping[str, Any],
        variables: Mapping[str, Any],
    ) -> str | None:
        if self._canary is None:
            self._canary = record.id  # the first record to reach a commit step is the canary

        try:
            command = self._command(record, run, epoch, step, row, variables, commit=True)
        except TemplateError as exc:
            self._transition(conn, record, "filling", "parked", str(exc))
            return "parked"

        # R2: resolve and hit-test BEFORE the write-ahead; a locator failure is not a commit.
        prepared = self.executor.prepare(command)
        if prepared.status != "ok":
            self._transition(
                conn,
                record,
                "filling",
                "parked",
                prepared.error or "the target could not be resolved",
                step=step,
                outcome="failed",
                error=prepared.error,
                rung=prepared.rung,
            )
            return "parked"

        # R3: the policy gate sits immediately before begin_commit.
        policy_gates = evaluate_policy(
            run.approval_policy,
            commits_attempted=commits_attempted(conn, run.id),
            mode=run.mode,
        )
        if policy_gates:
            verdict = self._gate(
                conn,
                record,
                run,
                "filling",
                step_id=step["id"],
                shown=_gate_values(step, row, variables),
            )
            if verdict == "rejected":
                return "parked"
            if verdict == "opened":
                return "awaiting_approval"

        with conn.transaction():
            begin_commit(conn, record.id, worker=self.worker_id, epoch=epoch)
        self._crash("after_submitting")

        acked = {"value": False}

        def on_dispatched() -> None:
            with conn.transaction():
                mark_submitted_unverified(conn, record.id, worker=self.worker_id, epoch=epoch)
            acked["value"] = True
            self._crash("after_ack")

        result = self.executor.dispatch(command, on_dispatched)
        self._crash("after_dispatch")

        if acked["value"]:
            # S1: a rejection is applied only from submitted_unverified, never from submitting.
            if result.status == "rejected":
                self._transition(
                    conn,
                    record,
                    "submitted_unverified",
                    "failed",
                    result.error or "the destination rejected the change",
                    step=step,
                    outcome="failed",
                    error=result.error,
                    observed=result.observed,
                    rung=result.rung,
                )
                return "failed"
        elif result.status == "not_dispatched":
            # L3: this worker knows no input event was sent.
            self._transition(
                conn,
                record,
                "submitting",
                "failed",
                result.error or "the commit command was never dispatched",
                step=step,
                outcome="failed",
                error=result.error,
            )
            return "failed"
        else:
            self._transition(
                conn,
                record,
                "submitting",
                "reconciling",
                "no dispatch proof",
                step=step,
                outcome="failed",
                rung=result.rung,
            )
            return self._reconcile(conn, record, run, epoch, step)

        condition = step.get("assert_after", {})
        passed = self._check(condition, step_id=step.get("id"), record_key=record.record_key)
        self._crash("after_assert")

        if passed:
            with conn.transaction():
                verified = verify_commit(
                    conn,
                    record.id,
                    worker=self.worker_id,
                    epoch=epoch,
                    destination_ref=result.observed.get("ref"),
                )
                record_step_event(
                    conn,
                    record=record,
                    step_id=step["id"],
                    outcome="ok",
                    observed=result.observed,
                    locator_rung=result.rung,
                )
            if not verified.key_inserted:
                set_run_status(conn, run.id, "paused", pause_reason="duplicate_detected")
                return "duplicate"
            return "verified"

        self._transition(
            conn,
            record,
            "submitted_unverified",
            "reconciling",
            "the assertion did not pass",
            step=step,
            outcome="failed",
        )
        return self._reconcile(conn, record, run, epoch, step)

    def _simple_step(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        step: Mapping[str, Any],
        row: Mapping[str, Any],
        variables: Mapping[str, Any],
    ) -> str | None:
        try:
            command = self._command(record, run, self._epoch, step, row, variables, commit=False)
        except TemplateError as exc:
            self._transition(conn, record, "filling", "parked", str(exc))
            return "parked"

        result = self.executor.perform(command, None)
        if result.status == "ok":
            # S4: every executor step writes a step event, in its own transaction.
            with conn.transaction():
                record_step_event(
                    conn,
                    record=record,
                    step_id=step["id"],
                    outcome="ok",
                    observed=result.observed,
                    locator_rung=result.rung,
                )
            return None

        fail_to = "failed" if step.get("on_failure") == "fail" else "parked"
        self._transition(
            conn,
            record,
            "filling",
            fail_to,
            result.error or "the step did not complete",
            step=step,
            outcome="failed",
            error=result.error,
            observed=result.observed,
            rung=result.rung,
        )
        return fail_to

    # -- approvals and reconcile ------------------------------------------

    def _gate(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        state: str,
        *,
        step_id: str,
        shown: dict[str, Any],
    ) -> str:
        """R3: pass when an approved request covers exactly these values; open one otherwise."""
        for decision in decisions_for_step(conn, record.id, step_id):
            if decision.shown != shown:
                continue
            if decision.decision == "approved":
                return "approved"
            self._transition(
                conn, record, state, "parked", decision.reason or "a reviewer rejected this commit"
            )
            return "rejected"

        with conn.transaction():
            open_approval_request(
                conn,
                tenant_id=record.tenant_id,
                run_id=run.id,
                run_record_id=record.id,
                step_id=step_id,
                kind="commit",
                shown=shown,
            )
            set_run_status(conn, run.id, "paused", pause_reason="awaiting_approval")
        return "opened"

    def _reconcile(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        step: Mapping[str, Any] | None = None,
    ) -> str:
        diff = self.reconciler.search(record)
        outcome = outcome_of(diff)

        if outcome == "verified":
            with conn.transaction():
                verified = verify_commit(
                    conn,
                    record.id,
                    worker=self.worker_id,
                    epoch=epoch,
                    destination_ref=diff.destination_ref,
                    reason="the destination has this commit",
                )
                if step is not None:
                    record_step_event(
                        conn,
                        record=record,
                        step_id=step["id"],
                        outcome="ok",
                        observed={"destination_ref": diff.destination_ref},
                    )
            if not verified.key_inserted:
                set_run_status(conn, run.id, "paused", pause_reason="duplicate_detected")
                return "duplicate"
            return "verified"

        if outcome == "pending":
            self._transition(
                conn, record, "reconciling", "pending", "the destination does not have it; retry"
            )
            return "pending"

        # S2: an ambiguous match or a search error parks the record; it never fails it.
        self._transition(
            conn,
            record,
            "reconciling",
            "parked",
            diff.error or "the destination check found more than one match",
        )
        return "parked"

    # -- primitives --------------------------------------------------------

    def _transition(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        expected: str,
        new: str,
        reason: str,
        *,
        step: Mapping[str, Any] | None = None,
        outcome: str = "ok",
        observed: dict[str, Any] | None = None,
        error: Any = None,
        rung: int | None = None,
    ) -> None:
        """One state change, and the step event that accompanies it, in one transaction."""
        with conn.transaction():
            change_state(
                conn,
                record.id,
                worker=self.worker_id,
                epoch=self._epoch,
                expected=expected,
                new=new,
                reason=reason,
                error=error,
            )
            if step is not None:
                record_step_event(
                    conn,
                    record=record,
                    step_id=str(step.get("id", "?")),
                    outcome=outcome,
                    observed=observed,
                    error=error,
                    locator_rung=rung,
                )

    def _check(
        self, condition: Mapping[str, Any], *, step_id: str | None, record_key: str | None
    ) -> bool:
        result = self.executor.check_condition(
            dict(condition),
            timeout_ms=condition_timeout(condition, self.fallback_timeout_ms),
            step_id=step_id,
            record_key=record_key,
        )
        return result.passed

    def _command(
        self,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        step: Mapping[str, Any],
        row: Mapping[str, Any],
        variables: Mapping[str, Any],
        *,
        commit: bool,
    ) -> StepCommand:
        rendered: dict[str, Any] = {"id": step["id"], "action": step["action"]}
        for key in ("targets", "within", "frame", "clear_first", "option_match", "timeout_ms"):
            if key in step:
                rendered[key] = step[key]
        if "value" in step:
            rendered["value"] = _render_value_source(step["value"], row, variables)
        if "condition" in step:
            rendered["condition"] = _render_condition(step["condition"], row, variables)
        if "assert_after" in step:
            rendered["assert_after"] = _render_condition(step["assert_after"], row, variables)
        rendered["targets"] = [
            _render_target(target, row, variables) for target in step.get("targets", [])
        ]

        return StepCommand(
            command_id=f"{record.id}:{step['id']}",
            run_id=run.id,
            run_record_id=record.id,
            record_key=record.record_key,
            idempotency_key=record.idempotency_key,
            lease_epoch=epoch,
            step=rendered,
            commit=commit,
            expires_at=self.clock.time()
            + condition_timeout(step.get("assert_after", {}), self.fallback_timeout_ms) / 1000.0,
        )

    def _crash(self, point: str) -> None:
        if self.crash_hooks is not None:
            self.crash_hooks(point)

    def _input_row(self, conn: psycopg.Connection, record: RecordClaim) -> dict[str, Any]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_SELECT_INPUT_ROW, {"id": record.input_record_id})
            fetched = cur.fetchone()
        if fetched is None:
            return {}
        clean = fetched["clean"]
        row = dict(clean) if isinstance(clean, dict) else {}
        if isinstance(fetched["raw"], dict):
            row = {**fetched["raw"], **row}
        return row

    def _recipe(self, conn: psycopg.Connection, run: RunClaim) -> dict[str, Any]:
        with conn.cursor() as cur:
            cur.execute(_SELECT_RECIPE, {"id": run.recipe_version_id})
            fetched = cur.fetchone()
        if fetched is None or not isinstance(fetched[0], dict):
            raise StaleLease(f"run {run.id} pins a recipe version that is missing")
        return fetched[0]


# -- helpers the loop uses ------------------------------------------------


def _rules(meta: Mapping[str, Any]) -> list[Any]:
    schema = meta.get("input_schema")
    if not isinstance(schema, dict):
        return []
    rules = schema.get("rules")
    return rules if isinstance(rules, list) else []


def _predicate_holds(predicate: Any, row: Mapping[str, Any]) -> bool:
    if not isinstance(predicate, dict):
        return False
    if "missing" in predicate:
        value = row.get(predicate["missing"])
        return value is None or value == "" or value == []
    if "not_in" in predicate:
        spec = predicate["not_in"] if isinstance(predicate["not_in"], dict) else {}
        value = row.get(spec.get("field"))
        allowed = {str(item) for item in spec.get("allowed_values", [])}
        if value is None:
            return True
        return str(value) not in allowed
    return False


def _resolve_variables(recipe: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the variables this runtime can: constants. The rest stay unbound (D-017).

    ``extract``, ``classify``, ``read_value`` and rule-table lookups are pre-batch/staging
    operations, not part of the deterministic batch loop, so a template that refers to one fails
    closed.
    """
    variables: dict[str, Any] = {}
    declared = recipe.get("variables")
    if not isinstance(declared, dict):
        return variables
    for name, spec in declared.items():
        source = spec.get("source") if isinstance(spec, dict) else None
        if isinstance(source, dict) and source.get("kind") == "constant":
            value = source.get("value")
            if isinstance(value, str) and is_template(value):
                try:
                    variables[name] = render(value, row=row, variables={})
                except TemplateError:
                    continue
            else:
                variables[name] = value
    return variables


def _safe_text(text: Any, row: Mapping[str, Any], variables: Mapping[str, Any]) -> str:
    """Render a human-readable reason; fall back to the raw text if it cannot be rendered."""
    if not isinstance(text, str):
        return str(text)
    try:
        return render(text, row=row, variables=variables)
    except TemplateError:
        return text


def _render_value_source(source: Any, row: Mapping[str, Any], variables: Mapping[str, Any]) -> Any:
    if not isinstance(source, dict):
        return source
    kind = source.get("kind")
    if kind == "input":
        column = source.get("column")
        if column not in row:
            raise TemplateError(f"the input row has no column {column!r}")
        value = row[column]
        if value is None:
            raise TemplateError(f"column {column!r} is empty for this record")
        return value
    if kind == "constant":
        value = source.get("value")
        if isinstance(value, str) and is_template(value):
            return render(value, row=row, variables=variables)
        return value
    if kind not in _RESOLVABLE_SOURCES:
        raise TemplateError(
            f"value source {kind!r} is a pre-batch operation; it is not resolved at run time"
        )
    return None


def _render_target(
    target: Any, row: Mapping[str, Any], variables: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    rendered = dict(target)
    by = target.get("by")
    if by == "role":
        rendered["name"] = render(str(target.get("name", "")), row=row, variables=variables)
        if "within" in target:
            rendered["within"] = render(str(target["within"]), row=row, variables=variables)
    elif by in ("testid", "text_fuzzy"):
        rendered["value"] = render(str(target.get("value", "")), row=row, variables=variables)
    elif by == "label":
        rendered["text"] = render(str(target.get("text", "")), row=row, variables=variables)
    return rendered


def _render_condition(
    condition: Any, row: Mapping[str, Any], variables: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(condition, dict):
        return {}
    rendered = dict(condition)
    for key in ("text_visible", "text_absent", "url_matches"):
        value = rendered.get(key)
        if isinstance(value, str) and is_template(value):
            rendered[key] = render(value, row=row, variables=variables)
    if isinstance(rendered.get("element_present"), dict):
        rendered["element_present"] = _render_selector(rendered["element_present"], row, variables)
    if isinstance(rendered.get("element_absent"), dict):
        rendered["element_absent"] = _render_selector(rendered["element_absent"], row, variables)
    return rendered


def _render_selector(
    selector: Mapping[str, Any], row: Mapping[str, Any], variables: Mapping[str, Any]
) -> dict[str, Any]:
    rendered = dict(selector)
    targets = selector.get("targets")
    if isinstance(targets, list):
        rendered["targets"] = [_render_target(target, row, variables) for target in targets]
    return rendered


def _gate_values(
    step: Mapping[str, Any], row: Mapping[str, Any], variables: Mapping[str, Any]
) -> dict[str, Any]:
    """The exact values a person is shown and therefore approves (R3).

    The step's rendered target and value, plus the input values of this record. That makes the
    approval specific: a decision can never cover a record whose values the person did not see.
    """
    values: dict[str, Any] = {
        "step": step.get("id"),
        "action": step.get("action"),
        "record": {key: row[key] for key in sorted(row)},
    }
    value = step.get("value")
    if isinstance(value, dict):
        try:
            values["value"] = _render_value_source(value, row, variables)
        except TemplateError:
            values["value"] = None
    targets = step.get("targets")
    if isinstance(targets, list) and targets:
        try:
            values["target"] = _render_target(targets[0], row, variables)
        except TemplateError:
            values["target"] = None
    return values


__all__ = ["RunLoop", "LedgerError"]
