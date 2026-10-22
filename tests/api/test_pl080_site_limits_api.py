"""PL-080: the daily limit per site, read and written from the Settings page.

The limit lives in ``tenants.settings->'site_daily_caps'`` as ``{origin: rows per day}``, the shape
PL-067's runner reads. Saving replaces that one key with a single ``jsonb_set``, so every other
setting is left exactly as it was. Only an admin changes the limits: the shared admin token the
other admin routes use, or the tenant's owner (the same rule the admin panel's sign-in uses). An
origin is stored the way a recipe's ``allowed_origins`` writes it, because PL-067 counts by exact
text.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from packages.compiler.assertions import origin_of
from services.api.admin import ADMIN_TOKEN_ENV_VAR
from services.api.operator import OPERATOR_ENV_VAR

TOKEN = "admin-token-for-tests"
TOKEN_HEADERS = {"X-ProcessLens-Admin-Token": TOKEN}
LINKEDIN = "https://www.linkedin.com"
PATH = "/api/v1/settings/site-limits"


class World:
    """One tenant with a task on LinkedIn, a person, and the tenant's current settings."""

    def __init__(self, conn: psycopg.Connection, settings: dict[str, Any] | None = None) -> None:
        self.conn = conn
        self.tenant_id: uuid.UUID = self._one(
            "INSERT INTO tenants (name, settings) VALUES ('Synthetic tenant', %s) RETURNING id",
            (json.dumps(settings if settings is not None else {}),),
        )
        self.workflow_id: uuid.UUID = self._one(
            "INSERT INTO workflows (tenant_id, name, destination_key, allowed_origins) "
            "VALUES (%s, 'LinkedIn leads', 'linkedin', %s) RETURNING id",
            (self.tenant_id, [LINKEDIN]),
        )
        recipe = {
            "meta": {
                "name": "LinkedIn leads",
                "allowed_origins": [origin_of("https://www.linkedin.com/feed/")],
            },
            "steps": [],
        }
        self.recipe_version_id: uuid.UUID = self._one(
            "INSERT INTO recipe_versions (tenant_id, workflow_id, version, recipe, change_reason) "
            "VALUES (%s, %s, 1, %s, 'test') RETURNING id",
            (self.tenant_id, self.workflow_id, json.dumps(recipe)),
        )

    def _one(self, sql: str, params: tuple = ()) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def person(self, *, role: str = "owner", deactivated: bool = False) -> uuid.UUID:
        return self._one(
            "INSERT INTO users (tenant_id, email, name, role, deactivated_at) "
            "VALUES (%s, %s, 'A person', %s, CASE WHEN %s THEN now() ELSE NULL END) RETURNING id",
            (
                self.tenant_id,
                f"{uuid.uuid4().hex[:8]}@example.com",
                role,
                deactivated,
            ),
        )

    def settings(self) -> dict[str, Any]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT settings FROM tenants WHERE id = %s", (self.tenant_id,))
            return cur.fetchone()[0]


@pytest.fixture
def connected(monkeypatch: MonkeyPatch, database_url: str, client: TestClient) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv(ADMIN_TOKEN_ENV_VAR, TOKEN)
    return client


@pytest.fixture
def world(conn: psycopg.Connection) -> World:
    return World(conn)


def saved(world: World) -> dict[str, int]:
    return world.settings().get("site_daily_caps", {})


# --- who may change them ----------------------------------------------------


def test_the_run_page_reads_the_limits_without_being_an_admin(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    """Everyone sees the limits; a builder sees them read-only."""
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="builder")))
    with world.conn.cursor() as cur:
        cur.execute(
            "UPDATE tenants SET settings = %s WHERE id = %s",
            (json.dumps({"site_daily_caps": {LINKEDIN: 25}}), world.tenant_id),
        )

    response = connected.get(PATH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sites"] == [{"origin": LINKEDIN, "limit": 25}]
    assert body["can_change"] is False


def test_an_admin_token_saves_a_limit(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="builder")))

    response = connected.put(
        PATH, json={"sites": [{"origin": LINKEDIN, "limit": 25}]}, headers=TOKEN_HEADERS
    )

    assert response.status_code == 200, response.text
    assert response.json()["sites"] == [{"origin": LINKEDIN, "limit": 25}]
    assert saved(world) == {LINKEDIN: 25}


def test_the_tenant_s_owner_may_save_from_the_page(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    """The page carries no admin token; its admin is the owner, as the admin panel defines one."""
    owner = world.person(role="owner")
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(owner))

    response = connected.put(PATH, json={"sites": [{"origin": LINKEDIN, "limit": 25}]})

    assert response.status_code == 200, response.text
    assert saved(world) == {LINKEDIN: 25}

    read = connected.get(PATH).json()
    assert read["can_change"] is True, "the page can offer add, edit and remove to this person"


def test_someone_who_is_not_an_admin_cannot_save(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="builder")))

    response = connected.put(PATH, json={"sites": [{"origin": LINKEDIN, "limit": 25}]})

    assert response.status_code == 403, response.text
    assert "admin" in response.json()["detail"].lower()
    assert saved(world) == {}, "a refused change wrote nothing"


