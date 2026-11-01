# TM - #ENTRY-ALERT - Entry setups & alerts
from __future__ import annotations

import asyncio
import html
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx2 as httpx

BACKOFF = (2.0, 10.0, 30.0)
CHANNELS = ("discord", "telegram", "both")
DISCORD_LIMIT = 2000
TELEGRAM_LIMIT = 4096


class NotifyConfigError(ValueError):
    pass


class RetryAfter(Exception):
    def __init__(self, seconds: float, message: str) -> None:
        super().__init__(message)
        self.seconds = seconds


@dataclass
class Alert:
    text: str
    title: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class INotifier(ABC):
    name = ""

    @abstractmethod
    async def send(self, alert: Alert) -> None:
        ...


class DiscordWebhookNotifier(INotifier):
    name = "discord"

    def __init__(self, webhook_url: str, *, timeout: float = 15.0, embed: bool = False) -> None:
        if not webhook_url:
            raise NotifyConfigError("discord: chua dat webhook_url")
        self.url = webhook_url
        self.timeout = timeout
        self.embed = embed

    async def send(self, alert: Alert) -> None:
        text = alert.text[:DISCORD_LIMIT]
        body: dict[str, Any] = ({"embeds": [{"title": alert.title[:256], "description": text}]}
                                if self.embed and alert.title else {"content": text})
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.url, json=body, timeout=self.timeout)
        if resp.status_code == 429:
            try:
                wait = float(resp.json().get("retry_after") or 1.0)
            except Exception:
                wait = float(resp.headers.get("Retry-After") or 1.0)
            raise RetryAfter(wait, f"discord 429, retry_after {wait}s")
        if resp.status_code >= 400:
            raise RuntimeError(f"discord HTTP {resp.status_code}: {resp.text[:200]}")


class TelegramBotNotifier(INotifier):
    name = "telegram"
    base_url = "https://api.telegram.org"

    def __init__(self, bot_token: str, chat_id: str, *, timeout: float = 15.0) -> None:
        if not bot_token or not chat_id:
            raise NotifyConfigError("telegram: chua dat bot_token hoac chat_id")
        self.token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    async def send(self, alert: Alert) -> None:
        text = html.escape(alert.text)[:TELEGRAM_LIMIT]
        if alert.title:
            text = f"<b>{html.escape(alert.title)}</b>\n{text}"[:TELEGRAM_LIMIT]
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/bot{self.token}/sendMessage",
                                     json={"chat_id": self.chat_id, "text": text,
                                           "parse_mode": "HTML"}, timeout=self.timeout)
        if resp.status_code == 429:
            try:
                wait = float((resp.json().get("parameters") or {}).get("retry_after") or 1.0)
            except Exception:
                wait = 1.0
            raise RetryAfter(wait, f"telegram 429, retry_after {wait}s")
        if resp.status_code >= 400:
            raise RuntimeError(f"telegram HTTP {resp.status_code}: {resp.text[:200]}")


def build(cfg: dict[str, Any]) -> list[INotifier]:
    channel = str(cfg.get("channel") or "discord").strip().lower()
    if channel not in CHANNELS:
        raise NotifyConfigError(f"notify.channel phai la mot trong {list(CHANNELS)}")
    out: list[INotifier] = []
    if channel in ("discord", "both"):
        d = cfg.get("discord") or {}
        out.append(DiscordWebhookNotifier(str(d.get("webhook_url") or ""),
                                          embed=bool(d.get("embed"))))
    if channel in ("telegram", "both"):
        t = cfg.get("telegram") or {}
        out.append(TelegramBotNotifier(str(t.get("bot_token") or ""), str(t.get("chat_id") or "")))
    return out


