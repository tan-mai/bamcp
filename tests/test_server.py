"""Tang server: tool cu khong doi hanh vi khi khong truyen tham so moi (TC-25),
check_trade / log_trade voi strategy = ORB (TC-22, TC-28), tool ORB co ban.

Chay tren thu muc du lieu tam (BAMCP_DATA_ROOT), khong goi mang: tai khoan san tat,
chua co file nen nao.
"""

# TM - #ORB - ORB Enhancement

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="bamcp-test-")
os.environ["BAMCP_DATA_ROOT"] = _TMP
os.environ["BAMCP_ACCOUNT_ENABLED"] = "false"
os.environ.pop("BAMCP_ORB_ENABLED", None)

import server  # noqa: E402

OLD_TODAY_KEYS = {"date", "symbol", "counted_from", "trades_taken", "trades_remaining",
                  "open_trades", "open_trades_journal", "realized_pnl", "journal_pnl",
                  "daily_stop_hit", "can_trade", "rules", "rules_scope",
                  "rules_changed_today", "exchange", "trades"}
# TM - #ORB-RULES - ORB Rule Set: khoa them cho quota/daily stop ORB rieng (BR-ORB-05/06)
ORB_TODAY_KEYS = {"orb_trades_taken", "orb_trades_remaining", "orb_realized_pnl",
                  "orb_daily_stop_hit", "can_trade_orb", "orb_status"}
OLD_CHECK_KEYS = {"date", "would_pass", "logged", "symbol", "side", "trade_type",
                  "demoted_to_scalp", "entry", "stop", "target",
                  "stop_points", "target_points", "rr", "margin_usd", "limits_applied",
                  "rule_violations"}
OLD_TIMEFRAME_KEYS = {"data_root", "market", "default_symbol", "symbols_tracked",
                      "fetcher_enabled", "interval_seconds", "last_fetch",
                      "last_fetch_error", "symbols"}
PAST = "2026-09-01"       # Thu Ba, cua so NY da het tu lau


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


def run(coro):
    return asyncio.run(coro)


def clean_journal():
    shutil.rmtree(server.JOURNAL_DIR, ignore_errors=True)
    shutil.rmtree(server.ORB_STATE_DIR, ignore_errors=True)


class RegressionTest(unittest.TestCase):
    """TC-25: khong truyen tham so moi -> khong co khoa moi, hanh vi cu giu nguyen."""

    def setUp(self):
        clean_journal()

    def test_get_today_status_unchanged(self):
        out = run(server.get_today_status())
        self.assertEqual(set(out), OLD_TODAY_KEYS | ORB_TODAY_KEYS)
        self.assertEqual(out["counted_from"], "journal")
        self.assertIsNone(out["exchange"])

    def test_check_trade_unchanged(self):
        out = server.check_trade(side="long", entry=100000, stop=99800, target=100400,
                                 margin_usd=10)
        self.assertNotIn("orb_checks", out)
        self.assertNotIn("strategy", out)
        self.assertTrue(OLD_CHECK_KEYS <= set(out), OLD_CHECK_KEYS - set(out))
        self.assertEqual(set(out) - OLD_CHECK_KEYS, set())

    def test_log_trade_only_adds_strategy(self):
        out = server.log_trade(side="long", entry=100000, stop=99800, target=100400,
                               margin_usd=10)
        self.assertEqual(set(out), {"logged", "trade", "rule_violations",
                                    "rules_changed_today"})
        self.assertEqual(out["trade"]["strategy"], "OTHER")
        for key in ("variant", "session_id", "or_date", "or_high", "or_low"):
            self.assertNotIn(key, out["trade"])

    def test_list_timeframes_keeps_old_keys(self):
        out = server.list_timeframes()
        self.assertTrue(OLD_TIMEFRAME_KEYS <= set(out))
        self.assertEqual(set(out) - OLD_TIMEFRAME_KEYS,
                         {"on_demand_timeframes", "orb_m5_history"})

    def test_account_pnl_without_account_still_raises(self):
        with self.assertRaises(Exception):
            run(server.get_account_pnl())


