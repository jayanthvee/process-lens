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

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from packages.ledger import (
    LEGAL_TRANSITIONS,
    IllegalTransition,
    LedgerError,
    RecordClaim,
    RunClaim,
    StaleLease,
    StateConflict,
    begin_commit,
    change_state,
    claim_record,
    close_out_exhausted,
    commits_attempted,
    decisions_for_step,
    get_record,
    get_run,
    mark_submitted_unverified,
    open_approval_request,
    reconciling_records,
    record_step_event,
    recover_run,
    release_run,
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

logger = logging.getLogger("processlens.runner")

# The outcomes of a record that end the canary's run as a failure.
_FAILURE_STATES = frozenset({"parked", "failed"})

# B2: the reason a commit-attempted record is parked instead of skipped.
ALREADY_SAVED_REASON = "found in the destination after an earlier save attempt: confirm it is ours"

# B2: the pause reason when a record could not be rescued by parking it.
INTERNAL_ERROR = "internal_error"

# R6: the heartbeat must outlive three times the longest step in a recipe plus the dispatch budget.
HEARTBEAT_TIMEOUT_SAFETY_FACTOR = 3.0
DISPATCH_BUDGET_SECONDS = 5.0

_PRE_EXIT = {"skip": "skipped", "park": "parked", "fail": "failed"}

_SELECT_INPUT_ROW = "SELECT raw, clean FROM input_records WHERE id = %(id)s"
_SELECT_RECIPE = "SELECT recipe FROM recipe_versions WHERE id = %(id)s"

# value sources the runtime can resolve today. The rest are pre-batch/staging operations (D-017).
_RESOLVABLE_SOURCES = frozenset({"input", "constant"})


class RunnerConfigError(RuntimeError):
    """The worker's configuration cannot safely run this recipe (R6)."""


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
        heartbeat_timeout_seconds: float | None = None,
        lease_check: Callable[[], None] | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.executor = executor
        self.reconciler = reconciler
        self.clock = clock
        self.crash_hooks = crash_hooks
        self.fallback_timeout_ms = fallback_timeout_ms
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
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

        # R6: never start a run whose lease could lapse mid-step: the heartbeat must outlive
        # three times the longest step in the recipe plus the dispatch budget.
        self._require_heartbeat_headroom(recipe)

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
                # B1: hand the run back when stopping. If it was resumed in the meantime, the
                # release matches nothing and the holder carries on instead of leaving it unclaimed.
                if current.status == "paused" and not self._hand_back(conn, run):
                    continue
                return

            with conn.transaction():
                record = claim_record(conn, run.id, self.worker_id, epoch)
            if record is None:
                break

            try:
                outcome = self._process_record(conn, record, run, epoch, recipe, meta, rules)
            except (IllegalTransition, StateConflict) as exc:
                # B2: a refused transition never crashes the worker. Park the record if that is
                # legal; otherwise pause the run for a person.
                outcome = self._handle_refused(conn, record, run, epoch, exc)
                if outcome is None:
                    return
            if self._canary == record.id and outcome in _FAILURE_STATES:
                with conn.transaction():
                    set_run_status(
                        conn,
                        run.id,
                        "paused",
                        pause_reason="canary_failed",
                        worker=self.worker_id,
                        epoch=epoch,
                    )
                self._hand_back(conn, run)
                return

        # R5: close out exhausted records, then complete only if every record is out.
        with conn.transaction():
            close_out_exhausted(conn, run.id, self.worker_id, epoch)
        remaining = get_run(conn, run.id)
        if remaining is None:
            return
        # R4: a cancel that arrived while the last record was finishing is honoured here, not lost.
        if remaining.cancel_requested_at is not None:
            self._finalise_cancel(conn, run, epoch)
            return
        from packages.ledger import all_records_in_exit_state

        with conn.transaction():
            if all_records_in_exit_state(conn, run.id):
                set_run_status(conn, run.id, "completed", worker=self.worker_id, epoch=epoch)
            else:
                set_run_status(
                    conn,
                    run.id,
                    "paused",
                    pause_reason="records_not_exit",
                    worker=self.worker_id,
                    epoch=epoch,
                )
        self._hand_back(conn, run)

    def _hand_back(self, conn: psycopg.Connection, run: RunClaim) -> bool:
        """Release a paused run (B1). False when it was resumed, so the caller keeps going."""
        with conn.transaction():
            return release_run(conn, run.id, self.worker_id)

    def _handle_refused(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        exc: LedgerError,
    ) -> str | None:
        """B2: a refused transition must never crash the worker.

        Park the record with the error when parking is legal from its current state; otherwise pause
        the run with ``internal_error`` and return ``None`` so the caller stops.
        """
        logger.warning(
            "run %s record %s: the ledger refused a transition: %s", run.id, record.id, exc
        )
        fresh = get_record(conn, record.id)
        state = fresh.state if fresh is not None else None
        if state is not None and (state, "parked") in LEGAL_TRANSITIONS:
            try:
                self._transition(
                    conn, record, state, "parked", f"the ledger refused a transition: {exc}"
                )
                return "parked"
            except (IllegalTransition, StateConflict):
                logger.warning(
                    "run %s record %s: parking was refused too: %s", run.id, record.id, exc
                )
        with conn.transaction():
            set_run_status(
                conn,
                run.id,
                "paused",
                pause_reason=INTERNAL_ERROR,
                worker=self.worker_id,
                epoch=epoch,
            )
        return None

    def _require_heartbeat_headroom(self, recipe: Mapping[str, Any]) -> None:
        """R6: refuse to start when the heartbeat could lapse during a legitimate step."""
        if self.heartbeat_timeout_seconds is None:
            return
        required = (
            HEARTBEAT_TIMEOUT_SAFETY_FACTOR * max_step_timeout_seconds(recipe)
            + DISPATCH_BUDGET_SECONDS
        )
        if self.heartbeat_timeout_seconds < required:
            raise RunnerConfigError(
                f"heartbeat timeout {self.heartbeat_timeout_seconds:g}s is below 3x the longest "
                f"step plus dispatch ({required:g}s); a worker blocked in a legitimate step "
                "would be mistaken for a dead one"
            )

    def _finalise_cancel(self, conn: psycopg.Connection, run: RunClaim, epoch: int) -> None:
        """R4: reconcile the commit window, release the working records, skip the rest, stop."""
        with conn.transaction():
            recover_run(conn, run.id, self.worker_id, epoch, reason="run cancelled")
        for record in reconciling_records(conn, run.id):
            self._reconcile(conn, record, run, epoch)
        with conn.transaction():
            skip_pending_records(conn, run.id, self.worker_id, epoch, reason="run cancelled")
            set_run_status(conn, run.id, "cancelled", worker=self.worker_id, epoch=epoch)

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
                reason = _safe_text(rule.get("reason", ""), row, variables)
                # A precheck rule ends the record. The precheck has finished, so the record passes
                # through `prechecked` — the state the spec defines as "precheck finished; the
                # result decides skip, park, or fill" — before it exits. `claimed -> skipped` is not
                # a legal transition; `park`/`fail` are legal straight from `claimed`.
                if target == "skipped":
                    # B2's rule: a record that may already have saved is never reported as skipped.
                    fresh = get_record(conn, record.id)
                    if fresh is not None and fresh.commit_attempted:
                        self._transition(conn, record, "claimed", "parked", ALREADY_SAVED_REASON)
                        return "parked"
                    self._transition(conn, record, "claimed", "prechecked", reason)
                    self._transition(conn, record, "prechecked", "skipped", reason)
                else:
                    self._transition(conn, record, "claimed", target, reason)
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
                # B2: a record that may already have saved is never dismissed as `skipped`. The
                # database refuses `filling -> skipped` for a commit-attempted record, and a recipe
                # that reaches this point after an earlier save attempt must not crash the worker.
                if outcome == "skipped":
                    fresh = get_record(conn, record.id)
                    if fresh is not None and fresh.commit_attempted:
                        self._transition(conn, record, box.state, "parked", ALREADY_SAVED_REASON)
                        return "parked"
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
                with conn.transaction():
                    set_run_status(
                        conn,
                        run.id,
                        "paused",
                        pause_reason="duplicate_detected",
                        worker=self.worker_id,
                        epoch=epoch,
                    )
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
            set_run_status(
                conn,
                run.id,
                "paused",
                pause_reason="awaiting_approval",
                worker=self.worker_id,
                epoch=self._epoch,
            )
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
                with conn.transaction():
                    set_run_status(
                        conn,
                        run.id,
                        "paused",
                        pause_reason="duplicate_detected",
                        worker=self.worker_id,
                        epoch=epoch,
                    )
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


def max_step_timeout_seconds(recipe: Mapping[str, Any]) -> float:
    """The longest timeout in the recipe, in seconds (R6).

    Considers a step's own ``timeout_ms``, its ``assert_after.timeout_ms`` and its condition
    timeout, and walks into a ``branch``'s ``then``/``else`` lists, because a commit nested in a
    branch blocks just as long as a top-level one.
    """
    longest_ms = 0

    def walk(steps: Any) -> None:
        nonlocal longest_ms
        if not isinstance(steps, list):
            return
        for step in steps:
            if not isinstance(step, dict):
                continue
            candidates = [step.get("timeout_ms")]
            assertion = step.get("assert_after")
            if isinstance(assertion, dict):
                candidates.append(assertion.get("timeout_ms"))
            condition = step.get("condition") or step.get("when")
            if isinstance(condition, dict):
                candidates.append(condition.get("timeout_ms"))
            for candidate in candidates:
                if isinstance(candidate, int):
                    longest_ms = max(longest_ms, candidate)
            walk(step.get("then"))
            walk(step.get("else"))
            walk(step.get("steps"))

    walk(recipe.get("steps"))
    return longest_ms / 1000.0


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

    The whole rendered target ladder and the value, plus the input values of this record. That is
    what makes the approval specific: a decision can never cover a record, an element or a value the
    person did not see.
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
        rendered: list[Any] = []
        for target in targets:
            try:
                rendered.append(_render_target(target, row, variables))
            except TemplateError:
                rendered.append(None)
        values["targets"] = rendered
    return values


__all__ = ["RunLoop", "LedgerError"]
