# Recipe specification (v1)

The machine-checkable contract is [`packages/recipe/schema/recipe.schema.json`](../../packages/recipe/schema/recipe.schema.json)
(JSON Schema 2020-12). A full worked example is
[`packages/recipe/examples/create-lead.recipe.json`](../../packages/recipe/examples/create-lead.recipe.json).
This page explains what the schema means and, in one section, what it deliberately cannot check. Where the two
disagree, the schema wins and this page has a bug.

A **recipe** is a typed JSON program. The compiler writes it from a recording, the editor changes it, the runner
executes it once per input record, and `recipe_versions.recipe` stores it. It is the source of truth for workflow
behaviour: the executor performs exactly these steps, in this order, and never decides an action itself
(constitution rules 1 and 4). Each saved recipe is an immutable version (`recipe_versions.version`), a run pins one
version, and `recipe_versions.format_version` records which version of *this* format the document uses.

The document has three parts: `meta`, `variables`, and `steps`.

## meta

What the workflow is for, what one record of input looks like, and the rules that decide duplicates and approvals.
The schema requires all seven fields: `name`, `when_to_use`, `input_schema`, `record_key`, `idempotency_key`,
`approval_policy`, `allowed_origins`.

Two of them carry decisions worth restating:

- **`record_key`** is the value that identifies one record everywhere: the run-level uniqueness key, the duplicate
  search in the destination, and the basis of the idempotency key. Normalize in the template (`{{row.email | lower}}`).
- **`idempotency_key`** is what `committed_keys` is unique on, per tenant and destination (decision D-012). A *create*
  workflow uses the record key, so a re-run never creates the record twice. An *update* workflow adds the new value
  (`{{row.sku}}:{{row.new_price}}:{{row.effective_date}}`), so the same change applies once while a later change to the
  same record is still a new key.

`input_schema.fields` declares each column's type, whether it is required, and whether it must be unique inside the
uploaded file (the in-file duplicate check). `input_schema.rules` are the precheck rules applied per record before the
destination is touched: `{"when": {"missing": "email"}, "then": "park", "reason": "..."}`. They can only end a record as
`skip`, `park`, or `fail`, which is exactly what the ledger accepts at that point (LEDGER_SPEC, `claimed`/`prechecked`).

## variables

Values the workflow needs that no step produces, each with a `source` and an optional `requires_review`. A step that
binds a name (`transform`, `match`, `aggregate`, `classify`, `extract`, `read_value`, `download`, and the loop variable
of `for_each`) creates a variable the same way, which later steps reference with `{{name}}`.

## steps

An array, executed top to bottom, each item with a unique `id` (written to `step_events.step_id`) and an `action`. The
action decides the shape: fields that do not belong to that action are rejected, and the fields it needs are required.

| `action` | Runs in | What it does |
| --- | --- | --- |
| `navigate` | Browser | Go to a URL inside `allowed_origins` |
| `fill` | Browser | Type a value into a field |
| `select` | Browser | Choose an option, including a custom combobox that is not a native `select` |
| `click` | Browser | Click an element |
| `upload_file` | Browser | Attach a file to a file input |
| `read_value` | Browser | Read what the page shows and bind it to a variable |
| `wait_for` | Browser | Wait until a condition holds; `timeout_ms` is required |
| `assert` | Browser | Check what the page shows |
| `download` | Browser + storage | Produce a file and bind it to a variable |
| `branch` | Runner | Run one of two step lists depending on a condition |
| `for_each` | Runner | Run a step list once per item of a collection |
| `transform` | Runner, plain code | Compute a value from the closed transform vocabulary |
| `match` | Runner, plain code | Compare two values, with a tolerance for amounts |
| `aggregate` | Runner, plain code | Combine many values into one |
| `classify` | Runner → AI service | Choose one category from a fixed list |
| `extract` | Runner → AI service | Read fields from a document |
| `approval_gate` | Runner → person | Stop and wait for a person |
| `end_record` | Runner | Finish the record: `verified`, `skipped`, `parked`, or `failed` |

`branch.then`, `branch.else`, and `for_each.steps` hold nested steps, validated by the same rules.

### Targets: how an element is found

Element steps carry `targets`, an ordered array of locator strategies — the ladder. The runner tries them in order and
records the rung that succeeded in `step_events.locator_rung`:

