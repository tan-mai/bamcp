# TM - #KL-SQL - Kline storage SQLite
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

MIGRATIONS: list[str] = [
    """
CREATE TABLE IF NOT EXISTS klines (
  id               TEXT    NOT NULL PRIMARY KEY,
  symbol           TEXT    NOT NULL,
  tf               TEXT    NOT NULL,
  open_time        INTEGER NOT NULL,
  close_time       INTEGER NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume           REAL NOT NULL,
  quote_volume     REAL,
  trades           INTEGER,
  taker_buy_volume REAL,
  UNIQUE (symbol, tf, open_time)
);
""",
    # TM - #M5-FETCH - Per-timeframe schedules + M5
    """
CREATE TABLE IF NOT EXISTS fetch_schedules (
  id               TEXT    NOT NULL PRIMARY KEY,
  name             TEXT    NOT NULL UNIQUE,
  timeframe        TEXT    NOT NULL,
  symbols          TEXT,
  cron             TEXT    NOT NULL,
  enabled          INTEGER NOT NULL DEFAULT 1,
  overlap_candles  INTEGER NOT NULL DEFAULT 3,
  last_run_at      INTEGER,
  last_status      TEXT,
  last_error       TEXT,
  last_duration_ms INTEGER,
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);
""",
    # TM - #ENTRY-ALERT - Entry setups & alerts
    """
CREATE TABLE IF NOT EXISTS setups (
  id               TEXT NOT NULL PRIMARY KEY,
  code             TEXT NOT NULL UNIQUE,
  symbol           TEXT NOT NULL,
  bias_date        TEXT NOT NULL,
  direction        TEXT NOT NULL,
  status           TEXT NOT NULL,
  zone_low         REAL NOT NULL,
  zone_high        REAL NOT NULL,
  trigger_type     TEXT NOT NULL,
  trigger_params   TEXT,
  confirm_type     TEXT,
  confirm_level    REAL,
  entry            REAL NOT NULL,
  stop             REAL NOT NULL,
  target           REAL NOT NULL,
  max_chase        REAL,
  invalidate_level REAL NOT NULL,
  m15_filter       INTEGER NOT NULL DEFAULT 1,
  expires_at       INTEGER NOT NULL,
  tr_high REAL, tr_low REAL, tr_since INTEGER,
  state_data       TEXT,
  note             TEXT,
  created_by       TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS setup_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  setup_id     TEXT,
  at           INTEGER NOT NULL,
  event        TEXT NOT NULL,
  from_status  TEXT, to_status TEXT,
  tf TEXT, candle_open_time INTEGER, price REAL,
  details      TEXT,
  notified     INTEGER NOT NULL DEFAULT 0,
  notify_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_setups_active ON setups(symbol, status);
CREATE INDEX IF NOT EXISTS ix_events_setup ON setup_events(setup_id, at);
""",
]

_MIGRATED: set[str] = set()
_LOCK = threading.Lock()


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def migrate(path: Path | str) -> int:
    key = str(Path(path).resolve())
    with _LOCK:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = int(row["v"] or 0) if row else 0
            for version, ddl in enumerate(MIGRATIONS[current:], start=current + 1):
                with conn:
                    for statement in [s for s in ddl.split(";") if s.strip()]:
                        conn.execute(statement)
                    conn.execute("DELETE FROM schema_version")
                    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                current = version
        _MIGRATED.add(key)
        return current


def ensure(path: Path | str) -> None:
    if str(Path(path).resolve()) not in _MIGRATED:
        migrate(path)
