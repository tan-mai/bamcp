# TM - #ENTRY-ALERT - Entry setups & alerts
from __future__ import annotations

import json
from typing import Any, Callable

STATUSES = ("WAITING", "IN_ZONE", "TRIGGERED", "CONFIRMED", "ENTRY_VALID", "MISSED",
            "BLOCKED", "FILLED", "INVALIDATED", "EXPIRED", "CANCELLED")
TERMINAL = ("FILLED", "INVALIDATED", "EXPIRED", "CANCELLED")
FILLABLE = ("TRIGGERED", "CONFIRMED", "ENTRY_VALID", "MISSED")
DIRECTIONS = ("long", "short")
TRIGGERS: dict[str, tuple[str, ...]] = {
    "spring": ("long",),
    "utad": ("short",),
    "sos": ("long",),
    "sow": ("short",),
    "rejection": ("long", "short"),
    "retest": ("long", "short"),
    "close_above": ("long",),
    "close_below": ("short",),
}
CONFIRMS = ("close_above", "close_below")
# rejection_* la nguong goi y theo cap de khai vao trigger_params cua setup rejection; watcher
# khong doc truc tiep, vi test va stopping action can hai dieu kien nguoc nhau.
PARAM_KEYS = ("vol_mult", "sma_period", "body_pct", "wick_pct", "retest_low_vol_mult",
              "confirm_within", "entry_valid_candles", "m15_filter",
              "rejection_test_max_vol", "rejection_stopping_min_vol")
LEVEL_KEYS = ("ref_low", "ref_high", "level")
# TM - #ENTRY-VOL - Nguong volume rieng cua rejection, boi so SMA volume M5, khai theo tung setup.
# Khong co mac dinh: test can vol thap, stopping action can vol cao.
REJECTION_VOL_KEYS = ("min_vol_mult", "max_vol_mult")
# Tham so ma tung trigger thuc su doc. Khai tham so khac thi validate tu choi, de khong co
# dieu kien nao trong tuong co hieu luc ma code bo qua.
TRIGGER_PARAMS: dict[str, tuple[str, ...]] = {
    "spring": ("vol_mult", "sma_period", "ref_low"),
    "utad": ("vol_mult", "sma_period", "ref_high"),
    "sos": ("vol_mult", "sma_period", "body_pct", "level"),
    "sow": ("vol_mult", "sma_period", "body_pct", "level"),
    "rejection": ("wick_pct", "sma_period", *REJECTION_VOL_KEYS),
    "retest": ("retest_low_vol_mult", "sma_period", "level"),
    "close_above": ("level",),
    "close_below": ("level",),
}
RETEST_LOOKBACK = 288
# TM - #ENTRY-HTF - Huong khung lon luu kem setup: (khoa trong htf, timeframe, nhan hien thi)
HTF_FRAMES = (("w", "1w", "W"), ("d", "1d", "D"), ("h4", "4h", "H4"))
HTF_WORDS = {"up": "tăng", "down": "giảm", "range": "đi ngang"}


class InsufficientData(Exception):
    def __init__(self, have: int, need: int) -> None:
        super().__init__(f"can {need} nen M5 lich su, moi co {have}")
        self.have = have
        self.need = need


def x_max(direction: str, entry: float, stop: float, target: float, min_rr: float) -> float:
    m = float(min_rr)
    if direction == "long":
        return ((target - entry) - m * (entry - stop)) / (1 + m)
    return ((entry - target) - m * (stop - entry)) / (1 + m)


def effective_max_chase(declared: float | None, xmax: float) -> float:
    return xmax if declared is None else min(float(declared), xmax)


