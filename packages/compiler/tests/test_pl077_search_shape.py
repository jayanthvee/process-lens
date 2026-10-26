"""PL-077: a search that finds the record to change is not a duplicate check.

PL-017 proposes "skip the row if the search finds it" whenever a recording searches for the record's
value. That is right for a create: a lead that is already there is skipped. It is wrong for an
update: the product the search finds is exactly the one to change, so every row would end
`skipped`. For an update the opposite rule holds -- if the search finds nothing there is nothing to
change, and the row is skipped with the site it looked in named in the reason (PL-106 item 7).

The two shapes are told apart from the recorded steps and their URLs only, never from app names:

* the search runs, then a click **navigates to a record's own page** -- a path whose last segment
  is all digits, not the page the search ran on -- and the commit happens after that click;
* anything else, including a click that lands on a page whose last segment is a word (`.../new`),
  a click that does not navigate, or no such click at all.
"""

from __future__ import annotations

import json
from typing import Any

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import EMAIL_TARGETS, SAVE_TARGETS, action_event
from packages.compiler.validation import validation_errors

SEARCH_BOX = [{"by": "role", "role": "searchbox", "name": "Search products"}]
SEARCH_ENTER = [{"by": "role", "role": "textbox", "name": "Search products"}]
RESULT_ROW = [{"by": "role", "role": "cell", "name": "SKU-1"}]
NEW_LINK = [{"by": "role", "role": "link", "name": "New lead"}]
PRICE = [{"by": "label", "text": "Sales Price"}]

LIST_URL = "https://crm.example.com/products"
RECORD_URL = "https://crm.example.com/products/17"
NEW_URL = "https://crm.example.com/products/new"

SAMPLE_ROW = {"sku": "SKU-1", "price": "19.99"}


def _options(**overrides: Any) -> CompileOptions:
    base: dict[str, Any] = {
        "name": "Change a price",
        "when_to_use": "Change the price of a product that exists.",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "sku",
    }
    base.update(overrides)
    return CompileOptions(**base)


def _press_enter(seq: int, url: str) -> dict[str, Any]:
    """The Enter that runs the search, in the shape the recorder emits (a press with its key)."""
    event = action_event(seq, "press", "press", SEARCH_ENTER, url=url)
    event["step"]["key"] = "Enter"
    return event


def _find_then_update() -> list[dict[str, Any]]:
    """Search for the SKU, open the result it finds, change the price, save: PL-063's shape."""
    return [
        action_event(1, "fill", "fill", SEARCH_BOX, value="SKU-1", url=LIST_URL),
        _press_enter(2, LIST_URL),
        action_event(3, "click", "click", RESULT_ROW, url=LIST_URL),
        action_event(4, "fill", "fill", PRICE, value="19.99", url=RECORD_URL),
        action_event(
            5,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=RECORD_URL,
            observation={"text_visible": "Saved"},
        ),
    ]


def _search_then_create() -> list[dict[str, Any]]:
    """Search for the key, then New lead -> fill -> Save: today's create, unchanged."""
    return [
        action_event(1, "fill", "fill", SEARCH_BOX, value="SKU-1", url=LIST_URL),
        _press_enter(2, LIST_URL),
        action_event(3, "click", "click", NEW_LINK, url=LIST_URL),
        action_event(4, "fill", "fill", EMAIL_TARGETS, value="SKU-1", url=NEW_URL),
        action_event(
            5,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=NEW_URL,
            observation={"text_visible": "Created"},
        ),
    ]


