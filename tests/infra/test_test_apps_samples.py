"""Shape checks for the sample CSVs that ship with the test apps.

`infra/test-apps/seed_data.py` is the single source of truth for what the Odoo and
Twenty seeders create, and the files in `infra/test-apps/samples/` are the
spreadsheets a person later feeds to ProcessLens against those apps. These tests
compare the committed CSVs with the seed data only: no running app, no database,
no network.

The directory name `test-apps` is not a valid package name, so the seed module is
loaded from its file path instead of being imported.
"""

from __future__ import annotations

import csv
import importlib.util
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_DATA_PATH = REPO_ROOT / "infra" / "test-apps" / "seed_data.py"
SAMPLES_DIR = REPO_ROOT / "infra" / "test-apps" / "samples"

LEADS_CSV = "usecase-1-leads.csv"
PRODUCTS_CSV = "usecase-4-products.csv"
ENROLLMENT_CSV = "usecase-6-enrollment.csv"
TWENTY_LEADS_CSV = "twenty-leads.csv"

LEADS_HEADER = ["first_name", "last_name", "email", "company", "phone", "source"]
PRODUCTS_HEADER = ["default_code", "name", "new_price"]
ENROLLMENT_HEADER = ["name", "email", "phone", "tags"]
TWENTY_HEADER = ["first_name", "last_name", "email", "company_name", "job_title", "phone"]

# (filename, header) pairs swept for non-synthetic email addresses.
CSV_EMAIL_FILES = [
    (LEADS_CSV, LEADS_HEADER),
    (ENROLLMENT_CSV, ENROLLMENT_HEADER),
    (TWENTY_LEADS_CSV, TWENTY_HEADER),
]


