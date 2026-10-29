"""PL-130: an update whose save was cut off is done again and read back, not parked.

The owner's price run of 2026-10-29 lost its worker in the commit window. The next worker found the
row in `submitting`, and with no way to look at the site (the production reconciler always says so)
parked it "could not verify the save automatically; check the destination" -- though Oak Standing
Desk showed the right price. An update cannot add a second copy of anything: setting the price
again is harmless, and the re-open reads it back. So a cut-off update is retried; a create is
still parked for a person, because doing it again could add it twice.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest

from packages.ledger import release_dead_worker_runs
from services.runner.reconcile import ParkingReconciler
from services.runner.testing import (
    CrashHooks,
    FakeDestination,
    FakeExecutor,
    FrozenClock,
    SimulatedCrash,
)
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe
from tests.runner.test_pl130_approve_before_save import STEPS


def _worker(database_url: str, worker_id: str, crash_at: tuple[str, ...] = ()) -> Worker:
    destination = FakeDestination()
    return Worker(
        database_url,
        worker_id=worker_id,
        executor=FakeExecutor(destination, conditions={"save-when-offered": True}),
        reconciler=ParkingReconciler(),
        clock=FrozenClock(),
        crash_hooks=CrashHooks(crash_at) if crash_at else None,
        heartbeat_interval=3600.0,
    )


def _transitions(conn: psycopg.Connection, run_id) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.from_state || '>' || t.to_state FROM run_record_transitions t "
            "JOIN run_records r ON r.id = t.run_record_id WHERE r.run_id = %s ORDER BY t.id",
            (run_id,),
        )
        return [row[0] for row in cur.fetchall()]


def test_a_cut_off_update_is_done_again_and_checked(
    seed: Callable[..., Seed], conn: psycopg.Connection, database_url: str
) -> None:
    base = seed(
        recipe(
            STEPS,
            approval_policy={"commit": "none"},
            record_key="{{row.product | lower}}",
            idempotency_key="{{row.product | lower}}:{{row.sales_price}}",
        )
    )
    base.add_input_record(
        key="oak", email="a@x.com", product="Oak Standing Desk", sales_price="7999"
    )
    run_id = base.create_run()

    with pytest.raises(SimulatedCrash):
        _worker(database_url, "deploy-worker-1", crash_at=("after_submitting",)).run_once()
    release_dead_worker_runs(conn, "deploy-worker-1")
    assert _worker(database_url, "deploy-worker-2").run_once() is True

    assert base.states(run_id) == {"oak standing desk": "verified"}
    transitions = _transitions(conn, run_id)
    assert "reconciling>pending" in transitions
    assert "reconciling>parked" not in transitions


def test_a_cut_off_create_is_still_left_for_a_person(
    seed: Callable[..., Seed], conn: psycopg.Connection, database_url: str
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    with pytest.raises(SimulatedCrash):
        _worker(database_url, "deploy-worker-3", crash_at=("after_submitting",)).run_once()
    release_dead_worker_runs(conn, "deploy-worker-3")
    assert _worker(database_url, "deploy-worker-4").run_once() is True

    assert base.states(run_id) == {"row-0001": "parked"}
