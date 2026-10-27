"""PL-113 S3, as corrected by T1's review (B1): a busy save is proved by the destination.

The row used to be marked `verified` from the page's check alone, which broke two invariants:

* `verified` for a commit step must come from ``verify_commit``, which writes ``committed_keys`` in
  the same transaction (AGENTS.md Â§4). This path wrote no key, so a re-run of the same row saved
  again.
* PL-107 item 11: "already done" only when the check was FALSE before the step and TRUE now. This
  path checked only "true now", so a confirmation left on screen from the previous row marked the
  row Done -- the very bug PL-113 item 1 exists to stop.

So the busy path now needs the check false before the step, and it ends through the normal commit
path: write-ahead, then reconcile, whose destination check is what writes the key.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from services.runner.executor import ConditionResult, StepResult
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import DEFAULT_STEPS, Seed, recipe

BUSY = (
    "the click target could not be resolved: the target is not clickable: "
    "the target is busy (disabled)"
)
SAVE = str(DEFAULT_STEPS[2]["id"])


def _reason(conn: psycopg.Connection, run_id) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def _committed_keys(conn: psycopg.Connection, run_id, key: str) -> int:
    """The keys this run's own record wrote (the session database is shared by every test)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM committed_keys k JOIN run_records r "
            "ON r.id = k.run_record_id WHERE r.run_id = %s AND k.idempotency_key = %s",
            (run_id, key),
        )
        return int(cur.fetchone()[0])


def _scripted_busy(*, before: bool, after: bool) -> ScriptedRun:
    """A save whose button is busy, and a check that answers `before` then `after`."""
    scripted = ScriptedRun.build()
    original_prepare = scripted.executor.prepare
    original_check = scripted.executor.check_condition
    seen = {"checks": 0}

    def prepare(command):
        if command.step.get("id") == SAVE:
            return StepResult(status="locator_failed", error=BUSY)
        return original_prepare(command)

    def check_condition(condition, **kwargs):
        if kwargs.get("step_id") == SAVE:
            seen["checks"] += 1
            return ConditionResult(passed=before if seen["checks"] == 1 else after)
        return original_check(condition, **kwargs)

    scripted.executor.prepare = prepare  # type: ignore[method-assign]
    scripted.executor.check_condition = check_condition  # type: ignore[method-assign]
    return scripted


def test_a_busy_save_whose_check_already_held_parks(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """The confirmation was on the page before the step, so it proves nothing about this row."""
    base = seed(recipe())
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    scripted = _scripted_busy(before=True, after=True)
    scripted.destination.seed("row-0001")

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
    assert _reason(conn, run_id) == "Couldn't click “Save”: the button was busy"
    assert _committed_keys(conn, run_id, "row-0001") == 0


def test_a_busy_save_the_page_completed_is_verified_with_a_committed_key(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """False before, true after, and the destination has it: a real commit, key and all."""
    base = seed(recipe())
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    scripted = _scripted_busy(before=False, after=True)
    scripted.destination.seed("row-0001")

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
    assert _reason(conn, run_id) == "added and checked"
    assert _committed_keys(conn, run_id, "row-0001") == 1


def test_a_busy_save_the_destination_does_not_have_is_not_verified(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """The page's check cannot stand in for the destination: nothing there, so no key."""
    base = seed(recipe())
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    scripted = _scripted_busy(before=False, after=False)

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id)["row-0001"] != "verified"
    assert _committed_keys(conn, run_id, "row-0001") == 0


def test_the_raw_error_is_kept_for_support(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe())
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    scripted = _scripted_busy(before=True, after=True)
    scripted.destination.seed("row-0001")

    assert make_worker(scripted).run_once() is True

    with conn.cursor() as cur:
        cur.execute(
            "SELECT error #>> '{}' FROM step_events WHERE run_record_id = "
            "(SELECT id FROM run_records WHERE run_id = %s) AND error IS NOT NULL",
            (run_id,),
        )
        errors = [row[0] for row in cur.fetchall()]
    assert BUSY in errors
