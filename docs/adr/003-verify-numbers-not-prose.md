# 003: Verify numbers in code, not with another LLM

**Context.** The costliest failure of a finance agent is a confident, made-up figure. LLM-as-judge is itself
probabilistic and can miss a wrong digit.

**Decision.** A deterministic verifier: every number in the answer must match tool output (or a number quoted in
the question) within rounding, with unit conversion; quotes must appear verbatim in retrieved passages; citations
and ¶ markers are sources, not claims. Failures force a revision (max 2). Provenance is recorded per number. An LLM
judge is used only for qualities code can't check (clarity, caveats) and never gates pass/fail.

**Consequences.** Fabricated numbers can't ship. It can't detect a *real* number attached to the wrong claim; the
expected-value evals and receipts (provenance) cover that partially. Next step: claim-level checks (entity + period).
