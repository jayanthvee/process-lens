"""PL-097: a record stopped on a step says why in words a person can act on.

The run loop keeps the extension's raw error on the step event for whoever debugs the run, while
the record's reason becomes the plain sentence from ``services.runner.step_words``. This test uses
the pilot's own failure: a click on an option the list does not have, reported by the extension as
a covered target.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import get_record
from services.runner.testing import NOT_CLICKABLE_ERROR, ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

OPTION_CLICK = {
    "id": "choose-salesperson",
    "name": "Click Priya Raman",
    "action": "click",
    "targets": [
        {"by": "role", "role": "option", "name": "{{row.salesperson}}"},
        {"by": "text_fuzzy", "value": "{{row.salesperson}}"},
    ],
}


def test_a_covered_option_click_parks_with_plain_words_and_keeps_the_raw_error(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    steps = [
        {"id": "open", "action": "navigate", "url": f"{ORIGIN}/leads/new"},
        OPTION_CLICK,
        {"id": "done", "action": "end_record", "outcome": "verified", "reason": "saved"},
    ]
    base = seed(recipe(steps))
    base.add_input_record(email="a@x.com", salesperson="Priya Ramen")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"choose-salesperson": "not_clickable"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert record.reason == "“Priya Ramen” isn't in the Salesperson list"

    assert _step_error(conn, record.id, "choose-salesperson") == NOT_CLICKABLE_ERROR


def _step_error(conn: psycopg.Connection, record_id: uuid.UUID, step_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT error FROM step_events WHERE run_record_id = %s AND step_id = %s "
            "ORDER BY id DESC LIMIT 1",
            (record_id, step_id),
        )
        row = cur.fetchone()
    return None if row is None else row[0]
