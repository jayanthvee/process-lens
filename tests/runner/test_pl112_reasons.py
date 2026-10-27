"""PL-112 item 3: a finished row says what happened, in plain words.

The ledger's default reason for a verified row was "assertion passed" -- engine wording on a
person's results screen. A read-only row said "steps completed". Both are now written in the words
the screens use, and a row stored with the old text still reads plainly.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe


def _reason(conn: psycopg.Connection, run_id) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def test_a_saved_row_is_added_and_checked(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A commit step verified by its own check carries the person's words, not the engine's."""
    steps = [
        {"id": "open", "action": "navigate", "url": "http://localhost:5173/leads/new"},
        {
            "id": "fill-email",
            "action": "fill",
            "targets": [{"by": "label", "text": "Email"}],
            "value": {"kind": "input", "column": "email"},
        },
        {
            "id": "save",
            "action": "click",
            "commit": True,
            "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
            "targets": [{"by": "role", "role": "button", "name": "Save"}],
        },
    ]
    base = seed(recipe(steps))
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    assert make_worker(ScriptedRun.build()).run_once() is True

    assert _reason(conn, run_id) == "added and checked"


def test_a_read_only_row_is_done(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A task with no commit step finishes ``filling -> verified`` and says only "done"."""
    steps = [
        {"id": "open", "action": "navigate", "url": "http://localhost:5173/leads/new"},
        {
            "id": "fill-email",
            "action": "fill",
            "targets": [{"by": "label", "text": "Email"}],
            "value": {"kind": "input", "column": "email"},
        },
    ]
    base = seed(recipe(steps))
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    assert make_worker(ScriptedRun.build()).run_once() is True
    assert base.states(run_id) == {"row-0001": "verified"}
    assert _reason(conn, run_id) == "done"
