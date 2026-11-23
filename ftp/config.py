"""Configuration: companies, SEC concepts, grid zones, paths."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("FTP_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.getenv("FTP_DB", DATA_DIR / "follow_the_power.db"))
SCHEMA_PATH = ROOT / "sql" / "schema.sql"
REQUEST_TIMEOUT_S = 60

# ------------------------------------------------------------------ SEC EDGAR
# SEC's fair-access policy requires a descriptive User-Agent with contact info.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT") or "follow-the-power research autkarsh025@users.noreply.github.com"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# The money trail, layer by layer: cloud companies spend (capex) -> chips, memory, servers, networking
# -> power & cooling equipment -> data-center landlords -> the utilities and generators selling the power.
# "role" = hyperscaler marks the four companies summed into "hyperscaler capex".
L1, L2, L3, L4, L5, L6 = ("1 · Cloud buyers", "2 · Chips & memory", "3 · Servers & networking",
                          "4 · Power & cooling gear", "5 · Data-center landlords", "6 · Electricity")
COMPANIES: dict[str, dict] = {
    "MSFT": {"cik": 789019, "name": "Microsoft", "role": "hyperscaler", "layer": L1},
    "GOOGL": {"cik": 1652044, "name": "Alphabet", "role": "hyperscaler", "layer": L1},
    "AMZN": {"cik": 1018724, "name": "Amazon", "role": "hyperscaler", "layer": L1},
    "META": {"cik": 1326801, "name": "Meta", "role": "hyperscaler", "layer": L1},
    "ORCL": {"cik": 1341439, "name": "Oracle", "role": "cloud", "layer": L1},
    "CRWV": {"cik": 1769628, "name": "CoreWeave", "role": "cloud", "layer": L1},
    "NVDA": {"cik": 1045810, "name": "NVIDIA", "role": "supplier", "layer": L2},
    "AMD": {"cik": 2488, "name": "AMD", "role": "chips", "layer": L2},
    "AVGO": {"cik": 1730168, "name": "Broadcom", "role": "chips", "layer": L2},
    "MRVL": {"cik": 1835632, "name": "Marvell", "role": "chips", "layer": L2},
    "MU": {"cik": 723125, "name": "Micron", "role": "memory", "layer": L2},
    "INTC": {"cik": 50863, "name": "Intel", "role": "chips", "layer": L2},
    "SMCI": {"cik": 1375365, "name": "Super Micro", "role": "servers", "layer": L3},
    "DELL": {"cik": 1571996, "name": "Dell", "role": "servers", "layer": L3},
    "ANET": {"cik": 1596532, "name": "Arista Networks", "role": "networking", "layer": L3},
    "VRT": {"cik": 1674101, "name": "Vertiv", "role": "power_gear", "layer": L4},
    "ETN": {"cik": 1551182, "name": "Eaton", "role": "power_gear", "layer": L4},
    "GEV": {"cik": 1996810, "name": "GE Vernova", "role": "power_gear", "layer": L4},
    "EQIX": {"cik": 1101239, "name": "Equinix", "role": "datacenter_reit", "layer": L5},
    "DLR": {"cik": 1297996, "name": "Digital Realty", "role": "datacenter_reit", "layer": L5},
    "D": {"cik": 715957, "name": "Dominion Energy", "role": "utility", "layer": L6},
    "AEP": {"cik": 4904, "name": "American Electric Power", "role": "utility", "layer": L6},
    "SO": {"cik": 92122, "name": "Southern Company", "role": "utility", "layer": L6},
    "NEE": {"cik": 753308, "name": "NextEra Energy", "role": "utility", "layer": L6},
    "CEG": {"cik": 1868275, "name": "Constellation", "role": "generator", "layer": L6},
    "VST": {"cik": 1692819, "name": "Vistra", "role": "generator", "layer": L6},
}
# Metric -> candidate us-gaap concepts, in priority order (companies tag the same idea differently,
# and switch tags over time; sec.quarterly merges them per quarter).
METRIC_CONCEPTS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "RegulatedAndUnregulatedOperatingRevenue"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements", "PaymentsToAcquireAndDevelopRealEstate", "PaymentsToDevelopRealEstateAssets"],
    "gross_profit": ["GrossProfit"],
}
_CLOUD, _CHIP, _GEAR, _REIT, _UTIL = (["revenue", "capex"], ["revenue", "gross_profit"], ["revenue"],
                                      ["revenue", "capex"], ["revenue", "capex"])
COMPANY_METRICS: dict[str, list[str]] = {
    **{t: _CLOUD for t in ("MSFT", "GOOGL", "AMZN", "META", "ORCL", "CRWV")},
    **{t: _CHIP for t in ("NVDA", "AMD", "AVGO", "MRVL", "MU", "INTC", "SMCI", "DELL", "ANET")},
    **{t: _GEAR for t in ("VRT", "ETN", "GEV", "CEG", "VST")},
    **{t: _REIT for t in ("EQIX", "DLR")},
    **{t: _UTIL for t in ("D", "AEP", "SO", "NEE")},
}
FIRST_FISCAL_YEAR = 2019   # enough history to see the AI build-out start

# ------------------------------------------------------------------ EIA (grid)
EIA_API_KEY_ENV = "EIA_API_KEY"
EIA_BASE_URL = "https://api.eia.gov/v2"
SUBREGION_ROUTE = "electricity/rto/region-sub-ba-data/data/"   # hourly demand by grid sub-zone
RETAIL_ROUTE = "electricity/retail-sales/data/"                # monthly sales by state & sector
PAGE_SIZE = 5000

# Data-center hotspots inside the grid: (parent BA, sub-zone code, label, IANA time zone)
GRID_ZONES: list[tuple[str, str, str, str]] = [
    ("PJM", "DOM", "Northern Virginia (PJM Dominion)", "America/New_York"),
    ("PJM", "AEP", "Ohio & central US (PJM AEP)", "America/New_York"),
    ("PJM", "CE", "Chicago (PJM ComEd)", "America/Chicago"),
    ("ERCO", "NCEN", "Dallas-Fort Worth (ERCOT North Central)", "America/Chicago"),
    ("ERCO", "FWES", "West Texas (ERCOT Far West)", "America/Chicago"),
]
PRIMARY_ZONE = "DOM"
# A representative weather point per zone (lat, lon) for temperature normalization.
ZONE_WEATHER_POINT: dict[str, tuple[float, float]] = {
    "DOM": (39.04, -77.49),    # Ashburn, VA ("Data Center Alley")
    "AEP": (39.96, -83.00),    # Columbus, OH
    "CE": (41.88, -87.63),     # Chicago, IL
    "NCEN": (32.78, -96.80),   # Dallas, TX
    "FWES": (31.99, -102.08),  # Midland, TX
}
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"   # free, no key (ERA5 reanalysis)
BALANCE_POINT_C = 18.0       # degree-day base: below = heating, above = cooling
# States with heavy data-center build-out; commercial-sector sales include data centers.
RETAIL_STATES: list[str] = ["VA", "TX", "OH", "AZ", "OR", "GA"]
RETAIL_SECTOR = "COM"
HOURLY_START = "2019-01-01"
