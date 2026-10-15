"""The one source of truth for what the test apps contain.

`seed_odoo.py` and `seed_twenty.py` build their records from this module, and the
sample CSVs in `samples/` are written to line up with it: use-case 1 rows that
duplicate a seeded lead are only real duplicates if they carry the same email as
`ODOO_LEADS`, and a "duplicate" Twenty row means the same as `TWENTY_PEOPLE`.
`tests/infra/test_test_apps_samples.py` checks that relationship.

Everything here is synthetic. Addresses use `example.test` / `processlens.test`,
which resolve nowhere, and no value is a credential for anything real.
"""

# --------------------------------------------------------------------- Odoo ---
# The three salespeople seeded into Odoo. `login` is the Odoo login, and they all
# share `ODOO_SALES_PASSWORD` from the env file.
ODOO_SALESPEOPLE = [
    {"name": "Priya Raman", "login": "sales1@processlens.test", "email": "sales1@processlens.test"},
    {"name": "Marcus Bell", "login": "sales2@processlens.test", "email": "sales2@processlens.test"},
    {
        "name": "Sofia Duarte",
        "login": "sales3@processlens.test",
        "email": "sales3@processlens.test",
    },
]

# Ten existing leads, so a duplicate check has something to find.
ODOO_LEADS = [
    {
        "name": "Dana Whitfield",
        "company": "Northwind Analytics",
        "email": "dana.whitfield@northwind-analytics.test",
        "phone": "+1 202 555 0141",
        "source": "web",
    },
    {
        "name": "Tomas Ferreira",
        "company": "Blue Harbour Logistics",
        "email": "tomas.ferreira@blueharbour.test",
        "phone": "+1 202 555 0142",
        "source": "web",
    },
    {
        "name": "Aiko Tanaka",
        "company": "Kanmon Robotics",
        "email": "aiko.tanaka@kanmon-robotics.test",
        "phone": "+81 3 5555 0143",
        "source": "referral",
    },
    {
        "name": "Grace Okafor",
        "company": "Mile High Catering",
        "email": "grace.okafor@milehigh-catering.test",
        "phone": "+1 303 555 0144",
        "source": "trade show",
    },
    {
        "name": "Lars Nyberg",
        "company": "Fjordline Ferries",
        "email": "lars.nyberg@fjordline.test",
        "phone": "+46 8 555 0145",
        "source": "web",
    },
    {
        "name": "Meera Shah",
        "company": "Sunrise Solar",
        "email": "meera.shah@sunrisesolar.test",
        "phone": "+1 480 555 0146",
        "source": "cold call",
    },
    {
        "name": "Bruno Costa",
        "company": "Pampa Textiles",
        "email": "bruno.costa@pampatextiles.test",
        "phone": "+55 11 5555 0147",
        "source": "referral",
    },
    {
        "name": "Hannah Lindqvist",
        "company": "Nordic Brew Works",
        "email": "hannah.lindqvist@nordicbrew.test",
        "phone": "+46 31 555 0148",
        "source": "trade show",
    },
    {
        "name": "Omar Haddad",
        "company": "Cedar & Stone Interiors",
        "email": "omar.haddad@cedarstone.test",
        "phone": "+971 4 555 0149",
        "source": "web",
    },
    {
        "name": "Elena Petrova",
        "company": "Volga Precision",
        "email": "elena.petrova@volgaprecision.test",
        "phone": "+7 495 555 0150",
        "source": "cold call",
    },
]

