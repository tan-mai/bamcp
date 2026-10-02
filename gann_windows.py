# TM - #GANN-TW - Gann Time Windows
"""Chieu thoi gian va hoi tu thanh cua so. Logic thuan, khong I/O.

Y tuong Gann: mot buoc ngoat da xay ra khong chi de lai mot muc gia, no con de
lai mot MOC THOI GIAN. Tu moc do dem ra cac chu ky (90, 144, 180, 360 ngay...),
cac ky niem nam, cac moc theo mua - cho nao nhieu phep dem cung tro ve mot ngay
thi ngay do dang de y hon.

Day la may phat sinh GIA THUYET, khong phai tin hieu. Co dang tin hay khong la
viec cua Phase 3 (backtest + permutation test). Vi vay trong so trong config la
so khoi diem, se chinh lai sau khi co so do.

Quy uoc: moi thu o day lam viec tren `datetime.date`, vi "ngay" cua feature nay
la ngay cua nen 1d. Pivot truyen vao phai co san khoa 'date' dang 'YYYY-MM-DD'.

KHONG LOOKAHEAD: ham nay khong tu loc pivot theo thoi gian - nguoi goi phai
truyen vao dung tap pivot da biet tai as_of (gann_pivots.live_pivots). O day chi
chan them tuoi pivot theo max_pivot_age_days.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

PROJECTION_TYPES = ("cycle", "anniversary", "seasonal", "swing_duration",
                    "range_square", "event")
# Chieu khong gan voi pivot nao thi khong co bac de nhan trong so bac.
NO_LEVEL_WEIGHT = 1.0


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _pivot_ref(pivot: dict[str, Any]) -> dict[str, Any]:
    """Phan pivot dua vao hit - chi du de nguoi doc biet dem tu dau."""
    return {"type": pivot.get("type"), "date": pivot.get("date"),
            "level": pivot.get("level"), "timeframe": pivot.get("timeframe")}


def merge_timeframes(daily: list[dict[str, Any]], weekly: list[dict[str, Any]],
                     margin_ms: int, week_span_ms: int) -> list[dict[str, Any]]:
    """Gop pivot 1d va 1w thanh mot danh sach, moi buoc ngoat DUNG MOT LAN.

    Nen 1d la goc: ngay cua no chinh xac, va pivot 1d trung pivot 1w da mang
    san bac major. Pivot 1w chi duoc them khi trong tuan cua no (+- margin)
    khong co pivot 1d cung loai - vd lich su 1d ngan hon 1w. Lay ca hai khung
    khong loc thi moi buoc ngoat lon bi dem hai lan (mot lan tu thu Hai cua
    tuan, mot lan tu ngay that), diem cua no tu nhien gap doi.

    Dung chung cho get_time_windows va backtest - hai noi phai thay cung mot
    tap pivot, khong thi backtest do mot thu khac voi cai tool dang dua ra.
    """
    out = [{**p, "span_days": 1} for p in daily]
    for item in weekly:
        low = int(item["time_ms"]) - margin_ms
        high = int(item["time_ms"]) + week_span_ms + margin_ms
        if any(p["type"] == item["type"] and low <= int(p["time_ms"]) < high
               for p in daily):
            continue
        out.append({**item, "span_days": 7})
    out.sort(key=lambda p: p["time_ms"])
    return out


def eligible_pivots(pivots: list[dict[str, Any]], as_of: date,
                    max_age_days: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pivot con duoc dem tu. Qua cu thi bo - dem tu pivot 5 nam truoc bang
    khung 1d thi ra ca ram ngay, khong con y nghia gi.
    """
    limits = max_age_days or {}
    out = []
    for pivot in pivots:
        moment = _as_date(pivot.get("date"))
        if moment is None or moment > as_of:
            continue
        cap = limits.get(pivot.get("level"))
        if cap is not None and (as_of - moment).days > int(cap):
            continue
        out.append({**pivot, "_date": moment})
    out.sort(key=lambda p: p["_date"])
    return out


# ---------------------------------------------------------------- tung loai chieu

