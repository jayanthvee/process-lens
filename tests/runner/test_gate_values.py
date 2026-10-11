"""R3 finding: an approval covers the whole rendered target ladder (finding 5)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe


def _requests(conn: psycopg.Connection, run_id: uuid.UUID) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, shown FROM approval_requests WHERE run_id = %s ORDER BY created_at, id",
            (run_id,),
        )
        return [{"id": row[0], "shown": row[1]} for row in cur.fetchall()]


def test_the_shown_values_include_the_whole_target_ladder(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A person approves every way the runner may find the element, not only the first rung."""
    steps = [
        {"id": "open", "action": "navigate", "url": "http://localhost:5173/x"},
        {
            "id": "save",
            "action": "click",
            "commit": True,
            "assert_after": {"text_visible": "Saved", "timeout_ms": 8000},
            "targets": [
                {"by": "role", "role": "button", "name": "Save"},
                {"by": "testid", "value": "lead-save"},
                {"by": "text_fuzzy", "value": "Save"},
            ],
        },
        {"id": "done", "action": "end_record", "outcome": "verified", "reason": "saved"},
    ]
    base = seed(recipe(steps, approval_policy={"commit": "first_record"}))
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True

    shown = _requests(conn, run_id)[0]["shown"]
    assert "targets" in shown, "the whole ladder is shown, not only the first rung"
    assert len(shown["targets"]) == 3
    assert shown["targets"][0]["by"] == "role"
    assert shown["targets"][1] == {"by": "testid", "value": "lead-save"}
    assert shown["targets"][2] == {"by": "text_fuzzy", "value": "Save"}
    assert shown["record"] == {"email": "k1@x.com", "key": "k1"}
