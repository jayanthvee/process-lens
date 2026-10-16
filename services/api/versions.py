"""Chrome extension versions, compared the way a person reads them.

An extension's version is dotted numbers: ``1.9.2``, ``1.10.0``, ``2.0``. Comparing them as text is
wrong the moment a part reaches two digits -- ``"1.10.0" < "1.9.2"`` as strings, which is the one
case PL-026 names -- so every comparison here is numeric and part by part.

A missing part counts as zero, so ``2.0`` and ``2.0.0`` are the same version. That is what makes the
floor usable: an admin types ``1.9`` and every ``1.9.0`` is at or above it.

A version this module cannot read is refused rather than guessed at: :class:`InvalidVersion` is what
the API turns into a 422, so a malformed floor is never stored and a malformed report is never
compared. The rules are Chrome's own, narrowed: at least one part, at most four, each one to four
digits, and no leading zero on a multi-digit part.
"""

from __future__ import annotations

import re

# One part of a version: up to four digits, and any leading zero only on the single digit `0`.
_PART = re.compile(r"^(?:0|[1-9][0-9]{0,3})$")
# Chrome allows at most four parts.
MAX_PARTS = 4


class InvalidVersion(ValueError):
    """A version string that is not a dotted sequence of numbers."""


def parse_version(text: str) -> tuple[int, int, int, int]:
    """A version as four numbers, so two of them can be compared directly.

    Missing parts are zero: ``2.0`` parses to ``(2, 0, 0, 0)``.
    """
    if not isinstance(text, str):
        raise InvalidVersion(f"a version must be text, not {type(text).__name__}")
    trimmed = text.strip()
    if trimmed == "":
        raise InvalidVersion("a version cannot be empty")
    parts = trimmed.split(".")
    if len(parts) > MAX_PARTS:
        raise InvalidVersion(f"a version has at most {MAX_PARTS} parts: {text!r}")
    numbers: list[int] = []
    for part in parts:
        if not _PART.match(part):
            raise InvalidVersion(f"{part!r} is not a version part: {text!r}")
        numbers.append(int(part))
    while len(numbers) < MAX_PARTS:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2], numbers[3])


def compare_versions(left: str, right: str) -> int:
    """-1 when ``left`` is older, 0 when they are the same version, 1 when ``left`` is newer.

    Raises :class:`InvalidVersion` when either side cannot be read, so a caller never compares
    against something that is not a version.
    """
    first = parse_version(left)
    second = parse_version(right)
    if first == second:
        return 0
    return -1 if first < second else 1


def is_below(reported: str | None, minimum: str | None) -> bool:
    """Whether the reported version is below the required floor.

    ``False`` when either side is absent: no report means "not seen yet", which is not the same as
    "out of date", and no floor means any version is acceptable.
    """
    if reported is None or minimum is None:
        return False
    return compare_versions(reported, minimum) < 0


__all__ = ["MAX_PARTS", "InvalidVersion", "compare_versions", "is_below", "parse_version"]
