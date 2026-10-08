"""The recipe contract: the schema accepts what it should and rejects what it must.

Unit tests for the format live next to the package that owns it. They read the shipped
schema and the shipped example, so a change to either that breaks the contract fails here.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

PACKAGE = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PACKAGE / "schema" / "recipe.schema.json"
EXAMPLE_PATH = PACKAGE / "examples" / "create-lead.recipe.json"

# The step types in the design's step table. The schema must cover all of them.
ACTIONS = (
    "navigate",
    "fill",
    "select",
    "click",
    "upload_file",
    "read_value",
    "wait_for",
    "assert",
    "download",
    "branch",
    "for_each",
    "transform",
    "match",
    "aggregate",
    "classify",
    "extract",
    "approval_gate",
    "end_record",
)

TARGET = [{"by": "role", "role": "button", "name": "Save"}]
CONDITION = {"text_visible": "Lead created"}

MINIMAL_BODY: dict[str, dict[str, Any]] = {
    "navigate": {"url": "https://crm.example.com/customers"},
    "fill": {"targets": TARGET, "value": {"kind": "input", "column": "email"}},
    "select": {"targets": TARGET, "value": {"kind": "input", "column": "interest"}},
    "click": {"targets": TARGET},
    "upload_file": {"targets": TARGET, "value": {"kind": "input", "column": "cv"}},
    "read_value": {"targets": TARGET, "as": "current_price"},
    "wait_for": {"condition": CONDITION, "timeout_ms": 5000},
    "assert": {"condition": CONDITION},
    "download": {"targets": TARGET, "as": "report"},
    "branch": {
        "when": CONDITION,
        "then": [{"id": "stop", "action": "end_record", "outcome": "skipped", "reason": "found"}],
    },
    "for_each": {
        "over": {"kind": "page", "variable": "lines"},
        "as": "line",
        "steps": [
            {"id": "note", "action": "end_record", "outcome": "parked", "reason": "line {{line}}"}
        ],
    },
    "transform": {
        "as": "first_name",
        "op": "first_word",
        "input": {"kind": "input", "column": "full_name"},
    },
    "match": {
        "as": "same_amount",
        "left": {"kind": "page", "variable": "invoiced"},
        "right": {"kind": "input", "column": "amount"},
        "compare": "amount_within",
        "tolerance": 0.01,
    },
    "aggregate": {"as": "total", "op": "sum", "over": {"kind": "page", "variable": "lines"}},
    "classify": {
        "as": "category",
        "input": {"kind": "input", "column": "subject"},
        "categories": ["billing", "delivery"],
    },
    "extract": {
        "document": {"kind": "input", "column": "cv"},
        "fields": {"email": {"type": "string"}, "start_date": {"type": "date"}},
    },
    "approval_gate": {"reason": "Check the values before submitting."},
    "end_record": {"outcome": "verified", "reason": "Lead created."},
}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def example() -> dict[str, Any]:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema)


def messages(validator: Draft202012Validator, document: Any) -> list[str]:
    """Every validation message, including the reasons nested inside a failed anyOf/oneOf."""

    def walk(error: ValidationError, found: list[str]) -> None:
        found.append(error.message)
        for child in error.context:
            walk(child, found)

    found: list[str] = []
    for error in validator.iter_errors(document):
        walk(error, found)
    return found


def step(action: str, **overrides: Any) -> dict[str, Any]:
    """A minimal valid step of the given action, with optional changes."""
    return {"id": "a-step", "action": action, **MINIMAL_BODY[action], **overrides}


def recipe(**meta_overrides: Any) -> dict[str, Any]:
    """A minimal valid recipe, with optional meta changes."""
    meta: dict[str, Any] = {
        "name": "Create a lead",
        "when_to_use": "Inquiries have to exist in the CRM exactly once.",
        "input_schema": {"fields": {"email": {"type": "string", "required": True}}},
        "record_key": "{{row.email | lower}}",
        "idempotency_key": "{{row.email | lower}}",
        "approval_policy": {"commit": "first_record"},
        "allowed_origins": ["https://crm.example.com"],
    }
    meta.update(meta_overrides)
    return {"format_version": 1, "meta": meta, "steps": [step("navigate")]}


# --- the schema and the example -------------------------------------------


def test_the_schema_is_a_valid_json_schema(schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_the_shipped_example_validates(
    validator: Draft202012Validator, example: dict[str, Any]
) -> None:
    assert messages(validator, example) == []


def test_the_example_exercises_a_commit_step(example: dict[str, Any]) -> None:
    commits = [item for item in example["steps"] if item.get("commit") is True]
    assert commits, "the example should show what a commit step looks like"
    for item in commits:
        assert "assert_after" in item


def test_a_minimal_recipe_validates(validator: Draft202012Validator) -> None:
    assert messages(validator, recipe()) == []


# --- every step type ------------------------------------------------------


@pytest.mark.parametrize("action", ACTIONS)
def test_every_design_step_type_has_a_working_shape(
    validator: Draft202012Validator, action: str
) -> None:
    document = recipe()
    document["steps"] = [step(action)]
    assert messages(validator, document) == []


def test_the_action_list_matches_the_schema(validator: Draft202012Validator) -> None:
    """Every documented action is one of the schema's branches, and nothing else is."""
    branches = validator.schema["$defs"]["step"]["allOf"][1]["oneOf"]
    declared = {
        validator.schema["$defs"][branch["$ref"].rsplit("/", 1)[-1]]["properties"]["action"][
            "const"
        ]
        for branch in branches
    }
    assert declared == set(ACTIONS)


