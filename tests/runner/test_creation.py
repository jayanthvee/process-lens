"""Run creation is fail-closed: every reason to refuse is listed, and nothing is written."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable

import psycopg
import pytest

from services.runner.creation import CreationError, create_run
from tests.runner.conftest import DEFAULT_STEPS, ORIGIN, Seed, recipe


def _count_records(conn: psycopg.Connection, run_id: uuid.UUID) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM run_records WHERE run_id = %s", (run_id,))
        return int(cur.fetchone()[0])


def _create(seed: Seed, document: dict, **overrides) -> uuid.UUID:
    """Store ``document`` as a new version and create a run pinned to it (or to an override)."""
    version_id = overrides.get("recipe_version_id", seed.new_version(document))
    with seed.conn.transaction():
        return create_run(
            seed.conn,
            workflow_id=overrides.get("workflow_id", seed.workflow_id),
            recipe_version_id=version_id,
            input_batch_id=overrides.get("input_batch_id", seed.batch_id),
            mode=overrides.get("mode", "batch"),
        )


# --- the happy shape --------------------------------------------------------


def test_creates_one_ledger_row_per_ready_input_record(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    base.add_input_record(email="b@x.com")
    base.add_input_record(email="c@x.com")
    base.add_input_record(email="d@x.com", status="excluded", issues=["no email"])
    base.add_input_record(email="e@x.com", status="needs_review")

    run_id = base.create_run()

    assert _count_records(conn, run_id) == 3, (
        "excluded and needs_review rows never become ledger rows"
    )
    with conn.cursor() as cur:
        cur.execute("SELECT destination_key, status, mode FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone() == ("demo-crm", "queued", "batch")


def test_copies_the_recipe_approval_policy_onto_the_run(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed(recipe(approval_policy={"commit": "first_record"}))
    base.add_input_record(email="a@x.com")

    run_id = base.create_run()

    with conn.cursor() as cur:
        cur.execute("SELECT approval_policy FROM runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == {"commit": "first_record"}


# --- fail closed ------------------------------------------------------------


def test_refuses_unsupported_action(seed: Callable[..., Seed]) -> None:
    steps = copy.deepcopy(DEFAULT_STEPS)
    steps.insert(
        0,
        {
            "id": "x",
            "action": "transform",
            "as": "y",
            "op": "lower",
            "input": {"kind": "constant", "value": "a"},
        },
    )
    base = seed()
    base.add_input_record(email="a@x.com")

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(steps))

    assert any("unsupported step action" in problem for problem in caught.value.problems)


def test_refuses_unknown_template_filter(seed: Callable[..., Seed]) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(record_key="{{row.key | shout}}"))

    assert any("unknown template filter" in problem for problem in caught.value.problems)


def test_refuses_missing_column(seed: Callable[..., Seed]) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(record_key="{{row.missing_column}}"))

    assert any("no column" in problem for problem in caught.value.problems)


def test_rendering_failure_lists_every_failing_row(seed: Callable[..., Seed]) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    base.add_input_record(email="b@x.com")

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(record_key="{{row.missing_column}}"))

    assert len(caught.value.problems) == 2, "one problem per failing row"


def test_refuses_two_rows_that_render_the_same_record_key(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    base.add_input_record(email="b@x.com")
    base.add_input_record(email="a@x.com")

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(record_key="{{row.email}}"))

    assert caught.value.problems == ["duplicate record_key 'a@x.com' on rows 1, 3"]
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM runs WHERE workflow_id = %s", (base.workflow_id,))
        assert cur.fetchone()[0] == 0, "no run is created when a record_key repeats"


def test_refuses_an_end_record_failed(seed: Callable[..., Seed]) -> None:
    steps = [
        {"id": "open", "action": "navigate", "url": f"{ORIGIN}/x"},
        {"id": "done", "action": "end_record", "outcome": "failed", "reason": "no"},
    ]
    base = seed()
    base.add_input_record(email="a@x.com")

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(steps))

    assert any("may not end a record failed" in problem for problem in caught.value.problems)


def test_refuses_an_impossible_end_record_outcome(seed: Callable[..., Seed]) -> None:
    steps = [{"id": "done", "action": "end_record", "outcome": "verified", "reason": "nothing ran"}]
    base = seed()
    base.add_input_record(email="a@x.com")

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(steps))

    assert any("before any step has run" in problem for problem in caught.value.problems)


# --- consistency (S5) -------------------------------------------------------


def _other_workflow(
    conn: psycopg.Connection, seed: Seed, *, tenant_id: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A second workflow with its own version and batch, under ``tenant_id`` or the seed."""
    tenant = tenant_id or seed.tenant_id
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, 'Other', 'demo-crm', %s) RETURNING id",
            (tenant, [ORIGIN]),
        )
        workflow_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'x') RETURNING id",
            (tenant, workflow_id, psycopg.types.json.Jsonb(recipe())),
        )
        version_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO input_batches (tenant_id, workflow_id, kind) "
            "VALUES (%s, %s, 'csv') RETURNING id",
            (tenant, workflow_id),
        )
        batch_id = cur.fetchone()[0]
    return workflow_id, version_id, batch_id


def test_refuses_a_recipe_version_from_another_workflow(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    _other_workflow_id, other_version, _batch = _other_workflow(conn, base)

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(), recipe_version_id=other_version)

    assert any("belongs to workflow" in problem for problem in caught.value.problems)


def test_refuses_an_input_batch_from_another_workflow(
    seed: Callable[..., Seed], conn: psycopg.Connection
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    _workflow, _version, other_batch = _other_workflow(conn, base)

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(), input_batch_id=other_batch)

    assert any("belongs to workflow" in problem for problem in caught.value.problems)


def test_refuses_a_cross_tenant_pin(seed: Callable[..., Seed], conn: psycopg.Connection) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (name) VALUES ('Other tenant') RETURNING id")
        other_tenant = cur.fetchone()[0]
    _workflow, other_version, _batch = _other_workflow(conn, base, tenant_id=other_tenant)

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(), recipe_version_id=other_version)

    assert caught.value.problems, "a cross-tenant pin must be refused"


def test_a_missing_workflow_is_refused(seed: Callable[..., Seed]) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")

    with pytest.raises(CreationError) as caught:
        _create(base, recipe(), workflow_id=uuid.uuid4())

    assert any("does not exist" in problem for problem in caught.value.problems)
