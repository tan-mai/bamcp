# TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
"""Tieu chi nghiem thu #WYCKOFF-TR. DB tam, khong goi mang.

Nen M15 la so lieu that BTCUSDT 05-08/11/2026 (REAL_M15 trong test_dashboard); TR b9a9d6b7 va payload
vi du lay dung tu tai lieu yeu cau.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_dashboard as td  # noqa: E402  dat BAMCP_DATA_ROOT ve thu muc tam truoc khi import server
from test_dashboard import BASE, H1, M15, VIEWER_PASS, Base, HttpBase, run, vn_ms  # noqa: E402

import server  # noqa: E402
import sqlite_db  # noqa: E402
import tr_logic  # noqa: E402

TR_ID = "b9a9d6b7-f423-4c3d-96a7-63ae58a2c37e"
REDRAW = "Ve lai khi M15 dong < 75158 hoac H1 dong > 76976.7"
LEGACY_EVENTS = [
    {"type": "SC", "t": vn_ms("2026-11-05 22:45"), "price": 75525},
    {"type": "AR", "t": vn_ms("2026-11-05 23:15"), "price": 76976.7},
    {"type": "ST", "t": vn_ms("2026-11-06 06:00"), "price": 75158},
    {"type": "SPRING", "t": vn_ms("2026-11-06 22:00"), "price": 74810.8},
    {"type": "TEST", "t": vn_ms("2026-11-07 00:45"), "price": 75472.8},
    {"type": "TEST", "t": vn_ms("2026-11-08 04:30"), "price": 75526.9},
]
# Payload vi du o muc 5 cua tai lieu
DOC_PAYLOAD = {
    "symbol": "BTCUSDT",
    "schematic": "accumulation",
    "status": "hypothesis",
    "levels": {"creek": 76532.5, "ice": 75158.0},
    "phases": [
        {"phase": "A", "start": {"event": "SC#1"}, "end": {"event": "ST#1"}, "note": "SC 75525 -> AR 76976.7 -> ST 75158"},
        {"phase": "B", "start": {"event": "ST#1"}, "end": {"event": "SPRING#1"}},
        {"phase": "C", "start": {"event": "SPRING#1"}, "note": "Spring 74810.8, test 2 lan, chua SOS"},
    ],
    "update_events": [
        {"ref": "SPRING#1", "status": "confirmed", "note": "M15 vol 9.8x, dong 99%, quet W/D 74909.4"},
        {"ref": "TEST#1", "status": "confirmed", "note": "vol 0.77x, dong 90%"},
        {"ref": "TEST#2", "status": "tentative", "note": "M15 04:30 vol 2.45x - vol cao, can them xac nhan"},
    ],
}


def h4_rows() -> list[list]:
    """Gop nen H1 that thanh H4 theo moc UTC nhu Binance (gio VN 03, 07, 11, 15, 19, 23)."""
    span = 4 * H1
    groups: dict[int, list[list]] = {}
    for row in td.real_h1_rows():
        groups.setdefault(row[0] // span * span, []).append(row)
    return [[key, rs[0][1], max(r[2] for r in rs), min(r[3] for r in rs), rs[-1][4], sum(r[5] for r in rs),
             key + span - 1] for key, rs in sorted(groups.items())]


def by_ref(tr: dict, ref: str) -> dict:
    return next(e for e in tr["events"] if e["ref"] == ref)


class TrBase(Base):
    def setUp(self):
        super().setUp()
        self.klines.upsert(self.symbol, "15m", td.real_m15_rows())

    def insert_legacy(self, tid=TR_ID, events=None, created=None, superseded=None, high=76976.7, low=75525.0,
                      since="2026-11-05 22:45"):
        created = created if created is not None else vn_ms("2026-11-08 08:18")
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            conn.execute(
                "INSERT INTO trading_ranges (id, symbol, timeframe, tr_high, tr_low, since, events, redraw_rule, "
                "superseded_by, status, created_at, updated_at) VALUES (?, ?, '15m', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, self.symbol, high, low, vn_ms(since), json.dumps(LEGACY_EVENTS if events is None else events),
                 REDRAW, superseded, "completed" if superseded else "hypothesis", created, created))
        return tid

    def migrated(self):
        self.insert_legacy()
        self.assertEqual(server._migrate_tr_events(), 6)
        return server.PLANS.active_tr(self.symbol)

    def doc_payload(self, **kw):
        self.migrated()
        out = server.update_tr(**{**DOC_PAYLOAD, **kw})
        self.assertTrue(out["changed"])
        return out

    def tr_count(self):
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            return conn.execute("SELECT COUNT(*) FROM trading_ranges").fetchone()[0]

    def event_count(self):
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            return conn.execute("SELECT COUNT(*) FROM tr_events").fetchone()[0]


class FixtureTest(unittest.TestCase):
    def test_m15_adds_up_to_real_h1(self):
        """Nen M15 chep tay: gop 4 nen phai ra dung nen H1 that cung gio (05/11 16:00 -> 07/11 15:00)."""
        h1 = {r[0]: r for r in td.real_h1_rows()}
        m15 = td.real_m15_rows()
        checked = 0
        for i in range(0, len(m15) - 3, 4):
            group = m15[i:i + 4]
            row = h1.get(group[0][0])
            if row is None:
                continue
            self.assertEqual(max(r[2] for r in group), row[2])
            self.assertEqual(min(r[3] for r in group), row[3])
            self.assertAlmostEqual(sum(r[5] for r in group), row[5], places=2)
            checked += 1
        self.assertEqual(checked, 48)


class MigrationTest(TrBase):
    def test_legacy_tr_gets_six_rows_with_vsa(self):
        """Nghiem thu: TR b9a9d6b7 co 6 dong tr_events (SC, AR, ST, SPRING, TEST, TEST), seq 1-6."""
        self.insert_legacy()
        self.assertEqual(server._migrate_tr_events(), 6)
        tr = server.PLANS.active_tr(self.symbol)
        self.assertEqual(tr["id"], TR_ID)
        self.assertEqual([e["type"] for e in tr["events"]], ["SC", "AR", "ST", "SPRING", "TEST", "TEST"])
        self.assertEqual([e["seq"] for e in tr["events"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual([e["side"] for e in tr["events"]], ["low", "high", "low", "low", "low", "low"])
        self.assertEqual({e["status"] for e in tr["events"]}, {"tentative"})
        self.assertEqual({e["created_by"] for e in tr["events"]}, {"claude"})
        self.assertEqual((tr["schematic"], tr["status"], tr["current_phase"]), ("undetermined", "hypothesis", None))
        spring = by_ref(tr, "SPRING#1")
        self.assertEqual(spring["t"], vn_ms("2026-11-06 22:00"))
        # vol / SMA20 cua 20 nen M15 truoc = 16180.055 / 2050.17. Tai lieu ghi "~9.8" theo cach tinh khac.
        self.assertAlmostEqual(spring["vol_ratio"], 7.89, places=2)
        self.assertEqual(spring["close_pct"], 99.2)
        self.assertAlmostEqual(by_ref(tr, "TEST#2")["vol_ratio"], 2.46, places=2)
        self.assertEqual(by_ref(tr, "TEST#2")["label"], "TEST 2")
        self.assertEqual(by_ref(tr, "SC#1")["label"], "SC")
        self.assertEqual(server._migrate_tr_events(), 0, "chay lai khong nhan doi")
        self.assertEqual(self.event_count(), 6)

    def test_sql_migration_sets_defaults_on_existing_rows(self):
        path = Path(tempfile.mkdtemp(prefix="trmig-", dir=td._TMP)) / "old.db"
        with mock.patch.object(sqlite_db, "MIGRATIONS", sqlite_db.MIGRATIONS[:7]):
            self.assertEqual(sqlite_db.migrate(path), 7)
        with sqlite_db.connect(path) as conn:
            for tid, superseded in (("old", "new"), ("new", None)):
                conn.execute("INSERT INTO trading_ranges (id, symbol, timeframe, tr_high, tr_low, since, events, "
                             "superseded_by, created_at, updated_at) VALUES (?, 'BTCUSDT', '15m', 2, 1, 0, '[]', ?, 0, 0)",
                             (tid, superseded))
        self.assertEqual(sqlite_db.migrate(path), len(sqlite_db.MIGRATIONS))
        with sqlite_db.connect(path) as conn:
            rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM trading_ranges")}
            columns = {r[1] for r in conn.execute("PRAGMA table_info(setups)")}
        self.assertEqual((rows["old"]["status"], rows["new"]["status"]), ("completed", "hypothesis"))
        self.assertEqual({r["schematic"] for r in rows.values()}, {"undetermined"})
        self.assertEqual({r["events_migrated"] for r in rows.values()}, {0})
        self.assertLessEqual({"tr_id", "event_ref", "expects", "phase"}, columns)

    def test_unmigrated_tr_still_readable(self):
        """Truoc khi chuyen, TR van doc duoc event tu cot json cu."""
        self.insert_legacy()
        tr = server.PLANS.active_tr(self.symbol)
        self.assertEqual([e["type"] for e in tr["events"]], ["SC", "AR", "ST", "SPRING", "TEST", "TEST"])
        self.assertEqual(tr["events"][0]["id"], "legacy-1")


class UpdateTrTest(TrBase):
    def test_doc_payload_three_phases_no_new_tr(self):
        """Nghiem thu: payload muc 5 -> 3 pha A/B/C lien nhau, current_phase = C, khong tao TR moi."""
        out = self.doc_payload()
        tr = out["tr"]
        self.assertEqual(tr["id"], TR_ID)
        self.assertEqual(self.tr_count(), 1)
        self.assertEqual([p["phase"] for p in tr["phases"]], ["A", "B", "C"])
        a, b, c = tr["phases"]
        self.assertEqual(a["start_t"], vn_ms("2026-11-05 22:45"))
        self.assertEqual(a["end_t"], b["start_t"])
        self.assertEqual(b["start_t"], vn_ms("2026-11-06 06:00"))
        self.assertEqual(b["end_t"], c["start_t"])
        self.assertEqual(c["start_t"], vn_ms("2026-11-06 22:00"))
        self.assertIsNone(c["end_t"])
        self.assertEqual(tr["current_phase"], "C")
        self.assertEqual((tr["schematic"], tr["status"]), ("accumulation", "hypothesis"))
        self.assertEqual(tr["levels"], {"creek": 76532.5, "ice": 75158.0})
        self.assertEqual([(e["ref"], e["status"]) for e in tr["events"] if e["type"] in ("SPRING", "TEST")],
                         [("SPRING#1", "confirmed"), ("TEST#1", "confirmed"), ("TEST#2", "tentative")])
        self.assertEqual(by_ref(tr, "ST#1")["phase"], "A", "event o bien pha thuoc pha thuong gap cua no")
        self.assertEqual(by_ref(tr, "SPRING#1")["phase"], "C")
        self.assertEqual(out["warnings"], [])
        self.assertEqual(len(out["updated"]), 3)
        self.assertIsInstance(out["plan_revision"], int)
        self.assertEqual(server.PLANS.snapshots(self.symbol, server._today())[-1]["trigger"], "update_tr")
        self.assertIn("tf=15m", out["dashboard_url"])

    def assert_rejected(self, pattern, **kw):
        before = (self.event_count(), json.dumps(server.PLANS.active_tr(self.symbol), default=str))
        with self.assertRaisesRegex(ValueError, pattern):
            server.update_tr(self.symbol, **kw)
        after = (self.event_count(), json.dumps(server.PLANS.active_tr(self.symbol), default=str))
        self.assertEqual(before, after, "bi tu choi thi khong ghi gi")

    def test_rejections(self):
        """Nghiem thu: t khong phai gio mo nen da dong, price ngoai nen, UTAD tren TR accumulation, pha
        chong nhau, sua tr_high."""
        self.doc_payload()
        self.assert_rejected("khong phai gio mo cua mot nen 15m da dong",
                             add_events=[{"type": "TEST", "t": "2026-11-07 00:50", "price": 75500}])
        self.assert_rejected("khong phai gio mo cua mot nen 15m da dong",
                             add_events=[{"type": "LPS", "t": "2026-11-09 10:00", "price": 75500}])
        self.assert_rejected(r"nam ngoai nen 15m 2026-11-07 01:00 \[75633, 75801.5\]",
                             add_events=[{"type": "LPS", "t": "2026-11-07 01:00", "price": 75000}])
        self.assert_rejected("UTAD.*khong khop schematic accumulation",
                             add_events=[{"type": "UTAD", "t": "2026-11-05 23:30", "price": 76957.8}])
        self.assert_rejected("chong len",
                             phases=[{"phase": "B", "end": "2026-11-06 23:00"}])
        self.assert_rejected("khong doi duoc tr_high", tr_high=77000)
        self.assert_rejected("khong doi duoc since", since="2026-11-05 23:00")
        self.assert_rejected("da co TEST", add_events=[{"type": "TEST", "t": "2026-11-07 00:45", "price": 75472.8}])
        self.assert_rejected("truoc since",
                             add_events=[{"type": "SC", "t": "2026-11-05 21:45", "price": 78011}])
        # Mot event hong la tu choi ca lan goi, ke ca event hop le di kem
        self.assert_rejected("khong phai gio mo", add_events=[
            {"type": "LPS", "t": "2026-11-07 04:30", "price": 75689},
            {"type": "LPS", "t": "2026-11-07 04:31", "price": 75689}])
        same = server.update_tr(self.symbol, tr_high=76976.7)
        self.assertFalse(same["changed"], "truyen dung neo cu thi khong phai doi neo")

    def test_ps_may_sit_before_since(self):
        self.migrated()
        out = server.update_tr(self.symbol, add_events=[{"type": "PS", "t": "2026-11-05 19:30", "price": 79640.5}])
        self.assertEqual(out["tr"]["events"][0]["type"], "PS")
        self.assertEqual([e["seq"] for e in out["tr"]["events"]], list(range(1, 8)))
        self.assertEqual(out["added"][0]["ref"], "PS#1")

    def test_utad_on_undetermined_accepted(self):
        self.migrated()
        out = server.update_tr(self.symbol, add_events=[{"type": "utad", "t": "2026-11-05 23:30", "price": 76957.8}])
        self.assertEqual(by_ref(out["tr"], "UTAD#1")["side"], "high")
        self.assertIn("UTAD#1", [a["ref"] for a in out["added"]])

    def test_schematic_change_checks_existing_events(self):
        self.migrated()
        server.update_tr(self.symbol, add_events=[{"type": "UTAD", "t": "2026-11-05 23:30", "price": 76957.8}])
        self.assert_rejected("UTAD.*khong khop schematic accumulation", schematic="accumulation")
        out = server.update_tr(self.symbol, schematic="accumulation",
                               update_events=[{"ref": "UTAD#1", "status": "failed", "note": "gan nham"}])
        self.assertEqual(out["tr"]["schematic"], "accumulation", "event da failed khong chan doi so do")

    def test_warnings_do_not_reject(self):
        self.doc_payload()
        out = server.update_tr(self.symbol, add_events=[
            {"type": "LPS", "t": "2026-11-07 04:30", "price": 75780},
            {"type": "SOS", "t": "2026-11-07 05:00", "price": 76182.5, "status": "confirmed"}])
        text = " | ".join(out["warnings"])
        self.assertIn("khac day nen 75689 qua 0.1%", text)
        self.assertIn("thuong thuoc pha D", text)
        self.assertIn("SOS confirmed nhung chua co LPS sau no", text)
        self.assertEqual(len(out["added"]), 2)

    def test_new_phase_closes_previous(self):
        self.doc_payload()
        out = server.update_tr(self.symbol, add_events=[{"type": "SOS", "t": "2026-11-07 05:00", "price": 76182.5}],
                               phases=[{"phase": "D", "start": {"event": "SOS#1"}}])
        phases = {p["phase"]: p for p in out["tr"]["phases"]}
        self.assertEqual(phases["C"]["end_t"], vn_ms("2026-11-07 05:00"))
        self.assertIsNone(phases["D"]["end_t"])
        self.assertEqual(out["tr"]["current_phase"], "D")
        self.assertEqual(sum(1 for p in out["tr"]["phases"] if p["end_t"] is None), 1)

    def test_refs_confirmed_by_and_noop(self):
        self.doc_payload()
        test1 = by_ref(server.PLANS.active_tr(self.symbol), "TEST#1")
        out = server.update_tr(self.symbol, update_events=[{"ref": "SPRING#1", "confirmed_by": "TEST#1"}])
        spring = by_ref(out["tr"], "SPRING#1")
        self.assertEqual(spring["confirmed_by"], test1["id"])
        self.assertEqual(spring["confirmed_by_label"], "TEST 1")
        again = server.update_tr(self.symbol, update_events=[{"ref": "SPRING#1", "confirmed_by": test1["id"]}])
        self.assertFalse(again["changed"])
        self.assertIsNone(again["plan_revision"], "khong doi gi thi khong sinh snapshot")
        with self.assertRaisesRegex(ValueError, "co 2 event TEST"):
            server.update_tr(self.symbol, update_events=[{"ref": "TEST", "status": "failed"}])
        with self.assertRaisesRegex(ValueError, "khong tim thay event 'TEST#9'"):
            server.update_tr(self.symbol, update_events=[{"ref": "TEST#9", "status": "failed"}])

    def test_no_tr_yet(self):
        with self.assertRaisesRegex(ValueError, "chua co TR khoa - tao TR qua save_scenarios"):
            server.update_tr(self.symbol, schematic="accumulation")

    def test_delete_only_within_24h(self):
        self.doc_payload()
        out = server.update_tr(self.symbol, add_events=[{"type": "LPS", "t": "2026-11-07 04:30", "price": 75689}],
                               update_events=[{"ref": "SPRING#1", "confirmed_by": "LPS#1"}])
        self.assertEqual(by_ref(out["tr"], "SPRING#1")["confirmed_by_label"], "LPS")
        with self.assertRaisesRegex(ValueError, "reason la bat buoc"):
            server.delete_tr_event(self.symbol, "LPS#1", " ")
        gone = server.delete_tr_event(self.symbol, "LPS#1", "gan nham nen")
        self.assertEqual(gone["deleted"]["type"], "LPS")
        self.assertEqual(gone["confirmed_by_cleared"], ["SPRING"])
        self.assertIsNone(by_ref(gone["tr"], "SPRING#1")["confirmed_by"])
        self.assertEqual([e["seq"] for e in gone["tr"]["events"]], list(range(1, 7)))
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            row = conn.execute("SELECT reason, event FROM tr_event_deletions").fetchone()
        self.assertEqual(row[0], "gan nham nen")
        self.assertEqual(json.loads(row[1])["type"], "LPS")
        # Event chuyen tu json cu mang created_at cua TR: lui 2 ngay la qua 24h
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            conn.execute("UPDATE tr_events SET created_at = ?", (int(time.time() * 1000) - 2 * 86_400_000,))
        with self.assertRaisesRegex(ValueError, "qua 24h.*status: 'failed'"):
            server.delete_tr_event(self.symbol, "SC#1", "thu")


class SaveScenariosMergeTest(TrBase):
    TR = {"timeframe": "15m", "tr_high": 76976.7, "tr_low": 75525, "since": "2026-11-05 22:45",
          "redraw_rule": REDRAW, "events": [dict(e) for e in LEGACY_EVENTS]}

    def save(self, tr, **kw):
        return server.save_scenarios(self.symbol, [], replace=False, bias_date="2026-11-08", tr=tr, **kw)

    def test_same_anchors_merge_into_active_tr(self):
        first = self.save(self.TR)["tr"]
        self.assertEqual([e["seq"] for e in first["events"]], list(range(1, 7)))
        resent = self.save(self.TR)
        self.assertEqual(resent["tr"]["id"], first["id"])
        self.assertIsNone(resent["plan_revision"], "gui lai dung danh sach cu la khong doi gi")
        more = self.save({**self.TR, "schematic": "accumulation", "levels": {"creek": 76532.5},
                          "events": self.TR["events"] + [{"type": "LPS", "t": "2026-11-07 04:30", "price": 75689,
                                                          "note": "giu day"}],
                          "phases": [{"phase": "C", "start": {"event": "SPRING#1"}}]})
        tr = more["tr"]
        self.assertEqual(tr["id"], first["id"])
        self.assertEqual(self.tr_count(), 1)
        self.assertEqual(len(tr["events"]), 7)
        self.assertEqual(by_ref(tr, "LPS#1")["note"], "giu day")
        self.assertEqual((tr["schematic"], tr["current_phase"], tr["levels"]), ("accumulation", "C", {"creek": 76532.5}))
        self.assertTrue(any("LPS" in w and "pha D" in w for w in more["warnings"]), more["warnings"])
        status = self.save({**self.TR, "events": [{**LEGACY_EVENTS[3], "status": "confirmed"}]})
        self.assertEqual(by_ref(status["tr"], "SPRING#1")["status"], "confirmed")
        self.assertEqual(len(status["tr"]["events"]), 7, "event khong gui lai thi giu nguyen")

    def test_new_anchors_new_tr_old_completed_or_failed(self):
        first = self.save(self.TR)["tr"]
        second = self.save({**self.TR, "tr_high": 77638.3, "events": []})["tr"]
        self.assertNotEqual(second["id"], first["id"])
        old = server.PLANS.tr(first["id"])
        self.assertEqual((old["superseded_by"], old["status"]), (second["id"], "completed"))
        third = self.save({**self.TR, "tr_low": 74810.8, "since": "2026-11-06 22:00", "events": []},
                          old_tr_status="failed")["tr"]
        self.assertEqual(server.PLANS.tr(second["id"])["status"], "failed")
        self.assertEqual(server.PLANS.active_tr(self.symbol)["id"], third["id"])
        with self.assertRaisesRegex(ValueError, "old_tr_status"):
            self.save({**self.TR, "tr_high": 78000}, old_tr_status="xyz")

    def test_bad_event_rejects_before_saving_anything(self):
        self.klines.upsert(self.symbol, "1h", td.real_h1_rows())
        with self.assertRaisesRegex(ValueError, "nam ngoai nen"):
            server.save_scenarios(self.symbol, [td.scenario()], bias_date="2026-11-07",
                                  tr={**self.TR, "events": [{"type": "SC", "t": "2026-11-05 22:45", "price": 70000}]})
        self.assertEqual(server.PLANS.scenarios(self.symbol, "2026-11-07"), [])
        self.assertEqual(self.tr_count(), 0)


class ChartTest(TrBase):
    def setUp(self):
        super().setUp()
        self.klines.upsert(self.symbol, "1h", td.real_h1_rows())
        self.klines.upsert(self.symbol, "4h", h4_rows())

    def wyckoff(self, out):
        return [m for m in out["markers"] if m["kind"] == "wyckoff"]

    def test_marker_bar_on_h1_and_h4(self):
        """Nghiem thu: marker SPRING tai nen H1 22:00 06/11, nen H4 19:00 06/11."""
        self.doc_payload()
        spring = vn_ms("2026-11-06 22:00")
        for tf, expected in (("1h", vn_ms("2026-11-06 22:00")), ("4h", vn_ms("2026-11-06 19:00")),
                             ("15m", spring)):
            out = run(server.get_chart_data(self.symbol, tf, 96, date="2026-11-08"))
            marker = next(m for m in self.wyckoff(out) if m["label"] == "SPRING")
            self.assertEqual(marker["t_bar"], expected, tf)
            self.assertEqual(marker["t"], spring)
            if tf != "15m":
                self.assertIn(expected, [c[0] for c in out["candles"]], f"nen {tf} co trong cua so")

    def test_chart_has_full_tr_and_six_markers(self):
        self.doc_payload()
        out = run(server.get_chart_data(self.symbol, "15m", 96, date="2026-11-08"))
        tr = out["tr"]
        self.assertEqual((tr["schematic"], tr["status"], tr["current_phase"]), ("accumulation", "hypothesis", "C"))
        self.assertEqual(tr["levels"], {"creek": 76532.5, "ice": 75158.0})
        self.assertEqual([p["phase"] for p in tr["phases"]], ["A", "B", "C"])
        markers = self.wyckoff(out)
        self.assertEqual([(m["label"], m["side"], m["status"]) for m in markers], [
            ("SC", "low", "tentative"), ("AR", "high", "tentative"), ("ST", "low", "tentative"),
            ("SPRING", "low", "confirmed"), ("TEST 1", "low", "confirmed"), ("TEST 2", "low", "tentative")])
        event = by_ref(tr, "TEST#2")
        for key in ("vol_ratio", "spread_ratio", "close_pct", "note", "seq", "tf"):
            self.assertIsNotNone(event[key], key)

    def test_superseded_tr_only_on_its_days(self):
        self.insert_legacy(tid="old", events=LEGACY_EVENTS[:2], created=vn_ms("2026-11-06 08:00"), superseded="new")
        self.insert_legacy(tid="new", events=LEGACY_EVENTS, created=vn_ms("2026-11-07 08:00"),
                           high=76976.71)
        server._migrate_tr_events()
        old_day = run(server.get_chart_data(self.symbol, "15m", 96, date="2026-11-06"))
        self.assertEqual(old_day["tr"]["id"], "old")
        self.assertEqual(len(self.wyckoff(old_day)), 2)
        self.assertEqual(old_day["tr"]["status"], "completed")
        new_day = run(server.get_chart_data(self.symbol, "15m", 96, date="2026-11-07"))
        self.assertEqual(new_day["tr"]["id"], "new")
        self.assertEqual(len(self.wyckoff(new_day)), 6)

    def test_old_snapshot_events_still_drawn(self):
        """Snapshot cu chi co events {type, t, price}: van ve duoc, co seq, side, nhan."""
        payload = {"bias": None, "setups": [], "scenarios": [], "tr": {
            "id": "x", "timeframe": "15m", "tr_high": 76976.7, "tr_low": 75525, "since": LEGACY_EVENTS[0]["t"],
            "events": LEGACY_EVENTS[:2] + [{"type": "TEST", "t": LEGACY_EVENTS[4]["t"], "price": 75472.8}],
            "redraw_rule": None}}
        server.PLANS.add_snapshot(self.symbol, "2026-11-07", "save_scenarios", payload)
        out = run(server.get_chart_data(self.symbol, "15m", 96, date="2026-11-07", revision=1))
        self.assertEqual([(e["seq"], e["side"], e["label"], e["status"]) for e in out["tr"]["events"]],
                         [(1, "low", "SC", "tentative"), (2, "high", "AR", "tentative"),
                          (3, "low", "TEST", "tentative")])
        self.assertEqual(len(self.wyckoff(out)), 3)
        self.assertEqual(out["tr"]["schematic"], "undetermined")

    def test_failed_event_and_old_revision(self):
        """Nghiem thu: doi event sang failed -> marker failed (trang an mac dinh); revision cu van ve nhu cu."""
        first = self.doc_payload()["plan_revision"]
        later = server.update_tr(self.symbol, update_events=[
            {"ref": "TEST#2", "status": "failed", "note": "dong duoi day test"}])["plan_revision"]
        day = server._today()
        live = run(server.get_chart_data(self.symbol, "15m", 96, date=day))
        self.assertEqual(next(m for m in self.wyckoff(live) if m["label"] == "TEST 2")["status"], "failed")
        old = run(server.get_chart_data(self.symbol, "15m", 96, date=day, revision=first))
        self.assertEqual(next(m for m in self.wyckoff(old) if m["label"] == "TEST 2")["status"], "tentative")
        newer = run(server.get_chart_data(self.symbol, "15m", 96, date=day, revision=later))
        self.assertEqual(by_ref(newer["tr"], "TEST#2")["note"], "dong duoi day test")

    def test_cache_drops_on_tr_change(self):
        self.doc_payload()
        args = (self.symbol, "15m", 96, "2026-11-08", False, None)
        first = server._chart_core(*args)
        self.assertIs(server._chart_core(*args), first)
        with mock.patch.object(server, "_plan_snapshot", return_value=None):
            server.update_tr(self.symbol, update_events=[{"ref": "TEST#2", "status": "confirmed"}])
        changed = server._chart_core(*args)
        self.assertIsNot(changed, first, "ghi TR thi bo cache ke ca khi snapshot loi")
        self.assertEqual(by_ref(changed["tr"], "TEST#2")["status"], "confirmed")


class SetupLinkTest(TrBase):
    SETUP = {**td.SETUP_A, "event_ref": "TEST#3", "expects": "LPS"}

    def test_setup_links_tr_phase_and_refs(self):
        """Nghiem thu: setup event_ref TEST#3 -> 'Pha C · giao dich TEST 3 · ky vong LPS'."""
        self.doc_payload()
        out = self.save_setups({**self.SETUP, "tr_high": 77000, "tr_since": "2026-11-05 22:45"})
        self.assertEqual(out["rejected"], [])
        saved = out["saved"][0]
        self.assertEqual((saved["tr_id"], saved["phase"], saved["event_ref"], saved["expects"]),
                         (TR_ID, "C", "TEST#3", "LPS"))
        self.assertTrue(any("tr_high 77000 khac TR lien ket 76976.7" in w for w in out["warnings"]), out["warnings"])
        row = server.SETUPS.get(saved["code"])
        self.assertEqual((row["tr_high"], row["tr_low"], row["tr_since"]),
                         (76976.7, 75525.0, vn_ms("2026-11-05 22:45")))
        view = server.get_setups(self.symbol, date="2026-11-07")["setups"][0]
        self.assertEqual((view["phase"], view["event_ref"], view["expects"]), ("C", "TEST#3", "LPS"))
        self.assertEqual(tr_logic.ref_label(view["event_ref"]), "TEST 3")

    def test_validation(self):
        self.doc_payload()
        out = self.save_setups({**self.SETUP, "event_ref": "FOO#1"}, {**self.SETUP, "event_ref": "TEST#0"},
                               {**self.SETUP, "expects": "XYZ"}, {**self.SETUP, "tr_id": "khong-co"})
        self.assertEqual([r["index"] for r in out["rejected"]], [0, 1, 2, 3])
        warn = self.save_setups({**self.SETUP, "event_ref": "UTAD", "direction": "long"})
        self.assertTrue(any("UTAD khong khop schematic accumulation" in w for w in warn["warnings"]))
        by_id = self.save_setups({**self.SETUP, "event_ref": by_ref(server.PLANS.active_tr(self.symbol), "TEST#2")["id"]})
        self.assertEqual(by_id["saved"][0]["event_ref"], "TEST#2")

    def test_no_tr_keeps_declared_values(self):
        out = self.save_setups({**self.SETUP, "tr_high": 77000})
        saved = out["saved"][0]
        self.assertEqual((saved["tr_id"], saved["phase"], saved["event_ref"]), (None, None, "TEST#3"))
        self.assertEqual(server.SETUPS.get(saved["code"])["tr_high"], 77000)

    def test_review_stats_group_by_phase(self):
        self.migrated()
        early = self.save_setups({**td.SETUP_A})["codes"][0]          # TR chua co pha
        server.update_tr(**DOC_PAYLOAD)
        late = self.save_setups(self.SETUP)["codes"][0]
        server.PLANS.save_review(server.SETUPS.get(late)["id"], outcome="tp_hit", r_multiple=2.0, note=None,
                                 reviewed_by="tan")
        stats = server.get_review_stats(self.symbol, "2026-11-01", "2026-11-30", group_by="phase")
        groups = {g["key"]: g for g in stats["groups"]}
        self.assertEqual(set(groups), {"C", "chua_co"})
        self.assertEqual((groups["C"]["setups"], groups["C"]["wins"]), (1, 1))
        cross = server.get_review_stats(self.symbol, "2026-11-01", "2026-11-30",
                                        group_by="direction,event_ref_type,phase")
        self.assertIn("long / TEST / C", [g["key"] for g in cross["groups"]])
        for key, expected in (("event_ref_type", {"TEST", "chua_co"}), ("expects", {"LPS", "chua_co"}),
                              ("schematic", {"accumulation"})):
            got = {g["key"] for g in server.get_review_stats(self.symbol, "2026-11-01", "2026-11-30",
                                                             group_by=key)["groups"]}
            self.assertEqual(got, expected, key)
        with self.assertRaisesRegex(ValueError, "group_by"):
            server.get_review_stats(self.symbol, group_by="phase,xyz")
        self.assertTrue(early)


