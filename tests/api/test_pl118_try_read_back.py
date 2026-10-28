"""PL-118 item 3: the try record carries what the page kept.

The read-back is the page's own displayed value, never the row's file value. It is returned on the
preview record so the Try one row card can say "now 799" or "still 910, not saved".
"""

from __future__ import annotations

import psycopg
from fastapi.testclient import TestClient

from tests.db.seed import Ledger

READ_BACK = [
    {"label": "Sales price", "value": "$ 799.00", "matched": True},
]


def test_a_records_read_back_is_returned(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="verified")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO step_events "
            "(tenant_id, run_record_id, step_id, attempt, started_at, ended_at, outcome, observed) "
            "VALUES (%s, %s, 'save', 1, now(), now(), 'ok', %s)",
            (ledger.tenant_id, record_id, psycopg.types.json.Jsonb({"read_back": READ_BACK})),
        )

    rows = connected.get(f"/api/v1/runs/{run.id}/records").json()

    assert rows[0]["read_back"] == READ_BACK


def test_a_record_with_no_read_back_returns_an_empty_list(
    connected: TestClient, ledger: Ledger
) -> None:
    run = ledger.new_run()
    ledger.add_record(run, state="pending")

    rows = connected.get(f"/api/v1/runs/{run.id}/records").json()

    assert rows[0]["read_back"] == []


def test_the_latest_read_back_wins(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run, state="parked")
    with conn.cursor() as cur:
        for value in ("910", "$ 799.00"):
            cur.execute(
                "INSERT INTO step_events "
                "(tenant_id, run_record_id, step_id, attempt, started_at, ended_at, outcome, "
                "observed) VALUES (%s, %s, 'save', 1, now(), now(), 'failed', %s)",
                (
                    ledger.tenant_id,
                    record_id,
                    psycopg.types.json.Jsonb(
                        {"read_back": [{"label": "Sales price", "value": value, "matched": False}]}
                    ),
                ),
            )

    rows = connected.get(f"/api/v1/runs/{run.id}/records").json()

    assert rows[0]["read_back"][0]["value"] == "$ 799.00"
