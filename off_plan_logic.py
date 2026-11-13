# TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
"""Logic thuan cua canh bao lenh ngoai ke hoach: phan loai, co, noi dung tin.

Khong goi san, khong doc DB. Server gom setup kem trang thai TAI LUC MO VI THE, TR, ket qua
check_trade roi goi cac ham o day - nen phat lai fills cu van cho dung ket qua nhu luc chay live.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

import entry_logic
import notifier
import tr_logic

IN_PLAN = "IN_PLAN"
CLASSIFICATIONS = ("IN_PLAN", "EARLY", "CHASE", "OUT_OF_ZONE", "COUNTER_PLAN", "NO_PLAN", "PRUNED_SETUP")
ADD_OFF_PLAN = "ADD_OFF_PLAN"
# Loai tin bat/tat duoc tren trang admin. IN_PLAN = tin "trong ke hoach nhung vi pham rule".
NOTIFY_KINDS = CLASSIFICATIONS + (ADD_OFF_PLAN,)
FLAGS = ("NO_STOP", "REVENGE", "RULE_BREACH", "AGAINST_TR_BRANCH", "REPEAT_OFF_PLAN", "SIZE_OVER_PLAN",
         ADD_OFF_PLAN)
BREACH_FLAGS = ("NO_STOP", "RULE_BREACH")
# Co phat sinh sau luc mo (thoi gian cho SL, lenh them): phan loai lai khong duoc xoa
KEEP_FLAGS = ("NO_STOP", ADD_OFF_PLAN)
VERDICTS = ("valid_unplanned", "discipline_breach", "execution_error", "misclassified")
REVIEW_STATUSES = ("PENDING", "REVIEWED")
EVENTS = ("OPENED", "ADDED", "NO_STOP", "STOPS", "CLOSED", "RECLASSIFIED", "REVIEWED")

# Setup da ket thuc tai luc mo vi the. CANCELLED khong nam day: setup bi huy (thuong do thay ke
# hoach) khong con la ke hoach, bo qua han.
ENDED = ("PRUNED", "INVALIDATED", "EXPIRED", "FILLED")
TRIGGERED = ("TRIGGERED", "CONFIRMED", "ENTRY_VALID", "BLOCKED")
NOT_TRIGGERED = ("WAITING", "IN_ZONE")
# Setup tien xa nhat duoc uu tien khi nhieu setup cung huong
RANK = {"ENTRY_VALID": 5, "CONFIRMED": 4, "BLOCKED": 4, "MISSED": 4, "TRIGGERED": 3, "IN_ZONE": 2,
        "WAITING": 1}
SIZE_TOLERANCE = 0.05
# Gio mo vi the (cTime) va fill dau tien lech nhau vai ms; quet lai thi khop theo cua so nay
MATCH_TOLERANCE_MS = 10_000
TF_LABEL = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "4h": "H4", "1d": "D1",
            "1w": "W1"}
CLASS_TEXT = {
    "IN_PLAN": "trong kế hoạch", "EARLY": "vào sớm, chưa có trigger", "CHASE": "đuổi giá",
    "OUT_OF_ZONE": "ngoài vùng setup", "COUNTER_PLAN": "ngược kế hoạch", "NO_PLAN": "không có kế hoạch",
    "PRUNED_SETUP": "theo setup đã kết thúc",
}


# ---------------------------------------------------------------- so va gio

def round2(value: Any) -> float | None:
    """Lam tron 2 chu so kieu ke toan (0.005 -> 0.01), khong theo float: -34.965 -> -34.97."""
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def price(value: Any) -> str:
    return "—" if value is None else entry_logic.fmt_price(value)


def money(value: Any) -> str:
    if value is None:
        return "—"
    number = round2(value)
    return ("−" if number < 0 else "") + f"{abs(number):.2f}"


def hms(ms: int | None, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz).strftime("%H:%M:%S") if ms else "—"


def local_day(ms: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz).strftime("%Y-%m-%d")


def code_prefix(symbol: str, day: str) -> str:
    """OP-{yyyymmdd}-{SYM}- ; so thu tu n do kho them vao."""
    return f"OP-{day.replace('-', '')}-{entry_logic.code_base(symbol)}-"


# ---------------------------------------------------------------- vi the tu fill

def summarize_group(group: dict[str, Any]) -> dict[str, Any]:
    """Mot nhom fill (server._group_fills_into_positions) -> vi the de doi chieu ke hoach.

    legs: moi lenh MO (order id) la mot chan, theo thu tu khop. Chan dau la lenh mo vi the, chan sau
    la lenh them. Fill tung phan cua cung mot lenh gop vao mot chan, khong phai lenh them.
    """
    fills = sorted(group.get("fills") or [], key=lambda f: int(f.get("ts") or 0))
    opening = [f for f in fills if abs(float(f.get("realized_pnl") or 0.0)) == 0]
    closing = [f for f in fills if abs(float(f.get("realized_pnl") or 0.0)) > 0]
    if not opening and fills and not group.get("carried_in"):
        # Dao chieu trong mot fill: fill dau vua dong vi the cu vua mo vi the nay (co realized_pnl)
        opening, closing = fills[:1], fills[1:]
    legs: dict[str, dict[str, Any]] = {}
    for fill in opening:
        key = str(fill.get("order_id") or f"fill-{fill.get('trade_id')}")
        leg = legs.setdefault(key, {"order_id": key, "at": int(fill.get("ts") or 0), "qty": 0.0, "cost": 0.0})
        leg["qty"] += float(fill.get("qty") or 0.0)
        leg["cost"] += float(fill.get("qty") or 0.0) * float(fill.get("price") or 0.0)
    out_legs = [{"order_id": leg["order_id"], "at": leg["at"], "qty": round(leg["qty"], 8),
                 "price": round(leg["cost"] / leg["qty"], 8) if leg["qty"] else None}
                for leg in sorted(legs.values(), key=lambda x: x["at"])]
    close_qty = sum(float(f.get("qty") or 0.0) for f in closing)
    return {
        "position_id": group.get("position_id"),
        "side": group.get("side"),
        "carried_in": bool(group.get("carried_in")),
        "closed": bool(group.get("closed")),
        "opened_at": int(fills[0]["ts"]) if fills else None,
        "closed_at": int(fills[-1]["ts"]) if fills and group.get("closed") else None,
        "legs": out_legs,
        "exit_avg": (round(sum(float(f["qty"]) * float(f["price"]) for f in closing) / close_qty, 8)
                     if close_qty else None),
        "realized_pnl": float(group.get("realized_pnl") or 0.0),
        "fee": float(group.get("fee") or 0.0),
    }


def legs_total(legs: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """(gia vao trung binh, tong khoi luong) cua cac chan."""
    qty = sum(float(leg["qty"]) for leg in legs if leg.get("price") is not None)
    if not qty:
        return None, None
    cost = sum(float(leg["qty"]) * float(leg["price"]) for leg in legs if leg.get("price") is not None)
    return round(cost / qty, 8), round(qty, 8)


def r_multiple(side: str, entry: float | None, exit_price: float | None, stop: float | None) -> float | None:
    """R = PnL / rui ro theo SL. Dong het o gia trung binh exit_price thi bang (exit - entry) / |entry - SL|,
    khong can gia tri hop dong cua san. Khong co SL thi khong tinh duoc."""
    if None in (entry, exit_price, stop) or float(entry) == float(stop):
        return None
    move = float(exit_price) - float(entry) if side == "long" else float(entry) - float(exit_price)
    return round2(move / abs(float(entry) - float(stop)))


def pick_stops(side: str, entry: float | None, orders: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """SL va TP that cua mot vi the tu lenh algo dang cho. Nhieu muc thi lay muc gan gia vao nhat
    (muc se khop truoc)."""
    exit_side = "sell" if side == "long" else "buy"
    stops: list[float] = []
    targets: list[float] = []
    for order in orders or []:
        pos_side = order.get("pos_side") or "net"
        if pos_side in ("long", "short") and pos_side != side:
            continue
        if pos_side == "net" and order.get("side") and order["side"] != exit_side:
            continue
        if order.get("sl"):
            stops.append(float(order["sl"]))
        if order.get("tp"):
            targets.append(float(order["tp"]))
        trigger = order.get("trigger")
        if trigger and entry is not None:
            below = float(trigger) < float(entry)
            (stops if below == (side == "long") else targets).append(float(trigger))
    ref = float(entry) if entry is not None else None

    def nearest(values: list[float]) -> float | None:
        if not values:
            return None
        return min(values, key=lambda v: abs(v - ref)) if ref is not None else values[0]

    return nearest(stops), nearest(targets)


# ---------------------------------------------------------------- setup tai luc mo vi the

def status_at(setup: dict[str, Any], events: list[dict[str, Any]], at: int, *,
              ignore_filled: bool = False) -> str | None:
    """Trang thai cua setup tai mot thoi diem, dung lai tu setup_events. None = setup chua ton tai.

    Quet bu sau khi mat ket noi phai cham theo ke hoach LUC DO, khong theo trang thai bay gio.
    ignore_filled: setup duoc gan tay (log_trade / reclassify) - FILLED la do chinh vi the nay.
    """
    if int(setup["created_at"]) > int(at):
        return None
    status = "WAITING"
    for event in sorted(events or [], key=lambda e: (int(e["at"]), int(e.get("id") or 0))):
        if int(event["at"]) > int(at):
            break
        if ignore_filled and event["event"] == "FILLED":
            continue
        if event.get("to_status"):
            status = event["to_status"]
    # Watcher chi danh EXPIRED o lan quet M5 sau: qua han la het han, khong cho event
    if status not in entry_logic.TERMINAL and int(at) > int(setup["expires_at"]):
        return "EXPIRED"
    return status


def _fold(text: Any) -> str:
    raw = str(text or "").replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(c)).lower()


_CAPITAL_RE = re.compile(r"goi y von\D{0,24}?(\d+(?:[.,]\d+)?)\s*%")
_RETEST_RE = re.compile(r"retest\D{0,30}?(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)")


def note_plan(note: Any) -> dict[str, Any]:
    """Quy uoc ghi trong note cua setup:
    - "Gợi ý vốn 80% hạn mức" (co the "tối đa 30%"): ti le von so voi max_margin_per_trade.
    - "Chia entry: ... retest LPSY 70635–70800 ...": ke hoach chia entry, phan sau chi vao trong vung retest.
    """
    text = _fold(note)
    match = _CAPITAL_RE.search(text)
    pct = float(match.group(1).replace(",", ".")) if match else None
    split = "chia entry" in text
    zone = None
    if split:
        found = _RETEST_RE.search(text[text.index("chia entry"):])
        if found:
            zone = sorted([float(found.group(1)), float(found.group(2))])
    return {"capital_pct": pct if pct is not None and 0 < pct <= 100 else None,
            "split_entry": split, "retest_zone": zone}


def candidate(setup: dict[str, Any], status: str, min_rr: float) -> dict[str, Any]:
    """Anh chup mot setup tai luc phan loai (luu vao candidate_setups)."""
    chase = (setup.get("state") or {}).get("max_chase")
    if chase is None:
        xm = entry_logic.x_max(setup["direction"], float(setup["entry"]), float(setup["stop"]),
                               float(setup["target"]), float(min_rr))
        chase = entry_logic.effective_max_chase(setup.get("max_chase"), xm)
    return {"id": setup["id"], "code": setup["code"], "label": setup.get("label") or entry_logic.setup_label(setup),
            "direction": setup["direction"], "status": status, "zone_low": float(setup["zone_low"]),
            "zone_high": float(setup["zone_high"]), "entry": float(setup["entry"]), "stop": float(setup["stop"]),
            "target": float(setup["target"]), "max_chase": round(max(float(chase), 0.0), 8),
            **note_plan(setup.get("note"))}


def _judge(cand: dict[str, Any], entry: float) -> tuple[str, str]:
    """Phan loai vi the theo MOT setup cung huong."""
    gap = float(entry) - cand["entry"]
    gap_text = ("+" if gap >= 0 else "−") + price(abs(gap))
    chase = cand["max_chase"]
    status = cand["status"]
    near = abs(gap) <= chase
    inside = cand["zone_low"] <= float(entry) <= cand["zone_high"]
    if status in TRIGGERED:
        if near:
            return IN_PLAN, (f"khớp {cand['code']} ({status}), giá vào lệch entry {price(cand['entry'])} "
                             f"{gap_text} trong max_chase {price(chase)}")
        return "CHASE", (f"{cand['code']} đã trigger ({status}) nhưng giá vào lệch entry {price(cand['entry'])} "
                         f"{gap_text} > max_chase {price(chase)}")
    if status == "MISSED":
        return "CHASE", f"{cand['code']} đã MISSED (giá chạy quá max_chase {price(chase)}) — vào là đuổi giá"
    if status in NOT_TRIGGERED:
        if inside or near:
            return "EARLY", f"{cand['code']} còn {status} — chưa có trigger"
        return "OUT_OF_ZONE", (f"giá vào ngoài vùng {price(cand['zone_low'])}–{price(cand['zone_high'])} của "
                               f"{cand['code']} và cách entry {price(cand['entry'])} {gap_text} > max_chase "
                               f"{price(chase)}")
    return "PRUNED_SETUP", f"{cand['code']} đã kết thúc ({status})"


def classify(side: str, entry: float, symbol: str, cands: list[dict[str, Any]],
             forced: dict[str, Any] | None = None) -> dict[str, Any]:
    """Phan loai vi the. cands: anh chup setup kem trang thai tai luc mo (da bo CANCELLED).

    Tra {classification, setup (ung vien khop | None), reason, listing (setup hien trong tin), listing_title}.
    """
    base = entry_logic.code_base(symbol)
    if forced is not None:
        kind, reason = _judge(forced, entry)
        return {"classification": kind, "setup": forced, "reason": f"gắn tay — {reason}", "listing": [forced],
                "listing_title": None}
    live = [c for c in cands if c["status"] not in ENDED]
    same = [c for c in live if c["direction"] == side]
    if same:
        best = max(same, key=lambda c: (RANK.get(c["status"], 0), -abs(c["entry"] - float(entry))))
        kind, reason = _judge(best, entry)
        return {"classification": kind, "setup": best, "reason": reason, "listing": [best], "listing_title": None}
    opposite = [c for c in live if c["direction"] != side]
    if opposite:
        other = "SHORT" if side == "long" else "LONG"
        return {"classification": "COUNTER_PLAN", "setup": None,
                "reason": f"kế hoạch {base} hôm nay chỉ có {other}", "listing": opposite,
                "listing_title": f"kế hoạch {base} hôm nay chỉ có {other}:"}
    ended = [c for c in cands if c["status"] in ENDED and c["direction"] == side]
    if ended:
        best = max(ended, key=lambda c: -abs(c["entry"] - float(entry)))
        return {"classification": "PRUNED_SETUP", "setup": best,
                "reason": "chỉ khớp setup đã kết thúc", "listing": ended,
                "listing_title": "chỉ khớp setup đã kết thúc:"}
    return {"classification": "NO_PLAN", "setup": None, "reason": f"không có setup nào cho {base} hôm nay",
            "listing": [], "listing_title": None}


def build_flags(*, classification: str, side: str, violations: list[str], tr: dict[str, Any] | None,
                nth: int, revenge: dict[str, Any] | None, size_over: dict[str, Any] | None,
                keep: list[str] | None = None) -> list[str]:
    found = set(f for f in keep or [] if f in KEEP_FLAGS)
    if revenge:
        found.add("REVENGE")
    if violations:
        found.add("RULE_BREACH")
    if tr and tr.get("branch") and side == tr_logic.COUNTER_DIRECTION.get(tr["branch"]):
        found.add("AGAINST_TR_BRANCH")
    if classification != IN_PLAN and nth >= 2:
        found.add("REPEAT_OFF_PLAN")
    if size_over:
        found.add("SIZE_OVER_PLAN")
    return [f for f in FLAGS if f in found]


def alertable(row: dict[str, Any]) -> bool:
    """Dong can Tan danh gia (co ma OP): ngoai ke hoach, hoac trong ke hoach ma pham rule / thieu SL /
    them vi the ngoai ke hoach."""
    flags = row.get("flags") or []
    return row["classification"] != IN_PLAN or any(f in flags for f in BREACH_FLAGS + (ADD_OFF_PLAN,))


# ---------------------------------------------------------------- noi dung tin

def tr_line(tr: dict[str, Any] | None) -> str | None:
    if not tr:
        return None
    tf = TF_LABEL.get(str(tr.get("timeframe") or ""), str(tr.get("timeframe") or "TR").upper())
    parts = [f"Pha {tr.get('phase') or '?'}"]
    if tr.get("prob_distribution") is not None or tr.get("prob_accumulation") is not None:
        parts.append(f"PP {tr.get('prob_distribution') if tr.get('prob_distribution') is not None else '?'}% / "
                     f"TL {tr.get('prob_accumulation') if tr.get('prob_accumulation') is not None else '?'}%")
    if tr.get("branch"):
        parts.append(f"nhánh {tr_logic.BRANCH_WORDS.get(tr['branch'], (tr['branch'],))[0]}")
    return f"TR {tf}: " + " · ".join(parts)


def _flag_text(flag: str, row: dict[str, Any]) -> str:
    explain = row.get("explain") or {}
    if flag == "RULE_BREACH":
        reasons = (row.get("rule_check") or {}).get("violations") or []
        return f"RULE_BREACH ({'; '.join(reasons)})" if reasons else flag
    if flag == "REVENGE" and explain.get("revenge"):
        rev = explain["revenge"]
        return (f"REVENGE ({rev.get('minutes')} phút sau {rev.get('ref')} đóng lỗ "
                f"{money(rev.get('pnl'))})")
    if flag == "AGAINST_TR_BRANCH" and (row.get("tr_snapshot") or {}).get("branch"):
        branch = row["tr_snapshot"]["branch"]
        return f"AGAINST_TR_BRANCH (TR nhánh {tr_logic.BRANCH_WORDS.get(branch, (branch,))[0]})"
    if flag == "SIZE_OVER_PLAN" and explain.get("size"):
        size = explain["size"]
        return (f"SIZE_OVER_PLAN (margin ${money(size.get('margin_usd'))} > gợi ý {size.get('capital_pct'):g}% × "
                f"{size.get('limit'):g} = ${money(size.get('suggested'))})")
    return flag


def _setup_bullet(cand: dict[str, Any]) -> str:
    return f"  • {cand.get('label') or cand.get('code')} ({cand.get('status')})"


def _footer(row: dict[str, Any], link: str | None) -> list[str]:
    lines = [f"👉 Mở Claude: \"đánh giá lệnh {row['code']}\""]
    if link:
        lines.append(f"📊 Dashboard: {link}")
    return lines


def _stop_line(row: dict[str, Any]) -> str | None:
    flags = row.get("flags") or []
    if "REVENGE" not in flags and "REPEAT_OFF_PLAN" not in flags:
        return None
    if row["classification"] == IN_PLAN:
        return "🛑 Mở lệnh ngay sau một lệnh lỗ — cân nhắc dừng giao dịch"
    nth = (row.get("explain") or {}).get("nth") or 1
    return f"🛑 Lệnh ngoài kế hoạch thứ {nth} hôm nay — cân nhắc dừng giao dịch"


def open_alert(row: dict[str, Any], tz: ZoneInfo, *, link: str | None = None) -> notifier.Alert:
    explain = row.get("explain") or {}
    kind = row["classification"]
    head = ("⚠️ TRONG KẾ HOẠCH NHƯNG VI PHẠM RULE" if kind == IN_PLAN else "⚠️ LỆNH NGOÀI KẾ HOẠCH")
    margin = f"${money(row['margin_usd'])}" if row.get("margin_usd") else "—"
    entry = explain.get("entry", row.get("entry_avg"))
    where = (f"{row['symbol']} · {row['side'].upper()} · vào {price(entry)} · margin {margin} · "
             f"{hms(row['opened_at'], tz)}")
    if explain.get("late_minutes"):
        where += f" · phát hiện trễ {explain['late_minutes']} phút"
    lines = [line for line in (_stop_line(row),) if line]
    lines += [f"{head} · {row['code']}", where]
    if explain.get("listing_title"):
        lines.append(f"Phân loại: {kind} — {explain['listing_title']}")
    else:
        lines.append(f"Phân loại: {kind} — {explain.get('reason') or CLASS_TEXT.get(kind, '')}")
    lines += [_setup_bullet(c) for c in explain.get("listing") or []]
    if explain.get("orb_reason"):
        lines.append(f"ORB: {explain['orb_reason']}")
    flags = row.get("flags") or []
    if flags:
        lines.append("Cờ: " + " · ".join(_flag_text(f, row) for f in flags))
    sl, tp = row.get("exchange_sl"), row.get("exchange_tp")
    if sl is not None or tp is not None:
        lines.append(f"SL sàn {price(sl)} · TP sàn {price(tp)}")
    line = tr_line(row.get("tr_snapshot"))
    if line:
        lines.append(line)
    lines += _footer(row, link)
    return notifier.Alert("\n".join(lines), title=f"{row['code']} {kind}",
                          meta={"off_plan_id": row["id"], "event": "OPENED"})


def add_alert(row: dict[str, Any], add: dict[str, Any], tz: ZoneInfo, *, link: str | None = None) -> notifier.Alert:
    lines = [f"⚠️ THÊM VỊ THẾ NGOÀI KẾ HOẠCH · {row['code']}",
             f"{row['symbol']} · {row['side'].upper()} · thêm {price(add.get('qty'))} @ {price(add.get('price'))} · "
             f"{hms(add.get('at'), tz)} (tổng {price(row.get('qty'))}, giá TB {price(row.get('entry_avg'))})",
             f"Phân loại: {ADD_OFF_PLAN} — {add.get('reason') or '—'}",
             f"Lệnh gốc: {row['classification']}"]
    lines += _footer(row, link)
    return notifier.Alert("\n".join(lines), title=f"{row['code']} {ADD_OFF_PLAN}",
                          meta={"off_plan_id": row["id"], "event": "ADDED"})


def no_stop_alert(row: dict[str, Any], tz: ZoneInfo, grace_seconds: int, *,
                  link: str | None = None) -> notifier.Alert:
    lines = [f"⚠️ CHƯA CÓ SL TRÊN SÀN · {row['code']}",
             f"{row['symbol']} · {row['side'].upper()} · vào {price(row.get('entry_avg'))} · mở "
             f"{hms(row['opened_at'], tz)} — sau {grace_seconds} giây vẫn không có lệnh SL",
             f"Phân loại: {row['classification']}"]
    lines += _footer(row, link)
    return notifier.Alert("\n".join(lines), title=f"{row['code']} NO_STOP",
                          meta={"off_plan_id": row["id"], "event": "NO_STOP"})


def close_alert(row: dict[str, Any]) -> notifier.Alert:
    if row.get("r_multiple") is not None:
        r_text = f"R thực tế {row['r_multiple']:+.2f}".replace("-", "−")
    else:
        r_text = "R: không có SL nên không tính được R"
    review = (f"đã đánh giá ({row['verdict']})" if row.get("review_status") == "REVIEWED"
              else "chưa đánh giá")
    text = (f"{row['code']} đã đóng · PnL thật {money(row.get('realized_pnl'))} (phí {money(row.get('fee'))}) · "
            f"{r_text} · {review}")
    return notifier.Alert(text, title=f"{row['code']} CLOSED", meta={"off_plan_id": row["id"], "event": "CLOSED"})
