"""Logic ORB thuan (orb.py): gio mo theo DST, lich nghi, validate phien, state
machine, sizing, bo loc range. TC-01..07, 11, 15..20 cua requirement."""

# TM - #ORB - ORB Enhancement

from __future__ import annotations

import unittest
from datetime import date

from orb_support import M5_MS, P, bar, default_sessions, ms, orb_cfg

import orb
import orb_rules


def params(**overrides):
    return orb.effective_params(orb_cfg(), None, overrides or None)


SESSIONS = default_sessions()
NY, LDN = SESSIONS["ny"], SESSIONS["ldn"]
OR_HIGH, OR_LOW = P + 30, P - 30
T0 = ms("2026-09-29 13:45")


def times(session, day):
    return orb.session_times(session, date.fromisoformat(day), params())


class SessionTimesTest(unittest.TestCase):
    def test_tc01_new_york_edt(self):
        t = times(NY, "2026-09-29")
        self.assertEqual(orb.iso_utc(t["open"]), "2026-09-29T13:30:00Z")
        self.assertEqual(orb.iso_utc(t["or_end"]), "2026-09-29T13:45:00Z")
        self.assertEqual(orb.iso_vn(t["open"]), "2026-09-29T20:30:00+07:00")
        # trade window mac dinh 180 phut tinh tu luc OR chot
        self.assertEqual(orb.iso_utc(t["window_end"]), "2026-09-29T16:45:00Z")

    def test_tc02_new_york_after_us_dst_ends(self):
        t = times(NY, "2026-11-02")
        self.assertEqual(orb.iso_utc(t["open"]), "2026-11-02T14:30:00Z")
        self.assertEqual(orb.iso_vn(t["open"]), "2026-11-02T21:30:00+07:00")

    def test_tc03_new_york_around_us_dst_start(self):
        self.assertEqual(orb.iso_utc(times(NY, "2026-03-06")["open"]), "2026-03-06T14:30:00Z")
        self.assertEqual(orb.iso_utc(times(NY, "2026-03-09")["open"]), "2026-03-09T13:30:00Z")

    def test_tc04_london_bst(self):
        t = times(LDN, "2026-09-29")
        self.assertEqual(orb.iso_utc(t["open"]), "2026-09-29T07:00:00Z")
        self.assertEqual(orb.iso_utc(t["or_end"]), "2026-09-29T07:15:00Z")
        self.assertEqual(orb.iso_vn(t["open"]), "2026-09-29T14:00:00+07:00")

    def test_tc05_london_around_bst_end(self):
        self.assertEqual(orb.iso_utc(times(LDN, "2026-10-23")["open"]), "2026-10-23T07:00:00Z")
        t = times(LDN, "2026-10-26")
        self.assertEqual(orb.iso_utc(t["open"]), "2026-10-26T08:00:00Z")
        self.assertEqual(orb.iso_vn(t["open"]), "2026-10-26T15:00:00+07:00")

    def test_tc06_uk_changed_us_not_yet(self):
        self.assertEqual(orb.iso_utc(times(LDN, "2026-10-28")["open"]), "2026-10-28T08:00:00Z")
        self.assertEqual(orb.iso_utc(times(NY, "2026-10-28")["open"]), "2026-10-28T13:30:00Z")

    def test_window_override_per_session(self):
        custom = {**NY, "trade_window_minutes": 60}
        t = orb.session_times(custom, date(2026, 9, 29), orb.effective_params(orb_cfg(), custom))
        self.assertEqual(orb.iso_utc(t["window_end"]), "2026-09-29T14:45:00Z")


