"""PL-018: the press action, its two refusals, the chip fold and weak observations.

The recorder's half of PL-018 rides the event envelope, so most of these tests build raw events:
``chip``, ``autocomplete`` and ``supersedes`` describe how an event came about, and a step may
never carry them.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.assertions import condition_from_observation
from packages.compiler.errors import CompileError
from packages.compiler.testing import DEFAULT_URL, EMAIL_TARGETS, SAVE_TARGETS, action_event


def press_event(
    seq: int, key: str, targets: list[dict[str, Any]] = EMAIL_TARGETS
) -> dict[str, Any]:
    """A recorded key press: a step with a key, and no commit."""
    return {
        "seq": seq,
        "kind": "press",
        "url": DEFAULT_URL,
        "step": {"id": f"press-{seq}", "action": "press", "targets": targets, "key": key},
    }


def chip_event(
    seq: int,
    value: str,
    *,
    supersedes: str | None = None,
    autocomplete: bool = True,
    targets: list[dict[str, Any]] = EMAIL_TARGETS,
) -> dict[str, Any]:
    """The fill a chip-add produced, with the relationship on the envelope."""
    event: dict[str, Any] = {
        "seq": seq,
        "kind": "fill",
        "url": DEFAULT_URL,
        "step": {
            "id": f"fill-{seq}",
            "action": "fill",
            "targets": targets,
            "value": {"kind": "constant", "value": value},
        },
        "chip": True,
    }
    if autocomplete:
        event["autocomplete"] = True
    if supersedes is not None:
        event["supersedes"] = supersedes
    return event


def unrecordable_event(seq: int) -> dict[str, Any]:
    """The recorder's mark that something happened it could not describe as a step."""
    return {"seq": seq, "kind": "unrecordable", "url": DEFAULT_URL, "reason": "chip-removal"}


def actions(recipe: dict[str, Any]) -> list[str]:
    return [step["action"] for step in recipe["steps"]]


# --- R1: a key press is never the committing step (§3.8) -------------------


def test_a_recording_that_ends_with_a_key_send_is_refused(options: CompileOptions) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(2, "click", "click", SAVE_TARGETS, name="Click Send"),
        press_event(3, "Control+Enter", SAVE_TARGETS),
    ]
    with pytest.raises(CompileError) as caught:
        compile_recording(recording, options)
    assert "The save or send was done with a key." in str(caught.value)


def test_a_press_named_as_the_commit_step_is_refused(options: CompileOptions) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        press_event(2, "Escape"),
        action_event(3, "click", "click", SAVE_TARGETS, name="Click Send"),
    ]
    with pytest.raises(CompileError) as caught:
        compile_recording(recording, replace(options, commit_step_ids={"press-2"}))
    assert "The save or send was done with a key." in str(caught.value)


# --- the press/submit fold (§3.6) and a press that is not a send -----------


def test_enter_then_submit_compiles_to_one_commit_click(options: CompileOptions) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        press_event(2, "Enter"),
        action_event(
            3,
            "submit",
            "click",
            SAVE_TARGETS,
            observation={"text_visible": "Record created"},
        ),
    ]
    result = compile_recording(recording, options)
    assert actions(result.recipe) == ["navigate", "fill", "click"]
    commit = next(step for step in result.recipe["steps"] if step.get("commit") is True)
    assert commit["action"] == "click"
    assert any("press/submit" in warning for warning in result.warnings)


def test_a_press_that_sends_nothing_stays_a_step(options: CompileOptions, errors: Any) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        press_event(2, "Escape"),
        action_event(3, "click", "click", SAVE_TARGETS, name="Click Save"),
    ]
    result = compile_recording(recording, options)
    assert actions(result.recipe) == ["navigate", "fill", "press", "click"]
    press = result.recipe["steps"][2]
    assert press["key"] == "Escape"
    assert "commit" not in press
    assert "assert_after" not in press
    assert errors(result.recipe) == []


def test_other_keys_still_compile_to_a_press_step(options: CompileOptions, errors: Any) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        press_event(2, "Tab"),
        action_event(3, "click", "click", SAVE_TARGETS, name="Click Save"),
    ]
    result = compile_recording(recording, options)
    assert errors(result.recipe) == []
    assert actions(result.recipe) == ["navigate", "fill", "press", "click"]
    assert result.recipe["steps"][2]["key"] == "Tab"


# --- R2: a chip removal that cannot be replayed (§4.4) ---------------------


def test_an_unrecordable_event_refuses_the_compile(options: CompileOptions) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        chip_event(2, "a@example.test"),
        unrecordable_event(3),
        action_event(4, "click", "click", SAVE_TARGETS, name="Click Send"),
    ]
    with pytest.raises(CompileError) as caught:
        compile_recording(recording, options)
    assert "A recipient was removed in a way that can't be replayed" in str(caught.value)


