# Messy CRM fixture (synthetic)

A tiny CRM whose pages are shaped like the real ones that broke the recorder:
Gmail and a lead CRM. `lead-crm` is tidy and a hand-written recipe drives it;
this site is the opposite on purpose, and it is the target for the recorder's
end-to-end run (PL-016b). Everything is in memory, every file is UTF-8, the
only dependency is the Python standard library, and nothing talks to the
network. Every address is fake on purpose (`alice@example.test`), and the store
refuses any other domain, so a real address cannot end up in a test run.

```
python server.py --port 0
{"url": "http://127.0.0.1:53123", "port": 53123, "crm_url": "http://127.0.0.1:53123", "crm_port": 53123}
```

`server.py` prints exactly one JSON line on stdout (access logs go to stderr)
and then serves. `url`, `port` and the `crm_*` aliases carry the same origin,
so a caller that already parses the `lead-crm` line can parse this one.

Run the fixture's own proof:

```
.\.venv\Scripts\python.exe fixtures\sites\messy-crm\selftest.py
```

## Files

| file | what it is |
|---|---|
| `store.py` | in-memory `MessyStore` (the saved records) and `DIRECTORY` (the synthetic address book the combobox filters), behind a `threading.Lock`. Pure logic, no HTTP. |
| `server.py` | `ThreadingHTTPServer` + `MessyCrmHandler`, `IdMinter` (the generated ids), `make_server(port)` for in-process use, `main()` for a real process. |
| `public/` | `index.html`, `recipients.html`, `notes.html`, `saved.html`, `second-page.html`, `app.js`, `styles.css`. |
| `selftest.py` | standalone script (not pytest): starts the server in-process, exercises every route, the test API and every switch, prints `SELFTEST PASSED` / `SELFTEST FAILED`, exits 0/1. |

## The four messes

1. **Ids are regenerated on every page load.** Each page is a template with
   `{{ID_x}}` tokens; the server mints a fresh id for every one of them on
   every request, in the three shapes a real page mixes: React's `:r1:`,
   Gmail's `:1a`, and a 16-character hex blob. Two loads of the same page have
   no id in common. A recorder that trusted an id records a selector that dies
   on the next load; the label, the `name`, the `aria-label` and the
   `data-testid` never change, so a correct recorder still has a hold.
2. **The recipient chips are removable and nested.** Each chip is a
   `role="option"` and is clickable, and it contains its own ✕. The ✕'s
   `aria-label` is `Remove <address>`, and that name has to win over the chip's.
   One chip (`alice@example.test`) is pre-rendered in the markup, and `app.js`
   adds more exactly the same way.
3. **The notes box is a `contenteditable`.** A plain form POST cannot submit
   one; `app.js` mirrors its text into the hidden `notes` input on every
   keystroke and again on submit.
4. **The save is a full-page POST.** `POST /save` answers **303** to
   `/saved.html`, which renders `<div role="status">Saved</div>` in its markup,
   so the status is observable as soon as the next page loads. `app.js` never
   posts the save itself (no fetch to the save route, no XHR), so this is a
   real top-level navigation.

### The stable shape the recorder's tests pin

The served recipients page contains this, as static markup in
`public/recipients.html` and `public/index.html` (only the enclosing ids are
regenerated):

```html
<div id="{{ID_CHIP_ALICE}}" class="chip" role="option" aria-selected="false" data-testid="recipient-chip" data-recipient="alice@example.test"><span class="chip-name">alice@example.test</span><button type="button" aria-label="Remove alice@example.test">✕</button></div>
```

* the chip container is itself clickable (`role="option"`), and the ✕ is nested
  inside it: that is the trap, and the ✕'s own name must win;
* the ✕ is `<button type="button" aria-label="Remove alice@example.test">` with
  that exact address, and it carries no `id`, no `class` and no `data-testid`:
  its accessible name is deliberately its only handle. `app.js` builds chips
  added by script in exactly the same shape;
* the hidden `recipients` input is pre-filled with the same address, so a save
  works before any script runs.

