"""Lead/lag + backtest on synthetic data where the true relationship is known."""
import numpy as np
import pandas as pd

from ftp import db, leadlag, power


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


def test_grid_model_beats_naive_when_grid_truly_drives_revenue():
    conn = db.connect(); load_synthetic(conn, True)
    s = leadlag.score(leadlag.backtest(conn)).set_index("model")["mae_pp"]
    assert s["grid"] < s["naive"] and s["grid"] < s["capex"]


def test_backtest_never_uses_future_revenue():
    conn = db.connect(); load_synthetic(conn, True)
    bt = leadlag.backtest(conn)
    # Each decision date is before that quarter's revenue was filed (quarter end + 30 days).
    filed = pd.PeriodIndex(bt["quarter"], freq="Q").end_time + pd.Timedelta(days=30)
    assert (pd.to_datetime(bt["decision_date"]) < filed).all()


def test_power_index_and_cross_correlation():
    conn = db.connect(); load_synthetic(conn, True)
    q = power.zone_quarterly(conn, "DOM")
    assert q["overnight_yoy"].notna().sum() > 15 and (q["flatness"] > 0.99).all()   # synthetic load is flat
    s = leadlag.series(leadlag.fin_versions(conn)).set_index("quarter")
    cc = leadlag.cross_correlation(q.set_index("quarter")["overnight_yoy"], s["nvda_yoy"])
    best = cc.loc[cc["corr"].abs().idxmax()]
    assert best["lag_quarters"] == 0 and best["corr"] > 0.9
