"""Write the pilot test files for use cases 1, 3 and 4: leads, invoices and product updates.

Every customer, product and salesperson is one `infra/test-apps/seed_odoo.py` creates, except
the rows that are wrong on purpose (their `expected` column says so).

    python internal/pilot/make_csvs.py            # writes into internal/pilot/files/
    python internal/pilot/make_csvs.py --tag r2   # a second round: new refs, emails, prices

The tag goes into every reference and email and moves every new price, so a second round is not
skipped as "already committed in an earlier run".
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


# Every seeded product's internal reference and list price (seed_data.ODOO_PRODUCTS).
SKUS = [
    ("PL-DESK-1001", 749.0),
    ("PL-DESK-1002", 689.0),
    ("PL-CHR-2001", 329.0),
    ("PL-CHR-2002", 899.0),
    ("PL-CHR-2003", 189.0),
    ("PL-MON-3001", 429.0),
    ("PL-MON-3002", 799.0),
    ("PL-DOC-4001", 199.0),
    ("PL-DOC-4002", 349.0),
    ("PL-KEY-5001", 129.0),
    ("PL-KEY-5002", 179.0),
    ("PL-MOU-6001", 89.0),
    ("PL-MOU-6002", 109.0),
    ("PL-CAM-7001", 79.0),
    ("PL-CAM-7002", 219.0),
    ("PL-AUD-8001", 259.0),
    ("PL-AUD-8002", 189.0),
    ("PL-LGT-9001", 119.0),
    ("PL-LGT-9002", 149.0),
    ("PL-PWR-9500", 99.0),
]


def _bump(tag: str) -> int:
    """A price rise that differs per round, so a new round's prices are new work, not repeats."""
    return 10 + sum(map(ord, tag)) % 40


def clean_products(tag: str) -> list[list[str]]:
    bump = _bump(tag)
    return [[sku, f"{price + bump:.2f}", "verified"] for sku, price in SKUS[:10]]


def messy_products(tag: str) -> list[list[str]]:
    bump = _bump(tag)
    price = dict(SKUS)

    def new(sku: str) -> str:
        return f"{price[sku] + bump:.2f}"

    return [
        ["PL-MON-3002", new("PL-MON-3002"), "verified (control row)"],
        ["  PL-DOC-4002 ", f" {new('PL-DOC-4002')} ", "verified: spaces trimmed"],
        ["pl-key-5002", new("PL-KEY-5002"), "verified: lower-case sku still finds the product"],
        ["PL-XXX-0000", "99.00", "parked: no product has this sku (search finds nothing)"],
        ["PL-MOU-600", "95.00", "parked: sku is cut short; must NOT update PL-MOU-6001 or 6002"],
        [
            "PL-CAM-7001",
            f"${price['PL-CAM-7001'] + bump:,.2f}",
            "verified at the number, or flagged; must not become 0",
        ],
        [
            "PL-CAM-7002",
            f"{price['PL-CAM-7002'] + bump + 1000:,.2f}",
            "verified at the full number (thousands comma); must not become 1.2 or 1",
        ],
        ["PL-AUD-8001", "", "flagged or parked: price empty; price must stay unchanged"],
        ["PL-AUD-8002", "free", "flagged or fails: price is a word; price must stay unchanged"],
        ["PL-LGT-9001", "-5.00", "flagged or fails: negative price"],
        [
            "PL-LGT-9002",
            f"{price['PL-LGT-9002']:.2f}",
            "skipped: Odoo already has this price, nothing to save",
        ],
        ["PL-PWR-9500", new("PL-PWR-9500"), "verified"],
        ["PL-PWR-9500", new("PL-PWR-9500"), "flagged as a duplicate of the row above"],
        [
            "PL-DESK-1001",
            f"{price['PL-DESK-1001'] + bump:.3f}",
            "verified; price rounds to 2 places",
        ],
        ["", "50.00", "flagged: no sku"],
        ["PL-CHR-2003", new("PL-CHR-2003"), "verified (control row)"],
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

    product_header = ["sku", "new_price", "expected"]
    write_csv("products-clean.csv", product_header, clean_products(tag), encoding="utf-8")
    # Excel's "CSV UTF-8": a byte-order mark, a title row above the header, blank rows between.
    write_csv(
        "products-messy.csv",
        [" SKU", "New Price ", "expected"],
        messy_products(tag),
        encoding="utf-8-sig",
        title="Price changes - November",
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
