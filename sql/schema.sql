-- follow-the-power schema (SQLite). Timestamps in UTC. Financial facts keep WHEN they became public.

-- Quarterly financial facts, point-in-time: one row per (company, metric, quarter, filing).
-- A restated number gets a new row with a later filed_date, so we can always ask
-- "what was known on date X?" without peeking at the future.
CREATE TABLE IF NOT EXISTS fin_quarterly (
    ticker        TEXT NOT NULL,
    metric        TEXT NOT NULL,          -- 'revenue' / 'capex'
    fiscal_year   INTEGER NOT NULL,
    fiscal_q      INTEGER NOT NULL,       -- 1..4
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    value_usd     REAL NOT NULL,
    filed_date    TEXT NOT NULL,          -- first public date of THIS value
    form          TEXT NOT NULL,          -- 10-Q / 10-K
    accession     TEXT,
    concept       TEXT NOT NULL,          -- which us-gaap tag it came from
    derived       INTEGER NOT NULL DEFAULT 0,  -- 1 = computed (e.g. Q4 = FY - 9M YTD)
    PRIMARY KEY (ticker, metric, fiscal_year, fiscal_q, filed_date)
);

-- Hourly demand in data-center hotspot sub-zones (e.g. PJM's Dominion zone).
CREATE TABLE IF NOT EXISTS zone_hourly (
    zone        TEXT NOT NULL,            -- 'DOM'
    parent      TEXT NOT NULL,            -- 'PJM'
    period_utc  TEXT NOT NULL,
    demand_mwh  REAL,
    PRIMARY KEY (zone, period_utc)
);

-- Monthly retail electricity sales by state and sector (commercial includes data centers).
CREATE TABLE IF NOT EXISTS state_monthly_sales (
    state        TEXT NOT NULL,
    sector       TEXT NOT NULL,
    month        TEXT NOT NULL,           -- 'YYYY-MM'
    sales_gwh    REAL,                    -- EIA reports million kWh = GWh
    PRIMARY KEY (state, sector, month)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    step          TEXT NOT NULL,
    rows_loaded   INTEGER DEFAULT 0,
    status        TEXT NOT NULL,
    error_message TEXT
);

-- Daily temperature at a representative point per zone (Open-Meteo / ERA5), for weather normalization.
CREATE TABLE IF NOT EXISTS zone_weather_daily (
    zone     TEXT NOT NULL,
    date     TEXT NOT NULL,                -- local date 'YYYY-MM-DD'
    tmean_c  REAL,
    tmin_c   REAL,
    tmax_c   REAL,
    PRIMARY KEY (zone, date)
);

-- Results of automated data-quality checks, one row per check per pipeline run.
CREATE TABLE IF NOT EXISTS quality_checks (
    run_at    TEXT NOT NULL,
    check_id  TEXT NOT NULL,
    subject   TEXT NOT NULL,               -- zone / ticker / table the check is about
    severity  TEXT NOT NULL,               -- 'critical' fails the run; 'warn' is reported
    passed    INTEGER NOT NULL,
    value     REAL,
    detail    TEXT,
    PRIMARY KEY (run_at, check_id, subject)
);
