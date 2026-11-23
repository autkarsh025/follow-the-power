"""Analytics on synthetic data with KNOWN answers: weather normalization, steps, forecast, matrix, quality."""
import numpy as np
import pandas as pd

from ftp import analytics, db, leadlag, pipeline, quality, sec
from fixtures import load_synthetic, load_weather_zone


def _zone():
    conn = db.connect()
    truth = load_weather_zone(conn)
    return conn, truth


def test_weather_normalization_removes_weather_and_keeps_growth():
    conn, truth = _zone()
    norm = analytics.weather_normalize(conn, "DOM")
    assert norm["r2"] > 0.95
    m = norm["monthly"].dropna(subset=["normalized_yoy"])
    # Away from the step, normalized YoY ~= true trend growth (5%); raw YoY is much noisier.
    calm = m[m["month"].between("2020-07", "2022-12")]
    assert abs(calm["normalized_yoy"].median() - 5) < 1.0
    assert calm["normalized_yoy"].std() < calm["actual_yoy"].std()
    assert norm["coef"]["cdd"] > 0 and norm["coef"]["hdd"] > 0


def test_step_detector_finds_the_injected_step():
    conn, truth = _zone()
    norm = analytics.weather_normalize(conn, "DOM")
    steps = analytics.step_changes(norm["daily"])
    hits = steps[(pd.to_datetime(steps["date"]) - pd.Timestamp(truth["step_date"])).abs() <= pd.Timedelta(days=10)]
    assert len(hits) == 1
    assert abs(hits["step_mw"].iloc[0] * 1.0 / 0.85 - truth["step_mw"]) < 250   # overnight shape factor ~0.85
    assert len(steps) <= 3      # no spurious steps from seasons / weather


def test_temperature_response_curves_shift_up():
    conn, _ = _zone()
    tr = analytics.temperature_response(conn, "DOM")
    a, b = tr["2019"], tr["2025"]
    common = set(a["temp_c"]) & set(b["temp_c"])
    t = sorted(common)[len(common) // 2]
    assert b["mw"][b["temp_c"].index(t)] > a["mw"][a["temp_c"].index(t)]


def test_duration_heatmap_profiles_added_and_forecast():
    conn, _ = _zone()
    h = analytics.hourly_local(conn, "DOM")
    ld = analytics.load_duration(h)
    assert ld["2019"]["pct_of_peak"][0] == 100.0 and 0 < ld["2019"]["load_factor"] < 1
    hm = analytics.heatmap(h)
    assert len(hm["mw"]) == 24 and len(hm["months"]) == 84
    assert len(analytics.yearly_profiles(h)) == 7
    daily = analytics.daily_frame(conn, "DOM", h)
    norm = analytics.weather_normalize(conn, "DOM", daily)
    add = analytics.added_load(norm, daily)
    assert add["added_mw"] > 0 and add["gpu_equivalent"] > 0
    fc = analytics.forecast(norm["monthly"])
    assert len(fc["months"]) == 24 and all(l <= m <= h_ for l, m, h_ in zip(fc["p10"], fc["p50"], fc["p90"]))
    assert 2 < fc["annual_growth_pct"] < 12
    summ = analytics.zone_summary(conn, "DOM", h, daily, norm)
    assert summ["normalized_12m_growth"] is not None


def test_chain_matrix_and_restatements():
    conn = db.connect()
    load_synthetic(conn, True)
    fin = leadlag.fin_versions(conn)
    restated = fin.iloc[[0]].copy()
    restated["value_usd"] *= 1.05
    restated["filed_date"] = "2030-01-01"
    db.upsert(conn, "fin_quarterly", restated.drop(columns="quarter"), ["ticker", "metric", "fiscal_year", "fiscal_q", "filed_date"])
    fin = leadlag.fin_versions(conn)
    r = analytics.restatements(fin)
    assert len(r) == 1 and abs(r["change_pct"].iloc[0] - 5) < 0.01
    from ftp import power
    df = analytics.chain_series(fin, power.zone_quarterly(conn, "DOM"))
    mx = analytics.lead_lag_matrix(df)
    i, j = mx["names"].index("N. Virginia baseload"), mx["names"].index("NVIDIA revenue")
    assert mx["lag"][i][j] == 0 and mx["corr"][i][j] > 0.8       # synthetic: grid drives same-quarter revenue
    assert mx["corr"][i][i] is None


def test_sec_merges_concepts_across_tag_changes():
    old = [{"start": "2019-01-01", "end": "2019-03-31", "val": 10, "filed": "2019-04-20", "form": "10-Q", "accn": "a"}]
    new = [{"start": "2020-01-01", "end": "2020-03-31", "val": 20, "filed": "2020-04-20", "form": "10-Q", "accn": "b"},
           {"start": "2019-01-01", "end": "2019-03-31", "val": 11, "filed": "2020-04-20", "form": "10-Q", "accn": "b"}]
    for y in (2019, 2020):
        new.append({"start": f"{y}-01-01", "end": f"{y}-12-31", "val": 100, "filed": f"{y + 1}-02-20", "form": "10-K", "accn": "k"})
    old.append({"start": "2019-01-01", "end": "2019-12-31", "val": 40, "filed": "2020-02-20", "form": "10-K", "accn": "k"})
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": old}},
                                   "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": new}}}}}
    q = sec.quarterly(facts, "X", "revenue")
    q1 = q[q["fiscal_q"] == 1].set_index("fiscal_year")
    assert q1.loc[2019, "concept"] == "Revenues" and q1.loc[2019, "value_usd"] == 10   # priority concept wins its years
    assert q1.loc[2020, "value_usd"] == 20                                               # later years still covered


