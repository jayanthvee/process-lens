"""Fold fragmented events into single actions.

Two folds, both required by the way a browser reports what a person did:

* **Keystrokes become one fill.** Typing a value fires an input event per
  character; adjacent fills on the same element are one action, and the last
  value is the one the person meant.
* **A click and its submit become one step.** Clicking a button that submits a
  form produces a click and then a submit. They describe one action, so they
  collapse into a single commit step. A form submitted without a click (Enter in
  a field) has no click to fold into, so its submit becomes the commit step.
"""

from __future__ import annotations

from dataclasses import replace

from .model import Event, target_signature


def _same_target(left: Event, right: Event) -> bool:
    if left.step is None or right.step is None:
        return False
    return target_signature(left.step.get("targets")) == target_signature(right.step.get("targets"))


def _recorded_value(step: dict | None) -> object:
    if not step:
        return None
    value = step.get("value")
    if isinstance(value, dict):
        return value.get("value")
    return value


def coalesce_fills(events: list[Event]) -> tuple[list[Event], list[str]]:
    """Merge adjacent fills on the same element, keeping the final value."""
    folded: list[Event] = []
    warnings: list[str] = []
    merged = 0

    for event in events:
        previous = folded[-1] if folded else None
        if (
            event.kind == "fill"
            and previous is not None
            and previous.kind == "fill"
            and _same_target(previous, event)
        ):
            value = _recorded_value(event.step)
            step = dict(previous.step or {})
            if isinstance(step.get("value"), dict):
                step["value"] = {**step["value"], "value": value}
            else:
                step["value"] = value
            folded[-1] = replace(previous, step=step, seq=event.seq)
            merged += 1
            continue
        folded.append(event)

    if merged:
        warnings.append(f"merged {merged} keystroke event(s) into their fill steps")
    return folded, warnings


def collapse_submits(events: list[Event]) -> tuple[list[Event], list[str]]:
    """Collapse a click and the submit it caused into one commit step."""
    collapsed: list[Event] = []
    warnings: list[str] = []
    merged = 0

    for event in events:
        if event.kind != "submit":
            collapsed.append(event)
            continue

        previous = collapsed[-1] if collapsed else None
        if previous is not None and previous.kind == "click" and _same_target(previous, event):
            collapsed[-1] = replace(
                previous,
                commit=True,
                observation=previous.observation or event.observation,
            )
            merged += 1
            continue

        if event.step is None:
            warnings.append("dropped a form submit with no identifiable control")
            continue

        collapsed.append(replace(event, kind="click", commit=True, observation=event.observation))

    if merged:
        warnings.append(f"folded {merged} click/submit pair(s) into commit steps")
    return collapsed, warnings
