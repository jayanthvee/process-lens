"""PL-123 Events: the owner's enrollment fixture compiles to a task that can be Done.

The event is opened by the sheet's Event column, the Attendees count is not part of the button's
name, the Questions tab is not a save, and the attendee form's in-place save gets an explicit save
click with the email read back on the saved page. The email is the key, and a create looks for an
attendee already registered before adding one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.compiler import CompileOptions, compile_recording

FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "owner" / "events_enroll.json"
)
LIST = "http://localhost:8069/odoo/events"
EVENT = "http://localhost:8069/odoo/events/1"
ATTENDEES = "http://localhost:8069/odoo/events/1/attendees"
NEW = "http://localhost:8069/odoo/events/1/attendees/new"
SAVED = "http://localhost:8069/odoo/events/1/attendees/1"

CARD = [{"by": "role", "role": "main", "name": "demo dayyyyyyyMy Company"}]
ATTENDEES_BUTTON = [
    {"by": "role", "role": "button", "name": "0Attendees"},
    {"by": "css", "selector": "button.btn.oe_stat_button.btn-outline-secondary"},
]
NEW_BUTTON = [
    {"by": "role", "role": "button", "name": "New"},
    {"by": "css", "selector": "button.btn.o-kanban-button-new"},
]
NAME = [
    {"by": "role", "role": "textbox", "name": "Attendee Name"},
    {"by": "label", "text": "Attendee Name"},
]
EMAIL = [
    {"by": "role", "role": "textbox", "name": "Email"},
    {"by": "label", "text": "Email"},
]
PHONE = [
    {"by": "role", "role": "textbox", "name": "Phone"},
    {"by": "label", "text": "Phone"},
]
QUESTIONS = [
    {"by": "role", "role": "list", "name": "Questions"},
    {"by": "css", "selector": "ul.nav.nav-tabs.flex-row"},
]

# The event form's own values, as the panel listed them: name, both dates, organizer, responsible,
# venue, limit registrations "on".
FORM_LOAD = [
    ("name", [{"by": "role", "role": "textbox", "name": "Event Name"}], "demo dayyyyyyy"),
    ("start", [{"by": "role", "role": "textbox", "name": "Start Date"}], "10/28/2026 10:00:00"),
    ("end", [{"by": "role", "role": "textbox", "name": "End Date"}], "10/28/2026 11:00:00"),
    ("organizer", [{"by": "role", "role": "textbox", "name": "Organizer"}], "Mitchell Admin"),
    ("responsible", [{"by": "role", "role": "textbox", "name": "Responsible"}], "Mitchell Admin"),
    ("venue", [{"by": "role", "role": "textbox", "name": "Venue"}], "My Company"),
    ("limit", [{"by": "role", "role": "checkbox", "name": "Limit Registrations"}], "on"),
]

SAMPLE = {
    "attendee_name": "Ava Patel",
    "email": "ava.patel@pilot-events.test",
    "phone": "555-0101",
    "event": "demo dayyyyyyy",
}


def _event(
    seq: int, kind: str, step: dict[str, Any] | None, url: str, **extra: Any
) -> dict[str, Any]:
    event: dict[str, Any] = {"seq": seq, "kind": kind, "url": url, "step": step}
    event.update(extra)
    return event


def _click(step_id: str, targets: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return {"id": step_id, "action": "click", "targets": targets, "name": name}


def _fill(step_id: str, targets: list[dict[str, Any]], value: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": step_id,
        "action": "fill",
        "targets": targets,
        "value": {"kind": "constant", "value": value},
        **extra,
    }


def owner_recording() -> list[dict[str, Any]]:
    events = [_event(1, "navigate", None, LIST)]
    events.append(
        _event(2, "click", _click("click-1", CARD, "Click demo dayyyyyyyMy Company"), LIST)
    )
    seq = 3
    for index, (_name, targets, value) in enumerate(FORM_LOAD):
        events.append(
            _event(
                seq,
                "fill",
                _fill(f"page-set-{index + 1}", targets, value),
                EVENT,
                page_set=True,
                keystrokes=0,
            )
        )
        seq += 1
    events.append(
        _event(seq, "click", _click("click-2", ATTENDEES_BUTTON, "Click 0Attendees"), EVENT)
    )
    seq += 1
    events.append(_event(seq, "click", _click("click-3", NEW_BUTTON, "Click New"), ATTENDEES))
    seq += 1
    for step_id, targets, value in (
        ("fill-1", NAME, "Ava Patel"),
        ("fill-2", EMAIL, "ava.patel@pilot-events.test"),
        ("fill-3", PHONE, "555-0101"),
    ):
        events.append(_event(seq, "fill", _fill(step_id, targets, value), NEW))
        seq += 1
    events.append(
        _event(
            seq,
            "click",
            {
                "id": "click-4",
                "action": "click",
                "targets": QUESTIONS,
                "name": "Click Questions",
                "assert_after": {"text_count_increases": "Ava Patel", "timeout_ms": 8000},
            },
            SAVED,
        )
    )
    return events


def _options(**overrides: Any) -> CompileOptions:
    values: dict[str, Any] = {
        "name": "events test",
        "when_to_use": "events test",
        "sample_row": dict(SAMPLE),
        "record_key_column": "email",
        "commit_step_ids": [],
        "save_by_itself": True,
    }
    values.update(overrides)
    return CompileOptions(**values)  # type: ignore[arg-type]


def _commits(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [step for step in steps if step.get("commit") is True]


def test_the_events_fixture_compiles_to_the_owners_six_steps() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["recipe"]["steps"][-1]["id"] == "click-4"

    result = compile_recording(owner_recording(), _options())
    steps = result.recipe["steps"]
    commits = _commits(steps)

    # 1. open the events list; 2. open the event by the row's value; 3. open its attendees;
    # 4. New; 5. the three fills; 6. the commit.
    assert steps[0]["action"] == "navigate"
    assert steps[0]["url"] == LIST
    event_click = next(
        step
        for step in steps
        if step["action"] == "click"
        and step.get("targets") == [{"by": "text_fuzzy", "value": "{{row.event}}"}]
    )
    assert event_click["intent"] == "open the event found by {{row.event}}"
    attendees_click = next(
        step
        for step in steps
        if step["action"] == "click"
        and any(rung.get("name") == "Attendees" for rung in step["targets"])
    )
    assert attendees_click["intent"] == "open the attendees of the event"
    assert any(
        step["action"] == "click"
        and step["targets"][0] == {"by": "role", "role": "button", "name": "New"}
        for step in steps
    ), steps
    new_at = next(
        index
        for index, step in enumerate(steps)
        if step["action"] == "click"
        and step["targets"][0] == {"by": "role", "role": "button", "name": "New"}
    )
    entered = [
        step["value"]["column"]
        for step in steps[new_at:]
        if step["action"] == "fill" and step["value"]["kind"] == "input"
    ]
    assert entered == ["attendee_name", "email", "phone"], entered
    assert len(commits) == 1, steps
    assert not any(step.get("id") == "end-record-unchecked" for step in steps), steps


def test_no_step_targets_a_glued_name_or_a_changing_count() -> None:
    result = compile_recording(owner_recording(), _options())
    rendered = json.dumps(result.recipe, sort_keys=True)
    assert "demo dayyyyyyyMy Company" not in rendered, rendered
    assert "0Attendees" not in rendered, rendered
    assert "Questions" not in rendered, rendered


def test_the_in_place_save_is_explicit_and_reads_the_email_back() -> None:
    result = compile_recording(owner_recording(), _options())
    commit = _commits(result.recipe["steps"])[0]

    check = commit["assert_after"]
    assert "url_matches" in check
    assert check["value_equals"]["value"] == {"kind": "input", "column": "email"}
    assert commit["intent"] == "save the record"
    assert any(rung.get("name") == "Save manually" for rung in commit["targets"])
    assert result.recipe["meta"]["record_key"] == "{{row.email | lower}}"


def test_the_sheet_event_column_is_a_task_field_and_opens_the_event() -> None:
    result = compile_recording(owner_recording(), _options())
    fields = result.recipe["meta"]["input_schema"]["fields"]
    assert fields["event"]["example"] == "demo dayyyyyyy"
    assert "{{row.event}}" in json.dumps(result.recipe["steps"])


def test_a_create_looks_for_the_email_before_new() -> None:
    result = compile_recording(owner_recording(), _options())
    steps = result.recipe["steps"]
    new_index = next(
        index
        for index, step in enumerate(steps)
        if step["action"] == "click"
        and step["targets"][0] == {"by": "role", "role": "button", "name": "New"}
    )
    before = steps[:new_index]
    assert any(
        step["action"] == "fill" and step["value"] == {"kind": "input", "column": "email"}
        for step in before
    ), before
    assert any(
        step["action"] == "branch"
        and step["when"]["element_present"]["targets"]
        == [{"by": "text_fuzzy", "value": "{{row.email}}"}]
        for step in before
    ), before


BREADCRUMB = [{"by": "role", "role": "link", "name": "Attendees"}]


def _leave_recording() -> list[dict[str, Any]]:
    """The attendee form left by the breadcrumb: the app saves and the list shows the row."""
    events = [
        _event(1, "navigate", None, LIST),
        _event(2, "click", _click("click-1", CARD, "Click demo dayyyyyyyMy Company"), LIST),
        _event(3, "click", _click("click-2", ATTENDEES_BUTTON, "Click 0Attendees"), EVENT),
        _event(4, "click", _click("click-3", NEW_BUTTON, "Click New"), ATTENDEES),
    ]
    seq = 5
    for step_id, targets, value in (
        ("fill-1", NAME, "Ava Patel"),
        ("fill-2", EMAIL, "ava.patel@pilot-events.test"),
        ("fill-3", PHONE, "555-0101"),
    ):
        events.append(_event(seq, "fill", _fill(step_id, targets, value), NEW))
        seq += 1
    events.append(_event(seq, "click", _click("click-4", BREADCRUMB, "Click Attendees"), NEW))
    seq += 1
    events.append({"seq": seq, "kind": "navigate", "url": ATTENDEES, "step": None})
    return events


def test_leaving_by_the_breadcrumb_saves_and_the_list_reads_the_email_back() -> None:
    result = compile_recording(_leave_recording(), _options())
    commits = _commits(result.recipe["steps"])

    assert len(commits) == 1, result.recipe["steps"]
    commit = commits[0]
    assert commit["targets"] == BREADCRUMB
    assert "element_present" in commit["assert_after"]
    assert commit["assert_after"]["element_present"]["targets"] == [
        {"by": "text_fuzzy", "value": "{{row.email}}"}
    ]
    assert "url_matches" in commit["assert_after"]
