"""Print the core results: data coverage, power index, lead/lag, backtest scores."""
from __future__ import annotations

import pandas as pd

from ftp import config, db, leadlag, power

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)


def main() -> None:
    conn = db.connect()
    print("== Coverage")
    print(pd.read_sql_query("SELECT ticker, metric, COUNT(DISTINCT fiscal_year || '-' || fiscal_q) AS quarters, "
                            "COUNT(*) AS versions, MIN(period_end) AS first, MAX(period_end) AS last "
                            "FROM fin_quarterly GROUP BY 1, 2", conn).to_string(index=False))
    print(pd.read_sql_query("SELECT zone, COUNT(*) AS hours, MIN(period_utc) AS first, MAX(period_utc) AS last "
                            "FROM zone_hourly GROUP BY 1", conn).to_string(index=False))
    print(pd.read_sql_query("SELECT state, COUNT(*) AS months, MIN(month), MAX(month) FROM state_monthly_sales "
                            "GROUP BY 1", conn).to_string(index=False))
    fin = leadlag.fin_versions(conn)
    if fin.empty:
        print("\nNo financial data loaded yet; skipping analysis.")
        return
    print("\n== Series (latest values)")
    s = leadlag.series(fin)
    print(s.tail(12).round(1).to_string(index=False))
    for zone in [z[1] for z in config.GRID_ZONES]:
        q = power.zone_quarterly(conn, zone)
        print(f"\n== Power index: {zone}")
        print(q.tail(12).round(3).to_string(index=False))
        merged = s.set_index("quarter").join(q.set_index("quarter"), how="inner")
        for a, b in [("capex_yoy", "nvda_yoy"), ("nvda_yoy", "overnight_yoy"), ("capex_yoy", "overnight_yoy")]:
            print(f"\n-- cross-correlation {a} -> {b} (positive lag = {a} leads)")
            print(leadlag.cross_correlation(merged[a], merged[b]).to_string(index=False))
        bt = leadlag.backtest(conn, zone)
        print(f"\n== Nowcast backtest ({zone})")
        print(bt.tail(12).to_string(index=False))
        print(leadlag.score(bt).to_string(index=False))
    va = power.state_sales_quarterly(conn, "VA")
    print("\n== Virginia commercial electricity sales (quarterly)")
    print(va.tail(8).to_string(index=False))


if __name__ == "__main__":
    main()
