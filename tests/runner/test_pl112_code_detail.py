"""PL-112 item 4: a failed code read carries the extension's own reason to the run.

The pause reason stays the stable ``code_required`` every screen already reads. ``pause_detail``
is what lets the Tasks row say "no code arrived", "two codes arrived", or "Gmail has not been
allowed yet" instead of one generic sentence.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from services.runner.testing import EventDrain, ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe

CODE_PAGE = "Enter the 6-digit code we emailed you"


def _pause_detail(conn: psycopg.Connection, run_id: Any) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT pause_detail FROM runs WHERE id = %s", (run_id,))
        return cur.fetchone()[0]


def test_a_code_read_that_found_nothing_says_why_the_run_paused(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.code_answers.append({"entered": False, "reason": "none"})
    drain = scripted.events
    assert isinstance(drain, EventDrain)
    original = scripted.executor.prepare
    pushed: list[bool] = []

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if not pushed:
            pushed.append(True)
            drain.push("code_required", CODE_PAGE)
        return result

    scripted.executor.prepare = wrapper  # type: ignore[method-assign]
    assert make_worker(scripted).run_once() is True

    assert base.run_status(run_id) == ("paused", "code_required")
    assert _pause_detail(conn, run_id) == "none"
