"""PL-123 N1: "back" is matched whole, never inside another word.

A control the page names for something else -- a backorder row, a page's background -- must never
be taken for the way back. A CSS hook spells the word as its own part (``o_back_button``), and a
label spells it as a word.
"""

from __future__ import annotations

from packages.compiler.compiler import _is_back_click
from packages.compiler.model import Candidate


def _click(name: str, *, selector: str | None = None) -> Candidate:
    targets = [{"by": "role", "role": "button", "name": name}]
    if selector is not None:
        targets = [{"by": "css", "selector": selector}]
    return Candidate(action="click", targets=targets, name=f"Click {name}")


def test_a_word_back_is_the_way_back() -> None:
    assert _is_back_click(_click("Back")) is True
    assert _is_back_click(_click("Go back to the list")) is True


def test_a_class_part_back_is_the_way_back() -> None:
    assert _is_back_click(_click("button", selector="button.o_back_button.btn")) is True
    assert _is_back_click(_click("button", selector=".breadcrumb-back")) is True


def test_back_inside_another_word_is_not_the_way_back() -> None:
    assert _is_back_click(_click("Backorder lines")) is False
    assert _is_back_click(_click("Background colour")) is False
    assert _is_back_click(_click("div", selector=".backorder-row")) is False
    assert _is_back_click(_click("div", selector="#page-background")) is False


def test_an_intent_saying_back_is_the_way_back() -> None:
    candidate = _click("button", selector=".nav-arrow")
    candidate.intent = "go back to the product list"
    assert _is_back_click(candidate) is True

    other = _click("button", selector=".nav-arrow")
    other.intent = "open the backorder list"
    assert _is_back_click(other) is False
