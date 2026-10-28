# Working rules (2026-10-27)

Three weeks of green gates did not make the owner's use cases work. These rules change what counts
as done and what we test against. They bind T1 and T2 on every ticket.

## 1. Done means the use case works

- A ticket is done when the owner's use case passes on the real site, from the side panel. Green
  gates are needed but are not enough.
- A report with a "Not done" list leaves its use case open. The next ticket for that use case names
  every leftover; nothing is dropped.

## 2. Real recordings are the fixtures

- When an owner test fails, T1 saves what the owner taught (the recipe from `/recipes/{id}`) and the
  run's events as a fixture, with row values, emails and codes scrubbed.
- The ticket that fixes it must compile and replay that exact fixture in a test, seen failing first.
- A scripted golden (Playwright clicking the right buttons) is not proof that a person's recording
  works. When a golden is scripted, it does the task the way the owner did: leave the form instead
  of clicking Save, wait on spinners, fix a typo.

## 3. The test apps match the use cases

Every use case's app is installed in `infra/test-apps`. Odoo Events (`event`) is missing today.

## 4. Find every problem before the ticket

A run stops at its first failure, so the next problem hides behind it. Before writing a ticket for
a failing use case, T1 goes through every step of the owner's recording and the page it acts on, and
lists each step that will break. One ticket covers the whole use case, not the first failure.

## 5. Scope freeze

Until use case #4 (Odoo Sales price update) and #6 (Odoo Events enrollment) pass, tickets only move a
failing use case. No new pages, imports, hosting work or other apps.

## 6. Don't burn time on the full suite

To see new tests fail, run only the new test files. Run the full gates once, at the end. The full
Python suite takes about 17 minutes.
