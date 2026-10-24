"""PL-089: a cancel nobody holds is finished by the supervisor, so it frees the destination.

Since runs belong to one Chrome (PL-087), a run cancelled after its Chrome went away has no worker
to finish it, and an open run blocks every other run on its destination.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import claim_run, request_cancel
from services.runner.cancel import finalise_unheld_cancels
from tests.runner.conftest import Seed


def _record_id(conn: psycopg.Connection, run_id: uuid.UUID, key: str) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM run_records WHERE run_id = %s AND record_key = %s", (run_id, key)
        )
        return cur.fetchone()[0]


def _walk(conn: psycopg.Connection, record_id: uuid.UUID, states: list[str]) -> None:
    for state in states:
        conn.execute("UPDATE run_records SET state = %s WHERE id = %s", (state, record_id))


def _seed_run(
    seed: Callable[..., Seed], keys: tuple[str, ...] = ("a", "b")
) -> tuple[Seed, uuid.UUID]:
    base = seed()
    for key in keys:
        base.add_input_record(key=key, email=f"{key}@x.com")
    return base, base.create_run(browser_id="browser-away-0001")


def test_a_queued_run_cancelled_while_its_chrome_is_away_is_finished(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base, run_id = _seed_run(seed)
    assert request_cancel(conn, run_id)

    assert finalise_unheld_cancels(conn) == [run_id]

    assert base.run_status(run_id) == ("cancelled", None)
    assert set(base.states(run_id).values()) == {"skipped"}


def test_a_lapsed_run_mid_save_parks_that_row_and_skips_the_rest(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    """The row that may have saved is never re-tried and never called skipped: a person checks."""
    base, run_id = _seed_run(seed)
    assert claim_run(conn, "gone-worker") is not None
    _walk(conn, _record_id(conn, run_id, "a"), ["claimed", "prechecked", "filling", "submitting"])
    conn.execute(
        "UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (run_id,)
    )
    assert request_cancel(conn, run_id)

    assert finalise_unheld_cancels(conn) == [run_id]

    assert base.states(run_id) == {"a": "parked", "b": "skipped"}
    assert base.run_status(run_id) == ("cancelled", None)


def test_a_run_a_live_worker_holds_is_left_to_that_worker(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base, run_id = _seed_run(seed)
    assert claim_run(conn, "live-worker") is not None
    assert request_cancel(conn, run_id)

    assert finalise_unheld_cancels(conn) == []

    assert base.run_status(run_id)[0] == "running"


def test_a_run_nobody_cancelled_is_never_touched(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base, run_id = _seed_run(seed)

    assert finalise_unheld_cancels(conn) == []

    assert base.run_status(run_id)[0] == "queued"
    assert set(base.states(run_id).values()) == {"pending"}


def test_a_finished_cancel_frees_the_destination_for_the_next_run(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    """The point of it all: Anna's run can start once Ben's abandoned one is cancelled."""
    base, abandoned = _seed_run(seed)
    assert claim_run(conn, "gone-worker") is not None
    conn.execute(
        "UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (abandoned,)
    )
    waiting = base.create_run(browser_id="browser-anna-0001")
    assert claim_run(conn, "anna-worker", browser_id="browser-anna-0001") is None

    assert request_cancel(conn, abandoned)
    finalise_unheld_cancels(conn)

    claimed = claim_run(conn, "anna-worker", browser_id="browser-anna-0001")
    assert claimed is not None and claimed.id == waiting
