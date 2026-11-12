# TM - #TR-BRANCH - Nhanh kich ban TR
"""Tieu chi nghiem thu #TR-BRANCH. DB tam, khong goi mang.

Nen M15 that BTCUSDT 05-08/11/2026 va TR b9a9d6b7 (6 event tentative, schematic undetermined) cua
test_wyckoff_tr. Vi du muc 7 cua tai lieu (TR 12/11) chua co nen trong kho test nen dung UTAD 23:30 05/11
(76957.8) lam event pha C; diem bang chung dung dung so cua muc 7.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_dashboard as td  # noqa: E402  dat BAMCP_DATA_ROOT ve thu muc tam truoc khi import server
from test_dashboard import BASE, VIEWER_PASS, HttpBase  # noqa: E402
from test_wyckoff_tr import TR_ID, TrBase  # noqa: E402

import admin_dashboard  # noqa: E402
import admin_entry  # noqa: E402
import entry_logic  # noqa: E402
import notifier  # noqa: E402
import server  # noqa: E402
import sqlite_db  # noqa: E402
import tr_logic  # noqa: E402

LONG_ACC = {**td.SETUP_A, "branch": "accumulation", "entry_event": "SPRING"}
LONG_RNG = {**td.SETUP_A, "branch": "range", "entry_event": "ST_B"}
SHORT_DIST = {**td.SETUP_B, "branch": "distribution", "entry_event": "UTAD", "ref_event": "AR#1"}
UTAD = {"type": "UTAD", "t": "2026-11-05 23:30", "price": 76957.8, "status": "confirmed"}
REASON_DOWN = "TR xác định nhánh giảm bởi UTAD#1 (pha C)"
# Muc 7: dist 7 = xu huong D/H4 2 + dinh thap dan 1 + cung o dinh 1 + su kien C/D 3; acc 2 = day cao dan 1 +
# hap thu o ST_B 1. Bo trong weight = lay thang diem.
EVIDENCE = [
    {"factor": "htf_trend", "side": "dist", "note": "D/H4 giam truoc TR"},
    {"factor": "phase_b_highs", "side": "dist", "note": "dinh thap dan"},
    {"factor": "test_highs", "side": "dist", "note": "cung o dinh"},
    {"factor": "phase_cd_event", "side": "dist", "note": "UTAD#1 confirmed"},
    {"factor": "phase_b_lows", "side": "acc", "note": "day cao dan"},
    {"factor": "test_lows", "side": "acc", "note": "hap thu o ST_B"},
]


class BranchBase(TrBase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(server.WATCHER, "notify_threadsafe")
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    def prepare(self, *setups):
        """TR b9a9d6b7 dang o pha B, chua xac dinh nhanh, roi luu setup lien ket TR."""
        self.migrated()
        server.update_tr(self.symbol, phases=[{"phase": "A", "start": {"event": "SC#1"}},
                                              {"phase": "B", "start": {"event": "ST#1"}}])
        self.assertIsNone(server.PLANS.active_tr(self.symbol)["branch"])
        out = self.save_setups(*setups)
        self.assertEqual(out["rejected"], [])
        self.notify.reset_mock()
        return out["codes"]

    def status(self, code):
        return server.SETUPS.get(code)["status"]

    def utad(self, **kw):
        return server.update_tr(self.symbol, add_events=[UTAD], **kw)


class NamingTest(BranchBase):
    def test_branch_direction_rejected(self):
        """Nghiem thu 1: branch=distribution, direction=long -> tu choi kem ly do."""
        out = self.save_setups({**td.SETUP_A, "branch": "distribution", "entry_event": "TEST"},
                               {**td.SETUP_B, "branch": "accumulation", "entry_event": "UT"},
                               {**td.SETUP_A, "branch": "range", "entry_event": "UTAD"},
                               {k: v for k, v in td.SETUP_A.items() if k != "branch"},
                               {**td.SETUP_A, "entry_event": None},
                               {**td.SETUP_A, "branch": "sideway"},
                               {**td.SETUP_A, "entry_event": "AR"},
                               {**td.SETUP_A, "ref_event": "XYZ#1"})
        self.assertEqual(out["saved"], [])
        errors = [r["error"] for r in out["rejected"]]
        self.assertIn("branch=distribution chi nhan short, setup nay la long", errors[0])
        self.assertIn("branch=accumulation chi nhan long, setup nay la short", errors[1])
        self.assertIn("entry_event UTAD la diem vao short, setup nay la long", errors[2])
        self.assertIn("thieu truong bat buoc branch", errors[3])
        self.assertIn("thieu truong bat buoc entry_event", errors[4])
        self.assertIn("branch phai la mot trong", errors[5])
        self.assertIn("entry_event phai la mot trong", errors[6])
        self.assertIn("ref_event:", errors[7])

    def test_code_and_auto_label(self):
        """Ma {SYM}-{yyyymmdd}-{L|S}-{ACC|DIST|RNG}-{pha}-{entry_event}-{n}; label tu sinh theo muc 2.3."""
        codes = self.prepare(LONG_ACC, LONG_RNG, SHORT_DIST, LONG_ACC)
        self.assertEqual(codes, ["BTC-20261107-L-ACC-B-SPRING-1", "BTC-20261107-L-RNG-B-ST_B-1",
                                 "BTC-20261107-S-DIST-B-UTAD-1", "BTC-20261107-L-ACC-B-SPRING-2"])
        view = {s["code"]: s for s in server.get_setups(self.symbol, date="2026-11-07")["setups"]}
        self.assertEqual(view[codes[0]]["label"], "BTC 07/11 · LONG · TÍCH LUỸ · Pha B · SPRING · 75400–75560")
        self.assertEqual(view[codes[1]]["label"], "BTC 07/11 · LONG · RANGE · Pha B · ST_B · 75400–75560")
        self.assertEqual(view[codes[2]]["label"],
                         "BTC 07/11 · SHORT · PHÂN PHỐI · Pha B · UTAD sau AR#1 · 77320–77600")
        self.assertEqual((view[codes[2]]["branch"], view[codes[2]]["entry_event"], view[codes[2]]["ref_event"]),
                         ("distribution", "UTAD", "AR#1"))
        # ma moi co the doc lai bang code nhu ma cu
        self.assertEqual(server.SETUPS.get(codes[1].lower())["code"], codes[1])

    def test_doc_example_label_and_custom_label(self):
        clean = {"symbol": "BTCUSDT", "bias_date": "2026-11-12", "direction": "short", "branch": "distribution",
                 "phase": "D", "entry_event": "LPSY", "ref_event": "SOW#1", "zone_low": 72400.0, "zone_high": 72740.0}
        self.assertEqual(entry_logic.setup_label(clean), "BTC 12/11 · SHORT · PHÂN PHỐI · Pha D · LPSY sau SOW#1 · 72400–72740")
        self.assertEqual(entry_logic.code_prefix("BTCUSDT", clean), "BTC-20261112-S-DIST-D-LPSY-")
        self.assertEqual(entry_logic.code_prefix("BTCUSDT", {**clean, "phase": None}), "BTC-20261112-S-DIST-X-LPSY-")
        out = self.save_setups({**LONG_ACC, "label": "  Spring   quet day W/D  "})
        self.assertEqual(out["saved"][0]["label"], "Spring quet day W/D")
        self.assertTrue(out["codes"][0].startswith("BTC-20261107-L-ACC-X-SPRING-"), "chua co TR -> pha X")

    def test_link_warnings(self):
        self.prepare()
        out = self.save_setups({**LONG_ACC, "event_ref": "TEST#3", "ref_event": "SOW#1"})
        text = " | ".join(out["warnings"])
        self.assertIn("event_ref TEST#3 khac entry_event SPRING", text)
        self.assertIn("ref_event SOW#1 chua co tren TR", text)

    def test_legacy_setup_gets_fallback_label(self):
        """Setup luu truoc ticket (khong co branch/label) van co label ghep tu truong dang co."""
        row = {"symbol": "BTCUSDT", "bias_date": "2026-11-07", "direction": "long", "trigger_type": "rejection",
               "phase": "C", "zone_low": 75400, "zone_high": 75560}
        self.assertEqual(entry_logic.setup_label(row), "BTC 07/11 · LONG · Pha C · rejection · 75400–75560")


class PruneTest(BranchBase):
    def test_utad_prunes_counter_longs_with_one_summary(self):
        """Nghiem thu 2: 2 long (acc, range) + 1 short; UTAD#1 confirmed -> 2 long PRUNED, short giu, 1 tin."""
        acc, rng, short = self.prepare(LONG_ACC, LONG_RNG, SHORT_DIST)
        out = self.utad()
        self.assertEqual((out["tr"]["branch"], out["tr"]["branch_by"]["ref"], out["tr"]["branch_by"]["phase"]),
                         ("distribution", "UTAD#1", "C"))
        self.assertEqual(out["branch"]["reason"], REASON_DOWN)
        self.assertTrue(out["branch"]["changed"])
        self.assertEqual([s["code"] for s in out["branch"]["pruned"]], [acc, rng])
        self.assertEqual((self.status(acc), self.status(rng), self.status(short)), ("PRUNED", "PRUNED", "WAITING"))
        self.assertEqual(out["branch"]["conflicts"], [])
        # Event setup PRUNED kem reason; state giu ly do de dashboard hien
        last = server.get_setup_events(setup_id=acc)["events"][-1]
        self.assertEqual((last["event"], last["from_status"], last["to_status"]), ("PRUNED", "WAITING", "PRUNED"))
        self.assertEqual(last["details"]["reason"], REASON_DOWN)
        self.assertEqual(server.SETUPS.get(rng)["state"]["pruned"]["reason"], REASON_DOWN)
        # Watcher bo qua PRUNED; log_trade khong gan duoc vao setup da loai
        self.assertEqual([s["code"] for s in server.SETUPS.active(self.symbol)], [short])
        self.assertIn("PRUNED", entry_logic.TERMINAL)
        # Dung mot tin tong hop, liet ke label bi loai
        self.notify.assert_called_once_with(out["branch"]["event_id"])
        event = server.SETUPS.event(out["branch"]["event_id"])
        self.assertEqual((event["event"], event["setup_id"]), ("TR_BRANCH", None))
        text = notifier.system_alert(event["event"], event["details"], server.TZ).text
        self.assertIn(f"BTCUSDT · {REASON_DOWN}", text)
        self.assertIn("Loại 2 setup ngược nhánh", text)
        self.assertIn(f"BTC 07/11 · LONG · TÍCH LUỸ · Pha B · SPRING · 75400–75560 ({acc})", text)
        self.assertIn(f"({rng})", text)
        self.assertNotIn(short, text)
        # TR_BRANCH nam trong timeline cua cap; trs cua get_setups co nhanh
        self.assertIn("TR_BRANCH", [e["event"] for e in server.get_setup_events(symbol=self.symbol)["events"]])
        self.assertEqual(server.get_setups(self.symbol, date="2026-11-07")["trs"][TR_ID]["branch"], "distribution")
        # Ghi lai TR ma nhanh khong doi thi khong loai them, khong gui them tin
        again = server.update_tr(self.symbol, note="giu nhanh giam")
        self.assertEqual((again["branch"]["changed"], again["branch"]["pruned"]), (False, []))
        self.notify.assert_called_once()

    def test_filled_counter_setup_only_warns(self):
        """Nghiem thu 3: nhu (2) nhung 1 long FILLED -> giu FILLED, co canh bao vi the nguoc nhanh."""
        acc, rng, short = self.prepare(LONG_ACC, LONG_RNG, SHORT_DIST)
        setup = server.SETUPS.get(acc)
        server.SETUPS.apply(setup, "FILLED", setup["state"], [
            {"event": "FILLED", "from_status": "WAITING", "to_status": "FILLED", "details": {"trade_id": 1}}])
        out = self.utad()
        self.assertEqual((self.status(acc), self.status(rng), self.status(short)), ("FILLED", "PRUNED", "WAITING"))
        self.assertEqual([s["code"] for s in out["branch"]["conflicts"]], [acc])
        self.assertEqual([s["code"] for s in out["branch"]["pruned"]], [rng])
        self.assertTrue(any("vi the nguoc nhanh TR" in w and acc in w for w in out["warnings"]), out["warnings"])
        last = server.get_setup_events(setup_id=acc)["events"][-1]
        self.assertEqual((last["event"], last["to_status"]), ("BRANCH_CONFLICT", "FILLED"))
        self.notify.assert_called_once_with(out["branch"]["event_id"])
        text = notifier.system_alert("TR_BRANCH", server.SETUPS.event(out["branch"]["event_id"])["details"],
                                     server.TZ).text
        self.assertIn("Vị thế ngược nhánh TR", text)
        self.assertIn(f"({acc})", text)

    def test_closed_filled_setup_no_warning(self):
        acc, short = self.prepare(LONG_ACC, SHORT_DIST)
        setup = server.SETUPS.get(acc)
        server.SETUPS.apply(setup, "FILLED", setup["state"], [{"event": "FILLED", "to_status": "FILLED"}])
        server.PLANS.save_review(setup["id"], outcome="tp_hit", r_multiple=2.0, note=None, reviewed_by="tan")
        out = self.utad()
        self.assertEqual((out["branch"]["pruned"], out["branch"]["conflicts"]), ([], []))
        self.notify.assert_not_called()

    def test_failed_phase_c_does_not_restore(self):
        """Event pha C chuyen failed: khong tu khoi phuc setup, tra warning de Claude luu lai."""
        acc, rng, short = self.prepare(LONG_ACC, LONG_RNG, SHORT_DIST)
        self.utad()
        self.notify.reset_mock()
        out = server.update_tr(self.symbol, update_events=[{"ref": "UTAD#1", "status": "failed", "note": "dong lai tren"}])
        self.assertIsNone(out["tr"]["branch"])
        text = " | ".join(out["warnings"])
        self.assertIn("UTAD#1 chuyen failed - TR ve trung tinh", text)
        self.assertIn(f"2 setup da PRUNED KHONG tu khoi phuc ({acc}, {rng})", text)
        self.assertEqual((self.status(acc), self.status(rng)), ("PRUNED", "PRUNED"))
        self.notify.assert_not_called()
        # TR trung tinh lai thi luu lai duoc setup long
        self.assertEqual(self.save_setups(LONG_ACC)["rejected"], [])

    def test_counter_setup_rejected_after_branch(self):
        self.prepare(SHORT_DIST)
        self.utad()
        out = self.save_setups(LONG_RNG, SHORT_DIST)
        self.assertEqual(len(out["saved"]), 1)
        self.assertIn(f"{REASON_DOWN} - setup long nguoc nhanh", out["rejected"][0]["error"])

    def test_schematic_change_decides_branch(self):
        """Doi schematic sang ho tich luy cung xac dinh nhanh tang: loai short, canh bao chua co event."""
        acc, short = self.prepare(LONG_ACC, SHORT_DIST)
        out = server.update_tr(self.symbol, schematic="accumulation")
        self.assertEqual(out["branch"]["reason"], "TR xác định nhánh tăng bởi schematic accumulation")
        self.assertEqual((self.status(acc), self.status(short)), ("WAITING", "PRUNED"))
        self.assertTrue(any("chua co event pha C/D confirmed" in w for w in out["warnings"]), out["warnings"])
        # Event pha C confirmed sau do: van nhanh tang, nguon doi sang event, khong loai them
        later = server.update_tr(self.symbol, update_events=[{"ref": "SPRING#1", "status": "confirmed"}])
        self.assertEqual((later["tr"]["branch_by"]["by"], later["tr"]["branch_by"]["ref"]), ("event", "SPRING#1"))
        self.assertEqual(later["branch"]["pruned"], [])

    def test_save_scenarios_merge_also_prunes(self):
        acc, short = self.prepare(LONG_ACC, SHORT_DIST)
        tr = {"timeframe": "15m", "tr_high": 76976.7, "tr_low": 75525, "since": "2026-11-05 22:45",
              "events": [UTAD], "prob_evidence": EVIDENCE}
        out = server.save_scenarios(self.symbol, [], replace=False, bias_date="2026-11-07", tr=tr)
        self.assertEqual(out["tr"]["id"], TR_ID)
        self.assertEqual([s["code"] for s in out["branch"]["pruned"]], [acc])
        self.assertEqual((out["prob"]["accumulation"], out["prob"]["distribution"]), (22, 78))
        self.assertEqual(self.status(acc), "PRUNED")
        self.notify.assert_called_once_with(out["branch"]["event_id"])
        history = server.PLANS.prob_history(TR_ID)
        self.assertEqual((history[-1]["plan_revision"], history[-1]["bias_date"]), (out["plan_revision"], "2026-11-07"))

    def test_pruned_snapshot_and_review_stats(self):
        acc, rng, short = self.prepare(LONG_ACC, LONG_RNG, SHORT_DIST)
        self.utad()
        snap = server.PLANS.snapshots(self.symbol, "2026-11-07", payload=True)[-1]
        self.assertEqual(snap["trigger"], "update_tr", "ngay co setup bi loai co snapshot moi")
        self.assertEqual({s["code"]: s["status"] for s in snap["payload"]["setups"]}[acc], "PRUNED")
        stats = server.get_review_stats(self.symbol, "2026-11-01", "2026-11-30", group_by="branch")
        self.assertEqual({g["key"]: g["setups"] for g in stats["groups"]},
                         {"accumulation": 1, "distribution": 1, "range": 1})
        cross = server.get_review_stats(self.symbol, "2026-11-01", "2026-11-30", group_by="direction,entry_event")
        self.assertIn("short / UTAD", [g["key"] for g in cross["groups"]])


