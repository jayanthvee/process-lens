# Lead CRM fixture (synthetic)

A tiny lead CRM used as the destination in the ProcessLens end-to-end tests.
Everything is in memory, every file is UTF-8, the only dependency is the
Python standard library, and nothing talks to the network. The addresses are
fake on purpose (`ana@example.test`).

```
python server.py --port 0
{"crm_url": "http://127.0.0.1:53123", "partner_url": "http://127.0.0.1:53124", "crm_port": 53123, "partner_port": 53124}
```

`server.py` prints exactly one JSON line on stdout (access logs go to stderr)
and then serves. The CRM and the partner site are always on **different
ports**, so the off-origin test has a real second origin
(`index.html` links to `<partner_url>/partner.html`).

Run the fixture's own proof:

```
.\.venv\Scripts\python.exe fixtures\sites\lead-crm\selftest.py
```

## Files

| file | what it is |
|---|---|
| `store.py` | in-memory `LeadStore`, keyed by lowercased email, behind a `threading.Lock`. Pure logic, no HTTP. |
| `server.py` | `ThreadingHTTPServer` + `LeadCrmHandler` (CRM origin) and `PartnerHandler` (partner origin). `make_server(port, partner_port)` for in-process use, `main()` for a real process. |
| `public/` | `index.html`, `new-lead.html`, `login.html`, `challenge.html`, `partner.html`, `app.js`, `styles.css`. |
| `recipe.json` | the v1 recipe the batch test pins. |
| `selftest.py` | standalone script (not pytest): starts the server in-process, exercises every route, the test API and every switch, prints `SELFTEST PASSED` / `SELFTEST FAILED`, exits 0/1. |

## Pages and the semantic ladder

* `index.html` - search form. `#lead-search` / `data-testid="lead-search"` /
  `<label for="lead-search">Search leads</label>` (so the accessible name is
  "Search leads"); the button is `#lead-search-button` /
  `data-testid="lead-search-button"` with role `button` and accessible name
  `Search`; "New lead" links to `new-lead.html`; "Open partner site" links to
  the partner origin. Results are an ARIA table (`role="table"`,
  `aria-label="Search results"`) whose rows are `<div data-lead-row>`. A hit
  gives exactly one `[data-lead-row]`; a miss gives none plus `No leads found`.
  The server renders results for `GET /?q=`, and `GET /search.json?q=` returns
  the same data for `app.js`'s live search. `GET /?q=` with an empty query is
  a search with no matches, so it says `No leads found`; a plain `GET /` has
  no results region at all.
* `new-lead.html` - `Email`, `Interest` (`Interest` with an empty value, then
  `Data course`), `Company`, and a `Save` button (`data-testid="lead-save"`,
  accessible name exactly `Save`). Posts `application/x-www-form-urlencoded`
  to `/leads/new`. One hidden decoy listbox with two duplicate `Data course`
  options sits between the select and the Company field, so an option lookup
  has to be scoped to the right listbox. No `required` attributes: native form
  validation would silently block the POST, so the server validates instead.
  A rejected form (duplicate email, `required_company`) comes back with what
  was already typed, so the record can be corrected instead of retyped. On a
  success the server renders exactly `<div role="status">Lead created</div>`;
  `app.js` never removes it, so a text assertion cannot lose the race.
* `login.html` - visible password field, `Sign in` button, title `Sign in`.
  A stray `POST /login.html` re-renders the page rather than 404ing.
* `challenge.html` - `<title>Just a moment</title>` and an iframe whose `src`
  is the Turnstile URL. Nothing depends on it resolving; it never loads.
* `partner.html` - `<h1>Partner</h1>`, served on the partner port only.

`server.py` injects the current switches into every page by replacing the
`<!--SWITCHES-->` token with `<script>window.__SWITCHES__ = {...};</script>`,
so a page (and `app.js`) can render a switch's effect.

## Test API (CRM origin only, never the partner origin)

