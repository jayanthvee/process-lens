-- PL-087: a run goes to the Chrome of the person who started it.
--
-- Each extension install has a browser id it made once (a random token kept in its own storage).
-- The dashboard tab learns it from the extension and sends it when it starts a run, and the run
-- keeps it here. The supervisor gives every attached Chrome its own worker, and a worker claims
-- only the runs of its browser, plus runs nobody bound (NULL: a run made through the API, or by a
-- browser without the extension). The id is a routing label, not a credential. The ledger guard
-- is untouched: the state machine never reads it.

ALTER TABLE runs ADD COLUMN browser_id text;

ALTER TABLE runs ADD CONSTRAINT runs_browser_id_shape CHECK (
  browser_id IS NULL OR browser_id ~ '^[A-Za-z0-9_-]{8,64}$'
);
