"""PL-079: an update is keyed by what it changes, and a row that already matches is skipped.

Two gaps, both seen in the price golden.

* **The key.** PL-077 knows a find-then-update from a create, but the compiled
  ``meta.idempotency_key`` was still the record key alone. For a create that is right -- one lead
  per email. For an update it means "this record was changed once, ever", so next week's new price
  for the same SKU is skipped as already committed. An update is keyed by the record **and the
  values it writes**: the record key's own template, then ``:``, then every column the update's
  fills write, in step order. A create's key does not move, byte for byte.

* **The row that is already right.** When the page already shows the row's values, the destination
  keeps its Save hidden, so the commit click cannot be prepared and the record parks. Nothing needs
  writing, so the recipe itself ends the record ``skipped``: a compiled ``branch`` placed right
  before the commit click, whose condition is the same ``element_absent`` on the commit control that
  PL-075 uses as the commit's proof. D-017: this is the recipe's own step, never a runner special
  case.

Both rules are compiled, visible in the recipe, and covered here before the golden repeats them on
real Odoo.
"""

from __future__ import annotations

import json
from typing import Any

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import EMAIL_TARGETS, action_event
from packages.compiler.validation import validation_errors

LIST_URL = "https://crm.example.com/products"
RECORD_URL = "https://crm.example.com/products/17"
NEW_URL = "https://crm.example.com/products/new"

SEARCH_BOX = [{"by": "role", "role": "searchbox", "name": "Search products"}]
SEARCH_ENTER = [{"by": "role", "role": "textbox", "name": "Search products"}]
RESULT_ROW = [{"by": "role", "role": "cell", "name": "SKU-1"}]
NEW_LINK = [{"by": "role", "role": "link", "name": "New product"}]
PRICE = [{"by": "label", "text": "Sales Price"}]
COST = [{"by": "label", "text": "Cost"}]
SAVE_TARGETS = [
    {"by": "role", "role": "button", "name": "Save manually"},
    {"by": "css", "selector": "button.o_form_button_save"},
]

SAMPLE_ROW = {"sku": "SKU-1", "price": "19.99"}

# What the recorder emits after Odoo's update-in-place save (PL-075): the Save control is gone and
# the page is still the record's own page. This is PL-075's proof shape, and the branch's condition.
UPDATE_OBSERVATION: dict[str, Any] = {
    "element_absent": {"targets": SAVE_TARGETS},
    "url_matches": "/products/\\d+",
}

ALREADY_REASON = "Already had these values, so nothing was changed."


def _options(**overrides: Any) -> CompileOptions:
    base: dict[str, Any] = {
        "name": "Change a price",
        "when_to_use": "Change the price of a product that exists.",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "sku",
        "commit_step_ids": ["save-1"],
    }
    base.update(overrides)
    return CompileOptions(**base)


def _press_enter(seq: int, url: str) -> dict[str, Any]:
    event = action_event(seq, "press", "press", SEARCH_ENTER, url=url)
    event["step"]["key"] = "Enter"
    return event


