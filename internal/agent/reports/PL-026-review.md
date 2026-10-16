# PL-026 review (T1)

**Verdict: approved.**

## Checks I ran
- **Unit tests:** `pytest --ignore=tests/e2e`, 819 passed.
- **End-to-end:** `test_batch_real_extension.py`, `test_record_compile_run.py` and `test_disconnect_mid_save.py`,
  7 passed. Together they cover the runner's freeze check and the extension's version report.
- **The `processlens_admin` role's grants** match the ticket. There is no write on `runs`, `run_records`,
  `committed_keys` or `jobs`.

## Follow-ups (not blocking)
- **`UPDATE (min_extension_version)` is granted to `processlens_admin`,** as this ticket asked, but ADM-001 sends
  that write through the API. Before ADM-001, either drop the grant or let ADM-001 write it directly; one path,
  not two.
- **`decided_by` on approvals can be spoofed** (it comes from the request body). That waits for the identity
  layer in the Railway/SSO ticket.
