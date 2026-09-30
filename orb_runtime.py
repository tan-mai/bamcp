"""ORB runtime: session store, state theo ngay, service cho tool MCP, scheduler.

# TM - #ORB - ORB Enhancement

Tach khoi server.py de test duoc ma khong dung HTTP: moi I/O ra ngoai (doc file
kline, goi Binance, doc journal, doc bias) deu duoc TIEM vao duoi dang ham.
Thoi gian cung duoc tiem (now_fn) de test gia lap mot ngay giao dich trong vai
mili giay.

Luong du lieu:
  SessionStore  <- trang admin ghi, scheduler + tool doc (file JSON, ghi atomic)
  StateStore    <- scheduler ghi (phase 1, watch), tool ghi (skip, taken)
  OrbService    <- lop mong cho tool MCP: tinh OR, tin hieu, plan, kiem tra lenh
  OrbScheduler  <- mot vong lap duy nhat, moi vong tu dung lai danh sach job tu
                   session store + state. Khong giu job trong bo nho nen khong
                   bao gio co job trung, va restart la tu dung lai dung nhu cu.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import orb
import orb_rules  # TM - #ORB-RULES - ORB Rule Set
from orb import H1_MS, M5_MS, M15_MS, iso_utc, iso_vn

UTC = timezone.utc
TF_MS = {"5m": M5_MS, "15m": M15_MS, "1h": H1_MS}
# Ten khung theo kieu tai lieu (M5/M15/H1) -> ten khung Binance
TF_ALIASES = {"M5": "5m", "M15": "15m", "H1": "1h", "5M": "5m", "15M": "15m", "1H": "1h",
              "5m": "5m", "15m": "15m", "1h": "1h"}

STALE_M5_MS = 10 * 60_000          # muc 4.2: M5 moi nhat cu hon 10 phut -> stale_m5
PHASE1_RETRY_MS = 30_000           # thieu nen OR -> thu lai sau 30 giay
PHASE1_MAX_ATTEMPTS = 6
PHASE1_DEADLINE_MS = 10 * 60_000   # qua or_end + 10 phut ma van thieu -> bo cuoc
MAX_SLEEP_MS = 60_000              # vong lap scheduler thuc day it nhat moi phut


def now_ms() -> int:
    return int(time.time() * 1000)


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception as exc:
        print(f"CANH BAO: khong doc duoc {path} - {type(exc).__name__}: {exc}", file=sys.stderr)
        return default


def _write(path: Path, payload: Any) -> None:
    """Ghi file tam roi rename: doc giua chung khong bao gio thay file do dang."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ---------------------------------------------------------------- log

