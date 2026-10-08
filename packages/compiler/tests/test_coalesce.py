"""Folding fragmented events: keystrokes into fills, click/submit pairs into one step."""

from __future__ import annotations

from packages.compiler.coalesce import coalesce_fills, collapse_submits
from packages.compiler.normalize import parse_events
from packages.compiler.testing import EMAIL_TARGETS, INTEREST_TARGETS, SAVE_TARGETS, action_event


def events(*raw: dict) -> list:
    parsed, _ = parse_events(list(raw))
    return parsed


def test_adjacent_fills_on_one_field_become_a_single_fill() -> None:
    folded, warnings = coalesce_fills(
        events(
            action_event(1, "fill", "fill", EMAIL_TARGETS, value="r"),
            action_event(2, "fill", "fill", EMAIL_TARGETS, value="ra"),
            action_event(3, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        )
    )
    assert len(folded) == 1
    assert folded[0].step["value"]["value"] == "ravi@example.com"
    assert any("merged 2" in warning for warning in warnings)


def test_fills_on_different_fields_are_not_merged() -> None:
    folded, _ = coalesce_fills(
        events(
            action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
            action_event(2, "fill", "fill", INTEREST_TARGETS, value="Data course"),
        )
    )
    assert len(folded) == 2


def test_a_fill_after_another_action_is_not_merged_across_it() -> None:
    folded, _ = coalesce_fills(
        events(
            action_event(1, "fill", "fill", EMAIL_TARGETS, value="one"),
            action_event(2, "click", "click", SAVE_TARGETS),
            action_event(3, "fill", "fill", EMAIL_TARGETS, value="two"),
        )
    )
    assert [event.kind for event in folded] == ["fill", "click", "fill"]


def test_non_adjacent_fills_on_one_field_stay_separate() -> None:
    folded, _ = coalesce_fills(
        events(
            action_event(1, "fill", "fill", EMAIL_TARGETS, value="one"),
            action_event(2, "select", "select", INTEREST_TARGETS, value="Data course"),
            action_event(3, "fill", "fill", EMAIL_TARGETS, value="two"),
        )
    )
    assert [event.kind for event in folded] == ["fill", "select", "fill"]


def test_a_click_and_its_submit_become_one_commit_step() -> None:
    folded, warnings = collapse_submits(
        events(
            action_event(1, "click", "click", SAVE_TARGETS, name="Click Save"),
            action_event(2, "submit", "click", SAVE_TARGETS),
        )
    )
    assert len(folded) == 1
    assert folded[0].kind == "click"
    assert folded[0].commit is True
    assert any("folded 1" in warning for warning in warnings)


def test_the_submit_contributes_its_observation_to_the_pair() -> None:
    folded, _ = collapse_submits(
        events(
            action_event(1, "click", "click", SAVE_TARGETS),
            action_event(2, "submit", "click", SAVE_TARGETS, observation={"text_visible": "Saved"}),
        )
    )
    assert folded[0].observation == {"text_visible": "Saved"}


def test_a_click_on_another_control_is_not_folded_into_a_submit() -> None:
    folded, warnings = collapse_submits(
        events(
            action_event(1, "click", "click", INTEREST_TARGETS),
            action_event(2, "submit", "click", SAVE_TARGETS),
        )
    )
    assert [event.kind for event in folded] == ["click", "click"]
    assert folded[0].commit is False
    assert folded[1].commit is True
    assert not any("folded" in warning for warning in warnings)


def test_a_submit_without_a_click_becomes_a_commit_step() -> None:
    folded, _ = collapse_submits(
        events(action_event(1, "submit", "click", SAVE_TARGETS, name="Sign in"))
    )
    assert len(folded) == 1
    assert folded[0].kind == "click"
    assert folded[0].commit is True


def test_a_submit_with_no_control_is_dropped_with_a_warning() -> None:
    folded, warnings = collapse_submits(
        events(
            {"seq": 1, "kind": "submit", "url": "https://app.example.com/records/new", "step": None}
        )
    )
    assert folded == []
    assert any("no identifiable control" in warning for warning in warnings)


def test_fills_are_coalesced_before_submits_collapse() -> None:
    """The full order of the two folds, as the compiler runs them."""
    parsed = events(
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="rav"),
        action_event(2, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(3, "click", "click", SAVE_TARGETS),
        action_event(4, "submit", "click", SAVE_TARGETS),
    )
    folded, _ = coalesce_fills(parsed)
    folded, _ = collapse_submits(folded)
    assert len(folded) == 2
    assert folded[0].step["value"]["value"] == "ravi@example.com"
    assert folded[1].commit is True
