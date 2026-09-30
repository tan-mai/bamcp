"""Trang quan ly phien (admin_orb) va backtest (orb_backtest) tren du lieu tong hop.
TC-11 (loi tung o), TC-23 (SL va TP cung nen -> tinh SL), tong hop in/out-of-sample,
by_session, CSV/JSON cua BacktestManager, thoi gian chay 2 nam."""

# TM - #ORB - ORB Enhancement

from __future__ import annotations

import csv
import json
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from orb_support import (ATR_BAR_HALF, INSIDE_HALF, M5_MS, M15_MS, H1_MS, OR_HALF, P,
                         FakeMarket, Clock, Harness, default_sessions, ms, orb_cfg,
                         scaled_rules)

import admin_orb
import orb_backtest

NY_0928 = ms("2026-09-28 13:30")
NY_0929 = ms("2026-09-29 13:30")
NY_0930 = ms("2026-09-30 13:30")
AFTER = ms("2026-10-02 00:00")


class AdminHandleTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.h.clock.now = ms("2026-09-29 06:00")

    def handle(self, payload):
        return admin_orb.handle(payload, sessions=self.h.sessions, now_ms=self.h.clock.now,
                                params_for=self.h.svc.params)

    def test_preview_has_window_times(self):
        status, body = self.handle({"action": "preview", "original_id": "ny",
                                    "session": self.h.session("ny")})
        self.assertEqual(status, 200, body)
        first = body["opens"][0]
        self.assertEqual(first["open_utc"], "2026-09-29T13:30:00Z")
        self.assertEqual(first["open_vn"], "2026-09-29T20:30:00+07:00")
        self.assertEqual(first["utc_offset"], "-04:00")
        self.assertEqual(first["window_end_utc"], "2026-09-29T16:45:00Z")

    def test_tc11_errors_per_field(self):
        bad = {"session_id": "Bad Id!", "name": "", "timezone": "Mars/Base",
               "open_time": "25:00", "trade_days": [], "holiday_calendar": "VN",
               "trade_window_minutes": 20, "max_trades": 0,
               "overrides": {"tp_r": "abc"}}
        status, body = self.handle({"action": "save", "session": bad})
        self.assertEqual(status, 400)
        self.assertEqual(set(body["errors"]),
                         {"session_id", "name", "timezone", "open_time", "trade_days",
                          "holiday_calendar", "trade_window_minutes", "max_trades",
                          "overrides"})
        self.assertEqual(self.h.sessions.ids(), ["ldn", "ny"])

    def test_duplicate_and_rename_rejected(self):
        status, body = self.handle({"action": "save",
                                    "session": {**self.h.session("ny"), "name": "NY 2"}})
        self.assertEqual((status, set(body["errors"])), (400, {"session_id"}))
        status, body = self.handle({"action": "save", "original_id": "ny",
                                    "session": {**self.h.session("ny"), "session_id": "nyse"}})
        self.assertEqual((status, set(body["errors"])), (400, {"session_id"}))
        status, body = self.handle({"action": "save", "original_id": "tokyo",
                                    "session": {**self.h.session("ny"), "session_id": "tokyo"}})
        self.assertEqual(status, 400)

    def test_override_whitelist(self):
        status, body = self.handle({"action": "save", "original_id": "ny", "session": {
            **self.h.session("ny"), "overrides": {"risk_usd": 50}}})
        self.assertEqual(status, 400)
        self.assertIn("overrides", body["errors"])
        # TM - #ORB-RULES - ORB Rule Set: tp_r la rule ORB -> form phien khong sua duoc
        status, body = self.handle({"action": "save", "original_id": "ny", "session": {
            **self.h.session("ny"), "overrides": {"tp_r": "2", "entry_mode": "retest"}}})
        self.assertEqual(status, 400)
        self.assertIn("Rule ORB", body["errors"]["overrides"])
        status, body = self.handle({"action": "save", "original_id": "ny", "session": {
            **self.h.session("ny"), "overrides": {"entry_mode": "retest"}}})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.h.session("ny")["overrides"], {"entry_mode": "retest"})
        params = self.h.svc.params(self.h.session("ny"))
        self.assertEqual((params["tp_r"], params["entry_mode"]), (1.5, "retest"))

    def test_session_form_keeps_rule_overrides(self):
        """Rule ORB cua phien (dat qua muc Rule ORB) khong mat khi luu form phien."""
        self.h.sessions.save({**self.h.session("ny"), "overrides": {"tp_r": 2.0}},
                             original_id="ny")
        form = {**self.h.session("ny"), "overrides": {"entry_mode": "retest"}}
        status, body = self.handle({"action": "save", "original_id": "ny", "session": form})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.h.session("ny")["overrides"], {"entry_mode": "retest", "tp_r": 2.0})
        params = self.h.svc.params(self.h.session("ny"))
        self.assertEqual((params["tp_r"], params["rule_sources"]["tp_r"]), (2.0, "session"))
        self.assertEqual(self.h.svc.params(self.h.session("ldn"))["tp_r"], 1.5)
        # gui lai dung gia tri dang luu thi khong coi la sua rule
        status, _ = self.handle({"action": "save", "original_id": "ny", "session": {
            **form, "overrides": {"entry_mode": "retest", "tp_r": 2}}})
        self.assertEqual(status, 200)

    def test_bad_action_and_unknown_session(self):
        self.assertEqual(self.handle({"action": "boom", "session_id": "ny"})[0], 400)
        self.assertEqual(self.handle({"action": "enable"})[0], 400)
        self.assertEqual(self.handle({"action": "disable", "session_id": "tokyo"})[0], 400)
        self.assertEqual(self.handle({"action": "save", "session": "x"})[0], 400)

    def test_history_records_changes(self):
        self.handle({"action": "disable", "session_id": "ldn"})
        self.handle({"action": "delete", "session_id": "ldn", "confirm": "ldn"})
        actions = [(r["action"], r.get("session_id")) for r in self.h.sessions.history()]
        self.assertIn(("update", "ldn"), actions)
        self.assertIn(("delete", "ldn"), actions)
        changed = next(r for r in self.h.sessions.history() if r["action"] == "update")
        self.assertEqual(changed["changes"], {"enabled": {"from": True, "to": False}})

    def test_render_escapes_user_text(self):
        self.h.sessions.save({**self.h.session("ny"), "name": "<script>alert(1)</script>"},
                             original_id="ny")
        page = admin_orb.render(rows=self.h.svc.list_sessions(True),
                                sessions=self.h.sessions.all(),
                                history=self.h.sessions.history(), action_path="/x/orb/action",
                                admin_path="/x", store_path="/data/orb/sessions.json",
                                global_params=self.h.svc.params(None), setup_required=False,
                                now_ms=self.h.clock.now)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("__ROWS__", page)
        self.assertIn('data-sid="ldn"', page)
        self.assertIn("America/New_York", page)


