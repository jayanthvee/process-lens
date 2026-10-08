# ProcessLens

**Teach a browser task once. Run it on every record, reliably.**

ProcessLens turns a single demonstration of a repetitive web task, such as entering leads into a CRM,
registering candidates, or keying in invoices, into a reusable workflow that runs across a whole
spreadsheet or folder of documents. Every record ends in a clear, explained state, every step is
recorded, and nothing is submitted twice.

> **Status:** early development. Not ready for production use or for real data.

## Highlights

- **Record once, run many.** Demonstrate a task in your browser; ProcessLens builds a workflow you can read and edit.
- **Safe batch runs.** Preview before anything is submitted, duplicate protection, and runs that resume cleanly after interruptions.
- **Documents in, data out.** Pull fields from résumés and invoices, with every value traced back to where it came from.
- **People stay in charge.** Anything uncertain is set aside for review instead of guessed.
- **Full audit trail.** Every step of every run is recorded and explainable.

## Tech stack

Python · FastAPI · PostgreSQL · TypeScript · React · Chrome extension (Manifest V3) · Playwright

## Repository layout

```
apps/        Web app, browser extension, demo websites
services/    API, run worker, document processing
packages/    Shared libraries and schemas
db/          Database migrations and tests
```

## Getting started

### Prerequisites

- **Docker** with Compose v2 - runs the local Postgres 16 and object storage.
- **Python 3.12**.
- **GNU Make** - the commands below are `make` targets.
- **gitleaks** (optional) - only needed for the pre-commit secret scan.

### Run it locally

```bash
cp .env.example .env            # Windows: copy .env.example .env
make up                         # start Postgres 16 and MinIO, wait until healthy

python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

make migrate                    # apply db/migrations, in order
make test                       # database and API tests
make lint                       # ruff
make fmt                        # ruff, rewriting files
```

`make down` stops the services; their data stays in named Docker volumes. The credentials in `.env`
are for local development only.

### The API

```bash
uvicorn services.api.main:app --reload --env-file .env
```

`GET /health` answers `{"status": "ok", "db": true}` while the database is reachable, and
`{"status": "ok", "db": false}` when it is not.

### Pre-commit

```bash
pre-commit install              # enables secret scanning on commit
pre-commit run --all-files
```

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

Copyright © 2026 the ProcessLens authors. All rights reserved.
The source is visible for review; no license to use, copy, or modify it is granted at this time.
