"""Reading and cleaning documents for the rest of the application.

The reader turns a Word document or a text PDF into columns and rows; the cleaner makes those
values tidy and says what it changed. Both are pure and deterministic, so a batch made from a
document is the same batch every time.
"""

from __future__ import annotations

from .clean import clean, csv_bytes
from .docx_table import (
    NO_READABLE_CONTENT,
    NOT_A_WORD_DOCUMENT,
    DocumentRefused,
    NoReadableContent,
    NotAWordDocument,
    read_docx,
)
from .pdf_document import NO_PDF_FIELDS, NO_PDF_TEXT, NOT_A_PDF, NotAPdf, read_pdf

__all__ = [
    "NO_PDF_FIELDS",
    "NO_PDF_TEXT",
    "NO_READABLE_CONTENT",
    "NOT_A_PDF",
    "NOT_A_WORD_DOCUMENT",
    "DocumentRefused",
    "NoReadableContent",
    "NotAPdf",
    "NotAWordDocument",
    "clean",
    "csv_bytes",
    "read_docx",
    "read_pdf",
]
