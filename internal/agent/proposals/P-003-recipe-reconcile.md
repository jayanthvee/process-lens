# P-003: Let a recipe say how to find its own record (reconcile) and what a rejection looks like

Status: **parked by the owner (2026-10-11)**. Revisit after real runs show how often saves end "couldn't confirm,
please check". Until then the runner parks uncertain saves for a person (PL-014's `ParkingReconciler`).
Author: T1 · Touches: `packages/recipe/schema/` (protected), so adopting it later needs an owner decision recorded
in `internal/docs/DECISIONS.md` before any implementation ticket

## The problem in one paragraph

After a save, ProcessLens sometimes cannot tell whether the save landed: the confirmation was missed, the browser
disconnected, or the worker crashed. The ledger then puts the record in `reconciling`, which means "check the
destination before doing anything else". But the recipe format has **no way to say how to check**. So the runner
cannot look, and the only safe answer is to park the record for a person. That is what PL-014 does by default.
It is safe, but every uncertain save becomes manual work. The format also has no way to say "this is what a
rejected save looks like", so the runner can never mark a commit `failed` with proof: anything short of
confirmation is treated as uncertain.

## Proposal

Add two optional parts to the recipe format, as **format version 2**. Version 1 recipes stay valid and keep the
park-on-uncertainty behaviour.

### 1. `meta.reconcile`: how to look the record up

```json
"reconcile": {
  "steps": [
    { "id": "rc-open",   "action": "navigate", "url": "https://crm.example.com/leads" },
    { "id": "rc-search", "action": "fill",  "targets": [{ "by": "label", "text": "Search leads" }],
      "value": { "kind": "input", "column": "email" } },
    { "id": "rc-go",     "action": "click", "targets": [{ "by": "role", "role": "button", "name": "Search" }] }
  ],
  "one_match":  { "element_count": { "selector": { "targets": [{ "by": "css", "selector": "[data-lead-row]" }] },
                                     "equals": 1 } },
  "no_match":   { "text_visible": "No leads found" },
  "timeout_ms": 15000
}
```

**Rules:**
- **Read-only, enforced by the schema.** The reconcile steps may use only `navigate`, `fill`, `select`, `click`,
  `wait_for` and `assert`. `commit: true` is forbidden, and so is `allow_destructive_control`. A reconcile can type
  into a search box and press Search. It can never save, delete or pay.
- **Three outcomes**, each evaluated as a condition with a timeout, never a fixed sleep:
  - `one_match` holds → `verified`, with its `committed_keys` row.
  - `no_match` holds for the whole timeout → `pending` (retry, using up an attempt).
  - Anything else (both, neither, or several matches) → `parked`.
- **Search lag.** Waiting up to `timeout_ms` for `one_match` gives a slow search index time to catch up before the
  runner concludes "not there". A "not found" answer must hold for the full window.
- **Update workflows** (use case 4, a price change) make `one_match` check the **new value**, for example
  `value_equals` on the price field. "The record exists" is never enough for an update: it always exists.

### 2. `reject_when` on a commit step: what a rejected save looks like

```json
{ "id": "save-lead", "action": "click", "commit": true,
  "assert_after": { "text_visible": "Lead created", "timeout_ms": 8000 },
  "reject_when":  { "element_present": { "targets": [{ "by": "css", "selector": "[role=alert].field-error" }] } } }
```

**After dispatch the runner waits for whichever condition holds first:**
- `assert_after` holds → `verified`.
- `reject_when` holds → `failed`. This is the positive rejection rule L2 requires: the page itself proved the save
  was refused.
- Neither holds by the timeout → `reconciling`. This is the same as today.

### 3. A new condition: `element_count`

`{ "element_count": { "selector": <selector>, "equals": n } }`, or `"at_least": n` in place of `equals`. `<selector>`
is the existing selector shape: a target ladder plus an optional `within`. It is needed to tell "exactly
one match" from "several", which `element_present` cannot do.

### 4. Where reconcile definitions come from

The compiler can draft `meta.reconcile` from the recording's own duplicate search. Use case 1 already records
"search by email → 0 results → continue". The builder confirms or edits the draft in the editor. A recipe without
a reconcile definition is still valid; it just parks uncertain saves.

## Options considered

| Option | What it means | Verdict |
|---|---|---|
| **1. `meta.reconcile` + `reject_when` (this proposal)** | Explicit, read-only, schema-checked, works for create and update workflows | **Recommended** |
| 2. Reuse the recipe's duplicate-search `branch` implicitly | No schema change. But the runner would have to guess which steps are "the search", and the branch's question ("does it exist?") is the wrong question for updates | Fragile, rejected |
| 3. No reconcile in the format; always park | Status quo after PL-014. Safe, but every uncertain save needs a person | A fallback, not a destination |

## Safety review (T1)

- **No new action path.** Reconcile steps are schema-forbidden from committing or touching destructive controls,
  and they run through the same executor guards (origins, challenge detection, hit-testing).
- **No AI involvement.** Reconcile is plain recipe data evaluated by code (D-017).
- **It only replaces "park for a person".** Without a definition, behaviour is exactly PL-014's. With one, the worst
  case of a badly written reconcile is a park.
- **One real risk: a reconcile that is too loose.** For example, `one_match` on text that appears for any lead. That
  would mark an unsaved record `verified`. Mitigations:
  - the compiler drafts from the recorded search, which includes the record key;
  - the schema requires the reconcile to reference at least one `{{row.*}}` value;
  - the preview run exercises the reconcile once before a batch.

## Implementation (a later ticket, after the decision)

1. `packages/recipe/schema/recipe.schema.v2.json` (v1 untouched), with an explicit v1→v2 upcast, which is the
   identity because both new parts are optional. Validator and compiler support for v2.
2. A `RecipeReconciler` in `services/runner/reconcile.py` that runs `meta.reconcile` through the executor, and
   `reject_when` handling in the commit flow.
3. Tests: create and update workflows; search lag that resolves within the timeout; several matches; a reconcile
   step that tries to commit (rejected by the schema); a recipe without reconcile (parks, as today).

## Decision requested

Approve option 1. If approved, it is recorded as **D-019**, and T1 writes the implementation ticket to follow PL-014.
