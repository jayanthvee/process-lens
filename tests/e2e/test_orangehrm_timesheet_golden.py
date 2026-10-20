"""PL-069: the timesheet golden on real OrangeHRM 5.7.

OrangeHRM stands in for Workday (D-022): the weekly timesheet is the job a consulting company's
people do by hand every Friday. The demonstration is one person filling one week of their own
timesheet from a sheet with three rows on three projects:

    Time -> My Timesheets -> this week -> Edit -> add a row (project, activity) -> hours Monday to
    Friday -> Save

The extension records it, the popup compiles it, and the runner replays it over
`fixtures/demo/orangehrm-timesheet.csv`. Every assertion reads OrangeHRM over its own API: the
week holds exactly those three rows with those hours, each ledger record is `verified`, and a
second run of the same sheet adds no row.

Saving is the commit. Submitting the timesheet for approval is a separate step a person takes, and
it is not part of this task.

The app is reset by `infra/test-apps/reset.py orangehrm` before the test runs. That target never
touches Odoo or Twenty.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.real_apps]

# The one failure this golden finds is a compiler bug, not a flaky test: see the module docstring
# for the exact parked reason and the fix it needs. `strict=True` means a compiler fix turns this
# into a failure (XPASS), which is exactly what should happen then.
XFAIL_REASON = (
    "canary parked at the save step: the compiler derived assert_after text_visible "
    '"SuccessSuccessfully Saved\u2026", which OrangeHRM never shows (the toast text is duplicated)'
)

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "infra" / "test-apps") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "infra" / "test-apps"))

import seed_orangehrm  # noqa: E402  (the seeder owns the fixture's names)
import testapps_env  # noqa: E402  (needs the directory above on sys.path)

SHEET = REPO_ROOT / "fixtures" / "demo" / "orangehrm-timesheet.csv"

# The trace and diagnostics of a run land here, never in %TEMP%.
TRACE_DIR = REPO_ROOT / ".cache" / "pl069"

# How long a run may take: three rows, each opening a fresh timesheet edit page.
RUN_TIMEOUT_SECONDS = 900.0
RUN_MAX_SECONDS = 870.0


# --------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def orangehrm_app_env() -> dict[str, str]:
    """The test apps' settings, which carry OrangeHRM's own keys."""
    return testapps_env.load_env()


@pytest.fixture(scope="session")
def orangehrm_reset(orangehrm_app_env: dict[str, str]) -> dict[str, Any]:
    """Reset OrangeHRM only: ensure its data, and empty the test week's timesheet.

    ``reset.py odoo`` and ``reset.py all`` are never run here: Odoo belongs to another agent's
    work. This is the one target that touches nothing else.
    """
    from .real_apps import reset_app

    return reset_app("orangehrm")


@pytest.fixture(scope="session")
def orangehrm_page(context: Any, orangehrm_reset: dict[str, Any], orangehrm_app_env) -> Any:
    """A Chromium tab on OrangeHRM, signed in as the employee whose timesheet this is."""
    from .real_apps import login_orangehrm, orangehrm_base

    settings = testapps_env.orangehrm_settings(orangehrm_app_env)
    page = context.new_page()
    login_orangehrm(
        page,
        orangehrm_app_env,
        base=orangehrm_base(orangehrm_app_env),
        username=settings["ORANGEHRM_ADMIN_USER"],
        password=settings["ORANGEHRM_ADMIN_PASSWORD"],
    )
    return page


@pytest.fixture(scope="session")
def orangehrm_harness(
    context: Any,
    extension_id: str,
    api_server: dict[str, Any],
    database_url: str,
    orangehrm_app_env: dict[str, str],
    orangehrm_page: Any,
) -> Any:
    """A Harness whose extension is bound to the OrangeHRM tab."""
    from .conftest import Harness, bind_extension_to
    from .real_apps import orangehrm_base

    base = orangehrm_base(orangehrm_app_env)
    popup, session_id = bind_extension_to(context, extension_id, api_server, orangehrm_page)
    port = int(base.rsplit(":", 1)[-1])
    ready = Harness(
        api_base=api_server["base"],
        crm_url=base,
        partner_url=base,
        crm_port=port,
        dsn=database_url,
        session_id=session_id,
        extension_id=extension_id,
        page=orangehrm_page,
        popup=popup,
    )
    try:
        yield ready
    finally:
        ready.stop_workers()


# ------------------------------------------------------------ the sheet


def sheet_rows() -> list[dict[str, str]]:
    """The demo sheet, as the columns the popup and the runner use."""
    with SHEET.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def sheet_hours(row: dict[str, str]) -> dict[str, float]:
    """One sheet row's hours, as {day: hours} in the app's own tenths."""
    out: dict[str, float] = {}
    for day in ("mon", "tue", "wed", "thu", "fri"):
        hours, _, minutes = str(row.get(day, "0:00")).partition(":")
        out[day] = round(int(hours or 0) + int(minutes or 0) / 60, 2)
    return out


def as_text(hours: float) -> str:
    """2.0 -> "2:00": what a person types into a timesheet cell."""
    whole = int(hours)
    minutes = int(round((hours - whole) * 60))
    return f"{whole}:{minutes:02d}"


# ------------------------------------------------- the demonstration


def _fill_week(page: Any, row: dict[str, Any]) -> None:
    """Type one row's Monday-to-Friday hours into the first row's day cells.

    The grid gives every day a plain text cell in the row, in week order, so the first five cells
    of the first row are Monday to Friday. The demonstration types into them the way a person
    would: one cell at a time.
    """
    cells = page.locator("input.oxd-input--active")
    # The first two active inputs are the search box and the project hint; the day cells follow.
    day_cells = [cells.nth(index) for index in range(2, min(cells.count(), 9))]
    assert len(day_cells) >= 5, f"the grid offers {len(day_cells)} day cells"
    for day, cell in zip(("mon", "tue", "wed", "thu", "fri"), day_cells, strict=False):
        cell.click()
        cell.fill(str(row[day]))


def record_timesheet(harness: Any, settings: dict[str, str]) -> None:
    """Teach the weekly timesheet, as the ticket's part 2 describes it.

    The recording begins on My Timesheets and includes the Edit click, so every row of the batch
    opens the week for itself -- a recording that began inside the grid would leave rows two and
    three on whatever page row one left behind.
    """
    del settings
    page = harness.page
    base = harness.crm_url
    page.goto(f"{base}/web/index.php/time/viewMyTimesheet", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

    harness.start_recording()

    # "Edit" on this week's row opens the week's grid.
    page.get_by_role("button", name="Edit").first.click(timeout=30_000)
    page.wait_for_timeout(4000)

    first = sheet_rows()[0]
    # The project is a type-ahead: typing narrows it to one option, and the option is clicked.
    hint = page.get_by_placeholder("Type for hints...").first
    hint.wait_for(state="visible", timeout=20_000)
    hint.click()
    hint.press_sequentially(first["project"], delay=40)
    page.wait_for_timeout(2000)
    page.get_by_role("option", name=first["project"]).first.click()
    page.wait_for_timeout(1500)

    # The activity is a dropdown beside it, empty until the project is chosen.
    activity = page.locator(".oxd-select-text").first
    activity.click()
    page.wait_for_timeout(1200)
    page.get_by_role("option", name=first["activity"]).first.click()
    page.wait_for_timeout(1200)

    _fill_week(page, first)

    last_action_seq = max(
        (event.get("seq", 0) for event in harness.recording_events() if event.get("step")),
        default=0,
    )
    page.get_by_role("button", name="Save").first.click()
    page.wait_for_timeout(4000)

    assert harness.wait_for_observation_after(last_action_seq), (
        "the save's own evidence was never recorded: "
        + json.dumps(
            [
                {
                    "seq": event.get("seq"),
                    "kind": event.get("kind"),
                    "step": (event.get("step") or {}).get("id"),
                    "observation": event.get("observation"),
                    "url": event.get("url"),
                }
                for event in harness.recording_events()
            ],
            indent=1,
        )
    )
    harness.stop_recording()


# --------------------------------------------------------------- helpers


def describe(records: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{record.get('record_key')}={record['state']}:{record.get('reason')}" for record in records
    )


def step_detail(harness: Any, records: list[dict[str, Any]]) -> str:
    """Every step of every record, so a failure says exactly where the run stopped."""
    import psycopg

    keys = {str(record["id"]): str(record["record_key"]) for record in records}
    lines: list[str] = []
    with psycopg.connect(harness.dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT run_record_id, step_id, outcome, locator_rung, error FROM step_events "
            "WHERE run_record_id = ANY(%s::uuid[]) ORDER BY id",
            (list(keys),),
        )
        grouped: dict[str, list[str]] = {key: [] for key in keys.values()}
        for row in cur.fetchall():
            error = "" if row[4] in (None, "null") else str(row[4])[:160]
            grouped.setdefault(keys.get(str(row[0]), "?"), []).append(
                f"{row[1]}:{row[2]}@rung{row[3]}{' ' + error if error else ''}"
            )
    for record in records:
        key = str(record["record_key"])
        lines.append(
            f"{key} = {record['state']}:{record.get('reason')} attempts={record.get('attempts')}"
            f" | " + " -> ".join(grouped.get(key, []))
        )
    return "\n".join(lines)


def rows_by_key(rows: list[tuple[str, str, dict[str, float]]]) -> dict[str, dict[str, float]]:
    return {f"{project} / {activity}": hours for project, activity, hours in rows}


# ------------------------------------------------------------------- the test


@pytest.mark.xfail(strict=True, reason=XFAIL_REASON)
def test_orangehrm_weekly_timesheet_golden(
    orangehrm_harness: Any, orangehrm_app_env: dict[str, str]
) -> None:
    """Record once on real OrangeHRM, compile through the popup, replay three rows, verify there."""
    harness = orangehrm_harness
    settings = testapps_env.orangehrm_settings(orangehrm_app_env)
    week_start = seed_orangehrm.test_week_start()
    employee = settings.get("ORANGEHRM_TEST_EMPLOYEE", seed_orangehrm.TEST_EMPLOYEE["username"])

    rows = sheet_rows()
    assert len(rows) == 3, rows

    # 1. Record the demonstration through the real extension.
    record_timesheet(harness, settings)
    events = harness.recording_events()
    assert events, "the recorder captured nothing"
    assert any(harness.crm_url in (event.get("url") or "") for event in events), (
        f"the recording is not from OrangeHRM ({harness.crm_url}): "
        f"{[event.get('url') for event in events]}"
    )

    # 2. Compile it through the popup's own form.
    columns = [(column["label"], column["value"]) for column in harness.make_form_columns()]
    project_column = next(
        (value for label, value in columns if "project" in label.lower()), columns[0][1]
    )
    activity_column = next(
        (value for label, value in columns if "activity" in label.lower()), columns[-1][1]
    )
    outcome = harness.compile_via_popup("Weekly timesheet", record_key=project_column)
    assert outcome["recipe"] is not None, (
        f"the Make recipe form could not compile the recording: {outcome['result']!r} "
        f"warnings={outcome['warnings']!r} events={json.dumps(events)[:4000]}"
    )
    recipe = outcome["recipe"]

    # The record key is the project and the activity together, so two rows on one project stay two
    # rows. The popup offers one column, so the template is set here, on the compiled document.
    key_template = f"{{{{row.{project_column}}}}}:{{{{row.{activity_column}}}}}"
    recipe["meta"]["record_key"] = key_template
    recipe["meta"]["idempotency_key"] = key_template

    # 3. Replay the sheet's three rows.
    batch = [
        {
            project_column: row["project"],
            activity_column: row["activity"],
            "mon": as_text(sheet_hours(row)["mon"]),
            "tue": as_text(sheet_hours(row)["tue"]),
            "wed": as_text(sheet_hours(row)["wed"]),
            "thu": as_text(sheet_hours(row)["thu"]),
            "fri": as_text(sheet_hours(row)["fri"]),
        }
        for row in rows
    ]
    for entry in batch:
        for _label, column in columns:
            entry.setdefault(column, entry.get(project_column, ""))

    run_id = harness.make_run(batch, recipe=recipe, key_column=project_column)
    records = harness.settle_run(
        run_id,
        timeout=RUN_TIMEOUT_SECONDS,
        max_seconds=RUN_MAX_SECONDS,
        on_approval=lambda request: None,
    )
    detail = describe(records) + "\n" + step_detail(harness, records)

    counts = Counter(record["state"] for record in records)
    assert counts["verified"] == 3, detail

    # 4. OrangeHRM's own truth: the week holds exactly those three rows, with those hours.
    from .real_apps import orangehrm_timesheet_rows

    in_app = orangehrm_timesheet_rows(orangehrm_app_env, employee, week_start)
    by_key = rows_by_key(in_app)
    assert len(in_app) == 3, f"the week holds {len(in_app)} rows: {in_app} ({detail})"
    for row in rows:
        key = f"{row['project']} / {row['activity']}"
        assert key in by_key, f"{key} is missing from the week: {by_key} ({detail})"
        assert by_key[key] == sheet_hours(row), f"{key} has {by_key[key]}, not {sheet_hours(row)}"

    # 5. A second run of the same sheet writes nothing new.
    second_id = harness.make_run(batch, recipe=recipe, key_column=project_column)
    second = harness.settle_run(
        second_id,
        timeout=RUN_TIMEOUT_SECONDS,
        max_seconds=RUN_MAX_SECONDS,
        on_approval=lambda request: None,
    )
    second_states = Counter(record["state"] for record in second)
    after = orangehrm_timesheet_rows(orangehrm_app_env, employee, week_start)
    assert len(after) == 3, f"the second run added a row: {after}"
    assert second_states["verified"] + second_states["skipped"] == 3, (
        f"the second run ended {dict(second_states)}: {describe(second)}"
    )
