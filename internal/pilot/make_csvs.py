"""Write the pilot test files: clean and messy CSVs for quotes, leads and invoices, and 500 leads.

Every customer, product and salesperson is one `infra/test-apps/seed_odoo.py` creates, except
the rows that are wrong on purpose (their `expected` column says so).

    python internal/pilot/make_csvs.py            # writes into internal/pilot/files/
    python internal/pilot/make_csvs.py --tag r2   # fresh references and emails for a second round

The tag goes into every reference and email, so a second round is not skipped as
"already committed in an earlier run".
"""

from __future__ import annotations

import argparse
import csv
import io
import random
from pathlib import Path

import openpyxl

OUT = Path(__file__).resolve().parent / "files"

CUSTOMERS = [
    "Ridgeline Outfitters",
    "Harbourview Dental",
    "Pinewood Preschool",
    "Corner Bakery Group",
    "Summit Climbing Gym",
]
PRODUCTS = [
    ("Oak Standing Desk", 749.0),
    ("Mesh Task Chair", 329.0),
    ("Executive Leather Chair", 899.0),
    ("Drafting Stool", 189.0),
    ("USB-C Dock 10-in-1", 199.0),
    ("Thunderbolt Dock", 349.0),
    ("Compact Mechanical Keyboard", 129.0),
    ("Ergonomic Split Keyboard", 179.0),
    ("Vertical Ergonomic Mouse", 89.0),
    ("Wireless Trackball", 109.0),
    ("1080p Webcam", 79.0),
    ("Noise-Cancelling Headset", 259.0),
    ("USB Conference Speaker", 189.0),
    ("Monitor Light Bar", 119.0),
    ("Standing Desk Power Bar", 99.0),
]
SALESPEOPLE = ["Priya Raman", "Marcus Bell", "Sofia Duarte"]
# A lead Odoo already holds (seed_data.ODOO_LEADS).
EXISTING_LEAD = ("Dana Whitfield", "dana.whitfield@northwind-analytics.test")

FIRST = [
    "Alex",
    "Sam",
    "Jordan",
    "Taylor",
    "Morgan",
    "Riley",
    "Casey",
    "Jamie",
    "Avery",
    "Quinn",
    "Rowan",
    "Harper",
    "Emerson",
    "Finley",
    "Hayden",
    "Kendall",
    "Logan",
    "Parker",
    "Reese",
    "Sage",
]
LAST = [
    "Nguyen",
    "Okafor",
    "Lindqvist",
    "Moreau",
    "Tanaka",
    "Haddad",
    "Kowalski",
    "Fernandes",
    "Brennan",
    "Achebe",
    "Novak",
    "Castillo",
    "Ivanova",
    "Mensah",
    "Bauer",
    "Sato",
    "Kaur",
    "Romero",
    "Whitaker",
    "Duval",
]
COMPANY_A = [
    "Northgate",
    "Bluewater",
    "Ironbark",
    "Silverline",
    "Redwood",
    "Clearpoint",
    "Highfield",
    "Stonebridge",
    "Brightpath",
    "Oakmont",
]
COMPANY_B = [
    "Logistics",
    "Analytics",
    "Foods",
    "Robotics",
    "Dental",
    "Studios",
    "Supply",
    "Consulting",
    "Freight",
    "Labs",
]


def write_csv(
    name: str,
    header: list[str],
    rows: list[list[str]],
    *,
    encoding: str,
    title: str | None = None,
    blank_every: int = 0,
) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    if title:
        writer.writerow([title])
        writer.writerow([])
    writer.writerow(header)
    for index, row in enumerate(rows, start=1):
        writer.writerow(row)
        if blank_every and index % blank_every == 0:
            writer.writerow([])
    (OUT / name).write_bytes(buffer.getvalue().encode(encoding))


