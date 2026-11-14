# TM - #BOOKMAP - Order flow Bookmap
"""Nghiem thu order flow phan logic, collector, backfill, store. DB tam, khong goi mang (REST / zip gia lap).

Nghiem thu 9 (dong bo so lenh), 13 (tuong), 14 (hap thu), 5 (POC/VAH/VAL doc lap), 16 (bat / tat symbol khong
restart), 1 (backfill chay lai khong nhan doi) chay o day. Nghiem thu chay that tren Binance: tests/check_orderflow_live.py.
"""
from __future__ import annotations

import asyncio
import io
import json
import random
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx2 as httpx  # noqa: E402

import orderflow_backfill  # noqa: E402
import orderflow_collector  # noqa: E402
import orderflow_config  # noqa: E402
import orderflow_logic as logic  # noqa: E402
import orderflow_store  # noqa: E402

UTC = timezone.utc
DAY = 86_400_000
MIN = 60_000

SYMBOLS_YAML = """
orderflow:
  enabled: true
  retention: { heatmap_days: 14, footprint_5s_days: 30, footprint_60s_days: 365, big_trades_days: 180, gaps_days: 365 }
  symbols:
    - { symbol: BTCUSDT, market: futures, enabled: true, bucket_size: 10, depth_range_pct: 0.03, snapshot_interval_sec: 2,
        footprint_res_sec: 5, big_trade_min_qty: 5, bubble_merge_ms: 200, wall_k: 5, absorption_min_qty: 50 }
    - { symbol: ETHUSDT, market: futures, enabled: true, bucket_size: 1, big_trade_min_qty: 100, absorption_min_qty: 1000 }
"""


class TmpConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bamcp-of-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.yaml = self.tmp / "config.yaml"
        self.yaml.write_text(f'paths:\n  data_root: "{self.tmp.as_posix()}/data"\n' + SYMBOLS_YAML, encoding="utf-8")
        self.config = orderflow_config.OrderflowConfig(self.yaml, self.tmp / "data")
        self.store = orderflow_store.OrderflowStore(self.config.get()["paths"]["db"])


# ---------------------------------------------------------------- nghiem thu 9: dong bo so lenh

def ev(U, u, pu=None, b=(), a=()):
    out = {"e": "depthUpdate", "E": 1, "U": U, "u": u, "b": [list(x) for x in b], "a": [list(x) for x in a]}
    if pu is not None:
        out["pu"] = pu
    return out


class BookSyncTest(unittest.TestCase):
    def test_futures_in_order_drop_stale_and_apply(self):
        book = logic.BookSync("futures")
        self.assertEqual(book.on_event(ev(90, 95, 89, b=[("100", "1")])), "buffered")   # cu: bo
        self.assertEqual(book.on_event(ev(96, 105, 95, b=[("101", "2")], a=[("102", "3")])), "buffered")
        status = book.on_snapshot(100, [["100", "5"], ["99", "1"]], [["102", "1"], ["103", "4"]])
        self.assertEqual(status, "ok")
        self.assertTrue(book.synced)
        self.assertEqual(book.bids, {100.0: 5.0, 99.0: 1.0, 101.0: 2.0})   # event u=95 < 100 khong duoc ap
        self.assertEqual(book.asks, {102.0: 3.0, 103.0: 4.0})
        self.assertEqual(book.on_event(ev(106, 110, 105, b=[("99", "0")], a=[("104", "0")])), "ok")  # xoa muc khong co: binh thuong
        self.assertEqual(book.bids, {100.0: 5.0, 101.0: 2.0})
        self.assertEqual(book.best(), (101.0, 102.0))

    def test_futures_missing_pu_resyncs(self):
        book = logic.BookSync("futures")
        book.on_event(ev(96, 105, 95))
        self.assertEqual(book.on_snapshot(100, [], []), "ok")
        self.assertEqual(book.on_event(ev(106, 110, 105)), "ok")
        self.assertEqual(book.on_event(ev(115, 120, 112)), "resync")   # thieu 111..114
        self.assertIn("pu=112", book.reason)

    def test_futures_first_event_must_cover_snapshot(self):
        book = logic.BookSync("futures")
        book.on_event(ev(150, 160, 149))
        self.assertEqual(book.on_snapshot(100, [], []), "resync")   # snapshot qua cu so voi stream

    def test_spot_rules(self):
        book = logic.BookSync("spot")
        book.on_event(ev(95, 100))
        book.on_event(ev(101, 103, b=[("10", "1")]))
        self.assertEqual(book.on_snapshot(101, [], []), "ok")     # U <= 102 <= u
        self.assertEqual(book.bids, {10.0: 1.0})
        self.assertEqual(book.on_event(ev(104, 104)), "ok")
        self.assertEqual(book.on_event(ev(106, 107)), "resync")

    def test_bucketize_and_prune(self):
        book = logic.BookSync("futures")
        book.on_snapshot(1, [["99.5", "1"], ["98.2", "2"], ["50", "9"]], [["100.5", "3"], ["101.9", "1"], ["200", "9"]])
        snap = logic.bucketize(book.bids, book.asks, mid=100, range_pct=0.03, bucket_size=1)
        self.assertEqual((snap["base_price"], snap["n_buckets"]), (97.0, 7))
        self.assertEqual(snap["bids"][:3], [0.0, 2.0, 1.0])
        self.assertEqual(snap["asks"][3:5], [3.0, 1.0])
        self.assertEqual(book.prune(100, 0.045), 2)
        self.assertNotIn(50.0, book.bids)


