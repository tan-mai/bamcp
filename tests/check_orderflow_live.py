# TM - #BOOKMAP - Order flow Bookmap
"""Kiem tra nghiem thu order flow tren du lieu THAT (goi mang Binance, doc orderflow.db that). Khong phai unit test:
ten file khong bat dau bang test_ nen pytest khong tu chay.

  .venv\\Scripts\\python tests\\check_orderflow_live.py --symbol BTCUSDT --day 2026-11-12 [--collector-hours 4]

1. (nghiem thu 2) Tong buy_vol + sell_vol cua mot ngay = tong quantity trong file aggTrades ngay do (< 0.01%).
2. (nghiem thu 2, 3) Tong theo gio khop volume va taker buy volume cua kline H1 (< 0.5%). Kline lay tu
   fapi/v1/klines - cung nguon fetcher BAMCP luu; neu kho kline BAMCP co du gio do thi so them voi kho.
3. (nghiem thu 5) POC / VAH / VAL cua mot khoang tinh doc lap tu lenh tho trong file = ket qua get_volume_profile.
4. (nghiem thu 4) Cac gio collector tu ghi (khong qua backfill): tong theo gio = kline H1 -> khong trung, khong thieu
   agg id (thieu / trung mot lenh la lech ngay).
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx2 as httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import orderflow_config  # noqa: E402
import orderflow_store  # noqa: E402

UTC = timezone.utc
H = 3_600_000
DAY = 86_400_000


def day_start(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def klines(client, symbol: str, start: int, end: int) -> dict[int, tuple[float, float]]:
    rows = client.get("https://fapi.binance.com/fapi/v1/klines",
                      params={"symbol": symbol, "interval": "1h", "startTime": start, "endTime": end - 1, "limit": 1000}).json()
    return {int(r[0]): (float(r[5]), float(r[9])) for r in rows}


def value_area_independent(trades, start, end, bucket):
    """Viet lai tu dau, khong dung orderflow_logic: gom tung lenh tho vao bucket, mo rong tu POC."""
    vol = defaultdict(float)
    for ts, price, qty in trades:
        if start <= ts < end:
            vol[int((price + 1e-9 * bucket) // bucket)] += qty
    keys = range(min(vol), max(vol) + 1)
    arr = [vol.get(k, 0.0) for k in keys]
    poc = arr.index(max(arr))
    lo = hi = poc
    acc, target = arr[poc], 0.7 * sum(arr)
    while acc < target - 1e-9 and (lo > 0 or hi < len(arr) - 1):
        up = arr[hi + 1] if hi + 1 < len(arr) else -1
        dn = arr[lo - 1] if lo > 0 else -1
        if up >= dn:
            hi += 1
            acc += up
        else:
            lo -= 1
            acc += dn
    base = keys[0]
    return (base + poc) * bucket + bucket / 2, (base + hi + 1) * bucket, (base + lo) * bucket


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--day", required=True, help="ngay UTC da backfill, YYYY-MM-DD")
    ap.add_argument("--collector-from", default="", help="'YYYY-MM-DD HH' UTC: gio dau tien collector ghi tron gio")
    ap.add_argument("--collector-hours", type=int, default=0)
    args = ap.parse_args()
    config = orderflow_config.OrderflowConfig(ROOT / "config.yaml")
    cfg = config.get()
    sym = config.symbol(args.symbol)
    store = orderflow_store.OrderflowStore(cfg["paths"]["db"])
    client = httpx.Client(timeout=120, follow_redirects=True)
    ok = True
    d0 = day_start(args.day)

    # 1. file vs footprint
    url = f"{cfg['endpoints'][sym['market']]['vision']}/{args.symbol}/{args.symbol}-aggTrades-{args.day}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a.zip"
        path.write_bytes(client.get(url).content)
        trades, file_total, hourly_buy = [], 0.0, defaultdict(float)
        with zipfile.ZipFile(path) as zf, zf.open(zf.namelist()[0]) as raw:
            for row in csv.reader(io.TextIOWrapper(raw, encoding="utf-8")):
                if not row[0].isdigit():
                    continue
                ts, price, qty = int(row[5]), float(row[1]), float(row[2])
                file_total += qty
                if row[6] == "false":
                    hourly_buy[ts - ts % H] += qty
                trades.append((ts, price, qty))
    buy, sell = store.delta_sum(args.symbol, d0, d0 + DAY)
    err = abs(buy + sell - file_total) / file_total * 100
    print(f"[1] {args.day}: file {file_total:.3f}, footprint {buy + sell:.3f}, lech {err:.6f}% (nguong 0.01%)")
    ok &= err < 0.01

    # 2. theo gio vs kline H1
    kl = klines(client, args.symbol, d0, d0 + DAY)
    rows = store.cvd_rows(args.symbol, d0, d0 + DAY, H)
    worst_v = worst_b = 0.0
    for t, b, s, *_ in rows:
        vol, taker = kl[t]
        worst_v = max(worst_v, abs(b + s - vol) / vol * 100)
        worst_b = max(worst_b, abs(b - taker) / taker * 100)
    print(f"[2] {len(rows)} gio: lech volume lon nhat {worst_v:.4f}%, lech taker buy lon nhat {worst_b:.4f}% (nguong 0.5%)")
    ok &= worst_v < 0.5 and worst_b < 0.5 and len(rows) == 24

    # 3. POC/VAH/VAL doc lap
    import server  # noqa: PLC0415 - can de goi dung tool
    a, b = d0 + 8 * H, d0 + 14 * H
    tool = server.get_volume_profile(args.symbol, str(a), str(b))
    mine = value_area_independent(trades, a, b, sym["bucket_size"])
    same = all(abs(x - y) < 1e-6 for x, y in zip((tool["poc"], tool["vah"], tool["val"]), mine))
    print(f"[3] 08:00-14:00 UTC: tool poc/vah/val {tool['poc']}/{tool['vah']}/{tool['val']}, doc lap {mine} -> {'KHOP' if same else 'LECH'}")
    ok &= same

    # 4. gio do collector ghi
    if args.collector_from and args.collector_hours:
        c0 = int(datetime.strptime(args.collector_from, "%Y-%m-%d %H").replace(tzinfo=UTC).timestamp() * 1000)
        kl = klines(client, args.symbol, c0, c0 + args.collector_hours * H)
        rows = {t: (b, s) for t, b, s, *_ in store.cvd_rows(args.symbol, c0, c0 + args.collector_hours * H, H)}
        for t in sorted(kl):
            b, s = rows.get(t, (0.0, 0.0))
            vol, taker = kl[t]
            ev, eb = abs(b + s - vol) / vol * 100, abs(b - taker) / taker * 100
            print(f"[4] {datetime.fromtimestamp(t / 1000, UTC):%Y-%m-%d %H}:00 collector {b + s:.3f} / kline {vol:.3f} "
                  f"lech {ev:.5f}%, taker lech {eb:.5f}%")
            ok &= ev < 0.5 and eb < 0.5
    print("KET QUA:", "DAT" if ok else "KHONG DAT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
