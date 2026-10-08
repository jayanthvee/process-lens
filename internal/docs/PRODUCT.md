# Product

Public-safe: this page can be reused in public material without edits. It names no internal documents,
competitors, or build tooling.

## Problem

Many teams still move data into web applications by hand: they read a spreadsheet row, a PDF, or an email,
then type it into a CRM, portal, or admin screen that has no usable API. The work is slow and error-prone,
and the cost of an error is high. A lead entered twice, an invoice posted twice, or a slot booked twice
takes longer to undo than to enter.

Existing options fail in two ways. Scripted automation breaks when a page changes and gives no clear account
of which records went through. Agent-style automation asks a model to choose every click on every run, so
reliability drops with each record. At 95% per record, a 20-row batch finishes cleanly only about a third of
the time.

## What ProcessLens does

A person demonstrates a task once in their own browser. ProcessLens turns that demonstration into a readable,
versioned workflow, previews it on one record, and then runs it on every record in a file or folder. The
workflow is fixed code, not a fresh decision each time. Each record ends in exactly one explained outcome:
**verified**, **skipped**, **failed**, or **parked for a person**. No record is ever submitted twice, even
after a crash or a re-run. Models help only at the edges: naming steps, reading documents, suggesting a value,
or proposing a fix. A person or a code check approves every one of those suggestions.

## Users

| Role | Does |
| --- | --- |
| Builder | Demonstrates a task, edits the workflow, sets the record key and the rules, uploads batches, previews one record, starts runs |
| Reviewer | Approves first commits, low-confidence values, and proposed repairs; corrects parked records |
| Viewer | Watches runs and reads results and audit trails |
| Owner | Manages the workspace, its members, and its data policy |

The roles match the `user_role` values in the database. One person often holds several of them.

## The ten use cases

All ten run on the same engine. None has its own script.

| # | Use case | Input | What happens per record | The duplicate it must never create |
| --- | --- | --- | --- | --- |
| 1 | Customer inquiries | Spreadsheet of inquiries | Search the CRM; create a lead and assign a salesperson only if none exists | A second lead for the same email |
| 2 | Candidates | Résumé PDFs | Read name, contact details, and skills from the résumé; create a candidate profile and attach the file | A second profile for the same person |
| 3 | Invoices | Invoice PDFs, some scanned | Read supplier, number, date, line items, and total; check that the lines add up; post after approval | The same invoice posted twice, even if numbered differently ("INV-042" and "42") |
| 4 | Product updates | Price list | Read the current price, show old and new values, then save the new price | The same price change applied twice |
| 5 | Order transfer | Orders on a source site | Read the order and its line items on one site; re-create it on another, storing the source order ID | A second copy of an order on re-run |
| 6 | Enrollment | List of people | Find or create the person, then enroll them | A second enrollment |
| 7 | Ticket routing | Support tickets | Classify each ticket into an allowed category; assign it to the matching team | A ticket routed twice |
| 8 | Report collection | Date range and list of reports | Download each report and combine totals; flag any missing report | A silently partial report set |
| 9 | Appointments | Appointment requests | Read free slots; re-check the slot just before booking; book one request at a time | Two bookings in one slot |
| 10 | Reconciliation | Two lists of transactions | Match by amount and reference within tolerances; apply only confirmed updates | An update applied without a confirmed match |

## Success measures

| Measure | Target | Checked by |
| --- | --- | --- |
| Duplicate submissions | Zero, including 20 forced crashes mid-save and full re-runs of the same file | Kill test and re-run test on the demo sites |
| Explained outcomes | Every record ends verified, skipped, failed, or parked, with a reason | Results screen and ledger tests |
| Model calls on a clean record | Zero for browser-only workflows | Model call log per run |
| Site changes | A changed page stops the run before any bulk write, and the broken step is repaired with approval | Drift tests on the demo sites |
| Document values | Every accepted value is backed by a quote from the document; uncertain values go to review | Golden sets of synthetic documents |
| Coverage | All ten use cases run end to end on one engine | Demo-site suite |
| Time saved | Manual and automated time measured for each use case | Timing benchmark |

## What ProcessLens will not do

- Decide at run time what to click. The workflow decides; a model never does.
- Store passwords. Users log in themselves. Runs use their existing session.
- Evade bot detection or solve CAPTCHAs. A challenge pauses the run for a person.
- Act on any site the workflow does not list.
- Guess. Uncertain values, ambiguous matches, and unfamiliar pages go to a person.

## Scope of the first release

The first release is an attended runner. The workflow runs in a dedicated window of the user's own Chrome
while the user is signed in. It is tested on synthetic demo sites only: a CRM, a recruitment portal, an
accounting portal, a shop, and a scheduler. An unattended cloud runner comes later and reuses the same
workflows and ledger.
