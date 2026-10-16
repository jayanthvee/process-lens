"""PL-026: extension versions compared numerically, part by part.

``1.10.0`` is newer than ``1.9.2`` -- as text it is older, which is the whole reason this module
exists -- and ``2.0`` is the same version as ``2.0.0``, so a floor an admin types as ``1.9`` is
usable. Anything that is not a version is refused rather than guessed at, because the API turns that
refusal into a 422 and the database then never holds a floor nobody can compare against.
"""

from __future__ import annotations

import pytest

from services.api.versions import (
    InvalidVersion,
    compare_versions,
    floor_is_met,
    is_below,
    parse_version,
)


@pytest.mark.parametrize(
    ("newer", "older"),
    [
        ("1.10.0", "1.9.2"),
        ("2.0.1", "2.0.0"),
        ("1.0.0", "0.99.99"),
        ("10.0.0", "9.9.9"),
        ("1.2.10", "1.2.9"),
        ("1.0", "0.9.9"),
    ],
)
def test_a_newer_version_compares_higher(newer: str, older: str) -> None:
    assert compare_versions(newer, older) == 1
    assert compare_versions(older, newer) == -1


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("2.0", "2.0.0"),
        ("2.0", "2.0.0.0"),
        ("1.10.0", "1.10.0"),
        ("1", "1.0.0"),
    ],
)
def test_a_missing_part_is_zero_so_these_are_the_same_version(left: str, right: str) -> None:
    assert compare_versions(left, right) == 0
    assert parse_version(left) == parse_version(right)


def test_the_four_parts_are_read_as_numbers() -> None:
    assert parse_version("1.10.0") == (1, 10, 0, 0)
    assert parse_version(" 2.0 ") == (2, 0, 0, 0), "surrounding space is not a different version"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "one.two",
        "1.2.3.4.5",
        "1..0",
        ".1",
        "1.",
        "-1",
        "1.2.x",
        "1.00001",
        "01.2.3",
        "1.2.3-beta",
        "1 .2.3",
        "1.2 .3",
    ],
)
def test_a_malformed_version_is_refused(bad: str) -> None:
    with pytest.raises(InvalidVersion):
        parse_version(bad)


def test_surrounding_space_is_trimmed_rather_than_refused() -> None:
    """A person typing the floor by hand may leave a space; the value stored is trimmed."""
    assert parse_version(" 1.9 ") == parse_version("1.9")
    assert parse_version("1.10.0\n") == parse_version("1.10.0")


def test_a_version_that_is_not_text_is_refused() -> None:
    with pytest.raises(InvalidVersion):
        parse_version(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("reported", "minimum", "below"),
    [
        ("1.9.2", "1.10.0", True),
        ("1.10.0", "1.10.0", False),
        ("1.10.1", "1.10.0", False),
        ("2.0", "1.10.0", False),
        ("1.9", "1.9.0", False),
        ("1.8.9", "1.9", True),
    ],
)
def test_the_floor_decision(reported: str, minimum: str, below: bool) -> None:
    assert is_below(reported, minimum) is below


def test_an_absent_report_or_an_absent_floor_is_never_below() -> None:
    """No report is "not seen yet", not "out of date", and no floor accepts any version."""
    assert is_below(None, "99.0.0") is False
    assert is_below("0.0.1", None) is False
    assert is_below(None, None) is False


@pytest.mark.parametrize(
    ("reported", "minimum", "met"),
    [
        ("1.10.0", "1.9.2", True),
        ("1.9.2", "1.10.0", False),
        ("1.10.0", "1.10.0", True),
        (None, "99.0.0", True),
        ("0.0.1", None, True),
        (None, None, True),
    ],
)
def test_the_floor_is_met_where_the_floor_decision_says_so(
    reported: str | None, minimum: str | None, met: bool
) -> None:
    """The gate's own form of the question answers the opposite way, and agrees."""
    assert floor_is_met(reported, minimum) is met
    assert is_below(reported, minimum) is (not met)


def test_a_floor_nobody_can_read_is_not_met_rather_than_an_error() -> None:
    """The column is granted to the admin role, so a value can arrive that the API never wrote.

    A run route and a page read both ask this question. Raising here is a 500 on a whole surface
    over one column value; answering "not met" refuses the run and names the value, which is what
    an admin can act on -- and it fails in the safe direction.
    """
    assert floor_is_met("1.9.2", "v1.9") is False
    assert floor_is_met("1.9.2", "") is False
    assert floor_is_met("later", "1.9.2") is False
