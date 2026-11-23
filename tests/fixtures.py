"""Synthetic database shared by tests: grid growth drives NVIDIA revenue growth (or not)."""
import numpy as np
import pandas as pd

from ftp import db


def load_synthetic(conn, grid_drives_revenue=True):
    """NVDA revenue growth for quarter T is driven by grid baseload growth in T (or is noise)."""
    rng = np.random.default_rng(0)
    quarters = pd.period_range("2019Q1", "2025Q4", freq="Q")
    base = 10_000 * np.cumprod(1 + rng.normal(0.02, 0.03, len(quarters)))          # overnight MW per quarter
    grid_yoy = pd.Series(base, index=quarters).pct_change(4) * 100
    rev, fin = [5e9] * 4, []
    for i, q in enumerate(quarters):
        if i >= 4:
            g = (3 * grid_yoy.iloc[i] if grid_drives_revenue else rng.normal(20, 15)) + rng.normal(0, 2)
            rev.append(rev[i - 4] * (1 + g / 100))
        fin.append({"ticker": "NVDA", "metric": "revenue", "fiscal_year": q.year, "fiscal_q": q.quarter,
                    "period_start": q.start_time.strftime("%Y-%m-%d"), "period_end": q.end_time.strftime("%Y-%m-%d"),
                    "value_usd": rev[i], "filed_date": (q.end_time + pd.Timedelta(days=30)).strftime("%Y-%m-%d"),
                    "form": "10-Q", "accession": "x", "concept": "Revenues", "derived": 0})
        for t in ("MSFT", "GOOGL", "AMZN", "META"):
            fin.append({**fin[-1], "ticker": t, "metric": "capex", "value_usd": 1e9 * (1 + i / 10),
                        "filed_date": (q.end_time + pd.Timedelta(days=28)).strftime("%Y-%m-%d"), "concept": "Capex"})
    db.upsert(conn, "fin_quarterly", pd.DataFrame(fin), ["ticker", "metric", "fiscal_year", "fiscal_q", "filed_date"])
    hours = pd.date_range("2019-01-01", "2025-12-31 23:00", freq="h", tz="UTC")
    level = pd.Series(base, index=quarters).reindex(hours.tz_convert("America/New_York").to_period("Q")).to_numpy()
    db.upsert(conn, "zone_hourly", pd.DataFrame({"zone": "DOM", "parent": "PJM",
              "period_utc": hours.strftime("%Y-%m-%d %H:%M:%S"), "demand_mwh": level}), ["zone", "period_utc"])


