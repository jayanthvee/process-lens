"""PL-099: a number column's value is typed as the number, without its decoration."""

from __future__ import annotations

import pytest

from packages.documents.kinds import as_typed, kind, plain_number


@pytest.mark.parametrize(
    ("written", "typed"),
    [
        ("$109.00", "109.00"),
        ("1,299.00", "1299.00"),
        ("€1,232,000", "1232000"),
        ("$-2.50", "-2.50"),
        ("89.999", "89.999"),
        ("0", "0"),
        ("349", "349"),
    ],
)
def test_a_number_loses_its_currency_sign_and_commas_and_nothing_else(
    written: str, typed: str
) -> None:
    assert plain_number(written) == typed


@pytest.mark.parametrize("written", ["free", "€79,50", "1 299", "+44 20", "5550101123", ""])
def test_what_is_not_a_number_has_no_plain_form(written: str) -> None:
    assert plain_number(written) is None


FIELDS = {
    "customer": {"type": "string", "example": "Summit Climbing Gym"},
    "price": {"type": "string", "example": "329.00"},
    "phone": {"type": "string", "example": "+1 555 0101"},
    "account": {"type": "string", "example": "5550101123"},
}


def test_only_a_number_column_with_a_number_in_it_changes() -> None:
    values = {
        "customer": "Acme $1,000 Ltd",
        "price": "$1,299.00",
        "phone": "1,299",
        "account": "$12",
    }

    assert as_typed(FIELDS, values) == {
        "customer": "Acme $1,000 Ltd",
        "price": "1299.00",
        "phone": "1,299",
        "account": "$12",
    }


def test_a_value_the_check_flags_is_typed_as_the_file_has_it() -> None:
    """ "free" and "€79,50" are flagged at the check; run anyway, nothing guesses."""
    assert as_typed(FIELDS, {"price": "free"}) == {"price": "free"}
    assert as_typed(FIELDS, {"price": "€79,50"}) == {"price": "€79,50"}


def test_a_column_the_task_does_not_know_is_left_alone() -> None:
    assert as_typed(FIELDS, {"extra": "$5"}) == {"extra": "$5"}
    assert kind("329.00") == "number"
