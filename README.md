# follow-the-power ⚡ the AI boom, seen through the electrical grid

**[▶ Live dashboard](https://autkarsh025.github.io/follow-the-power/)** · ![tests](https://github.com/autkarsh025/follow-the-power/actions/workflows/tests.yml/badge.svg)

A data pipeline and a verified Claude analyst agent that follow the AI build-out from **big tech's budgets → NVIDIA's revenue → the power grid**, using only official public data (SEC filings and U.S. Energy Information Administration grid data), with every number tracked **point-in-time**.

## In plain English

AI runs on GPUs, and GPUs run on electricity. Every NVIDIA chip ends up in a data center that draws power around the clock, and Northern Virginia (PJM's Dominion zone) hosts the largest concentration of data centers in the world. This project:

1. **Collects the money:** NVIDIA's quarterly revenue and the capital spending of Microsoft, Alphabet, Amazon and Meta, straight from SEC filings, remembering **the date each number was first published**.
2. **Collects the power:** hourly electricity demand in Northern Virginia since 2019, plus monthly commercial electricity sales in data-center states.
3. **Detects data centers in the grid:** they run 24/7, so they raise the **overnight baseload** and make the daily load curve flatter.
4. **Tests what leads what,** honestly: correlations come with noise bands, and forecasts are backtested using only data that was public at the time.
5. **Puts a Claude agent on top:** it answers questions using the data, **every number it writes is automatically verified**, and it's scored by **replaying history** so it can't see the future.

## Findings (real data, through Q3 2026)

- **The chain is visible: NVIDIA → capex → power.** NVIDIA's revenue growth runs ~3–4 quarters ahead of hyperscaler capex growth (r ≈ 0.51 vs. a ±0.42 noise band), and capex growth runs ~1 quarter ahead of Northern Virginia's overnight load growth (r ≈ 0.54 vs. ±0.40).
- **The scale:** hyperscaler capex reached **$202B in Q3 2026, +108% YoY**; NVIDIA's latest reported quarter was **$96.2B, +106%**.
- **The honest result:** the grid does **not** forecast NVIDIA's revenue better than a naive "growth continues" guess (average miss 41 vs. 36 percentage points). The grid confirms the build-out *after* the money moves; it doesn't front-run it.

## Architecture

```
 SEC EDGAR (XBRL companyfacts) ─┐                         ┌─ power index (overnight baseload, flatness)
                                ├─► point-in-time SQLite ─┼─ lead/lag (cross-correlation + noise bands)
 EIA (hourly zone demand,       │   every value + filed   ├─ nowcast backtest (no look-ahead)
      state retail sales) ──────┘   date / known date     └─ Claude agent: tools + skills + verifier
                                                                         │
                         GitHub Actions (daily) ─► evals + dashboard (GitHub Pages)
```

## Design decisions

**Point-in-time financials.** SEC's XBRL data mixes quarterly figures, year-to-date figures, full-year totals and restatements. `ftp/sec.py` turns them into clean quarters (direct quarterly facts, YTD differencing for cash-flow items, Q4 = FY − 9M) and keeps **every published version with its first filing date**. `sec.as_of(df, date)` answers "what was known on this date?", which is the foundation for honest backtests.

**Restatements vs. re-reports.** A value re-filed unchanged keeps its original date; a changed value becomes a new version. The current, unfinished fiscal year (no 10-K yet) is inferred from 10-Q filings, a bug the first real run exposed.

**The data-center signature.** Data centers draw flat power 24/7, so the analysis uses the median **1–5 AM load** (baseload) and **flatness** (overnight ÷ peak), compared year over year to cancel seasons.

**Honest statistics.** Every correlation is reported with its noise band (~2/√n). The nowcast backtest stands 7 days after each quarter ends (before NVIDIA reports), uses only filings public by then, and scores against the number as **first reported**.

**The agent** (`ftp/agent.py`, `ftp/tools.py`):
- **Tools** are point-in-time: with `as_of`, the agent literally can't read later data. Raw SQL is only offered without `as_of`, because time rules can't be enforced on arbitrary queries, and it runs on a read-only connection.
- **Skills** (`skills/*/SKILL.md`): variance analysis, lead/lag interpretation and grid signals, loaded on demand with `load_skill`.
- **Number verifier** (`ftp/verify.py`): every figure in an answer must match a number a tool returned (or a `calculate` result), within rounding. Unverifiable numbers trigger a forced revision.
- **Evaluation harness** (`ftp/evals.py`): expected answers are computed from the database, never hard-coded. Time-travel cases also list **future numbers that must not appear**. Results: `evals/results.md` and the dashboard.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env            # EIA_API_KEY (free), ANTHROPIC_API_KEY (for the agent), SEC_USER_AGENT="Name email"
pytest -q                        # offline: synthetic data + a scripted fake LLM
python -m ftp.pipeline           # load SEC + EIA data (~1 minute)
python -m ftp.report             # coverage, power index, lead/lag, backtest
python -m ftp.evals              # agent evaluation (Claude API; typically well under $1)
python -m ftp.dashboard          # docs/index.html
```

## Limits (said out loud)

- ~25–30 quarters is a small sample; lead/lag results are suggestive, not proof.
- Correlation of growth rates ≠ causation: the whole AI cycle moves these series together.
- One grid zone isn't the whole market; weather, electrification and population also move load.
- NVIDIA's total revenue is used. Its Data Center segment isn't in the SEC's simple XBRL API (it's dimensional data); extracting it from filings is a planned extension.

---
Data: SEC EDGAR; U.S. Energy Information Administration (EIA-930 hourly subregion demand; electric power retail sales). Not investment advice.
