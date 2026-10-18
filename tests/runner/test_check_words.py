"""PL-047: a row parked after its save says what ProcessLens looked for.

The runner knows the commit step's ``assert_after`` and how long it waited. When that check fails
and the record parks, the reason must name the check in plain words instead of the reconciler's
generic sentence. Every other parking path keeps today's reason.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg

from packages.ledger import get_record
from services.runner.check_words import describe_check, parking_reason
from services.runner.reconcile import PARKING_REASON, ParkingReconciler
from services.runner.testing import ScriptedRun
from services.runner.worker import Worker
from tests.runner.conftest import DEFAULT_STEPS, DESTINATION_KEY, Seed, recipe


def test_text_visible_names_the_text_and_the_wait() -> None:
    assert (
        describe_check({"text_visible": "Lead created", "timeout_ms": 8000}, {}, {})
        == 'the page never showed "Lead created" within 8 seconds'
    )


def test_text_absent_names_the_text_and_the_wait() -> None:
    assert (
        describe_check({"text_absent": "Saving...", "timeout_ms": 8000}, {}, {})
        == 'the page never stopped showing "Saving..." within 8 seconds'
    )


def test_text_count_increases_names_the_text_and_the_wait() -> None:
    assert (
        describe_check({"text_count_increases": "Ana Ruiz", "timeout_ms": 8000}, {}, {})
        == '"Ana Ruiz" did not appear one more time within 8 seconds'
    )


def test_url_matches_names_the_pattern_and_the_wait() -> None:
    assert (
        describe_check({"url_matches": "/leads/", "timeout_ms": 8000}, {}, {})
        == 'the page never went to a URL matching "/leads/" within 8 seconds'
    )


def test_element_present_names_the_element_and_the_wait() -> None:
    selector = {"targets": [{"by": "role", "role": "button", "name": "Save"}]}
    assert (
        describe_check({"element_present": selector, "timeout_ms": 8000}, {}, {})
        == 'the "Save" element never appeared within 8 seconds'
    )


def test_element_absent_names_the_element_and_the_wait() -> None:
    selector = {"targets": [{"by": "label", "text": "Saving..."}]}
    assert (
        describe_check({"element_absent": selector, "timeout_ms": 8000}, {}, {})
        == 'the "Saving..." element never disappeared within 8 seconds'
    )


def test_value_equals_names_the_value_and_the_wait() -> None:
    condition = {
        "value_equals": {
            "selector": {"targets": [{"by": "label", "text": "Email"}]},
            "value": {"kind": "constant", "value": "Ana Ruiz"},
        },
        "timeout_ms": 8000,
    }
    assert (
        describe_check(condition, {}, {})
        == 'the field never came to equal "Ana Ruiz" within 8 seconds'
    )


def test_a_templated_check_is_rendered_with_the_record() -> None:
    assert (
        describe_check(
            {"text_count_increases": "{{row.contact}}", "timeout_ms": 8000},
            {"contact": "Ana Ruiz"},
            {},
        )
        == '"Ana Ruiz" did not appear one more time within 8 seconds'
    )


def test_a_long_value_is_truncated_to_the_160_character_cap() -> None:
    sentence = describe_check({"text_visible": "x" * 400, "timeout_ms": 8000}, {}, {})

    assert len(sentence) == 160
    assert sentence.startswith('the page never showed "')
    assert sentence.endswith('…" within 8 seconds')


def test_an_unknown_kind_says_only_that_the_check_did_not_pass() -> None:
    assert describe_check({"text_shouts": "hello"}, {}, {}) == "the save's check did not pass"


def test_a_run_that_cannot_name_the_destination_says_the_destination() -> None:
    assert parking_reason("the save's check did not pass", None) == (
        "the save's check did not pass; check the destination: "
        "if the row is there, Dismiss it, otherwise Try again"
    )


def _walk(conn: psycopg.Connection, record_id: uuid.UUID, states: list[str]) -> None:
    for state in states:
        conn.execute("UPDATE run_records SET state = %s WHERE id = %s", (state, record_id))


def test_a_failed_check_parks_with_what_the_runner_looked_for(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """The commit's own check failed and the destination had several matches."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()

    scripted = ScriptedRun.build(ambiguous=["row-0001"])
    scripted.executor.conditions["save"] = False
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert record.reason == (
        'the page never showed "Lead created" within 8 seconds; '
        f"check {DESTINATION_KEY}: if the row is there, Dismiss it, otherwise Try again"
    )


def test_a_templated_failed_check_parks_with_the_rendered_value(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    steps = [dict(step) for step in DEFAULT_STEPS]
    steps[2] = {
        **steps[2],
        "assert_after": {"text_count_increases": "{{row.contact}}", "timeout_ms": 8000},
    }
    base = seed(recipe(steps))
    base.add_input_record(email="a@x.com", contact="Ana Ruiz")
    run_id = base.create_run()

    scripted = ScriptedRun.build(ambiguous=["row-0001"])
    scripted.executor.conditions["save"] = False
    worker = make_worker(scripted)
    assert worker.run_once() is True

    record = get_record(conn, base.record_ids(run_id)[0])
    assert record is not None
    assert record.state == "parked"
    assert record.reason == (
        '"Ana Ruiz" did not appear one more time within 8 seconds; '
        f"check {DESTINATION_KEY}: if the row is there, Dismiss it, otherwise Try again"
    )


def test_a_reconcile_with_no_failed_check_keeps_the_parking_reason(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    """A recovered record in the commit window is reconciled, not explained by a check."""
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    record_id = base.record_ids(run_id)[0]
    _walk(conn, record_id, ["claimed", "prechecked", "filling", "submitting"])

    worker = make_worker(ScriptedRun.build(), reconciler=ParkingReconciler())
    assert worker.run_once() is True

    record = get_record(conn, record_id)
    assert record is not None
    assert record.state == "parked"
    assert record.reason == PARKING_REASON


def test_a_search_error_with_no_failed_check_keeps_the_reconciler_error(
    seed: Callable[..., Seed],
    make_worker: Callable[..., Worker],
    conn: psycopg.Connection,
) -> None:
    base = seed()
    base.add_input_record(email="a@x.com")
    run_id = base.create_run()
    record_id = base.record_ids(run_id)[0]
    _walk(conn, record_id, ["claimed", "prechecked", "filling", "submitting"])

    worker = make_worker(ScriptedRun.build(errors=["row-0001"]))
    assert worker.run_once() is True

    record = get_record(conn, record_id)
    assert record is not None
    assert record.state == "parked"
    assert record.reason == "the destination search failed"