def resolve_params(defaults: dict[str, Any], override: dict[str, Any] | None,
                   trigger_params: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(defaults or {})
    out.update({k: v for k, v in (override or {}).items() if v is not None})
    out.update({k: v for k, v in (trigger_params or {}).items() if v is not None})
    return out


def levels(setup: dict[str, Any], params: dict[str, Any]) -> dict[str, float]:
    long = setup["direction"] == "long"
    return {
        "ref_low": float(params.get("ref_low", setup["zone_low"])),
        "ref_high": float(params.get("ref_high", setup["zone_high"])),
        "level": float(params.get("level", setup["zone_high"] if long else setup["zone_low"])),
    }


def code_letters(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def code_base(symbol: str) -> str:
    sym = symbol.upper()
    return sym[:-4] if sym.endswith("USDT") and len(sym) > 4 else sym


# TM - #ENTRY-HTF - Huong khung lon cua setup
def swing_trend(highs: list[float], lows: list[float], close: float) -> str | None:
    """up = dinh va day cao dan, down = dinh va day thap dan, con lai la range.

    Nen da dong vuot qua swing vua roi nguoc huong thi cau truc da gay: tra range.
    Chua du 2 dinh + 2 day thi tra None - khong doan.
    """
    if len(highs) < 2 or len(lows) < 2:
        return None
    if highs[-1] > highs[-2] and lows[-1] > lows[-2] and close >= lows[-1]:
        return "up"
    if highs[-1] < highs[-2] and lows[-1] < lows[-2] and close <= highs[-1]:
        return "down"
    return "range"


def htf_counter(direction: str, htf: dict[str, Any] | None) -> list[str]:
    """Nhan cac khung (va bias ngay) dang nguoc huong setup, vd ['D', 'H4', 'bias']."""
    htf = htf or {}
    trend, bias = ("down", "short") if direction == "long" else ("up", "long")
    out = [label for key, _, label in HTF_FRAMES if htf.get(key) == trend]
    return out + ["bias"] if htf.get("bias") == bias else out


def htf_text(htf: dict[str, Any] | None) -> str:
    if not htf:
        return ""
    parts = [f"{label} {HTF_WORDS.get(htf.get(key), '?')}" for key, _, label in HTF_FRAMES]
    return " · ".join(parts + [f"bias {htf.get('bias') or '?'}"])


# TM - #ENTRY-VOL - Dieu kien volume cua trigger, dang chu de hien trong tin va trang admin
def volume_rule(kind: str, params: dict[str, Any]) -> str:
    sma = f"SMA{params.get('sma_period', '?')}"
    if kind in ("spring", "utad", "sos", "sow"):
        return f"vol ≥ {float(params['vol_mult']):g}× {sma}"
    if kind == "retest":
        return f"vol ≤ {float(params['retest_low_vol_mult']):g}× {sma}"
    if kind == "rejection":
        bounds = [f"{sign} {float(params[key]):g}×"
                  for sign, key in zip(("≥", "≤"), REJECTION_VOL_KEYS) if params.get(key) is not None]
        if bounds:
            return f"vol {' và '.join(bounds)} {sma}"
    return "không xét vol"


def check_trigger(setup: dict[str, Any], bar: dict[str, Any], history: list[dict[str, Any]],
                  params: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    period = int(params["sma_period"])
    if len(history) < period:
        raise InsufficientData(len(history), period)
    window = history[-period:]
    sma = sum(float(b["volume"]) for b in window) / period
    volume = float(bar["volume"])
    ratio = volume / sma if sma > 0 else None
    vol_ok = sma > 0 and volume >= float(params["vol_mult"]) * sma
    o, h, low, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
    rng = h - low
    body = abs(c - o)
    zl, zh = float(setup["zone_low"]), float(setup["zone_high"])
    lv = levels(setup, params)
    long = setup["direction"] == "long"
    kind = setup["trigger_type"]
    body_ok = rng > 0 and body >= float(params["body_pct"]) * rng
    ref, extreme = lv["level"], None
    if kind == "spring":
        ref, extreme = lv["ref_low"], low
        hit = low < ref and c > ref and vol_ok
    elif kind == "utad":
        ref, extreme = lv["ref_high"], h
        hit = h > ref and c < ref and vol_ok
    elif kind == "sos":
        hit = c > ref and body_ok and vol_ok
    elif kind == "sow":
        hit = c < ref and body_ok and vol_ok
    elif kind == "rejection":
        wick_min = float(params["wick_pct"]) * rng
        if long:
            ref, extreme = zh, low
            hit = rng > 0 and (min(o, c) - low) >= wick_min and low <= zh and c > zh
        else:
            ref, extreme = zl, h
            hit = rng > 0 and (h - max(o, c)) >= wick_min and h >= zl and c < zl
        # TM - #ENTRY-VOL - validate bat buoc khai it nhat mot nguong; setup luu truoc ban nay
        # khong co nguong thi van chi xet hinh nen.
        vol_min, vol_max = (params.get(key) for key in REJECTION_VOL_KEYS)
        if vol_min is not None:
            hit = hit and sma > 0 and volume >= float(vol_min) * sma
        if vol_max is not None:
            hit = hit and sma > 0 and volume <= float(vol_max) * sma
    elif kind == "retest":
        since = setup.get("tr_since")
        prior = [b for b in history if since is None or int(b["open_time"]) >= int(since)]
        prior = prior if since is not None else prior[-RETEST_LOOKBACK:]
        broke = any((float(b["close"]) > ref) if long else (float(b["close"]) < ref) for b in prior)
        touch = low <= zh and h >= zl
        holds = c >= ref if long else c <= ref
        quiet = sma > 0 and volume <= float(params["retest_low_vol_mult"]) * sma
        hit = broke and touch and holds and quiet
    elif kind == "close_above":
        hit = c > ref
    elif kind == "close_below":
        hit = c < ref
    else:
        raise ValueError(f"trigger_type khong ho tro: {kind}")
    return bool(hit), {
        "trigger_type": kind, "ref": ref, "extreme": extreme, "close": c,
        "volume": volume, "vol_sma": round(sma, 8), "vol_ratio": round(ratio, 2) if ratio else None,
        "sma_period": period, "candle_open_time": int(bar["open_time"]),
    }


def step(setup: dict[str, Any], bar: dict[str, Any], ctx: dict[str, Any]
         ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    status = setup["status"]
    state = dict(setup.get("state") or {})
    events: list[dict[str, Any]] = []
    long = setup["direction"] == "long"
    close = float(bar["close"])
    entry, stop, target = float(setup["entry"]), float(setup["stop"]), float(setup["target"])
    zl, zh = float(setup["zone_low"]), float(setup["zone_high"])
    params = ctx["params"]

    def emit(to: str, event: str, notify: bool, **details: Any) -> None:
        nonlocal status
        events.append({"event": event, "from_status": status, "to_status": to, "price": close,
                       "candle_open_time": int(bar["open_time"]), "details": details,
                       "notify": notify})
        status = to

    def finish() -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        state["last_candle"] = int(bar["open_time"])
        state["last_price"] = close
        return status, state, events

    def entry_check() -> None:
        min_rr = float(ctx["min_rr"])
        xm = x_max(setup["direction"], entry, stop, target, min_rr)
        eff = effective_max_chase(setup.get("max_chase"), xm)
        state.update(x_max=round(xm, 8), max_chase=round(eff, 8), min_rr=min_rr)
        chase = close - entry if long else entry - close
        if status != "MISSED" and chase > eff:
            emit("MISSED", "MISSED", True, chase=round(chase, 8), max_chase=round(eff, 8))
            return
        if abs(close - entry) > eff:
            return
        verdict = ctx["check"](close)
        if verdict.get("would_pass"):
            state.update(entry_valid_count=0, entry_valid_price=close)
            emit("ENTRY_VALID", "ENTRY_VALID", True, chase=round(chase, 8),
                 max_chase=round(eff, 8), check=verdict)
            return
        if status == "CONFIRMED":
            reasons = list(verdict.get("rule_violations") or [])
            key = " | ".join(sorted(reasons))
            sent = state.get("blocked_sent") or {}
            if sent.get("date") != ctx["day"]:
                sent = {"date": ctx["day"], "keys": []}
            notify = key not in sent["keys"]
            if notify:
                sent["keys"].append(key)
            state["blocked_sent"] = sent
            emit("BLOCKED", "BLOCKED", notify, rule_violations=reasons, check=verdict)

    def zone_bar(may_exit: bool) -> None:
        hit, detail = False, None
        try:
            hit, detail = check_trigger(setup, bar, ctx["history"], params)
        except InsufficientData as exc:
            emit(status, "INSUFFICIENT_DATA", False, have=exc.have, need=exc.need)
        if hit:
            state.update(triggered_at=int(bar["close_time"]), trigger=detail, confirm_count=0)
            state.pop("confirm", None)
            emit("TRIGGERED", "TRIGGERED", bool(setup.get("confirm_type")), trigger=detail)
            if not setup.get("confirm_type"):
                emit("CONFIRMED", "CONFIRMED", False, confirm=None)
                entry_check()
        elif may_exit:
            came = state.get("came_from") or ("above" if long else "below")
            if (close > zh) if came == "above" else (close < zl):
                emit("WAITING", "ZONE_EXIT", False, came_from=came)

    if status in TERMINAL:
        return status, state, events

    if int(bar["close_time"]) > int(setup["expires_at"]):
        emit("EXPIRED", "EXPIRED", True, expires_at=int(setup["expires_at"]))
        return finish()

    inv = float(setup["invalidate_level"])
    # TM - #DASHBOARD - Setup dashboard: setup breakout co invalidate_level nam ben kia gia luc
    # tao, nen chi xet sau khi da co nen dong dat confirm. Chot lai trong state, khong mat di.
    armed = True
    if setup.get("invalidate_after_confirm"):
        if not state.get("confirm_reached"):
            level = float(setup["confirm_level"])
            if (close > level) if setup["confirm_type"] == "close_above" else (close < level):
                state["confirm_reached"] = True
        armed = bool(state.get("confirm_reached"))
    if armed and ((close < inv) if long else (close > inv)):
        m15 = ctx.get("m15_close")
        m15_ok = (not setup.get("m15_filter")) or (
            m15 is not None and ((m15 < inv) if long else (m15 > inv)))
        if m15_ok:
            emit("INVALIDATED", "INVALIDATED", True, close=close, invalidate_level=inv,
                 m15_close=m15, m15_filter=bool(setup.get("m15_filter")))
            return finish()

    if status == "WAITING":
        if float(bar["low"]) <= zh and float(bar["high"]) >= zl:
            opened = float(bar["open"])
            came = "above" if opened > zh else "below" if opened < zl else (
                "above" if long else "below")
            state["came_from"] = came
            # TM - #ENTRY-HTF - Moi setup chi bao IN_ZONE lan cham dau tien. Cham lai van
            # ghi event va van xet trigger, chi khong gui tin.
            first = not state.get("zone_alerted")
            state["zone_alerted"] = True
            emit("IN_ZONE", "IN_ZONE", first, came_from=came, repeat=not first)
            # Xet trigger ngay tren nen cham vung: voi rejection/spring day thuong chinh la
            # nen tin hieu. Nen nay chua xet ZONE_EXIT, de nen sau moi quyet dinh.
            zone_bar(may_exit=False)
    elif status == "IN_ZONE":
        zone_bar(may_exit=True)
    elif status == "TRIGGERED":
        count = int(state.get("confirm_count") or 0) + 1
        state["confirm_count"] = count
        level = float(setup["confirm_level"])
        met = close > level if setup["confirm_type"] == "close_above" else close < level
        if met:
            state["confirm"] = {"close": close, "level": level, "candles": count}
            emit("CONFIRMED", "CONFIRMED", False, confirm=state["confirm"])
        elif count >= int(params["confirm_within"]):
            emit("WAITING", "TRIGGER_FAILED", True, candles=count, confirm_level=level)
    elif status in ("CONFIRMED", "MISSED"):
        entry_check()
    elif status == "ENTRY_VALID":
        count = int(state.get("entry_valid_count") or 0) + 1
        state["entry_valid_count"] = count
        if count >= int(params["entry_valid_candles"]):
            emit("WAITING", "ENTRY_WINDOW_EXPIRED", True, candles=count)
    elif status == "BLOCKED":
        emit("WAITING", "UNBLOCKED", False)
    return finish()


# TM - #ENTRY-VOL - rejection phai noi ro dang can vol cao hay vol thap
def _rejection_volume(params: dict[str, Any]) -> dict[str, Any]:
    out = dict(params)
    for key in REJECTION_VOL_KEYS:
        if key not in params:
            continue
        try:
            value = float(params[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} phai la so, nhan duoc {params[key]!r}") from None
        if not 0 < value < float("inf"):
            raise ValueError(f"{key} phai > 0")
        out[key] = value
    vol_min, vol_max = (out.get(key) for key in REJECTION_VOL_KEYS)
    if vol_min is None and vol_max is None:
        raise ValueError("rejection phai khai trigger_params.max_vol_mult (test, vol thap) hoac "
                         "min_vol_mult (stopping action, vol cao), boi so cua SMA volume M5")
    if vol_min is not None and vol_max is not None and vol_min > vol_max:
        raise ValueError("min_vol_mult phai <= max_vol_mult")
    return out


def validate(raw: dict[str, Any], *, min_rr: float, now_ms: int,
             parse_time: Callable[[Any], int | None], default_expiry: Callable[[str], int],
             default_day: str, default_m15: bool) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("moi setup phai la object")

    def num(name: str, required: bool = True) -> float | None:
        value = raw.get(name)
        if value is None or value == "":
            if required:
                raise ValueError(f"thieu truong bat buoc {name}")
            return None
        try:
            out = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} phai la so, nhan duoc {value!r}") from None
        if out != out or out in (float("inf"), float("-inf")):
            raise ValueError(f"{name} khong hop le")
        return out

    direction = str(raw.get("direction") or "").strip().lower()
    if direction not in DIRECTIONS:
        raise ValueError("direction phai la long hoac short")
    kind = str(raw.get("trigger_type") or "").strip().lower()
    if kind not in TRIGGERS:
        raise ValueError(f"trigger_type phai la mot trong {list(TRIGGERS)}")
    if direction not in TRIGGERS[kind]:
        raise ValueError(f"trigger_type {kind} chi dung cho {'/'.join(TRIGGERS[kind])}")
    zone_low, zone_high = num("zone_low"), num("zone_high")
    entry, stop, target = num("entry"), num("stop"), num("target")
    invalidate = num("invalidate_level")
    if not zone_low < zone_high:
        raise ValueError("zone_low phai nho hon zone_high")
    long = direction == "long"
    if (stop >= entry) if long else (stop <= entry):
        raise ValueError(f"stop phai nam {'duoi' if long else 'tren'} entry ({direction})")
    if (target <= entry) if long else (target >= entry):
        raise ValueError(f"target phai nam {'tren' if long else 'duoi'} entry ({direction})")
    if (invalidate >= entry) if long else (invalidate <= entry):
        raise ValueError(f"invalidate_level phai nam {'duoi' if long else 'tren'} entry ({direction})")
    confirm = raw.get("confirm_type")
    confirm = str(confirm).strip().lower() if confirm not in (None, "", "none", "null") else None
    confirm_level = None
    if confirm is not None:
        if confirm not in CONFIRMS:
            raise ValueError(f"confirm_type phai la null hoac mot trong {list(CONFIRMS)}")
        confirm_level = num("confirm_level")
    # TM - #DASHBOARD - Setup dashboard: mac dinh true khi co confirm_type
    after_confirm = raw.get("invalidate_after_confirm")
    after_confirm = (confirm is not None if after_confirm is None
                     else after_confirm not in (False, 0, "0", "false"))
    if after_confirm and confirm is None:
        raise ValueError("invalidate_after_confirm chi dung duoc khi co confirm_type")
    max_chase = num("max_chase", required=False)
    if max_chase is not None and max_chase <= 0:
        raise ValueError("max_chase phai > 0 (bo trong = tu tinh)")
    params = raw.get("trigger_params") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params) if params.strip() else {}
        except ValueError:
            raise ValueError("trigger_params khong phai JSON hop le") from None
    if not isinstance(params, dict):
        raise ValueError("trigger_params phai la object")
    params = {k: v for k, v in params.items() if v is not None}
    unknown = sorted(set(params) - set(PARAM_KEYS) - set(LEVEL_KEYS) - set(REJECTION_VOL_KEYS))
    if unknown:
        raise ValueError(f"trigger_params co khoa khong ho tro: {unknown}")
    # TM - #ENTRY-VOL - Tham so trigger khong dung thi tu choi, khong am tham bo qua
    allowed = {*TRIGGER_PARAMS[kind], "entry_valid_candles", *(["confirm_within"] if confirm else [])}
    unused = sorted(set(params) - allowed)
    if unused:
        raise ValueError(f"trigger {kind} khong dung tham so {unused}; chi nhan {sorted(allowed)}")
    if kind == "rejection":
        params = _rejection_volume(params)
    xm = x_max(direction, entry, stop, target, min_rr)
    if xm <= 0:
        rr = abs(target - entry) / abs(entry - stop)
        raise ValueError(f"setup khong dat min_rr {min_rr:g} ngay tai entry (R:R {rr:.2f}, "
                         f"x_max {xm:.2f} <= 0)")
    day = str(raw.get("bias_date") or default_day).strip()
    expires = parse_time(raw.get("expires_at"))
    if expires is None:
        expires = default_expiry(day)
    if expires <= now_ms:
        raise ValueError("expires_at da qua")
    m15 = raw.get("m15_filter")
    tr_since = parse_time(raw.get("tr_since"))
    return {
        "bias_date": day, "direction": direction, "zone_low": zone_low, "zone_high": zone_high,
        "trigger_type": kind, "trigger_params": params or None, "confirm_type": confirm,
        "confirm_level": confirm_level, "entry": entry, "stop": stop, "target": target,
        "max_chase": max_chase, "invalidate_level": invalidate,
        "m15_filter": bool(default_m15 if m15 is None else m15 not in (False, 0, "0", "false")),
        "expires_at": int(expires), "tr_high": num("tr_high", required=False),
        "tr_low": num("tr_low", required=False), "tr_since": tr_since,
        "note": str(raw.get("note") or "") or None, "x_max": xm,
        "max_chase_effective": effective_max_chase(max_chase, xm),
        "invalidate_after_confirm": after_confirm,
    }


# TM - #DASHBOARD - Setup dashboard
def invalidate_broken(direction: str, invalidate_level: float, price: float) -> bool:
    return price <= invalidate_level if direction == "long" else price >= invalidate_level
