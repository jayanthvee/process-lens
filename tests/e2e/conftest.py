"""The end-to-end harness for PL-014.

One session-scoped stack drives the real extension in a real Chromium against the
synthetic lead-CRM fixture, with the real API, hub, runner worker and Postgres:

    fixture site -> scratch Postgres -> built extension -> persistent Chromium
    -> extension id -> API under uvicorn -> Connect in the popup -> runner worker

The order is not cosmetic: the hub pins ``PROCESSLENS_EXTENSION_ID`` and refuses a
socket whose origin is not that extension, and the id is only knowable after
Chromium has loaded the extension, so the API cannot start first.

A plain ``pytest -q`` collects nothing here and never imports Playwright: the
module guard below ignores every ``test_*.py`` unless ``PROCESSLENS_E2E=1``. The
browser is expensive, needs a full stack, and belongs out of pull-request CI.

R9: every Playwright artifact lives inside the repo. ``PLAYWRIGHT_BROWSERS_PATH``
is set at the top of this module, before Playwright is imported anywhere, so both
``playwright install`` and every run write to ``.cache/ms-playwright/`` and never
to ``%LOCALAPPDATA%``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYWRIGHT_BROWSERS_PATH = REPO_ROOT / ".cache" / "ms-playwright"
# Constitution §2.1: every artifact this harness creates lives under the repo's gitignored
# ``.cache/`` — worker logs and browser profiles included — never in ``%TEMP%``.
E2E_SCRATCH = REPO_ROOT / ".cache" / "e2e"


def _scratch_dir(name: str) -> Path:
    """A gitignored scratch directory inside the repo, created on demand."""
    path = E2E_SCRATCH / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# R9: set before Playwright is imported anywhere in this process or its children.
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS_PATH)

E2E_ENABLED = os.environ.get("PROCESSLENS_E2E") == "1"

# Requirement 4 (visible mode for demos). Both default off, so the suite and CI are unchanged:
# a headed run is opt-in, and ``PROCESSLENS_E2E_SLOWMO_MS`` (default 0) slows every Playwright
# action so a person can follow the recording.
E2E_HEADED = os.environ.get("PROCESSLENS_E2E_HEADED") == "1"
try:
    E2E_SLOWMO_MS = max(0, int(os.environ.get("PROCESSLENS_E2E_SLOWMO_MS") or "0"))
except ValueError:
    E2E_SLOWMO_MS = 0

# Without the flag, collect nothing: no Playwright import, no browser, stays fast and green.
if not E2E_ENABLED:
    collect_ignore_glob = ["test_*.py"]

FIXTURE_DIR = REPO_ROOT / "fixtures" / "sites" / "lead-crm"
FIXTURE_SERVER = FIXTURE_DIR / "server.py"
RECIPE_FILE = FIXTURE_DIR / "recipe.json"
# PL-016b requirement 2: the messy fixture has its own server, started the same way.
MESSY_DIR = REPO_ROOT / "fixtures" / "sites" / "messy-crm"
MESSY_SERVER = MESSY_DIR / "server.py"
# PL-018 requirement 6/7: the widget corpus is a third site, with the same handshake shape.
CORPUS_DIR = REPO_ROOT / "fixtures" / "sites" / "widget-corpus"
CORPUS_SERVER = CORPUS_DIR / "server.py"
EXTENSION_DIR = REPO_ROOT / "apps" / "extension"
EXTENSION_DIST = EXTENSION_DIR / "dist"

RUNNER_SECRET = "e2e-runner-secret"
PLACEHOLDER_ORIGIN = "http://127.0.0.1:0"

# The fixture recipe's own email first, so the search finds it and the record is skipped.
EXISTING_EMAIL = "ana@example.test"
# Three brand-new leads: the three the batch verifies.
NEW_EMAILS = ("bo@example.test", "cy@example.test", "di@example.test")
# The one the recipe's own `fill-company` branch parks: no company, so it never saves.
PARKED_EMAIL = "el@example.test"

WORKER_TIMEOUT_SECONDS = 90.0


# ---------------------------------------------------------------- small plumbing


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> Any:
    """One JSON request. Returns the decoded body; raises for a non-2xx status."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("content-type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        # A bare "HTTP Error 422" hides the reason; the body is the reason.
        detail = error.read().decode("utf-8", "replace")
        raise AssertionError(f"{method} {url} -> {error.code}: {detail}") from error
    return json.loads(body) if body else None


def _wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float,
    interval: float = 0.2,
    message: str | Callable[[], str],
) -> Any:
    """Poll ``predicate`` until it returns a truthy value, or fail after ``timeout`` seconds.

    ``message`` may be a callable, so a failure can describe the current state instead of only
    naming what was expected.
    """
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    text = message() if callable(message) else message
    raise AssertionError(f"timed out after {timeout:g}s: {text} (last={last!r})")


def _free_port() -> int:
    """A port the OS just handed out and nothing is listening on yet."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_line(stream: Any, *, timeout: float) -> str:
    """Read one line from a subprocess's stdout, or raise. Windows has no select on pipes."""
    result: dict[str, str] = {}

    def read() -> None:
        result["line"] = stream.readline()

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive() or "line" not in result:
        raise AssertionError("the fixture site never printed its handshake line")
    return result["line"]


