"""The condition-answer rules from T1's plan review: R1, R2 and R6.

R1 is the dangerous one. A `check` the browser never answered must never be read as "the condition
is false": a duplicate-search branch that takes its `else` creates a record that already exists.
R2 is the other: an unrendered condition searches for the literal ``{{row.email | lower}}``.
R6 is the ordering: the event beats the result it caused.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
import pytest

from packages.ledger import get_record
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

# A duplicate search: if the row is already in the results, the record is skipped.
TEMPLATED_BRANCH = {
    "id": "already-there",
    "action": "branch",
    "when": {
        "element_present": {
            "targets": [
                {"by": "role", "role": "cell", "name": "{{row.email | lower}}"},
                {"by": "text_fuzzy", "value": "{{row.email | lower}}"},
            ]
        }
    },
    "then": [
        {
            "id": "skip-it",
            "action": "end_record",
            "outcome": "skipped",
            "reason": "already present",
        }
    ],
}

SEARCH_STEPS: list[dict[str, Any]] = [
    {
        "id": "search",
        "action": "click",
        "targets": [{"by": "role", "role": "button", "name": "Search"}],
    },
    TEMPLATED_BRANCH,
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/leads/new"},
]


def _one_row(seed: Callable[..., Seed], **kwargs: Any):
    # The default rule parks a row with no email; every row here has one, so it never fires.
    base = seed(recipe(SEARCH_STEPS))
    base.add_input_record(email="Ana@Example.test", **kwargs)
    run_id = base.create_run()
    return base, run_id


def test_a_lost_branch_check_takes_neither_branch(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """R1: the check never answered, so the record is parked, not switched into the else branch."""
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build(condition_statuses={"already-there": "lost"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    states = base.states(run_id)
    assert states == {"row-0001": "parked"}, "neither branch ran; the record needs a person"
    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert "could not answer" in (record.reason or "")
    assert "skipped" not in " ".join(str(value) for value in states.values())


def test_a_refused_branch_check_takes_neither_branch(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build(condition_statuses={"already-there": "refused"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert "could not answer" in (record.reason or "")


def test_an_answered_branch_check_still_branches_normally(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """The control: a real `True` answer still skips, so R1 did not break the working path."""
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build(conditions={"already-there": True})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id) == {"row-0001": "skipped"}


def test_a_templated_branch_condition_reaches_the_executor_rendered(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """R2: the extension must search for the record's email, never for ``{{row.email | lower}}``."""
    base, _run_id = _one_row(seed)

    scripted = ScriptedRun.build(conditions={"already-there": True})
    seen: list[dict[str, Any]] = []

    def record(condition: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        seen.append(dict(condition))
        from services.runner.executor import ConditionResult

        return ConditionResult(passed=False)

    scripted.executor.check_condition = record  # type: ignore[method-assign]
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert seen, "the branch's condition must have reached the executor"
    rendered = repr(seen[0])
    assert "{{row.email" not in rendered, "an unrendered placeholder reached the browser"
    assert "ana@example.test" in rendered, "the record's own email must be in the condition"


def test_a_challenge_releases_a_pre_commit_record_instead_of_parking(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """R6: the event beats the `refused` result, and the ticket says the record is released."""
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build(plan={"search": "refused"})
    scripted.events.push("challenge", "a turnstile appeared")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    states = base.states(run_id)
    assert states == {"row-0001": "pending"}, "a pre-commit record is released, not parked"
    assert base.run_status(run_id)[0] == "paused"
    assert base.run_status(run_id)[1] == "challenge"


def test_an_unanswered_confirmation_probe_parks(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """R1 at the probe: no answer means park, never "absent", which would authorise the save."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(confirmation="unanswered")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert "could not tell" in (record.reason or "")
    assert base.states(run_id) == {"row-0001": "parked"}
    assert scripted.destination.count_for("row-0001") == 0, "no save was attempted"


def test_a_stale_confirmation_parks_before_the_save(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """PL-014 item 4: a confirmation already on the page proves nothing about this save."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(confirmation="present")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert "already on the page" in (record.reason or "")
    assert record.commit_attempted is False, "the record never entered the commit window"
    assert scripted.destination.count_for("row-0001") == 0


def test_an_absent_confirmation_lets_the_save_go_ahead(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """The control: a real "not there" answer still saves and verifies."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(confirmation="absent")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
    assert scripted.destination.count_for("row-0001") == 1


def test_a_fake_executor_cannot_report_a_stale_confirmation(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """The gate that keeps the existing suite meaningful: no page, no probe, no behaviour change."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    assert scripted.executor.confirmation == "unsupported"
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}


def test_peer_gone_loses_the_in_flight_command_at_once(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """R6: the runner's socket is up, so the event is what stops the run."""
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build()
    scripted.events.push("detached", "the extension disconnected")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "executor_disconnected")


def test_a_policy_blocked_event_pauses_the_run_with_policy_blocked(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build()
    scripted.events.push("policy_blocked", "your administrator's Chrome policy blocks ProcessLens")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "policy_blocked")
    assert base.states(run_id) == {"row-0001": "pending"}


def test_a_window_hidden_event_pauses_the_run_and_releases_the_row(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """PL-040: a hidden work window is the person's to fix; the row is released, never failed."""
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build(plan={"search": "refused"})
    scripted.events.push("window_hidden", "the ProcessLens window is hidden behind other windows")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "window_hidden")
    assert base.states(run_id) == {"row-0001": "pending"}


def test_a_login_required_event_pauses_the_run_with_login_required(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build()
    scripted.events.push("login_required", "a sign-in page appeared")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "login_required")


def test_an_off_origin_event_parks_the_record(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.events.push("off_origin", "the page moved to https://elsewhere.test")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert "elsewhere" in (record.reason or "")
    assert base.run_status(run_id)[0] != "paused", (
        "an off-origin page parks the record, not the run"
    )


def test_a_refused_non_commit_step_parks_even_when_the_recipe_says_fail(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """T1's answer 1: a guard refusal always parks, whatever `on_failure` says."""
    steps = [
        {
            "id": "delete-it",
            "action": "click",
            "on_failure": "fail",
            "targets": [{"by": "role", "role": "button", "name": "Delete lead"}],
        },
        {"id": "open", "action": "navigate", "url": f"{ORIGIN}/leads/new"},
    ]
    base = seed(recipe(steps))
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"delete-it": "refused"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}, "a refusal is never turned into failed"


def test_a_refused_commit_dispatch_fails_from_submitting(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """A guard that fires after a successful prepare: no input was sent, and that is evidence."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"save": "refused"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "failed"
    assert scripted.destination.count_for("row-0001") == 0


def test_the_internal_error_pause_hands_the_run_back(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
    monkeypatch: Any,
) -> None:
    """PL-013 follow-up: a resume after internal_error queues the run at once."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    import services.runner.run_loop as loop_module
    from packages.ledger import IllegalTransition

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise IllegalTransition("the guard refused this write")

    # Every ledger write is refused, so parking fails too and the loop must pause `internal_error`.
    monkeypatch.setattr(loop_module, "change_state", refuse)

    scripted = ScriptedRun.build()
    worker = make_worker(scripted)
    assert worker.run_once() is True

    status, reason = base.run_status(run_id)
    assert (status, reason) == ("paused", "internal_error")
    assert _worker_id(conn, run_id) is None, "the run was handed back, so a resume can claim it"


def _worker_id(conn: psycopg.Connection, run_id: Any) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT worker_id FROM runs WHERE id = %s", (run_id,))
        return cur.fetchone()[0]


def test_the_parking_reconciler_never_returns_a_record_to_pending(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """PL-014: with no reconcile definition, "not found" proves nothing, so a person looks."""
    from services.runner.reconcile import ParkingReconciler, outcome_of

    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.conditions["save"] = False  # the assertion never holds
    worker = make_worker(scripted, reconciler=ParkingReconciler())
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert "check the destination" in (record.reason or "")

    diff = ParkingReconciler().search(record)
    assert outcome_of(diff) == "parked", "an unverifiable search never says `pending`"


UNRENDERABLE_BRANCH: list[dict[str, Any]] = [
    {
        "id": "bad-branch",
        "action": "branch",
        "when": {"text_visible": "{{row.no_such_column}}"},
        "then": [{"id": "skip", "action": "end_record", "outcome": "skipped", "reason": "matched"}],
    },
]

UNRENDERABLE_GATE: list[dict[str, Any]] = [
    {
        "id": "gate",
        "action": "approval_gate",
        "reason": "a person must approve this commit",
        "when": {"text_visible": "{{row.no_such_column}}"},
    },
]


def test_a_branch_when_that_cannot_be_rendered_parks_the_record(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """F4: a template error in a branch ``when`` parks the record; it must not crash the worker."""
    base = seed(recipe(UNRENDERABLE_BRANCH))
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True, "a TemplateError must not escape the worker"

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert "no column" in (record.reason or "")
    assert base.states(run_id) == {"row-0001": "parked"}, "neither branch ran; a person decides"


def test_a_gate_when_that_cannot_be_rendered_parks_the_record(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """F4: the same rule for a gate condition — park, never crash the worker."""
    base = seed(recipe(UNRENDERABLE_GATE))
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert base.run_status(run_id)[0] != "paused", "the record parks; the run is not paused"


def test_a_none_value_source_raises_rather_than_comparing_against_empty() -> None:
    """F3: a value source resolving to None must refuse, never compare against an empty string."""
    from services.runner.run_loop import _render_condition
    from services.runner.templates import TemplateError

    condition = {
        "value_equals": {
            "selector": {"targets": [{"by": "testid", "value": "email"}]},
            "value": {"kind": "constant", "value": None},
        }
    }
    with pytest.raises(TemplateError):
        _render_condition(condition, {}, {})


def test_the_worker_does_not_claim_while_the_executor_is_disconnected(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker]
) -> None:
    """F5: a disconnected executor must not be handed a run it cannot drive."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.disconnected = True  # type: ignore[attr-defined]
    worker = make_worker(scripted)
    assert worker.run_once() is False, "a disconnected executor must not claim a run"
    assert base.run_status(run_id)[0] == "queued", "the run is left for a connected worker"
    assert base.states(run_id) == {"row-0001": "pending"}


def test_the_worker_re_attaches_before_it_gives_up(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker]
) -> None:
    """F5 + the ticket's reconnect criterion: a dropped executor is re-attached, then the run runs.

    The guard alone would stop the worker for the rest of its life, and the ticket's disconnect case
    ("reconnecting and resuming continues the run") could never be met. The retry is the other half.
    """
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.disconnected = True  # type: ignore[attr-defined]
    attempts: list[int] = []

    def reconnect() -> bool:
        attempts.append(1)
        # The second attempt finds the extension back, as it does once it re-attaches.
        if len(attempts) == 1:
            return False
        scripted.executor.disconnected = False  # type: ignore[attr-defined]
        return True

    worker = make_worker(scripted, reconnect=reconnect)
    assert worker.run_once() is False, "no claim while the executor is still gone"
    assert worker.run_once() is True, "once it re-attaches the run is claimed and processed"
    assert base.states(run_id) == {"row-0001": "verified"}
