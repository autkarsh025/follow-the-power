"""EIA: hourly demand in data-center hotspot grid zones + monthly state electricity sales."""
from __future__ import annotations

import logging
import os

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ftp import config

log = logging.getLogger(__name__)


def session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2,
                                                      status_forcelist=(429, 500, 502, 503, 504))))
    return s


def _key() -> str:
    key = os.getenv(config.EIA_API_KEY_ENV)
    if not key:
        raise RuntimeError(f"Set {config.EIA_API_KEY_ENV} (free key: https://www.eia.gov/opendata/)")
    return key


def fetch(route: str, params: dict, s: requests.Session | None = None) -> list[dict]:
    """Fetch all rows for an EIA v2 route, following pagination."""
    s = s or session()
    params = {**params, "api_key": _key(), "length": config.PAGE_SIZE}
    rows, offset = [], 0
    while True:
        params["offset"] = offset
        resp = s.get(f"{config.EIA_BASE_URL}/{route}", params=params, timeout=config.REQUEST_TIMEOUT_S)
        resp.raise_for_status()
        body = resp.json().get("response", {})
        page = body.get("data", [])
        rows.extend(page)
        if not page or len(rows) >= int(body.get("total", 0)):
            return rows
        offset += len(page)


def zone_hourly(parent: str, zone: str, start: str, end: str, s=None) -> pd.DataFrame:
    rows = fetch(config.SUBREGION_ROUTE, {
        "frequency": "hourly", "data[0]": "value", "facets[parent][]": parent, "facets[subba][]": zone,
        "start": f"{start}T00", "end": f"{end}T23", "sort[0][column]": "period", "sort[0][direction]": "asc",
    }, s)
    return clean_zone(rows, parent, zone)


def clean_zone(rows: list[dict], parent: str, zone: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["zone", "parent", "period_utc", "demand_mwh"])
    ts = pd.to_datetime(df["period"], format="%Y-%m-%dT%H", errors="coerce", utc=True)
    out = pd.DataFrame({"zone": zone, "parent": parent, "period_utc": ts.dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "demand_mwh": pd.to_numeric(df["value"], errors="coerce").astype("float64")})
    out = out[ts.notna().values]
    # Impossible values are dropped (logged), not "fixed": demand can't be <= 0.
    bad = out["demand_mwh"] <= 0
    if bad.any():
        log.warning("%s: dropping %d non-positive demand values", zone, int(bad.sum()))
    return out[~bad].drop_duplicates(["zone", "period_utc"], keep="last")


def state_sales(states: list[str], sector: str, s=None) -> pd.DataFrame:
    rows = fetch(config.RETAIL_ROUTE, {
        "frequency": "monthly", "data[0]": "sales", "facets[stateid][]": states, "facets[sectorid][]": sector,
        "start": config.HOURLY_START[:7], "sort[0][column]": "period", "sort[0][direction]": "asc",
    }, s)
    return clean_sales(rows)


def clean_sales(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["state", "sector", "month", "sales_gwh"])
    return pd.DataFrame({"state": df["stateid"], "sector": df["sectorid"], "month": df["period"],
                         "sales_gwh": pd.to_numeric(df["sales"], errors="coerce")}).drop_duplicates(
        ["state", "sector", "month"], keep="last")
