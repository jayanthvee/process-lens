"""PL-123 item 2: every compiled step carries one plain sentence of intent.

Teaching supplies the sentence through ``clean_recording``; without a model the compiler derives one
from the step's own words, so a recipe always has an intent for the run to ask about. A step the
person confirmed or the model wrote wins over the derived sentence.
"""

from __future__ import annotations

from typing import Any

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import action_event

LIST = "https://crm.example.com/products"
RECORD = "https://crm.example.com/products/17"
SEARCH = [{"by": "role", "role": "searchbox", "name": "Search products"}]
RESULT = [{"by": "role", "role": "cell", "name": "Oak Standing Desk"}]
PRICE = [{"by": "role", "role": "spinbutton", "name": "Sales Price"}]
BACK = [{"by": "css", "selector": "button.o_back_button.btn.btn-link"}]

SAMPLE = {"product": "Oak Standing Desk", "price": "799"}


def _options(**overrides: Any) -> CompileOptions:
    values: dict[str, Any] = {
        "name": "Update a price",
        "when_to_use": "Each row changes one product's price.",
        "sample_row": dict(SAMPLE),
        "record_key_column": "product",
        "input_fields": {
            "product": {"type": "string", "required": True},
            "price": {"type": "string", "required": True},
        },
    }
    values.update(overrides)
    return CompileOptions(**values)  # type: ignore[arg-type]


def _press_enter(seq: int) -> dict[str, Any]:
    event = action_event(seq, "press", "press", SEARCH, url=LIST)
    event["step"]["key"] = "Enter"
    return event


def _owner_recording() -> list[dict[str, Any]]:
    return [
        action_event(
            1, "fill", "fill", SEARCH, value="Oak Standing Desk", url=LIST, step_id="fill-1"
        ),
        _press_enter(2),
        action_event(3, "click", "click", RESULT, url=LIST, step_id="click-1"),
        action_event(4, "fill", "fill", PRICE, value="799", url=RECORD, step_id="fill-2"),
        action_event(5, "click", "click", BACK, url=RECORD, step_id="leave-1"),
        {"seq": 6, "kind": "navigate", "url": LIST, "step": None},
    ]


def test_every_compiled_step_carries_an_intent() -> None:
    result = compile_recording(
        _owner_recording(), _options(commit_step_ids=[], save_by_itself=True)
    )

    assert result.recipe["steps"], result.recipe
    action_steps = [
        step
        for step in result.recipe["steps"]
        if step["action"] not in ("branch", "end_record", "wait_for")
    ]
    assert all(step.get("intent") for step in action_steps), action_steps


def test_a_confirmed_intent_is_used_and_a_derived_one_fills_the_rest() -> None:
    result = compile_recording(
        _owner_recording(),
        _options(
            commit_step_ids=[],
            save_by_itself=True,
            intents={
                "fill-1": "type the product name into the search box",
                "leave-1": "go back to the product list",
            },
        ),
    )

    by_action = {(step["action"], step.get("name")): step for step in result.recipe["steps"]}
    fills = [step for step in result.recipe["steps"] if step["action"] == "fill"]
    assert fills[0]["intent"] == "type the product name into the search box"
    leaves = [
        step
        for step in result.recipe["steps"]
        if step["action"] == "click" and step["targets"] == BACK
    ]
    assert leaves[0]["intent"] == "go back to the product list"
    # Every other action step still has a sentence of its own.
    action_steps = [
        step
        for step in result.recipe["steps"]
        if step["action"] not in ("branch", "end_record", "wait_for")
    ]
    assert all(step.get("intent") for step in action_steps)
    assert by_action


def test_a_dropped_step_never_becomes_a_step_or_the_leave() -> None:
    recording = _owner_recording()
    # A recorded stray click after the changed field, before the real leave.
    stray = action_event(
        5,
        "click",
        "click",
        [{"by": "role", "role": "link", "name": "All Apps"}],
        url=RECORD,
        step_id="click-stray",
    )
    leave = action_event(6, "click", "click", BACK, url=RECORD, step_id="leave-1")
    recording = recording[:4] + [
        stray,
        leave,
        {"seq": 7, "kind": "navigate", "url": LIST, "step": None},
    ]

    result = compile_recording(
        recording,
        _options(commit_step_ids=[], save_by_itself=True, dropped_step_ids=["click-stray"]),
    )

    names = [step.get("name") or "" for step in result.recipe["steps"]]
    assert not any("All Apps" in name for name in names), names
    assert any(step.get("commit") for step in result.recipe["steps"]), result.recipe["steps"]
    assert not any(step.get("id") == "end-record-unchecked" for step in result.recipe["steps"]), (
        result.recipe["steps"]
    )