class ProbTest(BranchBase):
    def test_doc_example_78_22_and_history(self):
        """Nghiem thu 4: prob_evidence dist 7 / acc 2 -> 78% / 22%; lich su gan plan revision."""
        self.migrated()
        out = self.utad(prob_evidence=EVIDENCE)
        self.assertEqual((out["prob"]["distribution"], out["prob"]["accumulation"]), (78, 22))
        self.assertEqual((out["prob"]["weight_dist"], out["prob"]["weight_acc"]), (7.0, 2.0))
        self.assertEqual([e["weight"] for e in out["tr"]["prob_evidence"]], [2.0, 1.0, 1.0, 3.0, 1.0, 1.0])
        self.assertEqual((out["tr"]["prob_distribution"], out["tr"]["prob_accumulation"]), (78, 22))
        self.assertNotIn("phase_cd_event", " | ".join(out["warnings"]), "UTAD#1 da confirmed")
        history = server.PLANS.prob_history(TR_ID)
        self.assertEqual([(h["prob_accumulation"], h["plan_revision"]) for h in history], [(22, out["plan_revision"])])
        # Gui lai y nguyen: khong doi gi, khong them lich su
        same = server.update_tr(self.symbol, prob_evidence=EVIDENCE)
        self.assertFalse(same["changed"])
        changed = server.update_tr(self.symbol, prob_evidence=EVIDENCE[:4])
        self.assertEqual((changed["prob"]["distribution"], changed["prob"]["accumulation"]), (100, 0))
        self.assertEqual(len(server.PLANS.prob_history(TR_ID)), 2)
        cleared = server.update_tr(self.symbol, prob_evidence=[])
        self.assertEqual((cleared["prob"]["accumulation"], cleared["tr"]["prob_evidence"]), (None, []))
        # Bieu do tra prob_* cua TR va dien bien trong ngay
        chart = td.run(server.get_chart_data(self.symbol, "15m", 96, date=server._today()))
        self.assertIsNone(chart["tr"]["prob_accumulation"])
        self.assertEqual([h["prob_accumulation"] for h in chart["prob_history"]], [22, 0, None])
        self.assertEqual(chart["tr"]["branch"], "distribution")

    def test_validation_and_warnings(self):
        self.migrated()
        before = server.PLANS.active_tr(self.symbol)
        for bad, pattern in (([{"factor": "htf_trend", "side": "up"}], "side phai la acc hoac dist"),
                             ([{"factor": "khoi luong", "side": "acc"}], "ngoai thang diem phai khai weight"),
                             ([{"factor": "htf_trend", "side": "acc", "weight": -1}], r"weight phai > 0"),
                             ({"factor": "htf_trend"}, "phai la list")):
            with self.assertRaisesRegex(ValueError, pattern):
                server.update_tr(self.symbol, prob_evidence=bad)
        self.assertEqual(server.PLANS.active_tr(self.symbol)["prob_evidence"], before["prob_evidence"])
        out = server.update_tr(self.symbol, prob_evidence=[
            {"factor": "HTF_TREND", "side": "ACC", "weight": 5},
            {"factor": "khoi luong pha B", "side": "dist", "weight": 1.5},
            {"factor": "phase_cd_event", "side": "acc"},
            {"factor": "phase_cd_event", "side": "acc"}])
        text = " | ".join(out["warnings"])
        self.assertIn("weight 5 khac thang diem htf_trend = 2", text)
        self.assertIn("'khoi luong pha B' khong co trong thang diem", text)
        self.assertIn("phase_cd_event khai lan hai", text)
        self.assertIn("phase_cd_event ben acc nhung TR chua co SPRING/SHAKEOUT/SOS/JAC confirmed", text)
        # acc = 5 + 3 + 3 = 11, dist 1.5 -> 11 / 12.5 = 88%
        self.assertEqual((out["prob"]["accumulation"], out["prob"]["distribution"]), (88, 12))
        self.assertEqual(out["tr"]["prob_evidence"][0]["factor"], "htf_trend")

    def test_admin_weights_apply_to_next_update(self):
        self.migrated()
        status, body = server._dash_accounts_action(
            {"action": "config_save", "config": {"prob_weights": {"htf_trend": 4, "test_lows": ""}}}, None)
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(server._dash_cfg()["prob_weights"]["htf_trend"], 4.0)
        self.assertEqual(server._dash_cfg()["prob_weights"]["test_lows"], 1.0, "o trong = mac dinh")
        out = self.utad(prob_evidence=EVIDENCE)
        # dist 4 + 1 + 1 + 3 = 9, acc 2 -> 2 / 11 = 18%
        self.assertEqual((out["prob"]["accumulation"], out["prob"]["distribution"]), (18, 82))
        status, body = server._dash_accounts_action(
            {"action": "config_save", "config": {"prob_weights": {"htf_trend": 0}}}, None)
        self.assertEqual(status, 400)
        self.assertIn("prob_weights.htf_trend phai > 0", body["error"])
        page = admin_dashboard.render(server._dash_accounts_view(), action_path="/x", admin_path="/admin",
                                      setup_required=False)
        self.assertIn('data-weight="htf_trend"', page)
        self.assertIn('value="4"', page)
        self.assertIn("Thang điểm xác suất TR", page)


