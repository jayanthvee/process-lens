"""PL-123 item 1 at the API: Stop asks the model which steps the page did by itself.

The route is the panel's one call at Stop. With no model it answers the recording unchanged, so the
panel behaves exactly as it did before this ticket. With a stub model it drops a step and names the
rest; the model is never a real provider here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from services.ai import AiService, ModelReply
from tests.db.seed import Ledger

RECORDING = [
    {
        "seq": 1,
        "kind": "fill",
        "url": "http://localhost:8069/odoo/products/17",
        "step": {
            "id": "fill-1",
            "action": "fill",
            "name": "Fill Sales Price",
            "targets": [{"by": "role", "role": "textbox", "name": "Sales Price"}],
            "value": {"kind": "constant", "value": "799"},
        },
    },
    {
        "seq": 2,
        "kind": "click",
        "url": "http://localhost:8069/odoo/products/17",
        "step": {
            "id": "click-1",
            "action": "click",
            "name": "Click All Apps",
            "targets": [{"by": "role", "role": "link", "name": "All Apps"}],
        },
    },
    {
        "seq": 3,
        "kind": "navigation",
        "url": "http://localhost:8069/odoo/products",
        "step": None,
    },
]


class StubModel:
    """A model stub: one scripted JSON answer, no provider call."""

    provider = "deepseek"

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.calls: list[dict[str, str]] = []

    def complete(self, *, model: str, system: str, user: str) -> ModelReply:
        self.calls.append({"model": model, "system": system, "user": user})
        return ModelReply(text=json.dumps(self.answer), tokens_in=10, tokens_out=10)


def _user(ledger: Ledger, monkeypatch: MonkeyPatch) -> uuid.UUID:
    user_id = ledger.add_user()
    monkeypatch.setenv("PROCESSLENS_OPERATOR_ID", str(user_id))
    return user_id


def test_with_no_model_the_recording_is_shown_unchanged(
    connected: TestClient, ledger: Ledger, monkeypatch: MonkeyPatch
) -> None:
    _user(ledger, monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    response = connected.post("/api/v1/recordings/clean", json={"recording": RECORDING})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is False
    assert body["steps"] == [
        {"id": "fill-1", "keep": True, "intent": ""},
        {"id": "click-1", "keep": True, "intent": ""},
    ]


def test_a_stub_model_drops_a_stray_click_and_names_the_kept_step(
    connected: TestClient,
    ledger: Ledger,
    monkeypatch: MonkeyPatch,
    conn: psycopg.Connection,
) -> None:
    _user(ledger, monkeypatch)
    stub = StubModel(
        {
            "steps": [
                {"id": "fill-1", "keep": True, "intent": "set Sales Price to the row's New Price"},
                {"id": "click-1", "keep": False, "intent": ""},
            ]
        }
    )

    def from_env(connection: psycopg.Connection) -> AiService:
        return AiService(connection, client=stub, model="deepseek-flash")

    monkeypatch.setattr("services.api.compile.AiService.from_env", from_env)

    response = connected.post("/api/v1/recordings/clean", json={"recording": RECORDING})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["steps"] == [
        {"id": "fill-1", "keep": True, "intent": "set Sales Price to the row's New Price"},
        {"id": "click-1", "keep": False, "intent": ""},
    ]
    assert len(stub.calls) == 1
    with conn.cursor() as cur:
        cur.execute(
            "SELECT task, validation FROM ai_calls WHERE tenant_id = %s ORDER BY id DESC LIMIT 1",
            (ledger.tenant_id,),
        )
        assert cur.fetchone() == ("clean_recording", "pass")


def test_an_answer_that_adds_a_step_is_refused_and_the_recording_is_kept(
    connected: TestClient,
    ledger: Ledger,
    monkeypatch: MonkeyPatch,
) -> None:
    _user(ledger, monkeypatch)
    stub = StubModel(
        {
            "steps": [
                {"id": "fill-1", "keep": True, "intent": "set the price"},
                {"id": "click-1", "keep": False, "intent": ""},
                {"id": "made-up", "keep": True, "intent": "do something else"},
            ]
        }
    )

    def from_env(connection: psycopg.Connection) -> AiService:
        return AiService(connection, client=stub, model="deepseek-flash")

    monkeypatch.setattr("services.api.compile.AiService.from_env", from_env)

    response = connected.post("/api/v1/recordings/clean", json={"recording": RECORDING})

    body = response.json()
    assert body["available"] is False
    assert all(step["keep"] is True for step in body["steps"])
    assert "not a recorded step" in body["reason"]


def test_a_recording_with_no_actionable_steps_asks_nothing(
    connected: TestClient, ledger: Ledger, monkeypatch: MonkeyPatch
) -> None:
    _user(ledger, monkeypatch)

    response = connected.post(
        "/api/v1/recordings/clean", json={"recording": [{"seq": 1, "kind": "focus"}]}
    )

    assert response.status_code == 200
    assert response.json() == {"available": False, "steps": [], "reason": None}
