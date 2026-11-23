"""Daily temperature per grid zone (Open-Meteo historical archive, ERA5 reanalysis; free, no key).

Why: electricity demand swings with the weather (heating in winter, air conditioning in summer).
To see data centers, weather has to be taken out first. See ftp/analytics.weather_normalize.
"""
from __future__ import annotations

import pandas as pd

from ftp import config
from ftp.eia import session


def fetch_daily(zone: str, start: str, end: str, s=None) -> pd.DataFrame:
    lat, lon = config.ZONE_WEATHER_POINT[zone]
    tz = next(z[3] for z in config.GRID_ZONES if z[1] == zone)
    resp = (s or session()).get(config.WEATHER_URL, params={
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end, "timezone": tz,
        "daily": "temperature_2m_mean,temperature_2m_min,temperature_2m_max"}, timeout=config.REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    return clean(resp.json(), zone)


def clean(body: dict, zone: str) -> pd.DataFrame:
    d = body.get("daily") or {}
    df = pd.DataFrame({"zone": zone, "date": d.get("time", []), "tmean_c": d.get("temperature_2m_mean", []),
                       "tmin_c": d.get("temperature_2m_min", []), "tmax_c": d.get("temperature_2m_max", [])})
    return df.dropna(subset=["tmean_c"]).drop_duplicates(["zone", "date"], keep="last")


def degree_days(tmean_c: pd.Series, base: float = config.BALANCE_POINT_C) -> tuple[pd.Series, pd.Series]:
    """Heating and cooling degree days (°C·day) relative to the balance point."""
    return (base - tmean_c).clip(lower=0), (tmean_c - base).clip(lower=0)
