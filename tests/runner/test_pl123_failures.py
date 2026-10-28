"""PL-123's failure words: the app's refusal toast, and the back control found by its intent.

Both are ways a row parks with the truth instead of "couldn't find". The toast names the field the
app refused; the back control is found by the sentence the person taught even when the recorded CSS
does not match the wide window's breadcrumb.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import psycopg

from services.ai import AiService, ModelReply
from services.runner.executor import PageSnapshot, StepCommand, StepResult
from services.runner.finding import TargetFinder
from services.runner.testing import FakeExecutor, ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

BACK = [{"by": "css", "selector": "button.o_back_button.btn.btn-link"}]

FAILED_SAVE_STEPS = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/leads/new"},
    {
        "id": "fill-email",
        "action": "fill",
        "targets": [{"by": "label", "text": "Email"}],
        "value": {"kind": "input", "column": "email"},
    },
    {
        "id": "save",
        "action": "click",
        "commit": True,
        "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
    },
]

BACK_STEPS = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/products"},
    {
        "id": "leave",
        "action": "click",
        "name": "Click button",
        "intent": "go back to the product list",
        "targets": BACK,
    },
    {
        "id": "done",
        "action": "end_record",
        "outcome": "verified",
        "reason": "Read the saved list.",
    },
]


class StubModel:
    provider = "deepseek"

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, str]] = []

    def complete(self, *, model: str, system: str, user: str) -> ModelReply:
        self.calls.append({"model": model, "system": system, "user": user})
        answer = self.answers.pop(0) if self.answers else {"token": None}
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return ModelReply(text=text, tokens_in=1, tokens_out=1, cost_usd=Decimal("0"))


class BackPage(FakeExecutor):
    """A wide window: the back arrow's CSS fails, and the breadcrumb is the found element."""

    def perform(self, command: StepCommand, on_dispatched: Any = None) -> StepResult:
        if any(rung.get("by") == "snap" for rung in command.step.get("targets", [])):
            return StepResult(status="ok", rung=0)
        return super().perform(command, on_dispatched)


def test_a_failed_save_parks_with_the_apps_own_refusal(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe(FAILED_SAVE_STEPS))
    base.add_input_record(key="row-1", email="a@x.com")
    run_id = base.create_run()
    scripted = ScriptedRun.build(
        conditions={"save": False},
        condition_observations={"save": {"refusal": "Invalid fields: Name"}},
    )

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"row-1": "parked"}
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        reason = cur.fetchone()[0]
    assert reason == "Demo Crm didn't save it: Invalid fields: Name"
    assert "couldn't find" not in reason.lower()


def test_the_back_control_is_found_by_its_intent_when_the_css_misses(
    seed: Callable[..., Seed],
    database_url: str,
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe(BACK_STEPS))
    base.add_input_record(key="row-1", email="a@x.com")
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE tenants SET data_policy = %s WHERE id = %s",
            (
                psycopg.types.json.Jsonb({"pii_providers": ["deepseek"], "synthetic_only": True}),
                base.tenant_id,
            ),
        )
    run_id = base.create_run()
    destination = ScriptedRun.build(plan={"leave": "locator_failure"})
    stub = StubModel([{"token": "e7"}])
    page = BackPage(destination.destination, plan={"leave": "locator_failure"})
    page.snapshot_result = PageSnapshot(
        origin=ORIGIN,
        elements=[
            {
                "token": "e7",
                "role": "link",
                "name": "Products",
                "visible": True,
                "enabled": True,
                "tag": "a",
            }
        ],
    )
    worker = Worker(
        database_url,
        worker_id="worker-1",
        executor=page,
        reconciler=destination.reconciler,
        clock=destination.clock,
        finder=TargetFinder(conn, AiService(conn, client=stub, model="deepseek-flash")),
    )

    assert worker.run_once() is True

    assert base.states(run_id) == {"row-1": "verified"}
    assert len(stub.calls) == 1
    assert "go back to the product list" in stub.calls[0]["user"]
    assert len(page.snapshots) == 1