class CollectorBookTest(TmpConfig):
    """Thieu pu: collector phai resync, ghi gap vao orderflow_gaps va tang resyncs_today; dong bo lai thi dong gap."""

    def test_resync_records_gap_then_closes(self):
        async def main():
            collector = orderflow_collector.Collector(self.config)
            collector.fetch_snapshot = mock.AsyncMock()   # khong goi mang
            state = orderflow_collector.SymbolState(self.config.symbol("BTCUSDT"), None)
            collector.states["BTCUSDT"] = state
            collector.conns[("futures", "depth")] = mock.Mock(connected=True)
            collector.on_depth(state, ev(96, 105, 95, b=[("100", "1")]))
            await asyncio.sleep(0)
            collector.fetch_snapshot.assert_called()
            state.book.on_snapshot(100, [["99", "2"]], [["101", "2"]])
            collector.on_depth(state, ev(106, 110, 105))
            collector.on_depth(state, ev(115, 120, 114))          # pu 114 != 110
            await asyncio.sleep(0)
            self.assertEqual(state.resyncs_today, 1)
            self.assertFalse(state.book.loaded)
            gaps = self.store.gaps("BTCUSDT", 0, 10 ** 13, kind="depth")
            self.assertEqual(len(gaps), 1)
            self.assertTrue(gaps[0]["open"])
            self.assertIn("resync", gaps[0]["reason"])
            # Dong bo lai -> snapshot dau tien dong gap
            state.book.on_snapshot(130, [["99", "2"]], [["101", "2"]])
            state.book.on_event(ev(125, 131, 124))
            collector.take_snapshot(state, time.monotonic())
            gaps = self.store.gaps("BTCUSDT", 0, 10 ** 13, kind="depth")
            self.assertFalse(gaps[0]["open"])
            collector.write_status()
            row = self.store.status("BTCUSDT")[0]
            self.assertEqual(row["resyncs_today"], 1)
            self.assertEqual(self.store.snapshot_at("BTCUSDT")["best_bid"], 99.0)
            await collector.http.aclose()
            collector.db.close()
        asyncio.run(main())

    def test_crossed_book_resyncs(self):
        async def main():
            collector = orderflow_collector.Collector(self.config)
            collector.fetch_snapshot = mock.AsyncMock()
            state = orderflow_collector.SymbolState(self.config.symbol("BTCUSDT"), None)
            collector.states["BTCUSDT"] = state
            state.book.on_snapshot(1, [["101", "1"]], [["100", "1"]])
            collector.take_snapshot(state, time.monotonic())
            self.assertEqual(state.resyncs_today, 1)
            await collector.http.aclose()
            collector.db.close()
        asyncio.run(main())


# ---------------------------------------------------------------- lenh khop: footprint, bong bong, agg id

