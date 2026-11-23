"""Automated research memo: the agent writes a verified "state of the AI build-out" note.

The same agent, tools and verifier as the Q&A, pointed at a longer brief. Every number in the memo is
checked against tool output (unverifiable figures force a rewrite) and filing quotes are checked
word-for-word. Output: reports/latest.json (with receipts, shown on the dashboard) and
reports/<date>.md (an archive of every memo).

Usage:  python -m ftp.memo        (needs ANTHROPIC_API_KEY; one memo costs a few cents)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ftp import agent, config

OUT = config.ROOT / "reports"
BRIEF = """Write this week's research memo on the AI infrastructure build-out for a finance leadership team.
Structure it with these five short sections, each 2-4 sentences, with markdown headings (###):
### Spending: the latest hyperscaler capex and its growth (use quarterly_financials), plus capex intensity
for at least two cloud companies (company_financials + calculate).
### Suppliers: who in the supply chain is growing fastest right now (supply_chain_snapshot), naming at
least three companies from different layers with their YoY growth.
### Power: what the grid shows. Use zone_overview and weather_adjusted for the primary zone; mention the
weather-adjusted growth and any recent step change (step_changes).
### In their own words: one or two cited points from recent filings (search_filings), e.g. what drove
NVIDIA's Data Center revenue, or what a utility says about data-center demand.
### What to watch: two risks or signals for next quarter, grounded in the data (no exact forecasts).
Load the supply-chain and weather-adjustment skills first. Every number must come from a tool or `calculate`."""


def main(llm=None, db_path=None) -> dict:
    llm = llm or agent.anthropic_llm(max_tokens=2500)
    res = agent.run(BRIEF, llm, db_path=db_path, words=450, max_turns=20)
    now = datetime.now(timezone.utc)
    out = {"written_at": now.strftime("%Y-%m-%d %H:%M UTC"), "model": getattr(llm, "model", agent.MODEL),
           "answer": res.answer, "numbers": res.numbers, "trace": res.tool_calls, "verification": res.verification,
           "revisions": res.revisions, "usage": res.usage, "seconds": res.seconds, "error": res.error}
    OUT.mkdir(exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(out, indent=2, default=str))
    ok = res.verification.get("ok")
    (OUT / f"{now:%Y-%m-%d}.md").write_text(
        f"# AI build-out memo · {now:%Y-%m-%d}\n\n*Written by `{out['model']}` with follow-the-power's tools. "
        f"{len(res.numbers)} numbers, {'all verified' if ok else 'NOT all verified'} against tool output; "
        f"{res.revisions} forced revision(s).*\n\n{res.answer}\n\n---\nTools used: "
        + ", ".join(t["name"] for t in res.tool_calls) + "\n")
    print(f"memo: {len(res.answer.split())} words, {len(res.numbers)} numbers, verified={ok}, revisions={res.revisions}")
    return out


if __name__ == "__main__":
    main()
