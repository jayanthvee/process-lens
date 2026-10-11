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

from .clock import Clock, SystemClock
from .executor import ConditionResult, Executor, StepCommand, StepResult
from .reconcile import NoopReconciler, Reconciler
from .run_loop import RunLoop

Connect = Callable[[str], Any]

DEFAULT_HEARTBEAT_TIMEOUT = "1 minute"
DEFAULT_HEARTBEAT_INTERVAL = 20.0


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
        heartbeat_timeout: str = DEFAULT_HEARTBEAT_TIMEOUT,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        fallback_timeout_ms: int = 10_000,
        connect: Connect = default_connect,
    ) -> None:
        self.dsn = dsn
        self.worker_id = worker_id
        self.executor = executor
        self.reconciler = reconciler or NoopReconciler()
        self.clock = clock or SystemClock()
        self.crash_hooks = crash_hooks
        self.heartbeat_timeout = heartbeat_timeout
        self.heartbeat_interval = heartbeat_interval
        self.fallback_timeout_ms = fallback_timeout_ms
        self.connect = connect

    def run_once(self) -> bool:
        """Claim or take over one run and process it. Returns False when there is nothing to do."""
        from packages.ledger import claim_run, find_stale_run, get_run, recover_run, take_over_run

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
                    recover_run(conn, run.id, self.worker_id)
                loop = RunLoop(
                    worker_id=self.worker_id,
                    executor=self.executor,
                    reconciler=self.reconciler,
                    clock=self.clock,
                    crash_hooks=self.crash_hooks,
                    fallback_timeout_ms=self.fallback_timeout_ms,
                    lease_check=beat.check,
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
        return ConditionResult(passed=False)


def main(argv: list[str] | None = None) -> int:
    """``python -m services.runner --worker-id <id>``."""
    import os

    parser = argparse.ArgumentParser(prog="services.runner", description="ProcessLens run worker.")
    parser.add_argument("--worker-id", required=True, help="This worker's lease identity.")
    parser.add_argument("--database-url", default=None, help="Defaults to DATABASE_URL.")
    parser.add_argument("--heartbeat-timeout", default=DEFAULT_HEARTBEAT_TIMEOUT)
    parser.add_argument("--heartbeat-interval", type=float, default=DEFAULT_HEARTBEAT_INTERVAL)
    parser.add_argument("--max-runs", type=int, default=None)
    parser.add_argument(
        "--executor",
        choices=["disconnected"],
        default="disconnected",
        help="The browser executor. PL-014 adds the WebSocket executor.",
    )
    args = parser.parse_args(argv)

    dsn = args.database_url or os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("services.runner: DATABASE_URL is not set")
        return 2

    # The real executor arrives with PL-014. Until then the worker refuses to claim runs, so it
    # never writes a ledger row it cannot act on.
    print("services.runner: no browser executor is connected yet (PL-014); refusing to claim runs")
    return 0
