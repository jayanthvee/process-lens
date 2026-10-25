"""PL-101: a record key picked from a list is warned about before it skips real work.

ProcessLens does each key once, for good (``committed_keys``). A key the person picks from a list
-- a customer, a salesperson -- is shared by many rows by its nature: the pilot's invoices keyed on
the customer would make every customer's second invoice "already done", in this run and in every
later one. The compiler knows which columns feed a list (they template an option rung, or they are
the text typed to search it), so it says so in plain words.
"""

from __future__ import annotations

from typing import Any

from packages.compiler import compile_recording
from packages.compiler.tests.test_pl027_real_apps import FORM, ORIGIN, click, fill, options


def _salesperson_recording() -> list[dict[str, Any]]:
    """Name typed, salesperson typed and picked from the list, Save."""
    return [
        fill(1, "fill-1", "e.g. Product Pricing", "Demo Lead"),
        fill(2, "fill-2", "Salesperson", "Priya"),
        {
            "seq": 3,
            "kind": "click",
            "url": FORM,
            "step": {
                "id": "click-1",
                "action": "click",
                "name": "Click Priya Raman",
                "targets": [{"by": "role", "role": "option", "name": "Priya Raman"}],
            },
        },
        click(4, "click-2", "Save manually"),
        {"seq": 5, "kind": "navigate", "url": f"{ORIGIN}/odoo/crm/21", "step": None},
    ]


def test_a_key_chosen_from_a_list_is_warned_about() -> None:
    result = compile_recording(
        _salesperson_recording(),
        options(record_key_column="salesperson_2", commit_step_ids=["click-2"]),
    )

    assert result.key_warning is not None
    assert result.key_warning.startswith("Salesperson is picked from a list")
    assert "skipped" in result.key_warning
    assert result.key_warning in result.warnings


def test_the_text_typed_to_search_a_list_is_warned_about_too() -> None:
    result = compile_recording(
        _salesperson_recording(),
        options(record_key_column="salesperson", commit_step_ids=["click-2"]),
    )

    assert result.key_warning is not None
    assert result.key_warning.startswith("Salesperson is picked from a list")


def test_a_typed_key_is_not_warned_about() -> None:
    result = compile_recording(
        _salesperson_recording(),
        options(record_key_column="e_g_product_pricing", commit_step_ids=["click-2"]),
    )

    assert result.key_warning is None
