# Deploying ProcessLens on Railway

One page, in order. The repo is deployable (PL-051); this is the owner's path to a running
deployment. No step here needs anything but a Railway account and this repository.

ProcessLens is **two services on one image**: the **API** (serves the web app, the HTTP API and
the extension's WebSocket hub) and the **supervisor** (starts a runner worker when a run is
waiting and a Chrome session is attached). The image's default command is the API; the
supervisor service overrides the start command.

## 1. Create the project

1. Sign in at railway.com and choose **New Project → Deploy from GitHub repo**. Pick this
   repository and the `pl-051` branch (or `main` once merged).
2. Railway detects the root `Dockerfile` (`railway.json` in the repo already points at it) and
   builds. Name this service **api**.
3. In the service's **Settings → Networking → Generate Domain**, expose it publicly. Note the
   domain (e.g. `https://processlens-api.up.railway.app`) — this is `PROCESSLENS_PUBLIC_URL`.

## 2. Add Postgres

4. In the same project, **New → Database → PostgreSQL**. Railway starts it and exposes
   `DATABASE_URL` as a reference variable. In the **api** service's Variables, add
   `DATABASE_URL` as a **reference** to the Postgres plugin's `${{Postgres.DATABASE_URL}}`.

## 3. Set the variables

5. On each service's **Variables** tab, set what that service needs:

| Name | What it is | Example | Required | Services |
| --- | --- | --- | --- | --- |
| `DATABASE_URL` | The Postgres connection string (reference the plugin's variable, do not paste credentials) | `${{Postgres.DATABASE_URL}}` | yes | api, supervisor |
| `PROCESSLENS_RUNNER_SECRET` | Shared secret the runner workers authenticate to the hub with. Generate one yourself (e.g. `openssl rand -base64 32`); never commit it | `<generate one>` | yes | api, supervisor |
| `PROCESSLENS_EXTENSION_ID` | Your unpacked extension's id from `chrome://extensions`; the hub refuses every other extension origin, and an unset id accepts none | `<32-character id>` | yes | api |
| `PROCESSLENS_API_URL` | The API service's public URL, as the supervisor reaches it | `https://processlens-api.up.railway.app` | yes | supervisor |
| `PROCESSLENS_PUBLIC_URL` | The address you give coworkers for the dashboard; today nothing in the services reads it, it documents the intent | `https://processlens-api.up.railway.app` | no | api |
| `PROCESSLENS_OPERATOR_ID` | Overrides which user row the API treats as the operator; leave unset and `services.deploy api` ensures one owner in the `processlens` tenant on every deploy | `<uuid>` | no | api |
| `PORT` | The port to serve on | `8000` | no (Railway sets it; the image defaults to 8000) | api |

## 4. Deploy both services

6. The **api** service starts with the repo's `railway.json` command
   (`python -m services.deploy api`), which applies migrations, ensures the operator, and serves
   on `0.0.0.0:$PORT`. Railway's healthcheck watches `/health`.
7. For the supervisor, Railway's per-service `railway.json` cannot declare a second service from
   one config file. In the same project choose **New → GitHub Repo** again, pick the same repo,
   name the service **supervisor**, and in its Settings set the **Custom Start Command** to
   `python -m services.deploy supervisor`. It reads the same image (same builder settings) and
   binds no port — give it no public domain.

## 5. Verify

8. Open `<your domain>/health`. It answers `{"status": "ok", "db": true}` when the API is up and
   Postgres is reachable — that is the whole smoke test.
9. Log in at the same domain: the ensured operator owns the empty deployment; the first task you
   create is yours.

## 6. Point the extension at it

10. In the ProcessLens popup, set the address to `https://<your domain>` and press Connect.
    WebSockets go through Railway's public domain: the popup dials `wss://` there, not `ws://`.
    The extension already handles this — `socketBase` in
    `apps/extension/src/background/command-session.ts` rewrites an `https://` address to
    `wss://` (and `http://` to `ws://`) before dialing `/api/v1/executor/ws`, so nothing needs to
    change for the deployed URL.