class OrbTradeToolsTest(unittest.TestCase):
    def setUp(self):
        clean_journal()

    def test_orb_fields_rejected_for_other_strategy(self):
        with self.assertRaises(ValueError):
            server.log_trade(side="long", entry=100000, stop=99800, target=100400,
                             margin_usd=10, session_id="ny")
        with self.assertRaises(ValueError):
            server.log_trade(side="long", entry=100000, stop=99800, target=100400,
                             margin_usd=10, strategy="ORB")          # thieu session_id
        with self.assertRaises(ValueError):
            server.check_trade(side="long", entry=100000, stop=99800, target=100400,
                               margin_usd=10, strategy="SMC")

    def test_tc22_check_trade_orb_outside_window(self):
        out = server.check_trade(side="long", entry=100000, stop=99800, target=100400,
                                 margin_usd=10, strategy="ORB", session_id="ny",
                                 or_date=PAST)
        self.assertEqual(out["strategy"], "ORB")
        self.assertFalse(out["would_pass"])
        checks = out["orb_checks"]
        self.assertEqual((checks["session_id"], checks["or_date"]), ("ny", PAST))
        self.assertFalse(checks["in_window"])
        self.assertTrue(any("da het" in v for v in out["rule_violations"]))
        self.assertFalse(out["logged"])
        self.assertFalse((server.JOURNAL_DIR / f"{PAST}.json").exists())

    def test_tc28_log_trade_orb_then_limit(self):
        out = server.log_trade(side="long", entry=100000, stop=99800, target=100400,
                               margin_usd=10, strategy="ORB", session_id="NY",
                               variant="breakout", or_date=PAST, date=PAST)
        trade = out["trade"]
        self.assertEqual((trade["strategy"], trade["session_id"], trade["variant"],
                          trade["or_date"]), ("ORB", "ny", "breakout", PAST))
        self.assertEqual(out["orb_watch"]["stop_reason"], "taken")
        self.assertEqual(server.ORB_SVC.states.get("ny", PAST)["state"], "TAKEN")
        again = server.check_trade(side="long", entry=100000, stop=99800, target=100400,
                                   margin_usd=10, strategy="ORB", session_id="ny",
                                   or_date=PAST, date=PAST)
        self.assertEqual(again["orb_checks"]["limits"]["status"], "session_limit_reached")
        self.assertIn("vuot max_trades cua phien (1/1)", again["rule_violations"])

    def test_filtered_views(self):
        server.log_trade(side="long", entry=100000, stop=99800, target=100400,
                         margin_usd=10, strategy="ORB", session_id="ny", or_date=PAST,
                         date=PAST)
        server.log_trade(side="short", entry=100000, stop=100200, target=99600,
                         margin_usd=10, date=PAST)
        status = run(server.get_today_status(date=PAST, strategy="ORB"))
        self.assertEqual(status["filtered"]["trades"], 1)
        self.assertEqual(status["filtered"]["by_session"]["ny"]["trades"], 1)
        self.assertEqual(status["orb_skips"], [])
        # TM - #ORB-RULES - ORB Rule Set: quota chung khong dem lenh ORB (BR-ORB-06)
        self.assertEqual(status["trades_taken"], 1)
        self.assertEqual(status["orb_trades_taken"], 1)
        pnl = run(server.get_account_pnl(date=PAST, strategy="OTHER"))
        self.assertIn("exchange_error", pnl)                 # san tat nhung van co so nhat ky
        self.assertEqual(pnl["journal_filtered"]["trades"], 1)
        with self.assertRaises(ValueError):
            run(server.get_today_status(strategy="XYZ"))