def build_bars(*, skip_or_on: int | None = None) -> list[dict]:
    """Nen phang 18/9 -> 2/10; NY 28/9 thua (SL+TP cung nen), NY 29/9 thang TP."""
    m = FakeMarket(Clock())
    m.fill(ms("2026-09-18 00:00"), ms("2026-10-02 00:00"), ATR_BAR_HALF)
    for open_ms in (NY_0928, NY_0929, NY_0930):
        m.fill(open_ms, open_ms + M15_MS, OR_HALF)
        m.fill(open_ms + M15_MS, open_ms + M15_MS + 3 * H1_MS, INSIDE_HALF)
    # 28/9: pha len 13:50, nen vao lenh 13:55 cham ca SL 99970 va TP 100295
    m.put(NY_0928 + 20 * 60_000, P + 10, P + 120, P + 5, P + 100)
    m.put(NY_0928 + 25 * 60_000, P + 100, P + 400, P - 100, P)
    # 29/9: pha len 13:50, nen 13:55 chi cham TP
    m.put(NY_0929 + 20 * 60_000, P + 10, P + 120, P + 5, P + 100)
    m.put(NY_0929 + 25 * 60_000, P + 100, P + 300, P + 50, P + 250)
    if skip_or_on is not None:
        for t in range(skip_or_on, skip_or_on + M15_MS, M5_MS):
            m.bars.pop(t)
    return [m.bars[k] for k in sorted(m.bars)]


def sessions() -> list[dict]:
    return list(default_sessions().values())


class SimulateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = orb_backtest.simulate(
            build_bars(skip_or_on=NY_0930), sessions(), orb_cfg(),
            from_day=date(2026, 9, 28), to_day=date(2026, 9, 30),
            split_day=date(2026, 9, 29), initial_equity=100, now_ms=AFTER,
            rules=scaled_rules())
        cls.trades = [t for t in cls.result["trades"] if t["session_id"] == "ny"]

    def test_tc23_sl_and_tp_same_bar_is_loss(self):
        loss = self.trades[0]
        self.assertEqual(loss["or_date"], "2026-09-28")
        self.assertEqual((loss["entry_ref"], loss["sl"], loss["tp"]), (P + 100, P - 30, P + 295))
        self.assertEqual(loss["exit_reason"], "sl")
        self.assertTrue(loss["sl_tp_same_bar"])
        self.assertEqual(loss["exit_ref"], P - 30)
        self.assertLess(loss["pnl_usd"], 0)
        self.assertLess(loss["r_ideal"], -1)            # phi + truot gia lam lo hon 1R
        self.assertEqual(self.result["summary"]["sl_tp_same_bar"], 1)
        self.assertIn("SL va TP cung cham trong mot nen -> tinh SL",
                      self.result["assumptions"])

    def test_tp_trade_and_sizing(self):
        win = self.trades[1]
        self.assertEqual((win["or_date"], win["exit_reason"]), ("2026-09-29", "tp"))
        self.assertGreater(win["pnl_usd"], 0)
        # TM - #ORB-RULES - ORB Rule Set: 20 USD x100 / 100100 -> 0.019 (BR-ORB-15)
        self.assertEqual(win["qty"], 0.019)
        self.assertFalse(win["margin_capped"])
        self.assertTrue(win["executable"])
        self.assertLessEqual(win["margin"], 20)
        self.assertAlmostEqual(win["pnl_usd"],
                               win["qty"] * (win["exit_fill"] - win["entry_fill"])
                               - win["fees_usd"], places=3)

    def test_summary_split_and_by_session(self):
        s = self.result["summary"]
        self.assertEqual((s["trades"], s["wins"], s["losses"]), (2, 1, 1))
        self.assertEqual(s["exits"], {"sl": 1, "tp": 1})
        self.assertEqual((s["in_sample"]["trades"], s["in_sample"]["losses"]), (1, 1))
        self.assertEqual((s["out_of_sample"]["trades"], s["out_of_sample"]["wins"]), (1, 1))
        self.assertEqual(s["by_session"]["ny"]["trades"], 2)
        self.assertEqual(s["by_session"]["ldn"]["trades"], 0)
        self.assertEqual(s["ideal_sizing"]["all"]["signals"], 2)
        self.assertAlmostEqual(s["final_equity"], 100 + s["net_pnl_usd"], places=4)
        self.assertEqual([t["sample"] for t in self.trades], ["in_sample", "out_of_sample"])
        self.assertEqual(self.trades[-1]["equity_after"], round(s["final_equity"], 4))

    def test_data_gap_reported(self):
        self.assertEqual(self.result["data_gaps"],
                         [{"session_id": "ny", "date": "2026-09-30", "what": "or"}])
        self.assertEqual(self.result["summary"]["counters"]["data_missing"], 1)

    def test_unfinished_session_not_traded(self):
        res = orb_backtest.simulate(build_bars(), sessions(), orb_cfg(),
                                    from_day=date(2026, 9, 29), to_day=date(2026, 9, 29),
                                    now_ms=ms("2026-09-29 15:00"))
        self.assertEqual(res["summary"]["trades"], 0)
        self.assertEqual(res["summary"]["counters"]["not_finished"], 1)

    def test_filters_and_overrides(self):
        # min ratio 0.7 -> OR NY (0.6) bi loc too_narrow, khong co lenh
        res = orb_backtest.simulate(build_bars(), sessions(), orb_cfg(),
                                    from_day=date(2026, 9, 28), to_day=date(2026, 9, 29),
                                    rules_override={"min_or_atr_ratio": 0.65,
                                                    "max_or_atr_ratio": 0.9},
                                    now_ms=AFTER, rules=scaled_rules())
        self.assertEqual(res["summary"]["trades"], 0)
        self.assertEqual(res["summary"]["counters"]["filtered_too_narrow"], 2)
        # ngay tin -> bo qua
        res = orb_backtest.simulate(build_bars(), sessions(), orb_cfg(),
                                    from_day=date(2026, 9, 28), to_day=date(2026, 9, 29),
                                    news_days=["2026-09-29"], now_ms=AFTER,
                                    rules=scaled_rules())
        self.assertEqual(res["summary"]["trades"], 1)
        self.assertGreaterEqual(res["summary"]["counters"]["filtered_news_day"], 1)
        with self.assertRaises(ValueError):
            orb_backtest.simulate(build_bars(), sessions(), orb_cfg(),
                                  from_day=date(2026, 9, 29), to_day=date(2026, 9, 28))

    def test_two_years_under_budget(self):
        """2 nam M5 (~210 nghin nen, 2 phien) phai xong duoi 5 phut - thuc te vai giay."""
        m = FakeMarket(Clock())
        start, end = ms("2024-09-20 00:00"), ms("2026-09-29 00:00")
        step = 0
        for t in range(start, end, M5_MS):
            wave = ((step * 7919) % 400) - 200         # gia dao dong tat dinh
            step += 1
            price = P + wave
            m.bars[t] = {"open_time": t, "open": price - 20, "high": price + 60,
                         "low": price - 60, "close": price + 20, "volume": 10.0,
                         "close_time": t + M5_MS - 1}
        bars = [m.bars[k] for k in sorted(m.bars)]
        began = time.perf_counter()
        res = orb_backtest.simulate(bars, sessions(), orb_cfg(), from_day=date(2024, 9, 30),
                                    to_day=date(2026, 9, 28), now_ms=end)
        elapsed = time.perf_counter() - began
        self.assertLess(elapsed, 300)
        self.assertGreater(res["summary"]["counters"]["session_days"], 900)
        self.assertGreater(res["summary"]["trades"], 100)


