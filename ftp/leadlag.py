"""Does the grid move with the money? Lead/lag correlations and a point-in-time nowcast backtest.

Everything is compared as year-over-year growth (%), which removes seasonality and the trend.

1) Cross-correlation: corr(x_t, y_{t+k}) for k = -4..+4 quarters. A peak at k > 0 means x LEADS y.
   With ~20-30 quarters, correlations below ~2/sqrt(n) in size are indistinguishable from noise,
   so that band is reported alongside every number.

2) Nowcast backtest ("time travel"): for each quarter T, stand at decision date D = 7 days after
   T ends, which is before NVIDIA reports T. At D you know:
     - NVIDIA revenue growth through T-1 (only filings with filed_date <= D)
     - hyperscaler capex growth through T-1 (same rule)
     - grid baseload growth for T itself (grid data is published within days)
   Three models predict NVIDIA's revenue growth for T, each refit on history known at D:
     naive  : growth_T = growth_{T-1}
     capex  : growth_T ~ capex_growth_{T-1}
     grid   : growth_T ~ growth_{T-1} + overnight_baseload_growth_T
   Scored against NVIDIA's growth for T as FIRST reported. If the grid model doesn't beat naive,
   that's the finding, and it gets reported that way.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from ftp import config, power, sec

MIN_TRAIN = 8
DECISION_LAG_DAYS = power.GRID_PUBLICATION_LAG_DAYS


def _calendar_quarter(df: pd.DataFrame) -> pd.Series:
    mid = pd.to_datetime(df["period_start"]) + (pd.to_datetime(df["period_end"]) - pd.to_datetime(df["period_start"])) / 2
    return mid.dt.to_period("Q").astype(str)


def fin_versions(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM fin_quarterly", conn)
    if not df.empty:
        df["quarter"] = _calendar_quarter(df)
    return df


def series(fin: pd.DataFrame, as_of: str | None = None) -> pd.DataFrame:
    """Quarterly NVIDIA revenue and total hyperscaler capex (as known on `as_of`, or latest), plus YoY growth."""
    view = sec.as_of(fin, as_of or "2100-01-01")
    nv = view[(view["ticker"] == "NVDA") & (view["metric"] == "revenue")].set_index("quarter")["value_usd"]
    cap = view[(view["metric"] == "capex")]
    hyperscalers = [t for t, c in config.COMPANIES.items() if c["role"] == "hyperscaler"]
    pivot = cap[cap["ticker"].isin(hyperscalers)].pivot_table(index="quarter", columns="ticker", values="value_usd",
                                                              aggfunc="last")
    # Only the four hyperscalers, and only quarters where all four have reported.
    capex = pivot[hyperscalers].dropna().sum(axis=1) \
        if not pivot.empty and set(hyperscalers) <= set(pivot.columns) else pd.Series(dtype=float)
    out = pd.DataFrame({"nvda_revenue": nv, "hyperscaler_capex": capex}).sort_index()
    out.index = pd.PeriodIndex(out.index, freq="Q")
    out = out.asfreq("Q")
    out["nvda_yoy"] = 100 * (out["nvda_revenue"] / out["nvda_revenue"].shift(4) - 1)
    out["capex_yoy"] = 100 * (out["hyperscaler_capex"] / out["hyperscaler_capex"].shift(4) - 1)
    out.index = out.index.astype(str)
    return out.rename_axis("quarter").reset_index()


def cross_correlation(a: pd.Series, b: pd.Series, max_lag: int = 4) -> pd.DataFrame:
    """corr(a_t, b_{t+k}). Positive best k => a leads b by k quarters."""
    rows = []
    for k in range(-max_lag, max_lag + 1):
        pair = pd.concat([a, b.shift(-k)], axis=1).dropna()
        n = len(pair)
        r = float(pair.iloc[:, 0].corr(pair.iloc[:, 1])) if n >= 6 else np.nan
        rows.append({"lag_quarters": k, "corr": round(r, 3) if not np.isnan(r) else None, "n": n,
                     "noise_band": round(2 / np.sqrt(n), 3) if n else None})
    return pd.DataFrame(rows)


def _ols_predict(train: pd.DataFrame, x_cols: list[str], y_col: str, x_new: dict) -> float | None:
    t = train.replace([np.inf, -np.inf], np.nan).dropna(subset=x_cols + [y_col])
    if len(t) < MIN_TRAIN or any(x_new.get(c) is None or pd.isna(x_new.get(c)) for c in x_cols):
        return None
    X = np.column_stack([np.ones(len(t))] + [t[c].to_numpy(float) for c in x_cols])
    try:
        beta, *_ = np.linalg.lstsq(X, t[y_col].to_numpy(float), rcond=None)
    except np.linalg.LinAlgError:
        return None
    return float(beta[0] + sum(b * x_new[c] for b, c in zip(beta[1:], x_cols)))


def backtest(conn: sqlite3.Connection, zone: str = "DOM") -> pd.DataFrame:
    fin = fin_versions(conn)
    grid = power.zone_quarterly(conn, zone)
    if fin.empty or grid.empty:
        return pd.DataFrame()
    truth = series(fin.sort_values("filed_date").drop_duplicates(
        ["ticker", "metric", "fiscal_year", "fiscal_q"], keep="first"))          # as FIRST reported
    truth = truth.set_index("quarter")["nvda_yoy"]
    rows = []
    for T in truth.dropna().index:
        D = (pd.Period(T, freq="Q").end_time.normalize() + pd.Timedelta(days=DECISION_LAG_DAYS)).strftime("%Y-%m-%d")
        known = series(fin, as_of=D).set_index("quarter")
        g = grid[grid["known_date"] <= D].set_index("quarter")["overnight_yoy"]
        hist = known.join(g.rename("grid_yoy"), how="left")
        hist["nvda_yoy_prev"] = hist["nvda_yoy"].shift(1)
        hist["capex_yoy_prev"] = hist["capex_yoy"].shift(1)
        hist = hist[hist.index < T]                                              # T's own revenue isn't known at D
        prev = pd.Period(T, freq="Q") - 1
        last_growth = known["nvda_yoy"].get(str(prev))
        x = {"nvda_yoy_prev": last_growth, "capex_yoy_prev": known["capex_yoy"].get(str(prev)),
             "grid_yoy": g.get(T)}
        rows.append({
            "quarter": T, "decision_date": D, "actual_yoy": round(float(truth[T]), 2),
            "naive": None if last_growth is None or pd.isna(last_growth) else round(float(last_growth), 2),
            "capex": _ols_predict(hist, ["capex_yoy_prev"], "nvda_yoy", x),
            "grid": _ols_predict(hist, ["nvda_yoy_prev", "grid_yoy"], "nvda_yoy", x),
        })
    out = pd.DataFrame(rows)
    for m in ("capex", "grid"):
        out[m] = out[m].round(2)
    return out


def score(bt: pd.DataFrame) -> pd.DataFrame:
    """Mean absolute error (percentage points of YoY growth) on quarters where ALL models made a prediction."""
    if bt.empty:
        return pd.DataFrame(columns=["model", "mae_pp", "quarters"])
    common = bt.dropna(subset=["naive", "capex", "grid"])
    return pd.DataFrame([{"model": m, "mae_pp": round(float((common[m] - common["actual_yoy"]).abs().mean()), 2)
                          if len(common) else None, "quarters": len(common)} for m in ("naive", "capex", "grid")])
