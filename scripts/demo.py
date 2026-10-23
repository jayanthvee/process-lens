"""Run ProcessLens yourself, from one command.

    python scripts/demo.py --extension-id <id>

The command gets the local stack up and keeps a worker ready, so the whole loop
works with no test harness: teach a task in your own Chrome, press Save as task,
see it on the dashboard, upload ``fixtures/demo/odoo-inquiries.csv``, Try one
row, approve, and run the rest while the supervisor's worker drives the browser
your popup connected.

What it does, in order:

* refuses to start when another copy already holds the port or ``.data/demo.pid`` -- one plain
  line says which copy is in the way, and nothing is touched;
* applies the migrations (``db.migrate``);
* makes sure one operator user (owner role) exists in one tenant, and stores its
  id and a generated runner secret in ``.data/demo.env`` -- gitignored -- reusing
  them on later starts;
* builds ``apps/web`` and ``apps/extension`` when their ``dist`` is missing;
* starts the API on ``127.0.0.1:8000`` with ``PROCESSLENS_EXTENSION_ID``,
  ``PROCESSLENS_OPERATOR_ID`` and ``PROCESSLENS_RUNNER_SECRET`` set, exactly the
  way ``tests/e2e/conftest.py``'s ``api_server`` starts it;
* every 2 seconds, checks for a run a worker could take and for a connected
  extension session, and starts ``python -m services.runner`` -- the harness's
  own ``start_worker`` command -- when both exist, one worker at a time;
* says what it is doing, in plain words, each time the state changes: Chrome
  connecting or going away, a run waiting for Chrome, a runner starting and
  finishing.

The decision to start is a pure function (``worker_decision``), and the words
the window says are another one (``status_lines``); both are unit-tested in
``tests/demo/test_demo.py`` with no processes and no database. Ctrl+C stops the
worker and the API, and releases the pid lock file.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Run as `python scripts/demo.py`, Python puts scripts/ on the path, not the repository, so the
# project's own packages would not import without this.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.migrate import apply_migrations, database_url  # noqa: E402
from services.deploy import (  # noqa: E402,F401  (re-exported: the demo's tests pin these)
    EXTENSION_ID_KEY,
    OPERATOR_KEY,
    RUNNER_SECRET_KEY,
    START,
    WAIT,
    State,
    ensure_operator_row,
    finish_unheld_cancels,
    open_runs,
    status_lines,
    stop_process,
    waiting_run_id,
    worker_decision,
)
from services.deploy import (
    attached_session as deploy_attached_session,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / ".data"
ENV_FILE = DATA_DIR / "demo.env"
WEB_DIR = REPO_ROOT / "apps" / "web"
EXTENSION_DIR = REPO_ROOT / "apps" / "extension"
DEMO_SHEET = "fixtures/demo/odoo-inquiries.csv"

API_HOST = "127.0.0.1"
API_PORT = 8000
API_BASE = f"http://{API_HOST}:{API_PORT}"
HUB_URL = f"ws://{API_HOST}:{API_PORT}"

# PL-050: the lock that says this checkout's demo owns the database, and the line a person reads
# when a second copy tries to start. The message is one plain sentence on purpose: it is the whole
# answer, and it names the fix a person can act on (their other terminal).
PID_FILE = DATA_DIR / "demo.pid"
PORT_TAKEN_MESSAGE = (
    f"ProcessLens is already running on {API_BASE} (or something else is using port {API_PORT}). "
    "Close the other copy first: find its terminal and press Ctrl+C."
)

# The worker is started with the same arguments the e2e harness uses: poll for new runs every half
# a second, and stop waiting after 90 s -- the supervisor starts the next one when work appears.
WORKER_POLL_SECONDS = 0.5
WORKER_MAX_SECONDS = 90.0
# How often the supervisor looks, and how long it waits for the API to answer /health.
SUPERVISOR_POLL_SECONDS = 2.0
HEALTH_TIMEOUT_SECONDS = 30.0

# The supervisor's two answers and its state live in services.deploy now; the demo re-exports
# them (START, WAIT, State, worker_decision, waiting_run_id, status_lines, open_runs,
# stop_process) so its own tests and habits keep working unchanged.


# ---------------------------------------------------------------- one copy at a time


def port_in_use(port: int) -> bool:
    """Whether something already listens on ``127.0.0.1:<port>``.

    Binding is the only reliable question: a plain socket that says the address is taken gives the
    same answer the API's own bind would get. The socket is closed at once and never listens, so
    asking does not keep the port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((API_HOST, port))
        except OSError:
            return True
    return False


def _pid_alive(pid: int) -> bool:
    """Whether a process with this id is running.

    On POSIX, signal 0 asks the kernel without touching the process. Windows has no such signal
    (``os.kill`` there would terminate it), so a process handle and a zero-time wait answer
    instead. No new dependency: ``ctypes`` is part of the standard library.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        synchronize = 0x00100000
        wait_timeout = 0x00000102
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists; it just is not ours to signal.
        return True
    except OSError:
        return False
    return True


def other_demo_pid(path: Path) -> int | None:
    """The pid a lock file names, when that process is still alive.

    A missing file, an unreadable one, a file that holds no number, and a number whose process is
    gone are all "no other demo": a stale file is a crash's leftover, not a copy to respect.
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if _pid_alive(pid) else None


