# TM - #KL-SQL - Kline storage SQLite
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import sqlite_db

BAR_KEYS = ("open_time", "open", "high", "low", "close", "volume", "close_time")

UPSERT_SQL = """
INSERT INTO klines (id, symbol, tf, open_time, close_time, open, high, low, close,
                    volume, quote_volume, trades, taker_buy_volume)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, tf, open_time) DO UPDATE SET
  close_time = excluded.close_time,
  open = excluded.open,
  high = excluded.high,
  low = excluded.low,
  close = excluded.close,
  volume = excluded.volume,
  quote_volume = excluded.quote_volume,
  trades = excluded.trades,
  taker_buy_volume = excluded.taker_buy_volume
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _tail(bars: list[dict[str, Any]], limit: int | None,
          end_time: int | None) -> list[dict[str, Any]]:
    if end_time is not None:
        bars = [b for b in bars if int(b["open_time"]) <= int(end_time)]
    if limit:
        bars = bars[-int(limit):]
    return bars


def _coverage_row(symbol: str, tf: str, count: int, first: int | None,
                  last: int | None, updated_ms: int | None) -> dict[str, Any]:
    return {"symbol": symbol, "tf": tf, "count": int(count),
            "first_open_time": first, "last_open_time": last, "updated_ms": updated_ms}


class IKlineStore(ABC):
    kind = ""

    @abstractmethod
    def get_klines(self, symbol: str, tf: str, limit: int | None = None,
                   end_time: int | None = None) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def upsert(self, symbol: str, tf: str, klines: list[Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_latest_open_time(self, symbol: str, tf: str) -> int | None:
        ...

    @abstractmethod
    def get_coverage(self, symbol: str | None = None,
                     tf: str | None = None) -> list[dict[str, Any]]:
        ...

    def written_at(self, symbol: str, tf: str) -> int | None:
        return None


class JsonKlineStore(IKlineStore):
    kind = "json"

    def __init__(self, root: Path, *, path_fn: Callable[[str, str], Path],
                 normalize: Callable[[Any], list[dict[str, Any]]],
                 merge: Callable[[list[Any], list[Any], int | None], list[Any]],
                 retention: Callable[[str], int | None],
                 read_json: Callable[[Path, Any], Any],
                 write_json: Callable[[Path, Any], None],
                 timeframes: Callable[[], list[str]]) -> None:
        self.root = Path(root)
        self.path_fn = path_fn
        self.normalize = normalize
        self.merge = merge
        self.retention = retention
        self.read_json = read_json
        self.write_json = write_json
        self.timeframes = timeframes

    def _path(self, symbol: str, tf: str) -> Path:
        return self.path_fn(symbol.upper(), tf)

    def raw_rows(self, symbol: str, tf: str) -> list[Any]:
        path = self._path(symbol, tf)
        if not path.exists():
            return []
        raw = self.read_json(path, [])
        if isinstance(raw, list) and all(isinstance(r, (list, tuple)) for r in raw):
            return raw
        return self.normalize(raw)

    def get_klines(self, symbol: str, tf: str, limit: int | None = None,
                   end_time: int | None = None) -> list[dict[str, Any]]:
        path = self._path(symbol, tf)
        if not path.exists():
            return []
        return _tail(self.normalize(self.read_json(path, [])), limit, end_time)

    def upsert(self, symbol: str, tf: str, klines: list[Any]) -> dict[str, Any]:
        path = self._path(symbol, tf)
        existing = self.read_json(path, [])
        if not isinstance(existing, list):
            existing = []
        merged = self.merge(existing, klines, self.retention(tf))
        self.write_json(path, merged)
        return {"written": len(klines), "total": len(merged)}

    def get_latest_open_time(self, symbol: str, tf: str) -> int | None:
        bars = self.get_klines(symbol, tf)
        return int(bars[-1]["open_time"]) if bars else None

    def get_coverage(self, symbol: str | None = None,
                     tf: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            symbols = [symbol.upper()]
        elif self.root.is_dir():
            symbols = sorted(p.name for p in self.root.iterdir() if p.is_dir())
        else:
            symbols = []
        frames = [tf] if tf else list(self.timeframes())
        out: list[dict[str, Any]] = []
        for sym in symbols:
            for frame in frames:
                path = self._path(sym, frame)
                if not path.is_file():
                    continue
                try:
                    bars = self.normalize(self.read_json(path, []))
                except (OSError, ValueError):
                    continue
                out.append(_coverage_row(
                    sym, frame, len(bars),
                    int(bars[0]["open_time"]) if bars else None,
                    int(bars[-1]["open_time"]) if bars else None,
                    int(path.stat().st_mtime * 1000)))
        return out

    def written_at(self, symbol: str, tf: str) -> int | None:
        path = self._path(symbol, tf)
        try:
            return int(path.stat().st_mtime * 1000)
        except OSError:
            return None


class SqliteKlineStore(IKlineStore):
    kind = "sqlite"

    def __init__(self, path: Path, *, span_ms: Callable[[str], int],
                 now_ms: Callable[[], int] | None = None) -> None:
        self.path = Path(path)
        self.span_ms = span_ms
        self.now_ms = now_ms or _now_ms
        sqlite_db.migrate(self.path)

    def _row(self, tf: str, item: Any) -> tuple:
        if isinstance(item, (list, tuple)):
            open_time = int(item[0])
            close_time = int(item[6]) if len(item) > 6 and item[6] else 0
            values = [float(item[i]) for i in range(1, 6)]
            quote = float(item[7]) if len(item) > 7 and item[7] is not None else None
            trades = int(item[8]) if len(item) > 8 and item[8] is not None else None
            taker = float(item[9]) if len(item) > 9 and item[9] is not None else None
        else:
            open_time = int(item["open_time"])
            close_time = int(item.get("close_time") or 0)
            values = [float(item[k]) for k in ("open", "high", "low", "close")]
            values.append(float(item.get("volume") or 0.0))
            quote = item.get("quote_volume")
            trades = item.get("trades")
            taker = item.get("taker_buy_volume")
        if not close_time:
            close_time = open_time + self.span_ms(tf) - 1
        return (open_time, close_time, *values, quote, trades, taker)

    def get_klines(self, symbol: str, tf: str, limit: int | None = None,
                   end_time: int | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT open_time, open, high, low, close, volume, close_time FROM klines "
               "WHERE symbol = ? AND tf = ?")
        params: list[Any] = [symbol.upper(), tf]
        if end_time is not None:
            sql += " AND open_time <= ?"
            params.append(int(end_time))
        if limit:
            sql += " ORDER BY open_time DESC LIMIT ?"
            params.append(int(limit))
        else:
            sql += " ORDER BY open_time"
        with closing(sqlite_db.connect(self.path)) as conn:
            rows = conn.execute(sql, params).fetchall()
        if limit:
            rows = rows[::-1]
        return [{k: (int(r[k]) if k in ("open_time", "close_time") else float(r[k]))
                 for k in BAR_KEYS} for r in rows]

    def upsert(self, symbol: str, tf: str, klines: list[Any]) -> dict[str, Any]:
        sym = symbol.upper()
        now = self.now_ms()
        rows: list[tuple] = []
        skipped = 0
        for item in klines:
            row = self._row(tf, item)
            if row[1] > now:
                skipped += 1
                continue
            rows.append((str(uuid.uuid4()), sym, tf, *row))
        with closing(sqlite_db.connect(self.path)) as conn:
            with conn:
                conn.executemany(UPSERT_SQL, rows)
            total = conn.execute("SELECT COUNT(*) FROM klines WHERE symbol = ? AND tf = ?",
                                 (sym, tf)).fetchone()[0]
        return {"written": len(rows), "skipped_forming": skipped, "total": int(total)}

    def get_latest_open_time(self, symbol: str, tf: str) -> int | None:
        with closing(sqlite_db.connect(self.path)) as conn:
            row = conn.execute("SELECT MAX(open_time) FROM klines WHERE symbol = ? AND tf = ?",
                               (symbol.upper(), tf)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def get_coverage(self, symbol: str | None = None,
                     tf: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT symbol, tf, COUNT(*) AS n, MIN(open_time) AS first, "
               "MAX(open_time) AS last, MAX(close_time) AS closed FROM klines")
        where, params = [], []
        if symbol:
            where.append("symbol = ?")
            params.append(symbol.upper())
        if tf:
            where.append("tf = ?")
            params.append(tf)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY symbol, tf ORDER BY symbol, tf"
        with closing(sqlite_db.connect(self.path)) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_coverage_row(r["symbol"], r["tf"], r["n"], r["first"], r["last"], r["closed"])
                for r in rows]


def import_json(src: JsonKlineStore, dst: SqliteKlineStore) -> dict[str, Any]:
    details = []
    for cov in src.get_coverage():
        rows = src.raw_rows(cov["symbol"], cov["tf"])
        result = dst.upsert(cov["symbol"], cov["tf"], rows)
        details.append({"symbol": cov["symbol"], "tf": cov["tf"], "json_rows": len(rows),
                        "written": result["written"],
                        "skipped_forming": result["skipped_forming"],
                        "sqlite_total": result["total"]})
    return {"pairs": len(details), "rows_written": sum(d["written"] for d in details),
            "details": details}


def verify(src: IKlineStore, dst: IKlineStore, *, span_ms: Callable[[str], int],
           now_ms: int | None = None, max_samples: int = 5) -> dict[str, Any]:
    now = int(now_ms if now_ms is not None else _now_ms())
    pairs = sorted({(c["symbol"], c["tf"]) for c in src.get_coverage()}
                   | {(c["symbol"], c["tf"]) for c in dst.get_coverage()})
    report = []
    total = 0
    for sym, tf in pairs:
        span = span_ms(tf)
        left = [b for b in src.get_klines(sym, tf)
                if (b.get("close_time") or int(b["open_time"]) + span - 1) <= now]
        right = dst.get_klines(sym, tf)
        a = {int(b["open_time"]): b for b in left}
        b = {int(x["open_time"]): x for x in right}
        only_src = sorted(set(a) - set(b))
        only_dst = sorted(set(b) - set(a))
        diff = [t for t in sorted(set(a) & set(b))
                if any(float(a[t][k]) != float(b[t][k])
                       for k in ("open", "high", "low", "close", "volume"))]
        row = {
            "symbol": sym, "tf": tf,
            "count": [len(left), len(right)],
            "first_open_time": [left[0]["open_time"] if left else None,
                                right[0]["open_time"] if right else None],
            "last_open_time": [left[-1]["open_time"] if left else None,
                               right[-1]["open_time"] if right else None],
            "missing_in_dst": len(only_src),
            "missing_in_src": len(only_dst),
            "ohlcv_mismatch": len(diff),
        }
        mismatches = len(only_src) + len(only_dst) + len(diff)
        if row["count"][0] != row["count"][1]:
            mismatches = max(mismatches, 1)
        if mismatches:
            row["samples"] = {
                "missing_in_dst": only_src[:max_samples],
                "missing_in_src": only_dst[:max_samples],
                "ohlcv": [{"open_time": t,
                           src.kind: {k: a[t][k] for k in ("open", "high", "low", "close", "volume")},
                           dst.kind: {k: b[t][k] for k in ("open", "high", "low", "close", "volume")}}
                          for t in diff[:max_samples]],
            }
        row["mismatches"] = mismatches
        total += mismatches
        report.append(row)
    return {"ok": total == 0, "mismatches": total, "pairs": report,
            "columns": [src.kind, dst.kind], "closed_before_ms": now}
