"""PL-130: an update is approved before it saves, not after.

The owner's price run of 2026-10-29 asked "This run stopped before saving one row" while Oak
Standing Desk already showed 7,999: an update saves with its Save click (or by leaving the form),
then goes back to the list and re-opens the item, and the commit step -- where the approval sat --
is that re-open, whose read-back is the check. The approval now comes before the first step after
the last change, and the commit's own gate finds that decision instead of asking again.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from services.runner.clock import SystemClock
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe
from tests.runner.test_approval import _requests, decide_soon

LIST = f"{ORIGIN}/odoo/products"
SEARCH = [{"by": "role", "role": "searchbox", "name": "Search..."}]
ITEM = [{"by": "role", "role": "generic", "name": "{{row.product}}"}]
PRICE = [{"by": "label", "text": "Sales Price"}]
SAVE = [{"by": "role", "role": "button", "name": "Save manually"}]

STEPS: list[dict[str, Any]] = [
    {"id": "open-list", "action": "navigate", "url": LIST},
    {
        "id": "search",
        "action": "fill",
        "targets": SEARCH,
        "value": {"kind": "input", "column": "product"},
    },
    {"id": "run-search", "action": "press", "key": "Enter", "targets": SEARCH},
    {"id": "open-item", "action": "click", "targets": ITEM},
    {
        "id": "set-price",
        "action": "fill",
        "targets": PRICE,
        "value": {"kind": "input", "column": "sales_price"},
    },
    {
        "id": "save-when-offered",
        "action": "branch",
        "when": {"element_present": {"targets": SAVE}, "timeout_ms": 3000},
        "then": [{"id": "save", "action": "click", "targets": SAVE}],
    },
    {"id": "leave", "action": "navigate", "url": LIST},
    {
        "id": "reopen",
        "action": "click",
        "commit": True,
        "targets": ITEM,
        "assert_after": {
            "value_equals": {
                "selector": {"targets": PRICE},
                "value": {"kind": "input", "column": "sales_price"},
                "case_insensitive": True,
            },
            "timeout_ms": 8000,
        },
    },
]


def _update(seed: Callable[..., Seed]) -> Seed:
    base = seed(
        recipe(
            STEPS,
            approval_policy={"commit": "first_record"},
            record_key="{{row.product | lower}}",
            idempotency_key="{{row.product | lower}}:{{row.sales_price}}",
        )
    )
    base.add_input_record(
        key="oak", email="a@x.com", product="Oak Standing Desk", sales_price="7999"
    )
    return base


def _ran(scripted: ScriptedRun) -> list[str]:
    return [command.step.get("id") for command in scripted.executor.commands]


def test_a_rejected_update_never_saves(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    base = _update(seed)
    run_id = base.create_run(mode="preview")
    scripted = ScriptedRun.build(conditions={"save-when-offered": True})
    deciding = decide_soon(database_url, base.tenant_id, run_id, "rejected")

    assert make_worker(scripted, gate_wait_seconds=20.0, clock=SystemClock()).run_once() is True
    deciding.join(timeout=10)

    ran = _ran(scripted)
    assert "set-price" in ran
    assert "save" not in ran, "No means nothing was saved"
    assert "leave" not in ran, "leaving an Odoo form saves it too"
    assert base.states(run_id) == {"oak standing desk": "parked"}


def test_an_approved_update_is_asked_once_before_it_saves(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    base = _update(seed)
    run_id = base.create_run(mode="preview")
    scripted = ScriptedRun.build(conditions={"save-when-offered": True})
    ran_when_asked: list[list[str]] = []
    seen: list[tuple[str, str | None]] = []

    def remember(status: tuple[str, str | None]) -> None:
        seen.append(status)
        ran_when_asked.append(_ran(scripted))

    class Seen(list):
        def append(self, item: Any) -> None:  # type: ignore[override]
            remember(item)

    deciding = decide_soon(database_url, base.tenant_id, run_id, "approved", seen=Seen())

    assert make_worker(scripted, gate_wait_seconds=20.0, clock=SystemClock()).run_once() is True
    deciding.join(timeout=10)

    requests = _requests(conn, run_id)
    assert len(requests) == 1, "the re-open's own gate finds the decision; it does not ask again"
    assert seen == [("paused", "awaiting_approval")]
    assert ran_when_asked[0][-1] == "set-price", "asked after the change, before anything saves"
    ran = _ran(scripted)
    assert ran.index("save") < ran.index("reopen")
    assert base.states(run_id) == {"oak standing desk": "verified"}


def test_the_card_shows_each_value_once(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
    database_url: str,
) -> None:
    # The owner's card listed "New Price 7999, Product Oak, Product Oak, Sales price 7999": the
    # spreadsheet's columns and the task's fields they were matched to.
    document = recipe(
        STEPS,
        approval_policy={"commit": "first_record"},
        record_key="{{row.product | lower}}",
        idempotency_key="{{row.product | lower}}:{{row.sales_price}}",
    )
    document["meta"]["input_schema"]["fields"] = {
        "product": {"type": "string", "required": True},
        "sales_price": {"type": "string"},
        "email": {"type": "string"},
    }
    base = seed(document)
    record = base.add_input_record(
        key="oak", email="a@x.com", product="Oak Standing Desk", sales_price="7999"
    )
    conn.execute(
        "UPDATE input_records SET raw = %s WHERE id = %s",
        (
            psycopg.types.json.Jsonb({"Product": "Oak Standing Desk", "New Price": "7999"}),
            record,
        ),
    )
    run_id = base.create_run(mode="preview")
    deciding = decide_soon(database_url, base.tenant_id, run_id, "approved")

    scripted = ScriptedRun.build(conditions={"save-when-offered": True})
    assert make_worker(scripted, gate_wait_seconds=20.0, clock=SystemClock()).run_once() is True
    deciding.join(timeout=10)

    shown = _requests(conn, run_id)[0]["shown"]
    assert shown["record"] == {
        "email": "a@x.com",
        "product": "Oak Standing Desk",
        "sales_price": "7999",
    }
