"""Leases: one worker holds a run, a stale worker cannot act, and status is re-read every step."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

import psycopg
import pytest

from packages.ledger import (
    StaleLease,
    change_state,
    claim_record,
    claim_run,
    get_record,
    heartbeat,
    set_run_status,
    take_over_run,
)
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed


def test_only_one_worker_holds_the_run(seed: Callable[..., Seed], database_url: str) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    base.create_run()

    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def claim(name: str) -> None:
        with psycopg.connect(database_url, autocommit=True) as conn:
            barrier.wait(timeout=15)
            results[name] = claim_run(conn, name)

    threads = [threading.Thread(target=claim, args=(name,)) for name in ("w1", "w2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    holders = [name for name, claimed in results.items() if claimed is not None]
    assert len(holders) == 1, f"exactly one worker may hold the run, got {sorted(results)}"


def test_stale_worker_state_change_raises_stale_lease(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    run = claim_run(conn, "w1")
    assert run is not None
    record = claim_record(conn, run_id, "w1", run.lease_epoch)
    assert record is not None

    # The kill test's problem (S6): backdate the heartbeat to make a dead worker's lease stale.
    conn.execute(
        "UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (run_id,)
    )
    epoch = take_over_run(conn, run_id, "w2", heartbeat_timeout="1 minute")
    assert epoch == 1, "a takeover increments lease_epoch"

    with pytest.raises(StaleLease):
        heartbeat(conn, run_id, "w1", 0)
    with pytest.raises(StaleLease):
        change_state(
            conn,
            record.id,
            worker="w1",
            epoch=0,
            expected="claimed",
            new="prechecked",
            reason="stale write",
        )


def test_a_second_claim_finds_nothing_while_the_run_is_held(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    base.create_run()

    assert claim_run(conn, "w1") is not None
    assert claim_run(conn, "w2") is None, "the run is already running"


def test_a_fresh_heartbeat_is_not_taken_over(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None

    assert take_over_run(conn, run_id, "w2", heartbeat_timeout="1 minute") is None


def test_status_is_reread_at_every_step(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """R4: pausing the run mid-record is noticed at the next step boundary, not after the record."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    original = scripted.executor.perform
    calls = {"n": 0}

    def perform(command, on_dispatched=None):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:  # right after the navigate, before the commit step
            set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")
        return original(command, on_dispatched)

    scripted.executor.perform = perform  # type: ignore[method-assign]

    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id)[0] == "paused"
    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "pending", "the record was released, not abandoned in filling"
    assert calls["n"] == 1, "the loop stopped before the commit step"


def test_a_run_record_carries_a_lease_epoch(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    run = claim_run(conn, "w1")
    assert run is not None
    assert run.lease_epoch == 0
    assert run.worker_id == "w1"
    assert isinstance(run_id, uuid.UUID)


def test_a_stale_worker_cannot_claim_a_record(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    """Requirement 1: the fence covers the claim, not only the state change.

    Without it a worker whose lease was taken over could claim a record it cannot act on, burn an
    attempt, and mistake the stolen run for an empty one.
    """
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None

    conn.execute(
        "UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (run_id,)
    )
    assert take_over_run(conn, run_id, "w2", heartbeat_timeout="1 minute") == 1

    with pytest.raises(StaleLease):
        claim_record(conn, run_id, "w1", 0)

    with conn.cursor() as cur:
        cur.execute("SELECT state, attempts FROM run_records WHERE run_id = %s", (run_id,))
        state, attempts = cur.fetchone()
    assert (state, attempts) == ("pending", 0), "no attempt is burned by the stale claim"


def test_a_stale_worker_cannot_change_the_run_status(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    assert claim_run(conn, "w1") is not None
    conn.execute(
        "UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (run_id,)
    )
    assert take_over_run(conn, run_id, "w2", heartbeat_timeout="1 minute") == 1

    with pytest.raises(StaleLease):
        set_run_status(conn, run_id, "completed", worker="w1", epoch=0)

    with conn.cursor() as cur:
        cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == "running", (
            "the stale worker did not finish another worker's run"
        )


def test_an_operator_pause_is_not_fenced(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    """The pause endpoint is not acting under a lease, so it is not fenced."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    set_run_status(conn, run_id, "paused", pause_reason="paused_by_operator")

    with conn.cursor() as cur:
        cur.execute("SELECT status FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == "paused"
