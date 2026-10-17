"""Run ProcessLens yourself, from one command.

    python scripts/demo.py --extension-id <id>

The command gets the local stack up and keeps a worker ready, so the whole loop
works with no test harness: teach a task in your own Chrome, press Save as task,
see it on the dashboard, upload ``fixtures/demo/odoo-inquiries.csv``, Try one
row, approve, and run the rest while the supervisor's worker drives the browser
your popup connected.

What it does, in order:

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
  own ``start_worker`` command -- when both exist, one worker at a time.

The decision to start is a pure function (``worker_decision``), unit-tested in
``tests/demo/test_demo.py`` with no processes and no database. Ctrl+C stops the
worker and the API.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from pathlib import Path

import psycopg

from db.migrate import apply_migrations, database_url
from packages.ledger import OpenRunRow, list_open_runs

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

# The worker is started with the same arguments the e2e harness uses: poll for new runs every half
# a second, and stop waiting after 90 s -- the supervisor starts the next one when work appears.
WORKER_POLL_SECONDS = 0.5
WORKER_MAX_SECONDS = 90.0
# How often the supervisor looks, and how long it waits for the API to answer /health.
SUPERVISOR_POLL_SECONDS = 2.0
HEALTH_TIMEOUT_SECONDS = 30.0

TENANT_NAME = "demo"
OPERATOR_EMAIL = "owner@demo.processlens.local"
OPERATOR_NAME = "Demo owner"

OPERATOR_KEY = "PROCESSLENS_OPERATOR_ID"
RUNNER_SECRET_KEY = "PROCESSLENS_RUNNER_SECRET"
EXTENSION_ID_KEY = "PROCESSLENS_EXTENSION_ID"

# The supervisor's two answers.
START = "start"
WAIT = "wait"


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
    """
    stored = read_demo_env()
    secret = stored.get(RUNNER_SECRET_KEY, "").strip() or secrets.token_urlsafe(32)
    raw = stored.get(OPERATOR_KEY, "").strip()
    try:
        stored_operator = uuid.UUID(raw) if raw else None
    except ValueError:
        stored_operator = None

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        if stored_operator is not None:
            cur.execute("SELECT 1 FROM users WHERE id = %s", (stored_operator,))
            if cur.fetchone() is not None:
                return stored_operator, secret

        cur.execute("SELECT id FROM tenants WHERE name = %s", (TENANT_NAME,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO tenants (name) VALUES (%s) RETURNING id", (TENANT_NAME,))
            tenant_id = cur.fetchone()[0]
        else:
            tenant_id = row[0]
        cur.execute(
            "INSERT INTO users (tenant_id, email, name, role) "
            "VALUES (%s, %s, %s, 'owner') RETURNING id",
            (tenant_id, OPERATOR_EMAIL, OPERATOR_NAME),
        )
        operator = uuid.UUID(str(cur.fetchone()[0]))

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
    try:
        with urllib.request.urlopen(f"{API_BASE}/api/v1/executor/session", timeout=5) as response:
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


def stop_process(process: subprocess.Popen | None) -> None:
    """Terminate cleanly, then kill only if ten seconds were not enough."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


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

    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
    try:
        wait_for_health()
        print_steps(extension_id=args.extension_id)
        while True:
            if worker is not None and worker.poll() is not None:
                print(f"  the worker exited (code {worker.returncode})")
                worker = None
            session_id = attached_session()
            decision = worker_decision(open_runs(dsn), session_id, worker_alive=worker is not None)
            if decision == START and session_id is not None:
                worker_number += 1
                worker = start_worker(dsn, runner_secret, session_id, number=worker_number)
                print(
                    f"  started a worker for the browser session "
                    f"(log: .data/worker-logs/demo-worker-{worker_number}.log)"
                )
            time.sleep(SUPERVISOR_POLL_SECONDS)
    except KeyboardInterrupt:
        print()
        print("Stopping.")
    finally:
        stop_process(worker)
        stop_process(api)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
