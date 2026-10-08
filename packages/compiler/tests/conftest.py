"""Shared fixtures for the compiler tests."""

from __future__ import annotations

from typing import Any

import pytest

from packages.compiler import CompileOptions
from packages.compiler.testing import (
    EMAIL_TARGETS,
    INPUT_FIELDS,
    INTEREST_TARGETS,
    SAMPLE_ROW,
    SAVE_TARGETS,
    action_event,
    noise_event,
)
from packages.compiler.validation import recipe_schema, validation_errors


@pytest.fixture
def options() -> CompileOptions:
    return CompileOptions(
        name="Create a record from a sheet",
        when_to_use="Each row has to exist in the application exactly once.",
        sample_row=dict(SAMPLE_ROW),
        input_fields={name: dict(field) for name, field in INPUT_FIELDS.items()},
        record_key_column="email",
    )


@pytest.fixture
def recording() -> list[dict[str, Any]]:
    """A small but realistic demonstration: type, choose, save, confirm."""
    return [
        noise_event(1, "focus"),
        action_event(2, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com", name="Fill Email"),
        action_event(3, "fill", "fill", EMAIL_TARGETS, value="ravi@example.com"),
        action_event(4, "select", "select", INTEREST_TARGETS, value="Data course"),
        action_event(5, "click", "click", SAVE_TARGETS, name="Click Save"),
        action_event(
            6,
            "submit",
            "click",
            SAVE_TARGETS,
            observation={"text_visible": "Record created"},
        ),
    ]


@pytest.fixture(scope="session")
def schema() -> dict[str, Any]:
    return recipe_schema()


@pytest.fixture
def errors() -> Any:
    """The schema-validation helper, exposed for assertions in tests."""
    return validation_errors