1. `role` — accessible role plus accessible name, usually scoped by `within`.
2. `testid` — a stable test id.
3. `label` — the link between a control and its label text.
4. `css` — a short, stable selector.
5. `text_fuzzy` — fuzzy text, the last resort.

A rung counts as found only when **exactly one visible, enabled element** matches; none or several means the rung fails
and the next one is tried. `within` is the container scope (a bundle-level default, overridable per rung) and `frame` is
the path of iframes from the top document down, so an element inside an iframe is found through the frame path rather
than by luck.

### Values

Every value comes from one of six sources:

| `kind` | Meaning |
| --- | --- |
| `input` | A column of the current record |
| `constant` | A fixed value, possibly a template |
| `rule_table` | A lookup in a saved table, for example an input value to a dropdown option |
| `page` | A value a `read_value` step read from the page |
| `document` | A field an `extract` step read from a document |
| `ai_text` | Text a model produced — always requires approval |

Templates are strings with `{{ }}` placeholders: `{{row.<column>}}` for an input column, `{{<variable>}}` for a bound
value, and an optional pipe filter such as `{{row.email | lower}}`.

### Commits and approvals

`commit: true` marks the step that changes data in the destination. It is available on `fill`, `select`, `click`, and
`upload_file` — the steps that can write — and the schema requires such a step to carry `assert_after`, a condition
that proves the destination accepted the change. This is constitution rule 6 expressed in the contract, not in a
convention. The runner treats a commit differently from every other step: it writes `submitting` before the click and
`submitted_unverified` after, and only `assert_after` passing produces `verified` with the `committed_keys` row
(LEDGER_SPEC).

`meta.approval_policy.commit` says where a person must approve a commit (`first_record` is the default). Independently
of that policy, an `approval_gate` step stops the run for a person, and both the AI repair path and every AI value under
its confidence threshold need approval whatever the policy says (constitution rule 11).

## What the schema cannot check

JSON Schema validates one document; these need the validator, the compiler, or the runner:

- **Unique step ids.** The schema cannot compare ids across nesting levels.
- **Rung order.** A bundle may skip rungs, but it may not reorder them; the ladder order is enforced by the validator.
- **Exactly one match.** Whether a rung is unique, visible, and enabled is a fact about the page, known only at run time.
- **The origin allow-list.** `navigate.url` and every other URL are checked against `meta.allowed_origins` by the
  executor, which refuses to act on any other origin.
- **AI text needs approval.** The schema forces `requires_approval: true` on an `ai_text` source; ordering it before a
  commit is the runner's job.
- **Transform vocabulary.** `op` is a name from a closed vocabulary owned by the code that runs it. No code, script,
  expression, or regular expression is ever expressible in a recipe (AI_BOUNDARIES, rule 5).
- **Values that need review.** Confidence thresholds, quote-exists checks, and arithmetic validators run in code before
  a value is used.
- **Reference integrity.** That `{{email}}` refers to a variable that exists, or that `table` names a saved table, is
  checked by the validator with cross-field rules the schema format has no vocabulary for.

The validator that performs these checks is a separate ticket; this format is its input.

## Versioning and migration

`format_version` is 1 in this file, and the schema pins it with `const: 1`. A document written for a later version is
rejected here rather than half-read, and every stored version keeps the format it was written in.

When the format changes:

1. Add a new schema file (for example `recipe.schema.v2.json`) and keep v1; never edit a released schema in place.
2. Write the migration as an explicit upcast from v1 to v2 — a reviewable function, not an inference — and record the
   decision. `recipe_versions.format_version` says which one a stored recipe needs.
3. Old versions stay readable: a run pins one immutable version, and the runner executes a stored recipe with the
   schema version that recipe was written in, not the newest one.

## Related documents

[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md#the-recipe) · [LEDGER_SPEC.md](LEDGER_SPEC.md) ·
[AI_BOUNDARIES.md](AI_BOUNDARIES.md) · [DECISIONS.md](DECISIONS.md) (D-002, D-008, D-012)

Open items that this format leaves to the owner are listed in
[`agent/proposals/P-002-recipe-open-items.md`](../agent/proposals/P-002-recipe-open-items.md).
