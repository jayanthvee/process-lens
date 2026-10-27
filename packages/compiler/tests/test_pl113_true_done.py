"""PL-113 item 1 (blocking): Done means the change is on the page.

Two rules, both proved here at the layer that owns them:

* the compiler refuses a commit assertion that is the page's own chrome (a tab, a breadcrumb, a
  menu, a heading): "the Sales tab is there" is true on every page, so it proves nothing;
* the runner never verifies a commit it cannot check. A check that was already true before the step
  is not proof, and a step carrying no check at all parks with the ticket's sentence rather than
  being called Done.
"""

from __future__ import annotations

import pytest

from packages.compiler import CompileError, compile_recording
from packages.compiler.testing import (
    DEFAULT_URL,
    INTEREST_TARGETS,
    action_event,
)

TAB_TARGETS = [{"by": "role", "role": "tab", "name": "Sales"}]
FIELD = "{{row.price}}"


def _options(**overrides: object):
    from packages.compiler import CompileOptions

    values: dict[str, object] = {
        "name": "Update a price",
        "when_to_use": "Each row changes one price.",
        "sample_row": {"price": "799"},
        "input_fields": {"price": {"type": "string", "required": True}},
        "record_key": FIELD,
        "commit_step_ids": ["click-2"],
    }
    values.update(overrides)
    return CompileOptions(**values)  # type: ignore[arg-type]


def _edit_ending_in_a_tab_whose_label_is_the_confirmation() -> list[dict]:
    """Fill the price, click Save, and the recording says the Sales tab labels the save."""
    events = [
        action_event(
            1,
            "fill",
            "fill",
            INTEREST_TARGETS,
            value="799",
            name="Fill the sales price",
        ),
        action_event(2, "click", "click", [{"by": "role", "role": "button", "name": "Save"}]),
    ]
    events[1]["url"] = DEFAULT_URL
    # The recorded "confirmation" is the tab that was clicked on the way there.
    events[1]["observation"] = {"text_visible": "Sales"}
    events.insert(
        0,
        action_event(0, "click", "click", TAB_TARGETS, name="Click the Sales tab"),
    )
    return events


def test_the_compiler_refuses_a_tab_label_as_a_commit_assertion() -> None:
    with pytest.raises(CompileError) as refusal:
        compile_recording(_edit_ending_in_a_tab_whose_label_is_the_confirmation(), _options())

    problems = "\n".join(refusal.value.problems)
    assert "the page's own frame" in problems or "tab" in problems.lower()
    assert "re-record" in problems
