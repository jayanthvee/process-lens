# PL-024 review (T1)

**Verdict: approved.**

## Checks I ran
- **Unit tests:** `pytest --ignore=tests/e2e`, 719 passed.
- **End-to-end:** `test_record_compile_run.py`, `test_batch_real_extension.py` and
  `test_record_compile_run_messy.py`, 6 passed in 6 min 20 s. These run on the new `insert_batch` path through
  `make_run`.

## What I checked in the code
- **The middleware exception is one path wide.** Multipart is accepted only on `POST /api/v1/batches`. The origin,
  loopback and host checks are unchanged, and those are what actually stop a cross-site request.
- **The migration** adds one nullable column, so it is safe under any code that runs alongside it.
- **The mapping lives on the batch** and is applied when the run is created. `raw` stays exactly as uploaded, and
  the recipe is never mutated.
- **`limit`** on a run is capped at 1 or more and takes the ready rows in the person's row order.

## Follow-ups (not blocking)
- **Value matching doesn't work from the web.** The compiler should store each column's sample value from teaching
  in `input_schema`, for example as `examples`, so `/check` can match by value on the server.
- **The real-Odoo browser test** with `customer-inquiries.csv` waits for PL-027's Odoo task.
