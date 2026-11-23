# follow-the-power open dataset

Refreshed daily by GitHub Actions. All files are CSV (UTF-8, comma-separated). Times are UTC unless noted.

| File | One row per | Columns |
|---|---|---|
| `fin_quarterly.csv` | company × metric × fiscal quarter × **filing** (point-in-time: restatements are extra rows) | ticker, metric (revenue/capex/gross_profit), fiscal_year, fiscal_q, calendar quarter, period_start, period_end, value_usd, filed_date (first public date of this value), form, accession, concept (XBRL tag), derived (1 = computed, e.g. Q4 = FY − 9M) |
| `fin_latest.csv` | company × metric × calendar quarter (latest version) | ticker, name, layer, metric, quarter, value_b (USD billions), yoy (%) |
| `zone_monthly.csv` | grid zone × month | zone, month (local), overnight_mw (median 1–5 AM), peak_mw (median daily max), flatness, baseload_yoy (%) |
| `zone_weather_normalized.csv` | grid zone × month | zone, month, actual_mw, normalized_mw (weather removed), weather_part_mw, normalized_yoy, actual_yoy, tmean_c, hdd, cdd |
| `zone_daily.csv` | grid zone × local day | zone, date, overnight_mw (median 1–5 AM), normalized_mw, deweathered_mw (weather & weekday removed), tmean_c |
| `state_sales.csv` | state × month | state, sector (COM = commercial), month, sales_gwh |
| `history.csv` | run date × metric | run_date, metric (headline KPIs: capex, NVIDIA revenue, weather-adjusted zone growth, row counts), value |
| `schema.json` | the data contract | expected columns and types for every file; validated on each export |
| `quality_checks.csv` | check × subject (latest run) | check_id, subject, severity, passed, value, detail |

Sources: SEC EDGAR XBRL (companyfacts API); U.S. EIA (EIA-930 hourly subregion demand; electric power retail sales);
Open-Meteo historical weather (ERA5 reanalysis). Not investment advice.