class TradeLogicTest(unittest.TestCase):
    def test_footprint_and_bubbles(self):
        fp = logic.Footprint()
        fp.add(1000, 100.4, 1.0, False, 5, 1)
        fp.add(4999, 100.9, 2.0, True, 5, 1)
        fp.add(5000, 101.0, 3.0, False, 5, 1)
        rows = sorted(fp.drain()[5])
        self.assertEqual(rows, [(0, 100.0, 1.0, 2.0, 1, 1), (5000, 101.0, 3.0, 0.0, 1, 0)])
        self.assertEqual(fp.drain(), {})
        trades = [(1, 0, 100.0, 2.0, False), (2, 150, 102.0, 2.0, False), (3, 250, 101.0, 9.0, False),
                  (4, 260, 99.0, 6.0, True), (5, 300, 99.0, 1.0, False)]
        big = logic.big_trades_from(trades, 200, 4.0)
        self.assertEqual([(b["side"], b["qty"], b["n_trades"], b["first_agg_id"]) for b in big],
                         [("BUY", 4.0, 2, 1), ("BUY", 9.0, 1, 3), ("SELL", 6.0, 1, 4)])
        self.assertAlmostEqual(big[0]["price"], 101.0)   # VWAP


class CollectorTradeTest(TmpConfig):
    """agg id trung bi bo, thieu thi lay bu qua REST fromId: moi lenh dem dung mot lan (nghiem thu 4)."""

    def test_dedupe_and_fill_gap(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            frm = int(request.url.params["fromId"])
            calls.append(frm)
            rows = [{"a": i, "p": "100.5", "q": "1", "T": 1_000 + i, "m": False} for i in range(frm, min(frm + 2, 105))]
            return httpx.Response(200, json=rows)

        async def main():
            collector = orderflow_collector.Collector(self.config)
            await collector.http.aclose()
            collector.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            state = orderflow_collector.SymbolState(self.config.symbol("BTCUSDT"), {"last_agg_id": 100})
            collector.states["BTCUSDT"] = state
            task = asyncio.create_task(collector.trade_consumer(state))
            for agg in (101, 101, 100, 105, 106, 104):
                state.queue.put_nowait({"a": agg, "p": "100.5", "q": "1", "T": 1_000 + agg, "m": agg % 2 == 0})
            for _ in range(50):
                await asyncio.sleep(0.01)
                if state.queue.empty() and state.last_agg_id == 106:
                    break
            task.cancel()
            collector.flush_all()
            self.assertEqual(calls, [102, 104])
            buy, sell = self.store.delta_sum("BTCUSDT", 0, 10 ** 13)
            self.assertEqual(buy + sell, 6.0)     # 101..106, moi lenh mot lan
            self.assertEqual(self.store.status("BTCUSDT")[0]["last_agg_id"], 106)
            await collector.http.aclose()
            collector.db.close()
        asyncio.run(main())


# ---------------------------------------------------------------- nghiem thu 16: bat / tat symbol khong restart

class CollectorConfigTest(TmpConfig):
    def test_toggle_symbol_subscribes_without_restart(self):
        sent = []

        class FakeWs:
            async def send(self, text):
                sent.append(json.loads(text))

        async def idle(self):
            await asyncio.sleep(3600)

        async def main():
            with mock.patch.object(orderflow_collector.StreamConn, "run", idle):
                collector = orderflow_collector.Collector(self.config)
                await collector.apply_config(self.config.get())
                depth = collector.conns[("futures", "depth")]
                self.assertEqual(depth.streams, {"btcusdt@depth@100ms", "ethusdt@depth@100ms"})
                depth.connected, depth.ws = True, FakeWs()
                cfg = self.config.get()
                self.config.save_editable({**{k: cfg[k] for k in ("enabled", "retention")},
                                           "symbols": [{**s, "enabled": s["symbol"] != "ETHUSDT"} for s in cfg["symbols"]]})
                await collector.apply_config(self.config.get())
                self.assertEqual(depth.streams, {"btcusdt@depth@100ms"})
                self.assertNotIn("ETHUSDT", collector.states)
                self.assertEqual(sent[-1], {"method": "UNSUBSCRIBE", "params": ["ethusdt@depth@100ms"], "id": sent[-1]["id"]})
                self.assertEqual(self.store.status("ETHUSDT")[0]["last_error"], "tat tren admin")
                for c in collector.conns.values():
                    c.task.cancel()
                for s in collector.states.values():
                    s.consumer.cancel()
                await collector.http.aclose()
                collector.db.close()
        asyncio.run(main())

    def test_invalid_file_keeps_last_good_config(self):
        cfg = self.config.get()
        path = cfg["paths"]["override"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"symbols": [{"symbol": "BTCUSDT", "bucket_size": -1}]}), encoding="utf-8")
        time.sleep(0.01)
        again = self.config.get()
        self.assertIs(again, cfg)
        self.assertIn("bucket_size", self.config.last_error)
        with self.assertRaises(orderflow_config.ConfigError):
            self.config.save_editable({"symbols": [{"symbol": "BTC USDT", "bucket_size": 1, "big_trade_min_qty": 1,
                                                    "absorption_min_qty": 1}]})
        with self.assertRaises(orderflow_config.ConfigError):
            orderflow_config.clean_symbol({"symbol": "BTCUSDT", "bucket_size": 1, "big_trade_min_qty": 1,
                                           "absorption_min_qty": 1, "footprint_res_sec": 7})


