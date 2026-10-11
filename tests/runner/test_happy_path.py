"""The happy path: a batch ends with every record in an exit state, with a reason."""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe


def _reasoned(conn: psycopg.Connection, run_id) -> list[tuple[str, str | None]]:
    with conn.cursor() as cur:
        cur.execute("SELECT state, reason FROM run_records WHERE run_id = %s", (run_id,))
        return [(row[0], row[1]) for row in cur.fetchall()]


def _step_events(conn: psycopg.Connection, record_id) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT step_id FROM step_events WHERE run_record_id = %s ORDER BY id", (record_id,)
        )
        return [row[0] for row in cur.fetchall()]


def test_twenty_record_batch_verifies_every_record(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    keys = [f"row-{index:04d}" for index in range(20)]
    for key in keys:
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    worker = make_worker(scripted)

    assert worker.run_once() is True

    assert base.states(run_id) == dict.fromkeys(keys, "verified")
    assert scripted.destination.all_keys() == set(keys), "each key exists exactly once"
    assert base.run_status(run_id) == ("completed", None)
    assert all(reason for _state, reason in _reasoned(conn, run_id)), (
        "every record carries a reason"
    )


def test_a_mixed_batch_verifies_skips_and_parks(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    for key in ("k1", "k2", "k3"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    base.add_input_record(key="k4")  # no email: the precheck parks it
    base.add_input_record(key="k5")
    for key in ("k6", "k7"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()
    base.commit_key("k6", run_id)  # already committed by an earlier run
    base.commit_key("k7", run_id)

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True

    assert base.states(run_id) == {
        "k1": "verified",
        "k2": "verified",
        "k3": "verified",
        "k4": "parked",
        "k5": "parked",
        "k6": "skipped",
        "k7": "skipped",
    }
    assert base.run_status(run_id) == ("completed", None)
    assert all(reason for _state, reason in _reasoned(conn, run_id))


def test_every_executor_step_writes_a_step_event(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True

    record_id = base.record_ids(run_id)[0]
    events = _step_events(conn, record_id)
    assert events == ["open", "fill-email", "save"], "one event per executor step, in order"


def test_a_batch_with_no_commit_step_is_verified_read_only(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    steps = [
        {"id": "open", "action": "navigate", "url": "http://localhost:5173/x"},
        {
            "id": "fill-email",
            "action": "fill",
            "targets": [{"by": "label", "text": "Email"}],
            "value": {"kind": "input", "column": "email"},
        },
        {"id": "done", "action": "end_record", "outcome": "verified", "reason": "checked"},
    ]
    base = seed(recipe(steps))
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
