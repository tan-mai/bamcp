"""Bo rule ORB rieng (BR "ORB Rule Set"): acceptance criteria o tang rule
(orb_rules), service (OrbService tren Harness) va backtest.

Tang server / trang admin (check_trade ORB vs OTHER, update_rules(orb=true),
get_orb_config, /admin/orb/rules): tests/test_server.py - OrbRuleSetServerTest.

So lieu BTC theo muc 5 cua BR: SL toi da 350, TP toi thieu 200, min_rr 1.5,
quota 2/ngay, daily stop -25, margin 20 x 100.
"""

# TM - #ORB-RULES - ORB Rule Set

from __future__ import annotations

import copy
import json
import unittest
from datetime import date

from orb_support import (P, Harness, default_sessions, ms, orb_cfg, scaled_rules)
from test_orb_admin_backtest import AFTER, build_bars

import orb
import orb_backtest
import orb_rules

DAY = "2026-09-29"
D = date.fromisoformat(DAY)
NY_OPEN = ms("2026-09-29 13:30")
COSTS = {"taker_fee_pct": 0.05, "slippage_pct": 0.02}
ATR = 462.0
PRICE = 83500.0


def at(hhmm: str) -> int:
    return ms(f"{DAY} {hhmm}")


def btc_block() -> dict:
    """Khoi orb nhu lan migrate dau: defaults BR + seed BTC."""
    return orb_rules.migrate(orb_cfg(), "BTCUSDT")[0]


def btc(**override) -> dict:
    return orb_rules.resolve(btc_block(), "BTCUSDT", override=override)


def score(entry: float, stop: float, target: float, *, resolved: dict | None = None,
          trade_type: str = "scalp", day_trades: int = 0, day_pnl: float = 0.0,
          margin: float = 20, symbol: str = "BTCUSDT") -> dict:
    return orb_rules.score(
        symbol=symbol, side="long" if target > entry else "short", entry=entry, stop=stop,
        target=target, margin_usd=margin, trade_type=trade_type,
        resolved=resolved or btc(), max_margin_per_trade=20, day_trades=day_trades,
        day_pnl=day_pnl, or_date=DAY, costs=COSTS)


def text(verdict: dict) -> str:
    return " | ".join(verdict["rule_violations"])


class SeedAndMigrateTest(unittest.TestCase):
    def test_btc_seed_matches_br(self):
        values = btc()["values"]
        self.assertEqual(
            {k: values[k] for k in ("max_stop_points", "min_take_profit_points", "min_rr",
                                    "max_trades_per_day", "daily_stop_loss", "margin_usd",
                                    "leverage")},
            {"max_stop_points": 350, "min_take_profit_points": 200, "min_rr": 1.5,
             "max_trades_per_day": 2, "daily_stop_loss": -25, "margin_usd": 20,
             "leverage": 100})
        self.assertEqual(btc()["sources"]["max_trades_per_day"], "defaults")
        self.assertEqual(btc()["sources"]["min_rr"], "symbol")
        self.assertEqual(values["max_or_atr_ratio"], 0.7)        # BR-ORB-16
        self.assertEqual(values["min_or_atr_ratio"], 0.3)

    def test_migrate_reads_old_quota_and_writes_history(self):
        cfg = orb_cfg()
        cfg["max_orb_trades_per_day"] = 3
        cfg["filters"] = {**cfg["filters"], "max_or_atr_ratio": 1.2}
        block, row = orb_rules.migrate(cfg, "BTCUSDT")
        self.assertEqual(block["defaults"]["max_trades_per_day"], 3)
        self.assertEqual(block["defaults"]["max_or_atr_ratio"], 0.7)   # khong lay tu yaml
        self.assertEqual((row["source"], row["reason"]), ("migrate", "migrate tu config.yaml"))
        self.assertIn("orb.defaults.max_trades_per_day", row["changes"])


