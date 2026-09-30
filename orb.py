"""ORB (Opening Range Breakout) - logic thuan, khong I/O.

# TM - #ORB - ORB Enhancement

Module nay chi tinh toan: gio mo phien theo timezone (co DST), ngay le, validate
phien, Opening Range, ATR, state machine tin hieu, ke hoach lenh va sizing.
Khong doc file, khong goi mang, khong doc gio he thong - moi moc thoi gian deu
duoc truyen vao duoi dang epoch ms UTC. Nho vay cung du lieu + cung config thi
luon ra cung ket qua, va test duoc tung ham rieng.

Quy uoc nen (giong server._normalize_bars):
    {"open_time": ms, "open", "high", "low", "close", "volume", "close_time": ms}
Mot nen DA DONG khi open_time + do dai khung <= thoi diem dang xet.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import orb_rules  # TM - #ORB-RULES - ORB Rule Set

UTC = timezone.utc
# Gio VN co dinh UTC+7, khong co DST. Dung offset co dinh de khong phu thuoc
# bien TZ cua container.
VN = timezone(timedelta(hours=7))

M5_MS = 5 * 60_000
M15_MS = 15 * 60_000
H1_MS = 60 * 60_000

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
STATES = ("WAITING_OPEN", "FORMING", "RANGE_SET", "BREAKOUT_LONG", "BREAKOUT_SHORT",
          "FAILED_BREAKOUT_LONG", "FAILED_BREAKOUT_SHORT", "FILTERED", "EXPIRED",
          "SKIPPED", "TAKEN")
STOP_REASONS = ("taken", "skipped", "window_expired", "filtered")
ENTRY_MODES = ("close", "touch", "retest")
SL_MODES = ("opposite", "mid")
HOLIDAY_CALENDARS = ("none", "US", "UK")
STRATEGIES = ("ORB", "WYCKOFF", "OTHER")
VARIANTS = ("breakout", "retest", "reversal")

# Cua so nen H1 dung de tinh ATR kieu Wilder. Co dinh de live va backtest cung
# mot cach tinh: sau 200 nen, anh huong cua gia tri khoi dau nho hon 1e-6.
ATR_WINDOW = 200
# So nen dung lam trung binh cho volume_vs_avg
VOLUME_AVG_BARS = 20


# ---------------------------------------------------------------- thoi gian

def to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def from_ms(ts: int) -> datetime:
    return datetime.fromtimestamp(ts / 1000, UTC)


def iso_utc(ts: int | None) -> str | None:
    if ts is None:
        return None
    return from_ms(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_vn(ts: int | None) -> str | None:
    if ts is None:
        return None
    return from_ms(ts).astimezone(VN).strftime("%Y-%m-%dT%H:%M:%S+07:00")


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"ngay khong hop le: {value!r} (can YYYY-MM-DD)") from None


def parse_utc(value: str) -> int:
    """Nhan '2026-09-29T13:40:00Z', '2026-09-29 13:40' ... - luon hieu la UTC
    neu khong ghi offset."""
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"thoi diem khong hop le: {value!r} (can ISO, UTC)") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return to_ms(dt)


def parse_hhmm(value: str) -> dtime:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or "").strip())
    if not match:
        raise ValueError("open_time phai co dang HH:mm")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("open_time ngoai khoang 00:00-23:59")
    return dtime(hour, minute)


def session_tz(session: dict[str, Any]) -> ZoneInfo:
    return ZoneInfo(str(session["timezone"]))


def local_date(session: dict[str, Any], ts: int) -> date:
    """Ngay theo timezone cua phien tai thoi diem ts."""
    return from_ms(ts).astimezone(session_tz(session)).date()


def session_open_ms(session: dict[str, Any], day: date) -> int:
    """Gio mo cua phien vao ngay `day` (lich cua phien), quy ra epoch ms UTC.

    Quy doi TUNG NGAY qua zoneinfo, nen tu dung qua moc DST cua tung nuoc -
    khong bao gio cong/tru mot offset co dinh.
    """
    local = datetime.combine(day, parse_hhmm(session["open_time"]), tzinfo=session_tz(session))
    return to_ms(local.astimezone(UTC))


# ---------------------------------------------------------------- ngay le

def easter_sunday(year: int) -> date:
    """Thuat toan Anonymous Gregorian (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n = 1..5 tinh tu dau thang, n = -1 la lan cuoi cung trong thang."""
    if n > 0:
        first = date(year, month, 1)
        shift = (weekday - first.weekday()) % 7
        return first + timedelta(days=shift + 7 * (n - 1))
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed_us(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def us_holidays(year: int) -> dict[date, str]:
    """Ngay nghi cua NYSE (khong tinh ngay dong cua som)."""
    out: dict[date, str] = {}
    new_year = date(year, 1, 1)
    # NYSE khong nghi bu vao thu Sau 31/12 khi 1/1 roi vao thu Bay
    if new_year.weekday() == 6:
        out[new_year + timedelta(days=1)] = "New Year's Day (observed)"
    elif new_year.weekday() < 5:
        out[new_year] = "New Year's Day"
    out[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    out[_nth_weekday(year, 2, 0, 3)] = "Washington's Birthday"
    out[easter_sunday(year) - timedelta(days=2)] = "Good Friday"
    out[_nth_weekday(year, 5, 0, -1)] = "Memorial Day"
    if year >= 2022:
        out[_observed_us(date(year, 6, 19))] = "Juneteenth"
    out[_observed_us(date(year, 7, 4))] = "Independence Day"
    out[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    out[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
    out[_observed_us(date(year, 12, 25))] = "Christmas Day"
    for special, name in _US_SPECIAL.items():
        if special.year == year:
            out[special] = name
    return out


_US_SPECIAL = {
    date(2018, 12, 5): "National Day of Mourning (G.H.W. Bush)",
    date(2025, 1, 9): "National Day of Mourning (J. Carter)",
}


def uk_holidays(year: int) -> dict[date, str]:
    """Bank holiday England & Wales - cung la ngay nghi cua LSE."""
    out: dict[date, str] = {}
    new_year = date(year, 1, 1)
    while new_year.weekday() >= 5:
        new_year += timedelta(days=1)
    out[new_year] = "New Year's Day"
    easter = easter_sunday(year)
    out[easter - timedelta(days=2)] = "Good Friday"
    out[easter + timedelta(days=1)] = "Easter Monday"
    early_may = _nth_weekday(year, 5, 0, 1)
    spring = _nth_weekday(year, 5, 0, -1)
    if year == 2020:
        early_may = date(2020, 5, 8)          # doi sang ngay VE Day
    if year == 2022:
        spring = date(2022, 6, 2)             # Platinum Jubilee
    out[early_may] = "Early May Bank Holiday"
    out[spring] = "Spring Bank Holiday"
    out[_nth_weekday(year, 8, 0, -1)] = "Summer Bank Holiday"
    christmas, boxing = date(year, 12, 25), date(year, 12, 26)
    if christmas.weekday() == 5:              # Thu Bay -> nghi bu thu Hai, thu Ba
        out[date(year, 12, 27)] = "Christmas Day (substitute)"
        out[date(year, 12, 28)] = "Boxing Day (substitute)"
    elif christmas.weekday() == 6:            # Chu Nhat -> Boxing thu Hai, bu thu Ba
        out[boxing] = "Boxing Day"
        out[date(year, 12, 27)] = "Christmas Day (substitute)"
    else:
        out[christmas] = "Christmas Day"
        if boxing.weekday() == 5:             # 25 la thu Sau -> Boxing bu thu Hai
            out[date(year, 12, 28)] = "Boxing Day (substitute)"
        else:
            out[boxing] = "Boxing Day"
    for special, name in _UK_SPECIAL.items():
        if special.year == year:
            out[special] = name
    return out


_UK_SPECIAL = {
    date(2011, 4, 29): "Royal Wedding",
    date(2022, 6, 3): "Platinum Jubilee",
    date(2022, 9, 19): "State Funeral of Queen Elizabeth II",
    date(2023, 5, 8): "Coronation of King Charles III",
}


def holiday_name(calendar: str | None, day: date) -> str | None:
    cal = str(calendar or "none")
    if cal == "US":
        return us_holidays(day.year).get(day)
    if cal == "UK":
        return uk_holidays(day.year).get(day)
    return None


def trade_day_status(session: dict[str, Any], day: date) -> tuple[bool, str | None]:
    """(co giao dich khong, ly do neu khong)."""
    name = DAY_NAMES[day.weekday()]
    if name not in (session.get("trade_days") or []):
        return False, f"{name} khong nam trong trade_days"
    holiday = holiday_name(session.get("holiday_calendar"), day)
    if holiday:
        return False, f"ngay le {session.get('holiday_calendar')}: {holiday}"
    return True, None


def next_opens(session: dict[str, Any], after_ms: int, count: int = 3,
               max_days: int = 400) -> list[int]:
    """count lan mo ke tiep (ms UTC) sau after_ms, chi tinh ngay giao dich."""
    out: list[int] = []
    day = local_date(session, after_ms) - timedelta(days=1)
    for _ in range(max_days):
        ok, _reason = trade_day_status(session, day)
        if ok:
            ts = session_open_ms(session, day)
            if ts > after_ms:
                out.append(ts)
                if len(out) >= count:
                    break
        day += timedelta(days=1)
    return out


def previous_trade_day(session: dict[str, Any], day: date, max_days: int = 30) -> date | None:
    probe = day - timedelta(days=1)
    for _ in range(max_days):
        if trade_day_status(session, probe)[0]:
            return probe
        probe -= timedelta(days=1)
    return None


# ---------------------------------------------------------------- tham so

# Ten phang -> (section trong config orb, key, kieu, rang buoc).
# Section None = key nam thang o goc section orb.
# TM - #ORB-RULES - ORB Rule Set: bo khoi risk va max_orb_trades_per_day (BR-ORB-18).
# Cac khoa trong RULE_PARAM_KEYS van giu section de doc override long cua phien
# ({"exit": {"tp_r": 2}}), nhung gia tri lay tu bo rule ORB, khong tu config.yaml.
PARAM_SPEC: dict[str, tuple[str | None, str, str, Any]] = {
    "entry_mode": ("entry", "mode", "choice", ENTRY_MODES),
    "buffer_pct": ("entry", "buffer_pct", "float", (0.0, 5.0)),
    "allow_reversal": ("entry", "allow_reversal", "bool", None),
    "failed_lookback_bars": ("entry", "failed_lookback_bars", "int", (1, 36)),
    "sl_mode": ("exit", "sl_mode", "choice", SL_MODES),
    "tp_r": ("exit", "tp_r", "float", (0.0, 20.0)),
    "move_sl_to_be_at_r": ("exit", "move_sl_to_be_at_r", "float", (0.0, 20.0)),
    "time_exit_minutes": ("exit", "time_exit_minutes", "int", (5, 1440)),
    "atr_period": ("filters", "atr_period", "int", (2, 100)),
    "min_or_atr_ratio": ("filters", "min_or_atr_ratio", "float", (0.0, 10.0)),
    "max_or_atr_ratio": ("filters", "max_or_atr_ratio", "float", (0.0, 10.0)),
    "use_bias_filter": ("filters", "use_bias_filter", "bool", None),
    "skip_news_days": ("filters", "skip_news_days", "bool", None),
    "taker_fee_pct": ("costs", "taker_fee_pct", "float", (0.0, 1.0)),
    "slippage_pct": ("costs", "slippage_pct", "float", (0.0, 1.0)),
    "qty_step": ("exchange_limits", "qty_step", "float", (1e-8, 1e6)),
    "min_notional_usd": ("exchange_limits", "min_notional_usd", "float", (0.0, 1e9)),
    "trade_window_minutes": ("defaults", "trade_window_minutes", "int", (15, 720)),
    "max_trades": ("defaults", "max_trades", "int", (1, 20)),
}
# Phien chi duoc ghi de nhom entry/exit/filters (muc 4.9)
SESSION_OVERRIDE_SECTIONS = ("entry", "exit", "filters")
SESSION_OVERRIDE_KEYS = tuple(k for k, spec in PARAM_SPEC.items()
                              if spec[0] in SESSION_OVERRIDE_SECTIONS)
# TM - #ORB-RULES - ORB Rule Set: tham so doc tu bo rule ORB (BR-ORB-16, BR-ORB-17)
RULE_PARAM_KEYS = orb_rules.SESSION_FIELDS
# Override cua phien sua tren form phien; nhom rule thi sua o muc "Rule ORB"
SESSION_FORM_KEYS = tuple(k for k in SESSION_OVERRIDE_KEYS if k not in RULE_PARAM_KEYS)


def coerce_param(key: str, value: Any) -> Any:
    """Ep kieu + kiem tra rang buoc. Nem ValueError voi thong bao ro rang."""
    if key in RULE_PARAM_KEYS:
        return orb_rules.coerce(key, value)  # TM - #ORB-RULES - ORB Rule Set
    if key not in PARAM_SPEC:
        raise ValueError(f"tham so khong ho tro: {key}. Cho phep: {sorted(PARAM_SPEC)}")
    _section, _name, kind, rule = PARAM_SPEC[key]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"{key} phai la true/false")
    if kind == "choice":
        text = str(value).strip().lower()
        if text not in rule:
            raise ValueError(f"{key} phai la mot trong {list(rule)}")
        return text
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{key} phai la so, nhan duoc {value!r}") from None
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"{key} khong hop le")
    low, high = rule
    if not (low <= number <= high):
        raise ValueError(f"{key} phai nam trong [{low:g}, {high:g}]")
    if kind == "int":
        if number != int(number):
            raise ValueError(f"{key} phai la so nguyen")
        return int(number)
    return number


def normalize_overrides(raw: Any, allowed: Iterable[str] | None = None) -> dict[str, Any]:
    """Nhan dang phang {"entry_mode": "retest"} hoac long {"entry": {"mode": ...}}."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("overrides phai la object")
    reverse = {(spec[0], spec[1]): key for key, spec in PARAM_SPEC.items()}
    flat: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, dict) and key in {s[0] for s in PARAM_SPEC.values()}:
            for sub, sub_value in value.items():
                name = reverse.get((key, sub))
                if not name:
                    raise ValueError(f"tham so khong ho tro: {key}.{sub}")
                flat[name] = sub_value
        else:
            flat[str(key)] = value
    allowed_set = set(allowed) if allowed is not None else set(PARAM_SPEC)
    out: dict[str, Any] = {}
    for key, value in flat.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue                         # o trong = dung gia tri chung
        if key not in allowed_set:
            raise ValueError(f"khong duoc ghi de {key} o day. Cho phep: {sorted(allowed_set)}")
        out[key] = coerce_param(key, value)
    return out


