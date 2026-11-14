# TM - #BOOKMAP - Order flow Bookmap
"""Nghiem thu order flow phia server: API dashboard (chung auth /setup-dashboard, viewer goi duoc), SSE, trang admin
Order Flow, MCP tools, hieu nang heatmap 24h (nghiem thu 11). DB tam, khong goi mang.

Checkbox, ve canvas, phim tat chay o trinh duyet: xem muc 11 cua ticket va lan chay thu that (README order flow).
"""
from __future__ import annotations

import base64
import gzip
import json
import math
import random
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_dashboard as td  # noqa: E402  dat BAMCP_DATA_ROOT ve thu muc tam truoc khi import server
from test_dashboard import BASE, VIEWER_PASS, HttpBase  # noqa: E402

import orderflow_store  # noqa: E402
import server  # noqa: E402

S = orderflow_store.OrderflowStore
SYM = "BTCUSDT"


def reset_orderflow() -> None:
    cfg = server._of_cfg()
    db = cfg["paths"]["db"]
    if Path(td._TMP).resolve() not in db.resolve().parents:
        raise RuntimeError(f"test chi chay tren thu muc tam, dang tro vao {db}")
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    orderflow_store._MIGRATED.clear()
    cfg["paths"]["override"].unlink(missing_ok=True)
    server.OF_CONFIG.get(force=True)


def seed(now: int, minutes: int = 40, book: bool = True) -> None:
    """Lenh khop moi 5 giay quanh 67,000, bong bong, va snapshot so lenh 2 giay co mot tuong bid tai 66,900."""
    rng = random.Random(1)
    store = server._of_store()
    start = now - minutes * 60_000
    start -= start % 5000
    with closing(store.conn()) as conn, conn:
        fp = [(t, 67000.0 + 10 * rng.randint(-5, 5), rng.random() * 3, rng.random() * 3, 3, 3)
              for t in range(start, now, 5000)]
        S.add_footprint(conn, SYM, 5, fp)
        S.insert_big_trades(conn, SYM, [{"ts": start + k * 60_000, "price": 67000.0 + k, "qty": 5 + k,
                                         "side": "BUY" if k % 2 else "SELL", "n_trades": 3, "first_agg_id": 100 + k,
                                         "last_agg_id": 102 + k} for k in range(minutes)])
        if book:
            for t in range(start, now, 2000):
                bids = [0.0 if 66000 + i * 10 >= 67000 else 1.0 for i in range(201)]
                asks = [0.0 if 66000 + i * 10 < 67010 else 1.0 for i in range(201)]
                bids[90] = 25.0                              # 66,900: tuong ton tai suot
                S.insert_snapshot(conn, SYM, "futures", {"ts": t, "mid": 67005.0, "best_bid": 67000.0, "best_ask": 67010.0,
                                                          "bucket_size": 10.0, "base_price": 66000.0, "n_buckets": 201,
                                                          "bids": bids, "asks": asks})
        S.upsert_status(conn, SYM, {"connected": 1, "last_event_ts": now, "last_snapshot_ts": now - 2000,
                                    "last_agg_id": 200, "book_levels": 1500, "market": "futures", "book_synced": 1})


