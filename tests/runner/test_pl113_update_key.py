"""PL-113 item 5, at the ledger: an update runs once per exact change.

The key an update is filed under is the item *and* the values it writes, so the ledger's own
"already committed in an earlier run" check gives the ticket's rule: Oak Standing Desk -> 799 runs
once, and Oak Standing Desk -> 850 is new work later.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe

KEY = "{{row.key}}:{{row.price}}"


def _seed_one(seed: Callable[..., Seed], price: str) -> Seed:
    base = seed(recipe(idempotency_key=KEY))
    base.add_input_record(key="desk", price=price, email="a@x.com")
    return base


def test_the_same_change_twice_runs_once(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = _seed_one(seed, "799")
    run_id = base.create_run()
    # An earlier run already made exactly this change.
    base.commit_key("desk:799", run_id)

    assert make_worker(ScriptedRun.build()).run_once() is True

    assert base.states(run_id) == {"desk": "skipped"}
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == "already committed in an earlier run"


def test_a_new_price_for_the_same_item_is_new_work(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    base = _seed_one(seed, "850")
    run_id = base.create_run()
    # The earlier run changed the price to 799; this row changes it to 850.
    base.commit_key("desk:799", run_id)

    assert make_worker(ScriptedRun.build()).run_once() is True

    assert base.states(run_id) == {"desk": "verified"}
