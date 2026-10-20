"""PL-075: proof for a save that updates a record in place.

An update-in-place navigates nowhere and shows no confirmation, but the save control goes away.
The recorder reports that as ``element_absent`` (with the record page's URL as a second
predicate); the compiler must accept it as the commit's assertion, and must not warn that the URL
half of the condition is also true on the page the step ran on -- the URL only pins the record
page, and the disappeared control is the proof.

Nothing here loosens the refusal for a click that produced no evidence at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.errors import CompileError
from packages.compiler.testing import action_event
from packages.compiler.validation import validation_errors

BASE_URL = "http://127.0.0.1:8069"
PRODUCT = f"{BASE_URL}/odoo/products/2"

SEARCH_FILL = [{"by": "css", "selector": "input.o_searchview_input"}]
SAVE_TARGETS = [
    {"by": "role", "role": "button", "name": "Save manually"},
    {"by": "css", "selector": "button.o_form_button_save"},
]
SAMPLE_ROW: dict[str, str] = {"sku": "PL-DESK-1002"}

# What the recorder emits after Odoo's update-in-place save: the Save control is no longer
# visible, and the page is still the product's own record page.
UPDATE_OBSERVATION: dict[str, Any] = {
    "element_absent": {"targets": SAVE_TARGETS},
    "url_matches": "/odoo/products/\\d+",
}

ALSO_TRUE_ON_THE_PAGE_WARNING = "is also true on the page the step ran on"


def _options(**overrides: Any) -> CompileOptions:
    base: dict[str, Any] = {
        "name": "Update a product price",
        "when_to_use": "Each row is a new sales price for one product.",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "sku",
        "commit_step_ids": ["save-1"],
    }
    base.update(overrides)
    return CompileOptions(**base)


def _save(
    observation: dict[str, Any] | None,
    *,
    search_value: str = "PL-DESK-1002",
) -> list[dict[str, Any]]:
    """Search for the product, then press Save on its record page."""
    return [
        action_event(
            1,
            "fill",
            "fill",
            SEARCH_FILL,
            value=search_value,
            url=f"{BASE_URL}/odoo/products",
        ),
        action_event(
            2,
            "click",
            "click",
            SAVE_TARGETS,
            step_id="save-1",
            url=PRODUCT,
            observation=observation,
        ),
    ]


def _commit(result: Any) -> dict[str, Any]:
    return next(step for step in result.recipe["steps"] if step.get("commit") is True)


def test_a_disappeared_control_becomes_the_commit_assertion() -> None:
    result = compile_recording(_save(UPDATE_OBSERVATION), _options())

    check = _commit(result)["assert_after"]
    assert check["element_absent"] == {"targets": SAVE_TARGETS}
    # The URL half is carried too, so the proof is "the control is gone, on this record page".
    assert check["url_matches"] == "/odoo/products/\\d+"
    assert isinstance(check["timeout_ms"], int) and check["timeout_ms"] > 0
    assert validation_errors(result.recipe) == []


def test_the_url_half_does_not_warn_when_the_control_is_the_proof() -> None:
    """The URL matches the step's own page; that is expected here, not a weakness."""
    result = compile_recording(_save(UPDATE_OBSERVATION), _options())

    assert not any(ALSO_TRUE_ON_THE_PAGE_WARNING in warning for warning in result.warnings), (
        result.warnings
    )
    assert any("taken from the recorded observation" in warning for warning in result.warnings)


def test_a_click_with_no_evidence_at_all_is_still_refused() -> None:
    """PL-063's refusal stays: a save that produced nothing cannot become a commit.

    The search term is not one of the row's values, so it is not a column the record key or the
    new record's name could come from, and no fallback can invent an assertion for the save.
    """
    with pytest.raises(CompileError) as caught:
        compile_recording(_save(None, search_value="desk"), _options())

    assert any("has no assert_after" in problem for problem in caught.value.problems), (
        caught.value.problems
    )


# --- a container click that moved the page is an action -------------------------------------

LIST_PAGE = f"{BASE_URL}/odoo/products?view_type=list"
ROW_TARGETS = [{"by": "css", "selector": "tr.o_data_row"}]


def _row_click(observation: dict[str, Any] | None) -> list[dict[str, Any]]:
    """A click on Odoo's list row: css-only, no role and no label, but it opens the record."""
    return [
        action_event(
            1,
            "click",
            "click",
            ROW_TARGETS,
            step_id="open-1",
            url=LIST_PAGE,
            observation=observation,
        ),
        # One more step, so a recording whose row click is dropped still compiles: the question
        # is which steps the recipe has, not whether there is a recipe at all.
        action_event(
            2,
            "fill",
            "fill",
            SEARCH_FILL,
            value="PL-DESK-1002",
            url=PRODUCT,
        ),
    ]


def test_a_css_only_click_that_moved_the_page_is_kept() -> None:
    """PL-075: the row click that opens a product has no role, but its URL evidence proves it acted.

    Without it the compiled task fills the next field on the list page and every row parks; the
    container rule exists for card clicks that change nothing, and this click changed the page.
    """
    result = compile_recording(
        _row_click({"url_matches": "/odoo/products/\\d+"}),
        _options(commit_step_ids=[], record_key_column="sku"),
    )

    assert any(
        step.get("action") == "click"
        and (step.get("targets") or [{}])[0].get("selector") == "tr.o_data_row"
        for step in result.recipe["steps"]
    ), result.recipe["steps"]
    assert not any("dropped container click" in warning for warning in result.warnings)


def test_a_css_only_click_with_no_evidence_is_still_dropped() -> None:
    """The container rule still holds for a click that changed nothing."""
    result = compile_recording(
        _row_click(None),
        _options(commit_step_ids=[], record_key_column="sku"),
    )

    assert not any(step.get("action") == "click" for step in result.recipe["steps"]), result.recipe[
        "steps"
    ]
    assert any("dropped container click" in warning for warning in result.warnings)
