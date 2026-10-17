"""PL-031: make the owner's real Odoo quick-create recording replayable.

The recording in ``odoo_quick_create_owner.json`` is a real person's clicks and typing,
sanitized. Each test below encodes one of the six fixes that turned it from "all three rows
parked" into a recipe that compiles cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.compiler import CompileOptions, compile_recording

FIXTURE = Path(__file__).with_name("fixtures") / "odoo_quick_create_owner.json"

# The demo values the popup would send for this recording. The contact name is both the record
# key and the value Odoo copies into the Opportunity field, which is how we detect a page-set fill.
SAMPLE_ROW = {
    "contact": "Probe Person Zero",
    "email": "probe.zero@example.test",
    "phone": "5550000000",
    "expected_revenue": "3000",
}

INPUT_FIELDS = {
    "contact": {"type": "string", "required": True},
    "email": {"type": "string"},
    "phone": {"type": "string"},
    "expected_revenue": {"type": "string"},
}

ORIGIN = "http://localhost:8069"


def load_recording() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make_options(**overrides: Any) -> CompileOptions:
    base: dict[str, Any] = {
        "name": "New CRM lead quick create",
        "when_to_use": "Create a lead in Odoo CRM from the quick-create card.",
        "sample_row": dict(SAMPLE_ROW),
        "input_fields": {k: dict(v) for k, v in INPUT_FIELDS.items()},
        "record_key_column": "contact",
        "allowed_origins": [ORIGIN],
        "include_navigate": False,
    }
    base.update(overrides)
    return CompileOptions(**base)


def compile_fixture(**overrides: Any) -> Any:
    return compile_recording(load_recording(), make_options(**overrides))


# --- Fix 1: drop the Odoo '?' help marker from labels ------------------------


def test_no_question_mark_in_any_step_name_or_rung() -> None:
    """Odoo renders <label>Contact<sup>?</sup></label>; the recording must not keep the '?'."""
    result = compile_fixture()
    for step in result.recipe["steps"]:
        name = step.get("name", "")
        assert "?" not in name, f"step name still contains '?': {name}"
        for rung in step.get("targets", []):
            for key in ("name", "text", "value"):
                text = rung.get(key)
                if isinstance(text, str):
                    assert "?" not in text, f"rung {rung} still contains '?'"


def test_the_contact_column_is_named_contact_not_contact_question() -> None:
    """The derived input column from the Contact field must be ``contact``."""
    result = compile_fixture()
    fields = result.recipe["meta"]["input_schema"]["fields"]
    assert "contact" in fields
    assert "contact?" not in fields


# --- Fix 2: a retried field keeps only its last attempt ----------------------


def test_only_the_last_contact_fill_remains() -> None:
    """The owner fumbled the contact field; the first fill and the clicks between the two
    attempts are folded away, leaving only the final fill."""
    result = compile_fixture()
    contact_fills = [
        step
        for step in result.recipe["steps"]
        if step.get("action") == "fill" and step.get("value", {}).get("column") == "contact"
    ]
    assert len(contact_fills) == 1, contact_fills


def test_fixture_steps_two_through_seven_fold_away() -> None:
    """After folding, the recording is reduced to New, fill Contact, Create option, Email,
    Phone, Expected Revenue, and Add."""
    result = compile_fixture()
    actions = [step["action"] for step in result.recipe["steps"]]
    assert actions == ["click", "fill", "click", "fill", "fill", "fill", "click"]


# --- Fix 3: drop container clicks -------------------------------------------


def test_container_clicks_are_dropped_with_warnings() -> None:
    """Clicks on the card body and on the bare css cell must not become steps."""
    result = compile_fixture()
    names = {step.get("name", "") for step in result.recipe["steps"]}
    assert "Click Contact?OpportunityEmailPhoneExpected Revenue$ 0.00$" not in names
    assert "Click div" not in names
    assert any("dropped container click" in warning for warning in result.warnings)


# --- Fix 4: do not record what the page typed --------------------------------


def test_opportunity_fill_copied_from_contact_is_dropped() -> None:
    """Odoo copies the contact name into Opportunity; that fill has no keystrokes and must be
    treated as page_set, not a step."""
    result = compile_fixture()
    opportunity_fills = [
        step
        for step in result.recipe["steps"]
        if step.get("action") == "fill" and "Opportunity" in step.get("name", "")
    ]
    assert not opportunity_fills
    assert any("the page set that value itself" in warning for warning in result.warnings)


# --- Fix 5: a Create option must also accept an existing record --------------


def test_create_option_has_exact_match_first_then_create_template() -> None:
    """The Create "<contact>" option must resolve an existing record first, then the create
    option. The first rung is the exact column value, the second is the Create template."""
    result = compile_fixture()
    create_step = next(
        step
        for step in result.recipe["steps"]
        if step.get("action") == "click" and "Create" in step.get("name", "")
    )
    role_rungs = [rung for rung in create_step["targets"] if rung.get("by") == "role"]
    assert len(role_rungs) >= 2, role_rungs
    assert role_rungs[0] == {"by": "role", "role": "option", "name": "{{row.contact}}"}
    assert role_rungs[1] == {
        "by": "role",
        "role": "option",
        "name": 'Create "{{row.contact}}"',
    }


# --- Fix 6: a quick-create save must be checkable ----------------------------


def test_add_click_gets_assert_after_from_record_key() -> None:
    """The Add button changes no URL and shows no message; the compiler infers the assertion
    from the record key so the new card's name proves the save."""
    result = compile_fixture(commit_step_ids=["click-15"])
    add_step = next(
        step
        for step in result.recipe["steps"]
        if step.get("action") == "click" and step.get("name") == "Click Add"
    )
    assert add_step.get("commit") is True
    assert add_step.get("assert_after") == {
        "text_visible": "{{row.contact}}",
        "timeout_ms": 8000,
    }
    assert any(
        "checked by the new record's name appearing" in warning for warning in result.warnings
    )


