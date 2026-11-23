"""Open dataset: publish the cleaned tables as CSV + JSON under docs/data/ (served by GitHub Pages).

Anyone can download follow-the-power's point-in-time financials, grid metrics and weather-normalized
baseload without running the pipeline. docs/data/README.md documents every file and column.
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

from ftp import analytics, config, leadlag, power

OUT = config.ROOT / "docs" / "data"

DOC = """# Gigawatt Ledger open dataset

Refreshed daily by GitHub Actions. All files are CSV (UTF-8, comma-separated). Times are UTC unless noted.

| File | One row per | Columns |
|---|---|---|
| `fin_quarterly.csv` | company × metric × fiscal quarter × **filing** (point-in-time: restatements are extra rows) | ticker, metric (revenue/capex/gross_profit), fiscal_year, fiscal_q, calendar quarter, period_start, period_end, value_usd, filed_date (first public date of this value), form, accession, concept (XBRL tag), derived (1 = computed, e.g. Q4 = FY − 9M) |
| `fin_latest.csv` | company × metric × calendar quarter (latest version) | ticker, name, layer, metric, quarter, value_b (USD billions), yoy (%) |
| `zone_monthly.csv` | grid zone × month | zone, month (local), overnight_mw (median 1–5 AM), peak_mw (median daily max), flatness, baseload_yoy (%) |
| `zone_weather_normalized.csv` | grid zone × month | zone, month, actual_mw, normalized_mw (weather removed), weather_part_mw, normalized_yoy, actual_yoy, tmean_c, hdd, cdd |
| `zone_daily.csv` | grid zone × local day | zone, date, overnight_mw (median 1–5 AM), normalized_mw, deweathered_mw (weather & weekday removed), tmean_c |
| `state_sales.csv` | state × month | state, sector (COM = commercial), month, sales_gwh |
| `history.csv` | run date × metric | run_date, metric (headline KPIs: capex, NVIDIA revenue, weather-adjusted zone growth, row counts), value |
| `schema.json` | the data contract | expected columns and types for every file; validated on each export |
| `quality_checks.csv` | check × subject (latest run) | check_id, subject, severity, passed, value, detail |

