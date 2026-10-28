"""PL-124: the facts the cleaning model gets, and the columns the Try one row card lists.

The owner's typed price was dropped because the model saw it next to the page's own value for the
same field, with nothing to tell them apart. Each step now says who made it, and no value travels.
And a column the recording offered but no step uses is not listed as "About to add".
"""

from __future__ import annotations

from services.ai.schemas import CLEAN_SYSTEM_PROMPT
from services.api.batches import _mapping_for, _used_columns
from services.api.compile import _recorded_steps

PRICE = [{"by": "label", "text": "Sales Price"}]


def _fill(step_id: str, value: str, **envelope: object) -> dict[str, object]:
    return {
        "seq": 1,
        "kind": "fill",
        "url": "http://localhost:8069/odoo/products/1",
        "step": {
            "id": step_id,
            "action": "fill",
            "name": "Fill Sales Price",
            "targets": PRICE,
            "value": {"kind": "constant", "value": value},
        },
        **envelope,
    }


def test_each_step_says_who_made_it_and_carries_no_value() -> None:
    steps = _recorded_steps(
        [
            _fill("fill-page", "400.00", page_set=True, keystrokes=0),
            _fill("fill-typed", "3200", keystrokes=4),
        ]
    )

    assert [(step["id"], step["by"]) for step in steps] == [
        ("fill-page", "page"),
        ("fill-typed", "person"),
    ]
    assert all("value" not in step for step in steps)
    assert "3200" not in str(steps) and "400.00" not in str(steps)


def test_the_prompt_tells_the_model_what_by_means() -> None:
    assert '"by": "page"' in CLEAN_SYSTEM_PROMPT
    assert '"by": "person"' in CLEAN_SYSTEM_PROMPT


def _salestask(steps: list[dict[str, object]]) -> dict[str, object]:
    """The owner's saved task: a sales_price column that no step writes."""
    return {
        "meta": {
            "record_key": "{{row.search | lower}}",
            "idempotency_key": "{{row.search | lower}}",
            "input_schema": {
                "fields": {
                    "search": {"type": "string", "example": "Oak Standing Desk"},
                    "sales_price": {"type": "string", "example": "3200"},
                }
            },
        },
        "steps": steps,
    }


SEARCH_STEP = {
    "id": "fill-1",
    "action": "fill",
    "value": {"kind": "input", "column": "search"},
    "targets": [{"by": "role", "role": "searchbox", "name": "Search..."}],
}


def test_a_column_no_step_uses_is_not_about_to_be_added() -> None:
    document = _salestask([SEARCH_STEP])

    assert _used_columns(document) == {"search"}
    mapping = _mapping_for(
        document,
        ["Product", "New Price"],
        None,
        {"Product": "Oak Standing Desk", "New Price": "799"},
    )
    # Still matched, because a row check may name it; just not listed as about to be added.
    assert [(entry.column, entry.used) for entry in mapping] == [
        ("search", True),
        ("sales_price", False),
    ]


def test_a_column_a_step_writes_is_listed() -> None:
    price_step = {
        "id": "fill-2",
        "action": "fill",
        "value": {"kind": "input", "column": "sales_price"},
        "targets": PRICE,
    }
    document = _salestask([SEARCH_STEP, price_step])

    assert _used_columns(document) == {"search", "sales_price"}
