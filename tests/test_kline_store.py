# TM - #KL-SQL - Kline storage SQLite

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="bamcp-kl-")
os.environ.setdefault("BAMCP_DATA_ROOT", _TMP)
os.environ.setdefault("BAMCP_ACCOUNT_ENABLED", "false")

import admin_schedules  # noqa: E402
import kline_store  # noqa: E402
import server  # noqa: E402
import settings  # noqa: E402

H1 = 3_600_000
T0 = 1_760_000_400_000 // H1 * H1
NOW = T0 + 10 * H1 + 1_000


def tearDownModule():
    if Path(server.DATA_ROOT).resolve() != Path(_TMP).resolve():
        shutil.rmtree(_TMP, ignore_errors=True)


def raw(i: int, close: float | None = None) -> list:
    o = T0 + i * H1
    c = 100.0 + i if close is None else close
    return [o, f"{100 + i}", f"{101 + i}", f"{99 + i}", f"{c}", f"{10 + i}", o + H1 - 1,
            f"{1000 + i}", 50 + i, f"{5 + i}", "0", "0"]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bamcp-kl-case-"))
        self.sqlite = kline_store.SqliteKlineStore(self.dir / "bamcp.db", span_ms=server._tf_ms,
                                                   now_ms=lambda: NOW)
        root = self.dir / "klines"
        self.json = kline_store.JsonKlineStore(
            root, path_fn=lambda s, tf: root / s / f"{tf}.json",
            normalize=server._normalize_bars, merge=server._merge_bars,
            retention=lambda tf: None, read_json=server._read_json,
            write_json=server._write_json, timeframes=lambda: ["1h", "4h"])

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class SqliteStoreTest(Base):
    def test_migration_and_pragmas(self):
        conn = sqlite3.connect(self.dir / "bamcp.db")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"klines", "schema_version"} <= tables)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        conn.close()
        kline_store.SqliteKlineStore(self.dir / "bamcp.db", span_ms=server._tf_ms)

    def test_upsert_skips_forming_and_is_idempotent(self):
        rows = [raw(i) for i in range(11)]
        out = self.sqlite.upsert("btcusdt", "1h", rows)
        self.assertEqual(out["written"], 10)
        self.assertEqual(out["skipped_forming"], 1)
        conn = sqlite3.connect(self.dir / "bamcp.db")
        ids = dict(conn.execute("SELECT open_time, id FROM klines"))
        again = self.sqlite.upsert("BTCUSDT", "1h", [raw(3, close=555.0)])
        self.assertEqual(again["total"], 10)
        self.assertEqual(dict(conn.execute("SELECT open_time, id FROM klines")), ids)
        row = conn.execute("SELECT close, trades, quote_volume FROM klines WHERE open_time = ?",
                           (T0 + 3 * H1,)).fetchone()
        conn.close()
        self.assertEqual(row, (555.0, 53, 1003.0))

    def test_reads(self):
        self.sqlite.upsert("BTCUSDT", "1h", [raw(i) for i in range(10)])
        bars = self.sqlite.get_klines("BTCUSDT", "1h")
        self.assertEqual(len(bars), 10)
        self.assertEqual(list(bars[0]), list(kline_store.BAR_KEYS))
        self.assertEqual([b["open_time"] for b in self.sqlite.get_klines("BTCUSDT", "1h", 3)],
                         [T0 + i * H1 for i in (7, 8, 9)])
        self.assertEqual(self.sqlite.get_klines("BTCUSDT", "1h", 2, end_time=T0 + 4 * H1)[-1]["open_time"],
                         T0 + 4 * H1)
        self.assertEqual(self.sqlite.get_latest_open_time("BTCUSDT", "1h"), T0 + 9 * H1)
        self.assertIsNone(self.sqlite.get_latest_open_time("BTCUSDT", "4h"))
        cov = self.sqlite.get_coverage()
        self.assertEqual(len(cov), 1)
        self.assertEqual((cov[0]["count"], cov[0]["first_open_time"], cov[0]["last_open_time"]),
                         (10, T0, T0 + 9 * H1))
        self.assertEqual(self.sqlite.get_coverage("ETHUSDT"), [])


class ImportVerifyTest(Base):
    def test_import_twice_then_verify(self):
        self.json.upsert("BTCUSDT", "1h", [raw(i) for i in range(11)])
        first = kline_store.import_json(self.json, self.sqlite)
        second = kline_store.import_json(self.json, self.sqlite)
        self.assertEqual(first["rows_written"], 10)
        self.assertEqual(second["details"][0]["sqlite_total"], 10)
        report = kline_store.verify(self.json, self.sqlite, span_ms=server._tf_ms, now_ms=NOW)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["pairs"][0]["count"], [10, 10])

    def test_verify_reports_differences(self):
        self.json.upsert("BTCUSDT", "1h", [raw(i) for i in range(10)])
        kline_store.import_json(self.json, self.sqlite)
        self.sqlite.upsert("BTCUSDT", "1h", [raw(2, close=1.0)])
        self.json.upsert("BTCUSDT", "4h", [raw(0)])
        report = kline_store.verify(self.json, self.sqlite, span_ms=server._tf_ms, now_ms=NOW)
        self.assertFalse(report["ok"])
        by_tf = {p["tf"]: p for p in report["pairs"]}
        self.assertEqual(by_tf["1h"]["ohlcv_mismatch"], 1)
        self.assertEqual(by_tf["4h"]["missing_in_dst"], 1)


