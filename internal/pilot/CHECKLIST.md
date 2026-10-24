# Pilot test checklist: the brief's use cases 1, 3 and 4

These are the three use cases from the brief we are proving:
- use case 1, customer inquiries: a CSV of leads into Odoo CRM, including the 500-row run;
- use case 4, product updates: a sheet of SKUs and new prices, applied to Odoo products;
- use case 3, invoices: entered from a spreadsheet. The brief's version reads PDF invoices; that part is not built.

The files are in internal/pilot/files. Every row has an "expected" column that says what should happen to it. That column isn't part of the task: leave it unmapped on the review screen, and watch that nothing gets typed from it.

Run the clean file first. Only move on to the messy one once the clean one passes. That way a failure points at one thing.


## Before you start

1. Reset Odoo once: python infra/test-apps/reset.py odoo
2. The extension is installed from the newest zip (the one with the new access code).
3. Note the time you start each run. You need it for the time-saved check.


## Important: the record key

When you make the recipe, the popup asks which column identifies each row (the record key). ProcessLens remembers every key it has finished, for good. If a later row has the same key, it gets skipped as "already committed in an earlier run", even after Odoo is reset.

- Leads: key on email (the default).
- Product updates: key on sku. The key also includes the price, so a new price for the same product counts as new work.
- Invoices: do NOT key on customer. There are only 5 customers, so each customer's second invoice would be skipped. Record the reference column into Odoo instead (see below) and key on it.
- For a second round, make fresh files with new references, emails and prices:
  python internal/pilot/make_csvs.py --tag r2


## Brief use case 1: customer inquiries, as Odoo leads (the 500-row one)

Record:

1. CRM, then New. If quick-create pops up, open the full form.
2. Type the name, email, phone and company.
3. Salesperson: type the name and click the option.
4. Click Save once.

Make the recipe with email as the record key. Tick "park if empty" for email. Then:

1. Run leads-20-warmup.csv (20 rows). Expect 20 verified.
2. Run leads-messy.csv (17 rows, saved the old Excel way: Windows-1252 with semicolons). Check the "expected" column. Accents must look right in Odoo, the misspelled or unknown salesperson must not end verified with someone else, and the duplicate email must be flagged.
3. Run leads-500.csv. During it, try each of these once and write down what happened:
   - leave it alone for 30 minutes;
   - close the laptop lid for 2 minutes, then open it;
   - turn Wi-Fi off for 1 minute, then on;
   - click inside the work window once;
   - press Cancel, then start the remaining rows again.
4. When it ends: 500 leads in Odoo with @pilot-leads.test emails, no email twice.
5. Run leads-500.csv again: every row should be skipped and nothing new should appear in Odoo.


## Brief use case 4: product updates

Record:

1. Sales, then Products, then Products.
2. Click the search box, type the sku (PL-CHR-2001), press Enter.
3. Open the product it found.
4. Sales Price: select it and type the new price.
5. Click Save once.

Make the recipe with sku as the record key. Leave the rule the popup offers ("Park a row when the search finds nothing"). Then:

1. Run products-clean.csv (10 rows). Expect 10 verified, and each product's Sales Price in Odoo equal to the file.
2. Run products-clean.csv again. Expect every row skipped and no price changes.
3. Run products-messy.csv (16 rows). Check the "expected" column.

The rows that matter most in the messy file:

- PL-XXX-0000 (no such product) and PL-MOU-600 (cut short; it partly matches two products) must be parked. Check that neither PL-MOU-6001 nor PL-MOU-6002 changed price.
- Empty, "free" and negative prices must not change the product.
- PL-LGT-9002 already has that price, so it should be skipped with "Already had these values".
- $ signs and thousands commas must not turn into a wrong number.


## Brief use case 3: invoices, from a spreadsheet

Record:

1. Invoicing, then Customers, then Invoices, then New.
2. Customer: type it and click the option.
3. Other Info tab, then Customer Reference: type the reference.
4. Invoice Lines, then Add a line: product (click the option), quantity, unit price.
5. Click Save once. Do NOT click Confirm: the draft is the result.

Make the recipe with Reference as the record key, then:

1. Run invoices-clean.csv (10 rows). Expect 10 draft invoices.
2. Run invoices-messy.xlsx (an Excel file with a title row and money-formatted prices). Check the "expected" column. Prices are the risk here: $109.00, 1,299.00 and €79,50 must not end up as the wrong number.


## How to tell a run really worked (do this for every run)

Don't go by the green ticks alone.

1. Count: the number of new records in Odoo equals the number of rows the run page says verified.
2. Spot check: open 10 random records in Odoo and compare every field with the spreadsheet row.
3. Nothing extra: no customers, products or tags that weren't there before (look for "Acme Widgets Ltd").
4. Rerun: run the same file again. Every row should be skipped and nothing new should appear in Odoo.
5. Time saved: does the figure on the results page look believable for the number of rows?


## Write down for every problem

- the use case, the file and the row (the reference or email);
- what you expected and what happened;
- the row's state and reason on the run page;
- a screenshot of the run page and of Odoo.
