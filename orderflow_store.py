# TM - #BOOKMAP - Order flow Bookmap
"""orderflow.db: file SQLite rieng de collector ghi lien tuc khong tranh khoa voi DB kline.

Mot writer (collector, hoac job backfill khi ghi de mot khoang ngay), nhieu reader (server). WAL +
synchronous=NORMAL; auto_vacuum=INCREMENTAL dat luc tao file, job don du lieu chay incremental_vacuum.
Khoa chinh GUID (TEXT) theo convention BAMCP. ts la epoch ms UTC.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
import zlib
from array import array
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

DAY_MS = 86_400_000

# Bieu thuc SQL sinh GUID dang uuid4 cho cac lenh INSERT ... SELECT (gop footprint 5s -> 60s)
SQL_GUID = ("lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)), 2) || '-' "
            "|| substr('89ab', 1 + (abs(random()) % 4), 1) || substr(hex(randomblob(2)), 2) || '-' "
            "|| hex(randomblob(6)))")

MIGRATIONS: list[str] = [
    """
CREATE TABLE IF NOT EXISTS ob_snapshots (
  id          TEXT    NOT NULL PRIMARY KEY,
  symbol      TEXT    NOT NULL,
  market      TEXT    NOT NULL,
  ts          INTEGER NOT NULL,
  mid         REAL    NOT NULL,
  best_bid    REAL    NOT NULL,
  best_ask    REAL    NOT NULL,
  bucket_size REAL    NOT NULL,
  base_price  REAL    NOT NULL,
  n_buckets   INTEGER NOT NULL,
  bids        BLOB    NOT NULL,
  asks        BLOB    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ob_snapshots ON ob_snapshots(symbol, ts);
CREATE TABLE IF NOT EXISTS trade_footprint (
  id           TEXT    NOT NULL PRIMARY KEY,
  symbol       TEXT    NOT NULL,
  res_sec      INTEGER NOT NULL,
  ts           INTEGER NOT NULL,
  price_bucket REAL    NOT NULL,
  buy_vol      REAL    NOT NULL DEFAULT 0,
  sell_vol     REAL    NOT NULL DEFAULT 0,
  buy_cnt      INTEGER NOT NULL DEFAULT 0,
  sell_cnt     INTEGER NOT NULL DEFAULT 0,
  UNIQUE (symbol, res_sec, ts, price_bucket)
);
CREATE INDEX IF NOT EXISTS ix_footprint_ts ON trade_footprint(symbol, ts);
CREATE TABLE IF NOT EXISTS big_trades (
  id           TEXT    NOT NULL PRIMARY KEY,
  symbol       TEXT    NOT NULL,
  ts           INTEGER NOT NULL,
  price        REAL    NOT NULL,
  qty          REAL    NOT NULL,
  side         TEXT    NOT NULL,
  n_trades     INTEGER NOT NULL,
  first_agg_id INTEGER NOT NULL,
  last_agg_id  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_big_trades ON big_trades(symbol, ts);
CREATE UNIQUE INDEX IF NOT EXISTS ux_big_trades_agg ON big_trades(symbol, first_agg_id);
CREATE TABLE IF NOT EXISTS orderflow_gaps (
  id       TEXT    NOT NULL PRIMARY KEY,
  symbol   TEXT    NOT NULL,
  kind     TEXT    NOT NULL,
  start_ts INTEGER NOT NULL,
  end_ts   INTEGER,
  reason   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orderflow_gaps ON orderflow_gaps(symbol, start_ts);
CREATE TABLE IF NOT EXISTS orderflow_status (
  symbol           TEXT    NOT NULL PRIMARY KEY,
  connected        INTEGER NOT NULL DEFAULT 0,
  last_event_ts    INTEGER,
  last_snapshot_ts INTEGER,
  last_agg_id      INTEGER,
  book_levels      INTEGER NOT NULL DEFAULT 0,
  resyncs_today    INTEGER NOT NULL DEFAULT 0,
  updated_at       INTEGER NOT NULL,
  market           TEXT,
  trades_since_ts  INTEGER,
  book_since_ts    INTEGER,
  resync_day       TEXT,
  last_error       TEXT,
  pid              INTEGER,
  book_synced      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orderflow_jobs (
  id          TEXT    NOT NULL PRIMARY KEY,
  kind        TEXT    NOT NULL,
  symbol      TEXT    NOT NULL,
  from_date   TEXT    NOT NULL,
  to_date     TEXT    NOT NULL,
  status      TEXT    NOT NULL,
  progress    REAL    NOT NULL DEFAULT 0,
  message     TEXT,
  result      TEXT,
  pid         INTEGER,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  finished_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_orderflow_jobs ON orderflow_jobs(created_at);
""",
    # Snapshot gop san theo 10s / 60s / 300s (trung binh cac snapshot trong cua so, kem n_snapshots de tinh trung
    # binh co trong so). Heatmap khoang dai doc bang nay thay vi giai nen hang chuc nghin snapshot 2 giay.
    """
CREATE TABLE IF NOT EXISTS ob_snapshots_agg (
  id          TEXT    NOT NULL PRIMARY KEY,
  symbol      TEXT    NOT NULL,
  market      TEXT    NOT NULL,
  res_sec     INTEGER NOT NULL,
  ts          INTEGER NOT NULL,
  n_snapshots INTEGER NOT NULL,
  mid         REAL    NOT NULL,
  best_bid    REAL    NOT NULL,
  best_ask    REAL    NOT NULL,
  bucket_size REAL    NOT NULL,
  base_price  REAL    NOT NULL,
  n_buckets   INTEGER NOT NULL,
  bids        BLOB    NOT NULL,
  asks        BLOB    NOT NULL,
  UNIQUE (symbol, res_sec, ts)
);
""",
]

_MIGRATED: set[str] = set()
_LOCK = threading.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def guid() -> str:
    return str(uuid.uuid4())


def connect(path: Path | str, *, readonly: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000;")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def migrate(path: Path | str) -> int:
    path = Path(path)
    key = str(path.resolve())
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists() or path.stat().st_size == 0
        conn = sqlite3.connect(str(path), timeout=10.0)
        try:
            if fresh:
                # Phai dat truoc khi tao bang dau tien; sau do muon doi phai VACUUM ca file
                conn.execute("PRAGMA auto_vacuum=INCREMENTAL;")
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = int(row[0] or 0) if row else 0
            for version, ddl in enumerate(MIGRATIONS[current:], start=current + 1):
                with conn:
                    for statement in [s for s in ddl.split(";") if s.strip()]:
                        conn.execute(statement)
                    conn.execute("DELETE FROM schema_version")
                    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                current = version
        finally:
            conn.close()
        _MIGRATED.add(key)
        return current


def ensure(path: Path | str) -> None:
    if str(Path(path).resolve()) not in _MIGRATED:
        migrate(path)


# ---------------------------------------------------------------- ma hoa mang so

def pack(values: Iterable[float]) -> bytes:
    arr = array("f", values)
    if arr.itemsize != 4:  # pragma: no cover - moi nen tang pho bien deu la 4 byte
        raise RuntimeError("float32 khong phai 4 byte")
    import sys
    if sys.byteorder != "little":  # pragma: no cover
        arr.byteswap()
    return zlib.compress(arr.tobytes(), 6)


def unpack(blob: bytes) -> array:
    arr = array("f")
    arr.frombytes(zlib.decompress(blob))
    import sys
    if sys.byteorder != "little":  # pragma: no cover
        arr.byteswap()
    return arr


# ---------------------------------------------------------------- store

class OrderflowStore:
    """Truy cap orderflow.db. Moi lan goi mo ket noi rieng (an toan giua thread / process)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def conn(self) -> sqlite3.Connection:
        ensure(self.path)
        return connect(self.path)

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    # ------------------------------------------------------------ ghi (collector)

    @staticmethod
    def add_footprint(conn: sqlite3.Connection, symbol: str, res_sec: int,
                      rows: Iterable[tuple[int, float, float, float, int, int]]) -> int:
        """Cong don (ts, price_bucket, buy_vol, sell_vol, buy_cnt, sell_cnt). Collector chi gui phan moi
        (lenh co agg id > last_agg_id) nen cong don khong nhan doi."""
        data = [(guid(), symbol, res_sec, ts, pb, bv, sv, bc, sc) for ts, pb, bv, sv, bc, sc in rows]
        conn.executemany(
            """INSERT INTO trade_footprint (id, symbol, res_sec, ts, price_bucket, buy_vol, sell_vol, buy_cnt, sell_cnt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (symbol, res_sec, ts, price_bucket) DO UPDATE SET
                 buy_vol = buy_vol + excluded.buy_vol, sell_vol = sell_vol + excluded.sell_vol,
                 buy_cnt = buy_cnt + excluded.buy_cnt, sell_cnt = sell_cnt + excluded.sell_cnt""", data)
        return len(data)

    @staticmethod
    def insert_big_trades(conn: sqlite3.Connection, symbol: str, rows: Iterable[dict[str, Any]]) -> int:
        data = [(guid(), symbol, r["ts"], r["price"], r["qty"], r["side"], r["n_trades"], r["first_agg_id"],
                 r["last_agg_id"]) for r in rows]
        conn.executemany(
            """INSERT OR IGNORE INTO big_trades (id, symbol, ts, price, qty, side, n_trades, first_agg_id, last_agg_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", data)
        return len(data)

    @staticmethod
    def insert_snapshot(conn: sqlite3.Connection, symbol: str, market: str, snap: dict[str, Any]) -> None:
        conn.execute(
            """INSERT INTO ob_snapshots (id, symbol, market, ts, mid, best_bid, best_ask, bucket_size, base_price,
                                         n_buckets, bids, asks) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guid(), symbol, market, snap["ts"], snap["mid"], snap["best_bid"], snap["best_ask"],
             snap["bucket_size"], snap["base_price"], snap["n_buckets"], pack(snap["bids"]), pack(snap["asks"])))

    @staticmethod
    def insert_agg(conn: sqlite3.Connection, symbol: str, market: str, row: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR REPLACE INTO ob_snapshots_agg (id, symbol, market, res_sec, ts, n_snapshots, mid, best_bid,
                 best_ask, bucket_size, base_price, n_buckets, bids, asks) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guid(), symbol, market, row["res_sec"], row["ts"], row["n_snapshots"], row["mid"], row["best_bid"],
             row["best_ask"], row["bucket_size"], row["base_price"], row["n_buckets"], pack(row["bids"]),
             pack(row["asks"])))

    def rebuild_agg(self, symbol: str, market: str, levels: Iterable[int], until: int, since: int) -> int:
        """Gop lai cac cua so con thieu tu snapshot tho (collector khoi dong lai giua chung). Chi cac cua so
        ket thuc truoc `until` (cua so dang chay do collector tu gop)."""
        import orderflow_logic  # noqa: PLC0415 - tranh vong import luc nap module
        written = 0
        with closing(self.conn()) as conn:
            for res in levels:
                res_ms = int(res) * 1000
                row = conn.execute("SELECT MAX(ts) FROM ob_snapshots_agg WHERE symbol = ? AND res_sec = ?",
                                   (symbol, res)).fetchone()
                start = max(since, (row[0] + res_ms) if row[0] is not None else since)
                start -= start % res_ms
                stop = until - until % res_ms
                if stop <= start:
                    continue
                agg = orderflow_logic.SnapshotAgg(int(res))
                rows: list[dict[str, Any]] = []
                for r in conn.execute("SELECT * FROM ob_snapshots WHERE symbol = ? AND ts >= ? AND ts < ? ORDER BY ts",
                                      (symbol, start, stop)):
                    rows.extend(agg.add({"ts": r["ts"], "mid": r["mid"], "best_bid": r["best_bid"], "best_ask": r["best_ask"],
                                         "bucket_size": r["bucket_size"], "base_price": r["base_price"],
                                         "bids": unpack(r["bids"]), "asks": unpack(r["asks"])}))
                rows.extend(agg.close_all())
                with conn:
                    for item in rows:
                        self.insert_agg(conn, symbol, market, item)
                written += len(rows)
        return written

    def agg_rows(self, symbol: str, res_sec: int, start: int, end: int) -> list[sqlite3.Row]:
        with closing(self.conn()) as conn:
            return conn.execute(
                "SELECT ts, base_price, bucket_size, n_buckets, bids, asks, best_bid, best_ask, n_snapshots "
                "FROM ob_snapshots_agg WHERE symbol = ? AND res_sec = ? AND ts >= ? AND ts < ? ORDER BY ts",
                (symbol, res_sec, start, end)).fetchall()

    def snapshot_times(self, symbol: str, start: int, end: int) -> list[int]:
        with closing(self.conn()) as conn:
            return [r[0] for r in conn.execute("SELECT ts FROM ob_snapshots WHERE symbol = ? AND ts >= ? AND ts < ? "
                                               "ORDER BY ts", (symbol, start, end))]

    def open_gap(self, symbol: str, kind: str, start_ts: int, reason: str) -> str:
        """Mo mot gap. Da co gap cung loai dang mo thi giu gap cu (mot lan mat ket noi chi la mot gap)."""
        with closing(self.conn()) as conn, conn:
            row = conn.execute("SELECT id FROM orderflow_gaps WHERE symbol = ? AND kind = ? AND end_ts IS NULL",
                               (symbol, kind)).fetchone()
            if row:
                return row["id"]
            gid = guid()
            conn.execute("INSERT INTO orderflow_gaps (id, symbol, kind, start_ts, end_ts, reason) "
                         "VALUES (?, ?, ?, ?, NULL, ?)", (gid, symbol, kind, start_ts, reason))
            return gid

    def close_gaps(self, symbol: str, kind: str, end_ts: int) -> int:
        with closing(self.conn()) as conn, conn:
            return conn.execute("UPDATE orderflow_gaps SET end_ts = ? WHERE symbol = ? AND kind = ? AND end_ts IS NULL",
                                (end_ts, symbol, kind)).rowcount

    def close_all_gaps(self, kind: str, end_ts: int, symbols: Iterable[str]) -> None:
        for symbol in symbols:
            self.close_gaps(symbol, kind, end_ts)

    def add_gap(self, symbol: str, kind: str, start_ts: int, end_ts: int, reason: str) -> None:
        with closing(self.conn()) as conn, conn:
            conn.execute("INSERT INTO orderflow_gaps (id, symbol, kind, start_ts, end_ts, reason) VALUES (?, ?, ?, ?, ?, ?)",
                         (guid(), symbol, kind, start_ts, end_ts, reason))

    @staticmethod
    def upsert_status(conn: sqlite3.Connection, symbol: str, fields: dict[str, Any]) -> None:
        fields = {**fields, "updated_at": fields.get("updated_at") or now_ms()}
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
        conn.execute(f"INSERT INTO orderflow_status (symbol, {cols}) VALUES (?, {marks}) "
                     f"ON CONFLICT (symbol) DO UPDATE SET {updates}", (symbol, *fields.values()))

    # ------------------------------------------------------------ doc

    def status(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if not self.exists():
            return []
        with closing(self.conn()) as conn:
            if symbol:
                rows = conn.execute("SELECT * FROM orderflow_status WHERE symbol = ?", (symbol,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM orderflow_status ORDER BY symbol").fetchall()
        return [dict(r) for r in rows]

    def gaps(self, symbol: str, start: int, end: int, kind: str | None = None, now: int | None = None
             ) -> list[dict[str, Any]]:
        if not self.exists():
            return []
        now = now or now_ms()
        sql = ("SELECT kind, start_ts, end_ts, reason FROM orderflow_gaps WHERE symbol = ? AND start_ts < ? "
               "AND COALESCE(end_ts, ?) > ?")
        args: list[Any] = [symbol, end, now, start]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        with closing(self.conn()) as conn:
            rows = conn.execute(sql + " ORDER BY start_ts", args).fetchall()
        return [{**dict(r), "open": r["end_ts"] is None, "end_ts": r["end_ts"] if r["end_ts"] is not None else now}
                for r in rows]

    def coverage(self, symbol: str) -> dict[str, Any]:
        """Moc dau / cuoi cua tung loai du lieu: dung de biet symbol da co lenh khop, so lenh chua."""
        out = {"trades_from": None, "trades_to": None, "book_from": None, "book_to": None, "big_trades": 0}
        if not self.exists():
            return out
        with closing(self.conn()) as conn:
            row = conn.execute("SELECT MIN(ts) a, MAX(ts) b FROM trade_footprint WHERE symbol = ?", (symbol,)).fetchone()
            out["trades_from"], out["trades_to"] = row["a"], row["b"]
            row = conn.execute("SELECT MIN(ts) a, MAX(ts) b FROM ob_snapshots WHERE symbol = ?", (symbol,)).fetchone()
            out["book_from"], out["book_to"] = row["a"], row["b"]
        return out

    def footprint(self, symbol: str, start: int, end: int, bucket: float | None = None
                  ) -> list[sqlite3.Row]:
        """Dong footprint trong [start, end). Gop ca res nho lan 60s: hai loai khong bao gio chong nhau
        (gop 60s xoa dong res nho trong cung giao dich, backfill ghi dung mot loai cho moi khoang)."""
        with closing(self.conn()) as conn:
            return conn.execute(
                "SELECT ts, res_sec, price_bucket, buy_vol, sell_vol, buy_cnt, sell_cnt FROM trade_footprint "
                "WHERE symbol = ? AND ts >= ? AND ts < ? ORDER BY ts", (symbol, start, end)).fetchall()

    def cvd_rows(self, symbol: str, start: int, end: int, res_ms: int) -> list[tuple[int, float, float, float, float]]:
        """(t, buy, sell, high, low) theo buoc res_ms; high/low la bien bucket gia co lenh khop."""
        with closing(self.conn()) as conn:
            rows = conn.execute(
                "SELECT (ts / ?) * ? AS t, SUM(buy_vol), SUM(sell_vol), MAX(price_bucket), MIN(price_bucket) "
                "FROM trade_footprint WHERE symbol = ? AND ts >= ? AND ts < ? GROUP BY t ORDER BY t",
                (res_ms, res_ms, symbol, start, end)).fetchall()
        return [(int(r[0]), float(r[1] or 0), float(r[2] or 0), float(r[3]), float(r[4])) for r in rows]

    def delta_sum(self, symbol: str, start: int, end: int) -> tuple[float, float]:
        if end <= start:
            return 0.0, 0.0
        with closing(self.conn()) as conn:
            row = conn.execute("SELECT SUM(buy_vol), SUM(sell_vol) FROM trade_footprint WHERE symbol = ? "
                               "AND ts >= ? AND ts < ?", (symbol, start, end)).fetchone()
        return float(row[0] or 0), float(row[1] or 0)

    def profile_rows(self, symbol: str, start: int, end: int) -> list[tuple[float, float, float]]:
        with closing(self.conn()) as conn:
            rows = conn.execute(
                "SELECT price_bucket, SUM(buy_vol), SUM(sell_vol) FROM trade_footprint WHERE symbol = ? "
                "AND ts >= ? AND ts < ? GROUP BY price_bucket ORDER BY price_bucket", (symbol, start, end)).fetchall()
        return [(float(r[0]), float(r[1] or 0), float(r[2] or 0)) for r in rows]

    def big_trades(self, symbol: str, start: int, end: int, min_qty: float = 0.0, limit: int = 5000
                   ) -> list[dict[str, Any]]:
        """Lenh lon trong khoang; qua limit thi giu lenh lon nhat, tra theo thoi gian."""
        with closing(self.conn()) as conn:
            rows = conn.execute(
                "SELECT ts, price, qty, side, n_trades, first_agg_id, last_agg_id FROM big_trades WHERE symbol = ? "
                "AND ts >= ? AND ts < ? AND qty >= ? ORDER BY qty DESC LIMIT ?",
                (symbol, start, end, min_qty, limit)).fetchall()
        return sorted((dict(r) for r in rows), key=lambda r: r["ts"])

    def big_trades_after(self, symbol: str, after_agg_id: int, min_qty: float, limit: int = 500) -> list[dict[str, Any]]:
        """Lenh lon moi hon con tro agg id (stream SSE). agg id tang theo thoi gian nen khong sot, khong lap."""
        with closing(self.conn()) as conn:
            rows = conn.execute(
                "SELECT ts, price, qty, side, first_agg_id FROM big_trades WHERE symbol = ? AND first_agg_id > ? "
                "AND qty >= ? ORDER BY first_agg_id LIMIT ?", (symbol, after_agg_id, min_qty, limit)).fetchall()
        return [dict(r) for r in rows]

    def max_first_agg_id(self, symbol: str) -> int:
        with closing(self.conn()) as conn:
            row = conn.execute("SELECT MAX(first_agg_id) FROM big_trades WHERE symbol = ?", (symbol,)).fetchone()
        return int(row[0] or 0)

    def snapshots(self, symbol: str, start: int, end: int, columns: str = "*") -> list[sqlite3.Row]:
        with closing(self.conn()) as conn:
            return conn.execute(f"SELECT {columns} FROM ob_snapshots WHERE symbol = ? AND ts >= ? AND ts < ? ORDER BY ts",
                                (symbol, start, end)).fetchall()

    def snapshot_at(self, symbol: str, ts: int | None = None) -> sqlite3.Row | None:
        """Snapshot gan ts nhat (mac dinh: moi nhat)."""
        with closing(self.conn()) as conn:
            if ts is None:
                return conn.execute("SELECT * FROM ob_snapshots WHERE symbol = ? ORDER BY ts DESC LIMIT 1",
                                    (symbol,)).fetchone()
            before = conn.execute("SELECT * FROM ob_snapshots WHERE symbol = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
                                  (symbol, ts)).fetchone()
            after = conn.execute("SELECT * FROM ob_snapshots WHERE symbol = ? AND ts > ? ORDER BY ts LIMIT 1",
                                 (symbol, ts)).fetchone()
        if before is None or (after is not None and after["ts"] - ts < ts - before["ts"]):
            return after
        return before

    def snapshots_after(self, symbol: str, after_ts: int, limit: int = 50) -> list[sqlite3.Row]:
        with closing(self.conn()) as conn:
            return conn.execute("SELECT * FROM ob_snapshots WHERE symbol = ? AND ts > ? ORDER BY ts LIMIT ?",
                                (symbol, after_ts, limit)).fetchall()

    # ------------------------------------------------------------ job backfill

    def create_job(self, kind: str, symbol: str, from_date: str, to_date: str) -> dict[str, Any]:
        now = now_ms()
        row = {"id": guid(), "kind": kind, "symbol": symbol, "from_date": from_date, "to_date": to_date,
               "status": "queued", "progress": 0.0, "message": "chờ chạy", "result": None, "pid": None,
               "created_at": now, "updated_at": now, "finished_at": None}
        with closing(self.conn()) as conn, conn:
            conn.execute(f"INSERT INTO orderflow_jobs ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                         tuple(row.values()))
        return row

    def update_job(self, job_id: str, **fields: Any) -> None:
        fields["updated_at"] = now_ms()
        sets = ", ".join(f"{k} = ?" for k in fields)
        with closing(self.conn()) as conn, conn:
            conn.execute(f"UPDATE orderflow_jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))

    def job(self, job_id: str) -> dict[str, Any] | None:
        if not self.exists():
            return None
        with closing(self.conn()) as conn:
            row = conn.execute("SELECT * FROM orderflow_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.exists():
            return []
        with closing(self.conn()) as conn:
            rows = conn.execute("SELECT * FROM orderflow_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ don du lieu

    def retention(self, retention: dict[str, int], now: int | None = None) -> dict[str, int]:
        """Job hang ngay: gop footprint res nho het han sang 60s roi xoa, xoa du lieu het han, incremental_vacuum."""
        now = now or now_ms()
        out: dict[str, int] = {}
        cut_fine = now - int(retention["footprint_5s_days"]) * DAY_MS
        with closing(self.conn()) as conn:
            with conn:
                conn.execute(
                    f"""INSERT INTO trade_footprint (id, symbol, res_sec, ts, price_bucket, buy_vol, sell_vol, buy_cnt, sell_cnt)
                        SELECT {SQL_GUID}, symbol, 60, (ts / 60000) * 60000 AS m, price_bucket, SUM(buy_vol), SUM(sell_vol),
                               SUM(buy_cnt), SUM(sell_cnt)
                        FROM trade_footprint WHERE res_sec < 60 AND ts < ?
                        GROUP BY symbol, m, price_bucket
                        ON CONFLICT (symbol, res_sec, ts, price_bucket) DO UPDATE SET
                          buy_vol = buy_vol + excluded.buy_vol, sell_vol = sell_vol + excluded.sell_vol,
                          buy_cnt = buy_cnt + excluded.buy_cnt, sell_cnt = sell_cnt + excluded.sell_cnt""", (cut_fine,))
                out["footprint_rolled"] = conn.execute(
                    "DELETE FROM trade_footprint WHERE res_sec < 60 AND ts < ?", (cut_fine,)).rowcount
            plan = [
                ("ob_snapshots", "ts < ?", retention["heatmap_days"]),
                ("ob_snapshots_agg", "ts < ?", retention["heatmap_days"]),
                ("trade_footprint", "res_sec >= 60 AND ts < ?", retention["footprint_60s_days"]),
                ("big_trades", "ts < ?", retention["big_trades_days"]),
                ("orderflow_gaps", "end_ts IS NOT NULL AND end_ts < ?", retention["gaps_days"]),
            ]
            for table, where, days in plan:
                with conn:
                    out[table] = conn.execute(f"DELETE FROM {table} WHERE {where}", (now - int(days) * DAY_MS,)).rowcount
            with conn:
                conn.execute("DELETE FROM orderflow_jobs WHERE created_at < ?", (now - 90 * DAY_MS,))
            conn.execute("PRAGMA incremental_vacuum;")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        return out
