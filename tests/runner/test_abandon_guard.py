"""The abandon guard (PL-027): a record that leaves the tab must not reach the destination.

The defect these tests pin down is a real page's behaviour. Odoo autosaves a dirty form when the
document unloads, so a record the runner parked -- or a record whose commit gate nobody decided, or
a run the person cancelled -- had its half-filled form written by the destination itself, with no
commit step and (under `first_record`) no approval. The runner arms the tab's network guard when a
record leaves without committing, and this file asserts the runner's half of that: when it arms,
when it does not, and that what the guard refused reaches the ledger.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from services.runner.run_loop import ABANDON_GUARD_STEP_ID, BLOCKED_REQUEST_MESSAGE
from services.runner.testing import EventDrain, ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe


def _queue_during(scripted: ScriptedRun, method: str, kind: str, detail: str) -> None:
    """Queue a run-level event the moment the named executor call returns.

    An event pushed before the run starts is handled between records, where nothing is armed; a
    pause that arrives *while* a record is filling the page is the case these tests are about.
    """
    drain = scripted.events
    assert isinstance(drain, EventDrain)
    original = getattr(scripted.executor, method)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        drain.push(kind, detail)
        return result

    setattr(scripted.executor, method, wrapper)  # type: ignore[method-assign]


def _step_events(conn: psycopg.Connection, run_id: Any) -> list[dict[str, Any]]:
    """Every step event of a run, oldest first, with the columns these tests read."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.step_id, e.outcome, e.observed FROM step_events e "
            "JOIN run_records r ON r.id = e.run_record_id "
            "WHERE r.run_id = %s ORDER BY e.id",
            (run_id,),
        )
        return [
            {"step_id": row[0], "outcome": row[1], "observed": row[2] or {}}
            for row in cur.fetchall()
        ]


def _blocked(conn: psycopg.Connection, run_id: Any) -> list[dict[str, Any]]:
    """The step events that say the guard refused a request."""
    return [
        event
        for event in _step_events(conn, run_id)
        if event["observed"].get("message") == BLOCKED_REQUEST_MESSAGE
    ]


def test_a_parked_record_arms_the_guard(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A step that cannot be resolved parks the record, and the tab is dirty: arm."""
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.plan["save"] = "locator_failure"
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id)["k1"] == "parked"
    assert "arm" in scripted.executor.guard_calls, (
        "a parked record leaves a half-filled form behind"
    )


def test_a_verified_record_does_not_arm_the_guard(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A commit that landed is not an abandoned tab, so nothing is held back."""
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id)["k1"] == "verified"
    assert "arm" not in scripted.executor.guard_calls, "the tab committed, so nothing was held back"


def test_a_precheck_park_does_not_arm_the_guard(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A rule that parks a record before the browser is touched leaves no dirty form behind."""
    base = seed(recipe())
    base.add_input_record(key="k1", email=None)
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.states(run_id)["k1"] == "parked"
    assert "arm" not in scripted.executor.guard_calls, "the browser never opened this record"


def test_a_code_prompt_pause_keeps_the_page_open(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-074: the code page is where the person answers, so the guard must not retire it.

    The guard's retirement navigates the bound tab to ``about:blank``. For every other abandoned
    record that is the point; for a one-time-code prompt it takes the field away before the run
    page's code can reach it, and the run answers ``not_found`` forever.
    """
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    _queue_during(scripted, "prepare", "code_required", "Enter the 6-digit code we emailed you")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "code_required")
    assert base.states(run_id) == {"k1": "pending"}, "the record is released for the resume"
    assert "arm" not in scripted.executor.guard_calls, (
        "the guard would navigate the code page to about:blank"
    )


def test_a_login_pause_still_arms_the_guard(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-074 narrowed nothing else: a login wall still retires the half-filled form."""
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    _queue_during(scripted, "prepare", "login_required", "a sign-in page appeared")
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "login_required")
    assert "arm" in scripted.executor.guard_calls, (
        "every other pause leaves a half-filled form that must not autosave"
    )


def test_what_the_guard_refused_reaches_the_ledger(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """The release reports the requests it refused, and each becomes its own step event.

    The message is the point: a person reading the ledger learns that a record the runner abandoned
    tried to leave the page and was stopped, rather than inferring it from a lead that exists.
    """
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.guard_blocked = [
        {"method": "POST", "url": "http://localhost:5173/web/save"},
        {"method": "PUT", "url": "http://localhost:5173/web/beacon"},
    ]
    worker = make_worker(scripted)
    assert worker.run_once() is True

    blocked = _blocked(conn, run_id)
    assert len(blocked) == 2, _step_events(conn, run_id)
    urls = [event["observed"]["blocked_request"]["url"] for event in blocked]
    assert urls == ["http://localhost:5173/web/save", "http://localhost:5173/web/beacon"]
    assert blocked[0]["observed"]["blocked_request"]["method"] == "POST"
    assert blocked[0]["outcome"] == "ok", "a refusal by the guard is the guard working"
    assert blocked[0]["step_id"] == ABANDON_GUARD_STEP_ID
    assert scripted.executor.guard_calls[-1] == "disarm", "the run ended, so the guard was released"