class LogicTest(unittest.TestCase):
    def tr(self, *events, schematic="undetermined"):
        rows = [{"type": kind, "t": t, "price": 1, "status": status} for kind, t, status in events]
        return tr_logic.decorate_tr({"id": "x", "tr_high": 2, "tr_low": 1, "since": 0, "schematic": schematic,
                                     "events": rows})

    def test_branch_precedence(self):
        self.assertIsNone(self.tr(("SPRING", 5, "tentative"), ("UTAD", 6, "failed"))["branch"])
        self.assertEqual(self.tr(("SOS", 5, "confirmed"))["branch_by"]["phase"], "D", "khong co pha C thi pha D")
        both = self.tr(("SPRING", 5, "confirmed"), ("SOW", 9, "confirmed"))
        self.assertEqual((both["branch"], both["branch_by"]["ref"]), ("accumulation", "SPRING#1"), "pha C uu tien")
        latest = self.tr(("SPRING", 5, "confirmed"), ("UTAD", 9, "confirmed"))
        self.assertEqual(latest["branch"], "distribution", "hai event pha C: lay event muon nhat")
        self.assertEqual(self.tr(schematic="redistribution")["branch_by"],
                         {"branch": "distribution", "by": "schematic", "schematic": "redistribution"})
        again = tr_logic.decorate_tr(latest)
        self.assertEqual(again["branch_by"], latest["branch_by"], "decorate hai lan van nhu nhau")
        self.assertEqual(tr_logic.branch_reason(latest["branch_by"]), "TR xác định nhánh giảm bởi UTAD#1 (pha C)")

    def test_prob_rounding(self):
        evidence = [{"factor": "a", "side": "acc", "weight": 1.0}, {"factor": "b", "side": "dist", "weight": 7.0}]
        self.assertEqual(tr_logic.prob_split(evidence), (13, 87), "12.5 lam tron len")
        self.assertEqual(tr_logic.prob_split([]), (None, None))

    def test_new_statuses_and_events(self):
        self.assertIn("PRUNED", entry_logic.STATUSES)
        self.assertEqual({e for e, d in entry_logic.ENTRY_EVENTS.items() if d == "long"},
                         {"SPRING", "SHAKEOUT", "TEST", "SOS", "JAC", "LPS", "BU", "ST_B"})
        self.assertIn("PRUNED", admin_entry.STOPPED)

    def test_setup_alert_shows_label_above_htf(self):
        setup = {"id": "s", "code": "BTC-20261112-S-DIST-D-LPSY-1", "symbol": "BTCUSDT", "direction": "short",
                 "entry": 72500, "stop": 72800, "target": 71500, "zone_low": 72400, "zone_high": 72740,
                 "trigger_type": "rejection", "state": {}, "htf": {"w": "down", "d": "down", "h4": "up", "bias": "short"},
                 "label": "BTC 12/11 · SHORT · PHÂN PHỐI · Pha D · LPSY sau SOW#1 · 72400–72740"}
        lines = notifier.setup_alert(setup, {"event": "IN_ZONE", "details": {}}, server.TZ).text.splitlines()
        self.assertIn("BTC-20261112-S-DIST-D-LPSY-1 · SHORT · IN_ZONE", lines[0])
        self.assertEqual(lines[1], f"🏷 {setup['label']}")
        self.assertIn("NGƯỢC H4", lines[2])
        pruned = notifier.setup_alert({**setup, "state": {}}, {"event": "PRUNED", "details": {"reason": "x"}}, server.TZ)
        self.assertIn("PRUNED", pruned.text)


