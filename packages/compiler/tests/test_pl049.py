"""PL-049: a product pick is not a search, and an app-generated number is not a save check.

The fixture is the Odoo sales quotation PL-046 compiled (see
``internal/agent/reports/PL-046.md``): a fill into the field whose placeholder is "Search a
product", the click on the option it offered, and the save whose observation was the new
quotation's own number, ``S00001``. The save was sent from ``/odoo/sales/new`` and the page it
landed on was ``/odoo/sales/12``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import action_event
from packages.compiler.validation import validation_errors

BASE_URL = "http://127.0.0.1:8069"
SALES_NEW = f"{BASE_URL}/odoo/sales/new"
SALES_SAVED = f"{BASE_URL}/odoo/sales/12"
QUOTE_RECORDING = Path(__file__).parent / "fixtures" / "odoo_quote_pl049.json"

SAMPLE_ROW: dict[str, str] = {
    "customer": "Summit Climbing Gym",
    "product": "Mesh Task Chair",
    "quantity": "3",
}

PRODUCT_FILL = [
    {"by": "role", "role": "combobox", "name": "Search a product"},
    {"by": "css", "selector": "input.o-autocomplete--input.o_input"},
    {"by": "text_fuzzy", "value": "Search a product"},
]
SEARCH_BOX = [{"by": "role", "role": "searchbox", "name": "Search leads"}]
SEARCH_CONTAINER = [{"by": "role", "role": "textbox", "name": "Query", "within": "search-bar"}]
PLAIN_TEXTBOX = [{"by": "role", "role": "textbox", "name": "Email"}]
SEARCH_BUTTON = [{"by": "role", "role": "button", "name": "Search"}]
GO_BUTTON = [{"by": "role", "role": "button", "name": "Go"}]
SAVE_TARGETS = [
    {"by": "role", "role": "button", "name": "Save"},
    {"by": "css", "selector": "button.o_form_button_save"},
]


def _options(**overrides: Any) -> CompileOptions:
    base: dict[str, Any] = {
        "name": "New quotation",
        "when_to_use": "Each row is a sales quotation for one customer.",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "customer",
        "commit_step_ids": ["save-1"],
        "allowed_origins": [BASE_URL],
    }
    base.update(overrides)
    return CompileOptions(**base)


def _quotation(observation: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The PL-046 recording, with a different save observation when a test needs one."""
    events = json.loads(QUOTE_RECORDING.read_text(encoding="utf-8"))
    if observation is not None:
        save = next(event for event in events if event["seq"] == 3)
        save["observation"] = observation
    return events


