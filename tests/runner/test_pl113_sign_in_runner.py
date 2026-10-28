"""PL-113 S2: a sign-in row is Done only when the person is signed in.

The recipe's own last step is a ``wait_for`` on the page after the code page (compiled by
``packages/compiler``). The runner used to have no handling for ``wait_for`` at all: the step went
to the executor as an ordinary command and the row was marked Done whatever the page did. Now the
wait is a real check, and a sign-in that never finishes parks with a sentence that says so.

The wait also keeps the existing PL-102 path alive: an event that says the page is asking for a code
while the wait runs is drained at the step boundary, so the code is read from the person's email
exactly as it is during any other step.
"""

from __future__ import annotations

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
    "condition": {"url_matches": "/home(?:/.*)?$", "timeout_ms": 90_000},
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


def _reason(conn: psycopg.Connection, run_id) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def _sign_in_run(seed: Callable[..., Seed]) -> tuple[Seed, object]:
    base = seed(recipe(SIGN_IN_STEPS))
    base.add_input_record(email="ravi@example.test")
    return base, base.create_run()


def test_a_row_that_reaches_the_page_after_the_code_is_done(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base, run_id = _sign_in_run(seed)

    scripted = ScriptedRun.build(conditions={WAIT_STEP["id"]: True})
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
    assert _reason(conn, run_id) == "done"


def test_a_row_that_never_leaves_the_code_page_parks_and_says_so(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base, run_id = _sign_in_run(seed)

    scripted = ScriptedRun.build(conditions={WAIT_STEP["id"]: False})
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
    reason = _reason(conn, run_id) or ""
    # PL-117 item 2: a sign-in wait that times out says the site never got past the code page.
    assert reason == "The sign-in didn't finish: the site never got past the code page."
    assert "Dismiss" not in reason
    assert "URL" not in reason


def test_a_code_page_while_waiting_still_reads_the_code_from_email(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-102 is unchanged: the wait's own code page asks for the code exactly as before."""
    base, run_id = _sign_in_run(seed)
    scripted = ScriptedRun.build(conditions={WAIT_STEP["id"]: True})
    scripted.executor.code_answers = [{"entered": True, "cleared": True, "reason": "submitted"}]

    # The code page appears *while the wait is being checked*, which is when a real one would.
    original = scripted.executor.check_condition
    pushed = {"done": False}

    def check(condition, **kwargs):
        if kwargs.get("step_id") == WAIT_STEP["id"] and not pushed["done"]:
            pushed["done"] = True
            scripted.events.push("code_required", "the page is asking for a code")
        return original(condition, **kwargs)

    scripted.executor.check_condition = check  # type: ignore[method-assign]

    assert make_worker(scripted).run_once() is True

    assert scripted.executor.code_requests == 1
    assert base.states(run_id) == {"row-0001": "verified"}
