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

PL-026 adds the admin freeze, and it is one boundary: the tenant's ``runs_frozen_at`` is read at the
same step boundary where ``cancel_requested_at`` is read, and the run pauses there with
``pause_reason = 'admin_freeze'``. The record is released outside the commit window and reconciled
inside it, so a freeze never stops anything between ``submitting`` and ``verified``. Unfreezing does
not resume anything: each run is resumed by a person.

PL-014 adds the real-extension path, and three of its rules live here:

* R1  a condition check that got no answer never counts as "false". ``passed=False`` is only an
      answer when the executor actually polled; anything else parks (or reconciles) instead of
going down the ``else`` branch of a duplicate search.
* R2  every condition is rendered before it reaches the executor; the recipe's own
      ``{{row.email | lower}}`` must arrive as the record's email, never as the placeholder.
* R6  a run-level event beats the step result. Events are drained *before* a result is
      interpreted, so a challenge releases a pre-commit record instead of parking it through the
      ``refused`` rule, and ``peer_gone`` is authoritative rather than a hint. PL-014b reads the
      whole batch on each drain, and a run-level event anywhere in it takes precedence over an
      ``off_origin`` anywhere in it.
"""

from __future__ import annotations

import logging
import random
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from packages.ledger import (
    COMMIT_WINDOW_STATES,
    LEGAL_TRANSITIONS,
    WORKING_STATES,
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
    has_pending_records,
    mark_submitted_unverified,
    open_approval_request,
    reconciling_records,
    record_step_event,
    recover_run,
    release_run,
    run_pacing_settings,
    set_run_status,
    site_rows_submitted_today,
    skip_already_committed,
    skip_pending_records,
    tenant_is_frozen,
    tenant_site_caps,
    verify_commit,
)

from .approvals import evaluate_policy
from .check_words import describe_check, parking_reason
from .clock import Clock
from .conditions import condition_timeout
from .executor import ConditionResult, ConfirmationProbe, Executor, StepCommand, StepResult
from .finding import MAX_AI_CALLS_PER_RECORD, TargetFinder, find_failed_reason
from .reconcile import Reconciler, outcome_of
from .step_words import describe_step_failure, describe_wait_failure
from .templates import TemplateError, is_template, render

logger = logging.getLogger("processlens.runner")

# The outcomes of a record that end the canary's run as a failure.
_FAILURE_STATES = frozenset({"parked", "failed"})

# PL-027: the record outcomes that mean the tab may be holding a half-filled form nothing committed.
# `verified` is a commit that landed and `skipped` never filled anything, so neither is guarded.
_ABANDONED_OUTCOMES = frozenset({"parked", "failed", "cancelled", "paused", "awaiting_approval"})

# PL-074: the pause reasons whose page a person still has to answer. The code page is where the
# person's one-time code is typed, so the bound tab must stay on it; the abandon guard's retirement
# would navigate the tab to `about:blank` and take the field away. Every other pause still arms the
# guard: a released record's half-filled form must not autosave on the way out.
PAGE_KEEPING_PAUSES = frozenset({"code_required"})

# PL-102: the ledger's reason when a record was released because the code was read from email.
CODE_FROM_EMAIL_REASON = "the site's sign-in code was read from email"

# PL-027: the message a person reads on a step event when the abandon guard refused a request.
BLOCKED_REQUEST_MESSAGE = "blocked an unsaved change from leaving the page"

# The step id a blocked request is filed under when no step of its own was running: the guard is
# released either by a navigate (which has an id of its own) or by the run ending, and only the
# second case has no step to name.
ABANDON_GUARD_STEP_ID = "abandon-guard"

# PL-027: how long a record may hold the tab while a person decides a commit gate, and how often the
# wait re-reads the decision. The record is NOT released while it waits: under `first_record` it
# pauses AT the commit step with the form filled, and approving runs that step -- a restart from
# step 1 would navigate away and let the destination autosave the half-filled form.
DEFAULT_GATE_WAIT_SECONDS = 600.0
GATE_POLL_SECONDS = 0.5

# B2: the reason a commit-attempted record is parked instead of skipped.
ALREADY_SAVED_REASON = "found in the destination after an earlier save attempt: confirm it is ours"

# B2: the pause reason when a record could not be rescued by parking it.
INTERNAL_ERROR = "internal_error"

# R6: the heartbeat must outlive three times the longest step in a recipe plus the dispatch budget.
HEARTBEAT_TIMEOUT_SAFETY_FACTOR = 3.0
DISPATCH_BUDGET_SECONDS = 5.0

# PL-014: the run-level events that pause a run, and the pause reason each produces.
# PL-040: `window_hidden` -- the work window was covered or minimized, so the page stopped drawing.
# PL-068: `code_required` -- the site asked for a one-time code. The person types it on the run
# page; the released record is claimed again when the run resumes.
PAUSE_EVENTS = frozenset(
    {"challenge", "login_required", "policy_blocked", "window_hidden", "code_required"}
)
DISCONNECT_EVENTS = frozenset({"detached", "tab_closed"})
EXECUTOR_DISCONNECTED = "executor_disconnected"

# PL-026: an admin paused every run for this tenant. The switch is read at the step boundary, and
# the record's own reason says what happened in words a person reads.
ADMIN_FREEZE = "admin_freeze"
ADMIN_FREEZE_REASON = "an admin paused all runs"

# R1: a condition check the browser never answered decided nothing.
UNANSWERED_CONDITION_REASON = "the browser could not answer a condition check"

# PL-014 requirement 4: a confirmation already on the page proves nothing about this save.
ALREADY_ON_PAGE_REASON = "the confirmation was already on the page before saving"
UNKNOWN_CONFIRMATION_REASON = "could not tell whether the confirmation was already on the page"
# PL-118: a commit checked by the value it changed could not see that value on the page. The
# destination may still have taken the change, so a person checks it; the row is never Done.
READ_BACK_FAILED_REASON = "ProcessLens couldn't confirm the change was saved."

# The freshness probe asks "is it there now", so it polls once at the schema's own minimum.
CONFIRMATION_PROBE_TIMEOUT_MS = 100

# PL-067: pacing. The largest piece of a wait the run loop lets pass before it re-reads the run's
# status, so a pause or a cancel during a wait is noticed within about a second.
PACING_SLICE_SECONDS = 1.0
# The jitter pacing adds, as a fraction of the gap: a wait of 0-25% longer than the setting, so
# two runs started together do not act in lock-step. A rate limit, not a disguise.
PACING_JITTER_FRACTION = 0.25
# PL-067: the pause reason when a site's daily cap is reached.
SITE_DAILY_CAP = "site_daily_cap"

_PRE_EXIT = {"skip": "skipped", "park": "parked", "fail": "failed"}

_SELECT_INPUT_ROW = "SELECT raw, clean FROM input_records WHERE id = %(id)s"
_SELECT_RECIPE = "SELECT recipe FROM recipe_versions WHERE id = %(id)s"

# value sources the runtime can resolve today. The rest are pre-batch/staging operations (D-017).
_RESOLVABLE_SOURCES = frozenset({"input", "constant"})


class RunnerConfigError(RuntimeError):
    """The worker's configuration cannot safely run this recipe (R6)."""


def _pause_reason_for(kind: str) -> str:
    """The pause reason a run-level event produces (PL-014)."""
    return EXECUTOR_DISCONNECTED if kind in DISCONNECT_EVENTS else kind


