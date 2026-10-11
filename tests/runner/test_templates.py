"""Template rendering: filters, and every way a template refuses to render."""

from __future__ import annotations

import pytest

from services.runner.templates import TemplateError, is_template, render, render_row


def test_renders_an_input_column() -> None:
    assert (
        render("{{row.email}}", row={"email": "Ada@Example.com"}, variables={}) == "Ada@Example.com"
    )


def test_applies_filters_left_to_right() -> None:
    assert (
        render("{{row.email | lower | trim}}", row={"email": "  ADA@x.COM "}, variables={})
        == "ada@x.com"
    )
    assert render("{{row.email | upper}}", row={"email": "ada"}, variables={}) == "ADA"


def test_renders_a_variable() -> None:
    assert render("{{team}}", row={}, variables={"team": "sales"}) == "sales"


def test_leaves_text_without_placeholders_alone() -> None:
    assert render("Lead created", row={}, variables={}) == "Lead created"


def test_a_missing_column_is_an_error() -> None:
    with pytest.raises(TemplateError, match="no column"):
        render("{{row.nope}}", row={"email": "a"}, variables={})


def test_a_missing_variable_is_an_error() -> None:
    with pytest.raises(TemplateError, match="no variable"):
        render("{{interest_option}}", row={"email": "a"}, variables={})


def test_an_unknown_filter_is_an_error() -> None:
    with pytest.raises(TemplateError, match="unknown template filter"):
        render("{{row.email | shout}}", row={"email": "a"}, variables={})


def test_a_none_value_is_an_error() -> None:
    with pytest.raises(TemplateError, match="no value"):
        render("{{row.email}}", row={"email": None}, variables={})


def test_a_non_string_value_is_rendered_as_text() -> None:
    assert render("{{row.n}}", row={"n": 42}, variables={}) == "42"


def test_render_row_uses_only_the_row() -> None:
    assert render_row("{{row.key | lower}}", {"key": "ABC"}) == "abc"


def test_is_template() -> None:
    assert is_template("{{row.key}}") is True
    assert is_template("plain") is False
    assert is_template(7) is False
