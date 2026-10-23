# Handoff: ProcessLens — teach once, automate repetitive browser work

## Overview
ProcessLens lets an employee demonstrate a browser task once, review the recorded steps, and replay it for every row of a spreadsheet. One demonstration becomes a **versioned recipe** (a list of typed steps). A recipe runs once per input record. A **ledger** guarantees every record ends `verified`, `skipped`, `failed` or `parked` for a human — and is **never submitted twice**.

Hero use case: **Customer inquiry registration** into **Zoho CRM → Leads** from a 20-row CSV.

## About the design files
`ProcessLens.dc.html` is a **design reference built in HTML** — a working prototype of intended look and behavior, not production code. Recreate it in the target stack (recommended below). Open it directly in a browser (`support.js` must sit next to it). The prototype's logic class (bottom of the file, `class Component`) contains working reference implementations of the recorder, replay engine, ledger, and reconcile — read it; it is the most precise spec of behavior.

The Zoho CRM inside the prototype is **simulated** (fake browser frame). In production, ProcessLens drives the real site.

## Fidelity
**High-fidelity.** Final colors, type, spacing, copy and interactions. Recreate pixel-close.

---

## Recommended production architecture

| Piece | Responsibility | Suggested tech |
|---|---|---|
| **Browser extension** (Chrome/Edge MV3) | Recording (content script captures DOM events → semantic steps), supervised replay in the user's tab, side panel UI for live recording | TypeScript, MV3, `chrome.sidePanel`, `chrome.scripting`, content scripts |
| **Web app** | Dashboard, workflow editor, variables, rules, input review, run monitor, results, mappings, settings | React + TypeScript (Next.js/Vite), a component lib matching tokens below |
| **API + ledger** | Recipes (immutable versions), runs, per-record ledger with atomic state transitions, mappings, audit log | Postgres (row-level locks / `SELECT … FOR UPDATE SKIP LOCKED` for claims), REST or tRPC |
| **Cloud worker** (optional, unattended runs) | Executes recipes headlessly with a stored session | Playwright; same step executor as the extension |

Share the **step executor** (target resolution, fill/select/click, assertions) as one package used by both the extension and the worker.

### Extension — recording
- User opens target site in a normal tab → clicks extension → "Teach a task" → side panel opens.
- Content script attaches **capture-phase** listeners on `document`: `click`, `input`, `change`, `submit`, `focusin`.
- **Critical lesson from the prototype:** do *not* synchronously mutate anything (or post messages that trigger a re-render inside the page) from inside the listener before the page's own handlers run. Capture the fingerprint synchronously, then defer storage (`setTimeout(…,0)` / `postMessage`). Synchronous state updates on controlled inputs dropped keystrokes.
- Only `isTrusted` events are recorded (ignore replay-generated events).
- Rules:
  - Merge consecutive `input` events on the same element into **one Fill step** (key = `data-testid` || accessible name).
  - `select` → one **Select** step per change (merge consecutive changes on same element).
  - Clicks on inputs/labels/selects = focus noise → drop. Clicks with no interactive ancestor (`a,button,[role=button|link|tab]`) = stray → drop. Count both as "noise".
  - `submit` within 600 ms of a click on a `type=submit` button → drop (dedupe).
  - **Password fields are never recorded.** Show a masked "Password field skipped ••••••••" notice in the panel.
  - First step is always an implicit **Open** (navigate to starting URL).
- **Semantic fingerprint** per step (no coordinates): `action, role, accessibleName, label, testid, enclosing form/dialog name, url, recorded value, text`.
  - role: explicit `role` attr, else implicit (`a`→link, `button`→button, `select`→combobox, `input[type=search]`→searchbox, other inputs/textarea→textbox).
  - accessible name: `aria-label` → `<label for>` text (strip `*`) → `placeholder` → trimmed textContent (≤40 chars).
  - form: nearest `[data-form]`/`form[aria-label]`/`[role=dialog][aria-label]`.
