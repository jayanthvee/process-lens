# P-002: Recipe v1 open items

Status: open · Raised by: PL-004 · Date: 2026-10-08 · Needs: T1 review, then an owner decision per item

## Why this exists

PL-004 asks for a recipe format that is machine-checkable. Writing a schema forces a choice about everything it
mentions, so anything the design does not already settle is listed here instead of being invented in
[`recipe.schema.json`](../../../packages/recipe/schema/recipe.schema.json). Nothing below blocks the schema: v1 is
usable as written, and each item names what would change if the owner decides differently.

## 1. A bounded agent step (the example in the ticket)

The design's step list contains no agent step, and the schema has none. A recipe is a program; a step that lets a model
choose an action would break constitution rule 4 directly and rule 5 in effect, and it is the one thing the product
promises not to do (PRODUCT.md, "What ProcessLens will not do").

If a bounded "look at this region and tell me which element matches" step is ever wanted, it is not an action-choosing
step but a **data-producing** one, and it should be added as a seventh AI call type with:

- an input of page structure only (no values), like `propose_repair` already has;
- exactly one target bundle as output, checked against the old fingerprint;
- a hard bound on attempts, and no delete/cancel/refund/pay/transfer control as a result;
- a person's approval, producing a new immutable recipe version.

**Recommendation:** keep it out of v1. Add it later as a repair proposal (Phase 7), not as a recipe step.

## 2. `ai_text` is in the schema, but it has no call type

Requirement 4 of PL-004 lists AI-generated text as a value source, so `value_source` includes `kind: "ai_text"` with
`requires_approval` pinned to `true`. AI_BOUNDARIES says the same thing needs an owner decision, because it would be a
seventh call type with its own schema, router entry, and provider policy.

Decisions needed: the call type's name and input schema; which providers may see the payload (it is `personal` by
default); the confidence gate; and whether such a value may ever be the value of a `commit` step, or only of a
non-committing field.

**Recommendation:** allow it only for non-committing fields, always behind an `approval_gate`, until the retention
question in P-001 is decided.

## 3. The closed transform vocabulary has no list yet

`transform.op` is a name matching `^[a-z][a-z0-9_]{0,63}$`. The schema deliberately does not enumerate the vocabulary:
AI_BOUNDARIES calls it closed but never lists it, and enumerating it here would decide a question nobody has decided.
The validator that runs transforms owns the list.

Decisions needed: the initial operation set, and each operation's arguments. Candidates already implied by the design:
`lower`, `trim`, `first_word`, `last_word`, `replace`, `concat`, `parse_date`, `parse_amount`, and a reference
normalizer for invoice numbers (`INV-042` → `42`, use case 3).

**Recommendation:** decide the set with the transform ticket, then mirror it into the schema as an `enum` so a typo
fails validation instead of failing at run time.

## 4. `extract` and `classify` as steps contradict P-001

The schema includes `extract` and `classify` steps because the design's step table does. P-001 records the tension:
AI_BOUNDARIES says that until the owner chooses, nothing may call a model between `claimed` and `verified` except
`propose_repair`, and that extraction and classification belong to input preparation, before the ledger row exists.

**Recommendation:** keep both step types in the format, and have the runner refuse to execute them in a batch run until
the owner decides; a preview run may use them. The format can carry a step that the runner is not yet allowed to run.

## 5. Where precheck rules live

PL-004 puts the precheck rules in `meta.input_schema.rules`. The alternative is a top-level `precheck` section beside
`steps`, which would read better if the rules ever need more than one condition or a rule order.

**Recommendation:** keep them under `input_schema` in v1; the rules describe the input, and the ledger outcomes they
may produce (`skip`, `park`, `fail`) are exactly the ones legal at `claimed`/`prechecked`.

## 6. Smaller questions the schema settles for now

| Item | v1 says | Alternative |
| --- | --- | --- |
| `transform.args` | A free object of scalars | A per-operation argument schema, once the vocabulary is fixed |
| Ladder order | `targets` is ordered and at most five rungs; the validator checks the order | A canonical order enforced by the schema with `prefixItems` |
| `end_record.failed` | A recipe step may end a record as `failed` | Restrict steps to `verified`, `skipped`, `parked` and leave `failed` to the runner's close-out |
| Which steps may commit | `fill`, `select`, `click`, `upload_file` | Widen to `navigate` for a destination that creates through a URL |
| Step ids | Unique within the recipe, checked by the validator | Nothing in JSON Schema can express it |
| `select.option_match` | `text`, `value`, or `index` | Add a fuzzy option match once real dropdowns have been measured |

## What is not in question

The parts of v1 that come straight from accepted decisions, and so need no proposal: the three-part document
(meta/variables/steps), the semantic target ladder (D-002), immutable versions and `format_version` (D-008), the
`record_key` and `idempotency_key` split (D-012), `commit` requiring `assert_after` (constitution 6), and waits as
conditions with timeouts (constitution 20).
