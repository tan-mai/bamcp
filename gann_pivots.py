# TM - #GANN-TW - Gann Time Windows
"""Pivot kieu swing chart Gann va trang thai song. Logic thuan, khong I/O.

Tach rieng khoi server.py de test duoc bang du lieu dung tay, theo dung cach
orb.py tach logic thuan khoi orb_runtime.py. Moi moc thoi gian la epoch ms;
viec doi sang chu nam o tang tren, tru chuoi ngay 'YYYY-MM-DD' - cai do phai
biet mui gio nen duoc truyen vao.

Quy uoc nen giong server._normalize_bars:
    {"open_time": ms, "open", "high", "low", "close", "volume", "close_time": ms}
Chi truyen vao nen DA DONG.

KHONG LOOKAHEAD: pivot chi sinh ra khi da co du nen xac nhan dao chieu, va
confirmed_at luon la nen kich hoat viec do - luon sau time. Cho nen muon biet
"tai thoi diem T thi biet gi", loc theo confirmed_at <= T, khong phai theo time.
"""

from __future__ import annotations

from typing import Any

LEVELS = ("major", "intermediate", "minor")
# Khung sinh ra pivot major. Pivot tim tu khung nay deu la major; khung nho hon
# duoc xep bac theo bien do, va duoc nang len major khi trung voi mot pivot
# major (muc FR-1.1).
MAJOR_TIMEFRAME = "1w"
DAY_MS = 86_400_000


# ---------------------------------------------------------------- ATR

def atr_series(bars: list[dict[str, Any]], period: int) -> list[float | None]:
    """ATR kieu Wilder, phan tu i tinh bang cac nen tu 0 den i. Chua du nen -> None.

    Dung de do bien do mot chan song theo do bien dong cua chinh giai doan do:
    5% hoi nam 2019 va 5% hoi nam 2025 khong phai cung mot thu.
    """
    if period <= 0 or not bars:
        return [None] * len(bars)
    trs: list[float] = []
    out: list[float | None] = []
    value: float | None = None
    for i, bar in enumerate(bars):
        high, low = float(bar["high"]), float(bar["low"])
        if i == 0:
            true_range = high - low
        else:
            prev_close = float(bars[i - 1]["close"])
            true_range = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(true_range)
        if i + 1 < period:
            out.append(None)
        elif i + 1 == period:
            value = sum(trs) / period
            out.append(value)
        else:
            value = ((value or 0.0) * (period - 1) + true_range) / period
            out.append(value)
    return out


# ---------------------------------------------------------------- swing chart

def classify_bar(bar: dict[str, Any], ref: dict[str, Any]) -> str:
    """Xep loai nen so voi nen tham chieu: up / down / inside / outside.

    Theo swing chart Gann: nen tang co ca high va low cao hon, nen giam co ca
    hai thap hon. Nen nam gon trong nen truoc (inside) khong noi len dieu gi ve
    huong nen bi bo qua han - khong tinh la mot nhip pha, cung khong lam mat so
    dem dang co. Bang nhau duoc coi la khong vuot, xep vao inside.
    """
    higher = float(bar["high"]) > float(ref["high"])
    lower = float(bar["low"]) < float(ref["low"])
    if higher and lower:
        return "outside"
    if higher:
        return "up"
    if lower:
        return "down"
    return "inside"


