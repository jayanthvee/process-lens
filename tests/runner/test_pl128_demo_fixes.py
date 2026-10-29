"""PL-128: the owner's 2026-10-28 Sales and Events demo.

Events (run f5541185): the attendee form was filled, the save's check answered "not saved yet",
and then Odoo saved the new attendee by itself (it saves a form when its tab is hidden). The Save
button was gone when the run pressed it, so the row failed although Ava Patel was on the list.

Sales (run 3ee94ed1): Oak Standing Desk showed 100 -- the owner's own teaching had set it -- but
the row was skipped as "already committed in an earlier run", because a 13:40 task had once set it
to 7999. An update's key says a value was set once, not that the item still has it.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe


def _transitions(conn: psycopg.Connection, run_id) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.from_state || '>' || t.to_state FROM run_record_transitions t "
            "JOIN run_records r ON r.id = t.run_record_id WHERE r.run_id = %s ORDER BY t.id",
            (run_id,),
        )
        return [row[0] for row in cur.fetchall()]


def _committed(conn: psycopg.Connection, base: Seed, key: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM committed_keys WHERE tenant_id = %s AND idempotency_key = %s",
            (base.tenant_id, key),
        )
        return cur.fetchone()[0]


# -- Events: the page saved it by itself ---------------------------------------------------------


def test_a_save_the_page_already_made_is_verified_not_failed(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="ava.patel@pilot-events.test")
    run_id = base.create_run()

    # Not saved before the step (a definite "absent"), the Save could not be pressed, and the
    # save's own check passes now: the page saved this row itself.
    scripted = ScriptedRun.build(plan={"save": "lost_dispatch"}, confirmation="absent")
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "verified"}
    transitions = _transitions(conn, run_id)
    assert "submitting>reconciling" in transitions
    assert "reconciling>verified" in transitions
    assert "submitting>failed" not in transitions
    assert _committed(conn, base, "row-0001") == 1, "verified always writes its committed key"
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == "saved by the page and checked"


def test_a_save_that_could_not_be_pressed_and_did_not_happen_still_fails(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="ava.patel@pilot-events.test")
    run_id = base.create_run()

    scripted = ScriptedRun.build(
        plan={"save": "lost_dispatch"}, conditions={"save": False}, confirmation="absent"
    )
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "failed"}
    assert "submitting>failed" in _transitions(conn, run_id)
    assert _committed(conn, base, "row-0001") == 0


def test_without_a_definite_not_saved_before_the_step_the_check_is_never_trusted(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    # PL-107 item 11: with no proof the check was false before the step, a passing check could be
    # something left on screen, so the row fails exactly as before.
    base = seed()
    base.add_input_record(email="ava.patel@pilot-events.test")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"save": "lost_dispatch"}, confirmation="unsupported")
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-0001": "failed"}
    assert _committed(conn, base, "row-0001") == 0


# -- Sales: the site decides an update, not the ledger -------------------------------------------

UPDATE_KEY = "{{row.key}}:{{row.price}}"


def test_an_update_set_by_an_earlier_task_runs_again_and_is_checked(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe(idempotency_key=UPDATE_KEY))
    base.add_input_record(key="oak standing desk", price="7999", email="a@x.com")
    run_id = base.create_run()
    # The 13:40 task set Oak Standing Desk to 7999; the owner's teaching then set it to 100.
    base.commit_key("oak standing desk:7999", run_id)

    assert make_worker(ScriptedRun.build()).run_once() is True

    assert base.states(run_id) == {"oak standing desk": "verified"}
    status, reason = base.run_status(run_id)
    assert reason != "duplicate_detected", "setting a value again is not a second copy"
    assert status == "completed"


def test_a_create_already_added_is_still_skipped(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    # A create's key is its record key; adding it again would be a second copy, so the ledger
    # still skips it before anything runs.
    base = seed()
    base.add_input_record(email="ava.patel@pilot-events.test")
    run_id = base.create_run()
    base.commit_key("row-0001", run_id)

    assert make_worker(ScriptedRun.build()).run_once() is True

    assert base.states(run_id) == {"row-0001": "skipped"}
