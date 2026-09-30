"""Bo rule ORB rieng - logic thuan, khong I/O.

# TM - #ORB-RULES - ORB Rule Set

Lenh ORB duoc cham bang bo rule nay, tach khoi rule scalp/swing cua cap. Chi
`max_margin_per_trade` con dung chung voi moi chien luoc (BR-ORB-08).

Khoi `orb` trong rules.json:

    {"enabled": true,
     "defaults": {"max_trades_per_day": 2, "tp_r": 1.5, ...},
     "symbol_overrides": {"BTCUSDT": {"max_stop_points": 350, ...}}}

Thu tu uu tien, xet TUNG truong (BR-ORB-03, BR-ORB-16):
    override tam thoi (backtest rules_override / xem truoc tren admin)
    > override cua phien (chi nhom SESSION_FIELDS)
    > symbol_overrides.<SYMBOL>
    > defaults
Khong bao gio roi ve rule scalp/swing hay rule chung. Truong bat buoc khong co o
dau ca -> fail closed voi loi `chua dat rule ORB cho <SYMBOL>.<field>` (BR-ORB-04).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable

# ten -> kieu, can duoi, can tren, duoi co mo (>) hay dong (>=), tren mo hay dong, mo ta
FIELDS: dict[str, dict[str, Any]] = {
    # BR-ORB-01: nguong cham lenh
    "max_stop_points": {"kind": "float", "min": 0.0, "min_open": True, "max": 1e7,
                        "label": "SL toi da (diem)"},
    "min_take_profit_points": {"kind": "float", "min": 0.0, "min_open": True, "max": 1e7,
                               "label": "TP toi thieu (diem)"},
    "min_rr": {"kind": "float", "min": 0.0, "min_open": True, "max": 100.0,
               "label": "R:R toi thieu"},
    "max_trades_per_day": {"kind": "int", "min": 0, "max": 50,
                           "label": "So lenh ORB toi da / ngay (theo or_date)"},
    "daily_stop_loss": {"kind": "float", "min": -1e9, "max": 0.0,
                        "label": "Daily stop ORB (USD, <= 0)"},
    # BR-ORB-15: sizing theo margin co dinh + an toan thanh ly
    "margin_usd": {"kind": "float", "min": 0.0, "min_open": True, "max": 1e7,
                   "label": "Margin moi lenh (USD)"},
    "leverage": {"kind": "float", "min": 1.0, "max": 125.0, "label": "Don bay (x)"},
    "maint_margin_pct": {"kind": "float", "min": 0.0, "max": 100.0, "max_open": True,
                         "label": "Maintenance margin (%)"},
    "liq_safety_pct": {"kind": "float", "min": 0.0, "min_open": True, "max": 100.0,
                       "label": "SL toi da trong vung thanh ly (%)"},
    # BR-ORB-16: bo loc range
    "min_or_atr_ratio": {"kind": "float", "min": 0.0, "min_open": True, "max": 10.0,
                         "label": "OR/ATR toi thieu"},
    "max_or_atr_ratio": {"kind": "float", "min": 0.0, "min_open": True, "max": 10.0,
                         "label": "OR/ATR toi da"},
    # BR-ORB-17: tham so chien luoc
    "tp_r": {"kind": "float", "min": 0.0, "min_open": True, "max": 20.0, "label": "TP (R)"},
    "buffer_pct": {"kind": "float", "min": 0.0, "max": 5.0, "label": "Buffer (%)"},
    "move_sl_to_be_at_r": {"kind": "float", "min": 0.0, "max": 20.0,
                           "label": "Doi SL ve hoa von khi dat (R), 0 = tat"},
    "time_exit_minutes": {"kind": "int", "min": 5, "max": 1440,
                          "label": "Thoat theo thoi gian (phut)"},
    "use_bias_filter": {"kind": "bool", "label": "Loc theo bias"},
    "allow_reversal": {"kind": "bool", "label": "Cho vao lenh dao chieu (failed breakout)"},
    "skip_news_days": {"kind": "bool", "label": "Bo ngay co tin lon"},
}

TRADE_FIELDS = ("max_stop_points", "min_take_profit_points", "min_rr",
                "max_trades_per_day", "daily_stop_loss")
SIZING_FIELDS = ("margin_usd", "leverage", "maint_margin_pct", "liq_safety_pct")
RANGE_FIELDS = ("min_or_atr_ratio", "max_or_atr_ratio")
STRATEGY_FIELDS = ("tp_r", "buffer_pct", "move_sl_to_be_at_r", "time_exit_minutes",
                   "use_bias_filter", "allow_reversal", "skip_news_days")
# Phien duoc ghi de hai nhom nay (BR-ORB-16, BR-ORB-17); nguong cham lenh va
# sizing thi chi theo cap.
SESSION_FIELDS = RANGE_FIELDS + STRATEGY_FIELDS
# Truong bat buoc de cham mot lenh ORB (check_trade / log_trade)
SCORE_FIELDS = TRADE_FIELDS + ("leverage", "maint_margin_pct", "liq_safety_pct")
# Truong bat buoc de tinh tin hieu va plan
SIGNAL_FIELDS = SESSION_FIELDS + ("margin_usd", "leverage")

# Gia tri mac dinh cua schema (BR muc 4)
DEFAULTS: dict[str, Any] = {
    "max_trades_per_day": 2,
    "min_or_atr_ratio": 0.3,
    "max_or_atr_ratio": 0.7,
    "tp_r": 1.5,
    "buffer_pct": 0.02,
    "move_sl_to_be_at_r": 1.0,
    "time_exit_minutes": 180,
    "use_bias_filter": True,
    "allow_reversal": False,
    "skip_news_days": True,
}
# Gia tri khoi diem BTC da chot 30/09
BTC_SEED: dict[str, Any] = {
    "max_stop_points": 350.0,
    "min_take_profit_points": 200.0,
    "min_rr": 1.5,
    "daily_stop_loss": -25.0,
    "margin_usd": 20.0,
    "leverage": 100.0,
    "maint_margin_pct": 0.4,
    "liq_safety_pct": 80.0,
}
SEED_SYMBOLS = {"BTCUSDT": BTC_SEED}

# Field trong khoi orb cua config.yaml da chuyen sang rules.json (BR-ORB-18):
# (section, key) -> noi thay the
DEPRECATED_YAML: dict[tuple[str | None, str], str] = {
    ("risk", "account_equity"): "sizing theo orb.margin_usd x orb.leverage",
    ("risk", "risk_usd"): "sizing theo orb.margin_usd x orb.leverage",
    ("risk", "max_margin_usd"): "orb.margin_usd + max_margin_per_trade (rule chung)",
    ("risk", "max_leverage"): "orb.leverage",
    (None, "max_orb_trades_per_day"): "orb.max_trades_per_day",
    ("filters", "min_or_atr_ratio"): "orb.min_or_atr_ratio",
    ("filters", "max_or_atr_ratio"): "orb.max_or_atr_ratio",
    ("entry", "buffer_pct"): "orb.buffer_pct",
    ("entry", "allow_reversal"): "orb.allow_reversal",
    ("exit", "tp_r"): "orb.tp_r",
    ("exit", "move_sl_to_be_at_r"): "orb.move_sl_to_be_at_r",
    ("exit", "time_exit_minutes"): "orb.time_exit_minutes",
    ("filters", "use_bias_filter"): "orb.use_bias_filter",
    ("filters", "skip_news_days"): "orb.skip_news_days",
}
# Field cua config.yaml doc mot lan khi migrate (BR muc 6) -> truong ORB
MIGRATE_FROM_YAML: dict[tuple[str | None, str], str] = {
    (None, "max_orb_trades_per_day"): "max_trades_per_day",
    ("filters", "min_or_atr_ratio"): "min_or_atr_ratio",
    ("entry", "buffer_pct"): "buffer_pct",
    ("entry", "allow_reversal"): "allow_reversal",
    ("exit", "tp_r"): "tp_r",
    ("exit", "move_sl_to_be_at_r"): "move_sl_to_be_at_r",
    ("exit", "time_exit_minutes"): "time_exit_minutes",
    ("filters", "use_bias_filter"): "use_bias_filter",
    ("filters", "skip_news_days"): "skip_news_days",
    # max_or_atr_ratio KHONG lay tu yaml: BR-ORB-16 chot mac dinh 0.7
}

SOURCES = ("override", "session", "symbol", "defaults")


# ---------------------------------------------------------------- validate

def _rule_text(spec: dict[str, Any]) -> str:
    low, high = spec.get("min"), spec.get("max")
    lo = f"> {low:g}" if spec.get("min_open") else f">= {low:g}"
    hi = f"< {high:g}" if spec.get("max_open") else f"<= {high:g}"
    if spec["kind"] == "int":
        return f"so nguyen {lo} va {hi}"
    return f"{lo} va {hi}"


def coerce_bool(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"orb.{field} phai la true/false")


def coerce(field: str, value: Any) -> Any:
    """Ep kieu + kiem rang buoc cho mot truong ORB. ValueError voi loi ro rang."""
    spec = FIELDS.get(field)
    if spec is None:
        raise ValueError(f"truong ORB khong ho tro: {field}. Cho phep: {sorted(FIELDS)}")
    if spec["kind"] == "bool":
        return coerce_bool(field, value)
    if isinstance(value, bool):
        raise ValueError(f"orb.{field} phai la so, nhan duoc {value!r}")
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"orb.{field} phai la so, nhan duoc {value!r}") from None
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"orb.{field} khong hop le")
    low, high = spec["min"], spec["max"]
    too_low = number <= low if spec.get("min_open") else number < low
    too_high = number >= high if spec.get("max_open") else number > high
    is_int = number == int(number)
    if too_low or too_high or (spec["kind"] == "int" and not is_int):
        raise ValueError(f"orb.{field} phai {_rule_text(spec)}, nhan duoc {value!r}")
    return int(number) if spec["kind"] == "int" else number


def consistency(values: dict[str, Any], window_minutes: int | None = None) -> list[str]:
    """Rang buoc giua cac truong: 0 < min < max (BR-ORB-16), time_exit <= cua so
    phien (BR-ORB-17). Tra ve danh sach loi - rong la hop le."""
    errors: list[str] = []
    low, high = values.get("min_or_atr_ratio"), values.get("max_or_atr_ratio")
    if low is not None and high is not None and not float(low) < float(high):
        errors.append(f"min_or_atr_ratio ({float(low):g}) phai nho hon "
                      f"max_or_atr_ratio ({float(high):g})")
    minutes = values.get("time_exit_minutes")
    if window_minutes and minutes is not None and int(minutes) > int(window_minutes):
        errors.append(f"time_exit_minutes ({int(minutes)}) phai <= trade_window_minutes "
                      f"cua phien ({int(window_minutes)})")
    return errors


# ---------------------------------------------------------------- khoi orb

def _clean_layer(raw: Any, allowed: Iterable[str] = FIELDS) -> dict[str, Any]:
    """Giu cac truong hop le. Gia tri sai trong file bi bo - truong do coi nhu chua
    dat, nen cham lenh fail closed thay vi dung mot con so hong."""
    out: dict[str, Any] = {}
    allowed = set(allowed)
    for key, value in (raw.items() if isinstance(raw, dict) else ()):
        if key not in allowed or value is None:
            continue
        try:
            out[key] = coerce(key, value)
        except ValueError:
            continue
    return out


def normalize_block(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    try:
        enabled = coerce_bool("enabled", raw.get("enabled", True))
    except ValueError:
        enabled = True
    symbols = raw.get("symbol_overrides") if isinstance(raw.get("symbol_overrides"), dict) else {}
    return {
        "enabled": enabled,
        "defaults": _clean_layer(raw.get("defaults")),
        "symbol_overrides": {str(sym).strip().upper(): _clean_layer(layer)
                             for sym, layer in sorted(symbols.items())
                             if str(sym).strip() and isinstance(layer, dict)},
    }


def _yaml_value(cfg: dict[str, Any], section: str | None, key: str) -> Any:
    source = cfg if section is None else (cfg.get(section) or {})
    return source.get(key) if isinstance(source, dict) else None


def migrate(cfg: dict[str, Any], symbol: str = "BTCUSDT",
            at: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Tao khoi orb lan dau tu config.yaml (BR muc 6) + mot dong history.

    Quota lay tu `max_orb_trades_per_day`; min_or_atr_ratio va tham so chien luoc
    lay tu yaml neu hop le (giu nguyen hanh vi dang chay); max_or_atr_ratio dung
    0.7 theo BR-ORB-16. Cap ORB co gia tri khoi diem da chot (BTC) thi seed luon.
    """
    cfg = cfg or {}
    defaults = dict(DEFAULTS)
    for (section, key), field in MIGRATE_FROM_YAML.items():
        value = _yaml_value(cfg, section, key)
        if value is None:
            continue
        try:
            defaults[field] = coerce(field, value)
        except ValueError:
            continue
    if not defaults["min_or_atr_ratio"] < defaults["max_or_atr_ratio"]:
        defaults["min_or_atr_ratio"] = DEFAULTS["min_or_atr_ratio"]
    sym = str(symbol or "BTCUSDT").strip().upper()
    overrides = {sym: dict(SEED_SYMBOLS[sym])} if sym in SEED_SYMBOLS else {}
    block = {"enabled": True, "defaults": defaults, "symbol_overrides": overrides}
    changes = {f"orb.defaults.{k}": {"from": None, "to": v} for k, v in defaults.items()}
    for s, layer in overrides.items():
        changes.update({f"orb.symbol_overrides.{s}.{k}": {"from": None, "to": v}
                        for k, v in layer.items()})
    row = {
        "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "orb",
        "changes": changes,
        "reason": "migrate tu config.yaml",
        "source": "migrate",
    }
    return block, row


