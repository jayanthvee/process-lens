"""Build the small Word documents the document reader's tests use.

Run from anywhere::

    python fixtures/documents/make_fixtures.py

Every file is written next to this script. They are committed, so the tests do not need Word or
python-docx at run time -- but this script is what makes them reproducible.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document

HERE = Path(__file__).resolve().parent


def clean_table() -> None:
    """A plain three-column table, with a comma and a quote in one name for the CSV writer."""
    document = Document()
    document.add_heading("Inquiries", level=1)
    table = document.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    for column, text in enumerate(["Name", "Email", "Phone"]):
        table.cell(0, column).text = text
    rows = [
        ['Ana "Ani" Ruiz, Jr.', "Ana@Example.test", "+34 600 123 456"],
        ["Sam Lee", "sam@example.test", "+1 (415) 555-0134"],
    ]
    for index, values in enumerate(rows, start=1):
        for column, text in enumerate(values):
            table.cell(index, column).text = text
    document.save(HERE / "clean-table.docx")


def merged_header() -> None:
    """A header split across two rows: ``Contact`` spans two columns, ``Email`` sits under it."""
    document = Document()
    table = document.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Contact"
    table.cell(0, 2).text = "Email"
    for column, text in enumerate(["Name", "Phone", ""]):
        table.cell(1, column).text = text
    rows = [
        ["Ana Ruiz", "+34 600 123 456", "Ana@Example.test"],
        ["Sam Lee", "+1 (415) 555-0134", "sam@example.test"],
    ]
    for index, values in enumerate(rows, start=2):
        for column, text in enumerate(values):
            table.cell(index, column).text = text
    document.save(HERE / "merged-header.docx")


def label_blocks() -> None:
    """Two blocks of ``Label: value`` lines, separated by a blank paragraph."""
    document = Document()
    document.add_heading("New contacts", level=2)
    for line in [
        "Name: Ana Ruiz",
        "Email: Ana@Example.test",
        "Phone: +34 600 123 456",
        "",
        "Name: Sam Lee",
        "Email: sam@example.test",
        "Phone: +1 (415) 555-0134",
    ]:
        document.add_paragraph(line)
    document.save(HERE / "label-blocks.docx")


def bulleted_list() -> None:
    """A bulleted list of names under a heading."""
    document = Document()
    document.add_heading("Candidates", level=2)
    for name in ["Ana Ruiz", "Sam Lee", "Priya Nair"]:
        document.add_paragraph(name, style="List Bullet")
    document.save(HERE / "bulleted-list.docx")


def no_table() -> None:
    """Prose only: the reader has to refuse this one plainly."""
    document = Document()
    document.add_paragraph("A short note about the project.")
    document.add_paragraph("Nothing in this document is a table or a list.")
    document.save(HERE / "no-table.docx")


def main() -> None:
    clean_table()
    merged_header()
    label_blocks()
    bulleted_list()
    no_table()
    print(f"wrote five documents to {HERE}")


if __name__ == "__main__":
    main()
