"""The recipe compiler: a recorded demonstration in, a validated recipe out.

The compiler is pure code with no I/O and no model calls. It takes the ordered
events the browser recorder produced, folds them into a small number of steps,
parameterizes the recorded literals against a sample input row, and emits a
recipe that validates against the recipe JSON Schema.

Public entry point: :func:`compile_recording`.
"""

from __future__ import annotations

from .compiler import CompileOptions, CompileResult, compile_recording
from .errors import CompileError

__all__ = ["CompileError", "CompileOptions", "CompileResult", "compile_recording"]