- **Observe after each step** (~650 ms debounce): URL change, new toast text, inline validation errors, search result count. Turn into **suggested assertions**:
  - toast → `"Lead created" visible within 8s`
  - URL → `URL matches /crm/org/leads/:id`
  - search with 0 results → `0 results → continue · any match → skip as duplicate`
- Flash a highlight ring on the captured element (red while recording, indigo while replaying).

### Post-processing (on Stop)
- Plain-English names: `Open the Leads page`, `Search leads by email`, `Click "+ Create Lead"`, `Enter First Name`, `Choose Lead Owner`, `Click "Save" to create the lead`.
- Sections by heuristic: start `Open Leads`; searchbox fill → `Check for duplicate`; click whose name contains "create" → `Create lead`; commit → `Save and verify`.
- Click on a `type=submit` button = **Commit step**; default assertion `"Lead created" visible within 8s`.
- **Fallback targets** (tried in order): `role + name (within form)` → `test ID` → `label` → `fuzzy text`.

### Variables (bind to CSV)
On attaching the CSV, compare each recorded value with **row 1**:
- exact match to a column → `{{row.<col>}}` (case-insensitive for email)
- equals first word of `full_name` → derived `first_name = first_word(row.full_name)` ✓
- equals remaining words → `last_name = last_word(row.full_name)` ✓
- value is a salesperson → `owner = owner_by(service_interest)` (rule)
- else → constant
Each binding shows a check: evaluating the binding on row 1 must reproduce the recorded value (✓/✗). User can rebind via dropdown.

### Rules
- Record key: `lower(row.email)` (field + lowercase toggle).
- `Search finds a match → skip as duplicate`
- `Missing email → park for review`
- `Value not in dropdown → ask once, remember mapping`
- `Page fails to load → retry once then fail`
- Owner assignment: Data Analyst → Priya Nair; Cloud Engineering → Marcus Lee; UX Design, Cybersecurity → Sofia Alvarez.
- Approval policy: `first record` (default) | `every record` | `none`.

### Recipe versions
"Save" creates **Recipe vN** — immutable snapshot (steps, rules, ownerMap, approval, key). Runs pin a version. Editing after save marks `unsaved changes` → next save is vN+1. Version history drawer lists author + timestamp + JSON.

Recipe JSON shape (shown via "View recipe JSON", `J`):
```json
{
  "meta": {"id":"wf_customer_inquiry","name":"…","version":"v1","author":"…","created_at":"…","target":"https://crm.zoho.com/crm/org/leads","record_key":"lower(row.email)","approval":"first_record"},
  "variables": [{"name":"row.email","source":"csv"},{"name":"first_name","expr":"first_word(row.full_name)"}],
  "steps": [
    {"id":"s2","section":"Check for duplicate","name":"Search leads by email","action":"fill",
     "target":{"role":"searchbox","name":"Search leads"},
     "fallbacks":[{"testid":"zoho-search"},{"text_fuzzy":"Search leads"}],
     "value":"{{row.email}}",
     "assert_after":{"search_results":{"equals":0,"otherwise":"skip_duplicate"}}},
    {"id":"s12","section":"Save and verify","name":"Click \"Save\" to create the lead","action":"commit",
     "target":{"role":"button","name":"Save","within":{"form":"Create Lead"}},
     "assert_after":{"visible_text":"Lead created","timeout_ms":8000}}
  ],
  "rules": [{"when":"search.results > 0","then":"skip"}, {"assign":"owner","from":"service_interest","map":{…}}]
}
```

### Replay / step executor
For each step: wait for page idle → resolve target via fallbacks (retry once after reload if not found) → move ghost cursor → act:
- **fill:** native value setter + dispatch `input` (works with React-controlled inputs), typed char-by-char at watchable speed.
- **select:** native setter + `change`. If value not in options → apply saved mapping, else **ask human** (propose closest option by Levenshtein), optionally remember mapping.
- **click / commit:** `element.click()`.
- After searchbox fill + dup rule → if any result, **skip** with reason `Already in Zoho — "<name>", owner <owner>`.
- **Commit:** ledger → `submitting` → click → `submitted_unverified` → poll assertion up to timeout → `verified` or `failed` (capture screenshot + inline error text).
Speed toggle 1× / 4× (scales delays and assertion timeout display).

