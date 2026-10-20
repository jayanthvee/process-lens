"""PL-068: a verification-code page pauses the run, the person types the code, it carries on.

The site is the local ``fixtures/sites/code-login`` page: a heading asking for the 6-digit code,
six split boxes, and -- once the right code is in -- a small form. The code is fixed in the
fixture, and no email or SMS is read anywhere.

The demonstration is recorded *after* the test posts the code once, so the cookies are set and
the person never types a code while recording: the recording cannot carry one. The cookies are
then cleared, so the replay meets the code page for real. The run pauses on it, and the test
posts the code through the API exactly as the web app does. The extension types it into the
split boxes and presses the page's own Verify; the row then finishes the recorded form.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.conftest import Harness, bind_extension_to

pytestmark = [pytest.mark.e2e, pytest.mark.real_apps]

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "fixtures" / "sites" / "code-login"
TRACE_DIR = REPO_ROOT / ".cache" / "pl068"
CODE = "246810"
CANDIDATE = "Code Golden Candidate"


def start_site() -> tuple[subprocess.Popen[str], str]:
    """Start the fixture server and read its handshake line."""
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE_DIR / "server.py"), "--port", "0"],
        cwd=str(FIXTURE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line.startswith("{"):
            return process, json.loads(line)["url"]
    process.kill()
    raise AssertionError("the code-login fixture never printed its handshake line")


@pytest.fixture(scope="module")
def code_site() -> Any:
    process, url = start_site()
    try:
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="module")
def code_harness(
    context: Any,
    extension_id: str,
    api_server: dict[str, Any],
    database_url: str,
    code_site: str,
) -> Any:
    """A Harness whose extension is bound to the code-login site, as the real-app goldens do."""
    page = context.new_page()
    page.goto(code_site, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    popup, session_id = bind_extension_to(context, extension_id, api_server, page)
    port = int(code_site.rsplit(":", 1)[-1])
    ready = Harness(
        api_base=api_server["base"],
        crm_url=code_site,
        partner_url=code_site,
        crm_port=port,
        dsn=database_url,
        session_id=session_id,
        extension_id=extension_id,
        page=page,
        popup=popup,
    )
    # The code route checks that the person sending it is in the run's tenant, so the run is made in
    # the API operator's own tenant rather than a fresh one.
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute(
            "SELECT tenant_id FROM users WHERE id = %s", (api_server["operator_id"],)
        ).fetchone()
    ready.operator_tenant_id = str(row[0])
    try:
        yield ready
    finally:
        ready.stop_workers()


def test_a_code_page_pauses_the_run_and_the_person_s_code_carries_it_on(
    code_harness, code_site: str
) -> None:
    harness = code_harness
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    page = harness.page
    base = code_site

    # 1. Pass the code page once, by hand, so the demonstration never types a code. Playwright's
    #    request shares the browser context's cookie jar, so the recording's pages are the form.
    response = page.request.post(f"{base}/code", form={"code": CODE})
    assert response.ok, response.status
    page.goto(f"{base}/form", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    # 2. Record the small form: the candidate's name, then Save.
    harness.start_recording()
    page.locator("#name").click()
    page.locator("#name").press_sequentially(CANDIDATE, delay=20)
    page.wait_for_timeout(500)
    last_seq = max(
        (event.get("seq", 0) for event in harness.recording_events() if event.get("step")),
        default=0,
    )
    page.get_by_role("button", name="Save").click()
    page.wait_for_url(f"{base}/saved", timeout=20_000)
    page.wait_for_timeout(800)
    assert harness.wait_for_observation_after(last_seq), "the save's evidence was never recorded"
    harness.stop_recording()

    events = harness.recording_events()
    (TRACE_DIR / "recording.json").write_text(json.dumps(events, indent=1), encoding="utf-8")
    assert any("/form" in (event.get("url") or "") for event in events), (
        "the recording is not from the fixture"
    )
    assert not any(CODE in json.dumps(event) for event in events), (
        "a verification code reached the recording"
    )

    # 3. Compile through the popup, as a person would.
    columns = [(column["label"], column["value"]) for column in harness.make_form_columns()]
    (TRACE_DIR / "columns.json").write_text(json.dumps(columns, indent=1), encoding="utf-8")
    name_column = next(
        (value for label, value in columns if "name" in label.lower()), columns[0][1]
    )
    outcome = harness.compile_via_popup("New candidate", record_key=name_column)
    (TRACE_DIR / "compile.json").write_text(
        json.dumps({"columns": columns, "outcome": outcome}, indent=1), encoding="utf-8"
    )
    assert outcome["recipe"] is not None, (
        f"the Make recipe form could not compile: {outcome['result']!r} {outcome['warnings']!r}"
    )
    recipe = outcome["recipe"]

    # 4. Clear the cookies: the replay meets the code page for real.
    harness.page.context.clear_cookies()

    rows = [{column: CANDIDATE for _label, column in columns}]
    run_id = harness.make_run(
        rows, recipe=recipe, key_column=name_column, tenant_id=harness.operator_tenant_id
    )

    # The worker is what drives the run into its code page; the run is only queued until one runs.
    harness.start_worker(max_seconds=280)

    # 5. Wait for the pause, then send the code exactly as the web app does -- and nowhere else.
    deadline = time.monotonic() + 180
    detail: dict[str, Any] = {}
    while time.monotonic() < deadline:
        detail = harness.api_get(f"/api/v1/runs/{run_id}")
        if detail.get("pause_reason") == "code_required":
            break
        time.sleep(0.5)
    (TRACE_DIR / "paused-run.json").write_text(json.dumps(detail, indent=1), encoding="utf-8")
    assert detail.get("status") == "paused", f"the run never paused: {detail}"
    assert detail.get("pause_reason") == "code_required", detail

    sent = harness.api_post(f"/api/v1/runs/{run_id}/code", {"code": CODE})
    (TRACE_DIR / "code-response.json").write_text(json.dumps(sent, indent=1), encoding="utf-8")
    assert sent.get("entered") is True, sent
    assert CODE not in json.dumps(sent), "the answer echoed the code"

    # 6. Resume, let the recorded steps finish, and check the row and the site.
    harness.api_post(f"/api/v1/runs/{run_id}/resume")
    records = harness.wait_for_terminal(run_id, timeout=240)
    (TRACE_DIR / "records.json").write_text(json.dumps(records, indent=1), encoding="utf-8")
    assert [record["state"] for record in records] == ["verified"], records

    saved = harness.page.request.get(f"{base}/saved")
    assert saved.ok
    assert CANDIDATE in saved.text(), "the site never received the recorded form"
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("RECORDING:", json.dumps(events, indent=2, sort_keys=True))
        print("RECIPE:", json.dumps(recipe, indent=2, sort_keys=True))
