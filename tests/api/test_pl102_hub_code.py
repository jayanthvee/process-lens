"""PL-102: a code, or an ask to read one from email, reaches the run's own Chrome and nobody else's.

With one team access code, many people's Chromes are attached to the same hub. Before this, a code
went to whichever extension attached last -- someone else's Chrome, on someone else's page.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from services.api import hub


def _attach(session_id: str, browser_id: str) -> hub.Peer:
    peer = hub.Peer(role=hub.ROLE_EXTENSION, websocket=None)  # type: ignore[arg-type]
    hub._sessions[session_id] = hub.Session(
        session_id=session_id,
        session_token=f"tok-{session_id}",
        resume_token=None,
        tab_url="",
        tab_title="",
        expires_at=time.time() + 600,
        browser_id=browser_id,
        extension=peer,
    )
    return peer


async def _deliver(**kwargs: Any) -> tuple[dict[str, Any], hub.Peer, hub.Peer]:
    hub.reset_sessions()
    mine = _attach("s-mine", "chrome-of-lakshmi")
    theirs = _attach("s-theirs", "chrome-of-someone-else")

    async def answer() -> None:
        while mine.queue.empty():
            await asyncio.sleep(0.01)
        frame = json.loads(mine.queue.get_nowait())
        hub._resolve_enter_code(
            json.dumps(
                {
                    "protocol": 1,
                    "type": "result",
                    "command_id": frame["command_id"],
                    "status": "ok",
                    "observed": {"entered": True, "reason": "submitted", "cleared": True},
                }
            )
        )
        mine.queue.put_nowait(json.dumps(frame))

    responder = asyncio.create_task(answer())
    outcome = await hub.send_enter_code(run_id="r1", browser_id="chrome-of-lakshmi", **kwargs)
    await responder
    return outcome, mine, theirs


def test_a_code_goes_to_the_runs_own_chrome_only() -> None:
    outcome, mine, theirs = asyncio.run(_deliver(code="246810"))

    assert outcome == {"entered": True, "reason": "submitted", "cleared": True}
    assert theirs.queue.empty(), "another person's Chrome must never receive the code"
    assert json.loads(mine.queue.get_nowait())["code"] == "246810"


def test_an_ask_to_read_the_code_from_email_carries_no_code() -> None:
    outcome, mine, _ = asyncio.run(_deliver(code=None, from_email=True, remember=True))

    frame = json.loads(mine.queue.get_nowait())
    assert outcome["entered"] is True
    assert frame["from_email"] is True
    assert frame["remember"] is True
    assert "code" not in frame


def test_no_chrome_for_this_run_is_said_plainly() -> None:
    async def deliver() -> dict[str, Any]:
        hub.reset_sessions()
        _attach("s-theirs", "chrome-of-someone-else")
        return await hub.send_enter_code("246810", run_id="r1", browser_id="chrome-of-lakshmi")

    assert asyncio.run(deliver()) == {"entered": False, "reason": "no_extension"}
