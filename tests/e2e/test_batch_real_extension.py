"""The primary end-to-end case: a five-record batch on the fixture site.

One lead already exists (skipped), three are brand new (verified), and one has no
company, which is the record the fixture recipe's own ``fill-company`` branch parks.
Every assertion reads the fixture's test API or the ledger's own endpoints; no
screenshot is ever consulted.
"""

from __future__ import annotations

from collections import Counter

import pytest

from .conftest import EXISTING_EMAIL, NEW_EMAILS, PARKED_EMAIL

pytestmark = pytest.mark.e2e


def batch_rows() -> list[dict[str, str]]:
    """The five rows: one existing, three new, one without a company."""
    return [
        {"email": EXISTING_EMAIL, "interest": "Data course", "company": "Existing Co"},
        {"email": NEW_EMAILS[0], "interest": "Data course", "company": "New One"},
        {"email": NEW_EMAILS[1], "interest": "Data course", "company": "New Two"},
        {"email": NEW_EMAILS[2], "interest": "Data course", "company": "New Three"},
        {"email": PARKED_EMAIL, "interest": "Data course", "company": ""},
    ]


def test_a_five_record_batch_ends_three_verified_one_skipped_one_parked(harness) -> None:
    harness.seed_lead(EXISTING_EMAIL)
    run_id = harness.make_run(batch_rows())
    harness.start_worker(max_seconds=150)
    records = harness.wait_for_terminal(run_id, timeout=150)

    counts = Counter(record["state"] for record in records)
    # The reasons are part of the failure message: a record that parked for the wrong reason is a
    # different bug from one that never ran, and the state alone cannot tell them apart.
    detail = "; ".join(
        f"{record.get('record_key')}={record['state']}:{record.get('reason')}" for record in records
    )
    assert len(records) == 5, detail
    assert counts["verified"] == 3, detail
    assert counts["skipped"] == 1, detail
    assert counts["parked"] == 1, detail
    assert counts["failed"] == 0, detail


def test_each_email_exists_exactly_once_in_the_fixture(harness) -> None:
    harness.seed_lead(EXISTING_EMAIL)
    run_id = harness.make_run(batch_rows())
    harness.start_worker(max_seconds=150)
    harness.wait_for_terminal(run_id, timeout=150)

    leads = harness.fixture_state()["leads"]
    for email in (EXISTING_EMAIL, *NEW_EMAILS):
        assert leads.get(email, 0) == 1, f"{email!r} exists {leads.get(email, 0)} times"
    assert leads.get(PARKED_EMAIL, 0) == 0, "the parked record must not have saved a lead"


def test_a_stale_toast_does_not_verify_the_record(harness) -> None:
    # A "Lead created" toast left over from a previous save, byte-identical to a real one.
    harness.set_switches(stale_toast=True)
    email = NEW_EMAILS[0]
    row = {"email": email, "interest": "Data course", "company": "Stale Co"}
    run_id = harness.make_run([row])
    harness.start_worker(max_seconds=120)
    records = harness.wait_for_terminal(run_id, timeout=120)

    assert len(records) == 1
    assert records[0]["state"] != "verified", "a stale confirmation must not verify the save"
    assert harness.fixture_state()["leads"].get(email, 0) == 0
