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




def load_weather_zone(conn, zone="DOM", growth=0.05, step_date="2023-06-01", step_mw=600.0, seed=1):
    """Hourly load = trend (growth/yr) + a step at step_date + temperature response + daily shape + noise,
    plus matching daily weather. Returns the true parameters."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2019-01-01", "2025-12-31", freq="D")
    doy = days.dayofyear.to_numpy()
    tmean = 13 - 12 * np.cos(2 * np.pi * (doy - 15) / 365) + rng.normal(0, 4, len(days))
    hdd, cdd = np.clip(18 - tmean, 0, None), np.clip(tmean - 18, 0, None)
    years = (days - days[0]).days.to_numpy() / 365.25
    base = 8000 * (1 + growth) ** years + np.where(days >= pd.Timestamp(step_date), step_mw, 0)
    daily_level = base + 60 * hdd + 2 * hdd ** 2 + 90 * cdd + 4 * cdd ** 2
    hours = pd.date_range("2019-01-01", "2025-12-31 23:00", freq="h", tz="America/New_York")
    di = (hours.normalize().tz_localize(None) - days[0]).days.to_numpy()
    shape = 1 + 0.15 * np.sin(2 * np.pi * (hours.hour.to_numpy() - 9) / 24)
    load = daily_level[di] * shape + rng.normal(0, 40, len(hours))
    db.upsert(conn, "zone_hourly", pd.DataFrame({"zone": zone, "parent": "PJM",
              "period_utc": hours.tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S"), "demand_mwh": load}), ["zone", "period_utc"])
    db.upsert(conn, "zone_weather_daily", pd.DataFrame({"zone": zone, "date": days.strftime("%Y-%m-%d"), "tmean_c": tmean,
              "tmin_c": tmean - 5, "tmax_c": tmean + 5}), ["zone", "date"])
    return {"growth": growth, "step_date": step_date, "step_mw": step_mw}
