"""PL-117 item 1: a sign-in is compiled as a task that saves nothing.

The owner's Goodlabs sign-in ended with a Continue click marked as the commit, checked by
``url_matches "/home/"`` -- the page the code step eventually reached, not the page Continue itself
opens. A sign-in saves nothing in the destination, so the compile must behave exactly like PL-112's
"Nothing gets saved": no step is a commit, no step has an ``assert_after``, and the run is proved
only by the page after the code page (the ``sign-in-finished`` wait).
"""

from __future__ import annotations

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import EMAIL_TARGETS, SAVE_TARGETS, action_event

LOGIN_URL = "https://app.example.test/login"
HOME_URL = "https://app.example.test/home"


def sign_in_recording() -> list[dict]:
    """The owner's three steps.

    The Continue click carries the landing observation the recorder saw after the code was typed
    for the owner — the exact evidence that used to become Continue's ``assert_after``.
    """
    return [
        {"seq": 0, "kind": "navigate", "url": LOGIN_URL},
        action_event(1, "fill", "fill", EMAIL_TARGETS, value="ravi@example.test", url=LOGIN_URL),
        action_event(
            2,
            "click",
            "click",
            SAVE_TARGETS,
            name="Click Continue",
            url=LOGIN_URL,
            observation={"url_matches": r"/home(?:/.*)?$"},
        ),
    ]


def options(**overrides: object) -> CompileOptions:
    values: dict[str, object] = {
        "name": "Sign in to the app",
        "when_to_use": "Open the account's own pages.",
        "sample_row": {"email": "ravi@example.test"},
        "input_fields": {"email": {"type": "string", "required": True}},
        "record_key_column": "email",
        # The teaching screen still sends the Continue click as its choice; a sign-in ignores it.
        "commit_step_ids": ["click-2"],
        "sign_in": True,
        "sign_in_after_path": HOME_URL,
    }
    values.update(overrides)
    return CompileOptions(**values)  # type: ignore[arg-type]


def test_a_sign_in_has_no_commit_step_and_no_landing_check() -> None:
    result = compile_recording(sign_in_recording(), options())

    assert [step for step in result.recipe["steps"] if step.get("commit")] == []
    assert all("assert_after" not in step for step in result.recipe["steps"])


def test_a_sign_in_ends_with_the_one_check_that_its_sign_in_finished() -> None:
    result = compile_recording(sign_in_recording(), options())

    last = result.recipe["steps"][-1]
    assert last["id"] == "sign-in-finished"
    assert last["action"] == "wait_for"
    assert last["timeout_ms"] == 90_000


def test_the_same_recording_is_still_a_commit_when_it_is_not_a_sign_in() -> None:
    """The rule is scoped to sign-ins: an ordinary compile keeps its commit and its check."""
    result = compile_recording(sign_in_recording(), options(sign_in=False, sign_in_after_path=None))

    commits = [step for step in result.recipe["steps"] if step.get("commit")]
    assert len(commits) == 1
    assert commits[0]["name"] == "Click Continue"
    assert commits[0]["assert_after"] == {
        "url_matches": r"/home(?:/.*)?$",
        "timeout_ms": 8000,
    }
