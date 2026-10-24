"""The real executor over the PL-014 wire: R3, R4, R6, R7 (T1's plan review).

The hub socket is faked, so these prove the runner's half of the contract without a browser: every
reply is matched to its command, the wire expiry follows the command's own budget, a fresh uuid is
minted per send, and an unanswered check is not an answer.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from packages.ledger import RecordClaim, RunClaim
from services.runner.browser_executor import BrowserExecutor, ExecutorUnavailable
from services.runner.executor import StepCommand
from services.runner.run_loop import RunLoop
from services.runner.testing import FakeDestination, FakeExecutor, FakeReconciler, FrozenClock


class FakeSocket:
    """A scripted hub socket.

    ``script`` is either a list of replies, popped one per ``recv``, or a callable that receives the
    message just sent and returns the replies it provokes. When the script is empty, ``recv`` times
    out, which is how a silent extension behaves.
    """

    def __init__(
        self, script: list[Any] | Callable[[dict[str, Any]], list[Any]] | None = None
    ) -> None:
        self.script: Any = script if script is not None else []
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def send(self, payload: str) -> None:
        message = json.loads(payload)
        self.sent.append(message)
        if callable(self.script):
            self.script = list(self.script(message))

    def recv(self, timeout: float | None = None) -> str:
        if not self.script:
            raise TimeoutError("no reply")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)

    def close(self) -> None:
        self.closed = True


def wire(**fields: Any) -> dict[str, Any]:
    return {"protocol": 1, **fields}


def make_executor(
    script: list[Any] | Callable[[dict[str, Any]], list[Any]] | None = None,
    **kwargs: Any,
) -> tuple[BrowserExecutor, FakeSocket]:
    socket = FakeSocket(script)
    executor = BrowserExecutor(
        url="ws://127.0.0.1:8000/api/v1/runner/ws?session=s1",
        secret="s3cret",
        session="s1",
        worker_id="w1",
        connector=lambda url, secret: socket,
        **kwargs,
    )
    executor.connect()
    return executor, socket


def a_command(step: dict[str, Any] | None = None, *, commit: bool = False) -> StepCommand:
    return StepCommand(
        command_id="rec-1:save",
        run_id=uuid.uuid4(),
        run_record_id=uuid.uuid4(),
        record_key="a@x.test",
        idempotency_key="a@x.test",
        lease_epoch=1,
        step=step or {"id": "save", "action": "click", "timeout_ms": 3000},
        commit=commit,
        expires_at=0.0,
        allowed_origins=("http://127.0.0.1:9",),
    )


def a_record() -> RecordClaim:
    """A claimed ``run_records`` row, built by hand: the wire test needs no database."""
    return RecordClaim(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        input_record_id=uuid.uuid4(),
        record_key="a@x.test",
        idempotency_key="a@x.test",
        state="filling",
        attempts=0,
        max_attempts=3,
        commit_attempted=False,
        reason=None,
        destination_ref=None,
        claimed_by="w1",
    )


def a_run() -> RunClaim:
    """The run row that record belongs to."""
    return RunClaim(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        recipe_version_id=uuid.uuid4(),
        input_batch_id=None,
        mode="batch",
        status="running",
        pause_reason=None,
        approval_policy={},
        destination_key="crm",
        worker_id="w1",
        lease_epoch=1,
        cancel_requested_at=None,
    )


def test_a_lost_socket_is_a_lost_step_not_a_failure() -> None:
    executor, _socket = make_executor([ConnectionError("the hub went away")])
    result = executor.prepare(a_command())
    assert result.status == "lost"
    assert [kind for kind, _detail in executor.take_events()] == ["detached"]


def test_a_missing_ack_within_the_budget_is_lost() -> None:
    executor, socket = make_executor([])
    observed: list[str] = []
    result = executor.dispatch(a_command(commit=True), lambda: observed.append("fired"))
    assert result.status == "lost", "a missing ack is lost, never a failure"
    assert observed == [], "on_dispatched must not fire without an ack"
    assert socket.sent[-1]["type"] == "dispatch"


def test_on_dispatched_fires_exactly_once() -> None:
    def replies(message: dict[str, Any]) -> list[Any]:
        return [
            wire(type="ack", command_id=message["command_id"]),
            wire(type="result", command_id=message["command_id"], status="ok", observed={}),
        ]

    executor, _socket = make_executor(replies)
    observed: list[str] = []
    result = executor.dispatch(a_command(commit=True), lambda: observed.append("fired"))
    assert observed == ["fired"]
    assert result.status == "ok"


def test_a_late_ack_never_fires_the_next_commands_on_dispatched() -> None:
    """R3: a reply for a command that is not in flight is dropped, not adopted."""

    def replies(message: dict[str, Any]) -> list[Any]:
        return [
            wire(type="ack", command_id="a-command-from-the-previous-lease"),
            wire(type="ack", command_id=message["command_id"]),
            wire(type="result", command_id=message["command_id"], status="ok", observed={}),
        ]

    executor, _socket = make_executor(replies)
    observed: list[str] = []
    result = executor.dispatch(a_command(commit=True), lambda: observed.append("fired"))
    assert result.status == "ok"
    assert observed == ["fired"], "the stale ack must not have counted; the real ack fired once"


def test_a_stale_ack_alone_leaves_the_command_lost() -> None:
    executor, _socket = make_executor(
        lambda message: [wire(type="ack", command_id="not-this-command")]
    )
    observed: list[str] = []
    result = executor.dispatch(a_command(commit=True), lambda: observed.append("fired"))
    assert result.status == "lost"
    assert observed == []


def test_the_wire_deadline_is_the_reply_budget_for_every_command() -> None:
    """R4: the extension's own deadline is the instant the runner stops waiting.

    Every command stops being valid with its reply budget — the step's ``timeout_ms`` (which the
    run loop always puts on the wire) plus the ack slack — so no command is acted on after the
    runner has given up, and none is written off before the timeout it was given.
    """
    # One executor per assertion: a command that runs out of budget also ends the executor's
    # connection (R4), so the second call needs a fresh one.
    prepared, prepared_socket = make_executor([], now=lambda: 1000.0, ack_budget_seconds=5.0)
    prepared.prepare(a_command())
    assert prepared_socket.sent[-1]["expires_at"] == 1_008_000, (
        "a prepare expires with its reply budget, not 60s later"
    )

    dispatched, dispatch_socket = make_executor([], now=lambda: 1000.0, ack_budget_seconds=5.0)
    dispatched.dispatch(a_command(commit=True))
    assert dispatch_socket.sent[-1]["expires_at"] == 1_008_000, (
        "a dispatch expires with its reply budget, not 60s later"
    )


def test_a_step_without_a_timeout_reaches_the_extension_with_the_fallback() -> None:
    """The runner states the deadline it waits for, and waits longer than it.

    A step that names no ``timeout_ms`` used to reach the extension with none: the runner waited
    its bare 5s ack budget while the extension was free to spend a default of its own (8s for a
    navigation's load), and R4 then paused a live run. The wire now carries the run loop's
    fallback, and the wire deadline is that fallback plus the ack slack.
    """
    destination = FakeDestination()
    loop = RunLoop(
        worker_id="w1",
        executor=FakeExecutor(destination),
        reconciler=FakeReconciler(destination),
        clock=FrozenClock(),
        fallback_timeout_ms=10_000,
    )
    command = loop._command(
        a_record(),
        a_run(),
        1,
        {"id": "open-lead", "action": "navigate", "url": "https://crm.test/leads"},
        {},
        {},
        commit=False,
    )
    assert command.step["timeout_ms"] == 10_000, "the fallback travels on the wire"

    executor, socket = make_executor([], now=lambda: 1000.0, ack_budget_seconds=5.0)
    executor.prepare(command)
    sent = socket.sent[-1]
    assert sent["step"]["timeout_ms"] == 10_000, "the extension is given the fallback, not nothing"
    assert sent["expires_at"] == 1_015_000, "the wire deadline is the timeout plus the ack slack"
    assert sent["expires_at"] / 1000.0 - 1000.0 > sent["step"]["timeout_ms"] / 1000.0, (
        "the runner must outwait the timeout it gave the extension"
    )


def test_a_witnessed_autocomplete_fill_reaches_the_extension_with_its_witness() -> None:
    """PL-018: a fill the recorder witnessed must cross the wire with `autocomplete`.

    The executor's containment completion is gated on ``step.autocomplete === true`` and the
    compiler renders the witness onto the step, but the wire used to drop it: the fill replayed
    as an ordinary fill, the recipient was typed and never accepted, and every message was stored
    with ``to: []`` while the commit's own assertion (the toast) still passed — a golden run came
    back all-``verified`` with no recipient at all.
    """
    destination = FakeDestination()
    loop = RunLoop(
        worker_id="w1",
        executor=FakeExecutor(destination),
        reconciler=FakeReconciler(destination),
        clock=FrozenClock(),
        fallback_timeout_ms=10_000,
    )
    command = loop._command(
        a_record(),
        a_run(),
        1,
        {"id": "to", "action": "fill", "autocomplete": True},
        {},
        {},
        commit=False,
    )
    assert command.step["autocomplete"] is True, "the witness must cross the wire"


def test_a_press_step_reaches_the_extension_with_its_key() -> None:
    """PL-018: a press that survived the fold must cross the wire with the key it presses.

    The executor refuses an unknown key before sending anything, so a press that arrived without
    its ``key`` would park every record that carried one. ``press`` is in ``EXECUTOR_ACTIONS`` for
    exactly this step shape; the key is the field the action is.
    """
    destination = FakeDestination()
    loop = RunLoop(
        worker_id="w1",
        executor=FakeExecutor(destination),
        reconciler=FakeReconciler(destination),
        clock=FrozenClock(),
        fallback_timeout_ms=10_000,
    )
    command = loop._command(
        a_record(),
        a_run(),
        1,
        {"id": "accept", "action": "press", "key": "Enter"},
        {},
        {},
        commit=False,
    )
    assert command.step["key"] == "Enter", "the key must cross the wire"


def test_a_check_expires_with_its_own_polling_budget() -> None:
    executor, socket = make_executor([], now=lambda: 0.0, ack_budget_seconds=5.0)
    executor.check_condition({"text_visible": "Lead created"}, timeout_ms=8000, step_id="save")
    assert socket.sent[-1]["expires_at"] == 13_000


def test_a_takeover_workers_first_command_is_not_a_duplicate() -> None:
    """R7: a fresh uuid per send, so a restart cannot reuse an id the extension remembers."""
    command = a_command()
    first, first_socket = make_executor([])
    second, second_socket = make_executor([])
    first.prepare(command)
    second.prepare(command)

    first_id = first_socket.sent[-1]["command_id"]
    second_id = second_socket.sent[-1]["command_id"]
    assert first_id != second_id
    assert first_socket.sent[-1]["step_ref"] == "rec-1:save", (
        "the ledger's own id travels as step_ref"
    )


def test_peer_gone_loses_the_in_flight_command_at_once() -> None:
    """R6: the runner's own socket is still up, so `peer_gone` is what ends the wait."""
    executor, _socket = make_executor([wire(type="peer_gone")])
    result = executor.dispatch(a_command(commit=True))
    assert result.status == "lost"
    assert [kind for kind, _detail in executor.take_events()] == ["detached"]


def test_a_peer_unavailable_error_is_a_disconnect_not_a_step_failure() -> None:
    executor, _socket = make_executor(
        [wire(type="hub_error", code="peer_unavailable", command_id="x")]
    )
    result = executor.perform(a_command())
    assert result.status == "lost"
    assert [kind for kind, _detail in executor.take_events()] == ["detached"]


def test_an_event_between_a_dispatch_and_its_result_is_collected() -> None:
    """R6: an event is never lost because it arrived while a result was expected."""

    def replies(message: dict[str, Any]) -> list[Any]:
        return [
            wire(type="ack", command_id=message["command_id"]),
            wire(type="event", kind="challenge", detail="a turnstile appeared"),
            wire(
                type="result", command_id=message["command_id"], status="refused", error="challenge"
            ),
        ]

    executor, _socket = make_executor(replies)
    result = executor.dispatch(a_command(commit=True))
    assert result.status == "refused"
    assert executor.take_events() == [("challenge", "a turnstile appeared")]


def test_an_event_during_a_check_is_collected_and_the_check_goes_to_lost() -> None:
    executor, _socket = make_executor(
        [wire(type="event", kind="login_required", detail="signed out")]
    )
    result = executor.check_condition({"text_visible": "x"}, timeout_ms=500, step_id="save")
    assert result.answered is False, "R1: no answer is never a false answer"
    assert result.status == "lost"
    assert executor.take_events() == [("login_required", "signed out")]


def test_a_condition_with_no_answer_is_not_an_answer() -> None:
    executor, _socket = make_executor([])
    result = executor.check_condition({"text_visible": "x"}, timeout_ms=500, step_id="search")
    assert result.passed is False
    assert result.status == "lost"
    assert result.answered is False


def test_a_refused_check_is_reported_as_refused_not_as_false() -> None:
    executor, _socket = make_executor(
        lambda message: [
            wire(
                type="result",
                command_id=message["command_id"],
                status="refused",
                error="off origin",
            )
        ]
    )
    result = executor.check_condition({"text_visible": "x"}, timeout_ms=500, step_id="search")
    assert result.answered is False
    assert result.status == "refused"


def test_a_real_answer_is_an_answer() -> None:
    executor, _socket = make_executor(
        lambda message: [
            wire(type="condition", command_id=message["command_id"], passed=False, observed={})
        ]
    )
    result = executor.check_condition({"text_visible": "x"}, timeout_ms=500, step_id="search")
    assert result.answered is True
    assert result.passed is False, "the extension polled to the timeout and it never held"


def test_the_probe_separates_absent_from_unanswered() -> None:
    absent, _socket = make_executor(
        lambda message: [
            wire(type="condition", command_id=message["command_id"], passed=False, observed={})
        ]
    )
    assert absent.probe_confirmation({"text_visible": "x"}, step_id="save") == "absent"

    silent, _socket2 = make_executor([])
    assert silent.probe_confirmation({"text_visible": "x"}, step_id="save") == "unanswered"


def test_await_peer_needs_a_peer_ready() -> None:
    executor, _socket = make_executor([wire(type="peer_ready")])
    assert executor.await_peer(1.0) is True

    lonely, _socket2 = make_executor([wire(type="hub_error", code="peer_unavailable")])
    assert lonely.await_peer(1.0) is False


def test_no_reply_within_budget_pauses_the_run() -> None:
    """R4: a command that ran out of budget with the socket up must not let the runner move on."""
    executor, _socket = make_executor([])
    result = executor.prepare(a_command())
    assert result.status == "lost"
    # The same flag a lost socket raises, so the run pauses `executor_disconnected` rather than
    # sending the next command to an extension that may still be acting on this one.
    assert executor.disconnected is True
    assert [kind for kind, _detail in executor.take_events()] == ["detached"]


def test_a_lost_command_closes_the_socket_not_just_drops_it() -> None:
    """F5: a dropped socket can be reused; closing it makes the next send refuse."""
    executor, socket = make_executor([])
    result = executor.prepare(a_command())
    assert result.status == "lost"
    assert executor.disconnected is True
    assert socket.closed is True, "the socket must be closed, not merely forgotten"


def test_an_unanswered_check_raises_the_detach_flag_and_stops_sending() -> None:
    """F5: a check with no reply inside budget must leave the executor as detached as `_lost`."""
    executor, _socket = make_executor([])
    result = executor.check_condition({"text_visible": "x"}, timeout_ms=500, step_id="search")
    assert result.answered is False
    assert result.status == "lost"
    assert executor.disconnected is True, "an unanswered check is a lost executor, not a no"

    try:
        executor.prepare(a_command())
    except ExecutorUnavailable:
        pass
    else:  # pragma: no cover - the point is that a detached executor refuses to send
        raise AssertionError("a disconnected executor must not send another command")


def test_connect_refuses_when_the_hub_will_not_have_us() -> None:
    def refuse(url: str, secret: str) -> Any:
        raise OSError("connection refused")

    executor = BrowserExecutor(
        url="ws://127.0.0.1:8000/api/v1/runner/ws?session=s1",
        secret="wrong",
        session="s1",
        worker_id="w1",
        connector=refuse,
    )
    try:
        executor.connect()
    except Exception as exc:  # noqa: BLE001 - the point is the typed failure
        assert type(exc).__name__ == "ExecutorUnavailable"
    else:  # pragma: no cover - the connector always raises here
        raise AssertionError("a refused connection must not look like a connected executor")


# --- PL-090: a busy Chrome is not a lost Chrome ---------------------------------------------------


def test_a_step_whose_reply_is_late_but_arrives_is_not_lost() -> None:
    """A page that freezes Chrome (Odoo rebuilding its assets after a restart) answers late. With
    the socket still up, the runner waits for that late answer instead of pausing the whole run as
    if Chrome had gone."""

    def replies(message: dict[str, Any]) -> list[Any]:
        return [
            TimeoutError("the page kept Chrome busy past the budget"),
            wire(type="result", command_id=message["command_id"], status="ok", observed={}),
        ]

    executor, _socket = make_executor(replies)
    result = executor.prepare(a_command())

    assert result.status == "ok"
    assert executor.disconnected is False


def test_a_late_ack_after_the_budget_still_counts_for_a_dispatch() -> None:
    def replies(message: dict[str, Any]) -> list[Any]:
        return [
            TimeoutError("busy"),
            wire(type="ack", command_id=message["command_id"]),
            wire(type="result", command_id=message["command_id"], status="ok", observed={}),
        ]

    executor, _socket = make_executor(replies)
    fired: list[str] = []
    result = executor.dispatch(a_command(commit=True), lambda: fired.append("fired"))

    assert result.status == "ok"
    assert fired == ["fired"]
    assert executor.disconnected is False


def test_a_late_condition_answer_still_counts() -> None:
    def replies(message: dict[str, Any]) -> list[Any]:
        return [
            TimeoutError("busy"),
            wire(type="condition", command_id=message["command_id"], passed=True),
        ]

    executor, _socket = make_executor(replies)
    answer = executor.check_condition({"url_matches": "x"}, timeout_ms=1000)

    assert answer.status == "ok" and answer.passed is True
    assert executor.disconnected is False


def test_an_extension_silent_past_the_grace_too_is_still_lost() -> None:
    """The grace is one extension, not forever: silence past it detaches exactly as before."""
    executor, _socket = make_executor([TimeoutError("busy"), TimeoutError("still busy")])
    result = executor.prepare(a_command())

    assert result.status == "lost"
    assert executor.disconnected is True


def test_the_grace_waits_on_the_clock_not_on_a_second_timeout() -> None:
    """The second wait runs to the budget's end plus the grace, read from the executor's clock."""
    clock = {"now": 1000.0}
    waits: list[float] = []

    class Clocked(FakeSocket):
        def recv(self, timeout: float | None = None) -> str:
            waits.append(float(timeout or 0.0))
            clock["now"] += float(timeout or 0.0)
            raise TimeoutError("silent")

    socket = Clocked()
    executor = BrowserExecutor(
        url="ws://x",
        secret="s",
        session="s1",
        worker_id="w1",
        connector=lambda url, secret: socket,
        now=lambda: clock["now"],
        ack_budget_seconds=5.0,
        late_reply_grace_seconds=45.0,
    )
    executor.connect()
    executor.prepare(a_command({"id": "s1", "action": "click", "timeout_ms": 10_000}))

    assert waits == [15.0, 45.0]
