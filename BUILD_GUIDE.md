# Build guide: know follow-the-power cold

How every piece works, why it was built that way, and the questions an interviewer is likely to ask.
Read it top to bottom once; then use the "60-second pitch" and the Q&A before interviews.

---

## The 60-second pitch

> "I built a data platform that follows the AI boom through money and electricity. It pulls quarterly financials
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
