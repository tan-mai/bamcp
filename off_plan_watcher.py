# TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
"""Job position_watcher: doi chieu moi vi the moi mo tren san voi ke hoach setup trong ngay.

Chay doc lap voi watcher setup M5, poll moi off_plan.poll_seconds. Moi lan poll doc LAI toan bo fill
cua ngay (gom theo vi the nhu get_fills) va vi the dang mo, roi so voi bang off_plan_trades:
  vi the chua tung thay  -> OPENED  (phan loai, gui tin neu ngoai ke hoach)
  lenh mo moi cung chieu -> ADDED   (phan loai rieng phan them)
  het SL sau thoi gian cho -> NO_STOP (tin bo sung)
  vi the dong            -> CLOSED  (PnL that, R)
Doc san loi thi bo qua cap do trong lan poll - khong doan, khong gui tin. Lan poll thanh cong sau doc
lai ca ngay nen vi the mo trong luc loi duoc quet bu, phan loai theo ke hoach TAI LUC MO.
Chi canh bao va ghi nhan: khong chan lenh, khong dong lenh.
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import notifier
import off_plan_logic as opl

DAY_MS = 86_400_000
LATE_MS = 120_000          # phat hien sau luc mo qua muc nay thi ghi "phat hien tre" trong tin


def _now_ms() -> int:
    return int(time.time() * 1000)


class OffPlanWatcher:
    def __init__(self, store, *, cfg: Callable[[], dict[str, Any]], notify_cfg: Callable[[], dict[str, Any]],
                 active: Callable[[], bool], symbols: Callable[[], list[str]],
                 read: Callable[[str, int, int], Awaitable[dict[str, Any]]],
                 stops: Callable[[str], Awaitable[list[dict[str, Any]] | None]],
                 classify: Callable[..., dict[str, Any]],
                 check_add: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
                 tz: ZoneInfo, display_tz: Callable[[], ZoneInfo],
                 now_ms: Callable[[], int] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 deliver: Callable[..., Awaitable[str | None]] = notifier.deliver,
                 link: Callable[[dict[str, Any]], str | None] | None = None) -> None:
        self.store = store
        self.cfg = cfg
        self.notify_cfg = notify_cfg
        self.active = active
        self.symbols = symbols
        self.read = read
        self.read_stops = stops
        self.classify = classify
        self.check_add = check_add
        self.tz = tz
        self.display_tz = display_tz
        self.now_ms = now_ms or _now_ms
        self.sleep = sleep
        self.deliver = deliver
        self.link = link
        self.running = False
        self.last_poll: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()
        self._last_send: asyncio.Future | None = None
        self._stops_cache: dict[str, list[dict[str, Any]] | None] = {}

    # ---------------------------------------------------------- cau hinh

    def poll_seconds(self) -> float:
        try:
            return max(5.0, float(self.cfg().get("poll_seconds") or 15))
        except (TypeError, ValueError):
            return 15.0

    def grace_ms(self) -> int:
        try:
            return int(float(self.cfg().get("no_stop_grace_seconds", 60)) * 1000)
        except (TypeError, ValueError):
            return 60_000

    def wants(self, kind: str) -> bool:
        kinds = self.cfg().get("notify_classifications")
        return True if kinds is None else kind in kinds

    def day_window(self, now: int) -> tuple[str, int]:
        local = datetime.fromtimestamp(now / 1000, self.tz).date()
        start = datetime.combine(local, datetime.min.time(), self.tz)
        return local.isoformat(), int(start.timestamp() * 1000)

    # ---------------------------------------------------------- gui tin

    async def _send(self, alert: notifier.Alert, *, row_id: str | None = None,
                    event_id: str | None = None) -> str | None:
        cfg = dict(self.notify_cfg() or {})
        channel = str(self.cfg().get("channel") or "").strip().lower()
        if channel:
            cfg["channel"] = channel
        error = await self.deliver(cfg, alert)
        if row_id:
            self.store.update(row_id, notified=not error, notify_error=error)
        if event_id:
            self.store.mark_event(event_id, error)
        if error:
            print(f"BAMCP off-plan notify loi ({alert.title}): {error}", file=sys.stderr)
        return error

    def notify(self, alert: notifier.Alert, **marks: Any) -> None:
        """Gui theo hang doi: tin mo, them, dong cua cung mot lan quet bu di dung thu tu."""
        previous = self._last_send

        async def job() -> None:
            if previous is not None:
                try:
                    await previous
                except Exception:
                    pass
            await self._send(alert, **marks)

        task = asyncio.ensure_future(job())
        self._last_send = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _link(self, row: dict[str, Any]) -> str | None:
        return self.link(row) if self.link else None

    # ---------------------------------------------------------- doc san

    async def _stops(self, symbol: str) -> list[dict[str, Any]] | None:
        if symbol not in self._stops_cache:
            try:
                self._stops_cache[symbol] = await self.read_stops(symbol)
            except Exception as exc:
                print(f"BAMCP off-plan: khong doc duoc lenh SL/TP {symbol} - {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                self._stops_cache[symbol] = None
                return None
        return self._stops_cache[symbol]

    @staticmethod
    def _position_for(data: dict[str, Any], side: str, opened_at: int | None) -> dict[str, Any] | None:
        rows = [p for p in data.get("positions") or [] if p.get("side") == side]
        if not rows:
            return None
        if opened_at is None:
            return rows[0]
        return min(rows, key=lambda p: abs(int(p.get("opened_ms") or 0) - int(opened_at)))

    async def _exchange_stops(self, symbol: str, side: str, entry: float | None,
                              data: dict[str, Any]) -> tuple[float | None, float | None, bool]:
        """(sl, tp, doc_duoc). doc_duoc = False nghia la san / adapter khong tra duoc lenh algo."""
        orders = await self._stops(symbol)
        pos = self._position_for(data, side, None)
        extra = list((pos or {}).get("close_algos") or [])
        if orders is None and not extra:
            return None, None, False
        sl, tp = opl.pick_stops(side, entry, list(orders or []) + extra)
        return sl, tp, orders is not None

    # ---------------------------------------------------------- vong poll

    async def poll(self, now: int | None = None) -> dict[str, Any]:
        now = int(now if now is not None else self.now_ms())
        day, day_start = self.day_window(now)
        symbols = [str(s).upper() for s in self.symbols()]
        self._stops_cache = {}
        results = await asyncio.gather(*[self.read(s, day_start, now) for s in symbols], return_exceptions=True)
        ok: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, BaseException):
                errors[symbol] = f"{type(result).__name__}: {result}"
            else:
                ok[symbol] = result
        self._log_errors(errors)
        items: list[dict[str, Any]] = []
        for symbol, data in ok.items():
            items.extend(self._items(symbol, data, day_start))
        # Xu ly theo thu tu thoi gian tren MOI cap: vi the dong lo truoc phai co mat khi xet REVENGE
        processed = 0
        for item in sorted(items, key=lambda i: int(i["opened_at"])):
            try:
                if await self._sync(item, ok[item["symbol"]], now):
                    processed += 1
            except Exception as exc:
                errors[item["symbol"]] = f"{type(exc).__name__}: {exc}"
                print(f"BAMCP off-plan {item['symbol']} loi: {errors[item['symbol']]}", file=sys.stderr)
        for symbol, data in ok.items():
            try:
                await self._older(symbol, data, now, day_start)
                await self._check_stops(symbol, data, now)
            except Exception as exc:
                errors[symbol] = f"{type(exc).__name__}: {exc}"
                print(f"BAMCP off-plan {symbol} loi: {errors[symbol]}", file=sys.stderr)
        self.last_poll = {"at": now, "date": day, "symbols": sorted(ok), "errors": errors, "positions": processed}
        return self.last_poll

    def _log_errors(self, errors: dict[str, str]) -> None:
        for symbol, error in errors.items():
            if self.errors.get(symbol) != error:
                print(f"BAMCP off-plan: khong doc duoc san {symbol} - {error}. Bo qua cap nay, khong gui "
                      "canh bao; lan doc thanh cong sau se quet bu.", file=sys.stderr)
        for symbol in [s for s in self.errors if s not in errors]:
            print(f"BAMCP off-plan: doc lai duoc san {symbol} - quet bu vi the trong ngay", file=sys.stderr)
        self.errors = dict(errors)

    def _items(self, symbol: str, data: dict[str, Any], day_start: int) -> list[dict[str, Any]]:
        """Vi the trong ngay: tu fill (nguon chinh) + vi the dang mo ma fill chua kip ve."""
        out = []
        groups = [opl.summarize_group(g) for g in data.get("groups") or []]
        for g in groups:
            if g["carried_in"] or g["opened_at"] is None:
                continue
            pos = None if g["closed"] else self._position_for(data, g["side"], g["opened_at"])
            out.append({**g, "symbol": symbol, "source": "fills", "position": pos})
        for pos in data.get("positions") or []:
            opened = int(pos.get("opened_ms") or 0)
            if opened < day_start:
                continue
            if any(g["side"] == pos.get("side") and not g["closed"] and g["opened_at"] is not None
                   and abs(g["opened_at"] - opened) <= opl.MATCH_TOLERANCE_MS for g in groups):
                continue
            out.append({"symbol": symbol, "source": "position", "position_id": None, "side": pos.get("side"),
                        "carried_in": False, "closed": False, "opened_at": opened, "closed_at": None, "legs": [],
                        "entry_avg": pos.get("entry_price"), "qty": pos.get("size"), "position": pos,
                        "exit_avg": None, "realized_pnl": 0.0, "fee": 0.0})
        return out

    # ---------------------------------------------------------- mot vi the

    async def _sync(self, item: dict[str, Any], data: dict[str, Any], now: int, create: bool = True) -> bool:
        first = item["legs"][0]["order_id"] if item["legs"] else None
        row = self.store.match(item["symbol"], item["side"], item["opened_at"], first)
        if row is None:
            if not create:
                return False
            row = await self._open(item, data, now)
        else:
            row = self._enrich(row, item)
        row = await self._adds(row, item, now)
        if item["closed"] and row["closed_at"] is None:
            self._close(row, item)
        return True

    def _enrich(self, row: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if item.get("position_id") and row.get("position_id") != item["position_id"]:
            changes["position_id"] = item["position_id"]
        if item["legs"] and not row["orders"]:
            # Dong tao tu vi the dang mo (fill chua ve): lenh dau tien la lenh mo, cac lenh sau la lenh them
            changes["orders"] = [item["legs"][0]["order_id"]]
        orders = changes.get("orders") or row["orders"]
        known = [leg for leg in item["legs"] if leg["order_id"] in orders]
        if known:
            entry, qty = opl.legs_total(known)
            if entry is not None and (entry != row.get("entry_avg") or qty != row.get("qty")):
                changes.update(entry_avg=entry, qty=qty)
        pos = item.get("position") or {}
        for key, source in (("margin_usd", "margin_usd"), ("leverage", "leverage")):
            if row.get(key) is None and pos.get(source):
                changes[key] = pos[source]
        return self.store.update(row["id"], **changes) if changes else row

    async def _open(self, item: dict[str, Any], data: dict[str, Any], now: int) -> dict[str, Any]:
        leg = item["legs"][0] if item["legs"] else None
        entry = float(leg["price"] if leg else item.get("entry_avg") or 0)
        qty = float(leg["qty"] if leg else item.get("qty") or 0)
        pos = item.get("position") or {}
        sl = tp = None
        readable = False
        if not item["closed"]:
            sl, tp, readable = await self._exchange_stops(item["symbol"], item["side"], entry, data)
        info = self.classify({"symbol": item["symbol"], "side": item["side"], "entry": entry,
                              "opened_at": item["opened_at"], "margin_usd": pos.get("margin_usd"),
                              "sl": sl, "tp": tp})
        flags = list(info["flags"])
        if not item["closed"] and readable and sl is None and now - item["opened_at"] >= self.grace_ms():
            flags = [f for f in opl.FLAGS if f in set(flags) | {"NO_STOP"}]
        late = now - int(item["opened_at"])
        explain = {**info["explain"], "entry": entry}
        if late > LATE_MS:
            explain["late_minutes"] = round(late / 60000)
        row = self.store.insert({
            "position_id": item.get("position_id"), "symbol": item["symbol"], "side": item["side"],
            "trade_date": opl.local_day(item["opened_at"], self.tz), "opened_at": int(item["opened_at"]),
            "entry_avg": entry, "qty": qty, "margin_usd": pos.get("margin_usd"), "leverage": pos.get("leverage"),
            "exchange_sl": sl, "exchange_tp": tp, "classification": info["classification"], "flags": flags,
            "matched_setup_id": info.get("matched_setup_id"), "candidate_setups": info.get("candidates") or [],
            "tr_snapshot": info.get("tr"), "rule_check": info.get("rule_check"), "rule_set": info.get("rule_set"),
            "explain": explain, "orders": [leg["order_id"]] if leg else []})
        self.store.add_event(row["id"], "OPENED", {
            "source": item["source"], "entry": entry, "qty": qty, "classification": row["classification"],
            "flags": flags, "matched_setup_id": row["matched_setup_id"], "late_ms": late,
            "forced_by": info.get("forced_by")}, at=now)
        if opl.alertable(row):
            row = self.store.update(row["id"], code=self.store.assign_code(row["id"]))
            if self.wants(row["classification"]):
                self.notify(opl.open_alert(row, self.display_tz(), link=self._link(row)), row_id=row["id"])
        return row

    async def _adds(self, row: dict[str, Any], item: dict[str, Any], now: int) -> dict[str, Any]:
        if not row["orders"]:
            return row
        for leg in item["legs"]:
            if leg["order_id"] in row["orders"] or leg.get("price") is None:
                continue
            verdict = self.check_add(row, leg)
            orders = row["orders"] + [leg["order_id"]]
            entry, qty = opl.legs_total([g for g in item["legs"] if g["order_id"] in orders])
            changes: dict[str, Any] = {"orders": orders, "entry_avg": entry, "qty": qty}
            if not verdict.get("valid"):
                changes["flags"] = [f for f in opl.FLAGS if f in set(row["flags"]) | {opl.ADD_OFF_PLAN}]
            row = self.store.update(row["id"], **changes)
            add = {"order_id": leg["order_id"], "at": leg["at"], "qty": leg["qty"], "price": leg["price"],
                   "valid": bool(verdict.get("valid")), "reason": verdict.get("reason"),
                   "setup_code": verdict.get("setup_code")}
            event_id = self.store.add_event(row["id"], "ADDED", add, at=now)
            if not verdict.get("valid"):
                row = self.store.update(row["id"], code=self.store.assign_code(row["id"]))
                if self.wants(opl.ADD_OFF_PLAN):
                    self.notify(opl.add_alert(row, add, self.display_tz(), link=self._link(row)), event_id=event_id)
        return row

    def _close(self, row: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        realized, fee = opl.round2(item["realized_pnl"]), opl.round2(item["fee"])
        r_value = opl.r_multiple(row["side"], row.get("entry_avg"), item.get("exit_avg"), row.get("exchange_sl"))
        row = self.store.update(row["id"], closed_at=int(item["closed_at"] or self.now_ms()), realized_pnl=realized,
                                fee=fee, r_multiple=r_value)
        event_id = self.store.add_event(row["id"], "CLOSED", {
            "exit_avg": item.get("exit_avg"), "realized_pnl": realized, "fee": fee, "r_multiple": r_value,
            "r_note": None if r_value is not None else "không có SL nên không tính được R"})
        if row.get("code"):
            self.notify(opl.close_alert(row), event_id=event_id)
        return row

    # ---------------------------------------------------------- vi the tu hom truoc

    async def _older(self, symbol: str, data: dict[str, Any], now: int, day_start: int) -> None:
        """Vi the mo tu ngay truoc: dong da theo doi ma con mo, hoac vi the dang mo tren san mo trong 24h
        qua (watcher tat qua dem). Doc fill tu luc mo de gom dung vi the va PnL nhieu ngay."""
        rows = [r for r in self.store.open_rows(symbol) if r["opened_at"] < day_start]
        fresh = [int(p["opened_ms"]) for p in data.get("positions") or []
                 if p.get("opened_ms") and now - DAY_MS <= int(p["opened_ms"]) < day_start
                 and self.store.match(symbol, p.get("side"), int(p["opened_ms"])) is None]
        if not rows and not fresh:
            return
        start = min([r["opened_at"] for r in rows] + fresh) - 1000
        older = await self.read(symbol, start, now)
        for group in older.get("groups") or []:
            item = {**opl.summarize_group(group), "symbol": symbol, "source": "fills"}
            if item["carried_in"] or item["opened_at"] is None or item["opened_at"] >= day_start:
                continue
            # Day la id theo cua so doc nhieu ngay, khong phai id cua get_fills ngay do
            item["position_id"] = None
            item["position"] = None if item["closed"] else self._position_for(data, item["side"], item["opened_at"])
            # Chi tao dong moi cho vi the CON MO tren san; vi the cu da dong ma chua theo doi thi bo qua
            create = not item["closed"] and any(abs(item["opened_at"] - f) <= opl.MATCH_TOLERANCE_MS for f in fresh)
            await self._sync(item, data, now, create=create)

    # ---------------------------------------------------------- SL that

    async def _check_stops(self, symbol: str, data: dict[str, Any], now: int) -> None:
        for row in self.store.open_rows(symbol):
            if row["exchange_sl"] is not None:
                continue
            pos = self._position_for(data, row["side"], row["opened_at"])
            if pos is None:
                continue          # san khong con vi the: cho fill dong ve, khong bao NO_STOP
            sl, tp, readable = await self._exchange_stops(symbol, row["side"], row.get("entry_avg"), data)
            if sl is not None or (tp is not None and row["exchange_tp"] is None):
                row = self._stops_found(row, sl, tp, now)
            if (sl is None and readable and "NO_STOP" not in row["flags"]
                    and now - int(row["opened_at"]) >= self.grace_ms()):
                flags = [f for f in opl.FLAGS if f in set(row["flags"]) | {"NO_STOP"}]
                row = self.store.update(row["id"], flags=flags)
                event_id = self.store.add_event(row["id"], "NO_STOP", {"grace_seconds": self.grace_ms() // 1000},
                                                at=now)
                row = self.store.update(row["id"], code=self.store.assign_code(row["id"]))
                kind = row["classification"]
                if self.wants(kind):
                    self.notify(opl.no_stop_alert(row, self.display_tz(), self.grace_ms() // 1000,
                                                  link=self._link(row)), event_id=event_id)

    def _stops_found(self, row: dict[str, Any], sl: float | None, tp: float | None, now: int) -> dict[str, Any]:
        """SL/TP xuat hien sau luc mo: cham lai rule voi so that (giu nguyen phan loai). Dong chua tung bao
        ma nay moi pham rule thi gui tin mo; dong da bao roi chi cap nhat, khong gui lan hai."""
        sl = sl if sl is not None else row["exchange_sl"]
        tp = tp if row["exchange_tp"] is None else row["exchange_tp"]
        explain = row.get("explain") or {}
        info = self.classify({"symbol": row["symbol"], "side": row["side"], "entry": explain.get("entry", row["entry_avg"]),
                              "opened_at": row["opened_at"], "margin_usd": row.get("margin_usd"), "sl": sl, "tp": tp,
                              "row_id": row["id"]},
                             forced=row["matched_setup_id"] if explain.get("forced_by") else None)
        flags = [f for f in opl.FLAGS if f in set(info["flags"]) | {f for f in row["flags"] if f in opl.KEEP_FLAGS}]
        before = opl.alertable(row)
        row = self.store.update(row["id"], exchange_sl=sl, exchange_tp=tp, rule_check=info.get("rule_check"),
                                flags=flags)
        self.store.add_event(row["id"], "STOPS", {"sl": sl, "tp": tp, "flags": flags,
                                                  "violations": (row.get("rule_check") or {}).get("violations")},
                             at=now)
        if not before and opl.alertable(row) and not row.get("code"):
            row = self.store.update(row["id"], code=self.store.assign_code(row["id"]))
            if self.wants(row["classification"]):
                self.notify(opl.open_alert(row, self.display_tz(), link=self._link(row)), row_id=row["id"])
        return row

    # ---------------------------------------------------------- vong doi

    async def run_forever(self) -> None:
        self.running = True
        try:
            while True:
                try:
                    if self.active():
                        await self.poll()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"BAMCP off-plan loi: {type(exc).__name__}: {exc}", file=sys.stderr)
                await self.sleep(self.poll_seconds())
        finally:
            self.running = False
            for task in list(self._tasks):
                task.cancel()