Sources: SEC EDGAR XBRL (companyfacts API); U.S. EIA (EIA-930 hourly subregion demand; electric power retail sales);
Open-Meteo historical weather (ERA5 reanalysis). Not investment advice.
"""


# Data contract: the columns (and types) each published file promises. Validated on every export, so a
# pipeline change can't silently break downstream users of the open dataset.
CONTRACT = {
    "fin_quarterly.csv": {"ticker": "string", "metric": "string", "fiscal_year": "integer", "fiscal_q": "integer",
                          "quarter": "string", "period_start": "date", "period_end": "date", "value_usd": "number",
                          "filed_date": "date", "form": "string", "concept": "string", "derived": "integer"},
    "fin_latest.csv": {"ticker": "string", "name": "string", "layer": "string", "metric": "string", "quarter": "string",
                       "value_b": "number", "yoy": "number"},
    "zone_monthly.csv": {"zone": "string", "month": "string", "overnight_mw": "number", "peak_mw": "number",
                         "flatness": "number", "baseload_yoy": "number"},
    "zone_weather_normalized.csv": {"zone": "string", "month": "string", "actual_mw": "number", "normalized_mw": "number",
                                    "weather_part_mw": "number", "normalized_yoy": "number"},
    "zone_daily.csv": {"zone": "string", "date": "date", "overnight_mw": "number", "normalized_mw": "number",
                       "deweathered_mw": "number", "tmean_c": "number"},
    "state_sales.csv": {"state": "string", "sector": "string", "month": "string", "sales_gwh": "number"},
    "quality_checks.csv": {"check_id": "string", "subject": "string", "severity": "string", "passed": "integer"},
    "history.csv": {"run_date": "date", "metric": "string", "value": "number"},
}


def validate(name: str, df: pd.DataFrame) -> list[str]:
    """Contract violations for one file: missing columns, or values of the wrong type."""
    problems = []
    for col, typ in CONTRACT.get(name, {}).items():
        if col not in df:
            problems.append(f"{name}: missing column {col}")
            continue
        v = df[col].dropna()
        if typ in ("number", "integer") and len(v) and not pd.api.types.is_numeric_dtype(v):
            problems.append(f"{name}.{col}: expected {typ}")
        if typ == "date" and len(v) and pd.to_datetime(v, errors="coerce").isna().any():
            problems.append(f"{name}.{col}: unparseable dates")
    return problems


def append_history(kpis: dict, path=None) -> pd.DataFrame:
    """One row per (run date, metric): the headline numbers over time, for 'what changed since last run'."""
    path = path or OUT / "history.csv"
    today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    new = pd.DataFrame([{"run_date": today, "metric": k, "value": v} for k, v in kpis.items() if v is not None])
    old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["run_date", "metric", "value"])
    old = old[old["run_date"] != today]                       # re-runs on the same day replace that day's snapshot
    out = pd.concat([old, new], ignore_index=True).sort_values(["run_date", "metric"])
    out.to_csv(path, index=False)
    return out


def headline_kpis(conn) -> dict:
    fin = leadlag.fin_versions(conn)
    s = leadlag.series(fin)
    k = {}
    c = s.dropna(subset=["hyperscaler_capex"])
    if not c.empty:
        k["hyperscaler_capex_b"] = round(float(c["hyperscaler_capex"].iloc[-1]) / 1e9, 2)
        k["hyperscaler_capex_yoy"] = None if pd.isna(c["capex_yoy"].iloc[-1]) else round(float(c["capex_yoy"].iloc[-1]), 1)
    n = s.dropna(subset=["nvda_revenue"])
    if not n.empty:
        k["nvda_revenue_b"] = round(float(n["nvda_revenue"].iloc[-1]) / 1e9, 2)
    for _, zone, _, _ in config.GRID_ZONES:
        nm = analytics.weather_normalize(conn, zone)
        if nm:
            m = nm["monthly"].set_index("month")["normalized_mw"].dropna()
            if len(m) >= 24:
                k[f"{zone}_normalized_12m_growth"] = round(100 * (m.tail(12).mean() / m.iloc[-24:-12].mean() - 1), 2)
    k["sec_data_points"] = int(len(fin))
    k["grid_hours"] = int(conn.execute("SELECT COUNT(*) FROM zone_hourly").fetchone()[0])
    return k


def build(conn: sqlite3.Connection | None = None) -> dict:
    own = conn is None
    conn = conn or sqlite3.connect(config.DB_PATH)
    OUT.mkdir(parents=True, exist_ok=True)
    fin = leadlag.fin_versions(conn)
    files = {}
    if not fin.empty:
        fin.sort_values(["ticker", "metric", "fiscal_year", "fiscal_q", "filed_date"]).to_csv(OUT / "fin_quarterly.csv", index=False)
        cg = analytics.chain_growth(fin)
        cg.insert(1, "name", cg["ticker"].map(lambda t: config.COMPANIES.get(t, {}).get("name")))
        cg.insert(2, "layer", cg["ticker"].map(lambda t: config.COMPANIES.get(t, {}).get("layer")))
        cg.to_csv(OUT / "fin_latest.csv", index=False)
        files.update({"fin_quarterly.csv": len(fin), "fin_latest.csv": len(cg)})
    zm, zn, zd = [], [], []
    for _, zone, _, _ in config.GRID_ZONES:
        m = power.zone_monthly(conn, zone)
        if not m.empty:
            zm.append(m.assign(zone=zone))
        n = analytics.weather_normalize(conn, zone)
        if n:
            zn.append(n["monthly"].assign(zone=zone))
            zd.append(n["daily"].assign(zone=zone).round(1))
    if zm:
        df = pd.concat(zm)
        df[["zone"] + [c for c in df.columns if c != "zone"]].to_csv(OUT / "zone_monthly.csv", index=False)
        files["zone_monthly.csv"] = len(df)
    if zn:
        df = pd.concat(zn)
        df[["zone"] + [c for c in df.columns if c != "zone"]].to_csv(OUT / "zone_weather_normalized.csv", index=False)
        files["zone_weather_normalized.csv"] = len(df)
    if zd:
        df = pd.concat(zd)
        df[["zone"] + [c for c in df.columns if c != "zone"]].to_csv(OUT / "zone_daily.csv", index=False)
        files["zone_daily.csv"] = len(df)
    s = pd.read_sql_query("SELECT * FROM state_monthly_sales ORDER BY state, month", conn)
    s.to_csv(OUT / "state_sales.csv", index=False)
    files["state_sales.csv"] = len(s)
    from ftp import quality
    q = quality.latest(conn)
    q.to_csv(OUT / "quality_checks.csv", index=False)
    files["quality_checks.csv"] = len(q)
    hist = append_history(headline_kpis(conn))
    files["history.csv"] = len(hist)
    problems = []
    for name in files:
        if name in CONTRACT and (OUT / name).exists():
            problems += validate(name, pd.read_csv(OUT / name))
    (OUT / "schema.json").write_text(json.dumps({"files": CONTRACT, "violations": problems}, indent=1))
    if problems:
        raise RuntimeError(f"data contract violations: {problems}")
    (OUT / "README.md").write_text(DOC)
    (OUT / "manifest.json").write_text(json.dumps({"built_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
                                                   "files": files}, indent=1))
    if own:
        conn.close()
    print(f"exported {files}")
    return files


if __name__ == "__main__":
    build()
