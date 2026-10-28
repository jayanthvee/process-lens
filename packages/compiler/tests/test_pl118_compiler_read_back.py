"""PL-118 item 1 and 2: an edit is proved by reading the changed value back.

The owner's shape: open a product, change its Sales Price, then go back to the list (the app
saves by itself). The compiler must give the commit a ``value_equals`` read-back of the changed
field, and the "saves by itself" mode must leave the form the way the person did and then read
the value on the item's saved page. A check that is only the page's frame (a tab) is refused.
"""

from __future__ import annotations

from typing import Any

import pytest

from packages.compiler import CompileError, CompileOptions, compile_recording
from packages.compiler.testing import action_event
from packages.compiler.validation import validation_errors

LIST = "https://crm.example.com/products"
RECORD = "https://crm.example.com/products/17"
SEARCH_BOX = [{"by": "role", "role": "searchbox", "name": "Search products"}]
RESULT_ROW = [{"by": "role", "role": "cell", "name": "Oak Standing Desk"}]
PRICE = [{"by": "role", "role": "spinbutton", "name": "Sales Price"}]
SAVE = [{"by": "role", "role": "button", "name": "Save"}]
BREADCRUMB = [{"by": "role", "role": "link", "name": "Products"}]
TAB = [{"by": "role", "role": "tab", "name": "Sales"}]

SAMPLE_ROW = {"product": "Oak Standing Desk", "price": "799"}


def _options(**overrides: Any) -> CompileOptions:
    values: dict[str, Any] = {
        "name": "Update a price",
        "when_to_use": "Each row changes one product's price.",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "product",
        "input_fields": {
            "product": {"type": "string", "required": True},
            "price": {"type": "string", "required": True},
        },
    }
    values.update(overrides)
    return CompileOptions(**values)  # type: ignore[arg-type]


def _press_enter(seq: int, url: str) -> dict[str, Any]:
    event = action_event(seq, "press", "press", SEARCH_BOX, url=url)
    event["step"]["key"] = "Enter"
    return event


def _owner_shape(*, save_by_itself: bool) -> list[dict[str, Any]]:
    """Search, open the product, change Sales Price, then leave (or press Save)."""
    events = [
        action_event(1, "fill", "fill", SEARCH_BOX, value="Oak Standing Desk", url=LIST),
        _press_enter(2, LIST),
        action_event(3, "click", "click", RESULT_ROW, url=LIST, step_id="open-1"),
        action_event(4, "fill", "fill", PRICE, value="799", url=RECORD),
    ]
    if save_by_itself:
        # The app saves when the form is left: the breadcrumb goes back to the list.
        events.append(action_event(5, "click", "click", BREADCRUMB, url=RECORD, step_id="leave-1"))
        events.append({"seq": 6, "kind": "navigate", "url": LIST, "step": None})
    else:
        events.append(action_event(5, "click", "click", SAVE, url=RECORD, step_id="save-1"))
    return events


def _commit(result: Any) -> dict[str, Any]:
    commits = [step for step in result.recipe["steps"] if step.get("commit") is True]
    assert len(commits) == 1, result.recipe["steps"]
    return commits[0]


def test_a_save_click_on_an_edit_is_checked_by_the_value_it_changed() -> None:
    """Item 1: a find-then-change commit proves the field shows the row's value."""
    result = compile_recording(
        _owner_shape(save_by_itself=False), _options(commit_step_ids=["save-1"])
    )

    check = _commit(result)["assert_after"]
    assert "value_equals" in check
    spec = check["value_equals"]
    assert spec["selector"]["targets"] == PRICE
    assert spec["value"] == {"kind": "input", "column": "price"}
    assert spec.get("case_insensitive") is True
    assert validation_errors(result.recipe) == []


def test_the_saves_by_itself_mode_reads_the_value_back_on_the_saved_page() -> None:
    """Item 2: leave the form as the person did, re-open the item, read Sales Price."""
    result = compile_recording(
        _owner_shape(save_by_itself=True),
        _options(commit_step_ids=[], save_by_itself=True),
    )
    steps = result.recipe["steps"]
    commit = _commit(result)

    # The commit is a click under the existing rules: it re-opens the item after the leave, and
    # the read-back runs on the item's own page.
    assert commit["action"] == "click"
    # The row's own value, so each batch row re-opens its own item.
    assert commit["targets"] == [{"by": "role", "role": "cell", "name": "{{row.product}}"}]
    assert commit["assert_after"]["value_equals"]["selector"]["targets"] == PRICE
    # The leave the person made is still there, before the commit.
    actions = [step["action"] for step in steps]
    assert actions.index("navigate") < actions.index(commit["action"])
    assert any(step["action"] == "click" and step["targets"] == BREADCRUMB for step in steps)
    assert validation_errors(result.recipe) == []
    assert any("read" in warning and "back" in warning for warning in result.warnings), (
        result.warnings
    )


