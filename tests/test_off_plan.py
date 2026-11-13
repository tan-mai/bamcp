# TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
"""Nghiem thu #OFF-PLAN bang fill that ngay 13/11/2026 (OKX, BTCUSDT-P1..P3) va setup that ngay do.

Khong goi mang: san duoc gia lap bang ham read/stops tra fill da chuan hoa nhu adapter OKX, tin gui
vao danh sach thay vi Discord/Telegram.
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import closing, redirect_stderr
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="bamcp-op-")
os.environ.setdefault("BAMCP_DATA_ROOT", _TMP)
os.environ.setdefault("BAMCP_ACCOUNT_ENABLED", "false")

import admin_entry  # noqa: E402
import entry_logic  # noqa: E402
import entry_store  # noqa: E402
import entry_watcher  # noqa: E402
import off_plan_logic as opl  # noqa: E402
import off_plan_watcher  # noqa: E402
import server  # noqa: E402
import sqlite_db  # noqa: E402

VN = ZoneInfo("Asia/Ho_Chi_Minh")
TR_1311 = {"tr_id": "tr-1311", "timeframe": "15m", "phase": "B", "schematic": "undetermined", "branch": None,
           "prob_accumulation": 17, "prob_distribution": 83}

# Setup that ngay 13/11 (get_setups), tao luc 08:22:52
SOW = {"direction": "short", "zone_low": 70300, "zone_high": 70635.2, "trigger_type": "sow",
       "trigger_params": {"body_pct": 0.6, "level": 70635.2, "sma_period": 20, "vol_mult": 2},
       "entry": 70560, "stop": 71000, "target": 69500, "invalidate_level": 71550, "branch": "distribution",
       "entry_event": "SOW", "ref_event": "ST_B#1",
       "label": "BTC 13/11 · SHORT · PHÂN PHỐI · Pha B · SOW sau ST_B#1 · 70300–70635.2",
       "note": ("K3 · SOW: M15 đóng < 70635.2 vol ≥2x thân ≥60%. Chia entry: 1/2 khi nến SOW đóng, 1/2 chờ "
                "retest LPSY 70635–70800 từ dưới vol thấp bị từ chối. Gợi ý vốn 80% hạn mức (100 × 83%).")}
UTAD = {"direction": "short", "zone_low": 72194, "zone_high": 72650, "trigger_type": "utad",
        "trigger_params": {"ref_high": 72194, "sma_period": 20, "vol_mult": 2}, "entry": 72150, "stop": 72700,
        "target": 71100, "invalidate_level": 72650, "branch": "distribution", "entry_event": "UTAD",
        "ref_event": "AR#1", "label": "BTC 13/11 · SHORT · PHÂN PHỐI · Pha B · UTAD sau AR#1 · 72194–72650",
        "note": "K2 · UTAD. Gợi ý vốn 80% hạn mức."}
UT = {"direction": "short", "zone_low": 71950, "zone_high": 72194, "trigger_type": "rejection",
      "trigger_params": {"max_vol_mult": 0.8, "sma_period": 20, "wick_pct": 0.6}, "entry": 72050, "stop": 72350,
      "target": 71250, "invalidate_level": 72400, "branch": "range", "entry_event": "UT", "ref_event": "AR#1",
      "label": "BTC 13/11 · SHORT · RANGE · Pha B · UT sau AR#1 · 71950–72194",
      "note": "K5 · range pha B. Gợi ý vốn tối đa 30% hạn mức."}
SOS = {"direction": "long", "zone_low": 72194, "zone_high": 72450, "trigger_type": "sos",
       "trigger_params": {"body_pct": 0.6, "level": 72194, "sma_period": 20, "vol_mult": 2}, "entry": 72300,
       "stop": 71900, "target": 73100, "invalidate_level": 70600, "branch": "accumulation", "entry_event": "SOS",
       "label": "BTC 13/11 · LONG · TÍCH LUỸ · Pha B · SOS sau AR#1 · 72194–72450"}
SPRING = {"direction": "long", "zone_low": 70250, "zone_high": 70635.2, "trigger_type": "spring",
          "trigger_params": {"ref_low": 70635.2, "sma_period": 20, "vol_mult": 2}, "entry": 70700, "stop": 70200,
          "target": 71735, "invalidate_level": 70250, "branch": "accumulation", "entry_event": "SPRING",
          "label": "BTC 13/11 · LONG · TÍCH LUỸ · Pha B · SPRING sau ST_B#1 · 70250–70635.2"}
ST_B = {"direction": "long", "zone_low": 70635.2, "zone_high": 70950, "trigger_type": "rejection",
        "trigger_params": {"max_vol_mult": 0.8, "sma_period": 20, "wick_pct": 0.6}, "entry": 70800,
        "stop": 70550, "target": 71500, "invalidate_level": 70600, "branch": "range", "entry_event": "ST_B",
        "label": "BTC 13/11 · LONG · RANGE · Pha B · ST_B sau ST_B#1 · 70635.2–70950"}
TEST_DAYS = ("2026-11-12", "2026-11-13", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05")


def tearDownModule():
    if Path(server.DATA_ROOT).resolve() != Path(_TMP).resolve():
        shutil.rmtree(_TMP, ignore_errors=True)


def vn_ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=VN).timestamp() * 1000)


def fill(order: str, at: str, side: str, price: float, qty: float, pnl: float = 0.0, fee: float = -0.1,
         symbol: str = "BTCUSDT") -> dict:
    return {"trade_id": f"{order}-{at}-{qty}", "order_id": order, "ts": vn_ms(at), "side": side, "price": price,
            "qty": qty, "fee": fee, "fee_asset": "USDT", "realized_pnl": pnl, "symbol": symbol}


# Fill that 13/11 (get_fills), gop bot fill le cung lenh nhung giu dung tong khoi luong, PnL, phi
FILLS_1311 = [
    fill("4094675768835289089", "2026-11-13 08:27:27", "buy", 71103.3, 1.79, fee=-0.636),
    fill("4094675768835289089", "2026-11-13 08:27:27", "buy", 71103.3, 5.51, fee=-1.959),
    fill("4094675768835289089", "2026-11-13 08:27:28", "buy", 71103.3, 4.23, fee=-0.602),
    fill("4094752957584879617", "2026-11-13 09:05:47", "sell", 70800, 9.45, pnl=-28.657, fee=-3.3453),
    fill("4094752957584879617", "2026-11-13 09:05:47", "sell", 70800, 2.08, pnl=-6.308, fee=-0.7361),
    fill("4094794098472628225", "2026-11-13 09:26:13", "buy", 70550.2, 5.91, fee=-2.08475841),
    fill("4094807336367140865", "2026-11-13 09:32:48", "sell", 70288, 5.91, pnl=-15.49602, fee=-2.0770104),
    fill("4094811627408568321", "2026-11-13 09:34:56", "buy", 70411.4, 3.25, fee=-1.14418525),
    fill("4094845996575731713", "2026-11-13 09:52:00", "sell", 70100, 3.25, pnl=-10.1205, fee=-1.139125),
]


def wipe(symbols=("BTCUSDT", "ETHUSDT")) -> None:
    """Bang off-plan la cua rieng file test nay. Setup chi xoa cua cap/ngay dung trong test."""
    marks = ", ".join("?" for _ in TEST_DAYS)
    syms = ", ".join("?" for _ in symbols)
    with closing(sqlite_db.connect(server.SQLITE_PATH)) as conn, conn:
        conn.execute("DELETE FROM off_plan_events")
        conn.execute("DELETE FROM off_plan_trades")
        where = (f"symbol IN ({syms}) AND (bias_date IN ({marks}) OR status NOT IN "
                 f"({', '.join(repr(s) for s in entry_logic.TERMINAL)}))")
        conn.execute(f"DELETE FROM setup_events WHERE setup_id IN (SELECT id FROM setups WHERE {where})",
                     [*symbols, *TEST_DAYS])
        conn.execute(f"DELETE FROM setups WHERE {where}", [*symbols, *TEST_DAYS])


def make_setup(raw: dict, created: int, day: str, symbol: str = "BTCUSDT") -> dict:
    clean = entry_logic.validate(raw, min_rr=1.5, now_ms=created, parse_time=server._parse_moment,
                                 default_expiry=lambda d: server._parse_moment(d, end=True), default_day=day,
                                 default_m15=True)
    clean.update(entry_logic.validate_name(raw, clean["direction"]))
    clean["label"] = clean["label"] or entry_logic.setup_label({**clean, "symbol": symbol})
    with mock.patch.object(server.SETUPS, "now_ms", lambda: created):
        return server.SETUPS.insert(symbol, clean, "test")


def move(setup: dict, status: str, at: int, event: str | None = None, state: dict | None = None) -> dict:
    with mock.patch.object(server.SETUPS, "now_ms", lambda: at):
        server.SETUPS.apply(setup, status, {**setup["state"], **(state or {})},
                            [{"event": event or status, "from_status": setup["status"], "to_status": status,
                              "price": None, "candle_open_time": None, "details": {}}])
    return server.SETUPS.get(setup["id"])


class Base(unittest.TestCase):
    symbol = "BTCUSDT"

    def setUp(self):
        wipe()
        self.addCleanup(wipe)
        self.fills: list[dict] = []
        self.margins: dict[str, float] = {}
        self.stops: list[dict] | None = []
        self.down = False
        self.sent: list[str] = []
        self.now = 0
        for name, value in (("_op_orb", lambda symbol, side, at: None), ("_op_tr", lambda symbol, at: None)):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        async def read(symbol, start, end):
            if self.down:
                raise RuntimeError("okx: HTTP 503 - service unavailable")
            rows = [dict(f) for f in self.fills if f["symbol"] == symbol and start <= f["ts"] <= end]
            groups = server._fill_positions(rows, symbol)
            positions = []
            for g in groups:
                if g["closed"]:
                    continue
                entry, qty = opl.legs_total(opl.summarize_group(g)["legs"])
                positions.append({"symbol": symbol, "side": g["side"], "size": qty, "entry_price": entry,
                                  "opened_ms": int(g["fills"][0]["ts"]), "leverage": 100.0,
                                  "margin_usd": self.margins.get(g["fills"][0]["order_id"]), "close_algos": []})
            return {"positions": positions, "groups": groups}

        async def stops(symbol):
            if self.down:
                raise RuntimeError("okx: HTTP 503")
            return self.stops

        async def deliver(cfg, alert):
            self.sent.append(alert.text)
            return None

        self.watcher = off_plan_watcher.OffPlanWatcher(
            server.OP_STORE, cfg=server._op_cfg, notify_cfg=lambda: {"channel": "discord"}, active=lambda: True,
            symbols=lambda: [self.symbol], read=read, stops=stops, classify=server._op_classify,
            check_add=server._op_check_add, tz=server.TZ, display_tz=lambda: VN, now_ms=lambda: self.now,
            deliver=deliver)

    def poll(self, at: str) -> dict:
        self.now = vn_ms(at)

        async def main():
            out = await self.watcher.poll(self.now)
            await asyncio.gather(*list(self.watcher._tasks))
            return out
        with redirect_stderr(io.StringIO()):
            return asyncio.run(main())

    def row(self, position_id: str, day: str = "2026-11-13") -> dict:
        return next(r for r in server.OP_STORE.day_rows(day) if r["position_id"] == position_id)


class LogicTest(unittest.TestCase):
    def test_note_plan_reads_real_notes(self):
        self.assertEqual(opl.note_plan(SOW["note"]),
                         {"capital_pct": 80.0, "split_entry": True, "retest_zone": [70635.0, 70800.0]})
        self.assertEqual(opl.note_plan(UT["note"])["capital_pct"], 30.0)
        self.assertEqual(opl.note_plan("không ghi gì"), {"capital_pct": None, "split_entry": False, "retest_zone": None})

    def test_round_like_spec(self):
        self.assertEqual([opl.round2(v) for v in (-34.965, -15.49602, -10.1205)], [-34.97, -15.5, -10.12])
        self.assertEqual(opl.money(-34.965), "−34.97")

    def cand(self, **kw):
        base = {"id": "x", "code": "C", "label": "L", "direction": "short", "status": "WAITING",
                "zone_low": 70300.0, "zone_high": 70635.2, "entry": 70560.0, "stop": 71000.0, "target": 69500.0,
                "max_chase": 160.0, "capital_pct": None, "split_entry": False, "retest_zone": None}
        base.update(kw)
        return base

    def test_classify_table(self):
        cases = [
            ("short", 70600, [self.cand(status="CONFIRMED")], "IN_PLAN"),
            ("short", 70400, [self.cand(status="ENTRY_VALID")], "IN_PLAN"),
            ("short", 70300, [self.cand(status="TRIGGERED")], "CHASE"),
            ("short", 70560, [self.cand(status="MISSED")], "CHASE"),
            ("short", 70500, [self.cand(status="IN_ZONE")], "EARLY"),
            ("short", 71500, [self.cand(status="WAITING")], "OUT_OF_ZONE"),
            ("long", 71103.3, [self.cand()], "COUNTER_PLAN"),
            ("short", 70500, [self.cand(status="INVALIDATED")], "PRUNED_SETUP"),
            ("long", 70500, [self.cand(status="INVALIDATED")], "NO_PLAN"),
            ("long", 70500, [], "NO_PLAN"),
        ]
        for side, entry, cands, expected in cases:
            self.assertEqual(opl.classify(side, entry, "BTCUSDT", cands)["classification"], expected,
                             (side, entry, cands[0]["status"] if cands else None))
        # Nhieu setup cung huong: setup tien xa nhat thang, roi entry gan nhat
        out = opl.classify("short", 70600, "BTCUSDT", [self.cand(code="W", status="WAITING", entry=70600),
                                                      self.cand(code="T", status="CONFIRMED")])
        self.assertEqual(out["setup"]["code"], "T")
        self.assertEqual(out["classification"], "IN_PLAN")

    def test_status_at_rebuilds_history(self):
        setup = {"created_at": 100, "expires_at": 1000}
        events = [{"id": 1, "at": 100, "event": "CREATED", "to_status": "WAITING"},
                  {"id": 2, "at": 200, "event": "IN_ZONE", "to_status": "IN_ZONE"},
                  {"id": 3, "at": 300, "event": "INVALIDATED", "to_status": "INVALIDATED"}]
        self.assertIsNone(opl.status_at(setup, events, 50))
        self.assertEqual([opl.status_at(setup, events, t) for t in (150, 250, 350)],
                         ["WAITING", "IN_ZONE", "INVALIDATED"])
        self.assertEqual(opl.status_at(setup, events[:2], 1500), "EXPIRED")

    def test_pick_stops_net_and_hedge_modes(self):
        orders = [{"side": "sell", "pos_side": "net", "sl": 70900, "tp": 71800},
                  {"side": "buy", "pos_side": "net", "sl": 71500},            # lenh cua vi the short
                  {"side": "sell", "pos_side": "net", "trigger": 70850}]       # trigger duoi gia = SL
        self.assertEqual(opl.pick_stops("long", 71103.3, orders), (70900.0, 71800.0))
        self.assertEqual(opl.pick_stops("long", 71000, [{"side": "sell", "pos_side": "net", "trigger": 70950}]),
                         (70950.0, None))
        self.assertEqual(opl.pick_stops("short", 70500, [{"pos_side": "short", "sl": 70800}]), (70800.0, None))
        self.assertEqual(opl.r_multiple("long", 71103.3, 70800, 70900), -1.49)
        self.assertIsNone(opl.r_multiple("long", 71103.3, 70800, None))

    def test_flip_in_one_fill_opens_new_position_at_fill_price(self):
        fills = [fill("a", "2026-10-01 10:00:00", "buy", 70000, 1.0),
                 fill("b", "2026-10-01 10:05:00", "sell", 70100, 3.0, pnl=1.0),
                 fill("c", "2026-10-01 10:10:00", "buy", 70050, 2.0, pnl=0.5)]
        groups = server._fill_positions(fills, "BTCUSDT")
        flipped = opl.summarize_group(groups[1])
        self.assertEqual((flipped["side"], flipped["legs"][0]["price"], flipped["exit_avg"]), ("short", 70100, 70050))

    def test_partial_fills_of_one_order_are_one_leg(self):
        groups = server._fill_positions([dict(f) for f in FILLS_1311[:5]], "BTCUSDT")
        summary = opl.summarize_group(groups[0])
        self.assertEqual(len(summary["legs"]), 1)
        self.assertAlmostEqual(summary["legs"][0]["qty"], 11.53)
        self.assertEqual(summary["exit_avg"], 70800)


class Replay1311Test(Base):
    """Muc 9 (1-4, 8): phat lai fill 13/11. Ke hoach theo tien de cua ticket: chi co 3 setup SHORT."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(server, "_op_tr", lambda symbol, at: dict(TR_1311))
        patcher.start()
        self.addCleanup(patcher.stop)
        created = vn_ms("2026-11-13 08:22:52")
        self.codes = [make_setup(s, created, "2026-11-13")["code"] for s in (SOW, UTAD, UT)]
        self.margins = {"4094675768835289089": 15.0}

    def replay(self):
        timeline = ("2026-11-13 08:27:40", "2026-11-13 09:06:00", "2026-11-13 09:26:25", "2026-11-13 09:33:00",
                    "2026-11-13 09:35:10", "2026-11-13 09:52:10")
        self.fills = list(FILLS_1311)
        sent = []
        for at in timeline:
            before = len(self.sent)
            self.poll(at)
            sent.append(self.sent[before:])
        return sent

    def test_three_longs_against_short_plan(self):
        sent = self.replay()
        # (1) P1: mot tin trong lan poll ngay sau khi khop (08:27:27 -> poll 08:27:40, < 30 giay)
        self.assertEqual(len(sent[0]), 1)
        p1 = sent[0][0]
        self.assertEqual(p1.splitlines()[0], "⚠️ LỆNH NGOÀI KẾ HOẠCH · OP-20261113-BTC-1")
        self.assertEqual(p1.splitlines()[1], "BTCUSDT · LONG · vào 71103.3 · margin $15.00 · 08:27:27")
        self.assertIn("Phân loại: COUNTER_PLAN — kế hoạch BTC hôm nay chỉ có SHORT:", p1)
        self.assertIn("  • BTC 13/11 · SHORT · PHÂN PHỐI · Pha B · SOW sau ST_B#1 · 70300–70635.2 (WAITING)", p1)
        self.assertIn("  • BTC 13/11 · SHORT · PHÂN PHỐI · Pha B · UTAD sau AR#1 · 72194–72650 (WAITING)", p1)
        self.assertIn("  • BTC 13/11 · SHORT · RANGE · Pha B · UT sau AR#1 · 71950–72194 (WAITING)", p1)
        self.assertIn("TR M15: Pha B · PP 83% / TL 17%", p1)
        self.assertIn('👉 Mở Claude: "đánh giá lệnh OP-20261113-BTC-1"', p1)
        self.assertNotIn("🛑", p1)
        # (4) moi vi the dong -> tin ket qua voi PnL that
        self.assertEqual(sent[1], ["OP-20261113-BTC-1 đã đóng · PnL thật −34.97 (phí −7.28) · R: không có SL nên "
                                   "không tính được R · chưa đánh giá"])
        # (2) P2: COUNTER_PLAN + REVENGE (21 phut sau P1 dong lo) + REPEAT_OFF_PLAN, dong 🛑
        p2 = sent[2][0]
        self.assertEqual(p2.splitlines()[0], "🛑 Lệnh ngoài kế hoạch thứ 2 hôm nay — cân nhắc dừng giao dịch")
        self.assertIn("OP-20261113-BTC-2", p2.splitlines()[1])
        self.assertIn("COUNTER_PLAN", p2)
        self.assertIn("REVENGE (21 phút sau OP-20261113-BTC-1 đóng lỗ −34.97)", p2)
        self.assertIn("REPEAT_OFF_PLAN", p2)
        # Da cham daily stop (-20) sau P1: rule_check cung ham check_trade bao ra
        self.assertIn("RULE_BREACH (da cham daily_stop_loss: PnL hom nay -42.25 <= -20", p2)
        self.assertIn("đóng · PnL thật −15.50 (phí −4.16)", sent[3][0])
        # (3) P3: nhu P2, "thu 3", 2 phut sau P2 dong lo
        p3 = sent[4][0]
        self.assertEqual(p3.splitlines()[0], "🛑 Lệnh ngoài kế hoạch thứ 3 hôm nay — cân nhắc dừng giao dịch")
        self.assertIn("REVENGE (2 phút sau OP-20261113-BTC-2 đóng lỗ −15.50)", p3)
        self.assertIn("đóng · PnL thật −10.12 (phí −2.28)", sent[5][0])
        p1_row, p2_row, p3_row = (self.row(f"BTCUSDT-P{i}") for i in (1, 2, 3))
        self.assertEqual([r["classification"] for r in (p1_row, p2_row, p3_row)], ["COUNTER_PLAN"] * 3)
        self.assertEqual(p1_row["flags"], [])
        self.assertEqual(p2_row["flags"], ["REVENGE", "RULE_BREACH", "REPEAT_OFF_PLAN"])
        self.assertEqual(p3_row["flags"], ["REVENGE", "RULE_BREACH", "REPEAT_OFF_PLAN"])
        self.assertEqual(sorted(c["code"] for c in p1_row["candidate_setups"]), sorted(self.codes))
        self.assertEqual(p1_row["tr_snapshot"]["prob_distribution"], 83)
        # Poll lai khong gui them, khong tao them dong
        before = len(self.sent)
        self.poll("2026-11-13 09:52:30")
        self.assertEqual(len(self.sent), before)
        self.assertEqual(len(server.OP_STORE.day_rows("2026-11-13")), 3)

        # (8) get_review_stats tach 3 lenh ngoai ke hoach, tong PnL -60.59
        stats = server.get_review_stats(symbol="BTCUSDT", from_date="2026-11-13", to_date="2026-11-13")
        compare = stats["plan_compare"]
        self.assertEqual(compare["off_plan"]["trades"], 3)
        self.assertEqual(compare["off_plan"]["pnl"], -60.59)
        self.assertEqual(compare["off_plan"]["win_rate"], 0.0)
        self.assertEqual(compare["in_plan"]["trades"], 0)
        self.assertEqual([g["key"] for g in compare["by_classification"]], ["COUNTER_PLAN"])

        # Tool doc va danh gia
        listed = server.get_off_plan_trades(date="2026-11-13", symbol="BTCUSDT")
        self.assertEqual([t["code"] for t in listed["trades"]],
                         [f"OP-20261113-BTC-{i}" for i in (1, 2, 3)])
        self.assertEqual([e["event"] for e in listed["trades"][0]["events"]], ["OPENED", "CLOSED"])
        self.assertEqual(listed["trades"][0]["r_note"], "không có SL nên không tính được R")
        summary = server._op_summary("2026-11-13")
        self.assertEqual(summary["pending_review"], 3)
        out = server.review_off_plan_trade("OP-20261113-BTC-2", tan_reason="thấy rút chân ở 70550",
                                           verdict="discipline_breach", lesson="Đã chạm daily stop thì dừng.",
                                           add_to_plan=False)
        self.assertEqual(out["review_status"], "REVIEWED")
        again = server.review_off_plan_trade("OP-20261113-BTC-2", claude_assessment="Không có trigger nào.")
        self.assertEqual(again["trade"]["tan_reason"], "thấy rút chân ở 70550", "chi ghi de truong co truyen")
        self.assertEqual(again["trade"]["verdict"], "discipline_breach")
        with self.assertRaises(Exception):
            server.review_off_plan_trade("OP-20261113-BTC-2", verdict="sai")
        self.assertEqual(server._op_summary("2026-11-13")["pending_review"], 2)
        by_verdict = server.get_review_stats(symbol="BTCUSDT", from_date="2026-11-13",
                                             to_date="2026-11-13")["plan_compare"]["by_verdict"]
        self.assertEqual({g["key"]: g["trades"] for g in by_verdict}, {"chua_co": 2, "discipline_breach": 1})
        pending = server.get_off_plan_trades(date="2026-11-13", status="PENDING")
        self.assertEqual(pending["count"], 2)

    def test_markers_for_owner_chart(self):
        self.replay()
        markers = server._op_markers("BTCUSDT", "2026-11-13")
        self.assertEqual([(m["side"], m["price"], m["classification"]) for m in markers],
                         [("long", 71103.3, "COUNTER_PLAN"), ("long", 70550.2, "COUNTER_PLAN"),
                          ("long", 70411.4, "COUNTER_PLAN")])
        self.assertEqual(server._op_markers("ETHUSDT", "2026-11-13"), [])