# Twenty products with codes, prices and stock. `standard_price` is the cost.
ODOO_PRODUCTS = [
    {
        "default_code": "PL-DESK-1001",
        "name": "Oak Standing Desk",
        "list_price": 749.0,
        "standard_price": 455.0,
        "qty_available": 18,
    },
    {
        "default_code": "PL-DESK-1002",
        "name": "Birch Standing Desk",
        "list_price": 689.0,
        "standard_price": 410.0,
        "qty_available": 12,
    },
    {
        "default_code": "PL-CHR-2001",
        "name": "Mesh Task Chair",
        "list_price": 329.0,
        "standard_price": 188.0,
        "qty_available": 40,
    },
    {
        "default_code": "PL-CHR-2002",
        "name": "Executive Leather Chair",
        "list_price": 899.0,
        "standard_price": 540.0,
        "qty_available": 7,
    },
    {
        "default_code": "PL-CHR-2003",
        "name": "Drafting Stool",
        "list_price": 189.0,
        "standard_price": 104.0,
        "qty_available": 25,
    },
    {
        "default_code": "PL-MON-3001",
        "name": '27" 4K Monitor',
        "list_price": 429.0,
        "standard_price": 288.0,
        "qty_available": 33,
    },
    {
        "default_code": "PL-MON-3002",
        "name": '34" Ultrawide Monitor',
        "list_price": 799.0,
        "standard_price": 512.0,
        "qty_available": 9,
    },
    {
        "default_code": "PL-DOC-4001",
        "name": "USB-C Dock 10-in-1",
        "list_price": 199.0,
        "standard_price": 96.0,
        "qty_available": 54,
    },
    {
        "default_code": "PL-DOC-4002",
        "name": "Thunderbolt Dock",
        "list_price": 349.0,
        "standard_price": 205.0,
        "qty_available": 15,
    },
    {
        "default_code": "PL-KEY-5001",
        "name": "Compact Mechanical Keyboard",
        "list_price": 129.0,
        "standard_price": 68.0,
        "qty_available": 61,
    },
    {
        "default_code": "PL-KEY-5002",
        "name": "Ergonomic Split Keyboard",
        "list_price": 179.0,
        "standard_price": 98.0,
        "qty_available": 22,
    },
    {
        "default_code": "PL-MOU-6001",
        "name": "Vertical Ergonomic Mouse",
        "list_price": 89.0,
        "standard_price": 41.0,
        "qty_available": 48,
    },
    {
        "default_code": "PL-MOU-6002",
        "name": "Wireless Trackball",
        "list_price": 109.0,
        "standard_price": 57.0,
        "qty_available": 30,
    },
    {
        "default_code": "PL-CAM-7001",
        "name": "1080p Webcam",
        "list_price": 79.0,
        "standard_price": 34.0,
        "qty_available": 44,
    },
    {
        "default_code": "PL-CAM-7002",
        "name": "4K Webcam with Ring Light",
        "list_price": 219.0,
        "standard_price": 118.0,
        "qty_available": 11,
    },
    {
        "default_code": "PL-AUD-8001",
        "name": "Noise-Cancelling Headset",
        "list_price": 259.0,
        "standard_price": 142.0,
        "qty_available": 27,
    },
    {
        "default_code": "PL-AUD-8002",
        "name": "USB Conference Speaker",
        "list_price": 189.0,
        "standard_price": 101.0,
        "qty_available": 19,
    },
    {
        "default_code": "PL-LGT-9001",
        "name": "Monitor Light Bar",
        "list_price": 119.0,
        "standard_price": 55.0,
        "qty_available": 36,
    },
    {
        "default_code": "PL-LGT-9002",
        "name": "Desk Lamp with Qi Charger",
        "list_price": 149.0,
        "standard_price": 74.0,
        "qty_available": 21,
    },
    {
        "default_code": "PL-PWR-9500",
        "name": "Standing Desk Power Bar",
        "list_price": 99.0,
        "standard_price": 47.0,
        "qty_available": 58,
    },
]

# Five customers that each carry one open (posted) invoice.
ODOO_CUSTOMERS = [
    {
        "name": "Ridgeline Outfitters",
        "email": "ap@ridgeline-outfitters.test",
        "invoice_ref": "INV/2026/00041",
        "amount": 4280.0,
    },
    {
        "name": "Harbourview Dental",
        "email": "accounts@harbourview-dental.test",
        "invoice_ref": "INV/2026/00042",
        "amount": 1975.5,
    },
    {
        "name": "Pinewood Preschool",
        "email": "office@pinewood-preschool.test",
        "invoice_ref": "INV/2026/00043",
        "amount": 860.0,
    },
    {
        "name": "Corner Bakery Group",
        "email": "finance@cornerbakery.test",
        "invoice_ref": "INV/2026/00044",
        "amount": 3120.75,
    },
    {
        "name": "Summit Climbing Gym",
        "email": "admin@summit-climbing.test",
        "invoice_ref": "INV/2026/00045",
        "amount": 5640.0,
    },
]

# Use case 6 is enrollment-style: find or create a contact, then tag and enroll
# them. These are the contacts the seeder creates, with the tags it applies.
ODOO_ENROLLMENT_TAGS = [
    "Spring 2026 Cohort",
    "Autumn 2026 Cohort",
    "Beginner",
    "Advanced",
    "Waitlist",
]