class TradeDayTest(unittest.TestCase):
    def test_tc07_weekend(self):
        for day in (date(2026, 10, 3), date(2026, 10, 4)):      # Thu Bay, Chu nhat
            for session in (NY, LDN):
                ok, reason = orb.trade_day_status(session, day)
                self.assertFalse(ok)
                self.assertIn("trade_days", reason)

    def test_tc07_holidays(self):
        cases = [
            (NY, date(2026, 11, 26)),   # Thanksgiving
            (NY, date(2026, 7, 3)),     # 04/07 roi vao Thu Bay -> nghi Thu Sau
            (NY, date(2026, 4, 3)),     # Good Friday
            (NY, date(2026, 12, 25)),
            (LDN, date(2026, 4, 3)),    # Good Friday
            (LDN, date(2026, 4, 6)),    # Easter Monday
            (LDN, date(2026, 8, 31)),   # Summer bank holiday
            (LDN, date(2026, 12, 28)),  # Boxing Day (26/12 la Thu Bay) nghi bu
        ]
        for session, day in cases:
            with self.subTest(session=session["session_id"], day=day):
                ok, reason = orb.trade_day_status(session, day)
                self.assertFalse(ok)
                self.assertIn("ngay le", reason)

    def test_normal_days_trade(self):
        for session in (NY, LDN):
            self.assertEqual(orb.trade_day_status(session, date(2026, 9, 29)), (True, None))
        # Thanksgiving chi la ngay le cua lich US
        self.assertTrue(orb.trade_day_status(LDN, date(2026, 11, 26))[0])

    def test_next_opens_skip_weekend_and_holiday(self):
        after = ms("2026-10-02 18:00")               # Thu Sau, sau phien NY
        opens = [orb.iso_utc(x) for x in orb.next_opens(NY, after, 3)]
        self.assertEqual(opens, ["2026-10-05T13:30:00Z", "2026-10-06T13:30:00Z",
                                 "2026-10-07T13:30:00Z"])
        after = ms("2026-11-25 18:00")
        self.assertEqual(orb.iso_utc(orb.next_opens(NY, after, 1)[0]), "2026-11-27T14:30:00Z")


