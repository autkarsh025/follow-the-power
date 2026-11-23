"""SQLite helpers: idempotent upserts and run logging."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ftp import config


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(config.SCHEMA_PATH.read_text())
    return conn


def upsert(conn: sqlite3.Connection, table: str, df: pd.DataFrame, keys: list[str]) -> int:
    """INSERT ... ON CONFLICT(keys) DO UPDATE: re-running a load never duplicates rows."""
    if df is None or df.empty:
        return 0
    cols = list(df.columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in keys) or f"{keys[0]} = excluded.{keys[0]}"
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
           f"ON CONFLICT({', '.join(keys)}) DO UPDATE SET {updates}")
    clean = df[cols].astype(object).where(df[cols].notna(), None)
    with conn:
        conn.executemany(sql, list(clean.itertuples(index=False, name=None)))
    return len(df)


def log_run(conn: sqlite3.Connection, step: str, rows: int, status: str, error: str | None = None) -> None:
    with conn:
        conn.execute("INSERT INTO pipeline_runs (started_at, finished_at, step, rows_loaded, status, error_message) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (now(), now(), step, rows, status, error))