def _dotenv_dsn() -> str:
    """``DATABASE_URL`` from the environment, else ``.env``, exactly as ``db.migrate`` reads it."""
    from db.migrate import read_dotenv

    read_dotenv(REPO_ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise AssertionError("DATABASE_URL is not set (see .env)")
    return dsn


def _with_database(dsn: str, database: str) -> str:
    import psycopg

    params = {key: str(value) for key, value in psycopg.conninfo.conninfo_to_dict(dsn).items()}
    params["dbname"] = database
    return psycopg.conninfo.make_conninfo(**params)


def _drop_database(dsn: str, name: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


# ---------------------------------------------------------------- the harness


@dataclass
class Harness:
    """Everything a test needs to drive one end-to-end run."""

    api_base: str
    crm_url: str
    partner_url: str
    crm_port: int
    dsn: str
    session_id: str
    extension_id: str
    page: Any  # the CRM tab Playwright drives and the extension bound at Connect
    popup: Any  # the extension popup page
    workers: list[subprocess.Popen] = field(default_factory=list)
    worker_logs: list[Path] = field(default_factory=list)
    messy_url: str | None = None
    corpus_url: str | None = None

    # -- HTTP ------------------------------------------------------------
    def api_get(self, path: str) -> Any:
        return _http_json(f"{self.api_base}{path}")

    def api_post(
        self, path: str, payload: Any = None, headers: dict[str, str] | None = None
    ) -> Any:
        return _http_json(f"{self.api_base}{path}", method="POST", payload=payload, headers=headers)

    def fixture_get(self, path: str) -> Any:
        return _http_json(f"{self.crm_url}{path}")

    def messy_get(self, path: str) -> Any:
        """A request to the messy fixture (PL-016b); fails clearly when it was not started."""
        if not self.messy_url:
            raise AssertionError("the messy fixture is not part of this harness")
        return _http_json(f"{self.messy_url}{path}")

    def messy_state(self) -> dict[str, Any]:
        """The messy fixture's own truth: saved records, recipient counts and notes."""
        return self.messy_get("/__test__/state")

    def messy_reset(self) -> None:
        _http_json(f"{self.messy_url}/__test__/reset", method="POST", payload=None)

    def corpus_get(self, path: str) -> Any:
        """A request to the widget-corpus fixture (PL-018); fails when it was not started."""
        if not self.corpus_url:
            raise AssertionError("the widget-corpus fixture is not part of this harness")
        return _http_json(f"{self.corpus_url}{path}")

    def corpus_state(self) -> dict[str, Any]:
        """The corpus fixture's own truth: every widget's persisted value."""
        return self.corpus_get("/__test__/state")

    def corpus_reset(self) -> None:
        _http_json(f"{self.corpus_url}/__test__/reset", method="POST", payload=None)

    def fixture_state(self) -> dict[str, Any]:
        """The fixture's own truth: how many leads exist per email, plus its counters."""
        return self.fixture_get("/__test__/state")

    def set_switches(self, **switches: Any) -> dict[str, Any]:
        return self.fixture_post("/__test__/switches", switches)

    def fixture_post(self, path: str, payload: Any = None) -> Any:
        return _http_json(f"{self.crm_url}{path}", method="POST", payload=payload)

    def reset_fixture(self) -> None:
        """Empty the store and clear every switch, so one case cannot leak into the next."""
        self.fixture_post("/__test__/reset")

    def settle_page(self) -> None:
        """Return the bound tab to the CRM index, tolerating a run still driving it.

        The recipe navigates the same tab the harness holds, so the previous case's last navigation
        can still be in flight when the next one starts. A single ``goto`` then fails with
        "Navigation is interrupted by another navigation", which is a race, not a defect in the
        code under test. Wait for the page to settle, then retry a bounded number of times.
        """
        with contextlib.suppress(Exception):
            self.page.wait_for_load_state("load", timeout=5000)
        target = self.crm_url + "/"
        for attempt in range(6):
            try:
                self.page.goto(target, wait_until="commit", timeout=10_000)
                return
            except Exception:  # noqa: BLE001 - a race with an in-flight navigation
                if attempt == 5:
                    raise
                time.sleep(0.5)

    def seed_lead(
        self, email: str, *, interest: str = "Data course", company: str = "Seed Co"
    ) -> None:
        """Store one lead through the real save endpoint, so 'this lead already exists' is true."""
        body = urllib.parse.urlencode(
            {"email": email, "interest": interest, "company": company}
        ).encode("utf-8")
        request = urllib.request.Request(f"{self.crm_url}/leads/new", data=body, method="POST")
        request.add_header("content-type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(request, timeout=20.0):
            pass

    # -- runs ------------------------------------------------------------
    def make_run(
        self,
        rows: list[dict[str, Any]],
        *,
        recipe: dict[str, Any] | None = None,
        key_column: str = "email",
    ) -> str:
        """Seed one tenant/workflow/batch, ingest the recipe, and create the run through the API.

        ``key_column`` names the row column the ledger's *input record* key is taken from. It is
        not the recipe's ``record_key`` (the runner renders that from the recipe); it only has to
        be present on every row, so a batch may carry a row whose email is empty -- that record is
        the one a precheck rule parks, and it still needs an input key of its own.
        """
        import psycopg

        from packages.ledger import BatchRow, insert_batch

        document = recipe if recipe is not None else self.recipe()
        document = _rewrite_origin(document, self.crm_port)
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            tenant_id = self._one(
                conn, "INSERT INTO tenants (name) VALUES ('e2e tenant') RETURNING id"
            )
            workflow_id = self._one(
                conn,
                "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
                "VALUES (%s, 'E2E leads', 'lead-crm', %s) RETURNING id",
                (tenant_id, [self.crm_url]),
            )
            batch_id = insert_batch(
                conn,
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                kind="csv",
                rows=[
                    # The ledger requires a ready row to carry its record_key (0001_init's check).
                    BatchRow(
                        row_index=index,
                        raw=dict(row),
                        clean=dict(row),
                        record_key=(
                            str(row[key_column]).lower()
                            if row.get(key_column)
                            else f"row-{index:04d}"
                        ),
                    )
                    for index, row in enumerate(rows, start=1)
                ],
            )
        version = self.api_post(f"/api/v1/recipes?workflow_id={workflow_id}", document)
        run = self.api_post(
            "/api/v1/runs",
            {
                "workflow_id": str(workflow_id),
                "recipe_version_id": str(version["id"]),
                "input_batch_id": str(batch_id),
                "mode": "batch",
            },
        )
        return str(run["run_id"])

    @staticmethod
    def _one(conn: Any, sql: str, params: tuple = ()) -> Any:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def recipe(self) -> dict[str, Any]:
        """The fixture's pinned recipe, with the placeholder origin rewritten to the live port."""
        return _rewrite_origin(json.loads(RECIPE_FILE.read_text(encoding="utf-8")), self.crm_port)

    # -- the worker ------------------------------------------------------
    def start_worker(
        self,
        *,
        worker_id: str = "e2e-worker",
        session: str | None = None,
        max_seconds: float = WORKER_TIMEOUT_SECONDS,
        poll_seconds: float = 0.5,
        hub_url: str | None = None,
        heartbeat_timeout: float | None = None,
        heartbeat_interval: float | None = None,
    ) -> subprocess.Popen:
        """Start a runner worker against this session. Kept so teardown can kill it."""
        url = hub_url or self.api_base.replace("http://", "ws://", 1)
        command = [
            sys.executable,
            "-m",
            "services.runner",
            "--worker-id",
            worker_id,
            "--executor",
            "browser",
            "--session",
            session or self.session_id,
            "--hub-url",
            url,
            "--runner-secret",
            RUNNER_SECRET,
            "--poll-seconds",
            str(poll_seconds),
            "--max-seconds",
            str(max_seconds),
        ]
        if heartbeat_timeout is not None:
            command += ["--heartbeat-timeout", str(heartbeat_timeout)]
        if heartbeat_interval is not None:
            command += ["--heartbeat-interval", str(heartbeat_interval)]
        env = dict(os.environ)
        env["DATABASE_URL"] = self.dsn
        env["PYTHONPATH"] = str(REPO_ROOT)
        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=self._log_handle(worker_id),
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.workers.append(process)
        return process

    def _log_handle(self, worker_id: str) -> Any:
        """A per-worker log file.

        A worker's output must go to a file, never to an unread pipe: the pipe would fill and block
        the worker, and it would also hide the exact error the worker printed — which is the single
        most useful thing to see when a run does not progress.
        """
        path = _scratch_dir("worker-logs") / f"{worker_id}-{uuid.uuid4().hex[:8]}.log"
        self.worker_logs.append(path)
        return path.open("w", encoding="utf-8")

    def worker_output(self, *, tail: int = 1200) -> str:
        """The tail of the most recent worker's log, for a failure message."""
        for path in reversed(self.worker_logs):
            try:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if text:
                return text[-tail:]
        return ""

    def workers_exited(self) -> bool:
        """True when a worker was started and none is still running."""
        return bool(self.workers) and all(process.poll() is not None for process in self.workers)

    def stop_workers(self) -> None:
        for process in self.workers:
            if process.poll() is None:
                process.terminate()
        for process in self.workers:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        self.workers.clear()

    # -- polling ---------------------------------------------------------
    def describe(self, run_id: str) -> str:
        """The run and every record, compact, for a failure message.

        A bare "timed out" hides the answer. The status, the pause reason and each record's state
        and reason are exactly what a person needs to see, and they cost one request each.
        """
        try:
            detail = self.api_get(f"/api/v1/runs/{run_id}")
            records = self.api_get(f"/api/v1/runs/{run_id}/records")
        except Exception as error:  # noqa: BLE001 - a diagnostic must never raise
            return f"<could not read run {run_id}: {error}>"
        rows = "; ".join(
            f"{record.get('record_key')}={record.get('state')}({record.get('reason')})"
            for record in records
        )
        output = self.worker_output()
        suffix = f" | worker: {output!r}" if output else ""
        return (
            f"status={detail.get('status')} pause_reason={detail.get('pause_reason')!r} "
            f"records=[{rows}]{suffix}"
        )

    def wait_for_run(
        self,
        run_id: str,
        predicate: Callable[[list[dict[str, Any]]], Any],
        *,
        timeout: float = 90.0,
    ) -> Any:
        """Poll ``GET /api/v1/runs/{id}/records`` until ``predicate(records)`` is truthy."""
        return _wait_until(
            lambda: predicate(self.api_get(f"/api/v1/runs/{run_id}/records")),
            timeout=timeout,
            message=lambda: (
                f"run {run_id} records never satisfied the predicate: " + self.describe(run_id)
            ),
        )

    def wait_for_run_detail(
        self, run_id: str, predicate: Callable[[dict[str, Any]], Any], *, timeout: float = 90.0
    ) -> Any:
        """Wait until the run body satisfies ``predicate``.

        A run that reached ``completed``, ``cancelled`` or ``failed`` can never satisfy a predicate
        about *how* it paused, so the wait stops there instead of burning the whole timeout.
        """
        terminal = {"completed", "cancelled", "failed"}

        def satisfied() -> Any:
            body = self.api_get(f"/api/v1/runs/{run_id}")
            if predicate(body):
                return body
            if body.get("status") in terminal:
                raise AssertionError(
                    f"run {run_id} ended as {body.get('status')} and can never satisfy the "
                    f"predicate: {self.describe(run_id)}"
                )
            if self.workers_exited():
                raise AssertionError(
                    f"every worker exited and run {run_id} cannot progress: {self.describe(run_id)}"
                )
            return None

        return _wait_until(
            satisfied,
            timeout=timeout,
            message=lambda: f"run {run_id} never satisfied the predicate: " + self.describe(run_id),
        )

    def wait_for_terminal(self, run_id: str, *, timeout: float = 120.0) -> list[dict[str, Any]]:
        """Wait until every record is out and the run is no longer queued or running."""
        exit_states = {"verified", "skipped", "parked", "failed", "cancelled"}

        def settled() -> list[dict[str, Any]] | None:
            detail = self.api_get(f"/api/v1/runs/{run_id}")
            records = self.api_get(f"/api/v1/runs/{run_id}/records")
            if detail["status"] in {"completed", "paused", "cancelled", "failed"} and all(
                record["state"] in exit_states for record in records
            ):
                return records
            return None

        return _wait_until(
            settled,
            timeout=timeout,
            message=lambda: f"run {run_id} never settled: " + self.describe(run_id),
        )

    def states(self, run_id: str) -> dict[str, str]:
        """``record_key -> state`` for a run, read through the API."""
        return {
            record["record_key"]: record["state"]
            for record in self.api_get(f"/api/v1/runs/{run_id}/records")
        }

    # -- the extension ---------------------------------------------------
    def close_session(self) -> None:
        """Detach the extension socket deterministically (the hub's own operator control)."""
        self.api_post(
            f"/api/v1/executor/sessions/{self.session_id}/close",
            None,
            headers={"Authorization": f"Bearer {RUNNER_SECRET}"},
        )

    # -- recording (PL-016b) ---------------------------------------------
    def start_recording(self, *, timeout: float = 15.0) -> None:
        """Press Start in the popup, as a person would, and wait for the worker to say so."""
        self.popup.click("#clear")
        self.popup.click("#start")
        self.popup.wait_for_function(
            "() => document.getElementById('state')?.dataset.state === 'recording'",
            timeout=timeout * 1000,
        )

    def stop_recording(self, *, timeout: float = 15.0) -> None:
        self.popup.click("#stop")
        self.popup.wait_for_function(
            "() => document.getElementById('state')?.dataset.state === 'idle'",
            timeout=timeout * 1000,
        )

    def recording_events(self) -> list[dict[str, Any]]:
        """The recording the worker holds, read back through its own message channel."""
        return list(
            self.popup.evaluate(
                "async () => { const r = await chrome.runtime.sendMessage({type: 'record:get'});"
                " return r?.events ?? []; }"
            )
        )

    def wait_for_observation(self, *, timeout: float = 8.0) -> dict[str, Any] | None:
        """Wait until the recording carries an observation, and return the event it sits on.

        A full-page save reports its confirmation from the page it landed on, and the worker only
        attaches that report while the recording is still on. The test therefore waits here, before
        Stop, so the commit step compiles with its ``assert_after`` rather than without one.
        """
        return self.wait_for_observation_after(0, timeout=timeout)

    def wait_for_observation_after(
        self, seq: int, *, timeout: float = 20.0
    ) -> dict[str, Any] | None:
        """Wait for evidence attached to an action recorded after ``seq``.

        A recording that already carries one observation -- the click that opened a page, often --
        would satisfy a bare "has any observation" check at once, and a test that stopped there
        would cut the recording off before the *commit's* own evidence arrived. Naming the action
        the evidence belongs to is what makes the wait mean what it says.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.recording_events():
                if event.get("observation") and int(event.get("seq", 0)) > seq:
                    return event
            time.sleep(0.2)
        return None

    def compile_via_popup(
        self,
        name: str,
        *,
        record_key: str | None = None,
        park_fields: list[str] | None = None,
        timeout: float = 25.0,
    ) -> dict[str, Any]:
        """Drive the popup's own Make recipe form, then read back what it compiled and stored.

        This is the form a person uses: set the hub, name the workflow, choose the record key,
        tick the fields to park on, press Make recipe. The form posts the recording to
        ``/api/v1/recipes/compile`` with the record key, the built-in rules and commit step it
        chose, and stores the result under its two session keys. The returned dict carries the
        recipe and row (or ``None`` each when the route rejected the recording) and the form's own
        words for the outcome.
        """
        self.popup.evaluate("(v) => { document.getElementById('hub').value = v; }", self.api_base)
        self.popup.fill("#make-name", name)
        if record_key is not None:
            self.popup.select_option("#make-key", value=record_key)
        if park_fields is not None:
            # Tick exactly these input columns' "park if empty" boxes, and untick the rest, so the
            # recipe carries a precheck rule for each. This is the person marking fields required.
            self.popup.evaluate(
                "(wanted) => { const boxes = document.querySelectorAll("
                "'#make-park-fields input[type=\"checkbox\"]');"
                " for (const box of boxes) { const column = box.getAttribute('data-column') || '';"
                " const on = wanted.includes(column);"
                " if (box.checked !== on) { box.checked = on;"
                " box.dispatchEvent(new Event('change', { bubbles: true })); } } }",
                park_fields,
            )
        # Clear the two session keys first: they are shared across tests, and a rejected compile
        # stores nothing, so reading them back would otherwise hand back a previous test's recipe.
        self.popup.evaluate(
            "async () => { await chrome.storage.session.remove("
            "['processlens:executor-recipe', 'processlens:executor-row']); }"
        )
        self.popup.click("#make-go")
        self.popup.wait_for_function(
            "() => { const t = document.getElementById('make-result')?.textContent || '';"
            " return t.includes('is ready') || t.includes('Could not'); }",
            timeout=timeout * 1000,
        )
        result = self.popup.evaluate(
            "() => document.getElementById('make-result')?.textContent || ''"
        )
        warnings = self.popup.evaluate(
            "() => document.getElementById('make-warnings')?.textContent || ''"
        )
        stored = self.popup.evaluate(
            "async () => { const s = await chrome.storage.session.get("
            "['processlens:executor-recipe', 'processlens:executor-row']);"
            " return { recipe: s['processlens:executor-recipe'] ?? null,"
            " row: s['processlens:executor-row'] ?? null }; }"
        )
        # The form's own words decide: only a success is a compiled recipe.
        compiled = "is ready" in str(result)
        return {
            "recipe": stored.get("recipe") if compiled else None,
            "row": stored.get("row") if compiled else None,
            "result": result,
            "warnings": warnings,
        }

    def make_form_columns(self) -> list[dict[str, str]]:
        """The input columns the popup's Make recipe form is offering, as the person sees them.

        The recorded columns are the form's own answer to "what did this demonstration type?":
        the golden asserts on them rather than guessing names, and reads the email column back
        from them so it can tick the matching park box.
        """
        return list(
            self.popup.evaluate(
                "() => Array.from(document.querySelectorAll('#make-key option'))"
                ".map((o) => ({ value: o.value, label: (o.textContent || '').trim() }))"
            )
        )

    def operator_id(self) -> str:
        """A real operator user id, created once in the scratch database.

        ``approvals.decided_by`` is a foreign key to ``users``, so a decision needs a genuine user
        row; the API deliberately never invents one (``/api/v1/operator`` reads it from config).
        The harness creates one user under a scratch tenant and reuses it.
        """
        cached = getattr(self, "_operator", None)
        if cached is not None:
            return str(cached)
        import psycopg

        with psycopg.connect(self.dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO tenants (name) VALUES ('e2e operator') RETURNING id")
            tenant = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO users (tenant_id, email, name) "
                "VALUES (%s, 'operator@e2e.test', 'E2E Operator') RETURNING id",
                (tenant,),
            )
            operator = str(cur.fetchone()[0])
        self._operator = operator
        return operator

    def settle_run(
        self,
        run_id: str,
        *,
        timeout: float = 240.0,
        max_seconds: float = 260,
        on_approval: Any = None,
    ) -> list[dict[str, Any]]:
        """Start a worker, approve commit gates, and settle the run.

        One designed pause is driven here, exactly as the operator page would: a compiled recipe
        carries ``approval_policy: {commit: first_record}``, so the first commit is previewed and
        the run pauses on an open approval request.

        ``on_approval`` is called with each open request before it is approved, so a test can assert
        what the run looked like while a person was still deciding -- no record written yet, the tab
        held at the gate (PL-027).

        The canary no longer needs a resume (PL-017 requirement 2): the claim query claims by the
        input row, so the first record to reach a commit is row 0, which each golden test orders as
        a record that verifies.

        It returns once every record is out and the run is no longer running.
        """
        self.start_worker(max_seconds=max_seconds)
        exit_states = {"verified", "skipped", "parked", "failed", "cancelled"}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for request in self.api_get(f"/api/v1/runs/{run_id}/approvals"):
                if on_approval is not None:
                    on_approval(request)
                self.api_post(
                    "/api/v1/approvals",
                    {
                        "request_id": request["id"],
                        "decision": "approved",
                        "decided_by": self.operator_id(),
                    },
                )
            detail = self.api_get(f"/api/v1/runs/{run_id}")
            records = self.api_get(f"/api/v1/runs/{run_id}/records")
            settled = all(record["state"] in exit_states for record in records)
            if settled and (detail["status"] in {"completed", "paused"} or self.workers_exited()):
                return records
            time.sleep(0.4)
        raise AssertionError("the run never settled: " + self.describe(run_id))


_CLICK_OVERHEAD_JS = """
async (samples) => {
  const raf = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));
  const target =
    Array.from(document.querySelectorAll('button, a, input, select')).find((node) => {
      const box = node.getBoundingClientRect();
      return box.width > 0 && box.height > 0;
    }) || document.body;
  const out = [];
  for (let i = 0; i < samples; i += 1) {
    const started = performance.now();
    target.getBoundingClientRect();
    await raf();
    await raf();
    const settled = target.getBoundingClientRect();
    document.elementFromPoint(settled.left + settled.width / 2, settled.top + settled.height / 2);
    out.push(performance.now() - started);
  }
  return out;
}
"""


def measure_click_overhead(harness: Harness, samples: int) -> dict[str, Any]:
    """Measure the page-side work PL-017b adds to one click, on the bound tab.

    Per sample it runs what the executor's guard adds on a still page: a box measurement, two
    consecutive animation frames, a second measurement and one ``elementFromPoint`` at the box
    center, timed with ``performance.now()``. Returns ``{"median_ms", "samples_ms", "count"}``.

    The tab may still be finishing the run's last navigation when this is called, so it waits for
    the document to settle first: an evaluate that lands mid-navigation is told the execution
    context was destroyed, which is a timing fact about the measurement, not about the guard.
    """
    count = max(1, int(samples))
    # The run leaves the bound tab on the page of its last step, and that page may still be
    # navigating (a full-page save). Navigate to the fixture's stable index first: evaluating during
    # a navigation is told the execution context was destroyed, which is a timing fact about the
    # measurement, not about the guard.
    harness.settle_page()
    timings = [float(value) for value in harness.page.evaluate(_CLICK_OVERHEAD_JS, count)]
    ordered = sorted(timings)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "median_ms": round(median, 3),
        "samples_ms": [round(value, 3) for value in timings],
        "count": count,
    }


def _rewrite_origin(document: dict[str, Any], crm_port: int) -> dict[str, Any]:
    """Replace the recipe's placeholder origin (port 0) with the fixture's real port."""
    text = json.dumps(document).replace(PLACEHOLDER_ORIGIN, f"http://127.0.0.1:{crm_port}")
    return json.loads(text)


def _retire_open_runs(dsn: str) -> None:
    """Cancel any run an earlier test left open.

    Every test shares one session database, so a run left queued, running or paused would be
    claimed or taken over by the next test's worker instead of the run under test.
    """
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE runs SET status = 'cancelled', finished_at = now() "
            "WHERE status IN ('queued', 'running', 'paused')"
        )


def simple_recipe(
    steps: list[dict[str, Any]],
    *,
    origin: str,
    record_key: str = "{{row.email | lower}}",
    input_fields: dict[str, Any] | None = None,
    rules: list[dict[str, Any]] | None = None,
    approval_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A minimal valid v1 recipe, for the cases the fixture recipe does not cover."""
    fields = input_fields or {
        "email": {"type": "string"},
        "interest": {"type": "string"},
        "company": {"type": "string"},
    }
    schema: dict[str, Any] = {"fields": fields}
    # The schema requires at least one rule when the key is present, so an empty list is omitted.
    if rules:
        schema["rules"] = rules
    return {
        "format_version": 1,
        "meta": {
            "name": "E2E case",
            "when_to_use": "An end-to-end case against the lead-CRM fixture.",
            "input_schema": schema,
            "record_key": record_key,
            "idempotency_key": record_key,
            "approval_policy": approval_policy or {"commit": "none"},
            "allowed_origins": [origin],
        },
        "steps": steps,
    }


# ---------------------------------------------------------------- collection


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """When the flag is on but Playwright is absent, skip the e2e tests instead of erroring."""
    if not E2E_ENABLED:
        return
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        marker = pytest.mark.skip(reason="playwright is not installed (pip install -e .[dev])")
        for item in items:
            item.add_marker(marker)


# ---------------------------------------------------------------- session fixtures


@pytest.fixture(scope="session")
def _playwright() -> Iterator[Any]:
    """The Playwright driver, with the browser installed inside the repo if it is missing."""
    playwright_api = pytest.importorskip(
        "playwright.sync_api", reason="the end-to-end tests need playwright"
    )
    with playwright_api.sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope="session")
