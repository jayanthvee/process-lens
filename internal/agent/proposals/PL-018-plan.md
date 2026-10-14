# PL-018 plan: a recorder that captures what changed, not just the events it hears

Status: **Step 0, for T1 review.** No implementation commit exists on this branch beyond the ticket.
Author: T2 · Reviewer: T1 · Branch `pl-018` · Depends on: PL-017b (merged, `main` = `2649e62`)
Ticket: `internal/agent/tickets/PL-018.md` (committed here as `557da22`).

Where this plan and the ticket disagree, the ticket wins. Where both are silent, this plan decides and
flags the decision in §10 for T1 to overrule.

---

## 0. How to read this plan

The ticket's Step 0 asks seven questions. §§1–7 answer them in order. §8 lists the requirements that
are not among the seven questions (the step-id reset, weak observations, the D-019 renumbering) so that
nothing is missed. §8 lists the requirements that are not among the seven questions, §9 is the commit
order, §10 is what is open for T1, §11 records what the design review changed, and §12 is the ground
truth this plan was written from.
plan was written from.

Two constraints shape everything below and are worth stating once:

- **The recorder is a content script.** It can read the DOM, open shadow roots (`element.shadowRoot`)
  and same-origin subframe documents (`iframe.contentDocument`) directly. No injected page script is
  needed, and none is proposed.
- **The executor's guarantee is PL-017b's.** A click lands only on the element the guards approved. Any
  replay behaviour this plan adds (accepting an autocomplete suggestion) must go through that path, not
  around it.

---

## 1. The snapshot (Q1)

### 1.1 What it reads

One page-side pass over a bounded candidate set, in this order:

