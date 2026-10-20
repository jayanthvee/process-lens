"""PL-030: the demo supervisor's decision, as a pure function.

The supervisor polls two things -- the runs a worker could take, and the
extension session that is attached -- and decides whether to start its one
worker. The decision is a pure function over what the polls returned, so these
tests run no processes and touch no database.

PL-034 adds the words the window says: ``status_lines`` turns one tick's state
into the sentences a person reads, and prints nothing when nothing changed.

PL-050 adds the two checks that stop a second copy from starting: the port and
the pid lock file. The checks are small functions, so they are tested here with
no database and no API.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from packages.ledger.models import OpenRunRow
from scripts import demo
from scripts.demo import START, WAIT, State, status_lines, worker_decision


def state(**changes: Any) -> State:
    """One tick's view, with any field overridden."""
    values: dict[str, Any] = {
        "session_attached": False,
        "waiting_run": None,
        "worker_running_for": None,
        "last_exit": None,
    }
    values.update(changes)
    return State(**values)


def run(status: str, *, worker_live: bool = False) -> OpenRunRow:
    """One polled run row; the id is irrelevant to the decision."""
    return OpenRunRow(run_id=uuid.uuid4(), status=status, worker_live=worker_live)


def test_a_queued_run_starts_a_worker() -> None:
    """A run nobody has claimed, and a browser to drive: start."""
    assert worker_decision([run("queued")], "session-1", worker_alive=False) == START


def test_a_running_run_whose_worker_died_starts_a_new_one() -> None:
    """The lease says nobody is driving it, so the supervisor steps in."""
    assert (
        worker_decision([run("running", worker_live=False)], "session-1", worker_alive=False)
        == START
    )


def test_a_running_run_with_a_live_worker_waits() -> None:
    """The run's own worker is alive; the supervisor must not race it."""
    assert (
        worker_decision([run("running", worker_live=True)], "session-1", worker_alive=False) == WAIT
    )


def test_a_paused_run_waits_for_a_person() -> None:
    """A paused run waits for a person, not for a worker."""
    assert worker_decision([run("paused")], "session-1", worker_alive=False) == WAIT


def test_nothing_to_do_waits() -> None:
    """No runs at all: nothing to start, whatever the session says."""
    assert worker_decision([], "session-1", worker_alive=False) == WAIT


def test_a_live_worker_of_our_own_waits() -> None:
    """Only one worker at a time, even with a queued run and a session ready."""
    assert worker_decision([run("queued")], "session-1", worker_alive=True) == WAIT


def test_no_extension_session_waits() -> None:
    """A worker with no browser to drive cannot claim: wait for the popup to connect."""
    assert worker_decision([run("queued")], None, worker_alive=False) == WAIT


# --- the words the window says (PL-034) -------------------------------------


def test_a_session_that_connects_is_announced() -> None:
    """The popup pressed Connect: say so."""
    assert status_lines(state(), state(session_attached=True)) == ["Chrome is connected."]


def test_a_session_that_disconnects_says_what_to_do() -> None:
    """Chrome went away: the window names the fix, not just the fact."""
    assert status_lines(state(session_attached=True), state()) == [
        "Chrome is not connected: open the ProcessLens extension and press Connect."
    ]


def test_a_run_with_no_browser_says_it_is_waiting() -> None:
    """A run is open and nothing is attached: the window says what the demo is waiting on."""
    assert status_lines(state(), state(waiting_run="3f9c2a1b-1111-2222-3333-444455556666")) == [
        "A run is waiting for Chrome."
    ]


def test_a_connected_session_with_a_waiting_run_waits_silently() -> None:
    """Chrome is here and a run is open: that is a start, not a wait, so no waiting line."""
    previous = state(waiting_run="3f9c2a1b-1111-2222-3333-444455556666")
    current = state(session_attached=True, waiting_run="3f9c2a1b-1111-2222-3333-444455556666")

    assert status_lines(previous, current) == ["Chrome is connected."]
    assert status_lines(current, current) == []


def test_starting_a_runner_names_the_run() -> None:
    """The worker that just went out is identified by the run it will drive."""
    previous = state(session_attached=True)
    current = state(
        session_attached=True, worker_running_for="3f9c2a1b-1111-2222-3333-444455556666"
    )

    assert status_lines(previous, current) == ["Started a runner for run 3f9c2a1b."]