class OrderflowHttpTest(HttpBase):
    def setUp(self):
        super().setUp()
        reset_orderflow()
        self.addCleanup(reset_orderflow)
        self.now = int(time.time() * 1000)
        seed(self.now)

    def q(self, **extra):
        return {"symbol": SYM, "from": self.now - 30 * 60_000, "to": self.now, **extra}

    def test_requires_login_viewer_allowed(self):
        """Nghiem thu 7: tai khoan view-only goi duoc moi API order flow; chua dang nhap thi 401."""
        paths = ("status", "bubbles", "cvd", "volume-profile", "orderbook", "heatmap", "stream")

        async def main(c):
            for path in paths:
                res = await c.get(f"{BASE}/api/orderflow/{path}", params=self.q())
                self.assertEqual(res.status_code, 401, path)
            await self.login(c, "guest", VIEWER_PASS)
            status = (await c.get(f"{BASE}/api/orderflow/status", params={"symbol": SYM})).json()
            info = status["symbol"]
            self.assertTrue(info["enabled"] and info["has_trades"] and info["has_book"] and info["collector_live"])
            self.assertEqual(info["bucket_size"], 10.0)
            bubbles = (await c.get(f"{BASE}/api/orderflow/bubbles", params=self.q())).json()
            self.assertTrue(bubbles and all(b["qty"] >= 5 for b in bubbles))
            self.assertEqual(sorted(bubbles, key=lambda b: b["ts"]), bubbles)
            few = (await c.get(f"{BASE}/api/orderflow/bubbles", params=self.q(min_qty=30))).json()
            self.assertTrue(all(b["qty"] >= 30 for b in few) and len(few) < len(bubbles))
            cvd = (await c.get(f"{BASE}/api/orderflow/cvd", params=self.q(res=300))).json()
            self.assertTrue(all(p["t"] % 300_000 == 0 for p in cvd))
            self.assertAlmostEqual(cvd[-1]["cvd"], sum(p["delta"] for p in cvd), places=3)
            vp = (await c.get(f"{BASE}/api/orderflow/volume-profile", params=self.q())).json()
            self.assertTrue(66950 <= vp["poc"] <= 67060 and vp["val"] < vp["poc"] < vp["vah"])
            ob = (await c.get(f"{BASE}/api/orderflow/orderbook", params={"symbol": SYM, "range_pct": 0.01})).json()
            self.assertEqual(ob["bids"][0][0], 66990.0)
            self.assertIn([66900.0, 25.0], ob["bids"])
            for path in paths[:-1]:
                res = await c.get(f"{BASE}/api/orderflow/{path}", params=self.q())
                self.assertEqual(res.status_code, 200, path)
                self.assertEqual(res.headers["x-frame-options"], "DENY")
        self.session(main)

    def test_heatmap_payload(self):
        async def main(c):
            await self.login(c)
            res = await c.get(f"{BASE}/api/orderflow/heatmap", params=self.q(price_min=66500, price_max=67500,
                                                                               max_cols=300, max_rows=40),
                              headers={"accept-encoding": "gzip"})
            self.assertEqual(res.status_code, 200)
            out = res.json()
            self.assertLessEqual(out["n_rows"], 40)
            self.assertEqual(out["bucket_size"], 30.0)              # 101 bucket > 40 dong -> gop 3 bucket
            bids = base64.b64decode(out["bids"])
            self.assertEqual(len(bids), 4 * out["n_rows"] * out["n_cols"])
            self.assertEqual(len(out["t"]), out["n_cols"])
            self.assertEqual(out["coverage_start"] is not None, True)
            import array
            arr = array.array("f")
            arr.frombytes(bids)
            row = int((66900 - out["base_price"]) // out["bucket_size"])
            last_full = max(i for i, f in enumerate(out["filled"]) if f)
            self.assertGreaterEqual(arr[last_full * out["n_rows"] + row], 25.0)   # tuong 66,900 (gop 3 bucket)
        self.session(main)

    def test_stream_once(self):
        """SSE chi day phan cua cac lop trong layers."""
        async def main(c):
            await self.login(c, "guest", VIEWER_PASS)
            params = {"symbol": SYM, "layers": "bubbles,cvd,cob", "once": "1", "after_snap": self.now - 10_000,
                      "after_agg": 100, "res": 60}
            res = await c.get(f"{BASE}/api/orderflow/stream", params=params)
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.headers["content-type"].startswith("text/event-stream"))
            data = json.loads(res.text.split("event: tick\ndata: ", 1)[1].split("\n\n", 1)[0])
            self.assertIn("bubbles", data)
            self.assertIn("cob", data)
            self.assertNotIn("heatmap", data)
            self.assertLessEqual(len(data["cob"]["bids"]), 20)
            self.assertTrue(all(b["qty"] >= 5 for b in data["bubbles"]))
            bad = await c.get(f"{BASE}/api/orderflow/stream", params={"symbol": SYM, "layers": "vp"})
            self.assertEqual(bad.status_code, 400)
        self.session(main)

    def test_pages_have_orderflow_containers(self):
        async def main(c):
            await self.login(c, "guest", VIEWER_PASS)
            for url in (BASE, f"{BASE}/full?of=heatmap,gaps,bubbles,buy,sell,vp,vplines"):
                page = (await c.get(url)).text
                for marker in ('id="chartGrid"', 'id="chartMain"', 'id="ofSide"', 'id="cvdPane"', 'id="ofTip"',
                               "/static/orderflow.js?v="):
                    self.assertIn(marker, page)
                self.assertLess(page.index("orderflow.js"), page.index("dashboard.js"))
            self.assertEqual((await c.get(f"{BASE}/static/orderflow.js")).status_code, 200)
            # Cookie dashboard (ke ca viewer) khong mo duoc trang admin
            res = await c.get(server.ADMIN_OF_PATH)
            self.assertEqual(res.status_code, 302)
            self.assertIn(server.ADMIN_LOGIN_PATH, res.headers["location"])
        self.session(main)

    def test_symbol_without_orderflow(self):
        cfg = server._of_cfg()
        server.OF_CONFIG.save_editable({"enabled": True, "retention": cfg["retention"],
                                        "symbols": [{**s, "enabled": False} for s in cfg["symbols"]]})

        async def main(c):
            await self.login(c, "guest", VIEWER_PASS)
            info = (await c.get(f"{BASE}/api/orderflow/status", params={"symbol": SYM})).json()["symbol"]
            self.assertFalse(info["enabled"])
        self.session(main)


