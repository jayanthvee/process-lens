"""PL-065: what happened inside one row -- its step events, in order.

The results page asks one record for its steps, so a person can see which step filled what, how
long each one took, and where the row stopped. The read is tenant-filtered, and it never returns
``observed`` (the values the page held) or the whole ``error`` JSON -- only the message, shortened.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import psycopg

from packages.ledger.reads import record_steps
from tests.db.seed import Ledger

BASE = datetime(2026, 10, 20, 9, 0, 0, tzinfo=UTC)


def _step(
    conn: psycopg.Connection,
    ledger: Ledger,
    record_id: uuid.UUID,
    *,
    step_id: str,
    outcome: str = "ok",
    attempt: int = 1,
    starts_after: float = 0,
    takes: float = 0.4,
    observed: dict | None = None,
    error: object = None,
    ended: bool = True,
) -> None:
    """One ``step_events`` row, timed relative to ``BASE`` so the order is never a race."""
    started = BASE + timedelta(seconds=starts_after)
    ended_at = started + timedelta(seconds=takes) if ended else None
    conn.execute(
        """
        INSERT INTO step_events
          (tenant_id, run_record_id, step_id, attempt, started_at, ended_at, outcome,
           locator_rung, observed, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
        """,
        (
            ledger.tenant_id,
            record_id,
            step_id,
            attempt,
            started,
            ended_at,
            outcome,
            json.dumps(observed) if observed is not None else None,
            json.dumps(error) if error is not None else None,
        ),
    )


def test_the_steps_come_back_in_the_order_they_ran(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record = ledger.add_record(run)
    # Inserted out of order on purpose: the read orders by when a step began, not by insert id.
    _step(conn, ledger, record, step_id="save-lead", starts_after=1.0, takes=0.6)
    _step(conn, ledger, record, step_id="open-form", starts_after=0.0, takes=0.3)
    _step(conn, ledger, record, step_id="type-email", starts_after=0.3, takes=0.4)

    steps = record_steps(conn, record, ledger.tenant_id)

    assert [step.step_id for step in steps] == ["open-form", "type-email", "save-lead"]


def test_each_step_carries_its_run_times(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record = ledger.add_record(run)
    _step(conn, ledger, record, step_id="type-email", starts_after=0.0, takes=0.4)

    step = record_steps(conn, record, ledger.tenant_id)[0]

    assert step.attempt == 1
    assert step.outcome == "ok"
    assert step.started_at == BASE
    assert step.ended_at == BASE + timedelta(seconds=0.4)


def test_a_step_that_is_still_running_has_no_end(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record = ledger.add_record(run)
    _step(conn, ledger, record, step_id="wait-for-results", ended=False, outcome="skipped")

    step = record_steps(conn, record, ledger.tenant_id)[0]

    assert step.ended_at is None


def test_every_attempt_of_a_step_is_its_own_line(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record = ledger.add_record(run, attempts=2)
    _step(conn, ledger, record, step_id="click-save", attempt=1, outcome="retried", takes=1.0)
    _step(
        conn,
        ledger,
        record,
        step_id="click-save",
        attempt=2,
        outcome="ok",
        starts_after=1.0,
        takes=0.2,
    )

    steps = record_steps(conn, record, ledger.tenant_id)

    assert [(step.step_id, step.attempt, step.outcome) for step in steps] == [
        ("click-save", 1, "retried"),
        ("click-save", 2, "ok"),
    ]


def test_the_error_is_its_message_and_no_more(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record = ledger.add_record(run)
    _step(
        conn,
        ledger,
        record,
        step_id="click-save",
        outcome="failed",
        error={
            "message": "the Save button never became visible",
            "detail": {"targets": [{"by": "css", "selector": "#save"}], "attempts": 3},
        },
    )

    step = record_steps(conn, record, ledger.tenant_id)[0]

    assert step.error == "the Save button never became visible"


def test_a_long_error_is_cut_to_two_hundred_characters(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record = ledger.add_record(run)
    _step(
        conn,
        ledger,
        record,
        step_id="click-save",
        outcome="failed",
        error={"message": "x" * 500},
    )

    step = record_steps(conn, record, ledger.tenant_id)[0]

    assert step.error is not None
    assert len(step.error) == 200


def test_an_error_with_a_reason_instead_of_a_message_still_says_something(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record = ledger.add_record(run)
    _step(
        conn,
        ledger,
        record,
        step_id="select-status",
        outcome="failed",
        error={"reason": 'no option matched "approved"'},
    )

    step = record_steps(conn, record, ledger.tenant_id)[0]

    assert step.error == 'no option matched "approved"'


def test_an_error_that_is_only_a_string_is_still_read(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record = ledger.add_record(run)
    _step(conn, ledger, record, step_id="click-save", outcome="failed", error="timed out")

    assert record_steps(conn, record, ledger.tenant_id)[0].error == "timed out"


def test_an_error_with_nothing_readable_is_no_error(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record = ledger.add_record(run)
    _step(conn, ledger, record, step_id="click-save", outcome="failed", error={"code": 42})

    assert record_steps(conn, record, ledger.tenant_id)[0].error is None


def test_the_values_the_page_held_never_come_back(conn: psycopg.Connection, ledger: Ledger) -> None:
    """``observed`` can hold a row's values, so the read does not select it at all."""
    run = ledger.new_run()
    record = ledger.add_record(run)
    _step(
        conn,
        ledger,
        record,
        step_id="read-price",
        observed={"value": "SECRET-PRICE-42", "text": "SECRET-PRICE-42", "url": "https://x/y"},
    )

    steps = record_steps(conn, record, ledger.tenant_id)

    assert len(steps) == 1
    fields = set(vars(steps[0]))
    assert "observed" not in fields
    assert "SECRET" not in repr(steps[0])


def test_another_tenants_record_is_not_readable(conn: psycopg.Connection) -> None:
    """The read is filtered by tenant, so the wrong tenant gets nothing, not someone's row."""
    theirs = Ledger(conn)
    run = theirs.new_run()
    record = theirs.add_record(run)
    _step(conn, theirs, record, step_id="open-form")

    mine = Ledger(conn)

    assert record_steps(conn, record, mine.tenant_id) == []
    assert len(record_steps(conn, record, theirs.tenant_id)) == 1