def deprecated_fields(cfg: dict[str, Any]) -> list[str]:
    """Field cu con nam trong khoi orb cua config.yaml - bi bo qua (BR-ORB-18)."""
    out = []
    for (section, key), target in DEPRECATED_YAML.items():
        if _yaml_value(cfg or {}, section, key) is not None:
            path = f"orb.{key}" if section is None else f"orb.{section}.{key}"
            out.append(f"deprecated: config.yaml {path} bi bo qua - dung {target} "
                       "trong rules.json (trang /admin/orb)")
    return out


def resolve(block: dict[str, Any], symbol: str,
            session_values: dict[str, Any] | None = None,
            override: dict[str, Any] | None = None,
            session_id: str | None = None) -> dict[str, Any]:
    """Giai bo rule ORB cho mot cap (+ phien). Moi truong ghi ro nguon."""
    block = block or {}
    sym = str(symbol or "").strip().upper()
    layers = [
        ("override", _clean_layer(override)),
        ("session", _clean_layer(session_values, SESSION_FIELDS)),
        ("symbol", (block.get("symbol_overrides") or {}).get(sym) or {}),
        ("defaults", block.get("defaults") or {}),
    ]
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for field in FIELDS:
        for name, layer in layers:
            if field in layer:
                values[field] = layer[field]
                sources[field] = name
                break
    return {
        "symbol": sym,
        "session_id": session_id,
        "enabled": bool(block.get("enabled", True)),
        "values": values,
        "sources": sources,
        "missing": [f for f in FIELDS if f not in values],
    }


