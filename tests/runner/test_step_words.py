"""PL-097: a row that stops on a step says why in words the person can act on.

The pilot's messy lead file on Render parked or failed rows with the extension's own diagnosis:

    the click target could not be resolved: the target is not clickable: the box center is
    covered by <div>

for a salesperson the list does not have, and

    the fill step fill-6 resolved to an empty value; an empty fill is refused rather than submitted

for a row with no salesperson. ``describe_step_failure`` turns the step, the row and the raw error
into one sentence. The raw error stays on the step event for whoever debugs it.
"""

from __future__ import annotations

from services.runner.step_words import describe_step_failure

ROW = {"salesperson": "Priya Ramen", "company_name": "Lee Consulting", "e_g_product_pricing": ""}

OPTION_CLICK = {
    "id": "click-4",
    "name": "Click Priya Raman",
    "action": "click",
    "targets": [
        {"by": "role", "role": "option", "name": "{{row.salesperson}}"},
        {"by": "text_fuzzy", "value": "{{row.salesperson}}"},
    ],
}

SALESPERSON_FILL = {
    "id": "fill-6",
    "name": "Fill Salesperson",
    "action": "fill",
    "value": {"kind": "input", "column": "salesperson"},
    "targets": [{"by": "role", "role": "combobox", "name": "Salesperson"}],
}

SAVE_CLICK = {
    "id": "click-5",
    "name": "Click Save manually",
    "action": "click",
    "targets": [{"by": "role", "role": "button", "name": "Save manually"}],
}

COVERED = (
    "the click target could not be resolved: the target is not clickable: "
    "the box center is covered by <div>"
)


def test_an_option_the_list_does_not_have_names_the_value_and_the_field() -> None:
    sentence = describe_step_failure(OPTION_CLICK, ROW, {}, COVERED)

    assert sentence == "“Priya Ramen” isn't in the Salesperson list"


def test_an_empty_value_names_the_column() -> None:
    error = (
        "the fill step fill-6 resolved to an empty value; an empty fill is refused rather than "
        "submitted"
    )

    sentence = describe_step_failure(SALESPERSON_FILL, {"salesperson": ""}, {}, error)

    assert sentence == "No salesperson in this row"


def test_a_fill_the_page_changed_says_what_it_kept() -> None:
    error = (
        'the fill did not stick: the recipe typed "Kai Line\\r\\nBreak" but the field holds '
        '"Kai Line\\nBreak"'
    )
    step = {**SALESPERSON_FILL, "name": "Fill Name", "value": {"kind": "input", "column": "name"}}

    sentence = describe_step_failure(step, {"name": "Kai Line\r\nBreak"}, {}, error)

    assert sentence == "The page changed the name as it was typed; check the row's name"


def test_a_button_that_cannot_be_clicked_names_the_button() -> None:
    sentence = describe_step_failure(SAVE_CLICK, ROW, {}, COVERED)

    assert sentence == "Couldn't click “Save manually”: something was in front of it"


def test_a_target_that_is_not_on_the_page_names_it() -> None:
    error = "the click target could not be resolved: no rung matched"

    sentence = describe_step_failure(SAVE_CLICK, ROW, {}, error)

    assert sentence == "Couldn't find “Save manually” on the page"


def test_an_error_this_module_does_not_know_is_kept_but_short() -> None:
    error = "x" * 400

    sentence = describe_step_failure(SAVE_CLICK, ROW, {}, error)

    assert sentence.startswith("Step “Click Save manually” didn't finish: xxx")
    assert len(sentence) <= 160


def test_no_error_at_all_still_gives_a_sentence() -> None:
    assert describe_step_failure(SAVE_CLICK, ROW, {}, None) == (
        "Step “Click Save manually” didn't finish"
    )


def test_a_recorders_numbered_column_is_named_as_its_field() -> None:
    """PL-099: the invoice's product option reads `product_2`; the person knows a Product list."""
    step = {
        **OPTION_CLICK,
        "targets": [{"by": "role", "role": "option", "name": "{{row.product_2}}"}],
    }

    sentence = describe_step_failure(step, {"product_2": "Desk Lamp"}, {}, COVERED)

    assert sentence == "“Desk Lamp” isn't in the Product list"
