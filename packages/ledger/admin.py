"""The admin panel's own state: the freeze switch, the extension floor, and the audit log.

Every statement that names ``admin_modules``, ``extension_reports`` or ``admin_audit`` lives here,
and so do the admin-side reads and writes of ``tenants`` and ``users``, so the API writes no SQL of
its own. Functions take a connection and never open a transaction of their own: the caller owns the
boundary, because an admin action and its ``admin_audit`` row must land together or not at all.

Nothing here touches ``runs``, ``run_records``, ``committed_keys`` or ``jobs``: the runner owns
every write to the ledger, and the admin panel reaches those tables only through the API.

``tenant_is_frozen`` is called by the runner at every step boundary, next to its
``cancel_requested_at`` check, so it is a single lookup by primary key.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import ExtensionReportRow, TenantAdminState, UserRow, _row

_SELECT_TENANT_ADMIN_STATE = """
SELECT id AS tenant_id, runs_frozen_at, runs_frozen_by, min_extension_version
FROM tenants
WHERE id = %(tenant)s
"""

# The runner's step-boundary check. One indexed lookup, and no row means "not frozen".
_SELECT_TENANT_FROZEN = """
SELECT runs_frozen_at IS NOT NULL AS frozen
FROM tenants
WHERE id = %(tenant)s
"""

_FREEZE_RUNS = """
UPDATE tenants
SET runs_frozen_at = now(), runs_frozen_by = %(actor)s
WHERE id = %(tenant)s
"""

# The actor is cleared with the timestamp: the freeze has no author once it is lifted.
_UNFREEZE_RUNS = """
UPDATE tenants
SET runs_frozen_at = NULL, runs_frozen_by = NULL
WHERE id = %(tenant)s
"""

_SET_MIN_EXTENSION_VERSION = """
UPDATE tenants SET min_extension_version = %(version)s WHERE id = %(tenant)s
"""

# PL-080: one `jsonb_set` replaces `site_daily_caps` and leaves every other settings key exactly
# as it was. The key is created when it is not there yet (`create_if_missing`), and `settings`
# itself is NOT NULL, so no separate insert is ever needed.
_SET_SITE_DAILY_CAPS = """
UPDATE tenants
SET settings = jsonb_set(settings, '{site_daily_caps}', %(caps)s::jsonb, true)
WHERE id = %(tenant)s
"""

_SELECT_USER = """
SELECT id, tenant_id, role::text AS role, deactivated_at
FROM users
WHERE id = %(user)s
"""

_SELECT_EXTENSION_REPORT = """
SELECT user_id, tenant_id, version, reported_at
FROM extension_reports
WHERE user_id = %(user)s
"""

# One row per person: the newest report replaces the previous one.
_REPORT_EXTENSION = """
INSERT INTO extension_reports (user_id, tenant_id, version)
VALUES (%(user)s, %(tenant)s, %(version)s)
ON CONFLICT (user_id) DO UPDATE
SET version = excluded.version, tenant_id = excluded.tenant_id, reported_at = now()
RETURNING user_id, tenant_id, version, reported_at
"""

_INSERT_ADMIN_AUDIT = """
INSERT INTO admin_audit (tenant_id, actor_user_id, module, action, target, detail)
VALUES (%(tenant)s, %(actor)s, %(module)s, %(action)s, %(target)s, %(detail)s)
RETURNING id
"""


def get_tenant_admin_state(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> TenantAdminState | None:
    """A tenant's switches -- frozen or not, by whom, and the extension floor -- or ``None``."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_TENANT_ADMIN_STATE, {"tenant": tenant_id})
        row = cur.fetchone()
    return _row(TenantAdminState, row) if row is not None else None


