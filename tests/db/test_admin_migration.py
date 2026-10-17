"""Migration 0006: the admin module registry, the admin role, and the admin audit log.

These run against the real schema, built by every migration in ``db/migrations``, so the grants and
the append-only triggers checked here are the ones the deploy creates. The ledger rows come from the
seed fixture, so a test cannot forge a run or a record the database would reject in production.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from packages.ledger import (
    freeze_runs,
    get_extension_report,
    get_tenant_admin_state,
    get_user,
    record_admin_action,
    record_extension_report,
    set_min_extension_version,
    tenant_is_frozen,
    unfreeze_runs,
)
from tests.db.seed import Ledger

ADMIN_ROLE = "processlens_admin"
CORE_MIGRATION = "0006_admin_core.sql"


def _one(conn: psycopg.Connection, sql: str, params: tuple = ()) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _as_admin(conn: psycopg.Connection) -> None:
    """Become the role the admin panel connects as; the caller RESETs ROLE in a ``finally``."""
    conn.execute("SET ROLE processlens_admin")


def _reset_role(conn: psycopg.Connection) -> None:
    conn.execute("RESET ROLE")


# --- the admin module registry ----------------------------------------------


def test_admin_modules_names_this_migration(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, migration FROM admin_modules ORDER BY id")
        assert cur.fetchall() == [("core", CORE_MIGRATION)]
        cur.execute("SELECT added_at IS NOT NULL FROM admin_modules WHERE id = 'core'")
        assert cur.fetchone()[0] is True


# --- the role the admin panel connects as -----------------------------------


def test_the_admin_role_exists_and_cannot_log_in(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (ADMIN_ROLE,))
        assert cur.fetchall() == [(False,)]
        # Backslash escapes the underscore, so only the one admin role is expected.
        cur.execute(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE 'processlens\\_admin%' "
            "ORDER BY rolname"
        )
        assert cur.fetchall() == [(ADMIN_ROLE,)], "no other admin role was invented"


def test_the_admin_role_cannot_touch_the_ledger(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    record_id = ledger.add_record(run)
    input_record_id = _one(
        conn, "SELECT input_record_id FROM run_records WHERE id = %s", (record_id,)
    )

    _as_admin(conn)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE runs SET status = 'cancelled' WHERE id = %s", (run.id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE run_records SET state = 'parked' WHERE id = %s", (record_id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO run_records "
                "(tenant_id, run_id, input_record_id, record_key, idempotency_key) "
                "VALUES (%s, %s, %s, 'admin-insert', 'admin-insert')",
                (ledger.tenant_id, run.id, input_record_id),
            )
    finally:
        _reset_role(conn)

    assert _one(conn, "SELECT status FROM runs WHERE id = %s", (run.id,)) == "queued"
    assert _one(conn, "SELECT state FROM run_records WHERE id = %s", (record_id,)) == "pending"


def test_the_admin_role_writes_exactly_its_own_columns(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    user = ledger.add_user()

    _as_admin(conn)
    try:
        conn.execute(
            "UPDATE users SET role = 'reviewer', deactivated_at = now() WHERE id = %s", (user,)
        )
        # The identity column needs no sequence grant of its own.
        audit_id = conn.execute(
            "INSERT INTO admin_audit (tenant_id, actor_user_id, module, action, target) "
            "VALUES (%s, %s, 'core', 'change_role', %s) RETURNING id",
            (ledger.tenant_id, user, str(user)),
        ).fetchone()[0]
    finally:
        _reset_role(conn)

    assert isinstance(audit_id, int) and audit_id > 0
    assert _one(conn, "SELECT role::text FROM users WHERE id = %s", (user,)) == "reviewer"
    assert _one(conn, "SELECT deactivated_at IS NOT NULL FROM users WHERE id = %s", (user,)) is True


def test_the_admin_role_cannot_set_the_extension_floor(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    """PL-034: the panel sets ``min_extension_version`` through the API (ADM-001), not directly.

    The API connects as the database owner, so its own write is unaffected; the role the admin
    panel's login inherits keeps only the columns nothing else writes.
    """
    _as_admin(conn)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE tenants SET min_extension_version = '1.9.0' WHERE id = %s",
                (ledger.tenant_id,),
            )
    finally:
        _reset_role(conn)

    assert (
        _one(
            conn,
            "SELECT min_extension_version FROM tenants WHERE id = %s",
            (ledger.tenant_id,),
        )
        is None
    )


def test_an_ungranted_column_update_is_refused(conn: psycopg.Connection, ledger: Ledger) -> None:
    user = ledger.add_user()

    _as_admin(conn)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE users SET name = 'x' WHERE id = %s", (user,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE tenants SET runs_frozen_at = now() WHERE id = %s", (ledger.tenant_id,)
            )
    finally:
        _reset_role(conn)

    assert _one(conn, "SELECT name FROM users WHERE id = %s", (user,)) == "Synthetic person"
    assert (
        _one(
            conn,
            "SELECT runs_frozen_at IS NULL FROM tenants WHERE id = %s",
            (ledger.tenant_id,),
        )
        is True
    )


def test_the_admin_role_reads_every_table(conn: psycopg.Connection, ledger: Ledger) -> None:
    run = ledger.new_run()
    ledger.add_record(run)
    ledger.add_user()
    record_admin_action(conn, tenant_id=ledger.tenant_id, action="freeze_runs")

    _as_admin(conn)
    try:
        assert (
            _one(conn, "SELECT count(*) FROM runs WHERE tenant_id = %s", (ledger.tenant_id,)) == 1
        )
        assert (
            _one(conn, "SELECT count(*) FROM users WHERE tenant_id = %s", (ledger.tenant_id,)) == 1
        )
        assert _one(conn, "SELECT count(*) FROM tenants WHERE id = %s", (ledger.tenant_id,)) == 1
        assert (
            _one(conn, "SELECT count(*) FROM admin_audit WHERE tenant_id = %s", (ledger.tenant_id,))
            == 1
        )
        assert _one(conn, "SELECT count(*) FROM admin_modules") == 1
        assert _one(conn, "SELECT count(*) FROM extension_reports") >= 0
    finally:
        _reset_role(conn)


# --- the audit log ----------------------------------------------------------


def test_admin_audit_is_append_only(conn: psycopg.Connection, ledger: Ledger) -> None:
    audit_id = record_admin_action(conn, tenant_id=ledger.tenant_id, action="freeze_runs")

    with pytest.raises(psycopg.errors.RaiseException, match="admin_audit is append-only"):
        conn.execute("UPDATE admin_audit SET action = 'cancel_run' WHERE id = %s", (audit_id,))
    with pytest.raises(psycopg.errors.RaiseException, match="admin_audit is append-only"):
        conn.execute("DELETE FROM admin_audit WHERE id = %s", (audit_id,))
    with pytest.raises(psycopg.errors.RaiseException, match="TRUNCATE"):
        conn.execute("TRUNCATE admin_audit")

    assert _one(conn, "SELECT action FROM admin_audit WHERE id = %s", (audit_id,)) == "freeze_runs"


def test_admin_audit_takes_only_this_modules_actions(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        record_admin_action(conn, tenant_id=ledger.tenant_id, action="drop_everything")

    # Another module's actions are its own to constrain, in its own migration.
    other = record_admin_action(
        conn, tenant_id=ledger.tenant_id, module="billing", action="set_plan"
    )

    assert other > 0


def test_record_admin_action_writes_the_detail_it_is_given(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    audit_id = record_admin_action(
        conn,
        tenant_id=ledger.tenant_id,
        action="set_min_extension_version",
        target="1.9.0",
        detail={"version": "1.9.0"},
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT module, action, target, detail FROM admin_audit WHERE id = %s", (audit_id,)
        )
        assert cur.fetchone() == (
            "core",
            "set_min_extension_version",
            "1.9.0",
            {"version": "1.9.0"},
        )

    latest = record_admin_action(conn, tenant_id=ledger.tenant_id, action="unfreeze_runs")

    assert latest > audit_id
    assert _one(conn, "SELECT detail FROM admin_audit WHERE id = %s", (latest,)) == {}


# --- extension_reports ------------------------------------------------------


def test_extension_reports_is_keyed_by_user(conn: psycopg.Connection, ledger: Ledger) -> None:
    user = ledger.add_user()

    # The API is the validator: the database stores whatever version it is told.
    conn.execute(
        "INSERT INTO extension_reports (user_id, tenant_id, version) VALUES (%s, %s, %s)",
        (user, ledger.tenant_id, "not-a-version"),
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO extension_reports (user_id, tenant_id, version) VALUES (%s, %s, '1.9.0')",
            (user, ledger.tenant_id),
        )


def test_record_extension_report_upserts_one_row(conn: psycopg.Connection, ledger: Ledger) -> None:
    user = ledger.add_user()

    first = record_extension_report(conn, user_id=user, tenant_id=ledger.tenant_id, version="1.9.2")
    second = record_extension_report(
        conn, user_id=user, tenant_id=ledger.tenant_id, version="1.10.0"
    )

    assert (first.user_id, first.version) == (user, "1.9.2")
    assert second.version == "1.10.0" and second.reported_at >= first.reported_at
    assert _one(conn, "SELECT count(*) FROM extension_reports WHERE user_id = %s", (user,)) == 1

    stored = get_extension_report(conn, user)

    assert stored is not None and stored.version == "1.10.0"
    assert get_extension_report(conn, uuid.uuid4()) is None


# --- the new columns default to "not frozen", "any version" -----------------


def test_a_new_tenant_is_not_frozen_and_has_no_extension_floor(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT runs_frozen_at IS NULL, runs_frozen_by IS NULL, min_extension_version IS NULL "
            "FROM tenants WHERE id = %s",
            (ledger.tenant_id,),
        )
        assert cur.fetchone() == (True, True, True)

    assert tenant_is_frozen(conn, ledger.tenant_id) is False

    state = get_tenant_admin_state(conn, ledger.tenant_id)

    assert state is not None
    assert (state.runs_frozen_at, state.runs_frozen_by, state.min_extension_version) == (
        None,
        None,
        None,
    )


def test_the_freeze_switch_round_trips(conn: psycopg.Connection, ledger: Ledger) -> None:
    actor = ledger.add_user(role="owner")

    assert freeze_runs(conn, ledger.tenant_id, actor) is True
    assert tenant_is_frozen(conn, ledger.tenant_id) is True
    frozen = get_tenant_admin_state(conn, ledger.tenant_id)
    assert frozen is not None and frozen.runs_frozen_at is not None
    assert frozen.runs_frozen_by == actor

    assert unfreeze_runs(conn, ledger.tenant_id, actor) is True
    assert tenant_is_frozen(conn, ledger.tenant_id) is False
    cleared = get_tenant_admin_state(conn, ledger.tenant_id)
    assert cleared is not None and cleared.runs_frozen_at is None and cleared.runs_frozen_by is None

    assert set_min_extension_version(conn, ledger.tenant_id, "1.9.0") is True
    assert (
        _one(conn, "SELECT min_extension_version FROM tenants WHERE id = %s", (ledger.tenant_id,))
        == "1.9.0"
    )
    assert set_min_extension_version(conn, ledger.tenant_id, None) is True

    missing = uuid.uuid4()
    assert tenant_is_frozen(conn, missing) is False, "a tenant that is not there is not frozen"
    assert get_tenant_admin_state(conn, missing) is None
    assert set_min_extension_version(conn, missing, "1.9.0") is False


# --- the seed helper other test files import --------------------------------


def test_add_user_inserts_one_person_with_unique_defaults(
    conn: psycopg.Connection, ledger: Ledger
) -> None:
    first = ledger.add_user()
    second = ledger.add_user()
    owner = ledger.add_user(email="chief@example.com", name="The Boss", role="owner")

    with conn.cursor() as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (first,))
        assert cur.fetchone()[0] == "person-1@example.com"
        cur.execute("SELECT email FROM users WHERE id = %s", (second,))
        assert cur.fetchone()[0] == "person-2@example.com"
        cur.execute("SELECT email, name FROM users WHERE id = %s", (owner,))
        assert cur.fetchone() == ("chief@example.com", "The Boss")

    row = get_user(conn, owner)
    assert row is not None
    assert (row.id, row.tenant_id, row.role, row.deactivated_at) == (
        owner,
        ledger.tenant_id,
        "owner",
        None,
    )
    assert get_user(conn, uuid.uuid4()) is None


def test_add_user_can_deactivate_the_person(conn: psycopg.Connection, ledger: Ledger) -> None:
    user = ledger.add_user(deactivated=True)

    row = get_user(conn, user)

    assert row is not None and row.deactivated_at is not None