| kind | how it is found | what is read |
|---|---|---|
| inputs | `input` | `value`, `type`, `checked` for checkbox/radio |
| textareas | `textarea` | `value` |
| selects | `select` | the selected option's *text* (matching `selectedOptionText`, recorder.ts) |
| contenteditable | `[contenteditable='true'], [contenteditable='plaintext-only']` **roots only** (a nested contenteditable is not re-read; its root's text contains it) | `normalizeText(el.textContent)` |
| rich-text ARIA | `[role='textbox'][aria-multiline='true']` | text, as above |
| chip lists | labelled containers per §1.2, **excluding any listbox a combobox controls (§1.2.1)** | each chip's value (§1.3) |
| press targets | the `document.activeElement`, following open shadow roots and same-origin frames | identity only |

The candidate selector is one compound `querySelectorAll`, not a tree walk, so the cost is the browser's
own. Elements that are not visible (`el.checkVisibility?.()` when present, else `el.offsetParent === null
&& el.getClientRects().length === 0`) are skipped, except that a *hidden* field's value is never needed
for a diff and reading it leaks nothing.

#### 1.1.1 The baseline (added after the design review)

A diff needs a previous state, so the recorder takes snapshots at **six** moments, not four: at
**recording start** (the baseline — the page as the person found it, so a prefilled form produces no
steps), **on arrival** after a navigation (the new document's baseline), and before each of the four
actions the ticket names. A checkpoint with no baseline — the first one after attach, if the worker
started recording before the page loaded — is *the* baseline and emits nothing.

A field that **appears** between two checkpoints and already holds a non-empty value is recorded as a
fill (`"" → value`): that is the Gmail body editor case, where the editor is created typed-into — and it
is also what a page that seeds a value without an `input` event looks like. A field that appears empty
is nothing. A field that vanishes contributes nothing.

### 1.2 How a chip is recognised generically

A chip list is a container that:

1. is labelled — it has an `aria-label`, or is referenced by `aria-labelledby`, or sits inside a
   `<section>`/`<form>`/`<fieldset>`/`<div>` that has a `<label>`/heading naming it — **and**
2. is **not** an open combobox popup (§1.2.1) — **and**
3. holds children that each match *both*:
   - `role='option'`, `role='listitem'`, or (when the container is a `ul`/`ol`) a bare `li`; **and**
   - a chip identity that is **one of**: a value-shaped token that parses as an address (contains `@`),
     or a control whose accessible name starts with `Remove`.

No Gmail selector, no `data-address`, no class name is consulted. The rule is ARIA first, structure
second — and the identity clause is deliberately narrow: an arbitrary `[A-Za-z0-9._-]+` token would match
every class and id in the page, and "the container's only token child" would match every plain `<li>`.
Neither is used. A container whose children carry no address-shaped value and no remove control is **not**
a chip list and is not snapshotted at all.

#### 1.2.1 A combobox popup is not a chip list

An autocomplete's own suggestion popup is a labelled `[role='listbox']` full of `role='option'` children —
structurally identical to a chip list, and reading it as one would emit a phantom "chip added" for every
suggestion the person merely *saw*. Two exclusions prevent it, both structural:

- a listbox named by a combobox's `aria-controls`/`aria-owns` is that combobox's popup, never a chip
  list;
- a listbox that appears only while an `[aria-expanded='true']` control is present, and whose options are
  not address-shaped and carry no `Remove` control, is a popup.

The **chip container and the popup can coexist on one page** (Gmail has both). The popup is what makes a
suggestion clickable; the chips container — the one holding already-accepted tokens — is what is diffed.

### 1.3 The chip's value

Ordered rule, first match wins:

1. a single attribute on the chip whose value contains `@` and parses as an address (`data-address`,
   `data-email`, `data-value`, `value` — any attribute, not a fixed list);
2. the child remove-control's accessible name with a leading verb stripped (`"Remove bob@example.test"`
   → `bob@example.test`) when what remains parses as an address;
3. the chip's own normalized text when it contains exactly one address-shaped token;
4. otherwise the chip's normalized text (a display name is the last resort, never the first).

The **display name is never chosen when an address-shaped value exists anywhere on the chip** — that is
the rule the ticket names, expressed as a rule rather than a selector.

### 1.4 Shadow DOM and same-origin frames

- Open shadow roots: walked with `element.shadowRoot` after each candidate element is inspected, breadth
  first, bounded by the same element cap. A `slot` is not re-read; the assigned nodes are reached where
  they already sit in the light DOM.
- Same-origin iframes: `iframe.contentDocument` is read inside `try/catch`; a `SecurityError` marks the
  frame cross-origin (§6).
- Closed shadow roots: not reachable, and detected on the *event* path (§6).

### 1.5 What it never reads

- `input[type='password']`, and any field inside a credential context: the existing `isPasswordField` and
  `isCredentialContext` (fingerprint.ts:409, :436) are called unchanged, and a password field is skipped
  **before** its value is touched. The recorder's rule — not read, not buffered, not sent — is the rule
  here too.
- `type='file'` (no readable value), `type='hidden'` (not a person's edit), and anything inside a closed
  root or cross-origin frame.

### 1.6 How it stays fast

- One `querySelectorAll` for the candidate set; one visibility check per candidate.
- Element cap: **1500** candidates per snapshot; beyond it, the walk stops and emits one recorder warning
  (`"the page has more fields than ProcessLens snapshots; only the first 1500 were recorded"`), so the
  truncation is visible rather than silent.
- Snapshots are taken only at the four action boundaries the ticket names (before a click, before a
  navigation, before a press, before stop) — never on a timer, never on every keystroke.
- Per-element identity is a `WeakMap<Element, number>`; no attribute is written to the page (unlike the
  executor's marker, the recorder leaves the page byte-identical).
- The corpus selftest includes a synthetic 1000-field page and prints the snapshot's wall time; the
  budget is **< 16 ms** for 1000 fields on the test machine, reported in the report either way.

---

## 2. The diff → steps (Q2)

### 2.1 The shape of the diff

A snapshot is a `Map<identity, Entry>` where `Entry = {value, chipValues, role, targets}`. Identity is
the `WeakMap` id when the element object survives (`el.isConnected`), else a structural fingerprint
(tag + role + accessible name + index among its container's children) so a re-rendered list still
matches. The walk holds the **checkpoint baseline** — the snapshot taken at the previous checkpoint — and
the diff is `current − baseline`:

| change | compiles to |
|---|---|
| a non-empty text-like field whose value differs | `fill` (target from `computeTargets`, value = the new value, `clear_first: true`) |
| a field that became **empty** | nothing — a clearing is not a fill, and an empty fill is refused by the executor (step-runner.ts) and dropped by the compiler (compiler.py) |
| a select/combobox whose value differs without typing | `select` (option text, `option_match: "text"`) |
| a chip added to a chip list | the per-chip `fill` of §4 (value = the chip's value, `clear_first: false`) |
| a chip removed from a chip list | the click on its remove control; with no remove control present, **no step** and a warning (§4.4) |
| an element that appeared or vanished and is not a chip | nothing (a mere appearance is not an action) |

A field that is *typed into* is still a `fill`, exactly as today; the snapshot is what catches the ones
no event reports.

**A value the page set itself.** A field whose value changed with no focus and no `input` event on it
during the interval is still recorded — that is precisely the `contenteditable`-mutated-by-script case
the ticket exists for — but its fill carries the envelope warning `"this field changed without any
typing: the page set it"`, so the provenance is visible in the Make recipe form rather than silent.

**The accept leaves the field empty.** Accepting a suggestion usually clears the typed prefix from the
field and puts the value in a chip. That is one action, not a fill plus a clear plus a chip: when a text
field's value goes non-empty → empty *and* a chip appears in the chip container that field controls
within the same interval, the checkpoint emits **no fill for the clearing** — the chip step alone
carries the change, and the compiler's fold (§4.1) absorbs the prefix fill that preceded it. A field
that empties with no chip is not a step either (§2.1 row 2).

### 2.2 Ordering — the fix for the order bug

The recorder holds one rule: **a checkpoint runs before the triggering event is emitted, and any steps
the diff produces are emitted first, in snapshot order.** Concretely:

```
checkpoint();  // = snapshot(); diff(); emitDiffSteps()  — what the page changed since the last one
emitAction("click", clickStep(el));    // then the click itself
```

**The checkpoint runs at `pointerdown`/`mousedown` as well as `click`.** Many autocompletes accept a
suggestion on the pointer press, not on the click: by the time `click` fires, the field has been cleared
and the chip already exists, so a checkpoint that waited for `click` would see `"" → chip` and lose the
typed prefix entirely. Both are captured (in the capture phase, like every other listener here), and a
checkpoint that already ran for this action's `pointerdown` does not run again for its `click` — a flag
set on the pointer event and cleared after the click keeps one pointer gesture to one checkpoint.

The same checkpoint prefixes `onKeyDown`, the navigation path (`pagehide`/`record:leaving`) and stop.

The order bug the ticket names — a click recorded before the typing that produced it — cannot occur,
because the typing's checkpoint runs before the accept's own step, whichever order the page reports the
events in. There is a dedicated vitest for each order the corpus can produce:

- type "ma" → `click` the suggestion (the ticket's case): `fill(To, "ma")` precedes the accept;
- type "ma" → `mousedown`/`pointerdown` the suggestion: the checkpoint at the press catches the typed
  value first, and the fold (§4.1) then absorbs it;
- type "ma" → `mousedown` where the widget clears the field **and** adds the chip in the same tick: the
  chip-add is what the checkpoint sees, the prefix fill is reconstructed from the `input`/`onInput`
  buffer the recorder already holds (`pending`, recorder.ts), which is flushed as the fill before the
  chip step. This is the case the design review flagged as "failure (c) in reverse"; the replay is safe
  because §4.1 folds the two into one step either way.

### 2.3 Change and change back

One rule, at the recorder:

> A checkpoint emits a step for a field only when the field's value differs from its value at the
> **previous checkpoint**. A field that changed and changed back between two checkpoints is therefore
> identical to its baseline and produces **no step at all** — there is nothing for a later stage to
> cancel.

Trace, with the ticket's own case:

| moment | field | checkpoint baseline | emitted |
|---|---|---|---|
| recording starts | `""` | `""` (the baseline of §1.1.1) | — |
| person types `ma`, deletes it, then clicks Send | `""` | `""` | nothing (change-and-back) |
| person types `ma`, leaves it, then clicks Send | `"ma"` | `""` | `fill(To, "ma")` |

`coalesce_fills` (packages/compiler/coalesce.py) already collapses *adjacent* fills of the same field to
the last value — it works by `target_signature` equality and keeps the final value; it remains a backstop
for any run a later stage splits, and is **not** the fix for this requirement. The test is a vitest on the
recorder: type, delete, click — and assert the click is the only step emitted.

### 2.4 What is not a step

Focus, blur, scroll, mousemove, hover, and a click on a control with no name that changed nothing are
still noise. A checkpoint with an empty diff emits nothing at all — no empty steps, no ordering churn.

---

## 3. The `press` action (Q3)

### 3.1 The keys

| key | recorded when | not recorded when |
|---|---|---|
| `Enter` | always (accept a suggestion, submit a search, activate) | — |
| `Tab` | always (accepts a suggestion, moves focus with intent) | — |
| `Escape` | always (dismisses a listbox, cancels an editor) | — |
| `Backspace`, `Delete` | the target is not a text-entry element (a list/token delete) | inside an input/textarea/contenteditable — that is text editing, and the snapshot records the resulting value |
| `Ctrl+Enter`, `Cmd+Enter` | always | — |

Printable single characters are **never** recorded as presses: they are typing, and a fill already
carries the resulting value. The rule is "keys that only typed characters are not recorded", implemented
as a small table so it is testable in isolation.

### 3.2 The schema change (`packages/recipe/schema/recipe.schema.json`)

A new `$defs/step_press`, added to the `step` `oneOf` (line ~549):

```json
"step_press": {
  "title": "press",
  "description": "Send one key to the focused element. Not commit-capable: a key press alone changes no destination record.",
  "type": "object",
  "additionalProperties": false,
  "required": ["id", "action", "targets", "key"],
  "properties": {
    "id": { "$ref": "#/$defs/step_id" },
    "name": { "$ref": "#/$defs/step_name" },
    "description": { "$ref": "#/$defs/step_description" },
    "labels": { "$ref": "#/$defs/labels" },
    "timeout_ms": { "$ref": "#/$defs/timeout_ms" },
    "on_failure": { "$ref": "#/$defs/on_failure" },
    "action": { "const": "press" },
    "targets": { "$ref": "#/$defs/targets" },
    "within": { "$ref": "#/$defs/template" },
    "frame": { "$ref": "#/$defs/frame" },
    "key": { "enum": ["Enter", "Tab", "Escape", "Backspace", "Delete", "Control+Enter", "Meta+Enter"] }
  }
}
```

The shape mirrors `step_click` (recipe.schema.json:641) minus `commit`, `assert_after` and
`allow_destructive_control`, plus `key` and its `required` entry.

`commit` and `assert_after` are deliberately absent: the schema sets `additionalProperties: false` on
every step def, so a `press` step that carries `commit` is **invalid by construction** — "never
commit-capable on its own" is enforced by the schema, not by a convention. `commit_flag` is referenced
only by `step_fill`, `step_select`, `step_click` and `step_upload_file` (schema lines 600, 624, 652,
679), which is the D-017 rule already in force.

TypeScript side: `RecordedStep` gains `PressStep` (`recipe-types.ts`), and `RecordedKind` gains `"press"`.

### 3.3 The recorder half

A `keydown` listener joins the existing capture-phase listeners. On a recorded key it:

1. checkpoints (same three lines as §2.2) so any typing that preceded it is emitted first;
2. resolves the target — `document.activeElement`, descending through open shadow roots and into
   same-origin frames — and, like every other step, refuses to record a password field;
3. emits `{kind: "press", step: {id, action: "press", name: "Press Enter", targets, key}}`.

No `commit`, no `assert_after` — the envelope rule the schema test already enforces.

### 3.4 The executor half

- `EXECUTED_ACTIONS` (executor.ts) gains `"press"`; `validateRecipe` therefore accepts it and still
  refuses everything else.
- `performStep` gains a branch: resolve the target, then two CDP calls —
  `Input.dispatchKeyEvent {type: "keyDown", …}` and `{type: "keyUp", …}` — with the key, `code`,
  `windowsVirtualKeyCode`/`nativeVirtualKeyCode` from an extended `KEY_CODES`, and `modifiers`
  (`2` for Control, `4` for Meta) for the two chorded forms. Both calls go through the **PL-017b input
  gate**: `hooks.beforeInput()` before each, `onFirstInput` before the first, so a withdrawn or expired
  command cannot press a key.
- A `press` never marks a record committing: `commit` is absent from the step, and the executor's commit
  trace keys off `step.commit === true`.
- Frames: a `press` inside a named subframe uses the same `frameContextId` path as the other actions.

### 3.5 The compiler

Three places, all of which the design review found missing from the first draft:

- `ACTIONABLE_KINDS` (packages/compiler/model.py:16) gains `"press"`. It is consumed by
  **`normalize.py:140`**, not by `to_candidates`; without it a `press` event is dropped as "noise" with
a warning before the compiler ever sees it.
- `STEP_ACTIONS` (model.py:19) gains `"press"`, so `to_candidates` (compiler.py:121) accepts it.
- `_render_candidate` renders `key` through verbatim. Commit inference is untouched because a press is
  never a commit candidate. A press immediately followed by the same press on the same target (a
  double-fire) collapses to one.

### 3.6 Folding a press into the submit it caused

Pressing Enter in a field is how most forms submit. The browser then fires `submit`, and the recorder
today turns that into a click on the form's submit control (`onSubmit`, recorder.ts: `submitter ??
firstSubmitControl(form)`). Recorded naively that is **two** steps — `press(Enter)` and `click(Save)` —
and a replay would press Enter *and* click Save: the destination would be committed twice.

The compiler collapses the pair when all three hold: the press's key is `Enter`; the very next
actionable event is a `submit`; and the press's target is a field inside that form. The surviving step is
the **submit's click** — the commit-capable one — and the press is dropped. The person's keystroke is how
they triggered the save; the save is the action, and one click is the deterministic replay. When the
submit carries no submitter and no submit control, the press survives instead and the submit becomes the
same click-on-form fallback it is today, with a warning.

A `press(Enter)` that is **not** followed by a submit within the same action window (an autocomplete
accept, a search box) is untouched and stays a step of its own.

### 3.7 The run-creation gate — **a runner change, and an open question for T1 (Q3)**

`services/runner/creation.py:26` defines
`EXECUTOR_ACTIONS = frozenset({"navigate", "fill", "select", "click", "wait_for", "assert"})`, and
line 76 refuses any step whose action is not in `SUPPORTED_ACTIONS` with
`"... unsupported step action 'press'"`. So **a recipe containing a `press` step cannot be created as a
run at all** until that frozenset gains `press` — the golden e2e (§5) could never run a recorded Enter,
and nor could any real workflow.

The ticket's Out of scope says "Runner and protocol changes, except what the `press` executor half
needs. If more seems needed, stop and ask T1." The candidate change is one word in one frozenset (no
protocol change, no new step kind in the ledger), and it is arguably part of "the press executor half".
It is **not** taken unilaterally: this is open question 3 for T1. Options:

- **(a)** add `"press"` to `EXECUTOR_ACTIONS` and update the run-creation test's expected vocabulary
  (recommended — without it the ticket's own requirement 2 cannot be exercised end to end);
- **(b)** leave the runner untouched, in which case `press` steps compile but no run can be created with
  one, and the corpus golden must avoid them — which would make requirement 6's keyboard-autofill golden
  impossible.

**(a) is the plan's default assumption**, because (b) contradicts the ticket's own pass condition.

**Tests:** schema (a press step validates; a press with `commit: true` is rejected; a press without
`key` is rejected) in `packages/recipe/tests/test_recipe_schema.py`; recorder vitest (`keydown` → press
event; printable key → nothing; Backspace in a text field → nothing); executor vitest (the CDP calls and
the gate order; a withdrawn command sends no key).

---

## 4. Autocomplete / chip folding (Q4)

### 4.1 What the recording looks like, and what it compiles to

The ticket's sequence produces, in order (the accept's own event depends on how the person accepted;
§2.2's checkpoint runs before whichever one fires):

**Accepted by click** — type `ma`, click the suggestion:

```
fill(To, "ma")                  ← the checkpoint before the click sees the typed text
select(To, "Mary An ma@example.test")   ← the existing option branch (recorder.ts onClick, role === "option")
chip-add(To, "ma@example.test") ← the next checkpoint sees the chip; no fill for the cleared prefix (§2.1)
```

**Accepted by key** — type `ma`, press Enter/Tab:

```
fill(To, "ma")
press(Enter)
chip-add(To, "ma@example.test")
```

#### 4.1.1 The envelope carriers (added after the design review)

The compiler cannot fold what it cannot see: `Event` (packages/compiler/model.py) carries `kind`, `url`,
`step`, `observation` and `commit`, and a step may not carry extra fields (`additionalProperties: false`
on every step def). The relationship therefore rides the **envelope**, exactly as `warning` and `required`
already do (`EventInput`, apps/extension/src/shared/events.ts):

| envelope field | on which event | meaning |
|---|---|---|
| `chip: true` | the fill a chip-add produced | this fill's value is a chip's value, not typed text |
| `autocomplete: true` | the fill whose field showed a listbox of options for it | the field is an autocomplete, witnessed by the recorder |
| `supersedes: "<step id>"` | a chip-add fill that followed a prefix fill of the same field in the same interval | drop that fill; this chip is the same action |

`supersedes` is the mechanism the compiler folds on; `chip` and `autocomplete` are what make the fold
honest (and what the executor's replay rule mirrors, §4.3). All three are envelope-only, so
`schema-conformance.test.ts` — which validates `event.step` against `$defs/step` — keeps passing, and a
new assertion is added there for it.

#### 4.1.2 The fold

The compiler **folds** the run into one step, on `supersedes`:

- the superseded fill is **dropped** from the candidate list;
- the chip-add fill keeps the **chip's value** — its address, never the display name (§1.3);
- the accept step in between (a `select` of the option, or a `press`) is dropped when the fold consumed
  it, so a replay performs exactly one action ("compiles to one step").

Guards (all must hold, else no fold, and the steps stand as recorded together with an envelope warning
naming why):

- `supersedes` names a fill whose target is the **same field** (or an `role='option'` inside a listbox the
  field controls via `aria-controls`/`aria-owns`);
- the chip's value **contains** the typed text (case-insensitive) or the typed text was empty (a
  paste-only or click-only accept);
- the chip-add carries `autocomplete: true` — the recorder *witnessed* the field offering suggestions.

A chip that appears with **no** preceding fill or accept — a paste, or a widget pre-seeded by the page —
folds to the same single `fill`; there is nothing before it to consume. The negative case is covered too:
a `select` on a combobox with no chip and no containment does **not** fold, and both the select and the
following steps stand as recorded. A chip that does not contain the typed text does not fold either, and
keeps the envelope warning — the one place a fold could hide a wrong value, so it is a refusal, not a
guess.

### 4.2 The row value and the record key

Because the fill's value is the chip's value, the sample row carries the address; the Make recipe form's
record key therefore defaults to `mailjaitu@gmail.com`, not `"ma"`. **But the sample row is built in the
popup, not by the route**: `recordedValues` (apps/extension/src/popup/make-recipe.ts:104) walks the raw
events and the compile route only forwards what it is given (services/api/compile.py:41,69;
`sample_row`). A compiler-only fold would therefore still offer `"ma"` as a value and a key candidate.
`recordedValues` is taught the same `supersedes` rule — a superseded fill contributes no value and no
column — so the popup and the compiler cannot disagree. Tests on both sides assert it: a pytest on the
compile route's sample row and a popup vitest on the value/key lists.

### 4.3 On replay: containment matching

A `fill` is completed by the executor as follows, after the existing type-and-read-back. The precondition
is the **same predicate** the recorder used to set `autocomplete: true` (§4.1.1), so record and replay
cannot disagree about what an autocomplete is: the target has `role='combobox'`, or an
`aria-controls`/`aria-owns` pointing at a `[role='listbox']`, or — where neither is declared — a listbox
resolves for it by the existing `resolveListboxDescriptor` (step-runner.ts).

1. resolve the field's listbox; **no listbox** → nothing more happens, and the fill stands as today;
2. among that listbox's `role='option'` children, find those whose accessible text **contains** the typed
   value (case-insensitive, whitespace-normalised) *or* whose value-shaped token equals it;
3. exactly one match → accept it;
4. zero matches → **park**, `"no suggestion contains \"…\""`; more than one → **park**,
   `"more than one suggestion contains …"`.

Step 4 is what closes the design review's finding 7: a fill that the recorder *witnessed* as an
autocomplete (`autocomplete: true`, which the compiler carries onto the step as a `fill` with the chip's
value) must never fall through to "typed and moved on" — if the suggestion cannot be found, no half-written
value is submitted. The safer default is asymmetric on purpose: a fill **with** the witness parks when its
suggestion is missing; a fill **without** the witness is an ordinary fill.

**How the accept is performed.** The plan chooses a **click on the matched option**, through
`dispatchClick` — the PL-017b guarded path — rather than a synthesized `Enter`. Reasons: (a) it is
deterministic, whereas Enter depends on the widget's highlight state; (b) it reuses the one code path the
executor already proved safe; (c) the ticket does not prescribe the mechanism ("accept the suggestion"),
and a click is an accept. The corpus's Gmail-style widget accepts *both* keyboard (for recording) and a
suggestion click (for replay), so this is a property of the fixture, not an assumption about Gmail. A
widget that only accepts a key is reported as a limitation (§7).

This behaviour is **opt-in per field**: an ordinary text field with no listbox is filled exactly as
today. That is what keeps the lead-crm and messy-crm goldens unchanged, and both are re-run in the final
suite to prove it.

### 4.4 Chips removed

A removed chip is recorded as the click on its remove control (the ✕ the recorder already resolves by its
own accessible name). When a chip vanishes and no such control exists in the container, the recorder
records **nothing** and emits a warning naming the container, because guessing a list replacement is
exactly the kind of inference this ticket exists to remove. The corpus selftest proves the warning fires.

---

## 5. The widget corpus (Q5)

A new fixture set `fixtures/sites/widget-corpus/`, built on the messy-crm anatomy (§12.7): `server.py`
(stdlib only, one JSON handshake line, `/__test__` control API, `window.__SWITCHES__` injection),
`store.py`, `public/`, `selftest.py`, `README.md`. Every value is synthetic (`@example.test`).

| page | what it is | what is recorded | how it replays |
|---|---|---|---|
| `gmail-compose.html` | chip recipients with keyboard autofill (type → listbox → Enter/Tab accepts → chip), a `contenteditable` body, Send, an "Undo" toast | fill(To, prefix) folded to the chip value; fill(body) from the snapshot, though the editor fires no `input`; click(Send); the toast as the commit's assertion | type the row's address, accept the one suggestion whose text contains it, type the row's body, click Send; the fixture stores exactly one message per row |
| `plain-editor.html` | a `contenteditable` that fires `input` normally | fill with the body text | same, as a baseline that must not regress |
| `framework-editor.html` | a `contenteditable` mutated by script (direct DOM writes on a timer) with **no** `input` event | fill with the body text, caught only by the snapshot | same: the diff is the only source |
| `react-autocomplete.html` | autocomplete accepted by **clicking** the option | fill folded to the chip's value | containment match + click on the option |
| `shadow-form.html` | a form inside an **open shadow root** | fill/select on the shadow fields | same, through the shadow root |
| `iframe-form.html` | a form inside a **same-origin iframe** | fill on the framed field, with the frame path | same, with `frame` + `contextId` |

Per widget the plan commits to exactly one persisted value that the fixture's `/__test__/state` can
prove, so the corpus e2e's claim ("records and replays one value correctly") is checkable without
screenshots.

`selftest.py` checks: the handshake; every route; the DOM shape each page promises (the chip ARIA, the
editors' event behaviour, the shadow/iframe nesting); the state API; the switches; and the 1000-field
performance page of §1.6. It prints `SELFTEST PASSED (N checks)` and exits 0/1, like messy-crm's.

---

## 6. What stays out (Q6)

| surface | how it is detected | the message a person sees |
|---|---|---|
| **Canvas apps** | the click's resolved target is a `<canvas>`, or the element under the click point is a canvas with no accessible name | `"this control is drawn on a canvas: ProcessLens records fields and editable text, not pixels"` |
| **Cross-origin iframes** | `iframe.contentDocument` throws, or the frame's origin differs from the top document's | `"this page embeds another site's frame (origin N); ProcessLens records same-origin frames only"` |
| **Closed shadow roots** | `event.composedPath()[0] !== event.target` and the composed path's first node is not reachable in any tree the recorder can walk | `"this control is inside a closed shadow root; the page does not expose it, so it cannot be recorded"` |

Each is an envelope `warning` on the event (the existing channel: `recordedWarnings`,
make-recipe.ts:135), so it surfaces in the Make recipe form's warning list — `"step <id>: <message>"` —
and in the exported recording. None of them is a step, and none is guessed at. The corpus includes one
page per surface and the selftest asserts each message, so the wording is tested, not aspirational.

---

## 7. Risks, tests per item, and the e2e budget (Q7)

### 7.1 Risks

1. **The fill's autocomplete completion could change behaviour on ordinary fields.** Mitigation: it runs
   only for a combobox-like target with a resolved listbox; otherwise the fill is byte-identical to
   today. Both existing goldens are re-run in the final suite.
2. **A stale or wrong chip value in the fold.** Mitigation: the fold requires same-field containment and
   a chip that appeared in that field's container; otherwise nothing folds and the recorder's warning
   names it. Compiler unit tests cover the negative cases.
3. **Snapshots on very large pages.** Mitigation: the 1500 cap, the four-checkpoint rule, and the
   measured 1000-field budget in the selftest. The cap is reported as a warning when hit.
4. **Press steps causing an unintended submit on replay.** Mitigation: press is not commit-capable; the
   compiler never marks it a commit; the executor sends exactly one key pair with no focus change; the
   keys list is closed.
5. **Closed shadow roots are only detectable on the event path.** Mitigation: the message is emitted for
   the *click* that could not be described; the plan does not claim to detect them globally.
6. **Corpus e2e runtime.** Six widgets × (record + compile + run) is the longest thing in the suite.
   Mitigation: one test per widget inside the corpus file, the budget in §7.3, and a report that shows
   the real duration.
7. **The D-019 renumbering collides with the decision log's own rule** (§8.3, open question 1).
8. **A `press` step cannot reach a run at all without a one-word runner change** (§3.7). Called out
   explicitly rather than taken silently, per the ticket's stop-and-ask rule.
9. **A chip list and a combobox popup are structurally identical** (§1.2.1). The design review caught
   that the first draft would have read an autocomplete's suggestion list as a chip list and emitted a
   phantom step per suggestion; the exclusions in §1.2.1 are the fix, and the corpus's Gmail widget is
   the test that they hold.
10. **The chip-add and the accept can arrive in either order**, and a widget may clear the field and add
    the chip in one tick (§2.2). The `pending` buffer the recorder already keeps is the fallback; the
    fold (§4.1.2) makes both orders compile to one step.

### 7.2 Tests per item

| item | test | kind |
|---|---|---|
| req 1 snapshot scope | `apps/extension/tests/recorder-snapshot.test.ts` — inputs/textarea/select/contenteditable/role=textbox; a field that appears holding a value (§1.1.1); open shadow root; same-origin iframe; password never read (the value is not in any snapshot); the suggestion popup is not a chip list (§1.2.1) | vitest |
| req 1 chips | same file — chip recognised by ARIA/label, not by class; the chip's value is the address, not the display name; a `ul` nav menu is not a chip list | vitest |
| req 1 ordering | same file — the fill is emitted before the accept, for both the click and the pointerdown order; the ticket's exact sequence | vitest |
| req 1 change-back | same file — type, delete, click: the click is the only step; and a baseline taken at record start means a prefilled form yields no steps | vitest |
| req 2 press | `packages/recipe/tests/test_recipe_schema.py` (valid; `commit` rejected; `key` required); `recorder-keypress.test.ts`; `executor-press.test.ts` (CDP calls, gate order); `test_pl018.py` for the press/submit collapse (§3.6); `tests/` for the run-creation vocabulary (§3.7) | pytest + vitest |
| req 3 folding | `packages/compiler/tests/test_pl018.py` — `supersedes` folds, the negative cases, the sample row/key; a popup vitest for `recordedValues`; `executor-fill-autocomplete.test.ts` — containment match, single match clicks, zero/multiple parks, a witnessed autocomplete with no listbox parks | pytest + vitest |
| req 4 id reset | `recorder-session.test.ts` / `events` test — ids restart at 1 on start and on clear | vitest |
| req 5 weak observations | `packages/compiler/tests/test_pl018.py` — "Loading…", "Saving…", a `role=progressbar` and `aria-busy` text are not proposed | pytest |
| req 6 corpus | `fixtures/sites/widget-corpus/selftest.py`; `tests/e2e/test_gmail_compose_golden.py` | script + e2e |
| req 7 corpus e2e | `tests/e2e/test_widget_corpus.py` — one case per widget, each asserting its stored value; the report carries the "N of M" table | e2e |
| req 8 renumber | the DECISIONS.md text check (whichever form T1 picks) | review |

Each requirement also gets a revert-proof (revert, run, restore, record) in the report, as PL-017b did.

### 7.3 The e2e budget

- **`test_gmail_compose_golden.py`: at most 2 runs.**
- **`test_widget_corpus.py`: 2 runs.**
- **Then one full suite.**
- Before any run **past** this budget, work stops and T1 is asked — a disclosure afterwards is not
  enough (the ticket's words, and the instruction for this session).

Projected cost: the golden is a 3-row run (like messy-crm's, ~70 s per run including the stack); the
corpus is six record+compile+run cycles, estimated 4–6 minutes per run; the full suite is currently
9:43 and is expected to land around 16–20 minutes. No timeout is raised and no retry is added anywhere.

---

## 8. Requirements that are not among the seven questions

### 8.1 Reset the step-id counter (req 4)

`resetStepIds()` exists (events.ts) and is called only by tests. The counter lives in the *content
script's* module instance, so the reset must happen there: the popup's Start and Clear paths already
send `record:start` / `record:clear` to the worker (popup.ts:149, :162), and the worker already
broadcasts `record:state` to the tab (service-worker.ts, the `record:state` message). The recorder calls
`resetStepIds()` when it receives a state transition into `recording`, and on the clear message. Test:
two recordings in one page produce ids starting at 1 each time.

### 8.2 Weak observations are dropped (req 5)

The observation window already prefers confirmations (`CONFIRMATION_SELECTOR`). The new filter sits in
the **compiler**, so old recordings benefit too: `infer_assertion` refuses a candidate whose evidence is

- text matching `^(loading|saving|sending|working|please wait|one moment)\s*[…\.]*$` (case-insensitive),
- text ending in a bare ellipsis or `...` with no other content,
- anything read from a `role='progressbar'`/`role='status'` node whose `aria-busy='true'` at the moment
  it was read — the observation carries that flag (a small envelope addition, tested).

A step that loses its only evidence stays a non-commit step (a click with no assertion), exactly as an
unobserved click does today; a *commit* step without evidence still fails the schema, which is the
existing fail-closed behaviour.

### 8.3 D-019 renumbering (req 8) — **open question 1**

The ticket says "update the D-019 row so the layers read *AI suggestions and summary (PL-019),
verification (PL-020), self-healing (PL-021)*". The current row (DECISIONS.md:26) reads
"AI suggestions (PL-018) … (PL-019) … (PL-020)". So the change is a **renumbering of ticket references**,
not a change of substance.

But `internal/docs/DECISIONS.md:3-4` says: *"Protected file. Changes follow: proposal → T1 review →
owner approval → new entry here. Entries are never edited after acceptance; a later entry supersedes an
earlier one."*

**Recommended (default):** leave D-019 untouched and add **D-020** recording the recording-first
ordering and restating the layer list with the new numbers, marked as superseding D-019's numbering.
This respects the file's own rule and produces the text the ticket asks for, in the place the file
provides for it. **If T1 prefers the literal edit of D-019**, it is a one-cell change and will be done
instead. This is the only place the plan knowingly proposes something other than the ticket's literal
words.

---

## 9. Commit order (the order to review in)

1. `docs(plan): plan the state-diff recorder` — this file.
2. `feat(extension): snapshot and diff the page's editable state` — §1, §2, with the diff vitests
   (including the baseline, the chip-list exclusions and the pointerdown checkpoint).
3. `feat(recipe): add the press action to the schema and the types` — §3.2, with schema tests.
4. `feat(extension): record and replay key presses` — §3.3–§3.5 (recorder, executor, compiler +
   `normalize`), with vitests; the run-creation vocabulary only if T1 approves §3.7.
5. `feat(compiler): fold an autocompleted prefix into the chip's value` — §4.1–§4.2, with pytest and
   the popup `recordedValues` change, envelope `supersedes`/`chip`/`autocomplete`.
6. `feat(executor): complete an autocomplete fill by containment` — §4.3, with vitests.
7. `fix(extension): reset the recorded step ids when a recording starts or clears` — §8.1.
8. `feat(compiler): drop weak observations as assertions` — §8.2.
9. `feat(fixtures): add the widget corpus and its selftest` — §5.
10. `test(e2e): the Gmail-style golden and the corpus replay` — §5, §7.3.
11. `docs(decisions): record the recording-first layer order` (whichever form T1 picks), then
    `docs(report): record the PL-018 work, the coverage table and the real e2e output`.

Each commit is independently verifiable; the tree is green after each.

---

## 10. Open questions for T1

1. **D-019's form** (§8.3): superseding entry D-020 (recommended) or the literal one-cell edit?
2. **The accept mechanism on replay** (§4.3): click the matched option through the guarded click path
   (recommended), or synthesize `Enter`? The corpus supports both; the choice is which the *executor*
   performs when a real page offers a key-only widget.
3. **The run-creation vocabulary** (§3.7): may `EXECUTOR_ACTIONS` in `services/runner/creation.py` gain
   `"press"`? Without it a `press` step (and therefore requirement 2, and the keyboard-autofill golden)
   cannot be run at all. This is the one runner touch the plan asks for, and the ticket says to stop and
   ask.
4. **Chip removal without a remove control** (§4.4): warn and record nothing (recommended), or infer a
   list-replacement fill?
5. **Snapshot element cap** (§1.6): 1500 with a warning — a different number if T1 prefers.
6. **The corpus's iframe page**: same-origin iframe only (the ticket says so); the cross-origin case is a
   *warning* page, not a replay target. Confirm.
7. **The fold's witness requirement** (§4.1.2): the plan folds only when the recorder witnessed an
   autocomplete (`autocomplete: true`). A recording from an older build carries no witness, so it would
   not fold; that is the fail-safe direction (the steps stand as recorded). Confirm.

---

## 11. Changes from the design review

One reviewer pass (Sonnet 5.5, thinking high) read the plan against the code and returned **FAIL** with
seven blocking findings. Each was checked against the tree and the plan is revised as follows.

| # | finding | disposition |
|---|---|---|
| 1 | A newly appeared field holding text was ignored, and no baseline was defined | **Fixed**: §1.1.1 — a baseline at recording start, an arrival baseline, and a "appeared with a value" rule |
| 2 | The suggestion popup would be read as a chip list (a phantom step per suggestion) | **Fixed**: §1.2.1 — combobox-controlled listboxes and expand-only popups are excluded, and the tautological child clause is gone |
| 3 | The compiler cannot see the fold: no envelope carried the relationship | **Fixed**: §4.1.1 — the `chip`, `autocomplete` and `supersedes` envelope fields, with the conformance test extended |
| 4 | The sample row and record key are built in the **popup**, not the route | **Fixed**: §4.2 — `recordedValues` is taught the same rule, with tests on both sides |
| 5 | A `press` recipe cannot run: `ACTIONABLE_KINDS`, and `creation.py`'s `SUPPORTED_ACTIONS` | **Fixed + escalated**: §3.5 (normalize/compiler) and §3.7 (the runner question for T1, open question 3) |
| 6 | The checkpoint hooked `click`, not the earlier pointer event | **Fixed**: §2.2 — `pointerdown`/`mousedown` capture, one checkpoint per gesture |
| 7 | The fold's precondition and replay's opt-in disagreed | **Fixed**: §4.1.2 and §4.3 now share one predicate and one witness, and a witnessed fill with no listbox parks |
| 8 | A press(Enter) plus the form's submit would commit twice on replay | **Fixed**: §3.6 — the press folds into the submit it caused |

Findings the reviewer raised beyond the three named failures (the popup-as-chip-list, the missing
baseline, the runner gate) are folded in as first-class design sections rather than footnotes, because
each one would have shipped a silent defect.

---

## 12. Ground truth (what this plan was written from)

1. **The recorder listens to click, input, change, focusout, submit and pagehide** and nothing else
   (recorder.ts, `attachRecorder`'s listener block); **no `keydown`/`keyup` listener exists anywhere in
   `apps/extension/src`** — the only key events are the two `Input.dispatchKeyEvent` sends for Backspace
   in `dispatch.ts:161,168`. Step ids come from `nextStepId` (events.ts), which is reset only in tests.
2. **The envelope carries `warning` and `required`; steps never do** (`EventInput`, events.ts), and
   `schema-conformance.test.ts` validates every emitted step against `$defs/step` and asserts no recorder
   step carries `commit` or `assert_after`.
3. **The schema is strict**: `additionalProperties: false` on every step def; the step union is a `oneOf`
   over per-action defs (recipe.schema.json:549); `commit_flag` appears only in `step_fill`,
   `step_select`, `step_click`, `step_upload_file` (:600, :624, :652, :679); a commit step without
   `assert_after` is rejected by `commit_assertion_rule` (:535).
4. **The compiler drops unknown actions with a warning**: `STEP_ACTIONS = ("click", "fill", "select")`
   (packages/compiler/model.py:19) is consumed by `to_candidates` (compiler.py:121), while
   `ACTIONABLE_KINDS = frozenset({"click", "fill", "select", "submit"})` (model.py:16) is consumed by
   **`normalize.py:140`** — the design review corrected an earlier draft of this plan that said
   `to_candidates`. Both must learn `press` (§3.5).
5. **The executor performs four actions**: `EXECUTED_ACTIONS = new Set(["navigate", "fill", "select",
   "click"])` (executor.ts), and `validateRecipe` refuses anything else before step 1.
6. **The decision log is protected**: DECISIONS.md:3-4, quoted in §8.3; D-019 is at line 26.
7. **Two fixtures exist** (`fixtures/sites/lead-crm`, `fixtures/sites/messy-crm`); the messy server
   prints one JSON handshake line and serves an in-memory store (messy-crm/README.md:10-19); the e2e
   harness wires a second site with a subprocess fixture and a harness fixture
   (tests/e2e/conftest.py:82-83, :1145, `messy_harness`), gated by `PROCESSLENS_E2E=1`.
8. **The sample row is built in the popup**: `recordedValues` (apps/extension/src/popup/make-recipe.ts:104)
   derives it from the raw events; the compile route only forwards `sample_row`
   (services/api/compile.py:41,69) — so the fold must be taught in both places (§4.2).
9. **The run-creation gate refuses `press` today**: `EXECUTOR_ACTIONS` (services/runner/creation.py:26)
   and the check at :76 that raises `"unsupported step action"` (§3.7, open question 3).
8. **Warnings reach the person** through the event envelope (`recordedWarnings`, make-recipe.ts:135-142)
   and through the compile route's `warnings` list, both rendered in the Make recipe form's
   `#make-warnings`.
