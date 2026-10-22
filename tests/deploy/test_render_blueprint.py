"""The Render Blueprint (`render.yaml`) stays in step with what the deploy commands require."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services import deploy

yaml = pytest.importorskip("yaml")

BLUEPRINT = Path(__file__).resolve().parents[2] / "render.yaml"


def _blueprint() -> dict[str, Any]:
    return yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))


def _service(kind: str) -> dict[str, Any]:
    (found,) = [service for service in _blueprint()["services"] if service["type"] == kind]
    return found


def _keys(service: dict[str, Any]) -> set[str]:
    keys = {entry["key"] for entry in service["envVars"] if "key" in entry}
    groups = {entry["fromGroup"] for entry in service["envVars"] if "fromGroup" in entry}
    for group in _blueprint().get("envVarGroups", []):
        if group["name"] in groups:
            keys |= {entry["key"] for entry in group["envVars"]}
    return keys


def test_the_api_gets_every_setting_the_api_command_requires() -> None:
    api = _service("web")
    assert "dockerCommand" not in api, "the api runs the image's own CMD (services.deploy api)"
    assert api["healthCheckPath"] == "/health"
    assert {
        deploy.DATABASE_URL_KEY,
        deploy.RUNNER_SECRET_KEY,
        deploy.EXTENSION_ID_KEY,
    } <= _keys(api)


def test_the_supervisor_gets_every_setting_the_supervisor_command_requires() -> None:
    supervisor = _service("worker")
    assert supervisor["dockerCommand"] == "python -m services.deploy supervisor"
    assert {
        deploy.DATABASE_URL_KEY,
        deploy.API_URL_KEY,
        deploy.RUNNER_SECRET_KEY,
    } <= _keys(supervisor)


def test_both_services_share_one_runner_secret_and_neither_is_on_the_free_plan() -> None:
    api, supervisor = _service("web"), _service("worker")
    shared = {entry.get("fromGroup") for entry in api["envVars"]} & {
        entry.get("fromGroup") for entry in supervisor["envVars"]
    }
    assert shared - {None}, "the runner secret must come from one group both services read"
    # The free web tier sleeps, which drops the extension's WebSocket to the hub.
    assert api["plan"] != "free" and supervisor["plan"] != "free"


def test_the_api_has_a_generated_access_code_because_hosted_it_answers_nobody_without_one() -> None:
    """PL-086: Render serves the api publicly, so the team access code must exist from day one."""
    (entry,) = [e for e in _service("web")["envVars"] if e.get("key") == deploy.ACCESS_CODE_KEY]
    assert entry.get("generateValue") is True
