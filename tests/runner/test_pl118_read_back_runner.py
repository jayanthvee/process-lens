"""PL-118 item 1/3: a read-back that fails parks, and a read-back that holds is Done.

The owner's edit typed 799; the page kept showing 910. The old runner reconciled and could end the
row pending or failed, never with the plain sentence a person needs. A commit checked by
``value_equals`` is proved by the page: when it holds the row is verified and its
``committed_keys`` row is written; when it does not, the row parks with
"ProcessLens couldn't confirm the change was saved." The page's read-back is kept on the step
event so the try card can show what the page actually kept.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

READ_BACK_STEPS = [
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
                "selector": {
                    "targets": [
                        {"by": "label", "text": "Sales price"},
                    ],
                    "description": "Sales price",
                },
                "value": {"kind": "input", "column": "price"},
                "case_insensitive": True,
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


def _read_back_observed(conn: psycopg.Connection, run_id) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT observed FROM step_events WHERE step_id = 'save' AND run_record_id = "
            "(SELECT id FROM run_records WHERE run_id = %s) ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        row = cur.fetchone()
    return row[0] if row is not None else None


def _keys(conn: psycopg.Connection, run_id) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM committed_keys k JOIN run_records r "
            "ON r.id = k.run_record_id WHERE r.run_id = %s",
            (run_id,),
        )
        return cur.fetchone()[0]


def _edit_run(seed: Callable[..., Seed]):
    base = seed(recipe(READ_BACK_STEPS))
    base.add_input_record(email="a@x.com", price="799")
    return base, base.create_run()


def test_a_read_back_that_holds_is_verified_with_a_committed_key(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base, run_id = _edit_run(seed)
    scripted = ScriptedRun.build(
        conditions={"save": True},
        condition_observed={
            "save": {"read_back": [{"label": "Sales price", "value": "$ 799.00", "matched": True}]}
        },
    )

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
    assert _keys(conn, run_id) == 1
    observed = _read_back_observed(conn, run_id)
    assert observed is not None
    assert observed["read_back"][0]["value"] == "$ 799.00"


def test_a_read_back_that_still_shows_the_old_value_parks_with_the_plain_sentence(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base, run_id = _edit_run(seed)
    scripted = ScriptedRun.build(
        plan={"save": "no_commit"},
        conditions={"save": False},
        condition_observed={
            "save": {"read_back": [{"label": "Sales price", "value": "910", "matched": False}]}
        },
    )

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
    assert _reason(conn, run_id) == "ProcessLens couldn't confirm the change was saved."
    observed = _read_back_observed(conn, run_id)
    assert observed is not None
    assert observed["read_back"][0]["value"] == "910"
    assert observed["read_back"][0]["matched"] is False


def test_a_read_back_that_held_before_the_step_is_not_proof(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base, run_id = _edit_run(seed)
    scripted = ScriptedRun.build(conditions={"save": True}, confirmation="present")

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
    assert _reason(conn, run_id) == "the confirmation was already on the page before saving"
