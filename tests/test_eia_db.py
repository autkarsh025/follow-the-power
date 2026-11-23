import pandas as pd

from ftp import db, eia


def test_zone_cleaning_drops_impossible_and_duplicates():
    rows = [{"period": "2026-01-01T05", "value": "15000"}, {"period": "2026-01-01T06", "value": "0"},
            {"period": "2026-01-01T05", "value": "15100"}, {"period": "garbage", "value": "1"}]
    df = eia.clean_zone(rows, "PJM", "DOM")
    assert list(df["period_utc"]) == ["2026-01-01 05:00:00"] and df["demand_mwh"].iloc[0] == 15100


def test_sales_cleaning():
    df = eia.clean_sales([{"period": "2026-01", "stateid": "VA", "sectorid": "COM", "sales": "5123.4"}])
    assert df.iloc[0].to_dict() == {"state": "VA", "sector": "COM", "month": "2026-01", "sales_gwh": 5123.4}


def test_upsert_is_idempotent_and_updates():
    conn = db.connect()
    df = pd.DataFrame({"state": ["VA"], "sector": ["COM"], "month": ["2026-01"], "sales_gwh": [1.0]})
    db.upsert(conn, "state_monthly_sales", df, ["state", "sector", "month"])
    db.upsert(conn, "state_monthly_sales", df.assign(sales_gwh=2.0), ["state", "sector", "month"])
    assert conn.execute("SELECT COUNT(*), MAX(sales_gwh) FROM state_monthly_sales").fetchone() == (1, 2.0)
