"""Write the PDF fixtures for PL-091: text PDFs laid out the way an invoice or a price list is.

    python fixtures/documents/make_pdf_fixtures.py

No PDF library is needed: each file is written by hand, one Helvetica text object per string at
its own position, so a test can say exactly where every word sits. Text is WinAnsi (cp1252), the
encoding a PDF's standard fonts use, so a euro sign is one byte.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

# One string on a page: its left edge, its baseline, its text and its font size (points).
Text = tuple[float, float, str, float]


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _content(texts: list[Text]) -> bytes:
    parts = []
    for x, y, text, size in texts:
        parts.append(b"BT /F1 %d Tf %.1f %.1f Td (" % (size, x, y) + _escape(text) + b") Tj ET")
    return b"\n".join(parts)


def pdf(pages: list[list[Text]]) -> bytes:
    """A PDF with one A4 page per entry, every string placed where the entry says."""
    objects: list[bytes] = []
    page_count = len(pages)
    # 1 catalog, 2 page tree, 3 font, then a page and its content stream per page.
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(page_count))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    for index, texts in enumerate(pages):
        content_id = 5 + 2 * index
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        stream = _content(texts)
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


# The line-item table's columns: where each header starts on the page.
PRODUCT_X, QUANTITY_X, PRICE_X, AMOUNT_X = 50.0, 300.0, 380.0, 470.0


def invoice_page(
    number: str, date: str, customer: str, lines: list[tuple[str, str, str, str]], total: str
) -> list[Text]:
    """One invoice: a title, label lines, a line-item table, and a total under it."""
    texts: list[Text] = [
        (50, 790, "INVOICE", 18),
        (50, 750, f"Invoice Number: {number}", 10),
        (50, 735, f"Invoice Date: {date}", 10),
        (50, 720, f"Customer: {customer}", 10),
        (PRODUCT_X, 680, "Product", 10),
        (QUANTITY_X, 680, "Quantity", 10),
        (PRICE_X, 680, "Unit Price", 10),
        (AMOUNT_X, 680, "Amount", 10),
    ]
    y = 662.0
    for product, quantity, price, amount in lines:
        texts += [
            (PRODUCT_X, y, product, 10),
            (QUANTITY_X, y, quantity, 10),
            (PRICE_X, y, price, 10),
            (AMOUNT_X, y, amount, 10),
        ]
        y -= 16
    texts.append((PRICE_X, y - 20, f"Total: {total}", 10))
    texts.append((50, 60, "Thank you for your business. Payment due within 30 days.", 8))
    return texts


def main() -> None:
    files = {
        # Three invoices, one per page. The third customer's own name has a colon in it.
        "invoices-one-per-page.pdf": pdf(
            [
                invoice_page(
                    "INV-2026-0101",
                    "2026-10-20",
                    "Ridgeline Outfitters",
                    [("Thunderbolt Dock", "2", "349.00", "698.00")],
                    "698.00",
                ),
                invoice_page(
                    "INV-2026-0102",
                    "2026-10-21",
                    "Harbourview Dental",
                    [("Executive Leather Chair", "1", "€1,299.00", "€1,299.00")],
                    "€1,299.00",
                ),
                invoice_page(
                    "INV-2026-0103",
                    "2026-10-22",
                    "Pinewood Preschool: Main Campus",
                    [("Drafting Stool", "4", "189.00", "756.00")],
                    "756.00",
                ),
            ]
        ),
        # One invoice with two line items: two rows, the invoice's own fields on both.
        "invoice-two-lines.pdf": pdf(
            [
                invoice_page(
                    "INV-2026-0201",
                    "2026-10-23",
                    "Corner Bakery Group",
                    [
                        ("1080p Webcam", "3", "79.00", "237.00"),
                        ("Monitor Light Bar", "1", "119.00", "119.00"),
                    ],
                    "356.00",
                )
            ]
        ),
        # A price list: a table and nothing else.
        "price-list.pdf": pdf(
            [
                [
                    (50, 790, "Price changes", 16),
                    (50, 750, "SKU", 10),
                    (200, 750, "New Price", 10),
                    (50, 734, "PL-DESK-1001", 10),
                    (200, 734, "762.00", 10),
                    (50, 718, "PL-CHR-2001", 10),
                    (200, 718, "342.00", 10),
                    (50, 702, "PL-MON-3001", 10),
                    (200, 702, "442.00", 10),
                ]
            ]
        ),
        # A scan: a page with nothing on it but (in real life) a picture.
        "scanned.pdf": pdf([[]]),
        # A letter: text, but no labels and no table.
        "letter.pdf": pdf(
            [
                [
                    (50, 790, "Dear customer,", 11),
                    (
                        50,
                        770,
                        "we are writing to let you know that our prices change next month",
                        11,
                    ),
                    (50, 755, "and that our office will be closed on the first of November.", 11),
                    (50, 735, "Kind regards", 11),
                ]
            ]
        ),
    }
    for name, content in files.items():
        (HERE / name).write_bytes(content)
        print(name, len(content))


if __name__ == "__main__":
    main()