def test_an_unknown_action_is_rejected(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [dict(step("navigate"), action="teleport")]
    assert any(
        "is not valid under any of the given schemas" in m for m in messages(validator, document)
    )


@pytest.mark.parametrize(
    ("action", "missing"),
    [
        ("navigate", "url"),
        ("fill", "targets"),
        ("fill", "value"),
        ("select", "value"),
        ("click", "targets"),
        ("upload_file", "value"),
        ("read_value", "as"),
        ("wait_for", "condition"),
        ("wait_for", "timeout_ms"),
        ("assert", "condition"),
        ("download", "as"),
        ("branch", "when"),
        ("branch", "then"),
        ("for_each", "over"),
        ("for_each", "steps"),
        ("transform", "op"),
        ("transform", "input"),
        ("match", "compare"),
        ("aggregate", "op"),
        ("classify", "categories"),
        ("extract", "fields"),
        ("approval_gate", "reason"),
        ("end_record", "outcome"),
        ("end_record", "reason"),
    ],
)
def test_each_action_requires_its_own_fields(
    validator: Draft202012Validator, action: str, missing: str
) -> None:
    document = recipe()
    body = dict(MINIMAL_BODY[action])
    body.pop(missing)
    document["steps"] = [{"id": "a-step", "action": action, **body}]
    assert any(
        f"'{missing}' is a required property" in message
        for message in messages(validator, document)
    )


@pytest.mark.parametrize(
    ("action", "field", "value"),
    [
        ("click", "value", {"kind": "input", "column": "email"}),
        ("end_record", "targets", TARGET),
        ("transform", "targets", TARGET),
    ],
)
def test_fields_of_another_action_are_rejected(
    validator: Draft202012Validator, action: str, field: str, value: Any
) -> None:
    document = recipe()
    document["steps"] = [step(action, **{field: value})]
    assert any(
        f"Additional properties are not allowed ('{field}' was unexpected)" in message
        for message in messages(validator, document)
    )


def test_a_step_without_an_id_is_rejected(validator: Draft202012Validator) -> None:
    document = recipe()
    body = dict(MINIMAL_BODY["navigate"])
    document["steps"] = [{"action": "navigate", **body}]
    assert any(
        "'id' is a required property" in message for message in messages(validator, document)
    )


def test_a_step_id_must_be_a_usable_identifier(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [step("navigate", id="Open the customers page!")]
    assert any("does not match" in message for message in messages(validator, document))


# --- commit steps carry an assertion ---------------------------------------


def test_a_commit_step_with_an_assertion_is_accepted(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [step("click", commit=True, assert_after=CONDITION)]
    assert messages(validator, document) == []


def test_a_commit_step_without_an_assertion_is_rejected(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [step("click", commit=True)]
    assert "'assert_after' is a required property" in messages(validator, document)


def test_the_assertion_rule_applies_to_nested_steps(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [
        step(
            "branch",
            when=CONDITION,
            then=[{"id": "nested-save", "action": "click", "targets": TARGET, "commit": True}],
        )
    ]
    assert "'assert_after' is a required property" in messages(validator, document)


def test_a_commit_step_with_an_empty_assertion_is_rejected(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [step("click", commit=True, assert_after={"timeout_ms": 5000})]
    assert any("is not valid" in message for message in messages(validator, document))


@pytest.mark.parametrize("action", ["fill", "select", "click", "upload_file"])
def test_the_steps_that_can_write_may_commit(validator: Draft202012Validator, action: str) -> None:
    document = recipe()
    document["steps"] = [step(action, commit=True, assert_after=CONDITION)]
    assert messages(validator, document) == []


@pytest.mark.parametrize(
    "action",
    ["navigate", "read_value", "wait_for", "assert", "download", "end_record", "transform"],
)
def test_a_step_that_writes_nothing_cannot_commit(
    validator: Draft202012Validator, action: str
) -> None:
    document = recipe()
    document["steps"] = [step(action, commit=True, assert_after=CONDITION)]
    assert any(
        "unexpected" in message and "commit" in message for message in messages(validator, document)
    ), action


# --- meta -----------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "when_to_use",
        "input_schema",
        "record_key",
        "idempotency_key",
        "approval_policy",
        "allowed_origins",
    ],
)
def test_every_meta_field_is_required(validator: Draft202012Validator, field: str) -> None:
    document = recipe()
    del document["meta"][field]
    assert any(
        f"'{field}' is a required property" in message for message in messages(validator, document)
    )


def test_the_format_version_is_pinned(validator: Draft202012Validator) -> None:
    document = recipe()
    document["format_version"] = 2
    assert any("1 was expected" in message for message in messages(validator, document))


def test_an_unknown_top_level_key_is_rejected(validator: Draft202012Validator) -> None:
    document = recipe()
    document["notes"] = "hello"
    assert any(
        "Additional properties are not allowed" in message
        for message in messages(validator, document)
    )


def test_allowed_origins_must_be_origins(validator: Draft202012Validator) -> None:
    document = recipe(allowed_origins=["https://crm.example.com/customers"])
    assert any("does not match" in message for message in messages(validator, document))


def test_allowed_origins_cannot_be_empty(validator: Draft202012Validator) -> None:
    document = recipe(allowed_origins=[])
    assert any("should be non-empty" in message for message in messages(validator, document))


@pytest.mark.parametrize("policy", ["first_record", "every_record", "none"])
def test_the_simple_approval_policies_are_accepted(
    validator: Draft202012Validator, policy: str
) -> None:
    document = recipe(approval_policy={"commit": policy})
    assert messages(validator, document) == []


def test_first_n_needs_a_count(validator: Draft202012Validator) -> None:
    document = recipe(approval_policy={"commit": "first_n"})
    assert "'n' is a required property" in messages(validator, document)

    document = recipe(approval_policy={"commit": "first_n", "n": 3})
    assert messages(validator, document) == []


def test_a_precheck_rule_can_only_end_a_record_the_way_the_ledger_allows(
    validator: Draft202012Validator,
) -> None:
    for outcome in ("skip", "park", "fail"):
        document = recipe()
        document["meta"]["input_schema"]["rules"] = [
            {"when": {"missing": "email"}, "then": outcome, "reason": "No email."}
        ]
        assert messages(validator, document) == [], outcome

    document = recipe()
    document["meta"]["input_schema"]["rules"] = [
        {"when": {"missing": "email"}, "then": "ignore", "reason": "No email."}
    ]
    assert any("'ignore' is not one of" in message for message in messages(validator, document))


def test_input_field_types_are_known(validator: Draft202012Validator) -> None:
    document = recipe()
    document["meta"]["input_schema"]["fields"]["cv"] = {"type": "document"}
    assert messages(validator, document) == []

    document["meta"]["input_schema"]["fields"]["cv"] = {"type": "email"}
    assert any("is not one of" in message for message in messages(validator, document))


# --- targets and the ladder -----------------------------------------------


@pytest.mark.parametrize(
    "strategy",
    [
        {"by": "role", "role": "textbox", "name": "Email"},
        {"by": "testid", "value": "lead-email"},
        {"by": "label", "text": "Email"},
        {"by": "css", "selector": "#lead-email"},
        {"by": "text_fuzzy", "value": "Email"},
    ],
)
def test_each_ladder_rung_is_accepted(
    validator: Draft202012Validator, strategy: dict[str, Any]
) -> None:
    document = recipe()
    document["steps"] = [step("fill", targets=[strategy])]
    assert messages(validator, document) == []


def test_an_unknown_rung_is_rejected(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [step("click", targets=[{"by": "xpath", "selector": "//button"}])]
    assert any(
        "is not valid under any of the given schemas" in message
        for message in messages(validator, document)
    )


@pytest.mark.parametrize(
    ("strategy", "missing"),
    [
        ({"by": "role", "role": "button"}, "name"),
        ({"by": "role", "name": "Save"}, "role"),
        ({"by": "css"}, "selector"),
        ({"by": "testid"}, "value"),
        ({"by": "label"}, "text"),
    ],
)
def test_a_rung_requires_its_own_fields(
    validator: Draft202012Validator, strategy: dict[str, Any], missing: str
) -> None:
    document = recipe()
    document["steps"] = [step("click", targets=[strategy])]
    assert any(
        f"'{missing}' is a required property" in message
        for message in messages(validator, document)
    )


def test_a_ladder_cannot_repeat_a_rung(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [step("click", targets=[TARGET[0], copy.deepcopy(TARGET[0])])]
    assert any("non-unique" in message for message in messages(validator, document))


def test_a_ladder_is_bounded_and_not_empty(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [step("click", targets=[])]
    assert any("should be non-empty" in message for message in messages(validator, document))

    six_rungs = [{"by": "role", "role": f"role{index}", "name": "Save"} for index in range(6)]
    document["steps"] = [step("click", targets=six_rungs)]
    assert any("is too long" in message for message in messages(validator, document))


def test_a_frame_path_is_either_a_position_or_a_url(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [
        step("click", frame=[{"by": "index", "index": 0}, {"by": "url", "url_matches": "/embed/"}])
    ]
    assert messages(validator, document) == []

    document["steps"] = [step("click", frame=[{"by": "name", "name": "content"}])]
    assert any(
        "is not valid under any of the given schemas" in message
        for message in messages(validator, document)
    )


# --- conditions and values -------------------------------------------------


def test_a_condition_needs_something_to_check(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [step("wait_for", condition={"timeout_ms": 5000})]
    assert any(
        "is not valid under any of the given schemas" in message
        for message in messages(validator, document)
    )


def test_the_condition_kinds_are_accepted(validator: Draft202012Validator) -> None:
    conditions = [
        {"text_visible": "Lead created"},
        {"text_absent": "Lead created"},
        {"url_matches": "/leads/"},
        {"element_present": {"targets": TARGET}},
        {"element_absent": {"targets": TARGET}},
        {
            "value_equals": {
                "selector": {"targets": TARGET},
                "value": {"kind": "constant", "value": "1"},
            }
        },
    ]
    for condition in conditions:
        document = recipe()
        document["steps"] = [step("assert", condition=condition)]
        assert messages(validator, document) == [], condition


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "input", "column": "email"},
        {"kind": "constant", "value": "none"},
        {"kind": "constant", "value": 42},
        {
            "kind": "rule_table",
            "table": "interest_to_option",
            "key": {"kind": "input", "column": "interest"},
        },
        {"kind": "page", "variable": "current_price"},
        {"kind": "document", "field": "start_date"},
        {"kind": "ai_text", "template": "Summarize {{row.notes}}", "requires_approval": True},
    ],
)
def test_every_value_source_is_accepted(
    validator: Draft202012Validator, source: dict[str, Any]
) -> None:
    document = recipe()
    document["variables"] = {"a_value": {"source": source}}
    assert messages(validator, document) == []


def test_an_unknown_value_source_is_rejected(validator: Draft202012Validator) -> None:
    document = recipe()
    document["variables"] = {"a_value": {"source": {"kind": "guess", "column": "email"}}}
    assert any(
        "is not valid under any of the given schemas" in message
        for message in messages(validator, document)
    )


def test_ai_generated_text_always_declares_that_it_needs_approval(
    validator: Draft202012Validator,
) -> None:
    document = recipe()
    document["variables"] = {"summary": {"source": {"kind": "ai_text", "template": "Summary"}}}
    assert any(
        "'requires_approval' is a required property" in message
        for message in messages(validator, document)
    )

    document["variables"] = {
        "summary": {
            "source": {"kind": "ai_text", "template": "Summary", "requires_approval": False}
        }
    }
    assert any("True was expected" in message for message in messages(validator, document))


def test_amount_matching_needs_a_tolerance(validator: Draft202012Validator) -> None:
    body = dict(MINIMAL_BODY["match"])
    body.pop("tolerance")
    document = recipe()
    document["steps"] = [{"id": "a-step", "action": "match", **body}]
    assert "'tolerance' is a required property" in messages(validator, document)

    document = recipe()
    document["steps"] = [step("match", compare="amount_within", tolerance=0.01)]
    assert messages(validator, document) == []


def test_a_transform_is_a_name_not_code(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [
        step(
            "transform",
            op="__import__('os').system('rm -rf /')",
            input={"kind": "constant", "value": "x"},
        )
    ]
    assert any("does not match" in message for message in messages(validator, document))


# --- nesting --------------------------------------------------------------


def test_a_bad_step_inside_a_branch_is_rejected(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [
        step("branch", when=CONDITION, then=[{"id": "nested", "action": "teleport"}])
    ]
    assert any(
        "is not valid under any of the given schemas" in message
        for message in messages(validator, document)
    )


def test_a_step_list_cannot_be_empty(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = [step("branch", when=CONDITION, then=[])]
    assert any("should be non-empty" in message for message in messages(validator, document))


def test_a_recipe_needs_at_least_one_step(validator: Draft202012Validator) -> None:
    document = recipe()
    document["steps"] = []
    assert any("should be non-empty" in message for message in messages(validator, document))
