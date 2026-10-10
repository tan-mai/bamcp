# TM - #GANN-PL - Gann Price Levels
"""Moc gia Gann: Square of 9, va cac ham dung chung voi backtest. Logic thuan, khong I/O.

Tool MCP chi doc du lieu, goi ham o day, dong goi JSON - backtest goi lai dung
nhung ham nay, nen cai duoc kiem chung la cai dang chay that.

Quy uoc nen giong server._normalize_bars; chi truyen nen DA DONG truoc as_of.
Pivot truyen vao la pivot da biet tai as_of (gann_pivots.live_pivots), kem khoa
'date' dang 'YYYY-MM-DD'.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import gann_pivots

SQ9_STEPS = (45, 90, 180, 360)
# Moi zone toi da bay nhieu nguon trong output - giu JSON duoi ~8 KB.
MAX_SOURCES = 3
# Canh bao anchor dieu hoa toi da bay nhieu dong, phan con lai gom thanh mot dong.
MAX_HARMONIC_WARNINGS = 5


# ---------------------------------------------------------------- dung chung

def tick_decimals(symbol: str, configured: dict[str, Any] | None, price: float) -> int:
    """So le khi lam tron gia. Config truoc; khong co thi doan theo do lon cua gia."""
    table = configured or {}
    if symbol in table:
        return int(table[symbol])
    if price >= 1000:
        return 1
    if price >= 10:
        return 2
    if price >= 1:
        return 3
    return 4


def distance(price: float, ref: float, atr: float | None, decimals: int) -> dict[str, Any]:
    """Khoang cach CO DAU tu ref den price: duong = phia tren, am = phia duoi."""
    diff = float(price) - float(ref)
    return {
        "dist_pts": round(diff, decimals),
        "dist_pct": round(diff / ref * 100, 2) if ref else None,
        "dist_atr": round(diff / atr, 2) if atr else None,
    }


def atr_value(bars: list[dict[str, Any]], period: int) -> tuple[float | None, int]:
    """(ATR Wilder tai nen cuoi, so nen thuc su dung). It nen hon period thi lay
    trung binh TR cua nhung nen dang co - nguoi goi phai canh bao khi so nen < period.
    """
    if not bars:
        return None, 0
    period = max(1, int(period))
    if len(bars) >= period:
        return gann_pivots.atr_series(bars, period)[-1], period
    return gann_pivots.atr_series(bars, len(bars))[-1], len(bars)


def leg_extremes(state: dict[str, Any] | None, bars: list[dict[str, Any]],
                 dates: list[str]) -> dict[str, Any] | None:
    """Dau va cuc tri cua chan song dang chay, KEM NGAY cua nen cuc tri.

    gann_pivots.swing_state chi tra gia extreme_so_far; ngay cua no phai tim lai
    tren nen. Tim o day thay vi them truong vao swing_state, de output cua
    get_swing_state giu nguyen.
    """
    leg = (state or {}).get("current_leg")
    if not leg:
        return None
    pivot = leg["from_pivot"]
    index = next((i for i, bar in enumerate(bars)
                  if int(bar["open_time"]) == int(pivot["time_ms"])), None)
    if index is None:
        return None
    up = leg["direction"] == "up"
    best = index
    for i in range(index, len(bars)):
        if up and float(bars[i]["high"]) > float(bars[best]["high"]):
            best = i
        elif not up and float(bars[i]["low"]) < float(bars[best]["low"]):
            best = i
    price = float(bars[best]["high"] if up else bars[best]["low"])
    return {
        "direction": leg["direction"],
        "from": {"type": pivot["type"], "price": float(pivot["price"]), "date": dates[index],
                 "level": pivot.get("level") or "minor", "bar_index": index},
        "extreme": {"type": "high" if up else "low", "price": price, "date": dates[best],
                    "bar_index": best},
    }


def fmt_price(price: float, decimals: int) -> str:
    """Gia cho cau note: 81.6k voi gia lon, nguyen so le voi gia nho (ADA)."""
    if price >= 1000:
        return f"{price / 1000:.1f}k"
    return f"{price:.{decimals}f}"


# ---------------------------------------------------------------- cham / phan ung

def _touched(bar: dict[str, Any], low: float, high: float, tol: float) -> bool:
    return float(bar["low"]) <= high + tol and float(bar["high"]) >= low - tol


def touch_events(bars: list[dict[str, Any]], low: float, high: float, atr: float,
                 rule: dict[str, Any], first: int = 0,
                 last: int | None = None) -> list[dict[str, Any]]:
    """Cac lan gia cham vung [low, high] trong bars[first..last], kem ket cuc.

    Dung chung cho tool live va backtest - mot dinh nghia "cham / phan ung / pha".
      cham     : low <= high + touch_atr*ATR va high >= low - touch_atr*ATR.
                 Cac nen cham cach nhau khong qua reaction_bars la mot lan.
      tiep can : close nen truoc lan cham tren vung = test ho tro; duoi = khang cu.
      phan ung : trong reaction_bars nen ke tu nen cham, gia di nguoc huong tiep
                 can >= reaction_atr*ATR tinh tu mep vung, chua co nen dong pha.
      pha      : close vuot mep ben kia >= break_close_atr*ATR.
    Nen sau `last` duoc dung de xet ket cuc (backtest), khong de tim lan cham moi.
    Thieu nen de ket luan thi outcome = "none", complete = False.
    """
    if not bars or atr is None or atr <= 0:
        return []
    last = len(bars) - 1 if last is None else min(int(last), len(bars) - 1)
    tol = float(rule.get("touch_atr", 0.15)) * atr
    react = float(rule.get("reaction_atr", 1.0)) * atr
    brk = float(rule.get("break_close_atr", 0.25)) * atr
    span = max(1, int(rule.get("reaction_bars", 3)))

    events: list[dict[str, Any]] = []
    i = max(0, int(first))
    while i <= last:
        if not _touched(bars[i], low, high, tol):
            i += 1
            continue
        start = end = i
        j = i + 1
        while j <= last and j - end <= span:
            if _touched(bars[j], low, high, tol):
                end = j
            j += 1

        before = float(bars[start - 1]["close"]) if start > 0 else None
        if before is None:
            approach = None
        elif before > high:
            approach = "support"
        elif before < low:
            approach = "resistance"
        else:
            approach = "support" if before >= (low + high) / 2 else "resistance"

        outcome, at = "none", None
        stop = min(start + span, len(bars) - 1)
        for k in range(start, stop + 1):
            bar = bars[k]
            close = float(bar["close"])
            # Nen cham: high/low cua no co the da xay ra TRUOC luc cham (vd mo 118
            # roi rot xuong vung 100) - chi close la bang chung. Tu nen sau tro di
            # moi tinh high/low.
            top = close if k == start else float(bar["high"])
            bottom = close if k == start else float(bar["low"])
            if approach == "support":
                if close <= low - brk:
                    outcome, at = "broken", k
                    break
                if top - high >= react:
                    outcome, at = "held", k
                    break
            elif approach == "resistance":
                if close >= high + brk:
                    outcome, at = "broken", k
                    break
                if low - bottom >= react:
                    outcome, at = "held", k
                    break
        events.append({"start": start, "end": end, "approach": approach,
                       "outcome": outcome, "outcome_at": at,
                       "complete": outcome != "none" or start + span <= len(bars) - 1})
        i = end + 1
    return events


def zone_history(bars: list[dict[str, Any]], dates: list[str], low: float, high: float,
                 atr: float, rule: dict[str, Any], first: int) -> dict[str, Any]:
    """Tom tat lich su phan ung tai mot vung (Buoc 5 cua get_sq9_levels)."""
    events = touch_events(bars, low, high, atr, rule, first)
    decided = [e for e in events if e["outcome"] != "none"]
    last = events[-1] if events else None
    return {
        "touches": len(events),
        "reactions": sum(1 for e in events if e["outcome"] == "held"),
        "breaks": sum(1 for e in events if e["outcome"] == "broken"),
        "last_touch": dates[last["end"]] if last else None,
        "last_outcome": last["outcome"] if last else "none",
        # Lan co ket cuc gan nhat la bi pha: vai tro cua vung da doi chieu
        "flipped": bool(decided and decided[-1]["outcome"] == "broken"),
    }


# ---------------------------------------------------------------- Square of 9

def sq9_price(anchor: float, deg: float) -> float | None:
    """L(theta) = (sqrt(A) + theta/180)^2. 360 do = +-2 tren can. Can <= 0 -> None."""
    root = math.sqrt(anchor) + deg / 180.0
    return root * root if root > 0 else None


def angle_key(deg: float) -> str:
    """Khoa trong so goc theo |deg| mod 360: 0 / 180 / 90 (90, 270) / 45 (con lai)."""
    rest = abs(int(round(deg))) % 360
    if rest == 0:
        return "0"
    if rest == 180:
        return "180"
    if rest in (90, 270):
        return "90"
    return "45"


def sq9_ladder(anchor: float, step: int, max_rotations: int,
               low: float, high: float) -> list[tuple[int, float]]:
    """Cac muc (deg, gia) tu mot anchor, trong [low, high]. Bo theta = 0."""
    count = int(360 * max(0, int(max_rotations)) // int(step))
    out = []
    for k in range(-count, count + 1):
        if k == 0:
            continue
        deg = k * int(step)
        price = sq9_price(anchor, deg)
        if price is not None and low <= price <= high:
            out.append((deg, price))
    return out


def select_anchors(pivots: list[dict[str, Any]], leg: dict[str, Any] | None,
                   as_of: date, cfg: dict[str, Any],
                   level_weights: dict[str, float]) -> list[dict[str, Any]]:
    """Chon anchor tu dong (Buoc 1).

    Pivot trong lookback_days, sap theo trong so bac roi do moi, giu max_anchors.
    Dau va dinh/day cua chan song dang chay LUON duoc them (include_leg_extremes),
    khong bi cat boi max_anchors - chung la moc gan gia nhat, cat di thi nhung muc
    lien quan truc tiep toi nhip hien tai bien mat.
    """
    lookback = int(cfg.get("lookback_days") or 400)
    keep = max(1, int(cfg.get("max_anchors") or 6))
    candidates: dict[float, dict[str, Any]] = {}
    for pivot in pivots:
        moment = date.fromisoformat(pivot["date"])
        if (as_of - moment).days > lookback:
            continue
        item = {"price": float(pivot["price"]), "type": pivot["type"], "date": pivot["date"],
                "level": pivot.get("level") or "minor", "source": "pivot"}
        known = candidates.get(item["price"])
        if known is None or level_weights.get(item["level"], 0) > level_weights.get(known["level"], 0):
            candidates[item["price"]] = item
    # Trong so bac giam dan; cung trong so thi moi hon truoc
    ranked = sorted(candidates.values(),
                    key=lambda a: (-level_weights.get(a["level"], 0),
                                   -date.fromisoformat(a["date"]).toordinal()))
    chosen = ranked[:keep]

    if leg and cfg.get("include_leg_extremes", True):
        for extreme in (leg.get("from"), leg.get("extreme")):
            if not extreme or any(abs(a["price"] - extreme["price"]) < 1e-9 for a in chosen):
                continue
            known = candidates.get(float(extreme["price"]))
            chosen.append(dict(known) if known else {
                "price": float(extreme["price"]), "type": extreme["type"],
                "date": extreme["date"], "level": "minor", "source": "leg_extreme"})
    return chosen


def harmonic_groups(anchors: list[dict[str, Any]], step: int,
                    tol_root: float) -> tuple[list[int], list[tuple[int, int, float]]]:
    """Nhom anchor dieu hoa (Buoc 3). Tra ve (nhom cua tung anchor, cac cap).

    |sqrt(A1) - sqrt(A2)| gan mot boi so cua step/180 thi hai anchor sinh ra CUNG
    mot day muc - zone trung nhau do cau truc, khong phai hoi tu doc lap.
    """
    unit = int(step) / 180.0
    roots = [math.sqrt(a["price"]) for a in anchors]
    parent = list(range(len(anchors)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = []
    for i in range(len(anchors)):
        for j in range(i + 1, len(anchors)):
            gap = abs(roots[i] - roots[j])
            steps = round(gap / unit)
            if abs(gap - steps * unit) <= tol_root:
                pairs.append((i, j, gap / 2.0))      # 2 tren can = 1 vong 360
                parent[find(j)] = find(i)
    return [find(i) for i in range(len(anchors))], pairs


def build_zones(levels: list[dict[str, Any]], merge_dist: float) -> list[list[dict[str, Any]]]:
    """Gop single-linkage: hai muc ke nhau cach <= merge_dist thi chung zone."""
    ordered = sorted(levels, key=lambda lv: lv["price"])
    zones: list[list[dict[str, Any]]] = []
    for level in ordered:
        if zones and level["price"] - zones[-1][-1]["price"] <= merge_dist:
            zones[-1].append(level)
        else:
            zones.append([level])
    return zones


def anchor_label(anchor: dict[str, Any]) -> str:
    if anchor.get("source") == "manual":
        return f"manual {anchor['price']}"
    return f"{anchor['type']} {anchor['date']} {anchor['price']} {anchor['level']}"


def score_zone(members: list[dict[str, Any]], groups: list[int],
               anchors: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """Diem zone: moi anchor gop MOT muc (diem cao nhat); trong mot nhom dieu hoa
    chi tinh diem cao nhat cua nhom, khong cong don.
    """
    best: dict[int, dict[str, Any]] = {}
    for level in members:
        known = best.get(level["anchor"])
        if known is None or level["score"] > known["score"]:
            best[level["anchor"]] = level
    leader: dict[int, dict[str, Any]] = {}
    for idx, level in best.items():
        group = groups[idx]
        if group not in leader or level["score"] > leader[group]["score"]:
            leader[group] = level
    total = sum(level["score"] for level in leader.values())
    sources = []
    for idx, level in sorted(best.items(), key=lambda kv: -kv[1]["score"]):
        head = leader[groups[idx]]
        row = {"anchor": anchor_label(anchors[idx]), "deg": level["deg"],
               "price": level["price"], "score": round(level["score"], 3)}
        if head is not level:
            row["harmonic_of"] = str(anchors[head["anchor"]]["price"])
        sources.append(row)
    return total, sources


def sq9_analysis(*, anchors: list[dict[str, Any]], bars: list[dict[str, Any]],
                 dates: list[str], ref_price: float, atr: float, cfg: dict[str, Any],
                 reaction: dict[str, Any],
                 level_weights: dict[str, float], step: int, max_rotations: int,
                 range_pct: float, max_zones: int, decimals: int) -> dict[str, Any]:
    """Ca day Square of 9: sinh muc -> dieu hoa -> gop zone -> lich su -> chon."""
    angle_weights = {str(k): float(v) for k, v in (cfg.get("angle_weights") or {}).items()}
    low = ref_price * (1 - range_pct / 100)
    high = ref_price * (1 + range_pct / 100)
    warnings: list[str] = []

    levels = []
    for idx, anchor in enumerate(anchors):
        weight = level_weights.get(anchor["level"], 0.0)
        for deg, price in sq9_ladder(anchor["price"], step, max_rotations, low, high):
            levels.append({"anchor": idx, "deg": deg, "price": price,
                           "score": weight * angle_weights.get(angle_key(deg), 0.0)})

    tol_root = float(cfg.get("harmonic_anchor_tol_atr", 0.15)) * atr / (2 * math.sqrt(ref_price))
    groups, pairs = harmonic_groups(anchors, step, tol_root)
    for i, j, turns in pairs[:MAX_HARMONIC_WARNINGS]:
        warnings.append(
            f"2 anchor {anchors[i]['price']} va {anchors[j]['price']} cach nhau ~{turns:.0f} "
            "vong - cac zone trung do cau truc, khong phai hoi tu doc lap")
    if len(pairs) > MAX_HARMONIC_WARNINGS:
        warnings.append(f"con {len(pairs) - MAX_HARMONIC_WARNINGS} cap anchor dieu hoa khac")

    zones = []
    history_days = int(cfg.get("history_days") or 120)
    first = max(0, len(bars) - history_days)
    for members in build_zones(levels, float(cfg.get("zone_merge_atr", 0.15)) * atr):
        score, sources = score_zone(members, groups, anchors)
        zlow, zhigh = members[0]["price"], members[-1]["price"]
        weight = sum(m["score"] for m in members)
        mid = (sum(m["price"] * m["score"] for m in members) / weight if weight
               else (zlow + zhigh) / 2)
        side = "above" if zlow > ref_price else "below" if zhigh < ref_price else "inside"
        for source in sources:
            source["price"] = round(source["price"], decimals)
        zone = {"zone": [round(zlow, decimals), round(zhigh, decimals)],
                "mid": round(mid, decimals), "side": side, "score": round(score, 3),
                **distance(mid, ref_price, atr, decimals),
                "sources": sources[:MAX_SOURCES]}
        if len(sources) > MAX_SOURCES:
            zone["sources_total"] = len(sources)
        zone.update(zone_history(bars, dates, zlow, zhigh, atr, reaction, first))
        zones.append(zone)

    def brief(zone: dict[str, Any] | None) -> dict[str, Any] | None:
        if zone is None:
            return None
        keys = ("zone", "mid", "dist_pts", "dist_pct", "dist_atr", "score")
        return {k: zone[k] for k in keys}

    above = [z for z in zones if z["side"] == "above"]
    below = [z for z in zones if z["side"] == "below"]
    inside = [z for z in zones if z["side"] == "inside"]
    nearest_above = min(above, key=lambda z: z["mid"]) if above else None
    nearest_below = max(below, key=lambda z: z["mid"]) if below else None
    strongest_above = max(above, key=lambda z: (z["score"], -z["mid"])) if above else None
    strongest_below = max(below, key=lambda z: (z["score"], z["mid"])) if below else None

    # Ca thang gia qua dai thi giu max_zones vung GAN gia nhat, roi xep giam dan
    shown = sorted(zones, key=lambda z: abs(z["mid"] - ref_price))[:max(1, int(max_zones))]
    shown.sort(key=lambda z: -z["mid"])
    if not zones:
        warnings.append(f"khong co zone nao trong +-{range_pct}% quanh gia")

    return {
        "levels_generated": len(levels),
        "zones_total": len(zones),
        "nearest_above": brief(nearest_above),
        "nearest_below": brief(nearest_below),
        "strongest_above": brief(strongest_above),
        "strongest_below": brief(strongest_below),
        "zones": shown,
        "note": sq9_note(inside[0] if inside else None, nearest_above, nearest_below,
                         strongest_above, strongest_below, decimals),
        "warnings": warnings,
    }


def _history_text(zone: dict[str, Any]) -> str:
    when = zone.get("last_touch")
    short = f"{when[8:10]}/{when[5:7]}" if when else ""
    if zone.get("last_outcome") == "held":
        return f", da giu {short}"
    if zone.get("last_outcome") == "broken":
        return f", da bi pha {short}"
    return ""


def sq9_note(inside: dict[str, Any] | None, above: dict[str, Any] | None,
             below: dict[str, Any] | None, strong_above: dict[str, Any] | None,
             strong_below: dict[str, Any] | None, decimals: int) -> str:
    """Mot-hai cau ket luan theo mau cua requirement."""
    def span(zone: dict[str, Any]) -> str:
        return f"{fmt_price(zone['zone'][0], decimals)}-{fmt_price(zone['zone'][1], decimals)}"

    if inside:
        head = f"Gia dang nam trong zone {span(inside)} - vung quyet dinh."
    elif above and below:
        head = (f"Gia kep giua {fmt_price(below['mid'], decimals)} (ho tro{_history_text(below)}) "
                f"va {fmt_price(above['mid'], decimals)} (khang cu{_history_text(above)}).")
    elif below:
        head = (f"Khong co zone nao phia tren trong khoang xet; ho tro gan nhat "
                f"{fmt_price(below['mid'], decimals)}{_history_text(below)}.")
    elif above:
        head = (f"Khong co zone nao phia duoi trong khoang xet; khang cu gan nhat "
                f"{fmt_price(above['mid'], decimals)}{_history_text(above)}.")
    else:
        return "Khong co zone Square of 9 nao trong khoang xet."
    options = [(z, side) for z, side in ((strong_above, "phia tren"), (strong_below, "phia duoi"))
               if z is not None]
    if not options:
        return head
    zone, side = max(options, key=lambda o: o[0]["score"])
    return f"{head} Zone manh nhat {side}: {span(zone)}."
