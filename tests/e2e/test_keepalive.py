"""The keepalive: a run that waits more than thirty seconds must survive.

The save is deliberately slow (``slow_save_ms``), so the extension's service worker
sits connected and idle for longer than Chrome's MV3 suspension window while the
runner's condition check polls. The ping exchanged every twenty seconds is what
keeps the worker alive; without it Chrome suspends the worker, the check never gets
its answer, and the record parks instead of verifying.
"""

from __future__ import annotations

import time

import pytest

from .conftest import NEW_EMAILS, simple_recipe

pytestmark = pytest.mark.e2e

SLOW_SAVE_MS = 33_000
ASSERT_TIMEOUT_MS = 40_000


def test_the_run_survives_a_thirty_five_second_idle_wait(harness) -> None:
    email = NEW_EMAILS[0]
    harness.set_switches(slow_save_ms=SLOW_SAVE_MS)
    recipe = simple_recipe(
        [
            {
                "id": "open-new-lead",
                "action": "navigate",
                "url": f"{harness.crm_url}/new-lead.html",
            },
            {
                "id": "fill-email",
                "action": "fill",
                "targets": [{"by": "testid", "value": "lead-email"}],
                "value": {"kind": "input", "column": "email"},
            },
            {
                "id": "fill-company",
                "action": "fill",
                "targets": [{"by": "testid", "value": "lead-company"}],
                "value": {"kind": "input", "column": "company"},
            },
            {
                "id": "save-lead",
                "action": "click",
                "commit": True,
                "targets": [{"by": "role", "role": "button", "name": "Save"}],
                "assert_after": {"text_visible": "Lead created", "timeout_ms": ASSERT_TIMEOUT_MS},
            },
            {"id": "done", "action": "end_record", "outcome": "verified", "reason": "saved"},
        ],
        origin=harness.crm_url,
    )
    run_id = harness.make_run(
        [{"email": email, "interest": "Data course", "company": "Keepalive Co"}], recipe=recipe
    )

    started = time.monotonic()
    harness.start_worker(max_seconds=180)
    records = harness.wait_for_terminal(run_id, timeout=180)
    elapsed = time.monotonic() - started

    assert elapsed > 30, "the case is only meaningful if the extension really idled that long"
    assert records[0]["state"] == "verified", "the worker must survive the idle wait"
    assert harness.fixture_state()["leads"].get(email, 0) == 1