def detect_pivots(bars: list[dict[str, Any]], swing_bars: int,
                  timeframe: str) -> tuple[list[dict[str, Any]], float | None]:
    """Tim pivot bang quy tac dao chieu `swing_bars` nen.

    Dang o chan tang, gap du swing_bars nen giam lien tiep thi chan tang ket
    thuc: pivot high la high cao nhat cua chan vua xong, con nen hoan tat so dem
    la confirmed_at. Nguoc lai cho chan giam. Nen outside tinh la noi tiep chan
    hien tai (no vua vuot bien theo chieu dang di) va duoc mo rong cuc tri.

    Tra ve (pivot theo thu tu thoi gian, gia goc cua chan dau tien). Gia goc lam
    moc do bien do cho pivot dau tien - khong co no thi pivot dau khong co
    move_pct de loc nhieu.
    """
    pivots: list[dict[str, Any]] = []
    if len(bars) < 2 or swing_bars < 1:
        return pivots, None

    direction: str | None = None
    ref = bars[0]
    ext = 0                      # chi so nen dang giu cuc tri cua chan hien tai
    origin: float | None = None  # gia bat dau chan dau tien
    pending = 0                  # so nen nguoc chieu lien tiep dang dem

    for i in range(1, len(bars)):
        bar = bars[i]
        kind = classify_bar(bar, ref)
        if kind == "inside":
            continue
        ref = bar

        if direction is None:
            # Nen khong-inside dau tien quyet dinh huong chan mo dau. Outside
            # tinh la tang - no vua pha dinh, va o ngay dau bo du lieu thi khong
            # co can cu nao tot hon.
            direction = "down" if kind == "down" else "up"
            ext = i
            origin = float(bars[0]["low"]) if direction == "up" else float(bars[0]["high"])
            continue

        if kind == "outside" or kind == direction:
            pending = 0
            if direction == "up" and float(bar["high"]) > float(bars[ext]["high"]):
                ext = i
            elif direction == "down" and float(bar["low"]) < float(bars[ext]["low"]):
                ext = i
            continue

        pending += 1
        if pending < swing_bars:
            continue

        # Du so nen nguoc chieu -> chot pivot cua chan vua ket thuc.
        pivots.append({
            "type": "high" if direction == "up" else "low",
            "timeframe": timeframe,
            "time_ms": int(bars[ext]["open_time"]),
            "price": float(bars[ext]["high"] if direction == "up" else bars[ext]["low"]),
            "confirmed_at_ms": int(bar["open_time"]),
            "source": "auto",
            "index": ext,
        })
        direction = "down" if direction == "up" else "up"
        pending = 0
        # Cuc tri cua chan moi nam trong doan nen vua lam nen viec dao chieu.
        span = range(ext + 1, i + 1)
        if direction == "down":
            ext = min(span, key=lambda k: float(bars[k]["low"]))
        else:
            ext = max(span, key=lambda k: float(bars[k]["high"]))

    return pivots, origin


# ---------------------------------------------------------- bien do & loc nhieu

def annotate(pivots: list[dict[str, Any]], atr: list[float | None],
             origin: float | None) -> None:
    """Dien move_pct, move_atr, duration_bars - do so voi pivot nguoc chieu truoc.

    Sua truc tiep tren list. Phai goi lai sau moi lan them/bo pivot, vi bo mot
    pivot la doi moc do cua pivot ke tiep.
    """
    prev_price = origin
    prev_index = 0
    for pivot in pivots:
        index = pivot["index"]
        move = abs(pivot["price"] - prev_price) if prev_price else None
        pivot["move_pct"] = (round(move / prev_price * 100, 2)
                             if move is not None and prev_price else None)
        span = atr[index] if 0 <= index < len(atr) else None
        pivot["move_atr"] = round(move / span, 2) if move is not None and span else None
        pivot["duration_bars"] = max(0, index - prev_index)
        prev_price, prev_index = pivot["price"], index


def _is_weak(pivot: dict[str, Any], min_pct: float, min_atr: float) -> bool:
    """Pivot nhieu: nho ca theo % VA theo ATR. Nho mot trong hai thi giu lai.

    Hai thuoc do bat hai thu khac nhau - % bat bien do tuyet doi, ATR bat bien
    do so voi nhip thi truong luc do - nen chi loai khi ca hai deu noi la khong
    dang ke.
    """
    pct = pivot.get("move_pct")
    if pct is None or pct >= min_pct:
        return False
    span = pivot.get("move_atr")
    return span is None or span < min_atr


