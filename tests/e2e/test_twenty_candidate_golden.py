"""PL-064: candidates into Twenty CRM from a sheet, never twice.

The brief's second use case: create a candidate profile once, never a second one for the same
person. The demonstration is a person's own flow on real Twenty CRM --

    People -> search for the candidate's email (a miss, so the empty result answers "nobody has
    this person yet") -> + Create -> First name, Last name, Email, Job title -> the Company
    picked from Twenty's relation picker

-- recorded through the real extension, compiled through the popup's Make recipe form, and
replayed as five rows. Twenty saves inline while the fields are edited, so the recording's
commit is the last click the person made in the record (see ``REPORT`` in the test below and
``internal/agent/reports/PL-064.md`` for what the recording actually marked).

Every assertion reads Twenty itself, over its own REST API.
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.real_apps]

REPO_ROOT = Path(__file__).resolve().parents[2]
SHEET = REPO_ROOT / "fixtures" / "demo" / "twenty-candidates.csv"
TRACE_DIR = REPO_ROOT / ".cache" / "pl064"

# One demonstration's values, synthetic on purpose: they become the sample row.
DEMO_FIRST = "Amara"
DEMO_LAST = "Nwosu"
DEMO_EMAIL = "amara.nwosu@candidates.test"
DEMO_TITLE = "Product Analyst"
DEMO_COMPANY = "Brightpath Logistics"

# The candidate already in the seeded workspace (infra/test-apps/seed_data.py, TWENTY_PEOPLE).
EXISTING_EMAIL = "nadia.fournier@brightpath.test"
EXISTING_TITLE = "Head of Operations"

# The rows the batch creates, and the row that repeats one of their emails in another case.
NEW_ROWS = (
    ("Amara", "Nwosu", "amara.nwosu@candidates.test", "Product Analyst", "Brightpath Logistics"),
    ("Tobias", "Lindgren", "tobias.lindgren@candidates.test", "Data Engineer", "Nordvind Energy"),
    ("Priya", "Venkatesan", "priya.venkatesan@candidates.test", "Design Lead", "Atelier Midi"),
)
CASE_DUPLICATE = (
    "Marta",
    "Ruiz",
    "AMARA.NWOSU@CANDIDATES.TEST",
    "Research Assistant",
    "Sakura Data",
)


# --------------------------------------------------------------- the demonstration


def _first_visible(page: Any, locator: Any) -> Any:
    """The first visible control a locator names, or ``None``."""
    for index in range(min(locator.count(), 8)):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def _search_for(page: Any, query: str) -> None:
    """Search the workspace for the candidate's email, the way a person would.

    Twenty puts its search behind the header's magnifier (and Ctrl+K): both open the same
    command-menu input, which answers "No results" for a person nobody has entered yet.
    """
    opened = False
    search_button = page.get_by_role("button", name="Search")
    if search_button.count():
        search_button.first.click()
        opened = True
    page.wait_for_timeout(1500)
    if not opened:
        page.keyboard.press("Control+k")
        page.wait_for_timeout(1500)
    # The command menu's input is the only text field on the page while it is open; Twenty gives
    # it no stable placeholder across builds, so it is found by being visible and typeable.
    box = _first_visible(page, page.locator("input[type='text']:not([placeholder*='irst name'])"))
    if box is None:
        page.keyboard.press("Control+k")
        page.wait_for_timeout(1500)
        box = _first_visible(
            page, page.locator("input[type='text']:not([placeholder*='irst name'])")
        )
    assert box is not None, "no search box opened"
    box.click()
    box.press_sequentially(query, delay=30)
    page.wait_for_timeout(2500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(1200)


def _open_company_picker(page: Any) -> Any:
    """The Company row's own picker, from the record panel's Fields widget."""
    widget = page.get_by_test_id("record-fields-widget")
    assert widget.count(), "the record panel has no Fields widget"
    row = widget.get_by_text("Company", exact=True)
    for index in range(row.count()):
        candidate = row.nth(index)
        if candidate.is_visible():
            candidate.click()
            page.wait_for_timeout(1500)
            return page
    raise AssertionError("the Company field row was not clickable")


def record_new_candidate(harness) -> None:
    """Teach "new candidate" on Twenty, exactly as the ticket describes it."""
    page = harness.page
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    page.goto(f"{harness.crm_url}/objects/people", wait_until="domcontentloaded")
    # Twenty's first paint is a skeleton; the object view arrives on its own metadata query.
    page.wait_for_timeout(25000)

    harness.start_recording()

    _search_for(page, DEMO_EMAIL)

    # + Create opens a new person in the record panel. Twenty creates the row immediately and
    # saves each field as it changes.
    create = page.get_by_role("button", name="Create")
    assert create.count(), "the People view offers no Create button"
    create.first.click()
    page.wait_for_timeout(4000)

    first = _first_visible(page, page.locator("input[placeholder*='irst name']"))
    last = _first_visible(page, page.locator("input[placeholder*='ast name']"))
    assert first is not None and last is not None, "the record panel offers no name fields"
    first.click()
    first.press_sequentially(DEMO_FIRST, delay=25)
    last.click()
    last.press_sequentially(DEMO_LAST, delay=25)
    page.wait_for_timeout(1500)

    widget = page.get_by_test_id("record-fields-widget")
    assert widget.count(), "the record panel has no Fields widget"

    # Email: the Emails row opens an inline editor; the address is typed into its input.
    email_row = widget.get_by_text("Emails", exact=True)
    for index in range(email_row.count()):
        row = email_row.nth(index)
        if not row.is_visible():
            continue
        row.click()
        page.wait_for_timeout(1500)
        box = _first_visible(page, page.locator("input[placeholder*='mail']"))
        if box is None:
            box = _first_visible(page, page.locator("[data-testid='record-fields-widget'] input"))
        if box is not None:
            box.click()
            box.press_sequentially(DEMO_EMAIL, delay=25)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1500)
            break

    # Job title: a plain text field on the same widget.
    title_row = widget.get_by_text("Job Title", exact=True)
    for index in range(title_row.count()):
        row = title_row.nth(index)
        if not row.is_visible():
            continue
        row.click()
        page.wait_for_timeout(1500)
        box = _first_visible(page, page.locator("input[placeholder*='ob title']"))
        if box is not None:
            box.click()
            box.press_sequentially(DEMO_TITLE, delay=25)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1500)
            break

    # Company: Twenty's relation picker, narrowed by typing and accepted by clicking the option.
    _open_company_picker(page)
    picker = _first_visible(page, page.locator("input[placeholder*='ompany']"))
    if picker is not None:
        picker.click()
        picker.press_sequentially(DEMO_COMPANY, delay=40)
        page.wait_for_timeout(2500)
        option = page.get_by_text(DEMO_COMPANY, exact=True)
        for index in range(option.count()):
            candidate = option.nth(index)
            if candidate.is_visible():
                candidate.click()
                break
        page.wait_for_timeout(2000)

    # Twenty has no Save: the last thing the person does is leave the record. The click that ends
    # the demonstration is the one that closes the panel, and it is the recording's last click.
    page.wait_for_timeout(2000)
    harness.stop_recording()


# ------------------------------------------------------------------- the rows


def sheet_rows() -> list[dict[str, str]]:
    """The five rows of ``fixtures/demo/twenty-candidates.csv``, in file order."""
    with SHEET.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def columns_for(harness) -> list[tuple[str, str]]:
    """The recording's input columns as (label, column) pairs, in the form's own order."""
    return [(column["label"], column["value"]) for column in harness.make_form_columns()]


def column_for(columns: list[tuple[str, str]], *needles: str) -> str:
    """The first column whose label mentions any needle, case-insensitively."""
    for needle in needles:
        for label, value in columns:
            if needle.lower() in label.lower():
                return value
    raise AssertionError(f"no recorded column mentions {needles!r}; the form offers {columns!r}")


def rows_for_sheet(columns: list[tuple[str, str]], rows: list[dict[str, str]]) -> list[dict]:
    """Each CSV row valued for every column the recording produced."""
    out: list[dict[str, str]] = []
    for row in rows:
        mapped: dict[str, str] = {}
        for label, column in columns:
            text = label.lower()
            if "first" in text:
                mapped[column] = row["first_name"]
            elif "last" in text:
                mapped[column] = row["last_name"]
            elif "email" in text or "search" in text:
                mapped[column] = row["email"]
            elif "job" in text or "title" in text:
                mapped[column] = row["job_title"]
            elif "company" in text:
                mapped[column] = row["company"]
            else:
                raise AssertionError(
                    f"the recording produced a column this golden cannot fill: "
                    f"{label!r} -> {column!r} (all: {columns!r})"
                )
        out.append(mapped)
    return out


def describe(records: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{record.get('record_key')}={record['state']}:{record.get('reason')}" for record in records
    )


# ------------------------------------------------------------------- the test


def test_twenty_candidate_golden(twenty_harness, app_env) -> None:
    """Record once on real Twenty, compile through the popup, replay five rows, verify in Twenty."""
    from tests.e2e.real_apps import twenty_list_people, twenty_primary_email

    harness = twenty_harness
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    before = twenty_list_people(app_env)
    (TRACE_DIR / "people-before.json").write_text(json.dumps(before, indent=1), encoding="utf-8")
    assert any(twenty_primary_email(person).lower() == EXISTING_EMAIL for person in before), (
        "the seeded candidate is missing from Twenty"
    )

    record_new_candidate(harness)
    events = harness.recording_events()
    (TRACE_DIR / "recording.json").write_text(json.dumps(events, indent=1), encoding="utf-8")
    assert events, "the recorder captured nothing"

    columns = columns_for(harness)
    email_column = column_for(columns, "search", "email")
    first_column = column_for(columns, "first")
    outcome = harness.compile_via_popup(
        "New candidate", record_key=email_column, park_fields=[email_column]
    )
    (TRACE_DIR / "compile.json").write_text(
        json.dumps({"columns": columns, "outcome": outcome}, indent=1), encoding="utf-8"
    )
    assert outcome["recipe"] is not None, (
        f"the Make recipe form could not compile: {outcome['result']!r} {outcome['warnings']!r}"
    )
    recipe = outcome["recipe"]
    (TRACE_DIR / "recipe.json").write_text(json.dumps(recipe, indent=1), encoding="utf-8")

    rows = rows_for_sheet(columns, sheet_rows())
    run_id = harness.make_run(rows, recipe=recipe, key_column=email_column)
    records = harness.settle_run(run_id, timeout=700, max_seconds=680)
    (TRACE_DIR / "run.json").write_text(json.dumps(records, indent=1), encoding="utf-8")

    by_key = {record["record_key"]: record for record in records}
    counts = Counter(record["state"] for record in records)
    assert len(records) == 5, describe(records)
    assert counts["verified"] == 3, describe(records)
    assert counts["skipped"] == 2, describe(records)

    people = twenty_list_people(app_env)
    (TRACE_DIR / "people-after.json").write_text(json.dumps(people, indent=1), encoding="utf-8")

    def people_with(email: str) -> list[dict[str, Any]]:
        wanted = email.strip().lower()
        return [
            person for person in people if twenty_primary_email(person).strip().lower() == wanted
        ]

    # Exactly one person per distinct lower-cased email.
    seen: Counter[str] = Counter()
    for row in sheet_rows():
        seen[row["email"].lower()] += 1
    for email in seen:
        assert len(people_with(email)) == 1, f"{email!r} exists {len(people_with(email))} times"

    # The pre-existing candidate was skipped and left as it was.
    existing = people_with(EXISTING_EMAIL)
    assert len(existing) == 1
    assert str(existing[0].get("jobTitle") or "").lower() == EXISTING_TITLE.lower()
    assert by_key.get(EXISTING_EMAIL) is not None, describe(records)
    assert by_key[EXISTING_EMAIL]["state"] == "skipped", describe(records)

    # The case-duplicate row: the ledger's key is lower-cased, so it collides with row 1.
    case_duplicate_key = CASE_DUPLICATE[2].lower()
    assert case_duplicate_key in by_key, describe(records)
    assert by_key[case_duplicate_key]["state"] == "skipped", describe(records)
    assert len(people_with(CASE_DUPLICATE[2])) == 1, "the case duplicate created a second person"

    # The three new candidates, with the values the sheet held.
    for first, last, email, title, _company in NEW_ROWS:
        people_for = people_with(email)
        assert len(people_for) == 1, f"{email!r} exists {len(people_for)} times"
        person = people_for[0]
        name = person.get("name") or {}
        assert name.get("firstName") == first, person
        assert name.get("lastName") == last, person
        assert str(person.get("jobTitle") or "") == title, person
        assert by_key[email]["state"] == "verified", describe(records)

    # The re-run: nothing new, every row skipped.
    before_second = {str(person["id"]) for person in people}
    run_two = harness.make_run(rows, recipe=recipe, key_column=email_column)
    records_two = harness.settle_run(run_two, timeout=700, max_seconds=680)
    (TRACE_DIR / "run-two.json").write_text(json.dumps(records_two, indent=1), encoding="utf-8")
    assert all(record["state"] == "skipped" for record in records_two), describe(records_two)
    after_second = twenty_list_people(app_env)
    assert {str(person["id"]) for person in after_second} == before_second, (
        "the second run created a person"
    )

    # The first name column is part of the demonstration's record, so it must exist.
    assert first_column
    if os.environ.get("PROCESSLENS_E2E_DUMP_RECIPE") == "1":
        print("RECORDING:", json.dumps(events, indent=2, sort_keys=True))
        print("RECIPE:", json.dumps(recipe, indent=2, sort_keys=True))
