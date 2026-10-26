"""What kind of value a task column holds, read from the example it was taught with (PL-095).

A recorded task has no field types: every field is a string with the example the person typed. The
example is the only type information there is, so it decides the kind:

* ``email``: one ``@``, no whitespace, a dot in the domain;
* ``number``: digits, optional thousands commas, one decimal point, an optional currency sign. A
  long plain digit run (a phone or an ID), a leading ``+`` or an internal space is not a number.

The batch check uses the kind to flag a value that cannot be right; a run uses it to type a number
the way any number field accepts it (PL-099). Both read the same rules from here, so what the check
shows is what the run types. Pure and deterministic: no model, no network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NUMBER = re.compile(
    r"^(?:[€$£])?(?P<sign>-)?(?P<whole>\d{1,3}(?:,\d{3})*|\d+)(?P<fraction>\.\d+)?$"
)
# More plain digits than this, with no decimal point, is a phone or an ID rather than a number.
MAX_PLAIN_DIGITS = 6


def is_email(text: str) -> bool:
    """One ``@``, no whitespace, and a dot in the domain: ``local@domain.tld``."""
    return EMAIL.fullmatch(text.strip()) is not None


def plain_number(text: str) -> str | None:
    """The number as a number field takes it, or ``None`` when ``text`` is not a number.

    The currency sign and the thousands commas go; the sign, the digits and the decimals stay
    exactly as written: ``$1,299.00`` is ``1299.00``, never ``1299`` or ``1.299``.
    """
    match = NUMBER.fullmatch(text.strip())
    if match is None:
        return None
    digits = match.group("whole").replace(",", "")
    plain = "," not in match.group("whole") and match.group("fraction") is None
    if plain and len(digits) > MAX_PLAIN_DIGITS:
        return None
    sign = "-" if match.group("sign") else ""
    return f"{sign}{digits}{match.group('fraction') or ''}"


def number_value(text: str) -> Decimal | None:
    """The value as a number, or ``None`` when it is not one the example taught."""
    plain = plain_number(text)
    return None if plain is None else Decimal(plain)


def kind(example: Any) -> str | None:
    """The kind a task column was taught with: ``email``, ``number``, or nothing to check."""
    text = "" if example is None else str(example).strip()
    if not text:
        return None
    if is_email(text):
        return "email"
    if plain_number(text) is not None:
        return "number"
    return None


def as_typed(fields: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """The row's values as a run types them: a number column's number without its decoration.

    A price field that takes ``109.00`` can refuse ``$109.00`` (Odoo's invoice line does, and the
    save then never happens), while the person wrote the same number either way. Only a column
    taught with a number, and only a value that reads as one, changes; everything else, including a
    value the check flags ("free", "€79,50"), is left exactly as the file holds it.
    """
    typed = dict(values)
    for column, value in values.items():
        spec = fields.get(column)
        if not isinstance(spec, Mapping) or not isinstance(value, str):
            continue
        if kind(spec.get("example")) != "number":
            continue
        plain = plain_number(value)
        if plain is not None:
            typed[column] = plain
    return typed
