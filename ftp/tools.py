"""The agent's tools: point-in-time data access over the follow-the-power database.

Every tool respects `as_of`: with as_of="2024-03-01" the agent only sees financial numbers
filed by that date and grid data published by then. That's what makes the time-travel
evaluation honest: the agent literally cannot read the future.

Every number a tool returns is also recorded as EVIDENCE. The verifier later checks that
each number in the agent's answer matches some piece of evidence (or a calculation the
agent made with the `calculate` tool, whose results are evidence too).
"""
from __future__ import annotations

import ast
import json
import math
import operator
import sqlite3
from pathlib import Path

import pandas as pd

from ftp import config, leadlag, power, sec

SKILLS_DIR = config.ROOT / "skills"
SERIES_NAMES = ("nvda_yoy", "capex_yoy", "overnight_yoy")


def load_skills() -> dict[str, dict]:
    """Read skills/<name>/SKILL.md files: YAML-ish front matter (name, description) + instructions."""
    skills = {}
    for f in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = f.read_text()
        meta, body = {}, text
        if text.startswith("---"):
            _, fm, body = text.split("---", 2)
            for line in fm.strip().splitlines():
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        name = meta.get("name", f.parent.name)
        skills[name] = {"name": name, "description": meta.get("description", ""), "body": body.strip()}
    return skills


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def safe_eval(expr: str) -> float:
    """Arithmetic only (+ - * / ** and parentheses). No names, calls or attributes."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("only arithmetic is allowed")
    if len(expr) > 200:
        raise ValueError("expression too long")
    return ev(ast.parse(expr, mode="eval"))


def _r(v, nd=2):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), nd)


class Toolbox:
    def __init__(self, db_path: Path | None = None, as_of: str | None = None):
        self.db_path = Path(db_path or config.DB_PATH)
        self.as_of = as_of
        self.evidence: list[float] = []
        self.skills = load_skills()

    # ------------------------------------------------------------------ plumbing
    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)    # read-only: tools can't modify data

    def _record(self, obj):
        """Collect every number in a tool result as evidence."""
        if isinstance(obj, dict):
            for v in obj.values():
                self._record(v)
        elif isinstance(obj, list):
            for v in obj:
                self._record(v)
        elif isinstance(obj, (int, float)) and not isinstance(obj, bool) and not math.isnan(obj):
            self.evidence.append(float(obj))
        return obj

    def specs(self) -> list[dict]:
        """Tool definitions in the Claude API format. Raw SQL is only offered without as_of,
        because point-in-time rules can't be enforced on arbitrary queries."""
        s = [
            {"name": "quarterly_financials",
             "description": "Calendar-quarter NVIDIA revenue and total hyperscaler capex (Microsoft, Alphabet, Amazon, Meta) "
                            "in billions of USD, with year-over-year growth in %. Only numbers public on the as-of date.",
             "input_schema": {"type": "object", "properties": {"last_n": {"type": "integer", "description": "most recent N quarters (default 8)"}}}},
            {"name": "company_capex",
             "description": "Quarterly capital expenditures for one hyperscaler (MSFT, GOOGL, AMZN, META), billions USD.",
             "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "last_n": {"type": "integer"}}, "required": ["ticker"]}},
            {"name": "power_index",
             "description": "Northern Virginia (PJM Dominion zone) grid metrics by calendar quarter: overnight baseload (MW, "
                            "median 1-5 AM load), flatness (overnight / peak; data centers push it up), YoY baseload growth %.",
             "input_schema": {"type": "object", "properties": {"last_n": {"type": "integer"}}}},
            {"name": "state_sales",
             "description": "Quarterly commercial-sector electricity sales (GWh) and YoY growth % for a state "
                            "(VA, TX, OH, AZ, OR, GA). Data centers are counted as commercial customers.",
             "input_schema": {"type": "object", "properties": {"state": {"type": "string"}, "last_n": {"type": "integer"}}, "required": ["state"]}},
            {"name": "lead_lag",
             "description": "Cross-correlation between two YoY growth series at lags -4..+4 quarters. Positive best lag = "
                            "series_a LEADS series_b. Includes the noise band (~2/sqrt(n)): smaller correlations are not meaningful. "
                            f"Series: {', '.join(SERIES_NAMES)}.",
             "input_schema": {"type": "object", "properties": {"series_a": {"type": "string"}, "series_b": {"type": "string"}},
                              "required": ["series_a", "series_b"]}},
            {"name": "nowcast_scores",
             "description": "Backtest of forecasting NVIDIA's YoY revenue growth: naive (last quarter's growth), capex model, "
                            "grid model. Mean absolute error in percentage points; lower is better. Also per-quarter predictions.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "calculate",
             "description": "Arithmetic on numbers you already have (e.g. '105.9 - 85.2'). Use it for ANY number you derive; "
                            "the answer may only contain numbers from tool results or calculations.",
             "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}},
            {"name": "load_skill",
             "description": "Load the full instructions for a skill by name. Available skills: "
                            + "; ".join(f"{k}: {v['description']}" for k, v in self.skills.items()),
             "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
        ]
        if self.as_of is None:
            s.append({"name": "sql",
                      "description": "Read-only SQL (SELECT) on tables fin_quarterly, zone_hourly, state_monthly_sales. "
                                     "Max 50 rows returned.",
                      "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}})
        return s

    def run(self, name: str, args: dict) -> dict:
        try:
            fn = getattr(self, f"t_{name}")
        except AttributeError:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**(args or {}))
        except Exception as exc:  # tool errors go back to the model, they don't crash the run
            return {"error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------ tools
    def _fin(self) -> pd.DataFrame:
        with self._conn() as c:
            return leadlag.fin_versions(c)

    def t_quarterly_financials(self, last_n: int = 8) -> dict:
        s = leadlag.series(self._fin(), as_of=self.as_of).dropna(subset=["nvda_revenue", "hyperscaler_capex"], how="all")
        rows = [{"quarter": r.quarter, "nvda_revenue_usd_b": _r(r.nvda_revenue / 1e9),
                 "hyperscaler_capex_usd_b": _r(r.hyperscaler_capex / 1e9),
                 "nvda_yoy_pct": _r(r.nvda_yoy, 1), "capex_yoy_pct": _r(r.capex_yoy, 1)} for r in s.tail(int(last_n)).itertuples()]
        return self._record({"as_of": self.as_of or "latest", "rows": rows})

    def t_company_capex(self, ticker: str, last_n: int = 8) -> dict:
        ticker = ticker.upper()
        fin = self._fin()
        view = sec.as_of(fin, self.as_of or "2100-01-01")
        v = view[(view["ticker"] == ticker) & (view["metric"] == "capex")].sort_values("period_end").tail(int(last_n))
        rows = [{"quarter": r.quarter, "capex_usd_b": _r(r.value_usd / 1e9), "filed": r.filed_date} for r in v.itertuples()]
        return self._record({"ticker": ticker, "rows": rows})

    def _grid(self) -> pd.DataFrame:
        with self._conn() as c:
            q = power.zone_quarterly(c, "DOM")
        return q[q["known_date"] <= self.as_of] if self.as_of else q

    def t_power_index(self, last_n: int = 8) -> dict:
        q = self._grid().tail(int(last_n))
        rows = [{"quarter": r.quarter, "overnight_mw": _r(r.overnight_mw, 0), "flatness": _r(r.flatness, 3),
                 "overnight_yoy_pct": _r(r.overnight_yoy, 1)} for r in q.itertuples()]
        return self._record({"zone": "PJM Dominion (Northern Virginia)", "rows": rows})

    def t_state_sales(self, state: str, last_n: int = 8) -> dict:
        with self._conn() as c:
            q = power.state_sales_quarterly(c, state.upper())
        if self.as_of:
            q = q[q["known_date"] <= self.as_of]
        rows = [{"quarter": r.quarter, "sales_gwh": _r(r.sales_gwh, 0), "sales_yoy_pct": _r(r.sales_yoy, 1)}
                for r in q.tail(int(last_n)).itertuples()]
        return self._record({"state": state.upper(), "sector": "commercial", "rows": rows})

    def t_lead_lag(self, series_a: str, series_b: str) -> dict:
        if series_a not in SERIES_NAMES or series_b not in SERIES_NAMES:
            return {"error": f"series must be one of {SERIES_NAMES}"}
        s = leadlag.series(self._fin(), as_of=self.as_of).set_index("quarter")
        merged = s.join(self._grid().set_index("quarter"), how="outer")
        cc = leadlag.cross_correlation(merged[series_a], merged[series_b])
        best = cc.dropna(subset=["corr"]).loc[lambda d: d["corr"].abs().idxmax()] if cc["corr"].notna().any() else None
        out = {"series_a": series_a, "series_b": series_b, "table": cc.to_dict(orient="records")}
        if best is not None:
            out["strongest"] = {"lag_quarters": int(best["lag_quarters"]), "corr": float(best["corr"]),
                                "noise_band": float(best["noise_band"]),
                                "meaningful": bool(abs(best["corr"]) > best["noise_band"])}
        return self._record(out)

    def t_nowcast_scores(self) -> dict:
        with self._conn() as c:
            bt = leadlag.backtest(c)
        if self.as_of and not bt.empty:
            bt = bt[bt["decision_date"] <= self.as_of]
        scores = leadlag.score(bt)
        return self._record({"scores": scores.to_dict(orient="records"),
                             "recent_quarters": bt.tail(6).to_dict(orient="records")})

    def t_calculate(self, expression: str) -> dict:
        return self._record({"expression": expression, "result": round(safe_eval(expression), 4)})

    def t_load_skill(self, name: str) -> dict:
        sk = self.skills.get(name)
        return {"name": name, "instructions": sk["body"]} if sk else {"error": f"no skill named {name}"}

    def t_sql(self, query: str) -> dict:
        q = query.strip().rstrip(";")
        if not q.lower().startswith("select") or ";" in q:
            return {"error": "only a single SELECT statement is allowed"}
        with self._conn() as c:
            df = pd.read_sql_query(q, c)
        return self._record({"columns": list(df.columns), "rows": json.loads(df.head(50).to_json(orient="records"))})
