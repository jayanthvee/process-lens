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

Setup instructions will be added as the first components land.

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## License

Copyright © 2026 the ProcessLens authors. All rights reserved.
The source is visible for review; no license to use, copy, or modify it is granted at this time.
