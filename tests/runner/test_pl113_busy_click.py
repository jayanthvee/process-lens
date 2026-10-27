"""PL-113 S3: a click on a busy button that the app finished by itself is not a parked row.

The commonest way a form saves is a button that disables itself the moment it is clicked. The
extension now says "the target is busy" instead of "covered", and the runner asks the step's own
check whether the work happened anyway. When it did, the row is Done; when it did not, the row parks
with the busy sentence and the raw error is kept on the step event.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from services.runner.executor import StepResult
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import DEFAULT_STEPS, Seed, recipe

BUSY = (
    "the click target could not be resolved: the target is not clickable: "
    "the target is busy (disabled)"
)


def _reason(conn: psycopg.Connection, run_id) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def _scripted_busy() -> ScriptedRun:
    scripted = ScriptedRun.build()
    step = DEFAULT_STEPS[2]
    original = scripted.executor.prepare

    def prepare(command):
        if command.step.get("id") == step["id"]:
            return StepResult(status="locator_failed", error=BUSY)
        return original(command)

    scripted.executor.prepare = prepare  # type: ignore[method-assign]
    return scripted


def test_a_busy_save_whose_check_passes_is_done(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe())
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = _scripted_busy()
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
    assert _reason(conn, run_id) == "the page did it by itself"


def test_a_busy_save_the_page_did_not_save_parks_saying_it_was_busy(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe())
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = _scripted_busy()
    scripted.executor.conditions = {"save": False}
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
    assert _reason(conn, run_id) == "Couldn't click “Save”: the button was busy"


def test_the_raw_error_is_kept_for_support(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe())
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = _scripted_busy()
    scripted.executor.conditions = {"save": False}
    assert make_worker(scripted).run_once() is True

    with conn.cursor() as cur:
        cur.execute(
            "SELECT error #>> '{}' FROM step_events WHERE run_record_id = "
            "(SELECT id FROM run_records WHERE run_id = %s) AND error IS NOT NULL",
            (run_id,),
        )
        errors = [row[0] for row in cur.fetchall()]
    assert BUSY in errors
