# TM - #ENTRY-ALERT - Entry setups & alerts
# TM - #NOTIFY-CHANNEL - Tách kênh thông báo theo cặp
"""Module thong bao duy nhat.

notifier.send(symbol, category, severity, title, body, dedupe_ref=None, embed_fields=None) la cua duy nhat
de gui tin. Hub lo: chon route -> loc severity -> chong trung -> gom tin / gio yen lang -> hang doi gui
(gian cach theo kenh, gioi han nhom Telegram) -> thu lai / fallback -> ghi notify_log.
Loi gui tin khong bao gio duoc lan ra ngoai: watcher / fetcher goi send() khong bi dung.
"""
from __future__ import annotations

import asyncio
import html
import sys
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx2 as httpx

import entry_logic
import notify_logic as nl

BACKOFF = (2.0, 5.0, 15.0)
DISCORD_LIMIT = 2000
DISCORD_EMBED_LIMIT = 4096
TELEGRAM_LIMIT = 4096
TELEGRAM_BODY = 3600          # cat than tin TRUOC khi escape HTML de khong cat giua the / entity
DISCORD_PREFIXES = nl.DISCORD_PREFIXES
CHANNEL_GAP_MS = 1_000        # toi da 1 tin/giay/kenh
GROUP_LIMIT = 20              # toi da 20 tin/phut/nhom Telegram (cac topic chung mot chat_id)
GROUP_WINDOW_MS = 60_000
FAIL_THRESHOLD = 5            # kenh loi lien tuc >= 5 lan trong 10 phut -> mot tin SYSTEM_ERROR
FAIL_WINDOW_MS = 10 * 60_000
FAIL_ALERT_EVERY_MS = 3_600_000
MAX_RATE_WAITS = 5            # 429 lien tiep qua so nay thi coi la loi
FLUSH_EVERY = 15.0
PRUNE_EVERY_MS = 3_600_000
ROUTE_NAMES = {0: "default", 1: "symbol+category", 2: "symbol+*", 3: "*+category"}


class NotifyConfigError(ValueError):
    pass


class RetryAfter(Exception):
    def __init__(self, seconds: float, message: str) -> None:
        super().__init__(message)
        self.seconds = seconds


@dataclass
class Alert:
    """Noi dung mot tin + thong tin dinh tuyen. text la than tin (khong doi so voi truoc),
    title la dong ngan dung trong tin tom tat (label setup, ma OP...)."""
    text: str
    title: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    symbol: str = nl.WILDCARD
    category: str = "SYSTEM_NOTICE"
    severity: str | None = None
    label: str | None = None
    icon: str | None = None
    dedupe_ref: str | None = None
    embed_fields: list[dict[str, Any]] | None = None


@dataclass
class Outgoing:
    """Mot tin da dung xong, san sang dua cho provider."""
    header: str
    body: str = ""
    color: int | None = None
    prefix: str = ""
    fields: list[dict[str, Any]] | None = None
    sender_name: str = "BAMCP"

    def plain(self) -> str:
        return "\n".join(x for x in (self.prefix, self.header, self.body) if x)


class INotifier(ABC):
    name = ""

    @abstractmethod
    async def send(self, msg: Outgoing) -> None:
        ...


