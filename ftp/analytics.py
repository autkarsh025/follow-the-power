"""Deeper grid analytics: finding data centers in electricity demand.

  daily_frame          per local day: overnight baseload (median 1-5 AM), mean, peak, min
  weather_normalize    remove temperature effects -> "weather-normalized" baseload growth
  temperature_response load vs temperature, one curve per year (curves shift UP = new always-on load)
  step_changes         sudden sustained jumps in normalized baseload (a big new load switching on)
  load_duration        sorted hourly loads per year; a flatter curve = more 24/7 load
  heatmap              average load by month x hour of day
  added_load           MW of always-on load added since 2019, in GPU and household equivalents
  forecast             log-linear trend of normalized baseload with bootstrap uncertainty bands
  zone_summary         one row per zone for the comparison table
  chain_*              growth across the AI supply chain + a lead/lag matrix
  restatements         quarters whose reported value changed in a later SEC filing

Everything here is descriptive statistics with stated assumptions; the dashboard explains each in plain English.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from ftp import config, leadlag, power, weather

LAGS = range(-4, 5)

# Unit conversions for "how big is that?" (stated on the dashboard; deliberately round numbers)
KW_PER_GPU_IT = 1.4      # H100-class 8-GPU server draws ~10-11 kW incl. CPUs/networking -> ~1.4 kW per GPU
PUE = 1.3                # power usage effectiveness: cooling & losses on top of IT load
KW_PER_HOME = 1.2        # average US home uses ~10,500 kWh/yr ~= 1.2 kW on average


def tz_of(zone: str) -> str:
    return next(z[3] for z in config.GRID_ZONES if z[1] == zone)


def label_of(zone: str) -> str:
    return next(z[2] for z in config.GRID_ZONES if z[1] == zone)


def hourly_local(conn: sqlite3.Connection, zone: str, until: str | None = None) -> pd.DataFrame:
    """Hourly load in local time. `until` (YYYY-MM-DD, exclusive) gives a point-in-time view."""
    df = pd.read_sql_query("SELECT period_utc, demand_mwh FROM zone_hourly WHERE zone = ? AND demand_mwh IS NOT NULL "
                           "AND period_utc < ? ORDER BY period_utc", conn, params=(zone, until or "9999"))
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["period_utc"], utc=True).dt.tz_convert(tz_of(zone))
    df["date"] = df["ts"].dt.strftime("%Y-%m-%d")
    df["hour"] = df["ts"].dt.hour
    return df


def daily_frame(conn: sqlite3.Connection, zone: str, h: pd.DataFrame | None = None, until: str | None = None) -> pd.DataFrame:
    h = hourly_local(conn, zone, until) if h is None else h
    if h.empty:
        return pd.DataFrame(columns=["date", "overnight_mw", "mean_mw", "peak_mw", "min_mw"])
    g = h.groupby("date")["demand_mwh"]
    d = pd.DataFrame({"mean_mw": g.mean(), "peak_mw": g.max(), "min_mw": g.min(), "hours": g.size()})
    d["overnight_mw"] = h[h["hour"].isin(power.OVERNIGHT_HOURS)].groupby("date")["demand_mwh"].median()
    d = d[(d["hours"] >= 20)].dropna(subset=["overnight_mw"]).drop(columns="hours")
    return d.rename_axis("date").reset_index()


def _weather(conn, zone, until: str | None = None) -> pd.DataFrame:
    w = pd.read_sql_query("SELECT date, tmean_c FROM zone_weather_daily WHERE zone = ? AND date < ?", conn,
                          params=(zone, until or "9999"))
    if not w.empty:
        w["hdd"], w["cdd"] = weather.degree_days(w["tmean_c"])
    return w


def _design(df: pd.DataFrame) -> np.ndarray:
    """Weather terms: heating and cooling degree days, with curvature (demand rises faster in extremes)."""
    return np.column_stack([df["hdd"], df["hdd"] ** 2, df["cdd"], df["cdd"] ** 2])


def weather_normalize(conn: sqlite3.Connection, zone: str, daily: pd.DataFrame | None = None,
                      until: str | None = None) -> dict:
    """Weather-normalized overnight baseload.

    Model, per day:  overnight_load = level[month] + f(HDD, CDD) + weekend + noise
      level[month]  one free level per calendar month (YYYY-MM): absorbs ALL growth, so the weather
                    coefficients come only from day-to-day swings inside a month
      f             quadratic in heating & cooling degree days
    Normalized load = actual - f(actual weather) + f(normal weather for that time of year), where
    "normal" = the average weather effect for that calendar month over all years.
    Year-over-year growth of the normalized series is growth that weather can't explain.
    """
    d = daily_frame(conn, zone, until=until) if daily is None else daily
    w = _weather(conn, zone, until)
    if d.empty or w.empty:
        return {}
    df = d.merge(w, on="date").sort_values("date").reset_index(drop=True)
    if len(df) < 400:
        return {}
    df["month"] = df["date"].str[:7]
    df["weekend"] = (pd.to_datetime(df["date"]).dt.dayofweek >= 5).astype(float)
    fe = pd.get_dummies(df["month"]).astype(float).to_numpy()
    X = np.column_stack([fe, _design(df), df["weekend"]])
    y = df["overnight_mw"].to_numpy()
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    fitted = X @ beta
    r2 = 1 - ((y - fitted) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    wb = beta[fe.shape[1]:fe.shape[1] + 4]
    df["weather_effect"] = _design(df) @ wb
    w["cal_month"] = w["date"].str[5:7]
    w["effect"] = _design(w) @ wb
    normal = w.groupby("cal_month")["effect"].mean()
    df["normal_effect"] = df["month"].str[5:7].map(normal)
    df["normalized_mw"] = df["overnight_mw"] - df["weather_effect"] - beta[-1] * df["weekend"] + df["normal_effect"] \
        + beta[-1] * (2 / 7)
    # Weather AND weekday removed, nothing added back: trend + steps + noise. Used for step detection,
    # because the monthly "normal weather" add-back would itself create small jumps at month boundaries.
    df["deweathered_mw"] = df["overnight_mw"] - df["weather_effect"] - beta[-1] * df["weekend"]
    # weather-only model on the residual after removing month levels: how much of daily variation is weather?
    within = y - fe @ beta[:fe.shape[1]]
    wvar = 1 - ((within - _design(df) @ wb - beta[-1] * df["weekend"]) ** 2).sum() / ((within - within.mean()) ** 2).sum()
    m = df.groupby("month").agg(actual_mw=("overnight_mw", "mean"), normalized_mw=("normalized_mw", "mean"),
                                weather_mw=("weather_effect", "mean"), normal_mw=("normal_effect", "mean"),
                                tmean_c=("tmean_c", "mean"), hdd=("hdd", "sum"), cdd=("cdd", "sum"),
                                days=("date", "size")).reset_index()
    m = m[m["days"] >= 25].drop(columns="days")
    idx = pd.PeriodIndex(m["month"], freq="M")
    m = m.set_index(idx).asfreq("M")
    m["normalized_yoy"] = 100 * (m["normalized_mw"] / m["normalized_mw"].shift(12) - 1)
    m["actual_yoy"] = 100 * (m["actual_mw"] / m["actual_mw"].shift(12) - 1)
    m["weather_part_mw"] = m["actual_mw"] - m["normalized_mw"]
    m = m.dropna(subset=["actual_mw"])
    m.index = m.index.astype(str)
    m = m.drop(columns="month").rename_axis("month").reset_index().round(2)
    return {"monthly": m, "daily": df[["date", "overnight_mw", "normalized_mw", "deweathered_mw", "tmean_c"]],
            "r2": round(float(r2), 3), "weather_share_within_month": round(float(wvar), 3),
            "coef": {"hdd": round(float(wb[0]), 2), "hdd2": round(float(wb[1]), 3),
                     "cdd": round(float(wb[2]), 2), "cdd2": round(float(wb[3]), 3), "weekend": round(float(beta[-1]), 1)},
            "days": int(len(df))}


def temperature_response(conn: sqlite3.Connection, zone: str, daily: pd.DataFrame | None = None) -> dict:
    """Mean overnight load in 2 °C temperature bins, one curve per year. At the SAME temperature,
    a higher curve means more load that has nothing to do with the weather."""
    d = daily_frame(conn, zone) if daily is None else daily
    w = _weather(conn, zone)
    if d.empty or w.empty:
        return {}
    df = d.merge(w, on="date")
    df["year"] = df["date"].str[:4]
    df["bin"] = (np.floor(df["tmean_c"] / 2) * 2 + 1).clip(-15, 35)
    out = {}
    for yr, g in df.groupby("year"):
        b = g.groupby("bin")["overnight_mw"].agg(["mean", "size"])
        b = b[b["size"] >= 5]
        if len(b) >= 5:
            out[yr] = {"temp_c": b.index.tolist(), "mw": b["mean"].round(0).tolist()}
    return out


def deseasonalize(s: pd.Series) -> pd.Series:
    """Remove what weather doesn't explain but the calendar does (daylight, holidays, school terms):
    detrend with a centered 1-year rolling MEAN (a mean over a full year cancels a seasonal cycle exactly;
    a median doesn't, because seasonal values pile up near the peaks), average the remainder by day of year across years,
    smooth that profile over ±15 days, and subtract it. Trend and level shifts survive; seasons don't."""
    s = s.asfreq("D")
    trend = s.rolling(365, center=True, min_periods=300).mean()
    resid = s - trend
    doy = np.minimum(resid.index.dayofyear.to_numpy(), 365)
    prof = resid.groupby(doy).mean().reindex(range(1, 366)).interpolate(limit_direction="both")
    prof = pd.concat([prof.iloc[-15:], prof, prof.iloc[:15]]).rolling(31, center=True).mean().iloc[15:-15]
    prof.index = range(1, 366)
    return s - pd.Series(prof.reindex(doy).to_numpy(), index=s.index)


def step_changes(daily: pd.DataFrame, col: str = "deweathered_mw", window: int = 56, min_sep: int = 120,
                 k: float = 4.0, min_frac: float = 0.015) -> pd.DataFrame:
    """Sudden, sustained level shifts in daily baseload.

    The series is first deseasonalized (see `deseasonalize`). Then for every day t:
    jump = median(next `window` days) - median(previous `window` days) (8 weeks: long enough that a cold
    snap the weather model underfits doesn't qualify; validated on real data, where it flags the April 2020
    oil-price crash in West Texas and nothing spurious in the AI hubs, whose growth is a gradual ramp). A step is a local maximum of |jump|
    (no larger one within `min_sep` days) that is bigger than k x a robust noise scale (MAD of the jump
    series) AND bigger than `min_frac` of the typical load. Medians make it robust to single odd days;
    requiring a sustained shift ignores heat waves."""
    empty = pd.DataFrame(columns=["date", "step_mw", "before_mw", "after_mw", "pct"])
    if daily is None or daily.empty or col not in daily:
        return empty
    raw = daily.set_index(pd.to_datetime(daily["date"]))[col].asfreq("D")
    s = deseasonalize(raw) if raw.notna().sum() > 730 else raw
    before = s.rolling(window, min_periods=int(window * 0.8)).median().shift(1)
    after = s[::-1].rolling(window, min_periods=int(window * 0.8)).median()[::-1]
    jump = (after - before).dropna()
    if jump.empty:
        return empty
    mad = (jump - jump.median()).abs().median() * 1.4826
    thr = max(k * mad, min_frac * float(raw.median()))
    cand = jump[jump.abs() > thr]
    peaks = []
    for t, v in cand.abs().sort_values(ascending=False).items():
        if all(abs((t - p).days) > min_sep for p in peaks):
            peaks.append(t)
    rows = [{"date": t.strftime("%Y-%m-%d"), "step_mw": round(float(jump[t]), 0), "before_mw": round(float(before[t]), 0),
             "after_mw": round(float(after[t]), 0), "pct": round(100 * float(jump[t] / before[t]), 2)} for t in sorted(peaks)]
    out = pd.DataFrame(rows, columns=empty.columns)
    out.attrs["threshold_mw"] = round(float(thr), 0)
    return out


def load_duration(h: pd.DataFrame) -> dict:
    """Load-duration curve per calendar year: hourly loads sorted high->low, as % of that year's peak.
    Load factor = average / peak. Data centers fill in the bottom of the curve and raise the load factor."""
    if h.empty:
        return {}
    out = {}
    years = h["ts"].dt.year
    for yr in sorted(years.unique()):
        v = h.loc[years == yr, "demand_mwh"].to_numpy()
        if len(v) < 24 * 300:
            continue
        pk = v.max()
        pct = np.percentile(v, np.arange(100, -1, -2))
        out[str(yr)] = {"hours_pct": list(range(0, 101, 2)), "pct_of_peak": np.round(100 * pct / pk, 1).tolist(),
                        "load_factor": round(float(v.mean() / pk), 3), "peak_mw": round(float(pk)),
                        "min_mw": round(float(v.min()))}
    return out


def heatmap(h: pd.DataFrame) -> dict:
    if h.empty:
        return {}
    h = h.assign(month=h["ts"].dt.strftime("%Y-%m"))
    p = h.pivot_table(index="hour", columns="month", values="demand_mwh", aggfunc="mean")
    counts = h.groupby("month").size()
    p = p[[c for c in p.columns if counts[c] >= 24 * 25]]
    return {"months": list(p.columns), "hours": list(range(24)),
            "mw": [[None if np.isnan(x) else round(float(x)) for x in p.loc[hr].tolist()] for hr in range(24) if hr in p.index]}


def yearly_profiles(h: pd.DataFrame) -> dict:
    """Average hourly profile per year as % of that year's average peak hour."""
    if h.empty:
        return {}
    out = {}
    for yr, g in h.groupby(h["ts"].dt.year):
        if len(g) < 24 * 300:
            continue
        prof = g.groupby("hour")["demand_mwh"].mean().reindex(range(24))
        out[str(yr)] = {"mw": prof.round(0).tolist(), "pct_of_peak": (100 * prof / prof.max()).round(1).tolist()}
    return out


def _base(m: pd.Series) -> tuple[pd.Series | None, str]:
    """The comparison base: calendar 2019 if (nearly) complete, else the first 12 months of data
    (EIA's ERCOT sub-zones start mid-2019; a partial year would bias the base toward one season)."""
    y19 = m[m.index.str.startswith("2019")]
    if len(y19) >= 10:
        return y19, "2019"
    if len(m) >= 24:
        b = m.iloc[:12]
        return b, f"{b.index[0]} to {b.index[-1]}"
    return None, ""


def added_load(norm: dict, daily: pd.DataFrame) -> dict:
    """Always-on load added since 2019: last 12 months vs 2019 average of (normalized if available) baseload."""
    if norm:
        m = norm["monthly"].set_index("month")["normalized_mw"].dropna()
        basis = "weather-normalized"
    elif not daily.empty:
        m = daily.assign(month=daily["date"].str[:7]).groupby("month")["overnight_mw"].mean()
        basis = "raw"
    else:
        return {}
    base, base_window = _base(m)
    if base is None or len(m) < 24:
        return {}
    recent = m.tail(12)
    mw = float(recent.mean() - base.mean())
    return {"basis": basis, "base_mw": round(float(base.mean())), "recent_mw": round(float(recent.mean())),
            "base_window": base_window,
            "added_mw": round(mw), "added_pct": round(100 * mw / float(base.mean()), 1),
            "recent_window": f"{recent.index[0]} to {recent.index[-1]}",
            "gpu_equivalent": int(round(mw * 1000 / (KW_PER_GPU_IT * PUE), -3)),
            "homes_equivalent": int(round(mw * 1000 / KW_PER_HOME, -3)),
            "assumptions": {"kw_per_gpu_it": KW_PER_GPU_IT, "pue": PUE, "kw_per_home": KW_PER_HOME}}


def forecast(monthly: pd.DataFrame, col: str = "normalized_mw", fit_months: int = 36, horizon: int = 24,
             n_boot: int = 400, seed: int = 0) -> dict:
    """Log-linear trend on the last `fit_months` months, projected `horizon` months.

    Uncertainty: residual block bootstrap (blocks of 3 months keep short-run autocorrelation),
    refitting the trend each time, then adding resampled noise to the path. Reports the 10th/50th/90th
    percentiles. This is an extrapolation ("if the recent trend continues"), not a prediction of new projects."""
    if monthly is None or monthly.empty or col not in monthly:
        return {}
    s = monthly.set_index("month")[col].dropna().tail(fit_months)
    if len(s) < 24:
        return {}
    t = np.arange(len(s))
    y = np.log(s.to_numpy())
    b1, b0 = np.polyfit(t, y, 1)
    resid = y - (b0 + b1 * t)
    rng = np.random.default_rng(seed)
    tf = np.arange(len(s), len(s) + horizon)
    paths = np.empty((n_boot, horizon))
    nblk = int(np.ceil(len(s) / 3))
    for i in range(n_boot):
        starts = rng.integers(0, len(s) - 2, nblk)
        r = np.concatenate([resid[j:j + 3] for j in starts])[:len(s)]
        bb1, bb0 = np.polyfit(t, b0 + b1 * t + r, 1)
        noise = np.concatenate([resid[j:j + 3] for j in rng.integers(0, len(s) - 2, int(np.ceil(horizon / 3)))])[:horizon]
        paths[i] = np.exp(bb0 + bb1 * tf + noise)
    last = pd.Period(s.index[-1], freq="M")
    months = [str(last + k) for k in range(1, horizon + 1)]
    p10, p50, p90 = np.percentile(paths, [10, 50, 90], axis=0)
    return {"months": months, "p10": np.round(p10).tolist(), "p50": np.round(p50).tolist(), "p90": np.round(p90).tolist(),
            "fit_from": s.index[0], "fit_to": s.index[-1], "annual_growth_pct": round(100 * (np.exp(12 * b1) - 1), 1)}


def zone_summary(conn: sqlite3.Connection, zone: str, h: pd.DataFrame, daily: pd.DataFrame, norm: dict) -> dict:
    if daily.empty:
        return {}
    q = power.zone_quarterly(conn, zone)
    ld = load_duration(h)
    yrs = sorted(ld)
    row = {"zone": zone, "label": label_of(zone), "latest_quarter": q["quarter"].iloc[-1] if not q.empty else None,
           "overnight_yoy": None if q.empty else q["overnight_yoy"].iloc[-1],
           "flatness_now": None if q.empty else round(float(q["flatness"].iloc[-1]), 3),
           "load_factor_first": ld[yrs[0]]["load_factor"] if yrs else None,
           "load_factor_last": ld[yrs[-1]]["load_factor"] if yrs else None,
           "first_year": yrs[0] if yrs else None, "last_year": yrs[-1] if yrs else None}
    if norm:
        nm = norm["monthly"].set_index("month")["normalized_mw"].dropna()
        if len(nm) >= 24:
            row["normalized_12m_growth"] = round(100 * (nm.tail(12).mean() / nm.iloc[-24:-12].mean() - 1), 2)
        row["weather_r2"] = norm["r2"]
    row.update({f"added_{k}": v for k, v in added_load(norm, daily).items() if k in ("added_mw", "added_pct", "gpu_equivalent")})
    return {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()}


def normalized_index(norm: dict, daily: pd.DataFrame) -> dict:
    """Monthly baseload indexed to the 2019 average (= 100): normalized if possible, else raw."""
    if norm:
        m = norm["monthly"].set_index("month")["normalized_mw"].dropna()
    elif not daily.empty:
        m = daily.assign(month=daily["date"].str[:7]).groupby("month")["overnight_mw"].mean()
    else:
        return {}
    b, window = _base(m)
    if b is None or not b.mean() or np.isnan(b.mean()):
        return {}
    return {"month": m.index.tolist(), "index": (100 * m / b.mean()).round(1).tolist(), "base_window": window}


# ---------------------------------------------------------------------------- supply chain
def chain_growth(fin: pd.DataFrame, as_of: str | None = None) -> pd.DataFrame:
    """Calendar-quarter value and YoY growth for every company/metric (latest version, or as known on as_of)."""
    from ftp import sec
    v = sec.as_of(fin, as_of or "2100-01-01")
    rows = []
    for (t, m), g in v.groupby(["ticker", "metric"]):
        s = g.groupby("quarter")["value_usd"].last().sort_index()
        s.index = pd.PeriodIndex(s.index, freq="Q")
        s = s[~s.index.duplicated()].asfreq("Q")
        prev = s.shift(4)
        yoy = (100 * (s / prev - 1)).where(prev > 0)      # growth is undefined from a zero/negative base
        for q, val in s.items():
            if not np.isnan(val):
                rows.append({"ticker": t, "metric": m, "quarter": str(q), "value_b": round(val / 1e9, 3),
                             "yoy": None if np.isnan(yoy[q]) else round(float(yoy[q]), 2)})
    return pd.DataFrame(rows)


def chain_series(fin: pd.DataFrame, grid_q: pd.DataFrame, norm_q: pd.Series | None = None) -> pd.DataFrame:
    """Quarterly YoY growth series for the lead/lag matrix, indexed by calendar quarter."""
    cg = chain_growth(fin)
    s = leadlag.series(fin).set_index("quarter")
    cols = {"Hyperscaler capex": s["capex_yoy"]}
    names = {("ORCL", "capex"): "Oracle capex", ("NVDA", "revenue"): "NVIDIA revenue", ("AMD", "revenue"): "AMD revenue",
             ("AVGO", "revenue"): "Broadcom revenue", ("VRT", "revenue"): "Vertiv revenue",
             ("D", "capex"): "Dominion capex", ("D", "revenue"): "Dominion revenue",
             ("CEG", "revenue"): "Constellation revenue", ("VST", "revenue"): "Vistra revenue"}
    for (t, m), name in names.items():
        g = cg[(cg["ticker"] == t) & (cg["metric"] == m)]
        if not g.empty:
            cols[name] = g.set_index("quarter")["yoy"].astype(float)
    if not grid_q.empty:
        cols["N. Virginia baseload"] = grid_q.set_index("quarter")["overnight_yoy"]
    if norm_q is not None and not norm_q.empty:
        cols["N. Virginia baseload (weather-adj.)"] = norm_q
    df = pd.DataFrame(cols).sort_index()
    return df[df.index >= "2019Q1"]


def lead_lag_matrix(df: pd.DataFrame) -> dict:
    """For every ordered pair (row, col): the lag in -4..+4 with the strongest correlation.
    Positive lag = the ROW series leads the COLUMN series by that many quarters."""
    names = list(df.columns)
    corr = [[None] * len(names) for _ in names]
    lag = [[None] * len(names) for _ in names]
    band = [[None] * len(names) for _ in names]
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                continue
            cc = leadlag.cross_correlation(df[a], df[b]).dropna(subset=["corr"])
            if cc.empty:
                continue
            best = cc.loc[cc["corr"].abs().idxmax()]
            corr[i][j] = round(float(best["corr"]), 2)
            lag[i][j] = int(best["lag_quarters"])
            band[i][j] = round(float(best["noise_band"]), 2)
    return {"names": names, "corr": corr, "lag": lag, "band": band}


def norm_quarterly(norm: dict) -> pd.Series:
    if not norm:
        return pd.Series(dtype=float)
    m = norm["monthly"].dropna(subset=["normalized_mw"]).copy()
    m["quarter"] = pd.PeriodIndex(m["month"], freq="M").asfreq("Q").astype(str)
    q = m.groupby("quarter").agg(v=("normalized_mw", "mean"), n=("month", "size"))
    q = q[q["n"] == 3]["v"]
    q.index = pd.PeriodIndex(q.index, freq="Q")
    q = q.asfreq("Q")
    out = 100 * (q / q.shift(4) - 1)
    out.index = out.index.astype(str)
    return out.round(2)


def restatements(fin: pd.DataFrame) -> pd.DataFrame:
    """Quarters whose value changed in a later filing (point-in-time storage makes this visible)."""
    if fin.empty:
        return pd.DataFrame()
    rows = []
    for (t, m, fy, fq), g in fin.groupby(["ticker", "metric", "fiscal_year", "fiscal_q"]):
        g = g.sort_values("filed_date")
        if g["value_usd"].round(-3).nunique() < 2:
            continue
        first, last = g.iloc[0], g.iloc[-1]
        rows.append({"ticker": t, "metric": m, "fiscal": f"FY{fy} Q{fq}", "quarter": last["quarter"],
                     "first_b": round(first["value_usd"] / 1e9, 3), "first_filed": first["filed_date"],
                     "latest_b": round(last["value_usd"] / 1e9, 3), "latest_filed": last["filed_date"],
                     "change_pct": round(100 * (last["value_usd"] / first["value_usd"] - 1), 2), "versions": len(g)})
    return pd.DataFrame(rows).sort_values("latest_filed", ascending=False) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------- company-level analytics
def _company_yoy(fin: pd.DataFrame, ticker: str, metric: str = "revenue", as_of: str | None = None) -> pd.Series:
    cg = chain_growth(fin[(fin["ticker"] == ticker) & (fin["metric"] == metric)], as_of=as_of)
    return cg.set_index("quarter")["yoy"].astype(float) if not cg.empty else pd.Series(dtype=float)


def exposure(fin: pd.DataFrame) -> pd.DataFrame:
    """How tied is each company's revenue growth to hyperscaler capex growth?

    For lags 0..4 quarters (capex leading revenue), correlate capex YoY(t) with revenue YoY(t+lag); keep the
    lag with the highest correlation, then estimate beta (pp of revenue growth per 1 pp of capex growth) by OLS
    at that lag. 'meaningful' = correlation outside the ~2/sqrt(n) noise band."""
    capex = leadlag.series(fin).set_index("quarter")["capex_yoy"]
    rows = []
    for t, info in config.COMPANIES.items():
        if "revenue" not in config.COMPANY_METRICS.get(t, []):
            continue
        y = _company_yoy(fin, t)
        best = None
        for lag in range(0, 5):
            pair = pd.concat([capex, y.shift(-lag)], axis=1).dropna()
            if len(pair) < 8:
                continue
            r = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
            if best is None or r > best[1]:
                best = (lag, r, pair)
        if best is None:
            continue
        lag, r, pair = best
        slope, icpt = np.polyfit(pair.iloc[:, 0], pair.iloc[:, 1], 1)
        n = len(pair)
        rows.append({"ticker": t, "name": info["name"], "layer": info["layer"], "best_lag_q": lag, "corr": round(r, 2),
                     "beta": round(float(slope), 2), "r2": round(r * r, 2), "n": n, "noise_band": round(2 / np.sqrt(n), 2),
                     "meaningful": bool(abs(r) > 2 / np.sqrt(n))})
    return pd.DataFrame(rows).sort_values("corr", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def ratio_series(fin: pd.DataFrame, num: str, den: str, tickers: list[str]) -> dict:
    """Per company and calendar quarter: 100 * metric num / metric den (e.g. capex/revenue, gross_profit/revenue)."""
    cg = chain_growth(fin)
    out = {}
    for t in tickers:
        a = cg[(cg["ticker"] == t) & (cg["metric"] == num)].set_index("quarter")["value_b"]
        b = cg[(cg["ticker"] == t) & (cg["metric"] == den)].set_index("quarter")["value_b"]
        r = (100 * a / b).dropna()
        r = r[(r.index >= "2019Q1") & (b.reindex(r.index) > 0)]
        if len(r) >= 4:
            out[t] = {"quarter": r.index.tolist(), "pct": r.round(1).tolist()}
    return out


def capex_intensity(fin: pd.DataFrame) -> dict:
    return ratio_series(fin, "capex", "revenue", [t for t, m in config.COMPANY_METRICS.items() if "capex" in m and "revenue" in m])


def gross_margins(fin: pd.DataFrame) -> dict:
    return ratio_series(fin, "gross_profit", "revenue", [t for t, m in config.COMPANY_METRICS.items() if "gross_profit" in m])


def revenue_race(fin: pd.DataFrame, base_q: str = "2019Q4") -> dict:
    """Trailing-twelve-month revenue per company, indexed to its TTM at base_q (= 100; or its first full TTM)."""
    cg = chain_growth(fin)
    out = {}
    for t in config.COMPANIES:
        g = cg[(cg["ticker"] == t) & (cg["metric"] == "revenue")].set_index("quarter")["value_b"]
        if g.empty:
            continue
        g.index = pd.PeriodIndex(g.index, freq="Q")
        g = g[~g.index.duplicated()].asfreq("Q")
        ttm = g.rolling(4).sum().dropna()
        ttm.index = ttm.index.astype(str)
        ttm = ttm[ttm.index >= base_q]
        if len(ttm) < 4:
            continue
        out[t] = {"quarter": ttm.index.tolist(), "index": (100 * ttm / ttm.iloc[0]).round(1).tolist(),
                  "ttm_b": ttm.round(2).tolist(), "base": ttm.index[0]}
    return out


def layer_totals(fin: pd.DataFrame, since: str = "2019Q1") -> dict:
    """TTM revenue summed per supply-chain layer, using only companies with data back to `since` (consistent set)."""
    race = revenue_race(fin, base_q="2019Q4")
    layers: dict[str, list] = {}
    for t, d in race.items():
        if d["base"] == "2019Q4":
            layers.setdefault(config.COMPANIES[t]["layer"], []).append(pd.Series(d["ttm_b"], index=d["quarter"]))
    out = {}
    for layer, ss in sorted(layers.items()):
        tot = pd.concat(ss, axis=1).dropna().sum(axis=1)
        yoy = (100 * (tot / tot.shift(4) - 1)).round(1)
        out[layer] = {"quarter": tot.index.tolist(), "ttm_b": tot.round(1).tolist(),
                      "yoy": [None if np.isnan(v) else v for v in yoy.tolist()], "companies": len(ss)}
    return out


def filing_feed(fin: pd.DataFrame, n: int = 25) -> list[dict]:
    """Most recent SEC filings in the dataset with their headline values (the 'what just got reported' feed)."""
    if fin.empty:
        return []
    cg = chain_growth(fin)
    latest = fin.sort_values("filed_date").drop_duplicates(["ticker", "metric"], keep="last")
    rows = []
    for r in latest.itertuples():
        g = cg[(cg["ticker"] == r.ticker) & (cg["metric"] == r.metric) & (cg["quarter"] == r.quarter)]
        rows.append({"filed": r.filed_date, "ticker": r.ticker, "name": config.COMPANIES.get(r.ticker, {}).get("name"),
                     "form": r.form, "metric": r.metric, "quarter": r.quarter, "value_b": round(r.value_usd / 1e9, 2),
                     "yoy": None if g.empty or g["yoy"].iloc[0] is None else g["yoy"].iloc[0]})
    return sorted(rows, key=lambda x: x["filed"], reverse=True)[:n]


def company_backtest(conn: sqlite3.Connection, ticker: str, metric: str = "revenue",
                     zone: str | None = None, cache: dict | None = None) -> pd.DataFrame:
    """Point-in-time nowcast of a company's YoY growth for calendar quarter T, decided 7 days after T ends.

    Models (each refit only on history known at the decision date):
      naive       growth_T = growth_{T-1}
      capex       growth_T ~ hyperscaler capex growth_{T-1}
      own+capex   growth_T ~ growth_{T-1} + capex growth_{T-1}
      grid        growth_T ~ growth_{T-1} + Northern Virginia baseload growth_T
    Scored against growth as FIRST reported."""
    zone = zone or config.PRIMARY_ZONE
    cache = {} if cache is None else cache          # shared across companies: capex history per decision date
    if "fin" not in cache:
        cache["fin"] = leadlag.fin_versions(conn)
        cache["grid"] = power.zone_quarterly(conn, zone)
    fin, grid = cache["fin"], cache["grid"]
    sub = fin[((fin["ticker"] == ticker) & (fin["metric"] == metric)) | (fin["metric"] == "capex")]
    if sub[sub["ticker"] == ticker].empty or grid.empty:
        return pd.DataFrame()
    first = sub.sort_values("filed_date").drop_duplicates(["ticker", "metric", "fiscal_year", "fiscal_q"], keep="first")
    truth = _company_yoy(first, ticker, metric).dropna()
    rows = []
    for T in truth.index:
        if T < "2020Q1":
            continue
        D = (pd.Period(T, freq="Q").end_time.normalize() + pd.Timedelta(days=leadlag.DECISION_LAG_DAYS)).strftime("%Y-%m-%d")
        own = _company_yoy(sub, ticker, metric, as_of=D)
        if ("cap", D) not in cache:
            cache[("cap", D)] = leadlag.series(fin[fin["metric"] == "capex"], as_of=D).set_index("quarter")["capex_yoy"]
        cap = cache[("cap", D)]
        g = grid[grid["known_date"] <= D].set_index("quarter")["overnight_yoy"]
        hist = pd.DataFrame({"y": own, "cap": cap}).join(g.rename("grid"), how="left")
        hist["y_prev"], hist["cap_prev"] = hist["y"].shift(1), hist["cap"].shift(1)
        hist = hist[hist.index < T]
        prev = str(pd.Period(T, freq="Q") - 1)
        x = {"y_prev": own.get(prev), "cap_prev": cap.get(prev), "grid": g.get(T)}
        rows.append({"quarter": T, "decision_date": D, "actual_yoy": round(float(truth[T]), 2),
                     "naive": None if x["y_prev"] is None or pd.isna(x["y_prev"]) else round(float(x["y_prev"]), 2),
                     "capex": leadlag._ols_predict(hist, ["cap_prev"], "y", x),
                     "own+capex": leadlag._ols_predict(hist, ["y_prev", "cap_prev"], "y", x),
                     "grid": leadlag._ols_predict(hist, ["y_prev", "grid"], "y", x)})
    out = pd.DataFrame(rows)
    for m in ("capex", "own+capex", "grid"):
        if m in out:
            out[m] = pd.to_numeric(out[m]).round(2)
    return out


MODELS = ["naive", "capex", "own+capex", "grid"]


def backtest_scoreboard(conn: sqlite3.Connection, tickers: list[str]) -> tuple[pd.DataFrame, dict]:
    """MAE per company and model on quarters where every model predicted; plus each company's backtest rows."""
    rows, detail, cache = [], {}, {}
    for t in tickers:
        bt = company_backtest(conn, t, cache=cache)
        if bt.empty:
            continue
        common = bt.dropna(subset=MODELS)
        if len(common) < 4:
            continue
        mae = {m: round(float((common[m] - common["actual_yoy"]).abs().mean()), 1) for m in MODELS}
        best = min(mae, key=mae.get)
        rows.append({"ticker": t, "name": config.COMPANIES[t]["name"], "quarters": len(common), **mae, "best": best,
                     "beats_naive": best != "naive"})
        detail[t] = bt
    return pd.DataFrame(rows), detail


def variance_flags(fin: pd.DataFrame, z: float = 2.0, min_hist: int = 8) -> pd.DataFrame:
    """Unusual quarters: growth ACCELERATION (change in YoY growth vs the previous quarter) that is more than
    `z` robust standard deviations from that company's own history. Robust = median and MAD, so one wild
    quarter doesn't hide the next. An FP&A-style "what moved unusually this quarter" screen."""
    cg = chain_growth(fin)
    rows = []
    for (t, m), g in cg.groupby(["ticker", "metric"]):
        if m == "gross_profit":
            continue
        s = g.set_index("quarter")["yoy"].astype(float).dropna()
        acc = s.diff().dropna()
        for i in range(min_hist, len(acc)):
            hist = acc.iloc[:i]
            med = hist.median()
            mad = (hist - med).abs().median() * 1.4826
            if not mad or np.isnan(mad):
                continue
            score = (acc.iloc[i] - med) / mad
            if abs(score) >= z:
                q = acc.index[i]
                rows.append({"ticker": t, "name": config.COMPANIES.get(t, {}).get("name"), "metric": m, "quarter": q,
                             "yoy": round(float(s[q]), 1), "prev_yoy": round(float(s.shift(1)[q]), 1),
                             "change_pp": round(float(acc.iloc[i]), 1), "z": round(float(score), 1),
                             "direction": "acceleration" if score > 0 else "deceleration"})
    return pd.DataFrame(rows).sort_values("quarter", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def upcoming_filings(fin: pd.DataFrame, today: str | None = None, horizon_days: int = 60) -> list[dict]:
    """Estimated next report date per company: the same calendar point one year after the corresponding filing
    (companies keep a steady annual rhythm: Q3 reported ~late October every year), falling back to last filing +
    the median gap between filings. Shows what's coming up in the next `horizon_days`."""
    today_ts = pd.Timestamp(today) if today else pd.Timestamp.now().normalize()
    out = []
    for t, g in fin.groupby("ticker"):
        dates = sorted(pd.to_datetime(g["filed_date"].unique()))
        if len(dates) < 5:
            continue
        cands = [d + pd.DateOffset(years=1) for d in dates if d + pd.DateOffset(years=1) > dates[-1] + pd.Timedelta(days=30)]
        nxt = min(cands) if cands else dates[-1] + pd.Timedelta(days=float(np.median(np.diff(dates)).astype("timedelta64[D]").astype(int)))
        if today_ts - pd.Timedelta(days=7) <= nxt <= today_ts + pd.Timedelta(days=horizon_days):
            out.append({"ticker": t, "name": config.COMPANIES.get(t, {}).get("name"), "expected": nxt.strftime("%Y-%m-%d"),
                        "last_filed": dates[-1].strftime("%Y-%m-%d"), "days_until": int((nxt - today_ts).days)})
    return sorted(out, key=lambda r: r["expected"])



def growth_regimes(monthly: pd.DataFrame, col: str = "normalized_mw", max_breaks: int = 2, min_seg: int = 12) -> dict:
    """When did growth speed up or slow down? Piecewise-linear trend with 0..max_breaks breakpoints
    (continuous segments, each >= min_seg months), chosen by BIC so extra breaks must earn their keep.
    Returns each segment's slope in MW per year and its % growth rate."""
    if monthly is None or monthly.empty or col not in monthly:
        return {}
    s = monthly.set_index("month")[col].dropna()
    n = len(s)
    if n < 2 * min_seg:
        return {}
    t = np.arange(n, dtype=float)
    y = s.to_numpy()

    def fit(breaks):
        X = np.column_stack([np.ones(n), t] + [np.clip(t - b, 0, None) for b in breaks])   # hinge terms: continuous
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        rss = float(((y - X @ beta) ** 2).sum())
        return rss, beta, X @ beta

    best = None
    cands = [()] + [(b,) for b in range(min_seg, n - min_seg)]
    if max_breaks >= 2:
        cands += [(a, b) for a in range(min_seg, n - 2 * min_seg) for b in range(a + min_seg, n - min_seg)]
    for br in cands:
        rss, beta, yhat = fit(br)
        k = 2 + 2 * len(br)                       # each break: a slope change + its location
        bic = n * np.log(rss / n) + k * np.log(n)
        if best is None or bic < best[0]:
            best = (bic, br, beta, yhat)
    _, br, beta, yhat = best
    edges = [0, *br, n - 1]
    segs = []
    for i in range(len(edges) - 1):
        slope = beta[1] + sum(beta[2 + j] for j in range(i))
        a, b = edges[i], edges[i + 1]
        mid_level = float(yhat[(a + b) // 2])
        segs.append({"from": s.index[a], "to": s.index[b], "mw_per_year": round(12 * float(slope)),
                     "pct_per_year": round(100 * 12 * float(slope) / mid_level, 1)})
    return {"breaks": [s.index[b] for b in br], "segments": segs, "fitted": np.round(yhat).tolist(), "month": s.index.tolist()}