class ScoreTest(unittest.TestCase):
    """AC-01 .. AC-08, AC-15 o tang ham cham."""

    def test_ac01_orb_long_passes(self):
        verdict = score(83800, 83500, 84250)
        self.assertEqual(verdict["rule_violations"], [])
        self.assertEqual(verdict["rule_set"], "orb")
        self.assertEqual(verdict["limits_applied"]["rule_set"], "orb")
        self.assertEqual(verdict["rr"], 1.5)
        self.assertNotIn("900", text(verdict))

    def test_ac03_stop_too_wide(self):
        self.assertIn("SL 400.0 > orb.max_stop_points (350)", score(83800, 83400, 84400)["rule_violations"])

    def test_ac04_rr_below_min(self):
        verdict = score(83800, 83500, 84200)
        self.assertIn("R:R 1.33 < orb.min_rr (1.5)", verdict["rule_violations"])
        self.assertEqual(len(verdict["rule_violations"]), 1)

    def test_ac05_symbol_without_orb_rules(self):
        resolved = orb_rules.resolve(btc_block(), "XAUUSDT")
        verdict = score(2650, 2640, 2670, resolved=resolved, symbol="XAUUSDT")
        self.assertIn("chua dat rule ORB cho XAUUSDT.max_stop_points", verdict["rule_violations"])
        self.assertIn("chua dat rule ORB cho XAUUSDT.min_take_profit_points",
                      verdict["rule_violations"])

    def test_ac06_swing_is_ignored(self):
        swing = score(83800, 83500, 84250, trade_type="swing")
        self.assertFalse(swing["demoted_to_scalp"])
        self.assertEqual(swing["rule_violations"], [])
        self.assertEqual(swing["limits_applied"]["max_stop_points"], 350)
        self.assertNotIn("swing", json.dumps(swing["limits_applied"]))

    def test_ac07_third_trade_blocked(self):
        self.assertEqual(score(83800, 83500, 84250, day_trades=1)["rule_violations"], [])
        self.assertIn(f"vuot orb.max_trades_per_day (2): da co 2 lenh ORB ngay {DAY}",
                      score(83800, 83500, 84250, day_trades=2)["rule_violations"])

    def test_ac08_daily_stop(self):
        self.assertEqual(score(83800, 83500, 84250, day_pnl=-24.9)["rule_violations"], [])
        verdict = score(83800, 83500, 84250, day_pnl=-25.5)
        self.assertIn(f"da cham orb.daily_stop_loss: PnL ORB {DAY} -25.5 <= -25",
                      verdict["rule_violations"])

    def test_ac15_liquidation_guard(self):
        # 83500 x (1/100 - 0.4% - 0.07%) = 442.55 diem; 80% = 354.04
        liq = orb_rules.liq_limit(PRICE, btc()["values"], COSTS)
        self.assertEqual((liq["liq_distance_points"], liq["limit_points"]), (442.55, 354.04))
        verdict = score(83500, 83050, 84400, resolved=btc(max_stop_points=600))
        self.assertTrue(any(v.startswith("SL vuot vung an toan thanh ly (354.04 diem)")
                            for v in verdict["rule_violations"]), verdict["rule_violations"])
        self.assertNotIn("max_stop_points", text(verdict))

    def test_margin_is_the_only_shared_rule(self):
        verdict = score(83800, 83500, 84250, margin=25)
        self.assertEqual(verdict["rule_violations"], ["margin 25.0 > max_margin_per_trade (20)"])

    def test_orb_disabled(self):
        block = btc_block()
        block["enabled"] = False
        verdict = score(83800, 83500, 84250, resolved=orb_rules.resolve(block, "BTCUSDT"))
        self.assertIn("ORB dang tat (orb.enabled = false)", verdict["rule_violations"])


class InfeasibleTest(unittest.TestCase):
    """AC-11, AC-18, AC-20, AC-21 (BR-ORB-11)."""

    def warnings(self, **override) -> list[str]:
        return orb_rules.infeasible(btc(**override)["values"], ATR, PRICE, COSTS)

    def test_seed_has_no_warning(self):
        self.assertEqual(self.warnings(), [])

    def test_ac11_tp_cannot_reach_min(self):
        found = self.warnings(min_take_profit_points=900, max_or_atr_ratio=1.2)
        tp = [w for w in found if w.startswith("rule_infeasible: TP toi da")]
        self.assertEqual(len(tp), 1, found)
        self.assertIn("~832 diem", tp[0])                   # 1.5 x 1.2 x 462 = 831.6
        self.assertIn("< orb.min_take_profit_points (900)", tp[0])

    def test_ac18_or_wider_than_stop(self):
        found = self.warnings(max_or_atr_ratio=1.2)
        self.assertTrue(any("OR toi da ~554 + buffer ~17" in w and "> orb.max_stop_points (350)" in w
                            for w in found), found)

    def test_ac20_tp_r_two_is_fine(self):
        self.assertEqual(self.warnings(tp_r=2.0), [])

    def test_ac21_buffer_makes_stop_infeasible(self):
        found = self.warnings(buffer_pct=0.1)
        self.assertTrue(any("OR toi da ~323 + buffer ~84 = ~407 diem > orb.max_stop_points (350)"
                            in w for w in found), found)

    def test_tp_r_below_min_rr(self):
        self.assertIn("rule_infeasible: tp_r 1.2 < orb.min_rr (1.5) - moi plan deu truot R:R",
                      self.warnings(tp_r=1.2))

    def test_no_atr_no_warning(self):
        self.assertEqual(orb_rules.infeasible(btc(tp_r=1.2)["values"], None, PRICE, COSTS), [])


