"""``services.runner``: the run orchestrator.

It owns runs: one pinned recipe version against one input batch, record by record. Every ledger
write goes through :mod:`packages.ledger`; the browser stays behind the `Executor` seam. The
batch loop makes no model call (D-017).
"""

from __future__ import annotations

from .approvals import evaluate_policy
from .conditions import condition_timeout
from .creation import CreationError, create_run
from .executor import ConditionResult, Executor, StepCommand, StepResult
from .reconcile import NoopReconciler, ReconciledDiff, Reconciler, outcome_of
from .run_loop import RunLoop
from .templates import TemplateError, render
from .worker import Heartbeat, Worker, main

__all__ = [
    "ConditionResult",
    "CreationError",
    "Executor",
    "Heartbeat",
    "NoopReconciler",
    "ReconciledDiff",
    "Reconciler",
    "RunLoop",
    "StepCommand",
    "StepResult",
    "TemplateError",
    "Worker",
    "condition_timeout",
    "create_run",
    "evaluate_policy",
    "main",
    "outcome_of",
    "render",
]