def _branch(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((step for step in steps if step.get("action") == "branch"), None)


def _commit(result: Any) -> dict[str, Any]:
    return next(step for step in result.recipe["steps"] if step.get("commit") is True)


def _click(seq: int, targets: list[dict[str, Any]]) -> dict[str, Any]:
    return action_event(seq, "click", "click", targets, url=SALES_NEW)


def _enter(seq: int, targets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "seq": seq,
        "kind": "press",
        "url": SALES_NEW,
        "step": {
            "id": f"press-{seq}",
            "action": "press",
            "targets": targets,
            "key": "Enter",
        },
    }


def _create_after_search(
    fill_targets: list[dict[str, Any]], runner: dict[str, Any]
) -> list[dict[str, Any]]:
    """A search that the recording then follows with a save: the shape PL-017 branches on."""
    return [
        action_event(1, "fill", "fill", fill_targets, value="Summit Climbing Gym", url=SALES_NEW),
        runner,
        action_event(
            3,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=SALES_NEW,
            observation={"text_visible": "Quotation saved"},
        ),
        {"seq": 4, "kind": "navigate", "url": SALES_SAVED},
    ]


# --- a pick is not a search -------------------------------------------------


def test_a_fill_then_an_option_pick_is_not_a_search() -> None:
    result = compile_recording(_quotation(), _options())
    assert _branch(result.recipe["steps"]) is None
    assert validation_errors(result.recipe) == []


@pytest.mark.parametrize(
    "within",
    ["listbox:Products", "autocomplete:Search a product"],
)
def test_a_click_inside_a_listbox_is_an_option_pick(within: str) -> None:
    """The click names a listbox or an autocomplete dropdown, even without a role option rung."""
    option = [{"by": "role", "role": "button", "name": "Mesh Task Chair", "within": within}]
    recording = [
        action_event(1, "fill", "fill", PRODUCT_FILL, value="Mesh Task Chair", url=SALES_NEW),
        action_event(2, "click", "click", option, url=SALES_NEW),
        action_event(
            3,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=SALES_NEW,
            observation={"text_visible": "Quotation saved"},
        ),
        {"seq": 4, "kind": "navigate", "url": SALES_SAVED},
    ]
    steps = compile_recording(recording, _options()).recipe["steps"]
    assert _branch(steps) is None


def test_search_only_in_a_text_fuzzy_placeholder_is_not_a_search() -> None:
    """A text_fuzzy rung is the placeholder fallback, not a durable "this is a search" signal."""
    placeholder = [{"by": "text_fuzzy", "value": "Search a product"}]
    recording = _create_after_search(placeholder, _click(2, GO_BUTTON))
    steps = compile_recording(recording, _options()).recipe["steps"]
    assert _branch(steps) is None


@pytest.mark.parametrize(
    ("fill_targets", "runner"),
    [
        pytest.param(SEARCH_BOX, _click(2, GO_BUTTON), id="role-searchbox"),
        pytest.param(SEARCH_CONTAINER, _click(2, GO_BUTTON), id="search-container-within"),
        pytest.param(PLAIN_TEXTBOX, _click(2, SEARCH_BUTTON), id="button-named-search"),
        pytest.param(SEARCH_BOX, _enter(2, SEARCH_BOX), id="enter-in-search-box"),
    ],
)
def test_a_real_search_still_gets_the_duplicate_branch(
    fill_targets: list[dict[str, Any]], runner: dict[str, Any]
) -> None:
    recording = _create_after_search(fill_targets, runner)
    steps = compile_recording(recording, _options()).recipe["steps"]
    branch = _branch(steps)
    assert branch is not None
    assert branch["when"] == {
        "element_present": {
            "targets": [{"by": "role", "role": "cell", "name": "{{row.customer | lower}}"}]
        }
    }


# --- a generated number is not a save check ---------------------------------


@pytest.mark.parametrize("reference", ["S00001", "INV/2026/00012", "PO-0042"])
def test_a_generated_reference_is_not_pinned(reference: str) -> None:
    result = compile_recording(_quotation({"text_visible": reference}), _options())
    check = _commit(result)["assert_after"]
    assert "text_visible" not in check
    # The same fallback a weak observation gets: the shape of the page the save landed on.
    assert check["url_matches"] == "/odoo/sales/\\d+"
    assert validation_errors(result.recipe) == []


def test_a_generated_reference_falls_back_to_the_new_records_name() -> None:
    """With no page move, the rejected observation leaves the fill-based check in place."""
    recording = [
        action_event(
            1,
            "fill",
            "fill",
            [{"by": "role", "role": "combobox", "name": "Customer"}],
            value="Summit Climbing Gym",
            url=SALES_NEW,
        ),
        action_event(2, "fill", "fill", PRODUCT_FILL, value="Mesh Task Chair", url=SALES_NEW),
        action_event(
            3,
            "click",
            "click",
            [{"by": "role", "role": "option", "name": "[PL-CHR-2001] Mesh Task Chair"}],
            url=SALES_NEW,
        ),
        action_event(
            4,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=SALES_NEW,
            observation={"text_visible": "S00001"},
        ),
    ]
    result = compile_recording(recording, _options())
    check = _commit(result)["assert_after"]
    assert "text_visible" not in check
    assert check["text_count_increases"] == "{{row.customer}}"
    assert validation_errors(result.recipe) == []


def test_a_typed_value_is_still_pinned() -> None:
    result = compile_recording(_quotation({"text_visible": "Summit Climbing Gym"}), _options())
    assert _commit(result)["assert_after"]["text_visible"] == "Summit Climbing Gym"


def test_a_generated_looking_value_from_the_sample_row_is_still_pinned() -> None:
    """A reference the person typed is a sample-row value, so it is not the app's own number."""
    options = _options(sample_row={**SAMPLE_ROW, "reference": "S00001"})
    result = compile_recording(_quotation({"text_visible": "S00001"}), options)
    assert _commit(result)["assert_after"]["text_visible"] == "S00001"


def test_ordinary_words_are_still_pinned() -> None:
    result = compile_recording(_quotation({"text_visible": "Quotation saved"}), _options())
    assert _commit(result)["assert_after"]["text_visible"] == "Quotation saved"