def test_the_runner_finishing_is_announced() -> None:
    """Exit code 0: the run went through."""
    previous = state(
        session_attached=True, worker_running_for="3f9c2a1b-1111-2222-3333-444455556666"
    )
    current = state(session_attached=True, last_exit=0)

    assert status_lines(previous, current) == ["The runner finished."]


def test_the_runner_stopping_with_a_code_is_announced() -> None:
    """Any other exit code is named, so a crash is not silent."""
    previous = state(
        session_attached=True, worker_running_for="3f9c2a1b-1111-2222-3333-444455556666"
    )
    current = state(session_attached=True, last_exit=3)

    assert status_lines(previous, current) == ["The runner stopped with code 3."]


def test_an_unchanged_state_says_nothing() -> None:
    """Nothing moved between ticks: the window stays quiet."""
    quiet = state()
    busy = state(session_attached=True, waiting_run="x", last_exit=3)

    assert status_lines(quiet, quiet) == []
    assert status_lines(busy, busy) == []


# --- PL-050: refuse to start a second copy -----------------------------------


def test_a_held_port_is_reported_in_use() -> None:
    """A listening socket holds the port; once it closes, the port is free again."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind((demo.API_HOST, 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        assert demo.port_in_use(port) is True

    deadline = time.monotonic() + 5
    while demo.port_in_use(port) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert demo.port_in_use(port) is False


def test_a_missing_pid_file_is_not_another_demo(tmp_path: Path) -> None:
    assert demo.other_demo_pid(tmp_path / "demo.pid") is None


def test_a_live_pid_in_the_file_is_another_demo(tmp_path: Path) -> None:
    path = tmp_path / "demo.pid"
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    assert demo.other_demo_pid(path) == os.getpid()


def test_a_dead_pid_in_the_file_is_stale(tmp_path: Path) -> None:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait(timeout=60)
    path = tmp_path / "demo.pid"
    path.write_text(f"{finished.pid}\n", encoding="utf-8")
    assert demo.other_demo_pid(path) is None


def test_a_garbage_pid_file_is_stale(tmp_path: Path) -> None:
    path = tmp_path / "demo.pid"
    path.write_text("not a pid\n", encoding="utf-8")
    assert demo.other_demo_pid(path) is None


def test_starting_writes_our_pid(tmp_path: Path) -> None:
    path = tmp_path / "demo.pid"
    demo.write_pid_file(path)
    assert path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_cleanup_removes_a_file_holding_our_pid(tmp_path: Path) -> None:
    path = tmp_path / "demo.pid"
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    demo.remove_pid_file(path)
    assert not path.exists()


def test_cleanup_leaves_a_file_holding_another_pid(tmp_path: Path) -> None:
    path = tmp_path / "demo.pid"
    other = os.getpid() + 1
    path.write_text(f"{other}\n", encoding="utf-8")
    demo.remove_pid_file(path)
    assert path.read_text(encoding="utf-8").strip() == str(other)


def test_the_port_check_refuses_before_anything_else(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The port is taken: one plain line, exit 1, and no database work at all."""
    called: list[str] = []

    def record(name: str) -> None:
        called.append(name)

    monkeypatch.setattr(demo, "port_in_use", lambda port: True)
    monkeypatch.setattr(demo, "apply_migrations", lambda dsn: record("migrations"))
    monkeypatch.setattr(demo, "database_url", lambda: record("dsn") or "postgresql://unused")

    assert demo.main([]) == 1
    assert called == []
    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert printed == [demo.PORT_TAKEN_MESSAGE]


def test_an_alive_demo_pid_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The lock file names a live process: refuse, and the line names its pid."""
    monkeypatch.setattr(demo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(demo, "port_in_use", lambda port: False)
    monkeypatch.setattr(demo, "other_demo_pid", lambda path: 4242)
    monkeypatch.setattr(demo, "apply_migrations", lambda dsn: None)

    assert demo.main([]) == 1
    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(printed) == 1
    assert demo.API_BASE in printed[0]
    assert "pid 4242" in printed[0]
    assert "Close the other copy first" in printed[0]