# ---------------------------------------------------------------- nghiem thu 5: volume profile

class VolumeProfileTest(unittest.TestCase):
    def test_value_area_matches_brute_force(self):
        rng = random.Random(7)
        for _ in range(40):
            rows = [(round(100 + rng.randint(0, 60) * 0.5, 4), rng.random() * 5, rng.random() * 5) for _ in range(rng.randint(5, 200))]
            prof = logic.rebucket_profile(rows, 0.5)
            va = logic.value_area(prof, 0.5)
            # Doc lap: mang day du tu gia thap toi cao, mo rong tu POC sang phia lon hon (bang nhau thi len)
            vol = {}
            for p, b, s in rows:
                key = int(round(p / 0.5))
                vol[key] = vol.get(key, 0) + b + s
            keys = list(range(min(vol), max(vol) + 1))
            arr = [vol.get(k, 0) for k in keys]
            poc = arr.index(max(arr))
            lo = hi = poc
            acc = arr[poc]
            while acc < 0.7 * sum(arr) - 1e-9:
                up = arr[hi + 1] if hi + 1 < len(arr) else -1
                dn = arr[lo - 1] if lo > 0 else -1
                if up >= dn:
                    hi, acc = hi + 1, acc + up
                else:
                    lo, acc = lo - 1, acc + dn
            self.assertAlmostEqual(va["poc"], keys[poc] * 0.5 + 0.25)
            self.assertAlmostEqual(va["vah"], (keys[hi] + 1) * 0.5)
            self.assertAlmostEqual(va["val"], keys[lo] * 0.5)

    def test_nodes_and_cvd_divergence(self):
        rows = [{"price": float(p), "buy": v, "sell": 0, "total": v} for p, v in
                enumerate([1, 5, 9, 5, 1, 0.5, 1, 6, 12, 6, 1])]
        nodes = logic.volume_nodes(rows)
        self.assertEqual([n["price"] for n in nodes["hvn"]], [8.0, 2.0])
        self.assertEqual(nodes["lvn"][0]["price"], 5.0)
        # Gia tao dinh moi nhung CVD thap hon dinh cu
        cvd_rows = [(i * 60_000, b, s, h, h - 10) for i, (b, s, h) in enumerate(
            [(10, 1, 100), (10, 1, 105), (10, 1, 110), (1, 2, 108), (1, 2, 107), (1, 2, 106), (1, 2, 109), (1, 3, 112)])]
        points = logic.cvd_points(cvd_rows)
        self.assertEqual(points[2]["cvd"], 27)
        div = logic.divergence(points, [r[3] for r in cvd_rows], [r[4] for r in cvd_rows])
        self.assertEqual(div["type"], "bearish")
        # neo ngay UTC: ve 0 dau ngay, ngay dau cong them phan tu dau ngay toi from
        rows2 = [(DAY - 60_000, 1, 0, 0, 0), (DAY, 2, 0, 0, 0)]
        pts = logic.cvd_points(rows2, day_anchor=True, day_base={0: 5.0})
        self.assertEqual([p["cvd"] for p in pts], [6.0, 2.0])


# ---------------------------------------------------------------- heatmap

