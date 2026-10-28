"""PL-123 B1 at the runner: the searched text in the search box is never the search result.

Two owner shapes, each with the value visible twice -- once as a chip inside the search box, once
on the result:

* the Events duplicate check: row 2's email is only in the chip, so the row is added;
* the price task: a chip plus a card, so the row opens the card instead of parking on "More than
  one product matches".

The page is scripted: a chip list and a result list. The executor's fake decides what it "sees"
from the step's own ``exclude_search_box`` flag, so the test fails if the runner ever stops
carrying it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import psycopg

from services.runner.executor import ConditionResult, StepCommand, StepResult
from services.runner.testing import FakeExecutor, ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

ROW_TWO_EMAIL = "liam.chen@pilot-events.test"

CREATE_STEPS: list[dict[str, Any]] = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/events/1/attendees"},
    {
        "id": "search",
        "action": "fill",
        "intent": "search for the row's email",
        "targets": [{"by": "role", "role": "searchbox"}],
        "value": {"kind": "input", "column": "email"},
    },
    {
        "id": "run-search",
        "action": "press",
        "key": "Enter",
        "targets": [{"by": "role", "role": "searchbox"}],
    },
    {
        "id": "already-there",
        "action": "branch",
        "when": {
            "element_present": {
                "targets": [{"by": "text_fuzzy", "value": "{{row.email}}"}],
                "intent": "a result with this email",
                "exclude_search_box": True,
            }
        },
        "then": [
            {
                "id": "skip-it",
                "action": "end_record",
                "outcome": "skipped",
                "reason": "Already there: this email is already on the list.",
            }
        ],
    },
    {"id": "new", "action": "click", "targets": [{"by": "role", "role": "button", "name": "New"}]},
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
        "assert_after": {"text_visible": "Saved", "timeout_ms": 8000},
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
    },
]

PRICE_STEPS: list[dict[str, Any]] = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/products"},
    {
        "id": "open-product",
        "action": "click",
        "intent": "open the product found by {{row.product}}",
        "exclude_search_box": True,
        "targets": [{"by": "text_fuzzy", "value": "{{row.product}}"}],
    },
    {
        "id": "save",
        "action": "click",
        "commit": True,
        "assert_after": {"text_visible": "Saved", "timeout_ms": 8000},
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
    },
]


class ChipPage(FakeExecutor):
    """A page where the searched text shows in a chip, and maybe also on a result."""

    def __init__(self, destination: Any, *, results: list[str]) -> None:
        super().__init__(destination)
        self.chip = [ROW_TWO_EMAIL]
        self.results = results

    def check_condition(
        self,
        condition: Mapping[str, Any],
        *,
        timeout_ms: int,
        step_id: str | None = None,
        record_key: str | None = None,
    ) -> ConditionResult:
        spec = condition.get("element_present") or condition.get("element_absent")
        if isinstance(spec, dict):
            wanted = [
                str(rung.get("value") or "")
                for rung in spec.get("targets", [])
                if isinstance(rung, dict)
            ]
            in_results = any(text and text in self.results for text in wanted)
            in_chip = any(text and text in self.chip for text in wanted)
            present = in_results or (in_chip and spec.get("exclude_search_box") is not True)
            if "element_absent" in condition:
                return ConditionResult(passed=not present)
            return ConditionResult(passed=present)
        return super().check_condition(
            condition, timeout_ms=timeout_ms, step_id=step_id, record_key=record_key
        )

    def perform(self, command: StepCommand, on_dispatched: Any = None) -> StepResult:
        if command.step.get("id") == "open-product":
            # The recorded text is on the chip and on the card: without the flag that is two
            # matches, and the runner parks "More than one product matches".
            if command.step.get("exclude_search_box") is not True:
                return StepResult(
                    status="locator_failed",
                    error="the click target could not be resolved: more than one match",
                )
            return StepResult(status="ok", rung=1)
        return super().perform(command, on_dispatched)


def _worker(scripted: ScriptedRun, page: FakeExecutor, database_url: str) -> Worker:
    return Worker(
        database_url,
        worker_id="worker-1",
        executor=page,
        reconciler=scripted.reconciler,
        clock=scripted.clock,
    )


def _reason(conn: psycopg.Connection, run_id: Any) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
    return None if row is None else row[0]


def test_a_row_whose_email_is_only_a_chip_is_added_not_skipped(
    seed: Callable[..., Seed],
    database_url: str,
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe(CREATE_STEPS, record_key="{{row.email}}", idempotency_key="{{row.email}}"))
    base.add_input_record(key="row-2", email=ROW_TWO_EMAIL)
    run_id = base.create_run()
    scripted = ScriptedRun.build()
    # The destination has no attendee with this email: the only place it shows is the chip.
    page = ChipPage(scripted.destination, results=[])

    assert _worker(scripted, page, database_url).run_once() is True

    assert base.states(run_id) == {ROW_TWO_EMAIL: "verified"}


def test_the_same_row_would_be_skipped_without_the_exclusion(
    seed: Callable[..., Seed],
    database_url: str,
    conn: psycopg.Connection,
) -> None:
    """The chip alone reads as a result when nothing says to ignore the search box."""
    steps = [dict(step) for step in CREATE_STEPS]
    steps[3] = {
        **steps[3],
        "when": {
            "element_present": {
                "targets": [{"by": "text_fuzzy", "value": "{{row.email}}"}],
                "intent": "a result with this email",
            }
        },
    }
    base = seed(recipe(steps, record_key="{{row.email}}", idempotency_key="{{row.email}}"))
    base.add_input_record(key="row-2", email=ROW_TWO_EMAIL)
    run_id = base.create_run()
    scripted = ScriptedRun.build()
    page = ChipPage(scripted.destination, results=[])

    assert _worker(scripted, page, database_url).run_once() is True

    assert base.states(run_id) == {ROW_TWO_EMAIL: "skipped"}


def test_a_chip_plus_a_card_opens_the_card(
    seed: Callable[..., Seed],
    database_url: str,
    conn: psycopg.Connection,
) -> None:
    base = seed(
        recipe(
            PRICE_STEPS,
            record_key="{{row.product}}",
            idempotency_key="{{row.product}}",
            rules=[{"when": {"missing": "product"}, "then": "park", "reason": "missing"}],
        )
    )
    base.add_input_record(key="desk", product="Oak Standing Desk")
    run_id = base.create_run()
    scripted = ScriptedRun.build()
    page = ChipPage(scripted.destination, results=["Oak Standing Desk"])

    assert _worker(scripted, page, database_url).run_once() is True

    assert base.states(run_id) == {"Oak Standing Desk": "verified"}
    assert "More than one" not in (_reason(conn, run_id) or "")
