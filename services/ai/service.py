"""The AI service: validate, redact, call, validate, gate, log, return.

This is the only module that holds a provider key or talks to a model. Its public surface is the two
call types PL-122 builds:

* :meth:`AiService.find_target` -- one element token from a page snapshot, or ``not_here``.
* :meth:`AiService.clean_recording` -- per recorded step, keep/drop and an intent.

Every call goes through the same pipeline, in the order ``AI_BOUNDARIES.md`` states. A caller
cannot reach a model any other way, and cannot receive an answer that skipped the output schema or
the call type's code gate. Every call -- including a failed one -- writes one ``ai_calls`` row.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import jsonschema
import psycopg
import psycopg.types.json
from psycopg.rows import dict_row

from .client import (
    DeepSeekClient,
    ModelCallError,
    ModelClient,
    ModelReply,
)
from .redact import redact_clean_payload, redact_find_payload
from .schemas import (
    CLEAN_INPUT_SCHEMA,
    CLEAN_OUTPUT_SCHEMA,
    CLEAN_SYSTEM_PROMPT,
    FIND_INPUT_SCHEMA,
    FIND_OUTPUT_SCHEMA,
    FIND_SYSTEM_PROMPT,
)

Status = Literal["found", "not_here", "refused", "unavailable", "ok"]

# Roles a fill may point at (D-026 / AI_BOUNDARIES: "a text box or combobox").
_FILL_ROLES = frozenset({"textbox", "searchbox", "combobox"})
# A select opens a combobox's list; a native select is a combobox, a custom one is a listbox.
_SELECT_ROLES = frozenset({"combobox", "listbox"})
# A press acts on the focused field.
_PRESS_ROLES = frozenset({"textbox", "searchbox", "combobox", "listbox"})
_CHECK_ROLES = frozenset({"checkbox", "radio", "switch"})
# A click needs something clickable. These are fields, not controls.
_NOT_CLICKABLE = frozenset({"textbox", "searchbox", "slider", "spinbutton"})

_DESTRUCTIVE = re.compile(r"\b(delete|cancel|refund|pay|transfer)", re.IGNORECASE)

_TAG_ROLES = {
    "input": "textbox",
    "textarea": "textbox",
    "select": "combobox",
    "button": "button",
    "a": "link",
}


@dataclass(frozen=True)
class SnapshotElement:
    """One element the page snapshot offered the model."""

    token: str
    role: str = ""
    name: str = ""
    label: str = ""
    placeholder: str = ""
    text: str = ""
    section: str = ""
    visible: bool = True
    enabled: bool = True
    tag: str = ""
    ladder: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class FindTargetRequest:
    """One run-time question: which element on this page is the step's target?"""

    tenant_id: uuid.UUID
    workflow_id: uuid.UUID
    step_id: str
    origin: str
    intent: str
    action: str
    hints: tuple[dict[str, Any], ...] = ()
    snapshot: tuple[dict[str, Any], ...] = ()
    allow_destructive_control: bool = False
    page_url: str = ""


@dataclass(frozen=True)
class FindTargetResult:
    """The typed answer a caller may use. Only ``found`` carries an element."""

    status: Literal["found", "not_here", "refused", "unavailable"]
    token: str | None = None
    element: SnapshotElement | None = None
    reason: str | None = None
    ai_call_id: int | None = None


@dataclass(frozen=True)
class CleanRecordingRequest:
    """One teaching-time question: which recorded steps belong, and what are they for?"""

    tenant_id: uuid.UUID
    steps: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CleanStep:
    id: str
    keep: bool
    intent: str


@dataclass(frozen=True)
class CleanRecordingResult:
    status: Literal["ok", "refused", "unavailable"]
    steps: tuple[CleanStep, ...] = ()
    reason: str | None = None
    ai_call_id: int | None = None


@dataclass
class _Outcome:
    status: Literal["pass", "refused", "unavailable"]
    parsed: dict[str, Any] | None = None
    reason: str | None = None
    ai_call_id: int | None = None