def clean_quotes(tag: str) -> list[list[str]]:
    rows = []
    for i in range(10):
        product, _ = PRODUCTS[i]
        rows.append([f"Q-{tag}-{i + 1:03d}", CUSTOMERS[i % 5], product, str(i % 4 + 1), "verified"])
    return rows


def messy_quotes(tag: str) -> list[list[str]]:
    ref = lambda n: f"Q-{tag}-M{n:02d}"  # noqa: E731
    return [
        [ref(1), "Ridgeline Outfitters", "Oak Standing Desk", "2", "verified (control row)"],
        [ref(2), "  Harbourview Dental  ", "Mesh Task Chair ", "1", "verified: spaces trimmed"],
        [ref(3), "pinewood preschool", "drafting stool", "3", "verified: lower case still matches"],
        [
            ref(4),
            "Ridgline Outfiters",
            "Thunderbolt Dock",
            "1",
            "must NOT be verified: customer misspelled; no other customer, no new one",
        ],
        [
            ref(5),
            "Acme Widgets Ltd",
            "Wireless Trackball",
            "2",
            "must NOT be verified: customer does not exist; must not 'Create Acme Widgets Ltd'",
        ],
        [
            ref(6),
            "Corner Bakery Group",
            "Standing Desk",
            "1",
            "must NOT silently pick Oak or Birch: 'Standing Desk' matches two products",
        ],
        [
            ref(7),
            "Summit Climbing Gym",
            '27" 4K Monitor',
            "2",
            "verified: product name contains a quote mark",
        ],
        [
            ref(8),
            "Harbourview Dental",
            "PL-KEY-5001",
            "1",
            "verified as Compact Mechanical Keyboard: searched by product code",
        ],
        [ref(9), "Pinewood Preschool", "1080p Webcam", "3.0", "verified with quantity 3"],
        [
            ref(10),
            "Corner Bakery Group",
            "Monitor Light Bar",
            "",
            "flagged in review or parked: quantity empty",
        ],
        [
            ref(11),
            "Summit Climbing Gym",
            "USB Conference Speaker",
            "two",
            "flagged or fails: quantity is a word",
        ],
        [
            ref(12),
            "Ridgeline Outfitters",
            "Drafting Stool",
            "-1",
            "flagged or fails: negative quantity",
        ],
        [
            ref(13),
            "Harbourview Dental",
            "Ergonomic Split Keyboard",
            "1,000",
            "verified with quantity 1000, or flagged; must not become 1",
        ],
        [
            ref(13),
            "Harbourview Dental",
            "Ergonomic Split Keyboard",
            "1,000",
            "flagged as a duplicate of the row above (same reference)",
        ],
        [
            ref(14),
            "Pinewood Preschool",
            "Mesh Task Chiar",
            "1",
            "must NOT be verified: product misspelled",
        ],
        [
            "",
            "Summit Climbing Gym",
            "Oak Standing Desk",
            "1",
            "flagged: no reference (record key empty)",
        ],
        [ref(15), "Corner Bakery Group", "Noise-Cancelling Headset", "4", "verified (control row)"],
    ]


def clean_invoices(tag: str) -> list[list[str]]:
    rows = []
    for i in range(10):
        product, price = PRODUCTS[(i + 5) % len(PRODUCTS)]
        rows.append(
            [
                f"INV-{tag}-{i + 1:03d}",
                CUSTOMERS[i % 5],
                product,
                str(i % 3 + 1),
                f"{price:.2f}",
                "verified",
            ]
        )
    return rows


