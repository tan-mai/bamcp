# TM - #M5-FETCH - Per-timeframe schedules + M5
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Awaitable, Callable

import cron_utc
import sqlite_db

DEFAULT_SCHEDULES = (
    ("pull-5m", "5m", "5 */5 * * * *"),
    ("pull-15m", "15m", "5 */15 * * * *"),
    ("pull-1h", "1h", "5 0 * * * *"),
    ("pull-4h", "4h", "5 0 */4 * * *"),
    ("pull-1d", "1d", "5 0 0 * * *"),
    ("pull-1w", "1w", "5 0 0 * * MON"),
)
COLUMNS = ("id", "name", "timeframe", "symbols", "cron", "enabled", "overlap_candles",
           "last_run_at", "last_status", "last_error", "last_duration_ms",
           "created_at", "updated_at")
STATUSES = ("ok", "error", "skipped")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row(row: Any) -> dict[str, Any]:
    out = {k: row[k] for k in COLUMNS}
    out["symbols"] = json.loads(out["symbols"]) if out["symbols"] else None
    out["enabled"] = bool(out["enabled"])
    return out


class ScheduleStore:
    def __init__(self, path: Path, *, supported: Callable[[], list[str]],
                 span_ms: Callable[[str], int], now_ms: Callable[[], int] | None = None) -> None:
        self.path = Path(path)
        self.supported = supported
        self.span_ms = span_ms
        self.now_ms = now_ms or _now_ms
        sqlite_db.migrate(self.path)

    def _conn(self):
        return closing(sqlite_db.connect(self.path))

    def list(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM fetch_schedules ORDER BY timeframe, name").fetchall()
        order = {tf: i for i, tf in enumerate(self.supported())}
        items = [_row(r) for r in rows]
        items.sort(key=lambda s: (order.get(s["timeframe"], 99), s["name"]))
        return items

    def get(self, ref: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM fetch_schedules WHERE id = ? OR name = ?",
                               (ref, ref)).fetchone()
        if row is None:
            raise ValueError(f"khong tim thay schedule {ref!r}")
        return _row(row)

    def seed_defaults(self) -> int:
        with self._conn() as conn:
            if conn.execute("SELECT COUNT(*) FROM fetch_schedules").fetchone()[0]:
                return 0
        for name, tf, cron in DEFAULT_SCHEDULES:
            self.create({"name": name, "timeframe": tf, "cron": cron, "symbols": None,
                         "enabled": True, "overlap_candles": 3})
        return len(DEFAULT_SCHEDULES)

    def validate(self, data: dict[str, Any], current_id: str | None = None
                 ) -> tuple[dict[str, Any], list[str]]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("ten schedule khong duoc rong")
        tf = str(data.get("timeframe") or "").strip()
        if tf not in self.supported():
            raise ValueError(f"khung {tf!r} khong nam trong supported_timeframes {self.supported()}")
        spec = cron_utc.parse(str(data.get("cron") or ""))
        raw_symbols = data.get("symbols")
        if isinstance(raw_symbols, str):
            raw_symbols = [s for s in raw_symbols.replace(";", ",").split(",")]
        symbols = sorted({str(s).strip().upper() for s in (raw_symbols or []) if str(s).strip()})
        try:
            overlap = int(data.get("overlap_candles", 3))
        except (TypeError, ValueError):
            raise ValueError("overlap_candles phai la so nguyen") from None
        if not 0 <= overlap <= 1000:
            raise ValueError("overlap_candles phai trong [0, 1000]")
        with self._conn() as conn:
            clash = conn.execute("SELECT id FROM fetch_schedules WHERE name = ? AND id != ?",
                                 (name, current_id or "")).fetchone()
        if clash:
            raise ValueError(f"ten schedule {name!r} da ton tai")
        clean = {"name": name, "timeframe": tf, "cron": spec.expr, "symbols": symbols or None,
                 "enabled": bool(data.get("enabled", True)), "overlap_candles": overlap}
        return clean, cron_utc.warnings(spec, self.span_ms(tf), self.now_ms())

    def create(self, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        clean, warns = self.validate(data)
        now = self.now_ms()
        sid = str(uuid.uuid4())
        with self._conn() as conn, conn:
            conn.execute(
                "INSERT INTO fetch_schedules (id, name, timeframe, symbols, cron, enabled, "
                "overlap_candles, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, clean["name"], clean["timeframe"],
                 json.dumps(clean["symbols"]) if clean["symbols"] else None,
                 clean["cron"], int(clean["enabled"]), clean["overlap_candles"], now, now))
        return self.get(sid), warns

    def update(self, ref: str, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        current = self.get(ref)
        clean, warns = self.validate({**current, **data}, current["id"])
        with self._conn() as conn, conn:
            conn.execute(
                "UPDATE fetch_schedules SET name = ?, timeframe = ?, symbols = ?, cron = ?, "
                "enabled = ?, overlap_candles = ?, updated_at = ? WHERE id = ?",
                (clean["name"], clean["timeframe"],
                 json.dumps(clean["symbols"]) if clean["symbols"] else None,
                 clean["cron"], int(clean["enabled"]), clean["overlap_candles"],
                 self.now_ms(), current["id"]))
        return self.get(current["id"]), warns

    def set_enabled(self, ref: str, enabled: bool) -> dict[str, Any]:
        current = self.get(ref)
        with self._conn() as conn, conn:
            conn.execute("UPDATE fetch_schedules SET enabled = ?, updated_at = ? WHERE id = ?",
                         (int(bool(enabled)), self.now_ms(), current["id"]))
        return self.get(current["id"])

    def delete(self, ref: str) -> dict[str, Any]:
        current = self.get(ref)
        with self._conn() as conn, conn:
            conn.execute("DELETE FROM fetch_schedules WHERE id = ?", (current["id"],))
        return current

    def record_run(self, sid: str, *, at: int, status: str, error: str | None,
                   duration_ms: int) -> None:
        with self._conn() as conn, conn:
            conn.execute(
                "UPDATE fetch_schedules SET last_run_at = ?, last_status = ?, last_error = ?, "
                "last_duration_ms = ? WHERE id = ?", (at, status, error, duration_ms, sid))

    def preview(self, cron: str, timeframe: str, count: int = 3) -> dict[str, Any]:
        spec = cron_utc.parse(cron)
        now = self.now_ms()
        warns = cron_utc.warnings(spec, self.span_ms(timeframe), now) if timeframe else []
        return {"cron": spec.expr, "next_runs": cron_utc.next_runs(spec, now, count),
                "warnings": warns}


class FetchScheduler:
    def __init__(self, store: ScheduleStore, run: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
                 *, reload_seconds: float = 30.0, on_done: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
                 now_ms: Callable[[], int] | None = None, catch_up: bool = True) -> None:
        self.store = store
        self.run = run
        self.reload_seconds = max(1.0, float(reload_seconds))
        self.on_done = on_done
        self.catch_up = catch_up
        self.now_ms = now_ms or _now_ms
        self.next_due: dict[str, int] = {}
        self.running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None
        self._tasks: set[asyncio.Task] = set()

    def wake(self) -> None:
        loop, event = self._loop, self._event
        if loop is None or event is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass

    def _plan(self, schedules: list[dict[str, Any]], now: int,
              known: dict[str, tuple[str, int]]) -> dict[str, tuple[str, int]]:
        plan: dict[str, tuple[str, int]] = {}
        for item in schedules:
            if not item["enabled"]:
                continue
            key = f"{item['cron']}|{item['updated_at']}"
            old = known.get(item["id"])
            if old and old[0] == key:
                plan[item["id"]] = old
                continue
            try:
                plan[item["id"]] = (key, cron_utc.next_after(cron_utc.parse(item["cron"]), now))
            except ValueError as exc:
                print(f"BAMCP schedule {item['name']}: {exc}", file=sys.stderr)
        return plan

    async def execute(self, item: dict[str, Any]) -> dict[str, Any]:
        started = self.now_ms()
        try:
            outcome = await self.run(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            outcome = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        duration = self.now_ms() - started
        status = outcome.get("status") if outcome.get("status") in STATUSES else "error"
        try:
            self.store.record_run(item["id"], at=started, status=status,
                                  error=outcome.get("error"), duration_ms=duration)
        except Exception as exc:
            print(f"BAMCP schedule {item['name']}: khong ghi duoc ket qua - {exc}", file=sys.stderr)
        result = {**outcome, "status": status, "duration_ms": duration, "at": started}
        if self.on_done:
            self.on_done(item, result)
        return result

    def spawn(self, item: dict[str, Any]) -> asyncio.Task:
        task = asyncio.ensure_future(self.execute(item))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def missed(self, schedules: list[dict[str, Any]], now: int) -> list[dict[str, Any]]:
        out = []
        for item in schedules:
            if not item["enabled"]:
                continue
            try:
                spec = cron_utc.parse(item["cron"])
                if item["last_run_at"] is None or cron_utc.next_after(spec, int(item["last_run_at"])) <= now:
                    out.append(item)
            except ValueError:
                continue
        return out

    async def run_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()
        self.running = True
        plan: dict[str, tuple[str, int]] = {}
        schedules: dict[str, dict[str, Any]] = {}
        reload_at = 0.0
        try:
            while True:
                try:
                    now = self.now_ms()
                    if now >= reload_at or self._event.is_set():
                        self._event.clear()
                        rows = self.store.list()
                        schedules = {r["id"]: r for r in rows}
                        if self.catch_up and not plan and reload_at == 0.0:
                            for item in self.missed(rows, now):
                                self.spawn(item)
                        plan = self._plan(rows, now, plan)
                        reload_at = now + self.reload_seconds * 1000
                    for sid, (key, due) in list(plan.items()):
                        if due <= now and sid in schedules:
                            self.spawn(schedules[sid])
                            nxt = cron_utc.next_after(cron_utc.parse(schedules[sid]["cron"]), now)
                            plan[sid] = (key, nxt)
                    self.next_due = {sid: due for sid, (_k, due) in plan.items()}
                    wake_at = min([due for _k, due in plan.values()] + [reload_at])
                    sleep_s = max(0.2, min(60.0, (wake_at - self.now_ms()) / 1000))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"BAMCP fetch scheduler loi: {type(exc).__name__}: {exc}", file=sys.stderr)
                    sleep_s = 5.0
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=sleep_s)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.running = False
            for task in list(self._tasks):
                task.cancel()