def write_pid_file(path: Path) -> None:
    """Claim the lock with this process's pid."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")


def remove_pid_file(path: Path) -> None:
    """Release the lock, but only while it is still ours.

    A second copy that already took the lock must not lose it because the first one exits.
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    try:
        pid = int(raw)
    except ValueError:
        return
    if pid != os.getpid():
        return
    with contextlib.suppress(OSError):
        path.unlink()


def already_running_message(pid: int) -> str:
    """The pid refusal: the same plain line, naming the copy that is already running."""
    return (
        f"ProcessLens is already running on {API_BASE} (another demo, pid {pid}, is watching "
        "the same database). Close the other copy first: find its terminal and press Ctrl+C."
    )


# ---------------------------------------------------------------- the demo identity


def read_demo_env() -> dict[str, str]:
    """``KEY=VALUE`` lines from ``.data/demo.env``, or nothing when it is not there yet."""
    values: dict[str, str] = {}
    if not ENV_FILE.is_file():
        return values
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_demo_env(values: dict[str, str]) -> None:
    """Write the identity out, so later starts reuse it."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={values[key]}" for key in sorted(values)]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_operator(dsn: str) -> tuple[uuid.UUID, str]:
    """The demo operator's id and the runner secret, from ``.data/demo.env`` or freshly made.

    The stored operator is only reused while its ``users`` row still exists: a demo.env left over
    from a wiped database would otherwise configure an operator the API refuses (403) instead of a
    person who can own tasks. A fresh identity is one tenant with one owner, written back out.
    The database work itself is the deploy module's (``ensure_operator_row``), so the two paths
    cannot drift.
    """
    stored = read_demo_env()
    secret = stored.get(RUNNER_SECRET_KEY, "").strip() or secrets.token_urlsafe(32)
    raw = stored.get(OPERATOR_KEY, "").strip()

    operator = ensure_operator_row(dsn, raw or None)
    write_demo_env({OPERATOR_KEY: str(operator), RUNNER_SECRET_KEY: secret})
    return operator, secret


# ---------------------------------------------------------------- the builds


def npm() -> str:
    """The npm on PATH, as the harness resolves it."""
    return shutil.which("npm") or "npm"


def build_if_missing(app_dir: Path, dist_entry: str) -> None:
    """Build one npm app when its ``dist`` is missing, as the harness builds the extension."""
    if (app_dir / "dist" / dist_entry).is_file():
        return
    if not (app_dir / "node_modules").is_dir():
        subprocess.run([npm(), "--prefix", str(app_dir), "install"], cwd=str(REPO_ROOT), check=True)
    subprocess.run(
        [npm(), "--prefix", str(app_dir), "run", "build"], cwd=str(REPO_ROOT), check=True
    )


# ---------------------------------------------------------------- the API


def start_api(
    dsn: str, *, operator_id: uuid.UUID, runner_secret: str, extension_id: str | None
) -> subprocess.Popen:
    """The API and hub under uvicorn, exactly as ``tests/e2e/conftest.py`` starts it.

    The extension id is pinned when it was given, so the hub accepts the extension's origin; when
    it was not, the variable is removed rather than left over from an earlier run.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = dsn
    env["PYTHONPATH"] = str(REPO_ROOT)
    env[OPERATOR_KEY] = str(operator_id)
    env[RUNNER_SECRET_KEY] = runner_secret
    if extension_id:
        env[EXTENSION_ID_KEY] = extension_id
    else:
        env.pop(EXTENSION_ID_KEY, None)
    log_path = DATA_DIR / "api.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.api.main:app",
            "--host",
            API_HOST,
            "--port",
            str(API_PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
    )


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen(f"{API_BASE}/health", timeout=5) as response:
            return json.loads(response.read()).get("status") == "ok"
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return False


def wait_for_health() -> None:
    """Wait until the API answers ``/health``, or say where its log is."""
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _health_ok():
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"the API never answered {API_BASE}/health; its log is at {DATA_DIR / 'api.log'}"
    )


# ---------------------------------------------------------------- the supervisor


def attached_session() -> str | None:
    """The attached extension session's id, or ``None`` when the popup has not connected."""
    return deploy_attached_session(API_BASE)


def start_worker(dsn: str, runner_secret: str, session_id: str, *, number: int) -> subprocess.Popen:
    """One runner worker, with the harness's own ``start_worker`` arguments."""
    log_dir = DATA_DIR / "worker-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["DATABASE_URL"] = dsn
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "services.runner",
            "--worker-id",
            f"demo-worker-{number}",
            "--executor",
            "browser",
            "--session",
            session_id,
            "--hub-url",
            HUB_URL,
            "--runner-secret",
            runner_secret,
            "--poll-seconds",
            str(WORKER_POLL_SECONDS),
            "--max-seconds",
            str(WORKER_MAX_SECONDS),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=(log_dir / f"demo-worker-{number}.log").open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
    )


