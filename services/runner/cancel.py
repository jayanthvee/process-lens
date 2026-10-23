"""Finish a cancel that no live worker holds (PL-089).

A cancel is a request: ``request_cancel`` marks the run and its worker finishes it at the next
step boundary (R4). Since PL-087 a run belongs to one Chrome, and a person who cancels after closing
that Chrome leaves a request no worker will ever pick up. The run stays open, and an open run on a
destination blocks every other run there, for everyone.

So the supervisor finishes such a cancel itself, exactly the way a worker's ``_finalise_cancel``
does, under a lease of its own so a worker that appears meanwhile fences it out:

* the commit window goes to ``reconciling``, and the working records go back to ``pending``;
* every ``reconciling`` record is parked for a person, with the parking reconciler's words: with no
  browser nothing proves the destination lacks it (S2), so it is never re-queued;
* every ``pending`` record is skipped, and the run is ``cancelled``.

"No live worker holds it" is the takeover rule: no holder, or a heartbeat older than the timeout.
"""

from __future__ import annotations

import uuid

import psycopg

from packages.ledger import (
    DEFAULT_HEARTBEAT_TIMEOUT,
    StaleLease,
    change_state,
    reconciling_records,
    recover_run,
    set_run_status,
    skip_pending_records,
    take_cancelled_run,
    unheld_cancelled_runs,
)

from .reconcile import PARKING_REASON

FINALISER_ID = "cancel-finaliser"
CANCEL_REASON = "run cancelled"


def finalise_unheld_cancels(
    conn: psycopg.Connection,
    *,
    worker_id: str = FINALISER_ID,
    heartbeat_timeout: str = DEFAULT_HEARTBEAT_TIMEOUT,
) -> list[uuid.UUID]:
    """Finish every cancelled run no live worker holds; return the ones finished.

    Each run is one transaction. A run a worker claims meanwhile fences this out (``StaleLease``),
    and that worker finishes the cancel itself.
    """
    finished: list[uuid.UUID] = []
    for run_id in unheld_cancelled_runs(conn, heartbeat_timeout=heartbeat_timeout):
        try:
            with conn.transaction():
                epoch = take_cancelled_run(
                    conn, run_id, worker_id, heartbeat_timeout=heartbeat_timeout
                )
                if epoch is None:
                    continue
                recover_run(conn, run_id, worker_id, epoch, reason=CANCEL_REASON)
                for record in reconciling_records(conn, run_id):
                    change_state(
                        conn,
                        record.id,
                        worker=worker_id,
                        epoch=epoch,
                        expected="reconciling",
                        new="parked",
                        reason=PARKING_REASON,
                    )
                skip_pending_records(conn, run_id, worker_id, epoch, reason=CANCEL_REASON)
                set_run_status(conn, run_id, "cancelled", worker=worker_id, epoch=epoch)
        except StaleLease:
            continue
        finished.append(run_id)
    return finished
