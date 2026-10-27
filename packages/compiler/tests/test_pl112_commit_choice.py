"""PL-112 item 1: the person's save choice decides which step commits.

A sign-in is recorded as fill, click Continue, and the form's own submit. The browser's submit
would otherwise make Continue a commit step by itself, and a read-only task could never be saved.
When the request names the commit steps -- including an empty list, which is "Nothing gets saved" --
only those steps are commits. A request that omits the field keeps the compiler's earlier rule.
"""

from __future__ import annotations

import pytest

from packages.compiler import CompileError, CompileOptions, compile_recording
from packages.compiler.testing import (
    EMAIL_TARGETS,
    INTEREST_TARGETS,
    SAVE_TARGETS,
    action_event,
)

NOT_NEW_URL = "https://app.example.com/records"


def options(**overrides: object) -> CompileOptions:
    return CompileOptions(
        name="Sign in to the app",
        when_to_use="The account needs a code from its email before it shows its pages.",
        sample_row={"email": "ravi@example.com", "interest": "Data course"},
        input_fields={
            "email": {"type": "string", "required": True},
            "interest": {"type": "string"},
        },
        record_key_column="email",
        **overrides,
    )


def sign_in_recording(
    fill_targets: list[dict[str, object]] | None = None,
    fill_value: str = "ravi@example.com",
) -> list[dict]:
    """Fill a field, click Continue, and let the same form submit: one recorded sign-in."""
    events = [
        action_event(1, "fill", "fill", fill_targets or EMAIL_TARGETS, value=fill_value),
        action_event(
            2,
            "click",
            "click",
            SAVE_TARGETS,
            name="Click Continue",
            url=NOT_NEW_URL,
        ),
        action_event(3, "submit", "click", SAVE_TARGETS, url=NOT_NEW_URL),
    ]
    # The fill is on the page the click runs on; no navigation and no confirmation were observed.
    events[0]["url"] = NOT_NEW_URL
    return events


def test_nothing_gets_saved_compiles_a_read_only_recipe() -> None:
    """commit_step_ids=[] means no step is a commit, even the click the submit folded into."""
    result = compile_recording(sign_in_recording(), options(commit_step_ids=[]))

    assert [step for step in result.recipe["steps"] if step.get("commit")] == []
    assert all("assert_after" not in step for step in result.recipe["steps"])


def test_an_omitted_choice_keeps_the_compilers_own_commit_rule() -> None:
    """An older caller sends no choice at all, so the submit still marks Continue a commit."""
    with pytest.raises(CompileError) as refusal:
        compile_recording(sign_in_recording(), options())

    assert "a commit step must carry an assertion" in str(refusal.value)


def test_a_named_click_is_the_commit_step_and_still_needs_an_assertion() -> None:
    """Naming Continue makes it the commit; with no evidence, the refusal is unchanged."""
    recording = sign_in_recording(INTEREST_TARGETS, fill_value="Data course")

    with pytest.raises(CompileError) as refusal:
        compile_recording(
            recording,
            options(commit_step_ids=["click-2"]),
        )

    assert "a commit step must carry an assertion" in str(refusal.value)


def test_a_named_click_with_evidence_is_the_one_commit_step() -> None:
    """The named click carries commit and its assertion; the submit is not a second step."""
    recording = sign_in_recording()
    recording[2]["observation"] = {"text_visible": "Welcome"}

    result = compile_recording(recording, options(commit_step_ids=["click-2"]))

    commits = [step for step in result.recipe["steps"] if step.get("commit")]
    assert len(commits) == 1
    assert commits[0]["name"] == "Click Continue"
    assert commits[0]["assert_after"] == {"text_visible": "Welcome", "timeout_ms": 8000}
    assert len([step for step in result.recipe["steps"] if step["action"] == "click"]) == 1
