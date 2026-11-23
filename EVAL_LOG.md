# Eval log: every live run, what failed, and why

Live runs of `python -m ftp.evals` against the real database. Each failure was traced to a root cause, and the
cause was fixed with a regression test. **Most failures were in the grader or verifier, not the model**, which is
why the grader gets audited as hard as the agent.

| Run (2026-11-23) | Models | Result | Failures → root cause → fix |
|---|---|---|---|
| 1 | Sonnet 5.5 | 8/8 | first suite; agent + verifier working end to end |
| 2 | Sonnet 5.5 | 17/19 | `lead_lag`: answer 0.516 was *more precise* than the expected 0.52 → **grader** now accepts values that round to the target. `rag_capex_plans`: citation ¶-numbers read as numeric claims; one short quote shifted the quote pairing → **verifier** strips citations, pairs quotes before filtering. |
| 3 | Sonnet 5.5, Haiku 5.5 | 19/19, 19/19 | none; added a revision log to see what each draft got rejected for |
| 4 | Sonnet 5.5, Haiku 5.5 | 21/21, 19/21 | Haiku `unit_trap`: conversion constant "1000" flagged → **verifier** ignores pure conversion factors. Haiku `weather_adjusted`: empty final message → **agent** asks once for the written answer. Memo: one quote failed on a hyphen ("four-to-five") → quotes compared modulo dash style; **unverified memos are no longer published**. |
| 5 | Sonnet 5.5, Haiku 5.5 | 19/21, 21/21 | Sonnet `out_of_scope`: correct refusal ("my tools don't track Tesla") not in the phrase list → **grader** phrase list broadened. Sonnet `gross_margin`: a prose date ("August 26") read as a claim, then the agent argued with the checker inside its answer → **verifier** recognizes month-name dates; revision prompt says to fix silently. |

## Lessons

1. **Grade behavior, not wording.** Phrase lists for refusals are brittle; they need many accepted phrasings (or a judge).
2. **Precision isn't wrong.** Expected-value checks must accept answers more precise than the target.
3. **Sources aren't claims.** Citations, paragraph markers, dates and conversion factors contain digits but no data claims.
4. **Governance beats retries.** If output can't be verified after N revisions, don't publish it; keep the last good one.
5. **Run-to-run variance is real.** The same model scored 21/21 and 19/21 on consecutive runs; report pass rates over runs, not a single best score.