def missing_errors(resolved: dict[str, Any], fields: Iterable[str]) -> list[str]:
    return [f"chua dat rule ORB cho {resolved['symbol']}.{f}"
            for f in fields if f not in resolved["values"]]


# ---------------------------------------------------------------- tinh toan

def one_way_cost_pct(costs: dict[str, Any]) -> float:
    return float(costs.get("taker_fee_pct") or 0) + float(costs.get("slippage_pct") or 0)


def liq_limit(entry: float, values: dict[str, Any],
              costs: dict[str, Any]) -> dict[str, Any] | None:
    """Khoang cach SL toi da cho phep theo an toan thanh ly (BR-ORB-15).

    liq_distance = entry x (1/leverage - maint_margin_pct - phi 1 chieu)
    limit        = liq_distance x liq_safety_pct
    Phi 1 chieu = taker + truot gia cua `costs` trong config.yaml.
    """
    if not entry or any(values.get(f) is None
                        for f in ("leverage", "maint_margin_pct", "liq_safety_pct")):
        return None
    lev = float(values["leverage"])
    fraction = 1.0 / lev - float(values["maint_margin_pct"]) / 100 - one_way_cost_pct(costs) / 100
    distance = max(0.0, float(entry) * fraction)
    return {
        "liq_distance_points": round(distance, 2),
        "limit_points": round(distance * float(values["liq_safety_pct"]) / 100, 2),
        "leverage": lev,
        "maint_margin_pct": float(values["maint_margin_pct"]),
        "liq_safety_pct": float(values["liq_safety_pct"]),
        "one_way_cost_pct": round(one_way_cost_pct(costs), 4),
    }