class TimeAwareTest(Base):
    """Ke hoach THAT ngay 13/11 con 3 setup LONG (K1/K4/K5) tao cung luc 08:22:52, bi INVALIDATED luc
    09:15 va 09:45. Phan loai theo trang thai TAI LUC MO lenh, khong theo trang thai bay gio."""

    def test_real_plan_at_open_time(self):
        created = vn_ms("2026-11-13 08:22:52")
        made = {name: make_setup(raw, created, "2026-11-13")
                for name, raw in (("sow", SOW), ("utad", UTAD), ("ut", UT), ("sos", SOS), ("spring", SPRING),
                                  ("st_b", ST_B))}
        move(made["st_b"], "IN_ZONE", vn_ms("2026-11-13 09:00:20"))
        move(server.SETUPS.get(made["st_b"]["id"]), "INVALIDATED", vn_ms("2026-11-13 09:15:20"))
        move(made["sos"], "INVALIDATED", vn_ms("2026-11-13 09:15:20"))
        move(made["spring"], "IN_ZONE", vn_ms("2026-11-13 09:15:20"))
        move(server.SETUPS.get(made["spring"]["id"]), "INVALIDATED", vn_ms("2026-11-13 09:45:20"))
        self.fills = list(FILLS_1311)
        self.poll("2026-11-13 09:52:30")
        rows = {r["position_id"]: r for r in server.OP_STORE.day_rows("2026-11-13")}
        self.assertEqual(rows["BTCUSDT-P1"]["classification"], "OUT_OF_ZONE")
        self.assertIn("ngoài vùng 70635.2–70950", rows["BTCUSDT-P1"]["explain"]["reason"])
        self.assertEqual(rows["BTCUSDT-P2"]["classification"], "EARLY")
        self.assertIn("SPRING", rows["BTCUSDT-P2"]["explain"]["reason"])
        self.assertEqual(rows["BTCUSDT-P3"]["classification"], "EARLY")
        # Quet bu: tin ghi ro phat hien tre
        self.assertIn("phát hiện trễ 85 phút", self.sent[0])