ODOO_ENROLLMENT_CONTACTS = [
    {
        "name": "Ruth Adeyemi",
        "email": "ruth.adeyemi@example.test",
        "phone": "+1 415 555 0201",
        "tags": ["Spring 2026 Cohort", "Beginner"],
    },
    {
        "name": "Kenji Mori",
        "email": "kenji.mori@example.test",
        "phone": "+81 6 555 0202",
        "tags": ["Spring 2026 Cohort", "Advanced"],
    },
    {
        "name": "Clara Jensen",
        "email": "clara.jensen@example.test",
        "phone": "+45 33 555 0203",
        "tags": ["Autumn 2026 Cohort", "Beginner"],
    },
    {
        "name": "Diego Salazar",
        "email": "diego.salazar@example.test",
        "phone": "+52 55 5555 0204",
        "tags": ["Autumn 2026 Cohort", "Advanced"],
    },
    {
        "name": "Fatima Noor",
        "email": "fatima.noor@example.test",
        "phone": "+44 20 5555 0205",
        "tags": ["Spring 2026 Cohort", "Waitlist"],
    },
    {
        "name": "Peter Halvorsen",
        "email": "peter.halvorsen@example.test",
        "phone": "+47 22 555 0206",
        "tags": ["Beginner"],
    },
    {
        "name": "Anita Bakker",
        "email": "anita.bakker@example.test",
        "phone": "+31 20 555 0207",
        "tags": ["Advanced"],
    },
    {
        "name": "Joseph Mwangi",
        "email": "joseph.mwangi@example.test",
        "phone": "+254 20 555 0208",
        "tags": ["Waitlist"],
    },
]

# ------------------------------------------------------------------- Twenty ---
# Ten people and five companies in the Twenty workspace.
TWENTY_PEOPLE = [
    {
        "first_name": "Nadia",
        "last_name": "Fournier",
        "email": "nadia.fournier@brightpath.test",
        "job_title": "Head of Operations",
        "phone": "+1 617 555 0301",
    },
    {
        "first_name": "Samuel",
        "last_name": "Okonkwo",
        "email": "samuel.okonkwo@lagosfreight.test",
        "job_title": "Logistics Manager",
        "phone": "+234 1 555 0302",
    },
    {
        "first_name": "Ingrid",
        "last_name": "Solberg",
        "email": "ingrid.solberg@nordvind.test",
        "job_title": "Procurement Lead",
        "phone": "+47 21 555 0303",
    },
    {
        "first_name": "Ravi",
        "last_name": "Krishnan",
        "email": "ravi.krishnan@vedatech.test",
        "job_title": "CTO",
        "phone": "+91 80 5555 0304",
    },
    {
        "first_name": "Camille",
        "last_name": "Rousseau",
        "email": "camille.rousseau@atelier-midi.test",
        "job_title": "Design Director",
        "phone": "+33 1 5555 0305",
    },
    {
        "first_name": "Diego",
        "last_name": "Marchetti",
        "email": "diego.marchetti@bellaopera.test",
        "job_title": "Finance Manager",
        "phone": "+39 02 5555 0306",
    },
    {
        "first_name": "Yuki",
        "last_name": "Nakamura",
        "email": "yuki.nakamura@sakuradata.test",
        "job_title": "Data Scientist",
        "phone": "+81 3 5555 0307",
    },
    {
        "first_name": "Amara",
        "last_name": "Diallo",
        "email": "amara.diallo@sahelpulse.test",
        "job_title": "Growth Marketer",
        "phone": "+221 33 555 0308",
    },
    {
        "first_name": "Tobias",
        "last_name": "Krause",
        "email": "tobias.krause@rheinwerk.test",
        "job_title": "Plant Manager",
        "phone": "+49 211 5555 0309",
    },
    {
        "first_name": "Lucia",
        "last_name": "Fernandez",
        "email": "lucia.fernandez@suratelier.test",
        "job_title": "Retail Buyer",
        "phone": "+34 91 555 0310",
    },
]

TWENTY_COMPANIES = [
    {"name": "Brightpath Logistics", "domain": "brightpath.test"},
    {"name": "Nordvind Energy", "domain": "nordvind.test"},
    {"name": "Veda Technologies", "domain": "vedatech.test"},
    {"name": "Atelier Midi", "domain": "atelier-midi.test"},
    {"name": "Sakura Data", "domain": "sakuradata.test"},
]

# ------------------------------------------------- sample CSV expectations ---
# The numbers the sample CSVs must satisfy. The test in
# `tests/infra/test_test_apps_samples.py` reads these, so the files and the
# seeded data can never drift apart silently.
LEADS_CSV_ROWS = 20
LEADS_CSV_DUPLICATES = 3  # rows whose email matches an ODOO_LEADS email
LEADS_CSV_MISSING_EMAIL = 2  # rows with an empty email cell

PRODUCTS_CSV_ROWS = 10
PRODUCTS_CSV_UNKNOWN_CODES = 1  # rows whose code is not in ODOO_PRODUCTS

ENROLLMENT_CSV_ROWS = 8
TWENTY_LEADS_CSV_ROWS = 10
TWENTY_LEADS_CSV_DUPLICATES = 3  # rows whose email matches a TWENTY_PEOPLE email