class BiasPhaseTest(TrBase):
    def test_bias_phase_follows_tr(self):
        self.doc_payload()
        day = server._today()
        out = server.save_bias("neutral", "cho bien TR", symbol=self.symbol, date=day)
        self.assertEqual((out["phase"], out["warnings"]), ("C", []))
        self.assertEqual(server.get_bias(self.symbol, day)["phase"], "C")
        other = server.save_bias("neutral", "cho bien TR", symbol=self.symbol, phase="B", date=day)
        self.assertEqual(other["phase"], "B")
        self.assertIn("khac current_phase C", other["warnings"][0])


class LogicTest(unittest.TestCase):
    def test_labels_number_only_repeated_types(self):
        events = tr_logic.number_events([{"type": "TEST", "t": 3}, {"type": "SC", "t": 1}, {"type": "TEST", "t": 2}])
        self.assertEqual([(e["seq"], e["ref"], e["label"]) for e in events],
                         [(1, "SC#1", "SC"), (2, "TEST#1", "TEST 1"), (3, "TEST#2", "TEST 2")])

    def test_catalog_and_refs(self):
        self.assertEqual(tr_logic.event_type("msow"), "mSOW")
        self.assertEqual(tr_logic.side_of("AR_D"), "low")
        self.assertFalse(tr_logic.schematic_allows("reaccumulation", "UTAD"))
        self.assertTrue(tr_logic.schematic_allows("undetermined", "UTAD"))
        self.assertEqual(tr_logic.clean_event_ref("test#03"), "TEST#3")
        self.assertEqual(tr_logic.ref_type("TEST#3"), "TEST")

    def test_bar_open(self):
        opens = [0, 3_600_000, 7_200_000]
        self.assertEqual(tr_logic.bar_open(3_600_000 + M15 * 3, opens, H1), 3_600_000)
        self.assertEqual(tr_logic.bar_open(99 * H1 + 5, opens, H1), 99 * H1, "ngoai cua so thi lam tron theo khung")

    def test_diff_matches_by_type_and_time(self):
        before = {"id": "a", "tr_high": 2, "tr_low": 1, "since": 0, "events": [
            {"type": "SC", "t": 1, "price": 1}, {"type": "TEST", "t": 5, "price": 1, "status": "tentative"}]}
        after = {**before, "events": [{"type": "SC", "t": 1, "price": 1, "id": "e1"},
                                      {"type": "TEST", "t": 5, "price": 1, "status": "failed"},
                                      {"type": "LPS", "t": 9, "price": 1}]}
        diff = tr_logic.tr_diff(before, after)
        self.assertEqual([e["label"] for e in diff["added"]], ["LPS"])
        self.assertEqual([(e["label"], e["from"], e["to"]) for e in diff["status_changed"]],
                         [("TEST", "tentative", "failed")])
        self.assertFalse(diff["redrawn"])
        self.assertTrue(tr_logic.tr_diff(before, {**after, "id": "b", "tr_high": 3})["redrawn"])


