"""SEC tagging diagnostics: which XBRL concepts each company actually uses, by fiscal year.

Companies change tags, and utilities/REITs often tag capex differently. This report (run in CI after
loading) shows, per company and metric, the coverage of every candidate concept plus OTHER concepts that
look like the metric, so missing quarters can be fixed by adding the right concept to config.
Writes docs/data/sec_diagnostics.json.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from ftp import config, sec

LOOKALIKE = {"revenue": re.compile(r"Revenue", re.I),
             "capex": re.compile(r"^Payments(To|For)(Acquire|Construction|Capital|Develop).*(Property|Plant|Asset|Construction|Capital|Utility|RealEstate)", re.I),
             "gross_profit": re.compile(r"GrossProfit", re.I)}


def concept_years(facts: dict, concept: str) -> dict:
    df = sec.facts_frame(facts, concept)
    if df.empty:
        return {}
    df = df[df["end_d"] >= "2019-01-01"]
    return {"facts": int(len(df)), "first_end": str(df["end_d"].min().date()) if len(df) else None,
            "last_end": str(df["end_d"].max().date()) if len(df) else None,
            "last_filed": str(df["filed_d"].max().date()) if len(df) else None}


def run() -> dict:
    out = {}
    for ticker in config.COMPANIES:
        f = config.RAW_DIR / "sec" / f"{ticker}_companyfacts.json"
        if not f.exists():
            continue
        facts = json.loads(f.read_text())
        names = list(facts.get("facts", {}).get("us-gaap", {}).keys())
        for metric in config.COMPANY_METRICS[ticker]:
            cand = {c: concept_years(facts, c) for c in config.METRIC_CONCEPTS[metric]}
            others = {}
            for n in names:
                if n not in cand and LOOKALIKE[metric].search(n):
                    cy = concept_years(facts, n)
                    if cy and cy["last_end"] and cy["last_end"] >= "2025-01-01":
                        others[n] = cy
            q = sec.quarterly(facts, ticker, metric)
            latest = (q.sort_values(["fiscal_year", "fiscal_q"]).iloc[-1][["fiscal_year", "fiscal_q", "period_end", "concept"]]
                      .astype(str).to_dict() if not q.empty else None)
            out[f"{ticker} {metric}"] = {"candidates": cand, "lookalikes": dict(sorted(others.items(),
                                         key=lambda kv: -kv[1]["facts"])[:12]), "latest_quarter": latest,
                                         "bad_rows": q[q["value_usd"] <= 0][["fiscal_year", "fiscal_q", "period_start", "period_end",
                                                                              "value_usd", "concept", "derived"]].astype(str)
                                                     .to_dict(orient="records") if not q.empty else []}
    path = config.ROOT / "docs" / "data" / "sec_diagnostics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    print(f"wrote {path}")
    return out


if __name__ == "__main__":
    run()
