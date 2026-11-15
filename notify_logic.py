# TM - #NOTIFY-CHANNEL - Tách kênh thông báo theo cặp
"""Logic thuan cua kenh thong bao: loai tin, muc do, chon route, dong dau, gio yen lang, tom tat.

Khong goi mang, khong doc DB. notifier.Hub goi cac ham o day; test goi thang.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

SEVERITIES = ("low", "info", "high", "critical")
RANK = {name: i for i, name in enumerate(SEVERITIES)}
WILDCARD = "*"
PROVIDERS = ("telegram", "discord")
STATUSES = ("SENT", "FILTERED", "DEDUPED", "DIGESTED", "FAILED", "FALLBACK")
TEST = "TEST"
DIGEST = "DIGEST"

# category -> (severity mac dinh, emoji loai, nhan tren dong dau, nguon)
CATEGORIES: dict[str, tuple[str, str, str, str]] = {
    "ENTRY_IN_ZONE": ("info", "📍", "IN ZONE", "watcher M5 — giá vào vùng"),
    "ENTRY_TRIGGERED": ("high", "🎯", "ENTRY TRIGGERED", "watcher M5 — có trigger"),
    "ENTRY_CONFIRMED": ("high", "✅", "ENTRY CONFIRMED", "watcher M5 — xác nhận / bị chặn"),
    "ENTRY_MISSED": ("low", "⛔", "MISSED", "watcher M5 — lỡ entry / hết cửa sổ"),
    "ZONE_EXIT": ("low", "↩️", "ZONE EXIT", "watcher M5 — trigger hỏng, quay lại chờ"),
    "ENTRY_FILLED": ("info", "✅", "FILLED", "watcher — vị thế khớp setup"),
    "SETUP_INVALIDATED": ("info", "❌", "SETUP INVALIDATED", "watcher / prune / huỷ / hết hạn"),
    "OFF_PLAN": ("high", "⚠️", "OFF-PLAN", "#OFF-PLAN — lệnh ngoài kế hoạch"),
    "TRADE_RESULT": ("info", "🏁", "TRADE RESULT", "đóng vị thế"),
    "ORB_SIGNAL": ("high", "📈", "ORB SIGNAL", "ORB"),
    "BIAS_SAVED": ("low", "🧭", "BIAS SAVED", "save_bias"),
    "SYSTEM_ERROR": ("high", "❌", "SYSTEM ERROR", "fetcher, API sàn, gửi tin lỗi"),
    "SYSTEM_NOTICE": ("info", "ℹ️", "SYSTEM", "nhắc việc hệ thống (PLAN_MISSING)"),
}
# Loai chi dung noi bo: khong co trong ma tran route
EXTRA = {TEST: ("critical", "🧪", "TEST", "tin thử"), DIGEST: ("low", "🗂", "TÓM TẮT", "tin tổng hợp")}
# OFF_PLAN: hai loai nay nang len critical
CRITICAL_OFF_PLAN = ("COUNTER_PLAN", "REVENGE")

DEFAULT_SYMBOLS: dict[str, dict[str, str]] = {
    "BTCUSDT": {"emoji": "🟠", "color": "#F7931A"},
    "ETHUSDT": {"emoji": "🔷", "color": "#627EEA"},
    "XAUUSDT": {"emoji": "🟡", "color": "#D4AF37"},
}
SYSTEM_STYLE = {"emoji": "⚙️", "color": "#6B7280"}
OTHER_STYLE = {"emoji": "🔹", "color": "#64748B"}

SETTINGS_DEFAULTS: dict[str, Any] = {
    "default_channel_id": [],
    "dedupe_window_seconds": 300,
    "digest_minutes": 15,
    "digest_include_info": False,
    "digest_default": False,
    "quiet_hours": "",
    "log_retention_days": 30,
    "display_timezone": "",          # trong = mui gio cua server, giong watcher
    "symbols": {},
}
# Cot *_ref trong notify_channels giu KHOA cua secret trong khoi notify_secrets (alerts/config.json tren volume),
# khong giu gia tri. Khoa do server tu sinh, nguoi dung chi nhap token / webhook o trang admin.
REF_PREFIX = {"telegram_bot_token_ref": "TG_", "discord_webhook_ref": "DC_"}
SECRET_FIELDS = {"telegram_bot_token_ref": "telegram_bot_token", "discord_webhook_ref": "discord_webhook_url"}
DISCORD_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/",
                    "https://canary.discord.com/api/webhooks/", "https://ptb.discord.com/api/webhooks/")
_TG_TOKEN = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{20,}$")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_QUIET = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\s*$")


# ---------------------------------------------------------------- loai tin, muc do

def info(category: str) -> tuple[str, str, str, str]:
    return CATEGORIES.get(category) or EXTRA.get(category) or ("info", "🔔", category.replace("_", " "), "")


def norm_category(category: Any) -> str:
    text = str(category or "").strip().upper()
    if not text:
        raise ValueError("category la bat buoc")
    return text


def norm_severity(severity: Any, category: str) -> str:
    text = str(severity or "").strip().lower()
    if not text:
        return info(category)[0]
    if text not in RANK:
        raise ValueError(f"severity phai la mot trong {list(SEVERITIES)}")
    return text


def norm_symbol(symbol: Any) -> str:
    text = str(symbol or "").strip().upper()
    return text or WILDCARD


def rank(severity: str) -> int:
    return RANK.get(severity, RANK["info"])


def off_plan_severity(classification: str, flags: list[str] | None) -> str:
    marks = {classification, *(flags or [])}
    return "critical" if marks & set(CRITICAL_OFF_PLAN) else "high"


def dedupe_key(symbol: str, category: str, ref: Any) -> str | None:
    if ref is None or str(ref) == "":
        return None
    return f"{symbol}:{category}:{ref}"


# ---------------------------------------------------------------- cai dat

def settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Khoi notify da tron mac dinh. default_channel_id luon la list (chap nhan chuoi cu)."""
    raw = dict(raw or {})
    out = {**SETTINGS_DEFAULTS, **{k: raw[k] for k in SETTINGS_DEFAULTS if k in raw and raw[k] is not None}}
    ids = out["default_channel_id"]
    if isinstance(ids, str):
        ids = [s for s in (p.strip() for p in ids.split(",")) if s]
    out["default_channel_id"] = [str(i) for i in ids or []]
    for key, low in (("dedupe_window_seconds", 0), ("digest_minutes", 1), ("log_retention_days", 1)):
        try:
            out[key] = max(low, int(float(out[key])))
        except (TypeError, ValueError):
            out[key] = SETTINGS_DEFAULTS[key]
    out["digest_include_info"] = bool(out["digest_include_info"])
    out["digest_default"] = bool(out["digest_default"])
    symbols = {k: dict(v) for k, v in DEFAULT_SYMBOLS.items()}
    for sym, style in (out.get("symbols") or {}).items():
        if isinstance(style, dict):
            symbols[str(sym).upper()] = {**symbols.get(str(sym).upper(), OTHER_STYLE), **style}
    out["symbols"] = symbols
    return out


