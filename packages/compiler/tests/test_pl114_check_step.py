"""PL-114 item 1: a checkbox is recorded as the value it ended with, not as a click.

The recorder emits one ``check`` step carrying the yes/no the field was left in. These tests hold
the compiler to three things: the step renders (a ``false`` survives), the taught value binds to a
yes/no column of the sample row, and a check is not a commit unless the person names it.
"""

from __future__ import annotations

from packages.compiler import CompileOptions, compile_recording

ORIGIN = "https://app.example.com"
CHECK_URL = f"{ORIGIN}/events/new"
CHECK_TARGETS = [{"by": "role", "role": "checkbox", "name": "Limit registrations"}]


def options(sample_row: dict[str, object] | None = None, **overrides: object) -> CompileOptions:
    return CompileOptions(
        name="Publish an event",
        when_to_use="Each row adds one event to the site.",
        sample_row=sample_row or {"email": "ravi@example.com", "title": "Harbour lights"},
        record_key_column="email",
        **overrides,
    )


def check_recording(value: object = True, checked: bool = True) -> list[dict]:
    """One recorded check: the field was left ticked (or cleared), and that is the value."""
    return [
        {
            "seq": 1,
            "kind": "check",
            "url": CHECK_URL,
            "step": {
                "id": "check-1",
                "action": "check",
                "name": "Check 'Limit registrations'",
                "targets": CHECK_TARGETS,
                "value": {"kind": "constant", "value": value},
                "checked": checked,
            },
        }
    ]


def check_step(result) -> dict:
    return next(step for step in result.recipe["steps"] if step["action"] == "check")


def test_a_recorded_check_compiles_to_a_check_step() -> None:
    """The step keeps its value and the state the demonstration left, and nothing else."""
    step = check_step(compile_recording(check_recording(), options()))

    assert step["value"] == {"kind": "constant", "value": True}
    assert step["checked"] is True
    assert "clear_first" not in step
    assert "option_match" not in step


def test_a_cleared_check_survives_as_false() -> None:
    """``false`` is a value, not an absence: it must not be dropped on the way through."""
    step = check_step(compile_recording(check_recording(value=False, checked=False), options()))

    assert step["value"] == {"kind": "constant", "value": False}
    assert step["checked"] is False


def test_a_yes_no_column_binds_the_taught_check() -> None:
    """The sample row's "yes" is the demonstration of a yes/no column, so the check reads it."""
    result = compile_recording(
        check_recording(),
        options({"email": "ravi@example.com", "published": "yes", "title": "Harbour lights"}),
    )

    assert check_step(result)["value"] == {"kind": "input", "column": "published"}
    assert any("bound to the published column" in warning for warning in result.warnings)


def test_a_column_that_says_no_does_not_bind_a_taught_yes() -> None:
    """No cell means True, so the taught value stands and no column is guessed."""
    result = compile_recording(
        check_recording(),
        options({"email": "ravi@example.com", "published": "no", "title": "Harbour lights"}),
    )

    assert check_step(result)["value"] == {"kind": "constant", "value": True}
    assert not any("bound to" in warning for warning in result.warnings)


def test_a_check_never_commits_unless_it_is_named() -> None:
    """A check changes no destination record, so it is not a commit on its own."""
    result = compile_recording(check_recording(), options())

    assert [step for step in result.recipe["steps"] if step.get("commit")] == []


def test_a_named_check_is_never_the_save() -> None:
    """T1 review B1: a checkbox is never the save (AGENTS.md section 4 lists the commit steps).

    A person can pick the check's row by mistake. Rather than compiling a recipe the schema then
    refuses, the compile leaves the step as an ordinary check and says why in plain words.
    """
    result = compile_recording(check_recording(), options(commit_step_ids=["check-1"]))

    assert [step for step in result.recipe["steps"] if step.get("commit")] == []
    assert "commit" not in check_step(result)
    assert "assert_after" not in check_step(result)
    assert any(
        "a checkbox can't be the step that saves your work" in warning
        for warning in result.warnings
    ), result.warnings
