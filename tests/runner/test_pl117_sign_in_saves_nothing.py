"""PL-117 items 1 and 2: a sign-in saves nothing, and a sign-in that never finishes says so.

The compiled sign-in is the read-only recipe item 1 produces: navigate, fill the email, click
Continue, then wait for the page after the code. The ledger must not treat a finished sign-in as
"already done" for the next run of the same email, and a wait that times out must not read like a
save's failed check.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

WAIT_STEP = {
    "id": "sign-in-finished",
    "action": "wait_for",
    "name": "Wait until the sign-in finishes",
    "timeout_ms": 90_000,
    "condition": {"url_matches": r"/home(?:/.*)?$", "timeout_ms": 90_000},
}

SIGN_IN_STEPS = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/login"},
    {
        "id": "fill-email",
        "action": "fill",
        "targets": [{"by": "label", "text": "Email"}],
        "value": {"kind": "input", "column": "email"},
    },
    {
        "id": "continue",
        "action": "click",
        "targets": [{"by": "role", "role": "button", "name": "Continue"}],
    },
    WAIT_STEP,
]


def _sign_in_seed(seed: Callable[..., Seed]) -> Seed:
    base = seed(recipe(SIGN_IN_STEPS))
    base.add_input_record(email="ravi@example.test")
    return base


def _reason(conn: psycopg.Connection, run_id: uuid.UUID) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def _committed_keys(conn: psycopg.Connection, workflow_id: uuid.UUID) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM committed_keys WHERE workflow_id = %s", (workflow_id,))
        return int(cur.fetchone()[0])


def test_a_finished_sign_in_is_done_without_a_committed_key(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = _sign_in_seed(seed)
    run_id = base.create_run()

    scripted = ScriptedRun.build(conditions={WAIT_STEP["id"]: True})
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
    assert _reason(conn, run_id) == "done"
    # A sign-in saves nothing in the destination, so nothing may enter `committed_keys`; that row
    # is what would make the same email "already done" for every later run.
    assert _committed_keys(conn, base.workflow_id) == 0


def test_two_runs_of_the_same_sign_in_row_both_run_its_steps(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = _sign_in_seed(seed)
    first_run = base.create_run()

    first = ScriptedRun.build(conditions={WAIT_STEP["id"]: True})
    assert make_worker(first).run_once() is True
    assert base.states(first_run) == {"row-0001": "verified"}

    second_run = base.create_run()
    second = ScriptedRun.build(conditions={WAIT_STEP["id"]: True})
    assert make_worker(second).run_once() is True

    assert base.states(second_run) == {"row-0001": "verified"}
    ran = [command.step["id"] for command in second.executor.commands]
    assert "fill-email" in ran
    assert "continue" in ran
    assert _committed_keys(conn, base.workflow_id) == 0


def test_a_sign_in_stuck_on_the_code_page_says_exactly_that(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = _sign_in_seed(seed)
    run_id = base.create_run()

    scripted = ScriptedRun.build(
        conditions={WAIT_STEP["id"]: False},
        condition_observations={WAIT_STEP["id"]: {"code_page": True}},
    )
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
    reason = _reason(conn, run_id) or ""
    assert reason == "The sign-in didn't finish: the site never got past the code page."
    # A sign-in saves nothing, so there is nothing on the destination to check, and no regex or
    # URL reaches the row.
    assert "Dismiss" not in reason
    assert "URL" not in reason


def test_a_sign_in_stuck_elsewhere_names_the_page_it_never_reached(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = _sign_in_seed(seed)
    run_id = base.create_run()

    scripted = ScriptedRun.build(
        conditions={WAIT_STEP["id"]: False},
        condition_observations={WAIT_STEP["id"]: {"code_page": False}},
    )
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
    reason = _reason(conn, run_id) or ""
    assert reason == "The sign-in didn't finish: the site never reached the home page."
    assert "/" not in reason
