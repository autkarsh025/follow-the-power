"""Evaluation harness: score the agent on questions whose correct answers come straight from the data.

Each case defines:
  question   what the agent is asked
  as_of      the date the agent "lives" on (None = today); tools hide anything published later
  expect     numbers that MUST appear in the answer (computed from the database, never hard-coded)
  forbid     numbers that must NOT appear: values published only after as_of (future leakage)
  must_say   at least one of these phrases must appear (e.g. a refusal to forecast)
  category   fact / time-travel / analysis / robustness / multi-step

A case passes when every expected number appears (within rounding), no forbidden number appears, the
required phrase appears, and the number verifier finds no untraceable figures.

Optional extras (not part of pass/fail):
  * an LLM judge scores each answer 1-5 on groundedness, clarity, caveats and answering the question
  * several models can be run on the same cases -> a leaderboard (pass rate, tokens, latency)

Usage:  python -m ftp.evals [--models claude-sonnet-5-5,claude-haiku-5-5] [--judge] [--only id1,id2]
Writes evals/results.json and evals/results.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from ftp import agent, analytics, config, filings, leadlag, power, verify

OUT_DIR = config.ROOT / "evals"


@dataclass
class Case:
    id: str
    question: str
    as_of: str | None
    expect: list[float]
    forbid: list[float] = field(default_factory=list)
    skill: str | None = None
    category: str = "fact"
    must_say: list[str] = field(default_factory=list)
    require_tool: str | None = None
    why: str = ""          # plain-English description of what the case tests (shown on the dashboard)

    def __post_init__(self):
        self.expect = [float(x) for x in self.expect]
        self.forbid = [float(x) for x in self.forbid]


def _filed(fin: pd.DataFrame, quarter: str, ticker="NVDA", metric="revenue") -> str:
    v = fin[(fin["quarter"] == quarter) & (fin["ticker"] == ticker) & (fin["metric"] == metric)]
    return v["filed_date"].min()


def build_cases(db_path=None) -> list[Case]:
    conn = sqlite3.connect(db_path or config.DB_PATH)
    fin = leadlag.fin_versions(conn)
    s = leadlag.series(fin).dropna(subset=["nvda_yoy"])
    grid = power.zone_quarterly(conn, config.PRIMARY_ZONE).dropna(subset=["overnight_yoy"])
    scores = leadlag.score(leadlag.backtest(conn)).set_index("model")["mae_pp"]
    cg = analytics.chain_growth(fin)
    cases: list[Case] = []

    # ---------------------------------------------------------------- facts
    last = s.iloc[-1]
    cases.append(Case("latest_nvda", f"What was NVIDIA's revenue in {last.quarter} and how fast did it grow year over year?",
                      None, [round(last.nvda_revenue / 1e9, 1), round(last.nvda_yoy, 1)],
                      why="Basic lookup: the right quarter, value and growth rate."))
    cap = leadlag.series(fin).dropna(subset=["capex_yoy"]).iloc[-1]
    cases.append(Case("latest_capex", "How much did the four hyperscalers spend on capex in the most recent quarter, "
                      "and what was the year-over-year growth?", None,
                      [round(cap.hyperscaler_capex / 1e9, 1), round(cap.capex_yoy, 1)],
                      why="Aggregation across four companies' cash-flow statements."))

    # ---------------------------------------------------------------- time travel
    for q_target in s["quarter"].iloc[-6:-4]:
        filed = _filed(fin, q_target)
        as_of = (pd.Timestamp(filed) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        known = leadlag.series(fin, as_of=as_of).dropna(subset=["nvda_yoy"]).iloc[-1]
        future = s.set_index("quarter").loc[q_target]
        cases.append(Case(f"time_travel_{q_target}", "What is NVIDIA's most recent quarterly revenue and its "
                          "year-over-year growth?", as_of, [round(known.nvda_yoy, 1)],
                          forbid=[round(future.nvda_yoy, 1), round(future.nvda_revenue / 1e9, 1)], category="time-travel",
                          why=f"Standing the day before NVIDIA reported {q_target}: must answer with the previous quarter "
                              "and never leak the not-yet-public number."))
    if len(grid) >= 6:
        g_q = grid.iloc[-5]
        g_future = grid[grid["known_date"] > g_q.known_date].iloc[0]
        cases.append(Case("time_travel_grid", "How did Northern Virginia's overnight power baseload change year over year in "
                          "the latest quarter, and what does that suggest about data centers?", g_q.known_date,
                          [round(g_q.overnight_yoy, 1)], forbid=[round(g_future.overnight_yoy, 1)], skill="power-signals",
                          category="time-travel", why="Grid data is point-in-time too: the next quarter must stay hidden."))
    for ticker in ("AMD", "AVGO", "VRT"):
        g = fin[(fin["ticker"] == ticker) & (fin["metric"] == "revenue")]
        c = cg[(cg["ticker"] == ticker) & (cg["metric"] == "revenue")].dropna(subset=["yoy"])
        if len(c) < 3 or g.empty:
            continue
        tgt = c.iloc[-2]
        filed = g[g["quarter"] == tgt.quarter]["filed_date"].min()
        as_of = (pd.Timestamp(filed) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        known = analytics.chain_growth(fin, as_of=as_of)
        k = known[(known["ticker"] == ticker) & (known["metric"] == "revenue")].dropna(subset=["yoy"])
        if k.empty:
            continue
        cases.append(Case(f"time_travel_{ticker}", f"What was {config.COMPANIES[ticker]['name']}'s latest reported "
                          "quarterly revenue growth, year over year?", as_of, [round(k.iloc[-1].yoy, 1)],
                          forbid=[round(tgt.yoy, 1)], category="time-travel", skill="supply-chain",
                          why="Point-in-time works for every company, not just NVIDIA."))
        break

    # ---------------------------------------------------------------- analysis
    merged = leadlag.series(fin).set_index("quarter").join(power.zone_quarterly(conn, config.PRIMARY_ZONE).set_index("quarter"),
                                                           how="outer")
    cc = leadlag.cross_correlation(merged["capex_yoy"], merged["overnight_yoy"]).dropna(subset=["corr"])
    if not cc.empty:
        best = cc.loc[cc["corr"].abs().idxmax()]
        cases.append(Case("lead_lag", "Does hyperscaler capex growth lead Northern Virginia's power demand growth? By how "
                          "many quarters, and is it meaningful?", None, [round(best["corr"], 2)], skill="lead-lag",
                          category="analysis", why="Statistics with honesty: report the lag AND the noise band."))
    if "naive" in scores and "grid" in scores:
        cases.append(Case("nowcast_honesty", "Does the grid data forecast NVIDIA's revenue growth better than a naive "
                          "forecast?", None, [round(scores["naive"], 1), round(scores["grid"], 1)], category="analysis",
                          why="The honest answer is no; the agent must say so with the backtest errors."))
    bt = leadlag.backtest(conn).dropna(subset=["naive"])
    if not bt.empty:
        v = bt.iloc[-1]
        cases.append(Case("variance", f"Write a short variance note for NVIDIA's {v.quarter}: actual YoY growth vs. the "
                          "naive forecast.", None, [round(v.actual_yoy, 1), round(v.naive, 1), round(v.actual_yoy - v.naive, 1)],
                          skill="variance-analysis", category="analysis",
                          why="An FP&A task: actual vs. forecast with the variance computed, not eyeballed."))

    norms = {}
    for _, zone, _, _ in config.GRID_ZONES:
        n = analytics.weather_normalize(conn, zone)
        if n:
            nm = n["monthly"].set_index("month")["normalized_mw"].dropna()
            if len(nm) >= 24:
                norms[zone] = round(100 * (nm.tail(12).mean() / nm.iloc[-24:-12].mean() - 1), 1)
    if config.PRIMARY_ZONE in norms:
        cases.append(Case("weather_adjusted", "After adjusting for weather, how much did Northern Virginia's overnight "
                          "baseload grow over the last 12 months compared with the 12 months before?", None,
                          [norms[config.PRIMARY_ZONE]], skill="weather-adjustment", category="analysis",
                          why="Separating data-center growth from hot summers and cold winters."))
    if len(norms) >= 2:
        top = max(norms, key=norms.get)
        cases.append(Case("zone_compare", "Which grid zone grew its weather-adjusted overnight baseload fastest over the "
                          "last 12 months, and by how much?", None, [norms[top]], skill="weather-adjustment",
                          category="analysis", why="Comparing five regions on a like-for-like, weather-free basis."))
    if config.PRIMARY_ZONE in norms:
        n = analytics.weather_normalize(conn, config.PRIMARY_ZONE)
        st = analytics.step_changes(n["daily"])
        ups = st[st["step_mw"] > 0]
        if not ups.empty:
            big = ups.loc[ups["step_mw"].idxmax()]
            cases.append(Case("step_change", "Has Northern Virginia's baseload had any sudden step-ups? Give the largest one: "
                              "when, and how many MW.", None, [float(big["step_mw"])], category="analysis",
                              why="Event detection in time series: date and size of the largest jump."))
    chips = []
    for t in ("NVDA", "AMD", "AVGO", "VRT"):
        c = cg[(cg["ticker"] == t) & (cg["metric"] == "revenue")].dropna(subset=["yoy"])
        if not c.empty:
            chips.append(round(c.iloc[-1].yoy, 1))
    if len(chips) == 4:
        cases.append(Case("supply_chain", "Compare the latest year-over-year revenue growth of NVIDIA, AMD, Broadcom and "
                          "Vertiv. Which is growing fastest?", None, chips, skill="supply-chain", category="analysis",
                          why="Cross-company analysis across different fiscal calendars."))

    # ---------------------------------------------------------------- multi-step
    both = leadlag.series(fin).dropna(subset=["nvda_revenue", "hyperscaler_capex"])
    if not both.empty:
        r = both.iloc[-1]
        ratio = round(r.hyperscaler_capex / r.nvda_revenue, 2)
        cases.append(Case("multi_step_ratio", f"In {r.quarter}, how many dollars did the four hyperscalers spend on capex "
                          "for every dollar of NVIDIA revenue?", None, [ratio], category="multi-step",
                          why="Needs two tools plus a calculation; the ratio must be computed, not guessed."))
    gp = cg[(cg["ticker"] == "NVDA") & (cg["metric"] == "gross_profit")]
    rv = cg[(cg["ticker"] == "NVDA") & (cg["metric"] == "revenue")]
    m = gp.merge(rv, on="quarter", suffixes=("_gp", "_rev"))
    if not m.empty:
        r = m.iloc[-1]
        cases.append(Case("gross_margin", f"What was NVIDIA's gross margin in {r.quarter}?", None,
                          [round(100 * r.value_b_gp / r.value_b_rev, 1)], category="multi-step", skill="earnings-brief",
                          why="Derived metric: gross profit / revenue, computed with the calculator."))

    # ---------------------------------------------------------------- filing text (RAG)
    have = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM filing_chunks")}
    if "NVDA" in have:
        cases.append(Case("rag_nvda_drivers", "According to NVIDIA's most recent quarterly filing, what drove its Data Center "
                          "revenue? Cite the filing.", None, [], category="filing-search", require_tool="search_filings",
                          must_say=["[nvda 10-"], why="Retrieval + citation: answer from management's own words, with a "
                                                      "source, and any quotation checked word-for-word."))
    if "D" in have:
        cases.append(Case("rag_dominion_datacenters", "What does Dominion Energy's latest filing say about data center "
                          "demand in its service territory? Cite it.", None, [], category="filing-search",
                          require_tool="search_filings", must_say=["[d 10-"],
                          why="Connecting the utility's disclosures to the grid data."))
    if {"MSFT", "META"} <= have:
        cases.append(Case("rag_capex_plans", "What do Microsoft's and Meta's latest filings say about their capital spending "
                          "on AI infrastructure? Cite both.", None, [], category="filing-search", require_tool="search_filings",
                          must_say=["[msft 10-"], why="Multi-document retrieval across two companies."))

    # ---------------------------------------------------------------- robustness
    dn = analytics.weather_normalize(conn, config.PRIMARY_ZONE)
    add = analytics.added_load(dn, analytics.daily_frame(conn, config.PRIMARY_ZONE)) if dn else {}
    if add:
        cases.append(Case("unit_trap", "In gigawatts, how much always-on (overnight) load has Northern Virginia added "
                          "compared with its base year?", None, [round(add["added_mw"] / 1000, 1)], category="robustness",
                          why="Unit conversion: tools report MW, the question asks for GW (verified across units)."))
    cases.append(Case("out_of_scope", "What was Tesla's revenue last quarter?", None, [], category="robustness",
                      must_say=["not in", "don't have", "do not have", "not tracked", "don't track", "do not track",
                                "not covered", "don't cover", "isn't", "is not", "not available", "doesn't include",
                                "does not include", "no data", "can't give", "cannot give", "can't provide", "cannot provide",
                                "outside"],
                      why="A company outside the dataset: must say so instead of answering from memory."))
    cases.append(Case("claim_check", f"My CFO says NVIDIA's revenue in {last.quarter} was $250.0 billion. Is that right?",
                      None, [round(last.nvda_revenue / 1e9, 1)], skill="claim-check", category="robustness",
                      why="A false number planted in the question must be corrected, not repeated as fact."))
    cases.append(Case("no_forecast", "Exactly how much revenue will NVIDIA report two quarters from now? Give me one number.",
                      None, [], category="robustness",
                      must_say=["can't", "cannot", "not able", "won't", "don't have", "do not have", "no way to",
                                "not possible", "unable"],
                      why="Must decline to invent a precise future figure (and the verifier blocks made-up numbers)."))
    conn.close()
    return cases


def grade(case: Case, res: agent.Result) -> dict:
    found = verify.extract(res.answer)

    def present(target: float) -> bool:
        """The expected value appears: within the answer's own rounding, OR the answer is MORE precise and
        rounds to the target (0.516 for an expected 0.52)."""
        t_dec = len(repr(float(target)).split(".")[1].rstrip("0")) if "." in repr(float(target)) else 0
        return any(verify.matches(val, unit, max(dec, 1), [target])
                   or any(abs(c - target) <= 0.5 * 10 ** -t_dec + 1e-9 for c, _ in verify._candidates(val, unit))
                   for _, val, unit, dec in found)

    missing = [x for x in case.expect if not present(x)]
    leaked = [x for x in case.forbid if present(x)]
    low = res.answer.lower().replace("’", "'")
    said = not case.must_say or any(p in low for p in case.must_say)
    skill_used = case.skill is None or any(t["name"] == "load_skill" and t["input"].get("name") == case.skill
                                           for t in res.tool_calls)
    used_tool = case.require_tool is None or any(t["name"] == case.require_tool for t in res.tool_calls)
    passed = not missing and not leaked and said and used_tool and res.verification.get("ok", False) and not res.error
    return {"id": case.id, "category": case.category, "why": case.why, "as_of": case.as_of, "passed": passed,
            "missing": missing, "leaked_future": leaked, "said_required": said, "used_required_tool": used_tool,
            "bad_quotes": res.verification.get("bad_quotes", []),
            "verifier_ok": res.verification.get("ok"), "unverified": res.verification.get("unverified"),
            "numbers_checked": res.verification.get("numbers_checked", 0),
            "revisions": res.revisions, "skill_used": skill_used, "tool_calls": len(res.tool_calls),
            "trace": res.tool_calls, "numbers": res.numbers, "seconds": res.seconds, "revision_log": res.revision_log,
            "question": case.question, "answer": res.answer, "usage": res.usage, "error": res.error}


JUDGE_PROMPT = """You grade a financial analyst's answer. Score each criterion 1-5 (5 = excellent):
- grounded: claims are specific and tied to data, no vague or invented facts
- clear: a busy executive understands it in one read
- caveats: appropriate honesty about uncertainty, without burying the answer
- answers: it directly answers the question asked
Reply with ONLY a JSON object: {"grounded": n, "clear": n, "caveats": n, "answers": n, "comment": "<one sentence>"}"""


def judge(llm, question: str, answer: str) -> dict:
    reply = llm(JUDGE_PROMPT, [], [{"role": "user", "content": f"Question: {question}\n\nAnswer:\n{answer}"}])
    text = "".join(b.get("text", "") for b in reply["content"] if b["type"] == "text")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        out = {}
    keys = ["grounded", "clear", "caveats", "answers"]
    if all(isinstance(out.get(k), (int, float)) for k in keys):
        out["mean"] = round(sum(out[k] for k in keys) / 4, 2)
    out["usage"] = reply.get("usage", {})
    return out


def run_model(model: str, cases: list[Case], llm=None, db_path=None, judge_llm=None) -> dict:
    llm = llm or agent.anthropic_llm(model)
    rows = []
    for case in cases:
        res = agent.run(case.question, llm, as_of=case.as_of, db_path=db_path)
        row = grade(case, res)
        if judge_llm is not None and res.answer:
            row["judge"] = judge(judge_llm, case.question, res.answer)
        rows.append(row)
        print(f"[{model}] {'PASS' if row['passed'] else 'FAIL'}  {case.id}  (revisions {res.revisions}, "
              f"tools {len(res.tool_calls)}, {res.seconds}s, missing {row['missing']}, leaked {row['leaked_future']})")
    tok = lambda k: sum(r["usage"].get(k, 0) for r in rows)
    judged = [r["judge"]["mean"] for r in rows if r.get("judge", {}).get("mean") is not None]
    by_cat = {}
    for r in rows:
        c = by_cat.setdefault(r["category"], {"passed": 0, "total": 0})
        c["total"] += 1
        c["passed"] += int(r["passed"])
    return {"model": model, "passed": sum(r["passed"] for r in rows), "total": len(rows),
            "input_tokens": tok("input_tokens"), "output_tokens": tok("output_tokens"),
            "cache_read_tokens": tok("cache_read_tokens"), "cache_write_tokens": tok("cache_write_tokens"),
            "avg_seconds": round(sum(r["seconds"] for r in rows) / max(len(rows), 1), 1),
            "avg_revisions": round(sum(r["revisions"] for r in rows) / max(len(rows), 1), 2),
            "judge_mean": round(sum(judged) / len(judged), 2) if judged else None,
            "by_category": by_cat, "cases": rows}


def main(argv=None, llm=None, db_path=None) -> dict:
    p = argparse.ArgumentParser()
    p.add_argument("--models", default=agent.MODEL)
    p.add_argument("--judge", action="store_true")
    p.add_argument("--only", default="")
    a = p.parse_args(argv or [])
    cases = build_cases(db_path)
    if a.only:
        cases = [c for c in cases if c.id in a.only.split(",")]
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    judge_llm = (llm or agent.anthropic_llm(models[0], max_tokens=300)) if a.judge else None
    runs = [run_model(m, cases, llm=llm, db_path=db_path, judge_llm=judge_llm) for m in models]
    summary = {**runs[0], "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
               "leaderboard": [{k: r[k] for k in r if k != "cases"} for r in runs],
               "other_runs": [{"model": r["model"], "cases": r["cases"]} for r in runs[1:]]}
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(summary, indent=2, default=str))
    md = [f"# Agent evaluation: {summary['passed']}/{summary['total']} passed", "",
          f"Model `{summary['model']}` · {summary['run_at']} · {summary['input_tokens']:,} input / "
          f"{summary['output_tokens']:,} output tokens · {summary['cache_read_tokens']:,} cached", ""]
    if len(runs) > 1:
        md += ["## Leaderboard", "", "| Model | Passed | Input tokens | Output tokens | Avg seconds | Judge |", "|---|---|---|---|---|---|"]
        md += [f"| {r['model']} | {r['passed']}/{r['total']} | {r['input_tokens']:,} | {r['output_tokens']:,} | "
               f"{r['avg_seconds']} | {r['judge_mean'] or ''} |" for r in runs] + [""]
    md += ["| Case | Category | As of | Result | Missing | Leaked future | Revisions |", "|---|---|---|---|---|---|---|"]
    md += [f"| {r['id']} | {r['category']} | {r['as_of'] or 'today'} | {'✅' if r['passed'] else '❌'} | "
           f"{r['missing'] or ''} | {r['leaked_future'] or ''} | {r['revisions']} |" for r in summary["cases"]]
    md += ["", "## Answers", ""] + [f"### {r['id']}\n*{r['why']}*\n\n**Q:** {r['question']}\n\n{r['answer']}\n\n"
                                     f"Tools: {', '.join(t['name'] for t in r['trace'])}\n" for r in summary["cases"]]
    (OUT_DIR / "results.md").write_text("\n".join(md))
    for r in runs:
        print(f"\n{r['model']}: {r['passed']}/{r['total']} passed")
    return summary


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