def _merge_adjacent(pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hai pivot cung loai dung canh nhau thi giu cai cuc tri hon.

    Xay ra sau khi bo mot pivot nhieu o giua. confirmed_at lay moc MUON hon cua
    hai cai: pivot con lai chi thuc su duoc xac nhan khi nhip dao chieu thu hai
    xay ra - lay moc som hon la tu cho minh biet truoc.
    """
    out: list[dict[str, Any]] = []
    for pivot in pivots:
        if out and out[-1]["type"] == pivot["type"]:
            prev = out[-1]
            if pivot["type"] == "high":
                keep = pivot if pivot["price"] > prev["price"] else prev
            else:
                keep = pivot if pivot["price"] < prev["price"] else prev
            keep = dict(keep)
            keep["confirmed_at_ms"] = max(prev["confirmed_at_ms"],
                                          pivot["confirmed_at_ms"])
            out[-1] = keep
            continue
        out.append(pivot)
    return out


def filter_noise(pivots: list[dict[str, Any]], atr: list[float | None],
                 origin: float | None, min_pct: float,
                 min_atr: float) -> list[dict[str, Any]]:
    """Bo dan pivot nhieu, nho nhat truoc, den khi khong con cai nao nho.

    Phai lap chu khong quet mot luot: bo mot pivot xong thi hai pivot hai ben
    gop lai thanh mot chan dai hon, va chan dai hon do co the lam pivot ke tiep
    tu "du lon" thanh "nho".
    """
    out = [dict(p) for p in pivots]
    # Moi vong bo it nhat mot pivot nen vong lap huu han; chan them cho chac.
    for _ in range(len(out) + 1):
        annotate(out, atr, origin)
        weak = [(i, p) for i, p in enumerate(out) if _is_weak(p, min_pct, min_atr)]
        if not weak:
            break
        target = min(weak, key=lambda pair: pair[1]["move_pct"])[0]
        out.pop(target)
        out = _merge_adjacent(out)
    annotate(out, atr, origin)
    return out


# ---------------------------------------------------------------- phan cap

def assign_levels(pivots: list[dict[str, Any]], timeframe: str,
                  intermediate_move_pct: float,
                  major_pivots: list[dict[str, Any]] | None = None,
                  merge_days: int = 3) -> None:
    """Xep bac major / intermediate / minor. Sua truc tiep tren list.

    Pivot da bi ghim level bang tay (level_locked) thi khong doi.
    """
    if timeframe == MAJOR_TIMEFRAME:
        for pivot in pivots:
            if not pivot.get("level_locked"):
                pivot["level"] = "major"
        return
    window = max(0, int(merge_days)) * DAY_MS
    majors = major_pivots or []
    for pivot in pivots:
        if pivot.get("level_locked"):
            continue
        move = pivot.get("move_pct") or 0.0
        level = "intermediate" if move >= intermediate_move_pct else "minor"
        for major in majors:
            if (major["type"] == pivot["type"]
                    and abs(major["time_ms"] - pivot["time_ms"]) <= window):
                level = "major"
                break
        pivot["level"] = level


# ---------------------------------------------------------------- pivot thu cong

def apply_manual(pivots: list[dict[str, Any]], entries: list[dict[str, Any]],
                 bars: list[dict[str, Any]], bar_dates: list[str],
                 timeframe: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Ap pin/exclude tu nguoi dung. Tra ve (pivot moi, canh bao).

    Khop theo (ngay cua nen, loai) - dung ngay, khong do gan dung: trang admin
    liet ke san ngay that cua tung pivot nen nguoi dung khong phai tu go.

    - exclude: bo pivot do.
    - pin: pivot da co thi ep level; chua co thi them moi, gia lay tu high/low
      that cua nen do chu khong lay so nguoi dung go.
    """
    out = [dict(p) for p in pivots]
    warnings: list[str] = []
    index_of = {date: i for i, date in enumerate(bar_dates)}
    date_of = {int(bar["open_time"]): bar_dates[i] for i, bar in enumerate(bars)}

    for entry in entries or []:
        action = str(entry.get("action") or "").strip().lower()
        date = str(entry.get("date") or "").strip()
        kind = str(entry.get("type") or "").strip().lower()
        if action not in ("pin", "exclude") or kind not in ("high", "low") or not date:
            warnings.append(f"bo qua pivot thu cong khong hop le: {entry!r}")
            continue

        match = next((p for p in out
                      if p["type"] == kind and date_of.get(p["time_ms"]) == date), None)
        if action == "exclude":
            if match is None:
                warnings.append(f"exclude {kind} {date}: khong co pivot nao o day")
                continue
            out.remove(match)
            continue

        level = str(entry.get("level") or "major").strip().lower()
        if level not in LEVELS:
            warnings.append(f"pin {kind} {date}: level {level!r} khong hop le, dung major")
            level = "major"
        if match is not None:
            match["level"] = level
            match["level_locked"] = True
            match["source"] = "manual"
            continue

        index = index_of.get(date)
        if index is None:
            warnings.append(f"pin {kind} {date}: khong co nen {timeframe} ngay nay")
            continue
        bar = bars[index]
        # Pivot thu cong khong co nen xac nhan. Lay nen ke tiep lam confirmed_at
        # de giu dung quy uoc confirmed_at > time; khong co nen ke tiep thi lui
        # ve close_time cua chinh no.
        if index + 1 < len(bars):
            confirmed = int(bars[index + 1]["open_time"])
        else:
            confirmed = int(bar.get("close_time") or bar["open_time"]) + 1
        out.append({
            "type": kind,
            "timeframe": timeframe,
            "time_ms": int(bar["open_time"]),
            "price": float(bar["high"] if kind == "high" else bar["low"]),
            "confirmed_at_ms": confirmed,
            "source": "manual",
            "level": level,
            "level_locked": True,
            "index": index,
            "note": str(entry.get("note") or ""),
        })

    out.sort(key=lambda p: p["time_ms"])
    return out, warnings


# ---------------------------------------------------------------- tinh ca bo

def build(bars: list[dict[str, Any]], bar_dates: list[str], timeframe: str,
          cfg: dict[str, Any], manual: list[dict[str, Any]] | None = None,
          major_pivots: list[dict[str, Any]] | None = None
          ) -> tuple[list[dict[str, Any]], list[str]]:
    """Chay ca day: phat hien -> loc nhieu -> pivot thu cong -> xep bac.

    Thu tu nay quan trong. Loc nhieu truoc pivot thu cong, de cai nguoi dung
    ghim khong bi chinh thuat toan loc vua bo di. Xep bac sau cung, vi bac phu
    thuoc bien do - ma bien do chi dung sau khi list pivot da chot.
    """
    swing_bars = int(_per_tf(cfg.get("swing_bars"), timeframe, 2))
    atr = atr_series(bars, int(cfg.get("atr_period") or 14))
    pivots, origin = detect_pivots(bars, swing_bars, timeframe)
    pivots = filter_noise(
        pivots, atr, origin,
        float(_per_tf(cfg.get("min_move_pct"), timeframe, 0.0)),
        float(_per_tf(cfg.get("min_move_atr"), timeframe, 0.0)))
    pivots, warnings = apply_manual(pivots, manual or [], bars, bar_dates, timeframe)
    annotate(pivots, atr, origin)
    assign_levels(pivots, timeframe,
                  float(cfg.get("intermediate_move_pct") or 0.0),
                  major_pivots, int(cfg.get("major_merge_days") or 3))
    return pivots, warnings


def _per_tf(value: Any, timeframe: str, default: Any) -> Any:
    """Config dang {1d: x, 1w: y} hoac mot so dung chung cho moi khung."""
    if isinstance(value, dict):
        return value.get(timeframe, value.get(timeframe.lower(), default))
    return default if value is None else value


# ---------------------------------------------------------------- trang thai song

def _structure_start(pivots: list[dict[str, Any]], trend: str) -> int:
    """Pivot dau tien ma tu do cau truc xu huong hien tai con giu lien tuc.

    Di ngugc tu cuoi: voi xu huong tang, moi dinh phai thap hon dinh sau no va
    moi day phai thap hon day sau no. Cho nao khong con dung nua la cho xu
    huong nay bat dau.
    """
    last_high = last_low = None
    start = len(pivots) - 1
    for i in range(len(pivots) - 1, -1, -1):
        pivot = pivots[i]
        price = pivot["price"]
        if pivot["type"] == "high":
            if last_high is not None and (
                    price >= last_high if trend == "up" else price <= last_high):
                break
            last_high = price
        else:
            if last_low is not None and (
                    price >= last_low if trend == "up" else price <= last_low):
                break
            last_low = price
        start = i
    return start


def swing_state(pivots: list[dict[str, Any]], bars: list[dict[str, Any]],
                timeframe: str, max_corrections: int = 10) -> dict[str, Any]:
    """Xu huong, chan dang chay, cac nhip hoi, va overbalance thoi gian/gia.

    Overbalance la y chinh cua Gann o day: mot nhip hoi dai hon moi nhip hoi
    truoc do trong cung xu huong, hoac sau hon moi nhip truoc do, la dau hieu
    xu huong doi - khong phai mot nhip hoi binh thuong nua.

    Chi so nen cua pivot duoc tra cuu lai theo time_ms chu khong tin chi so da
    cache: backfill them nen cu vao dau file la moi chi so cu lech het.
    """
    empty = {
        "timeframe": timeframe, "trend": "range", "current_leg": None,
        "corrections_in_trend": [], "max_correction_bars": None,
        "max_correction_depth": None, "time_overbalanced": False,
        "price_overbalanced": False,
        "note": "chua du pivot de doc cau truc song",
    }
    if not bars or len(pivots) < 2:
        return empty

    index_of = {int(bar["open_time"]): i for i, bar in enumerate(bars)}
    usable = []
    for pivot in pivots:
        index = index_of.get(pivot["time_ms"])
        if index is not None:
            usable.append({**pivot, "index": index})
    if len(usable) < 2:
        return empty
    usable.sort(key=lambda p: p["index"])

    highs = [p for p in usable if p["type"] == "high"]
    lows = [p for p in usable if p["type"] == "low"]
    trend = "range"
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]:
            trend = "up"
        elif highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]:
            trend = "down"

    # Nhip hoi = chan nguoc xu huong da ket thuc, nam trong xu huong hien tai.
    corrections: list[dict[str, Any]] = []
    if trend in ("up", "down"):
        start = _structure_start(usable, trend)
        first, second = ("high", "low") if trend == "up" else ("low", "high")
        for i in range(start, len(usable) - 1):
            begin, end = usable[i], usable[i + 1]
            if begin["type"] != first or end["type"] != second:
                continue
            depth = abs(begin["price"] - end["price"])
            corrections.append({
                "from_ms": begin["time_ms"],
                "to_ms": end["time_ms"],
                "bars": end["index"] - begin["index"],
                "depth_points": round(depth, 2),
                "depth_pct": round(depth / begin["price"] * 100, 2) if begin["price"] else None,
            })

    recent = corrections[-max_corrections:]
    max_bars = max((c["bars"] for c in corrections), default=None)
    max_depth = max((c["depth_pct"] for c in corrections
                     if c["depth_pct"] is not None), default=None)

    last = usable[-1]
    last_index = len(bars) - 1
    leg_dir = "down" if last["type"] == "high" else "up"
    tail = bars[last["index"]:]
    extreme = (min(float(b["low"]) for b in tail) if leg_dir == "down"
               else max(float(b["high"]) for b in tail))
    amplitude = abs(extreme - last["price"])
    leg_bars = last_index - last["index"]
    leg = {
        "direction": leg_dir,
        "from_pivot": {"type": last["type"], "time_ms": last["time_ms"],
                       "price": last["price"], "level": last.get("level")},
        "bars": leg_bars,
        "amplitude_points": round(amplitude, 2),
        "amplitude_pct": round(amplitude / last["price"] * 100, 2) if last["price"] else None,
        "extreme_so_far": round(extreme, 2),
    }

    # Chan dang chay chi la "nhip hoi" khi no di nguoc xu huong. Dang di cung
    # chieu xu huong thi khong co gi de so, overbalance = False.
    counter = trend in ("up", "down") and leg_dir != trend
    time_over = bool(counter and max_bars is not None and leg_bars > max_bars)
    price_over = bool(counter and max_depth is not None
                      and leg["amplitude_pct"] is not None
                      and leg["amplitude_pct"] > max_depth)

    return {
        "timeframe": timeframe,
        "trend": trend,
        "current_leg": leg,
        "corrections_in_trend": recent,
        "max_correction_bars": max_bars,
        "max_correction_depth": max_depth,
        "time_overbalanced": time_over,
        "price_overbalanced": price_over,
        "note": _note(trend, leg_dir, counter, leg_bars, max_bars,
                      leg["amplitude_pct"], max_depth, time_over, price_over),
    }


