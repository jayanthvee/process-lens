"""The Apps page's own read: the sites the saved tasks may use.

One app per origin in a workflow's ``allowed_origins``, with the tasks that may use it and the
newest run of any of them. The read itself lives in ``packages.ledger.reads`` (the separation rule,
``tests/runner/test_sql_separation.py``); this module only names the app and shapes the JSON, so it
writes no ledger statement of its own and carries no row contents.

The name is worked out here, not in SQL, because it is a presentation rule: the two apps this
product ships with are recognised by their host or their usual port, and anything else is called by
its host, without the port.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from packages.ledger import AppRow, list_apps

from .db import connection

router = APIRouter(prefix="/api/v1", tags=["apps"])

# The origin's scheme, and nothing else: what is left is the host, the port and the path.
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

# The ports Odoo and Twenty are served on by default.
ODOO_PORT = 8069
TWENTY_PORT = 3000


def app_host(origin: str) -> str:
    """The host and port of an origin, e.g. ``localhost:8069`` (the page shows this in full)."""
    without_scheme = _SCHEME.sub("", origin.strip())
    return without_scheme.split("/", 1)[0]


def _port(host: str) -> int | None:
    """The port on the host, or ``None`` when it carries none."""
    _name, colon, tail = host.rpartition(":")
    return int(tail) if colon and tail.isdigit() else None


def app_name(origin: str) -> str:
    """The app an origin belongs to: "Odoo", "Twenty", or the host without its port.

    A host that merely contains the app's name counts too, so ``odoo.example.com`` is Odoo and
    ``twenty.example.com`` is Twenty whatever port either is served on.
    """
    host = app_host(origin)
    port = _port(host)
    lowered = host.lower()
    if "odoo" in lowered or port == ODOO_PORT:
        return "Odoo"
    if "twenty" in lowered or port == TWENTY_PORT:
        return "Twenty"
    return host.rsplit(":", 1)[0] if port is not None else host


class AppTaskEntry(BaseModel):
    """One task that may use an app's site."""

    id: uuid.UUID
    name: str


class AppEntry(BaseModel):
    """One app: its name, the site it lives on, its tasks, and the newest run of any of them."""

    name: str
    host: str
    origin: str
    tasks: list[AppTaskEntry]
    last_run_at: datetime | None
    last_run_status: str | None


def _entry(row: AppRow) -> AppEntry:
    return AppEntry(
        name=app_name(row.origin),
        host=app_host(row.origin),
        origin=row.origin,
        tasks=[AppTaskEntry(id=task.id, name=task.name) for task in row.tasks],
        last_run_at=row.last_run_at,
        last_run_status=row.last_run_status,
    )


@router.get("/apps", response_model=list[AppEntry])
def apps(tenant_id: Annotated[uuid.UUID | None, Query()] = None) -> list[AppEntry]:
    """Every app a saved task may use, sorted by name, with the tasks that use it."""
    with connection() as conn:
        rows = list_apps(conn, tenant_id=tenant_id)
    entries = [_entry(row) for row in rows]
    # Two sites can share a name (two Odoo hosts); the host breaks the tie, so the order is stable.
    entries.sort(key=lambda entry: (entry.name, entry.host))
    return entries


__all__ = ["app_host", "app_name", "router"]