def _load_seed_data() -> types.ModuleType:
    if not SEED_DATA_PATH.is_file():
        raise AssertionError(f"seed data module is missing at {SEED_DATA_PATH}")
    spec = importlib.util.spec_from_file_location("pl_test_apps_seed_data", SEED_DATA_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"no import spec for {SEED_DATA_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEED = _load_seed_data()


def _read_rows(filename: str, header: list[str]) -> list[dict[str, str]]:
    """Read one committed sample CSV, checking the header before the rows."""
    path = SAMPLES_DIR / filename
    if not path.is_file():
        raise AssertionError(f"sample CSV is missing at {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        found = reader.fieldnames
        if found != header:
            extra = sorted(set(found or []) - set(header))
            missing = sorted(set(header) - set(found or []))
            detail = (
                f"{filename} header {found}, expected {header}; extra {extra}, missing {missing}"
            )
            raise AssertionError(detail)
        return list(reader)


def _assert_row_count(filename: str, rows: list[dict[str, str]], expected: int) -> None:
    actual = len(rows)
    drift = actual - expected
    detail = f"{filename} has {actual} data rows, expected {expected}, off by {drift:+d}"
    assert actual == expected, detail


def _assert_no_problems(filename: str, problems: list[str]) -> None:
    detail = f"{filename} problems: {'; '.join(problems)}"
    assert not problems, detail


def _seeded_name(seeded: dict[str, str]) -> tuple[str, str]:
    if "first_name" in seeded:
        return (seeded["first_name"], seeded["last_name"])
    return tuple(seeded["name"].split(" ", 1))


def _assert_duplicate_rows(
    filename: str,
    rows: list[dict[str, str]],
    seeded_by_email: dict[str, dict[str, str]],
    expected: int,
) -> None:
    """Rows meant to be duplicates must repeat the seeded person exactly."""
    duplicates = [row for row in rows if row["email"] in seeded_by_email]
    matched = [row["email"] for row in duplicates]
    actual = len(duplicates)
    detail = f"{filename}: {actual} rows match a seeded email, expected {expected}"
    detail += f"; matched {matched}"
    assert actual == expected, detail
    bad = []
    for row in duplicates:
        seeded = seeded_by_email[row["email"]]
        expected_name = _seeded_name(seeded)
        actual_name = (row["first_name"], row["last_name"])
        if actual_name != expected_name:
            bad.append(f"{row['email']} has name {actual_name}, seeded name {expected_name}")
    bad_detail = f"{filename} duplicate rows must reuse the seeded name: {'; '.join(bad)}"
    assert not bad, bad_detail


def test_usecase_1_leads_csv_row_count() -> None:
    rows = _read_rows(LEADS_CSV, LEADS_HEADER)
    _assert_row_count(LEADS_CSV, rows, SEED.LEADS_CSV_ROWS)


def test_usecase_1_leads_csv_duplicate_emails_match_seeded_leads() -> None:
    rows = _read_rows(LEADS_CSV, LEADS_HEADER)
    seeded_by_email = {lead["email"]: lead for lead in SEED.ODOO_LEADS}
    _assert_duplicate_rows(LEADS_CSV, rows, seeded_by_email, SEED.LEADS_CSV_DUPLICATES)


def test_usecase_1_leads_csv_has_exactly_two_rows_without_email() -> None:
    rows = _read_rows(LEADS_CSV, LEADS_HEADER)
    empty = [row for row in rows if not row["email"].strip()]
    names = [(row["first_name"], row["last_name"]) for row in empty]
    expected = SEED.LEADS_CSV_MISSING_EMAIL
    drift = len(empty) - expected
    detail = f"{LEADS_CSV} has {len(empty)} rows with an empty email, expected {expected} "
    detail += f"(off by {drift:+d}); rows without email: {names}"
    assert len(empty) == expected, detail


def test_usecase_1_leads_csv_new_rows_look_like_enquiries() -> None:
    """Every row that is not a duplicate is a plausible enquiry with a source."""
    rows = _read_rows(LEADS_CSV, LEADS_HEADER)
    seeded_emails = {lead["email"] for lead in SEED.ODOO_LEADS}
    problems = []
    for line_no, row in enumerate(rows, start=2):
        is_new = bool(row["email"].strip()) and row["email"] not in seeded_emails
        has_name = bool(row["first_name"].strip()) and bool(row["last_name"].strip())
        if is_new and not has_name:
            problems.append(f"line {line_no}: new row without first and last name")
        if not row["source"].strip():
            problems.append(f"line {line_no}: empty source")
    _assert_no_problems(LEADS_CSV, problems)


def test_usecase_4_products_csv_row_count_and_single_unknown_code() -> None:
    rows = _read_rows(PRODUCTS_CSV, PRODUCTS_HEADER)
    _assert_row_count(PRODUCTS_CSV, rows, SEED.PRODUCTS_CSV_ROWS)
    known_codes = {product["default_code"] for product in SEED.ODOO_PRODUCTS}
    unknown = [row for row in rows if row["default_code"] not in known_codes]
    codes = [row["default_code"] for row in unknown]
    expected = SEED.PRODUCTS_CSV_UNKNOWN_CODES
    drift = len(unknown) - expected
    detail = f"{PRODUCTS_CSV} has {len(unknown)} unknown default_codes, expected {expected} "
    detail += f"(off by {drift:+d}); unknown codes: {codes}"
    assert len(unknown) == expected, detail
    expected_known = SEED.PRODUCTS_CSV_ROWS - expected
    known = len(rows) - len(unknown)
    drift = known - expected_known
    detail = f"{PRODUCTS_CSV} has {known} known-product rows, expected {expected_known}"
    detail += f" (off by {drift:+d})"
    assert known == expected_known, detail


def test_usecase_4_products_csv_known_rows_rename_and_repricing() -> None:
    """Known codes keep the seeded name and carry a price that is a real change."""
    rows = _read_rows(PRODUCTS_CSV, PRODUCTS_HEADER)
    seeded_by_code = {product["default_code"]: product for product in SEED.ODOO_PRODUCTS}
    problems = []
    for line_no, row in enumerate(rows, start=2):
        product = seeded_by_code.get(row["default_code"])
        if product is None:
            continue
        seeded_name = product["name"]
        if row["name"] != seeded_name:
            problems.append(f"line {line_no}: name {row['name']!r}, seeded {seeded_name!r}")
        raw_price = row["new_price"]
        try:
            new_price = float(raw_price)
        except ValueError:
            problems.append(f"line {line_no}: new_price {raw_price!r} is not a number")
            continue
        list_price = product["list_price"]
        if new_price == list_price:
            problems.append(f"line {line_no}: new_price {new_price} equals list_price {list_price}")
    _assert_no_problems(PRODUCTS_CSV, problems)


def test_usecase_6_enrollment_csv_row_count_and_required_fields() -> None:
    rows = _read_rows(ENROLLMENT_CSV, ENROLLMENT_HEADER)
    _assert_row_count(ENROLLMENT_CSV, rows, SEED.ENROLLMENT_CSV_ROWS)
    allowed = set(SEED.ODOO_ENROLLMENT_TAGS)
    problems = []
    for line_no, row in enumerate(rows, start=2):
        if not row["name"].strip():
            problems.append(f"line {line_no}: empty name")
        if not row["email"].strip():
            problems.append(f"line {line_no}: empty email")
        tags = [tag.strip() for tag in row["tags"].split(";") if tag.strip()]
        if not tags:
            problems.append(f"line {line_no}: no tags")
        outside = [tag for tag in tags if tag not in allowed]
        if outside:
            problems.append(f"line {line_no}: tags {outside} outside ODOO_ENROLLMENT_TAGS")
    _assert_no_problems(ENROLLMENT_CSV, problems)


def test_usecase_6_enrollment_csv_reuses_some_seeded_contacts() -> None:
    """Some rows must already exist in Odoo, so the run has to find not create."""
    rows = _read_rows(ENROLLMENT_CSV, ENROLLMENT_HEADER)
    seeded_emails = {contact["email"] for contact in SEED.ODOO_ENROLLMENT_CONTACTS}
    existing = [row["email"] for row in rows if row["email"] in seeded_emails]
    csv_emails = [row["email"] for row in rows]
    detail = f"{ENROLLMENT_CSV} matches none of the {len(seeded_emails)} seeded contacts; "
    detail += f"CSV emails: {csv_emails}"
    assert existing, detail


def test_twenty_leads_csv_row_count_and_duplicate_emails() -> None:
    rows = _read_rows(TWENTY_LEADS_CSV, TWENTY_HEADER)
    _assert_row_count(TWENTY_LEADS_CSV, rows, SEED.TWENTY_LEADS_CSV_ROWS)
    seeded_by_email = {person["email"]: person for person in SEED.TWENTY_PEOPLE}
    expected = SEED.TWENTY_LEADS_CSV_DUPLICATES
    _assert_duplicate_rows(TWENTY_LEADS_CSV, rows, seeded_by_email, expected)


def test_twenty_leads_csv_new_rows_have_company_and_title() -> None:
    rows = _read_rows(TWENTY_LEADS_CSV, TWENTY_HEADER)
    seeded_emails = {person["email"] for person in SEED.TWENTY_PEOPLE}
    problems = []
    for line_no, row in enumerate(rows, start=2):
        is_duplicate = row["email"] in seeded_emails
        if is_duplicate:
            continue
        if not row["company_name"].strip():
            problems.append(f"line {line_no}: new person without a company_name")
        if not row["job_title"].strip():
            problems.append(f"line {line_no}: new person without a job_title")
    _assert_no_problems(TWENTY_LEADS_CSV, problems)


def test_sample_csvs_only_use_synthetic_test_domains() -> None:
    offenders = []
    for filename, header in CSV_EMAIL_FILES:
        for line_no, row in enumerate(_read_rows(filename, header), start=2):
            email = row["email"].strip()
            if email and not email.endswith(".test"):
                offenders.append(f"{filename} line {line_no}: {email} is not a .test address")
    detail = f"sample CSVs must use only synthetic .test addresses: {'; '.join(offenders)}"
    assert not offenders, detail
