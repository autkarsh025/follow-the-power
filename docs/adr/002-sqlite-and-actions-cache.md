# 002: SQLite + GitHub Actions cache

**Context.** ~350k hourly rows, ~1.4k financial facts and ~5k text passages; one daily writer; free hosting.

**Decision.** A single SQLite file, restored from and saved to the Actions cache each run; incremental loads with a
revision window; upserts on natural keys. The public outputs (dashboard, CSVs) are committed to the repo.

**Consequences.** Zero infrastructure and zero cost; reproducible locally. If the cache is evicted, the next run
backfills automatically (incremental logic falls back to the start date). At larger scale: Parquet partitioned by
zone/month + DuckDB, or Postgres; the SQL and upsert pattern carry over.
