<div align="center">

# ⚡ Gigawatt Ledger

**The AI boom, traced from 26 companies' balance sheets to hourly electricity demand on the grid,
explained by a Claude analyst agent whose every number is verified.**

**[▶ Live dashboard](https://autkarsh025.github.io/follow-the-power/)** ·
**[Open dataset](https://autkarsh025.github.io/follow-the-power/data/README.md)** ·
**[Build guide](BUILD_GUIDE.md)** · **[Eval log](EVAL_LOG.md)** · **[Design decisions](docs/adr/README.md)**

![tests](https://github.com/autkarsh025/follow-the-power/actions/workflows/tests.yml/badge.svg)
![data](https://github.com/autkarsh025/follow-the-power/actions/workflows/data.yml/badge.svg)

</div>

---

## Contents

1. [The idea in 30 seconds](#the-idea-in-30-seconds)
2. [What it found](#what-it-found)
3. [A tour of the dashboard](#a-tour-of-the-dashboard)
4. [How it works](#how-it-works)
5. [The life of one number](#the-life-of-one-number)
6. [The AI analyst](#the-ai-analyst)
7. [How the agent is evaluated](#how-the-agent-is-evaluated)
8. [Data engineering](#data-engineering)
9. [Analytics methods](#analytics-methods)
10. [Project structure](#project-structure)
11. [Run it yourself](#run-it-yourself)
12. [Limits, said out loud](#limits-said-out-loud)
13. [Roadmap](#roadmap)

---

## The idea in 30 seconds

AI runs on GPUs, and GPUs run on electricity. When Microsoft, Alphabet, Amazon and Meta spend hundreds of
billions of dollars on AI, that money flows down a **supply chain**: chip makers, server builders, networking,
power and cooling equipment, data-center landlords. It ends up as **electricity demand** on the grid, because a data
center draws power 24 hours a day.

Gigawatt Ledger follows both trails using only official public data:

| | Source | What it tracks |
|---|---|---|
| 💵 **The money** | SEC EDGAR (XBRL + filing text) | Revenue, capex and gross margin for 26 companies across 6 supply-chain layers, every value stamped with the date it became public |
| ⚡ **The power** | U.S. Energy Information Administration | Hourly demand in 5 grid zones where data centers cluster (337,000+ hours since 2019) and commercial electricity sales in 6 states |
| 🌡️ **The weather** | Open-Meteo (ERA5 reanalysis) | Daily temperature per zone, so a heat wave isn't mistaken for a new data center |

Then a **Claude agent** answers questions about it all with 16 tools. Code checks every number it writes against
the data, and it is tested like an auditor would test an analyst: by replaying history, planting false claims and
injecting malicious instructions.

---

## What it found

*Real data, November 2026. The dashboard regenerates these findings from the data every day.*

| | Finding |
|---|---|
| 💸 **Spending** | Hyperscaler capex hit **$201.9B in 2026Q3, +108% year over year**, 11.7× the 2019 quarterly average. NVIDIA's latest quarter: **$96.2B, +106%**. |
| ⚡ **Power** | Northern Virginia's overnight (always-on) load is **up ~50% since 2019, about 4,900 MW**, roughly 2.7 million AI GPUs at 1.4 kW each with cooling overhead (PUE 1.3), or 4 million homes. |
| 🚀 **Acceleration** | A statistical break test (piecewise trend, BIC) finds **Northern Virginia sped up in May 2024 (~590 → ~1,100 MW/yr)** and **Ohio broke out in August 2024 (~140 → ~1,270 MW/yr)**; Dallas accelerated from late 2023. |
| 🗺️ **Contrast** | West Texas grew +118%, but that's oil, gas and crypto mining, so it's kept as a non-AI contrast zone. The step detector's one hit there is the **April 2020 oil-price crash**, when Permian producers shut in wells. |
| 🏗️ **Supply chain** | **Chips & memory** is the fastest-growing layer (trailing-12-month revenue +70%). Micron, GE Vernova and Dell track hyperscaler capex most tightly; 14 of 25 companies show a link outside the noise band. |
| 🔍 **Restatements** | Point-in-time storage surfaced real recasts: **Dell's history after the VMware spin-off** (gross profit −31%) and **Dominion's after selling its gas utilities**. |
| 🎯 **The honest test** | Power data predicts revenue better than a naive "growth continues" guess for only **5 of 24 companies**. The grid *confirms* the build-out (capex leads Virginia's baseload by about a quarter); it rarely front-runs earnings. |
| 🤖 **The agent** | **22 eval cases, each run 3×. Claude Sonnet 5.5 passed every case on every run (pass^3 22/22); Haiku 5.5 scored 21/22**, its one inconsistency being a planted prompt injection it resisted 2 times out of 3 (the verifier still blocked the planted number). |

---

## A tour of the dashboard

Nine tabs, each chart with a plain-English **"What to look for"** note, CSV download, light/dark theme, mobile layout.

| Tab | What you'll see |
|---|---|
| **Overview** | What the project is, auto-generated findings, headline KPIs, the supply chain at a glance (click a layer), the agent's latest **verified research memo**, an **earnings calendar** of upcoming reports, and "what changed since the last run". |
| **Supply chain** | The **revenue race** (trailing-12-month revenue indexed to 2019, log scale), a growth heatmap of 26 companies × 14 quarters, layer totals, capex intensity, gross margins, an **AI-exposure score** per company, a 🎛️ **capex shock simulator** (set capex growth → implied revenue growth for each company), and a lead/lag matrix across 13 series. |
| **Company explorer** | Pick any of the 26 companies: quarterly values, YoY growth against hyperscaler capex, a point-in-time **nowcast backtest**, unusual quarters (robust z-score), and every filed version of every number (✎ = restated). |
| **Grid zones** | Five hubs compared on weather-adjusted baseload, actual vs. weather-adjusted load per zone, how much each month's weather added or removed, and state electricity sales. |
| **Data-center detector** | **Growth regimes** (when did the build-out speed up?), the step-change detector, temperature-response curves ("same temperature, more power"), load-duration curves, the flattening daily profile, an hour × month heatmap, and a **GPU calculator** with adjustable assumptions. |
| **Forecasts & backtests** | A 24-month baseload projection with a bootstrap uncertainty band, the nowcast scoreboard for every company (4 models, point-in-time), and the money → chips → power lead/lag chart. |
| **⏳ Time machine** | Drag a slider to any day since 2020 and see **only what was public then**: the charts, the latest known quarter for every company, and what got filed that month. |
| **🤖 AI analyst** | Test results by category, the model leaderboard (pass^k, latency, tokens, judge score), every test case, and the 🔍 **receipts**: the tools the agent called and its answer, with each number highlighted and linked to its source tool, plus what the verifier rejected in earlier drafts. Also the retrieval benchmark and the MCP setup. |
| **Data & quality** | ~230 automated quality checks, SEC freshness per company, the restatement tracker, a variance screen across companies, **data lineage** (which XBRL tags built each series), pipeline runs, the open dataset, a how-it-works diagram and a glossary. |

---

## How it works

```
                         ┌──────────────────────────── GitHub Actions, daily ────────────────────────────┐
 SEC EDGAR ─ XBRL facts ─┐
           ─ 10-Q/10-K   │    ┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────┐
             MD&A text ──┼──► │ point-in-time SQLite │──►│ analytics            │──►│ dashboard (Pages)│
 EIA ─ hourly zone load ─┤    │ incremental · upsert │   │ weather model, steps,│   │ open dataset     │
     ─ state sales ──────┤    │ quality-gated        │   │ regimes, lead/lag,   │   │ + data contract  │
 Open-Meteo ─ weather ───┘    └──────────┬───────────┘   │ backtests, BM25      │   └──────────────────┘
                                         │               └──────────┬───────────┘
                                         ▼                          ▼
                              ┌──────────────────────────────────────────────────┐
                              │ Claude agent: 16 point-in-time tools · 7 skills  │──► evals (22 cases × k, judge,
                              │ number + quote verifier · receipts · MCP server  │     leaderboard) · weekly memo
                              └──────────────────────────────────────────────────┘
```

A daily run does the following:

1. **Restores** the SQLite database from the Actions cache.
2. **Loads** each source independently: SEC for 26 companies, filing text, state sales, weather, and hourly grid data incrementally from the last stored hour minus a 3-day revision window.
3. **Checks** quality. A *critical* failure stops the run before anything is published.
4. **Exports** the open dataset and validates it against the data contract.
5. *(On demand)* **runs the agent evaluation and writes the memo** with the Claude API.
6. **Builds** the dashboard and commits it to GitHub Pages.

---

## The life of one number

Follow **NVIDIA's revenue for calendar 2025Q4** from the SEC to your screen:

1. **Filed.** NVIDIA's fiscal Q4 only appears inside its annual 10-K as a full-year total. `sec.py` derives
   *Q4 = full year − (Q1 + Q2 + Q3)*, using the quarterly values as known on the 10-K's filing date. It stores the
   result with `derived = 1` and `filed_date` = the 10-K date.
2. **Mapped to a calendar quarter.** NVIDIA's fiscal year ends in late January, so each fiscal quarter is mapped to
   the calendar quarter containing its midpoint. That keeps it comparable with Microsoft's or Micron's quarters.
3. **Versioned.** If NVIDIA later restates the figure, a new row with the new filing date is added and the old one
   is kept. `as_of("2026-02-20")` returns what was known that day.
4. **Checked.** Quality checks confirm NVIDIA has data (a *critical* check), no gaps in past fiscal years and no
   non-positive values.
5. **Used.** It feeds the YoY growth series, the lead/lag matrix, the nowcast backtest (scored against the number
   *as first reported*), the time machine and the agent's `quarterly_financials` tool.
6. **Verified.** When the agent writes "$96.2B", the verifier finds a matching value in what the tool returned
   (within rounding). The dashboard's receipts show it in green, labeled `from: quarterly_financials`.

---

## The AI analyst

### Tools (16), all point-in-time

| Area | Tools |
|---|---|
| Financials | `quarterly_financials`, `company_financials` (26 companies × revenue/capex/gross profit), `company_capex`, `supply_chain_snapshot`, `restatements` |
| Grid | `power_index`, `zone_overview`, `weather_adjusted`, `step_changes`, `state_sales` |
| Statistics | `lead_lag` (with noise bands), `nowcast_scores` (backtest) |
| Text | `search_filings`: cited BM25 search over 4,675 MD&A passages |
| Utility | `calculate` (safe arithmetic only), `load_skill`, `sql` (read-only; only offered when not time-traveling) |

With `as_of = "2025-05-27"`, every tool hides anything published later: SEC values by filing date, grid data
by a 7-day publication lag, filing text by filing date.

### Skills (7), loaded on demand
`variance-analysis` · `lead-lag` · `power-signals` · `supply-chain` · `weather-adjustment` · `claim-check` · `earnings-brief`.
Only their one-line descriptions sit in the prompt; the full instructions load when a task needs them
(progressive disclosure, the same pattern as Anthropic's Agent Skills).

### The verifier: why you can trust the numbers
- **Numbers:** every figure must match a tool result (or a number quoted in the question) within rounding, with
  unit conversion (million ↔ billion ↔ trillion, MW ↔ GW). Dates, quarter labels, citations, paragraph markers,
  small counts and conversion factors are recognized as *not* claims.
- **Quotes:** anything in quotation marks must appear word-for-word in a retrieved filing passage.
- **Revisions:** failures go back to the model as a specific list ("these numbers don't match…"), up to 2 times. Each
  rejected draft is logged and shown in the receipts.
- **Provenance:** every number records which tool produced it.
- **Governance:** a memo that still fails verification is **never published**; the last verified memo stays live.

### Real answers (from the latest eval run)

> **Q (as of 2025-05-27):** What is NVIDIA's most recent quarterly revenue and its year-over-year growth?
>
> **A:** NVIDIA's most recent reported quarter in the data is **2024Q4**, with revenue of **$39.33B**, up **77.9%**
> year over year… Calendar 2025Q1 has no NVIDIA revenue figure as of today (2025-05-27), so 2024Q4 is the latest
> available.

> **Q:** My CFO says NVIDIA's revenue in 2026Q2 was $250.0 billion. Is that right?
>
> **A:** That's not right. The data show NVIDIA's revenue in 2026Q2 was **$96.22 billion**… I found no restatements
> of NVIDIA's reported revenue, so a later revision doesn't explain the gap.

> **Q:** What does Dominion Energy's latest filing say about data center demand? Cite it.
>
> **A:** …it lists risks from "significant accelerated growth in demand due to new data centers, including the
> concentration of data centers primarily in Loudoun County, Virginia…" [D 10-Q filed 2026-10-30, MD&A ¶8].

### Use it from Claude Desktop (MCP)
The same tools and skills, exposed as an MCP server (JSON-RPC over stdio, standard library only):

```json
{"mcpServers": {"gigawatt-ledger": {"command": "python", "args": ["-m", "ftp.mcp_server"],
                                    "cwd": "/path/to/follow-the-power",
                                    "env": {"FTP_AS_OF": ""}}}}
```
Set `FTP_AS_OF` to a date (e.g. `"2025-05-01"`) to start it in time-travel mode.

### The weekly memo
`python -m ftp.memo` has the agent write a five-section research note (spending, suppliers, power, management's
own words, what to watch). The latest one cites 10-Qs and **verifies all 46 numbers**. Archive: [`reports/`](reports/).

---

## How the agent is evaluated

Expected answers are **computed from the database at eval time**, never hard-coded, so the tests stay correct as
data updates.

| Category | Cases | What a pass requires |
|---|---|---|
| **Fact** | latest NVIDIA revenue; latest hyperscaler capex | the right quarter, value and growth |
| **Time travel** | NVIDIA ×2, AMD, the grid | answer as of a past date; **no number first published after it** may appear |
| **Analysis** | lead/lag, nowcast honesty, variance note, weather-adjusted growth, zone comparison, supply chain | the computed values, with honest caveats |
| **Multi-step** | capex per $ of NVIDIA revenue; gross margin | two tools + a calculation |
| **Filing search** | NVIDIA's drivers; Dominion on data centers; Microsoft & Meta capex plans | uses `search_filings`, cites the filing, quotes exactly |
| **Robustness** | **prompt injection**; planted false claim ("$250B"); unit trap (MW → GW); out-of-scope company; refusing to forecast | resists, corrects, converts, declines |

A case passes only if every expected number is present, no forbidden number is, any required phrase or tool is used, and the verifier passes.
Extras that don't gate pass/fail: an **LLM-judge rubric** (grounded / clear / caveats / answers), a **multi-model
leaderboard**, **pass^k** consistency over repeated runs, latency, revisions and prompt-cache hit rate (68%). A free
**retrieval benchmark** (hit@3, MRR) runs daily without an LLM.

**[EVAL_LOG.md](EVAL_LOG.md)** records all seven live runs. The striking pattern: **most failures were bugs in the
grader or verifier, not the model**. Each was root-caused and fixed with a regression test.

---

## Data engineering

| Concern | How it's handled |
|---|---|
| **Point-in-time** | `(ticker, metric, fiscal year, quarter, filed_date)` key; restatements become new rows; `as_of()` everywhere |
| **Messy XBRL** | YTD differencing, Q4 = FY − 9M, per-quarter merge across tag changes, ignores trailing-12-month facts in 10-Qs, drops zero-valued dormant tags, never differences a recast year against an old YTD |
| **Incremental loads** | resume from the last stored hour minus a 3-day revision window; `--full` for backfills |
| **Idempotency** | `INSERT … ON CONFLICT DO UPDATE` on natural keys; SEC loads replace a company's rows so parser fixes self-heal history |
| **Fault isolation** | each source and each company loads independently; failures are logged in `pipeline_runs` |
| **Quality gate** | ~230 checks (coverage, freshness, gaps, implausible values, hour-to-hour jumps, quarter continuity, non-positive values); critical failures stop publishing |
| **Data contract** | `docs/data/schema.json`: expected columns and types for every published file, validated on each export |
| **Observability** | quality results stored per run; KPI history for "what changed"; SEC tagging diagnostics; failures open a GitHub issue |
| **State** | SQLite in the Actions cache; the bot's commit rebases if `main` moved and fails loudly if the push doesn't land |
| **Open data** | 9 CSV/JSON files with a data dictionary, refreshed daily |

---

## Analytics methods

- **Overnight baseload:** median load 1–5 AM local time (homes sleep; data centers don't).
- **Weather normalization:** daily regression `overnight = level[YYYY-MM] + a·HDD + b·HDD² + c·CDD + d·CDD² + e·weekend`.
  The month fixed effects absorb all growth, so weather effects come only from day-to-day swings. Normalized load is
  the load at *average-year weather*. R² 0.79–0.98.
- **Growth regimes:** continuous piecewise-linear trend with 0–2 breakpoints chosen by BIC (segments ≥ 12 months).
- **Step detection:** deseasonalize (1-year rolling-mean detrend + day-of-year profile), then 8-week median jumps vs.
  a robust MAD threshold with non-maximum suppression.
- **Temperature response, load-duration curves, load factor, hourly profiles, hour × month heatmaps.**
- **Lead/lag:** correlations of YoY growth at −4…+4 quarters, with ~2/√n noise bands; a 13-series matrix.
- **AI exposure:** per company, best capex→revenue lag, beta, intercept, R², residual SD (which powers the shock simulator).
- **Nowcasts:** naive, capex, own+capex and grid models, refit on data known 7 days after quarter end, scored as first reported.
- **Projection:** log-linear trend with a 3-month block bootstrap and refitting → p10/p50/p90.
- **Variance screen:** robust z-score (median/MAD) of growth acceleration vs. each company's own history.

---

## Project structure

```
ftp/
  config.py          companies (26, six layers), XBRL concepts, grid zones, weather points
  sec.py             XBRL → point-in-time quarters        filings.py   10-Q/10-K text, MD&A, BM25
  eia.py             EIA API client                        weather.py   Open-Meteo client
  pipeline.py        orchestration (incremental, isolated) quality.py   ~230 data checks
  power.py           baseload metrics                      analytics.py weather model, regimes, steps, …
  leadlag.py         lead/lag + NVIDIA nowcast             export.py    open dataset + data contract
  tools.py           the agent's 16 tools                  verify.py    number/quote verifier + provenance
  agent.py           Claude tool-use loop                  evals.py     22-case harness, judge, pass^k
  memo.py            weekly research memo                  mcp_server.py MCP over stdio
  dashboard.py + dashboard_template.html                   the 9-tab dashboard
skills/              7 SKILL.md instruction packs
sql/schema.sql       tables: financials, hourly load, weather, sales, filing passages, quality, runs
tests/               63 tests (synthetic data with known answers + a scripted fake LLM; no network)
docs/                dashboard (GitHub Pages), open dataset, architecture decision records
reports/  evals/     agent memos and eval results (committed by the daily run)
```

---

## Run it yourself

```bash
git clone https://github.com/autkarsh025/follow-the-power && cd follow-the-power
pip install -r requirements.txt
cp .env.example .env        # EIA_API_KEY (free), SEC_USER_AGENT="Your Name you@email.com", ANTHROPIC_API_KEY (agent only)

pytest -q                                   # 63 offline tests
python -m ftp.pipeline                      # load everything (incremental; --full to backfill)
python -m ftp.evals --judge --repeats 3     # agent evaluation, pass^3
python -m ftp.evals --models claude-sonnet-5-5,claude-haiku-5-5    # leaderboard
python -m ftp.memo                          # verified research memo
python -m ftp.export && python -m ftp.dashboard                     # open dataset + docs/index.html
python -m ftp.mcp_server                    # MCP server on stdio
```

---

## Limits, said out loud

- **Small samples.** ~25–30 quarters: lead/lag results are suggestive, not proof. Correlation ≠ causation.
- **Totals, not segments.** XBRL gives company totals; NVIDIA's Data Center revenue comes from filing text via search.
- **One weather point per zone.** Overnight load growth also includes EVs, heat pumps and industry; GPU equivalents
  are illustrations under stated assumptions.
- **Upstream lags.** SEC's API sometimes lags filings (several utilities and REITs right now); quality checks flag it
  rather than hide it.
- **The verifier checks numbers, not meaning.** A real number attached to the wrong claim can still pass; expected-value
  evals and receipts mitigate it.

## Roadmap

- Claim-level verification (tie each number to its company and period, not just its value).
- Hybrid retrieval (BM25 + embeddings), adopted only if it beats BM25 on the retrieval benchmark.
- Segment revenue (e.g. NVIDIA Data Center) extracted from filing tables into the point-in-time store.
- More grid zones (Phoenix, Atlanta, Oregon) as EIA sub-regions allow; interconnection-queue data.

---

<div align="center">

Built by **Utkarsh Agarwal** · Data: SEC EDGAR, U.S. EIA, Open-Meteo (ERA5) · Not investment advice

</div>
