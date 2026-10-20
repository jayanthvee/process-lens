"""PL-067: runs go at a person's pace and stop under a site's daily cap.

The pacing settings ride the run row (`min_seconds_between_rows`,
`min_seconds_between_steps`, migration 0010), the waits go through the injected
clock (nothing here sleeps), and a pause or cancel during a wait is noticed
within one clock slice. The daily cap reads the tenant's
`site_daily_caps: {origin: number}` setting and counts this tenant's rows that
entered `submitting` today (UTC) on runs whose recipe allows that origin.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import ORIGIN, Seed, recipe


def _wrap_pauses(clock: object, hook: Callable[[int], None]) -> object:
    """A clock that calls ``hook`` with the pause count before every pause."""

    class Wrapped:
        def time(self) -> float:
            return clock.time()  # type: ignore[attr-defined]

        def pause(self, seconds: float) -> None:
            hook(Wrapped.count)
            Wrapped.count += 1
            clock.pause(seconds)  # type: ignore[attr-defined]

    Wrapped.count = 0
    return Wrapped()


def _pause_run(conn: psycopg.Connection, run_id: uuid.UUID) -> None:
    conn.execute(
        "UPDATE runs SET status = 'paused', pause_reason = 'paused_by_operator' WHERE id = %s",
        (run_id,),
    )


def _states(conn: psycopg.Connection, run_id: uuid.UUID) -> dict[str, str]:
    rows = conn.execute(
        "SELECT record_key, state FROM run_records WHERE run_id = %s", (run_id,)
    ).fetchall()
    return {row[0]: row[1] for row in rows}


# --- pacing ------------------------------------------------------------------


def test_a_row_gap_makes_the_second_claim_wait(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    make_worker: Callable[..., Worker],
) -> None:
    """A 30-second gap: the clock advances 30-37.5 seconds across the run (0-25% jitter)."""
    base = seed()
    base.add_input_record(email="a@x.com")
    base.add_input_record(email="b@x.com")
    scripted = ScriptedRun.build()
    run_id = base.create_run()
    conn.execute("UPDATE runs SET min_seconds_between_rows = 30 WHERE id = %s", (run_id,))
    worker = make_worker(scripted)
    worker.run_once()

    # The only waits in this run are the pacing waits: two records, one row gap between their
    # claims, no step gap, no gates. The jitter is 0-25% of the gap.
    assert 30.0 <= scripted.clock.time() <= 37.5, scripted.clock.time()
    assert _states(conn, run_id) == {"row-0001": "verified", "row-0002": "verified"}


def test_a_pause_during_the_wait_stops_the_run_without_claiming(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    make_worker: Callable[..., Worker],
) -> None:
    """The operator pauses mid-wait: the second row is never claimed."""
    base = seed()
    base.add_input_record(email="a@x.com")
    base.add_input_record(email="b@x.com")
    scripted = ScriptedRun.build()
    run_id = base.create_run()
    conn.execute("UPDATE runs SET min_seconds_between_rows = 30 WHERE id = %s", (run_id,))

    # The pause lands during the row wait: the clock's second pause call flips the run.
    def pause_hook(count: int) -> None:
        if count >= 1:
            _pause_run(conn, run_id)

    scripted.clock = _wrap_pauses(scripted.clock, pause_hook)  # type: ignore[assignment]
    worker = make_worker(scripted, clock=scripted.clock)
    worker.run_once()

    status, reason = base.run_status(run_id)
    assert status == "paused"
    assert reason == "paused_by_operator"
    assert _states(conn, run_id) == {"row-0001": "verified", "row-0002": "pending"}


def test_a_step_gap_waits_between_steps(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    make_worker: Callable[..., Worker],
) -> None:
    """A 2-second step gap, one record, three executor steps: the clock advances 6-7.5 seconds."""
    steps = [
        {"id": "open", "action": "navigate", "url": f"{ORIGIN}/leads/new"},
        {
            "id": "fill-email",
            "action": "fill",
            "targets": [{"by": "label", "text": "Email"}],
            "value": {"kind": "input", "column": "email"},
        },
        {
            "id": "save",
            "action": "click",
            "commit": True,
            "assert_after": {"text_visible": "Lead created", "timeout_ms": 8000},
            "targets": [{"by": "role", "role": "button", "name": "Save"}],
        },
        {
            "id": "done",
            "action": "end_record",
            "outcome": "verified",
            "reason": "Lead created for {{row.key}}.",
        },
    ]
    base = seed(recipe(steps))
    base.add_input_record(email="a@x.com")
    scripted = ScriptedRun.build()
    run_id = base.create_run()
    conn.execute("UPDATE runs SET min_seconds_between_steps = 2 WHERE id = %s", (run_id,))
    worker = make_worker(scripted)
    worker.run_once()

    # Three executor steps, each preceded by its own gap.
    assert 6.0 <= scripted.clock.time() <= 7.5, scripted.clock.time()
    assert _states(conn, run_id) == {"row-0001": "verified"}


def test_zero_settings_behave_exactly_like_today(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    make_worker: Callable[..., Worker],
) -> None:
    """No pacing set: the clock does not advance for pacing, and the run completes."""
    base = seed()
    base.add_input_record(email="a@x.com")
    base.add_input_record(email="b@x.com")
    scripted = ScriptedRun.build()
    run_id = base.create_run()
    worker = make_worker(scripted)
    worker.run_once()

    assert scripted.clock.time() == 0.0, scripted.clock.time()
    assert _states(conn, run_id) == {"row-0001": "verified", "row-0002": "verified"}
    status, _reason = base.run_status(run_id)
    assert status == "completed"


# --- the daily cap per site --------------------------------------------------


def _seed_cap(conn: psycopg.Connection, tenant_id: uuid.UUID, limit: int) -> None:
    conn.execute(
        "UPDATE tenants SET settings = %s WHERE id = %s",
        (psycopg.types.json.Jsonb({"site_daily_caps": {ORIGIN: limit}}), tenant_id),
    )


def _count_submitting(conn: psycopg.Connection, tenant_id: uuid.UUID, n: int) -> None:
    """`n` transitions into `submitting` today, on this tenant's own runs."""
    rows = conn.execute(
        "SELECT rr.id FROM run_records rr JOIN runs r ON r.id = rr.run_id "
        "WHERE r.tenant_id = %s LIMIT %s",
        (tenant_id, n),
    ).fetchall()
    for (record_id,) in rows:
        conn.execute(
            "INSERT INTO run_record_transitions (tenant_id, run_record_id, from_state, "
            " to_state, reason, actor, at) VALUES (%s, %s, 'filling', 'submitting', 'test', "
            " 'worker-1', now())",
            (tenant_id, record_id),
        )


