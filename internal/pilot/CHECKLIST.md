# Pilot test checklist: quote, lead, invoice

The files are in internal/pilot/files. Every row has an "expected" column that says what should happen to it. That column isn't part of the task: leave it unmapped on the review screen, and watch that nothing gets typed from it.

Run the clean file first. Only move on to the messy one once the clean one passes. That way a failure points at one thing.


## Before you start

1. PL-090 (slow page = "Chrome disconnected") is merged and deployed. If not, expect pauses on the 500 run and press Resume.
2. Reset Odoo once: python infra/test-apps/reset.py odoo
3. You have a fresh extension zip with the new access code.
4. Each tester can reach the Odoo they will use.
5. Note the time you start each run. You need it for the time-saved check.


## Important: the record key

When you make the recipe, the popup asks which column identifies each row (the record key). ProcessLens remembers every key it has finished, for good. If a later row has the same key, it gets skipped as "already committed in an earlier run", even after Odoo is reset.

- Leads: key on email (the default).
- Quotes and invoices: do NOT key on customer. There are only 5 customers, so each customer's second quote would be skipped. Record the reference column into Odoo instead (see below) and key on it.
- For a second round, make fresh files with new references and emails:
  python internal/pilot/make_csvs.py --tag r2


## Use case 1: Odoo quote

Record (with the extension):

1. Sales, then Quotations, then New.
2. Customer: type the full name and click the option.
3. Other Info tab, then Customer Reference: type the reference (Q-DEMO-001).
4. Order Lines tab, then Add a product: type the product name and click the option.
5. Quantity: type it.
6. Click Save once.

Make the recipe with Reference as the record key, then:

1. Run quotes-clean.csv (10 rows). Expect 10 verified.
2. Run quotes-messy.csv (17 rows). Check each row against its "expected" column.

The rows that matter most in the messy file:

- Misspelled or unknown customer, misspelled product, "Standing Desk" (matches two products): none of these may end verified with the wrong customer or product, or a newly created one.
- The duplicate reference and the empty reference should be flagged on the review screen, before the run starts.


## Use case 2: Odoo lead (the 500-row one)

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


## Use case 3: Odoo invoice

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
