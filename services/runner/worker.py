"""The worker: claim or take over a run, recover, process, and heartbeat on its own thread.

R6 is the reason the heartbeat is a thread: executor calls block (assertion timeouts, approval
waits), so a beat only between records would go stale mid-step, another worker would take the
run over, and two workers would act. The heartbeat beats on its own connection, because a
psycopg connection is not used from two threads at once. On any
:class:`~packages.ledger.StaleLease` the worker stops at
once and sends no further command.
"""

from __future__ import annotations

import argparse
import threading
import uuid
from collections.abc import Callable
from typing import Any

import psycopg

from packages.ledger import StaleLease, heartbeat

from .browser_executor import BrowserExecutor, ExecutorUnavailable
from .clock import Clock, SystemClock
from .executor import ConditionResult, ConfirmationProbe, Executor, StepCommand, StepResult
from .reconcile import NoopReconciler, ParkingReconciler, Reconciler
from .run_loop import RunLoop

Connect = Callable[[str], Any]

# R6: the heartbeat timeout must be well above the longest step, so a worker that is legitimately
# blocked in a step (an assertion timeout, an approval wait) is never mistaken for a dead one. The
# recipe schema caps `timeout_ms` at 120 000, so the largest a legal recipe can demand is
# 3 x 120s + the dispatch budget = 365s; the default covers that, and the worker still checks the
# configured value against the recipe it is about to run.
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 365.0
DEFAULT_HEARTBEAT_INTERVAL = 20.0


def heartbeat_timeout_interval(seconds: float) -> str:
    """The ``runs.heartbeat_at < now() - <interval>`` value for a timeout in seconds."""
    return f"{seconds} seconds"


def default_connect(dsn: str) -> Any:
    """Open a connection in autocommit mode.

    The worker owns every transaction boundary explicitly with ``conn.transaction()``, and the
    write-ahead pattern depends on a block really committing before the command is dispatched. An
    autocommit connection keeps a read from silently opening a transaction that swallows the next
    ``conn.transaction()`` block as a savepoint.
    """
    return psycopg.connect(dsn, autocommit=True)


class Heartbeat:
    """Beats a run's lease on its own connection and thread, and records a lost lease (R6)."""

    def __init__(
        self,
        connect: Connect,
        dsn: str,
        run_id: uuid.UUID,
        worker_id: str,
        epoch: int,
        *,
        interval: float,
    ) -> None:
        self._connect = connect
        self._dsn = dsn
        self._run_id = run_id
        self._worker_id = worker_id
        self._epoch = epoch
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stale_error: Exception | None = None
        self.beats = 0

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._beat, name=f"heartbeat-{self._run_id}", daemon=True
        )
        self._thread.start()

    def _beat(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                with self._connect(self._dsn) as conn:
                    heartbeat(conn, self._run_id, self._worker_id, self._epoch)
                self.beats += 1
            except StaleLease as exc:
                self.stale_error = exc
                return
            except psycopg.Error as exc:  # pragma: no cover - a transport hiccup, not a lease loss
                self.stale_error = exc
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._interval * 2))

    def check(self) -> None:
        """Raise if the lease was lost, so the loop stops before sending anything else."""
        if self.stale_error is not None:
            raise StaleLease(str(self.stale_error))

    def __enter__(self) -> Heartbeat:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