async def send_with_retry(notifier: INotifier, alert: Alert, *,
                          backoff: tuple[float, ...] = BACKOFF,
                          sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    attempt = 0
    while True:
        try:
            await notifier.send(alert)
            return
        except NotifyConfigError:
            raise
        except RetryAfter as exc:
            if attempt >= len(backoff):
                raise
            await sleep(exc.seconds)
        except Exception:
            if attempt >= len(backoff):
                raise
            await sleep(backoff[attempt])
        attempt += 1


async def deliver(cfg: dict[str, Any], alert: Alert, **kwargs: Any) -> str | None:
    try:
        targets = build(cfg)
    except NotifyConfigError as exc:
        return str(exc)
    errors: list[str] = []
    results = await asyncio.gather(*[send_with_retry(n, alert, **kwargs) for n in targets],
                                   return_exceptions=True)
    for notifier, result in zip(targets, results):
        if isinstance(result, BaseException):
            errors.append(f"{notifier.name}: {type(result).__name__}: {result}")
    return "; ".join(errors) or None


def _num(value: Any) -> str:
    if value is None:
        return "—"
    value = float(value)
    if abs(value) >= 100:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{value:.6g}"


def _signed(value: float) -> str:
    return ("+" if value >= 0 else "−") + _num(abs(value))


def _hm(ms: int | None, tz: ZoneInfo) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%H:%M")


def _when(ms: int | None, tz: ZoneInfo) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%Y-%m-%d %H:%M")


def _trigger_line(trigger: dict[str, Any] | None) -> str:
    if not trigger:
        return "Trigger: —"
    kind = trigger.get("trigger_type")
    start = trigger.get("extreme") if trigger.get("extreme") is not None else trigger.get("ref")
    vol = (f", vol {trigger['vol_ratio']}× SMA{trigger.get('sma_period')}"
           if trigger.get("vol_ratio") is not None else "")
    return f"Trigger: {kind} {_num(start)} → đóng {_num(trigger.get('close'))}{vol}"


def _confirm_line(setup: dict[str, Any], state: dict[str, Any]) -> str:
    if not setup.get("confirm_type"):
        return "Confirm: không cần (vào ngay sau trigger)"
    conf = state.get("confirm") or {}
    sign = ">" if setup["confirm_type"] == "close_above" else "<"
    if conf:
        return f"Confirm: đóng {_num(conf.get('close'))} {sign} {_num(setup.get('confirm_level'))}"
    return f"Confirm: chờ đóng {sign} {_num(setup.get('confirm_level'))}"


def _head(icon: str, setup: dict[str, Any], label: str, candle_ms: int | None,
          tz: ZoneInfo) -> str:
    return (f"{icon} {setup['code']} · {setup['direction'].upper()} · {label} "
            f"(nến M5 {_hm(candle_ms, tz)} đã đóng)")


def setup_alert(setup: dict[str, Any], event: dict[str, Any], tz: ZoneInfo,
                params: dict[str, Any] | None = None) -> Alert:
    state = setup.get("state") or {}
    details = event.get("details") or {}
    name = event["event"]
    candle = event.get("candle_open_time")
    price = event.get("price")
    entry, stop, target = float(setup["entry"]), float(setup["stop"]), float(setup["target"])
    long = setup["direction"] == "long"
    rr = abs(target - entry) / abs(entry - stop) if entry != stop else None
    plan = f"Entry {_num(entry)} · SL {_num(stop)} · TP {_num(target)}" + (
        f" · R:R {rr:.1f}" if rr else "")
    zone = f"{_num(setup['zone_low'])}–{_num(setup['zone_high'])}"
    if name == "IN_ZONE":
        lines = [_head("🟡", setup, "IN_ZONE", candle, tz),
                 f"Vào vùng {zone}, chuẩn bị. Chờ trigger {setup['trigger_type']}.",
                 f"Giá {_num(price)} · {plan}"]
    elif name == "TRIGGERED":
        lines = [_head("🔵", setup, "TRIGGERED", candle, tz), _trigger_line(details.get("trigger")),
                 _confirm_line(setup, state) + (
                     f" trong {(params or {}).get('confirm_within', '?')} nến"
                     if setup.get("confirm_type") else ""), plan]
    elif name == "TRIGGER_FAILED":
        lines = [_head("⚪", setup, "WAITING — trigger hỏng", candle, tz),
                 f"Quá {details.get('candles')} nến chưa đóng qua confirm "
                 f"{_num(setup.get('confirm_level'))}. Quay lại chờ vùng {zone}."]
    elif name == "ENTRY_VALID":
        check = details.get("check") or {}
        chase = float(price) - entry if long else entry - float(price)
        left = int((params or {}).get("entry_valid_candles") or 0) - int(state.get("entry_valid_count") or 0)
        rr_now = check.get("rr")
        ok = "✅" if check.get("would_pass") else "❌"
        quota = f" · quota {check['quota']}" if check.get("quota") else ""
        lines = [_head("🟢", setup, "ENTRY_VALID", candle, tz), _trigger_line(state.get("trigger")),
                 _confirm_line(setup, state),
                 f"Giá {_num(price)} · entry {_num(entry)} ({_signed(chase)}, max_chase "
                 f"{_num(details.get('max_chase'))}) · còn {max(0, left)} nến",
                 f"SL {_num(stop)} · TP {_num(target)} · R:R {rr:.1f}" + (
                     f" (tại giá {rr_now:g})" if rr_now is not None else ""),
                 f"check_trade: {ok}{quota}"]
    elif name == "MISSED":
        eff = float(details.get("max_chase") or 0)
        lines = ["⛔ MISSED — không đuổi",
                 f"{setup['code']} · {setup['direction'].upper()} · nến M5 {_hm(candle, tz)}: giá "
                 f"{_num(price)} cách entry {_num(entry)} {_signed(float(price) - entry)} "
                 f"(max_chase {_num(eff)})",
                 f"Chờ retest: vào lại khi giá về {_num(entry - eff)}–{_num(entry + eff)} "
                 f"(vùng {zone})"]
    elif name == "BLOCKED":
        reasons = details.get("rule_violations") or []
        lines = [_head("🚫", setup, "BLOCKED", candle, tz),
                 f"Giá {_num(price)} · {plan}", "check_trade không qua:"] + [f"- {r}" for r in reasons]
    elif name == "ENTRY_WINDOW_EXPIRED":
        lines = [_head("⌛", setup, "WAITING — hết cửa sổ entry", candle, tz),
                 f"Hết {details.get('candles')} nến mà chưa khớp lệnh. Giá {_num(price)}."]
    elif name == "FILLED":
        pos = details.get("position") or {}
        how = (f"vị thế OKX {pos.get('side')} {_num(pos.get('size'))} @ {_num(pos.get('entry_price'))}"
               if pos else f"log_trade #{details.get('trade_id')}")
        lines = [f"✅ {setup['code']} · {setup['direction'].upper()} · FILLED", f"Khớp: {how}", plan]
    elif name == "INVALIDATED":
        m15 = (f" · M15 đóng {_num(details.get('m15_close'))}"
               if details.get("m15_filter") else "")
        lines = [_head("❌", setup, "INVALIDATED", candle, tz),
                 f"Đóng {_num(price)} quá invalidate_level {_num(details.get('invalidate_level'))}{m15}"]
    elif name == "EXPIRED":
        lines = [f"⏱ {setup['code']} · {setup['direction'].upper()} · EXPIRED",
                 f"Hết hạn lúc {_when(setup['expires_at'], tz)}"]
    elif name == "CANCELLED":
        lines = [f"🗑 {setup['code']} · {setup['direction'].upper()} · CANCELLED",
                 f"Lý do: {details.get('reason') or '—'}"]
    else:
        lines = [f"{setup['code']} · {name}"]
    return Alert("\n".join(lines), title=f"{setup['code']} {name}",
                 meta={"setup_id": setup["id"], "event": name})


def system_alert(event: str, details: dict[str, Any], tz: ZoneInfo) -> Alert:
    if event == "PLAN_MISSING":
        text = (f"⚠️ PLAN_MISSING — {', '.join(details.get('symbols') or [])} chưa có setup nào "
                f"cho {details.get('bias_date')} (hạn {details.get('deadline')})")
    elif event == "FETCHER_STALE":
        rows = [f"- {s}: nến 5m mới nhất {_when(v.get('last_open_time'), tz)}"
                f" ({v.get('minutes')} phút trước)" if v.get("last_open_time") else f"- {s}: chưa có nến 5m"
                for s, v in (details.get("symbols") or {}).items()]
        text = "\n".join(["⚠️ FETCHER_STALE — quá 15 phút không có nến 5m mới"] + rows)
    elif event == "UNPLANNED_POSITION":
        pos = details.get("position") or {}
        text = (f"⚠️ Lệnh mở ngoài kế hoạch — {details.get('symbol')} {pos.get('side')} "
                f"{_num(pos.get('size'))} @ {_num(pos.get('entry_price'))} "
                f"(mở {_when(pos.get('opened_ms'), tz)})")
    elif event == "TEST":
        text = (f"✅ BAMCP test — kênh {details.get('channel') or 'thông báo'} hoạt động "
                f"({_when(details.get('at'), tz)})")
    else:
        text = f"{event}: {details}"
    return Alert(text, title=event, meta={"event": event})
