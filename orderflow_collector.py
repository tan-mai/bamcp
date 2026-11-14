# TM - #BOOKMAP - Order flow Bookmap
"""bamcp-orderflow: process rieng thu so lenh + lenh khop tu Binance, ghi vao orderflow.db.

Chay: python orderflow_collector.py            (systemd: bamcp-orderflow.service, Restart=always)
      python orderflow_collector.py --health   (exit 0 neu heartbeat con moi - healthcheck Docker)
      python orderflow_collector.py --run-seconds 600   (chay thu roi tu dung)

Khong chay trong process MCP server: collector loi / dung thi server va dashboard van doc du lieu cu.
Mot process xu ly moi symbol: moi market hai ket noi combined stream (futures: depth o /public, aggTrade o /market).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import logging.handlers
import os
import random
import signal
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx2 as httpx
from websockets.asyncio.client import connect as ws_connect

import orderflow_config
import orderflow_logic as logic
import orderflow_store

LOG = logging.getLogger("bamcp.orderflow")
UTC = timezone.utc


def now_ms() -> int:
    return int(time.time() * 1000)


def setup_logging(log_dir: Path, max_mb: int, backups: int) -> None:
    """Giong phan con lai cua BAMCP: dong 'BAMCP orderflow ...' ra stderr (docker logs), them file co rotate."""
    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    fmt = logging.Formatter("%(asctime)s BAMCP orderflow %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    fmt.converter = time.gmtime
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    LOG.addHandler(stream)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(log_dir / "collector.log", maxBytes=max_mb * 1024 * 1024,
                                                       backupCount=backups, encoding="utf-8")
        handler.setFormatter(fmt)
        LOG.addHandler(handler)
    except OSError as exc:
        LOG.warning("khong mo duoc file log %s: %s", log_dir, exc)


class RestLimiter:
    """Ton trong rate limit Binance: doc X-MBX-USED-WEIGHT-1M, gan tran thi doi sang phut ke tiep."""

    def __init__(self, soft_limit: int = 1800):
        self.soft_limit = soft_limit
        self.used = 0
        self.minute = 0

    async def before(self) -> None:
        minute = int(time.time() // 60)
        if minute != self.minute:
            self.minute, self.used = minute, 0
        if self.used >= self.soft_limit:
            wait = 60 - time.time() % 60 + 0.5
            LOG.warning("REST weight %s/phut - doi %.0fs", self.used, wait)
            await asyncio.sleep(wait)

    def after(self, response: httpx.Response) -> None:
        with contextlib.suppress(TypeError, ValueError):
            self.used = int(response.headers.get("x-mbx-used-weight-1m") or self.used)
        if response.status_code in (418, 429):
            self.used = self.soft_limit


class SymbolState:
    def __init__(self, cfg: dict[str, Any], status: dict[str, Any] | None):
        self.cfg = cfg
        self.symbol = cfg["symbol"]
        self.market = cfg["market"]
        self.book = logic.BookSync(self.market)
        self.fp = logic.Footprint()
        self.bubbles = logic.BubbleMerger(cfg["bubble_merge_ms"])
        self.pending_fp: dict[int, list[tuple]] = {}
        self.pending_big: list[dict[str, Any]] = []
        status = status or {}
        self.last_agg_id: int | None = status.get("last_agg_id")
        self.trades_since_ts: int | None = status.get("trades_since_ts")
        self.book_since_ts: int | None = status.get("book_since_ts")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        self.resync_day = today
        self.resyncs_today = int(status.get("resyncs_today") or 0) if status.get("resync_day") == today else 0
        self.last_trade_ts: int | None = None
        self.last_depth_ts: int | None = None
        self.last_snapshot_ts: int | None = status.get("last_snapshot_ts")
        self.next_snapshot = 0.0
        self.next_prune = 0.0
        self.snapshot_task: asyncio.Task | None = None
        self.resync_times: list[float] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.consumer: asyncio.Task | None = None
        self.last_error = ""
        self.dirty = False
        # Gap depth dang mo (trong DB); True luc khoi dong de snapshot dau tien dong gap con sot tu lan chay truoc
        self.depth_gap_open = True
        self.aggs: list[logic.SnapshotAgg] = []      # snapshot gop san 10s / 60s / 300s cho heatmap khoang dai

    def streams(self) -> tuple[str, str]:
        low = self.symbol.lower()
        return f"{low}@depth@100ms", f"{low}@aggTrade"

    def count_resync(self) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self.resync_day:
            self.resync_day, self.resyncs_today = today, 0
        self.resyncs_today += 1


class StreamConn:
    """Mot ket noi combined stream. Tu noi lai voi backoff 1s -> 60s, chu dong noi lai truoc moc 24h."""

    def __init__(self, collector: "Collector", market: str, kind: str, url: str):
        self.collector = collector
        self.market = market
        self.kind = kind            # depth | trades
        self.url = url
        self.streams: set[str] = set()
        self.ws = None
        self.connected = False
        self.task: asyncio.Task | None = None
        self.msg_id = 0
        self._send_times: list[float] = []

    async def _send(self, payload: dict[str, Any]) -> None:
        # Toi da 10 tin nhan / giay / ket noi: giu duoi 5 cho chac
        now = time.monotonic()
        self._send_times = [t for t in self._send_times if now - t < 1.0]
        if len(self._send_times) >= 5:
            await asyncio.sleep(1.0 - (now - self._send_times[0]) + 0.05)
        self._send_times.append(time.monotonic())
        await self.ws.send(json.dumps(payload))

    async def update(self, streams: set[str]) -> None:
        added, removed = streams - self.streams, self.streams - streams
        self.streams = set(streams)
        if not self.connected or self.ws is None:
            return
        for method, items in (("UNSUBSCRIBE", sorted(removed)), ("SUBSCRIBE", sorted(added))):
            for chunk in logic.iter_chunks(items, 100):
                self.msg_id += 1
                await self._send({"method": method, "params": chunk, "id": self.msg_id})

    async def run(self) -> None:
        backoff = 1.0
        tech = self.collector.cfg["tech"]
        while True:
            if not self.streams:
                await asyncio.sleep(1)
                continue
            started = time.monotonic()
            try:
                async with ws_connect(self.url, open_timeout=15, ping_interval=60, ping_timeout=120,
                                      max_size=2 ** 24, close_timeout=5) as ws:
                    self.ws = ws
                    self.connected = True
                    LOG.info("%s %s: da ket noi %s (%d stream)", self.market, self.kind, self.url, len(self.streams))
                    for chunk in logic.iter_chunks(sorted(self.streams), 100):
                        self.msg_id += 1
                        await self._send({"method": "SUBSCRIBE", "params": chunk, "id": self.msg_id})
                    await self.collector.on_connected(self)
                    backoff = 1.0
                    deadline = started + float(tech["reconnect_hours"]) * 3600
                    while True:
                        timeout = max(1.0, deadline - time.monotonic())
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=min(timeout, 90))
                        except asyncio.TimeoutError:
                            if time.monotonic() >= deadline:
                                LOG.info("%s %s: chu dong noi lai truoc moc 24h", self.market, self.kind)
                                break
                            raise ConnectionError("khong nhan duoc du lieu 90 giay")
                        self.collector.on_message(self, raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - moi loi mang deu dan toi noi lai
                LOG.warning("%s %s: mat ket noi - %s: %s", self.market, self.kind, type(exc).__name__, exc)
                self.collector.note_error(self, f"{type(exc).__name__}: {exc}")
            finally:
                was = self.connected
                self.connected = False
                self.ws = None
                if was:
                    await self.collector.on_disconnected(
                        self, "collector dung" if self.collector.stopping.is_set() else "reconnect")
            if time.monotonic() - started > 120:
                backoff = 1.0
            delay = backoff + random.random() * 0.5
            backoff = min(60.0, backoff * 2)
            await asyncio.sleep(delay)


class Collector:
    def __init__(self, config: orderflow_config.OrderflowConfig, *, run_seconds: float | None = None):
        self.config = config
        self.cfg = config.get()
        self.store = orderflow_store.OrderflowStore(self.cfg["paths"]["db"])
        orderflow_store.ensure(self.store.path)
        self.db = orderflow_store.connect(self.store.path)
        self.states: dict[str, SymbolState] = {}
        self.conns: dict[tuple[str, str], StreamConn] = {}
        self.http = httpx.AsyncClient(timeout=20)
        self.limiter = RestLimiter()
        self.run_seconds = run_seconds
        self.stopping = asyncio.Event()

    # ------------------------------------------------------------ vong doi

    async def run(self) -> None:
        LOG.info("khoi dong pid=%s db=%s", os.getpid(), self.store.path)
        await self.apply_config(self.cfg)
        tasks = [asyncio.create_task(c, name=n) for n, c in (
            ("snapshots", self.snapshot_loop()), ("flush", self.flush_loop()),
            ("heartbeat", self.heartbeat_loop()), ("config", self.config_loop()),
            ("retention", self.retention_loop()))]
        if self.run_seconds:
            tasks.append(asyncio.create_task(self._stop_after(self.run_seconds)))
        try:
            await self.stopping.wait()
        finally:
            self.stopping.set()
            for task in tasks:
                task.cancel()
            for conn in self.conns.values():
                if conn.task:
                    conn.task.cancel()
            for state in self.states.values():
                if state.consumer:
                    state.consumer.cancel()
            await asyncio.gather(*tasks, *[c.task for c in self.conns.values() if c.task],
                                 *[s.consumer for s in self.states.values() if s.consumer], return_exceptions=True)
            for state in self.states.values():
                state.pending_big.extend(g for g in state.bubbles.flush() if g["qty"] >= state.cfg["big_trade_min_qty"])
            self.flush_all()
            self.write_status(final=True)
            await self.http.aclose()
            self.db.close()
            LOG.info("da dung")

    async def _stop_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        self.stopping.set()

    # ------------------------------------------------------------ cau hinh

    async def config_loop(self) -> None:
        while True:
            await asyncio.sleep(float(self.cfg["tech"]["config_poll_sec"]))
            before = self.config.last_error
            try:
                cfg = self.config.get()
            except Exception as exc:  # noqa: BLE001
                LOG.error("doc config loi, giu cau hinh cu: %s", exc)
                continue
            if self.config.last_error and self.config.last_error != before:
                LOG.error("config khong hop le, giu cau hinh cu: %s", self.config.last_error)
            if cfg is not self.cfg:
                await self.apply_config(cfg)

    async def apply_config(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        wanted = {s["symbol"]: s for s in self.config.active_symbols()}
        for symbol in list(self.states):
            state = self.states[symbol]
            new = wanted.get(symbol)
            if new is None or new["market"] != state.market:
                LOG.info("%s: ngung theo doi (%s)", symbol, "tat tren admin" if new is None else "doi market")
                await self.drop_symbol(symbol, "tat tren admin" if new is None else "doi market")
            else:
                state.cfg = new                    # tham so moi co hieu luc tu snapshot ke tiep
                state.bubbles.merge_ms = new["bubble_merge_ms"]
        saved = {r["symbol"]: r for r in self.store.status()}
        for symbol, scfg in wanted.items():
            if symbol not in self.states:
                state = SymbolState(scfg, saved.get(symbol))
                state.aggs = [logic.SnapshotAgg(int(r)) for r in cfg["tech"]["heatmap_agg_levels"]]
                self.states[symbol] = state
                # Cua so gop con thieu tu lan chay truoc: gop lai tu snapshot tho, chay nen
                asyncio.create_task(self.rebuild_agg(state))
                # Lan chay truoc dung giua chung (crash, deploy): vung khong co snapshot tu do toi gio la gap
                last = state.last_snapshot_ts
                if last and now_ms() - last > 3000 * scfg["snapshot_interval_sec"]:
                    self.store.open_gap(symbol, "depth", last, "collector dung")
                state.consumer = asyncio.create_task(self.trade_consumer(state), name=f"trades-{symbol}")
                LOG.info("%s: bat dau theo doi (%s, bucket %s, last_agg_id %s)", symbol, state.market,
                         scfg["bucket_size"], state.last_agg_id)
        for market in orderflow_config.MARKETS:
            endpoints = cfg["endpoints"][market]
            for kind, key in (("depth", "ws_depth"), ("trades", "ws_trades")):
                streams = {s.streams()[0 if kind == "depth" else 1] for s in self.states.values() if s.market == market}
                conn = self.conns.get((market, kind))
                if conn is None and streams:
                    conn = self.conns[(market, kind)] = StreamConn(self, market, kind, endpoints[key])
                    conn.task = asyncio.create_task(conn.run(), name=f"ws-{market}-{kind}")
                if conn is not None:
                    await conn.update(streams)

    async def drop_symbol(self, symbol: str, reason: str) -> None:
        state = self.states.pop(symbol)
        if state.consumer:
            state.consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.consumer
        state.pending_big.extend(g for g in state.bubbles.flush() if g["qty"] >= state.cfg["big_trade_min_qty"])
        self.flush_state(state)
        self.store.close_gaps(symbol, "depth", now_ms())
        with self.db:
            orderflow_store.OrderflowStore.upsert_status(self.db, symbol, {"connected": 0, "book_synced": 0,
                                                                           "last_error": reason})

    # ------------------------------------------------------------ ket noi

    def conn_for(self, market: str, kind: str) -> StreamConn | None:
        return self.conns.get((market, kind))

    async def on_connected(self, conn: StreamConn) -> None:
        for state in self.states.values():
            if state.market != conn.market:
                continue
            if conn.kind == "depth":
                state.book.reset()          # cho event dau tien roi moi lay snapshot
            else:
                self.store.close_gaps(state.symbol, "conn", now_ms())

    async def on_disconnected(self, conn: StreamConn, reason: str) -> None:
        at = now_ms()
        for state in self.states.values():
            if state.market != conn.market:
                continue
            if conn.kind == "depth":
                self.store.open_gap(state.symbol, "depth", state.last_depth_ts or at, f"{reason}: mat ket noi depth")
                state.depth_gap_open = True
                state.book.reset()
            else:
                # Lenh khop thieu se duoc lay bu qua REST theo agg id; gap 'conn' chi ghi nhan lan noi lai
                self.store.open_gap(state.symbol, "conn", state.last_trade_ts or at, f"{reason}: mat ket noi aggTrade")

    def note_error(self, conn: StreamConn, text: str) -> None:
        for state in self.states.values():
            if state.market == conn.market:
                state.last_error = f"{conn.kind}: {text}"[:300]

    def on_message(self, conn: StreamConn, raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        data = msg.get("data") if isinstance(msg, dict) else None
        if not isinstance(data, dict):
            if isinstance(msg, dict) and msg.get("error"):
                LOG.error("%s %s: Binance tra loi %s", conn.market, conn.kind, msg)
            return
        state = self.states.get(str(data.get("s") or "").upper())
        if state is None or state.market != conn.market:
            return
        event = data.get("e")
        if event == "depthUpdate":
            self.on_depth(state, data)
        elif event == "aggTrade":
            state.queue.put_nowait(data)

    # ------------------------------------------------------------ so lenh

    def on_depth(self, state: SymbolState, ev: dict[str, Any]) -> None:
        state.last_depth_ts = int(ev.get("E") or now_ms())
        result = state.book.on_event(ev)
        if result == "resync":
            self.resync(state, state.book.reason)
        elif not state.book.loaded and (state.snapshot_task is None or state.snapshot_task.done()):
            state.snapshot_task = asyncio.create_task(self.fetch_snapshot(state))

    def resync(self, state: SymbolState, reason: str) -> None:
        LOG.warning("%s: resync so lenh - %s", state.symbol, reason)
        state.count_resync()
        self.store.open_gap(state.symbol, "depth", state.last_depth_ts or now_ms(), f"resync: {reason}"[:200])
        state.depth_gap_open = True
        state.book.reset()
        now = time.monotonic()
        state.resync_times = [t for t in state.resync_times if now - t < 60] + [now]
        delay = min(30.0, 2 ** max(0, len(state.resync_times) - 3)) if len(state.resync_times) > 3 else 0.0
        state.snapshot_task = asyncio.create_task(self.fetch_snapshot(state, delay))

    async def fetch_snapshot(self, state: SymbolState, delay: float = 0.0) -> None:
        await asyncio.sleep(delay + 0.3)      # de buffer co event truoc snapshot
        ep = self.cfg["endpoints"][state.market]
        try:
            await self.limiter.before()
            res = await self.http.get(ep["rest"] + ep["depth_path"],
                                      params={"symbol": state.symbol, "limit": ep["depth_limit"]})
            self.limiter.after(res)
            res.raise_for_status()
            body = res.json()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("%s: lay snapshot loi - %s", state.symbol, exc)
            state.last_error = f"snapshot: {exc}"[:300]
            state.book.reset()
            await asyncio.sleep(5)
            state.snapshot_task = asyncio.create_task(self.fetch_snapshot(state))
            return
        result = state.book.on_snapshot(body["lastUpdateId"], body.get("bids") or [], body.get("asks") or [])
        if result == "resync":
            self.resync(state, state.book.reason)
            return
        LOG.info("%s: da nap snapshot lastUpdateId=%s (%d muc)", state.symbol, body["lastUpdateId"], state.book.levels())

    async def rebuild_agg(self, state: SymbolState) -> None:
        now = now_ms()
        since = now - int(self.cfg["retention"]["heatmap_days"]) * orderflow_store.DAY_MS
        try:
            written = await asyncio.to_thread(self.store.rebuild_agg, state.symbol, state.market,
                                              self.cfg["tech"]["heatmap_agg_levels"], now, since)
            if written:
                LOG.info("%s: gop lai %d cua so snapshot", state.symbol, written)
        except Exception:  # noqa: BLE001
            LOG.exception("%s: gop lai snapshot loi", state.symbol)

    def book_ready(self, state: SymbolState) -> bool:
        conn = self.conn_for(state.market, "depth")
        return bool(conn and conn.connected and state.book.loaded and state.book.synced)

    async def snapshot_loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            mono = time.monotonic()
            for state in list(self.states.values()):
                if mono < state.next_snapshot:
                    continue
                # Lich co dinh (khong troi theo do tre vong lap); tre qua mot chu ky thi bat dau lai tu bay gio
                interval = state.cfg["snapshot_interval_sec"]
                state.next_snapshot = (state.next_snapshot + interval if mono - state.next_snapshot < interval
                                       else mono + interval)
                if not self.book_ready(state):
                    continue
                try:
                    self.take_snapshot(state, mono)
                except Exception as exc:  # noqa: BLE001
                    LOG.exception("%s: ghi snapshot loi", state.symbol)
                    state.last_error = f"snapshot: {exc}"[:300]

    def take_snapshot(self, state: SymbolState, mono: float) -> None:
        bid, ask = state.book.best()
        if bid is None or ask is None:
            return
        if bid >= ask:
            self.resync(state, f"so lenh bi cheo (bid {bid} >= ask {ask})")
            return
        cfg = state.cfg
        mid = (bid + ask) / 2
        if mono >= state.next_prune:
            state.book.prune(mid, cfg["depth_range_pct"] * 1.5)
            state.next_prune = mono + 10
        snap = logic.bucketize(state.book.bids, state.book.asks, mid=mid, range_pct=cfg["depth_range_pct"],
                               bucket_size=cfg["bucket_size"])
        ts = now_ms()
        snap.update(ts=ts, mid=mid, best_bid=bid, best_ask=ask)
        with self.db:
            orderflow_store.OrderflowStore.insert_snapshot(self.db, state.symbol, state.market, snap)
            for agg in state.aggs:
                for row in agg.add(snap):
                    orderflow_store.OrderflowStore.insert_agg(self.db, state.symbol, state.market, row)
        state.book_since_ts = state.book_since_ts or ts
        state.last_snapshot_ts = ts
        if state.depth_gap_open:
            if self.store.close_gaps(state.symbol, "depth", ts):
                LOG.info("%s: so lenh dong bo lai, dong gap", state.symbol)
            state.depth_gap_open = False

    # ------------------------------------------------------------ lenh khop

    async def trade_consumer(self, state: SymbolState) -> None:
        while True:
            data = await state.queue.get()
            agg = int(data["a"])
            if state.last_agg_id is not None:
                if agg <= state.last_agg_id:
                    continue                      # trung lap (noi lai, lay bu)
                if agg > state.last_agg_id + 1:
                    await self.fill_trades(state, state.last_agg_id + 1, agg - 1, int(data["T"]))
            elif state.trades_since_ts is None:
                state.trades_since_ts = int(data["T"])
            self.process_trade(state, agg, int(data["T"]), float(data["p"]), float(data["q"]), bool(data["m"]))

    def process_trade(self, state: SymbolState, agg: int, ts: int, price: float, qty: float, sell: bool) -> None:
        cfg = state.cfg
        state.fp.add(ts, price, qty, sell, cfg["footprint_res_sec"], cfg["bucket_size"])
        for group in state.bubbles.add(agg, ts, price, qty, sell):
            if group["qty"] >= cfg["big_trade_min_qty"]:
                state.pending_big.append(group)
        state.last_agg_id = agg
        state.last_trade_ts = ts
        state.dirty = True

    async def fill_trades(self, state: SymbolState, first: int, last: int, until_ts: int) -> None:
        """Lay bu aggTrade [first, last] qua REST fromId. Qua gioi han trang thi ghi gap va bat dau lai tu day."""
        ep = self.cfg["endpoints"][state.market]
        max_pages = int(self.cfg["tech"]["trade_gap_max_pages"])
        LOG.info("%s: lay bu aggTrade %s..%s (%s lenh)", state.symbol, first, last, last - first + 1)
        if (last - first + 1) > max_pages * 1000:
            self.trade_gap(state, until_ts, f"thieu {last - first + 1} lenh, qua {max_pages} trang REST")
            return
        cursor, pages = first, 0
        while cursor <= last and pages < max_pages:
            try:
                await self.limiter.before()
                res = await self.http.get(ep["rest"] + ep["agg_path"],
                                          params={"symbol": state.symbol, "fromId": cursor, "limit": 1000})
                self.limiter.after(res)
                res.raise_for_status()
                rows = res.json()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("%s: lay bu aggTrade loi - %s", state.symbol, exc)
                self.trade_gap(state, until_ts, f"lay bu loi: {exc}"[:200])
                return
            pages += 1
            if not rows:
                break
            for row in rows:
                agg = int(row["a"])
                if agg < cursor or agg > last:
                    continue
                self.process_trade(state, agg, int(row["T"]), float(row["p"]), float(row["q"]), bool(row["m"]))
                cursor = agg + 1
            if int(rows[-1]["a"]) < cursor - 1:
                break
        if cursor <= last:
            self.trade_gap(state, until_ts, f"REST khong tra du lenh {cursor}..{last}")

    def trade_gap(self, state: SymbolState, until_ts: int, reason: str) -> None:
        start = state.last_trade_ts or until_ts
        LOG.warning("%s: thieu lenh khop %s", state.symbol, reason)
        self.store.add_gap(state.symbol, "trades", start, until_ts, reason)
        state.trades_since_ts = until_ts          # vung lien tuc moi bat dau tu day
        state.last_agg_id = None

    # ------------------------------------------------------------ ghi DB

    async def flush_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            now = now_ms()
            for state in list(self.states.values()):
                horizon = max(now, state.last_trade_ts or 0)
                state.pending_big.extend(g for g in state.bubbles.flush(horizon)
                                         if g["qty"] >= state.cfg["big_trade_min_qty"])
            self.flush_all()

    def flush_all(self) -> None:
        for state in list(self.states.values()):
            self.flush_state(state)

    def flush_state(self, state: SymbolState) -> None:
        for res, rows in state.fp.drain().items():
            state.pending_fp.setdefault(res, []).extend(rows)
        if not (state.pending_fp or state.pending_big or state.dirty):
            return
        try:
            with self.db:
                for res, rows in state.pending_fp.items():
                    orderflow_store.OrderflowStore.add_footprint(self.db, state.symbol, res, rows)
                orderflow_store.OrderflowStore.insert_big_trades(self.db, state.symbol, state.pending_big)
                # last_agg_id ghi cung giao dich voi footprint: restart thi tiep tu dung lenh ke tiep
                orderflow_store.OrderflowStore.upsert_status(self.db, state.symbol, {
                    "last_agg_id": state.last_agg_id, "trades_since_ts": state.trades_since_ts,
                    "market": state.market})
        except Exception as exc:  # noqa: BLE001 - giu lai trong bo nho, lan sau ghi tiep
            LOG.warning("%s: ghi footprint loi, giu lai lan sau - %s", state.symbol, exc)
            state.last_error = f"db: {exc}"[:300]
            return
        state.pending_fp, state.pending_big, state.dirty = {}, [], False

    async def heartbeat_loop(self) -> None:
        while True:
            self.write_status()
            await asyncio.sleep(float(self.cfg["tech"]["heartbeat_sec"]))

    def write_status(self, final: bool = False) -> None:
        try:
            with self.db:
                for state in self.states.values():
                    depth = self.conn_for(state.market, "depth")
                    trades = self.conn_for(state.market, "trades")
                    connected = bool(depth and depth.connected and trades and trades.connected) and not final
                    events = [t for t in (state.last_depth_ts, state.last_trade_ts) if t]
                    orderflow_store.OrderflowStore.upsert_status(self.db, state.symbol, {
                        "connected": int(connected), "last_event_ts": max(events) if events else None,
                        "last_snapshot_ts": state.last_snapshot_ts, "last_agg_id": state.last_agg_id,
                        "book_levels": state.book.levels(), "resyncs_today": state.resyncs_today,
                        "resync_day": state.resync_day, "market": state.market,
                        "trades_since_ts": state.trades_since_ts, "book_since_ts": state.book_since_ts,
                        "book_synced": int(self.book_ready(state) and not final),
                        "last_error": "da dung" if final else (state.last_error or None), "pid": os.getpid()})
        except Exception as exc:  # noqa: BLE001
            LOG.warning("ghi heartbeat loi - %s", exc)

    # ------------------------------------------------------------ don du lieu hang ngay

    async def retention_loop(self) -> None:
        while True:
            hh, mm = (int(x) for x in str(self.cfg["tech"]["retention_utc"]).split(":"))
            now = datetime.now(UTC)
            due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if due <= now:
                due += timedelta(days=1)
            await asyncio.sleep((due - now).total_seconds())
            try:
                out = await asyncio.to_thread(self.store.retention, self.cfg["retention"])
                LOG.info("don du lieu: %s, db %.1f MB", out, self.store.size_bytes() / 1e6)
            except Exception:  # noqa: BLE001
                LOG.exception("don du lieu loi")


def health(config: orderflow_config.OrderflowConfig) -> int:
    """Healthcheck: moi symbol dang bat phai co heartbeat trong stale_sec."""
    cfg = config.get()
    active = {s["symbol"] for s in config.active_symbols()}
    if not active:
        return 0
    store = orderflow_store.OrderflowStore(cfg["paths"]["db"])
    rows = {r["symbol"]: r for r in store.status()}
    limit = now_ms() - int(cfg["tech"]["stale_sec"]) * 1000
    stale = [s for s in active if not rows.get(s) or (rows[s]["updated_at"] or 0) < limit]
    if stale:
        print(f"heartbeat cu: {', '.join(sorted(stale))}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BAMCP order flow collector")
    parser.add_argument("--config", default=os.environ.get("BAMCP_CONFIG") or str(Path(__file__).parent / "config.yaml"))
    parser.add_argument("--health", action="store_true", help="kiem tra heartbeat roi thoat")
    parser.add_argument("--run-seconds", type=float, default=None, help="chay chung nay giay roi dung (chay thu)")
    args = parser.parse_args(argv)
    with contextlib.suppress(ImportError):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env", override=False)
    config = orderflow_config.OrderflowConfig(args.config)
    if args.health:
        return health(config)
    cfg = config.get()
    tech = cfg["tech"]
    setup_logging(cfg["paths"]["logs"], int(tech["log_max_mb"]), int(tech["log_backups"]))
    if not cfg["enabled"]:
        LOG.info("orderflow.enabled = false - van chay de doi bat tren trang admin")
    collector = Collector(config, run_seconds=args.run_seconds)

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, collector.stopping.set)
        await collector.run()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