def _is_run_level_event(kind: str) -> bool:
    """A run-level event: one that stops the run whatever the record is doing (PL-014)."""
    return kind in PAUSE_EVENTS or kind in DISCONNECT_EVENTS


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
        events: Callable[[], list[tuple[str, str]]] | None = None,
        gate_wait_seconds: float = DEFAULT_GATE_WAIT_SECONDS,
        finder: TargetFinder | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.executor = executor
        self.reconciler = reconciler
        self.clock = clock
        self.crash_hooks = crash_hooks
        self.fallback_timeout_ms = fallback_timeout_ms
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        # PL-027: how long a record may hold the tab while a person decides its commit gate. The
        # record is held AT the commit step, never released and re-run.
        self.gate_wait_seconds = gate_wait_seconds
        # PL-027: how often that wait re-reads the decision, and the state that hold leaves.
        self.gate_poll_seconds = GATE_POLL_SECONDS
        self.lease_check = lease_check or (lambda: None)
        # R6: the drain the loop reads run-level events through. A test injects any sequence.
        self.events = events
        self._epoch = 0
        self._canary: uuid.UUID | None = None
        self._allowed_origins: tuple[str, ...] = ()
        self._deferred_events: list[tuple[str, str]] = []
        # PL-027: whether the record being processed actually drove the browser. A record parked by
        # a precheck rule never opened a page, so there is no half-filled form to guard.
        self._browser_touched = False
        # PL-027: the last record this run claimed, so the abandon guard can be released against a
        # record when the run ends with nothing left to name.
        self._last_record: RecordClaim | None = None
        # PL-067: the run's pacing settings, read once per run, and the clock times of the last
        # claim and the last executor command, which the gaps are measured from.
        self.min_seconds_between_rows = 0.0
        self.min_seconds_between_steps = 0.0
        self._last_claim_at: float | None = None
        self._last_step_at: float | None = None
        # PL-122: the remembered element and the model's answer, for this worker. ``None`` means
        # no finder was built (tests, or a worker with no AI service); a record then parks exactly
        # as it did before this ticket.
        self.finder = finder
        self._ai_calls_this_record = 0

    # -- the run -----------------------------------------------------------

    def process_run(self, conn: psycopg.Connection, run: RunClaim, epoch: int) -> None:
        """Run the batch: claim records, process them, then close out and complete."""
        self._epoch = epoch
        recipe = self._recipe(conn, run)
        meta = recipe.get("meta") if isinstance(recipe.get("meta"), dict) else {}
        rules = _rules(meta)
        origins = meta.get("allowed_origins")
        self._allowed_origins = tuple(str(o) for o in origins) if isinstance(origins, list) else ()

        # R6: never start a run whose lease could lapse mid-step: the heartbeat must outlive
        # three times the longest step in the recipe plus the dispatch budget.
        self._require_heartbeat_headroom(recipe)

        # PL-067: the run's own pacing, read once. A run pins its recipe for its whole life, and
        # the person's pacing is the same kind of decision.
        self.min_seconds_between_rows, self.min_seconds_between_steps = run_pacing_settings(
            conn, run.id
        )

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

            # R6: a run-level event that arrived between records pauses the run before the next
            # claim, so no new record is started against a page that is showing a challenge.
            if self._pause_for_events(conn, run, epoch):
                self._hand_back(conn, run)
                return

            # PL-067: pace before claiming, then the site's daily cap. Both run before any record
            # is touched, so a pause here leaves every record `pending` and nothing reaches the
            # commit window. A run with no row left to claim has nothing to pace or cap: it goes
            # on to the (empty) claim and completes.
            if has_pending_records(conn, run.id):
                if not self._pace_before_claim(conn, run, epoch):
                    self._hand_back(conn, run)
                    return
                if self._pause_at_site_cap(conn, run, epoch):
                    self._hand_back(conn, run)
                    return

            with conn.transaction():
                record = claim_record(conn, run.id, self.worker_id, epoch)
            if record is None:
                break
            self._last_record = record
            self._last_claim_at = self.clock.time()

            try:
                outcome = self._process_record(conn, record, run, epoch, recipe, meta, rules)
            except (IllegalTransition, StateConflict) as exc:
                # B2: a refused transition never crashes the worker. Park the record if that is
                # legal; otherwise pause the run for a person.
                outcome = self._handle_refused(conn, record, run, epoch, exc)
                if outcome is None:
                    # PL-013 follow-up: the internal_error pause hands the run back too, so a
                    # resume can claim it at once instead of finding it owned by a stopped worker.
                    self._hand_back(conn, run)
                    return
            # PL-027: a record that left the tab without committing may have left a half-filled form
            # behind, and real apps autosave those when the page unloads. The guard is armed here,
            # before the next record's navigate can unload the page; that navigate's arrival
            # releases it, and so does the end of the run. A record a precheck rule parked never
            # opened a page, so nothing is armed for it.
            if (
                outcome in _ABANDONED_OUTCOMES
                and self._browser_touched
                and not self._keeps_the_page(conn, run)
            ):
                self._arm_guard()
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
        # PL-027: the run is over, so the guard's job is done. Released here with whatever it held
        # back, so the ledger carries the proof even when no later navigate released it.
        if self._last_record is not None:
            self._release_guard(conn, self._last_record, ABANDON_GUARD_STEP_ID)
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

    # -- PL-067: pacing and the site daily cap -------------------------------

    def _pace(self, conn: psycopg.Connection, run: RunClaim, epoch: int, seconds: float) -> bool:
        """Wait at least ``seconds`` (plus 0-25% jitter), noticing a stop within one slice.

        Returns True when the run may carry on; False when it was paused or cancelled during the
        wait. The wait is sliced, and each slice re-reads the run's status from the database, so a
        pause is noticed within about a second of it happening. All time goes through the clock.
        """
        if seconds <= 0:
            return True
        delay = seconds * (1.0 + random.uniform(0.0, PACING_JITTER_FRACTION))
        deadline = self.clock.time() + delay
        while True:
            current = get_run(conn, run.id)
            if current is None or current.cancel_requested_at is not None:
                return False
            if current.status != "running":
                return False
            now = self.clock.time()
            if now >= deadline:
                return True
            self.clock.pause(min(PACING_SLICE_SECONDS, deadline - now))

    def _pace_before_claim(self, conn: psycopg.Connection, run: RunClaim, epoch: int) -> bool:
        """The row gap: the wait before claiming the next row, measured from the last claim.

        A gap belongs between two rows: the first claim of a run waits for nothing, and a run with
        no row left to claim spends nothing before finding that out.
        """
        if self.min_seconds_between_rows <= 0 or self._last_claim_at is None:
            return True
        if not has_pending_records(conn, run.id):
            return True
        elapsed = max(0.0, self.clock.time() - self._last_claim_at)
        return self._pace(conn, run, epoch, self.min_seconds_between_rows - elapsed)

    def _pace_for_step(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        box: _Box,
    ) -> bool:
        """The step gap: the wait before sending the next command, measured from the last one."""
        if self.min_seconds_between_steps <= 0:
            return True
        elapsed = (
            0.0 if self._last_step_at is None else max(0.0, self.clock.time() - self._last_step_at)
        )
        remaining = self.min_seconds_between_steps - elapsed
        # Every command that goes out restarts the gap, waited for or not: a slow step must not
        # let the step after it go out back to back.
        if remaining <= 0 or self._pace(conn, run, epoch, remaining):
            self._last_step_at = self.clock.time()
            return True
        # The run stopped during the wait: the record leaves the tab exactly as it does at the
        # step boundary's own pause.
        if box.state == "filling":
            self._transition(conn, record, "filling", "pending", "paused", refund_attempt=True)
            box.state = "pending"
        return False

    def _pause_at_site_cap(self, conn: psycopg.Connection, run: RunClaim, epoch: int) -> bool:
        """Pause the run when a site it works on has reached its daily cap (PL-067).

        The count is this tenant's rows that entered `submitting` today (UTC) on runs whose recipe
        allows the origin. The run pauses BEFORE claiming, so the record stays `pending`, nothing
        reaches the commit window, and the ledger's invariants are untouched.
        """
        if not self._allowed_origins:
            return False
        caps = tenant_site_caps(conn, run.tenant_id)
        if not caps:
            return False
        for origin in self._allowed_origins:
            limit = caps.get(origin)
            if limit is None:
                continue
            if site_rows_submitted_today(conn, run.tenant_id, origin) >= limit:
                with conn.transaction():
                    set_run_status(
                        conn,
                        run.id,
                        "paused",
                        pause_reason=SITE_DAILY_CAP,
                        worker=self.worker_id,
                        epoch=epoch,
                    )
                logger.warning(
                    "run %s paused: today's limit for %s (%d rows) is reached",
                    run.id,
                    origin,
                    limit,
                )
                return True
        return False

    # -- the abandon guard (PL-027) ----------------------------------------

    def _guard(self, action: str) -> list[dict[str, Any]]:
        """Arm or release the abandon guard, when the executor knows how.

        The scripted fake executor of the unit tests has no guard, and a guard is an extra defence
        rather than part of a record's outcome: a run must not fail because it could not be armed.
        """
        guard = getattr(self.executor, "guard", None)
        if not callable(guard):
            return []
        try:
            return list(guard(action))
        except Exception as exc:  # noqa: BLE001 - a defence that fails must not stop the run
            logger.warning("the abandon guard %s failed: %s", action, exc)
            return []

    def _keeps_the_page(self, conn: psycopg.Connection, run: RunClaim) -> bool:
        """Whether the run paused on a page a person still has to answer (PL-074).

        The run's own ``pause_reason`` is the source of truth, because it is what the ledger just
        recorded and what the run page shows: only a pause the person resolves in the bound tab --
        today, the site's one-time-code prompt -- leaves that page open.
        """
        current = get_run(conn, run.id)
        return current is not None and current.pause_reason in PAGE_KEEPING_PAUSES

    def _arm_guard(self) -> None:
        """A record has left the tab without committing: hold its unsaved changes in."""
        self._guard("arm")

    def _record_blocked(
        self, conn: psycopg.Connection, record: RecordClaim, step_id: str, blocked: Any
    ) -> None:
        """One step event per request the abandon guard refused (PL-027).

        This is the proof a parked record did not quietly reach the destination: the page tried to
        leave its half-filled form and the tab refused. Each refusal is its own row, so a person can
        see exactly what was held back rather than a count.
        """
        if not isinstance(blocked, list) or not blocked:
            return
        with conn.transaction():
            for entry in blocked:
                request = entry if isinstance(entry, dict) else {"url": str(entry)}
                record_step_event(
                    conn,
                    record=record,
                    step_id=step_id,
                    outcome="ok",
                    observed={
                        "message": BLOCKED_REQUEST_MESSAGE,
                        "blocked_request": {
                            "method": str(request.get("method", "")),
                            "url": str(request.get("url", "")),
                        },
                    },
                )

    def _release_guard(
        self, conn: psycopg.Connection, record: RecordClaim, fallback_step_id: str
    ) -> None:
        """Release the guard at the end of a run, and record what it held back."""
        blocked = self._guard("disarm")
        self._record_blocked(conn, record, fallback_step_id, blocked)

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
        # PL-027: a cancel can leave the tab holding a half-filled form, and the page will try to
        # autosave it on the way out. Armed here, before the run stops driving the tab.
        self._arm_guard()
        with conn.transaction():
            recover_run(conn, run.id, self.worker_id, epoch, reason="run cancelled")
        for record in reconciling_records(conn, run.id):
            self._reconcile(conn, record, run, epoch)
        with conn.transaction():
            skip_pending_records(conn, run.id, self.worker_id, epoch, reason="run cancelled")
            set_run_status(conn, run.id, "cancelled", worker=self.worker_id, epoch=epoch)
        if self._last_record is not None:
            self._release_guard(conn, self._last_record, ABANDON_GUARD_STEP_ID)

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
        self._browser_touched = False
        # PL-122: the model-call ceiling is per record, not per step or per run.
        self._ai_calls_this_record = 0

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

        self._transition(conn, record, "claimed", "prechecked", "checked the row")
        # Past the precheck, the recipe's own steps run: from here the tab is being driven, so a
        # record that leaves it has something to leave behind (PL-027).
        self._browser_touched = True
        box = _Box(state="prechecked")
        terminal = self._run_steps(
            conn, record, run, epoch, recipe.get("steps", []), row, variables, box
        )
        if terminal is not None:
            return terminal

        if box.state == "prechecked":
            self._transition(conn, record, "prechecked", "parked", "no terminal step")
            return "parked"
        self._transition(conn, record, "filling", "verified", "done")
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

            # PL-026: the admin freeze switch is read at this same boundary. A frozen tenant pauses
            # the run here, with its own reason, and the record is released outside the commit
            # window; a record in the commit window is reconciled first, because the one thing that
            # is never done is stopping between `submitting` and `verified`.
            if tenant_is_frozen(conn, run.tenant_id):
                self._stop_record_for_event(
                    conn, record, run, epoch, box, ADMIN_FREEZE, ADMIN_FREEZE_REASON
                )
                with conn.transaction():
                    set_run_status(
                        conn,
                        run.id,
                        "paused",
                        pause_reason=ADMIN_FREEZE,
                        worker=self.worker_id,
                        epoch=epoch,
                    )
                logger.warning("run %s paused: an admin paused all runs", run.id)
                return "paused"

            if current.status != "running":
                if box.state == "filling":
                    self._transition(
                        conn, record, "filling", "pending", "paused", refund_attempt=True
                    )
                return "paused"

            action = step.get("action")

            if action == "branch":
                # F4: `_check` renders the condition, and an unrenderable `when` must park the
                # record rather than raise out of the loop and kill the worker.
                try:
                    verdict = self._check(
                        step.get("when", {}),
                        step_id=step.get("id"),
                        record_key=record.record_key,
                        row=row,
                        variables=variables,
                    )
                except TemplateError as exc:
                    return self._park_for_template_error(conn, record, box, exc)
                # R6: a condition check is an executor call, so an event that arrived while it ran
                # beats its verdict.
                stopped = self._drain_events(conn, record, run, epoch, box)
                if stopped is not None:
                    return stopped
                unanswered = self._no_answer(conn, record, run, epoch, box, verdict)
                if unanswered is not None:
                    return unanswered
                # PL-122: a branch that would end the row for a person asks its intent when the
                # recorded target found nothing. A commit's assert_after never reaches here.
                verdict, find_terminal = self._maybe_find_condition(
                    conn, record, run, epoch, step, row, variables, box, verdict
                )
                if find_terminal is not None:
                    return find_terminal
                branch = step.get("then", []) if verdict.passed else step.get("else", [])
                terminal = self._run_steps(conn, record, run, epoch, branch, row, variables, box)
                if terminal is not None:
                    return terminal
                continue

            if action == "approval_gate":
                when = step.get("when")
                if when:
                    # F4: same rule as a branch `when` — a gate condition that cannot be rendered
                    # parks the record instead of crashing the worker.
                    try:
                        verdict = self._check(
                            when,
                            step_id=step.get("id"),
                            record_key=record.record_key,
                            row=row,
                            variables=variables,
                        )
                    except TemplateError as exc:
                        return self._park_for_template_error(conn, record, box, exc)
                    stopped = self._drain_events(conn, record, run, epoch, box)
                    if stopped is not None:
                        return stopped
                    unanswered = self._no_answer(conn, record, run, epoch, box, verdict)
                    if unanswered is not None:
                        return unanswered
                    if not verdict.passed:
                        continue
                shown = _gate_values(step, row, variables)
                verdict = self._gate(conn, record, run, box.state, step_id=step["id"], shown=shown)
                if verdict == "approved":
                    continue
                if verdict == "timeout":
                    # Nobody decided: the record leaves the tab under the abandon guard, and the
                    # run stays paused on the request so a person can still decide it.
                    self._transition(
                        conn, record, box.state, "parked", "nobody decided the approval gate"
                    )
                    box.state = "parked"
                return "parked"

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

            # PL-113 S2: a wait step is a real check, not a command. A sign-in recipe ends with
            # one: the page after the code page must be reached, or the row parks saying the
            # sign-in did not finish. A code page that appears while the wait runs is drained
            # below, so PL-102 reads the code from the person's email exactly as before.
            if action == "wait_for":
                if box.state == "prechecked":
                    self._transition(conn, record, "prechecked", "filling", "steps begin")
                    box.state = "filling"

                if not self._pace_for_step(conn, record, run, epoch, box):
                    return "paused"

                condition = step.get("condition")
                if not isinstance(condition, dict):
                    self._transition(
                        conn, record, box.state, "parked", "a wait step carries no condition"
                    )
                    box.state = "parked"
                    return "parked"

                try:
                    verdict = self._check(
                        condition,
                        step_id=step.get("id"),
                        record_key=record.record_key,
                        row=row,
                        variables=variables,
                    )
                except TemplateError as exc:
                    return self._park_for_template_error(conn, record, box, exc)

                # R6: a check is an executor call, so an event that arrived while it ran beats its
                # verdict -- a code page is asked for its code here, not after the wait gave up.
                stopped = self._drain_events(conn, record, run, epoch, box)
                if stopped is not None:
                    return stopped

                if not verdict.passed:
                    observed = verdict.observed if isinstance(verdict.observed, dict) else {}
                    code_page = observed.get("code_page")
                    self._transition(
                        conn,
                        record,
                        box.state,
                        "parked",
                        describe_wait_failure(
                            step,
                            row,
                            variables,
                            code_page=code_page if isinstance(code_page, bool) else None,
                        ),
                        step=step,
                        outcome="failed",
                    )
                    box.state = "parked"
                    return "parked"

                with conn.transaction():
                    record_step_event(conn, record=record, step_id=step["id"], outcome="ok")
                continue

            # An executor step. Enter `filling` on the first one.
            if box.state == "prechecked":
                self._transition(conn, record, "prechecked", "filling", "steps begin")
                box.state = "filling"

            # PL-067: pace before sending the next command. A pause or cancel during the wait
            # releases the record the same way a pause at the step boundary does.
            if not self._pace_for_step(conn, record, run, epoch, box):
                return "paused"

            if step.get("commit") is True:
                terminal = self._commit_step(conn, record, run, epoch, step, row, variables, box)
            else:
                terminal = self._simple_step(conn, record, run, epoch, step, row, variables, box)
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
        box: _Box,
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

        # PL-122: a target that found nothing (or more than one) may be asked about. The prepare is
        # retried on the remembered or model-found element; no input has been sent either way.
        find_terminal: str | None = None
        if (
            prepared.status == "locator_failed"
            and _is_missing_target(prepared.error)
            and step.get("targets")
        ):
            # PL-123: a row-named item several elements share is not a model question. Choosing
            # one would re-open the wrong record and read its value back as if it were this row's.
            ambiguous = _ambiguous_row_reason(step, row, variables, prepared.error)
            if ambiguous is not None:
                self._transition(
                    conn,
                    record,
                    "filling",
                    "parked",
                    ambiguous,
                    step=step,
                    outcome="failed",
                    error=prepared.error,
                )
                return "parked"
            prepared, command, find_terminal = self._prepare_with_finding(
                conn,
                record,
                run,
                epoch,
                step,
                row,
                variables,
                box,
                command,
                prepared,
                intent=self._intent_of(step, row, variables),
                action=str(step.get("action") or ""),
                allow_destructive=bool(step.get("allow_destructive_control")),
            )
            if find_terminal is not None:
                return find_terminal

        # R6: an event that arrived while the target was resolved beats the result it produced.
        stopped = self._drain_events(conn, record, run, epoch, box)
        if stopped is not None:
            return stopped

        if prepared.status == "refused":
            # PL-014: a guard refused it, so no input was sent. Parking is legal from `filling`,
            # and that is the outcome the ticket asks for (off-origin, destructive control).
            self._transition(
                conn,
                record,
                "filling",
                "parked",
                prepared.error or "the extension refused the command",
                step=step,
                outcome="failed",
                error=prepared.error,
                rung=prepared.rung,
            )
            return "parked"

        if prepared.status != "ok":
            # PL-113 S3 + T1 review B1: a button that was busy when it was clicked (disabled,
            # aria-busy, or taking no clicks) may still do the work by itself. Two invariants decide
            # whether that can be a commit:
            #
            # * PL-107 item 11: "already done" only holds when the check was FALSE before the step
            #   and TRUE now. A confirmation left on screen from the previous row -- or a check that
            #   is always true, the exact PL-113 item 1 bug -- must never mark this row Done. The
            #   "before" read sits here because `prepare` sends no input: nothing has happened to
            #   this row yet, and the post-assertion event drain keeps its own place.
            # * AGENTS.md section 4: a commit's `verified` comes from `verify_commit`, which writes
            #   `committed_keys` in the same transaction. `filling -> verified` here would leave no
            #   key, so a re-run of the same row would save again.
            #
            # So this path needs a definite false-before, and it ends through the normal commit
            # window: write-ahead, then reconcile, whose destination check ends in `verify_commit`.
            held_before: bool | None = None
            if _is_busy_failure(prepared.error) and step.get("assert_after"):
                try:
                    held_before = self._check_before_step(
                        step["assert_after"],
                        step_id=step.get("id"),
                        record_key=record.record_key,
                        row=row,
                        variables=variables,
                    )
                except TemplateError as exc:
                    return self._park_for_template_error(conn, record, box, exc)
            if held_before is False:
                verdict = self._check(
                    step["assert_after"],
                    step_id=step.get("id"),
                    record_key=record.record_key,
                    row=row,
                    variables=variables,
                )
                stopped = self._drain_events(conn, record, run, epoch, box)
                if stopped is not None:
                    return stopped
                if verdict.passed and verdict.status == "ok":
                    return self._commit_busy_step(conn, record, run, epoch, step, box)
            self._transition(
                conn,
                record,
                "filling",
                "parked",
                describe_step_failure(step, row, variables, prepared.error),
                step=step,
                outcome="failed",
                error=prepared.error,
                rung=prepared.rung,
            )
            return "parked"

        # PL-014 item 4 (fresh confirmation), R1: only a definite "it is not there" may save.
        confirmation = step.get("assert_after")
        if confirmation:
            probe = self._probe_confirmation(
                confirmation,
                step_id=step["id"],
                record_key=record.record_key,
                row=row,
                variables=variables,
            )
            if probe in ("present", "unanswered"):
                self._transition(
                    conn,
                    record,
                    "filling",
                    "parked",
                    ALREADY_ON_PAGE_REASON if probe == "present" else UNKNOWN_CONFIRMATION_REASON,
                    step=step,
                    outcome="failed",
                )
                return "parked"

        # R6: the probe is another executor call, so an event that arrived while it ran beats the
        # result it produced: a challenge that appeared with the page stops the save here, instead
        # of being drained only after the save had already been dispatched.
        stopped = self._drain_events(conn, record, run, epoch, box)
        if stopped is not None:
            return stopped

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
            if verdict == "timeout":
                # PL-027: nobody decided, so the record is abandoned rather than committed -- under
                # the guard, and without its half-filled form reaching the destination.
                self._transition(
                    conn, record, "filling", "parked", "nobody decided the commit gate"
                )
                return "parked"
            if verdict != "approved":
                return "parked"

        with conn.transaction():
            begin_commit(conn, record.id, worker=self.worker_id, epoch=epoch)
        # The box must follow the record into the commit window. An event drained after this point
        # decides the record's fate from `box.state`, and a stale `filling` there would ask the
        # ledger for `filling -> pending` on a record that is already `submitting`.
        box.state = "submitting"
        self._crash("after_submitting")

        acked = {"value": False}

        def on_dispatched() -> None:
            with conn.transaction():
                mark_submitted_unverified(conn, record.id, worker=self.worker_id, epoch=epoch)
            box.state = "submitted_unverified"
            acked["value"] = True
            self._crash("after_ack")

        result = self.executor.dispatch(command, on_dispatched)
        self._crash("after_dispatch")

        # R6: drain BEFORE interpreting the result. The extension sends an event ahead of the
        # `result refused` it causes, so a challenge releases a pre-commit record here instead of
        # being read as a refusal and parked.
        stopped = self._drain_events(conn, record, run, epoch, box)
        if stopped is not None:
            return stopped

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
                result.error or "The click was never sent.",
                step=step,
                outcome="failed",
                error=result.error,
            )
            return "failed"
        elif result.status == "refused":
            # PL-014 + T1's answer 1: a guard refused the dispatch after a successful prepare, so
            # no input was sent and that is evidence. `submitting -> parked` is not legal, so the
            # honest ledger outcome is `failed` with the refusal as its reason.
            self._transition(
                conn,
                record,
                "submitting",
                "failed",
                result.error or "ProcessLens stopped before sending it.",
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
                "ProcessLens couldn't tell whether the click was sent",
                step=step,
                outcome="failed",
                rung=result.rung,
            )
            return self._reconcile(conn, record, run, epoch, step)

        condition = step.get("assert_after", {})
        # R1: only a real answer decides. A check nobody answered is not "the assertion failed";
        # it takes the not-passed path (reconcile), which never marks a commit verified.
        verdict = self._check(
            condition,
            step_id=step.get("id"),
            record_key=record.record_key,
            row=row,
            variables=variables,
        )
        self._crash("after_assert")

        # R6: the assertion is another executor call, so an event that arrived while it ran still
        # beats its result. Without this drain a disconnect during the assertion would reconcile
        # the record and then pause the run `canary_failed`, not `executor_disconnected`.
        stopped = self._drain_events(conn, record, run, epoch, box)
        if stopped is not None:
            return stopped

        if verdict.passed:
            observed = dict(result.observed)
            # PL-118: the page's own read-back travels with the step event, so the try card can
            # show what the page kept. It is the page's value, never the row's file value.
            read_back = verdict.observed.get("read_back")
            if read_back is not None:
                observed["read_back"] = read_back
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
                    observed=observed,
                    locator_rung=_legal_rung(result.rung),
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

        failed_observed: dict[str, Any] = {}
        read_back = verdict.observed.get("read_back")
        if read_back is not None:
            failed_observed["read_back"] = read_back
        refusal = _refusal_save_reason(run, verdict)
        if refusal is not None:
            # PL-123: the app said why it refused ("Invalid fields: Name"). That sentence is more
            # useful than "couldn't find" or a generic check failure, and it names the field a
            # person has to fix.
            refusal_words = verdict.observed.get("refusal")
            if isinstance(refusal_words, str):
                failed_observed["refusal"] = refusal_words
            self._transition(
                conn,
                record,
                "submitted_unverified",
                "reconciling",
                "the app refused the save",
                step=step,
                outcome="failed",
                observed=failed_observed or None,
            )
            self._transition(conn, record, "reconciling", "parked", refusal)
            return "parked"
        if verdict.status == "ok" and "value_equals" in condition:
            # PL-118: a read-back that the page answers "no" is a proven failure to show the
            # change. The destination might still have it, so the row parks for a person to
            # check -- it is never verified from a weaker check. Reconciling would search for a
            # row that may not exist and could re-queue a change this page already refused.
            self._transition(
                conn,
                record,
                "submitted_unverified",
                "reconciling",
                "the page didn't show it saved",
                step=step,
                outcome="failed",
                observed=failed_observed or None,
            )
            self._transition(conn, record, "reconciling", "parked", READ_BACK_FAILED_REASON)
            return "parked"
        self._transition(
            conn,
            record,
            "submitted_unverified",
            "reconciling",
            "the page didn't show it saved",
            step=step,
            outcome="failed",
            observed=failed_observed or None,
        )
        return self._reconcile(
            conn,
            record,
            run,
            epoch,
            step,
            check_sentence=describe_check(
                condition,
                row,
                variables,
                step_name=str(step.get("name") or step.get("id") or ""),
            ),
        )

    def _check_before_step(
        self,
        condition: Mapping[str, Any],
        *,
        step_id: str | None,
        record_key: str | None,
        row: Mapping[str, Any] | None = None,
        variables: Mapping[str, Any] | None = None,
    ) -> bool | None:
        """Whether the step's check already held *before* the step ran (PL-107 item 11).

        ``True`` means the confirmation was on the page before this row did anything, so it proves
        nothing about this row. ``False`` is a definite "not there yet" and is the only answer that
        lets a saved-by-the-page path run. ``None`` is no answer at all: the executor could not
        read the page, and an unknown is never treated as a false.
        """
        rendered = dict(condition)
        rendered["timeout_ms"] = 0
        verdict = self._check(
            rendered,
            step_id=step_id,
            record_key=record_key,
            row=row,
            variables=variables,
        )
        if verdict.status != "ok":
            return None
        return verdict.passed

    def _commit_busy_step(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        step: Mapping[str, Any],
        box: _Box,
    ) -> str:
        """End a busy step's row through the commit window (PL-113 S3, T1 review B1).

        Nothing was pressed, but the page showed the change after the step -- so the row enters the
        commit window exactly as a dispatched save does: write-ahead first, then reconcile. The
        destination check is what ends in ``verify_commit``, which writes ``committed_keys`` in the
        same transaction; a destination without the record sends the row back to pending rather
        than calling it Done.
        """
        with conn.transaction():
            begin_commit(conn, record.id, worker=self.worker_id, epoch=epoch)
        box.state = "submitting"
        self._transition(
            conn,
            record,
            "submitting",
            "reconciling",
            "the page saved it by itself; checking the destination",
            step=step,
            outcome="failed",
        )
        box.state = "reconciling"
        return self._reconcile(conn, record, run, epoch, step)

    def _simple_step(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        step: Mapping[str, Any],
        row: Mapping[str, Any],
        variables: Mapping[str, Any],
        box: _Box,
    ) -> str | None:
        try:
            command = self._command(record, run, self._epoch, step, row, variables, commit=False)
        except TemplateError as exc:
            self._transition(conn, record, "filling", "parked", str(exc))
            return "parked"

        result = self.executor.perform(command, None)

        # R6: an event that arrived while the step ran beats the result.
        stopped = self._drain_events(conn, record, run, epoch, box)
        if stopped is not None:
            return stopped

        # PL-123: a name several items share never picks one, even though PL-122 could point at a
        # unique snapshot token. Two products with the same name are two records.
        ambiguous = _ambiguous_row_reason(step, row, variables, result.error)
        if ambiguous is not None:
            self._transition(
                conn,
                record,
                box.state,
                "parked",
                ambiguous,
                step=step,
                outcome="failed",
                error=result.error,
            )
            box.state = "parked"
            return "parked"

        # PL-122: the recorded ladder found nothing, or more than one. Try the remembered element,
        # then one model answer, and send the step once more with the snapshot token first.
        if (
            result.status == "locator_failed"
            and _is_missing_target(result.error)
            and step.get("targets")
        ):
            result, find_terminal = self._retry_simple_step(
                conn,
                record,
                run,
                epoch,
                step,
                row,
                variables,
                box,
                command,
                result,
                intent=self._intent_of(step, row, variables),
                action=str(step.get("action") or ""),
                allow_destructive=bool(step.get("allow_destructive_control")),
            )
            if find_terminal is not None:
                return find_terminal

        if result.status == "ok":
            # S4: every executor step writes a step event, in its own transaction.
            with conn.transaction():
                record_step_event(
                    conn,
                    record=record,
                    step_id=step["id"],
                    outcome="ok",
                    observed=result.observed,
                    locator_rung=_legal_rung(result.rung),
                )
            # PL-027: a navigate is what releases the abandon guard, and the requests it refused
            # are the proof that a record the runner abandoned did not quietly reach the
            # destination. One step event per refusal, filed under this navigate's own step id.
            self._record_blocked(conn, record, str(step["id"]), result.observed.get("blocked"))
            return None

        # PL-014: a refusal always parks, whatever the recipe's `on_failure` says. A guard is a
        # rule about what the extension will not do, and a compiler-written recipe must not be able
        # to turn it into a `failed` record.
        if result.status == "refused":
            fail_to = "parked"
            # PL-097: a refusal keeps the guard's own words; only a step that tried and failed
            # gets the plain sentence.
            reason = result.error or "the step did not complete"
        else:
            fail_to = "failed" if step.get("on_failure") == "fail" else "parked"
            reason = describe_step_failure(step, row, variables, result.error)
        self._transition(
            conn,
            record,
            "filling",
            fail_to,
            reason,
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
        """R3: pass when an approved request covers exactly these values; open one otherwise.

        Returns ``approved``, ``rejected`` or ``timeout`` -- never "opened", because opening a
        request is no longer the end of this call. Under PL-027 the record HOLDS the tab at its
        commit step while a person decides, so approving runs the commit that is already set up.
        Releasing the record instead (which is what the ledger's pause path used to do) sent it back
        to `pending`, and the next claim re-ran the recipe from its first step: on Odoo that
        navigate unloads the half-filled form and Odoo autosaves it, so a record reached the
        destination with no commit step and no approval.
        """
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
        return self._await_decision(conn, record, run, state, step_id=step_id, shown=shown)

    def _await_decision(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        state: str,
        *,
        step_id: str,
        shown: dict[str, Any],
    ) -> str:
        """Hold the record at its commit step until a person decides, or the wait runs out.

        The run is `paused` and still held by this worker, so the approvals API's ``resume_run``
        moves it back to `running` and the holder carries on -- which is what makes "approve" mean
        "execute the commit step that is already prepared" rather than "start the record again".

        A wait that runs out is an abandonment, not a decision: the caller parks the record, and the
        abandon guard keeps its half-filled form from reaching the destination.
        """
        deadline = self.clock.time() + self.gate_wait_seconds
        while True:
            for decision in decisions_for_step(conn, record.id, step_id):
                if decision.shown != shown:
                    continue
                if decision.decision == "approved":
                    return "approved"
                self._transition(
                    conn,
                    record,
                    state,
                    "parked",
                    decision.reason or "a reviewer rejected this commit",
                )
                return "rejected"
            current = get_run(conn, run.id)
            if current is None or current.cancel_requested_at is not None:
                return "timeout"
            if self.clock.time() >= deadline:
                logger.warning(
                    "run %s record %s: nobody decided the commit gate within %ss",
                    run.id,
                    record.record_key,
                    self.gate_wait_seconds,
                )
                return "timeout"
            # The heartbeat thread keeps the lease alive while the tab is held; this is where a
            # lease that lapsed anyway stops us, instead of a later command failing mysteriously.
            self.lease_check()
            self.clock.pause(self.gate_poll_seconds)

    def _reconcile(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        step: Mapping[str, Any] | None = None,
        *,
        check_sentence: str | None = None,
    ) -> str:
        """Check the destination; a search error or ambiguity parks the record (S2).

        PL-047: when the commit's own check failed, ``check_sentence`` says what the runner looked
        for, and the parked reason carries it instead of the reconciler's generic error. Every
        other caller leaves it ``None`` and keeps the reconciler's words exactly.
        """
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
                    reason="added and checked",
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
                conn,
                record,
                "reconciling",
                "pending",
                "the site does not have it yet; trying again",
            )
            return "pending"

        # S2: an ambiguous match or a search error parks the record; it never fails it.
        reason = diff.error or "the site shows more than one match for this row"
        if check_sentence is not None:
            reason = parking_reason(check_sentence, run.destination_key)
        self._transition(conn, record, "reconciling", "parked", reason)
        return "parked"

    # -- PL-014: run-level events (R6) -------------------------------------

    def _take_events(self) -> list[tuple[str, str]]:
        """Drain the run-level events the executor collected. Empty when there is no drain."""
        deferred, self._deferred_events = self._deferred_events, []
        return deferred + (list(self.events()) if self.events is not None else [])

    def _put_back(self, events: list[tuple[str, str]]) -> None:
        """Hold events this pass did not act on, in order, for the next drain."""
        self._deferred_events = list(events) + self._deferred_events

    def _pause_for_events(self, conn: psycopg.Connection, run: RunClaim, epoch: int) -> bool:
        """A pause event between records stops the run before the next claim. True when it did.

        Events this pass does not act on are held back rather than dropped: an ``off_origin`` seen
        before a claim still belongs to the record the runner is about to start.
        """
        pending = self._take_events()
        unhandled: list[tuple[str, str]] = []
        for kind, detail in pending:
            if kind in PAUSE_EVENTS or kind in DISCONNECT_EVENTS:
                with conn.transaction():
                    set_run_status(
                        conn,
                        run.id,
                        "paused",
                        pause_reason=_pause_reason_for(kind),
                        worker=self.worker_id,
                        epoch=epoch,
                    )
                logger.warning("run %s paused on a %s event: %s", run.id, kind, detail)
                continue
            unhandled.append((kind, detail))
        self._put_back(unhandled)
        return len(unhandled) != len(pending)

    def _drain_events(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        box: _Box,
    ) -> str | None:
        """R6: events are applied **before** any result is interpreted.

        Returns an outcome string when the run stops, or ``None`` to carry on.

        PL-014 requirement 7b: the whole batch is taken in one pass, and a run-level event
        (:data:`PAUSE_EVENTS`/:data:`DISCONNECT_EVENTS`) anywhere in it beats an ``off_origin``
        anywhere in it. Returning the first non-``None`` outcome meant an ``off_origin`` at the head
        of the batch parked the record and the challenge, login or detach behind it was never seen,
        so the run carried on against a page that had already gone. The run-level events are
        applied first, which is what makes the ticket's outcome land: the record is released and the
        run is paused, whatever position in the batch the ``off_origin`` held.
        """
        run_level: list[tuple[str, str]] = []
        other: list[tuple[str, str]] = []
        for event in self._take_events():
            if _is_run_level_event(event[0]):
                run_level.append(event)
            else:
                other.append(event)
        for kind, detail in run_level + other:
            outcome = self._apply_event(conn, record, run, epoch, box, kind, detail)
            if outcome is not None:
                return outcome
        return None

    def _apply_event(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        box: _Box,
        kind: str,
        detail: str,
    ) -> str | None:
        """Turn one event into a record outcome and, where the ticket says so, a run pause."""
        code_detail: str | None = None
        if kind == "code_required" and box.state in WORKING_STATES:
            entered, reason = self._code_from_email()
            if entered:
                # PL-102: the code came from the person's email and the page moved on. The record
                # is released (its attempt refunded) and claimed again, and the run carries on.
                self._stop_record_for_event(
                    conn, record, run, epoch, box, kind, CODE_FROM_EMAIL_REASON
                )
                logger.info("run %s: the site's code was read from email", run.id)
                return "pending"
            # PL-112: why the read did not happen, so the task's row can say it in plain words.
            code_detail = reason or "could_not_read"
        if kind in PAUSE_EVENTS or kind in DISCONNECT_EVENTS:
            self._stop_record_for_event(conn, record, run, epoch, box, kind, detail)
            with conn.transaction():
                set_run_status(
                    conn,
                    run.id,
                    "paused",
                    pause_reason=_pause_reason_for(kind),
                    pause_detail=code_detail,
                    worker=self.worker_id,
                    epoch=epoch,
                )
            logger.warning("run %s paused on a %s event: %s", run.id, kind, detail)
            return "paused"
        if kind == "off_origin":
            reason = detail or "the page moved off the allowed origin"
            if box.state in WORKING_STATES:
                self._transition(conn, record, box.state, "parked", reason)
                box.state = "parked"
                return "parked"
        return None

    def _code_from_email(self) -> tuple[bool, str]:
        """PL-102: ask the extension to read the site's code from email and type it.

        The first value is True only when a code was typed and the code page went away. The second
        is the extension's own reason when it did not (`none`, `ambiguous`, `not_allowed`, ...), so
        the run can carry it to the row without inventing a second vocabulary. An executor that
        cannot read email, a person who has not said yes, no single code, or a wrong code: no
        entry, and the run pauses for the person as before. Nothing about the code reaches the
        runner.
        """
        ask = getattr(self.executor, "code_from_email", None)
        if ask is None:
            return False, "not_connected"
        try:
            answer = ask()
        except Exception:  # noqa: BLE001 -- the person can still type it; never crash the run
            logger.warning("reading the code from email failed", exc_info=True)
            return False, "could_not_read"
        entered = bool(answer.get("entered")) and bool(answer.get("cleared"))
        return entered, str(answer.get("reason") or "")

    def _stop_record_for_event(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        box: _Box,
        kind: str,
        detail: str,
    ) -> None:
        """The ticket's rule: before the commit the record is released; inside it, reconcile."""
        reason = detail or kind
        if box.state in WORKING_STATES:
            # 0009: a pause never failed the row, so its claim is not counted as an attempt.
            self._transition(conn, record, box.state, "pending", reason, refund_attempt=True)
            box.state = "pending"
            return
        if box.state in COMMIT_WINDOW_STATES:
            if box.state != "reconciling":
                self._transition(conn, record, box.state, "reconciling", reason)
                box.state = "reconciling"
            self._reconcile(conn, record, run, epoch)

    def _no_answer(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        box: _Box,
        verdict: ConditionResult,
    ) -> str | None:
        """R1: a condition check nobody answered decides nothing.

        Neither branch is taken; the record is parked, or released and the run paused when a
        run-level event explains the silence (R6).
        """
        if verdict.answered:
            return None
        stopped = self._drain_events(conn, record, run, epoch, box)
        if stopped is not None:
            return stopped
        if box.state in WORKING_STATES:
            self._transition(conn, record, box.state, "parked", UNANSWERED_CONDITION_REASON)
            box.state = "parked"
            return "parked"
        return "paused"

    def _park_for_template_error(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        box: _Box,
        exc: TemplateError,
    ) -> str:
        """F4: a ``when`` that cannot be rendered parks the record, never the worker.

        A branch or gate condition reaches the executor through the same renderer a commit step
        uses, so a missing column or variable there used to raise out of ``_run_steps`` and kill the
        worker. Parking keeps the record for a person, exactly as ``_commit_step`` does.
        """
        if box.state in WORKING_STATES:
            self._transition(conn, record, box.state, "parked", str(exc))
            box.state = "parked"
            return "parked"
        return "paused"

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
        refund_attempt: bool = False,
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
                refund_attempt=refund_attempt,
            )
            if step is not None:
                record_step_event(
                    conn,
                    record=record,
                    step_id=str(step.get("id", "?")),
                    outcome=outcome,
                    observed=observed,
                    error=error,
                    locator_rung=_legal_rung(rung),
                )

    def _check(
        self,
        condition: Mapping[str, Any],
        *,
        step_id: str | None,
        record_key: str | None,
        row: Mapping[str, Any] | None = None,
        variables: Mapping[str, Any] | None = None,
    ) -> ConditionResult:
        """Check a page condition, rendering its templates first (R2).

        The recipe's own duplicate search looks for ``{{row.email | lower}}``; sent unrendered, the
        extension would search for that literal text, find nothing, and create a duplicate of every
        existing record. Every condition therefore passes through :func:`_render_condition` here,
        which is the one place a condition reaches the executor.
        """
        rendered = _render_condition(dict(condition), row or {}, variables or {})
        return self.executor.check_condition(
            rendered,
            timeout_ms=condition_timeout(rendered, self.fallback_timeout_ms),
            step_id=step_id,
            record_key=record_key,
        )

    def _probe_confirmation(
        self,
        condition: Mapping[str, Any],
        *,
        step_id: str | None,
        record_key: str | None,
        row: Mapping[str, Any] | None = None,
        variables: Mapping[str, Any] | None = None,
    ) -> ConfirmationProbe:
        """PL-014 item 4: is the confirmation already on the page, before the save is sent?

        ``"unsupported"`` means the executor cannot see page state, so the gate does not run; that
        is what keeps the scripted fake's behaviour unchanged. ``"unanswered"`` parks (R1).
        """
        rendered = _render_condition(dict(condition), row or {}, variables or {})
        return self.executor.probe_confirmation(rendered, step_id=step_id, record_key=record_key)

    # -- PL-122: finding an element when the recorded target is gone --------

    def _intent_of(
        self, step: Mapping[str, Any], row: Mapping[str, Any], variables: Mapping[str, Any]
    ) -> str:
        """The step's own sentence, or its name when teaching did not write one."""
        raw = step.get("intent") or step.get("name") or step.get("id") or ""
        return _safe_text(raw, row, variables).strip() or str(step.get("id") or "")

    def _memory_candidates(
        self,
        record: RecordClaim,
        run: RunClaim,
        step_id: Any,
        origins: tuple[str, ...],
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        """Every remembered ladder this step has on any of the recipe's origins.

        A multi-origin recipe gets one candidate per site. Trying them in order is safe: the
        extension only resolves an element that is actually on the current page, so a ladder
        remembered on another site simply fails and the next one is tried.
        """
        found: list[tuple[str, list[dict[str, Any]]]] = []
        seen: set[str] = set()
        for origin in origins:
            if not origin or origin in seen:
                continue
            seen.add(origin)
            ladder = self._memory_targets(record, run, step_id, origin)
            if ladder:
                found.append((origin, ladder))
        return found

    def _memory_targets(
        self, record: RecordClaim, run: RunClaim, step_id: Any, origin: str
    ) -> list[dict[str, Any]] | None:
        if self.finder is None:
            return None
        return self.finder.memory(
            tenant_id=record.tenant_id,
            workflow_id=run.workflow_id,
            step_id=str(step_id),
            origin=origin,
        )

    def _remember(
        self,
        record: RecordClaim,
        run: RunClaim,
        step_id: Any,
        origin: str,
        ladder: list[dict[str, Any]],
        *,
        used: bool,
    ) -> None:
        if self.finder is None or not ladder:
            return
        self.finder.remember(
            tenant_id=record.tenant_id,
            workflow_id=run.workflow_id,
            step_id=str(step_id),
            origin=origin,
            ladder=ladder,
            used=used,
        )

    def _ask_for_token(
        self,
        command: StepCommand,
        record: RecordClaim,
        run: RunClaim,
        step: Mapping[str, Any],
        *,
        intent: str,
        action: str,
        allow_destructive: bool,
    ) -> tuple[str | None, list[dict[str, Any]], str, str | None]:
        """One snapshot and one model answer, or a terminal ``parked`` marker.

        Returns ``(token, ladder, origin, terminal)``. The cap is checked before the call, so the
        seventh question in one record is never asked. ``terminal`` is ``'not_here'`` or
        ``'refused'`` when the model could not point at an element, and ``'cap'`` when the record
        has spent its questions. A step parks on any of them; a condition keeps today's answer on
        ``'not_here'`` only.
        """
        if self.finder is None or not self.finder.available:
            return None, [], "", None
        if self._ai_calls_this_record >= MAX_AI_CALLS_PER_RECORD:
            return None, [], "", "cap"
        answer = self.finder.ask(
            self.executor,
            command,
            tenant_id=record.tenant_id,
            workflow_id=run.workflow_id,
            step_id=str(step.get("id") or ""),
            intent=intent,
            action=action,
            allow_destructive=allow_destructive,
        )
        if answer.called:
            self._ai_calls_this_record += 1
        if answer.status == "found" and answer.token:
            return answer.token, answer.ladder, answer.origin, None
        if answer.status in ("not_here", "refused"):
            return None, [], answer.origin, answer.status
        return None, [], answer.origin, None

    def _park_find_failure(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        box: _Box,
        step: Mapping[str, Any],
        intent: str,
    ) -> None:
        self._transition(
            conn,
            record,
            box.state,
            "parked",
            find_failed_reason(intent),
            step=step,
            outcome="failed",
        )
        box.state = "parked"

    def _retry_simple_step(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        step: Mapping[str, Any],
        row: Mapping[str, Any],
        variables: Mapping[str, Any],
        box: _Box,
        command: StepCommand,
        result: StepResult,
        *,
        intent: str,
        action: str,
        allow_destructive: bool,
    ) -> tuple[StepResult, str | None]:
        """Remembered element first, then one model answer, then the snap rung."""
        for origin, memory in self._memory_candidates(
            record, run, step.get("id"), command.allowed_origins
        ):
            retry_command = self._command(
                record, run, epoch, step, row, variables, commit=False, targets=memory
            )
            retried = self.executor.perform(retry_command, None)
            stopped = self._drain_events(conn, record, run, epoch, box)
            if stopped is not None:
                return retried, stopped
            if retried.status == "ok":
                self._remember(record, run, step.get("id"), origin, memory, used=True)
                return retried, None
            if retried.status != "locator_failed" or not _is_missing_target(retried.error):
                # A busy, covered, refused or lost element is not a missing element: the model
                # has nothing to add, and the caller's own words are the honest ones.
                return retried, None

        token, ladder, found_origin, terminal = self._ask_for_token(
            command,
            record,
            run,
            step,
            intent=intent,
            action=action,
            allow_destructive=allow_destructive,
        )
        if terminal is not None:
            self._park_find_failure(conn, record, box, step, intent)
            return result, "parked"
        if token:
            targets = [{"by": "snap", "value": token}] + list(step.get("targets") or [])
            retry_command = self._command(
                record, run, epoch, step, row, variables, commit=False, targets=targets
            )
            retried = self.executor.perform(retry_command, None)
            stopped = self._drain_events(conn, record, run, epoch, box)
            if stopped is not None:
                return retried, stopped
            if retried.status == "ok":
                self._remember(record, run, step.get("id"), found_origin, ladder, used=False)
            return retried, None
        return result, None

    def _prepare_with_finding(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        step: Mapping[str, Any],
        row: Mapping[str, Any],
        variables: Mapping[str, Any],
        box: _Box,
        command: StepCommand,
        prepared: StepResult,
        *,
        intent: str,
        action: str,
        allow_destructive: bool,
    ) -> tuple[StepResult, StepCommand, str | None]:
        """A commit's prepare, retried on a remembered or model-found element.

        ``prepared`` is returned unchanged when nothing could be found, so the caller's existing
        failure path parks with today's sentence. A gate failure or the cap parks here with the
        ticket's sentence, because the element was asked about and could not be identified.
        """
        for origin, memory in self._memory_candidates(
            record, run, step.get("id"), command.allowed_origins
        ):
            retry_command = self._command(
                record, run, epoch, step, row, variables, commit=True, targets=memory
            )
            retried = self.executor.prepare(retry_command)
            if retried.status == "ok":
                self._remember(record, run, step.get("id"), origin, memory, used=True)
                return retried, retry_command, None
            if retried.status != "locator_failed" or not _is_missing_target(retried.error):
                return retried, retry_command, None

        token, ladder, found_origin, terminal = self._ask_for_token(
            command,
            record,
            run,
            step,
            intent=intent,
            action=action,
            allow_destructive=allow_destructive,
        )
        if terminal is not None:
            self._park_find_failure(conn, record, box, step, intent)
            return prepared, command, "parked"
        if token:
            targets = [{"by": "snap", "value": token}] + list(step.get("targets") or [])
            retry_command = self._command(
                record, run, epoch, step, row, variables, commit=True, targets=targets
            )
            retried = self.executor.prepare(retry_command)
            if retried.status == "ok":
                self._remember(record, run, step.get("id"), found_origin, ladder, used=False)
            return retried, retry_command, None
        return prepared, command, None

    def _condition_memory_says_present(
        self,
        record: RecordClaim,
        run: RunClaim,
        step: Mapping[str, Any],
        row: Mapping[str, Any],
        variables: Mapping[str, Any],
        origins: tuple[str, ...],
    ) -> bool | None:
        """Re-ask the condition with the remembered element. None means no usable memory."""
        for origin, memory in self._memory_candidates(record, run, step.get("id"), origins):
            verdict = self._check(
                {"element_present": {"targets": memory}},
                step_id=step.get("id"),
                record_key=record.record_key,
                row=row,
                variables=variables,
            )
            if verdict.status == "ok" and verdict.passed:
                self._remember(record, run, step.get("id"), origin, memory, used=True)
                return True
        return None

    def _maybe_find_condition(
        self,
        conn: psycopg.Connection,
        record: RecordClaim,
        run: RunClaim,
        epoch: int,
        step: Mapping[str, Any],
        row: Mapping[str, Any],
        variables: Mapping[str, Any],
        box: _Box,
        verdict: ConditionResult,
    ) -> tuple[ConditionResult, str | None]:
        """Ask the condition's intent when its targets find nothing, for a person-ending branch."""
        kind = _person_condition_kind(step, verdict)
        if kind is None:
            return verdict, None
        intent = self._intent_of(step, row, variables)
        if self._condition_memory_says_present(
            record, run, step, row, variables, self._allowed_origins
        ):
            return _condition_with_element(verdict, kind), None

        command = self._command(record, run, epoch, step, row, variables, commit=False)
        token, ladder, found_origin, terminal = self._ask_for_token(
            command,
            record,
            run,
            step,
            intent=intent,
            action="present",
            allow_destructive=True,
        )
        if terminal is not None:
            if terminal == "not_here":
                return verdict, None
            self._park_find_failure(conn, record, box, step, intent)
            return verdict, "parked"
        if token:
            self._remember(record, run, step.get("id"), found_origin, ladder, used=False)
            return _condition_with_element(verdict, kind), None
        return verdict, None

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
        targets: list[dict[str, Any]] | None = None,
    ) -> StepCommand:
        rendered: dict[str, Any] = {"id": step["id"], "action": step["action"]}
        # The step fields that travel verbatim. `autocomplete` and `key` are PL-018's, and both
        # were missing here: the executor gates the containment completion on the witness (without
        # it a recorded autocomplete replays half-written), and a press that survived the fold
        # parks on an unknown key when its key is dropped. Neither failure is visible to a commit's
        # own assertion — a golden run stored every message with `to: []` while every record read
        # `verified`.
        for key in (
            "targets",
            "within",
            "frame",
            "clear_first",
            "option_match",
            "autocomplete",
            "key",
        ):
            if key in step:
                rendered[key] = step[key]
        # The runner always states on the wire the deadline it will wait for (`_result_budget`
        # is this value plus the ack slack). A step that names no `timeout_ms` of its own gets
        # the fallback, so the extension never falls back to a default of its own that outlives
        # the wait — an 8s navigate load wait against a 5s budget is how R4 paused a live run.
        own_timeout = step.get("timeout_ms")
        rendered["timeout_ms"] = (
            own_timeout if isinstance(own_timeout, int) else self.fallback_timeout_ms
        )
        # `navigate` carries its destination here, and it is a template (`{{crm_base}}/` or a
        # `{{row.*}}` value): unrendered, the extension would navigate nowhere.
        if "url" in step:
            rendered["url"] = _safe_text(step["url"], row, variables)
        if isinstance(rendered.get("within"), str):
            rendered["within"] = _safe_text(rendered["within"], row, variables)
        if step.get("action") == "check":
            # PL-114: a check is a yes/no, not text. The extension receives the boolean itself so
            # it never has to guess, and a cell that says neither parks the record here.
            rendered["value"] = _check_boolean(step.get("value"), row, variables, step)
            if "checked" in step:
                rendered["checked"] = step["checked"]
        elif "value" in step:
            # The runner resolves the value source; the extension receives a constant it can use
            # as-is. A bare literal here would arrive with no `kind` and the extension would refuse
            # it, and an `input` source would have nothing to read against in command mode.
            rendered["value"] = {
                "kind": "constant",
                "value": _render_value_source(step["value"], row, variables),
            }
        if "condition" in step:
            rendered["condition"] = _render_condition(step["condition"], row, variables)
        if "assert_after" in step:
            rendered["assert_after"] = _render_condition(step["assert_after"], row, variables)
        rendered["targets"] = [
            _render_target(target, row, variables)
            for target in (step.get("targets", []) if targets is None else targets)
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
            allowed_origins=self._allowed_origins,
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


def _is_busy_failure(error: str | None) -> bool:
    """Whether the extension said the control was busy rather than absent or covered (PL-113 S3)."""
    return bool(error) and "target is busy" in str(error).lower()


def _row_template_column(step: Mapping[str, Any]) -> str | None:
    """The row column a step's target ladder names, when it names one."""
    for rung in step.get("targets") or []:
        if not isinstance(rung, Mapping):
            continue
        for key in ("name", "text", "value"):
            text = rung.get(key)
            if not isinstance(text, str):
                continue
            match = re.search(r"\{\{\s*row\.([a-z0-9_]+)", text)
            if match:
                return match.group(1)
    return None


def _refusal_save_reason(run: RunClaim, verdict: ConditionResult) -> str | None:
    """The app's own refusal sentence, when a save's check just failed (PL-123)."""
    if verdict.status != "ok":
        return None
    observed = verdict.observed if isinstance(verdict.observed, dict) else {}
    refusal = observed.get("refusal")
    if not isinstance(refusal, str) or not refusal.strip():
        return None
    where = " ".join(str(run.destination_key or "").replace("-", " ").split())
    who = where.title() if where else "The app"
    return f"{who} didn't save it: {refusal.strip()[:160]}"


def _ambiguous_row_reason(
    step: Mapping[str, Any],
    row: Mapping[str, Any],
    variables: Mapping[str, Any],
    error: str | None,
) -> str | None:
    """PL-123, T1's third walk item: a name several items share never picks one.

    A ladder that matches more than one element is usually a question for `find_target`. For a
    step that opens the item named by the row's own value, it is not a question: two products
    called "Standing Desk" are two different records, and choosing one would change the wrong
    product. The row parks with the plain sentence instead.
    """
    if not error or "more than one match" not in error.lower():
        return None
    column = _row_template_column(step)
    if column is None:
        return None
    intent = _safe_text(step.get("intent") or "", row, variables)
    match = re.search(r"(?:open|find|click) the ([a-z][a-z ]*?) (?:found|called|named)", intent)
    noun = match.group(1).strip() if match else column.replace("_", " ")
    value = row.get(column, "")
    return f"More than one {noun} matches '{value}'."


def _is_missing_target(error: str | None) -> bool:
    """Whether a locator failure means no element or more than one, not a busy or covered one.

    Only the first kind may become a model question: a control that is busy is the page working,
    and a control something covers is a click problem, not a missing element.
    """
    text = (error or "").lower()
    if not text:
        return False
    if "busy" in text or "not clickable" in text or "covered" in text:
        return False
    return (
        "could not be resolved" in text
        or "no rung" in text
        or "more than one match" in text
        or "no visible, enabled match" in text
    )


def _legal_rung(value: Any) -> int | None:
    """Only the recipe ladder's five rungs fit ``step_events.locator_rung``.

    A ``snap`` rung is not one of the five, so it is logged as no rung rather than as a number the
    recipe format never defined. The executor's own ``by`` field still says how it was found.
    """
    return value if isinstance(value, int) and 1 <= value <= 5 else None


def _ends_record_for_a_person(steps: Any) -> bool:
    """Whether this branch parks or skips the row for a person, rather than ending it as Done."""
    if not isinstance(steps, list):
        return False
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("action") == "end_record" and step.get("outcome") in ("parked", "skipped"):
            return True
        if _ends_record_for_a_person(step.get("then")) or _ends_record_for_a_person(
            step.get("else")
        ):
            return True
    return False


def _person_condition_kind(step: Mapping[str, Any], verdict: ConditionResult) -> str | None:
    """The condition kind a branch may ask as an intent, or None when it must not.

    Only ``element_present`` / ``element_absent``, and only when the branch that would be taken
    ends the record for a person. A condition that could mark a row Done, or a commit's
    ``assert_after``, never reaches this helper.
    """
    if verdict.status != "ok":
        return None
    when = step.get("when")
    if not isinstance(when, dict):
        return None
    keys = {key for key in when if key != "timeout_ms"}
    if keys == {"element_absent"}:
        if verdict.passed and _ends_record_for_a_person(step.get("then")):
            return "element_absent"
        return None
    if keys == {"element_present"}:
        if not verdict.passed and _ends_record_for_a_person(step.get("else")):
            return "element_present"
        return None
    return None


def _condition_with_element(verdict: ConditionResult, kind: str) -> ConditionResult:
    """The answer the condition would have given if its target ladder had found the element."""
    return ConditionResult(passed=kind == "element_present", observed=verdict.observed, status="ok")


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


# PL-114: the words a sheet cell may use for yes or no, the same vocabulary the compiler binds on.
_CHECK_YES_WORDS = frozenset({"yes", "y", "true", "1", "x", "\u2713", "\u2714", "on"})
_CHECK_NO_WORDS = frozenset({"no", "n", "false", "0", "", "off"})


def _check_boolean(
    source: Any,
    row: Mapping[str, Any],
    variables: Mapping[str, Any],
    step: Mapping[str, Any],
) -> bool:
    """The yes/no a check step sets, or the ticket's sentence when the value says neither."""
    value = _render_value_source(source, row, variables)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _CHECK_YES_WORDS:
            return True
        if text in _CHECK_NO_WORDS:
            return False
    raise TemplateError(f"\u2018{value}\u2019 isn't a yes or no for {_check_field_name(step)}.")


def _check_field_name(step: Mapping[str, Any]) -> str:
    """The field a check sets, in plain words, for the sentence a parked row shows."""
    for target in step.get("targets") or []:
        if isinstance(target, Mapping) and target.get("by") == "role":
            name = target.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    name = step.get("name")
    if isinstance(name, str) and name.strip():
        plain = name.strip()
        for prefix in ("Check ", "Tick "):
            if plain.startswith(prefix):
                plain = plain[len(prefix) :].strip()
                break
        if plain:
            return plain
    return str(step.get("id") or "")


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
    for key in ("text_visible", "text_absent", "text_count_increases", "url_matches"):
        value = rendered.get(key)
        if isinstance(value, str) and is_template(value):
            rendered[key] = render(value, row=row, variables=variables)
    if isinstance(rendered.get("element_present"), dict):
        rendered["element_present"] = _render_selector(rendered["element_present"], row, variables)
    if isinstance(rendered.get("element_absent"), dict):
        rendered["element_absent"] = _render_selector(rendered["element_absent"], row, variables)
    # `value_equals` carries a value source, and the page comparison is only meaningful once that
    # source is a literal. Left unrendered, the extension compares the field against "".
    equals = rendered.get("value_equals")
    if isinstance(equals, dict):
        spec = dict(equals)
        if isinstance(spec.get("selector"), dict):
            spec["selector"] = _render_selector(spec["selector"], row, variables)
        if "value" in spec:
            resolved = _render_value_source(spec["value"], row, variables)
            if resolved is None:
                # F3: no fail-open. A value source that resolved to nothing used to become "",
                # which silently compared the field against an empty string; refuse instead.
                raise TemplateError("the value_equals source resolved to nothing")
            spec["value"] = str(resolved)
        rendered["value_equals"] = spec
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