class FakeHistory:
    def __init__(self, bars):
        self.bars = bars
        self.calls = []

    def ensure(self, symbol, start, end, today):
        self.calls.append((symbol, start, end, today))
        return {"from": start.isoformat(), "to": end.isoformat(), "downloaded": [],
                "missing_remote": [], "errors": []}

    def load(self, symbol, start_ms, end_ms):
        return [b for b in self.bars if start_ms <= b["open_time"] < end_ms]


class BacktestManagerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.events = []
        self.mgr = orb_backtest.BacktestManager(
            self.root / "backtests", symbol="BTCUSDT", history=FakeHistory(build_bars()),
            history_years=2, load_live_m5=lambda symbol: [],
            log=lambda kind, **kw: self.events.append((kind, kw)), now_fn=lambda: AFTER)

    def request(self, **extra):
        return {"from_day": date(2026, 9, 28), "to_day": date(2026, 9, 29),
                "split_day": None, "overrides": None, "initial_equity": 100.0, **extra}

    def test_execute_writes_result_and_csv(self):
        out = self.mgr.execute("r1", self.request(), sessions(), orb_cfg(), [],
                               rules=scaled_rules())
        self.assertEqual(out["status"], "done", out.get("error"))
        self.assertEqual(out["summary"]["trades"], 2)
        self.assertNotIn("trades", out)                   # trade chi nam trong CSV
        folder = self.root / "backtests" / "r1"
        with (folder / "trades.csv").open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(list(rows[0]), list(orb_backtest.TRADE_COLUMNS))
        self.assertEqual([r["exit_reason"] for r in rows if r["session_id"] == "ny"],
                         ["sl", "tp"])
        with (folder / "equity.csv").open(encoding="utf-8") as fh:
            equity = list(csv.reader(fh))
        self.assertEqual(equity[1], ["", "0", "", "", "100.0"])
        self.assertEqual(len(equity), 4)                  # header + dau ky + 2 lenh
        saved = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["request"]["from_day"], "2026-09-28")
        self.assertEqual(self.mgr.result("r1")["summary"]["trades"], 2)
        self.assertEqual(self.mgr.runs(), ["r1"])
        self.assertEqual(self.events[-1][1]["status"], "done")

    def test_error_is_saved_not_raised(self):
        out = self.mgr.execute("r2", self.request(to_day=date(2026, 9, 1)), sessions(),
                               orb_cfg(), [])
        self.assertEqual(out["status"], "error")
        self.assertEqual(self.mgr.result("r2")["status"], "error")

    def test_result_rejects_path_tricks(self):
        for bad in ("", "../x", "a/b", "a\\b"):
            with self.assertRaises(ValueError):
                self.mgr.result(bad)
        with self.assertRaises(ValueError):
            self.mgr.result("nope")


if __name__ == "__main__":
    unittest.main()
