# PL-031 review (T1)

**Verdict: approved for fixes 1–5. Fix 6 is not solved, but it fails safe.** It moves to PL-032.

## What I checked
- **The fixture is the real recording, unedited.** Kimi's first pass had added made-up `keystrokes` values to it;
  T1 had that reverted. Fix 4 now works from the timing of the fills, and keystroke counts are used when present.
- **The fixture compiles to 8 clean steps:** New → Contact → the exact-or-Create option → Email → Phone → Expected
  Revenue → Add.
  - No `?` in any name.
  - Container clicks and the retried fill are dropped.
  - Opportunity is dropped, since the page set it.
- **Unit gates:** extension 417 tests, pytest 846, ruff clean.

## Replay on real Odoo (T1): the owner's recording, sanitized, one new contact and one existing
- **Run 1.** The lead and the new contact were **created correctly** (email, phone, revenue). The inferred check
  waited for the email, which a kanban card never shows, so the row parked and the run paused on the canary.
  - **T1 fix:** the inferred check now uses the first templated fill (the contact), with a test for an email-keyed
    task.
- **Run 2.** The runner's own guard refused before Add: "the confirmation was already on the page before saving".
  The contact's name is already visible in the quick-create form. **Nothing was saved.** That is the right outcome.

## Why fix 6 is not done
Plain `text_visible` can't prove a quick-create save, because the text is on screen before the click. The schema's
other conditions (`element_present`/`absent`, `value_equals`, `url_matches`) don't cover "a new record with this name
appeared".

**PL-032:** a new condition, *"text appears more times than before the click"* (`text_count_increases`).
- The executor evaluates it with a count taken before the commit.
- The compiler infers it for quick-create saves.

Until then, a quick-create task compiles but parks every row safely. It never saves without proof.

## Kimi notes, for the model comparison
- About 1 hour and $4–5, over two prompts.
- It needed one redirect for editing the fixture and one for a test it had flipped to match its code.
- The fixes themselves were good.
