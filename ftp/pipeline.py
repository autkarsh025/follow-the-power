"""Load everything: SEC financials (point-in-time) + grid zone demand + state electricity sales.

Usage:  python -m ftp.pipeline [--skip-hourly] [--start 2019-01-01]
Each step is logged in pipeline_runs; one failing source doesn't stop the others.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from ftp import config, db, eia, sec

log = logging.getLogger("ftp")
FIN_KEYS = ["ticker", "metric", "fiscal_year", "fiscal_q", "filed_date"]


def load_sec(conn) -> int:
    total = 0
    for ticker, info in config.COMPANIES.items():
        facts = sec.fetch_companyfacts(info["cik"])
        sec.save_raw(ticker, facts)
        for metric in config.COMPANY_METRICS[ticker]:
            q = sec.quarterly(facts, ticker, metric)
            total += db.upsert(conn, "fin_quarterly", q, FIN_KEYS)
            log.info("SEC %s %s: %d quarter-versions", ticker, metric, len(q))
        time.sleep(0.2)   # SEC fair access: stay far below 10 requests/second
    return total


def load_zones(conn, start: str, end: str) -> int:
    total = 0
    for parent, zone, label, _tz in config.GRID_ZONES:
        # Yearly chunks keep each request a manageable size.
        cur = date.fromisoformat(start)
        stop = date.fromisoformat(end)
        while cur <= stop:
            chunk_end = min(date(cur.year, 12, 31), stop)
            df = eia.zone_hourly(parent, zone, cur.isoformat(), chunk_end.isoformat())
            total += db.upsert(conn, "zone_hourly", df, ["zone", "period_utc"])
            log.info("EIA %s %s..%s: %d hours", zone, cur, chunk_end, len(df))
            cur = chunk_end + timedelta(days=1)
    return total


def load_sales(conn) -> int:
    df = eia.state_sales(config.RETAIL_STATES, config.RETAIL_SECTOR)
    return db.upsert(conn, "state_monthly_sales", df, ["state", "sector", "month"])


def run(start: str = config.HOURLY_START, skip_hourly: bool = False) -> dict[str, int]:
    conn = db.connect()
    end = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    steps = [("sec", lambda: load_sec(conn)), ("state_sales", lambda: load_sales(conn))]
    if not skip_hourly:
        steps.append(("zone_hourly", lambda: load_zones(conn, start, end)))
    results, failures = {}, []
    for name, fn in steps:
        try:
            results[name] = fn()
            db.log_run(conn, name, results[name], "success")
        except Exception as exc:  # each source is independent: log, record, keep going
            log.error("%s failed: %s", name, exc)
            db.log_run(conn, name, 0, "failed", f"{type(exc).__name__}: {exc}")
            failures.append(name)
    conn.close()
    if failures:
        raise RuntimeError(f"steps failed: {failures}")
    return results


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=config.HOURLY_START)
    p.add_argument("--skip-hourly", action="store_true")
    a = p.parse_args(argv)
    log.info("done: %s", run(a.start, a.skip_hourly))


if __name__ == "__main__":
    main()
