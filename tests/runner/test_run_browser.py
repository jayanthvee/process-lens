"""PL-087: a run goes to the Chrome of the person who started it.

A worker for one browser claims that browser's runs and unbound ones, never another browser's; a
worker with no browser (the harness, a laptop) claims as before.
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest

from packages.ledger import claim_run, find_stale_run
from tests.runner.conftest import Seed

ANNA = "browser-anna-0001"
BEN = "browser-ben-00001"


def test_a_worker_for_another_browser_cannot_claim_the_run(
    seed: Callable[..., Seed], database_url: str
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run(browser_id=ANNA)

    with psycopg.connect(database_url, autocommit=True) as conn:
        assert claim_run(conn, "ben-worker", browser_id=BEN) is None
        claimed = claim_run(conn, "anna-worker", browser_id=ANNA)

    assert claimed is not None and claimed.id == run_id


def test_an_unbound_run_goes_to_whichever_browser_asks(
    seed: Callable[..., Seed], database_url: str
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    with psycopg.connect(database_url, autocommit=True) as conn:
        claimed = claim_run(conn, "ben-worker", browser_id=BEN)

    assert claimed is not None and claimed.id == run_id


def test_a_worker_with_no_browser_claims_as_before(
    seed: Callable[..., Seed], database_url: str
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run(browser_id=ANNA)

    with psycopg.connect(database_url, autocommit=True) as conn:
        claimed = claim_run(conn, "harness-worker")

    assert claimed is not None and claimed.id == run_id


def test_a_stale_run_is_taken_over_only_by_its_own_browser(
    seed: Callable[..., Seed], database_url: str
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run(browser_id=ANNA)

    with psycopg.connect(database_url, autocommit=True) as conn:
        assert claim_run(conn, "anna-worker", browser_id=ANNA) is not None
        conn.execute(
            "UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (run_id,)
        )
        assert find_stale_run(conn, heartbeat_timeout="10 seconds", browser_id=BEN) is None
        stale = find_stale_run(conn, heartbeat_timeout="10 seconds", browser_id=ANNA)

    assert stale is not None and stale.id == run_id


def test_a_browser_id_of_the_wrong_shape_is_refused_by_the_database(
    seed: Callable[..., Seed],
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")

    with pytest.raises(psycopg.errors.CheckViolation):
        base.create_run(browser_id="not ok; drop")