def effective_params(cfg: dict[str, Any], session: dict[str, Any] | None = None,
                     overrides: dict[str, Any] | None = None,
                     rules: dict[str, Any] | None = None,
                     rules_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tham so phang da giai.

    Tham so ky thuat (entry.mode, ATR, phi, exchange_limits...): config chung <
    gia tri rieng cua phien < overrides.
    TM - #ORB-RULES - ORB Rule Set: nhom rule (RULE_PARAM_KEYS, nguong cham lenh,
    sizing) lay tu khoi `orb` cua rules.json qua orb_rules.resolve. `rules` None
    thi dung khoi migrate tu config (luc chua co rules.json, va trong test).
    """
    params: dict[str, Any] = {}
    for key, (section, name, _kind, _rule) in PARAM_SPEC.items():
        if key in RULE_PARAM_KEYS:
            continue                 # BR-ORB-18: yaml khong con la nguon
        source = cfg if section is None else (cfg.get(section) or {})
        if name in source and source[name] is not None:
            params[key] = source[name]
    params["atr_timeframe"] = str((cfg.get("filters") or {}).get("atr_timeframe") or "H1")
    session_flat: dict[str, Any] = {}
    if session:
        for key in ("trade_window_minutes", "max_trades"):
            if session.get(key) not in (None, ""):
                params[key] = int(session[key])
        session_flat = normalize_overrides(session.get("overrides"), SESSION_OVERRIDE_KEYS)
    flat = normalize_overrides(overrides) if overrides else {}
    params.update({k: v for k, v in session_flat.items() if k not in RULE_PARAM_KEYS})
    params.update({k: v for k, v in flat.items() if k not in RULE_PARAM_KEYS})
    missing = [k for k in PARAM_SPEC if k not in RULE_PARAM_KEYS and k not in params]
    if missing:
        raise ValueError(f"config orb thieu tham so: {missing}")

    symbol = str(cfg.get("symbol") or "BTCUSDT").upper()
    block = rules if rules is not None else orb_rules.migrate(cfg, symbol)[0]
    resolved = orb_rules.resolve(
        block, symbol,
        session_values={k: v for k, v in session_flat.items() if k in RULE_PARAM_KEYS},
        override={**{k: v for k, v in flat.items() if k in RULE_PARAM_KEYS},
                  **(rules_override or {})},
        session_id=(session or {}).get("session_id"))
    missing_rules = orb_rules.missing_errors(resolved, RULE_PARAM_KEYS)
    if missing_rules:
        raise ValueError("; ".join(missing_rules))
    params.update(resolved["values"])
    params["rule_sources"] = resolved["sources"]
    return params


# ---------------------------------------------------------------- phien

SESSION_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")


class SessionValidationError(ValueError):
    """Loi validate phien, kem loi theo tung field de trang admin hien dung cho."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def valid_timezone(name: str) -> bool:
    name = str(name or "").strip()
    if not name or name.startswith("/") or ".." in name:
        return False
    try:
        ZoneInfo(name)
    except Exception:
        return False
    return True