def _parse_json(text: str) -> dict[str, Any]:
    """Read the one JSON object a JSON-only response carries, tolerating a markdown fence."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped[3:].strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("the answer is not a JSON object")
    return parsed


def _role_of(element: SnapshotElement) -> str:
    if element.role.strip():
        return element.role.strip().lower()
    return _TAG_ROLES.get(element.tag.strip().lower(), "generic")


def _element_from(raw: Mapping[str, Any]) -> SnapshotElement:
    ladder: list[dict[str, Any]] = []
    candidates = raw.get("ladder")
    if isinstance(candidates, list):
        for rung in candidates:
            if isinstance(rung, dict):
                ladder.append(dict(rung))
    return SnapshotElement(
        token=str(raw.get("token", "")),
        role=str(raw.get("role") or ""),
        name=str(raw.get("name") or ""),
        label=str(raw.get("label") or ""),
        placeholder=str(raw.get("placeholder") or ""),
        text=str(raw.get("text") or ""),
        section=str(raw.get("section") or ""),
        visible=bool(raw.get("visible", True)),
        enabled=bool(raw.get("enabled", True)),
        tag=str(raw.get("tag") or ""),
        ladder=tuple(ladder),
    )


def _gate_find(
    parsed: Mapping[str, Any],
    elements: Mapping[str, SnapshotElement],
    token_counts: Mapping[str, int],
    action: str,
    allow_destructive: bool,
) -> str | None:
    """The call type's code check: exists, unique, role fits, not destructive. A reason or None."""
    token = parsed.get("token")
    if token is None:
        return None
    if not isinstance(token, str) or token not in elements:
        return "the answer named a token that is not in the snapshot"
    if token_counts.get(token) != 1:
        return "the answer named a token that is not unique in the snapshot"
    element = elements[token]
    if not element.visible:
        return "the answer named an element that is not visible"
    if not element.enabled:
        return "the answer named an element that is not enabled"
    role = _role_of(element)
    if action == "fill" and role not in _FILL_ROLES:
        return (
            f"the answer named a {role or 'non-field'} for a fill; "
            "a fill needs a text box or combobox"
        )
    if action == "select" and role not in _SELECT_ROLES:
        return (
            f"the answer named a {role or 'non-field'} for a select; "
            "a select needs a combobox or listbox"
        )
    if action == "press" and role not in _PRESS_ROLES:
        return f"the answer named a {role or 'non-control'} for a press; a press needs a field"
    if action == "check" and role not in _CHECK_ROLES:
        return (
            f"the answer named a {role or 'non-control'} for a check; "
            "a check needs a checkbox or switch"
        )
    if action == "click" and role in _NOT_CLICKABLE:
        return f"the answer named a {role} for a click; a click needs something clickable"
    if action == "click" and role != "option" and not allow_destructive:
        name = element.name or element.text or element.label
        if _DESTRUCTIVE.search(name):
            return (
                "the answer named a destructive control; set allow_destructive_control "
                "on the step to permit it"
            )
    return None


def _gate_clean(parsed: Mapping[str, Any], input_ids: list[str]) -> str | None:
    """Only dropping and naming: every recorded step once, no added step, no empty intent."""
    steps = parsed.get("steps")
    if not isinstance(steps, list):
        return "the answer has no steps"
    output_ids = [str(step.get("id", "")) for step in steps if isinstance(step, dict)]
    if len(set(output_ids)) != len(output_ids):
        return "the answer names the same step more than once"
    extra = sorted(set(output_ids) - set(input_ids))
    if extra:
        return f"the answer names a step that is not a recorded step: {extra[0]}"
    missing = sorted(set(input_ids) - set(output_ids))
    if missing:
        return f"the answer leaves out a recorded step: {missing[0]}"
    for step in steps:
        if not isinstance(step, dict):
            return "the answer has a step that is not an object"
        if not isinstance(step.get("keep"), bool):
            return "the answer does not say keep or drop for a step"
        intent = step.get("intent")
        if not isinstance(intent, str):
            return "the answer has no intent for a step"
        if step["keep"] and not intent.strip():
            return "a kept step has no intent"
        if len(intent) > 300:
            return "a step's intent is longer than 300 characters"
    return None