def _branches(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [step for step in steps if step.get("action") == "branch"]


# ---------------------------------------------------------- find-then-update


def test_a_find_then_update_skips_a_row_whose_search_finds_nothing() -> None:
    result = compile_recording(_find_then_update(), _options(commit_step_ids=["save-1"]))
    steps = result.recipe["steps"]

    branches = _branches(steps)
    assert len(branches) == 1, [step["action"] for step in steps]
    branch = branches[0]
    # The rule is the opposite of the duplicate check: no result in the table means the record is
    # not there, so the row is skipped instead of typing into whatever page it is on. There is
    # nothing for a person to do, which is why it is not a park (PL-106 item 7).
    assert branch["when"] == {
        "element_absent": {
            "targets": [{"by": "role", "role": "cell", "name": "{{row.sku | lower}}"}]
        }
    }
    assert branch["then"] == [
        {
            "id": "end-record-1",
            "action": "end_record",
            "outcome": "skipped",
            "reason": "Not found in crm.example.com.",
        }
    ]
    assert branch["name"] == "Skip a record when the search finds nothing"
    # The reason names the site the search ran on, so a person reading it knows where to look.
    assert branch["then"][0]["reason"] == "Not found in crm.example.com."


def test_the_park_branch_goes_after_the_search_and_before_the_click_that_opens_a_result() -> None:
    steps = compile_recording(_find_then_update(), _options(commit_step_ids=["save-1"])).recipe[
        "steps"
    ]

    actions = [step["action"] for step in steps]
    assert actions == ["navigate", "fill", "press", "branch", "click", "fill", "click"], actions
    # The search that runs the query is not the commit, exactly as for a create.
    assert "commit" not in steps[3 - 1]
    assert steps[-1].get("commit") is True


def test_the_recipe_is_valid() -> None:
    result = compile_recording(_find_then_update(), _options(commit_step_ids=["save-1"]))
    assert validation_errors(result.recipe) == []


def test_the_compiler_says_which_shape_it_compiled() -> None:
    update = compile_recording(_find_then_update(), _options(commit_step_ids=["save-1"]))
    create = compile_recording(_search_then_create(), _options(commit_step_ids=["save-1"]))

    assert update.search_kind == "update"
    assert create.search_kind == "create"


def test_a_recording_with_no_search_has_no_shape() -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="SKU-1", url=NEW_URL),
        action_event(
            2,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=NEW_URL,
            observation={"text_visible": "Created"},
        ),
    ]
    result = compile_recording(recording, _options(commit_step_ids=["save-1"]))
    assert result.search_kind is None
    assert _branches(result.recipe["steps"]) == []


def test_the_park_branch_can_be_turned_off() -> None:
    """D-019: the person unticks the rule, and the compiler proposes nothing."""
    options = _options(commit_step_ids=["save-1"], duplicate_check=False)
    result = compile_recording(_find_then_update(), options)
    assert _branches(result.recipe["steps"]) == []
    # The shape is still reported, so the popup can word its checkbox even when the tick is off.
    assert result.search_kind == "update"


def test_a_click_that_does_not_navigate_is_a_create() -> None:
    """A row click that only selects (no page change) opens nothing: not find-then-update."""
    recording = [
        action_event(1, "fill", "fill", SEARCH_BOX, value="SKU-1", url=LIST_URL),
        _press_enter(2, LIST_URL),
        action_event(3, "click", "click", RESULT_ROW, url=LIST_URL),
        action_event(4, "fill", "fill", EMAIL_TARGETS, value="SKU-1", url=LIST_URL),
        action_event(
            5,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=LIST_URL,
            observation={"text_visible": "Created"},
        ),
    ]
    result = compile_recording(recording, _options(commit_step_ids=["save-1"]))
    assert result.search_kind == "create"
    branch = _branches(result.recipe["steps"])[0]
    assert branch["when"]["element_present"] is not None


# ----------------------------------------------------------------- create


def test_a_create_recipe_is_unchanged_byte_for_byte() -> None:
    """Today's output for the same recording, exactly: the branch, its words, its place."""
    steps = compile_recording(_search_then_create(), _options(commit_step_ids=["save-1"])).recipe[
        "steps"
    ]

    branch = _branches(steps)[0]
    assert branch == {
        "id": "branch-1",
        "action": "branch",
        "name": "Skip a record if the search finds it",
        "when": {
            "element_present": {
                "targets": [{"by": "role", "role": "cell", "name": "{{row.sku | lower}}"}]
            }
        },
        "then": [
            {
                "id": "end-record-1",
                "action": "end_record",
                "outcome": "skipped",
                "reason": "duplicate",
            }
        ],
    }
    # A stable fingerprint of the whole document: any change to a create's recipe shows up here.
    assert json.dumps(steps, sort_keys=True).count("element_absent") == 0
    assert [step["action"] for step in steps] == [
        "navigate",
        "fill",
        "press",
        "branch",
        "click",
        "fill",
        "click",
    ]


def test_a_record_page_named_by_a_uuid_is_an_update() -> None:
    """Twenty-style record pages end in a UUID, not a number; they open one record too (T1)."""
    uuid_url = "https://crm.example.com/object/product/3f2b8c1e-9a4d-4e7b-8c21-5d6e7f809a1b"
    recording = [
        {**event, "url": uuid_url} if event["url"] == RECORD_URL else event
        for event in _find_then_update()
    ]
    result = compile_recording(recording, _options(commit_step_ids=["save-1"]))
    assert result.search_kind == "update"
