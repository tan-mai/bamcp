# TM - #DASHBOARD - Setup dashboard
"""Logic thuan cua setup dashboard: kiem tra kich ban/TR, thong ke volume, muc HTF,
auto_outcome, thong ke review. Khong dong vao DB hay mang de test duoc doc lap.
"""
from __future__ import annotations

import math
from typing import Any, Callable
from urllib.parse import unquote

import entry_logic

SCENARIO_KINDS = ("main", "alt")
SCENARIO_STATUSES = ("active", "played_out", "failed", "cancelled")
WYCKOFF_EVENTS = ("SC", "AR", "ST", "SPRING", "TEST", "SOS", "LPS", "UTAD", "SOW", "LPSY")
OUTCOMES = ("tp_hit", "sl_hit", "be", "manual_exit", "not_triggered", "missed", "invalidated")
# Ket qua cua lenh da dong. Cac ket qua con lai la khong co lenh, khong tinh win rate.
CLOSED_OUTCOMES = ("tp_hit", "sl_hit", "be", "manual_exit")
GROUP_BY = ("trigger_type", "direction", "symbol")
MARKER_EVENTS = ("IN_ZONE", "TRIGGERED", "MISSED", "INVALIDATED", "EXPIRED")
VOL_PERIOD = 20
MAX_BARS = 500
MAX_OFFSET_BARS = 1000
START_PRICE_TOLERANCE = 0.02
PLAN_KEYS = ("bias", "setups", "scenarios", "tr")


def _positive(value: Any, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} phai la so, nhan duoc {value!r}") from None
    if not math.isfinite(out) or out <= 0:
        raise ValueError(f"{name} phai > 0")
    return out


def _point_time(raw: Any, parse_time: Callable[[Any], int | None],
                anchor: Callable[[str], tuple[int, int]]) -> tuple[int, dict[str, Any] | None]:
    """Tra ve (epoch ms, offset goc). Offset {bars, tf} tinh tu nen da dong cuoi luc luu."""
    if isinstance(raw, dict):
        tf = str(raw.get("tf") or "").strip().lower()
        try:
            bars = int(raw.get("bars"))
        except (TypeError, ValueError):
            raise ValueError(f"offset can {{bars, tf}}, nhan duoc {raw!r}") from None
        if abs(bars) > MAX_OFFSET_BARS:
            raise ValueError(f"offset toi da {MAX_OFFSET_BARS} nen, nhan duoc {bars}")
        last_open, span = anchor(tf)
        return last_open + bars * span, {"bars": bars, "tf": tf}
    try:
        moment = parse_time(raw)
    except ValueError:
        moment = None
    if moment is None:
        raise ValueError(f"thoi gian cua diem khong hop le: {raw!r}")
    return int(moment), None


