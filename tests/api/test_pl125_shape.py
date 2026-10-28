"""PL-125: the task is read from its shape, not replayed from the recording.

The two recordings here are the owner's price attempts of 2026-10-29, rebuilt from the panel and
the saved recipes (`internal/agent/reviews/prices-fail-1`, `prices-fail-2`), plus an Events
enrollment taught the same messy way. Each carries what a real person's recording carries: values
Odoo filled in by itself, a stray menu click, a click on a stock button, a typo fixed, and (in the
second) another site's run recorded in the middle.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from packages.compiler.shape import NO_CHANGE, NO_ITEM, read_shape
from services.api.compile import CompileRequest, _compile_shape

ODOO = "http://localhost:8069"
LIST = f"{ODOO}/odoo/products"
ITEM = f"{ODOO}/odoo/products/1"
GOODLABS = "https://goodlabs.com/login"

SEARCH = [
    {"by": "role", "role": "searchbox", "name": "Search..."},
    {"by": "css", "selector": "input.o_searchview_input.o_input.d-print-none"},
]
PRICE = [{"by": "label", "text": "Sales Price"}]
BACK = [{"by": "css", "selector": "button.o_back_button.btn.btn-link"}]

_seq = 0


def _event(
    kind: str,
    url: str,
    targets: list[dict[str, Any]] | None = None,
    *,
    name: str | None = None,
    value: Any = None,
    page_set: bool | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    global _seq
    _seq += 1
    event: dict[str, Any] = {"seq": _seq, "kind": kind, "url": url, "at": "2026-10-29T05:36:00Z"}
    if kind == "navigate":
        event["step"] = None
        return event
    step: dict[str, Any] = {"id": f"{kind}-{_seq}", "action": kind, "targets": targets or []}
    if name:
        step["name"] = name
    if value is not None:
        step["value"] = {"kind": "constant", "value": value}
    if key:
        step["key"] = key
    event["step"] = step
    if page_set is not None:
        event["page_set"] = page_set
        event["keystrokes"] = 0 if page_set else 4
    return event


def _odoo_fills(price: str) -> list[dict[str, Any]]:
    """What Odoo puts in the product form by itself when it opens."""
    return [
        _event(
            "fill",
            ITEM,
            [{"by": "label", "text": "Product"}],
            name="Fill Product",
            value="Oak Standing Desk",
            page_set=True,
        ),
        _event(
            "fill",
            ITEM,
            [{"by": "label", "text": "Goods"}],
            name="Fill Goods",
            value="on",
            page_set=True,
        ),
        _event("fill", ITEM, PRICE, name="Fill Sales Price", value=price, page_set=True),
        _event(
            "fill",
            ITEM,
            [{"by": "label", "text": "Cost"}],
            name="Fill Cost",
            value="455.00",
            page_set=True,
        ),
        _event(
            "fill",
            ITEM,
            [{"by": "label", "text": "Reference"}],
            name="Fill Reference",
            value="PL-DESK-1001",
            page_set=True,
        ),
    ]


def searched_and_clicked_a_line() -> list[dict[str, Any]]:
    """Attempt 1: a stray menu, search, open by the "On hand" line, type 3200, leave by back."""
    return [
        _event("navigate", LIST),
        _event(
            "click",
            LIST,
            [{"by": "role", "role": "button", "name": "All Apps"}],
            name="Click All Apps",
        ),
        _event(
            "fill", LIST, SEARCH, name="Fill Search...", value="Oak Standing Desk", page_set=False
        ),
        _event("press", LIST, SEARCH, name="Press Enter", key="Enter"),
        _event(
            "click",
            LIST,
            [{"by": "role", "role": "generic", "name": "On hand: 18.00"}],
            name="Click On hand: 18.00",
        ),
        *_odoo_fills("400.00"),
        _event("fill", ITEM, PRICE, name="Fill Sales Price", value="3200", page_set=False),
        _event("click", ITEM, BACK, name="Click button"),
    ]


def clicked_the_card_with_another_run_inside() -> list[dict[str, Any]]:
    """Attempt 2: open the card, type 2000, a stock-button click, a Goodlabs run, leave by back."""
    return [
        _event("navigate", LIST),
        _event(
            "click",
            LIST,
            [{"by": "role", "role": "generic", "name": "Oak Standing Desk [PL-DESK-1001]"}],
            name="Click Oak Standing Desk [PL-DESK-1001]",
        ),
        *_odoo_fills("3200.00"),
        _event("fill", ITEM, PRICE, name="Fill Sales Price", value="2000", page_set=False),
        _event(
            "fill",
            GOODLABS,
            [{"by": "label", "text": "Email or phone number"}],
            name="Fill Email or phone number",
            value="owner@pilot.test",
            page_set=False,
        ),
        _event(
            "click",
            GOODLABS,
            [{"by": "role", "role": "button", "name": "Continue"}],
            name="Click Continue",
        ),
        _event(
            "click",
            ITEM,
            [{"by": "role", "role": "generic", "name": "18.00On Hand18.00Forecasted0"}],
            name="Click 18.00On Hand18.00Forecasted0",
        ),
        _event("click", ITEM, BACK, name="Click button"),
    ]


EVENT_LIST = f"{ODOO}/odoo/event/5/registrations"
EVENT_NEW = f"{EVENT_LIST}/new"


def enrolled_with_a_typo() -> list[dict[str, Any]]:
    """Events: open the event's attendees, New, name, a mistyped email fixed, phone, breadcrumb."""
    return [
        _event("navigate", f"{ODOO}/odoo/event"),
        _event(
            "click",
            f"{ODOO}/odoo/event",
            [{"by": "role", "role": "generic", "name": "demo dayyyyyyy"}],
            name="Click demo dayyyyyyy",
        ),
        _event(
            "click",
            f"{ODOO}/odoo/event/5",
            [{"by": "role", "role": "button", "name": "Attendees"}],
            name="Click Attendees",
        ),
        _event(
            "click", EVENT_LIST, [{"by": "role", "role": "button", "name": "New"}], name="Click New"
        ),
        _event(
            "fill",
            EVENT_NEW,
            [{"by": "label", "text": "Event"}],
            name="Fill Event",
            value="demo dayyyyyyy",
            page_set=True,
        ),
        _event(
            "fill",
            EVENT_NEW,
            [{"by": "label", "text": "Name"}],
            name="Fill Name",
            value="Ava Patel",
            page_set=False,
        ),
        _event(
            "fill",
            EVENT_NEW,
            [{"by": "label", "text": "Email"}],
            name="Fill Email",
            value="ava@pilot-events.tst",
            page_set=False,
        ),
        _event(
            "fill",
            EVENT_NEW,
            [{"by": "label", "text": "Email"}],
            name="Fill Email",
            value="ava@pilot-events.test",
            page_set=False,
        ),
        _event(
            "fill",
            EVENT_NEW,
            [{"by": "label", "text": "Phone"}],
            name="Fill Phone",
            value="555-0101",
            page_set=False,
        ),
        _event(
            "click",
            EVENT_NEW,
            [{"by": "role", "role": "link", "name": "Attendees"}],
            name="Click Attendees",
        ),
    ]


