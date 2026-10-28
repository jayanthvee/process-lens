"""PL-123 item 2: every step may carry one plain sentence of intent.

The intent is optional: a recipe taught before D-026 still validates, and the runner falls back to
the step's name. A recipe that carries one validates on every step kind, so the compiler can write
it without inventing a new step shape.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

PACKAGE = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PACKAGE / "schema" / "recipe.schema.json"
EXAMPLE_PATH = PACKAGE / "examples" / "create-lead.recipe.json"


def _validator() -> Draft202012Validator:
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _example() -> dict[str, Any]:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def test_an_old_recipe_without_intent_still_validates() -> None:
    recipe = _example()
    assert all("intent" not in step for step in recipe["steps"])
    assert list(_validator().iter_errors(recipe)) == []


def test_every_step_kind_accepts_an_intent() -> None:
    recipe = _example()
    for step in recipe["steps"]:
        step["intent"] = f"what {step['action']} is for"
        # A branch's nested steps are steps too.
        for nested in step.get("then", []) + step.get("else", []):
            nested["intent"] = "what the nested step is for"
    assert list(_validator().iter_errors(recipe)) == []


def test_intent_is_a_string_and_a_blank_one_is_not_a_sentence() -> None:
    recipe = _example()
    recipe["steps"][0]["intent"] = 7
    errors = list(_validator().iter_errors(recipe))
    assert errors, "a non-string intent must be refused"

    recipe = copy.deepcopy(_example())
    recipe["steps"][0]["intent"] = "x" * 501
    assert list(_validator().iter_errors(recipe)), "an intent longer than 500 must be refused"


def test_a_condition_can_carry_the_intent_it_asks_about() -> None:
    """The search question is asked as an intent on the condition (PL-123 item 4)."""
    recipe = _example()
    recipe["steps"][0]["intent"] = "open the customer list"
    # An element condition's selector carries its own intent, because the branch itself may not
    # be a step the person reads.
    recipe["steps"][5]["when"]["element_present"]["intent"] = "the customer named {{row.name}}"
    assert list(_validator().iter_errors(recipe)) == []


@pytest.mark.parametrize("value", ["", " ", "absent"])
def test_a_missing_intent_is_allowed(value: str) -> None:
    recipe = _example()
    if value == "absent":
        recipe["steps"][0].pop("intent", None)
    else:
        recipe["steps"][0]["intent"] = value
    assert list(_validator().iter_errors(recipe)) == []
