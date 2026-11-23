"""Lead/lag + backtest on synthetic data where the true relationship is known."""
import numpy as np
import pandas as pd

from fixtures import load_synthetic
from ftp import db, leadlag, power


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