def test_a_tab_is_never_the_proof_of_an_edit() -> None:
    """A check that is the page's frame is refused, even when it was recorded as the save."""
    events = [
        action_event(1, "fill", "fill", PRICE, value="799", url=RECORD),
        action_event(2, "click", "click", TAB, url=RECORD, step_id="save-1"),
    ]
    events[1]["observation"] = {"text_visible": "Sales"}

    with pytest.raises(CompileError) as refusal:
        compile_recording(events, _options(commit_step_ids=["save-1"]))

    problems = "\n".join(refusal.value.problems)
    assert "page's own frame" in problems or "tab" in problems.lower()


def test_no_read_back_says_so_and_every_row_is_left_for_a_person() -> None:
    """A value that is not a row column cannot be read back; the row can never be Done."""
    events = [
        action_event(1, "fill", "fill", SEARCH_BOX, value="Oak Standing Desk", url=LIST),
        _press_enter(2, LIST),
        action_event(3, "click", "click", RESULT_ROW, url=LIST, step_id="open-1"),
        # A literal the sample row does not carry: a batch row would not change it.
        action_event(4, "fill", "fill", PRICE, value="1", url=RECORD),
        action_event(5, "click", "click", BREADCRUMB, url=RECORD, step_id="leave-1"),
        {"seq": 6, "kind": "navigate", "url": LIST, "step": None},
    ]

    result = compile_recording(
        events,
        _options(
            commit_step_ids=[], save_by_itself=True, sample_row={"product": "Oak Standing Desk"}
        ),
    )

    assert result.recipe["steps"][-1]["action"] == "end_record"
    assert result.recipe["steps"][-1]["outcome"] == "parked"
    assert any("read" in warning and "back" in warning for warning in result.warnings), (
        result.warnings
    )
    assert validation_errors(result.recipe) == []


def test_a_recording_that_never_left_the_form_cannot_be_done() -> None:
    """No leave, no save shortcut: nothing shows what the app saved, so every row parks."""
    events = [
        action_event(1, "fill", "fill", SEARCH_BOX, value="Oak Standing Desk", url=LIST),
        _press_enter(2, LIST),
        action_event(3, "click", "click", RESULT_ROW, url=LIST, step_id="open-1"),
        action_event(4, "fill", "fill", PRICE, value="799", url=RECORD),
    ]

    result = compile_recording(events, _options(commit_step_ids=[], save_by_itself=True))

    assert result.recipe["steps"][-1]["action"] == "end_record"
    assert result.recipe["steps"][-1]["outcome"] == "parked"
    assert result.recipe["steps"][-1]["reason"] == (
        "ProcessLens couldn't confirm the change was saved."
    )
    assert any("without leaving it" in warning for warning in result.warnings), result.warnings
    assert validation_errors(result.recipe) == []


def test_a_save_shortcut_alone_is_still_refused() -> None:
    """PL-018 keeps its rule: a save sent with a key is refused, so it is never the leave.

    PL-118 item 2 says to use the app's save shortcut only when one was recorded, but a press is
    not commit-capable (AGENTS.md section 4). A recording whose last action is the shortcut is
    therefore refused with PL-018's sentence, exactly as before this ticket -- "saves by itself"
    cannot turn a key into the save.
    """
    shortcut = action_event(5, "press", "press", PRICE, url=RECORD, step_id="press-1")
    shortcut["step"]["key"] = "Control+Enter"
    events = [
        action_event(1, "fill", "fill", SEARCH_BOX, value="Oak Standing Desk", url=LIST),
        _press_enter(2, LIST),
        action_event(3, "click", "click", RESULT_ROW, url=LIST, step_id="open-1"),
        action_event(4, "fill", "fill", PRICE, value="799", url=RECORD),
        shortcut,
    ]

    with pytest.raises(CompileError) as refusal:
        compile_recording(events, _options(commit_step_ids=[], save_by_itself=True))

    assert "done with a key" in str(refusal.value)
