# TM - #M5-FETCH - Per-timeframe schedules + M5

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="bamcp-fs-")
os.environ.setdefault("BAMCP_DATA_ROOT", _TMP)
os.environ.setdefault("BAMCP_ACCOUNT_ENABLED", "false")

import cron_utc  # noqa: E402
import fetch_schedules  # noqa: E402
import kline_store  # noqa: E402
import server  # noqa: E402

M5 = 300_000
H1 = 3_600_000


def tearDownModule():
    if Path(server.DATA_ROOT).resolve() != Path(_TMP).resolve():
        shutil.rmtree(_TMP, ignore_errors=True)


def ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=cron_utc.UTC).timestamp() * 1000)


def utc(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, cron_utc.UTC).strftime("%Y-%m-%d %H:%M:%S")


class CronTest(unittest.TestCase):
    def test_defaults(self):
        after = ms("2026-11-01 10:07:30")
        expect = {
            "5 */5 * * * *": "2026-11-01 10:10:05",
            "5 */15 * * * *": "2026-11-01 10:15:05",
            "5 0 * * * *": "2026-11-01 11:00:05",
            "5 0 */4 * * *": "2026-11-01 12:00:05",
            "5 0 0 * * *": "2026-11-02 00:00:05",
            "5 0 0 * * MON": "2026-11-02 00:00:05",
        }
        for expr, want in expect.items():
            self.assertEqual(utc(cron_utc.next_after(cron_utc.parse(expr), after)), want, expr)

    def test_next_runs_and_ranges(self):
        spec = cron_utc.parse("0 30 9-10 * JAN-MAR 1-5")
        runs = [utc(t) for t in cron_utc.next_runs(spec, ms("2026-12-31 23:00:00"), 3)]
        self.assertEqual(runs, ["2027-01-01 09:30:00", "2027-01-01 10:30:00", "2027-01-04 09:30:00"])
        self.assertEqual(utc(cron_utc.next_after(cron_utc.parse("0 0 0 * * 7"), ms("2026-11-01 00:00:00"))),
                         "2026-11-08 00:00:00")

    def test_invalid(self):
        for expr in ("* * * * *", "61 * * * * *", "x * * * * *", "*/0 * * * * *", "5-1 * * * * *"):
            with self.assertRaises(ValueError, msg=expr):
                cron_utc.parse(expr)

    def test_warnings(self):
        now = ms("2026-11-01 10:07:30")
        self.assertEqual(cron_utc.warnings(cron_utc.parse("5 0 * * * *"), H1, now), [])
        dense = cron_utc.warnings(cron_utc.parse("5 */5 * * * *"), H1, now)
        self.assertTrue(any("day hon" in w for w in dense), dense)
        late = cron_utc.warnings(cron_utc.parse("5 30 * * * *"), H1, now)
        self.assertTrue(any("khong bam moc" in w for w in late), late)
        week = cron_utc.warnings(cron_utc.parse("5 0 0 * * MON"), 7 * 86_400_000, now)
        self.assertEqual(week, [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bamcp-fs-case-"))
        self.store = fetch_schedules.ScheduleStore(
            self.dir / "bamcp.db", supported=lambda: ["5m", "15m", "1h", "4h", "1d", "1w"],
            span_ms=server._tf_ms)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_seed_once(self):
        self.assertEqual(self.store.seed_defaults(), 6)
        self.assertEqual(self.store.seed_defaults(), 0)
        items = self.store.list()
        self.assertEqual([i["timeframe"] for i in items], ["5m", "15m", "1h", "4h", "1d", "1w"])
        self.assertTrue(all(i["enabled"] and i["symbols"] is None and i["overlap_candles"] == 3
                            for i in items))

    def test_crud_and_validation(self):
        item, warns = self.store.create({"name": "x", "timeframe": "1h", "cron": "5 */5 * * * *",
                                         "symbols": ["ethusdt", "BTCUSDT"]})
        self.assertEqual(item["symbols"], ["BTCUSDT", "ETHUSDT"])
        self.assertTrue(warns)
        for bad in ({"name": "x", "timeframe": "1h", "cron": "5 0 * * * *"},
                    {"name": "y", "timeframe": "3m", "cron": "5 0 * * * *"},
                    {"name": "z", "timeframe": "1h", "cron": "bad"}):
            with self.assertRaises(ValueError):
                self.store.create(bad)
        updated, warns = self.store.update(item["id"], {"cron": "5 0 * * * *", "symbols": []})
        self.assertEqual((updated["cron"], updated["symbols"], warns), ("5 0 * * * *", None, []))
        self.assertFalse(self.store.set_enabled("x", False)["enabled"])
        self.store.record_run(item["id"], at=1, status="ok", error=None, duration_ms=5)
        self.assertEqual(self.store.get("x")["last_status"], "ok")
        self.store.delete("x")
        self.assertEqual(self.store.list(), [])

    def test_scheduler_runs_independently(self):
        self.store.create({"name": "fast", "timeframe": "5m", "cron": "* * * * * *"})
        self.store.create({"name": "boom", "timeframe": "1h", "cron": "* * * * * *"})
        calls: list[str] = []

        async def run(item):
            calls.append(item["name"])
            if item["name"] == "boom":
                raise RuntimeError("binance down")
            return {"status": "ok", "error": None}

        async def main():
            scheduler = fetch_schedules.FetchScheduler(self.store, run, reload_seconds=1)
            task = asyncio.ensure_future(scheduler.run_forever())
            await asyncio.sleep(2.5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(main())
        self.assertGreaterEqual(calls.count("fast"), 1)
        self.assertGreaterEqual(calls.count("boom"), 1)
        self.assertEqual(self.store.get("fast")["last_status"], "ok")
        self.assertEqual(self.store.get("boom")["last_status"], "error")
        self.assertIn("binance down", self.store.get("boom")["last_error"])


class CatchUpTest(unittest.TestCase):
    def test_missed_runs_detected(self):
        d = Path(tempfile.mkdtemp(prefix="bamcp-fs-cu-"))
        try:
            store = fetch_schedules.ScheduleStore(d / "db", supported=lambda: ["1h", "1w"],
                                                  span_ms=server._tf_ms)
            fresh, _ = store.create({"name": "w", "timeframe": "1w", "cron": "5 0 0 * * MON"})
            hourly, _ = store.create({"name": "h", "timeframe": "1h", "cron": "5 0 * * * *"})
            now = ms("2026-11-02 00:30:00")
            store.record_run(hourly["id"], at=ms("2026-11-02 00:00:05"), status="ok", error=None,
                             duration_ms=1)
            sched = fetch_schedules.FetchScheduler(store, None)
            self.assertEqual([i["name"] for i in sched.missed(store.list(), now)], ["w"])
            store.record_run(fresh["id"], at=ms("2026-10-26 00:00:05"), status="ok", error=None,
                             duration_ms=1)
            self.assertEqual([i["name"] for i in sched.missed(store.list(), now)], ["w"])
            store.record_run(fresh["id"], at=ms("2026-11-02 00:00:05"), status="ok", error=None,
                             duration_ms=1)
            self.assertEqual(sched.missed(store.list(), now), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class FakeResponse:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return self.rows


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.params: list[dict] = []

    async def get(self, url, params=None, timeout=None):
        self.params.append(dict(params))
        return FakeResponse(self.pages.pop(0) if self.pages else [])


def bar(open_ms: int, span: int = M5) -> list:
    return [open_ms, "1", "2", "0.5", "1.5", "10", open_ms + span - 1, "15", 3, "4", "0", "0"]


class ServerRunTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bamcp-fs-srv-"))
        self.previous = server.KLINE_STORE
        server.KLINE_STORE = kline_store.SqliteKlineStore(self.dir / "bamcp.db", span_ms=server._tf_ms)
        self.delay = server.SCHEDULE_LATE_DELAY
        server.SCHEDULE_LATE_DELAY = 0

    def tearDown(self):
        server.KLINE_STORE = self.previous
        server.SCHEDULE_LATE_DELAY = self.delay
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_pull_from_latest_minus_overlap(self):
        now = int(time.time() * 1000)
        last_closed = now // M5 * M5 - M5
        server.KLINE_STORE.upsert("BTCUSDT", "5m", [bar(last_closed - i * M5) for i in range(5, 10)])
        latest = server.KLINE_STORE.get_latest_open_time("BTCUSDT", "5m")
        client = FakeClient([[bar(latest - 3 * M5 + i * M5) for i in range(9)]])
        out = asyncio.run(server._pull_since_latest(client, "BTCUSDT", "5m", 3))
        self.assertTrue(out["ok"], out)
        self.assertEqual(client.params[0]["startTime"], latest - 3 * M5)
        self.assertEqual(server.KLINE_STORE.get_latest_open_time("BTCUSDT", "5m"), last_closed)

    def test_retry_when_closed_candle_missing(self):
        now = int(time.time() * 1000)
        last_closed = now // M5 * M5 - M5
        stale = [bar(last_closed - M5)]
        client = FakeClient([stale, stale, stale, stale])
        out = asyncio.run(server._pull_since_latest(client, "BTCUSDT", "5m", 3))
        self.assertFalse(out["ok"])
        self.assertEqual(len(client.params), 4)

    def test_lock_busy_is_skipped(self):
        async def main():
            lock = server._fetch_lock(server.DEFAULT_SYMBOL, "1w")
            async with lock:
                return await server._run_schedule({"name": "t", "timeframe": "1w",
                                                   "symbols": None, "overlap_candles": 3})

        out = asyncio.run(main())
        self.assertEqual(out["status"], "skipped")

    def test_list_timeframes_has_schedules_and_5m(self):
        server.SCHEDULES.seed_defaults()
        out = server.list_timeframes()
        frames = {f["timeframe"]: f for f in out["symbols"][0]["timeframes"]}
        self.assertIn("5m", frames)
        self.assertEqual(frames["5m"]["schedules"][0]["name"], "pull-5m")
        self.assertEqual(set(frames["1h"]["schedules"][0]),
                         {"name", "enabled", "cron", "last_run_at", "last_status"})


if __name__ == "__main__":
    unittest.main()
