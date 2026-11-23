"""The power index: spotting data centers in grid data.

Data centers draw nearly flat power 24/7; homes and offices peak in the day and drop at night.
So data-center growth shows up as a rising OVERNIGHT BASELOAD: the lowest hours of the night
climb faster than the daily peak.

Metrics per zone, by month (local time):
  overnight_mw  median load 1-5 AM (the baseload)
  peak_mw       median of daily maximum load
  flatness      overnight_mw / peak_mw  (closer to 1 = flatter, more data-center-like)
  baseload_yoy  % change in overnight_mw vs the same month a year earlier (cancels seasons)

Point-in-time: EIA hourly data is published within days, so a quarter's grid data is treated as
known 7 days after the quarter ends, weeks before companies report earnings for that quarter.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from ftp import config

GRID_PUBLICATION_LAG_DAYS = 7
SALES_PUBLICATION_LAG_DAYS = 60   # EIA monthly retail sales arrive ~2 months later
OVERNIGHT_HOURS = (1, 2, 3, 4, 5)


def zone_monthly(conn: sqlite3.Connection, zone: str) -> pd.DataFrame:
    tz = next(z[3] for z in config.GRID_ZONES if z[1] == zone)
    df = pd.read_sql_query("SELECT period_utc, demand_mwh FROM zone_hourly WHERE zone = ? AND demand_mwh IS NOT NULL",
                           conn, params=(zone,))
    if df.empty:
        return pd.DataFrame(columns=["month", "overnight_mw", "peak_mw", "flatness", "baseload_yoy", "hours"])
    local = pd.to_datetime(df["period_utc"], utc=True).dt.tz_convert(tz)
    df["month"] = local.dt.strftime("%Y-%m")
    df["day"] = local.dt.date
    df["hour"] = local.dt.hour
    night = df[df["hour"].isin(OVERNIGHT_HOURS)].groupby("month")["demand_mwh"].median()
    peak = df.groupby(["month", "day"])["demand_mwh"].max().groupby("month").median()
    hours = df.groupby("month").size()
    m = pd.DataFrame({"overnight_mw": night, "peak_mw": peak, "hours": hours}).dropna()
    m = m[m["hours"] >= 24 * 25]                        # complete months only
    m["flatness"] = (m["overnight_mw"] / m["peak_mw"]).round(4)
    m.index = pd.PeriodIndex(m.index, freq="M")
    m = m.asfreq("M")
    m["baseload_yoy"] = (100 * (m["overnight_mw"] / m["overnight_mw"].shift(12) - 1)).round(2)
    m = m.dropna(subset=["overnight_mw"])
    m.index = m.index.astype(str)
    return m.rename_axis("month").reset_index()


def zone_quarterly(conn: sqlite3.Connection, zone: str) -> pd.DataFrame:
    """Calendar-quarter averages of the monthly metrics, with the date the quarter became known."""
    m = zone_monthly(conn, zone)
    if m.empty:
        return pd.DataFrame(columns=["quarter", "overnight_mw", "flatness", "baseload_yoy", "known_date"])
    m["quarter"] = pd.PeriodIndex(m["month"], freq="M").asfreq("Q").astype(str)
    q = m.groupby("quarter").agg(overnight_mw=("overnight_mw", "mean"), flatness=("flatness", "mean"),
                                 months=("month", "size")).reset_index()
    q = q[q["months"] == 3].drop(columns="months")
    q["overnight_yoy"] = (100 * (q["overnight_mw"] / q["overnight_mw"].shift(4) - 1)).round(2)
    q["known_date"] = (pd.PeriodIndex(q["quarter"], freq="Q").end_time.normalize()
                       + pd.Timedelta(days=GRID_PUBLICATION_LAG_DAYS)).strftime("%Y-%m-%d")
    return q


def state_sales_quarterly(conn: sqlite3.Connection, state: str, sector: str = config.RETAIL_SECTOR) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT month, sales_gwh FROM state_monthly_sales WHERE state = ? AND sector = ?",
                           conn, params=(state, sector))
    if df.empty:
        return pd.DataFrame(columns=["quarter", "sales_gwh", "sales_yoy", "known_date"])
    df["quarter"] = pd.PeriodIndex(df["month"], freq="M").asfreq("Q").astype(str)
    q = df.groupby("quarter").agg(sales_gwh=("sales_gwh", "sum"), months=("month", "size")).reset_index()
    q = q[q["months"] == 3].drop(columns="months")
    q["sales_yoy"] = (100 * (q["sales_gwh"] / q["sales_gwh"].shift(4) - 1)).round(2)
    q["known_date"] = (pd.PeriodIndex(q["quarter"], freq="Q").end_time.normalize()
                       + pd.Timedelta(days=SALES_PUBLICATION_LAG_DAYS)).strftime("%Y-%m-%d")
    return q
