"""Run-time finding: the remembered element first, then one model call, then a code gate.

A recipe never changes here. The only durable things this module writes are ``target_memory`` rows
(the ladder that worked) and the ``ai_calls`` rows the AI service writes itself. A hit lets the next
run skip the model; a miss parks the row with a sentence a person can read.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
import psycopg.types.json
from psycopg.rows import dict_row

from services.ai import AiService, FindTargetRequest, SnapshotElement

from .executor import Executor, PageSnapshot, SnapshotUnavailable, StepCommand

# D-026: the ceiling for one record. A task that works costs nothing on later runs; a task that is
# lost cannot spend an unbounded amount of the owner's credit looking for the page.
MAX_AI_CALLS_PER_RECORD = 6

_LADDER_MEMBERS = frozenset({"role", "testid", "label", "css", "text_fuzzy"})


def find_failed_reason(intent: str) -> str:
    """The one sentence a row carries when the model could not point at an element."""
    return f"ProcessLens couldn't find '{intent}' on this page."


@dataclass(frozen=True)
class FindingAnswer:
    """What one run-time question answered, before the runner acts on it."""

    status: Literal["found", "not_here", "refused", "unavailable"]
    token: str | None = None
    element: SnapshotElement | None = None
    ladder: list[dict[str, Any]] = field(default_factory=list)
    origin: str = ""
    reason: str | None = None
    # True only when the AI service actually made a model call (or tried to and logged one). A
    # snapshot with no elements, a missing key or a policy refusal costs nothing.
    called: bool = False


class TargetFinder:
    """The runner's seam onto ``target_memory`` and the AI service."""

    def __init__(self, conn: psycopg.Connection, ai: AiService | None) -> None:
        self.conn = conn
        self.ai = ai

    @property
    def available(self) -> bool:
        return self.ai is not None and self.ai.available

    def memory(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        step_id: str,
        origin: str,
    ) -> list[dict[str, Any]] | None:
        """The ladder this step last used on this site, or None when nothing is remembered."""
        if not origin:
            return None
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT ladder FROM target_memory WHERE tenant_id = %s AND workflow_id = %s "
                "AND step_id = %s AND origin = %s",
                (tenant_id, workflow_id, step_id, origin),
            )
            row = cur.fetchone()
        if row is None:
            return None
        ladder = row.get("ladder")
        if not isinstance(ladder, list):
            return None
        cleaned = [dict(rung) for rung in ladder if isinstance(rung, dict) and rung.get("by")]
        return cleaned[:5] or None

    def remember(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        step_id: str,
        origin: str,
        ladder: list[dict[str, Any]],
        used: bool = False,
    ) -> None:
        """Store the ladder that worked, or count one use of the ladder already stored."""
        if not origin or not ladder:
            return
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO target_memory "
                "(tenant_id, workflow_id, step_id, origin, ladder, hit_count) "
                "VALUES (%s, %s, %s, %s, %s, 1) "
                "ON CONFLICT (tenant_id, workflow_id, step_id, origin) DO UPDATE "
                "SET ladder = EXCLUDED.ladder, last_used_at = now(), "
                "hit_count = target_memory.hit_count + %s",
                (
                    tenant_id,
                    workflow_id,
                    step_id,
                    origin,
                    psycopg.types.json.Jsonb(ladder),
                    1 if used else 0,
                ),
            )

    def ask(
        self,
        executor: Executor,
        command: StepCommand,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        step_id: str,
        intent: str,
        action: str,
        allow_destructive: bool = False,
    ) -> FindingAnswer:
        """Take a snapshot and ask the one call type for a token. Never acts on the answer."""
        if self.ai is None or not self.ai.available:
            return FindingAnswer(status="unavailable", reason="no model is configured")
        try:
            snapshot: PageSnapshot = executor.snapshot(command)
        except SnapshotUnavailable as exc:
            return FindingAnswer(status="unavailable", reason=str(exc))
        if not snapshot.elements:
            return FindingAnswer(status="not_here", reason="the page snapshot was empty")
        origin = snapshot.origin or (
            command.allowed_origins[0] if len(command.allowed_origins) == 1 else ""
        )
        try:
            result = self.ai.find_target(
                FindTargetRequest(
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    step_id=step_id,
                    origin=origin,
                    intent=intent,
                    action=action,
                    hints=tuple(
                        target
                        for target in command.step.get("targets", [])
                        if isinstance(target, Mapping)
                    ),
                    snapshot=tuple(dict(element) for element in snapshot.elements),
                    allow_destructive_control=allow_destructive,
                    page_url="",
                )
            )
        except ValueError as exc:
            # A snapshot the call type will not accept (an unreadable origin, say) is not a reason
            # to kill the worker: the row parks the way it did before this ticket.
            return FindingAnswer(status="unavailable", reason=str(exc))
        ladder = _ladder_of(result.element) if result.element is not None else []
        return FindingAnswer(
            status=result.status,
            token=result.token,
            element=result.element,
            ladder=ladder,
            origin=snapshot.origin,
            reason=result.reason,
            called=result.ai_call_id is not None,
        )


def _ladder_of(element: SnapshotElement) -> list[dict[str, Any]]:
    """The element's own ladder, sanitised for storage and the next run's resolution."""
    cleaned: list[dict[str, Any]] = []
    for rung in element.ladder:
        by = str(rung.get("by") or "").strip()
        if by not in _LADDER_MEMBERS:
            continue
        if by == "role" and not str(rung.get("name") or "").strip():
            continue
        if by in ("testid", "text_fuzzy") and not str(rung.get("value") or "").strip():
            continue
        if by == "label" and not str(rung.get("text") or "").strip():
            continue
        if by == "css" and not str(rung.get("selector") or "").strip():
            continue
        cleaned.append(dict(rung))
    if not cleaned:
        if element.role.strip() and element.name.strip():
            cleaned.append({"by": "role", "role": element.role, "name": element.name})
        if element.label.strip():
            cleaned.append({"by": "label", "text": element.label})
    return cleaned[:5]


__all__ = [
    "MAX_AI_CALLS_PER_RECORD",
    "FindingAnswer",
    "TargetFinder",
    "find_failed_reason",
]
