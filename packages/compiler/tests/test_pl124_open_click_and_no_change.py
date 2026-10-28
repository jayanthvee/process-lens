"""PL-124: the owner's price task, compiled.

The owner searched a product, clicked the "On hand: 18.00" line of its card, changed the price and
left by the back arrow. Two faults reached the saved task:

* the open-click kept the literal "On hand: 18.00", which only one product has;
* with the typed price dropped before the compile, the update wrote nothing, so its key was the
  product alone and every product an earlier task touched was skipped as already done.

The open-click is now aimed at the row's own value, and an update that writes nothing is refused.
"""

from __future__ import annotations

from typing import Any

import pytest

from packages.compiler import CompileError, compile_recording
from packages.compiler.compiler import NO_CHANGE_MESSAGE
from packages.compiler.testing import SAVE_TARGETS, action_event
from packages.compiler.tests.test_pl077_search_shape import (
    LIST_URL,
    PRICE,
    RECORD_URL,
    SEARCH_BOX,
    _options,
    _press_enter,
)

ON_HAND = [
    {"by": "role", "role": "generic", "name": "On hand: 18.00"},
    {"by": "css", "selector": "div > article > main > div:nth-of-type(2)"},
    {"by": "text_fuzzy", "value": "On hand: 18.00"},
]
BACK = [{"by": "css", "selector": "button.o_back_button.btn.btn-link"}]


def _opened_by_a_line_of_the_card(*, with_price: bool) -> list[dict[str, Any]]:
    events = [
        action_event(1, "fill", "fill", SEARCH_BOX, value="SKU-1", url=LIST_URL),
        _press_enter(2, LIST_URL),
        action_event(3, "click", "click", ON_HAND, step_id="open-1", url=LIST_URL),
    ]
    if with_price:
        events.append(action_event(4, "fill", "fill", PRICE, value="19.99", url=RECORD_URL))
    events.append(
        action_event(
            5,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=RECORD_URL,
            observation={"text_visible": "Saved"},
        )
    )
    return events


def _left_by_the_back_arrow(*, with_price: bool) -> list[dict[str, Any]]:
    events = [
        action_event(1, "fill", "fill", SEARCH_BOX, value="SKU-1", url=LIST_URL),
        _press_enter(2, LIST_URL),
        action_event(3, "click", "click", ON_HAND, step_id="open-1", url=LIST_URL),
    ]
    if with_price:
        events.append(action_event(4, "fill", "fill", PRICE, value="19.99", url=RECORD_URL))
    events.append(action_event(5, "click", "click", BACK, step_id="back-1", url=RECORD_URL))
    events.append(
        {"seq": 6, "kind": "navigation", "url": LIST_URL, "step": None},
    )
    return events


def _open_click(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        step
        for step in steps
        if step.get("action") == "click" and "o_back_button" not in str(step.get("targets"))
    )


def test_the_click_on_a_line_of_the_card_opens_the_row_s_own_result() -> None:
    result = compile_recording(
        _opened_by_a_line_of_the_card(with_price=True),
        _options(commit_step_ids=["save-1"], intents={"open-1": "click On hand: 18.00"}),
    )
    click = _open_click(result.recipe["steps"])

    assert click["targets"] == [{"by": "text_fuzzy", "value": "{{row.sku}}"}]
    assert click["exclude_search_box"] is True
    # The sentence teaching wrote was about the literal line; the run-time find needs the row's.
    assert click["intent"] == "open the result found by {{row.sku}}"
    assert "On hand" not in str(click)


def test_the_update_key_carries_the_typed_price() -> None:
    result = compile_recording(
        _opened_by_a_line_of_the_card(with_price=True), _options(commit_step_ids=["save-1"])
    )

    assert "{{row.price}}" in result.recipe["meta"]["idempotency_key"]


def test_an_update_that_changes_nothing_is_refused() -> None:
    with pytest.raises(CompileError) as refused:
        compile_recording(
            _opened_by_a_line_of_the_card(with_price=False), _options(commit_step_ids=["save-1"])
        )

    assert str(refused.value).split("\n", 1)[0] == NO_CHANGE_MESSAGE


def test_the_owner_s_shape_left_by_the_back_arrow_with_no_change_is_refused() -> None:
    with pytest.raises(CompileError) as refused:
        compile_recording(
            _left_by_the_back_arrow(with_price=False),
            _options(save_by_itself=True, sample_row={"sku": "SKU-1"}),
        )

    assert str(refused.value).split("\n", 1)[0] == NO_CHANGE_MESSAGE


def test_the_owner_s_shape_with_the_typed_price_compiles_to_open_change_and_key() -> None:
    result = compile_recording(
        _left_by_the_back_arrow(with_price=True), _options(save_by_itself=True)
    )
    steps = result.recipe["steps"]

    assert _open_click(steps)["targets"] == [{"by": "text_fuzzy", "value": "{{row.sku}}"}]
    fills = [step for step in steps if step.get("action") == "fill"]
    assert any(step.get("value") == {"kind": "input", "column": "price"} for step in fills), fills
    assert "{{row.price}}" in result.recipe["meta"]["idempotency_key"]