class InPlanTest(Base):
    """Muc 9 (5): lenh short khop setup short sau trigger, trong max_chase -> IN_PLAN, khong gui tin."""
    day = "2026-10-01"

    def short_plan(self, **state):
        setup = make_setup({**SOW, "label": None}, vn_ms(f"{self.day} 08:00:00"), self.day)
        setup = move(setup, "TRIGGERED", vn_ms(f"{self.day} 09:00:20"))
        return move(setup, "CONFIRMED", vn_ms(f"{self.day} 09:00:20"), state=state or None)

    def test_in_plan_no_alert(self):
        self.short_plan()
        self.fills = [fill("o1", f"{self.day} 09:02:00", "sell", 70600, 2.0)]
        # SL 240 diem, TP 600 diem: trong rule mac dinh (max_stop 300, min_tp 500, min_rr 1.5)
        self.stops = [{"side": "buy", "pos_side": "net", "sl": 70840, "tp": 70000}]
        self.poll(f"{self.day} 09:02:15")
        row = self.row("BTCUSDT-P1", self.day)
        self.assertEqual(row["classification"], "IN_PLAN")
        self.assertIsNone(row["code"])
        self.assertEqual(row["flags"], [])
        self.assertEqual((row["exchange_sl"], row["exchange_tp"]), (70840.0, 70000.0))
        self.assertEqual(self.sent, [])
        # Dong lenh trong ke hoach: khong co ma thi khong gui tin ket qua, van tinh R cho thong ke
        self.fills.append(fill("o2", f"{self.day} 09:30:00", "buy", 70000, 2.0, pnl=12.0))
        self.poll(f"{self.day} 09:30:10")
        row = self.row("BTCUSDT-P1", self.day)
        self.assertEqual(self.sent, [])
        self.assertEqual(row["r_multiple"], 2.5)
        stats = server._op_compare(self.day, self.day, "BTCUSDT")
        self.assertEqual((stats["in_plan"]["trades"], stats["in_plan"]["avg_r"]), (1, 2.5))

    def test_chase_and_breach_alerts(self):
        self.short_plan()
        # Vuot max_chase (160) -> CHASE
        self.fills = [fill("o1", f"{self.day} 09:02:00", "sell", 70300, 2.0)]
        self.poll(f"{self.day} 09:02:10")
        self.assertEqual(self.row("BTCUSDT-P1", self.day)["classification"], "CHASE")
        self.assertIn("Phân loại: CHASE", self.sent[0])

    def test_in_plan_with_rule_breach_and_size(self):
        self.short_plan()
        self.fills = [fill("o1", f"{self.day} 09:02:00", "sell", 70600, 2.0)]
        self.margins = {"o1": 30.0}
        # SL rong 600 diem > max_stop_points 300, margin 30 > goi y 80% x 20
        self.stops = [{"side": "buy", "pos_side": "net", "sl": 71200, "tp": 69500}]
        self.poll(f"{self.day} 09:02:10")
        row = self.row("BTCUSDT-P1", self.day)
        self.assertEqual(row["classification"], "IN_PLAN")
        self.assertEqual(row["flags"], ["RULE_BREACH", "SIZE_OVER_PLAN"])
        self.assertTrue(row["code"].startswith("OP-20261001-BTC-"))
        self.assertTrue(self.sent[0].startswith("⚠️ TRONG KẾ HOẠCH NHƯNG VI PHẠM RULE · OP-20261001-BTC-1"))
        self.assertIn("SL 600.0 > max_stop_points (300)", self.sent[0])
        self.assertIn("SIZE_OVER_PLAN (margin $30.00 > gợi ý 80% × 20 = $16.00)", self.sent[0])


