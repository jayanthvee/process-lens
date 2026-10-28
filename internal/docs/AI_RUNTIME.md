# AI at run time: find the element, never decide the outcome

Decision: D-026 (replaces the "no model call in a batch" part of D-017, D-019 and D-020).
Status: proposed for the trial in PL-122 and PL-123, judged by the owner's three tasks.

## The problem

ProcessLens replays a recording with rules we wrote by hand. Every website behaviour we didn't
foresee became a ticket. Examples: Odoo saving by itself, a busy button, an emailed code, a
calendar, a form that fills fields as it opens, a product list shown as cards instead of a table.
The owner's production targets are outside websites and internal tools with no API. We can't test
those in advance, so rules alone won't reach them.

## The idea in one line

A step says what it is for, in words. The run uses the remembered element when it is still right,
and asks a model to find the element when it isn't. Our own checks still decide whether a row is
Done.

## What changes

1. **Steps carry an intent.** Each recipe step gets `intent`, one plain sentence, e.g. "type the
   product name into the search box", "open the product the search found", "set Sales Price to the
   row's New Price", "go back to the product list". The recorded targets stay as hints.
2. **Teaching is cleaned before it is saved.** When the person presses Stop, a build-time model call
   (`clean_recording`) marks the steps the page did by itself and steps that don't add anything
   (form-load fills, stray menu clicks), and writes each kept step's intent. The panel shows the
   cleaned list, and the person confirms it. A code gate allows only dropping and naming steps.
   It can't add a step or change a value.
3. **The run finds elements in two ways.** First the target ladder, then the site's remembered
   element. When neither gives exactly one element, a run-time call (`find_target`) gets the step's
   intent and a snapshot of the page. The snapshot holds the interactive elements and the text near
   them, with typed values removed. The call answers with one element id from that snapshot, or
   "not here". Code gates check the answer:
   - the element exists and is unique;
   - its role fits the action;
   - it is on an allowed origin;
   - it isn't destructive unless the step allows it.

   A hit is stored in `target_memory` for that task, step and site. A recipe never changes.
4. **Questions about the page are intents too.** "Did the search find this product?" is asked as an
   intent and resolved by `find_target`, not as a fixed table-cell check. "Not here" only ever
   stops the row for a person.

## What doesn't change

- The ledger, write-ahead, reconcile, the never-add-twice keys, the first-row approval, and the
  allowed origins.
- **Done is decided by checks, never by a model.** That means the read-back of the value, the
  page it lands on, or the confirmation shown.
- A model can make a row safer (find the element, or stop and ask) but never mark it `verified` or
  `skipped`.
- Every call goes through `services/ai` as set out in `AI_BOUNDARIES.md`. That means schema
  validation, a code check, the data policy and a row in `ai_calls`. The model gets no tools and
  no free-form actions.

## Cost and limits

- `find_target` runs only on a miss, so a task that works costs nothing extra on later runs.
- There is a cap per row (default: 6 calls). When it's reached, the row parks with "ProcessLens
  couldn't find '<intent>' on this page."
- Model: `claude-haiku-5-5` first, escalating once to `claude-sonnet-5-5` when the answer fails a
  gate. The trial measures the real cost per row, which goes in the report.

## How we know it works

The trial is PL-121 (the sign-in), PL-122 (the AI core and finding at run time) and PL-123
(cleaning at teaching, the search question and leaving the page). It is judged only by the
owner's three tasks, taught and run by the owner from the side panel:

1. the Goodlabs sign-in;
2. the Odoo price update, with the list shown as cards or as a table;
3. the Odoo Events enrollment.

If any of them fails, we stop and rethink rather than write patch tickets.
