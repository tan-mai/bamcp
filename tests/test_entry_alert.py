# TM - #ENTRY-ALERT - Entry setups & alerts

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="bamcp-ea-")
os.environ.setdefault("BAMCP_DATA_ROOT", _TMP)
os.environ.setdefault("BAMCP_ACCOUNT_ENABLED", "false")

import admin_entry  # noqa: E402
import entry_logic  # noqa: E402
import entry_store  # noqa: E402
import entry_watcher  # noqa: E402
import kline_store  # noqa: E402
import notifier  # noqa: E402
import server  # noqa: E402
import sqlite_db  # noqa: E402

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

    # TM - #ENTRY-VOL - rejection phai khai nguong volume, tham so thua bi tu choi
    def rejection(self, **params):
        return self.validate(trigger_type="rejection", trigger_params=params)

    def test_rejection_requires_volume_bound(self):
        with self.assertRaisesRegex(ValueError, "max_vol_mult"):
            self.rejection()
        self.assertEqual(self.rejection(max_vol_mult="0.8")["trigger_params"], {"max_vol_mult": 0.8})
        self.assertEqual(self.rejection(min_vol_mult=2)["trigger_params"], {"min_vol_mult": 2.0})
        both = self.rejection(min_vol_mult=1, max_vol_mult=3, wick_pct=0.5)["trigger_params"]
        self.assertEqual(both, {"min_vol_mult": 1.0, "max_vol_mult": 3.0, "wick_pct": 0.5})
        for bad in (dict(min_vol_mult=3, max_vol_mult=1), dict(max_vol_mult=0), dict(max_vol_mult=-1),
                    dict(max_vol_mult="abc"), dict(max_vol_mult=float("inf")),
                    dict(max_vol_mult=float("nan")), dict(max_vol_mult=None)):
            with self.assertRaises(ValueError, msg=bad):
                self.rejection(**bad)

    def test_unused_trigger_params_rejected(self):
        # XAU-20261105-C that: close_above khai vol_mult 1.3 nhung code khong doc
        for trigger, params in (("close_above", {"level": 67455, "vol_mult": 1.3}),
                                ("spring", {"level": 67455}), ("spring", {"max_vol_mult": 1}),
                                ("sos", {"wick_pct": 0.5}), ("retest", {"vol_mult": 2}),
                                ("rejection", {"max_vol_mult": 1, "vol_mult": 2}),
                                ("spring", {"m15_filter": False}), ("spring", {"confirm_within": 3})):
            with self.assertRaisesRegex(ValueError, "khong dung tham so", msg=(trigger, params)):
                self.validate(trigger_type=trigger, trigger_params=params)
        kept = {"close_above": {"level": 67455, "entry_valid_candles": 2},
                "spring": {"vol_mult": 1.5, "sma_period": 30, "ref_low": 67100},
                "sos": {"vol_mult": 1.5, "body_pct": 0.5, "level": 67455},
                "retest": {"retest_low_vol_mult": 0.7, "level": 67455}}
        for trigger, params in kept.items():
            self.assertEqual(self.validate(trigger_type=trigger, trigger_params=params)["trigger_params"], params)
        self.assertIsNone(self.validate(trigger_params={"vol_mult": None})["trigger_params"])
        confirmed = self.validate(confirm_type="close_above", confirm_level=67600,
                                  trigger_params={"confirm_within": 5})
        self.assertEqual(confirmed["trigger_params"], {"confirm_within": 5})

    def test_rejection_volume_bounds_at_trigger(self):
        h = history()  # SMA volume = 10
        s = setup(trigger_type="rejection")
        candle = bar(1, 67470, 67500, 67300, 67490, 25)
        check = lambda **p: entry_logic.check_trigger(s, candle, h, {**PARAMS, **p})[0]
        self.assertTrue(check(), "setup cu khong khai nguong: chi xet hinh nen")
        self.assertTrue(check(min_vol_mult=2.5))
        self.assertFalse(check(min_vol_mult=2.6))
        self.assertTrue(check(max_vol_mult=2.5))
        self.assertFalse(check(max_vol_mult=2.4))
        self.assertTrue(check(min_vol_mult=2, max_vol_mult=3))
        self.assertFalse(check(min_vol_mult=3, max_vol_mult=4))
        flat = [{**b, "volume": 0.0} for b in h]
        self.assertFalse(entry_logic.check_trigger(s, candle, flat, {**PARAMS, "min_vol_mult": 1})[0])
        short = setup(trigger_type="rejection", direction="short", entry=67100.0, stop=67500.0,
                      target=66000.0, invalidate_level=67600.0)
        up_wick = bar(1, 67150, 67350, 67130, 67140, 5)
        self.assertTrue(entry_logic.check_trigger(short, up_wick, h, {**PARAMS, "max_vol_mult": 0.8})[0])
        self.assertFalse(entry_logic.check_trigger(short, up_wick, h, {**PARAMS, "min_vol_mult": 2})[0])

    def test_volume_rule_text(self):
        rule = entry_logic.volume_rule
        self.assertEqual(rule("spring", PARAMS), "vol ≥ 2× SMA20")
        self.assertEqual(rule("sow", {**PARAMS, "vol_mult": 1.5}), "vol ≥ 1.5× SMA20")
        self.assertEqual(rule("retest", PARAMS), "vol ≤ 0.8× SMA20")
        self.assertEqual(rule("close_above", PARAMS), "không xét vol")
        self.assertEqual(rule("rejection", PARAMS), "không xét vol")
        self.assertEqual(rule("rejection", {**PARAMS, "max_vol_mult": 0.8}), "vol ≤ 0.8× SMA20")
        self.assertEqual(rule("rejection", {**PARAMS, "min_vol_mult": 1, "max_vol_mult": 3}),
                         "vol ≥ 1× và ≤ 3× SMA20")

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

    # TM - #ENTRY-HTF - Zone alert, trigger cadence, HTF
    def test_trigger_checked_on_first_touch_bar(self):
        # Nen that BTC-20261106-A 20:30 ngay 06/11: rau xuong vung, dong tren vung, vol lon
        real = setup(zone_low=75300.0, zone_high=75650.0, entry=75500.0, stop=75050.0,
                     target=76500.0, invalidate_level=75150.0, trigger_type="rejection")
        s, events = run(real, [bar(0, 76011.4, 76085.6, 75560.4, 75892.6, 5432.967)],
                        hist=[bar(i - 25, 76000, 76050, 75950, 76000) for i in range(25)])
        self.assertEqual([e["event"] for e in events],
                         ["IN_ZONE", "TRIGGERED", "CONFIRMED", "MISSED"])
        self.assertEqual(s["status"], "MISSED")

    def test_first_touch_bar_closing_outside_stays_in_zone(self):
        s, events = run(setup(), [bar(0, 67600, 67620, 67400, 67500)])
        self.assertEqual([(e["event"], e["notify"]) for e in events], [("IN_ZONE", True)])
        self.assertEqual(s["status"], "IN_ZONE")

    def test_trigger_checked_every_bar_while_in_zone(self):
        s, events = run(setup(), [bar(0, 67600, 67620, 67400, 67450),
                                  bar(1, 67450, 67460, 67400, 67430),
                                  bar(2, 67430, 67440, 67380, 67400),
                                  bar(3, 67400, 67480, 67150, 67480, 25)])
        self.assertEqual([e["event"] for e in events], ["IN_ZONE", "TRIGGERED", "CONFIRMED", "ENTRY_VALID"])
        self.assertEqual(events[1]["candle_open_time"], T0 + 3 * M5)

    def test_zone_alert_only_first_touch_but_trigger_stays_alive(self):
        touch, away = bar(0, 67600, 67620, 67400, 67500), bar(1, 67500, 67530, 67490, 67520)
        s, events = run(setup(), [touch, away, {**touch, "open_time": T0 + 2 * M5}, away])
        self.assertEqual([(e["event"], e["notify"]) for e in events],
                         [("IN_ZONE", True), ("ZONE_EXIT", False), ("IN_ZONE", False), ("ZONE_EXIT", False)])
        self.assertEqual([e["details"]["repeat"] for e in events if e["event"] == "IN_ZONE"], [False, True])
        s, events = run(s, [bar(4, 67500, 67520, 67150, 67480, 25)])
        self.assertEqual([(e["event"], e["notify"]) for e in events],
                         [("IN_ZONE", False), ("TRIGGERED", False), ("CONFIRMED", False), ("ENTRY_VALID", True)])

    def test_zone_alert_stays_silent_after_entry_window_expired(self):
        s, _ = run(setup(), [bar(0, 67600, 67620, 67400, 67450), bar(1, 67450, 67480, 67150, 67480, 25)]
                   + [bar(i, 67500, 67530, 67490, 67520) for i in (2, 3, 4)])
        self.assertEqual(s["status"], "WAITING")
        s, events = run(s, [bar(5, 67520, 67530, 67400, 67500)])
        self.assertEqual([(e["event"], e["notify"]) for e in events], [("IN_ZONE", False)])

    def test_invalidated_setup_ignores_return_to_zone(self):
        s, events = run(setup(), [bar(0, 67600, 67620, 67400, 67450), bar(1, 67100, 67120, 66900, 66950)])
        self.assertEqual(s["status"], "INVALIDATED")
        s, events = run(s, [bar(2, 67300, 67480, 67150, 67480, 25)])
        self.assertEqual(events, [])
        self.assertEqual(s["status"], "INVALIDATED")

    def test_swing_trend(self):
        self.assertEqual(entry_logic.swing_trend([10, 12], [5, 6], 11), "up")
        self.assertEqual(entry_logic.swing_trend([12, 10], [6, 5], 7), "down")
        self.assertEqual(entry_logic.swing_trend([12, 10], [5, 6], 7), "range")
        self.assertEqual(entry_logic.swing_trend([10, 12], [5, 6], 5.5), "range", "gay day cuoi")
        self.assertEqual(entry_logic.swing_trend([12, 10], [6, 5], 10.5), "range", "gay dinh cuoi")
        self.assertIsNone(entry_logic.swing_trend([10], [5, 6], 7))
        # So that BTCUSDT 07/11: D dinh va day thap dan du get_swing_state bao 'up'
        self.assertEqual(entry_logic.swing_trend([81953.7, 81188], [79178.6, 76270], 75810.1), "down")

    def test_htf_counter(self):
        htf = {"w": "up", "d": "down", "h4": "down", "bias": "neutral"}
        self.assertEqual(entry_logic.htf_counter("long", htf), ["D", "H4"])
        self.assertEqual(entry_logic.htf_counter("short", htf), ["W"])
        self.assertEqual(entry_logic.htf_counter("short", {**htf, "bias": "long"}), ["W", "bias"])
        self.assertEqual(entry_logic.htf_counter("long", {"w": "range", "d": None}), [])
        self.assertEqual(entry_logic.htf_counter("long", {}), [])
        self.assertEqual(entry_logic.htf_counter("long", None), [])


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

    # TM - #ENTRY-HTF - Nhan setup nguoc va huong khung lon trong tin
    def test_htf_label_in_alerts(self):
        htf = {"w": "up", "d": "down", "h4": "down", "bias": "neutral"}
        s, events = run(setup(htf=htf), [bar(0, 67600, 67620, 67400, 67450)])
        lines = notifier.setup_alert(s, events[0], VN, PARAMS).text.split("\n")
        self.assertIn("BTC-20261101-A · LONG · IN_ZONE", lines[0])
        self.assertEqual(lines[1], "⚠️ NGƯỢC D, H4")
        self.assertEqual(lines[2], "Khung lớn: W tăng · D giảm · H4 giảm · bias neutral")
        missed = notifier.setup_alert(s, {**events[0], "event": "MISSED", "details": {"max_chase": 90},
                                          "price": 67700}, VN).text.split("\n")
        self.assertIn("BTC-20261101-A · LONG", missed[1])
        self.assertEqual(missed[2], "⚠️ NGƯỢC D, H4")
        aligned = {**s, "direction": "short"}
        text = notifier.setup_alert({**aligned, "htf": {"w": "down", "d": "down", "h4": None, "bias": None}},
                                    events[0], VN, PARAMS).text
        self.assertNotIn("NGƯỢC", text)
        self.assertIn("Khung lớn: W giảm · D giảm · H4 ? · bias ?", text)
        old = notifier.setup_alert({**s, "htf": {}}, events[0], VN, PARAMS).text
        self.assertNotIn("Khung lớn", old)
        expired = notifier.setup_alert(s, {**events[0], "event": "EXPIRED", "details": {}}, VN).text
        self.assertNotIn("Khung lớn", expired)

    # TM - #ENTRY-VOL - Tin IN_ZONE noi ro dieu kien volume dang ap dung
    def test_in_zone_alert_states_volume_rule(self):
        s, events = run(setup(), [bar(0, 67600, 67620, 67400, 67450)])
        text = notifier.setup_alert(s, events[0], VN, PARAMS).text
        self.assertIn("Chờ trigger spring (vol ≥ 2× SMA20).", text)
        rej = {**s, "trigger_type": "rejection"}
        text = notifier.setup_alert(rej, events[0], VN, {**PARAMS, "max_vol_mult": 0.8}).text
        self.assertIn("Chờ trigger rejection (vol ≤ 0.8× SMA20).", text)
        self.assertIn("Chờ trigger rejection.", notifier.setup_alert(rej, events[0], VN).text)


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

    # TM - #ENTRY-HTF - Zone alert, trigger cadence, HTF
    def scan_at(self, index):
        self.now = T0 + (index + 1) * M5 + 20_000
        self.scan()

    def test_return_to_zone_sends_one_alert_but_still_triggers(self):
        row = self.store.insert("BTCUSDT", {**self.clean, "htf": {"d": "down"}}, "claude")
        touch, away = (67600, 67620, 67400, 67500), (67500, 67530, 67490, 67520)
        self.klines.upsert("BTCUSDT", "5m", self.to_rows(
            history(30) + [bar(0, *touch), bar(1, *away), bar(2, *touch), bar(3, *away),
                           bar(4, 67500, 67520, 67150, 67480, 25)]))
        for index in range(4):
            self.scan_at(index)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("IN_ZONE", self.sent[0])
        self.assertIn("⚠️ NGƯỢC D", self.sent[0])
        self.scan_at(4)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("ENTRY_VALID", self.sent[1])
        events = self.store.events(setup_id=row["id"])
        self.assertEqual([e["event"] for e in events],
                         ["CREATED", "IN_ZONE", "ZONE_EXIT", "IN_ZONE", "ZONE_EXIT", "IN_ZONE",
                          "TRIGGERED", "CONFIRMED", "ENTRY_VALID"])
        self.assertEqual([e["notified"] for e in events if e["event"] == "IN_ZONE"], [True, False, False])

    def test_first_touch_alert_survives_silent_tail_event(self):
        row = self.store.insert("BTCUSDT", self.clean, "claude")
        # Khong du nen lich su cho SMA: IN_ZONE kem INSUFFICIENT_DATA im lang o cuoi lo
        self.klines.upsert("BTCUSDT", "5m", self.to_rows(history(5) + [bar(0, 67600, 67620, 67400, 67450)]))
        self.scan_at(0)
        names = [e["event"] for e in self.store.events(setup_id=row["id"])]
        self.assertEqual(names, ["CREATED", "IN_ZONE", "INSUFFICIENT_DATA"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("IN_ZONE", self.sent[0])

    # TM - #ENTRY-CLOSED - Keo lai nen vua dong truoc khi xet
    def refreshing(self, on_refresh):
        calls = []

        async def refresh(symbol, timeframe):
            calls.append((symbol, timeframe))
            await on_refresh(symbol, timeframe)

        self.watcher.refresh = refresh
        return calls

    def spring_bars(self, close):
        # Xuyen zone_low 67200 voi vol 2.5x: dong tren 67200 moi la spring
        return self.to_rows(history(30) + [bar(0, 67300, 67320, 67150, close, 25)])

    def test_snapshot_not_final_does_not_trigger_after_refresh(self):
        # Mo phong ETH-20261104-B: ban chup dong tren muc, gia dong da chot lai duoi muc
        row = self.store.insert("BTCUSDT", self.clean, "claude")
        self.klines.upsert("BTCUSDT", "5m", self.spring_bars(67210))

        async def final(symbol, timeframe):
            self.klines.upsert("BTCUSDT", "5m", self.spring_bars(67190))

        calls = self.refreshing(final)
        self.scan_at(0)
        self.assertEqual(calls, [("BTCUSDT", "5m")])
        self.assertEqual([e["event"] for e in self.store.events(setup_id=row["id"])], ["CREATED", "IN_ZONE"])

    def test_same_snapshot_triggers_without_refresh(self):
        # Doi chung cho test tren: khong keo lai thi ban chup tao tin hieu sai
        row = self.store.insert("BTCUSDT", self.clean, "claude")
        self.klines.upsert("BTCUSDT", "5m", self.spring_bars(67210))
        self.scan_at(0)
        self.assertIn("TRIGGERED", [e["event"] for e in self.store.events(setup_id=row["id"])])

    def test_refresh_delivers_missing_candle(self):
        row = self.store.insert("BTCUSDT", self.clean, "claude")
        self.klines.upsert("BTCUSDT", "5m", self.to_rows(history(30)))

        async def arrive(symbol, timeframe):
            self.klines.upsert("BTCUSDT", "5m", self.spring_bars(67190))

        self.refreshing(arrive)
        self.scan_at(0)
        self.assertEqual(self.watcher.last_scan["errors"], {})
        self.assertEqual(self.store.events(setup_id=row["id"])[-1]["event"], "IN_ZONE")

    def test_refresh_failure_retries_then_uses_stored_data_and_reports(self):
        row = self.store.insert("BTCUSDT", self.clean, "claude")
        self.klines.upsert("BTCUSDT", "5m", self.spring_bars(67190))

        async def down(symbol, timeframe):
            raise RuntimeError("binance down")

        calls = self.refreshing(down)
        with mock.patch("sys.stderr"):
            self.scan_at(0)
        self.assertEqual(len(calls), entry_watcher.CANDLE_RETRIES + 1)
        self.assertEqual(self.watcher.last_scan["errors"], {"BTCUSDT": "xet tren nen chua keo lai duoc"})
        self.assertEqual(self.store.events(setup_id=row["id"])[-1]["event"], "IN_ZONE")

    def test_refresh_retries_only_until_success(self):
        self.store.insert("BTCUSDT", self.clean, "claude")
        self.klines.upsert("BTCUSDT", "5m", self.spring_bars(67190))
        outcomes = [RuntimeError("timeout"), None]

        async def flaky(symbol, timeframe):
            result = outcomes.pop(0)
            if result:
                raise result

        calls = self.refreshing(flaky)
        with mock.patch("sys.stderr"):
            self.scan_at(0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.watcher.last_scan["errors"], {})

    def test_refresh_only_symbols_with_active_setup(self):
        self.cfg["watcher"]["watch_symbols"] = ["ETHUSDT"]

        async def noop(symbol, timeframe):
            return None

        calls = self.refreshing(noop)
        self.scan_at(0)
        self.assertEqual(calls, [])

    def test_refresh_15m_only_at_m15_close_when_filter_is_on(self):
        filtered = {**self.clean, "m15_filter": True}
        self.store.insert("BTCUSDT", filtered, "claude")
        self.klines.upsert("BTCUSDT", "5m", self.to_rows(history(30)))
        calls = []

        async def record(symbol, timeframe):
            calls.append(timeframe)

        self.watcher.refresh = record
        m15 = server._tf_ms("15m")
        boundary = (T0 + m15) // m15 * m15
        self.now = boundary + 20_000
        self.klines.upsert("BTCUSDT", "5m", [[boundary - M5, 67650, 67700, 67600, 67650, 10, boundary - 1]])
        self.scan()
        self.assertEqual(sorted(calls), ["15m", "5m"])
        calls.clear()
        self.now = boundary + M5 + 20_000
        self.klines.upsert("BTCUSDT", "5m", [[boundary, 67650, 67700, 67600, 67650, 10, boundary + M5 - 1]])
        self.scan()
        self.assertEqual(calls, ["5m"])

    def test_refresh_hanging_is_bounded(self):
        self.store.insert("BTCUSDT", self.clean, "claude")
        self.klines.upsert("BTCUSDT", "5m", self.spring_bars(67190))

        async def hang(symbol, timeframe):
            await asyncio.sleep(3600)

        self.watcher.refresh = hang
        with mock.patch.object(entry_watcher, "REFRESH_TIMEOUT", 0.01), mock.patch("sys.stderr"):
            self.scan_at(0)
        self.assertIn("BTCUSDT", self.watcher.last_scan["errors"])

    def test_old_database_gets_htf_column(self):
        path = self.dir / "old.sqlite"
        conn = sqlite_db.connect(path)
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        for ddl in sqlite_db.MIGRATIONS[:3]:
            for statement in [s for s in ddl.split(";") if s.strip()]:
                conn.execute(statement)
        conn.execute("INSERT INTO schema_version (version) VALUES (3)")
        conn.execute(
            "INSERT INTO setups (id, code, symbol, bias_date, direction, status, zone_low, zone_high, "
            "trigger_type, entry, stop, target, invalidate_level, expires_at, created_at, updated_at) "
            "VALUES ('x', 'BTC-20261101-A', 'BTCUSDT', '2026-11-01', 'long', 'WAITING', 1, 2, 'spring', "
            "3, 1, 9, 0.5, 9, 1, 1)")
        conn.commit()
        conn.close()
        store = entry_store.SetupStore(path)
        self.assertEqual(store.get("x")["htf"], {})
        self.assertEqual(store.active("BTCUSDT")[0]["code"], "BTC-20261101-A")

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

    # TM - #ENTRY-HTF - Luu huong khung lon va nhan setup nguoc
    def test_save_stores_htf_and_counter_label(self):
        raw = {"direction": "long", "zone_low": 67200, "zone_high": 67450, "trigger_type": "spring",
               "entry": 67500, "stop": 67050, "target": 68400, "invalidate_level": 67000}
        down = {"last_closed_bar": {"close": 7}, "swing_highs": [{"price": 12}, {"price": 10}],
                "swing_lows": [{"price": 6}, {"price": 5}]}
        up = {"last_closed_bar": {"close": 11}, "swing_highs": [{"price": 10}, {"price": 12}],
              "swing_lows": [{"price": 5}, {"price": 6}]}
        contexts = {"1w": up, "1d": down, "4h": {"error": "khong co du lieu"}}
        with mock.patch.object(server, "_build_context", lambda sym, tf: contexts[tf]), \
                mock.patch.object(server, "_orb_bias", lambda sym, day: {"direction": "short"}):
            out = server.save_setups(server.DEFAULT_SYMBOL, [raw], replace=True)
        saved = out["saved"][0]
        self.assertEqual(saved["htf"], {"w": "up", "d": "down", "h4": None, "bias": "short"})
        self.assertEqual(saved["htf_counter"], ["D", "bias"])
        view = next(s for s in server.get_setups()["setups"] if s["code"] == saved["code"])
        self.assertEqual(view["htf_counter"], ["D", "bias"])
        page = admin_entry._setup_rows([view])
        self.assertIn("NGƯỢC D, bias", page)
        self.assertIn("W tăng · D giảm · H4 ? · bias short", page)

        def broken(sym, tf):
            raise RuntimeError("doc nen loi")

        with mock.patch.object(server, "_build_context", broken):
            out = server.save_setups(server.DEFAULT_SYMBOL, [raw], replace=True)
        self.assertEqual(len(out["saved"]), 1, "loi doc nen khong duoc chan luu setup")
        self.assertEqual(out["saved"][0]["htf_counter"], [])
        server.save_setups(server.DEFAULT_SYMBOL, [], replace=True)

    # TM - #ENTRY-VOL - Tu choi kem ly do va hien dieu kien volume khi luu qua tool
    def test_save_rejection_needs_volume_and_shows_rule(self):
        raw = {"direction": "long", "zone_low": 67200, "zone_high": 67450, "trigger_type": "rejection",
               "entry": 67500, "stop": 67050, "target": 68400, "invalidate_level": 67000}
        with mock.patch.object(server, "_setup_trends", lambda sym: {}):
            out = server.save_setups(server.DEFAULT_SYMBOL, [raw, {**raw, "trigger_params": {"max_vol_mult": 0.8}}],
                                     replace=True)
        self.assertEqual(out["rejected"][0]["index"], 0)
        self.assertIn("max_vol_mult", out["rejected"][0]["error"])
        self.assertEqual(len(out["saved"]), 1)
        view = next(s for s in server.get_setups()["setups"] if s["code"] == out["codes"][0])
        self.assertEqual(view["volume_rule"], "vol ≤ 0.8× SMA20")
        self.assertIn("vol ≤ 0.8× SMA20", admin_entry._setup_rows([view]))
        server.save_setups(server.DEFAULT_SYMBOL, [], replace=True)

    # TM - #SETUP-PARAMS - Tham so setup theo cap trong SQLite
    def clean_params(self):
        def wipe():
            with closing(sqlite_db.connect(server.SQLITE_PATH)) as conn, conn:
                conn.execute("DELETE FROM setup_params")
            server.ALERTS_FILE.unlink(missing_ok=True)
            server._ALERTS_CACHE.update(key=None, cfg=None)
        wipe()
        self.addCleanup(wipe)

    def test_config_seed_has_three_pairs_with_valid_params(self):
        seed = server.CFG["setup_params"]
        self.assertEqual(sorted(seed), ["BTCUSDT", "ETHUSDT", "XAUUSDT"])
        for symbol, values in seed.items():
            self.assertEqual(sorted(values), sorted(entry_logic.PARAM_KEYS), symbol)
            self.assertEqual(server._alert_params(values), values, symbol)

    def test_seed_saves_to_sqlite_once_and_never_overwrites(self):
        self.clean_params()
        self.assertEqual(server._seed_setup_params(), ["BTCUSDT", "ETHUSDT", "XAUUSDT"])
        with closing(sqlite_db.connect(server.SQLITE_PATH)) as conn:
            rows = conn.execute("SELECT symbol, source FROM setup_params ORDER BY symbol").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("BTCUSDT", "seed"), ("ETHUSDT", "seed"), ("XAUUSDT", "seed")])
        self.assertEqual(server._alerts_cfg()["watcher"]["symbol_overrides"]["XAUUSDT"]["vol_mult"], 2.0)
        self.assertEqual(server._seed_setup_params(), [], "khoi dong lai khong ghi them")
        server.SETUPS.save_params({"BTCUSDT": {"vol_mult": 3.0}}, "admin")
        self.assertEqual(server._seed_setup_params(), [])
        self.assertEqual(server.SETUPS.all_params()["BTCUSDT"], {"vol_mult": 3.0}, "khong ghi de chinh tay")

    def test_seed_adds_new_pair_and_keeps_legacy_manual_override(self):
        self.clean_params()
        server._write_json(server.ALERTS_FILE, {"watcher": {"symbol_overrides": {
            "btcusdt": {"vol_mult": 2.5}, "SOLUSDT": {"wick_pct": 0.7}, "BADUSDT": {"xyz": 1}}}})
        server._ALERTS_CACHE.update(key=None, cfg=None)
        with mock.patch("sys.stderr"):
            added = server._seed_setup_params()
        self.assertEqual(added, ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XAUUSDT"], "cap loi bi bo qua, khong lam hong")
        stored = server.SETUPS.all_params()
        self.assertEqual(stored["BTCUSDT"]["vol_mult"], 2.5, "gia tri chinh tay duoc giu")
        self.assertEqual(stored["BTCUSDT"]["wick_pct"], 0.6, "phan con lai lay tu goi y")
        self.assertEqual(stored["ETHUSDT"]["vol_mult"], 2.0)
        self.assertEqual(stored["SOLUSDT"], {"wick_pct": 0.7})

    def test_params_precedence_setup_then_pair_then_default(self):
        self.clean_params()
        server.SETUPS.save_params({"ETHUSDT": {"vol_mult": 3.0}}, "admin")
        server._ALERTS_CACHE.update(key=None, cfg=None)
        self.assertEqual(server.WATCHER.params_for("ETHUSDT")["vol_mult"], 3.0)
        self.assertEqual(server.WATCHER.params_for("BTCUSDT")["vol_mult"], 2.0)
        self.assertEqual(server.WATCHER.params_for("ETHUSDT", {"trigger_params": {"vol_mult": 4.0}})["vol_mult"], 4.0)

    def test_get_setup_params_tool(self):
        self.clean_params()
        server._seed_setup_params()
        server.SETUPS.save_params({server.DEFAULT_SYMBOL: {"rejection_test_max_vol": 0.7, "vol_mult": 2.5}}, "admin")
        server._ALERTS_CACHE.update(key=None, cfg=None)
        out = server.get_setup_params(server.DEFAULT_SYMBOL.lower())
        self.assertEqual(out["params"]["vol_mult"], 2.5)
        self.assertEqual((out["source"]["vol_mult"], out["source"]["wick_pct"]), ("symbol", "default"))
        self.assertEqual(out["rejection_trigger_params"],
                         {"test": {"max_vol_mult": 0.7}, "stopping": {"min_vol_mult": 2.0}})
        self.assertEqual(out["stored_by"], "admin")
        with closing(sqlite_db.connect(server.SQLITE_PATH)) as conn, conn:
            conn.execute("DELETE FROM setup_params")
        server._ALERTS_CACHE.update(key=None, cfg=None)
        empty = server.get_setup_params(server.DEFAULT_SYMBOL)
        self.assertEqual((empty["stored_by"], empty["updated_at"], empty["source"]["vol_mult"]), (None, None, "default"))

    def test_suggested_values_are_accepted_by_save_setups(self):
        self.clean_params()
        server._seed_setup_params()
        raw = {"direction": "long", "zone_low": 67200, "zone_high": 67450, "trigger_type": "rejection",
               "entry": 67500, "stop": 67050, "target": 68400, "invalidate_level": 67000}
        suggested = server.get_setup_params(server.DEFAULT_SYMBOL)["rejection_trigger_params"]
        with mock.patch.object(server, "_setup_trends", lambda sym: {}):
            out = server.save_setups(server.DEFAULT_SYMBOL, [{**raw, "trigger_params": suggested["test"]},
                                                             {**raw, "trigger_params": suggested["stopping"]}],
                                     replace=True)
        self.assertEqual((len(out["saved"]), out["rejected"]), (2, []))
        with mock.patch.object(server, "_setup_trends", lambda sym: {}):
            bad = server.save_setups(server.DEFAULT_SYMBOL, [{**raw, "trigger_params": {
                "max_vol_mult": 1, "rejection_test_max_vol": 0.8}}])
        self.assertIn("khong dung tham so", bad["rejected"][0]["error"])
        server.save_setups(server.DEFAULT_SYMBOL, [], replace=True)

    def test_admin_save_writes_pair_params_to_sqlite_only(self):
        self.clean_params()
        server._seed_setup_params()
        code, out = asyncio.run(server._alerts_action({
            "action": "watcher_save", "enabled": True, "plan_deadline_utc": "00:30",
            "watch_symbols": ["BTCUSDT"], "defaults": {},
            "symbol_overrides": {"BTCUSDT": {"vol_mult": 2.5}, "ETHUSDT": {}}}))
        self.assertEqual((code, out), (200, {"ok": True}))
        stored = server.SETUPS.all_params()
        self.assertEqual(stored["BTCUSDT"], {"vol_mult": 2.5})
        self.assertEqual(server.SETUPS.param_row("BTCUSDT")["source"], "admin")
        self.assertEqual(stored["ETHUSDT"], {}, "xoa tay thi de trong")
        self.assertEqual(stored["XAUUSDT"]["vol_mult"], 2.0, "cap khong gui len thi giu nguyen")
        self.assertNotIn("symbol_overrides", server._read_json(server.ALERTS_FILE, {})["watcher"])
        self.assertEqual(server.WATCHER.params_for("BTCUSDT")["vol_mult"], 2.5, "co hieu luc ngay")
        self.assertEqual(server._seed_setup_params(), [], "khoi dong lai khong nap lai cap da xoa tay")
        self.assertEqual(server.SETUPS.all_params()["ETHUSDT"], {})
        code, out = asyncio.run(server._alerts_action({
            "action": "watcher_save", "enabled": True, "plan_deadline_utc": "00:30",
            "watch_symbols": [], "defaults": {}, "symbol_overrides": {"BTCUSDT": {"vol_mult": -1}}}))
        self.assertEqual(code, 400)
        self.assertEqual(server.SETUPS.all_params()["BTCUSDT"], {"vol_mult": 2.5}, "du lieu loi khong ghi")

    # TM - #ENTRY-CLOSED - Ham keo lai nen cua watcher
    def test_watcher_refresh_pulls_with_overlap_and_reports_failure(self):
        seen = []

        async def pull(client, symbol, timeframe, overlap, source="schedule"):
            seen.append((symbol, timeframe, overlap, source))
            return {"ok": True}

        self.assertIs(server.WATCHER.refresh, server._watcher_refresh)
        with mock.patch.object(server, "_pull_since_latest", pull):
            asyncio.run(server._watcher_refresh("BTCUSDT", "5m"))
        self.assertEqual(seen, [("BTCUSDT", "5m", 1, "watcher")])

        async def late(client, symbol, timeframe, overlap, source="schedule"):
            return {"ok": False, "error": "chua co nen"}

        with mock.patch.object(server, "_pull_since_latest", late), \
                self.assertRaisesRegex(RuntimeError, "chua co nen"):
            asyncio.run(server._watcher_refresh("BTCUSDT", "5m"))

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
