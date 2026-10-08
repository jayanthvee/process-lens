"""Validation of a compiled recipe against the recipe JSON Schema.

The schema is the authority for the recipe format, so the compiler checks its
own output against it before returning. The schema file lives in the ``recipe``
package; reading it is a data dependency, not an import of that package's code.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import CompileError

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "packages" / "recipe" / "schema" / "recipe.schema.json"
)


@lru_cache(maxsize=1)
def recipe_schema() -> dict[str, Any]:
    """The recipe JSON Schema, loaded once."""
    if not SCHEMA_PATH.is_file():
        raise CompileError(f"the recipe schema is missing: {SCHEMA_PATH}")
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Any:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - the dependency is declared
        raise CompileError(
            "validating a recipe needs the 'jsonschema' package; install the project dependencies"
        ) from exc
    return Draft202012Validator(recipe_schema())


def validation_errors(recipe: Any) -> list[str]:
    """Every way the recipe fails the schema, most specific path first."""
    problems: list[str] = []
    for error in sorted(
        _validator().iter_errors(recipe), key=lambda item: list(item.absolute_path)
    ):
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        problems.append(f"{location}: {error.message}")
    return problems


def assert_valid_recipe(recipe: Any) -> None:
    """Raise :class:`CompileError` if the recipe does not match the schema."""
    problems = validation_errors(recipe)
    if problems:
        raise CompileError(
            "the compiled recipe does not match the recipe schema", problems=problems
        )
