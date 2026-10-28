"""PL-121: a sign-in run notices the code page at once, and carries on after the code.

The owner's Goodlabs run (67a019d5) put the code page up about 3 s after Continue, and the code
email arrived about 2 s after that. Nothing looked at the page until `sign-in-finished`'s single
90 s check ended, so the code look began from the end of the wait and the email from 06:07:12 was
already too old. Teaching worked because the recorder's own watch looks for a code page after each
action; the run had nothing like it while it waited.

The recipe below is the owner's saved one, replayed exactly: the compiler's own schema check runs
on it first, then the run is created through `create_run` (which validates it again) and driven by
the scripted page. A code typed during the wait keeps the record `filling` and the same wait goes
on; steps 1-3 are not sent again.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import psycopg

from packages.compiler.validation import validation_errors
from services.runner.executor import ConditionResult
from services.runner.testing import EventDrain, ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed

FIXTURE = Path(__file__).with_name("fixtures") / "pl121_goodlabs_sign_in.json"
WAIT_ID = "sign-in-finished"
CODE_PAGE = "Enter the 6-digit code we emailed you"
ROW_EMAIL = "person@example.com"
SIGN_IN_STUCK = "The sign-in didn't finish: the site never got past the code page."


def owner_recipe() -> dict[str, Any]:
    """The owner's saved recipe, exactly as `/recipes/{id}` returned it."""
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    recipe = document["recipe"]
    # The compiler is the authority for the recipe format, so the fixture is checked against it
    # before it is replayed: a fixture that no longer compiles is not the owner's task.
    problems = validation_errors(recipe)
    assert problems == [], problems
    return recipe


def sign_in_seed(seed: Callable[..., Seed]) -> Seed:
    base = seed(owner_recipe())
    base.add_input_record(email_or_phone_number=ROW_EMAIL)
    return base


def pause_detail(conn: psycopg.Connection, run_id: uuid.UUID) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT pause_detail FROM runs WHERE id = %s", (run_id,))
        return cur.fetchone()[0]


def committed_keys(conn: psycopg.Connection, workflow_id: uuid.UUID) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM committed_keys WHERE workflow_id = %s", (workflow_id,))
        return int(cur.fetchone()[0])


def record_reason(conn: psycopg.Connection, run_id: uuid.UUID) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def sent_steps(scripted: ScriptedRun) -> list[str]:
    return [str(command.step.get("id")) for command in scripted.executor.commands]