def day_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """So lenh + PnL rong cua cac lenh ORB (da loc theo or_date)."""
    closed = [r for r in rows if r.get("pnl") is not None]
    return {"trades": len(rows), "closed": len(closed),
            "pnl": round(sum(float(r["pnl"]) for r in closed), 2)}


# Sai so lam tron khi so R:R: plan lam tron entry/SL/TP 2 chu so (TP tinh tu entry
# chua lam tron) - sai lech toi da ~0.025 diem khi tp_r == min_rr
RR_TOLERANCE_POINTS = 0.05


def score(*, symbol: str, side: str, entry: float, stop: float, target: float,
          margin_usd: float, trade_type: str, resolved: dict[str, Any],
          max_margin_per_trade: float | None, day_trades: int, day_pnl: float,
          or_date: str, costs: dict[str, Any]) -> dict[str, Any]:
    """Cham mot lenh ORB (BR-ORB-02). Cung dang ket qua voi rule scalp/swing de
    tool cu doc duoc, cong them rule_set = "orb". trade_type bi bo qua: khong co
    han muc swing, khong demote."""
    side = side.strip().lower()
    if side not in ("long", "short"):
        raise ValueError("side phai la long hoac short")
    values = resolved["values"]
    stop_points = round(abs(float(entry) - float(stop)), 2)
    target_points = round(abs(float(target) - float(entry)), 2)
    rr = round(target_points / stop_points, 2) if stop_points else None
    violations: list[str] = []

    if not resolved.get("enabled", True):
        violations.append("ORB dang tat (orb.enabled = false)")
    violations += missing_errors(resolved, SCORE_FIELDS)

    stop_rule = values.get("daily_stop_loss")
    if stop_rule is not None and float(day_pnl) <= float(stop_rule):
        violations.append(f"da cham orb.daily_stop_loss: PnL ORB {or_date} "
                          f"{round(float(day_pnl), 2)} <= {float(stop_rule):g}")
    cap = values.get("max_trades_per_day")
    if cap is not None and int(day_trades) >= int(cap):
        violations.append(f"vuot orb.max_trades_per_day ({int(cap)}): da co {int(day_trades)} "
                          f"lenh ORB ngay {or_date}")
    max_stop = values.get("max_stop_points")
    if max_stop is not None and stop_points > float(max_stop):
        violations.append(f"SL {stop_points} > orb.max_stop_points ({float(max_stop):g})")
    min_tp = values.get("min_take_profit_points")
    if min_tp is not None and target_points < float(min_tp):
        violations.append(f"TP {target_points} < orb.min_take_profit_points ({float(min_tp):g})")
    min_rr = values.get("min_rr")
    if (min_rr is not None and stop_points
            and target_points + RR_TOLERANCE_POINTS < float(min_rr) * stop_points):
        violations.append(f"R:R {rr} < orb.min_rr ({float(min_rr):g})")
    liq = liq_limit(float(entry), values, costs)
    if liq is not None and stop_points > liq["limit_points"]:
        violations.append(
            f"SL vuot vung an toan thanh ly ({liq['limit_points']:g} diem): SL {stop_points} "
            f"> {liq['liq_safety_pct']:g}% cua ~{liq['liq_distance_points']:g} diem den gia "
            f"thanh ly o x{liq['leverage']:g}")
    max_margin = float(max_margin_per_trade or 0)
    if max_margin > 0 and float(margin_usd) > max_margin:
        violations.append(f"margin {float(margin_usd)} > max_margin_per_trade ({max_margin:g})")

    sources = resolved["sources"]

    def scope(field: str) -> str:
        src = sources.get(field)
        if src == "symbol":
            return f"orb rieng {resolved['symbol']}"
        return f"orb {src}" if src else "chua dat"

    return {
        "symbol": symbol,
        "side": side,
        "trade_type": (trade_type or "scalp").strip().lower(),
        "demoted_to_scalp": False,
        "entry": float(entry),
        "stop": float(stop),
        "target": float(target),
        "stop_points": stop_points,
        "target_points": target_points,
        "rr": rr,
        "margin_usd": float(margin_usd),
        "limits_applied": {
            "rule_set": "orb",
            "symbol": symbol,
            "max_margin_per_trade": max_margin or "khong gioi han",
            "max_stop_points": values.get("max_stop_points"),
            "min_take_profit_points": values.get("min_take_profit_points"),
            "min_rr": values.get("min_rr"),
            "max_trades_per_day": values.get("max_trades_per_day"),
            "daily_stop_loss": values.get("daily_stop_loss"),
            "liq_limit_points": liq["limit_points"] if liq else None,
            "leverage": values.get("leverage"),
            "maint_margin_pct": values.get("maint_margin_pct"),
            "liq_safety_pct": values.get("liq_safety_pct"),
            "max_stop_points_scope": scope("max_stop_points"),
            "min_take_profit_points_scope": scope("min_take_profit_points"),
            "max_margin_per_trade_scope": "chung moi chien luoc",
            "sources": dict(sources),
            "or_date": or_date,
            "orb_day_trades": int(day_trades),
            "orb_day_pnl": round(float(day_pnl), 2),
        },
        "rule_violations": violations,
        "rule_set": "orb",
    }


