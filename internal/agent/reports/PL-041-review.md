# PL-041 review (T1): approved

**Re-ran after merging `origin/main`:**
- `tests/api` and `test_sql_separation`: 190 passed;
- ruff is clean;
- the web check passed **162 tests**, plus the build.

**`run_blocking`.** The tenant and destination come from the run's own row, it answers only while the run is `queued`, and it matches the index's `running`/`paused` predicate.

**`keeps_failing_tasks`.** It counts only the last two *finished* runs: both added nothing, both have a row that needs a person, and they share a reason. A run still going neither earns nor clears the flag.

**One file outside the list (`services/api/home.py`).** Accepted: `GET /api/v1/tasks` lives there. The `TaskListEntry` subclass keeps `/home`'s shape unchanged.

**The double-click guards** use a `useRef` flag set before the request, which is the right fix.
