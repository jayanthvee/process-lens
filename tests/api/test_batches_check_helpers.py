"""PL-095: the kind and shape helpers the batch check reads a task's example with.

The end-to-end behaviour is pinned by ``test_batches_value_checks.py``; these are the boundaries
those tests only sample. ``_number_value`` is the single definition of a number, used both to read
a column's kind from its example and to judge a value, so its edges are tested once here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from services.api.batches import (
    _duplicate_label,
    _is_email,
    _kind,
    _kind_reasons,
    _number_value,
    _rank,
    _shape,
)


@pytest.mark.parametrize(
    "example",
    ["349.00", "10", "0", "$92", "\u20ac1,232.00", "\u00a399.50", "-5.00", "123456", "1234567.00"],
)
def test_an_example_that_is_a_number_teaches_a_number_column(example: str) -> None:
    assert _kind(example) == "number"


@pytest.mark.parametrize(
    "example",
    [
        "PL-CHR-2001",
        "+1 555 0101",  # a leading + and internal spaces are a phone
        "5550101123",  # seven digits with no decimal point is a phone or an ID
        "1234567",
        "Ana Ruiz",
        "ana@x.test",
        "",
        None,
    ],
)
def test_an_example_that_is_not_a_number_teaches_no_number_check(example: object) -> None:
    assert _kind(example) != "number"


@pytest.mark.parametrize("value", ["1,23", ".5", "5.", "--5", "-", "$", "1 232", "1e5", ""])
def test_a_malformed_number_is_not_a_number(value: str) -> None:
    assert _number_value(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("$92", "92"), ("1,232.00", "1232.00"), ("0", "0"), ("-5.00", "-5.00"), ("$-2.50", "-2.50")],
)
def test_a_number_reads_through_its_currency_sign_and_commas(value: str, expected: str) -> None:
    assert str(_number_value(value)) == expected


@pytest.mark.parametrize(
    "value", ["\u0663\u0664\u0665", "\u0967\u0968\u0969", "\u0e51\u0e52\u0e53"]
)
def test_a_number_in_another_scripts_digits_is_read_not_raised(value: str) -> None:
    """A price in Arabic-Indic, Devanagari or Thai digits is a number, and never a 500.

    ``\\d`` matches every Unicode decimal digit, and ``Decimal`` reads them, so a spreadsheet
    written in a non-Latin locale must not reach the check as an exception.
    """
    assert _number_value(value) is not None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ana@x.test", True),
        ("gus.hale.at.example", False),  # no @ at all
        ("bo@@x.test", False),  # two @
        ("a b@x.test", False),  # a space
        ("a@x", False),  # no dot in the domain
    ],
)
def test_an_email_is_one_at_sign_no_spaces_and_a_dot(value: str, expected: bool) -> None:
    assert _is_email(value) is expected


def test_the_shape_collapses_runs_and_keeps_separators() -> None:
    assert _shape("PL-CHR-2001") == "A-A-9"
    assert _shape("PL-KEY-5002") == "A-A-9"
    assert _shape("192.00") == "9.9"
    assert _shape("Ana Ruiz") == "A A"
    assert _shape("") == ""


def test_the_shape_matches_a_key_column_that_no_name_or_value_finds() -> None:
    row = {"SKU": "PL-KEY-5002", "New Price": "192.00"}

    assert _rank("search", "SKU", "PL-CHR-2001", row) == 2
    assert _rank("search", "New Price", "PL-CHR-2001", row) == 3


def test_an_empty_example_takes_no_part_in_the_shape_match() -> None:
    assert _rank("search", "SKU", "", {"SKU": ""}) == 3


def test_a_repeat_is_named_after_the_files_column() -> None:
    assert _duplicate_label("search", {"search": "SKU"}) == "SKU"


def test_an_unmapped_key_column_is_named_by_its_task_label() -> None:
    assert _duplicate_label("search", {"search": None}) == "search"
    assert _duplicate_label("search", {}) == "search"


def test_a_header_that_only_capitalises_the_task_column_keeps_the_task_wording() -> None:
    """The frozen API test pins ``Same email as row 1`` for a file header ``Email``."""
    assert _duplicate_label("email", {"email": "Email"}) == "email"


# --- T1 review: what the pilot's files would have got wrong -----------------------------------


def test_a_seven_figure_number_with_thousands_commas_is_a_number() -> None:
    """The six-digit rule is for a plain digit run (a phone, an ID), not for ``1,232,000``."""
    assert _number_value("1,232,000") == Decimal("1232000")
    assert _number_value("1232000") is None


def test_a_shape_of_words_or_digits_alone_matches_nothing() -> None:
    """``Ana Ruiz`` and ``Ruiz Hermanos`` share a shape, as do a quantity and a price."""
    assert _rank("name", "Company", "Ana Ruiz", {"Company": "Ruiz Hermanos"}) == 3
    assert _rank("quantity", "Unit Price", "2", {"Unit Price": "349"}) == 3
    assert _rank("search", "SKU", "PL-CHR-2001", {"SKU": "PL-KEY-5002"}) == 2


def test_a_one_letter_word_is_not_a_shared_name() -> None:
    """The 100-lead pilot run matched the lead's name field (``e_g_product_pricing``, from the
    placeholder "e.g. Product Pricing") to ``E-mail`` because both have an ``e``."""
    assert _rank("e_g_product_pricing", "E-mail", "Taylor Kowalski", {"E-mail": "t@x.test"}) == 3


def test_one_file_column_read_by_two_task_columns_is_flagged_once() -> None:
    document = {
        "meta": {
            "input_schema": {
                "fields": {
                    "search": {"type": "string", "example": "ana@x.test"},
                    "email": {"type": "string", "example": "ana@x.test"},
                }
            }
        }
    }
    values = {"search": "gus.hale.at.example", "email": "gus.hale.at.example"}
    assert _kind_reasons(document, values) == ["“gus.hale.at.example” isn't an email"]
