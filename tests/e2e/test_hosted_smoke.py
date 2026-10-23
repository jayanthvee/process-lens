"""The hosted ProcessLens, end to end: two Chromes, one Render, real Odoo.

Opt-in only: set ``PROCESSLENS_E2E_HOSTED_URL`` (the deployed API, e.g.
``https://processlens-api-a2bl.onrender.com``) and ``PROCESSLENS_E2E_HOSTED_CODE`` (its team access
code). It drives the deployed hub and supervisor, so it never runs with the ordinary suite.

What it proves, all through the public address and nothing local but Chrome and Odoo:

* the extension built with the hosted address and code connects (PL-086);
* a recording compiles and saves as a task on the hosted API;
* a spreadsheet uploads and checks there;
* a run goes to the Chrome of the person who started it (PL-087): Ben's run waits while Ben's
  Chrome is away, even though Anna's Chrome is connected and idle, then runs once Ben connects.

Odoo is the shared test app, so nothing else may drive it while this runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from .conftest import (
    EXTENSION_DIR,
    Harness,
    _launch_with_install,
    _scratch_dir,
    bind_extension_to,
)
from .test_odoo_quote_golden import (
    DEMO_CUSTOMER,
    DEMO_PRODUCT,
    ROWS,
    column_for,
    record_new_quote,
    recorded_columns,
    rows_for,
)

HOSTED_URL = os.environ.get("PROCESSLENS_E2E_HOSTED_URL", "").strip().rstrip("/")
HOSTED_CODE = os.environ.get("PROCESSLENS_E2E_HOSTED_CODE", "").strip()
EXTENSION_ID = "findeblpobkgndppbkmpcfliiijggben"  # the committed extension-key.pub's id
TASK_NAME = f"Hosted smoke {time.strftime('%m%d-%H%M%S')}"
RUN_SECONDS = 600
QUIET_SECONDS = 30

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real_apps,
    pytest.mark.skipif(
        not (HOSTED_URL and HOSTED_CODE),
        reason="set PROCESSLENS_E2E_HOSTED_URL and PROCESSLENS_E2E_HOSTED_CODE to run",
    ),
]

OUT = _scratch_dir("hosted-smoke")


def _note(message: str) -> None:
    with (OUT / "trace.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%H:%M:%S')} {message.rstrip()}\n")


# ---------------------------------------------------------------- the hosted API


class Hosted:
    """The deployed API, spoken to the way the extension and the supervisor do: a bearer code."""

    def __init__(self) -> None:
        self.client = httpx.Client(
            base_url=HOSTED_URL,
            headers={"authorization": f"Bearer {HOSTED_CODE}"},
            timeout=60.0,
        )

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(path, params=params or None)
        assert response.status_code == 200, f"GET {path}: {response.status_code} {response.text}"
        return response.json()

    def post(self, path: str, payload: Any) -> Any:
        response = self.client.post(path, json=payload)
        assert response.status_code in (200, 201), (
            f"POST {path}: {response.status_code} {response.text}"
        )
        return response.json()

    def upload(self, workflow_id: str, rows: list[dict[str, str]]) -> str:
        columns = list(rows[0])
        lines = [",".join(columns)] + [
            ",".join(json.dumps(row[column]) for column in columns) for row in rows
        ]
        response = self.client.post(
            "/api/v1/batches",
            data={"workflow_id": workflow_id},
            files={"file": ("rows.csv", ("\n".join(lines) + "\n").encode(), "text/csv")},
        )
        assert response.status_code == 201, f"upload: {response.status_code} {response.text}"
        return str(response.json()["batch_id"])

    def run(self, run_id: str) -> dict[str, Any]:
        return self.get(f"/api/v1/runs/{run_id}")

    def settle(self, run_id: str) -> dict[str, Any]:
        """Follow the run to its end, approving its first row as the run page's person does.

        A batch run that had no "Try one row" first asks for the first row's approval (PL-040).
        """
        deadline = time.monotonic() + RUN_SECONDS
        detail = self.run(run_id)
        while time.monotonic() < deadline:
            approval = detail.get("open_approval")
            if detail["status"] == "paused" and approval:
                self.post(
                    "/api/v1/approvals",
                    {
                        "request_id": approval["id"],
                        "decision": "approved",
                        "decided_by": self.get("/api/v1/operator")["operator_id"],
                    },
                )
                _note(f"run {run_id[:8]}: approved its first row")
            elif detail["status"] not in {"queued", "running"}:
                break
            time.sleep(3)
            detail = self.run(run_id)
        _note(f"run {run_id[:8]} settled: {json.dumps(detail)[:600]}")
        return detail


class TracedHarness(Harness):
    """The harness, with each recording call timed into the trace (a slow hub shows up here)."""

    def _state(self) -> str:
        try:
            return str(self.popup.evaluate("() => document.getElementById('state')?.dataset.state"))
        except Exception as exc:  # noqa: BLE001 - a diagnostic must not fail the test itself
            return f"unreadable ({exc})"

    def start_recording(self, *, timeout: float = 15.0) -> None:
        started = time.monotonic()
        super().start_recording(timeout=timeout)
        _note(f"start_recording {time.monotonic() - started:.1f}s, state {self._state()}")

    def recording_events(self) -> list[dict[str, Any]]:
        started = time.monotonic()
        events = super().recording_events()
        _note(
            f"recording_events {time.monotonic() - started:.2f}s: {len(events)} events,"
            f" state {self._state()}"
        )
        return events

    def stop_recording(self, *, timeout: float = 15.0) -> None:
        _note(f"stop_recording, state {self._state()}")
        try:
            super().stop_recording(timeout=timeout)
        except Exception:
            self.popup.screenshot(path=str(OUT / "popup-at-stop.png"))
            self.page.screenshot(path=str(OUT / "page-at-stop.png"))
            raise


# ---------------------------------------------------------------- the two Chromes


@pytest.fixture(scope="module")
def hosted_extension() -> Path:
    """The extension a tester is handed: the hosted address and code baked in, the fixed key."""
    dist = OUT / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    script = (
        "import { buildExtension } from './scripts/release-lib.mjs';"
        "await buildExtension({ dist: process.env.PL_DIST, hubUrl: process.env.PL_HUB,"
        " accessCode: process.env.PL_CODE });"
    )
    env = {**os.environ, "PL_DIST": str(dist), "PL_HUB": HOSTED_URL, "PL_CODE": HOSTED_CODE}
    node = shutil.which("node") or "node"
    subprocess.run(
        [node, "--input-type=module", "-e", script], cwd=str(EXTENSION_DIR), env=env, check=True
    )
    return dist


def _chrome(playwright: Any, extension: Path, name: str) -> Any:
    profile = tempfile.mkdtemp(prefix=f"hosted-{name}-", dir=str(_scratch_dir("profiles")))
    args = [f"--disable-extensions-except={extension}", f"--load-extension={extension}"]
    return _launch_with_install(playwright, profile, args)


@pytest.fixture(scope="module")
def chromes(_playwright: Any, hosted_extension: Path) -> Iterator[dict[str, Any]]:
    anna = _chrome(_playwright, hosted_extension, "anna")
    ben = _chrome(_playwright, hosted_extension, "ben")
    try:
        yield {"anna": anna, "ben": ben}
    finally:
        anna.close()
        ben.close()


def _odoo_tab(context: Any, odoo_app: Any) -> Any:
    from .real_apps import login_odoo, odoo_base

    page = context.new_page()
    login_odoo(page, odoo_app.env, base=odoo_base(odoo_app.env))
    return page


def _browser_id(popup: Any) -> str:
    answer = popup.evaluate(
        "async () => (await chrome.runtime.sendMessage({type: 'executor:whoami'})) ?? {}"
    )
    browser = str(answer.get("browserId") or "")
    assert browser, f"the extension reported no browser id: {answer!r}"
    return browser


# ---------------------------------------------------------------- the test


def test_two_chromes_through_the_hosted_processlens(chromes: dict[str, Any], odoo_app: Any) -> None:
    from .real_apps import odoo_base

    hosted = Hosted()
    base = odoo_base(odoo_app.env)
    _note(f"=== hosted smoke against {HOSTED_URL} ===")
    assert hosted.get("/health")["db"] is True

    # The customers: two for each person, made through Odoo's API, never the browser.
    odoo_app.partner_id(DEMO_CUSTOMER)
    odoo_app.product_id(DEMO_PRODUCT)
    products = [product for _customer, product, _qty in ROWS]
    for product in products:
        odoo_app.product_id(product)
    stamp = time.strftime("%H%M%S")
    names = {who: [f"Hosted {who} {stamp} {n}" for n in (1, 2)] for who in ("Anna", "Ben")}
    for name in names["Anna"] + names["Ben"]:
        odoo_app.create("res.partner", {"name": name, "is_company": True})

    # Anna: her Chrome connects to Render, and she teaches the task there.
    anna_page = _odoo_tab(chromes["anna"], odoo_app)
    anna_popup, anna_session = bind_extension_to(
        chromes["anna"], EXTENSION_ID, {"base": HOSTED_URL}, anna_page
    )
    anna = TracedHarness(
        api_base=HOSTED_URL,
        crm_url=base,
        partner_url=base,
        crm_port=int(base.rsplit(":", 1)[-1]),
        dsn="",
        session_id=anna_session,
        extension_id=EXTENSION_ID,
        page=anna_page,
        popup=anna_popup,
    )
    anna_browser = _browser_id(anna_popup)
    _note(f"anna connected: session {anna_session[:8]}, browser {anna_browser[:6]}")

    record_new_quote(anna)
    columns = recorded_columns(anna)
    customer_column = column_for(columns, "customer", "partner")
    outcome = anna.compile_via_popup(TASK_NAME, record_key=customer_column, park_fields=[])
    recipe = outcome["recipe"]
    assert recipe is not None, outcome
    task = next(row for row in hosted.get("/api/v1/tasks") if row["name"] == TASK_NAME)
    _note(f"task saved on the hosted API: {task['workflow_id']}")

    fields = recipe["meta"]["input_schema"]["fields"]
    options = {product: f"[{odoo_app.product_code(product)}] {product}" for product in products}

    def rows(customers: list[str]) -> list[dict[str, str]]:
        return [
            rows_for(
                fields,
                customer=customer,
                product=products[index],
                quantity=str(index + 2),
                product_option=options[products[index]],
            )
            for index, customer in enumerate(customers)
        ]

    def start(customers: list[str], browser: str) -> str:
        batch = hosted.upload(str(task["workflow_id"]), rows(customers))
        checked = hosted.get(
            f"/api/v1/batches/{batch}/check", recipe_version_id=str(task["recipe_version_id"])
        )
        assert checked["first_ready"] is not None, checked
        created = hosted.post(
            "/api/v1/runs",
            {
                "workflow_id": str(task["workflow_id"]),
                "recipe_version_id": str(task["recipe_version_id"]),
                "input_batch_id": batch,
                "mode": "batch",
                "browser_id": browser,
            },
        )
        return str(created["run_id"])

    # Ben: his Chrome has the extension, but he is away (disconnected) when his run is started.
    ben_page = _odoo_tab(chromes["ben"], odoo_app)
    ben_popup = chromes["ben"].new_page()
    ben_popup.goto(f"chrome-extension://{EXTENSION_ID}/popup/popup.html")
    ben_browser = _browser_id(ben_popup)
    assert ben_browser != anna_browser
    ben_popup.evaluate(
        "() => document.querySelectorAll('details').forEach((d) => { d.open = true; })"
    )
    ben_popup.wait_for_timeout(3000)  # the popup's own auto-connect settles first
    if not ben_popup.evaluate("() => document.getElementById('disconnect').disabled"):
        ben_popup.evaluate("document.getElementById('disconnect').click()")
    ben_popup.close()

    ben_run = start(names["Ben"], ben_browser)
    _note(f"ben's run {ben_run[:8]} created while ben is away")
    time.sleep(QUIET_SECONDS)
    assert hosted.run(ben_run)["status"] == "queued", (
        "Ben's run must wait for Ben's Chrome, not run in Anna's connected one"
    )

    # Anna's own run goes to her Chrome and finishes, while Ben's still waits.
    anna_run = start(names["Anna"], anna_browser)
    anna_done = hosted.settle(anna_run)
    assert anna_done["counts"]["verified"] == 2, anna_done
    assert hosted.run(ben_run)["status"] == "queued", "Ben's run moved without Ben's Chrome"

    # Ben comes back: his run goes to his Chrome.
    bind_extension_to(chromes["ben"], EXTENSION_ID, {"base": HOSTED_URL}, ben_page)
    ben_done = hosted.settle(ben_run)
    assert ben_done["counts"]["verified"] == 2, ben_done

    for name in names["Anna"] + names["Ben"]:
        assert odoo_app.orders_by_partner(odoo_app.partner_id(name)), f"no order in Odoo for {name}"
    summary = {"anna": anna_done["counts"], "ben": ben_done["counts"], "task": TASK_NAME}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _note("summary: " + json.dumps(summary))
