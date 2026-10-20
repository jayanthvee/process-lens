"""Make a document's columns and rows tidy, and say plainly what changed.

Every rule here is deterministic: no model, no clock, no locale. A header is trimmed and made
unique, a cell loses the characters a Word document carries invisibly, and a date is only rewritten
when the way it was written leaves no doubt about which day it means.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

# A non-breaking space is a space; a zero-width character is nothing at all.
_SPACES = str.maketrans({"\u00a0": " "})
_INVISIBLE = str.maketrans(
    {
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
    }
)
_SMART_QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
    }
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DAY_MONTH_YEAR = re.compile(r"^(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})$")
_MONTH_DAY_YEAR = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$")
# Numbers that could mean two different days, whichever way the writer read them.
_AMBIGUOUS_DATE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")

_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _month(name: str) -> int | None:
    return _MONTHS.get(name.strip().rstrip(".").lower())


def _real_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _as_iso(text: str) -> str | None:
    """The ISO form of a date written without ambiguity, or ``None``."""
    iso = _ISO.match(text)
    if iso is not None:
        year, month, day = (int(part) for part in iso.groups())
        found = _real_date(year, month, day)
        return found.isoformat() if found is not None else None

    day_first = _DAY_MONTH_YEAR.match(text)
    if day_first is not None:
        day, name, year = day_first.groups()
        month = _month(name)
        found = None if month is None else _real_date(int(year), month, int(day))
        return found.isoformat() if found is not None else None

    month_first = _MONTH_DAY_YEAR.match(text)
    if month_first is not None:
        name, day, year = month_first.groups()
        month = _month(name)
        found = None if month is None else _real_date(int(year), month, int(day))
        return found.isoformat() if found is not None else None
    return None


def _text(value: Any) -> str:
    """A cell as a person meant it: one line, plain quotes, no invisible characters."""
    text = "" if value is None else str(value)
    text = text.translate(_SPACES).translate(_INVISIBLE).translate(_SMART_QUOTES)
    text = re.sub(r"\s+", " ", text).strip()
    return _EMAIL.sub(lambda match: match.group(0).lower(), text)


def _cell(value: Any) -> tuple[str, bool]:
    """The cleaned cell, and whether it holds a date that could be read two ways."""
    text = _text(value)
    iso = _as_iso(text)
    if iso is not None:
        return iso, False
    return text, bool(_AMBIGUOUS_DATE.match(text))


def _headers(columns: Sequence[Any]) -> list[str]:
    """Headers trimmed, named by position when empty, and made unique."""
    used: set[str] = set()
    result: list[str] = []
    for position, name in enumerate(columns, start=1):
        base = _text(name) or f"Column {position}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base} {suffix}"
            suffix += 1
        used.add(candidate)
        result.append(candidate)
    return result


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def clean(
    columns: Sequence[Any],
    rows: Iterable[Sequence[Any]],
) -> tuple[list[str], list[dict[str, str]], list[str]]:
    """Return the cleaned columns, rows and the notes a person reads.

    Empty rows and exact duplicate rows are dropped and counted. A date that could be read two
    ways is left exactly as typed and counted, because guessing which day it means is worse than
    saying that the document is unclear.
    """
    headers = _headers(columns)
    kept: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    empty = 0
    duplicates = 0
    ambiguous = 0

    for raw in rows:
        values: list[str] = []
        for index in range(len(headers)):
            value, was_ambiguous = _cell(raw[index] if index < len(raw) else "")
            values.append(value)
            ambiguous += 1 if was_ambiguous else 0
        if not any(values):
            empty += 1
            continue
        key = tuple(values)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        kept.append(dict(zip(headers, values, strict=True)))

    notes: list[str] = []
    if empty:
        notes.append(f"Removed {_plural(empty, 'empty row')}")
    if duplicates:
        notes.append(f"Removed {_plural(duplicates, 'duplicate row')}")
    if ambiguous:
        verb = "was" if ambiguous == 1 else "were"
        notes.append(f"{_plural(ambiguous, 'date')} {verb} ambiguous and left as typed")
    return headers, kept, notes


def csv_bytes(columns: Sequence[str], rows: Iterable[dict[str, str]]) -> bytes:
    """The whole table as CSV, UTF-8 with a BOM so Excel opens it as text and not as mojibake."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns])
    return buffer.getvalue().encode("utf-8-sig")


__all__ = ["clean", "csv_bytes"]