| route | answer |
|---|---|
| `GET /__test__/state` | `{"leads": {"<email>": <count>}, "saves": <int>, "switches": {...}, ...}` |
| `POST /__test__/reset` | empties the store, clears the switches and the counters, `{"ok": true}` |
| `POST /__test__/switches` | merges a JSON body into the switches, `{"switches": {...}}` |
| `POST /__test__/mark-save` | `{"saves": <int>}`, one more save seen by the server |

`leads` maps each stored email to its count, which is how a duplicate save is
caught: a second save of the same address leaves the count at `1` while
`saves` goes up. Extra keys (`save_posts`, `mark_save_calls`, `created`,
`duplicates`, `rejected`, `disconnect_pending`) are informational; `saves`
counts every save attempt that reached the server, that is handled
`POST /leads/new` requests plus `mark-save` calls.

## Drift matrix

The expected ledger outcome is what the batch test should see for the record,
not what the page shows. `GET /__test__/state` is the fixture's own truth.

| switch | effect | expected ledger outcome | asserted by |
|---|---|---|---|
| `slow_save_ms` (int, 0) | `/leads/new` sleeps that long before answering; `app.js` shows the busy hint | `verified` - the save lands late, the commit is still one lead | `selftest.py::slow_save_ms`, and the e2e latency case |
| `stale_toast` (bool) | `new-lead.html` renders `<div role="status">Lead created</div>` on load, before any save | the markup is byte-identical to a real toast, so the page cannot prove a save: only `/__test__/state` can, where the lead is absent. `app.js` additionally marks the element with `data-stale-toast="true"` for a test that wants it | `selftest.py::stale_toast`, e2e "stale toast" case |
| `login_page` (bool) | `/leads/new` answers 302 to `/login.html` instead of saving | `parked` (or `failed`) - nothing was stored, so the commit cannot be `verified` | `selftest.py::login_page`, e2e "login wall" case |
| `challenge_page` (bool) | `/leads/new` answers `challenge.html` with status 200 instead of saving | `parked` (or `failed`) - the Turnstile page is a wall, not a save; `saves` still counts the attempt | `selftest.py::challenge_page`, e2e "challenge" case |
| `field_in_iframe` (bool) | the Email input is rendered inside a same-origin `<iframe srcdoc>`; `app.js` mirrors it into a hidden bridge input on submit | `verified` only if the filler traversed the frame; a frame-blind fill leaves the email empty and the server rejects the save as `parked` | `selftest.py::field_in_iframe`, e2e "field in iframe" case |
| `disconnect_mid_save` (bool) | the save happens normally and the state reports `"disconnect_pending": true` | the state pinpoints the save during which the extension socket should be dropped; the lead itself is `verified` unless the commit is missed | `selftest.py::disconnect_mid_save`, e2e "disconnect" case |
| `required_company` (bool) | an empty Company answers `<div role="alert" class="field-error">company is required</div>`, status 200, nothing stored | `parked` - the destination refused the record and `leads` stays empty | `selftest.py::required_company`, e2e "required field" case |

Checkout behaviour the matrix implies: `POST /__test__/reset` between cases,
so one switch cannot leak into the next record.

## `recipe.json`

A valid v1 recipe (validated against
`packages/recipe/schema/recipe.schema.json`). Two things to know before
ingesting it:

1. `allowed_origins` and the `crm_base` variable both contain the placeholder
   port **`0`** (`http://127.0.0.1:0`). The e2e test rewrites both to
   `crm_port` from the server's JSON line before ingest.
2. `approval_policy.commit` is `"none"`, so the batch runs unattended: the
   first commit is not previewed.

Steps: navigate to the CRM index, fill the search box from `email`, click
`Search`, branch on the results table (a cell holding the email means the lead
exists, so `end_record` `skipped`), navigate to the new-lead page, fill
`Email`, select `Interest`, fill `Company` inside a branch that parks a record
with no company, then click `Save` with `commit: true` and
`assert_after: {"text_visible": "Lead created", "timeout_ms": 8000}`.

`meta.input_schema.rules` holds one precheck rule, `{"when": {"missing":
"email"}, "then": "park", "reason": "no email address in this record"}`, which
is the one parked record of the batch.
