# TM - #ENTRY-ALERT - Entry setups & alerts
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, time as dtime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import entry_logic
import notifier

UTC = timezone.utc
SCAN_OFFSET_MS = 20_000
CANDLE_RETRIES = 3
CANDLE_RETRY_DELAY = 10.0
CATCH_UP = 12
STALE_MS = 15 * 60_000
SYSTEM_EVERY_MS = 3_600_000
LOAD_BARS = 500


def _now_ms() -> int:
    return int(time.time() * 1000)


class EntryWatcher:
    def __init__(self, store, *, klines: Callable[[], Any], cfg: Callable[[], dict[str, Any]],
                 min_rr: Callable[[str], float],
                 check: Callable[[str, str, float, float, float], dict[str, Any]],
                 positions: Callable[[str], Awaitable[list[dict[str, Any]] | None]],
                 tz: ZoneInfo, span_ms: Callable[[str], int],
                 now_ms: Callable[[], int] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 deliver: Callable[..., Awaitable[str | None]] = notifier.deliver) -> None:
        self.store = store
        self.klines = klines
        self.cfg = cfg
        self.min_rr = min_rr
        self.check = check
        self.positions = positions
        self.tz = tz
        self.span_ms = span_ms
        self.now_ms = now_ms or _now_ms
        self.sleep = sleep
        self.deliver = deliver
        self.running = False
        self.last_scan: dict[str, Any] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()

    def watcher_cfg(self) -> dict[str, Any]:
        return self.cfg().get("watcher") or {}

    def display_tz(self) -> ZoneInfo:
        name = (self.cfg().get("notify") or {}).get("display_timezone")
        try:
            return ZoneInfo(str(name)) if name else self.tz
        except Exception:
            return self.tz

    def params_for(self, symbol: str, setup: dict[str, Any] | None = None) -> dict[str, Any]:
        w = self.watcher_cfg()
        return entry_logic.resolve_params(w.get("defaults") or {},
                                          (w.get("symbol_overrides") or {}).get(symbol.upper()),
                                          (setup or {}).get("trigger_params"))

    def trigger_tf(self) -> str:
        return str(self.watcher_cfg().get("trigger_tf") or "5m")

    def next_wake(self, now: int) -> int:
        span = self.span_ms(self.trigger_tf())
        at = now // span * span + SCAN_OFFSET_MS
        return at if at > now else at + span

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def send_event(self, event_id: int, setup: dict[str, Any] | None = None) -> str | None:
        event = self.store.event(event_id)
        if event["setup_id"]:
            setup = setup or self.store.get(event["setup_id"])
            alert = notifier.setup_alert(setup, event, self.display_tz(),
                                         self.params_for(setup["symbol"], setup))
        else:
            alert = notifier.system_alert(event["event"], event["details"], self.display_tz())
        error = await self.deliver(self.cfg().get("notify") or {}, alert)
        self.store.mark_notified(event_id, error)
        if error:
            print(f"BAMCP notify loi ({event['event']}): {error}", file=sys.stderr)
        return error

    def notify(self, event_id: int, setup: dict[str, Any] | None = None) -> None:
        self._spawn(self.send_event(event_id, setup))

    def notify_threadsafe(self, event_id: int) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(lambda: self.notify(event_id))
        except RuntimeError:
            pass

    def _closed(self, symbol: str, tf: str, now: int, limit: int) -> list[dict[str, Any]]:
        bars = self.klines().get_klines(symbol, tf, limit=limit)
        return [b for b in bars if int(b["close_time"]) <= now]

    def process_symbol(self, symbol: str, now: int) -> list[tuple[int, dict[str, Any]]]:
        setups = self.store.active(symbol)
        if not setups:
            return []
        tf = self.trigger_tf()
        bars = self._closed(symbol, tf, now, LOAD_BARS)
        m15 = (self._closed(symbol, "15m", now, 50)
               if any(s["m15_filter"] for s in setups) else [])
        day = datetime.fromtimestamp(now / 1000, self.tz).date().isoformat()
        out: list[tuple[int, dict[str, Any]]] = []
        for setup in setups:
            params = self.params_for(symbol, setup)
            status, state = setup["status"], dict(setup["state"])
            events: list[dict[str, Any]] = []
            if now > int(setup["expires_at"]):
                events.append({"event": "EXPIRED", "from_status": status, "to_status": "EXPIRED",
                               "price": state.get("last_price"), "candle_open_time": None,
                               "details": {"expires_at": int(setup["expires_at"])}, "notify": True})
                status = "EXPIRED"
            else:
                last = state.get("last_candle")
                pending = [b for b in bars
                           if (last is None or int(b["open_time"]) > int(last))
                           and int(b["close_time"]) >= int(setup["created_at"])][-CATCH_UP:]
                for bar in pending:
                    if self.store.has_candle(setup["id"], int(bar["open_time"])):
                        state["last_candle"] = int(bar["open_time"])
                        continue
                    history = [b for b in bars if int(b["open_time"]) < int(bar["open_time"])]
                    m15_close = next((float(b["close"]) for b in reversed(m15)
                                      if int(b["close_time"]) <= int(bar["close_time"])), None)
                    ctx = {
                        "params": params, "history": history, "m15_close": m15_close,
                        "min_rr": self.min_rr(symbol), "day": day,
                        "check": lambda price, s=setup: self.check(
                            symbol, s["direction"], price, float(s["stop"]), float(s["target"])),
                    }
                    status, state, new = entry_logic.step({**setup, "status": status, "state": state},
                                                          bar, ctx)
                    events.extend(new)
                    if status in entry_logic.TERMINAL:
                        break
            if status == setup["status"] and state == setup["state"] and not events:
                continue
            ids = self.store.apply(setup, status, state, events, tf)
            if events and events[-1]["notify"]:
                out.append((ids[-1], {**setup, "status": status, "state": state}))
        return out

    async def match_positions(self, symbol: str) -> list[tuple[int, dict[str, Any] | None]]:
        fillable = [s for s in self.store.active(symbol) if s["status"] in entry_logic.FILLABLE
                    and s["state"].get("triggered_at")]
        if not fillable:
            return []
        try:
            rows = await self.positions(symbol)
        except Exception as exc:
            print(f"BAMCP watcher: khong doc duoc vi the {symbol} - {exc}", file=sys.stderr)
            return []
        out: list[tuple[int, dict[str, Any] | None]] = []
        first = min(int(s["state"]["triggered_at"]) for s in fillable)
        for pos in rows or []:
            opened = pos.get("opened_ms")
            if not opened or int(opened) <= first:
                continue
            key = f"{symbol}|{pos.get('side')}|{int(opened)}"
            if self.store.seen_position(key):
                continue
            cands = [s for s in fillable if s["direction"] == pos.get("side")
                     and int(opened) > int(s["state"]["triggered_at"])]
            position = {k: pos.get(k) for k in ("symbol", "side", "size", "entry_price", "opened_ms")}
            if cands:
                price = float(pos.get("entry_price") or 0)
                best = min(cands, key=lambda s: abs(float(s["entry"]) - price))
                event = {"event": "FILLED", "from_status": best["status"], "to_status": "FILLED",
                         "price": price, "candle_open_time": None,
                         "details": {"position": position, "position_key": key, "source": "okx"},
                         "notify": True}
                ids = self.store.apply(best, "FILLED", best["state"], [event])
                fillable.remove(best)
                out.append((ids[-1], {**best, "status": "FILLED"}))
            else:
                eid = self.store.system_event("UNPLANNED_POSITION",
                                              {"symbol": symbol, "position": position,
                                               "position_key": key},
                                              price=pos.get("entry_price"))
                out.append((eid, None))
        return out

    def _system(self, event: str, details: dict[str, Any], now: int) -> int | None:
        last = self.store.last_system_at(event)
        if last is not None and now - last < SYSTEM_EVERY_MS:
            return None
        return self.store.system_event(event, details, at=now)

    def system_checks(self, now: int) -> list[int]:
        w = self.watcher_cfg()
        symbols = [str(s).upper() for s in (w.get("watch_symbols") or [])]
        out: list[int] = []
        if not symbols:
            return out
        local = datetime.fromtimestamp(now / 1000, self.tz).date()
        try:
            hh, mm = (int(x) for x in str(w.get("plan_deadline_utc") or "00:30").split(":"))
            deadline = int(datetime.combine(local, dtime(hh, mm), UTC).timestamp() * 1000)
        except ValueError:
            deadline = None
        if deadline is not None and now >= deadline:
            missing = [s for s in symbols if not self.store.list(symbol=s, bias_date=local.isoformat())]
            if missing:
                eid = self._system("PLAN_MISSING", {"symbols": missing, "bias_date": local.isoformat(),
                                                    "deadline": f"{hh:02d}:{mm:02d} UTC"}, now)
                if eid:
                    out.append(eid)
        tf = self.trigger_tf()
        span = self.span_ms(tf)
        stale: dict[str, Any] = {}
        for sym in symbols:
            latest = self.klines().get_latest_open_time(sym, tf)
            if latest is None or int(latest) + span < now - STALE_MS:
                stale[sym] = {"last_open_time": latest,
                              "minutes": round((now - int(latest) - span) / 60000) if latest else None}
        if stale:
            eid = self._system("FETCHER_STALE", {"symbols": stale}, now)
            if eid:
                out.append(eid)
        return out

    async def scan(self, now: int | None = None) -> dict[str, Any]:
        now = int(now if now is not None else self.now_ms())
        tf = self.trigger_tf()
        span = self.span_ms(tf)
        expected = now // span * span - span
        w = self.watcher_cfg()
        symbols = sorted({s["symbol"] for s in self.store.active()}
                         | {str(s).upper() for s in (w.get("watch_symbols") or [])})
        pending = list(symbols)
        processed: list[str] = []
        errors: dict[str, str] = {}
        sent: list[int] = []
        for attempt in range(CANDLE_RETRIES + 1):
            ready = [s for s in pending
                     if (self.klines().get_latest_open_time(s, tf) or -1) >= expected]
            if attempt == CANDLE_RETRIES:
                ready = list(pending)
            for sym in ready:
                try:
                    for eid, setup in self.process_symbol(sym, self.now_ms() if attempt else now):
                        self.notify(eid, setup)
                        sent.append(eid)
                    for eid, setup in await self.match_positions(sym):
                        self.notify(eid, setup)
                        sent.append(eid)
                except Exception as exc:
                    errors[sym] = f"{type(exc).__name__}: {exc}"
                    print(f"BAMCP watcher {sym} loi: {errors[sym]}", file=sys.stderr)
                processed.append(sym)
            pending = [s for s in pending if s not in ready]
            if not pending:
                break
            await self.sleep(CANDLE_RETRY_DELAY)
        try:
            for eid in self.system_checks(self.now_ms()):
                self.notify(eid)
                sent.append(eid)
        except Exception as exc:
            errors["system"] = f"{type(exc).__name__}: {exc}"
        self.last_scan = {"at": now, "expected_candle": expected, "symbols": processed,
                          "notified_events": sent, "errors": errors}
        return self.last_scan

    async def run_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.running = True
        try:
            while True:
                try:
                    if self.watcher_cfg().get("enabled"):
                        await self.scan()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"BAMCP watcher loi: {type(exc).__name__}: {exc}", file=sys.stderr)
                now = self.now_ms()
                await self.sleep(max(1.0, (self.next_wake(now) - now) / 1000))
        finally:
            self.running = False
            for task in list(self._tasks):
                task.cancel()
