"""PL-123, T1's third price-walk item: an ambiguous row name never picks one.

"Standing Desk" names three products. The ladder matches more than one, and PL-122 could point at
one snapshot token -- but choosing one would change the wrong product. The row parks with the plain
sentence, naming the value a person has to disambiguate.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe

STEPS = [
    {"id": "open", "action": "navigate", "url": f"{ORIGIN}/products"},
    {
        "id": "open-product",
        "action": "click",
        "intent": "open the product found by {{row.product}}",
        "targets": [{"by": "role", "role": "generic", "name": "{{row.product}}"}],
    },
    {
        "id": "save",
        "action": "click",
        "commit": True,
        "assert_after": {"text_visible": "Saved", "timeout_ms": 8000},
        "targets": [{"by": "role", "role": "button", "name": "Save"}],
    },
]


def _seed(seed: Callable[..., Seed]) -> tuple[Seed, object]:
    base = seed(recipe(STEPS, record_key="{{row.product}}", idempotency_key="{{row.product}}"))
    base.add_input_record(key="Standing Desk", product="Standing Desk", email="a@x.com")
    return base, base.create_run()


def test_an_ambiguous_product_name_parks_without_asking_a_model(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base, run_id = _seed(seed)
    scripted = ScriptedRun.build(plan={"open-product": "ambiguous"})

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"Standing Desk": "parked"}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT reason FROM run_records WHERE run_id = %s AND record_key = %s",
            (run_id, "Standing Desk"),
        )
        assert cur.fetchone()[0] == "More than one product matches 'Standing Desk'."
    # No model was asked: the answer could never be safe.
    assert scripted.executor.snapshots == []


def test_the_same_rule_holds_when_the_ambiguous_click_is_the_commit(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed(recipe(STEPS[:2] + [
        {
            "id": "save",
            "action": "click",
            "commit": True,
            "assert_after": {"text_visible": "Saved", "timeout_ms": 8000},
            "targets": [{"by": "role", "role": "generic", "name": "{{row.product}}"}],
        }
    ], record_key="{{row.product}}", idempotency_key="{{row.product}}"))
    base.add_input_record(key="Standing Desk", product="Standing Desk", email="a@x.com")
    run_id = base.create_run()
    scripted = ScriptedRun.build(plan={"open-product": "ok", "save": "ambiguous"})

    assert make_worker(scripted).run_once() is True

    assert base.states(run_id) == {"Standing Desk": "parked"}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT reason FROM run_records WHERE run_id = %s AND record_key = %s",
            (run_id, "Standing Desk"),
        )
        assert cur.fetchone()[0] == "More than one product matches 'Standing Desk'."
    # The prepare never sent input, so nothing was written to the destination.
    assert scripted.destination.commits == 0