Keep that shape stable: the recorder's unit tests use it.

## Routes

| route | answer |
|---|---|
| `GET /`, `GET /index.html` | the compose page: combobox, chips, notes box, save form. Links to the second page, the recipients page and the notes page. |
| `GET /recipients.html`, `GET /recipients` | the chip mess on its own, with a save form and a link back. |
| `GET /notes.html`, `GET /notes` | the `contenteditable` mess on its own, with a save form and a link back. |
| `GET /saved.html`, `GET /saved` | the page a save lands on: `<div role="status">Saved</div>` plus what the store holds. |
| `GET /second-page.html`, `GET /second-page` | a second page with a link back to the compose page. |
| `GET /recipients.json` | the whole synthetic directory; `?q=ali` filters it on the name and the address, the way the combobox does. |
| `POST /save` | validates, stores, and answers `303` to `/saved.html`. A refused save answers the page its form came from (`page=compose\|recipients\|notes`) with what was typed and a `role="alert"` error. |
| `GET /app.js`, `GET /styles.css`, `GET /favicon.ico` | the two static assets, and a 204. |

A save with no recipients (the notes page posts none) is stored with none; a
recipient that is not `@example.test` is refused.

## Test API (this origin only)

| route | answer |
|---|---|
| `GET /__test__/state` | `{"saves": <int>, "recipients": {"<email>": <count>}, "notes": ["..."], "switches": {...}, ...}` |
| `POST /__test__/reset` | empties the store, clears the switches and the counters, `{"ok": true}` |
| `POST /__test__/switches` | merges a JSON body into the switches, `{"switches": {...}}` |

`GET /__test__/state` is the fixture's own truth: `recipients` maps each stored
address to how many saved records hold it, and `notes` lists every stored note
in save order. Extra keys (`save_posts`, `created`, `rejected`, `renders`,
`suggest_requests`, `static_chip`, `directory_size`) are informational.
`renders` is the id sequence, so a test can see that a page really was served
fresh.

## Drift matrix

The expected ledger outcome is what the end-to-end test should see for the
record, not what the page shows.

| switch | effect | expected outcome | asserted by |
|---|---|---|---|
| `slow_save_ms` (int, 0) | `POST /save` sleeps that long before answering | the save still lands, one redirect later | `selftest.py::slow_save_ms` |
| `slow_suggest_ms` (int, 0) | `GET /recipients.json` sleeps that long | a fast typist races the options: the listbox lags the keystrokes | `selftest.py::slow_suggest_ms` |
| `stale_status` (bool) | a page that saved nothing renders `<div role="status">Saved</div>` on load | the markup is byte-identical to a real status, so the page cannot prove a save: only `/__test__/state` can, where `saves` stays `0`. `app.js` additionally marks the element with `data-stale-status="true"` | `selftest.py::stale_status` |
| `reject_recipients` (bool) | `POST /save` answers the form's page with `<div role="alert" class="field-error">that recipient is not in the directory</div>`, status 200, nothing stored | `parked` (or `failed`) - the destination refused the record | `selftest.py::reject_recipients` |
| `suggest_empty` (bool) | `GET /recipients.json` always answers `[]` | the broken-autocomplete case: typing surfaces no option, so a recorder has nothing to pick | `selftest.py::suggest_empty` |

Checkout behaviour the matrix implies: `POST /__test__/reset` between cases, so
one switch cannot leak into the next record.

## Notes on the messiness

* The chips listbox holds `role="option"` chips that each contain a button,
  which no ARIA pattern endorses. That is the point: it is what a real compose
  window hands a resolver, and requirement 8 of PL-016 is about surviving it.
* `app.js` mints its own `:r1:`-style ids for the options it renders, so an
  option's id is fresh even before the next page load: the list is thrown away
  on the next keystroke.
* The two `slow_*` delays and the three booleans are all off on a fresh server,
  so the fixture is the happy path until a test asks for a trap.
* Nothing here records keyboard-only edits: the fixture does offer Escape and
  Enter on the combobox for a person, and the recorder has no `press` action,
  so none of that is observable to it.