class NoStopTest(Base):
    """Muc 9 (6): vi the khong co SL sau 60 giay -> co NO_STOP, gui tin bo sung mot lan."""
    day = "2026-10-02"

    def test_no_stop_after_grace(self):
        self.fills = [fill("o1", f"{self.day} 10:00:00", "buy", 70000, 1.0)]
        self.poll(f"{self.day} 10:00:12")
        row = self.row("BTCUSDT-P1", self.day)
        self.assertEqual(row["classification"], "NO_PLAN")
        self.assertNotIn("NO_STOP", row["flags"])
        self.assertEqual(len(self.sent), 1)
        self.poll(f"{self.day} 10:00:45")
        self.assertEqual(len(self.sent), 1, "chua het thoi gian cho")
        self.poll(f"{self.day} 10:01:02")
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1].startswith(f"⚠️ CHƯA CÓ SL TRÊN SÀN · {row['code']}"))
        self.assertIn("sau 60 giây vẫn không có lệnh SL", self.sent[1])
        self.assertIn("NO_STOP", self.row("BTCUSDT-P1", self.day)["flags"])
        self.poll(f"{self.day} 10:01:20")
        self.assertEqual(len(self.sent), 2, "chi gui mot lan")
        # SL dat muon: ghi lai, giu co NO_STOP (su that da xay ra)
        self.stops = [{"side": "sell", "pos_side": "net", "sl": 69800}]
        self.poll(f"{self.day} 10:02:00")
        row = self.row("BTCUSDT-P1", self.day)
        self.assertEqual(row["exchange_sl"], 69800.0)
        self.assertIn("NO_STOP", row["flags"])

    def test_unsupported_exchange_never_flags(self):
        self.stops = None
        self.fills = [fill("o1", f"{self.day} 11:00:00", "buy", 70000, 1.0)]
        self.poll(f"{self.day} 11:00:10")
        self.poll(f"{self.day} 11:05:00")
        self.assertNotIn("NO_STOP", self.row("BTCUSDT-P1", self.day)["flags"])
        self.assertEqual(len(self.sent), 1)

    def test_in_plan_without_stop_gets_code_and_alert(self):
        setup = make_setup({**SOW, "label": None}, vn_ms(f"{self.day} 08:00:00"), self.day)
        move(move(setup, "TRIGGERED", vn_ms(f"{self.day} 09:00:20")), "CONFIRMED", vn_ms(f"{self.day} 09:00:20"))
        self.fills = [fill("o1", f"{self.day} 09:02:00", "sell", 70600, 1.0)]
        self.poll(f"{self.day} 09:02:10")
        self.assertEqual(self.sent, [])
        self.poll(f"{self.day} 09:03:05")
        row = self.row("BTCUSDT-P1", self.day)
        self.assertEqual((row["classification"], row["flags"]), ("IN_PLAN", ["NO_STOP"]))
        self.assertEqual(len(self.sent), 1)
        self.assertIn(row["code"], self.sent[0])