def fixture_site() -> Iterator[dict[str, Any]]:
    """The two fixture origins, started as a subprocess and read from its handshake line."""
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE_SERVER), "--port", "0", "--partner-port", "0"],
        cwd=str(FIXTURE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        line = _read_line(process.stdout, timeout=30.0)
        handshake = json.loads(line)
        yield handshake
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A freshly created, fully migrated scratch database, dropped at the end of the session."""
    import uuid

    import psycopg

    from db.migrate import apply_migrations

    base_dsn = _dotenv_dsn()
    name = f"processlens_e2e_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(base_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    dsn = _with_database(base_dsn, name)
    try:
        apply_migrations(dsn, log=lambda _message: None)
        yield dsn
    finally:
        _drop_database(base_dsn, name)


@pytest.fixture(scope="session")
def built_extension() -> Path:
    """`apps/extension/dist`, built once if it is not already there."""
    if not (EXTENSION_DIST / "manifest.json").is_file():
        npm = shutil.which("npm") or "npm"
        subprocess.run(
            [npm, "--prefix", str(EXTENSION_DIR), "run", "build"],
            cwd=str(REPO_ROOT),
            check=True,
        )
    return EXTENSION_DIST


@pytest.fixture(scope="session")
def context(_playwright: Any, built_extension: Path) -> Iterator[Any]:
    """A persistent Chromium with the unpacked extension loaded.

    ``channel="chromium"`` selects Playwright's bundled build: branded Chrome and Edge no
    longer load an unpacked extension from the command line. The profile lives under the repo's
    gitignored ``.cache/`` (constitution §2.1), and the browser binary lives under
    ``.cache/ms-playwright`` (R9).
    """
    args = [
        f"--disable-extensions-except={built_extension}",
        f"--load-extension={built_extension}",
    ]
    profile = tempfile.mkdtemp(prefix="processlens-e2e-", dir=str(_scratch_dir("profiles")))
    browser_context = _launch_with_install(_playwright, profile, args)
    try:
        yield browser_context
    finally:
        browser_context.close()
        shutil.rmtree(profile, ignore_errors=True)


def _launch_with_install(playwright: Any, profile: str, args: list[str]) -> Any:
    """Launch the persistent context, installing the bundled Chromium once if it is missing."""
    # Requirement 4: the same two knobs for a first launch and a post-install retry.
    launch = {
        "user_data_dir": profile,
        "channel": "chromium",
        "headless": not E2E_HEADED,
        "slow_mo": E2E_SLOWMO_MS,
        "args": args,
    }
    try:
        return playwright.chromium.launch_persistent_context(**launch)
    except Exception as exc:  # noqa: BLE001 - the message decides whether to install
        message = str(exc)
        if "Executable doesn't exist" not in message and "playwright install" not in message:
            raise
        install = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            env=dict(os.environ),
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            raise AssertionError(
                "playwright could not install its bundled chromium: "
                f"{install.stderr.strip() or install.stdout.strip()}"
            ) from exc
        return playwright.chromium.launch_persistent_context(**launch)


@pytest.fixture(scope="session")
def extension_id(context: Any) -> str:
    """The extension's id, read from the browser's own target list (the hub pins it).

    ``context.service_workers`` is NOT reliable here: with ``--load-extension`` this Chromium
    registers the MV3 worker as a CDP target while Playwright's own tracked list can stay empty,
    which made this fixture time out even though the extension was loaded and running. Reading
    ``Target.getTargets`` over CDP, exactly as the repository's Node smoke test does, is the
    reliable source. Any page works as the session host; the persistent context opens one.
    """

    def worker_url() -> str | None:
        pages = context.pages
        if not pages:
            return None
        session = context.new_cdp_session(pages[0])
        try:
            targets = session.send("Target.getTargets")["targetInfos"]
        finally:
            session.detach()
        for target in targets:
            if target.get("type") == "service_worker" and "service-worker" in target.get("url", ""):
                return str(target["url"])
        return None

    url = _wait_until(
        worker_url,
        timeout=30.0,
        message="the extension's service worker never started (extension not loaded)",
    )
    return str(url).split("/")[2]


def _session_operator(database_url: str) -> str:
    """One owner user under its own tenant, for the API this session starts."""
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (name) VALUES ('e2e popup operator') RETURNING id")
        tenant = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO users (tenant_id, email, name, role) "
            "VALUES (%s, %s, 'E2E Popup Operator', 'owner') RETURNING id",
            (tenant, f"popup-{uuid.uuid4().hex[:8]}@e2e.test"),
        )
        return str(cur.fetchone()[0])


@pytest.fixture(scope="session")
def api_server(database_url: str, extension_id: str) -> Iterator[dict[str, Any]]:
    """The API and hub under uvicorn on an ephemeral port, with the extension id pinned."""
    port = _free_port()
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["PROCESSLENS_EXTENSION_ID"] = extension_id
    env["PROCESSLENS_RUNNER_SECRET"] = RUNNER_SECRET
    # PL-030: the popup's Save as task stores the task under the configured operator, so the API
    # needs one, or every compile_via_popup would read the failed save as a refusal.
    env["PROCESSLENS_OPERATOR_ID"] = _session_operator(database_url)
    env["PYTHONPATH"] = str(REPO_ROOT)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "services.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=(_scratch_dir("api-logs") / f"uvicorn-{port}.log").open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_until(
            lambda: _health_ok(base),
            timeout=30.0,
            message="the API never answered /health",
        )
        yield {"base": base, "port": port}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def _health_ok(base: str) -> bool:
    try:
        return _http_json(f"{base}/health")["status"] == "ok"
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _poll_until_steady(harness: Harness, run_id: str, timeout: float = 90.0) -> None:
    """Wait for a run to reach a state that will not change."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = harness.api_get(f"/api/v1/runs/{run_id}")
        if body["status"] in {"completed", "paused", "cancelled", "failed"}:
            return
        time.sleep(0.25)
    raise AssertionError(f"the warm-up run never settled: {harness.describe(run_id)}")


def warm_up(harness: Harness) -> None:
    """Run one trivial batch before the real cases.

    The first command a freshly launched Chromium handles is the slowest: the MV3 service worker is
    cold, the debugger attaches for the first time, and the page-side resolver is compiled. On this
    machine that cold command occasionally exceeded the runner's five-second ack budget, so the very
    first test would park a record as "the extension did not answer" while every later test passed.
    A throwaway run moves that one-off cost out of the cases under test. It asserts nothing and it
    is not part of any acceptance criterion.
    """
    recipe = simple_recipe(
        [
            {
                "id": "warm-open",
                "action": "navigate",
                "url": f"{harness.crm_url}/new-lead.html",
            },
            {
                "id": "warm-end",
                "action": "end_record",
                "outcome": "skipped",
                "reason": "warm-up",
            },
        ],
        origin=harness.crm_url,
    )
    harness.set_switches()
    run_id = harness.make_run([{"email": "warmup@example.test", "company": "Warm"}], recipe=recipe)
    harness.start_worker(worker_id="warm-up", max_seconds=90)
    try:
        _poll_until_steady(harness, run_id)
    finally:
        harness.stop_workers()


@pytest.fixture(scope="session")
def harness(
    context: Any,
    extension_id: str,
    api_server: dict[str, Any],
    fixture_site: dict[str, Any],
    database_url: str,
) -> Iterator[Harness]:
    """Open the CRM tab and the popup, press Connect, and hand back a ready Harness."""
    page = context.new_page()
    page.goto(fixture_site["crm_url"] + "/")
    popup = context.new_page()
    popup.goto(f"chrome-extension://{extension_id}/popup/popup.html")
    # PL-033 put Connect, the address and Try one row under a closed "More"; open it, as a
    # person would, so the controls the harness drives are visible.
    popup.evaluate("() => document.querySelectorAll('details').forEach((d) => { d.open = true; })")

    # The active tab at Connect time is the one the extension binds, so the CRM tab is
    # brought forward and the popup's hub is set without focusing anything.
    hub_url = api_server["base"]
    popup.evaluate("(value) => { document.getElementById('hub').value = value; }", hub_url)
    page.bring_to_front()
    popup.evaluate("document.getElementById('connect').click()")

    popup.wait_for_function(
        "() => document.getElementById('session-state')?.dataset.state === 'ready'",
        timeout=30_000,
    )
    stored = popup.evaluate(
        "async () => (await chrome.storage.session.get('processlens:command-session'))"
        "['processlens:command-session'] ?? null"
    )
    assert stored and stored.get("sessionId"), "the extension did not store a session id"

    ready = Harness(
        api_base=api_server["base"],
        crm_url=fixture_site["crm_url"],
        partner_url=fixture_site["partner_url"],
        crm_port=int(fixture_site["crm_port"]),
        dsn=database_url,
        session_id=str(stored["sessionId"]),
        extension_id=extension_id,
        page=page,
        popup=popup,
    )
    try:
        warm_up(ready)
        yield ready
    finally:
        ready.stop_workers()


@pytest.fixture(scope="session")
def messy_site() -> Iterator[dict[str, Any]]:
    """The messy-crm fixture, started as a subprocess and read from its handshake line."""
    process = subprocess.Popen(
        [sys.executable, str(MESSY_SERVER), "--port", "0"],
        cwd=str(MESSY_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        line = _read_line(process.stdout, timeout=30.0)
        yield json.loads(line)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="session")
def messy_harness(
    context: Any,
    extension_id: str,
    api_server: dict[str, Any],
    messy_site: dict[str, Any],
    database_url: str,
    harness: Harness,
) -> Iterator[Harness]:
    """A Harness whose extension is bound to the messy fixture (PL-016b requirement 2).

    The extension binds the tab that is active at Connect, and the command session holds one tab,
    so the messy tab must be brought forward and the session re-established against it. The popup
    and its two session keys are shared with the lead-crm harness; only the bound tab differs.
    """
    page = context.new_page()
    page.goto(messy_site["crm_url"] + "/")
    # A popup of its own: the lead-crm harness's popup and its session id must be left untouched,
    # or a later lead test drives the popup against the messy session.
    popup = context.new_page()
    popup.goto(f"chrome-extension://{extension_id}/popup/popup.html")
    # PL-033 put Connect, the address and Try one row under a closed "More"; open it, as a
    # person would, so the controls the harness drives are visible.
    popup.evaluate("() => document.querySelectorAll('details').forEach((d) => { d.open = true; })")
    popup.evaluate("v => { document.getElementById('hub').value = v; }", api_server["base"])
    page.bring_to_front()
    popup.evaluate("document.getElementById('connect').click()")
    popup.wait_for_function(
        "() => document.getElementById('session-state')?.dataset.state === 'ready'",
        timeout=30_000,
    )
    stored = popup.evaluate(
        "async () => (await chrome.storage.session.get('processlens:command-session'))"
        "['processlens:command-session'] ?? null"
    )
    assert stored and stored.get("sessionId"), "the extension did not store a session id"

    ready = Harness(
        api_base=api_server["base"],
        crm_url=messy_site["crm_url"],
        partner_url=messy_site["crm_url"],
        crm_port=int(messy_site["crm_port"]),
        dsn=database_url,
        session_id=str(stored["sessionId"]),
        extension_id=extension_id,
        page=page,
        popup=popup,
        messy_url=messy_site["crm_url"],
    )
    try:
        yield ready
    finally:
        ready.stop_workers()
        # The command session is one per extension; hand it back to the lead-crm tab so the shared
        # session is left as the lead-crm harness expects it, whatever order the tests ran in.
        with contextlib.suppress(Exception):
            harness.page.bring_to_front()
            harness.popup.evaluate(
                "v => { document.getElementById('hub').value = v; }", api_server["base"]
            )
            harness.popup.evaluate("document.getElementById('connect').click()")
            harness.popup.wait_for_function(
                "() => ['ready', 'connecting'].includes("
                "document.getElementById('session-state')?.dataset.state)",
                timeout=15_000,
            )


@pytest.fixture(scope="session")
def corpus_site() -> Iterator[dict[str, Any]]:
    """The widget-corpus fixture (PL-018), started as a subprocess from its handshake line."""
    process = subprocess.Popen(
        [sys.executable, str(CORPUS_SERVER), "--port", "0"],
        cwd=str(CORPUS_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        line = _read_line(process.stdout, timeout=30.0)
        yield json.loads(line)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="session")
def corpus_harness(
    context: Any,
    extension_id: str,
    api_server: dict[str, Any],
    corpus_site: dict[str, Any],
    database_url: str,
    harness: Harness,
) -> Iterator[Harness]:
    """A Harness whose extension is bound to the widget corpus (PL-018 requirements 6 and 7).

    Same shape as ``messy_harness``: the extension binds the tab that is active at Connect, so the
    corpus tab is brought forward and the session re-established against it, with a popup of its
    own so the lead-crm harness's session is left untouched. On teardown the session is handed back
    to the lead-crm tab.
    """
    page = context.new_page()
    page.goto(corpus_site["crm_url"] + "/")
    popup = context.new_page()
    popup.goto(f"chrome-extension://{extension_id}/popup/popup.html")
    # PL-033 put Connect, the address and Try one row under a closed "More"; open it, as a
    # person would, so the controls the harness drives are visible.
    popup.evaluate("() => document.querySelectorAll('details').forEach((d) => { d.open = true; })")
    popup.evaluate("v => { document.getElementById('hub').value = v; }", api_server["base"])
    page.bring_to_front()
    popup.evaluate("document.getElementById('connect').click()")
    popup.wait_for_function(
        "() => document.getElementById('session-state')?.dataset.state === 'ready'",
        timeout=30_000,
    )
    stored = popup.evaluate(
        "async () => (await chrome.storage.session.get('processlens:command-session'))"
        "['processlens:command-session'] ?? null"
    )
    assert stored and stored.get("sessionId"), "the extension did not store a session id"

    ready = Harness(
        api_base=api_server["base"],
        crm_url=corpus_site["crm_url"],
        partner_url=corpus_site["crm_url"],
        crm_port=int(corpus_site["crm_port"]),
        dsn=database_url,
        session_id=str(stored["sessionId"]),
        extension_id=extension_id,
        page=page,
        popup=popup,
        corpus_url=corpus_site["crm_url"],
    )
    try:
        yield ready
    finally:
        ready.stop_workers()
        with contextlib.suppress(Exception):
            harness.page.bring_to_front()
            harness.popup.evaluate(
                "v => { document.getElementById('hub').value = v; }", api_server["base"]
            )
            harness.popup.evaluate("document.getElementById('connect').click()")
            harness.popup.wait_for_function(
                "() => ['ready', 'connecting'].includes("
                "document.getElementById('session-state')?.dataset.state)",
                timeout=15_000,
            )


@pytest.fixture(autouse=True)
def _clean_fixture(request: pytest.FixtureRequest) -> Iterator[None]:
    """Every fixture-site test starts from a clean slate: no open run, empty store, CRM index.

    The page matters: the extension's guards run before every command against whatever the bound
    tab currently shows, so a page left on the login or challenge view by an earlier test would
    pause the next one. Navigating back to the index is the reset for that axis.

    A ``real_apps`` test drives Odoo or Twenty instead, has no fixture site of its own to clean,
    and binds the extension to its own tab (``tests/e2e/conftest.py``'s ``odoo_harness`` and
    ``twenty_harness``). Building the fixture-site harness for it would start a site nobody
    navigates and run its warm-up batch against the one extension session the real-app test needs,
    so the whole fixture is asked for lazily and skipped there.
    """
    if request.node.get_closest_marker("real_apps") is not None:
        yield
        return

    harness: Harness = request.getfixturevalue("harness")
    harness.stop_workers()
    _retire_open_runs(harness.dsn)
    harness.reset_fixture()
    harness.settle_page()
    # A test that closed the session mid-command leaves that command unwinding, and the extension
    # refuses new work until it settles; it reports "ready" only then. Starting before that would
    # park the next test's first record with "the previous command has not finished unwinding".
    harness.popup.wait_for_function(
        "() => document.getElementById('session-state')?.dataset.state === 'ready'",
        timeout=60_000,
    )
    yield
    harness.stop_workers()


__all__ = [
    "EXISTING_EMAIL",
    "NEW_EMAILS",
    "PARKED_EMAIL",
    "Harness",
    "bind_extension_to",
    "simple_recipe",
]


# ------------------------------------------------------- the real test apps (PL-027)
#
# The golden tests record and replay against Odoo 18 and Twenty CRM, the real apps PL-022
# stands up locally. They reuse everything above -- the built extension, the API and hub, the
# runner worker, the scratch database -- and differ in one thing: the tab the extension binds,
# and therefore what a recording sees. `Harness` is deliberately unchanged: the golden drives it
# exactly as the fixture tests do, so a difference in behaviour is a difference in the app, not
# in the test's own plumbing.
#
# The two apps are reset by `reset.py` before a test runs, which drops Odoo's database; the page
# therefore logs in again afterwards, which is why the order below is reset -> page -> connect.


def bind_extension_to(
    context: Any, extension_id: str, api_server: dict[str, Any], page: Any
) -> tuple[Any, str]:
    """Bind the extension's one command session to ``page`` and return (popup, session id).

    The extension binds the tab that is active when Connect is pressed, so the target tab is
    brought forward first. The popup is a page of its own: the session keys it holds are shared
    with every other popup, and the tab it is bound to is not.
    """
    popup = context.new_page()
    popup.goto(f"chrome-extension://{extension_id}/popup/popup.html")
    # PL-033 put Connect, the address and Try one row under a closed "More"; open it, as a
    # person would, so the controls the harness drives are visible.
    popup.evaluate("() => document.querySelectorAll('details').forEach((d) => { d.open = true; })")
    popup.evaluate("v => { document.getElementById('hub').value = v; }", api_server["base"])
    page.bring_to_front()
    popup.evaluate("document.getElementById('connect').click()")
    popup.wait_for_function(
        "() => document.getElementById('session-state')?.dataset.state === 'ready'",
        timeout=30_000,
    )
    stored = popup.evaluate(
        "async () => (await chrome.storage.session.get('processlens:command-session'))"
        "['processlens:command-session'] ?? null"
    )
    assert stored and stored.get("sessionId"), "the extension did not store a session id"
    return popup, str(stored["sessionId"])


APP_ORIGIN_ENV = "PROCESSLENS_E2E_SKIP_RESET"


@pytest.fixture(scope="session")
def app_env() -> dict[str, Any]:
    """The test apps' settings (`infra/test-apps/.env`, else the committed `.env.example`)."""
    from .real_apps import app_env as load

    return load()


@pytest.fixture(scope="session")
def odoo_reset(app_env: dict[str, Any]) -> dict[str, Any]:
    """Reset Odoo before the Odoo golden runs (PL-027 requirement 4).

    Session-scoped on purpose: the reset drops the database and installs five modules, which is
    minutes, and one pytest invocation is one run of the golden. Running the golden again is a
    second invocation, and that one resets again. ``PROCESSLENS_E2E_SKIP_RESET=1`` skips it for a
    developer who wants to watch the recording alone.
    """
    if os.environ.get(APP_ORIGIN_ENV) == "1":
        return {"target": "odoo", "seconds": 0.0, "summary": "skipped by request"}
    from .real_apps import reset_app

    return reset_app("odoo")


@pytest.fixture(scope="session")
def twenty_reset(app_env: dict[str, Any]) -> dict[str, Any]:
    """Reset Twenty before the Twenty golden runs; seconds, not minutes."""
    if os.environ.get(APP_ORIGIN_ENV) == "1":
        return {"target": "twenty", "seconds": 0.0, "summary": "skipped by request"}
    from .real_apps import reset_app

    return reset_app("twenty")


@pytest.fixture(scope="session")
def odoo_app(app_env: dict[str, Any], odoo_reset: dict[str, Any]) -> Any:
    """The XML-RPC client the Odoo golden seeds and verifies with, after the reset."""
    from .real_apps import Odoo

    return Odoo(app_env)


@pytest.fixture(scope="session")
def odoo_page(context: Any, odoo_app: Any) -> Iterator[Any]:
    """A Chromium tab on Odoo, logged in through the real form after the reset."""
    from .real_apps import login_odoo, odoo_base

    page = context.new_page()
    login_odoo(page, odoo_app.env, base=odoo_base(odoo_app.env))
    yield page


@pytest.fixture(scope="session")
def odoo_harness(
    context: Any,
    extension_id: str,
    api_server: dict[str, Any],
    database_url: str,
    odoo_app: Any,
    odoo_page: Any,
) -> Iterator[Harness]:
    """A Harness whose extension is bound to the Odoo tab.

    Same shape as ``messy_harness``: the extension holds one command session, so the Odoo tab is
    brought forward and the session is established against it.
    """
    from .real_apps import odoo_base

    base = odoo_base(odoo_app.env)
    popup, session_id = bind_extension_to(context, extension_id, api_server, odoo_page)
    port = int(base.rsplit(":", 1)[-1])
    ready = Harness(
        api_base=api_server["base"],
        crm_url=base,
        partner_url=base,
        crm_port=port,
        dsn=database_url,
        session_id=session_id,
        extension_id=extension_id,
        page=odoo_page,
        popup=popup,
    )
    try:
        yield ready
    finally:
        ready.stop_workers()


@pytest.fixture(scope="session")
def twenty_page(
    context: Any, twenty_reset: dict[str, Any], app_env: dict[str, Any]
) -> Iterator[Any]:
    """A Chromium tab on Twenty, logged in through the real form."""
    from .real_apps import login_twenty, twenty_base

    page = context.new_page()
    login_twenty(page, app_env, base=twenty_base(app_env))
    yield page


@pytest.fixture(scope="session")
def twenty_harness(
    context: Any,
    extension_id: str,
    api_server: dict[str, Any],
    database_url: str,
    app_env: dict[str, Any],
    twenty_page: Any,
) -> Iterator[Harness]:
    """A Harness whose extension is bound to the Twenty tab."""
    from .real_apps import twenty_base

    base = twenty_base(app_env)
    popup, session_id = bind_extension_to(context, extension_id, api_server, twenty_page)
    port = int(base.rsplit(":", 1)[-1])
    ready = Harness(
        api_base=api_server["base"],
        crm_url=base,
        partner_url=base,
        crm_port=port,
        dsn=database_url,
        session_id=session_id,
        extension_id=extension_id,
        page=twenty_page,
        popup=popup,
    )
    try:
        yield ready
    finally:
        ready.stop_workers()