class MigrationTest(unittest.TestCase):
    def test_new_columns_and_history_table(self):
        path = Path(tempfile.mkdtemp(prefix="trbranch-", dir=td._TMP)) / "old.db"
        sqlite_db.migrate(path)
        with sqlite_db.connect(path) as conn:
            setups = {r[1] for r in conn.execute("PRAGMA table_info(setups)")}
            ranges = {r[1] for r in conn.execute("PRAGMA table_info(trading_ranges)")}
            history = {r[1] for r in conn.execute("PRAGMA table_info(tr_prob_history)")}
        self.assertLessEqual({"branch", "entry_event", "ref_event", "label"}, setups)
        self.assertLessEqual({"prob_evidence", "prob_accumulation", "prob_distribution"}, ranges)
        self.assertLessEqual({"tr_id", "at", "evidence", "plan_revision", "bias_date"}, history)


class DashboardPrunedTest(HttpBase):
    def setUp(self):
        super().setUp()
        self.klines.upsert(self.symbol, "15m", td.real_m15_rows())
        patcher = mock.patch.object(server.WATCHER, "notify_threadsafe")
        patcher.start()
        self.addCleanup(patcher.stop)

    def prepare(self):
        TrBase.insert_legacy(self)
        server._migrate_tr_events()
        codes = self.save_setups(LONG_ACC, SHORT_DIST)["codes"]
        server.update_tr(self.symbol, add_events=[UTAD], prob_evidence=EVIDENCE)
        return codes

    def test_api_and_page_hide_pruned_by_default(self):
        """Nghiem thu 5: dashboard mac dinh khong thay PRUNED; bat nut thi thay, kem reason."""
        acc, short = self.prepare()

        async def main(c):
            await self.login(c, "guest", VIEWER_PASS)
            setups = (await c.get(f"{BASE}/api/setups", params={"symbol": self.symbol, "date": "2026-11-07"})).json()
            rows = {s["code"]: s for s in setups["setups"]}
            self.assertEqual((rows[acc]["status"], rows[short]["status"]), ("PRUNED", "WAITING"))
            self.assertEqual(rows[acc]["state"]["pruned"]["reason"], REASON_DOWN)
            self.assertTrue(rows[acc]["state"]["pruned"]["at_ms"])
            # TR chua co pha: label bo phan "Pha"
            self.assertEqual(rows[short]["label"], "BTC 07/11 · SHORT · PHÂN PHỐI · UTAD sau AR#1 · 77320–77600")
            # TR mau tao luc 08/11 nen ngay 07/11 (da qua) chua co TR khoa: doc TR o ngay hom nay
            chart = (await c.get(f"{BASE}/api/chart", params={"symbol": self.symbol, "date": server._today(),
                                                               "tf": "15m", "bars": 96})).json()
            self.assertEqual((chart["tr"]["branch"], chart["tr"]["branch_by"]["ref"]), ("distribution", "UTAD#1"))
            self.assertEqual((chart["tr"]["prob_accumulation"], chart["tr"]["prob_distribution"]), (22, 78))
            self.assertEqual(len(chart["tr"]["prob_evidence"]), 6)
            events = (await c.get(f"{BASE}/api/events", params={"symbol": self.symbol,
                                                                 "date": server._today()})).json()["events"]
            self.assertIn("TR_BRANCH", [e["event"] for e in events])
            page = (await c.get(BASE)).text
            self.assertIn('id="showPruned"', page)
            self.assertIn("prob_factors", page)
            script = (await c.get(server._dash_asset_url("dashboard.js"))).text
            for marker in ("Hiện", "setup đã loại", "shownSetups", "Đã loại:", "Nhánh ${word}",
                           "Điểm bằng chứng, chưa hiệu chỉnh thống kê"):
                self.assertIn(marker, script)
        self.session(main)


if __name__ == "__main__":
    unittest.main()