class RecoveryTest(Base):
    """Muc 9 (9): loi API san -> ghi log, khong gui tin sai; khoi phuc thi quet bu."""
    day = "2026-10-03"

    def test_outage_then_catch_up(self):
        self.fills = [fill("o1", f"{self.day} 10:00:00", "buy", 70000, 1.0)]
        self.down = True
        buf = io.StringIO()
        self.now = vn_ms(f"{self.day} 10:00:10")

        async def main():
            await self.watcher.poll(self.now)
            await self.watcher.poll(self.now + 15_000)
        with redirect_stderr(buf):
            asyncio.run(main())
        self.assertEqual(self.sent, [])
        self.assertEqual(server.OP_STORE.day_rows(self.day), [])
        self.assertEqual(buf.getvalue().count("khong doc duoc san BTCUSDT"), 1, "log mot lan, khong spam")
        self.assertIn("BTCUSDT", self.watcher.last_poll["errors"])
        self.down = False
        self.fills.append(fill("o2", f"{self.day} 10:04:00", "sell", 69900, 1.0, pnl=-1.0))
        self.poll(f"{self.day} 10:06:00")
        self.assertEqual(len(self.sent), 2)
        self.assertIn("phát hiện trễ 6 phút", self.sent[0])
        self.assertTrue(self.sent[1].endswith("chưa đánh giá"))
        self.assertEqual(self.watcher.last_poll["errors"], {})