class OrbToolsTest(unittest.TestCase):
    def test_list_sessions_and_config(self):
        out = server.list_orb_sessions()
        ids = [s["session_id"] for s in out["sessions"]]
        self.assertEqual(ids, ["ldn", "ny"])
        cfg = server.get_orb_config()
        self.assertEqual(cfg["symbol"], "BTCUSDT")
        self.assertNotIn("key", str(cfg).lower().split("exchange_limits")[0][-20:])

    def test_opening_range_without_data(self):
        out = server.get_opening_range(session_id="ny", date=PAST)
        rng = out["sessions"][0] if "sessions" in out else out
        self.assertEqual(rng["status"], "data_missing")

    def test_check_signal_rejects_future_and_bad_input(self):
        with self.assertRaises(ValueError):
            server.check_orb_signal(session_id="ny", as_of="2099-01-01T00:00:00Z")
        with self.assertRaises(ValueError):
            server.check_orb_signal(session_id="tokyo")

    def test_backtest_validates_dates(self):
        with self.assertRaises(ValueError):
            run(server.backtest_orb(from_date="2026-09-10", to_date="2026-09-01"))
        with self.assertRaises(ValueError):
            run(server.backtest_orb(from_date="2020-01-01", to_date="2026-01-01"))
        with self.assertRaises(ValueError):
            server.get_backtest_result("khong-co")


# TM - #ORB-RULES - ORB Rule Set

MARKET = {"symbol": "BTCUSDT", "atr_h1": 462.0, "price": 83500.0,
          "at_utc": "2026-09-29T14:00:00Z"}


def orb_history(since: int) -> list[dict]:
    return [h for h in server._rules_doc()["history"][since:] if h.get("scope") == "orb"]


def admin(action: str, scope: str = "defaults", changes: dict | None = None,
          reason: str = "BR ORB test") -> tuple[int, dict]:
    return server._orb_rules_action({"action": action, "scope": scope,
                                     "changes": changes or {}, "reason": reason})