def script_sign_in_page(
    scripted: ScriptedRun,
    *,
    code_page_after_seconds: float = 3.0,
    finishes: bool = True,
    moves_on_after_seconds: float = 0.0,
    code_page_after_code: bool = False,
) -> dict[str, Any]:
    """The page a sign-in run sees, scripted where the fake executor has no page.

    A poll spends its own budget, exactly as a real one does, and the page is read at the end of it:
    the wall probe the extension runs after a busy poll is what sends `code_required`, so the whole
    point of the owner's run is that a 90 s poll looks once after 90 s while a 3 s poll looks after
    3 s. The owner's page put its code prompt up about 3 s after Continue
    (`code_page_after_seconds`); a poll that ends before then sees no code page, and one that ends
    after it does.

    Once the code has been typed the page either moves on (`finishes`, after
    `moves_on_after_seconds` -- a slow dashboard load) or never does. Returns the `timeout_ms` each
    wait poll was asked for, and the clock time the run asked for the code.
    """
    drain = scripted.events
    assert isinstance(drain, EventDrain)
    original = scripted.executor.check_condition
    timeouts: list[int] = []
    asked_at: list[float] = []

    def wrapper(
        condition: Mapping[str, Any],
        *,
        timeout_ms: int,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConditionResult:
        if step_id != WAIT_ID:
            return original(
                condition, timeout_ms=timeout_ms, step_id=step_id, record_key=record_key
            )
        timeouts.append(timeout_ms)
        # What the page shows when this poll gives up: a real poll looks once, at its own end.
        poll_end = scripted.clock.time() + timeout_ms / 1000.0
        typed = scripted.executor.code_requests > 0
        code_shown = poll_end >= code_page_after_seconds
        scripted.clock.pause(timeout_ms / 1000.0)
        if code_shown and not typed:
            drain.push("code_required", CODE_PAGE)
        if typed and finishes:
            moved_on = poll_end >= asked_at[0] + moves_on_after_seconds if asked_at else False
            return ConditionResult(passed=moved_on, observed={"code_page": False})
        return ConditionResult(
            passed=False,
            observed={"code_page": code_shown or (typed and code_page_after_code)},
        )

    scripted.executor.check_condition = wrapper  # type: ignore[method-assign]

    ask = scripted.executor.code_from_email

    def asking() -> dict[str, Any]:
        if not asked_at:
            asked_at.append(scripted.clock.time())
        return ask()

    scripted.executor.code_from_email = asking  # type: ignore[method-assign]
    return {"timeouts": timeouts, "asked_at": asked_at}


def test_the_code_page_is_noticed_during_the_wait_and_the_run_carries_on(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-121 item 2: the code typed during the wait ends the row Done, with no restart."""
    base = sign_in_seed(seed)
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.code_answers.append({"entered": True, "reason": "submitted", "cleared": True})
    page = script_sign_in_page(scripted)
    assert make_worker(scripted).run_once() is True

    assert scripted.executor.code_requests == 1
    # The owner's page showed its code prompt about 3 s after Continue. The run noticed it and
    # typed the code within 5 s of the wait starting, instead of at the wait's own end.
    assert page["asked_at"], "the run never asked for the code"
    assert page["asked_at"][0] <= 5.0, page["asked_at"]
    assert base.states(run_id) == {ROW_EMAIL: "verified"}
    assert record_reason(conn, run_id) == "done"
    # A sign-in saves nothing: no `committed_keys` row may make this email "already done" later.
    assert committed_keys(conn, base.workflow_id) == 0
    # The record stayed `filling`: steps 1-3 were not sent again after the code was typed.
    sent = sent_steps(scripted)
    assert sent.count("navigate-1") == 1
    assert sent.count("fill-1") == 1
    assert sent.count("click-1") == 1


def test_the_code_page_is_noticed_on_a_short_slice_not_after_the_whole_wait(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-121 item 1: the wait asks in slices, so the code page is seen within seconds."""
    base = sign_in_seed(seed)
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.code_answers.append({"entered": True, "reason": "submitted", "cleared": True})
    page = script_sign_in_page(scripted)
    assert make_worker(scripted).run_once() is True

    # The poll the runner asked for was a slice of three seconds or less, not the step's whole
    # 90 s: this is what puts the code page in front of the run while it is still waiting.
    assert page["timeouts"][0] <= 3000, page["timeouts"]
    # The owner's page showed the code about 3 s after Continue. The run asked for the code at the
    # end of that first slice -- about 3 s in -- instead of when the 90 s wait ended.
    assert page["asked_at"], "the run never asked for the code"
    assert page["asked_at"][0] <= 5.0, page["asked_at"]
    assert scripted.executor.code_requests == 1
    assert base.states(run_id) == {ROW_EMAIL: "verified"}


def test_without_a_code_the_run_pauses_for_the_person_as_before(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-121 item 2: when the code is not typed, the run pauses exactly as today."""
    base = sign_in_seed(seed)
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.code_answers.append({"entered": False, "reason": "none"})
    script_sign_in_page(scripted)
    assert make_worker(scripted).run_once() is True

    assert scripted.executor.code_requests == 1
    assert base.run_status(run_id) == ("paused", "code_required")
    assert pause_detail(conn, run_id) == "none"
    assert base.states(run_id) == {ROW_EMAIL: "pending"}


def test_a_sign_in_stuck_after_the_code_parks_with_pl117s_sentence(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-121 item 2: the code is typed and the page never moves on, so the wait ends in words."""
    base = sign_in_seed(seed)
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.code_answers.append({"entered": True, "reason": "submitted", "cleared": True})
    page = script_sign_in_page(scripted, finishes=False, code_page_after_code=True)
    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {ROW_EMAIL: "parked"}
    assert record_reason(conn, run_id) == SIGN_IN_STUCK
    # The code was read once; the extended wait did not look for it again, and did not restart
    # the row to try the sign-in from the top.
    assert scripted.executor.code_requests == 1
    assert sent_steps(scripted).count("fill-1") == 1
    # The slices add up to the step's own 90 s: the wait was sliced, not lengthened by slicing.
    assert sum(page["timeouts"]) == 90_000, page["timeouts"]


def test_a_code_page_near_the_end_of_the_wait_gets_thirty_seconds_more(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-121 item 2: less than 30 s left when the code is typed, so the wait is given 30 s.

    The owner's page put its code prompt up 3 s in, but a slow page can leave the code until the
    last moment. Typing it at 87 s of a 90 s wait leaves 3 s, which is not enough for a dashboard
    that takes 20 s to appear; the same wait is given 30 s from the moment the code was typed.
    """
    base = sign_in_seed(seed)
    run_id = base.create_run()

    scripted = ScriptedRun.build()
    scripted.executor.code_answers.append({"entered": True, "reason": "submitted", "cleared": True})
    page = script_sign_in_page(scripted, code_page_after_seconds=85.0, moves_on_after_seconds=20.0)
    assert make_worker(scripted).run_once() is True

    assert scripted.executor.code_requests == 1
    assert base.states(run_id) == {ROW_EMAIL: "verified"}
    assert sent_steps(scripted).count("fill-1") == 1
    # The wait asked for longer than its own 90 s because the code gave it 30 s more, and it did
    # so without a second sign-in: the page was still the page the code was typed on.
    assert sum(page["timeouts"]) > 90_000, page["timeouts"]
    assert page["asked_at"][0] > 80.0, page["asked_at"]


def test_a_code_page_during_a_fill_step_still_releases_and_reclaims_the_record(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-121 item 2, the other side: outside a wait, PL-102's release-and-claim still stands."""
    base = sign_in_seed(seed)
    run_id = base.create_run()

    scripted = ScriptedRun.build(conditions={WAIT_ID: True})
    drain = scripted.events
    assert isinstance(drain, EventDrain)
    original = scripted.executor.perform
    shown: list[bool] = []

    def perform(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        command = args[0]
        if not shown and command.step.get("id") == "fill-1":
            shown.append(True)
            drain.push("code_required", CODE_PAGE)
        return result

    scripted.executor.perform = perform  # type: ignore[method-assign]
    scripted.executor.code_answers.append({"entered": True, "reason": "submitted", "cleared": True})
    assert make_worker(scripted).run_once() is True

    assert scripted.executor.code_requests == 1
    assert base.states(run_id) == {ROW_EMAIL: "verified"}
    # The record was released to `pending` and claimed again from step 1, exactly as PL-102 says.
    sent = sent_steps(scripted)
    assert sent.count("navigate-1") == 2
    assert sent.count("fill-1") == 2