class AddOnTest(Base):
    """Muc 3.4: phan them hop le khi theo ke hoach chia entry va nam trong vung retest."""
    day = "2026-10-04"

    def test_split_plan_retest(self):
        setup = make_setup({**SOW, "label": None}, vn_ms(f"{self.day} 08:00:00"), self.day)
        move(move(setup, "TRIGGERED", vn_ms(f"{self.day} 09:00:20")), "CONFIRMED", vn_ms(f"{self.day} 09:00:20"))
        self.stops = [{"side": "buy", "pos_side": "net", "sl": 70800, "tp": 70000}]
        self.fills = [fill("o1", f"{self.day} 09:02:00", "sell", 70560, 1.0)]
        self.poll(f"{self.day} 09:02:10")
        self.fills.append(fill("o2", f"{self.day} 09:40:00", "sell", 70700, 1.0))   # retest LPSY 70635-70800
        self.poll(f"{self.day} 09:40:10")
        row = self.row("BTCUSDT-P1", self.day)
        self.assertEqual(self.sent, [])
        self.assertEqual(row["orders"], ["o1", "o2"])
        self.assertEqual((row["qty"], row["entry_avg"]), (2.0, 70630.0))
        self.fills.append(fill("o3", f"{self.day} 09:50:00", "sell", 70450, 1.0))   # ngoai vung retest
        self.poll(f"{self.day} 09:50:10")
        row = self.row("BTCUSDT-P1", self.day)
        self.assertIn("ADD_OFF_PLAN", row["flags"])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith(f"⚠️ THÊM VỊ THẾ NGOÀI KẾ HOẠCH · {row['code']}"))
        self.assertIn("ngoài vùng retest 70635–70800", self.sent[0])
        events = server.OP_STORE.events([row["id"]])[row["id"]]
        self.assertEqual([(e["event"], e["details"].get("valid")) for e in events],
                         [("OPENED", None), ("ADDED", True), ("ADDED", False)])


