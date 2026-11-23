# ⚡ Gigawatt Ledger: build guide & interview prep

(Repo: `follow-the-power`; Python package: `ftp`.)

How every piece works, why it was built that way, and the questions an interviewer is likely to ask.
Read it top to bottom once; then use the "60-second pitch" and the Q&A before interviews.

---

## The 60-second pitch

> "I built **Gigawatt Ledger**, a data platform that follows the AI boom through money and electricity. It pulls quarterly financials
> for 26 companies across the AI supply chain from SEC filings, stored point-in-time so I can replay history
> without hindsight, and hourly power demand for five data-center hubs from the EIA, with weather removed by a
> regression. On top sits a Claude agent with 16 tools: SQL, statistics, cited search over 10-Q text. Code verifies
> every number it writes against the tool outputs. I evaluate it by time travel: I give it a past date and check
> it never leaks a number that wasn't public yet. It runs daily on GitHub Actions with quality gates and publishes
> a dashboard and an open dataset. The most interesting finding is an honest negative: grid data confirms the
> build-out but rarely predicts earnings better than a naive baseline."

---

## 1. Data sources and why each

| Source | What | Why it's interesting | Gotchas handled |
|---|---|---|---|
| SEC companyfacts API | Every XBRL fact a company ever filed | Official, free, and includes *filing dates* (point-in-time) | Tag switches, YTD-only cash flows, Q4 only in 10-K, restatements, trailing-12-month facts in utility 10-Qs, zero-valued dormant tags, fair-access User-Agent |
| SEC submissions + Archives | 10-Q/10-K documents | Management's own words; segment details XBRL lacks | Table-of-contents duplicates, hidden inline-XBRL headers |
| EIA-930 sub-region demand | Hourly MW per grid zone | Data centers show up as 24/7 load | Late revisions (3-day re-fetch window), missing hours, outliers |
| EIA retail sales | Monthly GWh by state & sector | Commercial sector includes data centers | ~2-month publication lag (treated as known 60 days after quarter end) |
| Open-Meteo (ERA5) | Daily temperature | Needed to remove weather | ~5-day lag; one point per zone |

## 2. Point-in-time: the core idea

Every financial value is stored with **`filed_date`**, the first day it was public. A restatement becomes a new
row. `sec.as_of(df, date)` returns the latest version filed on or before `date`. Grid data counts as known 7 days
after the quarter; state sales 60 days.

**Why it matters:** without it, a backtest "knows" revised numbers and next quarter's results: look-ahead bias,
the classic way backtests lie. The time machine tab, the nowcast backtests and the agent's time-travel evals all
use this one function.

**Q: How do you derive quarters from XBRL?** Income-statement items have ~90-day facts in 10-Qs; Q4 only exists
inside the annual 10-K figure, so Q4 = FY − (Q1+Q2+Q3) as known on the 10-K date. Cash-flow items are year-to-date,
so each quarter = YTD(q) − YTD(q−1), using the YTD known on the same filing date. Fiscal years come only from 10-K
annual facts (utilities also report trailing-12-month numbers in 10-Qs that would otherwise look like years).

