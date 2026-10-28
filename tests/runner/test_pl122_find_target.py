"""PL-122: a run asks a model to find an element when the remembered one is gone.

The page is scripted, the model is a stub, and the recipe is the owner's price task as recorded on
2026-10-28 (``tests/fixtures/owner/price_update_kanban.json``). The point of every test is the same:
code decides the outcome, and a model may only point at an element or stop the row for a person.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import psycopg.types.json
import pytest

from services.ai import AiService, ModelReply
from services.runner.executor import ConditionResult, PageSnapshot, StepCommand, StepResult
from services.runner.finding import TargetFinder, find_failed_reason
from services.runner.testing import FakeExecutor, ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

OWNER_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "owner" / "price_update_kanban.json"
)

OWNER_REASON = "No search called \u2018Oak Standing Desk\u2019 was found."

# PL-123 writes these sentences at teaching. PL-122 consumes them, so the recorded fixture is
# replayed with the intents a cleaned recording will carry.
OWNER_INTENTS = {
    "fill-1": "type {{row.search}} into the product search box",
    "click-1": "search for the product {{row.search}}",
    "branch-1": "the search result for {{row.search}}",
    "click-2": "open the product the search found",
    "fill-2": "set Sales Price to {{row.sales_price}}",
}

CARD: dict[str, Any] = {
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


class StubClient:
    """The model, scripted. Every call is recorded; nothing leaves this process."""

    provider = "deepseek"

    def __init__(self, answers: list[Any] | None = None) -> None:
        self.answers = list(answers or [])
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


class CardPage(FakeExecutor):
    """An Odoo product list shown as cards, with the recorded targets failing on it.

    The real extension would fail the table-cell target because the product is a card. Here the
    scripted page fails the recorded ladder for the named steps and answers the snapshot with the
    card. A memory ladder or a ``snap`` token succeeds, which is exactly what the real resolver
    does after the page has been tagged.
    """

    def __init__(
        self,
        destination: Any,
        *,
        failing: set[str] | None = None,
        origin: str = ORIGIN,
        card: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            destination,
            plan={step_id: "locator_failure" for step_id in (failing or set())},
        )
        self.origin = origin
        self.card = dict(card or CARD)
        self.failing = set(failing or set())
        self.snapshot_commands: list[StepCommand] = []
        self.snap_attempts: list[StepCommand] = []
        self.memory_attempts: list[StepCommand] = []

    def _is_snap(self, command: StepCommand) -> bool:
        targets = command.step.get("targets") or []
        return bool(targets) and isinstance(targets[0], Mapping) and targets[0].get("by") == "snap"

    def _is_memory(self, command: StepCommand) -> bool:
        targets = command.step.get("targets") or []
        return any(
            isinstance(target, Mapping)
            and target.get("by") == "role"
            and target.get("role") == "button"
            and target.get("name") == "Oak Standing Desk"
            for target in targets
        )

    def prepare(self, command: StepCommand) -> StepResult:
        if self._is_snap(command) or self._is_memory(command):
            self.commands.append(command)
            self.prepared.append(command)
            if self._is_memory(command):
                self.memory_attempts.append(command)
            return StepResult(status="ok", rung=1)
        return super().prepare(command)

    def perform(self, command: StepCommand, on_dispatched: Any = None) -> StepResult:
        if self._is_snap(command):
            self.commands.append(command)
            self.snap_attempts.append(command)
            return StepResult(status="ok", rung=0)
        if self._is_memory(command):
            self.commands.append(command)
            self.memory_attempts.append(command)
            return StepResult(status="ok", rung=1)
        return super().perform(command, on_dispatched)

    def snapshot(self, command: StepCommand) -> PageSnapshot:
        self.snapshot_commands.append(command)
        return PageSnapshot(origin=self.origin, elements=[dict(self.card)], truncated=False)

    def check_condition(
        self,
        condition: Mapping[str, Any],
        *,
        timeout_ms: int,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConditionResult:
        # The second run's memory probe asks whether the remembered card is present. This page has
        # it, so the branch takes today's "present" answer without a model call.
        selector = condition.get("element_present")
        if isinstance(selector, Mapping):
            targets = selector.get("targets")
            if isinstance(targets, list) and any(
                isinstance(target, Mapping)
                and target.get("role") == "button"
                and target.get("name") == "Oak Standing Desk"
                for target in targets
            ):
                return ConditionResult(passed=True, status="ok")
        return super().check_condition(
            condition,
            timeout_ms=timeout_ms,
            step_id=step_id,
            record_key=record_key,
        )


def owner_document() -> dict[str, Any]:
    """The owner's recorded recipe, with the intents a cleaned recording writes."""
    document = copy.deepcopy(json.loads(OWNER_FIXTURE.read_text(encoding="utf-8"))["recipe"])
    for step in document["steps"]:
        intent = OWNER_INTENTS.get(step["id"])
        if intent:
            step["intent"] = intent
    return document


