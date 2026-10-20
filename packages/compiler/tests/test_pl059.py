"""PL-059: a save sent from a "new" page is checked by the address it lands on.

The Odoo quotation golden now reaches Save on every row and parks at the save check: the
quotation's number is no longer pinned (PL-049), so the compiler fell back to "the new record's
name appears one more time" -- and on a quotation the customer's name is already on the form
before Save, so the count never goes up.

The durable evidence is the address: Odoo saves ``/odoo/sales/new`` and lands on
``/odoo/sales/42``; Twenty and most other apps do the same with ``/new`` and a record id or a
UUID. A pattern for the saved address is evidence precisely because the page the save was sent
from cannot satisfy it.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from packages.compiler import CompileOptions, compile_recording
from packages.compiler.testing import action_event
from packages.compiler.validation import validation_errors

BASE_URL = "http://127.0.0.1:8069"
SALES_NEW = f"{BASE_URL}/odoo/sales/new"
SALES_SAVED = f"{BASE_URL}/odoo/sales/42"
UUID_NEW = "https://crm.example.test/objects/companies/new"
UUID_SAVED = "https://crm.example.test/objects/companies/6f1c2a9e-0b7d-4c1e-9a55-3c2d1e0f9a11"

CUSTOMER_FILL = [{"by": "role", "role": "combobox", "name": "Customer"}]
SAVE_TARGETS = [
    {"by": "role", "role": "button", "name": "Save"},
    {"by": "css", "selector": "button.o_form_button_save"},
]
SAMPLE_ROW: dict[str, str] = {"customer": "Ridgeline Outfitters"}

NEW_PAGE_WARNING = "this save is checked by the page moving from /new to the saved record"


def _options(**overrides: Any) -> CompileOptions:
    base: dict[str, Any] = {
        "name": "New quotation",
        "when_to_use": "Each row is a sales quotation for one customer.",
        "sample_row": dict(SAMPLE_ROW),
        "record_key_column": "customer",
        "commit_step_ids": ["save-1"],
    }
    base.update(overrides)
    return CompileOptions(**base)


def _save(
    url: str = SALES_NEW,
    *,
    observation: dict[str, Any] | None = None,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """Fill the customer, press Save; optionally a navigate the recording observed after it."""
    events = [
        action_event(1, "fill", "fill", CUSTOMER_FILL, value="Ridgeline Outfitters", url=url),
        action_event(
            2, "click", "click", SAVE_TARGETS, step_id="save-1", url=url, observation=observation
        ),
    ]
    if after is not None:
        events.append({"seq": 3, "kind": "navigate", "url": after})
    return events


def _commit(result: Any) -> dict[str, Any]:
    return next(step for step in result.recipe["steps"] if step.get("commit") is True)


def test_a_save_from_a_new_page_is_checked_by_the_record_address() -> None:
    result = compile_recording(_save(), _options())
    check = _commit(result)["assert_after"]

    assert "url_matches" in check
    assert re.search(check["url_matches"], SALES_SAVED)
    assert not re.search(check["url_matches"], SALES_NEW)
    assert NEW_PAGE_WARNING in result.warnings
    assert validation_errors(result.recipe) == []


@pytest.mark.parametrize("page", [f"{SALES_NEW}/", f"{SALES_NEW}?debug=1"])
def test_the_new_page_may_carry_a_trailing_slash_or_a_query(page: str) -> None:
    result = compile_recording(_save(page), _options())
    check = _commit(result)["assert_after"]

    assert re.search(check["url_matches"], SALES_SAVED)
    assert not re.search(check["url_matches"], SALES_NEW)


def test_a_uuid_app_is_matched_by_the_same_check() -> None:
    result = compile_recording(_save(UUID_NEW), _options())
    check = _commit(result)["assert_after"]

    assert re.search(check["url_matches"], UUID_SAVED)
    assert not re.search(check["url_matches"], UUID_NEW)


def test_a_recorded_navigation_still_wins() -> None:
    result = compile_recording(_save(after=SALES_SAVED), _options())
    check = _commit(result)["assert_after"]

    assert check["url_matches"] == "/odoo/sales/\\d+"
    assert NEW_PAGE_WARNING not in result.warnings


def test_a_real_observation_still_wins() -> None:
    result = compile_recording(_save(observation={"text_visible": "Quotation saved"}), _options())
    assert _commit(result)["assert_after"]["text_visible"] == "Quotation saved"


def test_a_page_that_is_not_new_keeps_the_fill_check() -> None:
    result = compile_recording(_save("http://app.test/quotations"), _options())
    check = _commit(result)["assert_after"]

    assert check["text_count_increases"] == "{{row.customer}}"
    assert "url_matches" not in check


def test_a_generated_number_on_a_new_page_now_gets_the_url_check() -> None:
    """PL-049 still drops the number; PL-059 gives it the durable check instead of the fill."""
    result = compile_recording(_save(observation={"text_visible": "S00001"}), _options())
    check = _commit(result)["assert_after"]

    assert "text_visible" not in check
    assert "text_count_increases" not in check
    assert re.search(check["url_matches"], SALES_SAVED)
