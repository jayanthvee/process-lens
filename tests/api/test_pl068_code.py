"""PL-068: the code a person types on the run page.

The route accepts a code only while the run is paused waiting for one, only from a person in that
run's tenant, only in the shape the ticket allows -- and it never writes the code anywhere.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from services.api import runs as runs_module
from tests.db.seed import Ledger, Run

CODE = "246810"
OTHER_CODE = "998877"


@pytest.fixture(autouse=True)
def operator(monkeypatch: pytest.MonkeyPatch, ledger: Ledger) -> uuid.UUID:
    """The person using the page: an owner in the test tenant, as the API is configured."""
    user_id = ledger.add_user(role="owner")
    monkeypatch.setenv("PROCESSLENS_OPERATOR_ID", str(user_id))
    return user_id


def paused_run(conn: psycopg.Connection, ledger: Ledger) -> Run:
    """A finished-looking run that is paused waiting for a verification code."""
    run = ledger.new_run()
    conn.execute(
        "UPDATE runs SET status = 'paused', pause_reason = 'code_required' WHERE id = %s",
        (run.id,),
    )
    return run


def test_a_code_is_refused_unless_the_run_is_waiting_for_one(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    response = connected.post(f"/api/v1/runs/{run.id}/code", json={"code": CODE})
    assert response.status_code == 409, response.text


def test_a_run_paused_for_another_reason_is_refused(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()
    conn.execute(
        "UPDATE runs SET status = 'paused', pause_reason = 'login_required' WHERE id = %s",
        (run.id,),
    )
    response = connected.post(f"/api/v1/runs/{run.id}/code", json={"code": CODE})
    assert response.status_code == 409, response.text


def test_another_tenants_run_is_not_found(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (name) VALUES ('Another PL-068 tenant') RETURNING id")
        other_tenant = cur.fetchone()[0]
    run = paused_run(conn, ledger)
    conn.execute("UPDATE runs SET tenant_id = %s WHERE id = %s", (other_tenant, run.id))
    response = connected.post(f"/api/v1/runs/{run.id}/code", json={"code": CODE})
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("bad", ["", "12", "1234567890123", "12 34 56", "abc-def"])
def test_a_malformed_code_is_refused_with_422(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger, bad: str
) -> None:
    run = paused_run(conn, ledger)
    response = connected.post(f"/api/v1/runs/{run.id}/code", json={"code": bad})
    assert response.status_code == 422, response.text


def test_the_code_is_written_nowhere(
    connected: TestClient,
    conn: psycopg.Connection,
    ledger: Ledger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The code is delivered in memory and appears in no table, text or jsonb column."""
    run = paused_run(conn, ledger)

    delivered: list[str] = []

    async def accept(code: str | None, *, run_id: uuid.UUID, **_: object) -> dict[str, object]:
        delivered.append(code)
        return {"entered": True, "reason": "submitted"}

    monkeypatch.setattr(runs_module.hub, "send_enter_code", accept)

    response = connected.post(f"/api/v1/runs/{run.id}/code", json={"code": CODE})
    assert response.status_code == 200, response.text
    assert response.json()["entered"] is True
    assert CODE not in response.text
    assert delivered == [CODE]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND data_type IN ('text', 'character varying', 'jsonb', 'json')"
        )
        for table, column in cur.fetchall():
            cur.execute(
                f'SELECT count(*) FROM "{table}" WHERE "{column}"::text LIKE %s',
                (f"%{CODE}%",),
            )
            assert cur.fetchone()[0] == 0, f"the code reached {table}.{column}"


# -- PL-102: the run resumes by itself, and the code can be read from email ----------------------


def _run_status(conn: psycopg.Connection, run: Run) -> tuple[str, str | None]:
    row = conn.execute(
        "SELECT status::text, pause_reason FROM runs WHERE id = %s", (run.id,)
    ).fetchone()
    return row[0], row[1]


def test_a_code_that_takes_the_page_past_the_prompt_resumes_the_run(
    connected: TestClient,
    conn: psycopg.Connection,
    ledger: Ledger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = paused_run(conn, ledger)

    async def accept(code: str | None, **_: object) -> dict[str, object]:
        return {"entered": True, "reason": "submitted", "cleared": True}

    monkeypatch.setattr(runs_module.hub, "send_enter_code", accept)

    response = connected.post(f"/api/v1/runs/{run.id}/code", json={"code": CODE})

    assert response.status_code == 200, response.text
    assert response.json()["resumed"] is True
    assert _run_status(conn, run)[0] != "paused"


def test_a_wrong_code_leaves_the_run_paused(
    connected: TestClient,
    conn: psycopg.Connection,
    ledger: Ledger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = paused_run(conn, ledger)

    async def accept(code: str | None, **_: object) -> dict[str, object]:
        return {"entered": True, "reason": "submitted", "cleared": False}

    monkeypatch.setattr(runs_module.hub, "send_enter_code", accept)

    response = connected.post(f"/api/v1/runs/{run.id}/code", json={"code": CODE})

    assert response.json()["resumed"] is False
    assert _run_status(conn, run) == ("paused", "code_required")


def test_reading_the_code_from_email_asks_the_runs_own_chrome_with_no_code(
    connected: TestClient,
    conn: psycopg.Connection,
    ledger: Ledger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = paused_run(conn, ledger)
    conn.execute("UPDATE runs SET browser_id = 'chrome-of-lakshmi' WHERE id = %s", (run.id,))
    asked: list[dict[str, object]] = []

    async def accept(code: str | None, **kwargs: object) -> dict[str, object]:
        asked.append({"code": code, **kwargs})
        return {"entered": True, "reason": "submitted", "cleared": True}

    monkeypatch.setattr(runs_module.hub, "send_enter_code", accept)

    response = connected.post(f"/api/v1/runs/{run.id}/code-from-email", json={"remember": True})

    assert response.status_code == 200, response.text
    assert response.json() == {"entered": True, "reason": "submitted", "resumed": True}
    assert asked[0]["code"] is None
    assert asked[0]["from_email"] is True
    assert asked[0]["remember"] is True
    assert asked[0]["browser_id"] == "chrome-of-lakshmi"


def test_reading_the_code_from_email_says_why_it_could_not(
    connected: TestClient,
    conn: psycopg.Connection,
    ledger: Ledger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = paused_run(conn, ledger)

    async def accept(code: str | None, **_: object) -> dict[str, object]:
        return {"entered": False, "reason": "ambiguous"}

    monkeypatch.setattr(runs_module.hub, "send_enter_code", accept)

    response = connected.post(f"/api/v1/runs/{run.id}/code-from-email", json={})

    assert response.json() == {"entered": False, "reason": "ambiguous", "resumed": False}
    assert _run_status(conn, run) == ("paused", "code_required")


def test_reading_the_code_from_email_is_refused_unless_the_run_waits_for_one(
    connected: TestClient, conn: psycopg.Connection, ledger: Ledger
) -> None:
    run = ledger.new_run()

    response = connected.post(f"/api/v1/runs/{run.id}/code-from-email", json={})

    assert response.status_code == 409, response.text
