"""R6: a worker refuses to run a recipe whose steps could outlive its heartbeat."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from services.runner.run_loop import (
    DISPATCH_BUDGET_SECONDS,
    RunnerConfigError,
    max_step_timeout_seconds,
)
from services.runner.testing import ScriptedRun
from services.runner.worker import DEFAULT_HEARTBEAT_TIMEOUT_SECONDS, Worker
from tests.runner.conftest import Seed, recipe

# The recipe schema caps `timeout_ms` at 120 000, so this is the longest assertion a legal recipe
# can carry; the worker must still have headroom for it.
LONG_ASSERTION_STEPS = [
    {"id": "open", "action": "navigate", "url": "http://localhost:5173/x"},
    {
        "id": "save",
        "action": "click",
        "commit": True,
        "assert_after": {"text_visible": "Saved", "timeout_ms": 120_000},
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
    },
    {"id": "done", "action": "end_record", "outcome": "verified", "reason": "saved"},
]


def test_the_longest_step_is_found_including_assertions_and_branches() -> None:
    document = recipe(
        steps=[
            {"id": "open", "action": "navigate", "url": "http://localhost:5173/x"},
            {
                "id": "wait",
                "action": "wait_for",
                "condition": {"text_visible": "Ready"},
                "timeout_ms": 30_000,
            },
            {
                "id": "branch",
                "action": "branch",
                "when": {"text_visible": "Existing"},
                "then": [
                    {
                        "id": "nested-save",
                        "action": "click",
                        "commit": True,
                        "assert_after": {"text_visible": "Saved", "timeout_ms": 90_000},
                        "targets": [{"by": "role", "role": "button", "name": "Save"}],
                    }
                ],
            },
        ]
    )

    assert max_step_timeout_seconds(document) == 90.0


def test_a_worker_given_a_short_heartbeat_timeout_refuses_to_start_the_run(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    """A 2-minute assertion against a 60-second heartbeat is exactly the R6 hazard."""
    base = seed(recipe(LONG_ASSERTION_STEPS))
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build(), heartbeat_timeout_seconds=60.0)
    with pytest.raises(RunnerConfigError, match="heartbeat timeout"):
        worker.run_once()

    assert base.run_status(run_id)[0] == "running", (
        "the run was left for a correctly configured worker"
    )


def test_a_worker_with_enough_headroom_starts_the_run(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    base = seed(recipe(LONG_ASSERTION_STEPS))
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    # 3 x 120s + the dispatch budget, with a little room.
    worker = make_worker(
        ScriptedRun.build(), heartbeat_timeout_seconds=3 * 120 + DISPATCH_BUDGET_SECONDS + 1
    )
    assert worker.run_once() is True
    assert base.states(run_id) == {"row-0001": "verified"}


def test_the_default_timeout_has_headroom_for_the_largest_legal_recipe(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker]
) -> None:
    """The default covers 3 x the schema's maximum step timeout, so no legal recipe is refused."""
    base = seed(recipe(LONG_ASSERTION_STEPS))
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True
    assert base.states(run_id) == {"row-0001": "verified"}
    assert DEFAULT_HEARTBEAT_TIMEOUT_SECONDS >= 3 * 120 + DISPATCH_BUDGET_SECONDS


def test_the_timeout_is_compared_as_seconds() -> None:
    worker = Worker(
        "postgresql://unused",
        worker_id="w",
        executor=ScriptedRun.build().executor,
        heartbeat_timeout_seconds=75.0,
    )
    assert worker.heartbeat_timeout == "75.0 seconds"