class AiService:
    """The single entry point for both call types."""

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        client: ModelClient | None = None,
        model: str | None = None,
        escalation_model: str | None = None,
    ) -> None:
        self.conn = conn
        self.client = client
        # The owner names the model (``DEEPSEEK_MODEL``). There is no default: a key without a
        # model is as unavailable as no key at all, so a run never spends the owner's credit on a
        # model they did not choose.
        self.model = (model or "").strip() or None
        self.escalation_model = escalation_model

    @classmethod
    def from_env(
        cls,
        conn: psycopg.Connection,
        environ: Mapping[str, str] | None = None,
    ) -> AiService:
        """Build the service from ``DEEPSEEK_*``. With no key or no model, it is unavailable."""
        env = os.environ if environ is None else environ
        key = str(env.get("DEEPSEEK_API_KEY") or "").strip()
        model = str(env.get("DEEPSEEK_MODEL") or "").strip() or None
        escalation = str(env.get("DEEPSEEK_ESCALATION_MODEL") or "").strip() or None
        client = DeepSeekClient(api_key=key) if key and model else None
        return cls(
            conn,
            client=client,
            model=model,
            escalation_model=escalation,
        )

    @property
    def available(self) -> bool:
        return self.client is not None and self.model is not None

    @property
    def provider(self) -> str:
        return self.client.provider if self.client is not None else "deepseek"

    # -- the call types ----------------------------------------------------

    def find_target(self, request: FindTargetRequest) -> FindTargetResult:
        payload: dict[str, Any] = {
            "tenant_id": str(request.tenant_id).strip(),
            "workflow_id": str(request.workflow_id).strip(),
            "step_id": request.step_id,
            "origin": request.origin,
            "page_url": request.page_url,
            "intent": request.intent,
            "action": request.action,
            "allow_destructive_control": bool(request.allow_destructive_control),
            "hints": [dict(hint) for hint in request.hints],
            "snapshot": [dict(element) for element in request.snapshot],
        }
        try:
            jsonschema.validate(payload, FIND_INPUT_SCHEMA)
        except jsonschema.ValidationError as exc:
            raise ValueError(f"invalid find_target input: {exc.message}") from exc

        redacted = redact_find_payload(payload)
        elements = {
            str(element.get("token")): _element_from(element)
            for element in redacted["snapshot"]
            if isinstance(element, dict) and element.get("token")
        }
        token_counts = Counter(
            str(element.get("token"))
            for element in redacted["snapshot"]
            if isinstance(element, dict) and element.get("token")
        )
        action = str(redacted["action"])
        allow_destructive = bool(redacted.get("allow_destructive_control"))

        def gate(parsed: dict[str, Any]) -> str | None:
            return _gate_find(parsed, elements, token_counts, action, allow_destructive)

        outcome = self._run_pipeline(
            tenant_id=request.tenant_id,
            task="find_target",
            redacted=redacted,
            system_prompt=FIND_SYSTEM_PROMPT,
            output_schema=FIND_OUTPUT_SCHEMA,
            gate=gate,
            personal=True,
        )
        if outcome.status != "pass" or outcome.parsed is None:
            return FindTargetResult(
                status="unavailable" if outcome.status == "unavailable" else "refused",
                reason=outcome.reason,
                ai_call_id=outcome.ai_call_id,
            )

        token = outcome.parsed.get("token")
        if token is None:
            return FindTargetResult(status="not_here", ai_call_id=outcome.ai_call_id)
        return FindTargetResult(
            status="found",
            token=str(token),
            element=elements[str(token)],
            ai_call_id=outcome.ai_call_id,
        )

    def clean_recording(self, request: CleanRecordingRequest) -> CleanRecordingResult:
        payload: dict[str, Any] = {
            "tenant_id": str(request.tenant_id).strip(),
            "steps": [dict(step) for step in request.steps],
        }
        try:
            jsonschema.validate(payload, CLEAN_INPUT_SCHEMA)
        except jsonschema.ValidationError as exc:
            raise ValueError(f"invalid clean_recording input: {exc.message}") from exc

        redacted = redact_clean_payload(payload)
        input_ids = [str(step["id"]) for step in redacted["steps"]]

        def gate(parsed: dict[str, Any]) -> str | None:
            return _gate_clean(parsed, input_ids)

        outcome = self._run_pipeline(
            tenant_id=request.tenant_id,
            task="clean_recording",
            redacted=redacted,
            system_prompt=CLEAN_SYSTEM_PROMPT,
            output_schema=CLEAN_OUTPUT_SCHEMA,
            gate=gate,
            personal=False,
        )
        if outcome.status != "pass" or outcome.parsed is None:
            return CleanRecordingResult(
                status="unavailable" if outcome.status == "unavailable" else "refused",
                reason=outcome.reason,
                ai_call_id=outcome.ai_call_id,
            )
        steps = tuple(
            CleanStep(id=str(step["id"]), keep=bool(step["keep"]), intent=str(step["intent"]))
            for step in outcome.parsed["steps"]
        )
        return CleanRecordingResult(status="ok", steps=steps, ai_call_id=outcome.ai_call_id)

    # -- the pipeline ------------------------------------------------------

    def _run_pipeline(
        self,
        *,
        tenant_id: uuid.UUID,
        task: str,
        redacted: dict[str, Any],
        system_prompt: str,
        output_schema: dict[str, Any],
        gate: Callable[[dict[str, Any]], str | None],
        personal: bool,
    ) -> _Outcome:
        if self.client is None or self.model is None:
            return _Outcome("unavailable", reason="no model is configured")
        if not self._provider_allowed(tenant_id, personal=personal):
            return _Outcome(
                "unavailable",
                reason="the tenant's data policy does not allow this provider for this payload",
            )

        primary = self.model
        models = [primary]
        if self.escalation_model and self.escalation_model != primary:
            models.append(self.escalation_model)

        user = json.dumps(redacted, ensure_ascii=False, sort_keys=True)
        input_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()
        previous: int | None = None
        last_reason = "the model did not answer"
        saw_answer = False

        for model in models:
            started = time.monotonic()
            try:
                reply: ModelReply = self.client.complete(
                    model=model, system=system_prompt, user=user
                )
            except ModelCallError as exc:
                latency = int((time.monotonic() - started) * 1000)
                previous = self._write_call(
                    tenant_id=tenant_id,
                    task=task,
                    model=model,
                    input_hash=input_hash,
                    request=redacted,
                    response={"error": str(exc)},
                    validation="fail",
                    escalated_from=previous,
                    tokens_in=None,
                    tokens_out=None,
                    cost_usd=None,
                    latency_ms=latency,
                )
                last_reason = str(exc)
                continue

            saw_answer = True
            latency = int((time.monotonic() - started) * 1000)
            parsed: dict[str, Any] | None = None
            error: str | None = None
            try:
                parsed = _parse_json(reply.text)
                jsonschema.validate(parsed, output_schema)
            except (ValueError, jsonschema.ValidationError) as exc:
                error = f"the answer did not match the output schema: {exc}"
            else:
                error = gate(parsed)

            call_id = self._write_call(
                tenant_id=tenant_id,
                task=task,
                model=model,
                input_hash=input_hash,
                request=redacted,
                response=parsed if parsed is not None else {"raw": reply.text},
                validation="pass" if error is None else "fail",
                escalated_from=previous,
                tokens_in=reply.tokens_in,
                tokens_out=reply.tokens_out,
                cost_usd=reply.cost_usd,
                latency_ms=latency,
            )
            if error is None and parsed is not None:
                return _Outcome("pass", parsed=parsed, ai_call_id=call_id)
            previous = call_id
            last_reason = error or last_reason

        return _Outcome(
            "refused" if saw_answer else "unavailable",
            reason=last_reason,
            ai_call_id=previous,
        )

    def _provider_allowed(self, tenant_id: uuid.UUID, *, personal: bool) -> bool:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT data_policy FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
        if row is None:
            return False
        if not personal:
            return True
        policy = row.get("data_policy") if isinstance(row, dict) else None
        policy = policy if isinstance(policy, dict) else {}
        if policy.get("synthetic_only") is True:
            return True
        allowed = policy.get("pii_providers")
        return isinstance(allowed, list) and self.provider in allowed

    def _write_call(
        self,
        *,
        tenant_id: uuid.UUID,
        task: str,
        model: str,
        input_hash: str,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        validation: str,
        escalated_from: int | None,
        tokens_in: int | None,
        tokens_out: int | None,
        cost_usd: Any,
        latency_ms: int,
    ) -> int:
        """Write one audit row. A failed call is a row too; the log is append-only."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_calls (tenant_id, task, provider, model, input_hash, request, "
                "response, validation, escalated_from, tokens_in, tokens_out, cost_usd, "
                "latency_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    tenant_id,
                    task,
                    self.provider,
                    model,
                    input_hash,
                    psycopg.types.json.Jsonb(request),
                    psycopg.types.json.Jsonb(response) if response is not None else None,
                    validation,
                    escalated_from,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    latency_ms,
                ),
            )
            return int(cur.fetchone()[0])


__all__ = [
    "AiService",
    "CleanRecordingRequest",
    "CleanRecordingResult",
    "CleanStep",
    "FindTargetRequest",
    "FindTargetResult",
    "SnapshotElement",
]
