# follow-the-power ⚡ the AI boom, seen through money *and* electricity

**[▶ Live dashboard](https://autkarsh025.github.io/follow-the-power/)** · **[Open dataset](https://autkarsh025.github.io/follow-the-power/data/README.md)** · ![tests](https://github.com/autkarsh025/follow-the-power/actions/workflows/tests.yml/badge.svg)

A data platform and a **verified Claude analyst agent** that follow the AI build-out from big tech's budgets, down a
26-company supply chain, to hourly electricity demand in five data-center hubs. Built only from official public data
(SEC EDGAR, U.S. EIA, ERA5 weather), with every number stored **point-in-time**.

## In plain English

AI runs on GPUs, and GPUs run on electricity. When Microsoft, Alphabet, Amazon and Meta spend hundreds of billions
on AI, that money flows to chip makers, server builders, power-equipment makers and data-center landlords, and
ends up as **electricity demand** on the grid. This project:

1. **Follows the money:** revenue, capex and gross margins for 26 companies in six layers, parsed from SEC
   filings, remembering *the date each number became public* (so history can be replayed without hindsight).
2. **Follows the power:** hourly demand in 5 grid zones since 2019, with **weather removed** by a degree-day
   regression, so a hot summer isn't mistaken for data-center growth.
3. **Finds data centers in the grid:** they run 24/7, so they lift the *overnight* load. Step detection flags
   sudden sustained jumps (a campus switching on); temperature-response curves show more load *at the same temperature*.
4. **Tests what leads what, honestly:** lead/lag with noise bands, and point-in-time nowcast backtests for every
   company (does money or power predict revenue better than "growth continues"? Mostly not, and it says so).
5. **Puts a Claude agent on top:** 16 tools, 7 skills, cited filing search, an MCP server. Every number it writes is
   checked against tool output, quotes are checked word-for-word, and it's evaluated by **replaying history**.

## What it found (real data, Nov 2026)

| | |
|---|---|
| **Spending** | Hyperscaler capex hit **$201.9B in 2026Q3, +108% YoY**, 11.7× the 2019 quarterly average. NVIDIA's latest quarter: **$96.2B, +106%**. |
| **Power** | Northern Virginia's overnight baseload is **up ~50% since 2019 (~4,900 MW)**, about 2.7M GPUs at 1.4 kW × PUE 1.3. Ohio +22%, Dallas +14%, Chicago +3%. |
| **Acceleration** | A statistical break test (piecewise trend, BIC) finds **Northern Virginia accelerated in May 2024 (~590 → ~1,100 MW/yr)** and **Ohio broke out in August 2024 (~140 → ~1,270 MW/yr)**; Dallas sped up from late 2023. |
| **Contrast** | West Texas grew +118%, but that's oil, gas and crypto mining, so it's kept as a contrast zone and out of the data-center totals. The step detector's one hit there is the April 2020 oil crash. |
| **Supply chain** | Chips & memory is the fastest-growing layer (TTM revenue +70%). Micron, GE Vernova and Dell track hyperscaler capex most tightly. |
| **Honesty** | Grid data beats a naive nowcast for only **6 of 24** companies. The grid *confirms* the build-out; it rarely front-runs earnings. |
| **Agent** | **21/21 eval cases on both Claude Sonnet 5.5 and Haiku 5.5** (time travel, planted false claims, unit traps, out-of-scope questions, cited filing search, multi-step math); 235 numbers verified; LLM judge 4.27/5 (Sonnet) and 4.16/5 (Haiku). Across six live runs, most failures were grader/verifier bugs, each fixed with a test ([eval log](EVAL_LOG.md)). |
| **Data quality** | Automated checks caught SEC's API lagging on several utilities' and REITs' latest 10-Qs, and an XBRL tag holding zeros (fixed by tag fall-through). |

## Architecture

```
 SEC EDGAR ─ XBRL companyfacts (26 cos) ─┐
           └ 10-Q/10-K MD&A text ─────────┤                           ┌─ weather normalization, step detection
 EIA ───── hourly demand (5 zones),       ├─► point-in-time SQLite ───┼─ lead/lag matrix, AI-exposure scores
           state retail sales ────────────┤   incremental · idempotent├─ nowcast backtests (26 companies)
 Open-Meteo ERA5 daily temperature ───────┘   quality-gated           └─ BM25 filing search (4,600+ passages)
                                                     │
            Claude agent: 16 point-in-time tools · 7 skills · number + quote verifier · MCP server
                                                     │
        GitHub Actions (daily): load → quality checks → open dataset → [evals · memo] → dashboard (Pages)
```

## Engineering highlights

**Data engineering**
- **Point-in-time financials.** XBRL mixes quarterly, year-to-date, annual and restated values. `sec.py` derives clean
  quarters (direct facts, YTD differencing, Q4 = FY − 9M), keeps every version with its first filing date, and
  merges XBRL tags per quarter (companies switch tags; utilities report trailing-12-month figures inside 10-Qs,
  which the parser deliberately ignores when finding fiscal years).
- **Incremental, idempotent loads.** Hourly data resumes from the last stored hour minus a 3-day revision window;
  upserts make re-runs safe; the SQLite database persists between Actions runs in the cache. SEC loads replace a
  company's rows, so parser fixes self-heal stored history.
- **Quality gate.** ~230 checks per run (coverage, freshness, gaps, implausible values, quarter continuity,
  non-positive financials). Critical failures stop the run before anything is published.
- **Fault isolation.** Each source and each company loads independently; one bad filing can't block the rest.
- **Open dataset.** Cleaned tables published daily as CSV with a data dictionary.

**Analytics**
- Weather normalization: per-day regression with **month fixed effects** (absorbing all growth) + quadratic heating/
  cooling degree days + weekend effect; normalized = load at average-year weather (actual − actual weather effect +
  one constant average effect), which also removes the seasonal swing. R² 0.79–0.98.
- Growth regimes: continuous piecewise-linear trend with 0–2 breakpoints chosen by BIC: *when* did growth speed up?
- Step detection: deseasonalized (1-year rolling-mean detrend + day-of-year profile), then 4-week median jumps
  vs. a robust MAD threshold, with non-maximum suppression.
- Load-duration curves, hour × month heatmaps, temperature-response curves, bootstrap trend projection (block
  bootstrap with refitting), lead/lag with ~2/√n noise bands, robust-z variance screen, restatement tracker.

**AI engineering**
- **Agent** (`agent.py`): Claude tool use with prompt caching. With `as_of`, tools literally can't read later data;
  raw SQL is only offered without `as_of` and runs read-only.
- **Verifier** (`verify.py`): every figure must match a tool result (or a number quoted in the question) within
  rounding, with unit conversion (million↔billion, GW↔MW). Quotes must appear verbatim in retrieved passages.
  Failures force a revision. Every number carries **provenance** (which tool produced it).
- **RAG** (`filings.py`): MD&A extraction (skipping the table-of-contents copy and hidden inline-XBRL), paragraph-aware
  chunking, BM25 ranking, citations like `[NVDA 10-Q filed 2026-08-26, MD&A ¶14]`, point-in-time by filing date,
  plus an offline retrieval benchmark (hit@3, MRR).
- **Evals** (`evals.py`): ~20 cases across fact, time-travel, analysis, multi-step, filing-search and robustness
  (planted false claims, refusing to invent forecasts). Expected answers are computed from the data, never
  hard-coded; time-travel cases list future numbers that must not appear. Optional LLM-judge rubric and
  multi-model leaderboard; tokens, cache hits and latency tracked.
- **MCP server** (`mcp_server.py`): the same tools and skills over JSON-RPC/stdio for Claude Desktop/Code
  (standard library only, tested over a real subprocess).
- **Automated memo** (`memo.py`): the agent writes a verified weekly research note, archived in `reports/`.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env     # EIA_API_KEY (free), SEC_USER_AGENT="Name email", ANTHROPIC_API_KEY (agent only)
pytest -q                # ~50 tests, offline: synthetic data with known answers + a scripted fake LLM
python -m ftp.pipeline   # load everything (incremental; --full to backfill)
python -m ftp.evals --judge                      # agent evaluation
python -m ftp.evals --models claude-sonnet-5-5,claude-haiku-5-5   # leaderboard
python -m ftp.memo       # agent-written research memo
python -m ftp.export && python -m ftp.dashboard  # open dataset + docs/index.html
python -m ftp.mcp_server # MCP server on stdio
```

## Limits (said out loud)

- ~25–30 quarters is a small sample: lead/lag results are suggestive, not proof. Correlation ≠ causation.
- XBRL gives company totals, not segments (NVIDIA's Data Center revenue comes from filing text via search).
- One weather point per zone; overnight load growth also includes EVs, heat pumps and industry. GPU equivalents
  are illustrations under stated assumptions.
- SEC's companyfacts API sometimes lags filings; the quality checks flag it rather than hide it.

See **[EVAL_LOG.md](EVAL_LOG.md)** for every live eval run and the root cause of each failure, **[BUILD_GUIDE.md](BUILD_GUIDE.md)** for how every piece works and why, and **[docs/adr](docs/adr/README.md)** for the
architecture decision records (point-in-time storage, SQLite + cache, code-based verification, BM25, weather model,
publishing negative results).

---
Data: SEC EDGAR; U.S. Energy Information Administration (EIA-930, retail sales); Open-Meteo (ERA5). Not investment advice.
