# TM - #ENTRY-ALERT - Entry setups & alerts

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="bamcp-ea-")
os.environ.setdefault("BAMCP_DATA_ROOT", _TMP)
os.environ.setdefault("BAMCP_ACCOUNT_ENABLED", "false")

import entry_logic  # noqa: E402
import entry_store  # noqa: E402
import entry_watcher  # noqa: E402
import kline_store  # noqa: E402
import notifier  # noqa: E402
import server  # noqa: E402

M5 = 300_000
T0 = 1_790_000_100_000 // M5 * M5
VN = ZoneInfo("Asia/Ho_Chi_Minh")
PARAMS = dict(server.ALERT_PARAM_DEFAULTS)
PASS = {"would_pass": True, "rule_violations": [], "rr": 2.0, "quota": "0/3"}


def tearDownModule():
    if Path(server.DATA_ROOT).resolve() != Path(_TMP).resolve():
        shutil.rmtree(_TMP, ignore_errors=True)


def bar(i: int, o: float, h: float, low: float, c: float, v: float = 10.0) -> dict:
    t = T0 + i * M5
    return {"open_time": t, "open": o, "high": h, "low": low, "close": c, "volume": v,
            "close_time": t + M5 - 1}


def history(n: int = 25) -> list[dict]:
    return [bar(i - n, 67650, 67700, 67600, 67650) for i in range(n)]


def setup(**kw) -> dict:
    base = {"id": "s1", "code": "BTC-20261101-A", "symbol": "BTCUSDT", "status": "WAITING",
            "state": {}, "direction": "long", "zone_low": 67200.0, "zone_high": 67450.0,
            "trigger_type": "spring", "trigger_params": {}, "confirm_type": None,
            "confirm_level": None, "entry": 67500.0, "stop": 67050.0, "target": 68400.0,
            "max_chase": None, "invalidate_level": 67000.0, "m15_filter": False,
            "expires_at": T0 + 1000 * M5, "tr_since": None, "created_at": T0 - 100 * M5}
    base.update(kw)
    return base


def run(s: dict, bars: list[dict], check=lambda price: PASS, m15=None, hist=None):
    hist = list(hist if hist is not None else history())
    events = []
    for b in bars:
        ctx = {"params": PARAMS, "history": hist, "m15_close": m15, "min_rr": 1.5,
               "day": "2026-11-01", "check": check}
        status, state, new = entry_logic.step(s, b, ctx)
        s = {**s, "status": status, "state": state}
        events.extend(new)
        hist.append(b)
    return s, events


