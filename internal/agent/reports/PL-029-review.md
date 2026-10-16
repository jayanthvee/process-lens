# PL-029 review (T1)

**Verdict: approved, with one change by T1.**

All four fixes match the ticket, the tests were written first, the gates are green, and the branch merges cleanly
with `main`.

**T1 change:** "Running now" shows `running` and `paused` runs, and drops only `queued`. The ticket said
"`running` only", which was my mistake. Since PL-027, a run waiting on a "Try one row" approval is `paused`, and it
must not disappear from Home.

**Comparison note:** GLM-5.3-Flash finished this in about 10 minutes, at 96% cache and around 33 tokens a second.
It needed one review fix, which came from the ticket's wording, not the model.
