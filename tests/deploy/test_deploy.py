"""PL-051: the deploy start commands, configured only by the environment.

`services.deploy api` and `services.deploy supervisor` are the two long-running
start commands Railway runs. These tests run neither for real: uvicorn, the
migrations, the operator row and the supervisor's pollers are patched out, and
what is asserted is the contract a deploy depends on -- settings refused by
name when missing, the api command's order (migrations, operator, uvicorn on
0.0.0.0:$PORT), and a supervisor that decides with the demo's own tested
decision function and never binds a port.
"""

from __future__ import annotations

import socket
import uuid
from typing import Any

import pytest

from services import deploy


@pytest.fixture()
def deploy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every required setting present, so each test unsets exactly what it studies."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://deploy-test.invalid/db")
    monkeypatch.setenv("PROCESSLENS_RUNNER_SECRET", "<generate one>")
    monkeypatch.setenv("PROCESSLENS_EXTENSION_ID", "test-extension-id")
    monkeypatch.setenv("PROCESSLENS_API_URL", "https://api.example.test")


def test_missing_database_url_exits_1_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], deploy_env: None
) -> None:
    """One plain line, exit 1, and no database work at all."""
    touched: list[str] = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(deploy, "apply_migrations", lambda dsn: touched.append("migrations"))
    monkeypatch.setattr(deploy.uvicorn, "run", lambda *args, **kwargs: touched.append("uvicorn"))

    assert deploy.main(["api"]) == 1
    assert touched == []
    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(printed) == 1
    assert "DATABASE_URL" in printed[0]


def test_missing_extension_id_exits_1_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], deploy_env: None
) -> None:
    """The hub accepts no extension origin when the pinned id is absent, so it is required."""
    monkeypatch.delenv("PROCESSLENS_EXTENSION_ID", raising=False)
    monkeypatch.setattr(deploy, "apply_migrations", lambda dsn: None)

    assert deploy.main(["api"]) == 1
    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(printed) == 1
    assert "PROCESSLENS_EXTENSION_ID" in printed[0]


def test_missing_runner_secret_is_refused_not_generated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], deploy_env: None
) -> None:
    """Deploy mode never invents and never prints a secret: it names the variable and stops."""
    monkeypatch.delenv("PROCESSLENS_RUNNER_SECRET", raising=False)
    monkeypatch.setattr(deploy, "apply_migrations", lambda dsn: None)

    assert deploy.main(["api"]) == 1
    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(printed) == 1
    assert "PROCESSLENS_RUNNER_SECRET" in printed[0]


def test_a_hosted_api_without_an_access_code_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], deploy_env: None
) -> None:
    """PL-086: hosted, the access code is the only door, so a missing one stops start-up."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://processlens-api.onrender.com")
    monkeypatch.delenv("PROCESSLENS_ACCESS_CODE", raising=False)
    monkeypatch.setattr(deploy, "apply_migrations", lambda dsn: None)

    assert deploy.main(["api"]) == 1
    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(printed) == 1
    assert "PROCESSLENS_ACCESS_CODE" in printed[0]


def test_the_supervisor_presents_the_runner_secret_when_it_reads_the_session(
    monkeypatch: pytest.MonkeyPatch, deploy_env: None
) -> None:
    """PL-086: a hosted API answers the supervisor only with a credential."""
    seen: list[Any] = []

    class Answer:
        def __enter__(self) -> Answer:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"attached": true, "session_id": "abc"}'

    def urlopen(request: Any, timeout: float) -> Answer:
        seen.append(request)
        return Answer()

    monkeypatch.setattr(deploy.urllib.request, "urlopen", urlopen)

    assert deploy.attached_session("https://api.example.test/") == "abc"
    assert seen[0].full_url == "https://api.example.test/api/v1/executor/session"
    assert seen[0].get_header("Authorization") == "Bearer <generate one>"


def test_api_command_migrates_then_ensures_the_operator_then_serves(
    monkeypatch: pytest.MonkeyPatch, deploy_env: None
) -> None:
    """The order a Railway container depends on: schema, operator, uvicorn on 0.0.0.0:$PORT."""
    calls: list[Any] = []
    monkeypatch.setattr(deploy, "apply_migrations", lambda dsn: calls.append("migrations"))
    monkeypatch.setattr(
        deploy,
        "ensure_operator_row",
        lambda dsn, stored=None: calls.append("operator") or uuid.uuid4(),
    )
    monkeypatch.setattr(
        deploy.uvicorn,
        "run",
        lambda app, host=None, port=None, **kwargs: calls.append(("uvicorn", host, port)),
    )
    monkeypatch.setenv("PORT", "8080")

    assert deploy.main(["api"]) == 0
    assert calls == ["migrations", "operator", ("uvicorn", "0.0.0.0", 8080)]


def test_api_command_defaults_the_port_without_localhost_defaults(
    monkeypatch: pytest.MonkeyPatch, deploy_env: None
) -> None:
    """No PORT in the environment: still 0.0.0.0, on the conventional 8000."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(deploy, "apply_migrations", lambda dsn: None)
    monkeypatch.setattr(deploy, "ensure_operator_row", lambda dsn, stored=None: uuid.uuid4())
    monkeypatch.setattr(
        deploy.uvicorn,
        "run",
        lambda app, host=None, port=None, **kwargs: seen.update(host=host, port=port),
    )
    monkeypatch.delenv("PORT", raising=False)

    assert deploy.main(["api"]) == 0
    assert seen == {"host": "0.0.0.0", "port": 8000}


def test_supervisor_requires_the_api_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], deploy_env: None
) -> None:
    """The supervisor talks to the API over HTTP; without its address it cannot start."""
    monkeypatch.delenv("PROCESSLENS_API_URL", raising=False)

    assert deploy.main(["supervisor"]) == 1
    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(printed) == 1
    assert "PROCESSLENS_API_URL" in printed[0]


def test_supervisor_decides_with_the_demo_decision_and_never_binds(
    monkeypatch: pytest.MonkeyPatch, deploy_env: None
) -> None:
    """Two ticks: the decision is the demo's own function, and no port is ever bound."""
    real_decision = deploy.worker_decision
    decisions: list[tuple[list[Any], str | None, bool]] = []

    def spy(runs: Any, session: str | None, *, worker_alive: bool) -> str:
        decisions.append((list(runs), session, worker_alive))
        return real_decision(runs, session, worker_alive=worker_alive)

    monkeypatch.setattr(deploy, "worker_decision", spy)
    monkeypatch.setattr(deploy, "open_runs", lambda dsn: [])
    monkeypatch.setattr(deploy, "attached_session", lambda api_url: None)

    binds: list[Any] = []
    monkeypatch.setattr(socket.socket, "bind", lambda self, address: binds.append(address))

    ticks = {"n": 0}

    def fake_sleep(seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(deploy.time, "sleep", fake_sleep)

    assert deploy.main(["supervisor"]) == 0
    assert len(decisions) == 2
    assert all(session is None for _runs, session, _alive in decisions)
    assert binds == []


def test_demo_imports_the_moved_code() -> None:
    """scripts.demo keeps working by importing the moved code, not by keeping a copy."""
    import scripts.demo as demo

    assert demo.worker_decision is deploy.worker_decision
    assert demo.status_lines is deploy.status_lines
    assert demo.State is deploy.State
    assert demo.START is deploy.START
    assert demo.WAIT is deploy.WAIT


def test_deploy_mode_never_invents_a_secret() -> None:
    """The deploy module holds no secret generator: nothing can invent one by accident."""
    assert "secrets" not in deploy.__dict__
    assert not hasattr(deploy, "token_urlsafe")
