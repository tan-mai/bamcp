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
