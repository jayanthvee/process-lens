"""PL-102: a run that meets an emailed-code page gets the code from the person's Gmail by itself.

Before pausing for a person, the runner asks the extension to read the code from email and type it.
When that worked and the code page went away, the record is released and claimed again, and the run
carries on with nobody touching it. Anything else -- the person has not said yes yet, no single code
was found, the code was wrong -- pauses the run on the code page exactly as before (PL-068).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from services.runner.testing import EventDrain, ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe

CODE_PAGE = "Enter the 6-digit code we emailed you"


def _code_page_once(scripted: ScriptedRun) -> None:
    """The site asks for a code the first time the record reaches the page, and not again."""
    drain = scripted.events
    assert isinstance(drain, EventDrain)
    original = scripted.executor.prepare
    shown: list[bool] = []

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if not shown:
            shown.append(True)
            drain.push("code_required", CODE_PAGE)
        return result

    scripted.executor.prepare = wrapper  # type: ignore[method-assign]


def test_a_code_read_from_email_lets_the_run_carry_on(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.code_answers.append({"entered": True, "reason": "submitted", "cleared": True})
    _code_page_once(scripted)
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert scripted.executor.code_requests == 1
    assert base.run_status(run_id)[0] == "completed"
    assert base.states(run_id) == {"k1": "verified"}


def test_without_the_persons_yes_the_run_pauses_for_the_code_as_before(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.code_answers.append({"entered": False, "reason": "not_allowed"})
    _code_page_once(scripted)
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert scripted.executor.code_requests == 1
    assert base.run_status(run_id) == ("paused", "code_required")
    assert base.states(run_id) == {"k1": "pending"}
    assert "arm" not in scripted.executor.guard_calls, "the code page stays for the person"


def test_a_wrong_code_that_leaves_the_page_in_place_pauses_for_the_person(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.code_answers.append(
        {"entered": True, "reason": "submitted", "cleared": False}
    )
    _code_page_once(scripted)
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "code_required")
    assert base.states(run_id) == {"k1": "pending"}


def test_a_login_page_is_never_answered_from_email(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """Only a code page is. A password is never read, typed or looked for."""
    base = seed(recipe())
    base.add_input_record(key="k1", email="k1@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(events=[])
    drain = scripted.events
    assert isinstance(drain, EventDrain)
    original = scripted.executor.prepare

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        drain.push("login_required", "a sign-in page appeared")
        return result

    scripted.executor.prepare = wrapper  # type: ignore[method-assign]
    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert scripted.executor.code_requests == 0
    assert base.run_status(run_id) == ("paused", "login_required")