def _cycle(pivots, cfg, level_weights, window):
    """Tu moi pivot: + cycle_days. Moi con so co trong so rieng."""
    weight = float(cfg.get("weight") or 1.0)
    out = []
    for days, cycle_weight in (cfg.get("days") or {}).items():
        step = int(days)
        if step <= 0:
            continue
        for pivot in pivots:
            target = pivot["_date"] + timedelta(days=step)
            if not window(target):
                continue
            out.append({
                "type": "cycle", "days": step, "target": target,
                "weight": weight * level_weights.get(pivot.get("level"), NO_LEVEL_WEIGHT)
                * float(cycle_weight),
                "pivot": _pivot_ref(pivot),
            })
    return out


def _anniversary(pivots, cfg, level_weights, window):
    """Cung ngay-thang cac nam sau. 29/02 roi vao 28/02 nam khong nhuan."""
    weight = float(cfg.get("weight") or 1.0)
    levels = [str(x).lower() for x in (cfg.get("levels") or ["major"])]
    max_years = max(1, int(cfg.get("max_years") or 6))
    out = []
    for pivot in pivots:
        if pivot.get("level") not in levels:
            continue
        moment = pivot["_date"]
        for year in range(1, max_years + 1):
            try:
                target = moment.replace(year=moment.year + year)
            except ValueError:
                # 29/02 cua nam khong nhuan: lui ve 28/02
                target = moment.replace(year=moment.year + year, day=28)
            if not window(target):
                continue
            out.append({
                "type": "anniversary", "years": year, "target": target,
                "weight": weight * level_weights.get(pivot.get("level"), NO_LEVEL_WEIGHT),
                "pivot": _pivot_ref(pivot),
            })
    return out


def _seasonal(cfg, window, years):
    """Moc co dinh trong nam: phan/chi, giua mua. Khong gan voi pivot nao."""
    weight = float(cfg.get("weight") or 1.0)
    out = []
    for text in (cfg.get("dates") or []):
        parts = str(text).strip().split("-")
        if len(parts) != 2:
            continue
        try:
            month, day = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        for year in years:
            try:
                target = date(year, month, day)
            except ValueError:
                continue
            if window(target):
                out.append({"type": "seasonal", "name": f"{month:02d}-{day:02d}",
                            "target": target, "weight": weight})
    return out


def _swing_duration(pivots, cfg, level_weights, window, span_days):
    """Tu pivot gan nhat: + ratio x do dai cac chan song hoan chinh gan day.

    Chan song cu lap lai do dai cua no - mot nhip 40 ngay thuong keo theo nhung
    nhip 20, 40, 80 ngay. duration_bars dem bang NEN nen phai doi sang ngay,
    theo khung cua CHINH pivot do (span_days cua pivot, neu co): danh sach co
    the tron pivot 1d va 1w, ma 10 nen tuan la 70 ngay chu khong phai 10.
    """
    if not pivots:
        return []
    weight = float(cfg.get("weight") or 1.0)
    ratios = [float(r) for r in (cfg.get("ratios") or [0.5, 1.0, 2.0])]
    legs = max(1, int(cfg.get("legs") or 3))
    base = pivots[-1]
    out = []
    recent = [p for p in pivots if p.get("duration_bars")][-legs:]
    for source in recent:
        bar_days = max(1, int(source.get("span_days") or span_days or 1))
        leg_days = int(source["duration_bars"]) * bar_days
        for ratio in ratios:
            step = int(round(leg_days * ratio))
            if step <= 0:
                continue
            target = base["_date"] + timedelta(days=step)
            if not window(target):
                continue
            out.append({
                "type": "swing_duration", "days": step, "ratio": ratio,
                "leg_bars": int(source["duration_bars"]),
                "target": target,
                "weight": weight * level_weights.get(base.get("level"), NO_LEVEL_WEIGHT),
                "pivot": _pivot_ref(base),
            })
    return out


