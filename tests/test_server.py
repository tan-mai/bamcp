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
        self.assertEqual(set(out), OLD_TODAY_KEYS)
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
        self.assertEqual(status["trades_taken"], 2)          # quota van tinh chung
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


if __name__ == "__main__":
    unittest.main()
