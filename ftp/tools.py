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

from ftp import analytics, config, filings, leadlag, power, sec

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
        self.provenance: list[tuple[float, str]] = []    # (number, tool that returned it): the "receipts"
        self.skills = load_skills()
        self._tool = None
        self._zone_cache: dict[str, dict] = {}
        self.passages: list[str] = []                    # filing text retrieved this run (quotes are checked against it)
        # Grid data counts as public GRID_PUBLICATION_LAG_DAYS after the hour it describes.
        self.grid_until = ((pd.Timestamp(as_of) - pd.Timedelta(days=power.GRID_PUBLICATION_LAG_DAYS)).strftime("%Y-%m-%d")
                           if as_of else None)

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
            self.provenance.append((float(obj), self._tool or "?"))
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
            {"name": "company_financials",
             "description": "Quarterly revenue or capex for any tracked company, in billions USD with YoY growth %. "
                            "Tickers: MSFT, GOOGL, AMZN, META, ORCL (cloud buyers); NVDA, AMD, AVGO (chips); VRT (Vertiv, "
                            "data-center power & cooling equipment); D (Dominion Energy, the Northern Virginia utility; revenue "
                            "and capex); CEG (Constellation), VST (Vistra) (power generators). Metric: 'revenue' or 'capex'.",
             "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "metric": {"type": "string"},
                                                               "last_n": {"type": "integer"}}, "required": ["ticker", "metric"]}},
            {"name": "supply_chain_snapshot",
             "description": "Latest quarter's value and YoY growth for every company in the AI supply chain, grouped by "
                            "layer (cloud buyers -> chips -> power gear -> electricity).",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "zone_overview",
             "description": "Compare the grid zones (" + ", ".join(f"{z[1]}: {z[2]}" for z in config.GRID_ZONES) + "): "
                            "latest-quarter overnight baseload YoY %, weather-adjusted 12-month baseload growth %, MW of "
                            "always-on load added since 2019 and its rough GPU equivalent.",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "weather_adjusted",
             "description": "Monthly overnight baseload for one zone, actual vs WEATHER-NORMALIZED (temperature effects "
                            "removed with a degree-day regression), with YoY growth of each, the weather part in MW, and "
                            "model fit (R^2). Use it to separate data-center growth from hot summers / cold winters.",
             "input_schema": {"type": "object", "properties": {"zone": {"type": "string"}, "last_n": {"type": "integer"}},
                              "required": ["zone"]}},
            {"name": "step_changes",
             "description": "Sudden, sustained jumps in a zone's weather-adjusted baseload (e.g. a large new load being "
                            "energized): date, size in MW, before/after level, % change, and the detection threshold.",
             "input_schema": {"type": "object", "properties": {"zone": {"type": "string"}}, "required": ["zone"]}},
            {"name": "restatements",
             "description": "Quarters whose reported value was later revised in an SEC filing: original vs revised "
                            "value (billions USD), filing dates, % change. Optional ticker filter.",
             "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}}},
            {"name": "search_filings",
             "description": "Search management's discussion (MD&A) in recent 10-Q/10-K filings. Returns the most relevant "
                            "passages with a citation like [NVDA 10-Q filed 2026-08-27, MD&A ¶14]. Use for WHY questions "
                            "(drivers, segments such as NVIDIA's Data Center revenue, guidance language, risks). Cite every "
                            "passage you use; quote only exact text. Companies: " + ", ".join(filings.FILING_TICKERS) + ".",
             "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "ticker": {"type": "string"},
                                                               "k": {"type": "integer"}}, "required": ["query"]}},
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
                      "description": "Read-only SQL (SELECT) on tables fin_quarterly, zone_hourly, state_monthly_sales, zone_weather_daily, quality_checks. "
                                     "Max 50 rows returned.",
                      "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}})
        return s

    def run(self, name: str, args: dict) -> dict:
        try:
            fn = getattr(self, f"t_{name}")
        except AttributeError:
            return {"error": f"unknown tool {name}"}
        self._tool = name
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

    def t_company_financials(self, ticker: str, metric: str, last_n: int = 8) -> dict:
        ticker, metric = ticker.upper(), metric.lower()
        cg = analytics.chain_growth(self._fin(), as_of=self.as_of)
        g = cg[(cg["ticker"] == ticker) & (cg["metric"] == metric)].tail(int(last_n))
        if g.empty:
            return {"error": f"no {metric} data for {ticker}"}
        rows = [{"calendar_quarter": r.quarter, "value_usd_b": _r(r.value_b, 2), "yoy_pct": _r(r.yoy, 1)} for r in g.itertuples()]
        return self._record({"ticker": ticker, "name": config.COMPANIES.get(ticker, {}).get("name"), "metric": metric,
                             "rows": rows})

    def t_supply_chain_snapshot(self) -> dict:
        cg = analytics.chain_growth(self._fin(), as_of=self.as_of)
        out = []
        for (t, m), g in cg.groupby(["ticker", "metric"]):
            r = g.iloc[-1]
            out.append({"layer": config.COMPANIES[t]["layer"], "ticker": t, "name": config.COMPANIES[t]["name"],
                        "metric": m, "calendar_quarter": r.quarter, "value_usd_b": _r(r.value_b, 2), "yoy_pct": _r(r.yoy, 1)})
        return self._record({"companies": sorted(out, key=lambda x: (x["layer"], x["ticker"]))})

    def _zone(self, zone: str) -> dict:
        zone = zone.upper()
        if zone not in [z[1] for z in config.GRID_ZONES]:
            raise ValueError(f"zone must be one of {[z[1] for z in config.GRID_ZONES]}")
        if zone not in self._zone_cache:
            with self._conn() as c:
                h = analytics.hourly_local(c, zone, self.grid_until)
                daily = analytics.daily_frame(c, zone, h)
                norm = analytics.weather_normalize(c, zone, daily, until=self.grid_until)
                q = power.zone_quarterly(c, zone)
                if self.as_of:
                    q = q[q["known_date"] <= self.as_of]
            self._zone_cache[zone] = {"h": h, "daily": daily, "norm": norm, "q": q}
        return self._zone_cache[zone]

    def t_zone_overview(self) -> dict:
        rows = []
        for _, zone, label, _ in config.GRID_ZONES:
            z = self._zone(zone)
            if z["daily"].empty:
                continue
            add = analytics.added_load(z["norm"], z["daily"])
            nm = z["norm"]["monthly"].set_index("month")["normalized_mw"].dropna() if z["norm"] else pd.Series(dtype=float)
            rows.append({"zone": zone, "label": label,
                         "latest_quarter": z["q"]["quarter"].iloc[-1] if not z["q"].empty else None,
                         "overnight_yoy_pct": _r(z["q"]["overnight_yoy"].iloc[-1], 1) if not z["q"].empty else None,
                         "weather_adj_12m_growth_pct": _r(100 * (nm.tail(12).mean() / nm.iloc[-24:-12].mean() - 1), 1)
                         if len(nm) >= 24 else None,
                         "added_mw_since_2019": add.get("added_mw"), "added_pct": add.get("added_pct"),
                         "gpu_equivalent": add.get("gpu_equivalent")})
        return self._record({"zones": rows, "gpu_assumption": f"{analytics.KW_PER_GPU_IT} kW per GPU x PUE {analytics.PUE}"})

    def t_weather_adjusted(self, zone: str, last_n: int = 12) -> dict:
        z = self._zone(zone)
        if not z["norm"]:
            return {"error": f"no weather data for {zone}"}
        m = z["norm"]["monthly"].tail(int(last_n))
        rows = [{"month": r.month, "actual_mw": _r(r.actual_mw, 0), "weather_adjusted_mw": _r(r.normalized_mw, 0),
                 "weather_part_mw": _r(r.weather_part_mw, 0), "actual_yoy_pct": _r(r.actual_yoy, 1),
                 "weather_adjusted_yoy_pct": _r(r.normalized_yoy, 1), "avg_temp_c": _r(r.tmean_c, 1)} for r in m.itertuples()]
        nm = z["norm"]["monthly"].set_index("month")["normalized_mw"].dropna()
        out = {"zone": zone.upper(), "model_r2": z["norm"]["r2"], "rows": rows}
        if len(nm) >= 24:
            out["last_12m_vs_prior_12m_growth_pct"] = _r(100 * (nm.tail(12).mean() / nm.iloc[-24:-12].mean() - 1), 1)
        return self._record(out)

    def t_step_changes(self, zone: str) -> dict:
        z = self._zone(zone)
        if z["norm"]:
            st = analytics.step_changes(z["norm"]["daily"])
        else:
            st = analytics.step_changes(z["daily"], col="overnight_mw")
        return self._record({"zone": zone.upper(), "threshold_mw": st.attrs.get("threshold_mw"),
                             "steps": st.to_dict(orient="records")})

    def t_restatements(self, ticker: str | None = None) -> dict:
        fin = self._fin()
        if self.as_of:
            fin = fin[fin["filed_date"] <= self.as_of]
        r = analytics.restatements(fin)
        if ticker and not r.empty:
            r = r[r["ticker"] == ticker.upper()]
        return self._record({"restatements": [] if r.empty else r.head(15).to_dict(orient="records")})

    def t_search_filings(self, query: str, ticker: str | None = None, k: int = 5) -> dict:
        with self._conn() as c:
            hits = filings.search(c, query, ticker, self.as_of, min(int(k), 8))
        self.passages += [h["text"] for h in hits]
        if not hits:
            return {"passages": [], "note": "no matching passages (filing text may not be indexed for this company/date)"}
        from ftp import verify
        self._tool = "search_filings"
        for h in hits:
            for _, val, unit, _ in verify.extract(h["text"]):      # numbers stated in filing text are evidence too
                self._record(val)
                if unit in ("million", "m"):
                    self._record(val / 1000)                        # "$41,096 million" -> 41.096 (billions)
            h["text"] = h["text"][:1400]
        return self._record({"passages": [{k_: v for k_, v in h.items() if k_ not in ("url", "score")} for h in hits]})

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