class ServerStoreTest(Base):
    def test_get_klines_reads_selected_store(self):
        self.sqlite.now_ms = kline_store._now_ms
        self.sqlite.upsert(server.DEFAULT_SYMBOL, "1h", [raw(i) for i in range(5)])
        previous = server.KLINE_STORE
        server.KLINE_STORE = self.sqlite
        try:
            out = server.get_klines("1h", limit=3)
            listed = server.list_timeframes()
        finally:
            server.KLINE_STORE = previous
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["bars"][-1]["close"], 104.0)
        frame = next(f for f in listed["symbols"][0]["timeframes"] if f["timeframe"] == "1h")
        self.assertTrue(frame["exists"])


class ExportTest(Base):
    def test_export_keeps_retention_and_reads_back(self):
        self.sqlite.upsert("BTCUSDT", "1h", [raw(i) for i in range(10)])
        self.json.retention = lambda tf: 4
        out = kline_store.export_json(self.sqlite, self.json)
        self.assertEqual(out["rows_written"], 4)
        bars = self.json.get_klines("BTCUSDT", "1h")
        self.assertEqual([b["open_time"] for b in bars], [T0 + i * H1 for i in range(6, 10)])
        self.assertEqual(bars[-1]["close"], 109.0)
        self.assertEqual(self.sqlite.raw_rows("BTCUSDT", "1h", limit=1)[0][7], 1009.0)


class SwitchTest(Base):
    def setUp(self):
        super().setUp()
        self.saved = (server.KLINE_JSON, server._KLINE_SQLITE, server.KLINE_STORE, server.STORE)
        self.settings = settings.SettingsStore(self.dir / "settings.json", server.TZ)
        server.KLINE_JSON, server._KLINE_SQLITE = self.json, self.sqlite
        server.KLINE_STORE, server.STORE = self.json, self.settings

    def tearDown(self):
        server.KLINE_JSON, server._KLINE_SQLITE, server.KLINE_STORE, server.STORE = self.saved
        super().tearDown()

    def test_switch_both_ways(self):
        self.json.upsert("BTCUSDT", "1h", [raw(i) for i in range(6)])
        out = asyncio.run(server._kline_switch("sqlite"))
        self.assertTrue(out["ok"] and out["changed"], out)
        self.assertIs(server.KLINE_STORE, self.sqlite)
        self.assertEqual(settings.SettingsStore(self.dir / "settings.json", server.TZ).kline_store(),
                         "sqlite")
        self.sqlite.upsert("BTCUSDT", "1h", [raw(i) for i in range(6, 9)])
        back = asyncio.run(server._kline_switch("json"))
        self.assertTrue(back["ok"] and back["changed"], back)
        self.assertIs(server.KLINE_STORE, self.json)
        self.assertEqual(self.json.get_latest_open_time("BTCUSDT", "1h"), T0 + 8 * H1)
        self.assertEqual(self.settings.kline_store(), "json")
        again = asyncio.run(server._kline_switch("json"))
        self.assertFalse(again["changed"])

    def test_refuses_when_mismatched(self):
        self.json.upsert("BTCUSDT", "1h", [raw(i) for i in range(3)])
        bad = {"ok": False, "pairs": [{"symbol": "BTCUSDT", "tf": "1h", "count": [3, 2],
                                       "last_open_time": [1, 1], "missing_in_dst": 1,
                                       "ohlcv_mismatch": 0}]}
        with mock.patch.object(server.kline_store, "verify", return_value=bad):
            out = asyncio.run(server._kline_switch("sqlite"))
        self.assertFalse(out["ok"] or out["changed"])
        self.assertEqual(out["mismatched"][0]["tf"], "1h")
        self.assertIs(server.KLINE_STORE, self.json)
        self.assertIsNone(self.settings.kline_store())
        with self.assertRaises(ValueError):
            asyncio.run(server._kline_switch("csv"))

    def test_admin_page_shows_store(self):
        self.json.upsert("BTCUSDT", "1h", [raw(i) for i in range(3)])
        page = admin_schedules.render(server._schedules_view(), action_path="/a", admin_path="/admin",
                                      setup_required=False)
        self.assertIn("Chuyển sang SQLite", page)
        self.assertIn("config.yaml, storage.kline_store", page)
        asyncio.run(server._kline_switch("sqlite"))
        page = admin_schedules.render(server._schedules_view(), action_path="/a", admin_path="/admin",
                                      setup_required=False)
        self.assertIn("Chuyển về JSON", page)
        self.assertIn("settings.json", page)


if __name__ == "__main__":
    unittest.main()
