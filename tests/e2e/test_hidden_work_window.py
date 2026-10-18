"""PL-040: a covered work window comes back to the front instead of failing rows.

The owner's run on 2026-10-17 failed three rows ("19 element(s)", "no animation frame arrived")
because the work tab was behind another tab: Chrome stops drawing a hidden page. Here the work tab
is covered by a new tab in its own window, once before the run and again mid-run; every row must
still save, and none may fail or park.

Limit: Playwright keeps every page it controls drawing, so this harness cannot reproduce the hidden
page itself (the test passes with the check switched off, headless or headed). What it proves is
that bringing the work tab forward never breaks a run. The hidden case is covered by the unit tests
in `apps/extension/tests/window-hidden.test.ts` and was checked by hand in a person's Chrome.
"""

from __future__ import annotations

import time
from collections import Counter

import pytest

from .conftest import NEW_EMAILS

pytestmark = pytest.mark.e2e

# Open a tab in front of the work tab, in the work tab's own window, so the work page is hidden.
_COVER_WORK_TAB = """async () => {
  const stored = (await chrome.storage.session.get('processlens:command-session'))
    ['processlens:command-session'];
  if (!stored || stored.boundTabId < 0) return false;
  const tab = await chrome.tabs.get(stored.boundTabId);
  await chrome.tabs.create({ windowId: tab.windowId, url: 'about:blank', active: true });
  const work = await chrome.tabs.get(stored.boundTabId);
  return work.active === false;
}"""


def test_a_covered_work_tab_is_brought_back_and_every_row_saves(harness) -> None:
    rows = [
        {"email": email, "interest": "Data course", "company": f"Hidden {index}"}
        for index, email in enumerate(NEW_EMAILS)
    ]
    run_id = harness.make_run(rows)
    assert harness.popup.evaluate(_COVER_WORK_TAB) is True, "the work tab was not covered"
    harness.start_worker(max_seconds=150)

    # Cover it again once the first row is in, so a row in the middle of the run meets it too.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        records = harness.api_get(f"/api/v1/runs/{run_id}/records")
        if any(record["state"] == "verified" for record in records):
            break
        time.sleep(0.5)
    assert harness.popup.evaluate(_COVER_WORK_TAB) is True, "the work tab was not covered again"

    records = harness.wait_for_terminal(run_id, timeout=150)
    counts = Counter(record["state"] for record in records)
    detail = "; ".join(
        f"{record.get('record_key')}={record['state']}:{record.get('reason')}" for record in records
    )
    assert counts["verified"] == 3, detail
    assert counts["failed"] == 0 and counts["parked"] == 0, detail
    leads = harness.fixture_state()["leads"]
    for email in NEW_EMAILS:
        assert leads.get(email, 0) == 1, f"{email!r} exists {leads.get(email, 0)} times"
