# ProcessLens Recorder (Chrome extension)

A Manifest V3 extension that records a browser demonstration as **semantic steps** instead of
coordinate clicks. Each captured interaction carries a target ladder — accessible role and name
inside a named container, then a test id, then the label, then a stable CSS selector, then fuzzy
text — so a step still finds its element after a page regenerates its markup.

## What it captures

- **Clicks** on buttons, links, tabs, menu items, and options.
- **Typing** into text fields, coalesced into one `fill` step per field (captured when the field
  loses focus, so keystrokes do not become noise).
- **Selections** in dropdowns, written as a `select` step.
- **Form submits**, recorded as a `submit` event that targets the control that submitted.

## Security rule: passwords are never captured

An `input[type="password"]` (or a field with a password `autocomplete` hint) is skipped at the moment
of capture: its value is not read, buffered, or sent. This is checked in the recorder and covered by
tests, including one that types a secret and asserts it appears nowhere in the payload.

## Build

The extension carries the web app's side panel, so both packages need their dependencies:

```bash
npm --prefix apps/web ci
npm --prefix apps/extension ci
npm --prefix apps/extension run build   # writes a loadable extension to apps/extension/dist/
```

## Load it in Chrome

1. Open `chrome://extensions`.
2. Turn on **Developer mode**.
3. Choose **Load unpacked** and select `apps/extension/dist`.

## Use it

1. Click the extension's toolbar icon: the ProcessLens side panel opens beside the page.
2. In **Teach**, press **Start teaching**, then demonstrate the task on the page.
3. Press **Stop**, remove any step you did not mean, and press **Save as task**.
4. **Tasks** runs a task with a spreadsheet, or once when it needs no columns; **Runs** shows
   progress and puts a parked row's fix buttons on its own row.

The panel carries the ProcessLens address and access code the build was given; there is no address
field and no website to open.

## Development

```bash
npm run typecheck   # tsc --noEmit
npm run test        # vitest, jsdom
npm run build       # bundle into dist/
npm run validate    # check the built package is structurally loadable
npm run e2e         # load it in a real Chromium browser and record a demo
npm run check       # typecheck, test, build, validate
```

`npm run e2e` starts a local page in headless Chromium, loads the extension, starts recording
through the side panel, performs a small interaction, and reads back what the worker stored. It
loads the built package as an unpacked extension; set `CHROME_PATH` to choose a browser. It is not
part of `npm test`.

### One action, two events

A button that submits a form produces a `click` and then a `submit`, because the browser really does
both. The recorder keeps both: it records what happened, and the compiler later folds them into one
step. A form submitted without a click (for example with Enter) produces only the `submit`.

## Layout

```
manifest.json            extension manifest (MV3)
src/shared/              recipe types, fingerprints, event envelope, message protocol
src/content/recorder.ts  the recorder that runs in the page
src/background/          the service worker: state, session history, streaming, panel behavior
src/popup/               the compile-and-save logic the panel imports (and its legacy markup)
../web/src/panel/        the side panel: shell, Teach, Tasks, Runs (built into dist/panel/)
tests/                   fingerprint, recorder, panel, and schema-conformance tests
scripts/build.mjs        bundles src/ and the web app's panel into dist/
scripts/validate-package.mjs  checks the built package loads structurally
scripts/e2e-smoke.mjs    loads it in a real browser and records a demo
```

Captured steps are validated against the recipe JSON Schema in
[`packages/recipe/schema/recipe.schema.json`](../../packages/recipe/schema/recipe.schema.json) by the
conformance tests, so the recorder and the recipe format cannot drift apart.
