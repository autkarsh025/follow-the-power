"""Evaluation harness: score the agent on questions whose correct answers come straight from the data.

Each case defines:
  question  what the agent is asked
  as_of     the date the agent "lives" on (None = today); tools hide anything published later
  expect    numbers that MUST appear in the answer (computed from the database, never hard-coded)
  forbid    numbers that must NOT appear: values only published after as_of (future leakage)

A case passes when every expected number appears (within rounding), no forbidden number appears,
and the number verifier finds no untraceable figures.

Usage:  python -m ftp.evals            (needs ANTHROPIC_API_KEY; ~8 questions, typically well under $1)
Writes evals/results.json and evals/results.md.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from ftp import agent, config, leadlag, power, verify

OUT_DIR = config.ROOT / "evals"


@dataclass
class Case:
    id: str
    question: str
    as_of: str | None
    expect: list[float]
    forbid: list[float] = field(default_factory=list)
    skill: str | None = None


def _filed(fin: pd.DataFrame, quarter: str, ticker="NVDA", metric="revenue") -> str:
    v = fin[(fin["quarter"] == quarter) & (fin["ticker"] == ticker) & (fin["metric"] == metric)]
    return v["filed_date"].min()


def build_cases(db_path=None) -> list[Case]:
    conn = sqlite3.connect(db_path or config.DB_PATH)
    fin = leadlag.fin_versions(conn)
    s = leadlag.series(fin).dropna(subset=["nvda_yoy"])
    grid = power.zone_quarterly(conn, "DOM").dropna(subset=["overnight_yoy"])
    scores = leadlag.score(leadlag.backtest(conn)).set_index("model")["mae_pp"]
    cases: list[Case] = []

    last = s.iloc[-1]
    cases.append(Case("latest_nvda", f"What was NVIDIA's revenue in {last.quarter} and how fast did it grow year over year?",
                      None, [round(last.nvda_revenue / 1e9, 1), round(last.nvda_yoy, 1)]))

    cap = leadlag.series(fin).dropna(subset=["capex_yoy"]).iloc[-1]
    cases.append(Case("latest_capex", "How much did the four hyperscalers spend on capex in the most recent quarter, "
                      "and what was the year-over-year growth?", None,
                      [round(cap.hyperscaler_capex / 1e9, 1), round(cap.capex_yoy, 1)]))

    # Time travel: stand one day before a quarter's results were filed. The previous quarter is the latest known.
    for q_target in s["quarter"].iloc[-6:-4]:
        filed = _filed(fin, q_target)
        as_of = (pd.Timestamp(filed) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        known = leadlag.series(fin, as_of=as_of).dropna(subset=["nvda_yoy"]).iloc[-1]
        future = s.set_index("quarter").loc[q_target]
        cases.append(Case(f"time_travel_{q_target}", "What is NVIDIA's most recent quarterly revenue and its "
                          "year-over-year growth?", as_of, [round(known.nvda_yoy, 1)],
                          forbid=[round(future.nvda_yoy, 1), round(future.nvda_revenue / 1e9, 1)]))

    g_q = grid.iloc[-5]
    g_as_of = g_q.known_date
    g_future = grid[grid["known_date"] > g_as_of].iloc[0]
    cases.append(Case("time_travel_grid", "How did Northern Virginia's overnight power baseload change year over year in "
                      "the latest quarter, and what does that suggest about data centers?", g_as_of,
                      [round(g_q.overnight_yoy, 1)], forbid=[round(g_future.overnight_yoy, 1)], skill="power-signals"))

    merged = leadlag.series(fin).set_index("quarter").join(power.zone_quarterly(conn, "DOM").set_index("quarter"), how="outer")
    cc = leadlag.cross_correlation(merged["capex_yoy"], merged["overnight_yoy"])
    best = cc.dropna(subset=["corr"]).loc[lambda d: d["corr"].abs().idxmax()]
    cases.append(Case("lead_lag", "Does hyperscaler capex growth lead Northern Virginia's power demand growth? By how many "
                      "quarters, and is it meaningful?", None, [round(best["corr"], 2)], skill="lead-lag"))

    cases.append(Case("nowcast_honesty", "Does the grid data forecast NVIDIA's revenue growth better than a naive forecast?",
                      None, [round(scores["naive"], 1), round(scores["grid"], 1)]))

    bt = leadlag.backtest(conn).dropna(subset=["naive"])
    v = bt.iloc[-1]
    cases.append(Case("variance", f"Write a short variance note for NVIDIA's {v.quarter}: actual YoY growth vs. the naive "
                      "forecast.", None, [round(v.actual_yoy, 1), round(v.naive, 1), round(v.actual_yoy - v.naive, 1)],
                      skill="variance-analysis"))
    conn.close()
    return cases


def grade(case: Case, res: agent.Result) -> dict:
    found = verify.extract(res.answer)

    def present(target: float) -> bool:
        return any(verify.matches(val, unit, max(dec, 1), [target]) for _, val, unit, dec in found)

    missing = [x for x in case.expect if not present(x)]
    leaked = [x for x in case.forbid if present(x)]
    skill_used = case.skill is None or any(t["name"] == "load_skill" and t["input"].get("name") == case.skill
                                           for t in res.tool_calls)
    passed = not missing and not leaked and res.verification.get("ok", False) and not res.error
    return {"id": case.id, "as_of": case.as_of, "passed": passed, "missing": missing, "leaked_future": leaked,
            "verifier_ok": res.verification.get("ok"), "unverified": res.verification.get("unverified"),
            "revisions": res.revisions, "skill_used": skill_used, "tool_calls": len(res.tool_calls),
            "question": case.question, "answer": res.answer, "usage": res.usage}


def main(llm: agent.LLM | None = None, db_path=None) -> dict:
    llm = llm or agent.anthropic_llm()
    rows = []
    for case in build_cases(db_path):
        res = agent.run(case.question, llm, as_of=case.as_of, db_path=db_path)
        rows.append(grade(case, res))
        print(f"{'PASS' if rows[-1]['passed'] else 'FAIL'}  {case.id}  (revisions {res.revisions}, "
              f"tools {len(res.tool_calls)}, missing {rows[-1]['missing']}, leaked {rows[-1]['leaked_future']})")
    summary = {"run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "model": agent.MODEL,
               "passed": sum(r["passed"] for r in rows), "total": len(rows),
               "input_tokens": sum(r["usage"].get("input_tokens", 0) for r in rows),
               "output_tokens": sum(r["usage"].get("output_tokens", 0) for r in rows), "cases": rows}
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(summary, indent=2, default=str))
    md = [f"# Agent evaluation: {summary['passed']}/{summary['total']} passed", "",
          f"Model `{summary['model']}` · {summary['run_at']} · {summary['input_tokens']:,} input / "
          f"{summary['output_tokens']:,} output tokens", "",
          "| Case | As of | Result | Missing | Leaked future | Revisions |", "|---|---|---|---|---|---|"]
    md += [f"| {r['id']} | {r['as_of'] or 'today'} | {'✅' if r['passed'] else '❌'} | {r['missing'] or ''} | "
           f"{r['leaked_future'] or ''} | {r['revisions']} |" for r in rows]
    md += ["", "## Answers", ""] + [f"### {r['id']}\n**Q:** {r['question']}\n\n{r['answer']}\n" for r in rows]
    (OUT_DIR / "results.md").write_text("\n".join(md))
    print(f"\n{summary['passed']}/{summary['total']} passed")
    return summary


if __name__ == "__main__":
    main()
