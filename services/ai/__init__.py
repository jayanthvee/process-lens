"""The AI service's public module.

Only the two call types PL-122 builds are exposed. There is no generic prompt or completion
function: a caller cannot reach a model except through :class:`AiService`.
"""

from __future__ import annotations

from .client import (
    DeepSeekClient,
    ModelCallError,
    ModelClient,
    ModelReply,
    cost_of,
    estimate_tokens,
)
from .service import (
    AiService,
    CleanRecordingRequest,
    CleanRecordingResult,
    CleanStep,
    FindTargetRequest,
    FindTargetResult,
    SnapshotElement,
)

__all__ = [
    "AiService",
    "CleanRecordingRequest",
    "CleanRecordingResult",
    "CleanStep",
    "DeepSeekClient",
    "FindTargetRequest",
    "FindTargetResult",
    "ModelCallError",
    "ModelClient",
    "ModelReply",
    "SnapshotElement",
    "cost_of",
    "estimate_tokens",
]