def messy_invoices(tag: str) -> list[list[object]]:
    """Rows for the .xlsx: numbers stay numbers where Excel would keep them as numbers."""
    ref = lambda n: f"INV-{tag}-M{n:02d}"  # noqa: E731
    return [
        [ref(1), "Ridgeline Outfitters", "Thunderbolt Dock", 2, 349, "verified (control row)"],
        [
            ref(2),
            "Harbourview Dental",
            "Wireless Trackball",
            1,
            "$109.00",
            "verified at 109.00, or flagged; must not become 0 or 10900",
        ],
        [
            ref(3),
            "Pinewood Preschool",
            "Executive Leather Chair",
            1,
            "1,299.00",
            "verified at 1299.00; must not become 1.299 or 1",
        ],
        [
            ref(4),
            "Corner Bakery Group",
            "1080p Webcam",
            3,
            "€79,50",
            "flagged, or verified at 79.50; must not become 7950",
        ],
        [
            ref(5),
            "Summit Climbing Gym",
            "Monitor Light Bar",
            2,
            None,
            "price empty: note whether it is flagged or keeps Odoo's list price",
        ],
        [ref(6), "Ridgeline Outfitters", "Drafting Stool", 1, 0, "verified at 0.00 (free item)"],
        [
            ref(7),
            "Harbourview Dental",
            "USB-C Dock 10-in-1",
            2.5,
            199,
            "quantity 2.5: note what Odoo stores",
        ],
        [
            ref(8),
            "Pinewood Preschool",
            "Vertical Ergonomic Mouse",
            4,
            89.999,
            "verified; price rounds to 90.00",
        ],
        [
            ref(9),
            "Acme Widgets Ltd",
            "Wireless Trackball",
            1,
            109,
            "must NOT be verified: customer does not exist",
        ],
        [
            ref(10),
            "Corner Bakery Group",
            "Desk Lamp",
            1,
            149,
            "must NOT silently pick a product: 'Desk Lamp' is a partial name",
        ],
        [
            ref(11),
            "Summit Climbing Gym",
            "Noise-Cancelling Headset",
            1,
            259,
            "verified (control row)",
        ],
    ]


def leads_500(tag: str, count: int = 500) -> list[list[str]]:
    rng = random.Random(f"leads-{tag}")
    rows = []
    for i in range(count):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        company = f"{rng.choice(COMPANY_A)} {rng.choice(COMPANY_B)}"
        email = f"{first}.{last}.{tag}.{i + 1:03d}@pilot-leads.test".lower()
        phone = f"+1 555 {rng.randint(200, 999)} {rng.randint(1000, 9999)}"
        rows.append([f"{first} {last}", email, phone, company, SALESPEOPLE[i % 3]])
    return rows