class Worker:
    """Claims one run at a time and processes it."""

    def __init__(
        self,
        dsn: str,
        *,
        worker_id: str,
        executor: Executor,
        reconciler: Reconciler | None = None,
        clock: Clock | None = None,
        crash_hooks: Callable[[str], None] | None = None,
        heartbeat_timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        fallback_timeout_ms: int = 10_000,
        connect: Connect = default_connect,
        events: Callable[[], list[tuple[str, str]]] | None = None,
        reconnect: Callable[[], bool] | None = None,
    ) -> None:
        self.dsn = dsn
        self.worker_id = worker_id
        self.executor = executor
        self.reconciler = reconciler or NoopReconciler()
        self.clock = clock or SystemClock()
        self.crash_hooks = crash_hooks
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.heartbeat_timeout = heartbeat_timeout_interval(heartbeat_timeout_seconds)
        self.heartbeat_interval = heartbeat_interval
        self.fallback_timeout_ms = fallback_timeout_ms
        self.connect = connect
        # PL-014 R6: the run-level event drain the executor collects into (None for the fake path).
        self.events = events
        # F5's other half: a worker whose executor dropped re-attaches before it gives up, so a
        # transient disconnect does not stop it for the rest of its life (ticket criterion).
        self.reconnect = reconnect

    def run_once(self) -> bool:
        """Claim or take over one run and process it. Returns False when there is nothing to do."""
        from packages.ledger import claim_run, find_stale_run, get_run, recover_run, take_over_run

        # F5: a disconnected executor cannot drive a run, so claiming one would strand it in the
        # commit window. The worker first tries to re-attach; only when that fails does it stand
        # down, and the ticket's reconnect criterion is what makes the retry meaningful.
        if getattr(self.executor, "disconnected", False) and (
            self.reconnect is None or not self.reconnect()
        ):
            return False

        with self.connect(self.dsn) as conn:
            with conn.transaction():
                run = claim_run(conn, self.worker_id)

            epoch: int | None = None
            if run is not None:
                epoch = run.lease_epoch
            else:
                stale = find_stale_run(conn, heartbeat_timeout=self.heartbeat_timeout)
                if stale is None:
                    return False
                with conn.transaction():
                    epoch = take_over_run(
                        conn, stale.id, self.worker_id, heartbeat_timeout=self.heartbeat_timeout
                    )
                if epoch is None:
                    return False
                run = get_run(conn, stale.id)
                if run is None:
                    return False

            beat = Heartbeat(
                self.connect,
                self.dsn,
                run.id,
                self.worker_id,
                epoch,
                interval=self.heartbeat_interval,
            )
            with beat:
                # R4: recovery runs after every claim or takeover; on a fresh run it is a no-op.
                with conn.transaction():
                    recover_run(conn, run.id, self.worker_id, epoch)
                loop = RunLoop(
                    worker_id=self.worker_id,
                    executor=self.executor,
                    reconciler=self.reconciler,
                    clock=self.clock,
                    crash_hooks=self.crash_hooks,
                    fallback_timeout_ms=self.fallback_timeout_ms,
                    heartbeat_timeout_seconds=self.heartbeat_timeout_seconds,
                    lease_check=beat.check,
                    events=self.events,
                )
                loop.process_run(conn, run, epoch)
        return True

    def run_forever(self, *, max_runs: int | None = None) -> int:
        """Process runs until the queue is empty. Returns how many runs were processed."""
        processed = 0
        while max_runs is None or processed < max_runs:
            if not self.run_once():
                break
            processed += 1
        return processed


