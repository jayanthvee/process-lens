"""The real browser executor: the runner's half of the PL-014 command wire.

One WebSocket to the hub, relayed to the person's Chrome tab. The four methods of
:class:`~services.runner.executor.Executor` map onto three message types (``prepare``,
``dispatch``, ``check``), and the extension answers with ``ack``, ``result`` and
``condition``. ``apps/extension/src/shared/command-protocol.ts`` is the other mirror of
this contract.

Three rules from the plan review live here:

* **R3** every reply is matched against the command it answers. A late ``ack`` for an
  earlier command must never fire *this* command's ``on_dispatched``.
* **R4** the wire ``expires_at`` is minted at send time and is *not*
  :attr:`StepCommand.expires_at` — a `dispatch` that expects an ack expires with the ack
  budget, so the extension can never act after the runner has stopped waiting.
* **R7** the wire ``command_id`` is a fresh uuid, so a restarted worker cannot mint an id
  the extension still remembers from before a crash.

The client is blocking on purpose: ``on_dispatched`` writes ``submitted_unverified`` on the
connection the run loop is already using, so it must fire on the calling thread. The reader
therefore runs *inside* the calls this executor makes, never on a thread of its own.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from .executor import (
    ConditionResult,
    ConfirmationProbe,
    OnDispatched,
    StepCommand,
    StepResult,
)

logger = logging.getLogger("processlens.runner.browser")

PROTOCOL = 1
SUBPROTOCOL = "processlens.v1"
DEFAULT_ACK_BUDGET_SECONDS = 5.0
DEFAULT_COMMAND_TTL_SECONDS = 60.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0

# The statuses the runner understands, and the ones it maps to something narrower.
_KNOWN_STATUSES = frozenset(
    {"ok", "rejected", "locator_failed", "timeout", "lost", "not_dispatched", "refused"}
)


class ExecutorUnavailable(RuntimeError):
    """The extension is not reachable, so the worker must not claim a run."""


def _default_connector(url: str, secret: str) -> Any:
    """Open the runner's WebSocket. Imported lazily so the fake path needs no websockets."""
    from websockets.sync.client import connect

    return connect(
        url,
        additional_headers={"Authorization": f"Bearer {secret}"},
        subprotocols=[SUBPROTOCOL],
        open_timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        close_timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        max_size=None,
    )