class LogicTest(unittest.TestCase):
    def test_x_max_example(self):
        self.assertAlmostEqual(entry_logic.x_max("long", 67500, 67050, 68400, 1.5), 90.0)
        self.assertAlmostEqual(entry_logic.x_max("short", 67500, 67950, 66600, 1.5), 90.0)
        self.assertEqual(entry_logic.effective_max_chase(50, 90), 50)
        self.assertEqual(entry_logic.effective_max_chase(None, 90), 90)

    def test_codes(self):
        self.assertEqual([entry_logic.code_letters(i) for i in (0, 25, 26, 27)], ["A", "Z", "AA", "AB"])
        self.assertEqual(entry_logic.code_base("ETHUSDT"), "ETH")

    def validate(self, **kw):
        raw = {"direction": "long", "zone_low": 67200, "zone_high": 67450, "trigger_type": "spring",
               "entry": 67500, "stop": 67050, "target": 68400, "invalidate_level": 67000, **kw}
        return entry_logic.validate(raw, min_rr=1.5, now_ms=T0, parse_time=server._parse_moment,
                                    default_expiry=lambda d: T0 + 10 * M5, default_day="2026-11-01",
                                    default_m15=True)

    def test_validate(self):
        clean = self.validate()
        self.assertAlmostEqual(clean["max_chase_effective"], 90.0)
        self.assertTrue(clean["m15_filter"])
        self.assertAlmostEqual(self.validate(max_chase=40)["max_chase_effective"], 40)
        bad = [dict(target=68100), dict(zone_low=67500), dict(stop=67600),
               dict(trigger_type="utad"), dict(confirm_type="close_above"),
               dict(direction="flat"), dict(entry=None), dict(trigger_params={"x": 1}),
               dict(expires_at=T0 - 1)]
        for kw in bad:
            with self.assertRaises(ValueError, msg=kw):
                self.validate(**kw)
        with self.assertRaisesRegex(ValueError, "min_rr"):
            self.validate(target=68100)

    def test_triggers(self):
        s = setup(status="IN_ZONE")
        h = history()
        spring = bar(1, 67450, 67480, 67150, 67480, 25)
        self.assertTrue(entry_logic.check_trigger(s, spring, h, PARAMS)[0])
        self.assertFalse(entry_logic.check_trigger(s, {**spring, "volume": 15}, h, PARAMS)[0])
        with self.assertRaises(entry_logic.InsufficientData):
            entry_logic.check_trigger(s, spring, h[:5], PARAMS)
        sos = setup(trigger_type="sos")
        self.assertTrue(entry_logic.check_trigger(sos, bar(1, 67300, 67600, 67290, 67580, 30), h, PARAMS)[0])
        self.assertFalse(entry_logic.check_trigger(sos, bar(1, 67500, 67600, 67290, 67520, 30), h, PARAMS)[0])
        rej = setup(trigger_type="rejection")
        self.assertTrue(entry_logic.check_trigger(rej, bar(1, 67470, 67500, 67300, 67490), h, PARAMS)[0])
        ret = setup(trigger_type="retest")
        low_vol = bar(1, 67480, 67500, 67440, 67460, 5)
        self.assertTrue(entry_logic.check_trigger(ret, low_vol, h, PARAMS)[0])
        flat = [bar(i - 25, 67300, 67350, 67250, 67300) for i in range(25)]
        self.assertFalse(entry_logic.check_trigger(ret, low_vol, flat, PARAMS)[0])
        above = setup(trigger_type="close_above", trigger_params={"level": 67455})
        params = entry_logic.resolve_params(PARAMS, {}, above["trigger_params"])
        self.assertTrue(entry_logic.check_trigger(above, low_vol, h, params)[0])

    def test_spring_no_confirm_chains_to_entry_valid_then_window_expires(self):
        s, events = run(setup(), [bar(0, 67600, 67620, 67400, 67450),
                                  bar(1, 67450, 67480, 67150, 67480, 25)])
        self.assertEqual([e["event"] for e in events], ["IN_ZONE", "TRIGGERED", "CONFIRMED", "ENTRY_VALID"])
        self.assertEqual([e["notify"] for e in events], [True, False, False, True])
        self.assertEqual(s["state"]["max_chase"], 90.0)
        s, events = run(s, [bar(i, 67500, 67530, 67490, 67520) for i in (2, 3, 4)])
        self.assertEqual([e["event"] for e in events], ["ENTRY_WINDOW_EXPIRED"])
        self.assertEqual(s["status"], "WAITING")

    def test_confirm_missed_then_back(self):
        s, events = run(setup(confirm_type="close_above", confirm_level=67600.0),
                        [bar(0, 67600, 67620, 67400, 67450), bar(1, 67450, 67480, 67150, 67480, 25),
                         bar(2, 67480, 67660, 67470, 67650), bar(3, 67650, 67720, 67640, 67700),
                         bar(4, 67700, 67710, 67550, 67560)])
        self.assertEqual([e["event"] for e in events],
                         ["IN_ZONE", "TRIGGERED", "CONFIRMED", "MISSED", "ENTRY_VALID"])
        self.assertTrue(events[1]["notify"])

    def test_trigger_failed(self):
        s, events = run(setup(confirm_type="close_above", confirm_level=67600.0),
                        [bar(0, 67600, 67620, 67400, 67450), bar(1, 67450, 67480, 67150, 67480, 25)]
                        + [bar(i, 67480, 67500, 67460, 67490) for i in (2, 3, 4)])
        self.assertEqual(events[-1]["event"], "TRIGGER_FAILED")
        self.assertEqual(s["status"], "WAITING")

    def test_blocked_once_per_reason_per_day(self):
        fail = {"would_pass": False, "rule_violations": ["vuot max_trades_per_day (3)"]}
        moves = [bar(0, 67600, 67620, 67400, 67450), bar(1, 67450, 67480, 67150, 67480, 25),
                 bar(2, 67480, 67500, 67470, 67490), bar(3, 67490, 67500, 67400, 67450),
                 bar(4, 67450, 67480, 67150, 67480, 25)]
        s, events = run(setup(), moves, check=lambda price: fail)
        names = [e["event"] for e in events]
        self.assertEqual(names, ["IN_ZONE", "TRIGGERED", "CONFIRMED", "BLOCKED", "UNBLOCKED",
                                 "IN_ZONE", "TRIGGERED", "CONFIRMED", "BLOCKED"])
        blocked = [e["notify"] for e in events if e["event"] == "BLOCKED"]
        self.assertEqual(blocked, [True, False])

    def test_invalidation_and_m15_filter(self):
        crash = bar(0, 67100, 67120, 66900, 66950)
        s, events = run(setup(m15_filter=True), [crash], m15=67100)
        self.assertEqual(events, [])
        s, events = run(setup(m15_filter=True), [crash], m15=66980)
        self.assertEqual([e["event"] for e in events], ["INVALIDATED"])
        s2, ev2 = run(setup(status="FILLED"), [crash])
        self.assertEqual(ev2, [])

    def test_expired_and_zone_exit(self):
        s, events = run(setup(expires_at=T0), [bar(0, 67600, 67620, 67400, 67450)])
        self.assertEqual([e["event"] for e in events], ["EXPIRED"])
        s, events = run(setup(), [bar(0, 67600, 67620, 67400, 67450), bar(1, 67450, 67520, 67440, 67510)])
        self.assertEqual([e["event"] for e in events], ["IN_ZONE", "ZONE_EXIT"])


