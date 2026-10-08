# Board

Maintained by the control tower (Claude chat); the owner commits updates.
Status: `todo` · `in progress` · `review` · `done` · `blocked`

## Now

| Ticket | Title | Owner | Critical | Depends on | Status |
| --- | --- | --- | --- | --- | --- |
| PL-001 | Architecture contracts and design review | T1 Claude | no | SYSTEM_DESIGN.md in repo | todo |
| PL-002 | Scaffold, migrations, DB tests, CI, secret scanning | T2 DeepSeek | no | none | todo |

## Next

| Ticket | Title | Owner | Critical | Depends on | Status |
| --- | --- | --- | --- | --- | --- |
| PL-003 | Demo CRM with drift switches | T2 DeepSeek | no | PL-002 | todo |
| PL-004 | Recipe spec and JSON Schema v1 | T1 Claude | no | PL-001 | todo |
| PL-005 | CI guards for the constitution | T2 DeepSeek | no | PL-001, PL-002 | todo |
| R-002 | Review PL-002 | T1 Claude | — | PL-002 | todo |

## Phase plan

| Phase | Content | Exit test |
| --- | --- | --- |
| 0 | Constitution, repo, CI, contracts | CI green; guards fail on a deliberate violation |
| 1 | Recipe schema, ledger package, demo CRM | Recipe validates; ledger race tests pass |
| 2 | Recorder, compiler, extension executor, preview | One record runs end to end on the demo CRM |
| 3 | Crash recovery, canary, results, resume | Kill test: 20 kills mid-save, zero duplicates |
| 4 | Branches, read_value, for_each | Use cases 1, 4, 5, 6, 9 on demo sites |
| 5 | PDF/OCR, extraction, input review | Use cases 2 and 3; OCR bake-off recorded |
| 6 | Transform, match, aggregate, download, classify | Use cases 7, 8, 10 |
| 7 | AI repair | Drift scenarios healed with approval |
| 8 | Benchmark and polish | Manual vs automated timing for all ten use cases |