class DiscordWebhookNotifier(INotifier):
    name = "discord"

    def __init__(self, webhook_url: str, *, username: str | None = None, avatar_url: str | None = None,
                 timeout: float = 15.0) -> None:
        if not webhook_url:
            raise NotifyConfigError("discord: chua dat webhook_url")
        self.url = webhook_url
        self.username = username
        self.avatar_url = avatar_url
        self.timeout = timeout

    def payload(self, msg: Outgoing) -> dict[str, Any]:
        # Dong dau nam o content de thong bao day tren dien thoai hien dung cap / loai tin;
        # than tin nam trong embed mang mau rieng cua cap.
        body: dict[str, Any] = {"content": "\n".join(x for x in (msg.prefix, msg.header) if x)[:DISCORD_LIMIT],
                                "username": (self.username or msg.sender_name)[:80]}
        if self.avatar_url:
            body["avatar_url"] = self.avatar_url
        if msg.body or msg.fields:
            embed: dict[str, Any] = {"description": msg.body[:DISCORD_EMBED_LIMIT]}
            if msg.color is not None:
                embed["color"] = msg.color
            if msg.fields:
                embed["fields"] = [{"name": str(f.get("name") or "—")[:256], "value": str(f.get("value") or "—")[:1024],
                                    "inline": bool(f.get("inline", True))} for f in msg.fields[:25]]
            body["embeds"] = [embed]
        return body

    async def send(self, msg: Outgoing) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.url, json=self.payload(msg), timeout=self.timeout)
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

    def __init__(self, bot_token: str, chat_id: str, *, thread_id: int | None = None,
                 timeout: float = 15.0) -> None:
        if not bot_token or not chat_id:
            raise NotifyConfigError("telegram: chua dat bot_token hoac chat_id")
        self.token = bot_token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.timeout = timeout

    def payload(self, msg: Outgoing) -> dict[str, Any]:
        body = msg.body if len(msg.body) <= TELEGRAM_BODY else msg.body[:TELEGRAM_BODY] + "…"
        parts = [html.escape(msg.prefix)] if msg.prefix else []
        parts.append(f"<b>{html.escape(msg.header)}</b>")
        if body:
            parts.append(html.escape(body))
        for f in (msg.fields or [])[:25]:
            parts.append(f"<b>{html.escape(str(f.get('name') or '—'))}</b>: {html.escape(str(f.get('value') or '—'))}")
        out: dict[str, Any] = {"chat_id": self.chat_id, "text": "\n".join(parts)[:TELEGRAM_LIMIT],
                               "parse_mode": "HTML"}
        if self.thread_id:
            out["message_thread_id"] = int(self.thread_id)
        return out

    async def send(self, msg: Outgoing) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/bot{self.token}/sendMessage", json=self.payload(msg),
                                     timeout=self.timeout)
        if resp.status_code == 429:
            try:
                wait = float((resp.json().get("parameters") or {}).get("retry_after") or 1.0)
            except Exception:
                wait = 1.0
            raise RetryAfter(wait, f"telegram 429, retry_after {wait}s")
        if resp.status_code >= 400:
            raise RuntimeError(f"telegram HTTP {resp.status_code}: {resp.text[:200]}")


def make_sender(channel: dict[str, Any], secret: Callable[[str], str | None]) -> INotifier:
    """Dung provider tu mot dong notify_channels. Token / webhook doc qua khoa secret (nhap o /admin/notify),
    khong nam trong DB."""
    provider = channel.get("provider")
    if provider == "telegram":
        ref = channel.get("telegram_bot_token_ref")
        token = secret(ref) if ref else None
        if not token:
            raise NotifyConfigError("telegram: chua nhap bot token (trang /admin/notify, nut Sua kenh)")
        if not channel.get("telegram_chat_id"):
            raise NotifyConfigError("telegram: chua dat telegram_chat_id")
        return TelegramBotNotifier(token, str(channel["telegram_chat_id"]), thread_id=channel.get("telegram_thread_id"))
    if provider == "discord":
        ref = channel.get("discord_webhook_ref")
        url = secret(ref) if ref else None
        if not url:
            raise NotifyConfigError("discord: chua nhap webhook URL (trang /admin/notify, nut Sua kenh)")
        if not url.startswith(DISCORD_PREFIXES):
            raise NotifyConfigError("discord: webhook URL da luu khong phai webhook Discord")
        return DiscordWebhookNotifier(url, username=channel.get("discord_username"),
                                      avatar_url=channel.get("discord_avatar_url"))
    raise NotifyConfigError(f"provider khong ho tro: {provider!r}")