class DeprecatedYamlTest(unittest.TestCase):
    def test_ac25_old_risk_block_ignored(self):
        cfg = orb_cfg()
        cfg["risk"] = {"max_leverage": 10, "risk_usd": 1}
        cfg["exit"] = {**cfg["exit"], "tp_r": 3.0}
        found = orb_rules.deprecated_fields(cfg)
        self.assertTrue(any("orb.risk.max_leverage" in w for w in found), found)
        self.assertTrue(any("orb.exit.tp_r" in w for w in found), found)
        params = orb.effective_params(cfg, rules=btc_block())
        self.assertEqual((params["leverage"], params["tp_r"]), (100, 1.5))
        sizing, _warnings = orb.size_position(PRICE, 300, params)
        self.assertEqual(sizing["qty"], 0.023)

    def test_repo_config_has_no_deprecated_field(self):
        self.assertEqual(orb_rules.deprecated_fields(orb_cfg()), [])


class ServiceTest(unittest.TestCase):
    """OrbService tren du lieu gia (nen phang quanh P = 100,000, ATR H1 = 100)."""

    def setUp(self):
        self.h = Harness(rules=btc_block())
        self.addCleanup(self.h.close)
        self.h.clock.now = at("06:00")
        self.h.sessions.set_enabled("ldn", False)
        self.h.market.day(NY_OPEN)

    def ny(self):
        return self.h.session("ny")

    def orb_trade(self, tid: int, pnl: float | None = None) -> dict:
        return {"id": tid, "strategy": "ORB", "session_id": "ny", "or_date": DAY,
                "side": "long", "pnl": pnl, "_journal_date": DAY}

    def check(self, **kw) -> dict:
        args = {"side": "long", "entry": 83800, "stop": 83500, "target": 84250,
                "margin_usd": 20, "at": at("14:00"), **kw}
        return self.h.svc.score_trade("ny", DAY, **args)

    def breakout(self) -> dict:
        self.h.market.put(at("13:50"), P + 10, P + 120, P + 5, P + 100)
        self.h.drive(at("13:45"), at("13:56"))
        return self.h.svc.signal(self.ny(), D, at("13:56"))

    def test_ac01_check_trade_in_window_passes(self):
        self.h.market.mark_fetched(at("14:00"))
        verdict = self.check()
        self.assertTrue(verdict["would_pass"], verdict["rule_violations"])
        self.assertEqual(verdict["rule_set"], "orb")
        self.assertEqual(verdict["orb_checks"]["violations"], [])

    def test_ac06_swing_in_service(self):
        self.h.market.mark_fetched(at("14:00"))
        verdict = self.check(trade_type="swing")
        self.assertTrue(verdict["would_pass"])
        self.assertFalse(verdict["demoted_to_scalp"])

    def test_ac07_ac08_quota_and_stop_from_journal(self):
        self.h.market.mark_fetched(at("14:00"))
        self.h.journal.extend([self.orb_trade(1, -10.0), self.orb_trade(2, 5.0)])
        self.assertIn("vuot orb.max_trades_per_day (2)", " ".join(self.check()["orb_rule_violations"]))
        self.h.journal[:] = [self.orb_trade(1, -25.5)]
        verdict = self.check()
        self.assertEqual(len(verdict["orb_rule_violations"]), 1)
        self.assertIn("da cham orb.daily_stop_loss", verdict["orb_rule_violations"][0])
        self.assertEqual(self.h.svc.day_stats(D), {"trades": 1, "closed": 1, "pnl": -25.5})

    def test_ac10_plan_rule_check_matches_check_trade(self):
        sig = self.breakout()
        plan = sig["plan"]
        self.assertIsNotNone(plan)
        # TP 195 diem < 200: plan van tra ve de thay rule_check, nhung bi chan
        self.assertEqual((plan["entry"], plan["sl"], plan["tp"]), (P + 100, P - 30, P + 295))
        self.assertFalse(plan["rule_check"]["would_pass"])
        self.assertIn("rules", sig["blocked_by"])
        self.assertEqual(plan["blocked_by"], sig["blocked_by"])
        self.assertEqual(sig["filters"]["rules"], "fail")
        verdict = self.h.svc.score_trade("ny", DAY, side=plan["direction"], entry=plan["entry"],
                                         stop=plan["sl"], target=plan["tp"],
                                         margin_usd=plan["margin"], at=at("13:56"))
        self.assertEqual(verdict["rule_violations"], plan["rule_check"]["rule_violations"])
        self.assertIn("TP 195.0 < orb.min_take_profit_points (200)", verdict["rule_violations"])

    def test_ac14_plan_qty_uses_margin_x_leverage(self):
        self.h.rules["symbol_overrides"]["BTCUSDT"]["min_take_profit_points"] = 50
        sig = self.breakout()
        plan = sig["plan"]
        self.assertTrue(plan["rule_check"]["would_pass"], plan["rule_check"])
        self.assertEqual(plan["qty"], 0.019)                 # 20 x 100 / 100100
        self.assertNotIn("blocked_by", sig)

    def test_ac20_tp_r_change_applies_immediately(self):
        self.h.rules["symbol_overrides"]["BTCUSDT"]["min_take_profit_points"] = 50
        self.h.rules["defaults"]["tp_r"] = 2.0
        plan = self.breakout()["plan"]
        self.assertEqual(plan["tp"], P + 360)                 # 2R, R = 130
        self.assertEqual(self.h.svc.params(self.ny())["rule_sources"]["tp_r"], "defaults")

    def test_ac16_wide_or_filtered(self):
        h = Harness(rules=btc_block())
        self.addCleanup(h.close)
        h.clock.now = at("06:00")
        h.sessions.set_enabled("ldn", False)
        h.market.day(NY_OPEN, or_half=37.5)                 # OR 75 / ATR 100 = 0.75
        h.drive(at("13:45"), at("13:50"))
        rng = h.svc.opening_range(h.session("ny"), D, at("13:50"), h.svc.params(h.session("ny")))
        self.assertEqual((rng["range_flag"], rng["filtered"]), ("too_wide", True))
        self.assertIsNone(h.svc.signal(h.session("ny"), D, at("13:50"))["plan"])

    def test_ac17_session_override_resolves_per_session(self):
        self.h.rules["defaults"].update(max_or_atr_ratio=0.8, min_or_atr_ratio=0.25)
        self.h.sessions.save({**self.ny(), "overrides": {"max_or_atr_ratio": 0.9}},
                             original_id="ny")
        ny = self.h.svc.params(self.ny())
        ldn = self.h.svc.params(self.h.session("ldn"))
        self.assertEqual((ny["max_or_atr_ratio"], ny["rule_sources"]["max_or_atr_ratio"]),
                         (0.9, "session"))
        self.assertEqual((ldn["max_or_atr_ratio"], ldn["rule_sources"]["max_or_atr_ratio"]),
                         (0.8, "defaults"))
        self.assertEqual((ny["min_or_atr_ratio"], ldn["min_or_atr_ratio"]), (0.25, 0.25))

    def test_session_override_must_stay_consistent(self):
        with self.assertRaises(orb.SessionValidationError) as ctx:
            self.h.sessions.save({**self.ny(), "overrides": {"min_or_atr_ratio": 0.8}},
                                 original_id="ny")
        self.assertIn("overrides", ctx.exception.errors)

    def test_ac22_bias_filter_off(self):
        self.h.rules["symbol_overrides"]["BTCUSDT"]["min_take_profit_points"] = 50
        self.h.bias = {"direction": "short", "date": DAY}
        self.assertEqual(self.breakout()["blocked_by"], ["bias_against"])
        self.h.rules["defaults"]["use_bias_filter"] = False
        sig = self.h.svc.signal(self.ny(), D, at("13:56"))
        self.assertEqual(sig["filters"]["bias"], "off")
        self.assertNotIn("bias_against", sig.get("blocked_by", []))
        self.assertIsNotNone(sig["plan"])

    def test_ac23_news_day_filters_session(self):
        (self.h.root / "news_days.json").write_text(
            json.dumps([{"date": DAY, "note": "CPI"}]), encoding="utf-8")
        self.h.market.put(at("13:50"), P + 10, P + 120, P + 5, P + 100)
        self.h.drive(at("13:45"), at("13:56"))
        sig = self.h.svc.signal(self.ny(), D, at("13:56"))
        self.assertTrue(sig["opening_range"]["news_day"])
        self.assertTrue(sig["opening_range"]["filtered"])
        self.assertIn("news_day", sig["opening_range"]["filter_reason"])
        self.assertIsNone(sig["plan"])
        self.assertEqual(self.h.state("ny", DAY)["watch"]["stop_reason"], "filtered")
        # tat skip_news_days -> ngay tin khong con loc
        self.h.rules["defaults"]["skip_news_days"] = False
        rng = self.h.svc.opening_range(self.ny(), D, at("13:56"), self.h.svc.params(self.ny()))
        self.assertEqual((rng["news_day"], rng["filtered"]), (True, False))

    def test_ac24_orb_disabled(self):
        self.h.rules["enabled"] = False
        self.h.drive(at("13:40"), at("14:30"))
        self.assertIsNone(self.h.state("ny", DAY))           # khong co phase 1 / watch
        self.assertEqual(self.h.market.requests, [])
        self.assertEqual(self.h.sched.jobs(at("14:30")), [])
        sig = self.h.svc.signal(self.ny(), D, at("14:30"))
        self.assertEqual((sig["data_status"], sig["blocked_by"]), ("orb_disabled", ["orb_disabled"]))
        self.assertIn("ORB dang tat (orb.enabled = false)", self.check()["rule_violations"])
        self.assertEqual(self.h.svc.list_sessions()[0]["job_status"], "orb_disabled")

    def test_ac24_disable_stops_running_watch(self):
        self.h.drive(at("13:40"), at("13:51"))
        self.assertTrue(self.h.state("ny", DAY)["watch"]["active"])
        self.h.rules["enabled"] = False
        self.h.run_due(at("13:52"))
        watch = self.h.state("ny", DAY)["watch"]
        self.assertEqual((watch["active"], watch["stop_reason"]), (False, "orb_disabled"))