def _note(trend: str, leg_dir: str, counter: bool, leg_bars: int,
          max_bars: int | None, depth: float | None, max_depth: float | None,
          time_over: bool, price_over: bool) -> str:
    """Mot cau cho Claude doc nhanh, khong phai doc lai ca khoi JSON."""
    names = {"up": "tang", "down": "giam", "range": "di ngang"}
    name = names[trend]
    if not counter:
        return (f"Xu huong {name}, chan hien tai di cung chieu, da {leg_bars} nen. "
                f"Chua co nhip hoi dang theo doi.")
    head = (f"Xu huong {name}, dang trong nhip hoi {names[leg_dir]} - {leg_bars} nen"
            f"{f', sau {depth}%' if depth is not None else ''}.")
    if time_over and price_over:
        return (head + f" Vuot ca thoi gian (ky luc {max_bars} nen) va gia "
                f"(ky luc {max_depth}%): overbalance ca hai mat, canh bao xu huong doi.")
    if time_over:
        return head + f" Dai hon moi nhip hoi truoc ({max_bars} nen): overbalance thoi gian."
    if price_over:
        return head + f" Sau hon moi nhip hoi truoc ({max_depth}%): overbalance gia."
    parts = []
    if max_bars is not None:
        parts.append(f"{max_bars} nen")
    if max_depth is not None:
        parts.append(f"{max_depth}%")
    limit = " / ".join(parts) or "chua co moc so sanh"
    return head + f" Van trong gioi han cua cac nhip hoi truoc ({limit})."