# ---------------------------------------------------------------- the words a person reads


def print_steps(*, extension_id: str | None) -> None:
    """The four steps, in plain words, with the demo spreadsheet named."""
    print()
    print("ProcessLens is running. Four steps:")
    print()
    print("  1. Open chrome://extensions, turn on Developer mode, and choose Load unpacked")
    print(f"     with {EXTENSION_DIR} (built if it was missing).")
    if extension_id is None:
        print("  2. Copy the extension's ID from that page, stop this command with Ctrl+C,")
        print("     and start it again with --extension-id <that id>.")
    else:
        print(f"  2. The extension's ID is already pinned ({extension_id}); nothing to copy.")
    print(f"  3. In the popup, set the address to {API_BASE} and press Connect.")
    print(f"  4. Open {API_BASE} in this browser.")
    print()
    print(f"     The demo spreadsheet is {DEMO_SHEET}: 8 inquiries.")
    print("     Two of them (Elena Petrova, Omar Haddad) are already leads in Odoo. The task")
    print("     skips them only if you searched for the email before creating the lead while")
    print("     teaching; otherwise it would add them again. One row has no email: it stops")
    print("     and asks only if you ticked 'Stop and ask me if this is empty' for Email.")
    print()
    print("A supervisor is watching: when a run is waiting and your browser is connected,")
    print("it starts a worker to drive it. Press Ctrl+C to stop everything.")


def main(argv: list[str] | None = None) -> int:
    """One command: migrations, operator, builds, API, and the watching supervisor."""
    parser = argparse.ArgumentParser(
        prog="demo",
        description="Run ProcessLens locally for a person: one command, no test harness.",
    )
    parser.add_argument(
        "--extension-id",
        default=None,
        help="Your unpacked extension's id, from chrome://extensions.",
    )
    args = parser.parse_args(argv)

    # PL-050: a second copy fights the first over the same database, and the person who started it
    # sees "Try one row" hang at Queued with no explanation. Both refusals come before the
    # migrations, the builds and the API, and each is one plain line.
    if port_in_use(API_PORT):
        print(PORT_TAKEN_MESSAGE)
        return 1
    running_pid = other_demo_pid(PID_FILE)
    if running_pid is not None and running_pid != os.getpid():
        print(already_running_message(running_pid))
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_pid_file(PID_FILE)
    try:
        dsn = database_url()

        print("Applying migrations...")
        apply_migrations(dsn)
        print("Making sure one operator exists...")
        operator, runner_secret = ensure_operator(dsn)
        print("Building the web app and the extension when they are missing...")
        build_if_missing(WEB_DIR, "index.html")
        build_if_missing(EXTENSION_DIR, "manifest.json")

        api = start_api(
            dsn, operator_id=operator, runner_secret=runner_secret, extension_id=args.extension_id
        )
        worker: subprocess.Popen | None = None
        worker_number = 0
        worker_run_id: str | None = None
        worker_session: str | None = None
        last_exit: int | None = None
        previous = State(
            session_attached=False, waiting_run=None, worker_running_for=None, last_exit=None
        )
        try:
            wait_for_health()
            print_steps(extension_id=args.extension_id)
            while True:
                if worker is not None and worker.poll() is not None:
                    last_exit = worker.returncode
                    worker = None
                    worker_run_id = None
                # PL-089: a cancel no live worker holds is finished here, so it frees the app.
                finish_unheld_cancels(dsn)
                session_id = attached_session()
                # PL-037: a worker talks to the one Chrome session it was started for. When
                # Chrome reconnects, that session is gone and the worker would pause every run it
                # touches, so it is stopped and the next tick starts one for the new session.
                if (
                    worker is not None
                    and worker_session is not None
                    and session_id != worker_session
                ):
                    worker.terminate()
                    try:
                        worker.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        worker.kill()
                    worker = None
                    worker_run_id = None
                runs = open_runs(dsn)
                waiting_run = waiting_run_id(runs)
                decision = worker_decision(runs, session_id, worker_alive=worker is not None)
                if decision == START and session_id is not None and waiting_run is not None:
                    worker_number += 1
                    worker = start_worker(dsn, runner_secret, session_id, number=worker_number)
                    worker_session = session_id
                    worker_run_id = waiting_run
                    last_exit = None
                    started_log = f".data/worker-logs/demo-worker-{worker_number}.log"
                else:
                    started_log = None
                current = State(
                    session_attached=session_id is not None,
                    waiting_run=waiting_run,
                    worker_running_for=worker_run_id,
                    last_exit=last_exit,
                )
                for line in status_lines(previous, current):
                    print(f"  {line}")
                if started_log is not None:
                    print(f"    (its log: {started_log})")
                previous = current
                time.sleep(SUPERVISOR_POLL_SECONDS)
        except KeyboardInterrupt:
            print()
            print("Stopping.")
        finally:
            stop_process(worker)
            stop_process(api)
        return 0
    finally:
        remove_pid_file(PID_FILE)


if __name__ == "__main__":
    raise SystemExit(main())
