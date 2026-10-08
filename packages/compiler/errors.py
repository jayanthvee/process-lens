"""Compile-time errors."""

from __future__ import annotations


class CompileError(RuntimeError):
    """The recording could not be turned into a valid recipe.

    The compiler fails closed: it never returns a recipe it could not validate.
    Every message names a problem the builder can act on (re-record, add an
    assertion, supply a sample row), and ``problems`` carries the individual
    items when there are several.
    """

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        self.problems = list(problems or [])
        if self.problems:
            message = f"{message}\n  - " + "\n  - ".join(self.problems)
        super().__init__(message)
