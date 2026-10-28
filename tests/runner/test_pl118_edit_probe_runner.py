"""PL-118 review B1: a Save-click edit is verified by the Save control going away.

The compiler had begun giving any edit's commit a ``value_equals`` read-back of the changed field.
A real executor answers the runner's PL-014 freshness probe by evaluating the condition against the
page, and before the Save click that field already shows the value the run has just typed -- so the
probe said "present" and every row parked with "the confirmation was already on the page before
saving". Compiled from the owner's recording, the recipe must keep PL-075's proof: the Save control
is there before the click and gone after it.

The fake answers the probe from a scripted page (``probe_page``): for each predicate the commit's
condition carries, whether it holds before the click. The scripted ``conditions`` answer is what
the page shows after it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import action_event
from services.runner.run_loop import ALREADY_ON_PAGE_REASON
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed

LIST = f"{ORIGIN}/products"
RECORD = f"{ORIGIN}/products/17"
ROW = [{"by": "role", "role": "cell", "name": "Oak Standing Desk"}]
PRICE = [{"by": "role", "role": "spinbutton", "name": "Sales Price"}]
SAVE = [{"by": "role", "role": "button", "name": "Save"}]

# The page the Save click is sent from, as a person leaves it: the price field already shows the
# value the run typed (a Save click does not change the field), and the Save control is still
# there. Both are true statements about that page, and only one of them is proof of a save.
PAGE_BEFORE_SAVE = {"element_absent": False, "value_equals": True, "url_matches": True}


def _compiled() -> dict[str, Any]:
    """The owner's Save-click edit: open the product, change Sales Price, press Save."""
    recording = [
        action_event(1, "click", "click", ROW, url=LIST, step_id="open-1"),
        action_event(2, "fill", "fill", PRICE, value="799", url=RECORD),
        action_event(3, "click", "click", SAVE, url=RECORD, step_id="save-1"),
    ]
    # PL-075: Odoo puts its Save control away once the record is clean.
    recording[2]["observation"] = {
        "element_absent": {"targets": SAVE},
        "url_matches": "/products/\\d+",
    }
    options = CompileOptions(
        name="Update a price",
        when_to_use="Each row changes one product's price.",
        sample_row={"product": "Oak Standing Desk", "price": "799"},
        record_key_column="product",
        commit_step_ids=["save-1"],
        # No approval gate: this test is about the commit's proof, not a person's decision.
        approval_policy={"commit": "none"},
    )
    return compile_recording(recording, options).recipe


def _seed(seed: Callable[..., Seed], document: dict[str, Any]):
    base = seed(document)
    base.add_input_record(product="Oak Standing Desk", price="799")
    return base, base.create_run()


def _reason(conn: psycopg.Connection, run_id) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT reason FROM run_records WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def _commit(document: dict[str, Any]) -> dict[str, Any]:
    commits = [step for step in document["steps"] if step.get("commit") is True]
    assert len(commits) == 1, document["steps"]
    return commits[0]


def test_the_compiled_save_click_edit_carries_the_disappeared_control() -> None:
    """Review B1: the recipe must not check the field a Save click has already filled."""
    commit = _commit(_compiled())

    assert "value_equals" not in commit["assert_after"], commit["assert_after"]
    assert commit["assert_after"]["element_absent"] == {"targets": SAVE}


def test_a_save_click_edit_is_verified_when_the_save_control_disappears(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
) -> None:
    document = _compiled()
    commit = _commit(document)
    base, run_id = _seed(seed, document)
    scripted = ScriptedRun.build(
        # An honest page: true of the field the run typed into, false of the Save control.
        probe_page={commit["id"]: dict(PAGE_BEFORE_SAVE)},
        # After the click the control is gone, so the proof holds.
        conditions={commit["id"]: True},
    )

    assert make_worker(scripted).run_once() is True

    assert list(base.states(run_id).values()) == ["verified"]
    # The freshness probe really ran on this commit, so the test is not passing by its absence.
    assert scripted.executor.probes == [(commit["id"], "oak standing desk")]


def test_a_save_control_already_gone_before_the_click_parks_the_row(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """PL-079's signal, through the runner: nothing to save, so the row stops for a person."""
    document = _compiled()
    commit = _commit(document)
    base, run_id = _seed(seed, document)
    scripted = ScriptedRun.build(
        probe_page={
            commit["id"]: {"element_absent": True, "value_equals": True, "url_matches": True}
        },
        conditions={commit["id"]: True},
    )

    assert make_worker(scripted).run_once() is True

    assert list(base.states(run_id).values()) == ["parked"]
    assert _reason(conn, run_id) == ALREADY_ON_PAGE_REASON
