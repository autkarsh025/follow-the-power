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

DOC = """# follow-the-power open dataset

Refreshed daily by GitHub Actions. All files are CSV (UTF-8, comma-separated). Times are UTC unless noted.

| File | One row per | Columns |
|---|---|---|
| `fin_quarterly.csv` | company × metric × fiscal quarter × **filing** (point-in-time: restatements are extra rows) | ticker, metric (revenue/capex/gross_profit), fiscal_year, fiscal_q, calendar quarter, period_start, period_end, value_usd, filed_date (first public date of this value), form, accession, concept (XBRL tag), derived (1 = computed, e.g. Q4 = FY − 9M) |
| `fin_latest.csv` | company × metric × calendar quarter (latest version) | ticker, name, layer, metric, quarter, value_b (USD billions), yoy (%) |
| `zone_monthly.csv` | grid zone × month | zone, month (local), overnight_mw (median 1–5 AM), peak_mw (median daily max), flatness, baseload_yoy (%) |
| `zone_weather_normalized.csv` | grid zone × month | zone, month, actual_mw, normalized_mw (weather removed), weather_part_mw, normalized_yoy, actual_yoy, tmean_c, hdd, cdd |
| `zone_daily.csv` | grid zone × local day | zone, date, overnight_mw (median 1–5 AM), normalized_mw, deweathered_mw (weather & weekday removed), tmean_c |
| `state_sales.csv` | state × month | state, sector (COM = commercial), month, sales_gwh |
| `quality_checks.csv` | check × subject (latest run) | check_id, subject, severity, passed, value, detail |

Sources: SEC EDGAR XBRL (companyfacts API); U.S. EIA (EIA-930 hourly subregion demand; electric power retail sales);
Open-Meteo historical weather (ERA5 reanalysis). Not investment advice.
"""


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
    (OUT / "README.md").write_text(DOC)
    (OUT / "manifest.json").write_text(json.dumps({"built_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
                                                   "files": files}, indent=1))
    if own:
        conn.close()
    print(f"exported {files}")
    return files


if __name__ == "__main__":
    build()