### Ledger state machine (per record)
`pending → claimed → checking_duplicate → filling → submitting → submitted_unverified → verified`
Terminal: `verified | skipped | parked | failed`. Side states: `needs_help`, `reconciling`.
- Pre-checks on claim: empty key → parked; key seen earlier in file → skipped ("Duplicate key in file — same as row N"); key already verified in ledger (e.g. by preview) → verified "not submitted again".
- **Crash + resume (key trust feature):** if a worker dies after commit, record stays `submitted_unverified`. On resume, **reconcile first**: search the target for the record key; if found → mark `verified` *without clicking Save again*; if not found → re-queue. Show this explicitly in the UI.

### Expected demo tally (sample CSV)
13 verified · 4 skipped (row 1 created during teaching, row 4 Ananya exists, row 8 Tom exists case-insensitive, row 11 dup of row 2) · 2 parked (rows 5, 12 missing email) · 1 failed (row 18 phone "call me after 5" rejected). Row 2 verified in preview, row 9 "Data Analytics" → human maps to "Data Analyst".

---

## Screens / views

App shell: left sidebar 220px (collapses to 60px icon rail < 1280px) — Dashboard, Workflows, Runs, Input data, Mappings, Settings; footer shows connected app + shortcuts. Top bar 52px: workspace switcher, search (⌘K, filters workflows/pages), live-run pill, avatar. On hero-flow screens a 48px **progress strip** shows `Teach → Recipe → Input → Preview → Run → Results` with ✓/current/upcoming states and a "Next step" CTA.

