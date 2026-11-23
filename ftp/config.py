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

# The money trail: hyperscalers buy GPUs (capex) -> NVIDIA books revenue.
COMPANIES: dict[str, dict] = {
    "NVDA": {"cik": 1045810, "name": "NVIDIA", "role": "supplier"},
    "MSFT": {"cik": 789019, "name": "Microsoft", "role": "hyperscaler"},
    "GOOGL": {"cik": 1652044, "name": "Alphabet", "role": "hyperscaler"},
    "AMZN": {"cik": 1018724, "name": "Amazon", "role": "hyperscaler"},
    "META": {"cik": 1326801, "name": "Meta", "role": "hyperscaler"},
}
# Metric -> candidate us-gaap concepts, in priority order (companies tag the same idea differently).
METRIC_CONCEPTS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}
COMPANY_METRICS: dict[str, list[str]] = {
    "NVDA": ["revenue"],
    "MSFT": ["capex"], "GOOGL": ["capex"], "AMZN": ["capex"], "META": ["capex"],
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
    ("PJM", "DOM", "Dominion (Northern Virginia)", "America/New_York"),
]
# States with heavy data-center build-out; commercial-sector sales include data centers.
RETAIL_STATES: list[str] = ["VA", "TX", "OH", "AZ", "OR", "GA"]
RETAIL_SECTOR = "COM"
HOURLY_START = "2019-01-01"
