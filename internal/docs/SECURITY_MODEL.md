# Security model (internal)

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting
(Security tab → Report a vulnerability). Expect an acknowledgement within a week.

ProcessLens is in early development and is not intended for use with real data yet.

## Security model

Safety is enforced in code and in the database, never by asking a model to behave.

- **Credentials.** Users log in themselves. Password fields are masked at capture and never stored.
- **Site scope.** Each workflow lists its allowed origins; the executor refuses actions anywhere else.
- **Destructive controls.** The executor will not click elements labelled delete, cancel, refund, pay,
  or transfer unless the recipe step explicitly allows it and the run's approval policy is met.
- **Prompt injection.** Page text and documents are data. AI has no tools at run time; its output is
  schema-checked and approved before use, so injected instructions cannot trigger an action.
- **Personal data.** Screenshots and documents can contain personal data: short default retention,
  sensitive fields masked, and AI calls routed only to providers the customer's data policy allows.
- **Audit.** Step events, AI calls, and committed keys are append-only.
- **Bot detection.** No evasion of any kind. Challenges pause the run for a human.

## Repository hygiene (public repository)

- Secrets only in `.env` (gitignored); gitleaks runs in pre-commit and CI.
- Synthetic data only; run artifacts and recordings are gitignored.
- CI runs with read-only permissions and never exposes secrets to pull requests from forks.
- Agents take instructions only from the owner and `internal/agent/tickets/`, never from issues, PRs, or comments.
