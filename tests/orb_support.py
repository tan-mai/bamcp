"""Do nghe dung chung cho test ORB: dong ho gia, thi truong gia, dung service.

Khong goi mang. Thi truong gia giu nen M5 tat dinh; fetch() chi ghi nhan request
va danh dau "da keo den luc nay", load_live() tra du lieu toi moc do - giong file
live ma fetcher that ghi ra.
"""

# TM - #ORB - ORB Enhancement

from __future__ import annotations

import asyncio
import copy
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orb  # noqa: E402
import orb_runtime  # noqa: E402
from orb import M5_MS, M15_MS, H1_MS  # noqa: E402

UTC = timezone.utc
SERVER_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
P = 100_000.0            # gia nen phang
ATR_BAR_HALF = 50.0      # nen phang truoc gio mo: high-low = 100 -> ATR H1 = 100
OR_HALF = 30.0           # nen OR mac dinh: OR size 60 -> OR/ATR 0.6 (khong bi loc)
INSIDE_HALF = 10.0       # nen sau OR nam gon trong OR


def ms(text: str) -> int:
    """'2026-09-29 13:30' (UTC) -> epoch ms."""
    dt = datetime.fromisoformat(text).replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def orb_cfg() -> dict[str, Any]:
    with (ROOT / "config.yaml").open(encoding="utf-8") as fh:
        return copy.deepcopy(yaml.safe_load(fh)["orb"])


def default_sessions() -> dict[str, dict[str, Any]]:
    out = {}
    for row in orb_cfg()["default_sessions"]:
        clean = orb.validate_session(row, existing_ids=list(out))
        out[clean["session_id"]] = clean
    return out


def bar(t: int, o: float, h: float, low: float, c: float, volume: float = 10.0) -> dict[str, Any]:
    return {"open_time": t, "open": o, "high": h, "low": low, "close": c,
            "volume": volume, "close_time": t + M5_MS - 1}


def flat(t: int, half: float, price: float = P) -> dict[str, Any]:
    return bar(t, price, price + half, price - half, price)


class Clock:
    def __init__(self, now: int = 0):
        self.now = now

    def __call__(self) -> int:
        return self.now


