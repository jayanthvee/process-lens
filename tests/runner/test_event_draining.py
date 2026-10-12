"""PL-014b requirement 7: the run-level event drain around a commit step.

Three rules live here:

* **7a** the fresh-confirmation probe is an executor call, so the loop drains straight after it: an
  event that arrived while the probe ran beats the result the probe produced.
* **7b** ``_drain_events`` reads the whole batch, and a run-level event anywhere in it (a challenge,
  a login, a detach) beats an ``off_origin`` anywhere in it. An ``off_origin`` ahead of the
  run-level event used to park the record and hide the event behind it, so the run carried on
  against a page that had already gone.
* **C3** the commit's own ``assert_after`` check runs *after* the save was acknowledged, so an event
  that arrives while it runs is drained from the commit window and must be applied there, not after
  the assertion's verdict has been interpreted.
* the per-event meaning is otherwise unchanged: an ``off_origin`` on its own still parks the record.

Every event here is queued from *inside* the executor call it belongs to. The drain is read at fixed
points, so an event only counts as "arrived during this call" when the call itself queues it. That
is also what keeps these tests on the drain under test: a batch queued before the run starts is
handled by ``_pause_for_events`` between records, never by ``_drain_events``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import psycopg

from packages.ledger import get_record
from services.runner.testing import EventDrain, ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed

# The two events the precedence rule is about, each with the detail it carries on the wire.
OFF_ORIGIN = ("off_origin", "the page moved to https://elsewhere.test")
CHALLENGE = ("challenge", "a turnstile appeared")
DETACHED = ("detached", "the extension disconnected")


def _queue_during(scripted: ScriptedRun, method: str, *events: tuple[str, str]) -> None:
    """Queue ``events`` the moment the named executor call returns.

    ``method`` is ``"prepare"`` (the commit step's hit-test), ``"probe_confirmation"`` (the
    fresh-confirmation probe) or ``"check_condition"`` (the commit step's own ``assert_after``
    check, which runs after the save was acknowledged). The wrapper runs the real call first, then
    pushes, so the event is visible to the drain that follows that call and to none before it.
    """
    drain = scripted.events
    assert isinstance(drain, EventDrain)
    original = getattr(scripted.executor, method)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        for kind, detail in events:
            drain.push(kind, detail)
        return result

    setattr(scripted.executor, method, wrapper)  # type: ignore[method-assign]


def _one_row(seed: Callable[..., Seed]) -> tuple[Seed, uuid.UUID]:
    base = seed()
    base.add_input_record(email="ana@example.test")
    return base, base.create_run()


def test_a_challenge_during_the_confirmation_probe_stops_the_save(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """7a: an event that arrived while the probe ran beats the result the probe produced.

    The probe answers "absent", which on its own authorises the save. Without the drain after the
    probe the record is dispatched and reconciled as `verified` before the challenge is read; with
    it the event releases the pre-commit record and pauses the run, and no input is ever sent.
    """
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build(confirmation="absent")
    _queue_during(scripted, "probe_confirmation", CHALLENGE)

    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "challenge")
    assert base.states(run_id) == {"row-0001": "pending"}, (
        "the run-level event beats the probe's own result; the record is released, not dispatched"
    )
    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "pending"
    assert record.reason == CHALLENGE[1], "the release carries the event's own detail"
    assert record.commit_attempted is False, "the record never entered the commit window"
    assert scripted.destination.count_for("row-0001") == 0, "the save was never dispatched"


def test_an_off_origin_before_a_challenge_still_pauses_the_run(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """7b: the batch is read whole — the challenge behind the ``off_origin`` still wins."""
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build()
    _queue_during(scripted, "prepare", OFF_ORIGIN, CHALLENGE)

    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "challenge"), (
        "an off_origin earlier in the batch must not shadow the run-level event behind it"
    )
    assert base.states(run_id) == {"row-0001": "pending"}, (
        "the run-level event wins the record too: released, not parked by the off_origin"
    )
    assert scripted.destination.count_for("row-0001") == 0, "the save was never dispatched"


def test_an_off_origin_before_a_detach_still_disconnects_the_run(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """7b covers ``DISCONNECT_EVENTS`` as well: a detach behind an ``off_origin`` still pauses."""
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build()
    _queue_during(scripted, "prepare", OFF_ORIGIN, DETACHED)

    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "executor_disconnected")
    assert base.states(run_id) == {"row-0001": "pending"}, (
        "the record is released for the resume, not parked by the off_origin"
    )


def test_an_off_origin_on_its_own_still_parks_the_record(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """The regression guard: 7b must not change what a single ``off_origin`` means."""
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build()
    _queue_during(scripted, "prepare", OFF_ORIGIN)

    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked", "a lone off-origin page parks the record"
    assert record.reason == OFF_ORIGIN[1]
    assert base.states(run_id) == {"row-0001": "parked"}
    assert scripted.destination.count_for("row-0001") == 0, "the save was never dispatched"


def test_a_challenge_during_the_commit_assertion_parks_the_record(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """C3: a challenge while the commit's ``assert_after`` check ran leaves the record parked.

    The check the drain must follow is the runner's own ``_check(step["assert_after"])``, which runs
    **after** the save was acknowledged, so the record is inside the commit window when the event
    arrives. The ticket's promise is that the record ends ``parked`` and the run pauses
    ``challenge`` -- not ``canary_failed``, which is what the person sees today.

    ``no_commit`` is the wall: the save was acknowledged but never reached the CRM. The reconciler
    is scripted to error rather than to answer a clean "no match", because that is what the real
    run's :class:`~services.runner.reconcile.ParkingReconciler` does -- a walled page proves nothing
    about the destination, so the record parks for a person instead of being retried.

    Without the drain that follows the assertion the assertion's own verdict is interpreted first:
    the check passes (nothing scripted says otherwise), the record is verified, and the run is later
    paused ``challenge`` with the record already ``verified`` -- so removing that drain fails this
    test.
    """
    base, run_id = _one_row(seed)

    scripted = ScriptedRun.build(plan={"save": "no_commit"}, errors=("row-0001",))
    _queue_during(scripted, "check_condition", CHALLENGE)

    worker = make_worker(scripted)
    assert worker.run_once() is True

    assert base.run_status(run_id) == ("paused", "challenge"), (
        "the event's own reason must name the pause, not canary_failed"
    )
    assert base.states(run_id) == {"row-0001": "parked"}, (
        "the record is parked for a person, not released for an automatic retry, and not verified"
    )
    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert record.commit_attempted is True, "the save had been dispatched before the wall appeared"
    assert scripted.destination.count_for("row-0001") == 0, "the save never reached the CRM"
