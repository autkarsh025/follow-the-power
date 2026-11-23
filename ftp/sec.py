"""SEC EDGAR: quarterly revenue and capex, POINT-IN-TIME (each value keeps its first filing date).

The XBRL "companyfacts" API returns every reported value for every concept, including
year-to-date figures and restatements. Turning that into clean quarters takes three steps:

  1. Keep facts from 10-Q / 10-K filings, in USD.
  2. Income-statement items (revenue) are reported per quarter (~90-day durations) in 10-Qs,
     but Q4 only appears inside the full-year 10-K figure  ->  Q4 = FY - (Q1 + Q2 + Q3).
     Cash-flow items (capex) are reported YEAR-TO-DATE (3, 6, 9, 12 months)
     ->  each quarter = this YTD - previous YTD.
  3. For every quarter, record the date the number was first made public (`filed`).
     A later restatement is kept as a separate row. Analyses filter "filed_date <= as_of",
     so a backtest never sees a number before it existed.
"""
from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd
import requests

from ftp import config

log = logging.getLogger(__name__)
QUARTER_DAYS = (75, 105)      # a "quarter" duration, with slack for 13-week fiscal quarters
YEAR_DAYS = (350, 380)


def fetch_companyfacts(cik: int, session: requests.Session | None = None) -> dict:
    s = session or requests.Session()
    resp = s.get(config.SEC_FACTS_URL.format(cik=cik), headers={"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
                 timeout=config.REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def save_raw(ticker: str, facts: dict) -> None:
    out = config.RAW_DIR / "sec"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{ticker}_companyfacts.json").write_text(json.dumps(facts))


def facts_frame(facts: dict, concept: str) -> pd.DataFrame:
    """All USD facts for one us-gaap concept, as a DataFrame with durations."""
    units = facts.get("facts", {}).get("us-gaap", {}).get(concept, {}).get("units", {}).get("USD", [])
    df = pd.DataFrame(units)
    if df.empty or "start" not in df:          # instant (balance-sheet style) facts have no start date
        return pd.DataFrame()
    df = df[df["form"].isin(["10-Q", "10-K", "10-Q/A", "10-K/A"]) & df["start"].notna()].copy()
    df["start_d"] = pd.to_datetime(df["start"])
    df["end_d"] = pd.to_datetime(df["end"])
    df["days"] = (df["end_d"] - df["start_d"]).dt.days
    df["filed_d"] = pd.to_datetime(df["filed"])
    return df


def _fiscal_year_starts(df: pd.DataFrame) -> pd.DataFrame:
    """Annual (~365-day) periods define each fiscal year's start and end.

    The CURRENT fiscal year has no full-year figure yet (no 10-K), so it would be invisible.
    If 10-Q facts start the day after the last completed year ends, add that in-progress year."""
    # Only 10-K annual facts define fiscal years: utilities and REITs also report "twelve months ended
    # June 30" inside 10-Qs, which would otherwise look like (overlapping) fiscal years.
    annual = df[df["days"].between(*YEAR_DAYS) & df["form"].str.startswith("10-K")]
    yr = annual[["start_d", "end_d"]].drop_duplicates()
    yr = yr.sort_values("start_d").drop_duplicates("end_d")
    if not yr.empty:
        nxt = yr["end_d"].max() + pd.Timedelta(days=1)
        if (df["start_d"] == nxt).any():
            yr = pd.concat([yr, pd.DataFrame({"start_d": [nxt], "end_d": [nxt + pd.Timedelta(days=364)]})],
                           ignore_index=True)
    yr["fiscal_year"] = yr["end_d"].dt.year
    return yr


def quarterly(facts: dict, ticker: str, metric: str) -> pd.DataFrame:
    """Point-in-time quarterly values for one company and metric.

    Companies switch XBRL tags over time (e.g. `Revenues` -> `RevenueFromContract...` after ASC 606),
    so every candidate concept is converted and the results are merged PER QUARTER: each quarter
    takes its versions from the highest-priority concept that covers it. Using only the first
    concept found would silently drop the years tagged differently."""
    parts = []
    for rank, concept in enumerate(config.METRIC_CONCEPTS[metric]):
        df = facts_frame(facts, concept)
        if df.empty or not (df["days"].between(*QUARTER_DAYS).any() or df["days"].between(*YEAR_DAYS).any()):
            continue
        out = _to_quarters(df, ticker, metric, concept)
        if not out.empty:
            parts.append(out.assign(_rank=rank))
    if not parts:
        log.warning("%s %s: no usable concept found", ticker, metric)
        return pd.DataFrame()
    allq = pd.concat(parts, ignore_index=True)
    best = allq.groupby(["fiscal_year", "fiscal_q"])["_rank"].transform("min")
    return allq[allq["_rank"] == best].drop(columns="_rank").reset_index(drop=True)


def _to_quarters(df: pd.DataFrame, ticker: str, metric: str, concept: str) -> pd.DataFrame:
    """For each fiscal year and quarter, collect every published version of the quarter's value.

    Preference order per quarter:
      a) a direct ~90-day fact (income-statement items in 10-Qs),
      b) Q1's year-to-date value (which IS the quarter),
      c) YTD(q) - YTD(q-1), using the previous YTD as known on the same filing date,
      d) YTD(q) - sum of the earlier quarters as known on that date (when YTD(q-1) wasn't tagged).
    """
    rows: list[dict] = []
    for _, y in _fiscal_year_starts(df).iterrows():
        if y["fiscal_year"] < config.FIRST_FISCAL_YEAR:
            continue
        in_year = df[(df["start_d"] >= y["start_d"]) & (df["end_d"] <= y["end_d"] + pd.Timedelta(days=3))]
        ytd = in_year[in_year["start_d"] == y["start_d"]].copy()
        ytd = ytd[~(ytd["days"].between(*YEAR_DAYS) & ~ytd["form"].str.startswith("10-K"))]
        ytd["q"] = (ytd["days"] / 91.3).round().astype(int).clip(1, 4)
        direct = in_year[in_year["days"].between(*QUARTER_DAYS)]
        versions: dict[int, list[dict]] = {}
        for q in range(1, 5):
            target_end = y["start_d"] + pd.Timedelta(days=round(q * 91.3))
            d = direct[(direct["end_d"] - target_end).abs() <= pd.Timedelta(days=20)]
            if not d.empty:
                vs = [_row(ticker, metric, y, q, r["start_d"], r["end_d"], r["val"], r["filed_d"],
                           r["form"], r.get("accn"), concept, False) for _, r in d.iterrows()]
            else:
                vs = _derive(ytd, versions, y, q, ticker, metric, concept)
            versions[q] = vs
            rows.extend(vs)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Identical values re-reported later are the same version (keep the FIRST filing date);
    # a changed value is a restatement (kept as its own row with its own filing date).
    return (out.sort_values("filed_date")
               .drop_duplicates(["ticker", "metric", "fiscal_year", "fiscal_q", "value_usd"], keep="first")
               .drop_duplicates(["ticker", "metric", "fiscal_year", "fiscal_q", "filed_date"], keep="last")
               .reset_index(drop=True))


def _row(ticker, metric, y, q, start, end, value, filed, form, accn, concept, derived) -> dict:
    return {"ticker": ticker, "metric": metric, "fiscal_year": int(y["fiscal_year"]), "fiscal_q": int(q),
            "period_start": pd.Timestamp(start).strftime("%Y-%m-%d"), "period_end": pd.Timestamp(end).strftime("%Y-%m-%d"),
            "value_usd": float(value), "filed_date": pd.Timestamp(filed).strftime("%Y-%m-%d"), "form": form,
            "accession": accn, "concept": concept, "derived": int(derived)}


def _known(vs: list[dict], filed: pd.Timestamp) -> dict | None:
    """Latest version of a quarter that was public on `filed`."""
    k = [v for v in vs if v["filed_date"] <= filed.strftime("%Y-%m-%d")]
    return max(k, key=lambda v: v["filed_date"]) if k else None


def _derive(ytd, versions, y, q, ticker, metric, concept) -> list[dict]:
    rows = []
    for _, r in ytd[ytd["q"] == q].iterrows():
        if q == 1:
            rows.append(_row(ticker, metric, y, 1, r["start_d"], r["end_d"], r["val"], r["filed_d"],
                             r["form"], r.get("accn"), concept, False))
            continue
        prev = ytd[(ytd["q"] == q - 1) & (ytd["filed_d"] <= r["filed_d"])].sort_values("filed_d")
        if not prev.empty:
            p = prev.iloc[-1]
            rows.append(_row(ticker, metric, y, q, p["end_d"] + pd.Timedelta(days=1), r["end_d"],
                             r["val"] - p["val"], r["filed_d"], r["form"], r.get("accn"), concept, True))
            continue
        earlier = [_known(versions.get(k, []), r["filed_d"]) for k in range(1, q)]
        if all(earlier):
            start = pd.Timestamp(earlier[-1]["period_end"]) + pd.Timedelta(days=1)
            rows.append(_row(ticker, metric, y, q, start, r["end_d"], r["val"] - sum(e["value_usd"] for e in earlier),
                             r["filed_d"], r["form"], r.get("accn"), concept, True))
    return rows


def as_of(df: pd.DataFrame, when: str | date) -> pd.DataFrame:
    """Point-in-time view: the latest version of each quarter that was public on `when`."""
    when = pd.Timestamp(when).strftime("%Y-%m-%d")
    known = df[df["filed_date"] <= when].sort_values("filed_date")
    return known.drop_duplicates(["ticker", "metric", "fiscal_year", "fiscal_q"], keep="last")