class ValidateSessionTest(unittest.TestCase):
    BASE = {"session_id": "tokyo", "name": "Tokyo", "timezone": "Asia/Tokyo",
            "open_time": "09:00", "trade_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "holiday_calendar": "none"}

    def errors(self, **changes):
        with self.assertRaises(orb.SessionValidationError) as ctx:
            orb.validate_session({**self.BASE, **changes}, existing_ids=["ldn", "ny"])
        return ctx.exception.errors

    def test_valid(self):
        clean = orb.validate_session(self.BASE, existing_ids=["ldn", "ny"])
        self.assertEqual(clean["session_id"], "tokyo")
        self.assertTrue(clean["enabled"])
        self.assertIsNone(clean["trade_window_minutes"])

    def test_tc11_open_time_not_on_m15(self):
        errs = self.errors(open_time="08:10")
        self.assertEqual(list(errs), ["open_time"])
        self.assertIn("15", errs["open_time"])

    def test_tc11_bad_timezone(self):
        errs = self.errors(timezone="Mars/Olympus")
        self.assertEqual(list(errs), ["timezone"])

    def test_tc11_reports_every_field(self):
        errs = self.errors(open_time="8h", timezone="GMT+7x", trade_days=[], name="")
        self.assertEqual(set(errs), {"open_time", "timezone", "trade_days", "name"})

    def test_duplicate_and_bad_id(self):
        self.assertIn("session_id", self.errors(session_id="ny"))
        self.assertIn("session_id", self.errors(session_id="New York"))

    def test_id_cannot_change_on_edit(self):
        with self.assertRaises(orb.SessionValidationError) as ctx:
            orb.validate_session({**NY, "session_id": "ny2"}, existing_ids=["ny"], original=NY)
        self.assertIn("session_id", ctx.exception.errors)

    def test_override_whitelist(self):
        ok = orb.validate_session({**self.BASE, "overrides": {"entry_mode": "retest",
                                                             "tp_r": "2"}},
                                  existing_ids=[])
        self.assertEqual(ok["overrides"], {"entry_mode": "retest", "tp_r": 2.0})
        self.assertIn("overrides", self.errors(overrides={"risk_usd": 5}))
        self.assertIn("overrides", self.errors(overrides={"entry_mode": "market"}))


class ParamsTest(unittest.TestCase):
    def test_precedence_config_session_call(self):
        session = {**NY, "max_trades": 2, "overrides": {"tp_r": 2.0}}
        p = orb.effective_params(orb_cfg(), session, {"tp_r": 3})
        self.assertEqual(p["max_trades"], 2)
        self.assertEqual(p["tp_r"], 3.0)
        self.assertEqual(orb.effective_params(orb_cfg(), session)["tp_r"], 2.0)
        self.assertEqual(orb.effective_params(orb_cfg())["tp_r"], 1.5)

    def test_nested_and_invalid_overrides(self):
        self.assertEqual(orb.normalize_overrides({"entry": {"mode": "touch"}}),
                         {"entry_mode": "touch"})
        for bad in ({"tp_r": 0}, {"nope": 1}, {"allow_reversal": "maybe"}, "x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                orb.normalize_overrides(bad)

    def test_missing_config_key_fails_fast(self):
        cfg = orb_cfg()
        del cfg["exit"]["sl_mode"]
        with self.assertRaises(ValueError):
            orb.effective_params(cfg)

    # TM - #ORB-RULES - ORB Rule Set
    def test_rule_params_come_from_rule_set_not_yaml(self):
        cfg = orb_cfg()
        cfg["exit"] = {**cfg["exit"], "tp_r": 9.0}     # yaml cu bi bo qua (BR-ORB-18)
        block = orb_rules.migrate(orb_cfg())[0]
        block["defaults"]["tp_r"] = 2.0
        p = orb.effective_params(cfg, rules=block)
        self.assertEqual((p["tp_r"], p["rule_sources"]["tp_r"]), (2.0, "defaults"))
        del block["defaults"]["tp_r"]
        with self.assertRaises(ValueError) as ctx:
            orb.effective_params(cfg, rules=block)
        self.assertIn("chua dat rule ORB cho BTCUSDT.tp_r", str(ctx.exception))


def m5(i, o, h, low, c):
    return bar(T0 + i * M5_MS, o, h, low, c)


class ScanSignalsTest(unittest.TestCase):
    def test_tc15_wick_only_is_not_breakout(self):
        bars = [m5(0, P, P + 80, P - 5, P + 10)]      # rau vuot OR high, dong trong OR
        self.assertEqual(orb.scan_signals(OR_HIGH, OR_LOW, bars, params()), [])

    def test_tc16_close_above_is_breakout_long(self):
        bars = [m5(0, P, P + 20, P - 5, P + 10), m5(1, P + 10, P + 120, P + 5, P + 100)]
        events = orb.scan_signals(OR_HIGH, OR_LOW, bars, params())
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual((ev["state"], ev["direction"], ev["variant"]),
                         ("BREAKOUT_LONG", "long", "breakout"))
        self.assertTrue(ev["entry_ready"])
        self.assertEqual(ev["entry_price"], P + 100)
        self.assertTrue(orb.actionable(ev, params()))

    def test_close_below_is_breakout_short(self):
        events = orb.scan_signals(OR_HIGH, OR_LOW, [m5(0, P, P + 5, P - 90, P - 80)], params())
        self.assertEqual(events[0]["state"], "BREAKOUT_SHORT")
        self.assertEqual(events[0]["direction"], "short")

    def test_tc17_failed_breakout_within_lookback(self):
        bars = [m5(0, P, P + 150, P - 5, P + 100),      # pha len
                m5(1, P + 100, P + 110, P + 40, P + 60),  # van ngoai OR
                m5(2, P + 60, P + 70, P - 10, P)]          # dong cua tro vao OR
        events = orb.scan_signals(OR_HIGH, OR_LOW, bars, params())
        self.assertEqual([e["state"] for e in events], ["BREAKOUT_LONG", "FAILED_BREAKOUT_LONG"])
        failed = events[-1]
        self.assertEqual(failed["direction"], "short")
        self.assertEqual(failed["variant"], "reversal")
        self.assertEqual(failed["extreme"], P + 150)
        self.assertFalse(orb.actionable(failed, params()))
        self.assertTrue(orb.actionable(failed, params(allow_reversal=True)))

    def test_tc17_reversal_plan_uses_breakout_extreme(self):
        plan, warnings = orb.build_plan(direction="short", entry=P, or_high=OR_HIGH,
                                        or_low=OR_LOW, params=params(allow_reversal=True),
                                        trigger_close_ms=T0, variant="reversal",
                                        reversal_extreme=P + 150)
        self.assertEqual(plan["sl"], P + 150)
        self.assertEqual(plan["r_distance"], 150)
        self.assertEqual(plan["tp"], P - 225)
        self.assertEqual(plan["sl_mode"], "reversal_extreme")

    def test_close_back_inside_after_lookback_is_not_failed(self):
        bars = [m5(0, P, P + 150, P - 5, P + 100)] + \
               [m5(i, P + 100, P + 110, P + 40, P + 60) for i in (1, 2, 3)] + \
               [m5(4, P + 60, P + 70, P - 10, P)]
        events = orb.scan_signals(OR_HIGH, OR_LOW, bars, params())
        self.assertEqual([e["state"] for e in events], ["BREAKOUT_LONG"])

    def test_opposite_breakout_restarts(self):
        bars = [m5(0, P, P + 150, P - 5, P + 100),
                m5(1, P + 100, P + 100, P - 120, P - 100)]   # dong duoi OR low
        events = orb.scan_signals(OR_HIGH, OR_LOW, bars, params())
        self.assertEqual([e["state"] for e in events], ["BREAKOUT_LONG", "BREAKOUT_SHORT"])

    def test_retest_mode_waits_for_retest(self):
        p = params(entry_mode="retest")
        bars = [m5(0, P, P + 120, P + 40, P + 100)]
        events = orb.scan_signals(OR_HIGH, OR_LOW, bars, p)
        self.assertEqual(events[0]["state"], "BREAKOUT_LONG")
        self.assertFalse(events[0]["entry_ready"])
        self.assertFalse(orb.actionable(events[0], p))
        # cham lai canh OR high, dong cua tren -> vao lenh
        bars.append(m5(1, P + 100, P + 110, P + 20, P + 60))
        events = orb.scan_signals(OR_HIGH, OR_LOW, bars, p)
        self.assertEqual(events[-1]["variant"], "retest")
        self.assertTrue(events[-1]["entry_ready"])
        self.assertEqual(events[-1]["entry_price"], P + 60)
        self.assertTrue(orb.actionable(events[-1], p))

    def test_touch_mode_and_both_sides(self):
        p = params(entry_mode="touch")
        level = OR_HIGH * (1 + p["buffer_pct"] / 100)
        events = orb.scan_signals(OR_HIGH, OR_LOW, [m5(0, P, P + 60, P - 5, P + 40)], p)
        self.assertEqual(events[0]["state"], "BREAKOUT_LONG")
        self.assertAlmostEqual(events[0]["entry_price"], level)
        both = orb.scan_signals(OR_HIGH, OR_LOW, [m5(0, P, P + 100, P - 100, P)], p)
        self.assertEqual(both, [{"state": None, "index": 0, "warning": "both_sides_touched"}])


class SizingTest(unittest.TestCase):
    # TM - #ORB-RULES - ORB Rule Set: size = margin_usd x leverage / entry (BR-ORB-15)
    def test_ac14_sizing_margin_x_leverage(self):
        sizing, warnings = orb.size_position(83500, 300, params())
        self.assertEqual(sizing["qty"], 0.023)              # 20 x 100 / 83500 = 0.02395
        self.assertEqual(sizing["notional"], 1920.5)
        self.assertEqual(sizing["margin"], 19.2)             # 1920.5 / 100, lam tron 2 so
        self.assertEqual((sizing["margin_usd"], sizing["leverage"]), (20.0, 100.0))
        self.assertEqual(sizing["sizing"], "margin_x_leverage")
        self.assertEqual(sizing["fee_est_usd"], 2.69)       # 1920.5 x 2 x 0.07%
        self.assertEqual(sizing["risk_actual_usd"], 6.9)
        self.assertEqual(warnings, [])

    def test_tc18_plan_matches(self):
        plan, warnings = orb.build_plan(direction="long", entry=100000, or_high=100000,
                                        or_low=99500, params=params(), trigger_close_ms=T0,
                                        risk_usd=1)
        self.assertEqual((plan["sl"], plan["tp"], plan["r_distance"]), (99500, 100750, 500))
        # risk_usd khong con anh huong size
        self.assertEqual((plan["qty"], plan["notional"], plan["margin"]), (0.02, 2000, 20))
        self.assertEqual(plan["risk_usd"], 10)
        self.assertEqual(plan["time_exit_utc"], "2026-09-29T16:45:00Z")

    def test_tc19_below_min_notional_warns_and_keeps_size(self):
        p = {**params(), "margin_usd": 0.6, "leverage": 100.0}
        sizing, warnings = orb.size_position(60000, 600, p)
        self.assertEqual(sizing["qty"], 0.001)              # khong tu tang size
        self.assertEqual(sizing["notional"], 60)
        self.assertTrue(any(w.startswith("below_min_notional") for w in warnings))

    def test_qty_below_step(self):
        p = {**params(), "margin_usd": 0.4, "leverage": 100.0}
        sizing, warnings = orb.size_position(100000, 500, p)
        self.assertEqual(sizing["qty"], 0)
        self.assertTrue(any(w.startswith("qty_below_step") for w in warnings))

    def test_missing_sizing_rule_gives_no_qty(self):
        p = {k: v for k, v in params().items() if k != "margin_usd"}
        sizing, warnings = orb.size_position(100000, 500, p)
        self.assertIsNone(sizing["qty"])
        self.assertTrue(any(w.startswith("no_sizing") for w in warnings))

    def test_sl_mid(self):
        plan, _ = orb.build_plan(direction="long", entry=P + 40, or_high=OR_HIGH,
                                 or_low=OR_LOW, params=params(sl_mode="mid"),
                                 trigger_close_ms=T0)
        self.assertEqual(plan["sl"], P)
        self.assertEqual(plan["r_distance"], 40)

    def test_invalid_sl(self):
        plan, warnings = orb.build_plan(direction="long", entry=P - 40, or_high=OR_HIGH,
                                        or_low=OR_LOW, params=params(), trigger_close_ms=T0)
        self.assertIsNone(plan)
        self.assertTrue(warnings[0].startswith("invalid_sl"))


class RangeFilterTest(unittest.TestCase):
    def test_tc20_range_flags(self):
        p = params()
        self.assertEqual(orb.range_metrics(P + 10, P - 10, 100, p)["range_flag"], "too_narrow")
        self.assertEqual(orb.range_metrics(P + 70, P - 70, 100, p)["range_flag"], "too_wide")
        ok = orb.range_metrics(P + 30, P - 30, 100, p)
        self.assertEqual((ok["range_flag"], ok["size"], ok["size_atr_ratio"]), ("ok", 60, 0.6))
        # TM - #ORB-RULES - ORB Rule Set: bien 0.3 va 0.7 (BR-ORB-16) van hop le
        self.assertEqual(orb.range_metrics(P + 15, P - 15, 100, p)["range_flag"], "ok")
        self.assertEqual(orb.range_metrics(P + 35, P - 35, 100, p)["range_flag"], "ok")
        self.assertEqual(orb.range_metrics(P + 36, P - 36, 100, p)["range_flag"], "too_wide")
        self.assertIsNone(orb.range_metrics(P + 30, P - 30, None, p)["range_flag"])

    def test_ac16_or_atr_0748_is_too_wide(self):
        # OR 345.9 / ATR 462.42 = 0.748 > 0.7
        m = orb.range_metrics(83845.9, 83500.0, 462.42, params())
        self.assertEqual(m["range_flag"], "too_wide")

    def test_atr_wilder_flat(self):
        bars = [bar(i * 3600_000, P, P + 50, P - 50, P) for i in range(200)]
        self.assertAlmostEqual(orb.atr_wilder(bars, 14), 100.0)

    def test_bias_check(self):
        self.assertEqual(orb.bias_check("long", {"direction": "long"}, True), "ok")
        self.assertEqual(orb.bias_check("long", {"direction": "short"}, True), "against")
        self.assertEqual(orb.bias_check("long", {"direction": "neutral"}, True), "neutral")
        self.assertEqual(orb.bias_check("long", None, True), "missing")
        self.assertEqual(orb.bias_check("long", {"direction": "short"}, False), "off")


if __name__ == "__main__":
    unittest.main()