class ReclassifyTest(Base):
    """Muc 9 (7): log_trade(setup_id) cho vi the NO_PLAN voi setup hop le -> RECLASSIFIED, IN_PLAN."""
    symbol = "ETHUSDT"

    @classmethod
    def setUpClass(cls):
        server.STORE.symbols(server.DEFAULT_SYMBOL)     # seed BTC truoc khi them cap
        cls.added = not server.STORE.has_symbol("ETHUSDT")
        if cls.added:
            server.STORE.add_symbol("ETHUSDT")

    @classmethod
    def tearDownClass(cls):
        if cls.added:
            server.STORE.remove_symbol("ETHUSDT")

    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: (server.JOURNAL_DIR / f"{server._today()}.json").unlink(missing_ok=True))

    def test_log_trade_reclassifies(self):
        now = int(time.time() * 1000)
        opened = now - 120_000
        day = opl.local_day(opened, server.TZ)
        stamp = datetime.fromtimestamp(opened / 1000, VN).strftime("%Y-%m-%d %H:%M:%S")
        self.fills = [fill("e1", stamp, "buy", 3000, 1.0, symbol="ETHUSDT")]
        self.stops = [{"side": "sell", "pos_side": "net", "sl": 2950, "tp": 3100}]
        self.poll(datetime.fromtimestamp((opened + 10_000) / 1000, VN).strftime("%Y-%m-%d %H:%M:%S"))
        row = self.row("ETHUSDT-P1", day)
        self.assertEqual(row["classification"], "NO_PLAN")
        # Ke hoach luu muon (sau luc mo), setup da trigger + confirm
        raw = {"direction": "long", "zone_low": 2980, "zone_high": 3010, "trigger_type": "close_above",
               "entry": 3000, "stop": 2950, "target": 3100, "invalidate_level": 2940, "branch": "accumulation",
               "entry_event": "SOS", "expires_at": now + 3_600_000}
        setup = make_setup(raw, now - 60_000, day, symbol="ETHUSDT")
        setup = move(move(setup, "TRIGGERED", now - 50_000), "CONFIRMED", now - 50_000)
        with mock.patch.object(server.WATCHER, "notify_threadsafe", lambda event_id: None):
            out = server.log_trade(side="long", entry=3000, stop=2950, target=3100, margin_usd=10,
                                   symbol="ETHUSDT", setup_id=setup["code"])
        self.assertEqual(out["setup"]["status"], "FILLED")
        self.assertEqual(out["off_plan"]["from"], "NO_PLAN")
        self.assertEqual(out["off_plan"]["classification"], "IN_PLAN")
        row = server.OP_STORE.get(row["id"])
        self.assertEqual((row["classification"], row["matched_setup_id"]), ("IN_PLAN", setup["id"]))
        events = [e["event"] for e in server.OP_STORE.events([row["id"]])[row["id"]]]
        self.assertEqual(events, ["OPENED", "RECLASSIFIED"])
        # reclassify_position: setup nguoc huong bi tu choi
        with self.assertRaises(Exception):
            server.reclassify_position(row["code"], make_setup({**SOW, "label": None, "zone_low": 3050,
                                                                "zone_high": 3080, "entry": 3060, "stop": 3100,
                                                                "target": 2950, "invalidate_level": 3120,
                                                                "expires_at": now + 3_600_000},
                                                               now - 60_000, day, symbol="ETHUSDT")["code"])

    def test_journal_link_before_watcher_sees_position(self):
        now = int(time.time() * 1000)
        day = opl.local_day(now, server.TZ)
        raw = {"direction": "long", "zone_low": 2980, "zone_high": 3010, "trigger_type": "close_above",
               "entry": 3000, "stop": 2950, "target": 3100, "invalidate_level": 2940, "branch": "accumulation",
               "entry_event": "SOS", "expires_at": now + 3_600_000}
        setup = make_setup(raw, now - 600_000, day, symbol="ETHUSDT")
        move(setup, "TRIGGERED", now - 500_000)
        with mock.patch.object(server.WATCHER, "notify_threadsafe", lambda event_id: None):
            server.log_trade(side="long", entry=3000, stop=2950, target=3100, margin_usd=10, symbol="ETHUSDT",
                             setup_id=setup["code"])
        stamp = datetime.fromtimestamp((now - 30_000) / 1000, VN).strftime("%Y-%m-%d %H:%M:%S")
        self.fills = [fill("e1", stamp, "buy", 3005, 1.0, symbol="ETHUSDT")]
        self.poll(datetime.fromtimestamp(now / 1000, VN).strftime("%Y-%m-%d %H:%M:%S"))
        row = self.row("ETHUSDT-P1", day)
        self.assertEqual((row["classification"], row["matched_setup_id"]), ("IN_PLAN", setup["id"]))
        self.assertEqual(row["explain"]["forced_by"], "log_trade")


