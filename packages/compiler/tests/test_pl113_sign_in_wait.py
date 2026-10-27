"""PL-113 S2: a sign-in recipe ends by waiting for the page after the code.

The saved recipe used to be ``navigate /login -> fill email -> click Continue`` and nothing after
it: a run that clicked Continue was Done straight away, before the code page had even loaded. The
compiler now appends one ``wait_for`` step naming the page the demonstration reached after the code
page (its path pattern), with 90 s to spare so the code email can arrive. The runner treats a
sign-in wait that never passes as a parked row -- "Sign-in didn't finish: ..." -- never as Done.
"""

from __future__ import annotations

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.assertions import sign_in_landing_pattern
from packages.compiler.testing import EMAIL_TARGETS, action_event

LOGIN_URL = "https://app.example.test/login"
HOME_URL = "https://app.example.test/home"


def sign_in_recording() -> list[dict]:
    """Fill the email, click Continue, and land on the page after the code page."""
    return [
        {"seq": 0, "kind": "navigate", "url": LOGIN_URL},
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.test", url=LOGIN_URL),
        action_event(2, "click", "click", EMAIL_TARGETS, name="Click Continue", url=LOGIN_URL),
    ]


def options(**overrides: object) -> CompileOptions:
    values: dict[str, object] = {
        "name": "Sign in to the app",
        "when_to_use": "Open the account's own pages.",
        "sample_row": {"email": "ravi@example.test"},
        "input_fields": {"email": {"type": "string", "required": True}},
        "record_key_column": "email",
        "commit_step_ids": [],
        "sign_in": True,
        "sign_in_after_path": HOME_URL,
    }
    values.update(overrides)
    return CompileOptions(**values)  # type: ignore[arg-type]


def test_a_sign_in_recipe_ends_with_a_wait_for_the_page_after_the_code() -> None:
    result = compile_recording(sign_in_recording(), options())

    last = result.recipe["steps"][-1]
    assert last["action"] == "wait_for"
    assert last["id"] == "sign-in-finished"
    assert last["timeout_ms"] == 90_000
    assert last["condition"] == {
        "url_matches": sign_in_landing_pattern(HOME_URL),
        "timeout_ms": 90_000,
    }


def test_the_wait_matches_the_page_after_the_code_and_not_the_sign_in_page() -> None:
    """The pattern is evidence only if the page the click ran on cannot satisfy it."""
    import re

    pattern = sign_in_landing_pattern(HOME_URL)

    assert re.search(pattern, HOME_URL) is not None
    assert re.search(pattern, f"{HOME_URL}/inbox") is not None
    assert re.search(pattern, LOGIN_URL) is None
    # A neighbouring path is not the landing page.
    assert re.search(pattern, "https://app.example.test/homework") is None


def test_a_sign_in_with_no_page_after_the_code_says_so_and_adds_no_wait() -> None:
    """A recording that never showed the landing page cannot claim the sign-in finished."""
    result = compile_recording(sign_in_recording(), options(sign_in_after_path=None))

    assert all(step["action"] != "wait_for" for step in result.recipe["steps"])
    assert any("couldn't see the page after the code" in line for line in result.warnings)


def test_a_recording_that_is_not_a_sign_in_gets_no_wait() -> None:
    result = compile_recording(
        sign_in_recording(), options(sign_in=False, sign_in_after_path=HOME_URL)
    )

    assert all(step["action"] != "wait_for" for step in result.recipe["steps"])