def infeasible(values: dict[str, Any], atr: float | None, price: float | None,
               costs: dict[str, Any] | None = None) -> list[str]:
    """Canh bao cau hinh khong bao gio dat (BR-ORB-11), kem con so.

    atr = ATR H1 hien tai, price = gia hien tai (de doi buffer_pct ra diem).
    """
    out: list[str] = []
    if not atr:
        return out
    atr = float(atr)
    get = values.get
    tp_r, lo, hi = get("tp_r"), get("min_or_atr_ratio"), get("max_or_atr_ratio")
    max_stop, min_tp, min_rr = get("max_stop_points"), get("min_take_profit_points"), get("min_rr")
    if tp_r is not None and hi is not None and min_tp is not None:
        tp_max = float(tp_r) * float(hi) * atr
        if tp_max < float(min_tp):
            out.append(
                f"rule_infeasible: TP toi da ~{tp_max:.0f} diem (tp_r {float(tp_r):g} x "
                f"max_or_atr_ratio {float(hi):g} x ATR H1 {atr:.2f}) < "
                f"orb.min_take_profit_points ({float(min_tp):g}) - moi tin hieu ORB deu truot TP")
    if lo is not None and max_stop is not None:
        sl_min = float(lo) * atr
        if sl_min > float(max_stop):
            out.append(
                f"rule_infeasible: SL nho nhat ~{sl_min:.0f} diem (min_or_atr_ratio {float(lo):g} x "
                f"ATR H1 {atr:.2f}) > orb.max_stop_points ({float(max_stop):g})")
    if hi is not None and max_stop is not None:
        or_max = float(hi) * atr
        buffer = float(price) * float(get("buffer_pct") or 0) / 100 if price else 0.0
        if or_max + buffer > float(max_stop):
            out.append(
                f"rule_infeasible: OR toi da ~{or_max:.0f} + buffer ~{buffer:.0f} = "
                f"~{or_max + buffer:.0f} diem > orb.max_stop_points ({float(max_stop):g}) - bo loc "
                f"range (max_or_atr_ratio {float(hi):g}) cho qua nhung OR ma rule SL se chan")
    if tp_r is not None and min_rr is not None and float(tp_r) < float(min_rr):
        out.append(f"rule_infeasible: tp_r {float(tp_r):g} < orb.min_rr ({float(min_rr):g}) - "
                   "moi plan deu truot R:R")
    liq = liq_limit(float(price), values, costs or {}) if price else None
    if liq is not None and max_stop is not None and float(max_stop) > liq["limit_points"]:
        out.append(
            f"liq_unsafe: orb.max_stop_points ({float(max_stop):g}) > vung an toan thanh ly "
            f"~{liq['limit_points']:.0f} diem o x{liq['leverage']:g} (gia ~{float(price):.0f}) - "
            "SL xa hon muc do se bi chan")
    return out