def _range_square(pivots, cfg, level_weights, window, symbol):
    """Bien do gia cua chan cuoi chia scale_factor -> so ngay, dem tu pivot cuoi.

    Day la "squaring price and time" cua Gann. scale_factor phu thuoc thang do
    gia cua tung cap nen bat buoc dat rieng; khong co thi bo qua chu khong doan.
    """
    if len(pivots) < 2:
        return []
    scales = cfg.get("scale_factor") or {}
    scale = scales.get(symbol) or scales.get(symbol.upper())
    if not scale:
        return []
    weight = float(cfg.get("weight") or 1.0)
    base, previous = pivots[-1], pivots[-2]
    amplitude = abs(float(base["price"]) - float(previous["price"]))
    step = int(round(amplitude / float(scale)))
    if step <= 0:
        return []
    target = base["_date"] + timedelta(days=step)
    if not window(target):
        return []
    return [{
        "type": "range_square", "days": step,
        "amplitude": round(amplitude, 2), "scale_factor": float(scale),
        "target": target,
        "weight": weight * level_weights.get(base.get("level"), NO_LEVEL_WEIGHT),
        "pivot": _pivot_ref(base),
    }]


def _event(events, cfg, window, symbol):
    """Su kien vi mo nhap tay. symbols rong = ap cho moi cap."""
    weight = float(cfg.get("weight") or 1.0)
    out = []
    for item in events or []:
        if not isinstance(item, dict):
            continue
        target = _as_date(item.get("date"))
        if target is None or not window(target):
            continue
        scope = [str(s).upper() for s in (item.get("symbols") or [])]
        if scope and symbol.upper() not in scope:
            continue
        out.append({
            "type": "event", "name": str(item.get("name") or "su kien"),
            "target": target,
            "weight": weight * float(item.get("weight") or 1.0),
        })
    return out


# ---------------------------------------------------------------- cham diem

def projections(pivots: list[dict[str, Any]], as_of: date, start: date, end: date,
                cfg: dict[str, Any], events: list[dict[str, Any]] | None,
                symbol: str, span_days: int = 1) -> list[dict[str, Any]]:
    """Moi phep chieu roi vao khoang [start, end]. Loai nao tat thi khong co hit.

    start/end da noi rong them tolerance_days so voi khoang nguoi dung hoi, vi
    mot phep chieu roi ngoai bien van con lan diem vao trong.
    """
    projection_cfg = cfg.get("projections") or {}
    level_weights = {str(k): float(v) for k, v in
                     (projection_cfg.get("level_weights") or {}).items()}
    usable = eligible_pivots(pivots, as_of, projection_cfg.get("max_pivot_age_days"))
    # Anniversary co gioi han tuoi rieng (max_years) nen khong ap max_pivot_age_days:
    # voi mac dinh cua BR (major 1500 ngay ~ 4.1 nam, max_years 6) thi ky niem
    # nam thu 5, 6 se khong bao gio duoc dem - hai so do mau thuan nhau.
    known = eligible_pivots(pivots, as_of, None)

    def window(moment: date) -> bool:
        return start <= moment <= end

    def block(name: str) -> dict[str, Any]:
        return projection_cfg.get(name) or {}

    def on(name: str) -> bool:
        return bool(block(name).get("enabled"))

    out: list[dict[str, Any]] = []
    if on("cycle"):
        out += _cycle(usable, block("cycle"), level_weights, window)
    if on("anniversary"):
        out += _anniversary(known, block("anniversary"), level_weights, window)
    if on("seasonal"):
        out += _seasonal(block("seasonal"), window,
                         sorted({start.year, end.year}))
    if on("swing_duration"):
        out += _swing_duration(usable, block("swing_duration"), level_weights,
                               window, span_days)
    if on("range_square"):
        out += _range_square(usable, block("range_square"), level_weights,
                             window, symbol)
    if on("event"):
        out += _event(events, block("event"), window, symbol)
    return out


