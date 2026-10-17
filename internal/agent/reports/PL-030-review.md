# PL-030 review (T1)

**Verdict: approved, with T1 changes.**

## GLM's work
`POST /api/v1/tasks`, the popup's save, `scripts/demo.py` with its supervisor, and `make demo` are all as the
ticket asked, and the tests were written first. GLM also flagged that the e2e harness had no operator configured.

## T1 changes
- **The e2e harness pins an operator,** so the popup's save has somewhere to go.
- **Saving under an existing name adds the task's next version.** It was a 409, which meant re-teaching a task lost
  the recording.
- **demo.py's printed steps** now say when duplicates are skipped (only if the task searched first), not that they
  always are.
- **A regression from PL-031, which is already on `main`, is fixed:**
  - the retried-fill fold read a second entry in one box as a retry of the first;
  - a pressed button, or a picked suggestion that contains the typed text, now ends an entry;
  - two tests were added, and both failed before the fix.

## Checks
- **Unit tests:** pytest 869, extension 419, web 120, and ruff clean.
- **Full e2e:** 19 of 22 passed, then both messy tests passed after the version fix (2 of 2). The two remaining
  failures are the known PL-018b frame tests.