def validate_session(payload: dict[str, Any], *, existing_ids: Iterable[str],
                     original: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tra ve phien da chuan hoa, hoac nem SessionValidationError(field -> loi).

    original = ban dang luu khi sua (None khi them moi). session_id khong duoc
    doi sau khi tao.
    """
    errors: dict[str, str] = {}
    sid = str(payload.get("session_id") or "").strip()
    if original is not None:
        if sid and sid != original["session_id"]:
            errors["session_id"] = "khong duoc doi session_id sau khi tao"
        sid = original["session_id"]
    elif not SESSION_ID_RE.fullmatch(sid):
        errors["session_id"] = "chi gom chu thuong, so va _ (toi da 32 ky tu)"
    elif sid in set(existing_ids):
        errors["session_id"] = f"da co phien '{sid}'"

    name = str(payload.get("name") or "").strip()
    if not name:
        errors["name"] = "bat buoc"
    elif len(name) > 50:
        errors["name"] = "toi da 50 ky tu"

    tz_name = str(payload.get("timezone") or "").strip()
    if not valid_timezone(tz_name):
        errors["timezone"] = f"timezone IANA khong hop le: {tz_name!r} (vd Europe/London)"

    open_time = str(payload.get("open_time") or "").strip()
    try:
        parsed = parse_hhmm(open_time)
        if parsed.minute % 15:
            errors["open_time"] = "phut phai chia het cho 15 de trung moc nen M15"
        open_time = f"{parsed.hour:02d}:{parsed.minute:02d}"
    except ValueError as exc:
        errors["open_time"] = str(exc)

    raw_days = payload.get("trade_days") or []
    if isinstance(raw_days, str):
        raw_days = [d for d in re.split(r"[,\s]+", raw_days) if d]
    lookup = {d.lower(): d for d in DAY_NAMES}
    days: list[str] = []
    for item in raw_days:
        key = str(item).strip().lower()[:3]
        if key not in lookup:
            errors["trade_days"] = f"ngay khong hop le: {item!r}"
            break
        if lookup[key] not in days:
            days.append(lookup[key])
    if not days and "trade_days" not in errors:
        errors["trade_days"] = "chon it nhat mot ngay"
    days.sort(key=DAY_NAMES.index)

    calendar = str(payload.get("holiday_calendar") or "none").strip()
    calendar = {"us": "US", "uk": "UK", "none": "none"}.get(calendar.lower(), calendar)
    if calendar not in HOLIDAY_CALENDARS:
        errors["holiday_calendar"] = f"chi nhan {list(HOLIDAY_CALENDARS)}"

    window = payload.get("trade_window_minutes")
    if window in (None, ""):
        window = None
    else:
        try:
            window = int(float(str(window).strip()))
            if not (15 <= window <= 720) or window % 15:
                errors["trade_window_minutes"] = "15-720, boi so cua 15"
        except ValueError:
            errors["trade_window_minutes"] = "phai la so"

    max_trades = payload.get("max_trades")
    if max_trades in (None, ""):
        max_trades = None
    else:
        try:
            max_trades = int(float(str(max_trades).strip()))
            if max_trades < 1:
                errors["max_trades"] = "phai >= 1"
        except ValueError:
            errors["max_trades"] = "phai la so"

    try:
        overrides = normalize_overrides(payload.get("overrides"), SESSION_OVERRIDE_KEYS)
    except ValueError as exc:
        errors["overrides"] = str(exc)
        overrides = {}

    enabled = payload.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ("1", "true", "yes", "on")

    if errors:
        raise SessionValidationError(errors)
    return {
        "session_id": sid,
        "name": name,
        "timezone": tz_name,
        "open_time": open_time,
        "trade_days": days,
        "holiday_calendar": calendar,
        "trade_window_minutes": window,
        "max_trades": max_trades,
        "overrides": overrides,
        "enabled": bool(enabled),
    }


def session_times(session: dict[str, Any], day: date, params: dict[str, Any]) -> dict[str, int]:
    """Cac moc cua mot phien trong mot ngay (ms UTC)."""
    open_ms = session_open_ms(session, day)
    or_end = open_ms + M15_MS
    return {
        "open": open_ms,
        "or_end": or_end,
        "window_end": or_end + int(params["trade_window_minutes"]) * 60_000,
    }


# ---------------------------------------------------------------- nen

def closed_bars(bars: list[dict[str, Any]], span_ms: int, now_ms: int) -> list[dict[str, Any]]:
    return [b for b in bars if b["open_time"] + span_ms <= now_ms]


def aggregate(bars: list[dict[str, Any]], base_ms: int, span_ms: int) -> list[dict[str, Any]]:
    """Gop nen nho thanh nen lon (vd M5 -> M15/H1). Chi giu nhom DU nen: nhom
    thieu nen thi high/low co the sai, bo di con hon tra so sai."""
    need = span_ms // base_ms
    out: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    key = None
    for bar in bars:
        k = bar["open_time"] - bar["open_time"] % span_ms
        if k != key:
            if group and len(group) == need:
                out.append(_merge_group(key, group, span_ms))
            group, key = [], k
        group.append(bar)
    if group and len(group) == need:
        out.append(_merge_group(key, group, span_ms))
    return out


def _merge_group(key: int, group: list[dict[str, Any]], span_ms: int) -> dict[str, Any]:
    return {
        "open_time": key,
        "open": group[0]["open"],
        "high": max(b["high"] for b in group),
        "low": min(b["low"] for b in group),
        "close": group[-1]["close"],
        "volume": sum(b["volume"] for b in group),
        "close_time": key + span_ms - 1,
    }


def missing_ranges(bars: list[dict[str, Any]], start_ms: int, end_ms: int,
                   span_ms: int) -> list[dict[str, str]]:
    """Cac khoang nen bi thieu trong [start, end). Rong = du."""
    have = {b["open_time"] for b in bars}
    gaps: list[dict[str, str]] = []
    run_start = None
    t = start_ms
    while t < end_ms:
        if t not in have:
            if run_start is None:
                run_start = t
        elif run_start is not None:
            gaps.append({"from_utc": iso_utc(run_start), "to_utc": iso_utc(t)})
            run_start = None
        t += span_ms
    if run_start is not None:
        gaps.append({"from_utc": iso_utc(run_start), "to_utc": iso_utc(end_ms)})
    return gaps


def atr_wilder(bars: list[dict[str, Any]], period: int,
               window: int = ATR_WINDOW) -> float | None:
    """ATR kieu Wilder (giong TradingView) tren `window` nen cuoi. Can >= period+1 nen."""
    rows = bars[-window:]
    if len(rows) < period + 1:
        return None
    trs = []
    for prev, bar in zip(rows, rows[1:]):
        trs.append(max(bar["high"] - bar["low"],
                       abs(bar["high"] - prev["close"]),
                       abs(bar["low"] - prev["close"])))
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def average_volume(bars: list[dict[str, Any]], count: int = VOLUME_AVG_BARS) -> float | None:
    rows = bars[-count:]
    if not rows:
        return None
    return sum(b["volume"] for b in rows) / len(rows)


# ---------------------------------------------------------------- opening range

def range_metrics(high: float, low: float, atr_value: float | None,
                  params: dict[str, Any]) -> dict[str, Any]:
    mid = (high + low) / 2
    size = high - low
    ratio = (size / atr_value) if atr_value else None
    if ratio is None:
        flag = None
    elif ratio < float(params["min_or_atr_ratio"]):
        flag = "too_narrow"
    elif ratio > float(params["max_or_atr_ratio"]):
        flag = "too_wide"
    else:
        flag = "ok"
    return {
        "high": high,
        "low": low,
        "mid": round(mid, 2),
        "size": round(size, 2),
        "size_pct": round(size / mid * 100, 4) if mid else None,
        "atr": round(atr_value, 2) if atr_value is not None else None,
        "size_atr_ratio": round(ratio, 3) if ratio is not None else None,
        "range_flag": flag,
    }


# ---------------------------------------------------------------- state machine

def scan_signals(or_high: float, or_low: float, bars: list[dict[str, Any]],
                 params: dict[str, Any]) -> list[dict[str, Any]]:
    """Chay state machine tren chuoi nen M5 DA DONG sau khi OR chot.

    Tra ve danh sach su kien doi state theo thu tu thoi gian. Moi su kien:
      state, index (vi tri nen trong `bars`), direction (huong VAO LENH),
      entry_ready (co du dieu kien vao lenh o nen nay chua), entry_price,
      variant (breakout/retest/reversal), extreme (dinh/day cua cu pha - dung
      lam SL cho lenh dao chieu).

    Luat (BR-04, BR-05):
      close  - nen M5 dong cua ngoai OR.
      touch  - gia cham OR +/- buffer_pct. Hai phia cung cham trong mot nen thi
               khong biet phia nao truoc -> bo qua nen do (canh bao).
      retest - da pha bang gia dong cua, sau do mot nen quay lai cham canh OR va
               dong cua cung huong pha -> moi vao lenh.
      Failed breakout: trong failed_lookback_bars nen sau nen pha co nen dong cua
      tro vao trong OR. Sau do mot cu pha moi (bat ky phia nao) lai bat dau lai.
    """
    mode = params["entry_mode"]
    buffer = float(params["buffer_pct"]) / 100.0
    lookback = int(params["failed_lookback_bars"])
    up_level = or_high * (1 + buffer) if mode == "touch" else or_high
    dn_level = or_low * (1 - buffer) if mode == "touch" else or_low

    events: list[dict[str, Any]] = []
    brk: dict[str, Any] | None = None

    def inside(bar: dict[str, Any]) -> bool:
        return or_low <= bar["close"] <= or_high

    def breakout_at(i: int, bar: dict[str, Any]) -> dict[str, Any] | None:
        if mode == "touch":
            up, dn = bar["high"] >= up_level, bar["low"] <= dn_level
            if up and dn:
                events.append({"state": None, "index": i, "warning": "both_sides_touched"})
                return None
            if up:
                return {"direction": "long", "index": i, "extreme": bar["high"],
                        "entry_ready": True, "variant": "breakout",
                        "entry_price": max(up_level, bar["open"])}
            if dn:
                return {"direction": "short", "index": i, "extreme": bar["low"],
                        "entry_ready": True, "variant": "breakout",
                        "entry_price": min(dn_level, bar["open"])}
            return None
        if bar["close"] > or_high:
            direction = "long"
        elif bar["close"] < or_low:
            direction = "short"
        else:
            return None
        ready = mode == "close"
        return {"direction": direction, "index": i,
                "extreme": bar["high"] if direction == "long" else bar["low"],
                "entry_ready": ready, "variant": "breakout",
                "entry_price": bar["close"] if ready else None}

    def emit_breakout(b: dict[str, Any]) -> None:
        events.append({
            "state": "BREAKOUT_LONG" if b["direction"] == "long" else "BREAKOUT_SHORT",
            "index": b["index"], "trigger_index": b["index"],
            "direction": b["direction"], "entry_ready": b["entry_ready"],
            "entry_price": b["entry_price"], "variant": b["variant"],
            "extreme": b["extreme"], "breakout_index": b["index"],
        })

    def emit_failed(b: dict[str, Any], i: int, bar: dict[str, Any]) -> None:
        opposite = "short" if b["direction"] == "long" else "long"
        events.append({
            "state": ("FAILED_BREAKOUT_LONG" if b["direction"] == "long"
                      else "FAILED_BREAKOUT_SHORT"),
            "index": i, "trigger_index": i, "direction": opposite,
            "entry_ready": True, "entry_price": bar["close"], "variant": "reversal",
            "extreme": b["extreme"], "breakout_index": b["index"],
        })

    for i, bar in enumerate(bars):
        if brk is not None:
            # Pha nguoc sang phia doi dien = cu pha moi
            flipped = breakout_at(i, bar)
            if flipped and flipped["direction"] != brk["direction"]:
                brk = flipped
                emit_breakout(brk)
                if mode == "touch" and inside(bar):
                    emit_failed(brk, i, bar)
                    brk = None
                continue
            if brk["direction"] == "long":
                brk["extreme"] = max(brk["extreme"], bar["high"])
            else:
                brk["extreme"] = min(brk["extreme"], bar["low"])
            if i - brk["index"] <= lookback and inside(bar):
                emit_failed(brk, i, bar)
                brk = None
                continue
            if mode == "retest" and not brk["entry_ready"]:
                retest = (bar["low"] <= or_high and bar["close"] > or_high
                          if brk["direction"] == "long"
                          else bar["high"] >= or_low and bar["close"] < or_low)
                if retest:
                    brk.update(entry_ready=True, entry_price=bar["close"], variant="retest")
                    events.append({
                        "state": ("BREAKOUT_LONG" if brk["direction"] == "long"
                                  else "BREAKOUT_SHORT"),
                        "index": i, "trigger_index": i, "direction": brk["direction"],
                        "entry_ready": True, "entry_price": bar["close"],
                        "variant": "retest", "extreme": brk["extreme"],
                        "breakout_index": brk["index"],
                    })
            continue

        found = breakout_at(i, bar)
        if found is None:
            continue
        brk = found
        emit_breakout(brk)
        # touch: chinh nen cham cung co the dong cua tro vao trong OR
        if mode == "touch" and inside(bar):
            emit_failed(brk, i, bar)
            brk = None
    return events


def actionable(event: dict[str, Any], params: dict[str, Any]) -> bool:
    """Su kien nay co cho phep vao lenh khong."""
    if not event.get("state") or not event.get("entry_ready"):
        return False
    if event["state"].startswith("FAILED_"):
        return bool(params.get("allow_reversal"))
    return True


# ---------------------------------------------------------------- ke hoach lenh

def floor_step(value: float, step: float) -> float:
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    units = math.floor(value / step + 1e-9)
    return round(units * step, decimals + 2)


def stop_price(direction: str, or_high: float, or_low: float, params: dict[str, Any],
               reversal_extreme: float | None = None) -> float:
    """SL theo sl_mode. Lenh dao chieu (failed breakout) dat SL sau dinh/day cua
    cu pha hong - dat o canh OR thi SL nam sat entry, size phinh to vo ly."""
    if reversal_extreme is not None:
        return reversal_extreme
    if params["sl_mode"] == "mid":
        return (or_high + or_low) / 2
    return or_low if direction == "long" else or_high


def size_position(entry: float, r_distance: float,
                  params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """TM - #ORB-RULES - ORB Rule Set: size = margin_usd x leverage / entry, lam tron
    XUONG theo qty_step (BR-ORB-15) - thay cho sizing theo risk_usd.

    Khong bao gio tu tang size de dat min notional - chi canh bao (TC-19).
    Phi uoc tinh = notional x 2 chieu x (taker_fee + slippage) (BR-08).
    """
    warnings: list[str] = []
    step = float(params["qty_step"])
    margin_rule, leverage = params.get("margin_usd"), params.get("leverage")
    if margin_rule is None or leverage is None or entry <= 0:
        absent = [k for k in ("margin_usd", "leverage") if params.get(k) is None]
        warnings.append(f"no_sizing: chua dat rule ORB {absent} - khong tinh duoc qty")
        return {"qty": None, "qty_raw": None, "notional": None, "margin": None,
                "margin_usd": margin_rule, "leverage": leverage,
                "sizing": "margin_x_leverage", "risk_actual_usd": None,
                "fee_est_usd": None, "fee_in_r": None}, warnings
    leverage = float(leverage)
    qty_raw = float(margin_rule) * leverage / entry
    qty = floor_step(qty_raw, step)
    notional = qty * entry
    margin = notional / leverage
    cost_rate = (float(params["taker_fee_pct"]) + float(params["slippage_pct"])) / 100.0
    fee_est = notional * cost_rate * 2
    risk_actual = qty * r_distance
    if qty <= 0:
        warnings.append(
            f"qty_below_step: margin_usd {float(margin_rule):g} x {leverage:g} / entry "
            f"{entry:.2f} = {qty_raw:.6f} < qty_step {step:g} - khong vao duoc lenh")
    elif notional < float(params["min_notional_usd"]):
        warnings.append(
            f"below_min_notional: notional {notional:.2f} < min_notional_usd "
            f"{float(params['min_notional_usd']):g} - san se tu choi lenh; "
            "BAMCP khong tu tang size")
    return {
        "qty": qty,
        "qty_raw": round(qty_raw, 8),
        "notional": round(notional, 2),
        "margin": round(margin, 2),
        "margin_usd": float(margin_rule),
        "leverage": leverage,
        "sizing": "margin_x_leverage",
        "risk_actual_usd": round(risk_actual, 4),
        "fee_est_usd": round(fee_est, 2),
        "fee_in_r": round(fee_est / risk_actual, 3) if risk_actual > 0 else None,
    }, warnings


def build_plan(*, direction: str, entry: float, or_high: float, or_low: float,
               params: dict[str, Any], trigger_close_ms: int,
               risk_usd: float | None = None, variant: str = "breakout",
               reversal_extreme: float | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Entry / SL / TP / size / time exit cho mot tin hieu.

    TM - #ORB-RULES - ORB Rule Set: `risk_usd` giu lai cho tuong thich nhung khong
    con anh huong size (BR-ORB-15). plan.risk_usd = so USD mat neu cham SL (chua phi).
    """
    del risk_usd
    sl = stop_price(direction, or_high, or_low, params,
                    reversal_extreme if variant == "reversal" else None)
    r_distance = (entry - sl) if direction == "long" else (sl - entry)
    if r_distance <= 0:
        return None, [f"invalid_sl: entry {entry} va SL {sl} khong hop le cho lenh {direction}"]
    tp_r = float(params["tp_r"])
    tp = entry + tp_r * r_distance if direction == "long" else entry - tp_r * r_distance
    sizing, warnings = size_position(entry, r_distance, params)
    time_exit = trigger_close_ms + int(params["time_exit_minutes"]) * 60_000
    plan = {
        "direction": direction,
        "variant": variant,
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "tp_r": tp_r,
        "sl_mode": "reversal_extreme" if variant == "reversal" else params["sl_mode"],
        "r_distance": round(r_distance, 2),
        "risk_usd": sizing["risk_actual_usd"],
        **sizing,
        "move_sl_to_be_at_r": params.get("move_sl_to_be_at_r"),
        "time_exit_utc": iso_utc(time_exit),
        "time_exit_vn": iso_vn(time_exit),
    }
    return plan, warnings


def bias_check(direction: str | None, bias: dict[str, Any] | None,
               enabled: bool) -> str:
    """ok / against / neutral / missing / off."""
    if not enabled:
        return "off"
    if not bias or not bias.get("found", True) or not bias.get("direction"):
        return "missing"
    bias_dir = str(bias["direction"]).lower()
    if bias_dir == "neutral" or direction is None:
        return "neutral" if bias_dir == "neutral" else "ok"
    return "ok" if bias_dir == direction else "against"
