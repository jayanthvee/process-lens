"""PL-113 S3: a click that did not happen says exactly what stopped it.

The parked sentence was "something was in front of it" for every hit-test miss, including "the hit
test found no element at the box center" (the point is outside the window). The step's own error was
only in ``step_events``, which no screen showed, so nobody could tell which case it was.

Each case now says what it is: what covered the button (the covering element's own name, or its
kind), that the button is outside the window and the page did not scroll to it, or that the button
was busy. The raw error stays on the step event.
"""

from __future__ import annotations

from services.runner.step_words import describe_step_failure

CONTINUE = {
    "id": "continue",
    "action": "click",
    "name": "Click Continue",
    "targets": [{"by": "role", "role": "button", "name": "Continue"}],
}


def sentence(error: str) -> str:
    return describe_step_failure(CONTINUE, {}, {}, error)


def test_a_covered_button_names_what_covered_it() -> None:
    """The covering element's own name is what a person can go and look at."""
    assert (
        sentence(
            "the click target could not be resolved: the target is not clickable: "
            'the box center is covered by <button "Sign up">'
        )
        == "Couldn't click “Continue”: it was covered by “Sign up”"
    )


def test_a_covering_element_with_no_name_is_named_by_its_kind() -> None:
    assert (
        sentence(
            "the click target could not be resolved: the target is not clickable: "
            "the box center is covered by <div>"
        )
        == "Couldn't click “Continue”: it was covered by a div"
    )


def test_a_button_below_the_window_says_the_page_did_not_scroll() -> None:
    """This is the case that was reported as "something was in front of it" with nothing there."""
    assert (
        sentence(
            "the click target could not be resolved: the target is not clickable: "
            "the target is outside the window and the page didn't scroll to it"
        )
        == "Couldn't click “Continue”: the button is outside the window and the page didn't "
        "scroll to it"
    )


def test_a_busy_button_is_not_a_covered_one() -> None:
    assert (
        sentence(
            "the click target could not be resolved: the target is not clickable: "
            "the target is busy (disabled)"
        )
        == "Couldn't click “Continue”: the button was busy"
    )