def _compile(recording: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    request = CompileRequest(
        recording=recording,
        name="Price update",
        when_to_use="Price update",
        use_shape=True,
        **extra,
    )
    return _compile_shape(request).recipe


def _steps(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    return recipe["steps"]


# ------------------------------------------------------------------ the shape


def test_attempt_one_is_an_update_of_the_searched_item_with_the_typed_price() -> None:
    shape = read_shape(searched_and_clicked_a_line())

    assert shape.kind == "update"
    assert shape.item_text == "Oak Standing Desk"
    assert shape.item_column == "product"
    assert [(change.label, change.value, change.was) for change in shape.changes] == [
        ("Sales Price", "3200", "400.00")
    ]
    assert shape.save == "leave"


def test_attempt_two_finds_the_item_in_the_clicked_card_and_ignores_the_other_site() -> None:
    shape = read_shape(clicked_the_card_with_another_run_inside())

    assert shape.kind == "update"
    assert shape.item_text == "Oak Standing Desk"
    assert [change.value for change in shape.changes] == ["2000"]
    assert shape.save == "leave"


def test_the_events_enrollment_is_a_create_keyed_by_the_fixed_email() -> None:
    shape = read_shape(enrolled_with_a_typo())

    assert shape.kind == "create"
    assert [(change.label, change.value) for change in shape.changes] == [
        ("Name", "Ava Patel"),
        ("Email", "ava@pilot-events.test"),
        ("Phone", "555-0101"),
    ]
    assert shape.item_column == "email"
    assert shape.list_url == EVENT_LIST


# ------------------------------------------------------------------ the compiled task


def test_attempt_one_compiles_to_search_open_change_and_checked_leave() -> None:
    recipe = _compile(searched_and_clicked_a_line())
    text = json.dumps(recipe)

    assert "All Apps" not in text and "On hand" not in text
    assert recipe["meta"]["idempotency_key"] == "{{row.product | lower}}:{{row.sales_price}}"
    fills = [step for step in _steps(recipe) if step["action"] == "fill"]
    assert [step["value"] for step in fills] == [
        {"kind": "input", "column": "product"},
        {"kind": "input", "column": "sales_price"},
    ]
    opens = [
        step
        for step in _steps(recipe)
        if step["action"] == "click" and "{{row.product}}" in json.dumps(step["targets"])
    ]
    # The open, and the compiler's own re-open that reads the saved price back.
    assert len(opens) == 2 and opens[0].get("exclude_search_box") is True
    assert opens[1].get("commit") is True
    # A search that finds nothing parks the row instead of changing whatever is open.
    branches = [step for step in _steps(recipe) if step["action"] == "branch"]
    assert len(branches) == 1 and "{{row.product" in json.dumps(branches[0]["when"])
    # The leave is the list's address, never the recorded back arrow, which a wide window lacks.
    assert "o_back_button" not in text
    # The save is checked: some step carries a read-back of the price.
    assert "assert_after" in text


def test_attempt_two_compiles_without_the_other_site_or_the_stray_click() -> None:
    recipe = _compile(clicked_the_card_with_another_run_inside())
    text = json.dumps(recipe)

    assert "goodlabs" not in text.lower()
    assert "Continue" not in text and "Forecasted" not in text
    assert "PL-DESK-1001" not in text
    assert recipe["meta"]["allowed_origins"] == [ODOO]
    assert set(recipe["meta"]["input_schema"]["fields"]) == {"product", "sales_price"}
    assert recipe["meta"]["idempotency_key"] == "{{row.product | lower}}:{{row.sales_price}}"
    assert "assert_after" in text


def test_the_events_enrollment_compiles_to_new_fill_and_save_on_the_attendee_list() -> None:
    recipe = _compile(enrolled_with_a_typo())
    text = json.dumps(recipe)

    assert "demo dayyyyyyy" not in text
    assert "pilot-events.tst" not in text
    assert set(recipe["meta"]["input_schema"]["fields"]) == {"name", "email", "phone"}
    assert recipe["meta"]["record_key"] == "{{row.email | lower}}"
    navigates = [step for step in _steps(recipe) if step["action"] == "navigate"]
    assert navigates[0]["url"] == EVENT_LIST
    # Ava may already be enrolled by hand: the list is searched for the row's email before New,
    # and a hit ends the row as already there.
    branches = [step for step in _steps(recipe) if step["action"] == "branch"]
    assert len(branches) == 1
    assert "{{row.email}}" in json.dumps(branches[0]["when"])
    assert json.dumps(branches[0]["then"]).count("skipped") == 1
    # The new attendee is saved by the person's own leave, and that step is checked.
    commits = [step for step in _steps(recipe) if step.get("commit")]
    assert len(commits) == 1 and "assert_after" in commits[0]


# ------------------------------------------------------------------ refusals


def test_opening_an_item_and_changing_nothing_is_refused() -> None:
    recording = [
        event
        for event in searched_and_clicked_a_line()
        if (event.get("step") or {}).get("name") != "Fill Sales Price"
        or event.get("page_set") is True
    ]
    assert read_shape(recording).problem == NO_CHANGE
    with pytest.raises(HTTPException) as refused:
        _compile(recording)
    assert refused.value.detail["message"] == NO_CHANGE


def test_a_change_that_is_never_saved_or_left_is_saved_by_leaving() -> None:
    # PL-127: stopping on the item's page is taught as a leave; the run's check proves the save.
    shape = read_shape(searched_and_clicked_a_line()[:-1])
    assert shape.problem is None
    assert (shape.kind, shape.save, shape.save_event) == ("update", "leave", None)


def test_a_recording_that_never_opens_an_item_says_so() -> None:
    recording = [searched_and_clicked_a_line()[2]]
    assert read_shape(recording).problem == NO_ITEM