class HeatmapTest(unittest.TestCase):
    def test_average_and_merge_buckets(self):
        import zlib
        pack = lambda vals: zlib.compress(__import__("array").array("f", vals).tobytes())  # noqa: E731
        snaps = [(0, 100.0, 1.0, 4, pack([1, 2, 3, 4]), pack([0, 0, 0, 8]), 101.0, 103.0),
                 (1000, 101.0, 1.0, 4, pack([3, 3, 3, 3]), pack([0, 0, 0, 0]), 102.0, 104.0),
                 (5000, 100.0, 2.0, 2, pack([9, 9]), pack([9, 9]), 1, 2)]   # bucket khac: bo qua
        grid = logic.heatmap_grid(snaps, start=0, end=4000, step=2000, price_min=100, price_max=104.9,
                                  bucket_size=1.0, max_rows=3, unpack=zlib.decompress)
        self.assertEqual((grid["n_cols"], grid["n_rows"], grid["bucket_size"], grid["base_price"]), (2, 3, 2.0, 100.0))
        # cot 0: trung binh hai snapshot (snapshot 2 lech base 1 bucket); dong 100-101 / 102-103 / 104-105
        self.assertEqual([float(x) for x in grid["bids"][:3]], [((1 + 2) + 3) / 2, ((3 + 4) + (3 + 3)) / 2, (0 + 3) / 2])
        self.assertEqual(grid["filled"], [True, False])
        self.assertEqual(grid["bbo"][0], [101.5, 103.5])

    def test_aggregated_rows_give_same_grid_as_raw(self):
        """Dong gop san 10s (trung binh + n_snapshots) cho ra dung luoi nhu doc tung snapshot 2 giay."""
        import zlib
        rng = random.Random(5)
        raw, agg_rows = [], []
        agg = logic.SnapshotAgg(10)
        pack = orderflow_store.pack
        for i in range(300):
            t = i * 2000
            base = 1000.0 + rng.randint(-3, 3)
            bids = [rng.random() * 5 for _ in range(20)]
            asks = [rng.random() * 5 for _ in range(20)]
            raw.append((t, base, 1.0, 20, pack(bids), pack(asks), base + 9, base + 10))
            for row in agg.add({"ts": t, "mid": base + 9.5, "best_bid": base + 9, "best_ask": base + 10, "bucket_size": 1.0,
                                "base_price": base, "bids": bids, "asks": asks}):
                agg_rows.append((row["ts"], row["base_price"], 1.0, row["n_buckets"], pack(row["bids"]), pack(row["asks"]),
                                 row["best_bid"], row["best_ask"], row["n_snapshots"]))
        last = agg.close_all()[0]
        agg_rows.append((last["ts"], last["base_price"], 1.0, last["n_buckets"], pack(last["bids"]), pack(last["asks"]),
                         last["best_bid"], last["best_ask"], last["n_snapshots"]))
        opts = dict(start=0, end=600_000, step=30_000, price_min=995, price_max=1025, bucket_size=1.0, max_rows=100,
                    unpack=zlib.decompress)
        a = logic.heatmap_grid(raw, **opts)
        b = logic.heatmap_grid(agg_rows, **opts)
        self.assertEqual(sum(r[8] for r in agg_rows), 300)
        for x, y in zip(a["bids"], b["bids"]):
            self.assertAlmostEqual(float(x), float(y), delta=max(0.02, abs(float(x)) * 0.01))
        self.assertEqual(a["bbo"][3], b["bbo"][3])


# ---------------------------------------------------------------- nghiem thu 13: tuong thanh khoan

def snap(ts, mid, bids, asks, base=9700.0, n=61, size=10.0):
    b = [1.0 if base + i * size < mid - size else 0.0 for i in range(n)]
    a = [1.0 if base + i * size > mid else 0.0 for i in range(n)]
    for price, qty in bids.items():
        b[int(round((price - base) / size))] = qty
    for price, qty in asks.items():
        a[int(round((price - base) / size))] = qty
    return {"ts": ts, "mid": mid, "bucket_size": size, "base_price": base, "n_buckets": n, "bids": b, "asks": a}


