"""Build docs/index.html: the public dashboard (GitHub Pages). Data is embedded as JSON; Plotly draws it.

Usage:  python -m ftp.dashboard
"""
from __future__ import annotations

import json
import math
import sqlite3

import pandas as pd

from ftp import config, leadlag, power

TEMPLATE = config.ROOT / "ftp" / "dashboard_template.html"
OUT = config.ROOT / "docs" / "index.html"
EVALS = config.ROOT / "evals" / "results.json"


def clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def cols(df: pd.DataFrame) -> dict:
    return {c: [clean(x) for x in df[c].tolist()] for c in df.columns}


def day_shape(conn, zone="DOM") -> dict:
    """Average load by local hour, normalized to each period's daily peak: first full year vs last 12 months."""
    tz = next(z[3] for z in config.GRID_ZONES if z[1] == zone)
    df = pd.read_sql_query("SELECT period_utc, demand_mwh FROM zone_hourly WHERE zone = ?", conn, params=(zone,))
    if df.empty:
        return {}
    local = pd.to_datetime(df["period_utc"], utc=True).dt.tz_convert(tz)
    df["hour"], df["ts"] = local.dt.hour, local
    end = df["ts"].max()
    periods = {"2019": df[df["ts"].dt.year == 2019],
               "last 12 months": df[df["ts"] > end - pd.Timedelta(days=365)]}
    out = {}
    for label, p in periods.items():
        prof = p.groupby("hour")["demand_mwh"].mean()
        out[label] = {"hour": list(range(24)), "mw": [round(float(x)) for x in prof.reindex(range(24))],
                      "pct_of_peak": [round(100 * float(x) / float(prof.max()), 1) for x in prof.reindex(range(24))]}
    return out


def build() -> None:
    conn = sqlite3.connect(config.DB_PATH)
    fin = leadlag.fin_versions(conn)
    s = leadlag.series(fin)
    s["nvda_revenue_b"] = (s["nvda_revenue"] / 1e9).round(2)
    s["capex_b"] = (s["hyperscaler_capex"] / 1e9).round(2)
    s = s[s["quarter"] >= "2019Q1"]
    gq = power.zone_quarterly(conn, "DOM")
    gm = power.zone_monthly(conn, "DOM")
    merged = s.set_index("quarter").join(gq.set_index("quarter"), how="outer")
    cc = {f"{a}->{b}": cols(leadlag.cross_correlation(merged[a], merged[b]))
          for a, b in [("nvda_yoy", "capex_yoy"), ("capex_yoy", "overnight_yoy"), ("nvda_yoy", "overnight_yoy")]}
    bt = leadlag.backtest(conn)
    sc = leadlag.score(bt)
    states = {}
    for st in config.RETAIL_STATES:
        q = power.state_sales_quarterly(conn, st)
        if not q.empty:
            base = q[q["quarter"].str.startswith("2019")]["sales_gwh"].mean()
            q["index"] = (100 * q["sales_gwh"] / base).round(1)
            states[st] = cols(q[["quarter", "index", "sales_yoy"]])
    per_co = pd.read_sql_query("SELECT * FROM fin_quarterly WHERE metric='capex'", conn)
    per_co["quarter"] = leadlag._calendar_quarter(per_co)
    latest_co = (per_co.sort_values("filed_date").drop_duplicates(["ticker", "fiscal_year", "fiscal_q"], keep="last"))
    co = {t: cols(g.sort_values("quarter")[["quarter"]].assign(capex_b=(g.sort_values("quarter")["value_usd"] / 1e9).round(2)))
          for t, g in latest_co.groupby("ticker")}
    runs = pd.read_sql_query("SELECT step, status, rows_loaded, finished_at FROM pipeline_runs ORDER BY run_id DESC LIMIT 6", conn)
    data = {
        "series": cols(s[["quarter", "nvda_revenue_b", "capex_b", "nvda_yoy", "capex_yoy"]]),
        "grid_q": cols(gq), "grid_m": cols(gm), "cc": cc, "day_shape": day_shape(conn),
        "backtest": cols(bt) if not bt.empty else {}, "scores": sc.to_dict(orient="records"),
        "states": states, "company_capex": co, "runs": runs.to_dict(orient="records"),
        "evals": json.loads(EVALS.read_text()) if EVALS.exists() else None,
        "built_at": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "hours": int(conn.execute("SELECT COUNT(*) FROM zone_hourly").fetchone()[0]),
    }
    conn.close()
    payload = json.dumps(data, default=str, allow_nan=False).replace("</", "<\\/")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(TEMPLATE.read_text().replace("__DATA__", payload))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
