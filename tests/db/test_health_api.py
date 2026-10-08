"""The API health endpoint, which reports whether the database is reachable.

The endpoint is tiny, but its answer is a database fact, so it is tested here
with the rest of the database suite.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from services.api.main import app

client = TestClient(app)


def test_health_reports_a_reachable_database(monkeypatch: MonkeyPatch, database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True}


def test_health_reports_an_unreachable_database(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://processlens@127.0.0.1:1/processlens")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": False}


def test_health_reports_a_missing_database_url(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": False}
