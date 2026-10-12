# Review PL-016

Status: changes requested (two items) · Reviewer: T1 · Reviewed commit: 44e0ca5 (branch `pl-016`)

## Verdict

Strong work. Specifically:
- The loop is connected: record, then **Make recipe**, then the compile route, then **Run**.
- The front door's extension exception covers exactly two paths, with the pinned id only.
- The password and credential rules extend to rich text.
- The observation is handed across a full-page save and claimed once, by the top frame.
- The messy fixture is a real step up from `lead-crm`.
- The 20 revert proofs and the reviewer pass both caught real defects, and the fixes are correct.

Two items remain. The second is a requirement only half met.

## Gates (run by T1 on 44e0ca5)

```
python -m pytest -q                       628 passed
ruff check . / ruff format --check .      clean / 175 files already formatted
npm --prefix apps/extension run check     259 tests passed, build ok, validation passed
npm --prefix apps/web run check           passed
python fixtures/sites/messy-crm/selftest.py   SELFTEST PASSED (77 checks)
```

## Must fix

**C1. Recorded navigations are thrown away by the compiler (bug d is fixed only halfway).**
- The recorder now emits a `navigate` event for a top-level navigation that no recorded click caused: a typed URL,
  back/forward, or a reload.
- `parse_events` then drops every `navigate` (`normalize.py`). The only navigate step the compiler ever writes is
  the opening one (`compiler.py:210`).
- **Consequence:** a recording such as "home → Open partner site → Back → New lead" compiles to "click partner
  link, then click New lead". On replay the second click runs on the partner page, fails to resolve, and parks. That
  is safe, but every multi-page recording that uses Back or a typed URL is wrong.
- **Fix:** in the compiler, turn each recorded `navigate` into a `navigate` step at its position. The step uses the
  event's URL; for back/forward it is the URL the tab landed on.
  - A reload of the same URL becomes no step.
  - A `navigate` whose origin is not in `allowed_origins` is a compile problem (422), not a silent step.
  - A navigation caused by a recorded click still yields no step.
- **Tests:**
  - home → partner → back → New lead compiles to: open, click partner link, navigate home, click New lead;
  - a typed off-origin URL gives a 422 naming it;
  - a reload gives no step.

**C2. Requirement 9's warning: warn when only unstable rungs remain.**
- T2 reports that no step can be left with no rung, because the positional CSS path always produces one. That is
  true, but it hides exactly the case the warning is for: a step whose only remaining rung is a positional path
  (`div > div:nth-child(3)`) breaks as soon as the layout changes.
- **Fix:** when generated ids are dropped and the step has no role, test id, label or stable-id rung left, record
  the step with a warning ("only a positional selector remains") and show it in the **Make recipe** warnings.
- **Test:** a click on an element whose only id is generated, and with no name, label or test id, produces that
  warning.

## Rulings and notes

- **The `.codewhale/agents` profiles stay gitignored.** They are the operator's local agent configuration, not
  product code, and T1 owns them. The TOML escape defect was T1's mistake (a `\U` in the instruction text), and
  T2's local repair is correct. A clean checkout simply has no profiles. That is acceptable, and future tickets will
  say so.
- **The service worker's frame/tab gate is covered through `RecorderSession` only: accepted.** The PL-016b e2e
  exercises the real worker.
- **The two scratch servers** (ports 51331 and 8000) end with the session. Nothing to do.

## Instructions for T2

1. Fix C1 and C2 with the tests named. No other changes.
2. Run the gates from the ticket.
3. Add a "Review fixes" section to `internal/agent/reports/PL-016.md` with the real output. Commit it with this
   review, push, and stop for T1.

---

## Re-review: approved

Reviewed commit: c9f76fd (branch `pl-016`) · Verdict: **approved for merge**.

```
python -m pytest -q                       635 passed
ruff check . / ruff format --check .      clean / 177 files already formatted
npm --prefix apps/extension run check     266 tests passed, build ok, validation passed
npm --prefix apps/web run check           passed
python fixtures/sites/messy-crm/selftest.py   SELFTEST PASSED (77 checks)
```

- **C1.** A recorded `navigate` becomes a step at its position. A reload of the same URL gives no step. A navigation
  a click caused gives no step. A navigate outside an explicitly pinned `allowed_origins` is a 422. The opening
  navigate is no longer duplicated.
- **C2.** A step with only a positional rung carries "only a positional selector remains", and the **Make recipe**
  form shows it. The recipe stays schema-valid.

**Carried to PL-016b (not blocking):**
- When the caller does not pin `allowed_origins`, the compiler derives it from every origin the recording visited.
  So the off-origin rule fires only on an explicit pin.
- The **Make recipe** form should show the derived allowed origins and ask the person to confirm them before the
  recipe is stored.
