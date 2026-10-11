"""A re-run of the same batch submits nothing new: every key ends skipped."""

from __future__ import annotations

from collections.abc import Callable

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed


def test_second_run_skips_every_committed_key(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    base = seed()
    keys = [f"k{index}" for index in range(5)]
    for key in keys:
        base.add_input_record(key=key, email=f"{key}@x.com")

    first_run = base.create_run()
    scripted = ScriptedRun.build()
    worker = make_worker(scripted)
    assert worker.run_once() is True
    assert base.states(first_run) == dict.fromkeys(keys, "verified")
    keys_after_first = scripted.destination.all_keys()
    commits_after_first = scripted.destination.commits

    second_run = base.create_run()
    assert worker.run_once() is True

    assert base.states(second_run) == dict.fromkeys(keys, "skipped")
    assert scripted.destination.all_keys() == keys_after_first, "nothing new was written"
    assert scripted.destination.commits == commits_after_first, "nothing was submitted again"
    assert base.run_status(second_run) == ("completed", None)