class BacktestRulesTest(unittest.TestCase):
    """AC-13, AC-20 cho backtest_orb."""

    def run_bt(self, rules: dict | None = None, **kw) -> dict:
        return orb_backtest.simulate(
            build_bars(), list(default_sessions().values()), orb_cfg(),
            from_day=date(2026, 9, 28), to_day=date(2026, 9, 29),
            split_day=date(2026, 9, 29), initial_equity=100, now_ms=AFTER,
            rules=rules if rules is not None else scaled_rules(), **kw)

    def test_ac13_rules_snapshot_and_counters(self):
        res = self.run_bt()
        self.assertEqual(set(res) & {"rules_used", "rejected_by_rule", "stopped_days"},
                         {"rules_used", "rejected_by_rule", "stopped_days"})
        self.assertEqual(res["rules_used"]["by_session"]["ny"]["values"]["max_stop_points"], 350)
        counted = [t for t in res["trades"] if t["counted"]]
        self.assertEqual(len(counted), 2)
        self.assertTrue(all(t["rule_violations"] == "" for t in counted))

    def test_ac13_override_rejects_every_trade(self):
        res = self.run_bt(rules_override={"min_take_profit_points": 900})
        self.assertEqual(res["summary"]["trades"], 0)
        self.assertGreaterEqual(res["rejected_by_rule"].get("min_take_profit_points", 0), 2)
        self.assertEqual(res["rules_used"]["rules_override"], {"min_take_profit_points": 900})
        # rules.json (khoi rules truyen vao) khong bi sua
        self.assertEqual(scaled_rules()["symbol_overrides"]["BTCUSDT"]["min_take_profit_points"], 50)

    def test_daily_stop_days_reported(self):
        res = self.run_bt(rules_override={"daily_stop_loss": -0.01})
        self.assertEqual([d["or_date"] for d in res["stopped_days"]], ["2026-09-28"])

    def test_ac20_backtest_uses_new_tp_r(self):
        rules = scaled_rules()
        rules["defaults"]["tp_r"] = 2.0
        res = self.run_bt(rules)
        ny = [t for t in res["trades"] if t["session_id"] == "ny"]
        self.assertEqual({t["tp"] for t in ny}, {P + 360})

    def test_rules_block_not_mutated(self):
        rules = scaled_rules()
        before = copy.deepcopy(rules)
        self.run_bt(rules, rules_override={"tp_r": 2.5})
        self.assertEqual(rules, before)


if __name__ == "__main__":
    unittest.main()