def display_tz(cfg: dict[str, Any], fallback: ZoneInfo) -> ZoneInfo:
    try:
        return ZoneInfo(str(cfg.get("display_timezone"))) if cfg.get("display_timezone") else fallback
    except Exception:
        return fallback


def clean_settings(payload: dict[str, Any], channel_ids: set[str]) -> dict[str, Any]:
    """Kiem tra khoi cai dat chung tu trang admin truoc khi ghi."""
    def whole(name: str, low: int, high: int) -> int:
        try:
            value = float(payload.get(name))
        except (TypeError, ValueError):
            raise ValueError(f"{name} phai la so, nhan duoc {payload.get(name)!r}") from None
        if not low <= value <= high or value != int(value):
            raise ValueError(f"{name} phai la so nguyen tu {low} den {high}")
        return int(value)

    ids = payload.get("default_channel_id") or []
    ids = [ids] if isinstance(ids, str) else list(ids)
    unknown = [i for i in ids if i not in channel_ids]
    if unknown:
        raise ValueError(f"default_channel_id khong ton tai: {unknown}")
    quiet = str(payload.get("quiet_hours") or "").strip()
    if quiet:
        parse_quiet(quiet)
    tz_name = str(payload.get("display_timezone") or "").strip()     # trong = mui gio cua server (TZ)
    if tz_name:
        try:
            ZoneInfo(tz_name)
        except Exception:
            raise ValueError(f"display_timezone khong hop le: {tz_name}") from None
    symbols: dict[str, dict[str, str]] = {}
    for sym, style in (payload.get("symbols") or {}).items():
        if not isinstance(style, dict):
            raise ValueError(f"symbols.{sym} phai la object")
        emoji = str(style.get("emoji") or "").strip()
        color = str(style.get("color") or "").strip()
        if len(emoji) > 8:
            raise ValueError(f"emoji cua {sym} qua dai")
        if color and not _COLOR.match(color):
            raise ValueError(f"mau cua {sym} phai dang #RRGGBB")
        symbols[str(sym).strip().upper()] = {k: v for k, v in (("emoji", emoji), ("color", color)) if v}
    return {
        "default_channel_id": [str(i) for i in ids],
        "dedupe_window_seconds": whole("dedupe_window_seconds", 0, 86400),
        "digest_minutes": whole("digest_minutes", 1, 1440),
        "digest_include_info": payload.get("digest_include_info") in (True, 1, "1", "true", "on"),
        "digest_default": payload.get("digest_default") in (True, 1, "1", "true", "on"),
        "quiet_hours": quiet,
        "log_retention_days": whole("log_retention_days", 1, 365),
        "display_timezone": tz_name,
        "symbols": symbols,
    }


