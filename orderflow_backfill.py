# TM - #BOOKMAP - Order flow Bookmap
"""Backfill lenh khop: tinh lai trade_footprint va big_trades tu aggTrades lich su.

- Ngay truoc hom nay (UTC): file zip daily tren data.binance.vision. File chua co (404, thuong la hom qua) thi
  dung REST.
- Hom nay: REST aggTrades, tim moc bang cua so <= 1h roi lat trang fromId, doc X-MBX-USED-WEIGHT-1M de khong
  vuot rate limit.
- Ghi de theo khoang [dau ngay, cuoi khoang): xoa footprint + big_trades cua khoang roi ghi lai, nen chay lai bao
  nhieu lan so dong van vay. Khoang cua hom nay dung truoc 'mep ghi' cua collector dang chay 60 giay, nen hai ben
  khong bao gio cung ghi mot bucket.
- Heatmap (so lenh) KHONG backfill duoc: Binance khong co lich su so lenh mien phi.

Server goi: python orderflow_backfill.py --job <id> (process rieng, khong chiem GIL cua server MCP).
Tay:       python orderflow_backfill.py --symbol BTCUSDT --from 2026-11-07 --to 2026-11-13
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import traceback
import zipfile
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx2 as httpx

import orderflow_config
import orderflow_logic as logic
import orderflow_store

UTC = timezone.utc
DAY_MS = 86_400_000
MINUTE_MS = 60_000

Trade = tuple[int, int, float, float, bool]   # agg_id, ts, price, qty, sell


def now_ms() -> int:
    return int(time.time() * 1000)


def day_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def parse_day(text: str) -> date:
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"ngay phai co dang YYYY-MM-DD, nhan duoc {text!r}") from None


def validate_range(from_date: str, to_date: str, max_days: int, today: date | None = None) -> list[date]:
    today = today or datetime.now(UTC).date()
    start, end = parse_day(from_date), parse_day(to_date)
    if end < start:
        raise ValueError("to_date phai >= from_date")
    if end > today:
        raise ValueError(f"to_date khong duoc sau hom nay (UTC {today.isoformat()})")
    days = (end - start).days + 1
    if days > max_days:
        raise ValueError(f"toi da {max_days} ngay moi lan backfill, dang xin {days} ngay")
    return [start + timedelta(days=i) for i in range(days)]


# ---------------------------------------------------------------- nguon du lieu

def read_zip(path: Path) -> Iterator[Trade]:
    """CSV aggTrades: agg_trade_id, price, quantity, first_trade_id, last_trade_id, transact_time, is_buyer_maker[, ...].
    File futures co dong tieu de; file spot khong co va tinh thoi gian bang micro giay tu 2025."""
    with zipfile.ZipFile(path) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(name) as raw:
            for row in csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline="")):
                if not row or not row[0].strip().isdigit():
                    continue
                ts = int(row[5])
                if ts > 10 ** 14:
                    ts //= 1000
                yield int(row[0]), ts, float(row[1]), float(row[2]), row[6].strip().lower() == "true"


class Rest:
    def __init__(self, client: httpx.Client, base: str, path: str, symbol: str, soft_limit: int = 1500):
        self.client, self.url, self.symbol = client, base + path, symbol
        self.soft_limit = soft_limit
        self.requests = 0

    def get(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        for attempt in range(6):
            res = self.client.get(self.url, params={"symbol": self.symbol, **params})
            self.requests += 1
            used = int(res.headers.get("x-mbx-used-weight-1m") or 0)
            if res.status_code in (418, 429):
                wait = int(res.headers.get("retry-after") or 60)
                time.sleep(min(300, wait))
                continue
            if res.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            res.raise_for_status()
            if used >= self.soft_limit:
                time.sleep(60 - time.time() % 60 + 0.5)
            return res.json()
        raise RuntimeError(f"REST {self.url} loi lien tiep")

    def trades(self, start: int, end: int, progress: Callable[[int], None] | None = None) -> Iterator[Trade]:
        """Lenh trong [start, end): cua so <= 1h de tim lenh dau tien, roi lat trang fromId."""
        cursor = None
        t = start
        while cursor is None and t < end:
            rows = self.get({"startTime": t, "endTime": min(t + 3_600_000, end) - 1, "limit": 1000})
            if rows:
                cursor = int(rows[0]["a"])
            else:
                t += 3_600_000
        while cursor is not None:
            rows = self.get({"fromId": cursor, "limit": 1000})
            if not rows:
                return
            for row in rows:
                ts = int(row["T"])
                if ts >= end:
                    return
                if ts >= start:
                    yield int(row["a"]), ts, float(row["p"]), float(row["q"]), bool(row["m"])
            cursor = int(rows[-1]["a"]) + 1
            if progress:
                progress(int(rows[-1]["T"]))
            if len(rows) < 1000:
                return


def download(client: httpx.Client, url: str, dest: Path) -> bool:
    """Tai file zip. 404 tra False (file chua duoc dang)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with client.stream("GET", url) as res:
        if res.status_code == 404:
            return False
        res.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in res.iter_bytes(1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    return True


# ---------------------------------------------------------------- tinh va ghi

def aggregate(trades: Iterator[Trade], *, start: int, end: int, res_sec: int | None, bucket_size: float,
              merge_ms: int, min_qty: float | None) -> dict[str, Any]:
    fp = logic.Footprint()
    merger = logic.BubbleMerger(merge_ms)
    bigs: list[dict[str, Any]] = []
    stats = {"trades": 0, "buy_vol": 0.0, "sell_vol": 0.0, "first_id": None, "last_id": None,
             "first_ts": None, "last_ts": None}
    prev = None
    for agg, ts, price, qty, sell in trades:
        if ts < start or ts >= end:
            continue
        if prev is not None and agg <= prev:
            continue       # trung (REST lat trang chong nhau)
        prev = agg
        stats["trades"] += 1
        stats["sell_vol" if sell else "buy_vol"] += qty
        stats["first_id"] = stats["first_id"] or agg
        stats["first_ts"] = stats["first_ts"] or ts
        stats["last_id"], stats["last_ts"] = agg, ts
        if res_sec:
            fp.add(ts, price, qty, sell, res_sec, bucket_size)
        if min_qty is not None:
            bigs.extend(g for g in merger.add(agg, ts, price, qty, sell) if g["qty"] >= min_qty)
    if min_qty is not None:
        bigs.extend(g for g in merger.flush() if g["qty"] >= min_qty)
    return {"stats": stats, "footprint": fp.drain(), "big": bigs}


def write_range(store: orderflow_store.OrderflowStore, symbol: str, start: int, end: int,
                result: dict[str, Any], *, write_fp: bool, write_big: bool) -> dict[str, int]:
    out = {"footprint_rows": 0, "big_trades": 0}
    with closing(store.conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if write_fp:
            conn.execute("DELETE FROM trade_footprint WHERE symbol = ? AND ts >= ? AND ts < ?", (symbol, start, end))
            for res, rows in result["footprint"].items():
                out["footprint_rows"] += orderflow_store.OrderflowStore.add_footprint(conn, symbol, res, rows)
        if write_big:
            conn.execute("DELETE FROM big_trades WHERE symbol = ? AND ts >= ? AND ts < ?", (symbol, start, end))
            out["big_trades"] = orderflow_store.OrderflowStore.insert_big_trades(conn, symbol, result["big"])
    return out


def collector_frontier(store: orderflow_store.OrderflowStore, symbol: str, stale_sec: int) -> tuple[bool, int | None]:
    """(collector dang chay, mep ghi). Mep = thoi diem lenh cuoi collector da xu ly."""
    rows = store.status(symbol)
    if not rows:
        return False, None
    row = rows[0]
    live = bool(row.get("connected")) and (row.get("updated_at") or 0) >= now_ms() - stale_sec * 1000
    return live, row.get("last_event_ts")


def run(config: orderflow_config.OrderflowConfig, job_id: str, *, client: httpx.Client | None = None,
        today: date | None = None, clock: Callable[[], int] = now_ms) -> dict[str, Any]:
    cfg = config.get()
    store = orderflow_store.OrderflowStore(cfg["paths"]["db"])
    job = store.job(job_id)
    if job is None:
        raise ValueError(f"khong co job {job_id}")
    sym = config.symbol(job["symbol"])
    if sym is None:
        raise ValueError(f"{job['symbol']} chua khai trong order flow")
    store.update_job(job_id, status="running", pid=os.getpid(), message="bat dau")
    today = today or datetime.fromtimestamp(clock() / 1000, UTC).date()
    days = validate_range(job["from_date"], job["to_date"], int(cfg["tech"]["backfill_max_days"]), today)
    ep = cfg["endpoints"][sym["market"]]
    retention = cfg["retention"]
    own_client = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    rest = Rest(client, ep["rest"], ep["agg_path"], sym["symbol"])
    tmp_dir = cfg["paths"]["tmp"]
    summary: list[dict[str, Any]] = []
    last_id = None
    try:
        for n, day in enumerate(days):
            start, end = day_ms(day), day_ms(day) + DAY_MS
            now = clock()
            item: dict[str, Any] = {"date": day.isoformat()}
            if day >= today:
                end = min(end, (now - 2 * MINUTE_MS) // MINUTE_MS * MINUTE_MS)
            live, frontier = collector_frontier(store, sym["symbol"], int(cfg["tech"]["stale_sec"]))
            if live and frontier:
                end = min(end, (frontier - MINUTE_MS) // MINUTE_MS * MINUTE_MS)
            age_days = (now - end) / DAY_MS
            res = (sym["footprint_res_sec"] if age_days < retention["footprint_5s_days"]
                   else 60 if age_days < retention["footprint_60s_days"] else None)
            want_big = age_days < retention["big_trades_days"]
            if end <= start or (res is None and not want_big):
                item.update(skipped="ngoai thoi han luu hoac khong con khoang trong")
                summary.append(item)
                continue

            def progress(ts: int, base=n) -> None:
                frac = min(1.0, max(0.0, (ts - start) / max(1, end - start)))
                store.update_job(job_id, progress=round((base + frac) / len(days), 4),
                                 message=f"{day.isoformat()}: {datetime.fromtimestamp(ts / 1000, UTC):%H:%M} UTC")

            store.update_job(job_id, progress=round(n / len(days), 4), message=f"{day.isoformat()}: dang tai")
            source, zip_path = "rest", None
            if day < today:
                url = f"{ep['vision']}/{sym['symbol']}/{sym['symbol']}-aggTrades-{day.isoformat()}.zip"
                zip_path = tmp_dir / f"{sym['symbol']}-{day.isoformat()}-{job_id[:8]}.zip"
                if download(client, url, zip_path):
                    source = "file"
                else:
                    zip_path = None
            trades = read_zip(zip_path) if zip_path else rest.trades(start, end, progress)
            store.update_job(job_id, message=f"{day.isoformat()}: dang tinh ({source})")
            try:
                result = aggregate(trades, start=start, end=end, res_sec=res, bucket_size=sym["bucket_size"],
                                   merge_ms=sym["bubble_merge_ms"], min_qty=sym["big_trade_min_qty"] if want_big else None)
            finally:
                if zip_path:
                    zip_path.unlink(missing_ok=True)
            written = write_range(store, sym["symbol"], start, end, result, write_fp=res is not None, write_big=want_big)
            stats = result["stats"]
            if stats["last_id"]:
                last_id = max(last_id or 0, stats["last_id"])
            item.update(source=source, end_utc=datetime.fromtimestamp(end / 1000, UTC).strftime("%Y-%m-%d %H:%M"),
                        res_sec=res, trades=stats["trades"], buy_vol=round(stats["buy_vol"], 6),
                        sell_vol=round(stats["sell_vol"], 6), first_agg_id=stats["first_id"],
                        last_agg_id=stats["last_id"], **written)
            summary.append(item)
            store.update_job(job_id, progress=round((n + 1) / len(days), 4), result=json.dumps(summary),
                             message=f"{day.isoformat()}: xong {stats['trades']} lenh ({source})")
        # Collector dang tat: day last_agg_id toi lenh cuoi backfill, lan chay sau lay bu tu day, khong cong lai
        live, _ = collector_frontier(store, sym["symbol"], int(cfg["tech"]["stale_sec"]))
        if last_id and not live:
            row = (store.status(sym["symbol"]) or [{}])[0]
            if row.get("last_agg_id") is None or last_id > int(row["last_agg_id"]):
                with closing(store.conn()) as conn, conn:
                    orderflow_store.OrderflowStore.upsert_status(conn, sym["symbol"], {"last_agg_id": last_id,
                                                                                    "market": sym["market"]})
        store.update_job(job_id, status="done", progress=1.0, finished_at=now_ms(), result=json.dumps(summary),
                         message=f"xong {len(days)} ngay, {rest.requests} request REST")
        return {"status": "done", "days": summary}
    except Exception as exc:
        store.update_job(job_id, status="failed", finished_at=now_ms(), result=json.dumps(summary),
                         message=f"{type(exc).__name__}: {exc}"[:500])
        raise
    finally:
        if own_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BAMCP order flow backfill aggTrades")
    parser.add_argument("--config", default=os.environ.get("BAMCP_CONFIG") or str(Path(__file__).parent / "config.yaml"))
    parser.add_argument("--job")
    parser.add_argument("--symbol")
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    args = parser.parse_args(argv)
    config = orderflow_config.OrderflowConfig(args.config)
    job_id = args.job
    if not job_id:
        if not (args.symbol and args.from_date and args.to_date):
            parser.error("can --job hoac --symbol --from --to")
        cfg = config.get()
        validate_range(args.from_date, args.to_date, int(cfg["tech"]["backfill_max_days"]))
        store = orderflow_store.OrderflowStore(cfg["paths"]["db"])
        job_id = store.create_job("backfill", args.symbol.upper(), args.from_date, args.to_date)["id"]
        print(f"job {job_id}", file=sys.stderr)
    try:
        out = run(config, job_id)
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
