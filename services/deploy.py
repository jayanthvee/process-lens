"""Start ProcessLens as deployed services, configured only by the environment.

    python -m services.deploy api          # migrations, operator, uvicorn on 0.0.0.0:$PORT
    python -m services.deploy supervisor   # watch for waiting runs, start a worker per session

Railway (D-021) runs ProcessLens as long-running services instead of the demo's one process
(``scripts/demo.py``), which stays the local path and imports its reusable parts from here.

What each command does:

* ``api`` applies the migrations (``db.migrate``, idempotent), makes sure one operator user
  exists, and serves the API, the hub and the built web app under uvicorn on ``0.0.0.0:$PORT``.
  Nothing is built at start-up: the image already contains ``apps/web/dist``.
* ``supervisor`` runs the demo's own watch loop -- start a worker for a waiting run when a
  Chrome session is attached, stop it when that session goes -- reading the database directly
  and the API's address from ``PROCESSLENS_API_URL``. It never binds a port.

Every setting comes from the environment; a missing required one stops start-up with one plain
line naming it, and deploy mode never invents a secret: the runner secret must be provided as
``PROCESSLENS_RUNNER_SECRET``. Nothing here writes files or generates credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# `python -m services.deploy` puts the checkout on the path already; the explicit insert keeps
# the module importable the way `scripts/demo.py` runs it (as a script, from anywhere).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402
import uvicorn  # noqa: E402

from db.migrate import apply_migrations  # noqa: E402
from packages.ledger import OpenRunRow, list_open_runs  # noqa: E402
from services.api import access  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

DATABASE_URL_KEY = "DATABASE_URL"
OPERATOR_KEY = "PROCESSLENS_OPERATOR_ID"
RUNNER_SECRET_KEY = "PROCESSLENS_RUNNER_SECRET"
EXTENSION_ID_KEY = "PROCESSLENS_EXTENSION_ID"
API_URL_KEY = "PROCESSLENS_API_URL"
PORT_KEY = "PORT"
# PL-086: a hosted API (services.api.access) requires the team's access code.
ACCESS_CODE_KEY = "PROCESSLENS_ACCESS_CODE"

DEFAULT_PORT = 8000

# The supervisor is the demo's, moved: the same poll rhythm and the same worker arguments
# (the harness's own start_worker command) -- only the addresses now come from the environment.
WORKER_POLL_SECONDS = 0.5
WORKER_MAX_SECONDS = 90.0
SUPERVISOR_POLL_SECONDS = 2.0

TENANT_NAME = "processlens"
OPERATOR_EMAIL = "owner@processlens.local"
OPERATOR_NAME = "ProcessLens owner"

# The supervisor's two answers, and one tick's view of the world -- the pure parts the demo's
# tests (tests/demo/test_demo.py) pin, re-exported there by scripts/demo.py.
START = "start"
WAIT = "wait"


class MissingSetting(Exception):
    """A required environment variable is missing; the message is the one line a person reads."""


@dataclass(frozen=True)
class State:
    """One tick of the supervisor's view, summarized for ``status_lines``.

    ``waiting_run`` is the id of the run a worker could take, or ``None``;
    ``worker_running_for`` is the id of the run the worker we started will drive;
    ``last_exit`` is the previous worker's exit code, reset when a new one starts.
    """

    session_attached: bool
    waiting_run: str | None
    worker_running_for: str | None
    last_exit: int | None


def worker_decision(runs: Sequence[OpenRunRow], session: str | None, *, worker_alive: bool) -> str:
    """Whether the supervisor should start a worker now: ``start`` or ``wait``.

    The rule, in order:

    * our own worker is still alive: wait -- only one worker at a time;
    * no extension session is attached: wait -- a worker with no browser to drive
      refuses to claim, so starting one would only fail and exit;
    * a run is ``queued``: start -- nobody has claimed it;
    * a run is ``running`` whose lease has lapsed: start -- its worker is gone;
    * anything else (every running run has a live worker, or a paused run waits
      for a person): wait.

    ``session`` is the attached session's id, or ``None`` when none is attached.
    Each run arrives with whether a live worker holds it, decided by the same
    lease rule the ledger's takeover uses, so the two can never disagree.
    """
    if worker_alive:
        return WAIT
    if session is None:
        return WAIT
    for row in runs:
        if row.status == "queued":
            return START
        if row.status == "running" and not row.worker_live:
            return START
    return WAIT


def waiting_run_id(runs: Sequence[OpenRunRow]) -> str | None:
    """The id of the run a worker could take -- the same rows ``worker_decision`` starts on."""
    for row in runs:
        if row.status == "queued" or (row.status == "running" and not row.worker_live):
            return str(row.run_id)
    return None


def status_lines(previous: State, current: State) -> list[str]:
    """The words the window says this tick, given the tick before.

    Only a change produces a sentence, so the window never says the same thing twice: a run that
    keeps waiting, a session that stays connected and a worker that keeps running are all silence.
    The waiting line names Chrome, not the session, because Chrome is what the person can fix.
    """
    lines: list[str] = []

    if current.session_attached != previous.session_attached:
        if current.session_attached:
            lines.append("Chrome is connected.")
        else:
            lines.append(
                "Chrome is not connected: open the ProcessLens extension and press Connect."
            )

    waiting_now = current.waiting_run is not None and not current.session_attached
    waiting_before = previous.waiting_run is not None and not previous.session_attached
    if waiting_now and not waiting_before:
        lines.append("A run is waiting for Chrome.")

    if current.worker_running_for and current.worker_running_for != previous.worker_running_for:
        lines.append(f"Started a runner for run {current.worker_running_for[:8]}.")

    if current.last_exit is not None and current.last_exit != previous.last_exit:
        if current.last_exit == 0:
            lines.append("The runner finished.")
        else:
            lines.append(f"The runner stopped with code {current.last_exit}.")

    return lines


# ---------------------------------------------------------------- settings from the environment


def require_setting(name: str) -> str:
    """The variable's value, or the one plain line naming it and a stop."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSetting(f"Set {name} in the environment; ProcessLens cannot start without it.")
    return value