class EventLog:
    """Log su kien ORB: moi dong mot JSON trong logs/YYYY-MM-DD.jsonl (ngay UTC)
    va mot dong tren stderr (hien trong `docker compose logs`)."""

    def __init__(self, root: Path, now_fn: Callable[[], int] = now_ms, echo: bool = True):
        self.root = Path(root)
        self.now_fn = now_fn
        self.echo = echo
        self._lock = threading.Lock()

    def __call__(self, event: str, **fields: Any) -> None:
        at = self.now_fn()
        row = {"at_utc": iso_utc(at), "event": event, **fields}
        line = json.dumps(row, ensure_ascii=False, default=str)
        try:
            with self._lock:
                self.root.mkdir(parents=True, exist_ok=True)
                with (self.root / f"{iso_utc(at)[:10]}.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError as exc:
            print(f"CANH BAO: khong ghi duoc log ORB - {exc}", file=sys.stderr)
        if self.echo:
            print(f"BAMCP orb {line}", file=sys.stderr)

    def read(self, day: str, event: str | None = None) -> list[dict[str, Any]]:
        path = self.root / f"{day}.jsonl"
        if not path.exists():
            return []
        rows = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if event is None or row.get("event") == event:
                    rows.append(row)
        return rows


# ---------------------------------------------------------------- session store

class SessionStore:
    """Danh sach phien ORB (BR-12: khong nam trong config, do trang admin quan ly).

    Lan dau chua co file thi gieo tu `orb.default_sessions` trong config. Tu do
    file la nguon su that. Doc lai khi mtime doi, nen sua tay file cung co hieu luc.
    """

    def __init__(self, path: Path, seed: list[dict[str, Any]] | None, log: EventLog,
                 now_fn: Callable[[], int] = now_ms):
        self.path = Path(path)
        self.seed = list(seed or [])
        self.log = log
        self.now_fn = now_fn
        self.listeners: list[Callable[[], None]] = []
        # TM - #ORB-RULES - ORB Rule Set: kiem them khi luu phien (vd min < max OR/ATR,
        # time_exit <= cua so). Moi ham tra {field: loi}; rong = hop le.
        self.validators: list[Callable[[dict[str, Any]], dict[str, str]]] = []
        self._lock = threading.RLock()
        self._doc: dict[str, Any] | None = None
        self._stamp: int | None = None

    def _load(self) -> dict[str, Any]:
        with self._lock:
            stamp = self.path.stat().st_mtime_ns if self.path.exists() else None
            if self._doc is not None and stamp == self._stamp:
                return self._doc
            if stamp is None:
                sessions: list[dict[str, Any]] = []
                for row in self.seed:
                    try:
                        sessions.append(orb.validate_session(
                            row, existing_ids=[s["session_id"] for s in sessions]))
                    except orb.SessionValidationError as exc:
                        print(f"CANH BAO: bo qua phien mac dinh {row!r} - {exc}", file=sys.stderr)
                doc = {"sessions": sessions, "history": [{
                    "at_utc": iso_utc(self.now_fn()), "action": "seed",
                    "session_ids": [s["session_id"] for s in sessions]}]}
                self._persist(doc)
                return self._doc  # type: ignore[return-value]
            raw = _read(self.path, None)
            if not isinstance(raw, dict):
                print(f"CANH BAO: {self.path} khong phai JSON object - coi nhu rong",
                      file=sys.stderr)
                raw = {}
            sessions = []
            for row in raw.get("sessions") or []:
                try:
                    sessions.append(orb.validate_session(
                        row, existing_ids=[s["session_id"] for s in sessions]))
                except (orb.SessionValidationError, AttributeError, TypeError) as exc:
                    print(f"CANH BAO: phien hong trong {self.path}: {row!r} - {exc}",
                          file=sys.stderr)
            self._doc = {"sessions": sessions,
                         "history": list(raw.get("history") or [])[-200:],
                         "updated_at": raw.get("updated_at")}
            self._stamp = stamp
            return self._doc

    def _persist(self, doc: dict[str, Any]) -> None:
        doc["updated_at"] = iso_utc(self.now_fn())
        doc["history"] = list(doc.get("history") or [])[-200:]
        _write(self.path, doc)
        self._doc = doc
        self._stamp = self.path.stat().st_mtime_ns
        for listener in list(self.listeners):
            try:
                listener()
            except Exception as exc:          # listener hong khong duoc chan viec luu
                print(f"CANH BAO: listener session store loi - {exc}", file=sys.stderr)

    # ---------------------------------------------------------- doc

    def all(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        rows = copy.deepcopy(self._load()["sessions"])
        return rows if include_disabled else [s for s in rows if s["enabled"]]

    def get(self, session_id: str) -> dict[str, Any] | None:
        sid = str(session_id or "").strip().lower()
        for row in self._load()["sessions"]:
            if row["session_id"] == sid:
                return copy.deepcopy(row)
        return None

    def ids(self) -> list[str]:
        return [s["session_id"] for s in self._load()["sessions"]]

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return copy.deepcopy(self._load()["history"][-limit:])

    # ---------------------------------------------------------- ghi

    def save(self, payload: dict[str, Any], *, original_id: str | None = None) -> dict[str, Any]:
        """Them moi (original_id=None) hoac sua. Nem SessionValidationError."""
        with self._lock:
            doc = copy.deepcopy(self._load())
            sessions = doc["sessions"]
            original = None
            if original_id:
                original = next((s for s in sessions if s["session_id"] == original_id), None)
                if original is None:
                    raise orb.SessionValidationError(
                        {"session_id": f"khong tim thay phien '{original_id}'"})
            clean = orb.validate_session(
                payload, existing_ids=[s["session_id"] for s in sessions], original=original)
            errors: dict[str, str] = {}
            for check in self.validators:
                errors.update(check(clean) or {})
            if errors:
                raise orb.SessionValidationError(errors)
            if original is None:
                sessions.append(clean)
                action, changes = "create", {k: {"from": None, "to": v} for k, v in clean.items()}
            else:
                changes = {k: {"from": original.get(k), "to": v}
                           for k, v in clean.items() if original.get(k) != v}
                if not changes:
                    return clean
                sessions[sessions.index(original)] = clean
                action = "update"
            doc["history"].append({"at_utc": iso_utc(self.now_fn()), "action": action,
                                   "session_id": clean["session_id"], "changes": changes})
            self._persist(doc)
        self.log("session_change", action=action, session_id=clean["session_id"],
                 changes=changes)
        return clean

    def set_enabled(self, session_id: str, enabled: bool) -> dict[str, Any]:
        current = self.get(session_id)
        if current is None:
            raise ValueError(f"khong tim thay phien '{session_id}'")
        return self.save({**current, "enabled": bool(enabled)},
                         original_id=current["session_id"])

    def delete(self, session_id: str) -> None:
        """Chi xoa khoi danh sach. Journal, state va ket qua backtest cu giu nguyen."""
        with self._lock:
            doc = copy.deepcopy(self._load())
            sid = str(session_id or "").strip().lower()
            kept = [s for s in doc["sessions"] if s["session_id"] != sid]
            if len(kept) == len(doc["sessions"]):
                raise ValueError(f"khong tim thay phien '{session_id}'")
            removed = next(s for s in doc["sessions"] if s["session_id"] == sid)
            doc["sessions"] = kept
            doc["history"].append({"at_utc": iso_utc(self.now_fn()), "action": "delete",
                                   "session_id": sid, "removed": removed})
            self._persist(doc)
        self.log("session_change", action="delete", session_id=sid)


# ---------------------------------------------------------------- state store

def _new_state(session_id: str, or_date: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "or_date": or_date,
        "open_utc": None,
        "or_end_utc": None,
        "window_end_utc": None,
        "phase1": {"status": "pending", "ran_utc": None, "attempts": 0,
                   "retry_at_ms": None, "catch_up": False, "result": None, "error": None},
        "watch": {"active": False, "started_utc": None, "stopped_utc": None,
                  "stop_reason": None, "last_tick_utc": None, "next_tick_ms": None,
                  "ticks": 0, "m5_requests": 0, "last_error": None},
        "state": None,
        "last_signal": None,
        "transitions": [],
        "skip": None,
        "taken": None,
    }


class StateStore:
    """Trang thai cua MOT phien trong MOT ngay: state/<session_id>/<or_date>.json.

    Scheduler (vong lap async) va tool MCP (thread rieng) cung ghi vao day, nen
    moi lan sua deu di qua update(): khoa, doc lai ban moi nhat tu dia, sua, ghi.
    """

    def __init__(self, root: Path, now_fn: Callable[[], int] = now_ms):
        self.root = Path(root)
        self.now_fn = now_fn
        self._lock = threading.RLock()

    def path(self, session_id: str, or_date: str) -> Path:
        return self.root / session_id / f"{or_date}.json"

    def get(self, session_id: str, or_date: str) -> dict[str, Any] | None:
        with self._lock:
            data = _read(self.path(session_id, or_date), None)
        return data if isinstance(data, dict) else None

    def update(self, session_id: str, or_date: str,
               fn: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        with self._lock:
            state = self.get(session_id, or_date) or _new_state(session_id, or_date)
            fn(state)
            state["updated_utc"] = iso_utc(self.now_fn())
            _write(self.path(session_id, or_date), state)
            return copy.deepcopy(state)

    def session_dirs(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def dates(self, session_id: str) -> list[str]:
        folder = self.root / session_id
        if not folder.exists():
            return []
        return sorted(p.stem for p in folder.glob("*.json"))

    # ---------------------------------------------------------- job info

    def _jobs_path(self) -> Path:
        return self.root / "_jobs.json"

    def jobs(self) -> dict[str, Any]:
        with self._lock:
            data = _read(self._jobs_path(), {})
        return data if isinstance(data, dict) else {}

    def job(self, key: str) -> dict[str, Any]:
        return dict(self.jobs().get(key) or {})

    def set_job(self, key: str, **fields: Any) -> None:
        with self._lock:
            data = self.jobs()
            row = dict(data.get(key) or {})
            row.update(fields)
            data[key] = row
            _write(self._jobs_path(), data)


def watch_view(state: dict[str, Any] | None) -> dict[str, Any]:
    w = (state or {}).get("watch") or {}
    return {
        "active": bool(w.get("active")),
        "started_utc": w.get("started_utc"),
        "stopped_utc": w.get("stopped_utc"),
        "stop_reason": w.get("stop_reason"),
        "last_tick_utc": w.get("last_tick_utc"),
        "next_tick_utc": iso_utc(w.get("next_tick_ms")) if w.get("active") else None,
        "ticks": int(w.get("ticks") or 0),
        "m5_requests": int(w.get("m5_requests") or 0),
    }


def _stop_watch(state: dict[str, Any], reason: str, at: int) -> bool:
    """Dung watch neu chua dung. True neu vua dung (de biet ma ghi log)."""
    w = state["watch"]
    if not w.get("active") and w.get("stop_reason"):
        return False
    w.update(active=False, stopped_utc=iso_utc(at), stop_reason=reason, next_tick_ms=None)
    return True


def _set_state(state: dict[str, Any], new: str | None, at: int, **extra: Any) -> bool:
    prev = state.get("state")
    if new == prev:
        return False
    state["state"] = new
    state.setdefault("transitions", []).append(
        {"at_utc": iso_utc(at), "from": prev, "to": new, **extra})
    return True


def _bar_view(bar: dict[str, Any], span_ms: int, avg_volume: float | None = None) -> dict[str, Any]:
    spread = bar["high"] - bar["low"]
    return {
        "time_utc": iso_utc(bar["open_time"]),
        "open_utc": iso_utc(bar["open_time"]),
        "open_vn": iso_vn(bar["open_time"]),
        "close_utc": iso_utc(bar["open_time"] + span_ms),
        "open": bar["open"],
        "high": bar["high"],
        "low": bar["low"],
        "close": bar["close"],
        "volume": bar["volume"],
        "spread": round(spread, 2),
        "close_in_bar_pct": round((bar["close"] - bar["low"]) / spread * 100, 1) if spread else None,
        "volume_vs_avg": round(bar["volume"] / avg_volume, 2) if avg_volume else None,
    }


# ---------------------------------------------------------------- service

class OrbService:
    """Moi phep tinh ma tool MCP can. Khong tu goi mang - chi doc nhung gi scheduler
    va fetcher da keo ve (BR-03: M5 chi duoc pull trong giai doan watch)."""

    def __init__(self, *, cfg: dict[str, Any], sessions: SessionStore, states: StateStore,
                 log: EventLog,
                 load_live: Callable[[str, str], tuple[list[dict[str, Any]], int | None]],
                 load_history: Callable[[str, int, int], list[dict[str, Any]]],
                 get_bias: Callable[[str, str], dict[str, Any] | None],
                 journal_rows: Callable[[list[str]], list[dict[str, Any]]],
                 skips_dir: Path, news_file: Path | None, tz: Any,
                 now_fn: Callable[[], int] = now_ms,
                 rules_fn: Callable[[], dict[str, Any]] | None = None,
                 shared_rules_fn: Callable[[str], dict[str, Any]] | None = None):
        self.cfg = cfg
        self.symbol = str(cfg.get("symbol") or "BTCUSDT").upper()
        self.delay_ms = int((cfg.get("scheduler") or {}).get("candle_close_delay_seconds", 20)) * 1000
        self.sessions = sessions
        self.states = states
        self.log = log
        self.load_live = load_live
        self.load_history = load_history
        self.get_bias = get_bias
        self.journal_rows = journal_rows
        self.skips_dir = Path(skips_dir)
        self.news_file = Path(news_file) if news_file else None
        self.tz = tz
        self.now = now_fn
        self.notify: Callable[[], None] = lambda: None
        # TM - #ORB-RULES - ORB Rule Set: khoi `orb` cua rules.json va rule chung cua
        # cap (chi lay max_margin_per_trade). Doc lai moi lan dung -> sua co hieu luc ngay.
        seed_block = orb_rules.migrate(cfg, self.symbol)[0]
        self.rules_fn = rules_fn or (lambda: seed_block)
        self.shared_rules_fn = shared_rules_fn or (lambda _symbol: {})
        orb.effective_params(cfg, rules=seed_block)   # config sai thi bao ngay luc khoi dong
        sessions.validators.append(self._validate_session_rules)

    # ---------------------------------------------------------- tien ich

    def params(self, session: dict[str, Any] | None = None,
               overrides: dict[str, Any] | None = None,
               rules_override: dict[str, Any] | None = None) -> dict[str, Any]:
        return orb.effective_params(self.cfg, session, overrides, rules=self.rules_block(),
                                    rules_override=rules_override)

    # TM - #ORB-RULES - ORB Rule Set ------------------------------------------

    def rules_block(self) -> dict[str, Any]:
        return orb_rules.normalize_block(self.rules_fn())

    def enabled(self) -> bool:
        """Cong tac orb.enabled (BR-ORB-17)."""
        return bool(self.rules_block()["enabled"])

    @staticmethod
    def session_rule_values(session: dict[str, Any] | None) -> dict[str, Any]:
        """Override rule ORB cua phien (nhom SESSION_FIELDS)."""
        if not session:
            return {}
        flat = orb.normalize_overrides(session.get("overrides"), orb.SESSION_OVERRIDE_KEYS)
        return {k: v for k, v in flat.items() if k in orb.RULE_PARAM_KEYS}

    def rules_for(self, session: dict[str, Any] | None = None, symbol: str | None = None,
                  override: dict[str, Any] | None = None) -> dict[str, Any]:
        """Bo rule ORB da giai cho cap (+ phien) - mot ham duy nhat cho check_trade,
        log_trade, check_orb_signal, get_today_status va backtest."""
        return orb_rules.resolve(self.rules_block(), symbol or self.symbol,
                                 session_values=self.session_rule_values(session),
                                 override=override,
                                 session_id=(session or {}).get("session_id"))

    def costs(self) -> dict[str, float]:
        raw = self.cfg.get("costs") or {}
        return {"taker_fee_pct": float(raw.get("taker_fee_pct") or 0),
                "slippage_pct": float(raw.get("slippage_pct") or 0)}

    def window_minutes(self, session: dict[str, Any]) -> int | None:
        value = session.get("trade_window_minutes") or \
            (self.cfg.get("defaults") or {}).get("trade_window_minutes")
        return int(value) if value else None

    def _validate_session_rules(self, session: dict[str, Any]) -> dict[str, str]:
        errors = orb_rules.consistency(self.rules_for(session)["values"],
                                       self.window_minutes(session))
        return {"overrides": "; ".join(errors)} if errors else {}

    def market_snapshot(self, at: int | None = None) -> dict[str, Any]:
        """ATR H1 va gia gan nhat (nen da dong) - de tinh canh bao rule_infeasible."""
        now = self.now() if at is None else at
        atr_value = None
        try:
            atr_value, _missing = self._atr(now, self.params())
        except Exception as exc:
            print(f"BAMCP orb: khong tinh duoc ATR hien tai - {exc}", file=sys.stderr)
        price = None
        for tf in ("5m", "15m", "1h"):
            try:
                bars, _fetched = self.load_live(self.symbol, tf)
            except Exception:
                continue
            closed = [b for b in bars if b["open_time"] + TF_MS[tf] <= now]
            if closed:
                price = float(closed[-1]["close"])
                break
        return {"symbol": self.symbol, "atr_h1": round(atr_value, 2) if atr_value else None,
                "price": price, "at_utc": iso_utc(now)}

    def day_stats(self, day: date) -> dict[str, Any]:
        """So lenh + PnL rong ORB theo or_date (BR-ORB-09: tu nhat ky)."""
        return orb_rules.day_stats(self._orb_rows(day))

    def _score(self, session: dict[str, Any], day: date, *, symbol: str, side: str,
               entry: float, stop: float, target: float, margin_usd: float,
               trade_type: str) -> dict[str, Any]:
        stats = self.day_stats(day)
        shared = self.shared_rules_fn(symbol) or {}
        return orb_rules.score(
            symbol=symbol, side=side, entry=entry, stop=stop, target=target,
            margin_usd=margin_usd, trade_type=trade_type,
            resolved=self.rules_for(session, symbol),
            max_margin_per_trade=shared.get("max_margin_per_trade"),
            day_trades=stats["trades"], day_pnl=stats["pnl"], or_date=day.isoformat(),
            costs=self.costs())

    def score_trade(self, session_id: str, or_date: str = "", *, side: str, entry: float,
                    stop: float, target: float, margin_usd: float, trade_type: str = "scalp",
                    symbol: str | None = None, at: int | None = None) -> dict[str, Any]:
        """Cham mot lenh ORB: rule ORB (BR-ORB-02) + quy tac phien (orb_checks).
        check_trade, log_trade va check_orb_signal deu di qua day (BR-ORB-10)."""
        checks = self.orb_checks(session_id, or_date, at=at)
        session = self.resolve(session_id, include_disabled=True)[0]
        sym = str(symbol or self.symbol).strip().upper()
        verdict = self._score(session, orb.parse_date(checks["or_date"]), symbol=sym,
                              side=side, entry=entry, stop=stop, target=target,
                              margin_usd=margin_usd, trade_type=trade_type)
        rule_only = list(verdict["rule_violations"])
        if sym != self.symbol:
            rule_only.insert(0, f"ORB chi chay tren {self.symbol}, lenh nay la {sym}")
        verdict["rule_violations"] = rule_only + checks["violations"]
        verdict["orb_rule_violations"] = rule_only
        verdict["would_pass"] = not verdict["rule_violations"]
        verdict["orb_checks"] = checks
        return verdict

    def resolve(self, session_id: str = "", include_disabled: bool = False) -> list[dict[str, Any]]:
        if session_id:
            found = self.sessions.get(session_id)
            if not found:
                raise ValueError(f"khong co phien '{session_id}'. Dang co: {self.sessions.ids()}")
            return [found]
        return self.sessions.all(include_disabled=include_disabled)

    def local_day(self, ts: int) -> str:
        """Ngay theo timezone cua server (dung cho journal va bias)."""
        return datetime.fromtimestamp(ts / 1000, self.tz).strftime("%Y-%m-%d")

    def default_day(self, session: dict[str, Any], as_of: int) -> date:
        """Hom nay theo timezone cua phien - tru khi trade window cua phien hom qua
        con chua het (phien mo gan nua dem)."""
        today = orb.local_date(session, as_of)
        yesterday = today - timedelta(days=1)
        if orb.trade_day_status(session, yesterday)[0]:
            end = orb.session_times(session, yesterday, self.params(session))["window_end"]
            if as_of < end:
                return yesterday
        return today

    def news_days(self) -> list[str]:
        days = set(str(d) for d in ((self.cfg.get("filters") or {}).get("news_days") or []))
        if self.news_file and self.news_file.exists():
            raw = _read(self.news_file, [])
            if isinstance(raw, dict):
                raw = raw.get("dates") or raw.get("days") or []
            for item in raw if isinstance(raw, list) else []:
                days.add(str(item.get("date") if isinstance(item, dict) else item)[:10])
        return sorted(days)

    def _series(self, tf: str, start: int, end: int, as_of: int) -> tuple[list[dict[str, Any]], str]:
        """Nen DA DONG cua khung tf trong [start, end). Uu tien file live; thieu thi
        bu tu M5 lich su (gop len khung lon neu can)."""
        span = TF_MS[tf]
        bars, fetched = self.load_live(self.symbol, tf)
        # Nen chi chac chan da chot neu no dong TRUOC lan ghi file gan nhat: file
        # ghi luc nen dang chay se giu high/low do dang cua nen do.
        cut = as_of if fetched is None else min(as_of, fetched - 1000)
        live = [b for b in bars if start <= b["open_time"] < end and b["open_time"] + span <= cut]
        need = max(0, (min(end, as_of) - start) // span)
        if len(live) >= need:
            return live, "live"
        hist = self.load_history(self.symbol, start, end)
        if tf != "5m":
            hist = orb.aggregate(hist, M5_MS, span)
        hist = [b for b in hist if b["open_time"] + span <= as_of]
        if not hist:
            return live, "live"
        merged = {b["open_time"]: b for b in hist}
        merged.update({b["open_time"]: b for b in live})
        return [merged[k] for k in sorted(merged)], ("live+history" if live else "history")

    def _atr(self, open_ms: int, params: dict[str, Any]) -> tuple[float | None, list[dict[str, str]]]:
        """ATR(period) H1 tren ATR_WINDOW nen H1 da dong TRUOC gio mo."""
        last_open = (open_ms // H1_MS) * H1_MS - H1_MS
        start = last_open - (orb.ATR_WINDOW - 1) * H1_MS
        rows, _src = self._series("1h", start, last_open + H1_MS, open_ms)
        missing = orb.missing_ranges(rows, start, last_open + H1_MS, H1_MS)
        if missing:
            return None, missing
        return orb.atr_wilder(rows, int(params["atr_period"])), []

    # ---------------------------------------------------------- opening range

    def opening_range(self, session: dict[str, Any], day: date, as_of: int,
                      params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or self.params(session)
        t = orb.session_times(session, day, params)
        out: dict[str, Any] = {
            "session_id": session["session_id"],
            "session_name": session["name"],
            "date_local": day.isoformat(),
            "timezone": session["timezone"],
            "open_time_local": session["open_time"],
            "or_timeframe": "M15",
            "status": None,
            "open_utc": iso_utc(t["open"]),
            "open_vn": iso_vn(t["open"]),
            "or_end_utc": iso_utc(t["or_end"]),
            "or_end_vn": iso_vn(t["or_end"]),
            "window_end_utc": iso_utc(t["window_end"]),
            "window_end_vn": iso_vn(t["window_end"]),
            "high": None, "low": None, "mid": None, "size": None, "size_pct": None,
            "atr": None, "atr_period": int(params["atr_period"]),
            "atr_timeframe": params["atr_timeframe"],
            "size_atr_ratio": None, "range_flag": None,
            "volume": None, "volume_vs_avg": None,
            "news_day": day.isoformat() in self.news_days(),
            "filtered": None, "filter_reason": None,
        }
        ok, reason = orb.trade_day_status(session, day)
        if not ok:
            out.update(status="no_session", reason=reason)
            return out
        if as_of < t["open"]:
            out["status"] = "pending"
            return out
        if as_of < t["or_end"]:
            out.update(status="forming",
                       note="Nen M15 dau phien chua dong - chua co High/Low chot.")
            return out

        rows, source = self._series("15m", t["open"], t["or_end"], as_of)
        bar = next((b for b in rows if b["open_time"] == t["open"]), None)
        if bar is None:
            out.update(status="data_missing", source=source,
                       missing=orb.missing_ranges([], t["open"], t["or_end"], M15_MS),
                       note="Chua co nen M15 dau phien. Goi refresh_data hoac doi scheduler.")
            return out

        atr_value, atr_missing = self._atr(t["open"], params)
        metrics = orb.range_metrics(bar["high"], bar["low"], atr_value, params)
        prev, _ = self._series("15m", t["open"] - orb.VOLUME_AVG_BARS * M15_MS, t["open"], as_of)
        avg = orb.average_volume(prev)
        out.update(metrics)
        out.update(source=source, volume=bar["volume"],
                   volume_vs_avg=round(bar["volume"] / avg, 2) if avg else None)
        if atr_value is None:
            out.update(status="data_missing", missing=atr_missing,
                       note=f"Thieu nen H1 de tinh ATR({params['atr_period']}) - "
                            "chua ket luan duoc bo loc range.")
            return out

        reasons = []
        if metrics["range_flag"] != "ok":
            reasons.append(metrics["range_flag"])
        if out["news_day"] and params["skip_news_days"]:
            reasons.append("news_day")
        out.update(status="set", filtered=bool(reasons),
                   filter_reason=",".join(reasons) or None)
        return out

    # ---------------------------------------------------------- journal ORB

    def _orb_rows(self, day: date) -> list[dict[str, Any]]:
        """Lenh ORB co or_date = day, tren moi phien. Doc cac ngay journal quanh do
        vi journal chia theo ngay cua server, con or_date theo ngay cua phien."""
        days = [(day + timedelta(days=d)).isoformat() for d in (-1, 0, 1, 2)]
        return [r for r in self.journal_rows(days)
                if str(r.get("strategy") or "").upper() == "ORB"
                and str(r.get("or_date") or "") == day.isoformat()]

    def limits(self, session: dict[str, Any], day: date,
               params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or self.params(session)
        rows = self._orb_rows(day)
        stats = orb_rules.day_stats(rows)
        mine = sum(1 for r in rows if r.get("session_id") == session["session_id"])
        cap_session = int(params["max_trades"])
        # TM - #ORB-RULES - ORB Rule Set: quota ngay = orb.max_trades_per_day (BR-ORB-05).
        # Chua dat -> coi nhu het quota (fail closed, BR-ORB-04).
        cap_day = params.get("max_trades_per_day")
        stop = params.get("daily_stop_loss")
        stop_hit = stop is not None and stats["pnl"] <= float(stop)
        status = "ok"
        if mine >= cap_session:
            status = "session_limit_reached"
        elif cap_day is None or len(rows) >= int(cap_day):
            status = "day_limit_reached"
        sources = params.get("rule_sources") or {}
        return {"session_trades": mine, "max_trades": cap_session,
                "day_trades": len(rows),
                "max_orb_trades_per_day": int(cap_day) if cap_day is not None else None,
                "max_trades_per_day_source": sources.get("max_trades_per_day"),
                "day_pnl": stats["pnl"], "daily_stop_loss": stop,
                "orb_daily_stop_hit": stop_hit,
                "counted_by": "or_date", "status": status}

    def _taken(self, session: dict[str, Any], day: date, as_of: int,
               state: dict[str, Any] | None) -> dict[str, Any] | None:
        best = None
        for row in self._orb_rows(day):
            if row.get("session_id") != session["session_id"]:
                continue
            try:
                at = int(datetime.fromisoformat(str(row.get("opened_at"))).timestamp() * 1000)
            except (TypeError, ValueError):
                at = 0
            if at <= as_of and (best is None or at < best["at_ms"]):
                best = {"at_ms": at, "at_utc": iso_utc(at), "trade_id": row.get("id"),
                        "journal_date": row.get("_journal_date"), "side": row.get("side")}
        taken = (state or {}).get("taken")
        if best is None and taken and int(taken.get("at_ms") or 0) <= as_of:
            best = taken
        return best

    # ---------------------------------------------------------- tin hieu

    def signal(self, session: dict[str, Any], day: date, as_of: int,
               risk_usd: float | None = None, params: dict[str, Any] | None = None
               ) -> dict[str, Any]:
        params = params or self.params(session)
        sid = session["session_id"]
        t = orb.session_times(session, day, params)
        rng = self.opening_range(session, day, as_of, params)
        state = self.states.get(sid, day.isoformat())
        out: dict[str, Any] = {
            "session_id": sid,
            "session_name": session["name"],
            "date_local": day.isoformat(),
            "as_of_utc": iso_utc(as_of),
            "as_of_vn": iso_vn(as_of),
            "state": None,
            "entry_mode": params["entry_mode"],
            "opening_range": {k: rng.get(k) for k in (
                "status", "high", "low", "mid", "size", "atr", "size_atr_ratio",
                "range_flag", "news_day", "filtered", "filter_reason", "open_utc",
                "open_vn", "or_end_utc", "window_end_utc", "window_end_vn")},
            "or": {"high": rng.get("high"), "low": rng.get("low")},
            "direction": None,
            "variant": None,
            "trigger_candle": None,
            "plan": None,
            # pass | ly do chan; None = chua xet toi (vd chua co tin hieu thi chua xet bias)
            "filters": {"range": None, "bias": None, "news": None, "daily_limit": None,
                        "rules": None},
            "bias": None,
            "limits": None,
            "window_ends_utc": iso_utc(t["window_end"]),
            "watch": watch_view(state),
            "data_status": "ok",
            "warnings": [],
        }
        if risk_usd not in (None, 0, ""):
            out["warnings"].append("risk_usd_ignored: size ORB = orb.margin_usd x orb.leverage "
                                   "(BR-ORB-15), risk_usd khong con anh huong qty")
        # TM - #ORB-RULES - ORB Rule Set: cong tac orb.enabled (BR-ORB-17)
        if not self.enabled():
            out.update(data_status="orb_disabled", blocked_by=["orb_disabled"],
                       note="ORB dang tat (orb.enabled = false) - bat lai o trang /admin/orb.")
            return out
        status = rng["status"]
        if status in ("set", "data_missing"):
            flag = rng.get("range_flag")
            out["filters"]["range"] = "pass" if flag == "ok" else (flag or "unknown")
            out["filters"]["news"] = ("off" if not params["skip_news_days"]
                                      else "news_day" if rng["news_day"] else "pass")
        if status == "no_session":
            nxt = orb.next_opens(session, as_of, 1)
            out.update(data_status="no_session", reason=rng.get("reason"),
                       next_open_utc=iso_utc(nxt[0]) if nxt else None,
                       next_open_vn=iso_vn(nxt[0]) if nxt else None)
            return out
        if status == "pending":
            out["state"] = "WAITING_OPEN"
            return out
        if status == "forming":
            out["state"] = "FORMING"
            return out

        taken = self._taken(session, day, as_of, state)
        skip = (state or {}).get("skip")
        if skip and int(skip.get("at_ms") or 0) > as_of:
            skip = None
        out["limits"] = self.limits(session, day, params)
        out["filters"]["daily_limit"] = ("pass" if out["limits"]["status"] == "ok"
                                         else out["limits"]["status"])

        def finish(name: str) -> dict[str, Any]:
            out["state"] = name
            if taken:
                out["taken"] = {k: v for k, v in taken.items() if k != "at_ms"}
            if skip:
                out["skip"] = {"at_utc": skip.get("at_utc"), "reason": skip.get("reason")}
            return out

        if status == "data_missing":
            out.update(data_status="data_missing", missing=rng.get("missing"),
                       note=rng.get("note"))
            if taken:
                return finish("TAKEN")
            if skip:
                return finish("SKIPPED")
            return out
        if taken:
            return finish("TAKEN")
        if skip:
            return finish("SKIPPED")
        if rng["filtered"]:
            return finish("FILTERED")

        # --- quet nen M5 sau khi OR chot (chi doc file, khong pull)
        cut = min(as_of, t["window_end"])
        bars, source = self._series("5m", t["or_end"], t["window_end"], cut)
        prefix: list[dict[str, Any]] = []
        expect = t["or_end"]
        for bar in bars:
            if bar["open_time"] != expect:
                break
            prefix.append(bar)
            expect += M5_MS
        grace = self.delay_ms + 40_000
        check_end = min(t["window_end"], ((as_of - grace) // M5_MS) * M5_MS)
        missing = orb.missing_ranges(prefix, t["or_end"], check_end, M5_MS) \
            if check_end > t["or_end"] else []
        if missing:
            out.update(data_status="data_missing", missing=missing,
                       note="Thieu nen M5 trong giai doan theo doi - state chi tinh "
                            "den truoc khoang thieu, khong dua ra plan.")
        out["m5_bars"] = len(prefix)
        out["m5_source"] = source

        events = orb.scan_signals(float(rng["high"]), float(rng["low"]), prefix, params)
        for ev in events:
            if ev.get("warning"):
                out["warnings"].append(
                    f"{ev['warning']}: nen M5 {iso_utc(prefix[ev['index']]['open_time'])} "
                    "cham ca hai phia OR - bo qua nen nay")
        signals = [ev for ev in events if ev.get("state")]
        last = signals[-1] if signals else None
        out["events"] = [{"state": ev["state"], "bar_utc": iso_utc(prefix[ev["index"]]["open_time"]),
                          "direction": ev["direction"], "entry_ready": ev["entry_ready"],
                          "variant": ev["variant"]} for ev in signals[-10:]]

        if watch_view(state)["active"]:
            last_close = (prefix[-1]["open_time"] + M5_MS) if prefix else t["or_end"]
            if self.now() - last_close > STALE_M5_MS:
                out["warnings"].append(
                    f"stale_m5: nen M5 moi nhat dong luc {iso_utc(last_close)}, "
                    "cu hon 10 phut trong khi watch dang chay")

        if last is not None:
            trigger = prefix[last["trigger_index"]]
            before = prefix[max(0, last["trigger_index"] - orb.VOLUME_AVG_BARS):last["trigger_index"]]
            out["trigger_candle"] = _bar_view(trigger, M5_MS, orb.average_volume(before))
            out["direction"] = last["direction"]
            out["variant"] = last["variant"]
        if prefix:
            out["last_close"] = prefix[-1]["close"]

        if as_of >= t["window_end"]:
            out["last_signal_state"] = last["state"] if last else "RANGE_SET"
            return finish("EXPIRED")
        if last is None:
            return finish("RANGE_SET")

        out["state"] = last["state"]
        if not orb.actionable(last, params):
            if last["state"].startswith("FAILED_"):
                out["note"] = ("Failed breakout. allow_reversal = false nen khong co plan "
                               "vao lenh nguoc chieu.")
            else:
                out["note"] = "Da pha OR, dang cho nen retest canh OR roi dong cua cung huong."
            return out

        bias = self.get_bias(self.symbol, self.local_day(as_of))
        bias_status = orb.bias_check(last["direction"], bias, bool(params["use_bias_filter"]))
        out["filters"]["bias"] = "pass" if bias_status == "ok" else bias_status
        out["bias"] = {"status": bias_status,
                       "direction": (bias or {}).get("direction"),
                       "date": (bias or {}).get("date")}
        blocked: list[str] = []
        if bias_status == "against":
            blocked.append("bias_against")
            out["warnings"].append(
                f"bias_against: bias {bias.get('direction')} nguoc huong {last['direction']}")
        elif bias_status in ("neutral", "missing"):
            out["warnings"].append(f"bias_{bias_status}: khong co bias xac nhan huong lenh")
        if out["limits"]["status"] != "ok":
            blocked.append(out["limits"]["status"])
        if missing:
            blocked.append("data_missing")

        trigger = prefix[last["trigger_index"]]
        plan, plan_warnings = orb.build_plan(
            direction=last["direction"], entry=float(last["entry_price"]),
            or_high=float(rng["high"]), or_low=float(rng["low"]), params=params,
            trigger_close_ms=trigger["open_time"] + M5_MS, risk_usd=risk_usd,
            variant=last["variant"], reversal_extreme=last.get("extreme"))
        out["warnings"].extend(plan_warnings)
        if plan and prefix:
            sign = 1 if plan["direction"] == "long" else -1
            plan["move_from_entry_r"] = round(
                sign * (prefix[-1]["close"] - plan["entry"]) / plan["r_distance"], 2)
        if plan:
            # TM - #ORB-RULES - ORB Rule Set: cham plan bang dung ham cua check_trade
            # (BR-ORB-10). rule_check.would_pass = ket luan cua check_trade voi dung bo so.
            margin = plan.get("margin")
            verdict = self.score_trade(
                sid, day.isoformat(), side=plan["direction"], entry=plan["entry"],
                stop=plan["sl"], target=plan["tp"],
                margin_usd=float(margin if margin is not None else params.get("margin_usd") or 0),
                at=as_of)
            plan["rule_check"] = {
                "would_pass": verdict["would_pass"],
                "rule_violations": verdict["rule_violations"],
                "rule_set": "orb",
                "limits_applied": verdict["limits_applied"],
            }
            rules_fail = bool(verdict["orb_rule_violations"])
            out["filters"]["rules"] = "fail" if rules_fail else "pass"
            if rules_fail:
                blocked.append("rules")
            if plan.get("qty") is None:
                blocked.append("no_sizing")
        if blocked:
            out["blocked_by"] = blocked
            if "rules" in blocked and plan:
                # AC-10: van tra plan de thay rule_check, danh dau khong vao duoc
                plan["blocked_by"] = list(blocked)
                out["plan"] = plan
        else:
            out["plan"] = plan
        return out

    # ---------------------------------------------------------- hanh dong

    def skip(self, session_id: str, reason: str) -> dict[str, Any]:
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("reason la bat buoc - ghi ro vi sao bo phien")
        session = self.resolve(session_id, include_disabled=True)[0]
        now = self.now()
        day = self.default_day(session, now)
        current = self.signal(session, day, now)
        base = {"session_id": session["session_id"], "or_date": day.isoformat()}
        if current["data_status"] == "no_session":
            return {**base, "state": None, "skipped": False, "watch": current["watch"],
                    "message": f"Hom nay khong co phien: {current.get('reason')}"}
        stopped = current["watch"]["stop_reason"] is not None
        if current["state"] in ("TAKEN", "SKIPPED", "EXPIRED", "FILTERED") or stopped:
            return {**base, "state": current["state"], "skipped": False,
                    "watch": current["watch"],
                    "message": "Phien da dung tu truoc - giu nguyen trang thai, khong ghi them."}

        def apply(st: dict[str, Any]) -> None:
            st["skip"] = {"at_ms": now, "at_utc": iso_utc(now), "reason": reason}
            _stop_watch(st, "skipped", now)
            _set_state(st, "SKIPPED", now, reason=reason)

        state = self.states.update(session["session_id"], day.isoformat(), apply)
        journal_day = self.local_day(now)
        path = self.skips_dir / f"{journal_day}.json"
        rows = _read(path, [])
        rows = rows if isinstance(rows, list) else []
        row = {
            "id": len(rows) + 1,
            "strategy": "ORB",
            "variant": "skipped",
            "symbol": self.symbol,
            "session_id": session["session_id"],
            "or_date": day.isoformat(),
            "reason": reason,
            "state_before": current["state"],
            "or_high": current["opening_range"]["high"],
            "or_low": current["opening_range"]["low"],
            "at": datetime.fromtimestamp(now / 1000, self.tz).isoformat(timespec="seconds"),
            "at_utc": iso_utc(now),
        }
        rows.append(row)
        _write(path, rows)
        self.log("state_change", session_id=session["session_id"], or_date=day.isoformat(),
                 frm=current["state"], to="SKIPPED", reason=reason)
        self.log("watch", action="stop", session_id=session["session_id"],
                 or_date=day.isoformat(), stop_reason="skipped")
        self.notify()
        return {**base, "state": "SKIPPED", "skipped": True, "watch": watch_view(state),
                "journal": {"file": str(path), **row}}

    def mark_taken(self, session_id: str, or_date: str, trade: dict[str, Any]) -> dict[str, Any]:
        now = self.now()

        def apply(st: dict[str, Any]) -> None:
            if not st.get("taken"):
                st["taken"] = {"at_ms": now, "at_utc": iso_utc(now),
                               "trade_id": trade.get("id"), "side": trade.get("side"),
                               "journal_date": trade.get("_journal_date")}
            _stop_watch(st, "taken", now)
            _set_state(st, "TAKEN", now, trade_id=trade.get("id"))

        state = self.states.update(session_id, or_date, apply)
        self.log("state_change", session_id=session_id, or_date=or_date, to="TAKEN",
                 trade_id=trade.get("id"))
        self.log("watch", action="stop", session_id=session_id, or_date=or_date,
                 stop_reason="taken")
        self.notify()
        return watch_view(state)

    def orb_checks(self, session_id: str, or_date: str = "",
                   at: int | None = None) -> dict[str, Any]:
        """Quy tac ORB cho check_trade / log_trade (BR-11)."""
        if not session_id:
            raise ValueError("strategy = ORB bat buoc co session_id")
        session = self.resolve(session_id, include_disabled=True)[0]
        now = self.now() if at is None else at
        day = orb.parse_date(or_date) if or_date else self.default_day(session, now)
        params = self.params(session)
        t = orb.session_times(session, day, params)
        rng = self.opening_range(session, day, now, params)
        state = self.states.get(session["session_id"], day.isoformat()) or {}
        limits = self.limits(session, day, params)
        violations: list[str] = []
        if not session["enabled"]:
            violations.append(f"phien {session['session_id']} dang tat")
        in_window = t["or_end"] <= now <= t["window_end"]
        if rng["status"] == "no_session":
            violations.append(f"khong phai ngay giao dich cua phien: {rng.get('reason')}")
            in_window = False
        elif now < t["or_end"]:
            violations.append(f"ngoai trade window: OR chua chot (chot luc {iso_utc(t['or_end'])}"
                              f" / {iso_vn(t['or_end'])})")
        elif now > t["window_end"]:
            violations.append(f"ngoai trade window: da het luc {iso_utc(t['window_end'])}"
                              f" / {iso_vn(t['window_end'])}")
        if rng["status"] == "data_missing":
            violations.append("chua xac dinh duoc OR/ATR (thieu du lieu) - khong kiem tra "
                              "duoc bo loc range")
        if rng.get("filtered"):
            violations.append(f"range bi loc: {rng['filter_reason']} "
                              f"(OR/ATR = {rng.get('size_atr_ratio')})")
        if state.get("skip"):
            violations.append(f"phien da skip luc {state['skip'].get('at_utc')}: "
                              f"{state['skip'].get('reason')}")
        if limits["session_trades"] >= limits["max_trades"]:
            violations.append(f"vuot max_trades cua phien ({limits['session_trades']}/"
                              f"{limits['max_trades']})")
        # TM - #ORB-RULES - ORB Rule Set: quota ngay va daily stop ORB nam trong rule
        # ORB (score_trade) - khong lap lai o day.
        return {
            "session_id": session["session_id"],
            "or_date": day.isoformat(),
            "orb_enabled": self.enabled(),
            "in_window": in_window,
            "window_utc": {"from": iso_utc(t["or_end"]), "to": iso_utc(t["window_end"])},
            "window_vn": {"from": iso_vn(t["or_end"]), "to": iso_vn(t["window_end"])},
            "opening_range": {k: rng.get(k) for k in (
                "status", "high", "low", "range_flag", "size_atr_ratio", "filtered",
                "filter_reason")},
            "skipped": bool(state.get("skip")),
            "limits": limits,
            "violations": violations,
        }

    # ---------------------------------------------------------- liet ke

    def job_status(self, session: dict[str, Any]) -> dict[str, Any]:
        job = self.states.job(session["session_id"])
        if not self.enabled():
            status = "orb_disabled"          # TM - #ORB-RULES - ORB Rule Set
        elif not session["enabled"]:
            status = "paused"
        elif job.get("status") == "error":
            status = "error"
        else:
            status = "scheduled"
        return {"job_status": status, "last_run_utc": job.get("last_run_utc"),
                "last_error": job.get("last_error") if status == "error" else None}

    def list_sessions(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        now = self.now()
        rows = []
        for session in self.sessions.all(include_disabled=include_disabled):
            params = self.params(session)
            day = self.default_day(session, now)
            state = self.states.get(session["session_id"], day.isoformat())
            opens = orb.next_opens(session, now, 1)
            rows.append({
                "session_id": session["session_id"],
                "name": session["name"],
                "timezone": session["timezone"],
                "open_time": session["open_time"],
                "trade_days": session["trade_days"],
                "holiday_calendar": session["holiday_calendar"],
                "trade_window_minutes": params["trade_window_minutes"],
                "max_trades": params["max_trades"],
                "overrides": session["overrides"],
                "enabled": session["enabled"],
                "next_open_utc": iso_utc(opens[0]) if opens else None,
                "next_open_vn": iso_vn(opens[0]) if opens else None,
                **self.job_status(session),
                "current": {"or_date": day.isoformat(),
                            "state": (state or {}).get("state"),
                            "phase1": ((state or {}).get("phase1") or {}).get("status")},
                "watch": watch_view(state),
            })
        return rows


# ---------------------------------------------------------------- scheduler

def next_tick_after(ts: int, delay_ms: int) -> int:
    """Moc tick ke tiep: moi 5 phut, tre delay_ms sau luc nen M5 dong."""
    return ((ts - delay_ms) // M5_MS + 1) * M5_MS + delay_ms


class OrbScheduler:
    """Hai giai doan cho moi phien (muc 4.10), dung lai tu dau moi vong lap.

    Khong giu danh sach job trong bo nho: moi vong, jobs() suy ra viec can lam tu
    session store (dang bat, gio mo theo timezone cua phien) va state tren dia.
    Vi vay:
      - sua/tat/xoa phien co hieu luc o vong ke tiep (admin goi wake() de chay ngay),
      - restart service tu dung lai dung cac job con hieu luc,
      - khong the co job trung cho cung session + ngay.
    """

    def __init__(self, service: OrbService,
                 fetch: Callable[[str, str, int | None, str], Awaitable[dict[str, Any]]],
                 history_update: Callable[[], Awaitable[Any]] | None = None,
                 history_ready: Callable[[], bool] | None = None):
        self.svc = service
        self.fetch = fetch
        self.history_update = history_update
        self.history_ready = history_ready or (lambda: False)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None
        self._history_task: asyncio.Task | None = None

    @property
    def log(self) -> EventLog:
        return self.svc.log

    def wake(self) -> None:
        """Goi tu bat ky thread nao (tool MCP chay tren worker thread)."""
        loop, event = self._loop, self._event
        if loop is None or event is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass

    # ---------------------------------------------------------- lap lich

    def jobs(self, now: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.svc.enabled():
            return out                       # TM - #ORB-RULES - ORB Rule Set: orb.enabled = false
        for session in self.svc.sessions.all(include_disabled=False):
            sid = session["session_id"]
            try:
                params = self.svc.params(session)
                today = orb.local_date(session, now)
                for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
                    if not orb.trade_day_status(session, day)[0]:
                        continue
                    t = orb.session_times(session, day, params)
                    # ngay truoc da het cua so: khong catch-up phase 1 (chi dong watch con treo)
                    past = day < today and t["window_end"] <= now
                    key = day.isoformat()
                    state = self.svc.states.get(sid, key)
                    if past and not state:
                        continue
                    if state and state.get("open_utc") and state["open_utc"] != iso_utc(t["open"]):
                        state = self._reset(session, key, state, now)
                    phase1 = (state or {}).get("phase1") or {}
                    if phase1.get("status", "pending") == "pending":
                        if past:
                            continue
                        at = phase1.get("retry_at_ms") or (t["or_end"] + self.svc.delay_ms)
                        out.append({"kind": "phase1", "session": session, "day": day, "at": at})
                    elif (state.get("watch") or {}).get("active"):
                        at = state["watch"].get("next_tick_ms") or now
                        out.append({"kind": "watch", "session": session, "day": day, "at": at})
            except Exception as exc:
                self._failed(sid, "plan", exc, now)
        return out

    def _reset(self, session: dict[str, Any], key: str, state: dict[str, Any],
               now: int) -> dict[str, Any]:
        """Admin doi gio mo sau khi phase 1 da chay: bo ket qua cu, chay lai theo gio moi."""
        sid = session["session_id"]

        def apply(st: dict[str, Any]) -> None:
            _stop_watch(st, "session_changed", now)
            snapshot = {k: v for k, v in st.items() if k != "superseded"}
            fresh = _new_state(sid, key)
            fresh["superseded"] = (st.get("superseded") or [])[-4:] + [snapshot]
            fresh["skip"], fresh["taken"] = st.get("skip"), st.get("taken")
            st.clear()
            st.update(fresh)

        self.log("session_change", action="reschedule", session_id=sid, or_date=key,
                 old_open_utc=state.get("open_utc"))
        return self.svc.states.update(sid, key, apply)

    def _stop_orphans(self, now: int) -> None:
        """Phien bi tat hoac xoa ma watch con chay -> go ngay. Tat ca ORB
        (orb.enabled = false) thi go moi watch."""
        orb_off = not self.svc.enabled()     # TM - #ORB-RULES - ORB Rule Set
        enabled = set() if orb_off else {
            s["session_id"] for s in self.svc.sessions.all(include_disabled=False)}
        known = set(self.svc.sessions.ids())
        for sid in self.svc.states.session_dirs():
            if sid in enabled:
                continue
            for key in self.svc.states.dates(sid)[-3:]:
                state = self.svc.states.get(sid, key)
                if not state or not (state.get("watch") or {}).get("active"):
                    continue
                reason = ("orb_disabled" if orb_off
                          else "disabled" if sid in known else "deleted")
                self.svc.states.update(sid, key, lambda st: _stop_watch(st, reason, now))
                self.log("watch", action="stop", session_id=sid, or_date=key, stop_reason=reason)

    def next_wake(self, now: int) -> int:
        future = [j["at"] for j in self.jobs(now) if j["at"] > now]
        return min(future + [now + MAX_SLEEP_MS])

    async def run_due(self, now: int | None = None) -> list[dict[str, Any]]:
        now = self.svc.now() if now is None else now
        try:
            self._stop_orphans(now)
        except Exception as exc:
            print(f"BAMCP orb: loi khi go watch mo coi - {exc}", file=sys.stderr)
        due = [j for j in self.jobs(now) if j["at"] <= now]
        if due:
            await asyncio.gather(*[self._run(job, now) for job in due])
        self._maybe_history(now)
        return due

    async def _run(self, job: dict[str, Any], now: int) -> None:
        sid = job["session"]["session_id"]
        try:
            if job["kind"] == "phase1":
                ok = await self.phase1(job["session"], job["day"], now)
            else:
                ok = await self.watch_tick(job["session"], job["day"], now)
            fields: dict[str, Any] = {"last_run_utc": iso_utc(now), "last_action": job["kind"]}
            if ok:
                fields.update(status="ok", last_error=None)
            self.svc.states.set_job(sid, **fields)
        except Exception as exc:
            # BR-16: loi o mot phien khong duoc lan sang phien khac
            self._failed(sid, job["kind"], exc, now)

    def _failed(self, sid: str, action: str, exc: BaseException | str, now: int) -> None:
        text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
        try:
            self.svc.states.set_job(sid, status="error", last_error=text,
                                    last_run_utc=iso_utc(now), last_action=action)
        except Exception:
            pass
        self.log("job_error", session_id=sid, action=action, error=text)

    async def run_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()
        self.log("scheduler", action="start",
                 sessions=[s["session_id"] for s in self.svc.sessions.all(False)])
        while True:
            try:
                await self.run_due()
                now = self.svc.now()
                sleep_ms = max(500, min(MAX_SLEEP_MS, self.next_wake(now) - now))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"BAMCP orb scheduler loi: {type(exc).__name__}: {exc}", file=sys.stderr)
                sleep_ms = 5_000
            try:
                await asyncio.wait_for(self._event.wait(), timeout=sleep_ms / 1000)
            except asyncio.TimeoutError:
                pass
            self._event.clear()

    # ---------------------------------------------------------- giai doan 1

    async def phase1(self, session: dict[str, Any], day: date, now: int) -> bool:
        """Chot OR. Tra ve False khi ket thuc voi loi (job_status = error)."""
        sid, key = session["session_id"], day.isoformat()
        params = self.svc.params(session)
        t = orb.session_times(session, day, params)
        catch_up = now >= t["window_end"]
        fetch_errors = []
        for tf in ("15m", "1h"):
            try:
                result = await self.fetch(self.svc.symbol, tf, None, f"orb_phase1:{sid}")
                if not result.get("ok"):
                    fetch_errors.append(f"{tf}: {result.get('error')}")
            except Exception as exc:
                fetch_errors.append(f"{tf}: {type(exc).__name__}: {exc}")
        rng = self.svc.opening_range(session, day, now, params)
        state = self.svc.states.get(sid, key) or {}
        attempts = int(((state.get("phase1") or {}).get("attempts") or 0)) + 1
        status = rng["status"]
        self.log("phase1", session_id=sid, or_date=key, status=status, attempts=attempts,
                 catch_up=catch_up, fetch_errors=fetch_errors or None)

        if status in ("pending", "forming"):
            self.svc.states.update(sid, key, lambda st: st["phase1"].update(
                attempts=attempts, retry_at_ms=t["or_end"] + self.svc.delay_ms))
            return True
        if status == "no_session":
            self.svc.states.update(sid, key, lambda st: st["phase1"].update(
                status="no_session", ran_utc=iso_utc(now), attempts=attempts))
            return True
        if status == "data_missing":
            give_up = (catch_up or attempts >= PHASE1_MAX_ATTEMPTS
                       or now + PHASE1_RETRY_MS > t["or_end"] + PHASE1_DEADLINE_MS)
            error = f"data_missing: {rng.get('missing')}"
            if fetch_errors:
                error += f"; fetch: {fetch_errors}"

            def fail(st: dict[str, Any]) -> None:
                st.update(open_utc=iso_utc(t["open"]), or_end_utc=iso_utc(t["or_end"]),
                          window_end_utc=iso_utc(t["window_end"]))
                st["phase1"].update(attempts=attempts, error=error, ran_utc=iso_utc(now))
                if give_up:
                    st["phase1"].update(status="error", retry_at_ms=None)
                    _stop_watch(st, "data_missing", now)
                else:
                    st["phase1"]["retry_at_ms"] = now + PHASE1_RETRY_MS

            self.svc.states.update(sid, key, fail)
            if give_up:
                self._failed(sid, "phase1", error, now)
            return False if give_up else True

        # status == "set"
        self.log("or", session_id=sid, or_date=key, high=rng["high"], low=rng["low"],
                 size=rng["size"], atr=rng["atr"], size_atr_ratio=rng["size_atr_ratio"],
                 range_flag=rng["range_flag"], news_day=rng["news_day"],
                 filtered=rng["filtered"], filter_reason=rng["filter_reason"],
                 source=rng.get("source"))
        taken = self.svc._taken(session, day, now, state)
        compact = {k: rng.get(k) for k in ("high", "low", "mid", "size", "atr",
                                           "size_atr_ratio", "range_flag", "news_day",
                                           "filtered", "filter_reason", "source")}
        started = {"flag": False, "reason": None}

        def apply(st: dict[str, Any]) -> None:
            st.update(open_utc=iso_utc(t["open"]), or_end_utc=iso_utc(t["or_end"]),
                      window_end_utc=iso_utc(t["window_end"]))
            st["phase1"].update(status="done", ran_utc=iso_utc(now), attempts=attempts,
                                retry_at_ms=None, catch_up=catch_up, result=compact,
                                error=None)
            w = st["watch"]
            if w.get("active") or w.get("stop_reason"):
                return                       # da skip/taken tu truoc
            if rng["filtered"]:
                _stop_watch(st, "filtered", now)
                _set_state(st, "FILTERED", now, reason=rng["filter_reason"])
                started["reason"] = "filtered"
            elif taken:
                _stop_watch(st, "taken", now)
                _set_state(st, "TAKEN", now)
                started["reason"] = "taken"
            elif st.get("skip"):
                _stop_watch(st, "skipped", now)
                _set_state(st, "SKIPPED", now)
                started["reason"] = "skipped"
            elif catch_up:
                # Lo ca trade window (service tat): khong pull M5 bu - BR-03
                _stop_watch(st, "window_expired", now)
                _set_state(st, "EXPIRED", now, catch_up=True)
                started["reason"] = "window_expired"
            else:
                first = t["or_end"] + M5_MS + self.svc.delay_ms
                w.update(active=True, started_utc=iso_utc(now), stopped_utc=None,
                         stop_reason=None, next_tick_ms=max(first, now))
                _set_state(st, "RANGE_SET", now)
                started["flag"] = True

        self.svc.states.update(sid, key, apply)
        if started["flag"]:
            self.log("watch", action="start", session_id=sid, or_date=key,
                     window_end_utc=iso_utc(t["window_end"]))
        elif started["reason"]:
            self.log("watch", action="not_started", session_id=sid, or_date=key,
                     stop_reason=started["reason"])
        return not fetch_errors

    # ---------------------------------------------------------- giai doan 2

    async def watch_tick(self, session: dict[str, Any], day: date, now: int) -> bool:
        sid, key = session["session_id"], day.isoformat()
        state = self.svc.states.get(sid, key)
        if not state or not (state.get("watch") or {}).get("active"):
            return True
        params = self.svc.params(session)
        t = orb.session_times(session, day, params)
        if now > t["window_end"] + self.svc.delay_ms + M5_MS:
            # service vua bat lai khi cua so da het: dong watch, khong keo M5 ngoai cua so
            def expire(st: dict[str, Any]) -> None:
                if st["watch"].get("active"):
                    _stop_watch(st, "window_expired", now)
                    _set_state(st, "EXPIRED", now, catch_up=True)

            self.svc.states.update(sid, key, expire)
            self.log("watch", action="stop", session_id=sid, or_date=key,
                     stop_reason="window_expired", catch_up=True)
            return True
        span_bars = max(0, (min(now, t["window_end"]) - t["or_end"]) // M5_MS)
        limit = int(min(1000, span_bars + orb.VOLUME_AVG_BARS + 5))
        error = None
        try:
            result = await self.fetch(self.svc.symbol, "5m", limit, f"orb_watch:{sid}")
            if not result.get("ok"):
                error = str(result.get("error"))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        sig = self.svc.signal(session, day, now, params=params)
        change = {"from": None, "to": None}

        def apply(st: dict[str, Any]) -> None:
            w = st["watch"]
            if not w.get("active"):
                return                        # vua bi taken/skip o thread khac
            w["ticks"] = int(w.get("ticks") or 0) + 1
            w["m5_requests"] = int(w.get("m5_requests") or 0) + 1
            w["last_tick_utc"] = iso_utc(now)
            w["last_error"] = error
            new = sig["state"]
            prev = st.get("state")
            if new and _set_state(st, new, now, bar_utc=(sig.get("trigger_candle") or {}).get("open_utc")):
                change.update({"from": prev, "to": new})
            st["last_signal"] = {k: sig.get(k) for k in ("state", "direction", "variant",
                                                           "data_status", "last_close")}
            if new in ("TAKEN", "SKIPPED"):
                _stop_watch(st, new.lower(), now)
            elif now >= t["window_end"]:
                _stop_watch(st, "window_expired", now)
                if _set_state(st, "EXPIRED", now):
                    change.update({"from": prev if change["to"] is None else change["to"],
                                   "to": "EXPIRED"})
            else:
                w["next_tick_ms"] = next_tick_after(now, self.svc.delay_ms)

        after = self.svc.states.update(sid, key, apply)
        self.log("watch_tick", session_id=sid, or_date=key, state=after.get("state"),
                 m5_bars=sig.get("m5_bars"), data_status=sig.get("data_status"),
                 fetch_error=error, next_tick_utc=watch_view(after)["next_tick_utc"])
        if change["to"]:
            self.log("state_change", session_id=sid, or_date=key, frm=change["from"],
                     to=change["to"])
        if not after["watch"]["active"]:
            self.log("watch", action="stop", session_id=sid, or_date=key,
                     stop_reason=after["watch"]["stop_reason"])
        return error is None

    # ---------------------------------------------------------- M5 lich su

    def _maybe_history(self, now: int) -> None:
        """Cap nhat M5 lich su mot lan moi thang (ngay 2, 00:30 UTC) - chi khi da tai lan dau."""
        if self.history_update is None or not self.history_ready():
            return
        if self._history_task is not None and not self._history_task.done():
            return
        current = datetime.fromtimestamp(now / 1000, UTC)
        due = datetime(current.year, current.month, 2, 0, 30, tzinfo=UTC)
        due_ms = int(due.timestamp() * 1000)
        if now < due_ms:
            return
        last = self.svc.states.job("_history").get("last_run_ms") or 0
        if last >= due_ms:
            return

        async def job() -> None:
            try:
                report = await self.history_update()
                self.svc.states.set_job("_history", last_run_ms=now, last_run_utc=iso_utc(now),
                                        status="ok", last_error=None,
                                        report={k: report.get(k) for k in ("downloaded", "errors")}
                                        if isinstance(report, dict) else None)
            except Exception as exc:
                self.svc.states.set_job("_history", last_run_ms=now, last_run_utc=iso_utc(now),
                                        status="error", last_error=f"{type(exc).__name__}: {exc}")

        self._history_task = asyncio.get_running_loop().create_task(job())