class WallTest(unittest.TestCase):
    def build(self):
        snaps, fp = [], []
        for i in range(900):                       # 30 phut, snapshot 2 giay
            t = i * 2000
            m = t / 60000
            mid = 10030.0 if 19.5 <= m < 22 else 9955.0 if 24.5 <= m < 27 else 10000.0
            bids = {9900.0: 20.0}                                   # A: ton tai ca 30 phut
            if m < 25:
                bids[9950.0] = 30.0                                 # C: bi khop het luc phut 25
            asks = {}
            if m < 20:
                asks[10050.0] = 20.0                                # B: bi rut luc phut 20, khong co khop
            asks[10250.0] = max(1.0, 20.0 - 19.0 * m / 30.0)        # D: giam dan khi gia o xa
            snaps.append(snap(t, mid, bids, asks))
        for k in range(6):                                          # khop 29 tai 9950 quanh phut 25
            fp.append((25 * MIN - 5000 + k * 5000, 9950.0, 0.0, 29.0 / 6))
        return snaps, fp

    def test_statuses(self):
        snaps, fp = self.build()
        walls = logic.detect_walls(snaps, fp, bucket_size=10, wall_k=5, min_persist_min=15, range_pct=0.03,
                                   include_inactive=True)
        by_price = {w["price"]: w for w in walls}
        self.assertEqual(by_price[9900.0]["status"], "ACTIVE")
        self.assertGreaterEqual(by_price[9900.0]["persisted_min"], 20)
        self.assertEqual(by_price[10050.0]["status"], "PULLED")
        self.assertEqual(by_price[9950.0]["status"], "CONSUMED")
        self.assertEqual(by_price[10250.0]["status"], "FADED")
        active = logic.detect_walls(snaps, fp, bucket_size=10, wall_k=5, min_persist_min=15, range_pct=0.03)
        self.assertEqual([w["price"] for w in active], [9900.0])

    def test_short_lived_or_flickering(self):
        snaps = [snap(i * 2000, 10000.0, {9900.0: 20.0} if (i < 300 and i % 4 != 3) else {}, {}) for i in range(600)]
        walls = logic.detect_walls(snaps, [], bucket_size=10, wall_k=5, min_persist_min=5, range_pct=0.03,
                                   include_inactive=True)
        self.assertEqual(len(walls), 1)                 # dut 1 snapshot moi 4 van la mot tuong lien tuc
        walls = logic.detect_walls(snaps, [], bucket_size=10, wall_k=5, min_persist_min=15, range_pct=0.03,
                                   include_inactive=True)
        self.assertEqual(walls, [])                     # chi ton tai 10 phut


# ---------------------------------------------------------------- nghiem thu 14: hap thu

class AbsorptionTest(unittest.TestCase):
    T0 = 10 * MIN

    def build(self):
        snaps, fp = [], []
        for i in range(-60, 900):
            t = self.T0 + i * 2000
            broke = t >= self.T0 + 10 * MIN + 3000
            zone = {} if broke and t < self.T0 + 11 * MIN else {9980.0: 30.0, 9990.0: 30.0, 10000.0: 30.0}
            snaps.append(snap(t, 10005.0, zone, {10010.0: 30.0}, base=9800.0, n=41))
        for k in range(12):                                  # ban chu dong 72 tai 9990, gia khong nhuc nhich
            fp.append((self.T0 + k * 5000, 9990.0, 0.0, 6.0))
            fp.append((self.T0 + k * 5000, 10000.0, 1.0, 0.0))
        for k in range(2, 17):                               # 15 phut sau: gia di len toi 10100
            fp.append((self.T0 + k * MIN, 10000.0 + k * 6, 2.0, 0.0))
        t1 = self.T0 + 10 * MIN                              # truong hop 2: ban lon va gia xuyen qua
        for k, (price, qty) in enumerate([(9990.0, 30.0), (9980.0, 30.0), (9960.0, 20.0), (9940.0, 20.0), (9920.0, 10.0)]):
            fp.append((t1 + k * 5000, price, 0.0, qty))
        return snaps, sorted(fp)

    def test_detects_absorption_but_not_breakthrough(self):
        snaps, fp = self.build()
        events = logic.detect_absorption(fp, snaps, bucket_size=10, min_qty=50, start=self.T0 - MIN,
                                         end=self.T0 + 12 * MIN, data_end=self.T0 + 30 * MIN)
        self.assertEqual(len(events), 1, events)
        e = events[0]
        self.assertEqual((e["side_label"], e["price"], e["price_displacement_buckets"]), ("BULLISH_ABSORPTION", 9990.0, 0))
        self.assertGreaterEqual(e["aggressor_qty"], 50)
        self.assertGreaterEqual(e["wall_qty_after"], 0.5 * e["wall_qty_before"])
        self.assertIsNotNone(e["outcome_15m"])
        self.assertGreater(e["outcome_15m"]["max_favorable"], e["outcome_15m"]["max_adverse"])

    def test_requires_book(self):
        with self.assertRaises(ValueError):
            logic.detect_absorption([], [], bucket_size=10, min_qty=1, start=0, end=1, data_end=1)