# --- the chip fold (§4.1.2) ----------------------------------------------


def test_a_chip_fill_folds_the_prefix_and_the_accept_into_one_step(
    options: CompileOptions,
) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ma", step_id="fill-1"),
        press_event(2, "Enter"),
        chip_event(3, "ma@example.test", supersedes="fill-1"),
    ]
    result = compile_recording(recording, options)
    fills = [step for step in result.recipe["steps"] if step["action"] == "fill"]
    assert len(fills) == 1
    assert fills[0]["value"] == {"kind": "constant", "value": "ma@example.test"}
    assert "press" not in actions(result.recipe)
    assert any("folded the fill superseded" in warning for warning in result.warnings)


def test_a_chip_value_that_does_not_contain_the_typed_text_does_not_fold(
    options: CompileOptions,
) -> None:
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ma", step_id="fill-1"),
        press_event(2, "Enter"),
        chip_event(3, "mel@example.test", supersedes="fill-1"),
    ]
    result = compile_recording(recording, options)
    fills = [step for step in result.recipe["steps"] if step["action"] == "fill"]
    assert [step["value"]["value"] for step in fills] == ["ma", "mel@example.test"]
    assert "press" in actions(result.recipe)
    assert any("does not contain the typed text" in warning for warning in result.warnings)


def test_a_chip_fill_with_nothing_to_consume_stays_one_fill(options: CompileOptions) -> None:
    result = compile_recording([chip_event(1, "ravi@example.com")], options)
    assert actions(result.recipe) == ["navigate", "fill"]
    assert not any("folded the fill superseded" in warning for warning in result.warnings)


def test_an_option_click_is_folded_into_the_chip_too(options: CompileOptions) -> None:
    """The ticket's "by click, key or paste": the option click is the accept, not a step.

    Left unfolded, the replay would accept the suggestion twice: once through the executor's
    containment completion and once through the recorded click, whose option no longer exists.
    """
    option_targets = [
        {"by": "role", "role": "option", "name": "Mary An <ma@example.test>"},
        {"by": "css", "selector": "#suggestions > span"},
    ]
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ma", step_id="fill-1"),
        action_event(2, "click", "click", option_targets, name="Click Mary An", step_id="click-2"),
        chip_event(3, "ma@example.test", supersedes="fill-1"),
    ]
    result = compile_recording(recording, options)
    assert actions(result.recipe) == ["navigate", "fill"]
    fills = [step for step in result.recipe["steps"] if step["action"] == "fill"]
    assert fills[0]["value"] == {"kind": "constant", "value": "ma@example.test"}
    assert any("folded the fill superseded" in warning for warning in result.warnings)


def test_an_ordinary_click_between_the_prefix_and_the_chip_is_not_folded(
    options: CompileOptions,
) -> None:
    """Only a click that names a suggestion option is the accept; anything else stands."""
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ma", step_id="fill-1"),
        action_event(2, "click", "click", SAVE_TARGETS, name="Click Save", step_id="click-2"),
        chip_event(3, "ma@example.test", supersedes="fill-1"),
    ]
    result = compile_recording(recording, options)
    assert actions(result.recipe) == ["navigate", "click", "fill"]


# --- weak observations (§8.2) ---------------------------------------------


@pytest.mark.parametrize(
    "observation",
    [
        {"text_visible": "Loading\u2026"},
        {"text_visible": "Saving..."},
        {"text_visible": "\u2026"},
        {"text_visible": "Saving your changes", "busy": True},
        {"text_visible": "   "},
        {"text_visible": "Please wait"},
    ],
)
def test_a_weak_observation_is_not_an_assertion(observation: dict[str, Any]) -> None:
    assert condition_from_observation(observation) is None


def test_a_real_confirmation_is_still_an_assertion() -> None:
    condition = condition_from_observation({"text_visible": "Record created"})
    assert condition is not None
    assert condition["text_visible"] == "Record created"


def test_a_commit_with_only_a_weak_observation_is_refused(options: CompileOptions) -> None:
    # PL-059: a save on a "/new" page now has an address check; this fixture stays on a page
    # without that signal, so a weak observation is still the only evidence and is refused.
    form = "https://app.example.com/records"
    recording = [
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com", url=form),
        action_event(
            2,
            "click",
            "click",
            SAVE_TARGETS,
            name="Click Save",
            url=form,
            observation={"text_visible": "Loading\u2026"},
        ),
    ]
    with pytest.raises(CompileError, match="must carry an assertion"):
        compile_recording(recording, replace(options, commit_step_ids={"click-2"}))
