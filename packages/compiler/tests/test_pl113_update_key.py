"""PL-113 item 5: an update's "already done" key includes the new value.

The key was the item alone, so a second price for the same product could never run again. PL-079
made an update's idempotency key the record key *and* the values it writes; this pins the ticket's
own case: (Desk, 799) twice is one piece of work, (Desk, 850) is new work.

Rendering the key is the ledger's own act -- ``committed_keys`` stores the rendered string -- so the
test renders it with the runner's renderer, exactly as a run does.
"""

from __future__ import annotations

from packages.compiler import compile_recording
from packages.compiler.tests.test_pl077_search_shape import _find_then_update, _options
from services.runner.templates import render


def _key_pattern() -> str:
    result = compile_recording(_find_then_update(), _options(commit_step_ids=["save-1"]))
    return result.recipe["meta"]["idempotency_key"]


def test_the_same_product_and_price_is_one_key() -> None:
    pattern = _key_pattern()
    row = {"sku": "Oak Standing Desk", "price": "799"}

    assert render(pattern, row=row, variables={}) == render(pattern, row=row, variables={})


def test_a_new_price_for_the_same_product_is_new_work() -> None:
    pattern = _key_pattern()

    first = render(pattern, row={"sku": "Oak Standing Desk", "price": "799"}, variables={})
    second = render(pattern, row={"sku": "Oak Standing Desk", "price": "850"}, variables={})

    assert first != second
    # The item is still part of the key: two products at one price are not the same work.
    other = render(pattern, row={"sku": "Mesh Task Chair", "price": "799"}, variables={})
    assert first != other
