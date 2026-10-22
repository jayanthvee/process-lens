# Deploying ProcessLens on Render

Render runs the same Docker image Railway would (`Dockerfile`). Everything Render needs is in
`render.yaml` at the repository root: the **api** web service (the API, the hub the extension
connects to, and the dashboard), the **supervisor** background worker that starts a runner for
each waiting run, and **Postgres**. `internal/docs/DEPLOY.md` (Railway) is still right about
what each variable means.

## Before you start

- **The extension needs a fixed id.** Run the extension's make-key step and commit
  `extension-key.pub` (PL-052), then build the extension. The id Chrome shows in
  `chrome://extensions` is the `PROCESSLENS_EXTENSION_ID` you'll paste below. Without a fixed
  key, the id changes with the folder and the hub refuses the extension.

## 1. Create the Blueprint (about 5 minutes)

1. In Render: **New → Blueprint**. Connect GitHub and pick `jayanthvee/process-lens`, branch
   `main`.
2. Render reads `render.yaml` and lists three resources: `processlens-api`,
   `processlens-supervisor` and `processlens-db`. It asks for the two values marked
   `sync: false`:
   - **PROCESSLENS_EXTENSION_ID** (on the api): the 32-character id from `chrome://extensions`.
   - **PROCESSLENS_API_URL** (on the supervisor): leave it as a placeholder for now, such as
     `https://example.com`. You'll know the real address after step 3.
3. Press **Apply**. Postgres is created first, then both services build from the Dockerfile,
   which takes about 5–8 minutes.

## 2. Point the supervisor at the api

4. Open **processlens-api** and copy its address from the top of the page
   (`https://processlens-api-xxxx.onrender.com`).
5. Open **processlens-supervisor → Environment**, set **PROCESSLENS_API_URL** to that address,
   and save. Render restarts the supervisor by itself.

## 3. Check it

6. Open `<api address>/health`. It answers `{"status": "ok", "db": true}` when the API is up and
   Postgres is reachable.
7. Open `<api address>` itself: it asks for the team access code (PL-086). The code is on
   **processlens-api → Environment → PROCESSLENS_ACCESS_CODE**, which Render generated. Paste it
   once; this browser stays signed in for 30 days.
8. In the supervisor's **Logs**, a line every few seconds says it's watching for runs, with no
   errors.

## 4. Build the extension for it

9. Build the copy you hand out with the address and the access code baked in, so nobody types
   either:

   ```bash
   PROCESSLENS_HUB_URL=https://<api address> PROCESSLENS_ACCESS_CODE=<the code> npm --prefix apps/extension run package
   ```

   The zip lands in `apps/extension/release/` (git ignores it; it holds the code, so share it only
   with the team). Unzip it and use **Load unpacked**. The popup connects by itself; the address
   and the code are also under **More** if they ever change. The extension switches `https://`
   to `wss://` for the hub by itself.

## Hosted mode, in one paragraph

On a laptop the API answers only this machine. Hosted, it answers only its public address
(`RENDER_EXTERNAL_URL`, or `PROCESSLENS_PUBLIC_URL` for your own domain), and only callers with a
credential: the dashboard's cookie, the extension's access code, or the supervisor's runner
secret. `/health` stays open for Render's check. Rotating `PROCESSLENS_ACCESS_CODE` signs everyone
out; rebuild the extension with the new one.

## What it costs

These are the plans `render.yaml` asks for; check Render's pricing page for current figures.

- Web service (0.5 CPU, 512 MB, `0.5c-512mb`): about $7 a month.
- Background worker (`0.5c-512mb`): about $7 a month.
- Postgres (`0.1c-256mb`): about $6 a month.

Billing is prorated, so two weeks of testing costs about half a month. To stop paying while
nobody is using it, suspend both services from their Settings page; Postgres keeps the data.

## Moving off Render later

Nothing here is Render-specific apart from this file and this page. The image, the start
commands and the variables are the same ones Railway uses.