def test_an_email_keyed_quick_create_is_checked_by_the_card_name_not_the_email() -> None:
    """Keyed by email, the save is still checked by the contact: the card never shows the email.

    T1 replayed this fixture on real Odoo keyed by email: the lead was created correctly, but the
    check waited for the email on the new card, never saw it, and parked the row.
    """
    result = compile_fixture(commit_step_ids=["click-15"], record_key_column="email")
    add_step = next(step for step in result.recipe["steps"] if step.get("name") == "Click Add")
    assert add_step.get("assert_after", {}).get("text_visible") == "{{row.contact}}"


def test_the_fixture_compiles_without_422() -> None:
    """With the Add button marked as the commit step, the recording must compile to a valid
    recipe instead of refusing with "no assert_after"."""
    result = compile_fixture(commit_step_ids=["click-15"])
    assert result.recipe is not None
    assert result.recipe["steps"]


# --- Acceptance: the whole cleaned-up recipe ---------------------------------


def test_acceptance_recipe_shape() -> None:
    """Navigate (if any), New, fill Contact, Create/exact option, Email, Phone, Expected Revenue,
    Add with a text_visible assertion from the contact. No more than 8 steps, no '?' anywhere."""
    result = compile_fixture(commit_step_ids=["click-15"])
    steps = result.recipe["steps"]
    assert len(steps) <= 8, [step.get("name") for step in steps]

    expected = [
        ("click", "Click New"),
        ("fill", "Fill Contact"),
        ("click", 'Click Create "Probe Person Zero"'),
        ("fill", "Fill Email"),
        ("fill", "Fill Phone"),
        ("fill", "Fill Expected Revenue"),
        ("click", "Click Add"),
    ]
    for index, (action, name) in enumerate(expected):
        assert steps[index]["action"] == action, steps[index]
        assert steps[index]["name"] == name, steps[index]

    assert steps[-1].get("commit") is True
    assert steps[-1].get("assert_after", {}).get("text_visible") == "{{row.contact}}"

    recipe_text = json.dumps(result.recipe)
    assert "?" not in recipe_text


def _box_fill(seq: int, value: str) -> dict[str, Any]:
    return {
        "seq": seq,
        "kind": "fill",
        "url": f"{ORIGIN}/compose",
        "step": {
            "action": "fill",
            "id": f"fill-{seq}",
            "name": "Fill Recipients",
            "targets": [{"by": "role", "role": "combobox", "name": "Recipients"}],
            "value": {"kind": "constant", "value": value},
        },
    }


def _option_click(seq: int, text: str) -> dict[str, Any]:
    return {
        "seq": seq,
        "kind": "click",
        "url": f"{ORIGIN}/compose",
        "step": {
            "action": "click",
            "id": f"click-{seq}",
            "name": f"Click {text}",
            "targets": [{"by": "role", "role": "option", "name": text}],
        },
    }


def test_two_picked_entries_in_one_box_are_not_a_retry() -> None:
    """Two recipients typed into one box, each picked from its suggestions, both stay.

    The retried-fill fold once read the second recipient as a retry of the first and dropped the
    first pick, so every replayed row chose the box's default recipient (caught by T1's messy e2e).
    """
    events = [
        _box_fill(1, "da"),
        _option_click(2, "dana@example.test"),
        _box_fill(3, "ev"),
        _option_click(4, "eve@example.test"),
    ]
    result = compile_recording(
        events,
        make_options(
            sample_row={"first": "dana@example.test", "second": "eve@example.test"},
            input_fields={"first": {"type": "string"}, "second": {"type": "string"}},
            record_key_column="first",
            commit_step_ids=[],
            duplicate_check=False,
        ),
    )
    clicks = [step for step in result.recipe["steps"] if step.get("action") == "click"]
    assert len(clicks) == 2, result.recipe["steps"]
    assert not any("folded the retried fill" in warning for warning in result.warnings)


def test_an_entry_finished_with_a_button_is_not_a_retry() -> None:
    """bob -> Add -> carol -> Add: two entries, not a retry, even though "Add" shares the label.

    The fold once matched the button's text plus its test id ("Add recipient-add") against the
    field's label "Add recipient", read the Add press as fiddling with the field, and dropped bob.
    """

    def add_click(seq: int) -> dict[str, Any]:
        return {
            "seq": seq,
            "kind": "click",
            "url": f"{ORIGIN}/compose",
            "step": {
                "action": "click",
                "id": f"click-{seq}",
                "name": "Click Add",
                "targets": [
                    {"by": "role", "role": "button", "name": "Add"},
                    {"by": "testid", "value": "recipient-add"},
                ],
            },
        }

    events = [
        _box_fill(1, "bob@example.test"),
        add_click(2),
        _box_fill(3, "carol@example.test"),
        add_click(4),
    ]
    result = compile_recording(
        events,
        make_options(
            sample_row={"first": "bob@example.test", "second": "carol@example.test"},
            input_fields={"first": {"type": "string"}, "second": {"type": "string"}},
            record_key_column="first",
            commit_step_ids=[],
            duplicate_check=False,
        ),
    )
    fills = [step for step in result.recipe["steps"] if step.get("action") == "fill"]
    assert len(fills) == 2, result.recipe["steps"]
    assert not any("folded the retried fill" in warning for warning in result.warnings)
