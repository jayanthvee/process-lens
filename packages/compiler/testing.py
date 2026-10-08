"""Builders for recorded events, used by tests and by any caller that needs to
construct or inspect a recording.

The shape here is the one the browser recorder emits: an ordered list of
``{"seq", "kind", "url", "step"}`` events, where ``step`` is a recipe step and
``kind`` is the interaction that produced it. Keeping the builders with the
compiler makes the expected input explicit in one place.
"""

from __future__ import annotations

from typing import Any

DEFAULT_URL = "https://app.example.com/records/new"

EMAIL_TARGETS: list[dict[str, Any]] = [
    {"by": "role", "role": "textbox", "name": "Email", "within": "form:New record"},
    {"by": "testid", "value": "record-email"},
    {"by": "label", "text": "Email"},
]
INTEREST_TARGETS: list[dict[str, Any]] = [
    {"by": "role", "role": "combobox", "name": "Interest", "within": "form:New record"},
    {"by": "testid", "value": "record-interest"},
]
SAVE_TARGETS: list[dict[str, Any]] = [
    {"by": "role", "role": "button", "name": "Save", "within": "form:New record"},
    {"by": "testid", "value": "record-save"},
]

SAMPLE_ROW: dict[str, str] = {"email": "ravi@example.com", "interest": "Data course"}

INPUT_FIELDS: dict[str, dict[str, Any]] = {
    "email": {"type": "string", "required": True},
    "interest": {"type": "string"},
}


def action_event(
    seq: int,
    kind: str,
    action: str,
    targets: list[dict[str, Any]],
    *,
    value: Any = None,
    name: str | None = None,
    url: str = DEFAULT_URL,
    step_id: str | None = None,
    observation: dict[str, Any] | None = None,
    frame: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One recorded event, in the shape the recorder emits."""
    step: dict[str, Any] = {
        "id": step_id or f"{action}-{seq}",
        "action": action,
        "targets": targets,
    }
    if name is not None:
        step["name"] = name
    if value is not None:
        step["value"] = {"kind": "constant", "value": value}
    if frame is not None:
        step["frame"] = frame
    event: dict[str, Any] = {"seq": seq, "kind": kind, "url": url, "step": step}
    if observation is not None:
        event["observation"] = observation
    return event


def noise_event(seq: int, kind: str, url: str = DEFAULT_URL) -> dict[str, Any]:
    """An event that carries no intent, such as focus or scroll."""
    return {"seq": seq, "kind": kind, "url": url}