def require_settings(*names: str) -> None:
    """Refuse at once when any required setting is missing, naming the first one."""
    for name in names:
        require_setting(name)


# ---------------------------------------------------------------- the API command


def ensure_operator_row(dsn: str, stored_operator_id: str | uuid.UUID | None = None) -> uuid.UUID:
    """The operator user's id, reusing one or creating it -- no files, no secrets.

    The stored operator (``PROCESSLENS_OPERATOR_ID`` when the caller passes one) is only reused
    while its ``users`` row still exists: configuration left over from a wiped database would
    otherwise configure an operator the API refuses. A fresh identity is one tenant with one
    owner, created once: the same tenant name and email are reused on the next start, so the
    command is idempotent on a database that already has them.
    """
    if stored_operator_id is None:
        stored: uuid.UUID | None = None
    elif isinstance(stored_operator_id, uuid.UUID):
        stored = stored_operator_id
    else:
        try:
            stored = uuid.UUID(stored_operator_id.strip())
        except ValueError:
            stored = None

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        if stored is not None:
            cur.execute("SELECT 1 FROM users WHERE id = %s", (stored,))
            if cur.fetchone() is not None:
                return stored

        cur.execute("SELECT id FROM tenants WHERE name = %s", (TENANT_NAME,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO tenants (name) VALUES (%s) RETURNING id", (TENANT_NAME,))
            tenant_id = cur.fetchone()[0]
        else:
            tenant_id = row[0]
        cur.execute(
            "SELECT id FROM users WHERE tenant_id = %s AND email = %s", (tenant_id, OPERATOR_EMAIL)
        )
        existing = cur.fetchone()
        if existing is not None:
            return uuid.UUID(str(existing[0]))
        cur.execute(
            "INSERT INTO users (tenant_id, email, name, role) "
            "VALUES (%s, %s, %s, 'owner') RETURNING id",
            (tenant_id, OPERATOR_EMAIL, OPERATOR_NAME),
        )
        return uuid.UUID(str(cur.fetchone()[0]))


def api_command() -> int:
    """Migrations, operator, then the API on ``0.0.0.0:$PORT`` -- in that order, always."""
    dsn = require_setting(DATABASE_URL_KEY)
    require_setting(RUNNER_SECRET_KEY)
    require_setting(EXTENSION_ID_KEY)
    if access.hosted():
        # Hosted, the access code is the only door: never start one that is open to everyone.
        require_setting(ACCESS_CODE_KEY)

    print("Applying migrations...")
    apply_migrations(dsn)
    print("Making sure one operator exists...")
    operator_id = ensure_operator_row(dsn, os.environ.get(OPERATOR_KEY, "").strip() or None)
    os.environ[OPERATOR_KEY] = str(operator_id)

    port = int(os.environ.get(PORT_KEY, "").strip() or DEFAULT_PORT)
    print(f"Serving on 0.0.0.0:{port}.")
    uvicorn.run("services.api.main:app", host="0.0.0.0", port=port, log_level="warning")
    return 0


# ---------------------------------------------------------------- the supervisor command


def supervisor_api_base(api_url: str) -> str:
    """The API's base URL, trimmed, so both polls and the hub address come from one variable."""
    return api_url.strip().rstrip("/")


def hub_ws_url(api_url: str) -> str:
    """The hub's WebSocket address: ``https://`` becomes ``wss://``, ``http://`` becomes ``ws://``.

    The extension's own ``socketBase`` (apps/extension/src/background/command-session.ts) makes
    exactly this replacement, so the worker and the popup agree on how the hub is reached.
    """
    base = supervisor_api_base(api_url)
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :]
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :]
    return base


