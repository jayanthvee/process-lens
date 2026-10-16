"""PL-026: the admin freeze, read by the runner at its step boundary.

The switch is one column on ``tenants``, and the runner reads it at the same boundary where it reads
``cancel_requested_at``. These tests drive the real loop over the real schema, so what they prove is
the loop's own behaviour and not a copy of it:

* a freeze pauses the run there, with ``pause_reason = 'admin_freeze'``, and the record it was
  holding goes back to ``pending`` rather than being left claimed;
* a commit that was already in the window is finished, never torn: a record in ``submitting`` is
  reconciled and verified before anything pauses, and a commit that lands while the freeze arrives
  stays verified;
* unfreezing resumes nothing -- a person does that, and until they do the run stays paused;
* the extension floor is not the runner's business at all, so a run already going finishes even when
  a floor is set under it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import freeze_runs, resume_run, set_min_extension_version, unfreeze_runs
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed


def _record_id(conn: psycopg.Connection, run_id: uuid.UUID, record_key: str) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM run_records WHERE run_id = %s AND record_key = %s",
            (run_id, record_key),
        )
        return cur.fetchone()[0]


def _walk(conn: psycopg.Connection, record_id: uuid.UUID, states: list[str]) -> None:
    for state in states:
        conn.execute("UPDATE run_records SET state = %s WHERE id = %s", (state, record_id))


def _transitions(conn: psycopg.Connection, run_id: uuid.UUID) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.from_state || '>' || t.to_state FROM run_record_transitions t "
            "JOIN run_records r ON r.id = t.run_record_id WHERE r.run_id = %s ORDER BY t.id",
            (run_id,),
        )
        return [row[0] for row in cur.fetchall()]


def _three_rows(base: Seed) -> uuid.UUID:
    for key in ("a", "b", "c"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    return base.create_run()


def test_a_frozen_tenant_starts_nothing_and_pauses_the_run(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """The first boundary is enough: no record is ever claimed into a commit while frozen."""
    base = seed()
    run_id = _three_rows(base)
    freeze_runs(conn, base.tenant_id)

    worker = make_worker(ScriptedRun.build())

    assert worker.run_once() is True
    assert base.run_status(run_id) == ("paused", "admin_freeze")
    assert set(base.states(run_id).values()) == {"pending"}, "nothing was committed while frozen"
    assert conn.execute("SELECT count(*) FROM committed_keys").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT count(*) FROM step_events e JOIN run_records r ON r.id = e.run_record_id "
            "WHERE r.run_id = %s",
            (run_id,),
        ).fetchone()[0]
        == 0
    )


def test_a_commit_that_arrives_during_the_freeze_is_not_torn(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """The freeze lands while a record's commit is being asserted.

    The commit is already in the window, so it is finished and verified; the run pauses at the
    boundary after it, and the records that had not started are left ``pending``. This is the
    ticket's "never stop between ``submitting`` and ``verified``", exercised from the outside.
    """
    base = seed()
    run_id = _three_rows(base)
    base.set_order(run_id, ["a", "b", "c"])

    scripted = ScriptedRun.build()
    original = scripted.executor.check_condition
    frozen = {"done": False}

    def check(condition, *, timeout_ms, step_id=None, record_key=None):  # type: ignore[no-untyped-def]
        # The tenant is frozen while the canary's own assertion is in flight: the save has already
        # been dispatched, so the record may be in the destination and must not be abandoned.
        if not frozen["done"]:
            frozen["done"] = True
            freeze_runs(conn, base.tenant_id)
        return original(condition, timeout_ms=timeout_ms, step_id=step_id, record_key=record_key)

    scripted.executor.check_condition = check  # type: ignore[method-assign]

    worker = make_worker(scripted)

    assert worker.run_once() is True
    states = base.states(run_id)
    assert states["a"] == "verified", "the commit in the window was finished, not torn"
    assert states["b"] == "pending" and states["c"] == "pending"
    assert base.run_status(run_id) == ("paused", "admin_freeze")
    assert "submitted_unverified>verified" in _transitions(conn, run_id)


def test_a_record_in_submitting_is_reconciled_before_the_run_pauses(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A record a stopped worker left in the commit window is reconciled, never abandoned.

    Recovery runs before any claim, so the record is checked against the destination first and the
    freeze then pauses the run with every record accounted for.
    """
    base = seed()
    for key in ("a", "b"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    stranded = _record_id(conn, run_id, "a")
    _walk(conn, stranded, ["claimed", "prechecked", "filling", "submitting"])
    scripted = ScriptedRun.build()
    scripted.destination.seed("a")
    freeze_runs(conn, base.tenant_id)

    worker = make_worker(scripted)

    assert worker.run_once() is True
    states = base.states(run_id)
    assert states["a"] == "verified", "the commit window was reconciled before anything paused"
    assert states["b"] == "pending"
    assert base.run_status(run_id) == ("paused", "admin_freeze")
    transitions = _transitions(conn, run_id)
    assert "submitting>reconciling" in transitions
    assert "reconciling>verified" in transitions


def test_unfreezing_resumes_nothing(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A person resumes each run. Until they do, an unfrozen run stays exactly where it stopped."""
    base = seed()
    run_id = _three_rows(base)
    freeze_runs(conn, base.tenant_id)

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True
    assert base.run_status(run_id) == ("paused", "admin_freeze")

    unfreeze_runs(conn, base.tenant_id)

    # Nothing in the worker picks a paused run up on its own, so a second pass has nothing to do.
    assert worker.run_once() is False
    assert base.run_status(run_id) == ("paused", "admin_freeze")
    assert set(base.states(run_id).values()) == {"pending"}

    # A person resumes it, and from there it runs to the end as any resumed run does.
    assert resume_run(conn, run_id) is True
    assert worker.run_once() is True
    assert base.run_status(run_id) == ("completed", None)
    assert set(base.states(run_id).values()) == {"verified"}


def test_the_extension_floor_never_stops_a_run_already_going(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """The floor is a gate on starting and resuming; it says nothing about a run in flight."""
    base = seed()
    run_id = _three_rows(base)
    set_min_extension_version(conn, base.tenant_id, "99.0.0")

    worker = make_worker(ScriptedRun.build())

    assert worker.run_once() is True
    assert base.run_status(run_id) == ("completed", None)
    assert set(base.states(run_id).values()) == {"verified"}