class DashboardTrTest(HttpBase):
    def setUp(self):
        super().setUp()
        self.klines.upsert(self.symbol, "15m", td.real_m15_rows())

    def prepare(self):
        TrBase.insert_legacy(self)
        server._migrate_tr_events()
        server.update_tr(**{**DOC_PAYLOAD, "note": "NOTE-TR tich luy"})
        server.update_tr(self.symbol, add_events=[{"type": "LPS", "t": "2026-11-07 04:30", "price": 75689}],
                         update_events=[{"ref": "TEST#2", "status": "failed", "note": "NOTE-EVENT"}])

    def test_viewer_sees_phases_events_and_tr_card(self):
        """Nghiem thu: tai khoan viewer thay pha, event, the TR."""
        self.prepare()
        day = server._today()

        async def main(c):
            await self.login(c, "guest", VIEWER_PASS)
            chart = (await c.get(f"{BASE}/api/chart", params={"symbol": self.symbol, "date": "2026-11-08",
                                                               "tf": "15m", "bars": 96})).json()
            self.assertEqual([p["phase"] for p in chart["tr"]["phases"]], ["A", "B", "C"])
            self.assertEqual(len(chart["tr"]["events"]), 7)
            self.assertEqual(chart["tr"]["note"], "NOTE-TR tich luy")
            self.assertEqual(chart["tr"]["redraw_rule"], REDRAW)
            self.assertEqual(by_ref(chart["tr"], "TEST#2")["note"], "NOTE-EVENT")
            self.assertEqual(len([m for m in chart["markers"] if m["kind"] == "wyckoff"]), 7)
            changes = (await c.get(f"{BASE}/api/tr-changes", params={"symbol": self.symbol, "date": day})).json()
            self.assertEqual((changes["from_revision"], changes["to_revision"]), (1, 2))
            self.assertEqual([e["label"] for e in changes["added"]], ["LPS"])
            self.assertEqual([(e["label"], e["from"], e["to"]) for e in changes["status_changed"]],
                             [("TEST 2", "tentative", "failed")])
        self.session(main)

    def test_page_has_tr_card_and_layer_toggles(self):
        async def main(c):
            await self.login(c)
            page = (await c.get(BASE)).text
            # TM - #DASH-LAYOUT - Checkbox lop do JS sinh trong menu "Lop" o goc chart, tu danh sach LAYERS
            for marker in ('id="trCard"', 'id="layerMenu"', 'id="layerList"', 'id="popover"'):
                self.assertIn(marker, page)
            script = (await c.get(server._dash_asset_url("dashboard.js"))).text
            for marker in ("hoveredObjectId", "update_tr", "/tr-changes", '["phase", "Pha"', '["event", "Sự kiện Wyckoff"',
                           '["creek", "Creek/Ice"', '["failed", "Event hỏng", false]'):
                self.assertIn(marker, script)
        self.session(main)


if __name__ == "__main__":
    unittest.main()
