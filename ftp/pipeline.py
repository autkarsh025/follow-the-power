"""Load everything: SEC financials (point-in-time) + grid zone demand + state electricity sales.

Usage:  python -m ftp.pipeline [--skip-hourly] [--full] [--start 2019-01-01]

Incremental by default: hourly grid data and weather are fetched from the last stored timestamp
(minus a few days, to pick up EIA's late revisions), so the daily run takes seconds instead of
re-downloading 8 years. Upserts make every step idempotent: re-running never duplicates rows.
--full forces a complete backfill. Each step is logged in pipeline_runs; one failing source
doesn't stop the others. Data-quality checks run last; a failed CRITICAL check fails the run.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from ftp import config, db, eia, quality, sec, weather

log = logging.getLogger("ftp")
FIN_KEYS = ["ticker", "metric", "fiscal_year", "fiscal_q", "filed_date"]


def load_sec(conn) -> int:
    """One company failing (e.g. a filing format change) is logged and skipped, not fatal;
    the quality checks then flag the missing company. Only if every company fails does the step fail."""
    total, failed = 0, []
    for ticker, info in config.COMPANIES.items():
        try:
            facts = sec.fetch_companyfacts(info["cik"])
            sec.save_raw(ticker, facts)
            for metric in config.COMPANY_METRICS[ticker]:
                q = sec.quarterly(facts, ticker, metric)
                total += db.upsert(conn, "fin_quarterly", q, FIN_KEYS)
                log.info("SEC %s %s: %d quarter-versions", ticker, metric, len(q))
        except Exception as exc:
            log.error("SEC %s failed: %s", ticker, exc)
            failed.append(ticker)
        time.sleep(0.2)   # SEC fair access: stay far below 10 requests/second
    if len(failed) == len(config.COMPANIES):
        raise RuntimeError("every SEC company failed")
    return total


REVISION_WINDOW_DAYS = 3   # EIA revises recent hours; always re-fetch the last few days


def resume_from(conn, table: str, col: str, zone: str, default: str, back_days: int) -> str:
    """Incremental start date: last stored timestamp for this zone minus a revision window."""
    last = conn.execute(f"SELECT MAX({col}) FROM {table} WHERE zone = ?", (zone,)).fetchone()[0]
    if not last:
        return default
    return max(default, (date.fromisoformat(last[:10]) - timedelta(days=back_days)).isoformat())


def load_zones(conn, start: str, end: str, full: bool = False) -> int:
    total = 0
    for parent, zone, label, _tz in config.GRID_ZONES:
        z_start = start if full else resume_from(conn, "zone_hourly", "period_utc", zone, start, REVISION_WINDOW_DAYS)
        # Yearly chunks keep each request a manageable size.
        cur = date.fromisoformat(z_start)
        stop = date.fromisoformat(end)
        while cur <= stop:
            chunk_end = min(date(cur.year, 12, 31), stop)
            df = eia.zone_hourly(parent, zone, cur.isoformat(), chunk_end.isoformat())
            total += db.upsert(conn, "zone_hourly", df, ["zone", "period_utc"])
            log.info("EIA %s %s..%s: %d hours", zone, cur, chunk_end, len(df))
            cur = chunk_end + timedelta(days=1)
    return total


def load_weather(conn, start: str, end: str, full: bool = False) -> int:
    total = 0
    for _, zone, _, _ in config.GRID_ZONES:
        w_start = start if full else resume_from(conn, "zone_weather_daily", "date", zone, start, 10)
        df = weather.fetch_daily(zone, w_start, end)
        total += db.upsert(conn, "zone_weather_daily", df, ["zone", "date"])
        log.info("weather %s %s..%s: %d days", zone, w_start, end, len(df))
    return total


def load_sales(conn) -> int:
    df = eia.state_sales(config.RETAIL_STATES, config.RETAIL_SECTOR)
    return db.upsert(conn, "state_monthly_sales", df, ["state", "sector", "month"])


def run(start: str = config.HOURLY_START, skip_hourly: bool = False, full: bool = False) -> dict[str, int]:
    conn = db.connect()
    end = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    w_end = (datetime.now(timezone.utc).date() - timedelta(days=6)).isoformat()   # reanalysis lags ~5 days
    steps = [("sec", lambda: load_sec(conn)), ("state_sales", lambda: load_sales(conn)),
             ("weather", lambda: load_weather(conn, start, w_end, full))]
    if not skip_hourly:
        steps.append(("zone_hourly", lambda: load_zones(conn, start, end, full)))
    results, failures = {}, []
    for name, fn in steps:
        try:
            results[name] = fn()
            db.log_run(conn, name, results[name], "success")
        except Exception as exc:  # each source is independent: log, record, keep going
            log.error("%s failed: %s", name, exc)
            db.log_run(conn, name, 0, "failed", f"{type(exc).__name__}: {exc}")
            failures.append(name)
    checks = quality.run_all(conn)
    bad = checks[(checks["severity"] == "critical") & (checks["passed"] == 0)]
    log.info("quality: %d/%d checks passed", int(checks["passed"].sum()), len(checks))
    for r in checks[checks["passed"] == 0].itertuples():
        log.warning("quality %s %s [%s]: %s", r.check_id, r.subject, r.severity, r.detail)
    conn.close()
    if not bad.empty:
        failures.append("quality:" + ",".join(bad["subject"] + "/" + bad["check_id"]))
    if failures:
        raise RuntimeError(f"steps failed: {failures}")
    return results


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=config.HOURLY_START)
    p.add_argument("--skip-hourly", action="store_true")
    p.add_argument("--full", action="store_true", help="full backfill instead of incremental")
    a = p.parse_args(argv)
    log.info("done: %s", run(a.start, a.skip_hourly, a.full))


if __name__ == "__main__":
    main()
