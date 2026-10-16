# PL-028 review (T1)

**Verdict: approved.**

The diff is exactly the ticket:
- the schema's optional `example` (a string, at most 200 characters);
- the compiler setting it from the sample row, and leaving it out when the value is empty or whitespace;
- `/check` falling back to the recipe's examples, with a `sample_row` the caller passes still winning.

The tests were written first and seen to fail. Unit gates: 824 passed, and ruff is clean.

**How it was built:** two T2 instances (GLM-5.3-Flash and DeepSeek Flash) ran this ticket in the same worktree at
the same time, by mistake. GLM wrote the implementation and the tests; DeepSeek verified them, fixed one format line
and committed. The result is one consistent change, but the comparison between the two models is void.

**Follow-up (not blocking):** an `example` is a real value from the person's demonstration, such as a real email,
and it is now stored in the recipe. Admin's "no row contents" rule should treat `meta.input_schema.fields.*.example`
as row content.