def allow_deepseek(conn: psycopg.Connection, tenant: uuid.UUID) -> None:
    """The trial's tenant policy: synthetic page text may go to the owner's DeepSeek account."""
    conn.execute(
        "UPDATE tenants SET data_policy = %s WHERE id = %s",
        (
            psycopg.types.json.Jsonb({"pii_providers": ["deepseek"], "synthetic_only": True}),
            tenant,
        ),
    )


@pytest.fixture(autouse=True)
def _pl122_database_url(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker opens its own connection; give it the full DSN the test's conn was built from."""
    monkeypatch.setenv("DATABASE_URL", database_url)


def finder(
    conn: psycopg.Connection, stub: StubClient, *, model: str = "deepseek-flash"
) -> TargetFinder:
    ai = AiService(conn, client=stub, model=model, escalation_model=None)
    return TargetFinder(conn, ai)


def record_of(conn: psycopg.Connection, run_id: uuid.UUID) -> tuple[str, str, str | None]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT record_key, state, reason FROM run_records WHERE run_id = %s", (run_id,)
        )
        return cur.fetchone()


def ai_call_count(conn: psycopg.Connection, tenant: uuid.UUID) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_calls WHERE tenant_id = %s", (tenant,))
        return int(cur.fetchone()[0])


def simple_finding_document(
    *, step_id: str = "open-product", intent: str = "open the product the search found"
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = [
        {"id": "open", "action": "navigate", "url": f"{ORIGIN}/products"},
        {
            "id": step_id,
            "action": "click",
            "intent": intent,
            "targets": [{"by": "css", "selector": "td.product-name"}],
        },
        {"id": "done", "action": "end_record", "outcome": "verified", "reason": "opened it"},
    ]
    return recipe(steps)


def run_owner(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    *,
    stub: StubClient,
    failing: set[str],
    card: Mapping[str, Any] | None = None,
    runs: int = 1,
) -> tuple[Seed, list[uuid.UUID], CardPage]:
    base = seed(owner_document())
    allow_deepseek(conn, base.tenant_id)
    base.add_input_record(search="Oak Standing Desk", sales_price="799")
    scripted = ScriptedRun.build()
    page = CardPage(
        scripted.destination,
        failing=failing,
        origin="http://localhost:8069",
        card=card,
    )
    scripted.executor = page
    worker = make_worker(scripted, finder(conn, stub))
    run_ids: list[uuid.UUID] = []
    for _ in range(runs):
        run_id = base.create_run()
        run_ids.append(run_id)
        assert worker.run_once() is True
    return base, run_ids, page


def make_worker(scripted: ScriptedRun, finder_: TargetFinder) -> Worker:
    """A worker over this test's database, with the page and the model scripted."""
    return Worker(
        os.environ["DATABASE_URL"],
        worker_id="worker-1",
        executor=scripted.executor,
        reconciler=scripted.reconciler,
        clock=scripted.clock,
        crash_hooks=scripted.crash_hooks,
        heartbeat_interval=3600.0,
        events=scripted.events,
        gate_wait_seconds=5.0,
        finder=finder_,
    )


def test_the_owner_kanban_row_carries_on_when_the_product_is_a_card(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    stub = StubClient([{"token": "e7"}, {"token": "e7"}])
    base, run_ids, page = run_owner(seed, conn, stub=stub, failing={"click-2"})
    run_id = run_ids[0]

    key, state, reason = record_of(conn, run_id)

    # The branch did not fire: the card is the search result, so the row went on to the click and
    # only parked at the recording's own last step, which never confirmed the save.
    assert key == "oak standing desk"
    assert state == "parked"
    assert reason != OWNER_REASON
    assert reason == "ProcessLens couldn't confirm the change was saved."

    # One call for the branch's question and one for the click that could not find the card.
    assert len(stub.calls) == 2
    assert ai_call_count(conn, base.tenant_id) == 2

    # The click on the product was sent again with the snapshot token first.
    click_two = [c for c in page.snap_attempts if c.step.get("id") == "click-2"]
    assert len(click_two) == 1
    assert click_two[0].step["targets"][0] == {"by": "snap", "value": "e7"}

    # The hit is remembered for the next run, under the step and origin it worked on.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT step_id, origin, ladder FROM target_memory WHERE tenant_id = %s "
            "ORDER BY step_id",
            (base.tenant_id,),
        )
        rows = cur.fetchall()
    assert [row[0] for row in rows] == ["branch-1", "click-2"]
    assert all(row[1] == "http://localhost:8069" for row in rows)
    assert rows[1][2][0] == {
        "by": "role",
        "role": "button",
        "name": "Oak Standing Desk",
    }


def test_the_second_run_uses_the_remembered_element_and_makes_no_model_call(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    stub = StubClient([{"token": "e7"}, {"token": "e7"}])
    base, run_ids, page = run_owner(seed, conn, stub=stub, failing={"click-2"}, runs=2)

    first, second = run_ids
    _key, first_state, first_reason = record_of(conn, first)
    _key, second_state, second_reason = record_of(conn, second)

    assert first_state == "parked"
    assert second_state == "parked"
    assert second_reason == first_reason
    assert len(stub.calls) == 2  # both calls happened on the first run
    assert len(page.snapshot_commands) == 2  # branch-1 and click-2, on the first run only

    with conn.cursor() as cur:
        cur.execute(
            "SELECT step_id, hit_count FROM target_memory WHERE tenant_id = %s ORDER BY step_id",
            (base.tenant_id,),
        )
        hits = dict(cur.fetchall())
    assert hits["branch-1"] >= 2
    assert hits["click-2"] >= 2


def test_a_branch_that_parks_on_an_absent_result_asks_the_intent_first(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    stub = StubClient([{"token": "e7"}])
    base, run_ids, page = run_owner(seed, conn, stub=stub, failing=set())
    run_id = run_ids[0]

    _key, state, reason = record_of(conn, run_id)

    # The card is the search result, so "no cell" is not "no product": the branch did not fire.
    assert state == "parked"
    assert reason != OWNER_REASON
    assert len(stub.calls) == 1
    assert page.snapshot_commands[0].step["id"] == "branch-1"


def test_not_here_keeps_todays_answer_for_a_condition(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    stub = StubClient([{"token": None}])
    base, run_ids, _page = run_owner(seed, conn, stub=stub, failing=set())
    run_id = run_ids[0]

    _key, state, reason = record_of(conn, run_id)

    assert state == "parked"
    assert reason == OWNER_REASON
    assert len(stub.calls) == 1


def _gated_recipe() -> dict[str, Any]:
    return simple_finding_document()


def _run_gated(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    *,
    stub: StubClient,
    card: Mapping[str, Any] | None = None,
    step_id: str = "open-product",
    intent: str = "open the product the search found",
) -> tuple[Seed, uuid.UUID]:
    base = seed(simple_finding_document(step_id=step_id, intent=intent))
    allow_deepseek(conn, base.tenant_id)
    base.add_input_record(key="row-1", email="a@x.com")
    scripted = ScriptedRun.build()
    page = CardPage(scripted.destination, failing={"open-product"}, card=card)
    scripted.executor = page
    run_id = base.create_run()
    assert make_worker(scripted, finder(conn, stub)).run_once() is True
    return base, run_id


def test_a_token_that_is_not_in_the_snapshot_parks_with_the_sentence(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    stub = StubClient([{"token": "e999"}])
    base, run_id = _run_gated(seed, conn, stub=stub)

    _key, state, reason = record_of(conn, run_id)

    assert state == "parked"
    assert reason == find_failed_reason("open the product the search found")
    assert len(stub.calls) == 1


def test_a_destructive_button_is_refused_and_the_row_parks_with_the_sentence(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    card = dict(CARD, name="Delete product", text="Delete product")
    stub = StubClient([{"token": "e7"}])
    _base, run_id = _run_gated(seed, conn, stub=stub, card=card)

    _key, state, reason = record_of(conn, run_id)

    assert state == "parked"
    assert reason == find_failed_reason("open the product the search found")


def test_a_text_box_is_refused_for_a_click_and_the_row_parks_with_the_sentence(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    card = dict(CARD, role="textbox", name="Search...", text="")
    stub = StubClient([{"token": "e7"}])
    _base, run_id = _run_gated(seed, conn, stub=stub, card=card)

    _key, state, reason = record_of(conn, run_id)

    assert state == "parked"
    assert reason == find_failed_reason("open the product the search found")


def test_the_seventh_model_call_in_a_record_does_not_happen(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    steps: list[dict[str, Any]] = [{"id": "open", "action": "navigate", "url": ORIGIN}]
    for index in range(1, 8):
        steps.append(
            {
                "id": f"open-{index}",
                "action": "click",
                "intent": f"open product {index}",
                "targets": [{"by": "css", "selector": f".card-{index}"}],
            }
        )
    steps.append({"id": "done", "action": "end_record", "outcome": "verified", "reason": "done"})
    base = seed(recipe(steps))
    allow_deepseek(conn, base.tenant_id)
    base.add_input_record(key="row-1", email="a@x.com")

    stub = StubClient([{"token": "e7"} for _ in range(7)])
    scripted = ScriptedRun.build()
    page = CardPage(
        scripted.destination,
        failing={f"open-{index}" for index in range(1, 8)},
    )
    scripted.executor = page
    run_id = base.create_run()
    assert make_worker(scripted, finder(conn, stub)).run_once() is True

    _key, state, reason = record_of(conn, run_id)

    assert state == "parked"
    assert reason == find_failed_reason("open product 7")
    assert len(stub.calls) == 6
    assert len(page.snap_attempts) == 6
    assert ai_call_count(conn, base.tenant_id) == 6


def test_no_key_parks_as_today_and_raises_nothing(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    base = seed(simple_finding_document())
    allow_deepseek(conn, base.tenant_id)
    base.add_input_record(key="row-1", email="a@x.com")
    scripted = ScriptedRun.build()
    page = CardPage(scripted.destination, failing={"open-product"})
    scripted.executor = page
    run_id = base.create_run()
    ai = AiService(conn, client=None)
    assert make_worker(scripted, TargetFinder(conn, ai)).run_once() is True

    _key, state, reason = record_of(conn, run_id)

    assert state == "parked"
    assert reason is not None
    assert find_failed_reason("open the product the search found") not in reason
    assert ai_call_count(conn, base.tenant_id) == 0
    assert page.snapshot_commands == []


def test_a_commits_assert_after_never_asks_a_model(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    steps: list[dict[str, Any]] = [
        {"id": "open", "action": "navigate", "url": f"{ORIGIN}/products/1"},
        {
            "id": "save",
            "action": "click",
            "commit": True,
            "targets": [{"by": "role", "role": "button", "name": "Save"}],
            "assert_after": {
                "element_present": {
                    "targets": [{"by": "role", "role": "link", "name": "Not on this page"}]
                }
            },
        },
    ]
    base = seed(recipe(steps))
    allow_deepseek(conn, base.tenant_id)
    base.add_input_record(key="row-1", email="a@x.com")
    stub = StubClient([{"token": "e7"}])
    scripted = ScriptedRun.build(
        probe_page={"save": {"element_present": False}},
    )
    run_id = base.create_run()

    assert make_worker(scripted, finder(conn, stub)).run_once() is True

    _key, state, _reason = record_of(conn, run_id)
    assert state == "verified"
    assert stub.calls == []
    assert ai_call_count(conn, base.tenant_id) == 0


def test_a_snapshot_with_no_origin_is_unavailable_not_an_exception(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
) -> None:
    """A page that will not say where it is cannot key a memory or a call. The row still parks."""
    document = simple_finding_document()
    # Two origins, so the recipe names no single site to fall back on: the page's own origin is the
    # only key a memory could use, and this page will not give one.
    document["meta"]["allowed_origins"] = [ORIGIN, "http://localhost:8069"]
    base = seed(document)
    allow_deepseek(conn, base.tenant_id)
    base.add_input_record(key="row-1", email="a@x.com")
    scripted = ScriptedRun.build()
    page = CardPage(scripted.destination, failing={"open-product"}, origin="")
    scripted.executor = page
    run_id = base.create_run()

    assert make_worker(scripted, finder(conn, StubClient([{"token": "e7"}]))).run_once() is True

    _key, state, reason = record_of(conn, run_id)
    assert state == "parked"
    assert reason is not None
    assert ai_call_count(conn, base.tenant_id) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM target_memory WHERE tenant_id = %s", (base.tenant_id,))
        assert cur.fetchone()[0] == 0