def messy_leads(tag: str) -> list[list[str]]:
    mail = lambda n: f"messy.{tag}.{n:02d}@pilot-leads.test"  # noqa: E731
    long_name = "Maximilian " + "Alexander-" * 18 + "Hohenberg"
    return [
        [
            "Ana Ruiz",
            mail(1),
            "+34 91 555 0101",
            "Ruiz Hermanos",
            "Priya Raman",
            "verified (control row)",
        ],
        [
            "  Ben Carter ",
            f"  {mail(2)} ",
            "+1 555 0102",
            " Carter Freight ",
            "Marcus Bell",
            "verified: spaces trimmed everywhere",
        ],
        [
            "José Núñez",
            mail(3),
            "+52 55 5555 0103",
            "Café Olé S.A.",
            "Sofia Duarte",
            "verified: accents survive (file is saved the old Excel way, Windows-1252)",
        ],
        [
            "Zoë Ångström",
            mail(4),
            "+46 8 555 0104",
            "Smith, Jones & Co",
            "Priya Raman",
            "verified: comma inside the company name",
        ],
        [
            'Chris "CJ" Jones',
            mail(5),
            "+1 555 0105",
            "Jones Labs",
            "Marcus Bell",
            "verified: quote marks inside the name",
        ],
        [
            "Dee Patel",
            mail(6),
            "4.47911E+11",
            "Patel Foods",
            "Sofia Duarte",
            "phone Excel turned into 4.47911E+11: note what gets typed",
        ],
        [
            "Eli Moss",
            mail(7),
            "02079460958",
            "Moss & Daughters",
            "Priya Raman",
            "verified: leading zero in phone kept",
        ],
        [
            "Fay Grant",
            "",
            "+1 555 0108",
            "Grant Studio",
            "Marcus Bell",
            "flagged or parked: no email",
        ],
        [
            "Gus Hale",
            "gus.hale.at.example",
            "+1 555 0109",
            "Hale Supply",
            "Sofia Duarte",
            "flagged: not an email",
        ],
        [
            "Ana Ruiz again",
            mail(1).upper(),
            "+34 91 555 0110",
            "Ruiz Hermanos",
            "Priya Raman",
            "flagged as a duplicate of row 1 (same email, different case)",
        ],
        [
            EXISTING_LEAD[0],
            EXISTING_LEAD[1],
            "+1 202 555 0141",
            "Northwind Analytics",
            "Marcus Bell",
            "Odoo already has this lead: note if a second copy is created",
        ],
        [
            "Hana Kim",
            mail(12),
            "+82 2 555 0112",
            "Kim Robotics",
            "Priya Ramen",
            "must NOT be verified: salesperson misspelled; must not pick someone else",
        ],
        [
            "Ivo Petrov",
            mail(13),
            "+359 2 555 0113",
            "Petrov Freight",
            "John Smith",
            "must NOT be verified: salesperson does not exist",
        ],
        [
            "Jo Lee",
            mail(14),
            "+1 555 0114",
            "Lee Consulting",
            "",
            "salesperson empty: note what happens",
        ],
        [
            long_name,
            mail(15),
            "+49 30 555 0115",
            "Hohenberg GmbH",
            "Sofia Duarte",
            "verified: very long name typed in full",
        ],
        [
            "Kai Line\nBreak",
            mail(16),
            "+1 555 0116",
            "Break Media",
            "Priya Raman",
            "name has a line break inside the cell: note what gets typed",
        ],
        [
            "Lou Reed",
            mail(17),
            "+1 555 0117",
            "Reed Analytics",
            "Marcus Bell",
            "verified (control row)",
        ],
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="r1", help="goes into every reference and email")
    tag = parser.parse_args().tag
    OUT.mkdir(parents=True, exist_ok=True)

    quote_header = ["reference", "customer", "product", "quantity", "expected"]
    write_csv("quotes-clean.csv", quote_header, clean_quotes(tag), encoding="utf-8")
    # Excel's "CSV UTF-8": a byte-order mark, a title row above the header, blank rows between.
    write_csv(
        "quotes-messy.csv",
        [" Reference", "Customer ", "PRODUCT", "Qty", "expected"],
        messy_quotes(tag),
        encoding="utf-8-sig",
        title="Quotes to enter - October",
        blank_every=5,
    )

    invoice_header = ["reference", "customer", "product", "quantity", "unit price", "expected"]
    write_csv("invoices-clean.csv", invoice_header, clean_invoices(tag), encoding="utf-8")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Invoices"
    sheet.append(["Invoices to raise"])
    sheet.append([])
    sheet.append(["Reference", "Customer", "Product", "Quantity", "Unit Price", "expected"])
    for row in messy_invoices(tag):
        sheet.append(row)
    for cell in sheet["E"][3:]:
        if isinstance(cell.value, (int, float)):
            cell.number_format = '"$"#,##0.00'
    book.save(OUT / "invoices-messy.xlsx")

    lead_header = ["name", "email", "phone", "company", "salesperson"]
    write_csv("leads-500.csv", lead_header, leads_500(tag), encoding="utf-8")
    write_csv("leads-20-warmup.csv", lead_header, leads_500(f"{tag}w", 20), encoding="utf-8")
    # The old Excel "CSV (Comma delimited)": Windows-1252, semicolons as a European Excel writes.
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Full Name", "E-mail", "Phone", "Company", "Sales Person", "expected"])
    writer.writerows(messy_leads(tag))
    (OUT / "leads-messy.csv").write_bytes(buffer.getvalue().encode("cp1252"))

    for path in sorted(OUT.iterdir()):
        print(path.name, path.stat().st_size)


if __name__ == "__main__":
    main()
