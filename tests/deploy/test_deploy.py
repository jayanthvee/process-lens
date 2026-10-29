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
import urllib.error
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


def _open(status: str, browser: str | None, *, live: bool = False) -> Any:
    return deploy.OpenRunRow(
        run_id=uuid.uuid4(), status=status, worker_live=live, browser_id=browser
    )


ANNA = deploy.AttachedSession("session-anna", "browser-anna")
BEN = deploy.AttachedSession("session-ben", "browser-ben")


def test_a_run_goes_only_to_the_chrome_that_started_it() -> None:
    """PL-087: Anna's run starts a worker for Anna's Chrome, never for Ben's."""
    runs = [_open("queued", "browser-anna")]

    assert deploy.sessions_to_start(runs, [BEN, ANNA], busy=set()) == [ANNA]


def test_each_person_gets_a_worker_at_once() -> None:
    """Two people, two runs: two workers, side by side, instead of one waiting for the other."""
    runs = [_open("queued", "browser-anna"), _open("queued", "browser-ben")]

    assert deploy.sessions_to_start(runs, [ANNA, BEN], busy=set()) == [ANNA, BEN]
    assert deploy.sessions_to_start(runs, [ANNA, BEN], busy={"session-anna"}) == [BEN]


def test_an_unbound_run_may_go_to_any_chrome_and_a_bound_one_waits_for_its_own() -> None:
    assert deploy.sessions_to_start([_open("queued", None)], [BEN], busy=set()) == [BEN]
    assert deploy.sessions_to_start([_open("queued", "browser-anna")], [BEN], busy=set()) == []


def test_a_run_a_live_worker_holds_starts_nothing_but_a_lapsed_one_does() -> None:
    held = [_open("running", "browser-anna", live=True)]
    lapsed = [_open("running", "browser-anna", live=False)]

    assert deploy.sessions_to_start(held, [ANNA], busy=set()) == []
    assert deploy.sessions_to_start(lapsed, [ANNA], busy=set()) == [ANNA]


def test_supervisor_starts_one_worker_per_chrome_and_never_binds(
    monkeypatch: pytest.MonkeyPatch, deploy_env: None
) -> None:
    """Two ticks with two attached Chromes and a run each: one worker each, started once."""
    runs = [_open("queued", "browser-anna"), _open("queued", "browser-ben")]
    monkeypatch.setattr(deploy, "open_runs", lambda dsn: runs)
    finished: list[str] = []
    monkeypatch.setattr(deploy, "finish_unheld_cancels", lambda dsn: finished.append(dsn) or [])
    monkeypatch.setattr(deploy, "attached_sessions", lambda api_url, secret=None: [ANNA, BEN])
    started: list[tuple[str, str | None]] = []

    class Alive:
        returncode = None

        def poll(self) -> None:
            return None

    def fake_start(api_url: str, secret: str, session_id: str, *, number: int, browser_id=None):
        started.append((session_id, browser_id))
        return Alive()

    monkeypatch.setattr(deploy, "start_worker", fake_start)
    monkeypatch.setattr(deploy, "stop_process", lambda process: None)
    binds: list[Any] = []
    monkeypatch.setattr(socket.socket, "bind", lambda self, address: binds.append(address))
    ticks = {"n": 0}

    def fake_sleep(seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(deploy.time, "sleep", fake_sleep)

    assert deploy.main(["supervisor"]) == 0
    assert started == [("session-anna", "browser-anna"), ("session-ben", "browser-ben")]
    assert len(finished) == 2, "PL-089: every tick finishes the cancels nobody holds"
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


def test_a_chrome_list_the_api_could_not_give_is_not_an_empty_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PL-092: a failed read answers None, so the supervisor cannot take it for "nobody is here"."""

    def unreachable(*_args: Any, **_kwargs: Any) -> Any:
        raise urllib.error.URLError("the API is restarting")

    monkeypatch.setattr(deploy.urllib.request, "urlopen", unreachable)

    assert deploy.attached_sessions("https://api.example.test", "secret") is None


def test_a_failed_read_of_the_connected_chromes_stops_no_worker(
    monkeypatch: pytest.MonkeyPatch, deploy_env: None
) -> None:
    """PL-092: one blip in the API (a restart, a timeout) must not stop every running worker.

    Tick 1 sees Anna and starts her worker; tick 2 cannot read the list; tick 3 sees Anna again.
    Her worker is never stopped and never started twice.
    """
    runs = [_open("queued", "browser-anna")]
    monkeypatch.setattr(deploy, "open_runs", lambda dsn: runs)
    monkeypatch.setattr(deploy, "finish_unheld_cancels", lambda dsn: [])
    reads = iter([[ANNA], None, [ANNA]])
    monkeypatch.setattr(deploy, "attached_sessions", lambda api_url, secret=None: next(reads))
    started: list[str] = []
    stopped: list[Any] = []

    class Alive:
        returncode = None

        def poll(self) -> None:
            return None

    def fake_start(api_url: str, secret: str, session_id: str, *, number: int, browser_id=None):
        started.append(session_id)
        return Alive()

    monkeypatch.setattr(deploy, "start_worker", fake_start)
    monkeypatch.setattr(deploy, "stop_process", stopped.append)
    ticks = {"n": 0}

    def fake_sleep(seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(deploy.time, "sleep", fake_sleep)

    assert deploy.main(["supervisor"]) == 0
    assert started == ["session-anna"]
    # The only stop is the shutdown's own, after the last tick.
    assert len(stopped) == 1


def test_a_stopped_runner_hands_its_runs_back_at_once(
    monkeypatch: pytest.MonkeyPatch, deploy_env: None
) -> None:
    """PL-130: when a runner's process ends, or its Chrome goes, its runs go to the next runner now.

    Tick 1 starts Anna's and Ben's runners. Before tick 2, Anna's runner has exited by itself and
    Ben's Chrome is gone, so his runner is stopped. Both hand back their runs by their own ids.
    """
    runs = [_open("queued", "browser-anna"), _open("queued", "browser-ben")]
    monkeypatch.setattr(deploy, "open_runs", lambda dsn: runs)
    monkeypatch.setattr(deploy, "finish_unheld_cancels", lambda dsn: [])
    reads = iter([[ANNA, BEN], [ANNA], [ANNA]])
    monkeypatch.setattr(deploy, "attached_sessions", lambda api_url, secret=None: next(reads))
    released: list[str] = []
    monkeypatch.setattr(
        deploy, "release_worker_runs", lambda dsn, worker_id: released.append(worker_id) or []
    )
    processes: dict[str, Any] = {}

    class Process:
        def __init__(self) -> None:
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

    def fake_start(api_url: str, secret: str, session_id: str, *, number: int, browser_id=None):
        processes[session_id] = Process()
        return processes[session_id]

    monkeypatch.setattr(deploy, "start_worker", fake_start)
    monkeypatch.setattr(deploy, "stop_process", lambda process: None)
    ticks = {"n": 0}

    def fake_sleep(seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] == 1:
            processes["session-anna"].returncode = 1
        if ticks["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(deploy.time, "sleep", fake_sleep)

    assert deploy.main(["supervisor"]) == 0
    assert released == ["deploy-worker-1", "deploy-worker-2"]