def tenant_is_frozen(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    """Whether runs are paused for this tenant. ``False`` when there is no such tenant.

    One query, so the runner can call it at every step boundary.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_TENANT_FROZEN, {"tenant": tenant_id})
        row = cur.fetchone()
    return bool(row["frozen"]) if row is not None else False


def freeze_runs(
    conn: psycopg.Connection, tenant_id: uuid.UUID, actor_user_id: uuid.UUID | None = None
) -> bool:
    """Pause everything for this tenant. Returns ``True`` when the tenant was there to freeze."""
    with conn.cursor() as cur:
        cur.execute(_FREEZE_RUNS, {"tenant": tenant_id, "actor": actor_user_id})
        return cur.rowcount > 0


def unfreeze_runs(
    conn: psycopg.Connection, tenant_id: uuid.UUID, actor_user_id: uuid.UUID | None = None
) -> bool:
    """Lift the freeze. Returns ``True`` when the tenant was there to unfreeze.

    ``actor_user_id`` is accepted only so a caller can pass it to the ``admin_audit`` row it writes
    in the same transaction: lifting a freeze clears the author with it, so the column is not used
    here.
    """
    with conn.cursor() as cur:
        cur.execute(_UNFREEZE_RUNS, {"tenant": tenant_id})
        return cur.rowcount > 0


def set_min_extension_version(
    conn: psycopg.Connection, tenant_id: uuid.UUID, version: str | None
) -> bool:
    """Set the oldest extension version this tenant may run on; ``None`` clears it.

    Returns ``True`` when the tenant was there to change.
    """
    with conn.cursor() as cur:
        cur.execute(_SET_MIN_EXTENSION_VERSION, {"tenant": tenant_id, "version": version})
        return cur.rowcount > 0


def set_site_daily_caps(
    conn: psycopg.Connection, tenant_id: uuid.UUID, caps: dict[str, int]
) -> bool:
    """Replace this tenant's `site_daily_caps`; every other settings key is untouched.

    The whole map is written at once, through one `jsonb_set`, because the Settings page saves one
    list: a partial write would leave caps the person removed still standing. Returns ``True`` when
    the tenant was there to change.
    """
    with conn.cursor() as cur:
        cur.execute(_SET_SITE_DAILY_CAPS, {"tenant": tenant_id, "caps": Jsonb(caps)})
        return cur.rowcount > 0


def get_user(conn: psycopg.Connection, user_id: uuid.UUID) -> UserRow | None:
    """One person's tenant, role and deactivation, or ``None`` when there is no such person."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_USER, {"user": user_id})
        row = cur.fetchone()
    return _row(UserRow, row) if row is not None else None


def get_extension_report(conn: psycopg.Connection, user_id: uuid.UUID) -> ExtensionReportRow | None:
    """The version this person's extension last reported, or ``None`` when it never has."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_EXTENSION_REPORT, {"user": user_id})
        row = cur.fetchone()
    return _row(ExtensionReportRow, row) if row is not None else None


def record_extension_report(
    conn: psycopg.Connection, *, user_id: uuid.UUID, tenant_id: uuid.UUID, version: str
) -> ExtensionReportRow:
    """Store the version a person's extension just reported, replacing the previous one."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            _REPORT_EXTENSION,
            {"user": user_id, "tenant": tenant_id, "version": version},
        )
        row = cur.fetchone()
    return _row(ExtensionReportRow, row)


def record_admin_action(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    action: str,
    module: str = "core",
    actor_user_id: uuid.UUID | None = None,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    """Write one ``admin_audit`` row and return its id. ``None`` detail is stored as ``{}``.

    The caller writes this in the same transaction as the change it describes: an action without its
    audit row, or an audit row without its action, must not survive.
    """
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_ADMIN_AUDIT,
            {
                "tenant": tenant_id,
                "actor": actor_user_id,
                "module": module,
                "action": action,
                "target": target,
                "detail": Jsonb(detail if detail is not None else {}),
            },
        )
        row = cur.fetchone()
    return int(row[0])


__all__ = [
    "freeze_runs",
    "get_extension_report",
    "get_tenant_admin_state",
    "get_user",
    "record_admin_action",
    "record_extension_report",
    "set_min_extension_version",
    "set_site_daily_caps",
    "tenant_is_frozen",
    "unfreeze_runs",
]