**Q: A company switched XBRL tags. What happens?** Every candidate concept is parsed; each quarter takes the
highest-priority concept that covers it. Revenue/capex ≤ 0 is dropped so a dormant tag full of zeros falls through
(that's a real Vertiv case the quality checks caught).

## 3. Pipeline engineering (the Tesla data-engineering angle)

- **Incremental:** `resume_from` = last stored timestamp − revision window. Daily runs take about a minute, not a
  full backfill. `--full` exists for backfills.
- **Idempotent:** `INSERT … ON CONFLICT DO UPDATE` keyed on natural keys; re-running never duplicates. SEC loads
  replace a company's rows (companyfacts is the full history), so parser fixes repair old rows.
- **Fault isolation:** each source, and each company, in its own try/except; a step's failure is logged to
  `pipeline_runs` and the rest continue; the run fails at the end if anything failed.
- **Quality gate (`quality.py`):** coverage, freshness, gaps, implausible values, hour-to-hour jumps, quarter
  continuity, non-positive values. *Critical* failures (no data for the primary zone or NVIDIA) fail the run
  before publishing; warnings go to the dashboard. Results are stored per run.
- **State between runs:** the SQLite DB lives in the GitHub Actions cache; the bot commit rebases if `main` moved.
- **Eval failures that were grader bugs:** the first live run scored 17/19. One "failure" was a *more precise*
  answer (0.516 vs. expected 0.52); the other was the verifier reading citation ¶-numbers as claims and mis-pairing
  quotes. Fixed with tests → 19/19 on two models. Lesson: audit the grader as hard as the model.
- **Real bugs caught by these systems:** `.gitignore` hid `docs/data`; a hidden-XBRL parser bug produced 0
  passages; the capex sum silently included new companies (caught by a sanity check against the morning's
  number; now a regression test); SEC's API lagging on utilities' latest 10-Qs (flagged, not hidden).

**Q: Why SQLite?** Single file, zero ops, fast enough for ~350k hourly rows, cacheable in CI. The schema and
upserts port directly to Postgres; the natural next step at scale is Parquet partitioned by zone/month + DuckDB.

## 4. Analytics: be able to explain each in two sentences

- **Overnight baseload:** median load 1–5 AM. Homes/offices sleep; data centers don't.
- **Weather normalization:** per-day regression `overnight = level[YYYY-MM] + a·HDD + b·HDD² + c·CDD + d·CDD² +
  e·weekend`. The month fixed effects absorb *all* growth, so weather coefficients come only from day-to-day swings
  within a month (no confusion between "it got hotter" and "it grew"). Normalized = load at *average-year weather*:
  actual − weather(actual) + one constant (the average weather effect over all days). Using one constant rather than
  a per-month "normal" also removes the seasonal swing, so trends are readable. R² 0.79–0.98 across zones.
- **Growth regimes:** continuous piecewise-linear trend (hinge terms) with 0–2 breakpoints, each segment ≥ 12 months,
  chosen by BIC (n·ln(RSS/n) + k·ln n), so a break must earn its parameters. Real result: Northern Virginia sped up
  in May 2024 and Ohio in August 2024. A straight line gets zero breaks (tested).
- **Step detection:** deseasonalize (1-year rolling-mean detrend, then subtract a smoothed day-of-year profile;
  a mean, not a median, because a median over a sinusoid is biased), then jump = median(next 8 weeks) −
  median(previous 8 weeks). Steps = local maxima above max(4×MAD, 1.5% of load). Tuned on real data: 4-week windows
  flagged a long January cold snap the weather model underfit; 8-week windows flag only the April 2020 oil crash in
  West Texas. Data-center growth is a ramp, so a quiet detector in the hubs is the expected, honest result.
- **Temperature response:** mean load per 2 °C bin per year. Curves shifting up = more load at the same temperature.
- **Load-duration curve / load factor:** sorted hours; average ÷ peak rises as always-on load grows.
- **Lead/lag:** corr(x_t, y_{t+k}), k = −4..4, on YoY growth (removes seasonality and trend). Noise band ≈ 2/√n.
- **AI exposure:** for each company, the capex→revenue lag (0–4q) with the highest correlation, plus OLS beta and
  significance. **Lead/lag matrix:** the same for every pair of 13 series.
- **Nowcast backtests:** for each company and quarter, stand 7 days after quarter end (before reporting), refit
  naive / capex / own+capex / grid models on data known then, score against the number *as first reported*.
- **Forecast:** log-linear trend on 36 months of normalized baseload; block bootstrap (3-month blocks keep
  autocorrelation) with refitting for p10/p50/p90. Explicitly an extrapolation.
- **Variance screen:** robust z-score (median/MAD) of growth acceleration vs. the company's own history.

**Q: Your grid model doesn't beat naive. Isn't that a failure?** No, it's the finding. Grid load responds to
capex with a lag (capex leads baseload by ~1 quarter), so it *confirms* spending already visible in filings. A
project that reports a clean negative with a fair test is more credible than one that overfits a positive.

## 5. The agent (the NVIDIA Finance AI angle)

**Loop:** Claude gets the question, a system prompt (rules + skills index) and 16 tool schemas. It calls tools;
results go back; repeat until it answers (max turns). System prompt and tools are marked for **prompt caching**.

**Tools are point-in-time:** `Toolbox(as_of=…)` filters every query. Raw `sql` is offered *only* without `as_of`
(time rules can't be enforced on arbitrary SQL) and uses a read-only SQLite URI.

**Skills (progressive disclosure):** `skills/*/SKILL.md`, a name and description in the prompt, with full instructions
loaded on demand via `load_skill`. Seven: variance-analysis, lead-lag, power-signals, supply-chain,
weather-adjustment, claim-check, earnings-brief. Same pattern as Anthropic's Agent Skills.

**Verifier:** extracts numbers (skipping years, quarter labels, dates, small counts), converts units, and requires
each to match tool evidence within rounding (half a unit in the last digit or 0.5%). Numbers quoted in the user's
question are allowed (so it can rebut "$250B"). Quotes in double quotes must appear verbatim in retrieved passages.
Any failure → a revision request naming the bad numbers (max 2). **Provenance** records which tool produced each
number → the dashboard's receipts.

**Q: What can't the verifier catch?** Semantics: a real number attached to the wrong claim ("AMD grew 56%" when
56% was NVIDIA's). Mitigations: expected-number evals per case, the LLM-judge rubric, and provenance in receipts
for human review. Next step: claim-level checks (tie each number to its entity and period).

**RAG:** BM25 over MD&A passages with a small recency boost; point-in-time by filing date; citations required;
numbers in passages count as evidence (with million→billion conversion). **Why BM25, not embeddings?**
Transparent, no extra API or index service, strong on financial jargon and tickers; the retrieval benchmark
(hit@3 / MRR) is the hook for testing embeddings or hybrid search later.

**MCP server:** JSON-RPC 2.0 over stdio, standard library only: `initialize`, `tools/list`, `tools/call`,
`prompts/list|get` (skills as prompts). `FTP_AS_OF` starts it in time-travel mode.

## 6. Evaluation design

- **Expected values are computed from the database** at eval time, so evals never go stale as data updates.
- **Categories:** fact, time-travel (forbid list = values first filed after `as_of`), analysis, multi-step (ratios,
  margins), filing-search (citation required + tool required), robustness (planted false claim; refuse to forecast).
- **Pass** = all expected numbers present ∧ no forbidden number ∧ required phrase/tool ∧ verifier OK ∧ no error.
- **Extras:** LLM judge (grounded/clear/caveats/answers, 1–5; not gating), multi-model leaderboard, tokens, cache
  hits, latency, revisions. Plus an offline **retrieval benchmark** run daily for free.
- **CI uses a scripted fake LLM:** deterministic, free, tests the loop/verifier/grading logic. Live evals run on
  demand (workflow input) so API spend is controlled.

**Q: How do you stop eval overfitting?** Expected answers come from data, not from the agent's past outputs;
time-travel dates are derived from filing dates; cases test behaviors (no leakage, rebut false claims) rather
than wording.

## 7. Dashboard

Single static HTML (GitHub Pages) with data embedded as JSON; Plotly charts; 9 tabs with hash routing; light/dark
theme (colorblind-validated palette, fixed color per entity); CSV export per chart; every chart has a plain-English
"What to look for". The time machine recomputes the as-of view in the browser from the versions table.

## 8. Mapping to the roles

**NVIDIA Finance AI Engineering:** LLM agents with tool use; RAG with citations; evals (time travel, adversarial,
judge, leaderboard); MCP; skills; automated finance reporting (variance analysis, memos); SEC financial data;
guardrails against hallucinated numbers.

**Tesla Data Engineer, Energy:** grid/energy time series at hourly resolution; incremental idempotent pipelines;
data-quality gates; weather normalization; load-duration curves; anomaly/step detection; forecasting with
uncertainty; orchestration in CI; open data publishing.

## 9. Questions to ask *them*

- "How do you evaluate LLM outputs that contain financial figures today: human review, rule checks, or both?"
- "Where does point-in-time data matter in your forecasting or reporting?"
- "What does data quality monitoring look like for your energy telemetry pipelines?"

## 10. Résumé bullets (pick 3–4; numbers are from the live project)

**follow-the-power** · Python, SQL, Claude API, MCP, GitHub Actions · github.com/autkarsh025/follow-the-power

- Built a daily, incremental data pipeline joining SEC XBRL financials for 26 AI-supply-chain companies (stored
  point-in-time with filing dates), 337k+ hours of EIA grid demand across 5 data-center hubs, and ERA5 weather;
  ~230 automated quality checks gate every publish.
- Developed weather-normalized load analytics (degree-day regression with month fixed effects, R² 0.79–0.98) and
  BIC-selected growth-regime detection, identifying Northern Virginia's and Ohio's 2024 accelerations in
  data-center load (≈+1,100–1,270 MW/yr).
- Built a Claude tool-use analyst agent (16 point-in-time tools, cited BM25 search over 4,600+ 10-Q/10-K passages,
  MCP server) with a code-based verifier that traces every number to source data and blocks unverified output.
- Designed a 22-case evaluation suite (time travel, prompt injection, planted false claims, unit traps), with
  pass^k consistency and a multi-model leaderboard: 21/21 on Claude Sonnet 5.5 and Haiku 5.5. Root-caused failures
  across 6 live runs, mostly grader bugs, each fixed with a regression test (63 tests).

**Tesla-flavored variant (data engineering, energy):** lead with bullets 1–2 and add: "Detected structural changes
in hourly load (step detection validated on the April 2020 oil-crash load drop in West Texas) and published an
open dataset with a validated data contract."

**NVIDIA-flavored variant (Finance AI):** lead with bullets 3–4 and add: "Automated a weekly verified research memo
and variance-analysis workflow (FP&A-style) with a governance rule that unverified reports are never published."


---

## 11. The 3-minute demo script (share your screen on the live dashboard)

1. **Overview (30 s).** "AI spending turns into chips, then into electricity. This tracks all three." Point to the
   findings: $201.9B of capex at +108%, Northern Virginia up ~4,900 MW. Show the memo: "an agent wrote this, and every
   number in green was checked against the data."
2. **Supply chain (30 s).** The revenue race (log scale): "Micron, Super Micro and NVIDIA pulled away in 2023." Drag the
   **capex shock simulator**: "if big tech's capex growth fell to 0%, here's each company's implied revenue growth,
   from its historical sensitivity, with error bars."
3. **Data-center detector (40 s).** Pick Ohio → growth regimes: "flat until August 2024, then ~1,270 MW a year. The
   break is chosen by BIC, not by me." Temperature response: "same temperature, more load every year."
4. **Time machine (30 s).** Drag to early 2023: "this is exactly what was public that day. The backtests and the
   agent's time-travel tests use the same function, so nothing peeks at the future."
5. **AI analyst (40 s).** Open the receipts for `claim_check` or `prompt_injection`: "the tools it called, each number
   traced to its source, and what the verifier rejected in earlier drafts. Sonnet passed 22/22 on three repeated runs."
6. **Close (10 s).** "And the honest finding: grid data rarely beats a naive forecast of earnings. It confirms
   spending rather than predicting it, and the dashboard says so."

## 12. Numbers to know cold

| | |
|---|---|
| Companies / layers | 26 / 6 (cloud buyers, chips & memory, servers & networking, power & cooling gear, data-center landlords, electricity) |
| Grid | 5 zones, 337k+ hourly rows since 2019; Northern Virginia overnight +~50% (~4,900 MW) |
| Regimes | Northern Virginia ~590 → ~1,100 MW/yr (May 2024); Ohio ~140 → ~1,270 MW/yr (Aug 2024) |
| Spending | hyperscaler capex $201.9B in 2026Q3 (+108% YoY); NVIDIA $96.2B (+106%) |
| Honest test | grid beats naive for 5 of 24 companies |
| Weather model | R² 0.79–0.98; balance point 18 °C |
| Text | 4,675 MD&A passages; retrieval hit@3 100%, MRR 1.00 on 15 queries |
| Agent | 16 tools, 7 skills; 22 eval cases; pass^3: Sonnet 22/22, Haiku 21/22; judge ≈4.3/5; 68% of input tokens from prompt cache |
| Engineering | 63 tests; ~230 quality checks per run; 9 open-data files with a validated contract |

## 13. Worked examples

**XBRL → quarters (the test case `test_trailing_twelve_month_facts_in_10q_are_not_fiscal_years`).** A utility files:
Q1 = 110 (10-Q), H1 YTD = 230 and Q2 = 120 (10-Q), *trailing twelve months to June* = 450 (also in the 10-Q), 9M YTD =
360 (10-Q). Naively, the 450 looks like a fiscal year (a ~365-day period) and shifts every quarter. The parser only
lets **10-K** annual facts define fiscal years, so it yields Q1 110, Q2 120, Q3 360 − 230 = 130. That bug was hiding
the latest quarters of several utilities until a quality check (filing freshness) flagged it.

**Recast guard.** Dominion's FY2021 was recast in 2024 after it sold its gas utilities (400 → 250, say). Q4 = FY − 9M
would give 250 − 300 = −50 if the recast year were differenced with the *original* 2021 nine-month YTD. Derived
quarters therefore only combine facts filed within ~a year of each other.

**Weather model, in one day.** On a 2 °C January night in Ohio, HDD = 18 − 2 = 16. With coefficients a ≈ 74 MW per
degree-day and b ≈ 4.2 per HDD², weather adds ≈ 74·16 + 4.2·256 ≈ 2,260 MW to overnight load. Normalization subtracts
that and adds back the *average* weather effect of all days, leaving load "at average-year weather".

**Verifier, in one answer.** "Capex was **$201.92B** in 2026Q3, up **107.6%** [MSFT 10-Q filed 2026-10-28, MD&A ¶35]."
Extracted claims: 201.92 (unit B) and 107.6 (%). "2026Q3", the filing date and "¶35" are stripped as non-claims. Both
match `quarterly_financials` output within tolerance (0.5% or half a unit of the last shown digit) → receipts show them
green, `from: quarterly_financials`.

## 14. The agent loop, step by step

```
user question ──► system prompt (rules + as-of date + skills index) + 16 tool schemas  [prompt-cached]
      │
      ▼
 Claude ──► tool_use(load_skill "claim-check") ──► SKILL.md instructions
      │──► tool_use(quarterly_financials)     ──► rows; every number → evidence (+ which tool)
      │──► tool_use(calculate "250 - 96.22")  ──► 153.78 → evidence
      ▼
 draft answer ──► verifier: numbers ⊆ evidence ∪ question numbers? quotes ⊆ retrieved passages?
      │ no  → "these numbers don't match: …" (logged), up to 2 revisions
      │ empty answer → one nudge to write it
      ▼ yes
 final answer + receipts (tool trace, provenance per number, revision log, tokens, seconds)
```

## 15. More interview questions (with short answers)

- **"Why not just ask the LLM to be careful?"** Instructions reduce errors; they don't guarantee anything. The
  verifier is deterministic and cheap, and it caught real slips in live runs (e.g. a computed % not run through `calculate`).
- **"How did you pick 1–5 AM?"** It's the trough of residential and commercial load; median (not mean) resists odd hours.
  A sensitivity check is easy (2–6 AM gives the same story).
- **"Why month fixed effects instead of a time trend in the weather model?"** A trend imposes a shape; fixed effects
  let growth be anything (including steps) while weather coefficients come from within-month variation only.
- **"Your exposure scores use ~25 points. Isn't that fragile?"** Yes. That's why every correlation shows its noise band,
  the simulator shows ±1 SD error bars, and only 'meaningful' links feed the simulator.
- **"What happens if EIA is down?"** That step fails and is logged; others continue; the quality gate decides whether
  publishing is safe; the next run's incremental window refills the gap automatically.
- **"How would you scale this to 500 companies and 50 zones?"** Partition hourly data (Parquet by zone/month) with
  DuckDB or a warehouse; parallelize SEC pulls within fair-access limits; move orchestration to Airflow/Dagster with
  per-asset freshness checks; precompute dashboard aggregates.
- **"How do you know the evals aren't too easy?"** They include adversarial cases (prompt injection, planted claims,
  unit traps), consistency runs (pass^k revealed Haiku's injection weakness), and they *have* failed: 7 runs, each
  failure root-caused in the eval log.
- **"What's the most important bug you found?"** The capex sum silently including new companies after the expansion
  ($239.9B instead of $201.9B). It was caught by comparing against a number I already trusted, and it's now a
  regression test. It's the reason for automated sanity checks.
- **"What would you build next?"** Claim-level verification (number + entity + period), segment revenue from filing
  tables, and hybrid retrieval gated by the benchmark.
- **"Why GitHub Actions instead of Airflow?"** One daily DAG with six steps doesn't need a scheduler cluster; Actions
  gives scheduling, secrets, caching, logs and free compute. The steps are already isolated, so moving them to
  Airflow tasks would be mechanical.
