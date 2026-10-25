"""Reading a text PDF into columns and rows (PL-091): invoices, a price list, and the refusals."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from packages.documents import (
    NO_PDF_FIELDS,
    NO_PDF_TEXT,
    NOT_A_PDF,
    DocumentRefused,
    NoReadableContent,
    NotAPdf,
    read_pdf,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "documents"

INVOICE_COLUMNS = [
    "Invoice Number",
    "Invoice Date",
    "Customer",
    "Product",
    "Quantity",
    "Unit Price",
    "Amount",
    "Total",
]


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_one_invoice_per_page_is_one_row_per_page() -> None:
    columns, rows = read_pdf(load("invoices-one-per-page.pdf"))

    assert columns == INVOICE_COLUMNS
    assert rows == [
        {
            "Invoice Number": "INV-2026-0101",
            "Invoice Date": "2026-10-20",
            "Customer": "Ridgeline Outfitters",
            "Product": "Thunderbolt Dock",
            "Quantity": "2",
            "Unit Price": "349.00",
            "Amount": "698.00",
            "Total": "698.00",
        },
        {
            "Invoice Number": "INV-2026-0102",
            "Invoice Date": "2026-10-21",
            "Customer": "Harbourview Dental",
            "Product": "Executive Leather Chair",
            "Quantity": "1",
            "Unit Price": "€1,299.00",
            "Amount": "€1,299.00",
            "Total": "€1,299.00",
        },
        {
            "Invoice Number": "INV-2026-0103",
            "Invoice Date": "2026-10-22",
            # Only the first colon on a line separates the label from its value.
            "Customer": "Pinewood Preschool: Main Campus",
            "Product": "Drafting Stool",
            "Quantity": "4",
            "Unit Price": "189.00",
            "Amount": "756.00",
            "Total": "756.00",
        },
    ]


def test_a_multi_word_header_is_one_column() -> None:
    columns, _rows = read_pdf(load("invoices-one-per-page.pdf"))

    # "Unit Price" is two words on the page but one header: the gap between them is a space,
    # not a column gap.
    assert "Unit Price" in columns
    assert "Unit" not in columns
    assert "Price" not in columns


def test_a_title_and_a_footer_without_a_label_are_not_fields() -> None:
    columns, rows = read_pdf(load("invoices-one-per-page.pdf"))

    assert "INVOICE" not in columns
    assert not any("Thank you" in value for row in rows for value in row.values())


def test_an_invoice_with_two_line_items_is_two_rows_sharing_its_fields() -> None:
    columns, rows = read_pdf(load("invoice-two-lines.pdf"))

    assert columns == INVOICE_COLUMNS
    assert [(row["Product"], row["Quantity"], row["Unit Price"]) for row in rows] == [
        ("1080p Webcam", "3", "79.00"),
        ("Monitor Light Bar", "1", "119.00"),
    ]
    for row in rows:
        assert row["Invoice Number"] == "INV-2026-0201"
        assert row["Customer"] == "Corner Bakery Group"
        assert row["Total"] == "356.00"


def test_a_table_alone_is_one_row_per_table_row() -> None:
    columns, rows = read_pdf(load("price-list.pdf"))

    assert columns == ["SKU", "New Price"]
    assert rows == [
        {"SKU": "PL-DESK-1001", "New Price": "762.00"},
        {"SKU": "PL-CHR-2001", "New Price": "342.00"},
        {"SKU": "PL-MON-3001", "New Price": "442.00"},
    ]


def test_a_pdf_with_no_text_is_refused_as_a_scan() -> None:
    with pytest.raises(NoReadableContent) as refused:
        read_pdf(load("scanned.pdf"))

    assert str(refused.value) == NO_PDF_TEXT
    assert "scanned" in NO_PDF_TEXT


def test_a_pdf_with_text_but_no_fields_or_table_is_refused() -> None:
    with pytest.raises(NoReadableContent) as refused:
        read_pdf(load("letter.pdf"))

    assert str(refused.value) == NO_PDF_FIELDS


@pytest.mark.parametrize(
    "content",
    [b"", b"hello, this is plain text", b"PK\x03\x04 not a pdf, a zip"],
)
def test_a_file_that_is_not_a_pdf_is_refused(content: bytes) -> None:
    with pytest.raises(NotAPdf) as refused:
        read_pdf(content)

    assert str(refused.value) == NOT_A_PDF
    assert isinstance(refused.value, DocumentRefused)


def test_a_truncated_pdf_is_refused_or_read_but_never_crashes() -> None:
    content = load("invoices-one-per-page.pdf")

    # A cut-off download: the reader may still find the first page, or refuse in one sentence,
    # but no library error may reach the person.
    with contextlib.suppress(DocumentRefused):
        read_pdf(content[: len(content) // 3])


def test_reading_is_deterministic() -> None:
    content = load("invoices-one-per-page.pdf")

    assert read_pdf(content) == read_pdf(content)