class BrowserExecutor:
    """Drives the real extension over the hub for one run."""

    def __init__(
        self,
        *,
        url: str,
        secret: str,
        session: str,
        worker_id: str,
        connector: Callable[[str, str], Any] | None = None,
        now: Callable[[], float] | None = None,
        ack_budget_seconds: float = DEFAULT_ACK_BUDGET_SECONDS,
        command_ttl_seconds: float = DEFAULT_COMMAND_TTL_SECONDS,
    ) -> None:
        self.url = url
        self.secret = secret
        self.session = session
        self.worker_id = worker_id
        self._connector = connector or _default_connector
        # Wall clock, not a monotonic clock: `expires_at` travels to the browser, which compares it
        # against `Date.now()`. A monotonic reading (seconds since boot) is decades behind epoch
        # milliseconds, so every command would look long expired on arrival.
        self._now = now or time.time
        self.ack_budget_seconds = ack_budget_seconds
        self.command_ttl_seconds = command_ttl_seconds
        self._socket: Any | None = None
        self._events: deque[tuple[str, str]] = deque()
        self.disconnected = False

    # -- transport ---------------------------------------------------------

    @property
    def socket(self) -> Any:
        if self._socket is None:
            raise ExecutorUnavailable("the executor is not connected to the hub")
        return self._socket

    def connect(self) -> None:
        """Open the socket. Raises :class:`ExecutorUnavailable` when the hub refuses it."""
        try:
            self._socket = self._connector(self.url, self.secret)
        except Exception as exc:  # noqa: BLE001 - any transport failure means "not reachable"
            self.disconnected = True
            raise ExecutorUnavailable(str(exc)) from exc
        self.disconnected = False

    def close(self) -> None:
        if self._socket is not None:
            with contextlib.suppress(Exception):
                self._socket.close()
            self._socket = None

    def __enter__(self) -> BrowserExecutor:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def await_peer(self, timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> bool:
        """Wait for the hub's ``peer_ready`` (S5): claim once the extension is attached."""
        deadline = self._now() + timeout_seconds
        for _ in range(64):
            kind, _message = self._read(deadline)
            if kind == "peer_ready":
                return True
            if kind in ("peer_gone", "closed", "timeout"):
                return False
        return False

    def take_events(self) -> list[tuple[str, str]]:
        """Drain the run-level events collected while waiting for replies (R6)."""
        drained = list(self._events)
        self._events.clear()
        return drained

    # -- the Executor protocol --------------------------------------------

    def prepare(self, command: StepCommand) -> StepResult:
        wire_id = self._send_command("prepare", command, extra=self._step_payload(command))
        return self._step_result(wire_id, deadline=self._now() + self._result_budget(command))

    def dispatch(
        self, command: StepCommand, on_dispatched: OnDispatched | None = None
    ) -> StepResult:
        # R4: a dispatch that expects an ack is valid only as long as the runner will wait for it.
        wire_id = self._send_command(
            "dispatch",
            command,
            extra=self._step_payload(command),
            ttl=self.ack_budget_seconds,
        )
        ack_deadline = self._now() + self.ack_budget_seconds
        acked = False
        while not acked:
            kind, message = self._read(ack_deadline, command_id=wire_id)
            if kind == "ack":
                # R3: `_read` only returns an ack whose command_id matched.
                acked = True
                if on_dispatched is not None:
                    on_dispatched()
                continue
            if kind == "result":
                # A result can overtake its ack only if the extension misbehaved; take it.
                return self._status_to_result(message)
            return self._lost(kind, message)
        return self._step_result(wire_id, deadline=self._now() + self._result_budget(command))

    def perform(
        self, command: StepCommand, on_dispatched: OnDispatched | None = None
    ) -> StepResult:
        wire_id = self._send_command("dispatch", command, extra=self._step_payload(command))
        return self._step_result(wire_id, deadline=self._now() + self._result_budget(command))

    def check_condition(
        self,
        condition: dict[str, Any],
        *,
        timeout_ms: int,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConditionResult:
        command = self._synthetic(record_key)
        wire_id = self._send_command(
            "check",
            command,
            extra={
                "condition": condition,
                "timeout_ms": timeout_ms,
                "step_id": step_id,
                "record_key": record_key,
            },
            ttl=max(timeout_ms / 1000.0, 0.0) + self.ack_budget_seconds,
        )
        deadline = self._now() + timeout_ms / 1000.0 + self.ack_budget_seconds
        kind, message = self._read(deadline, command_id=wire_id)
        if kind == "condition":
            # R1: only a real answer counts. `passed=False` here means the extension polled.
            return ConditionResult(passed=bool(message.get("passed")), status="ok")
        if kind == "result" and message.get("status") == "refused":
            return ConditionResult(passed=False, status="refused")
        return ConditionResult(passed=False, status="lost")

    def probe_confirmation(
        self,
        condition: dict[str, Any],
        *,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConfirmationProbe:
        """PL-014 item 4. A real poll separates ``absent`` from ``unanswered`` (R1)."""
        result = self.check_condition(
            condition, timeout_ms=100, step_id=step_id, record_key=record_key
        )
        if not result.answered:
            # The page was asked and did not answer: park, never save.
            return "unanswered"
        if result.passed:
            return "present"
        return "absent"

    # -- message construction ---------------------------------------------

    def _step_payload(self, command: StepCommand) -> dict[str, Any]:
        return {
            "step": command.step,
            "commit": command.commit,
            "allowed_origins": list(command.allowed_origins),
        }

    def _send_command(
        self,
        kind: str,
        command: StepCommand,
        *,
        extra: dict[str, Any],
        ttl: float | None = None,
    ) -> str:
        if self._socket is None:
            raise ExecutorUnavailable("the executor is not connected to the hub")
        # R7: a fresh uuid per send, so a restarted worker never reuses an id the extension knows.
        wire_id = str(uuid.uuid4())
        message = {
            "protocol": PROTOCOL,
            "type": kind,
            "command_id": wire_id,
            "step_ref": command.command_id,
            "run_id": str(command.run_id),
            "run_record_id": str(command.run_record_id),
            "lease_epoch": command.lease_epoch,
            "expires_at": int((self._now() + (ttl or self.command_ttl_seconds)) * 1000),
            **extra,
        }
        self._socket.send(json.dumps(message))
        return wire_id

    def _synthetic(self, record_key: str | None) -> StepCommand:
        """A command envelope for a `check`, which has no step of its own."""
        return StepCommand(
            command_id=f"check:{record_key or 'run'}",
            run_id=uuid.UUID(int=0),
            run_record_id=uuid.UUID(int=0),
            record_key=record_key or "",
            idempotency_key="",
            lease_epoch=0,
            step={},
            commit=False,
            expires_at=0.0,
        )

    def _result_budget(self, command: StepCommand) -> float:
        own = command.step.get("timeout_ms")
        seconds = float(own) / 1000.0 if isinstance(own, int) else 0.0
        return max(seconds, 0.0) + self.ack_budget_seconds

    # -- the reader --------------------------------------------------------

    def _read(self, deadline: float, command_id: str | None = None) -> tuple[str, Any]:
        """Read one message, applying R3 and collecting events.

        Returns ``(kind, message)``. ``kind`` is one of ``ack``, ``result``, ``condition``,
        ``peer_ready``, ``peer_gone``, ``timeout`` or ``closed``. A message that is not
        addressed to ``command_id`` is logged and dropped (R3), and the read continues: a stale
        reply or an event must never be mistaken for this command's answer.
        """
        while True:
            kind, message = self._read_once(deadline, command_id)
            if kind != "ignored":
                return (kind, message)

    def _read_once(self, deadline: float, command_id: str | None) -> tuple[str, Any]:
        remaining = deadline - self._now()
        if remaining <= 0:
            return ("timeout", None)
        if self._socket is None:
            return ("closed", None)
        try:
            raw = self._socket.recv(timeout=remaining)
        except TimeoutError:
            return ("timeout", None)
        except Exception as exc:  # noqa: BLE001 - a closed or broken socket is "not reachable"
            logger.warning("the hub connection failed: %s", exc)
            self._socket = None
            self.disconnected = True
            self._events.append(("detached", f"the hub connection failed: {exc}"))
            return ("closed", None)
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("dropping a message that is not JSON")
            return ("ignored", None)
        return self._route(message, command_id)

    def _route(self, message: Any, command_id: str | None) -> tuple[str, Any]:
        if not isinstance(message, dict) or message.get("protocol") != PROTOCOL:
            logger.warning("dropping a message with the wrong protocol: %r", message)
            return ("ignored", None)
        kind = message.get("type")
        if kind == "event":
            self._events.append(
                (str(message.get("kind", "detached")), str(message.get("detail", "")))
            )
            return ("ignored", None)
        if kind == "peer_gone":
            # R6: authoritative, not a hint. The runner's socket is still up; the extension is not.
            self._events.append(("detached", "the extension disconnected from the hub"))
            return ("peer_gone", None)
        if kind == "hub_error":
            if message.get("code") == "peer_unavailable":
                self._events.append(("detached", "the extension is not connected to the hub"))
            return ("peer_gone", None)
        if kind == "peer_ready":
            return ("peer_ready", message)
        if kind == "ping":
            if self._socket is not None:
                self._socket.send(json.dumps({"protocol": PROTOCOL, "type": "pong"}))
            return ("ignored", None)
        if kind == "session":
            return ("ignored", None)
        if kind in ("ack", "result", "condition"):
            # R3: only the reply to the command being awaited.
            if command_id is None or message.get("command_id") != command_id:
                logger.warning("dropping a %s for a command that is not in flight", kind)
                return ("ignored", None)
            return (str(kind), message)
        logger.warning("dropping an unknown message type %r", kind)
        return ("ignored", None)

    def _step_result(self, command_id: str, *, deadline: float) -> StepResult:
        while True:
            kind, message = self._read(deadline, command_id=command_id)
            if kind == "result":
                return self._status_to_result(message)
            if kind == "ack":
                continue
            return self._lost(kind, message)

    def _status_to_result(self, message: Any) -> StepResult:
        status = str((message or {}).get("status", "lost"))
        if status not in _KNOWN_STATUSES:
            status = "lost"
        observed = message.get("observed")
        return StepResult(
            status=status,  # type: ignore[arg-type]
            observed=observed if isinstance(observed, dict) else {},
            error=message.get("error"),
            rung=message.get("rung") if isinstance(message.get("rung"), int) else None,
        )

    def _lost(self, kind: str, message: Any) -> StepResult:
        """Anything that is neither an ack nor a result is a step the runner cannot prove."""
        if kind in ("peer_gone", "closed"):
            return StepResult(status="lost", error="the extension is no longer connected")
        if kind == "timeout":
            return StepResult(
                status="lost",
                error="the extension did not answer within the command budget",
            )
        if kind == "result":
            return self._status_to_result(message)
        return StepResult(status="lost", error=f"the extension did not answer ({kind})")

    # -- a helper the worker uses -----------------------------------------

    def wait_for_peer_or_raise(
        self, timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    ) -> None:
        """S5: refuse to claim a run until the extension is attached."""
        if not self.await_peer(timeout_seconds):
            raise ExecutorUnavailable("the extension never attached to this session")