def new_ref(field: str) -> str:
    """Khoa moi cho mot secret trong notify_secrets (vd TG_3F9A1C2B). Khong lo gi ve gia tri."""
    return f"{REF_PREFIX[field]}{secrets.token_hex(4).upper()}"


def secret_field(provider: str) -> str:
    """Cot *_ref ma provider dung."""
    return "telegram_bot_token_ref" if provider == "telegram" else "discord_webhook_ref"


def clean_secret(provider: str, value: Any) -> str | None:
    """Kiem tra bot token / webhook URL nhap tu trang admin. Trong = giu gia tri dang luu."""
    text = str(value or "").strip()
    if not text:
        return None
    if provider == "telegram":
        if not _TG_TOKEN.match(text):
            raise ValueError("bot token Telegram phai dang 123456789:AA... (lay tu @BotFather)")
    elif not text.startswith(DISCORD_PREFIXES):
        raise ValueError("webhook phai bat dau bang https://discord.com/api/webhooks/")
    return text


def clean_channel(payload: dict[str, Any]) -> dict[str, Any]:
    """Cac cot sua duoc cua mot kenh. Token / webhook di rieng (clean_secret), khoa secret do server gan."""
    provider = str(payload.get("provider") or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"provider phai la mot trong {list(PROVIDERS)}")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("name la bat buoc")
    out: dict[str, Any] = {"name": name[:80], "provider": provider,
                           "enabled": payload.get("enabled") not in (False, 0, "0", "false", "off")}
    if provider == "telegram":
        chat = str(payload.get("telegram_chat_id") or "").strip()
        if not chat:
            raise ValueError("telegram can telegram_chat_id")
        if not re.match(r"^(-?\d{1,20}|@[A-Za-z0-9_]{4,64})$", chat):
            raise ValueError("telegram_chat_id phai la so (vd -100...) hoac @ten_kenh")
        thread = payload.get("telegram_thread_id")
        if thread in (None, ""):
            thread = None
        else:
            try:
                thread = int(thread)
            except (TypeError, ValueError):
                raise ValueError("telegram_thread_id phai la so nguyen") from None
        out.update(telegram_chat_id=chat, telegram_thread_id=thread, discord_webhook_ref=None,
                   discord_username=None, discord_avatar_url=None)
    else:
        avatar = str(payload.get("discord_avatar_url") or "").strip() or None
        if avatar and not avatar.startswith("https://"):
            raise ValueError("discord_avatar_url phai la https://...")
        out.update(discord_username=(str(payload.get("discord_username") or "").strip()[:80] or None),
                   discord_avatar_url=avatar, telegram_bot_token_ref=None, telegram_chat_id=None,
                   telegram_thread_id=None)
    return out


def secret_refs(channel: dict[str, Any]) -> list[str]:
    return [channel[k] for k in REF_PREFIX if channel.get(k)]


