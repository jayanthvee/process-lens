"""The data policy's redaction pass.

The snapshot never carries a typed value in the first place (the extension does not read one), but
the service does not trust its caller: a ``value`` beside a snapshot element or a recorded step is
removed before the payload is hashed, stored or sent. Email addresses and phone numbers in page text
are masked as well.

`ai_calls.request` stores exactly this redacted payload, never the prompt and never a typed value.
"""

from __future__ import annotations

import re
from typing import Any

# Keys that are never page labels and are never safe to send. `token` is deliberately absent: it is
# the snapshot's element id, not a session token.
_SECRET_KEYS = frozenset(
    {"password", "passcode", "secret", "cookie", "cookies", "session", "session_token"}
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Seven or more digits, with the separators a phone number uses. A price such as "799" is not one.
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def _mask_text(text: str) -> str:
    masked = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[phone]", masked)


def _mask_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _mask_tree(item)
            for key, item in value.items()
            if str(key).lower() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_mask_tree(item) for item in value]
    if isinstance(value, tuple):
        return [_mask_tree(item) for item in value]
    if isinstance(value, str):
        return _mask_text(value)
    return value


def redact_find_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The page snapshot and hints, with typed values removed and personal text masked."""
    redacted = _mask_tree(payload)
    snapshot = redacted.get("snapshot")
    if isinstance(snapshot, list):
        for element in snapshot:
            if isinstance(element, dict):
                element.pop("value", None)
                element.pop("typed_value", None)
                element.pop("input_value", None)
    return redacted


def redact_clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Recorded steps with every typed value removed; only ids, actions and labels reach a model."""
    redacted = _mask_tree(payload)
    steps = redacted.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                step.pop("value", None)
                step.pop("typed_value", None)
                step.pop("input_value", None)
    return redacted