def _update(
    fills: list[tuple[list[dict[str, Any]], str]],
    *,
    observation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Search for the SKU, open the record the search finds, write ``fills``, then save."""
    events = [
        action_event(1, "fill", "fill", SEARCH_BOX, value="SKU-1", url=LIST_URL),
        _press_enter(2, LIST_URL),
        action_event(3, "click", "click", RESULT_ROW, url=LIST_URL),
    ]
    seq = 4
    for targets, value in fills:
        events.append(action_event(seq, "fill", "fill", targets, value=value, url=RECORD_URL))
        seq += 1
    events.append(
        action_event(
            seq,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=RECORD_URL,
            observation=observation,
        )
    )
    return events


def _create() -> list[dict[str, Any]]:
    """Search for the key, then New product -> fill -> Save: today's create, unchanged."""
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


# --------------------------------------------------------------- the key


def test_an_update_key_carries_the_values_it_writes() -> None:
    result = compile_recording(
        _update([(PRICE, "19.99")], observation=UPDATE_OBSERVATION), _options()
    )
    meta = result.recipe["meta"]

    assert result.search_kind == "update"
    assert meta["record_key"] == "{{row.sku | lower}}"
    # The record key's own template, then ':', then the column the update writes.
    assert meta["idempotency_key"] == "{{row.sku | lower}}:{{row.price}}"


def test_the_key_lists_the_update_fills_in_step_order() -> None:
    sample = {"sku": "SKU-1", "price": "19.99", "cost": "8.50"}
    result = compile_recording(
        _update([(PRICE, "19.99"), (COST, "8.50")], observation=UPDATE_OBSERVATION),
        _options(sample_row=sample),
    )

    assert result.recipe["meta"]["idempotency_key"] == (
        "{{row.sku | lower}}:{{row.price}}:{{row.cost}}"
    )


def test_the_key_uses_only_columns_the_sample_row_has() -> None:
    """A fill the demonstration typed but the sample row does not carry is no column to key on."""
    result = compile_recording(
        _update([(PRICE, "19.99")], observation=UPDATE_OBSERVATION),
        _options(sample_row={"sku": "SKU-1"}),
    )

    assert result.search_kind == "update"
    assert result.recipe["meta"]["idempotency_key"] == "{{row.sku | lower}}"


def test_a_create_key_is_the_record_key_alone() -> None:
    """Byte for byte what a create compiled to before this ticket."""
    result = compile_recording(_create(), _options())

    assert result.search_kind == "create"
    assert result.recipe["meta"]["idempotency_key"] == "{{row.sku | lower}}"
    assert "{{row.price}}" not in json.dumps(result.recipe, sort_keys=True)


def test_an_explicit_idempotency_key_is_left_alone() -> None:
    """A caller's own key -- a person's decision -- outranks the compiler's rule."""
    result = compile_recording(
        _update([(PRICE, "19.99")], observation=UPDATE_OBSERVATION),
        _options(idempotency_key="{{row.sku}}:{{row.price}}:{{row.effective_date}}"),
    )

    assert result.recipe["meta"]["idempotency_key"] == (
        "{{row.sku}}:{{row.price}}:{{row.effective_date}}"
    )


# ------------------------------------------------- the already-matches branch


def test_an_update_skips_a_row_that_is_already_right_before_it_saves() -> None:
    recipe = compile_recording(
        _update([(PRICE, "19.99")], observation=UPDATE_OBSERVATION), _options()
    ).recipe
    steps = recipe["steps"]

    actions = [step["action"] for step in steps]
    assert actions == ["navigate", "fill", "press", "branch", "click", "fill", "branch", "click"], (
        actions
    )
    commit_index = next(index for index, step in enumerate(steps) if step.get("commit") is True)
    branch = steps[commit_index - 1]
    assert branch["action"] == "branch"
    # The condition is the same element_absent on the commit control PL-075 uses as the proof:
    # absent before the click means the page has nothing to save.
    commit = steps[commit_index]
    assert branch["when"] == {"element_absent": {"targets": SAVE_TARGETS}}
    assert branch["when"]["element_absent"] == commit["assert_after"]["element_absent"]
    assert branch["then"] == [
        {
            "id": "end-record-1",
            "action": "end_record",
            "outcome": "skipped",
            "reason": ALREADY_REASON,
        }
    ]
    assert branch["name"] == "Skip a record whose values are already there"
    # The save is still the recipe's commit, and the branch never is.
    assert "commit" not in branch


def test_a_create_recipe_gets_no_already_matches_branch() -> None:
    recipe = compile_recording(_create(), _options()).recipe
    branches = _branches(recipe["steps"])

    assert len(branches) == 1
    assert "element_absent" not in branches[0]["when"]
    assert ALREADY_REASON not in json.dumps(recipe, sort_keys=True)


def test_an_update_whose_commit_proves_nothing_else_gets_no_such_branch() -> None:
    """Only a PL-075-shaped commit -- a control that disappears -- can carry this branch."""
    recipe = compile_recording(
        _update([(PRICE, "19.99")], observation={"text_visible": "Saved"}), _options()
    ).recipe
    branches = _branches(recipe["steps"])

    # The park branch is still there; the already-matches branch is not.
    assert len(branches) == 1
    assert branches[0]["when"] == {
        "element_absent": {
            "targets": [{"by": "role", "role": "cell", "name": "{{row.sku | lower}}"}]
        }
    }
    assert ALREADY_REASON not in json.dumps(recipe, sort_keys=True)


def test_a_create_whose_save_disappears_still_gets_no_such_branch() -> None:
    """The rule is the update shape's: a create that found nothing still has a record to create."""
    recording = _create()
    recording[-1]["observation"] = {"element_absent": {"targets": SAVE_TARGETS}}
    result = compile_recording(recording, _options())

    assert result.search_kind == "create"
    assert ALREADY_REASON not in json.dumps(result.recipe, sort_keys=True)


def test_the_update_recipe_is_valid() -> None:
    result = compile_recording(
        _update([(PRICE, "19.99")], observation=UPDATE_OBSERVATION), _options()
    )
    assert validation_errors(result.recipe) == []