def test_quality_checks_and_incremental_resume():
    conn, _ = _zone()
    load_synthetic(conn, True)
    checks = quality.run_all(conn, today=pd.Timestamp("2026-01-02", tz="UTC"))
    dom = checks[(checks["subject"] == "DOM")].set_index("check_id")
    assert dom.loc["has_data", "passed"] == 1 and dom.loc["coverage_30d", "passed"] == 1
    assert checks[(checks["subject"] == "AEP") & (checks["check_id"] == "has_data")]["passed"].iloc[0] == 0
    assert len(quality.latest(conn)) == len(checks)
    assert pipeline.resume_from(conn, "zone_hourly", "period_utc", "DOM", "2019-01-01", 3) == "2025-12-29"
    assert pipeline.resume_from(conn, "zone_hourly", "period_utc", "NOPE", "2019-01-01", 3) == "2019-01-01"


def test_company_level_analytics():
    conn = db.connect()
    load_synthetic(conn, True)
    load_weather_zone(conn, "DOM")   # overwrite DOM with weather-driven load (still has growth)
    fin = leadlag.fin_versions(conn)
    ex = analytics.exposure(fin)
    assert list(ex["ticker"]) == ["NVDA"] and -1 <= ex["corr"].iloc[0] <= 1 and 0 <= ex["best_lag_q"].iloc[0] <= 4
    race = analytics.revenue_race(fin)
    assert race["NVDA"]["index"][0] == 100.0
    assert analytics.capex_intensity(fin) == {}          # synthetic data has no hyperscaler revenue
    feed = analytics.filing_feed(fin)
    assert feed[0]["filed"] >= feed[-1]["filed"]
    sb, detail = analytics.backtest_scoreboard(conn, ["NVDA"])
    assert len(sb) == 1 and sb["best"].iloc[0] in analytics.MODELS and not detail["NVDA"].empty
    assert all(detail["NVDA"]["decision_date"] > detail["NVDA"]["quarter"].str[:4])


def test_variance_flags_catch_a_sudden_acceleration():
    conn = db.connect()
    load_synthetic(conn, False)
    fin = leadlag.fin_versions(conn)
    q = fin[(fin["ticker"] == "NVDA")].sort_values("quarter")
    target = q["quarter"].iloc[-3]
    fin.loc[(fin["ticker"] == "NVDA") & (fin["quarter"] == target), "value_usd"] *= 3     # an outlier quarter
    flags = analytics.variance_flags(fin)
    hit = flags[(flags["ticker"] == "NVDA") & (flags["quarter"] == target)]
    assert len(hit) == 1 and hit["direction"].iloc[0] == "acceleration" and hit["z"].iloc[0] >= 2


def test_step_detector_ignores_calendar_seasonality():
    """A strong non-weather seasonal swing (e.g. daylight) must not swamp a real step."""
    days = pd.date_range("2019-01-01", "2025-12-31", freq="D")
    rng = np.random.default_rng(3)
    season = 900 * np.sin(2 * np.pi * days.dayofyear / 365.25)
    level = 10_000 + 1.5 * np.arange(len(days)) + np.where(days >= "2023-06-01", 400, 0)
    df = pd.DataFrame({"date": days.strftime("%Y-%m-%d"), "deweathered_mw": level + season + rng.normal(0, 60, len(days))})
    st = analytics.step_changes(df)
    near = st[(pd.to_datetime(st["date"]) - pd.Timestamp("2023-06-01")).abs() <= pd.Timedelta(days=10)]
    assert len(near) == 1 and 250 < near["step_mw"].iloc[0] < 550
    assert len(st) <= 2