class HeatmapPerfTest(HttpBase):
    """Nghiem thu 11: heatmap 24h BTC (snapshot 2 giay, +-3%, bucket 10 = ~405 bucket) tra ve < 1.5 giay, payload < 3MB."""

    def setUp(self):
        super().setUp()
        reset_orderflow()
        self.addCleanup(reset_orderflow)

    def test_24h(self):
        now = int(time.time() * 1000)
        start = now - 86_400_000
        rng = random.Random(3)
        store = server._of_store()
        mid = 67000.0
        # Nhu collector: moi snapshot vua ghi ban tho vua cong vao cac cua so gop 10s / 60s / 300s
        aggs = [server.orderflow_logic.SnapshotAgg(r) for r in server._of_cfg()["tech"]["heatmap_agg_levels"]]
        with closing(store.conn()) as conn, conn:
            rows = []
            for k, t in enumerate(range(start, now, 2000)):
                mid += rng.gauss(0, 3)
                base = math.floor(mid * 0.97 / 10) * 10
                n = 405
                bids = [0.0] * n
                asks = [0.0] * n
                for i in range(n):
                    price = base + i * 10
                    depth = rng.lognormvariate(2.5, 0.8) * (1 + 3 * math.exp(-abs(price - mid) / 300))
                    if price < mid:
                        bids[i] = depth
                    else:
                        asks[i] = depth
                rows.append((orderflow_store.guid(), SYM, "futures", t, mid, mid - 5, mid + 5, 10.0, base, n,
                             orderflow_store.pack(bids), orderflow_store.pack(asks)))
                snap = {"ts": t, "mid": mid, "best_bid": mid - 5, "best_ask": mid + 5, "bucket_size": 10.0,
                        "base_price": base, "bids": bids, "asks": asks}
                for agg in aggs:
                    for item in agg.add(snap):
                        S.insert_agg(conn, SYM, "futures", item)
                if len(rows) >= 2000:
                    conn.executemany("INSERT INTO ob_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                    rows = []
            conn.executemany("INSERT INTO ob_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            S.add_footprint(conn, SYM, 5, [(now - 5000, mid, 1, 1, 1, 1)])

        async def main(c):
            await self.login(c)
            params = {"symbol": SYM, "from": start, "to": now, "price_min": mid * 0.97, "price_max": mid * 1.03,
                      "max_cols": 1500}
            t0 = time.perf_counter()
            res = await c.get(f"{BASE}/api/orderflow/heatmap", params=params, headers={"accept-encoding": "gzip"})
            elapsed = time.perf_counter() - t0
            self.assertEqual(res.status_code, 200)
            wire = len(gzip.compress(res.content, 5)) if res.headers.get("content-encoding") != "gzip" else None
            size = int(res.headers.get("content-length") or 0) or wire or len(res.content)
            out = res.json()
            print(f"\nheatmap 24h: {out['snapshots']} snapshot -> {out['n_cols']}x{out['n_rows']}, "
                  f"{elapsed:.2f}s, payload {size / 1e6:.2f} MB (gzip)", file=sys.stderr)
            self.assertEqual(out["snapshots"], 43200)          # moi snapshot deu duoc tinh (gop san + tho o cua so cuoi)
            self.assertEqual(out["agg_res_sec"], 10)
            self.assertLess(elapsed, 1.5)
            self.assertLess(size, 3_000_000)
        self.session(main)


class OrderflowAdminTest(HttpBase):
    def setUp(self):
        super().setUp()
        reset_orderflow()
        self.addCleanup(reset_orderflow)
        seed(int(time.time() * 1000), minutes=20)

    def session(self, coro_fn, ip="10.0.0.1", **kwargs):
        async def logged_in(c):
            self.assertEqual((await self.admin_login(c)).status_code, 303)
            return await coro_fn(c)
        return super().session(logged_in, ip, **kwargs)

    def test_page_save_status_backfill(self):
        async def main(c):
            page = await c.get(server.ADMIN_OF_PATH)
            self.assertEqual(page.status_code, 200)
            self.assertIn("Order Flow", page.text)
            self.assertIn(f'href="{server.ADMIN_OF_PATH}"', page.text)
            act = server.ADMIN_OF_ACTION_PATH
            st = (await c.post(act, json={"action": "status"})).json()
            row = next(r for r in st["rows"] if r["symbol"] == SYM)
            self.assertTrue(row["collector_live"])
            cfg = server._of_cfg()
            new = {"enabled": True, "retention": {**cfg["retention"], "heatmap_days": 7},
                   "symbols": [*cfg["symbols"], {"symbol": "SOLUSDT", "market": "spot", "enabled": True, "bucket_size": 0.05,
                                                 "big_trade_min_qty": 500, "absorption_min_qty": 5000}]}
            res = await c.post(act, json={"action": "save", "config": new})
            self.assertEqual(res.status_code, 200, res.text)
            saved = json.loads(cfg["paths"]["override"].read_text(encoding="utf-8"))
            self.assertEqual(saved["retention"]["heatmap_days"], 7)
            self.assertEqual(saved["symbols"][-1]["depth_range_pct"], 0.03)    # truong bo trong lay mac dinh
            self.assertEqual(server.OF_CONFIG.symbol("SOLUSDT")["market"], "spot")
            bad = await c.post(act, json={"action": "save", "config": {**new, "symbols": [{"symbol": "X", "bucket_size": 0}]}})
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(json.loads(cfg["paths"]["override"].read_text(encoding="utf-8")), saved)
            res = await c.post(act, json={"action": "backfill", "symbol": SYM, "from_date": "2026-01-01", "to_date": "2026-06-01"})
            self.assertEqual(res.status_code, 400)
            with mock.patch("subprocess.Popen") as popen:
                popen.return_value = mock.Mock(pid=4242, poll=mock.Mock(return_value=None))
                res = await c.post(act, json={"action": "backfill", "symbol": SYM, "from_date": "2026-11-07",
                                              "to_date": "2026-11-08"})
                self.assertEqual(res.status_code, 200, res.text)
                args = popen.call_args[0][0]
                self.assertEqual(args[-2:], ["--job", res.json()["job_id"]])
                again = await c.post(act, json={"action": "backfill", "symbol": SYM, "from_date": "2026-11-07",
                                                "to_date": "2026-11-08"})
                self.assertIn("dang co job", again.json()["note"])
                self.assertEqual(popen.call_count, 1)
        self.session(main)


class OrderflowToolsTest(td.Base):
    def setUp(self):
        super().setUp()
        reset_orderflow()
        self.addCleanup(reset_orderflow)
        self.now = int(time.time() * 1000)
        seed(self.now, minutes=40)

    def test_tools(self):
        status = server.get_orderflow_status()
        self.assertTrue(status["enabled"])
        btc = next(s for s in status["symbols"] if s["symbol"] == SYM)
        self.assertTrue(btc["collector_live"])
        summary = server.get_orderflow_summary(SYM, 30)
        for key in ("symbol", "from", "to", "data_coverage", "trades", "cvd", "bubbles", "book"):
            self.assertIn(key, summary)
        self.assertTrue(summary["book"]["available"])
        self.assertEqual(summary["book"]["walls_bid"][0]["price"], 66900.0)
        self.assertGreater(summary["bubbles"]["buy"]["count"] + summary["bubbles"]["sell"]["count"], 0)
        cvd = server.get_cvd(SYM, str(self.now - 30 * 60_000), str(self.now), 300)
        self.assertEqual(cvd["points"], len(cvd["series"]))
        self.assertIn("divergence", cvd)
        vp = server.get_volume_profile(SYM, str(self.now - 30 * 60_000), str(self.now))
        self.assertTrue(vp["val"] < vp["poc"] < vp["vah"])
        walls = server.get_liquidity_walls(SYM, 0.03, 15)
        self.assertEqual([w["price"] for w in walls["walls"]], [66900.0])
        self.assertEqual(walls["walls"][0]["status"], "ACTIVE")
        ob = server.get_orderbook_snapshot(SYM, "", 0.005)
        self.assertEqual(ob["bids"][0][0], 66990.0)
        self.assertIsNotNone(ob["imbalance_1pct"])
        events = server.get_absorption_events(SYM)
        self.assertIn("events", events)
        with self.assertRaises(ValueError):
            server.get_absorption_events("ETHUSDT")          # chua co so lenh ETH
        with self.assertRaises(ValueError):
            server.get_cvd(SYM, str(self.now - 31 * 86_400_000), str(self.now), 60)
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value = mock.Mock(pid=1, poll=mock.Mock(return_value=None))
            job = server.backfill_orderflow(SYM, "2026-11-10", "2026-11-11")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(server._of_store().job(job["job_id"])["pid"], 1)

    def test_registered_tools(self):
        names = set(server.mcp._tool_manager._tools) if hasattr(server.mcp, "_tool_manager") else None
        if names is None:
            self.skipTest("SDK khong lo danh sach tool")
        for name in ("get_orderflow_summary", "get_liquidity_walls", "get_cvd", "get_volume_profile",
                     "get_absorption_events", "get_orderbook_snapshot", "backfill_orderflow", "get_orderflow_status"):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()
