"""PL-127: the owner's Events enrollment of 2026-10-28, which could not be saved as taught.

The recording is rebuilt from the task the panel saved (`internal/agent/reviews/events-fail-1`):
the events list, a click on the event's card, Attendees, New, then the attendee's name, email and
phone -- and no Save, because Odoo's Save is an unlabeled cloud icon. The shape refused it, the
panel fell back to its old step list, and that task replayed the card click, which landed on
another element, and marked "Click New" as the save.
"""

from __future__ import annotations

import json
from typing import Any

from packages.compiler.shape import read_shape
from services.api.compile import CompileRequest, _compile_shape

ODOO = "http://localhost:8069"
EVENTS = f"{ODOO}/odoo/events"
ATTENDEES = f"{ODOO}/odoo/events/1/attendees"
NEW = f"{ATTENDEES}/new"


def _event(seq: int, kind: str, url: str, **step: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"seq": seq, "kind": kind, "url": url, "at": "2026-10-28T13:40:00Z"}
    if kind == "navigate":
        event["step"] = None
        return event
    page_set = step.pop("page_set", None)
    event["step"] = {"id": f"{kind}-{seq}", "action": kind, **step}
    if page_set is not None:
        event["page_set"] = page_set
        event["keystrokes"] = 0 if page_set else 6
    return event


def _fill(seq: int, label: str, selector: str, value: str) -> dict[str, Any]:
    return _event(
        seq,
        "fill",
        NEW,
        name=f"Fill {label}",
        targets=[
            {"by": "role", "role": "textbox", "name": label},
            {"by": "label", "text": label},
            {"by": "css", "selector": selector},
        ],
        value={"kind": "constant", "value": value},
        page_set=False,
    )


def owners_enrollment() -> list[dict[str, Any]]:
    return [
        _event(1, "navigate", EVENTS),
        _event(
            2,
            "click",
            EVENTS,
            name="Click demo dayyyyyyy",
            targets=[
                {"by": "role", "role": "generic", "name": "demo dayyyyyyy"},
                {"by": "css", "selector": "main > div > div:nth-of-type(1) > span"},
                {"by": "text_fuzzy", "value": "demo dayyyyyyy"},
            ],
        ),
        _event(
            3,
            "click",
            f"{EVENTS}/1",
            name="Click Attendees",
            targets=[
                {"by": "role", "role": "button", "name": "Attendees"},
                {"by": "css", "selector": "button.btn.oe_stat_button.btn-outline-secondary"},
            ],
        ),
        _event(
            4,
            "click",
            ATTENDEES,
            name="Click New",
            targets=[
                {"by": "role", "role": "button", "name": "New"},
                {"by": "css", "selector": "button.btn.o-kanban-button-new"},
            ],
        ),
        _event(
            5,
            "fill",
            NEW,
            name="Fill Event",
            targets=[{"by": "label", "text": "Event"}],
            value={"kind": "constant", "value": "demo dayyyyyyy"},
            page_set=True,
        ),
        _fill(6, "Attendee Name", "#name_0", "ben baller"),
        _fill(7, "Email", "#email_0", "benbenn@gmauk.com"),
        _fill(8, "Phone", "#phone_0", "123456799"),
    ]


def _compile(recording: list[dict[str, Any]], save: str | None = None) -> dict[str, Any]:
    request = CompileRequest(
        recording=recording,
        name="attenddee task",
        when_to_use="attenddee task",
        use_shape=True,
        save=save,
    )
    return _compile_shape(request).recipe


def test_stopping_on_the_new_attendee_is_a_create_saved_by_leaving() -> None:
    shape = read_shape(owners_enrollment())
    assert shape.problem is None
    assert (shape.kind, shape.save) == ("create", "leave")
    assert shape.item_column == "email"
    assert [change.label for change in shape.changes] == ["Attendee Name", "Email", "Phone"]


def test_the_task_starts_on_the_attendee_list_and_never_replays_the_card_click() -> None:
    recipe = _compile(owners_enrollment(), save="leave")
    text = json.dumps(recipe)
    assert "demo dayyyyyyy" not in text
    assert "oe_stat_button" not in text
    first = recipe["steps"][0]
    assert first["action"] == "navigate" and first["url"].rstrip("/") == ATTENDEES
    # "Click New" opens the form; it is never the save.
    new = next(
        step
        for step in recipe["steps"]
        if step.get("action") == "click" and "New" in json.dumps(step.get("targets"))
    )
    assert new.get("commit") is not True
    assert any(step.get("commit") is True for step in _all_steps(recipe["steps"]))


def test_the_unlabeled_cloud_save_is_kept_as_the_save_when_it_was_pressed() -> None:
    recording = owners_enrollment() + [
        _event(
            9,
            "click",
            NEW,
            name="Click Save manually",
            targets=[
                {"by": "role", "role": "button", "name": "Save manually"},
                {"by": "css", "selector": "button.o_form_button_save.btn.btn-light"},
            ],
        )
    ]
    shape = read_shape(recording)
    assert (shape.kind, shape.save) == ("create", "button")
    recipe = _compile(recording, save="button")
    saves = [step for step in _all_steps(recipe["steps"]) if step.get("commit") is True]
    assert len(saves) == 1 and "Save manually" in json.dumps(saves[0]["targets"])


def _all_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in steps:
        out.append(step)
        for key in ("then", "else", "steps"):
            nested = step.get(key)
            if isinstance(nested, list):
                out.extend(_all_steps(nested))
    return out