class DisconnectedExecutor:
    """The browser executor before PL-014 wires the extension.

    It reports ``lost`` for every call, which the loop treats as "short of proof", so it never
    claims a commit it cannot perform. ``main`` refuses to claim a run while this is the executor.
    """

    def prepare(self, command: StepCommand) -> StepResult:
        return StepResult(status="lost", error="no browser executor is connected")

    def dispatch(
        self, command: StepCommand, on_dispatched: Callable[[], None] | None = None
    ) -> StepResult:
        return StepResult(status="lost", error="no browser executor is connected")

    def perform(
        self, command: StepCommand, on_dispatched: Callable[[], None] | None = None
    ) -> StepResult:
        return StepResult(status="lost", error="no browser executor is connected")

    def check_condition(
        self,
        condition: dict[str, Any],
        *,
        timeout_ms: int,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConditionResult:
        # R1: with no browser there is no answer, and no answer is never "the condition is false".
        return ConditionResult(passed=False, status="lost")

    def probe_confirmation(
        self,
        condition: dict[str, Any],
        *,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConfirmationProbe:
        # No page to look at, so the fresh-confirmation gate does not run.
        return "unsupported"


def _run_with_polling(
    worker: Worker, *, max_runs: int | None, poll_seconds: float, stop_after_seconds: float
) -> int:
    """Process runs until the budget is spent. With ``poll_seconds > 0`` an idle queue waits.

    The idle interval is an ``Event.wait``, not a fixed sleep: rule 20's "no fixed sleeps" governs
    how the browser is driven (every step wait is a condition with a timeout), and an interruptible
    event is what lets the worker stop promptly instead of blocking. ``poll_seconds == 0`` exits as
    soon as the queue is empty.
    """
    import threading
    import time

    idle = threading.Event()
    processed = 0
    deadline = time.monotonic() + stop_after_seconds if stop_after_seconds > 0 else None
    while max_runs is None or processed < max_runs:
        if worker.run_once():
            processed += 1
            continue
        if poll_seconds <= 0:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        idle.wait(poll_seconds)
    return processed


def main(argv: list[str] | None = None) -> int:
    """``python -m services.runner --worker-id <id>``."""
    import os

    parser = argparse.ArgumentParser(prog="services.runner", description="ProcessLens run worker.")
    parser.add_argument("--worker-id", required=True, help="This worker's lease identity.")
    parser.add_argument("--database-url", default=None, help="Defaults to DATABASE_URL.")
    parser.add_argument(
        "--heartbeat-timeout",
        type=float,
        default=DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        help="Seconds a run's lease survives without a beat (R6: at least 3x the longest step).",
    )
    parser.add_argument("--heartbeat-interval", type=float, default=DEFAULT_HEARTBEAT_INTERVAL)
    parser.add_argument("--max-runs", type=int, default=None)
    parser.add_argument(
        "--executor",
        choices=["disconnected", "browser"],
        default="disconnected",
        help="`browser` drives the real extension over the hub (PL-014).",
    )
    parser.add_argument("--session", default=None, help="The hub session id (browser executor).")
    parser.add_argument(
        "--hub-url",
        default="ws://127.0.0.1:8000",
        help="The hub's base URL; the runner channel is derived from it.",
    )
    parser.add_argument(
        "--runner-secret",
        default=None,
        help="Defaults to PROCESSLENS_RUNNER_SECRET. Required with --executor browser.",
    )
    parser.add_argument(
        "--peer-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the extension to attach before refusing to claim (S5).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=0.0,
        help="Keep waiting for runs; 0 exits when the queue is empty.",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=0.0,
        help="Stop waiting after this long; 0 means no limit.",
    )
    args = parser.parse_args(argv)

    dsn = args.database_url or os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("services.runner: DATABASE_URL is not set")
        return 2

    if args.executor == "disconnected":
        # Fail closed: with no browser the worker refuses to claim, so it never writes a ledger
        # row it cannot act on.
        print(
            "services.runner: no browser executor is connected (--executor disconnected); "
            "refusing to claim runs"
        )
        return 0

    session = (args.session or "").strip()
    secret = (args.runner_secret or os.environ.get("PROCESSLENS_RUNNER_SECRET", "")).strip()
    if not session:
        print("services.runner: --session is required with --executor browser")
        return 2
    if not secret:
        print("services.runner: PROCESSLENS_RUNNER_SECRET is not set")
        return 2

    url = f"{args.hub_url.rstrip('/')}/api/v1/runner/ws?session={session}"
    executor = BrowserExecutor(url=url, secret=secret, session=session, worker_id=args.worker_id)
    try:
        executor.connect()
        # S5: claim nothing until the extension is attached, not merely until the socket is open.
        executor.wait_for_peer_or_raise(args.peer_timeout)
    except ExecutorUnavailable as exc:
        print(f"services.runner: {exc}")
        executor.close()
        return 3

    def reconnect() -> bool:
        """F5: try to bring a dropped executor back before a claim is refused.

        A disconnect is not necessarily permanent: the extension re-attaches to the same session
        with its resume token, and the ticket's reconnect criterion expects the run to continue.
        Close the dead socket first so a half-open one is never reused.
        """
        executor.close()
        try:
            executor.connect()
            executor.wait_for_peer_or_raise(args.peer_timeout)
        except ExecutorUnavailable:
            return False
        return True

    worker = Worker(
        dsn,
        worker_id=args.worker_id,
        executor=executor,
        # PL-014: until P-003 is decided, an uncertain save is parked for a person.
        reconciler=ParkingReconciler(),
        heartbeat_timeout_seconds=args.heartbeat_timeout,
        heartbeat_interval=args.heartbeat_interval,
        events=executor.take_events,
        reconnect=reconnect,
    )
    try:
        processed = _run_with_polling(
            worker,
            max_runs=args.max_runs,
            poll_seconds=args.poll_seconds,
            stop_after_seconds=args.max_seconds,
        )
    finally:
        executor.close()
    print(f"services.runner: processed {processed} run(s)")
    return 0