class OrbTest(Base):
    """Muc 3.5: vi the khop phien ORB dang chay -> IN_PLAN (rule_set orb); ngoai window -> NO_PLAN kem ly do."""
    day = "2026-10-05"

    def test_orb_match_and_reason(self):
        with mock.patch.object(server, "_op_orb", lambda s, side, at: {"match": True, "session_id": "ny",
                                                                        "or_date": self.day,
                                                                        "reason": "phiên ny phá long trong trade window"}):
            self.fills = [fill("o1", f"{self.day} 21:00:00", "buy", 70000, 1.0)]
            self.poll(f"{self.day} 21:00:10")
        row = self.row("BTCUSDT-P1", self.day)
        self.assertEqual((row["classification"], row["rule_set"]), ("IN_PLAN", "orb"))
        self.assertEqual(self.sent, [])
        with mock.patch.object(server, "_op_orb", lambda s, side, at: {"match": False,
                                                                        "reason": "phiên ny ngoài trade window (20:30–22:30)"}):
            self.fills.append(fill("o2", f"{self.day} 21:10:00", "sell", 70100, 1.0, pnl=1.0))
            self.fills.append(fill("o3", f"{self.day} 23:00:00", "buy", 70000, 1.0))
            self.poll(f"{self.day} 23:00:10")
        self.assertEqual(self.row("BTCUSDT-P2", self.day)["classification"], "NO_PLAN")
        self.assertIn("ORB: phiên ny ngoài trade window (20:30–22:30)", self.sent[0])


class WiringTest(unittest.TestCase):
    def test_entry_watcher_skips_unplanned_when_off_plan_runs(self):
        folder = Path(tempfile.mkdtemp(prefix="bamcp-op-ew-"))
        self.addCleanup(shutil.rmtree, folder, True)
        store = entry_store.SetupStore(folder / "db.sqlite")
        clean = entry_logic.validate(
            {"direction": "long", "zone_low": 67200, "zone_high": 67450, "trigger_type": "spring",
             "entry": 67500, "stop": 67050, "target": 68400, "invalidate_level": 67000},
            min_rr=1.5, now_ms=1, parse_time=server._parse_moment, default_expiry=lambda d: 10 ** 13,
            default_day="2026-10-01", default_m15=False)
        row = store.insert("BTCUSDT", clean, "test")
        store.apply(row, "TRIGGERED", {"triggered_at": 1000}, [])

        async def positions(symbol):
            return [{"symbol": "BTCUSDT", "side": "short", "size": 1, "entry_price": 1, "opened_ms": 5000}]

        def make(flag):
            return entry_watcher.EntryWatcher(store, klines=lambda: None, cfg=lambda: {}, min_rr=lambda s: 1.5,
                                              check=lambda *a: {}, positions=positions, tz=VN, span_ms=server._tf_ms,
                                              unplanned=flag)
        self.assertEqual(asyncio.run(make(lambda: False).match_positions("BTCUSDT")), [])
        self.assertEqual(len(asyncio.run(make(lambda: True).match_positions("BTCUSDT"))), 1)

    def test_admin_settings_validation_and_page(self):
        good = {"enabled": True, "poll_seconds": "15", "no_stop_grace_seconds": 60, "revenge_window_minutes": 30,
                "channel": "", "notify_classifications": ["COUNTER_PLAN", "IN_PLAN"]}
        self.assertEqual(server._off_plan_clean(good)["notify_classifications"], ["IN_PLAN", "COUNTER_PLAN"])
        for bad in ({"poll_seconds": 2}, {"channel": "email"}, {"notify_classifications": ["X"]},
                    {"no_stop_grace_seconds": "a"}):
            with self.assertRaises(ValueError, msg=bad):
                server._off_plan_clean({**good, **bad})
        page = admin_entry.render(server._alerts_view("", ""), action_path="/a", admin_path="/admin",
                                  schedules_path="/s", setup_required=False)
        self.assertIn("Off-plan · lệnh ngoài kế hoạch", page)
        self.assertIn('data-op-kind="COUNTER_PLAN" checked', page)
        self.assertNotIn("__OP_", page)

    def test_evaluate_trade_without_stop_or_target(self):
        rules = server._load_rules("BTCUSDT")
        out = server._evaluate_trade(symbol="BTCUSDT", side="long", entry=70000, stop=None, target=None,
                                     margin_usd=0, trade_type="scalp", trades=[], rules=rules, realized=0)
        self.assertEqual((out["stop_points"], out["target_points"], out["rr"]), (None, None, None))
        self.assertEqual(out["rule_violations"], [])


if __name__ == "__main__":
    unittest.main()
