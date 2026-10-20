"""The cleaner's rules, one at a time: headers, cells, rows, and the notes a person reads."""

from __future__ import annotations

from packages.documents.clean import clean


def test_headers_are_trimmed_deduplicated_and_named_by_position() -> None:
    columns, _rows, _notes = clean([" Email ", "Email", "", "Phone"], [])

    assert columns == ["Email", "Email 2", "Column 3", "Phone"]


def test_a_header_that_collides_with_a_named_empty_column_still_gets_a_number() -> None:
    columns, _rows, _notes = clean(["Column 2", ""], [])

    assert columns == ["Column 2", "Column 2 2"]


def test_whitespace_and_invisible_characters_are_removed() -> None:
    columns, rows, _notes = clean(
        ["Name", "Email"],
        [["  Ana\u00a0\u200b\u200c\u200d\ufeff  Ruiz\t\n", " Ana@Example.TEST "]],
    )

    assert columns == ["Name", "Email"]
    assert rows[0] == {"Name": "Ana Ruiz", "Email": "ana@example.test"}


def test_smart_quotes_are_made_plain() -> None:
    _columns, rows, _notes = clean(["Name"], [["\u201cAna\u201d \u2018Ani\u2019"]])

    assert rows[0] == {"Name": "\"Ana\" 'Ani'"}


def test_a_phone_number_is_left_as_typed_but_trimmed() -> None:
    _columns, rows, _notes = clean(["Phone"], [["  +34 600 123 456  "]])

    assert rows[0] == {"Phone": "+34 600 123 456"}


def test_unambiguous_dates_are_written_as_iso() -> None:
    _columns, rows, notes = clean(
        ["When"],
        [["2026-03-12"], ["13 March 2026"], ["March 14, 2026"], ["15 Mar 2026"]],
    )

    assert [row["When"] for row in rows] == [
        "2026-03-12",
        "2026-03-13",
        "2026-03-14",
        "2026-03-15",
    ]
    assert notes == []


def test_an_ambiguous_date_is_left_as_typed_and_counted() -> None:
    _columns, rows, notes = clean(["When"], [["03/04/2026"], ["2026-03-12"]])

    assert rows[0] == {"When": "03/04/2026"}
    assert rows[1] == {"When": "2026-03-12"}
    assert notes == ["1 date was ambiguous and left as typed"]


def test_the_ambiguous_date_note_counts_in_the_plural() -> None:
    _columns, rows, notes = clean(["When"], [["03/04/2026"], ["04/05/2026"]])

    assert len(rows) == 2
    assert notes == ["2 dates were ambiguous and left as typed"]


def test_numbers_with_thousands_separators_keep_their_text() -> None:
    _columns, rows, _notes = clean(["Amount"], [["1,234"], ["1.234,56"]])

    assert rows == [{"Amount": "1,234"}, {"Amount": "1.234,56"}]


def test_empty_rows_are_dropped_and_counted() -> None:
    _columns, rows, notes = clean(
        ["Name", "Email"],
        [["Ana", "ana@example.test"], ["", "  "], ["Sam", "sam@example.test"]],
    )

    assert rows == [
        {"Name": "Ana", "Email": "ana@example.test"},
        {"Name": "Sam", "Email": "sam@example.test"},
    ]
    assert notes == ["Removed 1 empty row"]


def test_exact_duplicate_rows_are_dropped_and_counted() -> None:
    _columns, rows, notes = clean(
        ["Name", "Email"],
        [
            ["Ana", "ana@example.test"],
            [" Ana ", "ANA@example.test"],
            ["Sam", "sam@example.test"],
        ],
    )

    assert rows == [
        {"Name": "Ana", "Email": "ana@example.test"},
        {"Name": "Sam", "Email": "sam@example.test"},
    ]
    # The second row is a duplicate only after the cleaner has had its say.
    assert notes == ["Removed 1 duplicate row"]


def test_empty_and_duplicate_rows_are_reported_together_in_order() -> None:
    _columns, rows, notes = clean(
        ["Name"],
        [["Ana"], [""], ["Ana"], [""], ["Sam"]],
    )

    assert rows == [{"Name": "Ana"}, {"Name": "Sam"}]
    assert notes == ["Removed 2 empty rows", "Removed 1 duplicate row"]


def test_nothing_is_reported_when_nothing_was_removed() -> None:
    _columns, rows, notes = clean(["Name"], [["Ana"]])

    assert rows == [{"Name": "Ana"}]
    assert notes == []


def test_a_row_missing_a_column_gets_an_empty_value() -> None:
    columns, rows, _notes = clean(["Name", "Email"], [["Ana"]])

    assert columns == ["Name", "Email"]
    assert rows == [{"Name": "Ana", "Email": ""}]