class OrbRuleSetServerTest(unittest.TestCase):
    """BR "ORB Rule Set" o tang tool / trang admin (AC tang rule, plan, backtest:
    tests/test_orb_rules.py). Moi test chay tren rules.json, phien va news days moi;
    ATR H1 ~462 / gia 83,500 gia lap qua market_snapshot. Test chay bat ky gio nao
    nen lenh ORB thuong ngoai trade window - loi rule ORB doc o orb_rule_violations."""

    @classmethod
    def setUpClass(cls):
        server.STORE.symbols(server.DEFAULT_SYMBOL)     # seed BTC truoc khi them cap
        cls.added = [s for s in ("ETHUSDT", "XAUUSDT") if not server.STORE.has_symbol(s)]
        for sym in cls.added:
            server.STORE.add_symbol(sym)

    @classmethod
    def tearDownClass(cls):
        for sym in cls.added:
            server.STORE.remove_symbol(sym)
        cls.reset()

    @staticmethod
    def reset():
        clean_journal()
        for path in (server.RULES_FILE, server.ORB_SESSIONS_FILE, server.ORB_NEWS_FILE):
            path.unlink(missing_ok=True)

    def setUp(self):
        self.reset()
        self.day = server._today()
        svc = server.ORB_SVC
        svc.market_snapshot = lambda at=None: dict(MARKET)
        self.addCleanup(vars(svc).pop, "market_snapshot", None)
        # rule chung de doi chieu (BR muc 5): quota 5, daily stop -69, BTC TP toi thieu 900
        server.update_rules({"max_trades_per_day": 5, "daily_stop_loss": -69}, "BR ORB test")
        server.update_rules({"min_take_profit_points": 900}, "BR ORB test", symbol="BTCUSDT")

    def check(self, entry: float = 83800, stop: float = 83500, target: float = 84250,
              **kw) -> dict:
        return server.check_trade(side="long" if target > entry else "short", entry=entry,
                                  stop=stop, target=target, margin_usd=20, **kw)

    def check_orb(self, *args, **kw) -> dict:
        return self.check(*args, strategy="ORB", session_id="ny", **kw)

    def check_other_ok(self) -> dict:
        return self.check(stop=83550, target=84700)    # SL 250 / TP 900: dat rule chung

    def log_orb(self, session_id: str, pnl: float | None = None) -> None:
        out = server.log_trade(side="long", entry=83800, stop=83500, target=84250,
                               margin_usd=20, strategy="ORB", session_id=session_id,
                               or_date=self.day, date=self.day)
        if pnl is not None:
            server.close_trade(out["trade"]["id"], exit_price=83800, pnl=pnl, date=self.day)

    def test_ac01_ac02_same_numbers_two_rule_sets(self):
        out = self.check_orb()
        self.assertEqual(out["orb_rule_violations"], [])
        self.assertEqual(out["limits_applied"]["rule_set"], "orb")
        self.assertEqual(out["limits_applied"]["min_take_profit_points"], 200)
        self.assertNotIn("(900)", " ".join(out["rule_violations"]))
        # con lai chi la quy tac phien (trade window / du lieu) cua orb_checks
        self.assertEqual(out["rule_violations"], out["orb_checks"]["violations"])
        other = self.check()
        self.assertFalse(other["would_pass"])
        self.assertEqual(other["rule_violations"], ["TP 450.0 < min_take_profit_points (900)"])

    def test_ac03_ac04_ac06_orb_thresholds(self):
        self.assertIn("SL 400.0 > orb.max_stop_points (350)",
                      self.check_orb(stop=83400, target=84400)["orb_rule_violations"])
        self.assertEqual(self.check_orb(target=84200)["orb_rule_violations"],
                         ["R:R 1.33 < orb.min_rr (1.5)"])
        swing = self.check_orb(trade_type="swing")
        self.assertEqual((swing["orb_rule_violations"], swing["demoted_to_scalp"]), ([], False))
        self.assertEqual(swing["limits_applied"]["rule_set"], "orb")

    def test_ac05_symbol_without_orb_rules(self):
        out = self.check_orb(entry=2650, stop=2640, target=2670, symbol="XAUUSDT")
        self.assertFalse(out["would_pass"])
        for field in ("max_stop_points", "min_take_profit_points"):
            self.assertIn(f"chua dat rule ORB cho XAUUSDT.{field}", out["orb_rule_violations"])

    def test_ac07_orb_quota_is_separate(self):
        self.log_orb("ldn")
        self.log_orb("ny")
        self.assertIn(f"vuot orb.max_trades_per_day (2): da co 2 lenh ORB ngay {self.day}",
                      self.check_orb()["orb_rule_violations"])
        other = self.check_other_ok()
        self.assertTrue(other["would_pass"], other["rule_violations"])
        status = run(server.get_today_status())
        self.assertEqual((status["trades_taken"], status["trades_remaining"]), (0, 5))
        self.assertEqual((status["orb_trades_taken"], status["orb_trades_remaining"],
                          status["can_trade_orb"]), (2, 0, False))

    def test_ac08_orb_daily_stop_is_separate(self):
        self.log_orb("ny", pnl=-25.5)
        violations = self.check_orb()["orb_rule_violations"]
        self.assertTrue(any(v.startswith("da cham orb.daily_stop_loss") for v in violations),
                        violations)
        status = run(server.get_today_status())
        self.assertEqual((status["orb_daily_stop_hit"], status["can_trade_orb"],
                          status["daily_stop_hit"]), (True, False, False))
        self.assertTrue(self.check_other_ok()["would_pass"])

    def test_ac09_general_stop_does_not_block_orb(self):
        out = server.log_trade(side="short", entry=83800, stop=84050, target=82900,
                               margin_usd=20, date=self.day)
        server.close_trade(out["trade"]["id"], exit_price=84050, pnl=-69, date=self.day)
        other = self.check_other_ok()["rule_violations"]
        self.assertTrue(any(v.startswith("da cham daily_stop_loss") for v in other), other)
        self.assertEqual(self.check_orb()["orb_rule_violations"], [])
        status = run(server.get_today_status())
        self.assertEqual((status["daily_stop_hit"], status["can_trade_orb"]), (True, True))

    def test_ac11_ac18_infeasible_on_config_and_admin_page(self):
        server.update_rules({"min_take_profit_points": 900}, "AC-11", symbol="BTCUSDT", orb=True)
        out = server.update_rules({"max_or_atr_ratio": 1.2}, "AC-11", orb=True)
        warnings = server.get_orb_config()["warnings"]
        self.assertEqual(warnings, out["warnings"])
        self.assertTrue(any(w.startswith("rule_infeasible: TP toi da ~832 diem") and "(900)" in w
                            for w in warnings), warnings)
        self.assertTrue(any("OR toi da ~554 + buffer ~17" in w for w in warnings), warnings)
        page = server._orb_admin_page()
        self.assertIn("rule_infeasible: TP toi da ~832 diem", page)

    def test_ac12_admin_edit_min_rr(self):
        since = len(server._rules_doc()["history"])
        status, body = admin("save", "symbol:BTCUSDT", {"min_rr": 2}, reason="  ")
        self.assertEqual(status, 400)
        self.assertIn("reason", body["error"])
        status, body = admin("save", "symbol:BTCUSDT", {"min_rr": 2}, reason="siet R:R")
        self.assertEqual((status, body["updated"], body["history_rows"]), (200, True, 1))
        rules = server.get_rules(symbol="BTCUSDT")
        self.assertEqual(rules["orb_resolved"]["values"]["min_rr"], 2.0)
        rows = orb_history(since)
        self.assertEqual([(r["field"], r["from"], r["to"], r["source"], r["reason"], r["symbol"])
                          for r in rows], [("min_rr", 1.5, 2.0, "admin", "siet R:R", "BTCUSDT")])
        violations = self.check_orb()["orb_rule_violations"]
        self.assertTrue(any("< orb.min_rr (2" in v for v in violations), violations)

    def test_ac17_or_atr_ratio_by_session(self):
        since = len(server._rules_doc()["history"])
        for scope, changes in (("defaults", {"max_or_atr_ratio": 0.8}),
                               ("defaults", {"min_or_atr_ratio": 0.25}),
                               ("session:ny", {"max_or_atr_ratio": 0.9})):
            status, body = admin("save", scope, changes, reason="AC-17")
            self.assertEqual(status, 200, body)
        sessions = {s["session_id"]: s["rules"] for s in server.get_orb_config()["sessions"]}
        self.assertEqual(sessions["ldn"]["max_or_atr_ratio"], {"value": 0.8, "source": "defaults"})
        self.assertEqual(sessions["ny"]["max_or_atr_ratio"], {"value": 0.9, "source": "session"})
        for sid in ("ldn", "ny"):
            self.assertEqual(sessions[sid]["min_or_atr_ratio"],
                             {"value": 0.25, "source": "defaults"})
        self.assertEqual([(r["layer"], r["field"], r["source"]) for r in orb_history(since)],
                         [("defaults", "max_or_atr_ratio", "admin"),
                          ("defaults", "min_or_atr_ratio", "admin"),
                          ("session", "max_or_atr_ratio", "admin")])
        self.assertEqual(server.ORB_SESSIONS.get("ny")["overrides"], {"max_or_atr_ratio": 0.9})
        # rule khong theo phien thi khong dat o pham vi phien
        self.assertEqual(admin("save", "session:ny", {"min_rr": 2})[0], 400)
        self.assertEqual(admin("save", "session:ny", {"enabled": False})[0], 400)

    def test_ac19_eth_swing_regression(self):
        out = server.check_trade(symbol="ETHUSDT", side="long", trade_type="swing",
                                 entry=2486, stop=2462, target=2556, margin_usd=20)
        self.assertEqual((out["would_pass"], out["demoted_to_scalp"]), (False, True))
        self.assertNotIn("orb_checks", out)

    def test_ac20_tp_r_two_saves_clean(self):
        since = len(server._rules_doc()["history"])
        status, body = admin("save", "defaults", {"tp_r": 2.0}, reason="AC-20")
        self.assertEqual((status, body["warnings"]), (200, []))
        self.assertEqual(server.ORB_SVC.params(server.ORB_SESSIONS.get("ny"))["tp_r"], 2.0)
        self.assertEqual([(r["field"], r["source"]) for r in orb_history(since)],
                         [("tp_r", "admin")])

    def test_ac21_preview_shows_infeasible_before_save(self):
        since = len(server._rules_doc()["history"])
        status, body = admin("preview", "defaults", {"buffer_pct": 0.1}, reason="")
        self.assertEqual((status, body["updated"], body["dry_run"]), (200, False, True))
        self.assertTrue(any("OR toi da ~323 + buffer ~84" in w for w in body["warnings"]),
                        body["warnings"])
        self.assertEqual(server.get_rules()["orb"]["defaults"]["buffer_pct"], 0.02)
        self.assertEqual(orb_history(since), [])

    def test_ac23_news_day_from_admin(self):
        status, body = server._orb_rules_action({"action": "news_add", "date": self.day,
                                                 "note": "CPI"})
        self.assertEqual((status, body["news_days"]), (200, [{"date": self.day, "note": "CPI"}]))
        rng = server.get_opening_range(session_id="ny", date=self.day)
        rng = rng["sessions"][0] if "sessions" in rng else rng
        self.assertTrue(rng["news_day"])
        server._orb_rules_action({"action": "news_delete", "date": self.day})
        rng = server.get_opening_range(session_id="ny", date=self.day)
        rng = rng["sessions"][0] if "sessions" in rng else rng
        self.assertFalse(rng["news_day"])
        self.assertEqual(server._orb_rules_action({"action": "news_delete",
                                                   "date": self.day})[0], 400)

    def test_ac24_disable_from_admin(self):
        self.assertEqual(admin("save", "defaults", {"enabled": False}, reason="AC-24")[0], 200)
        sig = server.check_orb_signal(session_id="ny")["sessions"][0]
        self.assertEqual((sig["data_status"], sig["blocked_by"], sig["plan"]),
                         ("orb_disabled", ["orb_disabled"], None))
        self.assertIn("ORB dang tat (orb.enabled = false)",
                      self.check_orb()["orb_rule_violations"])
        self.assertFalse(run(server.get_today_status())["can_trade_orb"])
        cfg = server.get_orb_config()
        self.assertEqual((cfg["enabled"], cfg["module_enabled"]), (False, True))
        admin("save", "defaults", {"enabled": True}, reason="AC-24 bat lai")
        self.assertEqual(self.check_orb()["orb_rule_violations"], [])

    def test_ac25_deprecated_yaml_ignored(self):
        server.ORB_CFG["risk"] = {"max_leverage": 10, "risk_usd": 1}
        self.addCleanup(server.ORB_CFG.pop, "risk", None)
        warnings = server.get_orb_config()["warnings"]
        self.assertTrue(any("orb.risk.max_leverage" in w for w in warnings), warnings)
        params = server.ORB_SVC.params()
        self.assertEqual(params["leverage"], 100)
        sizing, _warnings = server.orb.size_position(83500, 300, params)
        self.assertEqual(sizing["qty"], 0.023)

    def test_backtest_rules_override_validated(self):
        with self.assertRaises(ValueError):
            run(server.backtest_orb(from_date="2026-09-01", to_date="2026-09-10",
                                    rules_override={"min_or_atr_ratio": 0.9}))
        with self.assertRaises(ValueError):
            run(server.backtest_orb(from_date="2026-09-01", to_date="2026-09-10",
                                    rules_override={"khong_co": 1}))


if __name__ == "__main__":
    unittest.main()
