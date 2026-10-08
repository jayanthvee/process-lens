"""Inferring the assertion that proves a commit happened."""

from __future__ import annotations

import pytest

from packages.compiler.assertions import (
    DEFAULT_ASSERTION_TIMEOUT_MS,
    condition_from_navigation,
    condition_from_observation,
    infer_assertion,
    origin_of,
    path_pattern,
)


def test_an_observed_confirmation_becomes_an_assertion() -> None:
    condition = condition_from_observation({"text_visible": "Record created"})
    assert condition == {
        "text_visible": "Record created",
        "timeout_ms": DEFAULT_ASSERTION_TIMEOUT_MS,
    }


@pytest.mark.parametrize(
    "observation",
    [
        {"text_visible": "Saved"},
        {"text_absent": "Saving…"},
        {"element_present": {"targets": [{"by": "role", "role": "button", "name": "Save"}]}},
        {"element_absent": {"targets": [{"by": "role", "role": "dialog", "name": "New record"}]}},
        {
            "value_equals": {
                "selector": {"targets": [{"by": "css", "selector": "#status"}]},
                "value": {"kind": "constant", "value": "Done"},
            }
        },
    ],
)
def test_every_observation_predicate_is_understood(observation: dict) -> None:
    condition = condition_from_observation(observation)
    assert condition is not None
    assert condition["timeout_ms"] == DEFAULT_ASSERTION_TIMEOUT_MS


def test_an_empty_observation_yields_no_assertion() -> None:
    assert condition_from_observation(None) is None
    assert condition_from_observation({}) is None


def test_an_unknown_observation_key_yields_no_assertion() -> None:
    assert condition_from_observation({"dom_diff": "changed"}) is None


def test_a_navigation_becomes_a_url_assertion() -> None:
    condition = condition_from_navigation(
        "https://app.example.com/records/new", "https://app.example.com/records"
    )
    assert condition == {"url_matches": "/records", "timeout_ms": DEFAULT_ASSERTION_TIMEOUT_MS}


def test_a_navigation_that_did_not_happen_yields_no_assertion() -> None:
    same = "https://app.example.com/records/new"
    assert condition_from_navigation(same, same) is None


def test_a_cross_origin_navigation_is_not_asserted_here() -> None:
    condition = condition_from_navigation(
        "https://app.example.com/records/new", "https://accounts.example.net/welcome"
    )
    assert condition is None


def test_origin_of_reads_scheme_host_and_port() -> None:
    assert origin_of("https://app.example.com/records/1?x=2") == "https://app.example.com"
    assert origin_of("http://localhost:5173/form") == "http://localhost:5173"
    assert origin_of("not a url") == ""


def test_path_pattern_drops_the_last_segment() -> None:
    assert path_pattern("https://app.example.com/records/42") == "/records/"
    assert path_pattern("https://app.example.com/records/") == "/records/"
    assert path_pattern("https://app.example.com/") == "/"
    assert path_pattern("https://app.example.com") is None


def test_an_observation_wins_over_a_navigation() -> None:
    condition = infer_assertion(
        {"text_visible": "Record created"},
        "https://app.example.com/records/new",
        "https://app.example.com/records",
    )
    assert condition is not None
    assert "text_visible" in condition


def test_a_multi_segment_path_drops_its_last_segment() -> None:
    """The last segment often carries a record id that differs per record."""
    condition = condition_from_navigation(
        "https://app.example.com/records/new", "https://app.example.com/records/42"
    )
    assert condition is not None
    assert condition["url_matches"] == "/records/"


def test_navigation_is_used_when_there_is_no_observation() -> None:
    condition = infer_assertion(
        None,
        "https://app.example.com/records/new",
        "https://app.example.com/records",
    )
    assert condition == {"url_matches": "/records", "timeout_ms": DEFAULT_ASSERTION_TIMEOUT_MS}


def test_no_evidence_yields_no_assertion() -> None:
    assert infer_assertion(None, "https://app.example.com/records/new", None) is None


def test_the_timeout_is_configurable() -> None:
    condition = condition_from_observation({"text_visible": "Saved"}, timeout_ms=1500)
    assert condition == {"text_visible": "Saved", "timeout_ms": 1500}
