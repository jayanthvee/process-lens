"""PL-017 requirement 2: a run's records are claimed in input-row order.

The ready rows of a batch share one ``created_at`` (they are inserted together), so ordering by
``created_at, id`` let the random uuid decide the claim order and made the canary -- the first
record to reach a commit -- non-deterministic. The claim query now orders by the input row's
``row_index``.
"""

from __future__ import annotations

import uuid

from packages.ledger import claim_record
from tests.db.seed import Ledger

# One timestamp for every record, so only row_index can order them.
SAME_CREATED_AT = "2026-01-01T00:00:00+00:00"


def _insert_input_record(conn, ledger: Ledger, run, row_index: int) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO input_records (tenant_id, batch_id, row_index, raw, record_key, status) "
            "VALUES (%s, %s, %s, '{}', %s, 'ready') RETURNING id",
            (ledger.tenant_id, run.batch_id, row_index, f"row-{row_index}"),
        )
        return cur.fetchone()[0]


def test_records_are_claimed_in_input_row_order(conn, ledger: Ledger) -> None:
    run = ledger.new_run()
    # The ids ascend in the REVERSE of row order, and every created_at is identical: only
    # input_records.row_index can put these records in row order.
    record_ids = [uuid.UUID(int=n) for n in (9, 8, 7, 6, 5)]
    for row_index, record_id in enumerate(record_ids):
        input_id = _insert_input_record(conn, ledger, run, row_index)
        conn.execute(
            "INSERT INTO run_records "
            "(id, tenant_id, run_id, input_record_id, record_key, state, attempts, max_attempts, "
            " idempotency_key, created_at) "
            "VALUES (%s, %s, %s, %s, %s, 'pending', 0, 3, %s, %s)",
            (
                record_id,
                ledger.tenant_id,
                run.id,
                input_id,
                f"row-{row_index}",
                f"row-{row_index}",
                SAME_CREATED_AT,
            ),
        )
    # A live lease, so claim_record's fencing check passes.
    conn.execute(
        "UPDATE runs SET status = 'running', worker_id = 'w-order', lease_epoch = 1 WHERE id = %s",
        (run.id,),
    )

    claimed: list[uuid.UUID] = []
    for _ in range(len(record_ids)):
        record = claim_record(conn, run.id, "w-order", 1)
        assert record is not None, "five pending records, so five claims"
        claimed.append(record.input_record_id)

    with conn.cursor() as cur:
        cur.execute("SELECT id, row_index FROM input_records WHERE batch_id = %s", (run.batch_id,))
        row_index_by_input = dict(cur.fetchall())

    assert [row_index_by_input[input_id] for input_id in claimed] == [0, 1, 2, 3, 4], (
        "row 0 must be claimed first, so row 0 is the canary"
    )
