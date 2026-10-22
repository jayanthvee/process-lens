"""The Settings page's daily limits per site (PL-080).

A tenant's caps live in ``tenants.settings->'site_daily_caps'`` as ``{origin: rows per day}``, the
shape PL-067's runner reads. The page reads them and saves the whole list at once; the write is one
``jsonb_set`` (``packages.ledger.set_site_daily_caps``), so every other settings key is left exactly
as it was.

**The origin rule.** A recipe's ``meta.allowed_origins`` is compiled by
:func:`packages.compiler.assertions.origin_of`, which writes ``scheme://netloc`` from the recorded
page URL: the scheme and host a browser reports (lower case), the port when the URL named one,
``www.`` kept when the site's own URL has it, and **no trailing slash and no path**. PL-067 counts a
site's rows by exact text (``recipe->'meta'->'allowed_origins' ? origin`` and ``caps.get(origin)``),
so an origin typed here is normalised to that same shape before it is stored: the scheme and host
are lower-cased, one trailing slash is dropped, and anything that is not a bare site -- a path, a
query, a fragment, a name and password, a scheme other than http(s) -- is refused rather than
silently altered. ``www.`` is never added or removed: it is part of the host the recipe recorded.

**Who may change them.** Everyone sees the limits. A change needs an admin: the shared admin token
(``services.api.admin.require_admin_token``, the check every existing admin route uses) or the
tenant's owner, which is the same rule the admin panel's own sign-in applies. The web page carries
no token, so without the owner rule the add/edit/remove controls on it could never work.
"""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import urlsplit

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from packages.ledger import reads, set_site_daily_caps

from .admin import admin_token_matches
from .db import connection
from .operator import operator_user, require_operator_user

router = APIRouter(prefix="/api/v1", tags=["settings"])

SITE_LIMITS_PATH = "/api/v1/settings/site-limits"

# The person role the admin panel admits as an admin (ADM-001: an owner is an admin).
ADMIN_ROLE = "owner"

REFUSED_DETAIL = "Only an admin can change the daily limits."
ORIGIN_DETAIL = "an origin must be https://host or http://host[:port], with no path"
LIMIT_DETAIL = "a limit is a whole number of rows per day, from 1 to 10,000"
DUPLICATE_DETAIL = "is listed more than once"


class SiteLimitRow(BaseModel):
    """One row of the Settings section: a site and its number of rows per day."""

    origin: str
    limit: Annotated[int, Field(strict=True, ge=1, le=10_000)]


class SiteLimitsBody(BaseModel):
    """The whole list the page is saving. An empty list removes every cap."""

    sites: list[SiteLimitRow]


class SiteLimitOut(BaseModel):
    origin: str
    limit: int


class SiteLimits(BaseModel):
    """What the page is told: this tenant's caps, and whether this person may change them."""

    tenant_id: uuid.UUID
    sites: list[SiteLimitOut]
    can_change: bool


def normalise_origin(raw: str) -> str:
    """An origin in the shape a recipe's ``allowed_origins`` writes it.

    Raises :class:`ValueError` with a sentence a person can act on when the value is not a bare
    site. A single trailing slash is dropped, because it changes nothing about which site it is and
    a person copying a URL will bring one; anything more specific is refused instead of guessed at.
    """
    value = raw.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError(ORIGIN_DETAIL)
    if parts.hostname is None:
        raise ValueError(ORIGIN_DETAIL)
    if parts.username is not None or parts.password is not None:
        raise ValueError(ORIGIN_DETAIL)
    if parts.query or parts.fragment:
        raise ValueError(ORIGIN_DETAIL)
    if parts.path not in {"", "/"}:
        raise ValueError(ORIGIN_DETAIL)
    try:
        port = parts.port
    except ValueError as exc:  # a port that is not a number
        raise ValueError(ORIGIN_DETAIL) from exc
    host = parts.hostname
    if ":" in host:  # an IPv6 literal keeps its brackets
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return f"{parts.scheme.lower()}://{netloc}"


def _caps_from(body: SiteLimitsBody) -> dict[str, int]:
    """The validated rows as the stored map, refusing a site named twice."""
    caps: dict[str, int] = {}
    for index, row in enumerate(body.sites, start=1):
        try:
            origin = normalise_origin(row.origin)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"site {index}: {exc}") from exc
        if origin in caps:
            raise HTTPException(
                status_code=422, detail=f"site {index}: {origin} {DUPLICATE_DETAIL}"
            )
        caps[origin] = row.limit
    return caps


def may_change_site_limits(conn: psycopg.Connection, request: Request) -> bool:
    """Whether this request is an admin's: the tenant's owner, or the admin token."""
    person = operator_user(conn)
    if person is not None and person.role == ADMIN_ROLE and person.deactivated_at is None:
        return True
    return admin_token_matches(request)


def _answer(conn: psycopg.Connection, tenant_id: uuid.UUID, can_change: bool) -> SiteLimits:
    caps = reads.tenant_site_caps(conn, tenant_id)
    return SiteLimits(
        tenant_id=tenant_id,
        sites=[SiteLimitOut(origin=origin, limit=caps[origin]) for origin in sorted(caps)],
        can_change=can_change,
    )


@router.get("/settings/site-limits", response_model=SiteLimits)
def get_site_limits(request: Request) -> SiteLimits:
    """This tenant's daily limits per site, read-only for everyone who is not an admin."""
    with connection() as conn:
        person = require_operator_user(conn)
        return _answer(conn, person.tenant_id, may_change_site_limits(conn, request))


@router.put("/settings/site-limits", response_model=SiteLimits)
def put_site_limits(payload: SiteLimitsBody, request: Request) -> SiteLimits:
    """Replace this tenant's daily limits per site. An admin only; nothing else is touched."""
    with connection() as conn:
        person = require_operator_user(conn)
        if not may_change_site_limits(conn, request):
            raise HTTPException(status_code=403, detail=REFUSED_DETAIL)
        caps = _caps_from(payload)
        if not set_site_daily_caps(conn, person.tenant_id, caps):
            raise HTTPException(status_code=404, detail=f"tenant {person.tenant_id} does not exist")
        conn.commit()
        return _answer(conn, person.tenant_id, True)


__all__ = [
    "SITE_LIMITS_PATH",
    "SiteLimitRow",
    "SiteLimits",
    "SiteLimitsBody",
    "may_change_site_limits",
    "normalise_origin",
    "router",
]
