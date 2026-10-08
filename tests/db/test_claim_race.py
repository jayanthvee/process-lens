"""Two workers claiming records from the same run never claim one twice.

The claim query is the one the run worker uses: the oldest pending record of a
run that still has attempts left, ordered by creation time, locked with
``FOR UPDATE SKIP LOCKED`` so a second worker moves on instead of blocking.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from tests.db.seed import Ledger

WORKERS = 2
RECORDS_PER_ROUND = 50
ROUNDS = 10
BARRIER_TIMEOUT_SECONDS = 60

CLAIM_NEXT_RECORD = """
WITH next AS (
  SELECT id FROM run_records
  WHERE run_id = %s AND state = 'pending' AND attempts < max_attempts
  ORDER BY created_at, id
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE run_records r
SET state = 'claimed', claimed_by = %s, claimed_at = now()
FROM next
WHERE r.id = next.id
RETURNING r.id
"""


@pytest.mark.parametrize("round_index", range(ROUNDS))
def test_two_workers_claim_every_record_exactly_once(
    database_url: str, ledger: Ledger, round_index: int
) -> None:
    run, expected_ids = ledger.seed_records(RECORDS_PER_ROUND)

    barrier = threading.Barrier(WORKERS)
    claimed: list[uuid.UUID] = []
    claimed_lock = threading.Lock()

    def worker(index: int) -> None:
        with psycopg.connect(database_url) as connection:
            barrier.wait(timeout=BARRIER_TIMEOUT_SECONDS)
            while True:
                with connection.transaction(), connection.cursor() as cur:
                    cur.execute(CLAIM_NEXT_RECORD, (run.id, f"worker-{index}"))
                    row = cur.fetchone()
                if row is None:
                    return
                with claimed_lock:
                    claimed.append(row[0])

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(worker, index) for index in range(WORKERS)]
        for future in futures:
            future.result()

    assert len(claimed) == RECORDS_PER_ROUND, "every record is claimed by someone"
    assert sorted(claimed) == sorted(expected_ids)
    assert len(set(claimed)) == RECORDS_PER_ROUND, "no record is claimed twice"

    with ledger.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), sum(attempts) FROM run_records "
            "WHERE run_id = %s AND state = 'claimed'",
            (run.id,),
        )
        assert cur.fetchone() == (RECORDS_PER_ROUND, RECORDS_PER_ROUND), (
            "one claim each: the database agrees, and attempts moved once per record"
        )
