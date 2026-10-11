"""Prechecks: a rule ends the record before the browser is touched.

A `then: "skip"` rule is the case that must not crash: `claimed -> skipped` is not a legal ledger
transition, so the record passes through `prechecked` (the state the spec defines as "precheck
finished; the result decides skip, park, or fill") on its way out.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import get_record
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import Seed, recipe

SKIP_RULE = [{"when": {"missing": "email"}, "then": "skip", "reason": "already handled elsewhere"}]
PARK_RULE = [{"when": {"missing": "email"}, "then": "park", "reason": "no email address"}]
FAIL_RULE = [{"when": {"missing": "email"}, "then": "fail", "reason": "this row can never work"}]


def _transitions(conn: psycopg.Connection, run_id: uuid.UUID) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.from_state || '>' || t.to_state FROM run_record_transitions t "
            "JOIN run_records r ON r.id = t.run_record_id WHERE r.run_id = %s ORDER BY t.id",
            (run_id,),
        )
        return [row[0] for row in cur.fetchall()]


def _run_with_rule(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], rules: list[dict]
) -> tuple[Seed, uuid.UUID]:
    base = seed(recipe(rules=rules))
    base.add_input_record(key="no-email")  # the rule fires
    base.add_input_record(key="has-email", email="b@x.com")  # the rule does not fire
    run_id = base.create_run()
    worker = make_worker(ScriptedRun.build())
    assert worker.run_once() is True
    return base, run_id


def test_a_skip_rule_ends_the_record_skipped_without_crashing(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    """`then: "skip"` must not raise `illegal ledger transition claimed -> skipped`."""
    base, run_id = _run_with_rule(seed, make_worker, SKIP_RULE)

    states = base.states(run_id)
    assert states["no-email"] == "skipped"
    assert states["has-email"] == "verified", "the run continued past the skipped record"
    assert base.run_status(run_id) == ("completed", None)
    assert "claimed>prechecked" in _transitions(conn, run_id)
    assert "prechecked>skipped" in _transitions(conn, run_id)
    assert "claimed>skipped" not in _transitions(conn, run_id)

    record_id = _record_id_for(conn, run_id, "no-email")
    record = get_record(conn, record_id)
    assert record is not None
    assert record.reason == "already handled elsewhere"


def _record_id_for(conn: psycopg.Connection, run_id: uuid.UUID, record_key: str) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM run_records WHERE run_id = %s AND record_key = %s", (run_id, record_key)
        )
        return cur.fetchone()[0]


def test_a_park_rule_ends_the_record_parked(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    base, run_id = _run_with_rule(seed, make_worker, PARK_RULE)

    assert base.states(run_id)["no-email"] == "parked"
    assert "claimed>parked" in _transitions(conn, run_id)


def test_a_fail_rule_ends_the_record_failed(
    seed: Callable[..., Seed], make_worker: Callable[..., Worker], conn: psycopg.Connection
) -> None:
    base, run_id = _run_with_rule(seed, make_worker, FAIL_RULE)

    assert base.states(run_id)["no-email"] == "failed"
    assert "claimed>failed" in _transitions(conn, run_id)