def clean_points(raw: Any, parse_time: Callable[[Any], int | None],
                 anchor: Callable[[str], tuple[int, int]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("kich ban can it nhat 2 diem")
    points: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("moi diem phai la object {t, price, note?}")
        moment, offset = _point_time(item.get("t"), parse_time, anchor)
        point: dict[str, Any] = {"t": moment, "price": _positive(item.get("price"), "price")}
        if offset:
            point["offset"] = offset
        if str(item.get("note") or "").strip():
            point["note"] = str(item["note"]).strip()
        if points and moment <= points[-1]["t"]:
            raise ValueError("cac diem phai theo thoi gian tang dan")
        points.append(point)
    return points


def validate_scenario(raw: Any, *, parse_time: Callable[[Any], int | None],
                      anchor: Callable[[str], tuple[int, int]],
                      setup_exists: Callable[[str], bool]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("moi kich ban phai la object")
    kind = str(raw.get("kind") or "main").strip().lower()
    if kind not in SCENARIO_KINDS:
        raise ValueError(f"kind phai la mot trong {list(SCENARIO_KINDS)}")
    label = str(raw.get("label") or "").strip()
    if not label:
        raise ValueError("thieu label")
    codes = raw.get("setup_codes") or []
    if not isinstance(codes, list):
        raise ValueError("setup_codes phai la list")
    codes = [str(c).strip().upper() for c in codes if str(c).strip()]
    missing = [c for c in codes if not setup_exists(c)]
    if missing:
        raise ValueError(f"setup_codes khong ton tai: {missing}")
    return {"kind": kind, "label": label, "setup_codes": codes,
            "points": clean_points(raw.get("points"), parse_time, anchor),
            "condition": str(raw.get("condition") or "").strip() or None}


def start_warning(label: str, points: list[dict[str, Any]], last_close: float | None) -> str | None:
    """Diem dau xa gia dong cuoi: chi canh bao, khong tu choi."""
    if not last_close:
        return None
    gap = abs(float(points[0]["price"]) - last_close) / last_close
    if gap <= START_PRICE_TOLERANCE:
        return None
    return (f"{label}: diem dau {points[0]['price']:.10g} cach gia dong cuoi {last_close:.10g} "
            f"{gap * 100:.2f}% (> {START_PRICE_TOLERANCE * 100:g}%)")


def validate_tr(raw: Any, *, parse_time: Callable[[Any], int | None],
                timeframes: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("tr phai la object")
    timeframe = str(raw.get("timeframe") or "15m").strip().lower()
    if timeframe not in timeframes:
        raise ValueError(f"tr.timeframe phai la mot trong {timeframes}")
    high, low = _positive(raw.get("tr_high"), "tr_high"), _positive(raw.get("tr_low"), "tr_low")
    if low >= high:
        raise ValueError("tr_low phai nho hon tr_high")
    try:
        since = parse_time(raw.get("since"))
    except ValueError:
        since = None
    if since is None:
        raise ValueError(f"tr.since khong hop le: {raw.get('since')!r}")
    events = raw.get("events") or []
    if not isinstance(events, list):
        raise ValueError("tr.events phai la list")
    clean_events = []
    for item in events:
        if not isinstance(item, dict):
            raise ValueError("moi tr.event phai la object {type, t, price}")
        kind = str(item.get("type") or "").strip().upper()
        if kind not in WYCKOFF_EVENTS:
            raise ValueError(f"tr.event type phai la mot trong {list(WYCKOFF_EVENTS)}, nhan duoc {kind!r}")
        try:
            moment = parse_time(item.get("t"))
        except ValueError:
            moment = None
        if moment is None:
            raise ValueError(f"tr.event {kind}: thoi gian khong hop le")
        clean_events.append({"type": kind, "t": int(moment),
                             "price": _positive(item.get("price"), f"gia cua {kind}")})
    clean_events.sort(key=lambda e: e["t"])
    return {"timeframe": timeframe, "tr_high": high, "tr_low": low, "since": int(since),
            "events": clean_events, "redraw_rule": str(raw.get("redraw_rule") or "").strip() or None}


def tr_differs(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    if old is None:
        return True
    return any(old.get(k) != new.get(k)
               for k in ("timeframe", "tr_high", "tr_low", "since", "events", "redraw_rule"))


def volume_stats(closed: list[dict[str, Any]], count: int, mult: float, *,
                 period: int = VOL_PERIOD) -> tuple[list[float | None], list[int]]:
    """SMA volume cua `period` nen TRUOC moi nen (cung cach tinh voi check_trigger) cho `count`
    nen cuoi, kem danh sach open_time cua nen co vol >= mult x SMA. Chi xet nen da dong.
    """
    averages: list[float | None] = []
    spikes: list[int] = []
    for index in range(max(0, len(closed) - count), len(closed)):
        window = closed[max(0, index - period):index]
        if len(window) < period:
            averages.append(None)
            continue
        average = sum(float(b["volume"]) for b in window) / period
        averages.append(round(average, 4))
        if average > 0 and float(closed[index]["volume"]) >= float(mult) * average:
            spikes.append(int(closed[index]["open_time"]))
    return averages, spikes


def forming_from_lower(bars: list[dict[str, Any]], open_time: int) -> dict[str, Any] | None:
    """Ghep nen dang chay tu nen M5 da dong, dung khi kho nen khong luu nen chua dong."""
    rows = [b for b in bars if int(b["open_time"]) >= open_time]
    if not rows:
        return None
    return {"open_time": open_time, "open": float(rows[0]["open"]),
            "high": max(float(b["high"]) for b in rows), "low": min(float(b["low"]) for b in rows),
            "close": float(rows[-1]["close"]), "volume": sum(float(b["volume"]) for b in rows)}


def htf_levels(swings: dict[str, dict[str, list[dict[str, Any]]]], price: float | None,
               per_side: int = 2) -> list[dict[str, Any]]:
    """Swing W/D gan gia nhat, moi khung `per_side` muc phia tren va phia duoi gia."""
    if price is None:
        return []
    out: list[dict[str, Any]] = []
    for tf, block in swings.items():
        rows = [{"tf": tf, "kind": kind, "price": float(row["price"]), "time": row.get("time")}
                for kind, key in (("high", "swing_highs"), ("low", "swing_lows"))
                for row in block.get(key) or []]
        above = sorted({r["price"]: r for r in rows if r["price"] >= price}.values(),
                       key=lambda r: r["price"])[:per_side]
        below = sorted({r["price"]: r for r in rows if r["price"] < price}.values(),
                       key=lambda r: -r["price"])[:per_side]
        out.extend(above + below)
    return out


def origin_plan(snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Ke hoach goc: moi phan (bias, setups, scenarios, TR) lay ban DAU TIEN co noi dung.

    Quy trinh chuan la save_bias -> save_setups -> save_scenarios nen revision 1 thuong chi co
    bias. Ghep lai moi la ke hoach luc sang, ma revise ve sau khong lam doi duoc.
    """
    plan: dict[str, Any] = {}
    used: list[int] = []
    for snap in sorted(snapshots, key=lambda s: s["revision"]):
        payload = snap.get("payload") or {}
        taken = False
        for key in PLAN_KEYS:
            if key not in plan and payload.get(key):
                plan[key] = payload[key]
                taken = True
        if taken:
            used.append(int(snap["revision"]))
    if not used:
        return None
    return {**{k: plan.get(k) for k in PLAN_KEYS}, "revisions_used": used}


def trigger_window(setup: dict[str, Any], events: list[dict[str, Any]]
                   ) -> tuple[int, int | None] | None:
    """Khoang nen xet TP/SL: (nen chi tinh SAU moc nay theo close_time, moc cuoi hoac None).

    Lay TRIGGERED dau tien da qua confirm; TRIGGERED ma confirm hong thi bo qua.
    Lenh khop tu log_trade ma chua trigger thi tinh tu luc khop. None = chua co gi de xet.
    """
    start = None
    for event in events:
        name = event["event"]
        if name == "TRIGGERED":
            start = event.get("candle_open_time")
        elif name == "TRIGGER_FAILED":
            start = None
        elif name == "CONFIRMED" and start is not None:
            break
    final = next((e for e in reversed(events) if e.get("to_status") in entry_logic.TERMINAL), None)
    if setup["status"] == "FILLED":
        if start is None:
            start = int(final["at"]) if final else None
            return None if start is None else (start, None)
        return int(start), None
    if start is None:
        return None
    return int(start), int(final["at"]) if final else None


def auto_outcome(setup: dict[str, Any], events: list[dict[str, Any]],
                 bars: list[dict[str, Any]]) -> str | None:
    """Ket qua he thong tinh tu nen M5 khi setup da ket thuc. None = chua ket luan duoc.

    Cung mot nen cham ca TP lan SL thi tinh sl_hit (bao thu). FILLED chua cham moc nao thi tra
    None de lan sau tinh tiep: luc setup sang FILLED lenh moi bat dau.
    """
    status = setup["status"]
    if status not in entry_logic.TERMINAL:
        return None
    window = trigger_window(setup, events)
    if window is None:
        return "invalidated" if status == "INVALIDATED" else "not_triggered"
    after, until = window
    long = setup["direction"] == "long"
    stop, target = float(setup["stop"]), float(setup["target"])
    for bar in sorted(bars, key=lambda b: int(b["open_time"])):
        if int(bar["open_time"]) <= after:
            continue
        if until is not None and int(bar["close_time"]) > until:
            break
        high, low = float(bar["high"]), float(bar["low"])
        if (low <= stop) if long else (high >= stop):
            return "sl_hit"
        if (high >= target) if long else (low <= target):
            return "tp_hit"
    if status == "FILLED":
        return None
    if status == "INVALIDATED":
        return "invalidated"
    return "missed" if any(e["event"] == "MISSED" for e in events) else None


def scenario_status_for(outcome: str | None) -> str | None:
    """Ket qua review keo theo trang thai kich ban gan voi setup. Con lai giu nguyen."""
    return {"tp_hit": "played_out", "sl_hit": "failed", "invalidated": "failed"}.get(outcome or "")


def _stat_row(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    triggered = sum(1 for r in rows if r.get("triggered"))
    closed = [r for r in rows if r.get("outcome") in CLOSED_OUTCOMES]
    # Thoat tay hoac hoa von ma R duong van la thang; tp_hit thi luon la thang
    wins = sum(1 for r in closed if r["outcome"] == "tp_hit"
               or (r["outcome"] in ("be", "manual_exit") and float(r.get("r_multiple") or 0) > 0))
    rs = [float(r["r_multiple"]) for r in rows if r.get("r_multiple") is not None]
    outcomes: dict[str, int] = {}
    for row in rows:
        name = row.get("outcome") or "chua_co"
        outcomes[name] = outcomes.get(name, 0) + 1
    return {
        "key": key, "setups": len(rows), "triggered": triggered,
        "trigger_rate": round(triggered / len(rows) * 100, 1) if rows else None,
        "closed": len(closed), "wins": wins,
        "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None, "r_count": len(rs),
        "outcomes": outcomes,
    }


def review_stats(rows: list[dict[str, Any]], group_by: str) -> dict[str, Any]:
    if group_by not in GROUP_BY:
        raise ValueError(f"group_by phai la mot trong {list(GROUP_BY)}")
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[group_by]), []).append(row)
    return {"total": _stat_row("total", rows),
            "groups": [_stat_row(key, groups[key]) for key in sorted(groups)]}


def link_positions(groups: list[dict[str, Any]], setups: list[dict[str, Any]],
                   events: dict[str, list[dict[str, Any]]],
                   explicit: dict[str, list[str]]) -> dict[str, str]:
    """Gan vi the tren san voi setup: {position_id: code}.

    Uu tien lenh da luu trong review (exchange_fill_ids), roi den event FILLED: vi the watcher
    da khop (so moc mo vi the) hoac log_trade (vi the mo gan luc ghi nhat ky nhat, trong 1 gio).
    Khong khop duoc thi de trong: trang review danh dau la lenh khong gan setup.
    """
    links: dict[str, str] = {}
    by_code = {s["code"]: s for s in setups}
    for code, ids in explicit.items():
        for group in groups:
            if set(ids) & set(group.get("order_ids") or []):
                links[group["position_id"]] = code
    for setup in setups:
        if setup["status"] != "FILLED" or setup["code"] in links.values():
            continue
        event = next((e for e in reversed(events.get(setup["id"]) or []) if e["event"] == "FILLED"), None)
        if event is None:
            continue
        position = (event.get("details") or {}).get("position") or {}
        target = int(position.get("opened_ms") or event["at"])
        tolerance = 120_000 if position.get("opened_ms") else 3_600_000
        best = None
        for group in groups:
            if group["position_id"] in links or group.get("side") != by_code[setup["code"]]["direction"]:
                continue
            opened = group.get("opened_ms")
            if opened is None or abs(int(opened) - target) > tolerance:
                continue
            if best is None or abs(int(opened) - target) < abs(int(best["opened_ms"]) - target):
                best = group
        if best is not None:
            links[best["position_id"]] = setup["code"]
    return links


def safe_next(value: Any, base: str) -> str:
    """Chi nhan duong dan tuong doi nam trong base. Sai thi ve trang chinh (chong open redirect)."""
    text = str(value or "")
    if not text.startswith(base):
        return base
    rest = text[len(base):]
    if rest and rest[0] not in "/?#":
        return base
    path = unquote(rest.split("?", 1)[0].split("#", 1)[0])
    if "\\" in text or ".." in path or "//" in path or any(ord(c) < 32 for c in unquote(text)):
        return base
    if path.rstrip("/") in ("/login", "/logout"):
        return base
    return text
