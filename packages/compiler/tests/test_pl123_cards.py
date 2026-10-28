"""PL-123 item 4 and T1's price walk: a card, a search, and the row's own value.

The search's "did it find it" question is an intent on the condition, not a table cell. A task
taught by clicking the card without searching opens the list and finds the item by the row's value.
Matching trims spaces and ignores case, so a person's lower-case search still names the card.
"""

from __future__ import annotations

from typing import Any

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import action_event

LIST = "https://crm.example.com/products"
RECORD = "https://crm.example.com/products/17"
SEARCH = [{"by": "role", "role": "searchbox", "name": "Search products"}]
SUGGESTION = [
    {"by": "role", "role": "listitem", "name": "Search Product for: Oak Standing Desk"},
    {"by": "css", "selector": "li.o_menu_item.dropdown-item.focus"},
]
CARD = [
    {"by": "role", "role": "generic", "name": "Oak Standing Desk"},
    {"by": "css", "selector": "main > div > span"},
]
PRICE = [{"by": "role", "role": "spinbutton", "name": "Sales Price"}]
BACK = [{"by": "css", "selector": "button.o_back_button.btn.btn-link"}]

SAMPLE = {"product": "Oak Standing Desk", "new_price": "799"}


def _options(**overrides: Any) -> CompileOptions:
    values: dict[str, Any] = {
        "name": "Update a price",
        "when_to_use": "Each row changes one product's price.",
        "sample_row": dict(SAMPLE),
        "record_key_column": "product",
        "input_fields": {
            "product": {"type": "string", "required": True},
            "new_price": {"type": "string", "required": True},
        },
        "commit_step_ids": [],
        "save_by_itself": True,
    }
    values.update(overrides)
    return CompileOptions(**values)  # type: ignore[arg-type]


def _search_events(search_value: str) -> list[dict[str, Any]]:
    fill = action_event(1, "fill", "fill", SEARCH, value=search_value, url=LIST, step_id="fill-1")
    suggestion = action_event(2, "click", "click", SUGGESTION, url=LIST, step_id="click-1")
    fill["step"]["name"] = "Fill Search..."
    suggestion["step"]["name"] = "Click Search Product for: Oak Standing Desk"
    return [fill, suggestion, *_open_and_change()]


def _open_and_change() -> list[dict[str, Any]]:
    return [
        action_event(3, "click", "click", CARD, url=LIST, step_id="click-2"),
        action_event(4, "fill", "fill", PRICE, value="799", url=RECORD, step_id="fill-2"),
        action_event(5, "click", "click", BACK, url=RECORD, step_id="leave-1"),
        {"seq": 6, "kind": "navigate", "url": LIST, "step": None},
    ]


def _branches(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [step for step in steps if step.get("action") == "branch"]


def _commits(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [step for step in steps if step.get("commit") is True]


def test_the_search_question_is_an_intent_and_not_a_table_cell() -> None:
    result = compile_recording(_search_events("Oak Standing Desk"), _options())
    branch = _branches(result.recipe["steps"])[0]

    for kind in ("element_present", "element_absent"):
        if kind in branch["when"]:
            condition = branch["when"][kind]
            assert condition["intent"] == "the search result for {{row.product}}"
            assert all(rung.get("role") != "cell" for rung in condition["targets"]), condition
            assert any(
                "{{row.product}}" in str(rung.get("value", "")) for rung in condition["targets"]
            ), condition


def test_clicking_the_card_without_a_search_finds_it_by_the_rows_value() -> None:
    recording = [
        action_event(1, "click", "click", CARD, url=LIST, step_id="click-1"),
        action_event(3, "fill", "fill", PRICE, value="799", url=RECORD, step_id="fill-1"),
        action_event(4, "click", "click", BACK, url=RECORD, step_id="leave-1"),
        {"seq": 5, "kind": "navigate", "url": LIST, "step": None},
    ]

    result = compile_recording(recording, _options())
    steps = result.recipe["steps"]
    card = next(
        step
        for step in steps
        if step["action"] == "click"
        and step["targets"]
        == [
            {"by": "role", "role": "generic", "name": "{{row.product}}"},
            {"by": "css", "selector": "main > div > span"},
        ]
    )
    assert card["intent"] == "open the product found by {{row.product}}"
    assert _branches(steps) == [], steps


def test_a_lower_case_search_with_a_trailing_space_still_names_the_card() -> None:
    result = compile_recording(_search_events("oak standing desk "), _options())
    steps = result.recipe["steps"]

    fill = next(step for step in steps if step["action"] == "fill")
    assert fill["value"] == {"kind": "input", "column": "product"}

    card = next(
        step
        for step in steps
        if step["action"] == "click" and step["targets"][0].get("name") == "{{row.product}}"
    )
    assert card["targets"][0] == {"by": "role", "role": "generic", "name": "{{row.product}}"}
    assert card["intent"] == "open the product found by {{row.product}}"
    assert _commits(steps), steps


def test_the_search_run_is_not_mistaken_for_the_card() -> None:
    result = compile_recording(_search_events("Oak Standing Desk"), _options())
    steps = result.recipe["steps"]

    suggestion = next(
        step
        for step in steps
        if step["action"] == "click"
        and step["targets"][0].get("role") == "listitem"
        and "{{row.product}}" in step["targets"][0].get("name", "")
    )
    assert "{{row.product}}" in suggestion["targets"][0]["name"]
    assert suggestion.get("commit") is not True
    # The branch that asks whether the search found the item follows the search, not the card.
    branch = _branches(steps)[0]
    assert steps.index(branch) == steps.index(suggestion) + 1
