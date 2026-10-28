"""PL-113 item 5, at the ledger: an update's key is the item and the values it writes.

PL-128 changed the first rule. The ledger used to skip "the same change twice": Oak Standing Desk
-> 799 ran once. But the site can be changed back after a run -- the owner's own teaching set Oak
to 100 after an earlier run set 7999 -- and the skip then left the wrong price. An update is now
decided by the site: it runs again and its saved value is read back. A new price for the same item
is still new work.
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


def test_the_same_change_twice_runs_again_and_is_checked(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = _seed_one(seed, "799")
    run_id = base.create_run()
    # An earlier run already made exactly this change; the site may have been changed since.
    base.commit_key("desk:799", run_id)

    assert make_worker(ScriptedRun.build()).run_once() is True

    assert base.states(run_id) == {"desk": "verified"}
    with conn.cursor() as cur:
        cur.execute("SELECT pause_reason FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] != "duplicate_detected"


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
