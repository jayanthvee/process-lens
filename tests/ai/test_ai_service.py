"""PL-122: the two AI call types, the pipeline around them, and the ai_calls ledger.

Every service here is built with ``StubClient``. No test imports a provider SDK, opens a socket or
needs a key: the stub is the model.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from services.ai import (
    AiService,
    CleanRecordingRequest,
    FindTargetRequest,
    FindTargetResult,
    ModelReply,
)


class StubClient:
    """A model client that answers from a script and records what it was sent."""

    provider = "deepseek"

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, model: str, system: str, user: str) -> ModelReply:
        self.calls.append({"model": model, "system": system, "user": user})
        answer = self.answers.pop(0) if self.answers else {"token": None}
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return ModelReply(
            text=text,
            tokens_in=max(1, len(user) // 4),
            tokens_out=max(1, len(text) // 4),
            cost_usd=Decimal("0.000500"),
        )


def a_snapshot(**extra: Any) -> list[dict[str, Any]]:
    card: dict[str, Any] = {
        "token": "e7",
        "role": "button",
        "name": "Oak Standing Desk",
        "label": "",
        "placeholder": "",
        "text": "Oak Standing Desk",
        "section": "Products",
        "visible": True,
        "enabled": True,
        "tag": "div",
        "ladder": [
            {"by": "role", "role": "button", "name": "Oak Standing Desk"},
            {"by": "css", "selector": "div.o_kanban_record:nth-of-type(1)"},
        ],
    }
    card.update(extra)
    return [card]


def a_request(tenant_id: uuid.UUID, **extra: Any) -> FindTargetRequest:
    fields: dict[str, Any] = {
        "tenant_id": tenant_id,
        "workflow_id": uuid.uuid4(),
        "step_id": "click-2",
        "origin": "http://localhost:8069",
        "intent": "open the product the search found",
        "action": "click",
        "hints": [{"by": "role", "role": "generic", "name": "Oak Standing Desk"}],
        "snapshot": a_snapshot(),
    }
    fields.update(extra)
    return FindTargetRequest(**fields)


def a_service(
    conn: psycopg.Connection,
    *answers: Any,
    **extra: Any,
) -> tuple[AiService, StubClient]:
    client = StubClient(list(answers))
    return (
        AiService(
            conn,
            client=client,
            model="deepseek-flash",
            escalation_model=extra.pop("escalation_model", None),
            **extra,
        ),
        client,
    )


def _call_rows(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT id, task, provider, model, request, response, validation, escalated_from, "
            "tokens_in, tokens_out, cost_usd FROM ai_calls WHERE tenant_id = %s ORDER BY id",
            (tenant_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def test_find_target_returns_the_one_element_the_model_named(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    service, client = a_service(conn, {"token": "e7"})

    result = service.find_target(a_request(tenant_id))

    assert result.status == "found"
    assert result.token == "e7"
    assert result.element is not None
    assert result.element.name == "Oak Standing Desk"
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "deepseek-flash"

    rows = _call_rows(conn, tenant_id)
    assert len(rows) == 1
    assert rows[0]["task"] == "find_target"
    assert rows[0]["provider"] == "deepseek"
    assert rows[0]["validation"] == "pass"
    assert rows[0]["tokens_in"] and rows[0]["tokens_out"]


def test_a_typed_value_is_redacted_before_the_model_and_the_ledger(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    snapshot = a_snapshot(value="799", typed_value="799")
    service, client = a_service(conn, {"token": "e7"})

    service.find_target(a_request(tenant_id, snapshot=snapshot))

    assert "799" not in client.calls[0]["user"]
    rows = _call_rows(conn, tenant_id)
    assert "799" not in json.dumps(rows[0]["request"])


def test_a_schema_failure_is_logged_as_fail_and_escalates_once(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    service, client = a_service(
        conn, "this is not JSON", {"token": "e7"}, escalation_model="deepseek-v4-pro"
    )

    result = service.find_target(a_request(tenant_id))

    assert result.status == "found"
    assert [call["model"] for call in client.calls] == [
        "deepseek-flash",
        "deepseek-v4-pro",
    ]
    rows = _call_rows(conn, tenant_id)
    assert [row["model"] for row in rows] == ["deepseek-flash", "deepseek-v4-pro"]
    assert rows[0]["validation"] == "fail"
    assert rows[1]["validation"] == "pass"
    assert rows[1]["escalated_from"] == rows[0]["id"]


def test_a_token_that_is_not_in_the_snapshot_is_refused_by_the_code_gate(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    service, _client = a_service(conn, {"token": "e999"})

    result = service.find_target(a_request(tenant_id))

    assert result.status == "refused"
    assert "not in the snapshot" in (result.reason or "")
    rows = _call_rows(conn, tenant_id)
    assert rows[0]["validation"] == "fail"


def test_a_destructive_button_is_refused_for_a_click(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    snapshot = a_snapshot(name="Delete product", text="Delete product")
    service, _client = a_service(conn, {"token": "e7"})

    result = service.find_target(a_request(tenant_id, snapshot=snapshot))

    assert result.status == "refused"
    assert "destructive" in (result.reason or "").lower()


def test_a_text_box_is_refused_for_a_click(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    snapshot = a_snapshot(role="textbox", name="Search...", text="")
    service, _client = a_service(conn, {"token": "e7"})

    result = service.find_target(a_request(tenant_id, snapshot=snapshot))

    assert result.status == "refused"
    assert "textbox" in (result.reason or "").lower()


def test_not_here_is_an_answer_not_a_guess(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    service, _client = a_service(conn, {"token": None})

    result = service.find_target(a_request(tenant_id))

    assert result.status == "not_here"
    assert result.token is None


def test_no_key_is_unavailable_and_never_crashes(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    service = AiService(conn, client=None)

    result = service.find_target(a_request(tenant_id))

    assert isinstance(result, FindTargetResult)
    assert result.status == "unavailable"
    assert _call_rows(conn, tenant_id) == []


def test_a_tenant_policy_that_does_not_allow_the_provider_is_unavailable(
    conn: psycopg.Connection,
) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (name) VALUES ('Default policy tenant') RETURNING id")
        closed_tenant = cur.fetchone()[0]
    service, client = a_service(conn, {"token": "e7"})

    result = service.find_target(a_request(closed_tenant))

    assert result.status == "unavailable"
    assert client.calls == []
    assert _call_rows(conn, closed_tenant) == []


def test_clean_recording_keeps_drops_and_names_steps(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    service, _client = a_service(
        conn,
        {
            "steps": [
                {"id": "fill-1", "keep": True, "intent": "type the product name"},
                {"id": "click-1", "keep": False, "intent": ""},
            ]
        },
    )

    result = service.clean_recording(
        CleanRecordingRequest(
            tenant_id=tenant_id,
            steps=[
                {
                    "id": "fill-1",
                    "action": "fill",
                    "targets": [{"by": "role", "role": "searchbox", "name": "Search..."}],
                    "value": {"kind": "constant", "value": "799"},
                },
                {"id": "click-1", "action": "click", "targets": []},
            ],
        )
    )

    assert result.status == "ok"
    assert [(step.id, step.keep, step.intent) for step in result.steps] == [
        ("fill-1", True, "type the product name"),
        ("click-1", False, ""),
    ]
    rows = _call_rows(conn, tenant_id)
    assert rows[-1]["task"] == "clean_recording"
    assert rows[-1]["validation"] == "pass"


def test_clean_recording_cannot_add_a_step(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    service, _client = a_service(
        conn,
        {
            "steps": [
                {"id": "fill-1", "keep": True, "intent": "type the product name"},
                {"id": "not-recorded", "keep": True, "intent": "do something else"},
            ]
        },
    )

    result = service.clean_recording(
        CleanRecordingRequest(
            tenant_id=tenant_id,
            steps=[{"id": "fill-1", "action": "fill", "targets": []}],
        )
    )

    assert result.status == "refused"
    assert "not a recorded step" in (result.reason or "")
    rows = _call_rows(conn, tenant_id)
    assert rows[-1]["validation"] == "fail"


def test_clean_recording_strips_a_recorded_value(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    service, client = a_service(
        conn, {"steps": [{"id": "fill-1", "keep": True, "intent": "type the product name"}]}
    )

    service.clean_recording(
        CleanRecordingRequest(
            tenant_id=tenant_id,
            steps=[
                {
                    "id": "fill-1",
                    "action": "fill",
                    "targets": [],
                    "value": {"kind": "constant", "value": "799"},
                }
            ],
        )
    )

    assert "799" not in client.calls[0]["user"]
    rows = _call_rows(conn, tenant_id)
    assert "799" not in json.dumps(rows[-1]["request"])


def test_an_input_that_is_not_a_find_target_request_never_reaches_the_model(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    service, client = a_service(conn, {"token": "e7"})

    with pytest.raises(ValueError):
        service.find_target(a_request(tenant_id, action="teleport"))

    assert client.calls == []