def score_days(targets: list[dict[str, Any]], start: date, end: date,
               tolerance_days: int) -> dict[date, list[dict[str, Any]]]:
    """Trai diem tung phep chieu ra quanh ngay no tro tay vao.

    proximity = 1 - |offset| / (tolerance + 1): dung ngay thi an tron diem, lech
    dan thi mat dan, qua tolerance thi khong tinh. Nho vay mot cua so khong co
    bien cung nhac - diem cao nhat nam o giua.
    """
    tolerance = max(0, int(tolerance_days))
    out: dict[date, list[dict[str, Any]]] = {}
    for target in targets:
        for offset in range(-tolerance, tolerance + 1):
            day = target["target"] + timedelta(days=offset)
            if day < start or day > end:
                continue
            proximity = 1 - abs(offset) / (tolerance + 1)
            score = round(float(target["weight"]) * proximity, 4)
            if score <= 0:
                continue
            hit = {k: v for k, v in target.items() if k not in ("target", "weight")}
            hit["date"] = target["target"].isoformat()
            hit["offset"] = offset
            hit["score"] = round(score, 2)
            out.setdefault(day, []).append(hit)
    return out


def merge_windows(day_hits: dict[date, list[dict[str, Any]]], min_score: float,
                  merge_gap_days: int, max_hits: int) -> list[dict[str, Any]]:
    """Chuoi ngay lien tiep co diem du nguong -> mot cua so.

    Hai doan cach nhau khong qua merge_gap_days ngay duoi nguong thi gop lam
    mot: hai cua so sat nhau la mot vung thoi gian, khong phai hai co hoi.

    hits cua cua so lay cua ngay DINH, dung voi score = diem dinh. Lay het hit
    ca cua so thi con so va danh sach khong con khop nhau.
    """
    totals = {day: round(sum(h["score"] for h in hits), 2)
              for day, hits in day_hits.items()}
    qualified = sorted(day for day, total in totals.items() if total >= min_score)
    if not qualified:
        return []

    gap = max(0, int(merge_gap_days))
    groups: list[list[date]] = [[qualified[0]]]
    for day in qualified[1:]:
        if (day - groups[-1][-1]).days <= gap + 1:
            groups[-1].append(day)
        else:
            groups.append([day])

    out = []
    for group in groups:
        peak = max(group, key=lambda d: (totals[d], -d.toordinal()))
        hits = sorted(day_hits[peak], key=lambda h: -h["score"])
        out.append({
            "from": group[0].isoformat(),
            "to": group[-1].isoformat(),
            "peak": peak.isoformat(),
            "score": totals[peak],
            "days": (group[-1] - group[0]).days + 1,
            "hits": hits[:max(1, int(max_hits))],
            "hit_count": len(day_hits[peak]),
        })
    out.sort(key=lambda w: (-w["score"], w["from"]))
    return out


def build(pivots: list[dict[str, Any]], as_of: date, horizon_days: int,
          cfg: dict[str, Any], events: list[dict[str, Any]] | None,
          symbol: str, span_days: int = 1, min_score: float | None = None,
          max_windows: int = 5) -> dict[str, Any]:
    """Ca day: chieu -> cham diem -> gop cua so. Tra ve dict da san de serialize."""
    projection_cfg = cfg.get("projections") or {}
    scoring = cfg.get("scoring") or {}
    tolerance = int(projection_cfg.get("tolerance_days") or 0)
    threshold = float(scoring.get("min_score") or 0.0) if not min_score else float(min_score)

    start = as_of
    end = as_of + timedelta(days=max(0, int(horizon_days)))
    # Noi rong khi di chieu: phep chieu roi ngoai bien van lan diem vao trong.
    targets = projections(pivots, as_of, start - timedelta(days=tolerance),
                          end + timedelta(days=tolerance), cfg, events, symbol,
                          span_days)
    day_hits = score_days(targets, start, end, tolerance)
    windows = merge_windows(day_hits, threshold,
                            int(scoring.get("merge_gap_days") or 0),
                            int(scoring.get("max_hits_per_window") or 8))
    return {
        "as_of": as_of.isoformat(),
        "horizon_days": int(horizon_days),
        "min_score": round(threshold, 2),
        "tolerance_days": tolerance,
        "projections_counted": len(targets),
        "windows": windows[:max(1, int(max_windows))],
        "windows_found": len(windows),
    }
