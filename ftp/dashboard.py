"""Build docs/index.html: the public dashboard (GitHub Pages). Data is embedded as JSON; Plotly draws it.

Usage:  python -m ftp.dashboard
"""
from __future__ import annotations

import json
import math
import sqlite3

import numpy as np
import pandas as pd

from ftp import analytics, config, filings, leadlag, power, quality

TEMPLATE = config.ROOT / "ftp" / "dashboard_template.html"
OUT = config.ROOT / "docs" / "index.html"
EVALS = config.ROOT / "evals" / "results.json"
MEMO = config.ROOT / "reports" / "latest.json"
MANIFEST = config.ROOT / "docs" / "data" / "manifest.json"
HISTORY = config.ROOT / "docs" / "data" / "history.csv"


def clean(v):
    if isinstance(v, (np.floating, float)):
        v = float(v)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def cols(df: pd.DataFrame) -> dict:
    return {c: [clean(x) for x in df[c].tolist()] for c in df.columns}


def recs(df: pd.DataFrame) -> list[dict]:
    return [{k: clean(v) for k, v in r.items()} for r in df.to_dict(orient="records")]


def deep(o):
    if isinstance(o, dict):
        return {str(k): deep(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [deep(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return recs(o)
    return clean(o)


def zone_payload(conn, zone: str) -> dict | None:
    h = analytics.hourly_local(conn, zone)
    if h.empty:
        return None
    daily = analytics.daily_frame(conn, zone, h)
    norm = analytics.weather_normalize(conn, zone, daily)
    monthly = power.zone_monthly(conn, zone)
    if norm:
        steps = analytics.step_changes(norm["daily"])
        d = norm["daily"].set_index(pd.to_datetime(norm["daily"]["date"]))
        weekly = d[["overnight_mw", "deweathered_mw"]].resample("W").median().dropna()
    else:
        steps = analytics.step_changes(daily, col="overnight_mw")
        d = daily.set_index(pd.to_datetime(daily["date"]))
        weekly = d[["overnight_mw"]].resample("W").median().dropna()
    out = {
        "zone": zone, "label": analytics.label_of(zone), **config.ZONE_NOTES.get(zone, {"role": "hub", "note": ""}), "parent": next(z[0] for z in config.GRID_ZONES if z[1] == zone),
        "monthly": cols(monthly),
        "weekly": {"date": weekly.index.strftime("%Y-%m-%d").tolist(),
                   **{c: weekly[c].round(0).tolist() for c in weekly.columns}},
        "steps": recs(steps), "step_threshold": steps.attrs.get("threshold_mw"),
        "duration": analytics.load_duration(h), "heatmap": analytics.heatmap(h), "profiles": analytics.yearly_profiles(h),
        "temp_response": analytics.temperature_response(conn, zone, daily),
        "added": analytics.added_load(norm, daily), "index": analytics.normalized_index(norm, daily),
        "summary": analytics.zone_summary(conn, zone, h, daily, norm),
        "hours": int(len(h)), "first": h["ts"].min().strftime("%Y-%m-%d"), "last": h["ts"].max().strftime("%Y-%m-%d %H:00"),
    }
    if norm:
        nm = norm["monthly"]
        out["norm"] = cols(nm[["month", "actual_mw", "normalized_mw", "weather_part_mw", "normalized_yoy", "actual_yoy",
                               "tmean_c", "hdd", "cdd"]])
        out["norm_stats"] = {"r2": norm["r2"], "coef": norm["coef"], "days": norm["days"],
                             "weather_share_within_month": norm["weather_share_within_month"]}
        out["forecast"] = analytics.forecast(nm)
        out["regimes"] = analytics.growth_regimes(nm)
    return out


def findings(d: dict) -> list[dict]:
    """Plain-English headline findings, generated from the data (so they stay true as data updates)."""
    out = []
    s = d["series"]

    def last(xs, ys):
        for x, y in zip(reversed(xs), reversed(ys)):
            if y is not None:
                return x, y
        return None, None

    q, cap = last(s["quarter"], s["capex_b"])
    _, capy = last(s["quarter"], s["capex_yoy"])
    base = [v for x, v in zip(s["quarter"], s["capex_b"]) if x.startswith("2019") and v is not None]
    if cap and base:
        out.append({"figure": f"${cap:,.1f}B", "title": f"Hyperscaler capex in {q}",
                    "text": f"Microsoft, Alphabet, Amazon and Meta combined: {capy:+.0f}% on a year earlier and "
                            f"{cap / (sum(base) / len(base)):.1f} times their 2019 quarterly average."})
    dom = d["zones"].get(config.PRIMARY_ZONE)
    if dom and dom.get("added"):
        a = dom["added"]
        g = dom["summary"].get("normalized_12m_growth")
        out.append({"figure": f"+{a['added_mw']:,} MW", "title": "Always-on load added in Northern Virginia since 2019",
                    "text": f"Overnight demand is {a['added_pct']:+.0f}% higher, roughly {a['gpu_equivalent'] / 1e6:.1f} million AI GPUs "
                            f"running around the clock, or {a['homes_equivalent'] / 1e6:.1f} million homes."
                            + (f" Adjusted for weather, it grew {g:+.1f}% over the last 12 months." if g is not None else "")})
    accel = []
    for z in d["zones"].values():
        segs = (z.get("regimes") or {}).get("segments") or []
        # A real acceleration: recent (2022+), fast (>= 300 MW/yr), and not just a rebound from a shrinking
        # segment (Chicago's COVID dip and recovery would otherwise count).
        if (z.get("role") == "hub" and len(segs) >= 2 and segs[-1]["from"] >= "2022-01" and segs[-1]["mw_per_year"] >= 300
                and segs[-2]["mw_per_year"] >= 0 and segs[-1]["mw_per_year"] > 1.5 * max(segs[-2]["mw_per_year"], 50)):
            accel.append((segs[-1]["mw_per_year"] - segs[-2]["mw_per_year"], z, segs))
    for _, z, segs in sorted(accel, key=lambda x: -x[0])[:2]:
        when = pd.Period(segs[-1]["from"], freq="M").strftime("%B %Y")
        out.append({"figure": f"{segs[-1]['mw_per_year']:,} MW/yr", "title": f"{z['label'].split(' (')[0]} accelerated in {when}",
                    "text": f"Up from about {segs[-2]['mw_per_year']:,} MW a year before that. The break point is chosen by a "
                            "statistical test (BIC), not by hand, and lines up with a wave of new data-center campuses."})
    hubs = [z for z in d["zones"].values() if z.get("role") == "hub" and (z.get("added") or {}).get("added_mw", 0) > 0]
    tot = sum(z["added"]["added_mw"] for z in hubs)
    if tot:
        contrast = [z for z in d["zones"].values() if z.get("role") == "contrast" and z.get("added")]
        extra = (f" West Texas added {contrast[0]['added']['added_mw']:,} MW on its own, but that growth is oil, gas and "
                 "crypto mining, so it is tracked as a contrast and left out." if contrast else "")
        out.append({"figure": f"{tot:,} MW", "title": f"Added across {len(hubs)} data-center hubs",
                    "text": f"Weather-adjusted always-on load versus each hub's base year.{extra}"})
    ex = d.get("exposure") or []
    good = [e for e in ex if e["meaningful"]]
    if good:
        top = good[0]
        out.append({"figure": f"r = {top['corr']:.2f}", "title": f"{top['name']} tracks big tech's capex most closely",
                    "text": f"Correlation between its revenue growth and hyperscaler capex growth, at a {top['best_lag_q']}-quarter lag. "
                            f"{len(good)} of {len(ex)} companies show a link stronger than chance."})
    lt = d.get("layers") or {}
    if lt:
        best = max(lt.items(), key=lambda kv: (kv[1]["yoy"][-1] or -1e9))
        out.append({"figure": f"{best[1]['yoy'][-1]:+.0f}%", "title": f"{best[0].split('· ')[-1]} is the fastest-growing layer",
                    "text": f"Trailing-twelve-month revenue, now ${best[1]['ttm_b'][-1]:,.0f}B across the companies in that layer."})
    sb = d.get("scoreboard") or []
    if sb:
        beat = [r for r in sb if r["grid"] < r["naive"]]
        out.append({"figure": f"{len(beat)} of {len(sb)}", "title": "Companies where grid data beats a naive forecast",
                    "text": "Power data confirms the build-out after the money moves. It rarely predicts a company's revenue "
                            "better than assuming last quarter's growth continues."})
    ev = d.get("evals")
    if ev:
        out.append({"figure": f"{ev['passed']}/{ev['total']}", "title": "Evaluation cases passed by the AI analyst",
                    "text": "Including time travel, planted false claims, prompt injection and cited filing search, with every "
                            "number in every answer traced back to the data."})
    return out


def build() -> None:
    conn = sqlite3.connect(config.DB_PATH)
    fin = leadlag.fin_versions(conn)
    s = leadlag.series(fin)
    s["nvda_revenue_b"] = (s["nvda_revenue"] / 1e9).round(2)
    s["capex_b"] = (s["hyperscaler_capex"] / 1e9).round(2)
    s = s[s["quarter"] >= "2019Q1"]
    gq = power.zone_quarterly(conn, config.PRIMARY_ZONE)
    merged = s.set_index("quarter").join(gq.set_index("quarter"), how="outer")
    cc = {f"{a}->{b}": cols(leadlag.cross_correlation(merged[a], merged[b]))
          for a, b in [("nvda_yoy", "capex_yoy"), ("capex_yoy", "overnight_yoy"), ("nvda_yoy", "overnight_yoy")]}
    bt = leadlag.backtest(conn)
    zones = {}
    for _, z, _, _ in config.GRID_ZONES:
        p = zone_payload(conn, z)
        if p:
            zones[z] = p
    dom_norm = analytics.weather_normalize(conn, config.PRIMARY_ZONE)
    chain_df = analytics.chain_series(fin, gq, analytics.norm_quarterly(dom_norm))
    cg = analytics.chain_growth(fin)
    cg = cg[cg["quarter"] >= "2018Q1"]
    tickers = [t for t in config.COMPANIES if "revenue" in config.COMPANY_METRICS[t]]
    sb, detail = analytics.backtest_scoreboard(conn, tickers)
    states = {}
    for st in config.RETAIL_STATES:
        q = power.state_sales_quarterly(conn, st)
        if not q.empty:
            base = q[q["quarter"].str.startswith("2019")]["sales_gwh"].mean()
            q["index"] = (100 * q["sales_gwh"] / base).round(1)
            states[st] = cols(q[["quarter", "index", "sales_yoy", "sales_gwh"]])
    versions = fin[["ticker", "metric", "quarter", "value_usd", "filed_date"]].copy()
    versions["value_b"] = (versions["value_usd"] / 1e9).round(3)
    has_chunks = conn.execute("SELECT COUNT(*) FROM filing_chunks").fetchone()[0] if \
        conn.execute("SELECT name FROM sqlite_master WHERE name='filing_chunks'").fetchone() else 0
    from ftp.tools import Toolbox, load_skills
    tb = Toolbox(config.DB_PATH)
    data = {
        "meta": {"built_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
                 "hours": int(conn.execute("SELECT COUNT(*) FROM zone_hourly").fetchone()[0]),
                 "fin_rows": int(len(fin)), "chunks": int(has_chunks), "primary_zone": config.PRIMARY_ZONE,
                 "companies": {t: {"name": c["name"], "layer": c["layer"], "role": c["role"],
                                   "metrics": config.COMPANY_METRICS[t]} for t, c in config.COMPANIES.items()},
                 "gpu_kw": analytics.KW_PER_GPU_IT, "pue": analytics.PUE, "home_kw": analytics.KW_PER_HOME},
        "series": cols(s[["quarter", "nvda_revenue_b", "capex_b", "nvda_yoy", "capex_yoy"]]),
        "cc": cc, "backtest": cols(bt) if not bt.empty else {}, "scores": recs(leadlag.score(bt)),
        "zones": zones, "states": states,
        "chain": recs(cg), "versions": recs(versions[["ticker", "metric", "quarter", "value_b", "filed_date"]]),
        "exposure": recs(analytics.exposure(fin)), "intensity": analytics.capex_intensity(fin),
        "margins": analytics.gross_margins(fin), "race": analytics.revenue_race(fin), "layers": analytics.layer_totals(fin),
        "matrix": analytics.lead_lag_matrix(chain_df), "feed": analytics.filing_feed(fin),
        "restatements": recs(analytics.restatements(fin)),
        "flags": recs(analytics.variance_flags(fin).head(80)),
        "upcoming": analytics.upcoming_filings(fin),
        "lineage": analytics.lineage(fin),
        "history": recs(pd.read_csv(HISTORY)) if HISTORY.exists() else [],
        "memo": json.loads(MEMO.read_text()) if MEMO.exists() else None,
        "memo_archive": sorted([f.stem for f in (config.ROOT / "reports").glob("20*.md")], reverse=True)[:20]
        if (config.ROOT / "reports").exists() else [],
        "scoreboard": recs(sb), "bt_detail": {t: cols(v) for t, v in detail.items()},
        "grid_q": cols(gq),
        "quality": recs(quality.latest(conn)) if conn.execute("SELECT name FROM sqlite_master WHERE name='quality_checks'").fetchone() else [],
        "runs": recs(pd.read_sql_query("SELECT step, status, rows_loaded, finished_at, error_message FROM pipeline_runs "
                                       "ORDER BY run_id DESC LIMIT 12", conn)),
        "retrieval": filings.retrieval_eval(conn) if has_chunks else {},
        "evals": json.loads(EVALS.read_text()) if EVALS.exists() else None,
        "manifest": json.loads(MANIFEST.read_text()) if MANIFEST.exists() else None,
        "agent": {"tools": [{"name": t["name"], "description": t["description"]} for t in tb.specs()],
                  "skills": [{"name": k, "description": v["description"]} for k, v in load_skills().items()]},
    }
    data["findings"] = findings(data)
    conn.close()
    payload = json.dumps(deep(data), default=str, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(TEMPLATE.read_text().replace("__DATA__", payload))
    print(f"wrote {OUT} ({len(payload) / 1e6:.2f} MB of data)")


if __name__ == "__main__":
    build()
