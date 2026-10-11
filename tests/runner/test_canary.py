"""The canary: the first record of a run to reach a commit step guards the whole batch.

Under R2 a locator failure happens in ``prepare``, before the write-ahead, so the record is parked
never commit-attempted. That is what lets the canary catch it.
"""

from __future__ import annotations

from collections.abc import Callable

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed


def test_locator_failure_on_first_commit_pauses_canary_failed(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    base = seed()
    for key in ("k1", "k2", "k3"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(plan={"save": "locator_failure"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "canary_failed")
    states = list(base.states(run_id).values())
    assert states.count("parked") == 1, "the canary record is parked, not failed"
    assert states.count("pending") == 2, "no other record is claimed after the canary fails"


def test_a_later_locator_failure_does_not_pause_the_run(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    """Only the first commit is the canary; a later locator failure just parks that record."""
    base = seed()
    for key in ("k1", "k2", "k3"):
        base.add_input_record(key=key, email=f"{key}@x.com")
    run_id = base.create_run()

    # One record's commit cannot find its target; the others commit normally. k1 is claimed
    # it is the canary and it verifies; k2's failure is therefore not a canary failure.
    base.set_order(run_id, ["k1", "k2", "k3"])
    scripted = ScriptedRun.build(plan={"k2:save": "locator_failure"})
    worker = make_worker(scripted)
    assert worker.run_once() is True

    states = base.states(run_id)
    assert states["k2"] == "parked"
    assert [states[key] for key in ("k1", "k3")] == ["verified", "verified"]
    assert base.run_status(run_id) == ("completed", None)