async def send_with_retry(notifier: INotifier, msg: Outgoing, *,
                          backoff: tuple[float, ...] = BACKOFF,
                          sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """Gui mot tin, thu lai theo backoff. 429 thi cho dung retry_after."""
    attempt = 0
    while True:
        try:
            await notifier.send(msg)
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


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class _Msg:
    symbol: str
    category: str
    severity: str
    label: str
    icon: str
    title: str
    body: str
    at: int
    header: str
    color: int | None = None
    fields: list[dict[str, Any]] | None = None
    dedupe_key: str | None = None


class Hub:
    """Bo dieu phoi gui tin. Trang thai chong trung / tom tat / gian cach nam trong bo nho:
    khoi dong lai thi mat (toi da them mot tin trung, tin tom tat chua gui bi bo)."""

    def __init__(self, store, *, settings: Callable[[], dict[str, Any]], secret: Callable[[str], str | None],
                 tz: ZoneInfo, now_ms: Callable[[], int] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 sender: Callable[[dict[str, Any], Callable[[str], str | None]], INotifier] = make_sender) -> None:
        self.store = store
        self.settings = settings
        self.secret = secret
        self.tz = tz
        self.now_ms = now_ms or _now_ms
        self.sleep = sleep
        self.sender = sender
        self.running = False
        self._dedupe: dict[str, tuple[int, int]] = {}
        self._digest: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_sent: dict[str, int] = {}
        self._group_sent: dict[str, deque[int]] = {}
        self._blocked: dict[str, int] = {}
        self._failures: dict[str, deque[int]] = {}
        self._alerted: dict[str, int] = {}
        self._tasks: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    # ---------------------------------------------------------- tien ich

    def cfg(self) -> dict[str, Any]:
        return nl.settings(self.settings())

    def display_tz(self, cfg: dict[str, Any] | None = None) -> ZoneInfo:
        return nl.display_tz(cfg or self.cfg(), self.tz)

    def _lock(self, key: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            self._loop, self._locks = loop, {}
        return self._locks.setdefault(key, asyncio.Lock())

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _secrets(self, channel: dict[str, Any]) -> list[str]:
        out = []
        for ref in nl.secret_refs(channel):
            try:
                value = self.secret(ref)
            except Exception:
                value = None
            if value:
                out.append(value)
        return out

    def _log(self, msg: _Msg, status: str, channel: dict[str, Any] | None, error: str | None) -> None:
        try:
            self.store.log(symbol=msg.symbol, category=msg.category, severity=msg.severity, status=status,
                           channel_id=channel["id"] if channel else None, dedupe_key=msg.dedupe_key, error=error,
                           preview=f"{msg.header}\n{msg.body}")
        except Exception as exc:
            print(f"BAMCP notify: khong ghi duoc notify_log - {type(exc).__name__}: {exc}", file=sys.stderr)

    @staticmethod
    def _row(channel: dict[str, Any] | None, status: str, error: str | None = None, **extra: Any) -> dict[str, Any]:
        return {"channel_id": channel["id"] if channel else None, "name": channel["name"] if channel else None,
                "provider": channel["provider"] if channel else None, "status": status, "error": error, **extra}

    @staticmethod
    def _result(level: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
        statuses = [r["status"] for r in rows]
        status = next((s for s in ("SENT", "FALLBACK", "DIGESTED", "DEDUPED", "FILTERED", "FAILED")
                       if s in statuses), "FAILED")
        errors = [f"{r['name'] or 'notify'}: {r['error']}" for r in rows if r["status"] == "FAILED" and r["error"]]
        return {"status": status, "error": "; ".join(errors) or None, "route_level": level,
                "route": ROUTE_NAMES.get(level, "default"), "channels": rows}

    def _message(self, cfg: dict[str, Any], symbol: Any, category: Any, severity: Any, title: Any, body: Any,
                 dedupe_ref: Any, embed_fields: list[dict[str, Any]] | None, label: str | None,
                 icon: str | None) -> _Msg:
        sym = nl.norm_symbol(symbol)
        cat = nl.norm_category(category)
        sev = nl.norm_severity(severity, cat)
        _, d_icon, d_label, _ = nl.info(cat)
        now = self.now_ms()
        icon, label = icon or d_icon, label or d_label
        return _Msg(symbol=sym, category=cat, severity=sev, label=label, icon=icon, title=str(title or ""),
                    body=str(body or ""), at=now, fields=embed_fields,
                    header=nl.header(cfg, sym, icon, label, now, self.display_tz(cfg)),
                    color=nl.color_int(nl.style(cfg, sym)["color"]), dedupe_key=nl.dedupe_key(sym, cat, dedupe_ref))

    # ---------------------------------------------------------- gui

    async def send(self, symbol: str, category: str, severity: str | None = None, title: str = "", body: str = "",
                   dedupe_ref: Any = None, embed_fields: list[dict[str, Any]] | None = None, *,
                   label: str | None = None, icon: str | None = None, retry: bool = True,
                   fallback: bool = True) -> dict[str, Any]:
        """Khong bao gio raise. Tra {status, error, route_level, route, channels: [...]}."""
        try:
            return await self._send(symbol, category, severity, title, body, dedupe_ref, embed_fields,
                                    label=label, icon=icon, retry=retry, fallback=fallback)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"BAMCP notify loi ({symbol} {category}): {error}", file=sys.stderr)
            return {"status": "FAILED", "error": error, "route_level": None, "route": None, "channels": []}

    async def _send(self, symbol, category, severity, title, body, dedupe_ref, embed_fields, *, label, icon,
                    retry, fallback) -> dict[str, Any]:
        cfg = self.cfg()
        msg = self._message(cfg, symbol, category, severity, title, body, dedupe_ref, embed_fields, label, icon)
        channels = {c["id"]: c for c in self.store.channels()}
        level, routes = nl.select_routes(self.store.routes(), msg.symbol, msg.category)
        rows: list[dict[str, Any]] = []
        targets: list[tuple[dict[str, Any], bool]] = []
        if level:
            for route in routes:
                channel = channels.get(route["channel_id"])
                if channel is None:
                    continue
                # Duoi nguong cua route thi bo qua, KHONG roi xuong cap sau
                if nl.rank(msg.severity) < nl.rank(route["min_severity"]):
                    error = f"severity {msg.severity} < min_severity {route['min_severity']}"
                    self._log(msg, "FILTERED", channel, error)
                    rows.append(self._row(channel, "FILTERED", error))
                elif not channel["enabled"]:
                    self._log(msg, "FILTERED", channel, "kenh dang tat")
                    rows.append(self._row(channel, "FILTERED", "kenh dang tat"))
                else:
                    targets.append((channel, bool(route["digest"])))
        else:
            for cid in cfg["default_channel_id"]:
                channel = channels.get(cid)
                if channel and channel["enabled"]:
                    targets.append((channel, cfg["digest_default"]))
            if not targets:
                error = "chua co route khop va chua dat kenh mac dinh dang bat (notify.default_channel_id)"
                self._log(msg, "FAILED", None, error)
                return self._result(level, [self._row(None, "FAILED", error)])
        if not targets:
            return self._result(level, rows)
        # Chong trung: cung dedupe_key trong cua so thi khong gui lai, tru khi severity tang
        if msg.dedupe_key:
            previous = self._dedupe.get(msg.dedupe_key)
            window = cfg["dedupe_window_seconds"] * 1000
            if previous and msg.at - previous[0] < window and nl.rank(msg.severity) <= previous[1]:
                for channel, _ in targets:
                    self._log(msg, "DEDUPED", channel, None)
                    rows.append(self._row(channel, "DEDUPED"))
                return self._result(level, rows)
            self._dedupe[msg.dedupe_key] = (msg.at, nl.rank(msg.severity))
        tz = self.display_tz(cfg)
        quiet = None if msg.severity == "critical" else nl.quiet_end(cfg["quiet_hours"], msg.at, tz)
        lowish = msg.severity == "low" or (msg.severity == "info" and cfg["digest_include_info"])
        now_targets = []
        for channel, digest in targets:
            if quiet is not None or (digest and lowish):
                due = quiet if quiet is not None else nl.digest_due(msg.at, cfg["digest_minutes"])
                self._queue_digest(channel, msg, due)
                self._log(msg, "DIGESTED", channel, None)
                rows.append(self._row(channel, "DIGESTED", due_at=due))
            else:
                now_targets.append(channel)
        rows += await asyncio.gather(*[self._deliver(c, msg, retry=retry, fallback=fallback) for c in now_targets])
        return self._result(level, rows)

    async def _deliver(self, channel: dict[str, Any], msg: _Msg, *, retry: bool = True,
                       fallback: bool = True) -> dict[str, Any]:
        error = await self._attempt(channel, msg, retry=retry)
        if error is None:
            self._log(msg, "SENT", channel, None)
            return self._row(channel, "SENT")
        defaults = []
        if fallback:
            ids = self.cfg()["default_channel_id"]
            defaults = [c for c in self.store.channels()
                        if c["id"] in ids and c["enabled"] and c["id"] != channel["id"]]
        if defaults:
            done, failed = [], []
            for target in defaults:
                err = await self._attempt(target, msg, prefix=nl.fallback_line(channel["name"]), retry=retry)
                (failed if err else done).append((target, err))
            if done:
                self._log(msg, "FALLBACK", channel, error)
                return self._row(channel, "FALLBACK", error, fallback_to=[t["name"] for t, _ in done])
            error += "; fallback loi: " + "; ".join(f"{t['name']}: {e}" for t, e in failed)
        self._log(msg, "FAILED", channel, error)
        return self._row(channel, "FAILED", error)

    async def _attempt(self, channel: dict[str, Any], msg: _Msg, *, prefix: str = "",
                       retry: bool = True) -> str | None:
        """Gui mot tin vao mot kenh (co thu lai). None = thanh cong, chuoi = loi (da xoa secret)."""
        secrets = self._secrets(channel)
        try:
            sender = self.sender(channel, self.secret)
        except NotifyConfigError as exc:
            error = nl.scrub(str(exc), secrets)
            self._failed(channel, error)
            return error
        out = Outgoing(header=msg.header, body=msg.body, color=msg.color, prefix=prefix, fields=msg.fields,
                       sender_name=f"BAMCP · {nl.short_symbol(msg.symbol)}")
        delays = BACKOFF if retry else ()
        attempt = rate_waits = 0
        async with self._lock(f"ch:{channel['id']}"):
            while True:
                await self._pace(channel)
                try:
                    await sender.send(out)
                except NotifyConfigError as exc:
                    error = nl.scrub(str(exc), secrets)
                    self._failed(channel, error)
                    return error
                except RetryAfter as exc:
                    # 429: chan ca nhom toi het retry_after roi gui lai, khong tinh la mot lan loi
                    self._blocked[self._group(channel)] = self.now_ms() + int(float(exc.seconds) * 1000)
                    rate_waits += 1
                    if rate_waits > MAX_RATE_WAITS:
                        error = nl.scrub(str(exc), secrets)
                        self._failed(channel, error)
                        return error
                    continue
                except Exception as exc:
                    error = nl.scrub(f"{type(exc).__name__}: {exc}", secrets)
                    self._failed(channel, error)
                    if attempt >= len(delays):
                        return error
                    await self.sleep(delays[attempt])
                    attempt += 1
                    continue
                self._ok(channel)
                return None

    @staticmethod
    def _group(channel: dict[str, Any]) -> str:
        if channel["provider"] == "telegram":
            return f"tg:{channel.get('telegram_chat_id')}"
        return f"dc:{channel['id']}"

    async def _pace(self, channel: dict[str, Any]) -> None:
        group = self._group(channel)
        while True:
            wait = self._blocked.get(group, 0) - self.now_ms()
            if wait <= 0:
                break
            await self.sleep(wait / 1000)
        wait = self._last_sent.get(channel["id"], -CHANNEL_GAP_MS) + CHANNEL_GAP_MS - self.now_ms()
        if wait > 0:
            await self.sleep(wait / 1000)
        if channel["provider"] == "telegram":
            async with self._lock(group):
                sent = self._group_sent.setdefault(group, deque())
                while True:
                    now = self.now_ms()
                    while sent and now - sent[0] >= GROUP_WINDOW_MS:
                        sent.popleft()
                    if len(sent) < GROUP_LIMIT:
                        sent.append(now)
                        break
                    await self.sleep((GROUP_WINDOW_MS - (now - sent[0])) / 1000)
        self._last_sent[channel["id"]] = self.now_ms()

    def _ok(self, channel: dict[str, Any]) -> None:
        self._failures.pop(channel["id"], None)
        try:
            self.store.mark_ok(channel["id"])
        except Exception:
            pass

    def _failed(self, channel: dict[str, Any], error: str) -> None:
        try:
            self.store.mark_error(channel["id"], error)
        except Exception:
            pass
        now = self.now_ms()
        fails = self._failures.setdefault(channel["id"], deque())
        fails.append(now)
        while fails and now - fails[0] > FAIL_WINDOW_MS:
            fails.popleft()
        if len(fails) >= FAIL_THRESHOLD and now - self._alerted.get(channel["id"], -FAIL_ALERT_EVERY_MS) >= FAIL_ALERT_EVERY_MS:
            self._alerted[channel["id"]] = now
            body = (f"Kênh {channel['name']} ({channel['provider']}) lỗi {len(fails)} lần trong 10 phút.\n"
                    f"Lỗi gần nhất: {error}\nKiểm tra ở trang admin › Thông báo.")
            # Task rieng: khong cho trong lock cua kenh dang loi
            self._spawn(self.send(nl.WILDCARD, "SYSTEM_ERROR", "high", f"Kênh {channel['name']} lỗi", body,
                                  label="NOTIFY ERROR"))

    # ---------------------------------------------------------- tom tat

    def _queue_digest(self, channel: dict[str, Any], msg: _Msg, due: int) -> None:
        key = (channel["id"], msg.symbol)
        bucket = self._digest.setdefault(key, {"channel_id": channel["id"], "symbol": msg.symbol, "items": [],
                                               "due": due, "since": msg.at})
        bucket["due"] = min(bucket["due"], due)
        first = msg.body.split("\n", 1)[0] if msg.body else ""
        bucket["items"].append({"label": msg.label, "title": msg.title or first, "at": msg.at,
                                "severity": msg.severity})

    def pending_digest(self) -> list[dict[str, Any]]:
        return [{"channel_id": b["channel_id"], "symbol": b["symbol"], "items": len(b["items"]), "due_at": b["due"]}
                for b in self._digest.values()]

    async def flush_digests(self, *, force: bool = False) -> int:
        """Gui cac tin tom tat da den han. Trong gio yen lang thi giu lai toi het gio."""
        cfg = self.cfg()
        tz = self.display_tz(cfg)
        now = self.now_ms()
        if not force and nl.quiet_end(cfg["quiet_hours"], now, tz) is not None:
            return 0
        channels = {c["id"]: c for c in self.store.channels()}
        span = cfg["digest_minutes"] * 60_000
        sent = 0
        for key, bucket in list(self._digest.items()):
            if not force and bucket["due"] > now:
                continue
            self._digest.pop(key, None)
            channel = channels.get(bucket["channel_id"])
            if channel is None or not channel["enabled"] or not bucket["items"]:
                continue
            end = now if force else bucket["due"]
            head, body = nl.digest_text(cfg, bucket["symbol"], bucket["items"], bucket["since"] // span * span,
                                        end, tz)
            top = max(bucket["items"], key=lambda i: nl.rank(i["severity"]))["severity"]
            msg = _Msg(symbol=bucket["symbol"], category=nl.DIGEST, severity=top, label=nl.EXTRA[nl.DIGEST][2],
                       icon=nl.EXTRA[nl.DIGEST][1], title="", body=body, at=now, header=head,
                       color=nl.color_int(nl.style(cfg, bucket["symbol"])["color"]))
            await self._deliver(channel, msg)
            sent += 1
        return sent

    # ---------------------------------------------------------- tin thu

    async def test_channel(self, channel_id: str) -> dict[str, Any]:
        """Nut Gui thu: gui thang vao mot kenh, khong retry, khong fallback."""
        channel = self.store.channel(channel_id)
        cfg = self.cfg()
        now = self.now_ms()
        hm = datetime.fromtimestamp(now / 1000, self.display_tz(cfg)).strftime("%H:%M")
        msg = _Msg(symbol=nl.WILDCARD, category=nl.TEST, severity="critical", label="TEST", icon="🧪",
                   title=channel["name"], body="Tin thử từ BAMCP — kênh này nhận được tin.", at=now,
                   header=f"🧪 TEST · {channel['name']} · {hm}")
        error = await self._attempt(channel, msg, retry=False)
        self._log(msg, "FAILED" if error else "SENT", channel, error)
        return {"ok": error is None, "error": error, "channel": {"id": channel["id"], "name": channel["name"],
                                                                 "provider": channel["provider"]}}

    async def test_route(self, symbol: str, category: str = nl.TEST) -> dict[str, Any]:
        """test_notification: gui tin thu qua dung duong route se chon (khong retry, khong fallback)."""
        cat = nl.norm_category(category)
        sym = nl.norm_symbol(symbol)
        label = "TEST" if cat == nl.TEST else f"TEST {nl.info(cat)[2]}"
        return await self.send(sym, cat, None, f"Tin thử route {sym} + {cat}",
                               "Tin thử từ test_notification — kiểm tra đường route.", label=label, icon="🧪",
                               retry=False, fallback=False)

    # ---------------------------------------------------------- vong doi

    def prune(self) -> int:
        cfg = self.cfg()
        now = self.now_ms()
        window = cfg["dedupe_window_seconds"] * 1000
        for key in [k for k, (at, _) in self._dedupe.items() if now - at >= window]:
            self._dedupe.pop(key, None)
        return self.store.prune(now - cfg["log_retention_days"] * 86_400_000)

    async def run_forever(self, every: float = FLUSH_EVERY) -> None:
        self.running = True
        last_prune = None
        try:
            while True:
                try:
                    await self.flush_digests()
                    now = self.now_ms()
                    if last_prune is None or now - last_prune >= PRUNE_EVERY_MS:
                        last_prune = now
                        self.prune()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"BAMCP notify hub loi: {type(exc).__name__}: {exc}", file=sys.stderr)
                await self.sleep(every)
        finally:
            self.running = False
            for task in list(self._tasks):
                task.cancel()


_HUB: Hub | None = None


def configure(hub: Hub | None) -> None:
    global _HUB
    _HUB = hub


def hub() -> Hub | None:
    return _HUB


async def send(symbol: str, category: str, severity: str | None = None, title: str = "", body: str = "",
               dedupe_ref: Any = None, embed_fields: list[dict[str, Any]] | None = None,
               **kwargs: Any) -> dict[str, Any]:
    """Cua duy nhat de gui tin. Khong bao gio raise."""
    if _HUB is None:
        return {"status": "FAILED", "error": "notifier chua duoc cau hinh", "route_level": None, "route": None,
                "channels": []}
    return await _HUB.send(symbol, category, severity, title, body, dedupe_ref, embed_fields, **kwargs)


async def dispatch(alert: Alert) -> str | None:
    """Gui mot Alert qua notifier.send. Tra loi (str) neu co kenh khong nhan duoc tin, None neu on."""
    result = await send(alert.symbol, alert.category, alert.severity, alert.title, alert.text, alert.dedupe_ref,
                        alert.embed_fields, label=alert.label, icon=alert.icon)
    return result.get("error")


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


# TM - #ENTRY-HTF - Tin can ra quyet dinh thi kem huong khung lon va nhan setup nguoc
HTF_EVENTS = ("IN_ZONE", "TRIGGERED", "ENTRY_VALID", "MISSED", "BLOCKED")

# TM - #NOTIFY-CHANNEL - Tách kênh thông báo theo cặp
# event setup -> (category, severity rieng hoac None = mac dinh cua category, nhan dong dau, emoji)
SETUP_ROUTES: dict[str, tuple[str, str | None, str, str]] = {
    "IN_ZONE": ("ENTRY_IN_ZONE", None, "IN ZONE", "📍"),
    "TRIGGERED": ("ENTRY_TRIGGERED", None, "ENTRY TRIGGERED", "🎯"),
    "ENTRY_VALID": ("ENTRY_CONFIRMED", None, "ENTRY CONFIRMED", "✅"),
    "BLOCKED": ("ENTRY_CONFIRMED", None, "ENTRY BLOCKED", "🚫"),
    "MISSED": ("ENTRY_MISSED", None, "MISSED", "⛔"),
    "ENTRY_WINDOW_EXPIRED": ("ENTRY_MISSED", None, "ENTRY WINDOW EXPIRED", "⌛"),
    "TRIGGER_FAILED": ("ZONE_EXIT", None, "TRIGGER FAILED", "↩️"),
    "FILLED": ("ENTRY_FILLED", None, "FILLED", "✅"),
    "INVALIDATED": ("SETUP_INVALIDATED", None, "SETUP INVALIDATED", "❌"),
    "EXPIRED": ("SETUP_INVALIDATED", None, "SETUP EXPIRED", "⏱"),
    "CANCELLED": ("SETUP_INVALIDATED", None, "SETUP CANCELLED", "🗑"),
    "PRUNED": ("SETUP_INVALIDATED", None, "SETUP PRUNED", "✂️"),
    "BRANCH_CONFLICT": ("SETUP_INVALIDATED", "high", "BRANCH CONFLICT", "⚠️"),
}


def _htf_lines(setup: dict[str, Any]) -> list[str]:
    text = entry_logic.htf_text(setup.get("htf"))
    if not text:
        return []
    counter = entry_logic.htf_counter(setup["direction"], setup.get("htf"))
    return ([f"⚠️ NGƯỢC {', '.join(counter)}"] if counter else []) + [f"Khung lớn: {text}"]


def setup_alert(setup: dict[str, Any], event: dict[str, Any], tz: ZoneInfo,
                params: dict[str, Any] | None = None, *, link: str | None = None) -> Alert:
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
        # TM - #ENTRY-VOL - Noi ro trigger nay co xet volume khong
        rule = f" ({entry_logic.volume_rule(setup['trigger_type'], params)})" if params else ""
        lines = [_head("🟡", setup, "IN_ZONE", candle, tz),
                 f"Vào vùng {zone}, chuẩn bị. Chờ trigger {setup['trigger_type']}{rule}.",
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
    # TM - #TR-BRANCH - Setup bi loai vi nguoc nhanh TR / vi the nguoc nhanh. Thuong gui gop qua TR_BRANCH.
    elif name == "PRUNED":
        lines = [f"✂️ {setup['code']} · {setup['direction'].upper()} · PRUNED",
                 f"Lý do: {details.get('reason') or '—'}"]
    elif name == "BRANCH_CONFLICT":
        lines = [f"⚠️ {setup['code']} · {setup['direction'].upper()} · vị thế ngược nhánh TR",
                 f"{details.get('reason') or '—'} — setup đã FILLED, giữ nguyên trạng thái"]
    else:
        lines = [f"{setup['code']} · {name}"]
    # Tin MISSED co dong ma setup o dong 2; cac tin khac o dong 1
    at = 2 if name == "MISSED" else 1
    if name in HTF_EVENTS:
        lines[at:at] = _htf_lines(setup)
    # TM - #TR-BRANCH - Ten tuong minh ngay duoi dong ma setup
    if setup.get("label"):
        lines.insert(at, f"🏷 {setup['label']}")
    # TM - #DASHBOARD - Setup dashboard
    if link:
        lines.append(f"📊 Dashboard: {link}")
    # TM - #NOTIFY-CHANNEL - Tách kênh thông báo theo cặp
    category, severity, tag, icon = SETUP_ROUTES.get(name, ("SETUP_INVALIDATED", None, name.replace("_", " "), None))
    return Alert("\n".join(lines), title=setup.get("label") or setup["code"],
                 meta={"setup_id": setup["id"], "event": name}, symbol=setup["symbol"], category=category,
                 severity=severity, label=tag, icon=icon, dedupe_ref=f"{setup['id']}:{name}")


def system_alert(event: str, details: dict[str, Any], tz: ZoneInfo) -> Alert:
    # TM - #NOTIFY-CHANNEL - Tách kênh thông báo theo cặp: tin khong gan duoc cap thi symbol '*' (route System)
    route: dict[str, Any] = {"symbol": nl.WILDCARD, "category": "SYSTEM_NOTICE", "label": event.replace("_", " ")}
    if event == "PLAN_MISSING":
        text = (f"⚠️ PLAN_MISSING — {', '.join(details.get('symbols') or [])} chưa có setup nào "
                f"cho {details.get('bias_date')} (hạn {details.get('deadline')})")
        route.update(label="PLAN MISSING", icon="⚠️",
                     title=f"Chưa có setup: {', '.join(details.get('symbols') or [])}")
    elif event == "FETCHER_STALE":
        rows = [f"- {s}: nến 5m mới nhất {_when(v.get('last_open_time'), tz)}"
                f" ({v.get('minutes')} phút trước)" if v.get("last_open_time") else f"- {s}: chưa có nến 5m"
                for s, v in (details.get("symbols") or {}).items()]
        text = "\n".join(["⚠️ FETCHER_STALE — quá 15 phút không có nến 5m mới"] + rows)
        route.update(category="SYSTEM_ERROR", label="FETCHER ERROR",
                     title=f"Không có nến 5m mới: {', '.join(details.get('symbols') or {})}")
    elif event == "UNPLANNED_POSITION":
        pos = details.get("position") or {}
        text = (f"⚠️ Lệnh mở ngoài kế hoạch — {details.get('symbol')} {pos.get('side')} "
                f"{_num(pos.get('size'))} @ {_num(pos.get('entry_price'))} "
                f"(mở {_when(pos.get('opened_ms'), tz)})")
        route.update(symbol=details.get("symbol") or nl.WILDCARD, category="OFF_PLAN", label="OFF-PLAN",
                     dedupe_ref=details.get("position_key"),
                     title=f"{pos.get('side')} {_num(pos.get('size'))} @ {_num(pos.get('entry_price'))}")
    elif event == "TEST":
        text = (f"✅ BAMCP test — kênh {details.get('channel') or 'thông báo'} hoạt động "
                f"({_when(details.get('at'), tz)})")
        route.update(category=nl.TEST, label="TEST", icon="🧪")
    # TM - #TR-BRANCH - Mot tin tong hop cho mot lan TR xac dinh nhanh
    elif event == "TR_BRANCH":
        pruned = details.get("pruned") or []
        conflicts = details.get("conflicts") or []
        rows = [f"🔀 {details.get('symbol')} · {details.get('reason')}"]
        if pruned:
            rows.append(f"Loại {len(pruned)} setup ngược nhánh (watcher ngừng theo dõi):")
            rows += [f"- {s.get('label') or s.get('code')} ({s.get('code')})" for s in pruned]
        if conflicts:
            rows.append(f"⚠️ Vị thế ngược nhánh TR — {len(conflicts)} setup đã FILLED, giữ nguyên:")
            rows += [f"- {s.get('label') or s.get('code')} ({s.get('code')})" for s in conflicts]
        if details.get("link"):
            rows.append(f"📊 Dashboard: {details['link']}")
        text = "\n".join(rows)
        route.update(symbol=details.get("symbol") or nl.WILDCARD, category="SETUP_INVALIDATED", label="TR BRANCH",
                     icon="🔀", severity="high" if conflicts else None, title=str(details.get("reason") or ""))
    else:
        text = f"{event}: {details}"
    return Alert(text, title=route.pop("title", event), meta={"event": event}, **route)