def mask(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return f"****{text[-4:]}" if len(text) > 8 else "****"


def scrub(text: str, secrets: list[str]) -> str:
    """Xoa secret khoi chuoi loi truoc khi ghi log / tra ra ngoai."""
    for value in secrets:
        if value and len(value) >= 6:
            text = text.replace(value, mask(value))
    return text


# ---------------------------------------------------------------- nhan dien tin

def short_symbol(symbol: str) -> str:
    if symbol == WILDCARD:
        return "SYSTEM"
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def style(cfg: dict[str, Any], symbol: str) -> dict[str, str]:
    if symbol == WILDCARD:
        return dict(SYSTEM_STYLE)
    return {**OTHER_STYLE, **(cfg.get("symbols") or {}).get(symbol, {})}


def color_int(color: str) -> int | None:
    return int(color[1:], 16) if color and _COLOR.match(color) else None


def header(cfg: dict[str, Any], symbol: str, icon: str, label: str, at_ms: int, tz: ZoneInfo) -> str:
    """{emoji_cap} {SYM} · {emoji_loai} {LOAI TIN} · {HH:mm}"""
    hm = datetime.fromtimestamp(at_ms / 1000, tz).strftime("%H:%M")
    return f"{style(cfg, symbol)['emoji']} {short_symbol(symbol)} · {icon} {label} · {hm}"


def fallback_line(channel_name: str) -> str:
    return f"⚠️ FALLBACK (kênh {channel_name} lỗi)"


# ---------------------------------------------------------------- route

def select_routes(routes: list[dict[str, Any]], symbol: str, category: str) -> tuple[int, list[dict[str, Any]]]:
    """Cap route dau tien co dong enabled: 1 symbol+category, 2 symbol+*, 3 *+category.
    (0, []) = khong co route nao -> kenh mac dinh. Tin symbol '*' thi cap 2 la route '*'+'*' (System)."""
    levels = ((symbol, category), (symbol, WILDCARD), (WILDCARD, category))
    for level, (sym, cat) in enumerate(levels, start=1):
        hit = [r for r in routes if r.get("enabled") and r["symbol"] == sym and r["category"] == cat]
        if hit:
            return level, hit
    return 0, []


def route_categories() -> list[str]:
    return list(CATEGORIES)


# ---------------------------------------------------------------- gio yen lang, tom tat

def parse_quiet(text: str) -> tuple[int, int]:
    match = _QUIET.match(str(text or ""))
    if not match:
        raise ValueError("quiet_hours phai dang HH:MM-HH:MM, vd 23:00-05:00")
    h1, m1, h2, m2 = (int(x) for x in match.groups())
    if not (h1 < 24 and h2 < 24 and m1 < 60 and m2 < 60):
        raise ValueError("quiet_hours sai gio")
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start == end:
        raise ValueError("quiet_hours: gio bat dau va ket thuc trung nhau")
    return start, end


def quiet_end(text: str, at_ms: int, tz: ZoneInfo) -> int | None:
    """Dang trong gio yen lang thi tra moc (ms) het gio yen lang, khong thi None."""
    if not text:
        return None
    try:
        start, end = parse_quiet(text)
    except ValueError:
        return None
    local = datetime.fromtimestamp(at_ms / 1000, tz)
    minute = local.hour * 60 + local.minute
    inside = start <= minute < end if start < end else (minute >= start or minute < end)
    if not inside:
        return None
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    stop = day + timedelta(minutes=end)
    if stop <= local:
        stop += timedelta(days=1)
    return int(stop.timestamp() * 1000)


def digest_due(at_ms: int, minutes: int) -> int:
    span = max(1, int(minutes)) * 60_000
    return (at_ms // span + 1) * span


def digest_text(cfg: dict[str, Any], symbol: str, items: list[dict[str, Any]], start_ms: int, end_ms: int,
                tz: ZoneInfo) -> tuple[str, str]:
    """(dong dau, than) cua tin tom tat. items: {label, title, at}."""
    def hm(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, tz).strftime("%H:%M")

    head = (f"{style(cfg, symbol)['emoji']} {short_symbol(symbol)} · {EXTRA[DIGEST][1]} "
            f"{EXTRA[DIGEST][2]} {hm(start_ms)}–{hm(end_ms)}")
    lines = [f"• {i['label']}: {i['title']} ({hm(i['at'])})" for i in items]
    return head, "\n".join(lines)
