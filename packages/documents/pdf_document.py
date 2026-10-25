"""Read a text PDF into columns and rows.

Only a PDF that carries real text is read; a scan needs OCR, which ProcessLens does not do. A page
is understood through the positions of its words: a ``Label: value`` line is a field, and a header
line with two or more word groups starts a table whose rows are the lines below it. A title or a
footer is ignored. The file is only read: nothing in it is executed.
"""

from __future__ import annotations

import contextlib
import io
from typing import Any

import pdfplumber

from .docx_table import DocumentRefused, NoReadableContent

NOT_A_PDF = "This file is not a PDF."
NO_PDF_TEXT = (
    "This PDF has no text ProcessLens can read; a scanned PDF needs OCR, which ProcessLens "
    "does not do yet."
)
NO_PDF_FIELDS = "This PDF has no labelled fields or table ProcessLens can read."

# Two words on one line share a top within this many points.
_LINE_TOLERANCE = 2.0
# A space is about a quarter of the font's size; a gap of two spaces starts a new word group.
_SPACE_WIDTH = 0.25
_GROUP_GAP = 2.0


class NotAPdf(DocumentRefused):
    """The upload is not a PDF, whatever its name says."""


def _open(content: bytes) -> Any:
    """Open the PDF, or say plainly that it is not one.

    The magic check keeps a renamed spreadsheet from being handed to the parser at all. A parser
    error means the bytes are not a PDF it can read -- a truncated download, an encrypted file
    with no password -- so the person gets the same one sentence.
    """
    if not content.startswith(b"%PDF-"):
        raise NotAPdf(NOT_A_PDF)
    try:
        return pdfplumber.open(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001 - every parser error is a refusal, not a 500
        raise NotAPdf(NOT_A_PDF) from exc


def _lines(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The page's words grouped into lines: words whose tops are within two points."""
    lines: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= _LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda item: item["x0"]) for line in lines]


def _groups(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Runs of words separated by less than about two space widths."""
    groups: list[list[dict[str, Any]]] = []
    for word in words:
        if groups:
            previous = groups[-1][-1]
            space = _SPACE_WIDTH * max(word.get("height", 0.0), previous.get("height", 0.0))
            if word["x0"] - previous["x1"] < _GROUP_GAP * space:
                groups[-1].append(word)
                continue
        groups.append([word])
    return groups


def _text(words: list[dict[str, Any]]) -> str:
    return " ".join(word["text"] for word in words)


def _label(line: list[dict[str, Any]]) -> tuple[str, str] | None:
    """The label and its value when the line holds a colon, split at the first one."""
    text = _text(line)
    if ":" not in text:
        return None
    label, value = text.split(":", 1)
    label, value = label.strip(), value.strip()
    return (label, value) if label else None


def _header(line: list[dict[str, Any]]) -> list[tuple[float, str]] | None:
    """The column edges and names when the line is made of two or more word groups."""
    groups = _groups(line)
    if len(groups) < 2:
        return None
    return [(group[0]["x0"], _text(group)) for group in groups]


def _cells(line: list[dict[str, Any]], header: list[tuple[float, str]]) -> dict[str, str] | None:
    """The line's words under the header's columns, or ``None`` when one falls outside them."""
    cells: dict[str, list[str]] = {name: [] for _left, name in header}
    for word in line:
        column = None
        for left, name in header:
            if word["x0"] < left:
                break
            column = name
        if column is None:
            return None
        cells[column].append(word["text"])
    return {name: " ".join(values) for name, values in cells.items()}


def _read_page(words: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, str]]]:
    """One page's columns and rows, in the order its words are first seen."""
    lines = _lines(words)
    labels: dict[str, str] = {}
    columns: list[str] = []
    table_rows: list[dict[str, str]] = []

    def remember(name: str) -> None:
        if name not in columns:
            columns.append(name)

    index = 0
    while index < len(lines):
        line = lines[index]
        label = _label(line)
        if label is not None:
            name, value = label
            labels[name] = value
            remember(name)
            index += 1
            continue

        header = _header(line)
        if header is None:
            index += 1
            continue

        # A header is a table only when a line below it is a row: a footer sentence with wide
        # gaps must not become an empty table of its own.
        rows: list[dict[str, str]] = []
        following = index + 1
        while following < len(lines):
            candidate = lines[following]
            if _label(candidate) is not None:
                break
            cells = _cells(candidate, header)
            if cells is None:
                break
            rows.append(cells)
            following += 1

        if rows:
            for _left, name in header:
                remember(name)
            table_rows.extend(rows)
        index = following

    if table_rows:
        return columns, [{**labels, **row} for row in table_rows]
    if labels:
        return columns, [dict(labels)]
    return columns, []


def read_pdf(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Return the PDF's columns and rows, or raise :class:`DocumentRefused`.

    A page with labels and a table gives one row per table row, carrying the page's labels; a page
    with only labels gives one row; a page with only a table gives one row per table row. Pages
    are read in order, so one invoice per page is one row per page.
    """
    pdf = _open(content)
    columns: list[str] = []
    rows: list[dict[str, str]] = []
    text_seen = False
    try:
        try:
            for page in pdf.pages:
                try:
                    words = page.extract_words()
                except Exception:  # noqa: BLE001 - a damaged page stops the read, never a crash
                    break
                if not words:
                    continue
                text_seen = True
                page_columns, page_rows = _read_page(words)
                for name in page_columns:
                    if name not in columns:
                        columns.append(name)
                rows.extend(page_rows)
        except Exception:  # noqa: BLE001 - a damaged document keeps the pages already read
            pass
    finally:
        with contextlib.suppress(Exception):
            pdf.close()

    if not text_seen:
        raise NoReadableContent(NO_PDF_TEXT)
    if not rows:
        raise NoReadableContent(NO_PDF_FIELDS)
    return columns, [{name: row.get(name, "") for name in columns} for row in rows]


__all__ = [
    "NO_PDF_FIELDS",
    "NO_PDF_TEXT",
    "NOT_A_PDF",
    "NotAPdf",
    "read_pdf",
]