class FakeNotifier(notifier.INotifier):
    name = "fake"

    def __init__(self, failures, error=RuntimeError("down")):
        self.failures = failures
        self.error = error
        self.sent = 0

    async def send(self, alert):
        if self.failures:
            self.failures -= 1
            raise self.error
        self.sent += 1


class NotifierTest(unittest.TestCase):
    def test_backoff_and_retry_after(self):
        waits = []

        async def sleep(s):
            waits.append(s)

        n = FakeNotifier(2)
        asyncio.run(notifier.send_with_retry(n, notifier.Alert("x"), sleep=sleep))
        self.assertEqual((n.sent, waits), (1, [2.0, 10.0]))
        waits.clear()
        n = FakeNotifier(1, notifier.RetryAfter(1.5, "429"))
        asyncio.run(notifier.send_with_retry(n, notifier.Alert("x"), sleep=sleep))
        self.assertEqual(waits, [1.5])
        waits.clear()
        with self.assertRaises(RuntimeError):
            asyncio.run(notifier.send_with_retry(FakeNotifier(9), notifier.Alert("x"), sleep=sleep))
        self.assertEqual(waits, [2.0, 10.0, 30.0])

    def test_both_with_one_channel_missing(self):
        sent = []

        async def send(self, alert):
            sent.append((self.chat_id, alert.text))

        cfg = {"channel": "both", "telegram": {"bot_token": "1:x", "chat_id": "42"}}
        with mock.patch.object(notifier.TelegramBotNotifier, "send", send):
            err = asyncio.run(notifier.deliver(cfg, notifier.Alert("x")))
        self.assertEqual(sent, [("42", "x")])
        self.assertEqual(err, "discord: chua dat webhook_url")

    def test_config_error_and_message(self):
        err = asyncio.run(notifier.deliver({"channel": "telegram"}, notifier.Alert("x")))
        self.assertIn("telegram", err)
        s, events = run(setup(), [bar(0, 67600, 67620, 67400, 67450),
                                  bar(1, 67450, 67480, 67150, 67520, 25)])
        alert = notifier.setup_alert(s, events[-1], VN, PARAMS)
        self.assertIn("BTC-20261101-A · LONG · ENTRY_VALID", alert.text)
        self.assertIn("Trigger: spring 67150 → đóng 67520, vol 2.5× SMA20", alert.text)
        self.assertIn("entry 67500 (+20, max_chase 90) · còn 3 nến", alert.text)
        self.assertIn("SL 67050 · TP 68400 · R:R 2.0", alert.text)
        missed = notifier.setup_alert(s, {**events[-1], "event": "MISSED",
                                          "details": {"max_chase": 90}, "price": 67700}, VN)
        self.assertTrue(missed.text.startswith("⛔ MISSED — không đuổi"))


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bamcp-ea-w-"))
        self.now = T0 + 6 * M5 + 20_000
        self.klines = kline_store.SqliteKlineStore(self.dir / "db.sqlite", span_ms=server._tf_ms,
                                                   now_ms=lambda: self.now)
        self.store = entry_store.SetupStore(self.dir / "db.sqlite", now_ms=lambda: T0 - 30 * M5)
        self.sent = []
        self.positions = []
        self.cfg = {"notify": {"channel": "discord"},
                    "watcher": {"enabled": True, "trigger_tf": "5m", "watch_symbols": [],
                                "plan_deadline_utc": "00:30", "defaults": PARAMS,
                                "symbol_overrides": {}}}

        async def deliver(cfg, alert):
            self.sent.append(alert.text)
            return None

        async def positions(symbol):
            return self.positions

        async def sleep(s):
            return None

        self.watcher = entry_watcher.EntryWatcher(
            self.store, klines=lambda: self.klines, cfg=lambda: self.cfg, min_rr=lambda s: 1.5,
            check=lambda *a: PASS, positions=positions, tz=VN, span_ms=server._tf_ms,
            now_ms=lambda: self.now, sleep=sleep, deliver=deliver)
        self.clean = entry_logic.validate(
            {"direction": "long", "zone_low": 67200, "zone_high": 67450, "trigger_type": "spring",
             "entry": 67500, "stop": 67050, "target": 68400, "invalidate_level": 67000,
             "m15_filter": False, "expires_at": T0 + 500 * M5},
            min_rr=1.5, now_ms=T0 - 40 * M5, parse_time=server._parse_moment,
            default_expiry=lambda d: 0, default_day="2026-11-01", default_m15=False)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def scan(self):
        async def main():
            out = await self.watcher.scan(self.now)
            await asyncio.gather(*list(self.watcher._tasks))
            return out
        return asyncio.run(main())

    def to_rows(self, bars):
        return [[b["open_time"], b["open"], b["high"], b["low"], b["close"], b["volume"],
                 b["close_time"]] for b in bars]

    def test_catch_up_notifies_final_state_only_and_is_idempotent(self):
        row = self.store.insert("BTCUSDT", self.clean, "claude")
        self.klines.upsert("BTCUSDT", "5m", self.to_rows(
            history(30) + [bar(0, 67600, 67620, 67400, 67450), bar(1, 67450, 67480, 67150, 67480, 25)]
            + [bar(i, 67500, 67530, 67490, 67520) for i in (2, 3, 4, 5)]))
        self.scan()
        events = [e["event"] for e in self.store.events(setup_id=row["id"])]
        self.assertEqual(events, ["CREATED", "IN_ZONE", "TRIGGERED", "CONFIRMED", "ENTRY_VALID",
                                  "ENTRY_WINDOW_EXPIRED"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("hết cửa sổ entry", self.sent[0])
        self.scan()
        self.assertEqual(len(self.store.events(setup_id=row["id"])), 6)
        self.assertEqual(self.store.get(row["id"])["status"], "WAITING")

    def test_fill_from_position_and_unplanned(self):
        row = self.store.insert("BTCUSDT", self.clean, "claude")
        state = {"triggered_at": T0, "last_candle": T0 + 5 * M5}
        self.store.apply(self.store.get(row["id"]), "ENTRY_VALID", state, [])
        self.klines.upsert("BTCUSDT", "5m", self.to_rows(history(30) + [bar(5, 67500, 67530, 67490, 67520)]))
        self.positions = [{"symbol": "BTC-USDT-SWAP", "side": "short", "size": 1, "entry_price": 67510,
                           "opened_ms": T0 + 3 * M5},
                          {"symbol": "BTC-USDT-SWAP", "side": "long", "size": 1, "entry_price": 67515,
                           "opened_ms": T0 + 4 * M5}]
        self.scan()
        self.assertEqual(self.store.get(row["id"])["status"], "FILLED")
        names = {e["event"] for e in self.store.events()}
        self.assertTrue({"FILLED", "UNPLANNED_POSITION"} <= names)
        self.assertTrue(any("ngoài kế hoạch" in t for t in self.sent))

    def test_system_alerts_rate_limited(self):
        self.cfg["watcher"]["watch_symbols"] = ["BTCUSDT"]
        self.scan()
        names = [e["event"] for e in self.store.events()]
        self.assertIn("FETCHER_STALE", names)
        self.assertIn("PLAN_MISSING", names)
        self.scan()
        self.assertEqual([e["event"] for e in self.store.events()].count("FETCHER_STALE"), 1)
        self.assertTrue(self.store.events()[0]["notified"])


class ServerToolsTest(unittest.TestCase):
    def test_save_get_cancel_log(self):
        raw = {"direction": "long", "zone_low": 67200, "zone_high": 67450, "trigger_type": "spring",
               "entry": 67500, "stop": 67050, "target": 68400, "invalidate_level": 67000}
        out = server.save_setups(server.DEFAULT_SYMBOL, [raw, {**raw, "target": 68100}, raw])
        self.assertEqual(len(out["saved"]), 2)
        self.assertIn("min_rr", out["rejected"][0]["error"])
        base = out["codes"][0][:-1]
        self.assertEqual(out["codes"], [base + "A", base + "B"])
        self.assertEqual(out["saved"][0]["max_chase_effective"], 90.0)
        again = server.save_setups(server.DEFAULT_SYMBOL, [raw], replace=True)
        self.assertEqual(sorted(again["replaced"]), sorted(out["codes"]))
        self.assertEqual(again["codes"], [base + "C"])
        listed = server.get_setups()
        statuses = {s["code"]: s["status"] for s in listed["setups"]}
        self.assertEqual(statuses[base + "A"], "CANCELLED")
        self.assertEqual(statuses[base + "C"], "WAITING")
        logged = server.log_trade(side="long", entry=67500, stop=67050, target=68400,
                                  margin_usd=10, date="2026-09-02", setup_id=base + "C")
        self.assertEqual(logged["setup"]["status"], "FILLED")
        self.assertEqual(logged["trade"]["setup_code"], base + "C")
        with self.assertRaises(ValueError):
            server.cancel_setup(base + "C", "test")
        events = server.get_setup_events(setup_id=base + "C")["events"]
        self.assertEqual([e["event"] for e in events], ["CREATED", "FILLED"])
        server.save_setups(server.DEFAULT_SYMBOL, [], replace=True)
        (server.JOURNAL_DIR / "2026-09-02.json").unlink(missing_ok=True)

    def test_notify_test_per_channel(self):
        code, out = asyncio.run(server._alerts_action({"action": "notify_test", "target": "telegram"}))
        self.assertEqual((code, out["ok"], out["channel"]), (200, False, "telegram"))
        self.assertIn("telegram: chua dat bot_token", out["error"])
        code, out = asyncio.run(server._alerts_action({"action": "notify_test", "target": "sms"}))
        self.assertEqual(code, 400)
        alert = notifier.system_alert("TEST", {"at": T0, "channel": "discord"}, VN)
        self.assertTrue(alert.text.startswith("✅ BAMCP test — kênh discord hoạt động"))


if __name__ == "__main__":
    unittest.main()