def attached_session(api_url: str, runner_secret: str | None = None) -> str | None:
    """The attached extension session's id, or ``None`` when the popup has not connected.

    A hosted API (PL-086) answers only a caller with a credential, so the supervisor presents the
    runner secret it already holds; a local API ignores the header.
    """
    secret = runner_secret if runner_secret is not None else os.environ.get(RUNNER_SECRET_KEY, "")
    headers = {"Authorization": f"Bearer {secret.strip()}"} if secret.strip() else {}
    request = urllib.request.Request(
        f"{supervisor_api_base(api_url)}/api/v1/executor/session", headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return None
    if body.get("attached") is not True:
        return None
    session_id = body.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def open_runs(dsn: str) -> list[OpenRunRow]:
    """The runs a worker could take, read fresh each tick so a database restart heals itself."""
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=3) as conn:
            return list_open_runs(conn)
    except psycopg.Error as exc:
        print(f"  could not read the runs: {exc}")
        return []


def start_worker(
    api_url: str, runner_secret: str, session_id: str, *, number: int
) -> subprocess.Popen:
    """One runner worker, with the harness's own ``start_worker`` arguments.

    The secret rides the argument the worker's own CLI accepts (it also reads
    ``PROCESSLENS_RUNNER_SECRET`` from the environment); it is inherited from the container, never
    invented, and never written anywhere.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = os.environ.get(DATABASE_URL_KEY, "")
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "services.runner",
            "--worker-id",
            f"deploy-worker-{number}",
            "--executor",
            "browser",
            "--session",
            session_id,
            "--hub-url",
            hub_ws_url(api_url),
            "--runner-secret",
            runner_secret,
            "--poll-seconds",
            str(WORKER_POLL_SECONDS),
            "--max-seconds",
            str(WORKER_MAX_SECONDS),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
    )


def stop_process(process: subprocess.Popen | None) -> None:
    """Terminate cleanly, then kill only if ten seconds were not enough."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def supervisor_command() -> int:
    """The demo's watch loop: a worker for a waiting run when Chrome is attached, one at a time."""
    dsn = require_setting(DATABASE_URL_KEY)
    api_url = require_setting(API_URL_KEY)
    runner_secret = require_setting(RUNNER_SECRET_KEY)

    print("Supervisor watching: a run waiting for Chrome gets a worker.")
    worker: subprocess.Popen | None = None
    worker_number = 0
    worker_session: str | None = None
    worker_run_id: str | None = None
    last_exit: int | None = None
    previous = State(
        session_attached=False, waiting_run=None, worker_running_for=None, last_exit=None
    )
    try:
        while True:
            if worker is not None and worker.poll() is not None:
                last_exit = worker.returncode
                worker = None
                worker_run_id = None
            session_id = attached_session(api_url)
            # A worker talks to the one Chrome session it was started for. When Chrome reconnects,
            # that session is gone and the worker would pause every run it touches, so it is
            # stopped and the next tick starts one for the new session.
            if worker is not None and worker_session is not None and session_id != worker_session:
                stop_process(worker)
                worker = None
                worker_run_id = None
            runs = open_runs(dsn)
            waiting_run = waiting_run_id(runs)
            decision = worker_decision(runs, session_id, worker_alive=worker is not None)
            if decision == START and session_id is not None and waiting_run is not None:
                worker_number += 1
                worker = start_worker(api_url, runner_secret, session_id, number=worker_number)
                worker_session = session_id
                worker_run_id = waiting_run
                last_exit = None
            current = State(
                session_attached=session_id is not None,
                waiting_run=waiting_run,
                worker_running_for=worker_run_id,
                last_exit=last_exit,
            )
            for line in status_lines(previous, current):
                print(f"  {line}")
            previous = current
            time.sleep(SUPERVISOR_POLL_SECONDS)
    except KeyboardInterrupt:
        print()
        print("Stopping.")
    finally:
        stop_process(worker)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch one start command; a missing setting prints its name and exits 1."""
    parser = argparse.ArgumentParser(
        prog="python -m services.deploy",
        description="Run ProcessLens as deployed services, configured by the environment.",
    )
    parser.add_argument("command", choices=["api", "supervisor"])
    args = parser.parse_args(argv)
    try:
        if args.command == "api":
            return api_command()
        return supervisor_command()
    except MissingSetting as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
