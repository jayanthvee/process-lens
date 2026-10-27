"""PL-113 item 1 (blocking): a commit is Done only when its own check proves the change.

The owner's run said Done for rows where nothing changed: the commit's check was the page's chrome
(a tab label, true everywhere) and the field still showed the old value. These are the runner's three
guarantees:

* a check that already held before the step ran is not proof -- the row parks;
* a check the page does not satisfy after the save parks (typed 799, the page still shows 910);
* a commit step carrying no check at all parks with the ticket's sentence, never Done.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

EDIT_STEPS = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/products/1"},
    {
        "id": "fill-price",
        "action": "fill",
        "name": "Fill the sales price",
        "targets": [{"by": "label", "text": "Sales price"}],
        "value": {"kind": "input", "column": "price"},
    },
    {
        "id": "save",
        "action": "click",
        "commit": True,
        "assert_after": {
            "value_equals": {
                "selector": {"targets": [{"by": "label", "text": "Sales price"}]},
                "value": {"kind": "input", "column": "price"},
            },
            "timeout_ms": 8000,
        },
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
    },
]


def _reason(conn: psycopg.Connection, run_id) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def _edit_run(seed: Callable[..., Seed], steps=EDIT_STEPS):
    base = seed(recipe(steps))
    base.add_input_record(email="a@x.com", price="799")
    return base, base.create_run()


def test_a_read_back_that_still_shows_the_old_value_is_never_done(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """Typed 799, the page still shows 910 after the save: never Done."""
    base, run_id = _edit_run(seed)
    # The click was acknowledged and nothing reached the destination: the read-back failed and
    # reconciliation finds no row, so the record cannot be called Done.
    scripted = ScriptedRun.build(plan={"save": "no_commit"}, conditions={"save": False})

    assert make_worker(scripted).run_once() is True

    # Never Done: the page did not show the row's value, and reconciliation found no row either.
    # Whether the ledger parks the row or fails it depends on the evidence it has (a definite
    # absence is a failure); neither is "verified".
    assert base.states(run_id)["row-0001"] != "verified"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT outcome FROM step_events WHERE step_id = 'save' AND run_record_id = "
            "(SELECT id FROM run_records WHERE run_id = %s)",
            (run_id,),
        )
        outcomes = [row[0] for row in cur.fetchall()]
    assert "failed" in outcomes


def test_a_read_back_that_shows_the_rows_value_is_done(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    base, run_id = _edit_run(seed)
    scripted = ScriptedRun.build(conditions={"save": True})

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}


def test_a_check_that_already_held_before_the_step_is_not_proof(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A tab label is true before the save, so it cannot prove the save did anything."""
    base, run_id = _edit_run(seed)
    scripted = ScriptedRun.build(confirmation="present")

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
    assert _reason(conn, run_id) == "the confirmation was already on the page before saving"