# ---------------------------------------------------------------- store: don du lieu

class RetentionTest(TmpConfig):
    def test_rollup_and_delete(self):
        now = 400 * DAY
        with closing(self.store.conn()) as conn, conn:
            S = orderflow_store.OrderflowStore
            S.add_footprint(conn, "BTCUSDT", 5, [(now - 40 * DAY + k * 5000, 100.0, 1.0, 2.0, 1, 1) for k in range(24)])
            S.add_footprint(conn, "BTCUSDT", 5, [(now - DAY, 100.0, 3.0, 0.0, 1, 0)])
            S.add_footprint(conn, "BTCUSDT", 60, [(now - 370 * DAY, 100.0, 1.0, 0.0, 1, 0)])
            S.insert_big_trades(conn, "BTCUSDT", [{"ts": now - 200 * DAY, "price": 1, "qty": 9, "side": "BUY",
                                                    "n_trades": 1, "first_agg_id": 1, "last_agg_id": 1}])
            S.insert_snapshot(conn, "BTCUSDT", "futures", {"ts": now - 15 * DAY, "mid": 1, "best_bid": 1, "best_ask": 1,
                                                            "bucket_size": 1, "base_price": 0, "n_buckets": 1, "bids": [1], "asks": [1]})
        self.store.add_gap("BTCUSDT", "depth", now - 400 * DAY, now - 399 * DAY, "x")
        out = self.store.retention(self.config.get()["retention"], now=now)
        self.assertEqual(out["footprint_rolled"], 24)
        with closing(self.store.conn()) as conn:
            rows = conn.execute("SELECT res_sec, COUNT(*), SUM(buy_vol), SUM(sell_vol) FROM trade_footprint GROUP BY res_sec").fetchall()
            self.assertEqual([tuple(r) for r in rows], [(5, 1, 3.0, 0.0), (60, 2, 24.0, 48.0)])   # 2 phut 60s, tong giu nguyen
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM big_trades").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ob_snapshots").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orderflow_gaps").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA auto_vacuum").fetchone()[0], 2)   # INCREMENTAL


# ---------------------------------------------------------------- nghiem thu 1: backfill chay lai khong nhan doi

def make_zip(trades, header=True) -> bytes:
    buf = io.StringIO()
    if header:
        buf.write("agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n")
    for a, ts, p, q, sell in trades:
        buf.write(f"{a},{p},{q},{a},{a},{ts},{'true' if sell else 'false'}\n")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("x.csv", buf.getvalue())
    return out.getvalue()


