-- Ledger guarantees smoke test. Run against a fresh database after 0001_init.sql:
--   psql -d processlens -f db/tests/ledger_guard_test.sql
-- Expected: steps 3, 5, 7, 8, 9 print ERROR (they are the illegal operations); all others succeed.
\set ON_ERROR_STOP 0
\set QUIET 1
INSERT INTO tenants (id, name) VALUES ('00000000-0000-0000-0000-000000000001','Demo');
INSERT INTO workflows (id, tenant_id, name, destination_key, allowed_origins)
  VALUES ('00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-000000000001','Inquiries','demo-crm','{http://localhost:5173}');
INSERT INTO recipe_versions (id, tenant_id, workflow_id, version, recipe, change_reason)
  VALUES ('00000000-0000-0000-0000-0000000000b1','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1',1,'{"steps":[]}','compiled from recording');
INSERT INTO input_batches (id, tenant_id, workflow_id, kind) VALUES ('00000000-0000-0000-0000-0000000000c1','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1','csv');
INSERT INTO input_records (id, tenant_id, batch_id, row_index, raw, record_key, status)
  VALUES ('00000000-0000-0000-0000-0000000000d1','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000c1',0,'{"email":"ravi@example.com"}','ravi@example.com','ready'),
         ('00000000-0000-0000-0000-0000000000d2','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000c1',1,'{"email":"ana@example.com"}','ana@example.com','ready');
INSERT INTO runs (id, tenant_id, workflow_id, recipe_version_id, input_batch_id, mode)
  VALUES ('00000000-0000-0000-0000-0000000000e1','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000b1','00000000-0000-0000-0000-0000000000c1','batch');
INSERT INTO run_records (id, tenant_id, run_id, input_record_id, record_key)
  VALUES ('00000000-0000-0000-0000-0000000000f1','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000e1','00000000-0000-0000-0000-0000000000d1','ravi@example.com'),
         ('00000000-0000-0000-0000-0000000000f2','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000e1','00000000-0000-0000-0000-0000000000d2','ana@example.com');

\echo '--- 1. claim query (SKIP LOCKED) returns one record, attempts auto-incremented'
WITH next AS (
  SELECT id FROM run_records
  WHERE run_id = '00000000-0000-0000-0000-0000000000e1' AND state = 'pending' AND attempts < max_attempts
  ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
UPDATE run_records r SET state = 'claimed', claimed_by = 'worker-1', claimed_at = now()
FROM next WHERE r.id = next.id RETURNING r.record_key, r.state, r.attempts;

\echo '--- 2. happy path transitions'
UPDATE run_records SET state='prechecked' WHERE id='00000000-0000-0000-0000-0000000000f1';
UPDATE run_records SET state='filling' WHERE id='00000000-0000-0000-0000-0000000000f1';
UPDATE run_records SET state='submitting' WHERE id='00000000-0000-0000-0000-0000000000f1';
\echo '--- 3. ILLEGAL: submitting -> pending (blind retry) must be rejected'
UPDATE run_records SET state='pending' WHERE id='00000000-0000-0000-0000-0000000000f1';
\echo '--- 4. crash path: submitting -> reconciling -> verified'
UPDATE run_records SET state='reconciling' WHERE id='00000000-0000-0000-0000-0000000000f1';
UPDATE run_records SET state='verified' WHERE id='00000000-0000-0000-0000-0000000000f1';
INSERT INTO committed_keys (tenant_id, destination_key, idempotency_key, workflow_id, run_record_id)
  VALUES ('00000000-0000-0000-0000-000000000001','demo-crm','ravi@example.com','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000f1');
\echo '--- 5. ILLEGAL: verified -> pending must be rejected'
UPDATE run_records SET state='pending' WHERE id='00000000-0000-0000-0000-0000000000f1';
\echo '--- 6. duplicate commit of same key: ON CONFLICT DO NOTHING inserts 0 rows'
INSERT INTO committed_keys (tenant_id, destination_key, idempotency_key, workflow_id, run_record_id)
  VALUES ('00000000-0000-0000-0000-000000000001','demo-crm','ravi@example.com','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000f1')
  ON CONFLICT (tenant_id, destination_key, idempotency_key) DO NOTHING RETURNING id;
\echo '--- 7. ILLEGAL: editing a recipe version must be rejected'
UPDATE recipe_versions SET recipe='{}' WHERE id='00000000-0000-0000-0000-0000000000b1';
\echo '--- 8. ILLEGAL: deleting a committed key must be rejected'
DELETE FROM committed_keys;
\echo '--- 9. retry cap: 3 claims allowed, 4th rejected by CHECK'
UPDATE run_records SET state='claimed' WHERE id='00000000-0000-0000-0000-0000000000f2';
UPDATE run_records SET state='pending' WHERE id='00000000-0000-0000-0000-0000000000f2';
UPDATE run_records SET state='claimed' WHERE id='00000000-0000-0000-0000-0000000000f2';
UPDATE run_records SET state='pending' WHERE id='00000000-0000-0000-0000-0000000000f2';
UPDATE run_records SET state='claimed' WHERE id='00000000-0000-0000-0000-0000000000f2';
UPDATE run_records SET state='pending' WHERE id='00000000-0000-0000-0000-0000000000f2';
UPDATE run_records SET state='claimed' WHERE id='00000000-0000-0000-0000-0000000000f2';
SELECT record_key, state, attempts FROM run_records ORDER BY record_key;
\echo '--- 10. run_summary view'
SELECT total, verified, skipped, parked, failed, in_progress FROM run_summary;