1. **Dashboard** — page title, "+ Teach a task (T)" primary; "Up next" card (context-aware next action); grid of 10 workflow cards (`repeat(auto-fill,minmax(250px,1fr))`, gap 12) with status chip (Not taught / Draft / Live · vN / Running / Not started); Recent runs table.
2. **Workflows** — table: #, Workflow, Target, Status, Steps, Version, Owner, Updated. Non-hero workflows open a static editor (Draft: read-only steps + amber issue banner; Not started: empty state, disabled Teach button + "connect the target app" note).
3. **Teach** — left: browser window (tab strip #DEE1E6, address bar, status badge "ProcessLens is recording this tab"; frame border 2px #DC2626 while recording, 2px #4338CA while automating). Right panel (380/330/290px by width):
   - Setup: workflow name, tab picker (only target tab selectable), "Start recording (R)", row-1 reference values.
   - Recording: **guided checklist** (5 tasks with values to type, live ticks and counters), live step stream (number, action badge, name, `role "name" · form "…"`, value chip, green "→ observed" lines, indigo "Suggested check"), noise counter, Discard / Stop recording (Esc).
4. **Workflow editor** — header: name, version chip, Version history, View recipe JSON (J), Save Recipe vN, Review input →. Tabs: Steps · Variables · Rules.
   - Steps: grouped by section headers; rows draggable (HTML5 DnD, indigo 3px drop indicator), action badge (OPEN/CLICK grey, FILL blue #1D4ED8/#E8EFFD, SELECT violet #6D28D9/#F1EBFE, COMMIT white on #18181B), value chip (binding = indigo mono chip), Commit badge, edit (inline name/value), delete (toast with Undo). Expand → fingerprint table + ranked fallbacks + `assert_after` + observed changes.
   - Variables: CSV card (Upload / Use sample / Replace), row-1 cells, bindings table (step, recorded value, bound-to chip + select, ✓/✗ check), derived variables.
   - Rules: record key, toggle rules list, owner assignment selects, approval segmented control.
5. **Input review** — table of all rows, every cell inline-editable, flagged cells amber border #F2C46B; validation chips per row (missing email, duplicate key in file, value not an option). Skeleton while loading. "Preview one record →".
6. **Preview** — browser + panel: step checklist with live dots (pending #D4D4CF / running #4338CA / done #15803D), resolved values and "via role + name". Stops before commit → **approval card** listing exact field values + binding source; Approve (Enter) clicks Save → Verified card → "Start batch run".
7. **Run monitor** — header: run #, recipe version, status chip, progress bar, 1×/4×, **Simulate crash** (arms a crash after next Save), Resume. Crash banner (red) + frozen overlay on browser. Right: **Needs your help** inbox card (amber), **Reconcile** card (indigo, step-by-step log), **Ledger** list with per-record state chips and Canary badge on row 1.
8. **Results** — stat cards (Verified/Skipped/Parked/Failed, clickable filters), dark **Time saved** card (manual 3 min/record vs. automated run + review), filter pills, table: #, record, outcome chip, reason, time, "Fix and re-queue"/"Fix and retry". Row expand: step timing bars, audit trail, failure screenshot thumbnail. Export CSV.
9. **Runs / Input data / Mappings / Settings** — tables + empty states; Settings has connected apps, replay speed, **Reset demo** with confirm.

Simulated Zoho (inside frame, Lato font): dark top bar #1C2B3A with module tabs, Leads list (search by email, case-insensitive; "+ Create Lead" #2B6FD6), Create Lead form (Lead Owner*, First, Last*, Email, Phone, Lead Source, Service Interest, Description; Save/Cancel), validation (red #D93025 inline errors), green toast #1E8E3E "Lead created", lead detail page.

## Interactions & behavior
- Keyboard: `T` teach, `R` start recording, `Esc` stop recording / close overlays, `J` toggle JSON, `Enter` approve in preview, `⌘K` search.
- Toasts bottom-right (#18181B), 3.4 s, optional action (Undo).
- Ghost cursor: indigo arrow + "ProcessLens" label, `transform` transition 400ms/speed `cubic-bezier(.3,.1,.2,1)`, click ring 0.5s.
- Element flash: 2px ring, fade + spread 0.9s.
- New list items: fade-up 0.25s.
- Status chips everywhere use the semantic palette below.

## State (single session, in memory in prototype; persisted in production)
`zoho {leads, route, form, errors, toast}` · `rec {status, steps, noise}` · `wf {steps, rows, cols, rules, ownerMap, approval, keyField, keyLower, versions[], dirty}` · `pv {status, per-step status/values}` · `run {status, ledger[], queue, speed, armed, help, recon}` · `keyLedger {key → verified via}` · `mappings[]` · `runs[]`.

## Design tokens
- Fonts: **Geist** (UI, 400/500/600), **Geist Mono** (recipe/IDs/values), Lato (simulated Zoho only).
- Type: body 13px/1.45; small 11.5–12px; page title 22px/600, −0.02em; section 14px/600.
- Neutrals: page #F6F6F4 · surface #FFFFFF · sidebar #FBFBFA · border #E6E6E1 · control border #DADAD5 · divider #F0F0EC · text #18181B · text-2 #52525B · text-3 #71717A · muted #A1A1AA.
- Accent: #4338CA (hover #3730A3), soft #EEF0FF, ring rgba(67,56,202,.12), light #A5B4FC.
- Status: verified #15803D/#E7F5EC · skipped #52525B/#EDEDEA · parked/help #B45309/#FEF0DC · failed #B91C1C/#FDECEC · unverified #854D0E/#FEF6C7 · in-progress #4338CA/#EEF0FF · recording #DC2626.
- Radius: controls 6px, cards 10px, chips 999px, badges 4px.
- Heights: buttons 32–34px, chips 20–22px, rows ~40px.
- Shadow: browser frame `0 10px 30px rgba(24,24,27,.10)`; menus `0 12px 32px rgba(24,24,27,.12)`.

## Assets
No images. Icons are inline 24-viewBox stroke paths (Lucide-style); swap for your icon library. "Zoho CRM" is a plain text wordmark — do not use Zoho's logo.

## Files
- `ProcessLens.dc.html` — full prototype (template + logic class). Open in a browser with `support.js` alongside.
- `support.js` — prototype runtime only; not needed in production.