class BackfillTest(TmpConfig):
    DAY1 = date(2026, 11, 10)

    def trades(self, day_ms, start_id, n, step_ms=9000):
        rng = random.Random(start_id)
        return [(start_id + i, day_ms + i * step_ms, 100 + rng.randint(0, 50), round(rng.random() * 8, 3), i % 3 == 0)
                for i in range(n)]

    def client(self, files, rest_trades):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "data.binance.vision" in url:
                name = url.rsplit("/", 1)[-1]
                return httpx.Response(200, content=files[name]) if name in files else httpx.Response(404)
            q = request.url.params
            if "fromId" in q:
                rows = [t for t in rest_trades if t[0] >= int(q["fromId"])][:1000]
            else:
                rows = [t for t in rest_trades if int(q["startTime"]) <= t[1] <= int(q["endTime"])][:1000]
            return httpx.Response(200, json=[{"a": a, "T": ts, "p": str(p), "q": str(q_), "m": s} for a, ts, p, q_, s in rows],
                                  headers={"x-mbx-used-weight-1m": "20"})
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_idempotent_and_matches_file(self):
        d1 = orderflow_backfill.day_ms(self.DAY1)
        day1 = self.trades(d1, 1000, 9000)                       # ~22.5 gio
        d2 = d1 + DAY                                           # file chua co -> REST
        day2 = self.trades(d2, 20_000, 2500)
        today = date(2026, 11, 12)
        d3 = d2 + DAY
        day3 = self.trades(d3, 40_000, 600)                     # hom nay toi ~01:30
        files = {"BTCUSDT-aggTrades-2026-11-10.zip": make_zip(day1)}
        clock = lambda: d3 + 2 * 3_600_000                      # noqa: E731 - 02:00 UTC hom nay
        counts = []
        for _ in range(2):
            job = self.store.create_job("backfill", "BTCUSDT", "2026-11-10", "2026-11-12")
            out = orderflow_backfill.run(self.config, job["id"], client=self.client(files, day2 + day3), today=today, clock=clock)
            self.assertEqual([d["source"] for d in out["days"]], ["file", "rest", "rest"])
            with closing(self.store.conn()) as conn:
                counts.append((conn.execute("SELECT COUNT(*), SUM(buy_vol), SUM(sell_vol) FROM trade_footprint").fetchone()[:],
                               conn.execute("SELECT COUNT(*) FROM big_trades").fetchone()[0]))
        self.assertEqual(counts[0], counts[1])
        buy, sell = self.store.delta_sum("BTCUSDT", d1, d1 + DAY)
        self.assertAlmostEqual(buy + sell, sum(t[3] for t in day1), places=6)
        self.assertEqual(self.store.job(job["id"])["status"], "done")
        # Collector khong chay: last_agg_id day toi lenh cuoi da backfill
        self.assertEqual(self.store.status("BTCUSDT")[0]["last_agg_id"], day3[-1][0])

    def test_today_stops_before_collector_frontier(self):
        today = date(2026, 11, 12)
        d3 = orderflow_backfill.day_ms(today)
        day3 = self.trades(d3, 40_000, 1200, step_ms=6000)       # 0h -> 2h
        now = int(time.time() * 1000)
        with closing(self.store.conn()) as conn, conn:
            orderflow_store.OrderflowStore.upsert_status(conn, "BTCUSDT", {"connected": 1, "updated_at": now,
                                                                         "last_event_ts": d3 + 3_600_000, "last_agg_id": 999_999})
        job = self.store.create_job("backfill", "BTCUSDT", "2026-11-12", "2026-11-12")
        out = orderflow_backfill.run(self.config, job["id"], client=self.client({}, day3), today=today,
                                     clock=lambda: d3 + 5 * 3_600_000)
        self.assertEqual(out["days"][0]["end_utc"], "2026-11-12 00:59")
        with closing(self.store.conn()) as conn:
            self.assertLess(conn.execute("SELECT MAX(ts) FROM trade_footprint").fetchone()[0], d3 + 3_600_000 - 60_000)
        self.assertEqual(self.store.status("BTCUSDT")[0]["last_agg_id"], 999_999)   # collector dang chay: khong dung

    def test_validation(self):
        with self.assertRaises(ValueError):
            orderflow_backfill.validate_range("2026-01-01", "2026-06-01", 90, date(2026, 11, 12))
        with self.assertRaises(ValueError):
            orderflow_backfill.validate_range("2026-11-12", "2026-11-13", 90, date(2026, 11, 12))
        with self.assertRaises(ValueError):
            orderflow_backfill.validate_range("2026-11-12", "2026-11-11", 90, date(2026, 11, 12))
        self.assertEqual(len(orderflow_backfill.validate_range("2026-08-15", "2026-11-12", 90, date(2026, 11, 12))), 90)

    def test_spot_zip_microseconds_without_header(self):
        path = self.tmp / "s.zip"
        path.write_bytes(make_zip([(1, 1_700_000_000_000_123, 10.5, 2.0, True)], header=False))
        self.assertEqual(list(orderflow_backfill.read_zip(path)), [(1, 1_700_000_000_000, 10.5, 2.0, True)])


if __name__ == "__main__":
    unittest.main()
