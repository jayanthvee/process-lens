"""PL-114 item 1: the runner resolves a check's yes/no and sends the extension a boolean.

A check step reads its value from the row like any fill does, but the extension must never guess:
the command carries ``value: true`` or ``value: false``. A cell that is not a yes/no parks the
record with the ticket's sentence rather than ticking the wrong box.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

CHECK_STEPS = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/events/new"},
    {
        "id": "tick",
        "action": "check",
        "name": "Check 'Limit registrations'",
        "targets": [{"by": "role", "role": "checkbox", "name": "Limit registrations"}],
        "value": {"kind": "input", "column": "published"},
    },
    {"id": "done", "action": "end_record", "outcome": "verified", "reason": "Event created."},
]


def _reason(conn: psycopg.Connection, run_id) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def _run(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    published: object,
):
    base = seed(recipe(CHECK_STEPS))
    base.add_input_record(email="a@x.com", published=published)
    run_id = base.create_run()
    scripted = ScriptedRun.build()
    assert make_worker(scripted).run_once() is True
    return base, run_id, scripted


def _check_command(scripted: ScriptedRun):
    return next(
        command for command in scripted.executor.commands if command.step.get("id") == "tick"
    )


def test_a_yes_column_ticks_the_box_and_the_row_is_verified(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    base, run_id, scripted = _run(seed, make_worker, "yes")

    assert base.states(run_id) == {"row-0001": "verified"}
    assert _check_command(scripted).step["value"] is True


def test_a_value_that_is_not_yes_or_no_parks_with_the_sentence(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base, run_id, _scripted = _run(seed, make_worker, "maybe")

    assert base.states(run_id) == {"row-0001": "parked"}
    assert _reason(conn, run_id) == "\u2018maybe\u2019 isn't a yes or no for Limit registrations."


@pytest.mark.parametrize(("published", "expected"), [("", False), ("X", True), ("\u2713", True)])
def test_the_yes_no_words_coerce_to_a_boolean(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    published: str,
    expected: bool,
) -> None:
    base, run_id, scripted = _run(seed, make_worker, published)

    assert base.states(run_id) == {"row-0001": "verified"}
    assert _check_command(scripted).step["value"] is expected