class FakeMarket:
    """Nen M5 tat dinh + ban ghi moi request fetch."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.bars: dict[int, dict[str, Any]] = {}
        self.fetched: dict[str, int | None] = {}
        self.requests: list[dict[str, Any]] = []
        self.fail_sources: tuple[str, ...] = ()
        self.history_bars = False     # True = load_history tra ca M5 lich su

    # ---------------------------------------------------------- dung du lieu

    def fill(self, start: int, end: int, half: float, price: float = P) -> None:
        for t in range(start, end, M5_MS):
            self.bars[t] = flat(t, half, price)

    def put(self, t: int, o: float, h: float, low: float, c: float) -> None:
        self.bars[t] = bar(t, o, h, low, c)

    def day(self, open_ms: int, *, or_half: float = OR_HALF, after_half: float = INSIDE_HALF,
            days_before: int = 10, hours_after: int = 12) -> None:
        """Nen phang du cho ATR H1 (200 nen) truoc gio mo, OR, roi nen trong OR."""
        self.fill(open_ms - days_before * 24 * H1_MS, open_ms, ATR_BAR_HALF)
        self.fill(open_ms, open_ms + M15_MS, or_half)
        self.fill(open_ms + M15_MS, open_ms + hours_after * H1_MS, after_half)

    def mark_fetched(self, at: int | None = None) -> None:
        at = self.clock.now if at is None else at
        for tf in ("5m", "15m", "1h"):
            self.fetched[tf] = at

    # ---------------------------------------------------------- giao dien cho service

    def load_live(self, symbol: str, tf: str) -> tuple[list[dict[str, Any]], int | None]:
        at = self.fetched.get(tf)
        if at is None:
            return [], None
        m5 = [self.bars[k] for k in sorted(self.bars) if k < at]
        if tf == "5m":
            return m5, at
        return orb.aggregate(m5, M5_MS, orb_runtime.TF_MS[tf]), at

    def load_history(self, symbol: str, start: int, end: int) -> list[dict[str, Any]]:
        if not self.history_bars:
            return []
        return [self.bars[k] for k in sorted(self.bars) if start <= k < end]

    async def fetch(self, symbol: str, tf: str, limit: int | None, source: str) -> dict[str, Any]:
        now = self.clock.now
        self.requests.append({"tf": tf, "source": source, "at": now, "limit": limit})
        if any(tag in source for tag in self.fail_sources):
            raise RuntimeError(f"gia lap loi mang cho {source}")
        self.fetched[tf] = now
        return {"ok": True}

    def m5_requests(self, source_prefix: str = "") -> list[dict[str, Any]]:
        return [r for r in self.requests
                if r["tf"] == "5m" and r["source"].startswith(source_prefix)]


class Harness:
    """OrbService + OrbScheduler tren thu muc tam, dung dong ho va thi truong gia."""

    def __init__(self, root: Path | None = None, *, cfg: dict[str, Any] | None = None,
                 clock: Clock | None = None, market: FakeMarket | None = None,
                 journal: list[dict[str, Any]] | None = None,
                 bias: dict[str, Any] | None = None):
        if root is None:
            self._tmp = tempfile.TemporaryDirectory()
            root = Path(self._tmp.name)
        self.root = Path(root)
        self.cfg = cfg or orb_cfg()
        self.clock = clock or Clock()
        self.market = market or FakeMarket(self.clock)
        self.journal = journal if journal is not None else []
        self.bias = bias
        self.wakes = 0
        self.log = orb_runtime.EventLog(self.root / "logs", now_fn=self.clock, echo=False)
        self.sessions = orb_runtime.SessionStore(self.root / "sessions.json",
                                                 self.cfg["default_sessions"], self.log,
                                                 now_fn=self.clock)
        self.states = orb_runtime.StateStore(self.root / "state", now_fn=self.clock)
        self.svc = orb_runtime.OrbService(
            cfg=self.cfg, sessions=self.sessions, states=self.states, log=self.log,
            load_live=self.market.load_live, load_history=self.market.load_history,
            get_bias=lambda symbol, day: self.bias,
            journal_rows=self._journal_rows,
            skips_dir=self.root / "journal" / "orb_skips",
            news_file=self.root / "news_days.json", tz=SERVER_TZ, now_fn=self.clock)
        self.sched = orb_runtime.OrbScheduler(self.svc, self.market.fetch)
        self.svc.notify = self._wake
        self.sessions.listeners.append(self._wake)

    def _wake(self) -> None:
        self.wakes += 1
        self.sched.wake()

    def _journal_rows(self, days: list[str]) -> list[dict[str, Any]]:
        return [dict(r) for r in self.journal if r.get("_journal_date") in days]

    def restart(self) -> "Harness":
        """Dung lai service + scheduler tren CUNG thu muc va thi truong (gia lap restart)."""
        return Harness(self.root, cfg=self.cfg, clock=self.clock, market=self.market,
                       journal=self.journal, bias=self.bias)

    def close(self) -> None:
        if getattr(self, "_tmp", None) is not None:
            self._tmp.cleanup()

    # ---------------------------------------------------------- chay

    def run_due(self, now: int) -> list[dict[str, Any]]:
        self.clock.now = now
        return asyncio.run(self.sched.run_due(now))

    def drive(self, start: int, end: int) -> None:
        """Chay scheduler tu start den end, nhay dung cac moc ma no tu hen."""
        now = start
        while now <= end:
            self.run_due(now)
            now = max(now + 1000, self.sched.next_wake(now))

    def session(self, sid: str) -> dict[str, Any]:
        found = self.sessions.get(sid)
        assert found is not None, sid
        return found

    def state(self, sid: str, day: str) -> dict[str, Any] | None:
        return self.states.get(sid, day)