def test_a_turned_off_owner_cannot_save(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="owner", deactivated=True)))

    response = connected.put(PATH, json={"sites": [{"origin": LINKEDIN, "limit": 25}]})

    assert response.status_code == 403, response.text
    assert saved(world) == {}


def test_a_wrong_token_changes_nothing(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="builder")))

    response = connected.put(
        PATH,
        json={"sites": [{"origin": LINKEDIN, "limit": 25}]},
        headers={"X-ProcessLens-Admin-Token": "not-the-token"},
    )

    assert response.status_code == 403, response.text
    assert saved(world) == {}


# --- what is stored ---------------------------------------------------------


def test_other_settings_keys_are_left_alone(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="owner")))
    with world.conn.cursor() as cur:
        cur.execute(
            "UPDATE tenants SET settings = %s WHERE id = %s",
            (json.dumps({"retention_days": 30, "site_daily_caps": {LINKEDIN: 5}}), world.tenant_id),
        )

    response = connected.put(PATH, json={"sites": [{"origin": LINKEDIN, "limit": 25}]})

    assert response.status_code == 200, response.text
    assert world.settings() == {"retention_days": 30, "site_daily_caps": {LINKEDIN: 25}}


def test_a_row_removed_on_screen_is_removed_from_the_setting(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="owner")))
    connected.put(PATH, json={"sites": [{"origin": LINKEDIN, "limit": 25}]})

    response = connected.put(PATH, json={"sites": []})

    assert response.status_code == 200, response.text
    assert response.json()["sites"] == []
    assert saved(world) == {}


def test_a_limit_is_a_whole_number_from_1_to_10000(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="owner")))

    for limit in (0, 10_001, 3.5, "twenty", True):
        response = connected.put(PATH, json={"sites": [{"origin": LINKEDIN, "limit": limit}]})
        assert response.status_code == 422, f"{limit!r}: {response.text}"

    assert saved(world) == {}


def test_the_ends_of_the_limit_range_are_allowed(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="owner")))

    one = connected.put(PATH, json={"sites": [{"origin": LINKEDIN, "limit": 1}]})
    assert one.status_code == 200, one.text
    assert saved(world) == {LINKEDIN: 1}
    big = connected.put(PATH, json={"sites": [{"origin": LINKEDIN, "limit": 10_000}]})
    assert big.status_code == 200, big.text
    assert saved(world) == {LINKEDIN: 10_000}


@pytest.mark.parametrize(
    "origin",
    [
        "linkedin.com",
        "https://linkedin.com/in/someone",
        "https://linkedin.com/feed/",
        "ftp://linkedin.com",
        "https://",
        "https://user:pw@linkedin.com",
        "https://linkedin.com?tab=people",
        "https://linkedin.com#top",
        "  ",
    ],
)
def test_an_origin_must_be_a_site_with_no_path(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch, origin: str
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="owner")))

    response = connected.put(PATH, json={"sites": [{"origin": origin, "limit": 25}]})

    assert response.status_code == 422, f"{origin!r}: {response.text}"
    assert saved(world) == {}


def test_the_origin_is_stored_the_way_a_recipe_writes_it(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    """A recipe writes ``scheme://host[:port]``: no trailing slash, ``www.`` kept.

    The stored key must equal what ``origin_of`` writes for the recipe's own recorded URL, or
    PL-067's exact-text count would never see the cap.
    """
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="owner")))
    recipe_origin = origin_of("https://www.linkedin.com/feed/")

    response = connected.put(
        PATH, json={"sites": [{"origin": "HTTPS://WWW.LinkedIn.com/", "limit": 25}]}
    )

    assert response.status_code == 200, response.text
    assert list(saved(world)) == [recipe_origin], "the cap is keyed by the recipe's own origin text"
    assert response.json()["sites"] == [{"origin": recipe_origin, "limit": 25}]

    # A port is part of the origin, and survives. The page saves the whole list, so it is sent here.
    connected.put(
        PATH,
        json={
            "sites": [
                {"origin": recipe_origin, "limit": 25},
                {"origin": "http://localhost:5173/", "limit": 5},
            ]
        },
    )
    assert saved(world) == {recipe_origin: 25, "http://localhost:5173": 5}


def test_two_rows_that_name_the_same_site_are_refused(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="owner")))

    response = connected.put(
        PATH,
        json={
            "sites": [
                {"origin": LINKEDIN, "limit": 25},
                {"origin": f"{LINKEDIN}/", "limit": 30},
            ]
        },
    )

    assert response.status_code == 422, response.text
    assert saved(world) == {}


def test_the_answer_is_ordered_by_origin(
    connected: TestClient, world: World, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv(OPERATOR_ENV_VAR, str(world.person(role="owner")))
    with world.conn.cursor() as cur:
        cur.execute(
            "UPDATE tenants SET settings = %s WHERE id = %s",
            (
                json.dumps({"site_daily_caps": {"https://z.example": 2, "https://a.example": 1}}),
                world.tenant_id,
            ),
        )

    body = connected.get(PATH).json()

    assert [site["origin"] for site in body["sites"]] == ["https://a.example", "https://z.example"]
