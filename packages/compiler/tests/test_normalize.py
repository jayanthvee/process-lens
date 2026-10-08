"""Reading a raw stream: keep the actions, drop the noise, report what happened."""

from __future__ import annotations

import pytest

from packages.compiler.errors import CompileError
from packages.compiler.normalize import parse_events
from packages.compiler.testing import EMAIL_TARGETS, action_event, noise_event


def test_keeps_the_actionable_kinds_in_order() -> None:
    events, warnings = parse_events(
        [
            action_event(1, "click", "click", EMAIL_TARGETS),
            action_event(2, "fill", "fill", EMAIL_TARGETS, value="a"),
            action_event(3, "select", "select", EMAIL_TARGETS, value="b"),
            action_event(4, "submit", "click", EMAIL_TARGETS),
        ]
    )
    assert [event.kind for event in events] == ["click", "fill", "select", "submit"]
    assert warnings == []


@pytest.mark.parametrize("kind", ["focus", "blur", "scroll", "mousemove", "keydown"])
def test_drops_events_that_carry_no_intent(kind: str) -> None:
    events, warnings = parse_events(
        [noise_event(1, kind), action_event(2, "click", "click", EMAIL_TARGETS)]
    )
    assert [event.kind for event in events] == ["click"]
    assert any(kind in warning for warning in warnings)


def test_counts_dropped_noise_by_kind() -> None:
    events, warnings = parse_events(
        [noise_event(1, "focus"), noise_event(2, "focus"), noise_event(3, "scroll")]
    )
    assert events == []
    assert "dropped 2 'focus' event(s) as noise" in warnings
    assert "dropped 1 'scroll' event(s) as noise" in warnings


def test_accepts_an_object_that_carries_the_events() -> None:
    events, _ = parse_events({"events": [action_event(1, "click", "click", EMAIL_TARGETS)]})
    assert len(events) == 1


def test_orders_events_by_their_sequence_number() -> None:
    events, _ = parse_events(
        [
            action_event(9, "click", "click", EMAIL_TARGETS),
            action_event(2, "click", "click", EMAIL_TARGETS),
        ]
    )
    assert [event.seq for event in events] == [2, 9]


def test_skips_entries_that_are_not_objects() -> None:
    events, warnings = parse_events([action_event(1, "click", "click", EMAIL_TARGETS), "nonsense"])
    assert len(events) == 1
    assert any("not an object" in warning for warning in warnings)


def test_skips_events_with_no_kind() -> None:
    events, warnings = parse_events([{"seq": 1, "url": "https://app.example.com"}])
    assert events == []
    assert any("no kind" in warning for warning in warnings)


def test_a_malformed_step_becomes_an_empty_step() -> None:
    events, warnings = parse_events([{"seq": 1, "kind": "click", "step": "not-an-object"}])
    assert events[0].step is None
    assert any("malformed step" in warning for warning in warnings)


def test_a_malformed_observation_is_ignored() -> None:
    events, warnings = parse_events([{"seq": 1, "kind": "click", "step": None, "observation": 7}])
    assert events[0].observation is None
    assert any("malformed observation" in warning for warning in warnings)


def test_normalizes_the_kind_case() -> None:
    events, _ = parse_events([{"seq": 1, "kind": "CLICK", "step": None}])
    assert events[0].kind == "click"


def test_a_recording_that_is_not_a_list_is_rejected() -> None:
    with pytest.raises(CompileError):
        parse_events("https://app.example.com")


def test_an_object_without_events_is_rejected() -> None:
    with pytest.raises(CompileError, match="no 'events' list"):
        parse_events({"recording": []})
