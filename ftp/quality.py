"""Automated data-quality checks, run after every load.

Each check returns pass/fail with a measured value. 'critical' checks fail the pipeline run
(so a broken source can't silently publish a wrong dashboard); 'warn' checks are recorded and
shown on the dashboard's Data quality tab. Results are stored in the quality_checks table.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pandas as pd

from ftp import config, db


def _check(check_id, subject, severity, passed, value=None, detail=""):
    return {"check_id": check_id, "subject": subject, "severity": severity, "passed": int(bool(passed)),
            "value": None if value is None else round(float(value), 4), "detail": detail}


def zone_checks(conn: sqlite3.Connection, zone: str, today: pd.Timestamp) -> list[dict]:
    df = pd.read_sql_query("SELECT period_utc, demand_mwh FROM zone_hourly WHERE zone = ? ORDER BY period_utc",
                           conn, params=(zone,))
    sev = "critical" if zone == config.PRIMARY_ZONE else "warn"
    if df.empty:
        return [_check("has_data", zone, sev, False, 0, "no hourly rows loaded")]
    ts = pd.to_datetime(df["period_utc"], utc=True)
    out = [_check("has_data", zone, sev, True, len(df), f"{len(df):,} hours")]
    lag_h = (today - ts.max()).total_seconds() / 3600
    out.append(_check("freshness_hours", zone, "warn", lag_h <= 96, lag_h, f"latest hour {ts.max():%Y-%m-%d %H:00} UTC"))
    # a source that is a day behind is a blip; a week behind means the feed is broken, so the primary zone fails the run
    out.append(_check("freshness_hours_hard", zone, sev, lag_h <= 7 * 24, lag_h, f"latest hour {ts.max():%Y-%m-%d %H:00} UTC (limit 7 days)"))
    recent = ts[ts > ts.max() - pd.Timedelta(days=30)]       # 30 days ending at the latest hour (freshness is separate)
    cov = len(recent) / (30 * 24)
    out.append(_check("coverage_30d", zone, "warn", cov >= 0.95, cov, f"{cov:.1%} of hours present (30 days to latest hour)"))
    full = pd.date_range(ts.min(), ts.max(), freq="h")
    out.append(_check("gap_hours_total", zone, "warn", len(full) - len(ts) <= 0.01 * len(full), len(full) - len(ts),
                      f"{len(full) - len(ts):,} missing hours since {ts.min():%Y-%m-%d}"))
    v = df["demand_mwh"]
    med = v.median()
    spikes = int(((v > 2.5 * med) | (v < 0.25 * med)).sum())
    out.append(_check("implausible_values", zone, "warn", spikes <= 24, spikes,
                      f"{spikes} hours outside 0.25x-2.5x the median ({med:,.0f} MW)"))
    jumps = int((v.pct_change().abs() > 0.4).sum())
    out.append(_check("hourly_jumps_40pct", zone, "warn", jumps <= 24, jumps, f"{jumps} hour-to-hour jumps over 40%"))
    return out


def fin_checks(conn: sqlite3.Connection, today: pd.Timestamp) -> list[dict]:
    fin = pd.read_sql_query("SELECT * FROM fin_quarterly", conn)
    out = []
    for ticker, metrics in config.COMPANY_METRICS.items():
        for metric in metrics:
            f = fin[(fin["ticker"] == ticker) & (fin["metric"] == metric)]
            sev = "critical" if (ticker, metric) == ("NVDA", "revenue") else "warn"
            subj = f"{ticker} {metric}"
            if f.empty:
                out.append(_check("has_data", subj, sev, False, 0, "no quarters parsed from SEC XBRL"))
                continue
            out.append(_check("has_data", subj, sev, True, len(f), f"{f[['fiscal_year', 'fiscal_q']].drop_duplicates().shape[0]} quarters"))
            age = (today.tz_localize(None) - pd.Timestamp(f["filed_date"].max())).days
            out.append(_check("filing_freshness_days", subj, "warn", age <= 135, age, f"latest filing {f['filed_date'].max()}"))
            neg = int((f["value_usd"] <= 0).sum())
            out.append(_check("non_positive_values", subj, "warn", neg == 0, neg, f"{neg} quarters <= 0"))
            latest = f.sort_values("filed_date").drop_duplicates(["fiscal_year", "fiscal_q"], keep="last")
            latest = latest.sort_values(["fiscal_year", "fiscal_q"])
            yrs = latest.groupby("fiscal_year")["fiscal_q"].nunique()
            holes = int((yrs.iloc[:-1] < 4).sum()) if len(yrs) > 1 else 0
            out.append(_check("quarter_continuity", subj, "warn", holes == 0, holes, f"{holes} past fiscal years missing a quarter"))
    return out


def other_checks(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for st in config.RETAIL_STATES:
        n = conn.execute("SELECT COUNT(*) FROM state_monthly_sales WHERE state = ?", (st,)).fetchone()[0]
        out.append(_check("has_data", f"{st} sales", "warn", n >= 24, n, f"{n} months"))
    for _, zone, _, _ in config.GRID_ZONES:
        n = conn.execute("SELECT COUNT(*) FROM zone_weather_daily WHERE zone = ?", (zone,)).fetchone()[0]
        out.append(_check("has_data", f"{zone} weather", "warn", n >= 365, n, f"{n} days"))
    return out


def run_all(conn: sqlite3.Connection, today: pd.Timestamp | None = None) -> pd.DataFrame:
    today = today or pd.Timestamp.now(tz="UTC")
    rows = []
    for _, zone, _, _ in config.GRID_ZONES:
        rows += zone_checks(conn, zone, today)
    rows += fin_checks(conn, today) + other_checks(conn)
    df = pd.DataFrame(rows)
    df.insert(0, "run_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    db.upsert(conn, "quality_checks", df, ["run_at", "check_id", "subject"])
    return df


def latest(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM quality_checks WHERE run_at = (SELECT MAX(run_at) FROM quality_checks)", conn)
