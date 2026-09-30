"""Runtime ORB (orb_runtime.py) voi dong ho + thi truong gia: scheduler hai giai
doan, watch M5, skip/taken, admin doi phien, restart, loi co lap theo phien.
TC-07..10, 12..14, 16, 17, 21, 22, 26..31 cua requirement."""

# TM - #ORB - ORB Enhancement

from __future__ import annotations

import json
import unittest
from datetime import date

from orb_support import (ATR_BAR_HALF, M5_MS, M15_MS, H1_MS, OR_HALF, P, Harness, ms)

import admin_orb

DAY = "2026-09-29"                       # Thu Ba, My dang EDT, Anh dang BST
D = date.fromisoformat(DAY)
NY_OPEN = ms("2026-09-29 13:30")
NY_OR_END = ms("2026-09-29 13:45")
NY_WINDOW_END = ms("2026-09-29 16:45")
LDN_OPEN = ms("2026-09-29 07:00")
LDN_OR_END = ms("2026-09-29 07:15")
LDN_WINDOW_END = ms("2026-09-29 10:15")
DELAY = 20_000


def at(hhmmss: str) -> int:
    return ms(f"{DAY} {hhmmss}")


class Base(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.h.clock.now = at("06:00")

    def ny_only(self, or_half: float = OR_HALF) -> None:
        self.h.sessions.set_enabled("ldn", False)
        self.h.market.day(NY_OPEN, or_half=or_half)

    def both(self) -> None:
        m = self.h.market
        m.fill(LDN_OPEN - 10 * 24 * H1_MS, NY_OPEN + 12 * H1_MS, ATR_BAR_HALF)
        m.fill(LDN_OPEN, LDN_OPEN + M15_MS, OR_HALF)
        m.fill(NY_OPEN, NY_OPEN + M15_MS, OR_HALF)

    def ny(self):
        return self.h.session("ny")

    def state(self, sid: str = "ny"):
        return self.h.state(sid, DAY)

    def watch(self, sid: str = "ny"):
        return (self.state(sid) or {}).get("watch") or {}


class OpeningRangeTest(Base):
    def test_tc14_forming_before_or_closes(self):
        self.ny_only()
        self.h.market.mark_fetched(at("13:40"))
        rng = self.h.svc.opening_range(self.ny(), D, at("13:40"))
        self.assertEqual(rng["status"], "forming")
        self.assertIsNone(rng["high"])
        self.assertIsNone(rng["low"])
        self.assertEqual(self.h.svc.signal(self.ny(), D, at("13:40"))["state"], "FORMING")
        self.assertEqual(self.h.svc.signal(self.ny(), D, at("13:00"))["state"], "WAITING_OPEN")

    def test_or_set_with_atr_and_filter(self):
        self.ny_only()
        self.h.market.mark_fetched(at("13:46"))
        rng = self.h.svc.opening_range(self.ny(), D, at("13:46"))
        self.assertEqual(rng["status"], "set")
        self.assertEqual((rng["high"], rng["low"], rng["size"]), (P + 30, P - 30, 60))
        self.assertEqual((rng["atr"], rng["size_atr_ratio"], rng["range_flag"]), (100, 0.6, "ok"))
        self.assertFalse(rng["filtered"])
        self.assertEqual(rng["open_utc"], "2026-09-29T13:30:00Z")
        self.assertEqual(rng["open_vn"], "2026-09-29T20:30:00+07:00")

    def test_data_missing_without_bars(self):
        self.h.sessions.set_enabled("ldn", False)
        rng = self.h.svc.opening_range(self.ny(), D, at("13:46"))
        self.assertEqual(rng["status"], "data_missing")
        self.assertTrue(rng["missing"])

    def test_news_day_filter(self):
        self.ny_only()
        (self.h.root / "news_days.json").write_text(json.dumps([DAY]), encoding="utf-8")
        self.h.market.mark_fetched(at("13:46"))
        rng = self.h.svc.opening_range(self.ny(), D, at("13:46"))
        self.assertTrue(rng["news_day"])
        self.assertEqual(rng["filter_reason"], "news_day")

    def test_tc07_no_session_on_weekend(self):
        self.ny_only()
        sat = date(2026, 10, 3)
        now = ms("2026-10-03 14:00")
        self.h.clock.now = now
        self.assertEqual(self.h.svc.opening_range(self.ny(), sat, now)["status"], "no_session")
        sig = self.h.svc.signal(self.ny(), sat, now)
        self.assertEqual(sig["data_status"], "no_session")
        self.assertEqual(sig["next_open_utc"], "2026-10-05T13:30:00Z")
        # scheduler khong dung job cho Thu Bay / Chu nhat
        for now in (ms("2026-10-03 14:00"), ms("2026-10-04 14:00")):
            days = {j["day"].weekday() for j in self.h.sched.jobs(now)}
            self.assertFalse(days & {5, 6})


class WatchFlowTest(Base):
    def test_tc26_range_ok_starts_watch_without_m5(self):
        self.ny_only()
        due = self.h.run_due(NY_OR_END + DELAY)
        self.assertEqual([(j["kind"], j["session"]["session_id"]) for j in due],
                         [("phase1", "ny")])
        st = self.state()
        self.assertEqual(st["state"], "RANGE_SET")
        self.assertEqual(st["phase1"]["status"], "done")
        self.assertTrue(st["watch"]["active"])
        self.assertEqual(st["watch"]["next_tick_ms"], NY_OR_END + M5_MS + DELAY)
        self.assertEqual({(r["tf"], r["source"]) for r in self.h.market.requests},
                         {("15m", "orb_phase1:ny"), ("1h", "orb_phase1:ny")})
        self.assertEqual(self.h.market.m5_requests(), [])
        row = self.h.svc.list_sessions()[0]
        self.assertEqual((row["session_id"], row["job_status"]), ("ny", "scheduled"))
        self.assertTrue(row["watch"]["active"])

    def test_tc27_filtered_range_never_pulls_m5(self):
        self.ny_only(or_half=5)                  # OR 10 / ATR 100 = 0.1
        self.h.drive(at("13:00"), at("17:30"))
        st = self.state()
        self.assertEqual(st["state"], "FILTERED")
        self.assertFalse(st["watch"]["active"])
        self.assertEqual(st["watch"]["stop_reason"], "filtered")
        self.assertEqual(st["phase1"]["result"]["range_flag"], "too_narrow")
        self.assertEqual(self.h.market.m5_requests(), [])
        self.assertEqual(self.h.svc.signal(self.ny(), D, at("14:00"))["state"], "FILTERED")

    def test_tc20_too_wide_is_filtered(self):
        self.ny_only(or_half=80)                 # OR 160 / ATR 100 = 1.6
        self.h.run_due(NY_OR_END + DELAY)
        self.assertEqual(self.state()["state"], "FILTERED")
        self.assertEqual(self.state()["phase1"]["result"]["range_flag"], "too_wide")

    def test_tc30_and_tc21_window_expires(self):
        self.ny_only()
        self.h.drive(at("13:00"), at("17:30"))
        st = self.state()
        self.assertEqual(st["state"], "EXPIRED")
        self.assertFalse(st["watch"]["active"])
        self.assertEqual(st["watch"]["stop_reason"], "window_expired")
        self.assertEqual(st["watch"]["ticks"], 36)              # 180 phut / 5
        self.assertEqual(len(self.h.market.m5_requests()), 36)
        self.assertEqual(max(r["at"] for r in self.h.market.m5_requests()),
                         NY_WINDOW_END + DELAY)
        sig = self.h.svc.signal(self.ny(), D, at("17:00"))
        self.assertEqual(sig["state"], "EXPIRED")
        self.assertEqual(sig["last_signal_state"], "RANGE_SET")
        self.assertIsNone(sig["plan"])

    def test_tc15_wick_only_stays_range_set(self):
        self.ny_only()
        self.h.market.put(at("13:50"), P, P + 80, P - 5, P + 10)
        self.h.drive(at("13:45"), at("13:56"))
        self.assertEqual(self.state()["state"], "RANGE_SET")
        self.assertIsNone(self.h.svc.signal(self.ny(), D, at("13:56"))["plan"])

    def test_tc16_breakout_long_has_plan(self):
        self.ny_only()
        self.h.market.put(at("13:50"), P + 10, P + 120, P + 5, P + 100)
        self.h.drive(at("13:45"), at("13:56"))
        self.assertEqual(self.state()["state"], "BREAKOUT_LONG")
        self.assertTrue(self.watch()["active"])            # van theo doi toi khi taken/skip/het gio
        sig = self.h.svc.signal(self.ny(), D, at("13:56"))
        self.assertEqual((sig["state"], sig["direction"], sig["variant"]),
                         ("BREAKOUT_LONG", "long", "breakout"))
        plan = sig["plan"]
        self.assertEqual((plan["entry"], plan["sl"], plan["tp"]), (P + 100, P - 30, P + 295))
        # TM - #ORB-RULES - ORB Rule Set: 20 USD x100 / 100100 -> 0.019 (BR-ORB-15)
        self.assertEqual(plan["qty"], 0.019)
        self.assertEqual(plan["rule_check"]["would_pass"], True)
        self.assertEqual(sig["trigger_candle"]["open_utc"], "2026-09-29T13:50:00Z")
        self.assertIn("bias_missing", " ".join(sig["warnings"]))
        with_risk = self.h.svc.signal(self.ny(), D, at("13:56"), risk_usd=0.5)
        self.assertEqual(with_risk["plan"]["qty"], 0.019)
        self.assertIn("risk_usd_ignored", " ".join(with_risk["warnings"]))

    def test_bias_against_blocks_plan(self):
        self.ny_only()
        self.h.bias = {"direction": "short", "date": DAY}
        self.h.market.put(at("13:50"), P + 10, P + 120, P + 5, P + 100)
        self.h.drive(at("13:45"), at("13:56"))
        sig = self.h.svc.signal(self.ny(), D, at("13:56"))
        self.assertEqual(sig["blocked_by"], ["bias_against"])
        self.assertIsNone(sig["plan"])
        self.h.bias = {"direction": "long", "date": DAY}
        sig = self.h.svc.signal(self.ny(), D, at("13:56"))
        self.assertEqual(sig["filters"]["bias"], "pass")
        self.assertIsNotNone(sig["plan"])

    def test_tc17_failed_breakout_reversal_only_when_allowed(self):
        self.ny_only()
        self.h.market.put(at("13:50"), P + 10, P + 150, P + 5, P + 100)
        self.h.market.put(at("13:55"), P + 100, P + 110, P - 10, P)
        self.h.drive(at("13:45"), at("14:01"))
        self.assertEqual(self.state()["state"], "FAILED_BREAKOUT_LONG")
        sig = self.h.svc.signal(self.ny(), D, at("14:01"))
        self.assertEqual(sig["state"], "FAILED_BREAKOUT_LONG")
        self.assertIsNone(sig["plan"])
        self.assertIn("allow_reversal", sig["note"])

        self.h.sessions.save({**self.ny(), "overrides": {"allow_reversal": True}},
                             original_id="ny")
        sig = self.h.svc.signal(self.ny(), D, at("14:01"))
        plan = sig["plan"]
        self.assertEqual((plan["direction"], plan["variant"]), ("short", "reversal"))
        self.assertEqual((plan["entry"], plan["sl"]), (P, P + 150))
        self.assertEqual(self.state()["phase1"]["status"], "done")   # sua override khong reset job

    def test_stale_m5_warning(self):
        self.ny_only()
        self.h.drive(at("13:45"), at("13:51"))
        self.h.clock.now = at("14:05")
        sig = self.h.svc.signal(self.ny(), D, at("14:05"))
        self.assertTrue(any(w.startswith("stale_m5") for w in sig["warnings"]))
        self.assertEqual(sig["data_status"], "data_missing")

    def test_tc28_taken_stops_watch(self):
        self.ny_only()
        self.h.market.put(at("13:50"), P + 10, P + 120, P + 5, P + 100)
        self.h.drive(at("13:45"), at("13:56"))
        self.h.clock.now = at("13:57")
        view = self.h.svc.mark_taken("ny", DAY, {"id": 1, "side": "long",
                                                 "_journal_date": DAY})
        self.assertFalse(view["active"])
        self.assertEqual(view["stop_reason"], "taken")
        self.assertEqual(self.state()["state"], "TAKEN")
        before = len(self.h.market.m5_requests())
        self.h.drive(at("13:58"), at("17:30"))
        self.assertEqual(len(self.h.market.m5_requests()), before)
        self.assertEqual(self.state()["watch"]["stop_reason"], "taken")
        self.assertEqual(self.h.svc.signal(self.ny(), D, at("14:30"))["state"], "TAKEN")

    def test_tc29_skip_stops_watch_and_journals(self):
        self.ny_only()
        self.h.drive(at("13:45"), at("14:01"))
        self.h.clock.now = at("14:02")
        with self.assertRaises(ValueError):
            self.h.svc.skip("ny", "  ")
        out = self.h.svc.skip("ny", "CPI 19:30 VN, dung ngoai")
        self.assertTrue(out["skipped"])
        self.assertEqual(out["state"], "SKIPPED")
        self.assertEqual(out["watch"]["stop_reason"], "skipped")
        self.assertEqual(out["journal"]["variant"], "skipped")
        self.assertEqual(out["journal"]["reason"], "CPI 19:30 VN, dung ngoai")
        rows = json.loads((self.h.root / "journal" / "orb_skips" / f"{DAY}.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual([(r["strategy"], r["variant"], r["session_id"]) for r in rows],
                         [("ORB", "skipped", "ny")])
        before = len(self.h.market.m5_requests())
        self.h.drive(at("14:03"), at("17:30"))
        self.assertEqual(len(self.h.market.m5_requests()), before)
        self.assertEqual(self.state()["state"], "SKIPPED")
        again = self.h.svc.skip("ny", "lan hai")
        self.assertFalse(again["skipped"])
        self.assertEqual(len(json.loads((self.h.root / "journal" / "orb_skips" /
                                         f"{DAY}.json").read_text(encoding="utf-8"))), 1)

    def test_catch_up_after_window_does_not_pull_m5(self):
        self.ny_only()
        self.h.run_due(at("17:10"))                      # service vua bat lai sau phien
        st = self.state()
        self.assertEqual(st["state"], "EXPIRED")
        self.assertEqual(st["watch"]["stop_reason"], "window_expired")
        self.assertTrue(st["phase1"]["catch_up"])
        self.assertEqual(self.h.market.m5_requests(), [])

    def test_tc31_m5_requests_only_inside_watch_windows(self):
        self.both()
        self.h.drive(at("06:50"), at("17:30"))
        windows = {"ldn": (LDN_OR_END + M5_MS + DELAY, LDN_WINDOW_END + DELAY),
                   "ny": (NY_OR_END + M5_MS + DELAY, NY_WINDOW_END + DELAY)}
        m5 = self.h.market.m5_requests()
        self.assertTrue(m5)
        for req in m5:
            sid = req["source"].split(":", 1)[1]
            self.assertTrue(req["source"].startswith("orb_watch:"), req)
            lo, hi = windows[sid]
            self.assertTrue(lo <= req["at"] <= hi, req)
        for sid in ("ldn", "ny"):
            self.assertEqual(len(self.h.market.m5_requests(f"orb_watch:{sid}")), 36)
            self.assertEqual(self.watch(sid)["stop_reason"], "window_expired")
            self.assertEqual(self.watch(sid)["m5_requests"], 36)
        # log su kien co du m5_request? (fetch gia khong ghi) - kiem tra log watch
        events = self.h.log.read(DAY, "watch")
        actions = [(e["session_id"], e["action"]) for e in events]
        self.assertEqual(actions.count(("ldn", "start")), 1)
        self.assertEqual(actions.count(("ny", "start")), 1)


class AdminChangeTest(Base):
    TOKYO = {"session_id": "tokyo", "name": "Tokyo", "timezone": "Asia/Tokyo",
             "open_time": "09:00", "trade_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
             "holiday_calendar": "none"}

    def handle(self, payload):
        return admin_orb.handle(payload, sessions=self.h.sessions, now_ms=self.h.clock.now,
                                params_for=self.h.svc.params)

    def test_tc08_add_session_is_scheduled_without_restart(self):
        now = ms("2026-09-29 00:05")                     # 09:05 Tokyo
        self.h.clock.now = now
        wakes = self.h.wakes
        status, body = self.handle({"action": "save", "session": self.TOKYO})
        self.assertEqual(status, 200, body)
        self.assertGreater(self.h.wakes, wakes)
        self.assertEqual(len(body["opens"]), 3)
        rows = {r["session_id"]: r for r in self.h.svc.list_sessions()}
        self.assertEqual(rows["tokyo"]["job_status"], "scheduled")
        self.assertEqual(rows["tokyo"]["next_open_utc"], "2026-09-30T00:00:00Z")
        jobs = [j for j in self.h.sched.jobs(now)
                if j["session"]["session_id"] == "tokyo" and j["day"] == D]
        self.assertEqual([(j["kind"], j["day"].isoformat(), j["at"]) for j in jobs],
                         [("phase1", DAY, ms("2026-09-29 00:15:20"))])

    def test_tc11_admin_rejects_bad_fields(self):
        status, body = self.handle({"action": "save",
                                    "session": {**self.TOKYO, "open_time": "08:10",
                                                "timezone": "Asia/Hanoii"}})
        self.assertEqual(status, 400)
        self.assertEqual(set(body["errors"]), {"open_time", "timezone"})
        self.assertIsNone(self.h.sessions.get("tokyo"))

    def test_preview_shows_dst_shift(self):
        now = ms("2026-10-22 12:00")
        self.h.clock.now = now
        status, body = self.handle({"action": "preview", "session": {
            **self.TOKYO, "session_id": "ldn2", "timezone": "Europe/London",
            "open_time": "08:00"}})
        self.assertEqual(status, 200, body)
        self.assertEqual([o["open_utc"] for o in body["opens"]],
                         ["2026-10-23T07:00:00Z", "2026-10-26T08:00:00Z",
                          "2026-10-27T08:00:00Z"])
        self.assertIsNone(self.h.sessions.get("ldn2"))   # preview khong luu

    def test_delete_requires_confirm(self):
        status, body = self.handle({"action": "delete", "session_id": "ldn"})
        self.assertEqual(status, 400)
        self.assertIsNotNone(self.h.sessions.get("ldn"))
        status, _ = self.handle({"action": "delete", "session_id": "ldn", "confirm": "ldn"})
        self.assertEqual(status, 200)
        self.assertIsNone(self.h.sessions.get("ldn"))

    def test_tc09_disable_removes_job_and_stops_watch(self):
        self.ny_only()
        self.h.drive(at("13:45"), at("13:51"))
        self.assertTrue(self.watch()["active"])
        self.h.clock.now = at("13:52")
        status, _ = self.handle({"action": "disable", "session_id": "ny"})
        self.assertEqual(status, 200)
        before = len(self.h.market.m5_requests())
        self.h.run_due(at("13:55:20"))
        self.assertFalse(self.watch()["active"])
        self.assertEqual(self.watch()["stop_reason"], "disabled")
        self.assertEqual(len(self.h.market.m5_requests()), before)
        self.assertEqual(self.h.sched.jobs(at("13:56")), [])
        self.assertNotIn("ny", [s["session_id"] for s in self.h.svc.resolve()])
        self.assertEqual(self.h.svc.list_sessions(), [])
        disabled = self.h.svc.list_sessions(include_disabled=True)
        self.assertEqual({r["session_id"]: r["job_status"] for r in disabled},
                         {"ldn": "paused", "ny": "paused"})
        # van xem duoc khi chi dinh session_id
        self.assertEqual(self.h.svc.resolve("ny")[0]["session_id"], "ny")

    def test_delete_stops_watch(self):
        self.ny_only()
        self.h.drive(at("13:45"), at("13:51"))
        self.h.sessions.delete("ny")
        self.h.run_due(at("13:55:20"))
        self.assertEqual(self.watch()["stop_reason"], "deleted")

    def test_tc10_change_open_time_replaces_job(self):
        self.ny_only()
        self.h.market.fill(at("14:00"), at("14:15"), OR_HALF)
        self.h.drive(at("13:45"), at("13:51"))
        self.h.clock.now = at("13:52")
        status, body = self.handle({"action": "save", "original_id": "ny",
                                    "session": {**self.ny(), "open_time": "10:00"}})
        self.assertEqual(status, 200, body)
        jobs = [j for j in self.h.sched.jobs(at("13:52:30"))
                if j["session"]["session_id"] == "ny" and j["day"] == D]
        self.assertEqual([(j["kind"], j["day"].isoformat(), j["at"]) for j in jobs],
                         [("phase1", DAY, at("14:15:20"))])
        st = self.state()
        self.assertEqual(st["superseded"][-1]["watch"]["stop_reason"], "session_changed")
        self.assertFalse(st["watch"]["active"])
        self.h.drive(at("13:53"), at("14:16"))
        st = self.state()
        self.assertEqual(st["open_utc"], "2026-09-29T14:00:00Z")
        self.assertTrue(st["watch"]["active"])
        jobs = [j for j in self.h.sched.jobs(at("14:16"))
                if j["session"]["session_id"] == "ny" and j["day"] == D]
        self.assertEqual(len(jobs), 1)


class RestartAndErrorsTest(Base):
    def test_tc12_restart_rebuilds_jobs_without_duplicates(self):
        self.both()
        self.h.drive(at("13:40"), at("13:51"))    # ldn da het (catch-up), ny dang watch
        before = [(j["kind"], j["session"]["session_id"], j["day"], j["at"])
                  for j in self.h.sched.jobs(at("13:52"))]
        h2 = self.h.restart()
        after = [(j["kind"], j["session"]["session_id"], j["day"], j["at"])
                 for j in h2.sched.jobs(at("13:52"))]
        self.assertEqual(before, after)
        keys = [(k, s, d) for k, s, d, _ in after]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(("watch", "ny", D, NY_OR_END + 2 * M5_MS + DELAY), after)
        h2.drive(at("13:52"), at("14:01"))
        self.assertEqual(h2.state("ny", DAY)["watch"]["ticks"], 3)

    def test_tc12_restart_before_open(self):
        self.ny_only()
        h2 = self.h.restart()
        jobs = [(j["kind"], j["session"]["session_id"], j["day"].isoformat(), j["at"])
                for j in h2.sched.jobs(at("12:00"))]
        # hom nay + ngay mai (job ngay mai luon duoc lap san), khong co ngay hom qua
        self.assertEqual(jobs, [("phase1", "ny", DAY, NY_OR_END + DELAY),
                                ("phase1", "ny", "2026-09-30", NY_OR_END + DELAY + 24 * H1_MS)])

    def test_restart_after_window_closes_watch_without_m5(self):
        self.ny_only()
        self.h.drive(at("13:45"), at("13:51"))
        before = len(self.h.market.m5_requests())
        for now in (at("18:00"), ms("2026-09-30 06:00")):    # cung ngay / sang ngay sau
            h2 = self.h.restart()
            h2.run_due(now)
            st = h2.state("ny", DAY)
            self.assertEqual((st["state"], st["watch"]["stop_reason"]),
                             ("EXPIRED", "window_expired"))
            self.assertFalse(st["watch"]["active"])
            self.assertEqual(len(self.h.market.m5_requests()), before)

    def test_no_catch_up_for_yesterday(self):
        self.ny_only()
        self.h.run_due(at("06:00"))           # NY 2026-09-28 da het tu 16:45Z hom qua
        self.assertEqual(self.h.market.requests, [])
        self.assertIsNone(self.h.state("ny", "2026-09-28"))

    def test_tc13_error_in_london_job_is_isolated(self):
        self.both()
        real = self.h.svc.opening_range

        def broken(session, *args, **kwargs):
            if session["session_id"] == "ldn":
                raise RuntimeError("gia lap loi job London")
            return real(session, *args, **kwargs)

        self.h.svc.opening_range = broken
        due = self.h.run_due(NY_OR_END + DELAY)            # ca hai phase1 cung den han
        self.assertEqual(sorted(j["session"]["session_id"] for j in due), ["ldn", "ny"])
        rows = {r["session_id"]: r for r in self.h.svc.list_sessions()}
        self.assertEqual(rows["ldn"]["job_status"], "error")
        self.assertIn("gia lap loi job London", rows["ldn"]["last_error"])
        self.assertEqual(rows["ny"]["job_status"], "scheduled")
        self.assertTrue(self.watch("ny")["active"])
        self.h.drive(NY_OR_END + DELAY + 1000, at("14:01"))
        self.assertEqual(self.watch("ny")["ticks"], 3)
        self.assertEqual(self.h.states.job("ny")["status"], "ok")

    def test_tc13_london_fetch_failure_gives_up_then_new_york_runs(self):
        self.both()
        self.h.market.fail_sources = ("orb_phase1:ldn",)
        self.h.drive(at("07:00"), at("07:30"))
        ldn = self.state("ldn")
        self.assertEqual(ldn["phase1"]["status"], "error")
        self.assertEqual(ldn["phase1"]["attempts"], 6)
        self.assertEqual(ldn["watch"]["stop_reason"], "data_missing")
        self.assertEqual(self.h.states.job("ldn")["status"], "error")
        self.h.drive(at("13:40"), at("14:01"))
        self.assertTrue(self.watch("ny")["active"])
        rows = {r["session_id"]: r for r in self.h.svc.list_sessions()}
        self.assertEqual((rows["ldn"]["job_status"], rows["ny"]["job_status"]),
                         ("error", "scheduled"))
        self.assertEqual(self.h.market.m5_requests("orb_watch:ldn"), [])


class OrbChecksTest(Base):
    def trade(self, sid, or_date=DAY, opened="2026-09-29T20:50:00+07:00", tid=1):
        return {"id": tid, "strategy": "ORB", "session_id": sid, "or_date": or_date,
                "side": "long", "opened_at": opened, "_journal_date": DAY}

    def test_window_checks(self):
        self.ny_only()
        self.h.market.mark_fetched(at("14:00"))
        early = self.h.svc.orb_checks("ny", DAY, at=at("13:40"))
        self.assertFalse(early["in_window"])
        self.assertIn("OR chua chot", " ".join(early["violations"]))
        ok = self.h.svc.orb_checks("ny", DAY, at=at("14:00"))
        self.assertTrue(ok["in_window"])
        self.assertEqual(ok["violations"], [])
        self.assertEqual(ok["window_utc"], {"from": "2026-09-29T13:45:00Z",
                                            "to": "2026-09-29T16:45:00Z"})
        late = self.h.svc.orb_checks("ny", DAY, at=at("17:00"))
        self.assertIn("da het", " ".join(late["violations"]))

    def test_tc22_session_limit(self):
        self.ny_only()
        self.h.market.mark_fetched(at("14:00"))
        self.h.journal.append(self.trade("ny"))
        checks = self.h.svc.orb_checks("ny", DAY, at=at("14:00"))
        self.assertEqual(checks["limits"]["status"], "session_limit_reached")
        self.assertIn("vuot max_trades cua phien (1/1)", checks["violations"])

    def test_tc22_day_limit(self):
        self.ny_only()
        self.h.market.mark_fetched(at("14:00"))
        self.h.sessions.save({**self.ny(), "max_trades": 3}, original_id="ny")
        self.h.journal.extend([self.trade("ldn", opened="2026-09-29T14:20:00+07:00"),
                               self.trade("ny", tid=2)])
        checks = self.h.svc.orb_checks("ny", DAY, at=at("14:00"))
        self.assertEqual(checks["limits"]["day_trades"], 2)
        self.assertEqual(checks["limits"]["status"], "day_limit_reached")
        # TM - #ORB-RULES - ORB Rule Set: quota ngay la rule ORB (score_trade)
        verdict = self.h.svc.score_trade("ny", DAY, side="long", entry=P + 100, stop=P - 30,
                                         target=P + 295, margin_usd=19, at=at("14:00"))
        self.assertIn("vuot orb.max_trades_per_day (2): da co 2 lenh ORB ngay 2026-09-29",
                      verdict["rule_violations"])
        self.assertFalse(verdict["would_pass"])
        # trade cua or_date khac khong tinh
        self.h.journal[:] = [self.trade("ldn", or_date="2026-09-28")]
        self.assertEqual(self.h.svc.orb_checks("ny", DAY, at=at("14:00"))["violations"], [])
        verdict = self.h.svc.score_trade("ny", DAY, side="long", entry=P + 100, stop=P - 30,
                                         target=P + 295, margin_usd=19, at=at("14:00"))
        self.assertEqual(verdict["rule_violations"], [])

    def test_day_limit_blocks_signal_plan(self):
        self.ny_only()
        self.h.journal.extend([self.trade("ldn", opened="2026-09-29T14:20:00+07:00"),
                               self.trade("ldn", opened="2026-09-29T15:20:00+07:00", tid=2)])
        self.h.market.put(at("13:50"), P + 10, P + 120, P + 5, P + 100)
        self.h.drive(at("13:45"), at("13:56"))
        sig = self.h.svc.signal(self.ny(), D, at("13:56"))
        self.assertEqual(sig["state"], "BREAKOUT_LONG")
        # TM - #ORB-RULES - ORB Rule Set: quota ngay cung la rule ORB -> "rules"; plan
        # van tra de thay rule_check (AC-10) nhung danh dau blocked_by
        self.assertEqual(sig["blocked_by"], ["day_limit_reached", "rules"])
        self.assertEqual(sig["plan"]["blocked_by"], ["day_limit_reached", "rules"])
        self.assertFalse(sig["plan"]["rule_check"]["would_pass"])

    def test_filtered_and_skipped_are_violations(self):
        self.ny_only(or_half=5)
        self.h.market.mark_fetched(at("14:00"))
        checks = self.h.svc.orb_checks("ny", DAY, at=at("14:00"))
        self.assertIn("range bi loc: too_narrow", " ".join(checks["violations"]))

    def test_unknown_session(self):
        with self.assertRaises(ValueError):
            self.h.svc.orb_checks("tokyo", DAY)
        with self.assertRaises(ValueError):
            self.h.svc.orb_checks("", DAY)


if __name__ == "__main__":
    unittest.main()