def test_the_cap_pauses_before_claiming_and_the_record_stays_pending(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    make_worker: Callable[..., Worker],
) -> None:
    """Two rows are allowed today and two already went: the third row is never claimed."""
    base = seed()
    for email in ("a@x.com", "b@x.com", "c@x.com"):
        base.add_input_record(email=email)
    scripted = ScriptedRun.build()
    run_id = base.create_run()
    _seed_cap(conn, base.tenant_id, 2)
    _count_submitting(conn, base.tenant_id, 2)

    worker = make_worker(scripted)
    worker.run_once()

    status, reason = base.run_status(run_id)
    assert (status, reason) == ("paused", "site_daily_cap")
    states = _states(conn, run_id)
    assert list(states.values()).count("pending") == 3, states
    assert scripted.destination.commits == 0, "nothing reached the commit window"


def test_another_tenant_s_rows_do_not_count(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    make_worker: Callable[..., Worker],
) -> None:
    """Someone else's submits today are not this tenant's rows."""
    base = seed()
    for email in ("a@x.com", "b@x.com"):
        base.add_input_record(email=email)
    scripted = ScriptedRun.build()
    run_id = base.create_run()
    # The tenant's own two rows fit under its cap of two; five rows of someone else's do not count.
    _seed_cap(conn, base.tenant_id, 2)
    other_tenant = conn.execute(
        "INSERT INTO tenants (name) VALUES ('Someone else') RETURNING id"
    ).fetchone()[0]
    _count_submitting(conn, other_tenant, 5)

    worker = make_worker(scripted)
    worker.run_once()

    status, reason = base.run_status(run_id)
    assert (status, reason) == ("completed", None)
    assert _states(conn, run_id) == {"row-0001": "verified", "row-0002": "verified"}


def test_a_cap_counts_only_its_own_site(
    seed: Callable[..., Seed],
    conn: psycopg.Connection,
    make_worker: Callable[..., Worker],
) -> None:
    """A cap on another origin does not touch a run whose recipe does not allow that origin."""
    base = seed()
    base.add_input_record(email="a@x.com")
    scripted = ScriptedRun.build()
    run_id = base.create_run()
    conn.execute(
        "UPDATE tenants SET settings = %s WHERE id = %s",
        (
            psycopg.types.json.Jsonb({"site_daily_caps": {"https://other.example": 0}}),
            base.tenant_id,
        ),
    )

    worker = make_worker(scripted)
    worker.run_once()

    status, reason = base.run_status(run_id)
    assert (status, reason) == ("completed", None)
