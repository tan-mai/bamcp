"""BAMCP - Binance Analysis MCP server."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2 as httpx
import uvicorn
import yaml

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

import admin       # module cuc bo: trang cai dat
import exchanges   # module cuc bo: adapter doc tai khoan san
import settings    # module cuc bo: luu credential xuong volume

# Nap .env neu co, de chay local khong phai export tay moi lan mo terminal.
# override=False: bien moi truong that luon thang .env, nen Docker (compose truyen
# bien vao container) khong bi file nay de len.
with contextlib.suppress(ImportError):
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)

CONFIG_PATH = Path(os.environ.get("BAMCP_CONFIG", Path(__file__).parent / "config.yaml"))

with CONFIG_PATH.open("r", encoding="utf-8") as fh:
    CFG: dict[str, Any] = yaml.safe_load(fh)

SRV = CFG["server"]
PATHS = CFG["paths"]
KL = CFG["klines"]
FETCH = CFG.get("fetcher", {"enabled": False})
RULES_SEED: dict[str, Any] = dict(CFG["rules"])   # gia tri goc, chi dung khi chua co rules.json
AUTH = SRV.get("auth") or {}
ACCOUNT = CFG.get("account") or {"enabled": False}


def _env(name: str) -> str | None:
    """Bien moi truong thang config. Dung cho Docker: secret khong nam trong image."""
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


# Override tu env. Thu tu uu tien: env > config.yaml
if _env("BAMCP_HOST"):
    SRV["host"] = _env("BAMCP_HOST")
if _env("BAMCP_PORT"):
    SRV["port"] = int(_env("BAMCP_PORT"))
if _env("BAMCP_ALLOWED_HOSTS"):
    SRV["allowed_hosts"] = [h.strip() for h in _env("BAMCP_ALLOWED_HOSTS").split(",") if h.strip()]
if _env("BAMCP_DATA_ROOT"):
    PATHS["data_root"] = _env("BAMCP_DATA_ROOT")
if _env("BAMCP_SYMBOL"):
    FETCH["symbol"] = _env("BAMCP_SYMBOL")
if _env("BAMCP_MARKET"):
    FETCH["market"] = _env("BAMCP_MARKET")

# Gia tri KHOI TAO tu env/config.yaml. Chung chi duoc dung de nap settings.json
# lan dau; sau do trang admin la nguon su that. Neu khong lam vay thi bam Luu
# tren trang admin xong ma bien moi truong van thang - kieu loi kho hieu nhat.
AUTH_ENABLED = bool(AUTH.get("enabled", True))
AUTH_REALM = AUTH.get("realm") or "BAMCP"
SEED_USER = _env("BAMCP_USERNAME") or (AUTH.get("username") or "")
SEED_PASS = _env("BAMCP_PASSWORD") or (AUTH.get("password") or "")

if _env("BAMCP_EXCHANGE"):
    ACCOUNT["exchange"] = _env("BAMCP_EXCHANGE")
if _env("BAMCP_ACCOUNT_ENABLED"):
    ACCOUNT["enabled"] = _env("BAMCP_ACCOUNT_ENABLED").lower() in ("1", "true", "yes", "on")
SEED_EX_ENABLED = bool(ACCOUNT.get("enabled"))
SEED_EX_NAME = (ACCOUNT.get("exchange") or "").strip().lower()
SEED_EX_KEY = _env("BAMCP_EXCHANGE_KEY") or ""
SEED_EX_SECRET = _env("BAMCP_EXCHANGE_SECRET") or ""
SEED_EX_PASSPHRASE = _env("BAMCP_EXCHANGE_PASSPHRASE") or ""

TZ = ZoneInfo(_env("TZ") or CFG.get("timezone", "UTC"))

DATA_ROOT = Path(PATHS["data_root"]).expanduser()
if not DATA_ROOT.is_absolute():
    DATA_ROOT = (CONFIG_PATH.parent / DATA_ROOT).resolve()

KLINES_DIR = DATA_ROOT / PATHS["klines_dir"]
BIAS_DIR = DATA_ROOT / PATHS["bias_dir"]
JOURNAL_DIR = DATA_ROOT / PATHS["journal_dir"]
RULES_FILE = DATA_ROOT / PATHS.get("rules_file", "rules.json")
SETTINGS_FILE = DATA_ROOT / PATHS.get("settings_file", "settings.json")

STORE = settings.SettingsStore(SETTINGS_FILE, TZ)


def _seed_settings() -> None:
    """Chuyen gia tri env/config vao settings.json neu file con trong.

    Chay mot lan luc dung app. Da co gia tri trong settings.json thi khong dong vao.
    """
    if SEED_USER and SEED_PASS and not STORE.has_auth():
        try:
            STORE.set_auth(SEED_USER, SEED_PASS)
        except ValueError as exc:
            # Khong duoc nuot im: nguoi dung da dat BAMCP_PASSWORD ma no bi tu choi
            print(f"CANH BAO: bo qua BAMCP_PASSWORD - {exc}", file=sys.stderr)

    if SEED_EX_KEY and SEED_EX_SECRET and not STORE.has_exchange_credentials():
        try:
            STORE.set_exchange(
                enabled=SEED_EX_ENABLED,
                name=SEED_EX_NAME,
                symbol=str(ACCOUNT.get("symbol") or ""),
                key=SEED_EX_KEY,
                secret=SEED_EX_SECRET,
                passphrase=SEED_EX_PASSPHRASE,
            )
        except ValueError as exc:
            print(f"CANH BAO: bo qua credential san tu env - {exc}", file=sys.stderr)


def _account_enabled() -> bool:
    """Doc moi lan goi - doi trong trang admin co hieu luc ngay, khong can restart."""
    return bool(STORE.exchange()["enabled"])


# ---------------------------------------------------------------- helpers

def _today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ---------------------------------------------------------------- rules

# Rang buoc ky thuat, KHONG phai rang buoc ky luat. Chung chi chan gia tri lam
# vo logic tinh toan (vd daily_stop_loss duong -> stop_hit dung ngay tu lenh dau).
# Muon noi long hay siet chat bao nhieu la quyen cua ban.
_RULE_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "max_trades_per_day": (0, None),
    "max_margin_per_trade": (0, None),
    "daily_stop_loss": (None, 0),      # phai <= 0
    "max_stop_points": (0, None),
    "min_take_profit_points": (0, None),
    # Han muc swing. swing_max_margin_per_trade = 0 nghia la khong gioi han.
    "swing_min_take_profit_points": (0, None),
    "swing_max_margin_per_trade": (0, None),
    "swing_max_stop_points": (0, None),
}


def _load_rules() -> dict[str, Any]:
    """Doc rule moi lan goi, khong cache. Sua rules.json la co hieu luc ngay.

    File thieu key nao thi lay key do tu config.yaml, nen them rule moi vao config
    khong lam hong file dang co.
    """
    stored = _read_json(RULES_FILE, None)
    values = stored.get("values") if isinstance(stored, dict) else None
    if not isinstance(values, dict):
        values = {}
    return {**RULES_SEED, **values}


def _rules_history(limit: int = 0) -> list[dict[str, Any]]:
    stored = _read_json(RULES_FILE, None)
    history = stored.get("history") if isinstance(stored, dict) else None
    if not isinstance(history, list):
        return []
    return history[-limit:] if limit else history


def _validate_rule_changes(changes: dict[str, Any]) -> dict[str, float]:
    """Tra ve int cho gia tri nguyen, float cho gia tri le."""
    if not changes:
        raise ValueError("changes rong, khong co gi de doi")

    clean: dict[str, float] = {}
    for key, raw in changes.items():
        if key not in RULES_SEED:
            raise ValueError(
                f"rule khong ton tai: {key}. Cho phep: {sorted(RULES_SEED)}")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{key} phai la so, nhan duoc {type(raw).__name__}")
        value = float(raw)
        low, high = _RULE_BOUNDS.get(key, (None, None))
        if low is not None and value < low:
            raise ValueError(f"{key} phai >= {low}, nhan duoc {value}")
        if high is not None and value > high:
            raise ValueError(f"{key} phai <= {high}, nhan duoc {value}")
        # Giu so nguyen la int: "3 lenh" doc de chiu hon "3.0 lenh" trong nhat ky
        clean[key] = int(value) if value == int(value) else value
    return clean


def _changed_today(history: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [h for h in history if str(h.get("at", "")).startswith(day)]


def _apply_rule_changes(changes: dict[str, Any], reason: str) -> dict[str, Any]:
    """Duong DUY NHAT de doi rule. Ca tool update_rules lan trang admin deu di qua day.

    Co mot cua thi lich su khong bao gio thung: khong co cach nao doi rule ma
    khong de lai dau, du doi tu Claude hay tu trinh duyet.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("reason la bat buoc - ghi ro vi sao doi rule")

    clean = _validate_rule_changes(changes)
    current = _load_rules()
    diff = {k: {"from": current.get(k), "to": v}
            for k, v in clean.items() if current.get(k) != v}
    if not diff:
        return {"updated": False, "reason": "gia tri moi trung gia tri cu",
                "rules": current}

    stored = _read_json(RULES_FILE, None)
    history = stored.get("history") if isinstance(stored, dict) else None
    if not isinstance(history, list):
        history = []
    history.append({"at": _now_iso(), "changes": diff, "reason": reason})

    values = {**current, **clean}
    _write_json(RULES_FILE, {
        "values": values,
        "updated_at": _now_iso(),
        "history": history,
    })
    return {
        "updated": True,
        "changes": diff,
        "reason": reason,
        "rules": values,
        "changed_today": len(_changed_today(history, _today())),
    }


# ---------------------------------------------------------------- klines

def _validate_timeframe(timeframe: str) -> str:
    tf = timeframe.strip().lower()
    if tf not in KL["timeframes"]:
        raise ValueError(f"timeframe khong hop le: {timeframe}. Cho phep: {KL['timeframes']}")
    return tf


def _normalize_bars(raw: Any) -> list[dict[str, float]]:
    """Chap nhan ca raw Binance array lan object OHLCV."""
    wrapper = KL.get("wrapper_key") or ""
    if wrapper and isinstance(raw, dict):
        raw = raw.get(wrapper, [])
    if isinstance(raw, dict):
        for key in ("data", "klines", "candles", "result"):
            if key in raw:
                raw = raw[key]
                break
    if not isinstance(raw, list):
        raise ValueError("File kline khong phai dang list")

    bars: list[dict[str, float]] = []
    for row in raw:
        if isinstance(row, (list, tuple)):
            if len(row) < 6:
                continue
            bars.append({
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": int(row[6]) if len(row) > 6 else 0,
            })
        elif isinstance(row, dict):
            def pick(*names: str) -> Any:
                for n in names:
                    if n in row:
                        return row[n]
                return None
            ot = pick("open_time", "openTime", "time", "t", "timestamp")
            ct = pick("close_time", "closeTime", "T")
            bars.append({
                "close_time": int(ct) if ct is not None else 0,
                "open_time": int(ot) if ot is not None else 0,
                "open": float(pick("open", "o")),
                "high": float(pick("high", "h")),
                "low": float(pick("low", "l")),
                "close": float(pick("close", "c")),
                "volume": float(pick("volume", "v") or 0.0),
            })
    bars.sort(key=lambda b: b["open_time"])
    return bars


def _load_bars(timeframe: str) -> list[dict[str, float]]:
    tf = _validate_timeframe(timeframe)
    path = _kline_path(tf)
    if not path.exists():
        raise FileNotFoundError(f"Khong tim thay file kline: {path}")
    return _normalize_bars(_read_json(path, []))


def _bar_time(bar: dict[str, float]) -> str:
    ts = bar.get("open_time", 0)
    if not ts:
        return ""
    if ts > 10_000_000_000:      # milliseconds
        ts = ts / 1000
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M")


_TF_MS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
          "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200,
          "1d": 86400, "3d": 259200, "1w": 604800, "1M": 2592000}


def _tf_ms(timeframe: str) -> int:
    return _TF_MS.get(timeframe.lower(), 0) * 1000


def _split_closed(bars: list[dict[str, float]], timeframe: str
                  ) -> tuple[list[dict[str, float]], dict[str, float] | None]:
    """Tach nen da dong khoi nen dang chay. VSA chi duoc tinh tren nen da dong."""
    span = _tf_ms(timeframe)
    now_ms = int(time.time() * 1000)
    closed, forming = [], None
    for bar in bars:
        end = bar.get("close_time") or (bar["open_time"] + span - 1 if span else 0)
        if end and end > now_ms:
            forming = bar
        else:
            closed.append(bar)
    return closed, forming


def _find_swings(bars: list[dict[str, float]], strength: int) -> dict[str, list[dict[str, Any]]]:
    highs, lows = [], []
    for i in range(strength, len(bars) - strength):
        window = bars[i - strength:i + strength + 1]
        if bars[i]["high"] == max(b["high"] for b in window):
            highs.append({"time": _bar_time(bars[i]), "price": bars[i]["high"]})
        if bars[i]["low"] == min(b["low"] for b in window):
            lows.append({"time": _bar_time(bars[i]), "price": bars[i]["low"]})
    return {"swing_highs": highs[-5:], "swing_lows": lows[-5:]}


def _pct(value: float, low: float, high: float) -> float | None:
    span = high - low
    if span <= 0:
        return None
    return round((value - low) / span * 100, 2)


def _build_context(timeframe: str) -> dict[str, Any]:
    all_bars = _load_bars(timeframe)
    if not all_bars:
        return {"timeframe": timeframe, "error": "khong co du lieu"}

    bars, forming = _split_closed(all_bars, timeframe)
    if not bars:
        return {"timeframe": timeframe, "error": "chua co nen nao dong"}

    lookback = min(int(KL["context_lookback"]), len(bars))
    window = bars[-lookback:]
    last = window[-1]

    span = _tf_ms(timeframe)
    now_ms = int(time.time() * 1000)
    last_end = last.get("close_time") or (last["open_time"] + span - 1 if span else 0)
    age_min = round((now_ms - last_end) / 60000, 1) if last_end else None
    freshness = {
        "last_closed_bar_age_minutes": age_min,
        "expected_bar_minutes": round(span / 60000) if span else None,
        # Tre hon 2 nen = pipeline co van de, dung tin ket qua phan tich
        "stale": bool(span and last_end and (now_ms - last_end) > 2 * span),
    }

    current = None
    if forming:
        done = ((now_ms - forming["open_time"]) / span * 100) if span else None
        current = {
            "time": _bar_time(forming),
            "price": forming["close"],
            "high_so_far": forming["high"],
            "low_so_far": forming["low"],
            "volume_so_far": forming["volume"],
            "completion_pct": round(done, 1) if done is not None else None,
            "note": "Nen CHUA DONG. Khong dung so lieu nay de danh gia VSA.",
        }

    range_high = max(b["high"] for b in window)
    range_low = min(b["low"] for b in window)
    avg_volume = sum(b["volume"] for b in window) / lookback
    avg_spread = sum(b["high"] - b["low"] for b in window) / lookback
    last_spread = last["high"] - last["low"]

    return {
        "timeframe": timeframe,
        "bars_available": len(all_bars),
        "closed_bars": len(bars),
        "lookback": lookback,
        "freshness": freshness,
        "current_bar": current,
        "last_closed_bar": {
            "time": _bar_time(last),
            "open": last["open"],
            "high": last["high"],
            "low": last["low"],
            "close": last["close"],
            "volume": last["volume"],
        },
        "range": {
            "high": range_high,
            "low": range_low,
            "height": round(range_high - range_low, 2),
            "close_position_pct": _pct(last["close"], range_low, range_high),
        },
        # VSA tinh tren NEN DA DONG cuoi cung, khong phai nen dang chay
        "vsa": {
            "spread": round(last_spread, 2),
            "avg_spread": round(avg_spread, 2),
            "spread_ratio": round(last_spread / avg_spread, 2) if avg_spread else None,
            "volume": last["volume"],
            "avg_volume": round(avg_volume, 2),
            "volume_ratio": round(last["volume"] / avg_volume, 2) if avg_volume else None,
            "close_in_bar_pct": _pct(last["close"], last["low"], last["high"]),
        },
        **_find_swings(window, int(KL["swing_strength"])),
    }


# ---------------------------------------------------------------- fetcher

FETCH_STATE: dict[str, Any] = {"last_run": None, "last_error": None, "results": {}}
FETCH_LOCK = asyncio.Lock()


def _binance_url() -> str:
    key = "base_url_spot" if FETCH.get("market") == "spot" else "base_url_futures"
    return FETCH[key]


def _kline_path(timeframe: str) -> Path:
    return KLINES_DIR / PATHS["kline_filename"].format(timeframe=timeframe)


def _merge_bars(old: list[Any], new: list[Any], cap: int) -> list[Any]:
    """Gop theo openTime, nen moi ghi de nen cu cung moc thoi gian."""
    merged: dict[int, Any] = {int(r[0]): r for r in old if isinstance(r, (list, tuple)) and r}
    for row in new:
        merged[int(row[0])] = row
    ordered = [merged[k] for k in sorted(merged)]
    return ordered[-cap:] if cap else ordered


async def _fetch_one(client: httpx.AsyncClient, timeframe: str) -> dict[str, Any]:
    params = {
        "symbol": FETCH["symbol"],
        "interval": timeframe,
        "limit": int(FETCH["fetch_limit"]),
    }
    retries = max(1, int(FETCH.get("retry", 3)))
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = await client.get(_binance_url(), params=params,
                                    timeout=float(FETCH["request_timeout"]))
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                raise ValueError("Binance tra ve du lieu rong")

            path = _kline_path(timeframe)
            existing = _read_json(path, [])
            if not isinstance(existing, list):
                existing = []
            merged = _merge_bars(existing, rows, int(FETCH.get("max_history", 1000)))
            _write_json(path, merged)
            return {"ok": True, "fetched": len(rows), "total": len(merged),
                    "at": _now_iso()}
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:          # khong ngu sau lan thu cuoi
                await asyncio.sleep(2 ** attempt)
    return {"ok": False, "error": str(last_exc), "at": _now_iso()}


async def fetch_all(timeframes: list[str] | None = None) -> dict[str, Any]:
    """Mot lan pull duy nhat tai mot thoi diem, du goi tu MCP tool, HTTP API hay vong lap nen."""
    targets = [tf.lower() for tf in (timeframes or KL["timeframes"])]
    async with FETCH_LOCK:
        async with httpx.AsyncClient() as client:
            pairs = await asyncio.gather(*[_fetch_one(client, tf) for tf in targets])
    results = dict(zip(targets, pairs))
    FETCH_STATE["last_run"] = _now_iso()
    FETCH_STATE["results"] = results
    failed = [tf for tf, r in results.items() if not r.get("ok")]
    FETCH_STATE["last_error"] = f"loi o: {', '.join(failed)}" if failed else None
    return results


async def _fetcher_loop() -> None:
    """Vong lap nen thay cho cron. Song cung vong doi cua app, khong can scheduler ngoai.

    Khong co duong nao thoat khoi vong lap ngoai cancel luc tat server: mot exception
    lot ra ngoai se giet task, va khong ai restart no - container van chay, health van
    xanh, nhung du lieu dung im. Nen moi thu deu nam trong try.
    """
    interval = int(FETCH.get("interval_seconds", 900))
    first = bool(FETCH.get("run_on_start", True))
    while True:
        if not first:
            await asyncio.sleep(interval)
        first = False
        try:
            await fetch_all()
        except asyncio.CancelledError:
            raise                      # tat server: de no thoat that
        except Exception as exc:
            FETCH_STATE["last_error"] = f"{type(exc).__name__}: {exc}"
            print(f"BAMCP fetcher loi: {FETCH_STATE['last_error']}", file=sys.stderr)


TRADE_TYPES = ("scalp", "swing")


def _evaluate_trade(*, side: str, entry: float, stop: float, target: float,
                    margin_usd: float, trade_type: str,
                    trades: list[dict[str, Any]], rules: dict[str, Any],
                    realized: float) -> dict[str, Any]:
    """Cham mot lenh theo bo rule dang hieu luc. Khong ghi gi.

    Hai bo han muc: scalp (chat) va swing (rong). Nhung KHONG duoc tu dan nhan
    swing de lach - lenh chi duoc huong han muc swing khi TP thuc su dat nguong
    swing_min_take_profit_points. Neu khong, no bi ha xuong scalp va ghi lai
    dieu do. Cai nhan la he qua cua con so, khong phai y muon.
    """
    side = side.strip().lower()
    if side not in ("long", "short"):
        raise ValueError("side phai la long hoac short")
    trade_type = (trade_type or "scalp").strip().lower()
    if trade_type not in TRADE_TYPES:
        raise ValueError(f"trade_type phai la {' hoac '.join(TRADE_TYPES)}")

    stop_points = round(abs(float(entry) - float(stop)), 2)
    target_points = round(abs(float(target) - float(entry)), 2)
    swing_threshold = float(rules["swing_min_take_profit_points"])

    violations: list[str] = []
    demoted = False

    if trade_type == "swing" and target_points < swing_threshold:
        demoted = True
        trade_type = "scalp"
        violations.append(
            f"khai swing nhung TP {target_points} < swing_min_take_profit_points "
            f"({swing_threshold:g}) - ap dung han muc scalp")

    if trade_type == "swing":
        max_margin = float(rules["swing_max_margin_per_trade"])
        max_stop = float(rules["swing_max_stop_points"])
        min_tp = swing_threshold
        prefix = "swing_"
    else:
        max_margin = float(rules["max_margin_per_trade"])
        max_stop = float(rules["max_stop_points"])
        min_tp = float(rules["min_take_profit_points"])
        prefix = ""

    # Dung ngay quan trong hon moi rule khac -> dat len dau
    if realized <= float(rules["daily_stop_loss"]):
        violations.append(
            f"da cham daily_stop_loss: PnL hom nay {realized} <= {rules['daily_stop_loss']}")
    if len(trades) >= int(rules["max_trades_per_day"]):
        violations.append(f"vuot max_trades_per_day ({rules['max_trades_per_day']})")
    if stop_points > max_stop:
        violations.append(f"SL {stop_points} > {prefix}max_stop_points ({max_stop:g})")
    if target_points < min_tp:
        violations.append(f"TP {target_points} < {prefix}min_take_profit_points ({min_tp:g})")
    # max_margin = 0 nghia la khong gioi han (chi dung cho swing)
    if max_margin > 0 and float(margin_usd) > max_margin:
        violations.append(f"margin {margin_usd} > {prefix}max_margin_per_trade ({max_margin:g})")

    return {
        "side": side,
        "trade_type": trade_type,
        "demoted_to_scalp": demoted,
        "entry": float(entry),
        "stop": float(stop),
        "target": float(target),
        "stop_points": stop_points,
        "target_points": target_points,
        "rr": round(target_points / stop_points, 2) if stop_points else None,
        "margin_usd": float(margin_usd),
        "limits_applied": {
            "max_margin_per_trade": max_margin or "khong gioi han",
            "max_stop_points": max_stop,
            "min_take_profit_points": min_tp,
        },
        "rule_violations": violations,
    }


# ---------------------------------------------------------------- server

# Quy trinh phan tich nam trong config.yaml (key `instructions`) de sua duoc ma
# khong phai build lai image. Doi xong can `docker compose restart` vi Claude chi
# doc instructions mot lan luc bat tay.
DEFAULT_INSTRUCTIONS = (
    "Du lieu BTC da khung tu Binance. Moi con so phai lay tu cac tool nay, "
    "khong duoc uoc luong hay nho lai."
)

mcp = MCPServer(
    name="bamcp",
    instructions=(CFG.get("instructions") or DEFAULT_INSTRUCTIONS).strip(),
)


@mcp.tool()
def list_timeframes() -> dict[str, Any]:
    """Liet ke cac khung thoi gian co san va trang thai file du lieu."""
    out = []
    for tf in KL["timeframes"]:
        path = KLINES_DIR / PATHS["kline_filename"].format(timeframe=tf)
        entry = {"timeframe": tf, "file": str(path), "exists": path.exists()}
        if path.exists():
            entry["updated_at"] = datetime.fromtimestamp(
                path.stat().st_mtime, TZ).strftime("%Y-%m-%d %H:%M:%S")
        out.append(entry)
    return {
        "data_root": str(DATA_ROOT),
        "symbol": FETCH.get("symbol"),
        "market": FETCH.get("market"),
        "fetcher_enabled": bool(FETCH.get("enabled")),
        "last_fetch": FETCH_STATE["last_run"],
        "last_fetch_error": FETCH_STATE["last_error"],
        "timeframes": out,
    }


@mcp.tool()
def get_klines(timeframe: str, limit: int = 0, include_forming: bool = False) -> dict[str, Any]:
    """Lay nen OHLCV tho cua mot khung thoi gian.

    timeframe: mot trong cac khung khai bao o config (vd 1w, 1d, 4h, 1h, 15m).
    limit: so nen gan nhat, 0 = dung default_limit trong config.
    include_forming: mac dinh False, chi tra nen DA DONG. Bat True thi nen dang chay
      duoc them o cuoi voi is_closed=false - chi de biet gia hien tai, khong doc VSA tu no.
    """
    tf = _validate_timeframe(timeframe)
    closed, forming = _split_closed(_load_bars(tf), tf)

    n = int(limit) if limit else int(KL["default_limit"])
    n = max(1, min(n, int(KL["max_limit"])))

    keys = ("open", "high", "low", "close", "volume")
    rows = [{"time": _bar_time(b), "is_closed": True, **{k: b[k] for k in keys}}
            for b in closed[-n:]]
    attached = bool(include_forming and forming)
    if attached:
        rows.append({"time": _bar_time(forming), "is_closed": False,
                     **{k: forming[k] for k in keys}})

    return {
        "timeframe": tf,
        "count": len(rows),
        "closed_bars": len(rows) - (1 if attached else 0),
        "forming_bar_included": attached,
        "note": "Chi nen co is_closed=true moi duoc dung de danh gia VSA.",
        "bars": rows,
    }


@mcp.tool()
async def refresh_data(timeframes: list[str] | None = None) -> dict[str, Any]:
    """Keo du lieu moi nhat tu Binance ngay lap tuc, khong cho den chu ky tiep theo.

    Dung khi can gia moi nhat truoc luc tim entry.
    """
    targets = timeframes or KL["timeframes"]
    for tf in targets:
        _validate_timeframe(tf)
    return {"refreshed_at": _now_iso(), "results": await fetch_all(targets)}


@mcp.tool()
def get_context(timeframes: list[str] | None = None) -> dict[str, Any]:
    """Tom tat da khung: bien range, vi tri gia trong range, spread/volume cho VSA, swing gan nhat.

    timeframes: bo trong = lay tat ca khung trong config.
    """
    targets = timeframes or KL["timeframes"]
    result = {}
    for tf in targets:
        try:
            result[tf.lower()] = _build_context(tf.lower())
        except Exception as exc:
            result[tf.lower()] = {"error": str(exc)}
    return {"generated_at": _now_iso(), "contexts": result}


@mcp.tool()
def get_bias(date: str = "") -> dict[str, Any]:
    """Doc bias da luu. date bo trong = hom nay (theo timezone trong config)."""
    day = date or _today()
    data = _read_json(BIAS_DIR / f"{day}.json", None)
    if data is None:
        return {"date": day, "found": False,
                "message": "Chua co bias cho ngay nay. Chay buoc D/H4 roi save_bias."}
    return {"date": day, "found": True, **data}


@mcp.tool()
def save_bias(
    direction: str,
    summary: str,
    phase: str = "",
    key_levels: dict[str, float] | None = None,
    action_zone: str = "",
    notes: str = "",
    date: str = "",
) -> dict[str, Any]:
    """Luu bias trong ngay sau khi phan tich khung lon.

    direction: long | short | neutral
    phase: pha Wyckoff dang o (A/B/C/D/E)
    key_levels: vd {"creek": 111000, "ice": 105000}
    action_zone: vung gia cho setup o khung nho
    """
    day = date or _today()
    direction = direction.strip().lower()
    if direction not in ("long", "short", "neutral"):
        raise ValueError("direction phai la long, short hoac neutral")

    path = BIAS_DIR / f"{day}.json"
    previous = _read_json(path, None)
    history = previous.pop("history", []) if isinstance(previous, dict) else []
    if previous:
        history.append(previous)

    payload = {
        "direction": direction,
        "summary": summary,
        "phase": phase,
        "key_levels": key_levels or {},
        "action_zone": action_zone,
        "notes": notes,
        "updated_at": _now_iso(),
        "history": history[-10:],
    }
    _write_json(path, payload)
    return {"saved": True, "date": day, "file": str(path), "revisions": len(history)}


@mcp.tool()
def get_rules(history_limit: int = 10) -> dict[str, Any]:
    """Doc bo quy dinh dang co hieu luc va lich su thay doi.

    history_limit: so lan doi gan nhat can xem, 0 = xem tat ca.
    """
    history = _rules_history()
    return {
        "rules": _load_rules(),
        "defaults": RULES_SEED,
        "file": str(RULES_FILE),
        "changes_total": len(history),
        "changed_today": _changed_today(history, _today()),
        "history": history[-history_limit:] if history_limit else history,
    }


@mcp.tool()
def update_rules(changes: dict[str, float], reason: str) -> dict[str, Any]:
    """Doi quy dinh giao dich. Co hieu luc NGAY cho moi lan goi tool sau do.

    changes: chi dien rule muon doi, vd {"max_trades_per_day": 2}.
      Rule khong nhac den thi giu nguyen.
    reason: bat buoc. Vi sao doi. Duoc ghi vao lich su cung moc thoi gian,
      va hien lai trong get_today_status neu doi trong ngay dang giao dich.
    """
    return _apply_rule_changes(changes, reason)


@mcp.tool()
async def get_today_status(date: str = "") -> dict[str, Any]:
    """Kiem tra quota lenh va PnL trong ngay truoc khi vao lenh moi.

    Khi account.enabled = true, khoi 'exchange' chua so THAT lay tu san va
    'can_trade' duoc tinh theo so that do, khong phai theo nhat ky tu khai.
    """
    day = date or _today()
    rules = _load_rules()
    trades = _read_json(JOURNAL_DIR / f"{day}.json", [])
    closed = [t for t in trades if t.get("pnl") is not None]
    journal_pnl = round(sum(float(t["pnl"]) for t in closed), 2)
    edits = _changed_today(_rules_history(), day)

    # Mac dinh: chi co nhat ky tu khai
    trades_counted = len(trades)
    pnl_counted = journal_pnl
    source = "journal"
    exchange_block: dict[str, Any] | None = None

    if _account_enabled():
        try:
            data = await _account_day(day)
            positions = await _open_positions()
            exchange_block = {
                "exchange": data["exchange"],
                "orders": len(data["order_ids"]),
                "fills": len(data["fills"]),
                "open_positions": positions["open_positions"],
                **data["totals"],
            }
            # San la nguon su that. Lenh quen log van tinh vao quota.
            trades_counted = max(len(trades), len(data["order_ids"]))
            pnl_counted = data["totals"]["net"]
            source = "exchange"
        except Exception as exc:
            # Mat ket noi san khong duoc lam hong buoc kiem tra ky luat
            exchange_block = {"error": str(exc),
                              "note": "khong doc duoc san, dang dung so tu nhat ky"}

    remaining = max(0, int(rules["max_trades_per_day"]) - trades_counted)
    stop_hit = pnl_counted <= float(rules["daily_stop_loss"])

    return {
        "date": day,
        "counted_from": source,
        "trades_taken": trades_counted,
        "trades_remaining": remaining,
        # Dem theo dung nguon dang tin: san bao vi the mo, nhat ky bao lenh chua dong
        "open_trades": (exchange_block["open_positions"] if source == "exchange"
                        else len(trades) - len(closed)),
        "open_trades_journal": len(trades) - len(closed),
        "realized_pnl": pnl_counted,
        "journal_pnl": journal_pnl,
        "daily_stop_hit": stop_hit,
        "can_trade": remaining > 0 and not stop_hit,
        "rules": rules,
        # Rule bi doi trong chinh ngay dang giao dich la tin hieu dang de y
        "rules_changed_today": edits,
        "exchange": exchange_block,
        "trades": trades,
    }


@mcp.tool()
def log_trade(
    side: str,
    entry: float,
    stop: float,
    target: float,
    margin_usd: float,
    trade_type: str = "scalp",
    setup: str = "",
    date: str = "",
) -> dict[str, Any]:
    """Ghi mot lenh vua vao. Tra ve canh bao neu pham rule, nhung van ghi de nhat ky dung thuc te.

    trade_type: scalp (mac dinh, han muc chat) hoac swing (han muc rong hon).
      Khai swing ma TP khong dat nguong swing_min_take_profit_points thi lenh
      tu dong bi ha xuong han muc scalp - dan nhan khong lach duoc.
    """
    day = date or _today()
    path = JOURNAL_DIR / f"{day}.json"
    trades = _read_json(path, [])
    realized = round(sum(float(t["pnl"]) for t in trades if t.get("pnl") is not None), 2)
    rules = _load_rules()

    verdict = _evaluate_trade(
        side=side, entry=entry, stop=stop, target=target, margin_usd=margin_usd,
        trade_type=trade_type, trades=trades, rules=rules, realized=realized,
    )
    violations = verdict["rule_violations"]

    trade = {
        "id": len(trades) + 1,
        "side": verdict["side"],
        "trade_type": verdict["trade_type"],
        "entry": verdict["entry"],
        "stop": verdict["stop"],
        "target": verdict["target"],
        "stop_points": verdict["stop_points"],
        "target_points": verdict["target_points"],
        "rr": verdict["rr"],
        "margin_usd": verdict["margin_usd"],
        "setup": setup,
        "opened_at": _now_iso(),
        "pnl": None,
        # Chup lai rule dang hieu luc luc vao lenh. Doi rule ve sau khong sua duoc
        # nhat ky cu, nen doc lai van biet luc do minh dang choi theo luat nao.
        "rules_at_entry": rules,
        "limits_applied": verdict["limits_applied"],
        "demoted_to_scalp": verdict["demoted_to_scalp"],
        "rule_violations": violations,
    }
    trades.append(trade)
    _write_json(path, trades)
    return {
        "logged": True,
        "trade": trade,
        "rule_violations": violations,
        "rules_changed_today": _changed_today(_rules_history(), day),
    }


@mcp.tool()
def check_trade(side: str, entry: float, stop: float, target: float,
                margin_usd: float, trade_type: str = "scalp",
                date: str = "") -> dict[str, Any]:
    """Cham thu mot lenh theo rule ma KHONG ghi vao nhat ky.

    Dung truoc khi bam lenh: xem no duoc xep scalp hay swing, han muc nao ap
    dung, co pham rule gi khong. Muon ghi that thi goi log_trade voi cung tham so.
    """
    day = date or _today()
    trades = _read_json(JOURNAL_DIR / f"{day}.json", [])
    realized = round(sum(float(t["pnl"]) for t in trades if t.get("pnl") is not None), 2)

    verdict = _evaluate_trade(
        side=side, entry=entry, stop=stop, target=target, margin_usd=margin_usd,
        trade_type=trade_type, trades=trades, rules=_load_rules(), realized=realized,
    )
    return {
        "date": day,
        "would_pass": not verdict["rule_violations"],
        "logged": False,
        **verdict,
    }


@mcp.tool()
def close_trade(trade_id: int, exit_price: float, pnl: float, note: str = "",
                date: str = "") -> dict[str, Any]:
    """Dong mot lenh da ghi va cap nhat PnL thuc te."""
    day = date or _today()
    path = JOURNAL_DIR / f"{day}.json"
    trades = _read_json(path, [])
    for trade in trades:
        if trade.get("id") == int(trade_id):
            trade["exit_price"] = float(exit_price)
            trade["pnl"] = float(pnl)
            trade["close_note"] = note
            trade["closed_at"] = _now_iso()
            _write_json(path, trades)
            return {"closed": True, "trade": trade}
    raise ValueError(f"Khong tim thay trade id {trade_id} trong ngay {day}")


# ---------------------------------------------------------------- account

def _exchange() -> exchanges.BaseExchange:
    """Dung adapter tu credential trong settings.json.

    Doc lai moi lan goi, nen doi key trong trang admin la co hieu luc ngay.
    """
    cfg = STORE.exchange()
    if not cfg["enabled"]:
        raise ValueError(
            "Doc tai khoan san dang TAT. Bat len trong trang admin "
            f"({SRV.get('admin_path', '/admin')}).")
    if not cfg["name"]:
        raise ValueError("Chua chon san. Vao trang admin chon binance, bybit hoac okx.")

    # Phan khong phai secret (base_url, category, inst_type) van lay tu config.yaml
    options = ACCOUNT.get(cfg["name"]) or {}
    return exchanges.build(
        cfg["name"],
        key=cfg["key"],
        secret=cfg["secret"],
        passphrase=cfg["passphrase"],
        symbol=cfg["symbol"] or str(ACCOUNT.get("symbol") or ""),
        base_url=options.get("base_url", ""),
        timeout=float(ACCOUNT.get("request_timeout", 20)),
        recv_window=int(ACCOUNT.get("recv_window", 5000)),
        options=options,
    )


def _day_window_ms(day: str) -> tuple[int, int]:
    """Bien mot ngay lich (theo timezone trong config) thanh cap moc ms."""
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000) - 1


def _stamp(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S")


def _summarize(settlements: list[dict[str, Any]]) -> dict[str, float]:
    """Gop settlement theo loai. Day la con so that, da tru phi va funding."""
    totals = {"realized_pnl": 0.0, "commission": 0.0, "funding": 0.0}
    for row in settlements:
        kind = row.get("type")
        if kind in totals:
            totals[kind] += float(row.get("amount") or 0.0)
    totals = {k: round(v, 4) for k, v in totals.items()}
    totals["net"] = round(sum(totals.values()), 4)
    return totals


async def _account_day(day: str) -> dict[str, Any]:
    """Fills + settlement cua mot ngay, da chuan hoa. Dung chung cho nhieu tool."""
    start_ms, end_ms = _day_window_ms(day)
    adapter = _exchange()
    async with httpx.AsyncClient() as client:
        fills = await adapter.fills(client, start_ms, end_ms)
        settlements = await adapter.settlements(client, start_ms, end_ms)
    for row in fills:
        row["time"] = _stamp(row["ts"])
    for row in settlements:
        row["time"] = _stamp(row["ts"])
    return {
        "date": day,
        "exchange": adapter.name,
        "symbol": adapter.symbol,
        "fills": fills,
        "order_ids": sorted({f["order_id"] for f in fills if f.get("order_id")}),
        "settlements": settlements,
        "totals": _summarize(settlements),
    }


async def _open_positions() -> dict[str, Any]:
    """Ham thuan, khong boc decorator, de cac tool khac goi lai duoc."""
    adapter = _exchange()
    async with httpx.AsyncClient() as client:
        positions = await adapter.positions(client)
    return {
        "exchange": adapter.name,
        "symbol": adapter.symbol,
        "checked_at": _now_iso(),
        "open_positions": len(positions),
        "positions": positions,
        "flat": not positions,
    }


@mcp.tool()
async def get_positions() -> dict[str, Any]:
    """Vi the dang mo THAT TREN SAN. Day la su that, khong phai nhat ky tu khai.

    Dung truoc khi phan tich de biet minh dang cam gi, va sau khi vao lenh de
    xac nhan lenh da khop dung gia du dinh.
    """
    return await _open_positions()


@mcp.tool()
async def get_fills(date: str = "") -> dict[str, Any]:
    """Lenh da khop trong ngay, lay thang tu san. date bo trong = hom nay."""
    data = await _account_day(date or _today())
    return {
        "date": data["date"],
        "exchange": data["exchange"],
        "symbol": data["symbol"],
        "fill_count": len(data["fills"]),
        "order_count": len(data["order_ids"]),
        "fills": data["fills"],
    }


@mcp.tool()
async def get_account_pnl(date: str = "") -> dict[str, Any]:
    """PnL THUC trong ngay, tach rieng lai/lo, phi giao dich va funding.

    Khac voi realized_pnl trong get_today_status - so do la tu khai. So o day
    lay tu sao ke cua san nen da bao gom phi va funding, thuong xau hon so tu khai.
    """
    data = await _account_day(date or _today())
    return {
        "date": data["date"],
        "exchange": data["exchange"],
        "symbol": data["symbol"],
        "totals": data["totals"],
        "note": "net = realized_pnl + commission + funding. Phi va funding la so am.",
        "settlements": data["settlements"],
    }


@mcp.tool()
async def reconcile_journal(date: str = "") -> dict[str, Any]:
    """Doi chieu nhat ky tu ghi voi du lieu that tren san. Chi ra cho lech.

    Chay cuoi ngay, hoac bat cu luc nao nghi minh quen log mot lenh.
    """
    day = date or _today()
    trades = _read_json(JOURNAL_DIR / f"{day}.json", [])
    journal_pnl = round(
        sum(float(t["pnl"]) for t in trades if t.get("pnl") is not None), 2)

    data = await _account_day(day)
    exchange_net = data["totals"]["net"]

    findings = []
    if len(data["order_ids"]) > len(trades):
        findings.append(
            f"san ghi nhan {len(data['order_ids'])} lenh nhung nhat ky chi co "
            f"{len(trades)} - co lenh chua log")
    elif len(trades) > len(data["order_ids"]):
        findings.append(
            f"nhat ky co {len(trades)} lenh nhung san chi thay "
            f"{len(data['order_ids'])} - co lenh log nhung khong khop")

    gap = round(journal_pnl - exchange_net, 2)
    if abs(gap) >= float(ACCOUNT.get("pnl_tolerance", 0.5)):
        findings.append(
            f"PnL lech {gap}: nhat ky {journal_pnl} vs san {exchange_net}. "
            "Phan lon do phi giao dich va funding khong duoc ghi khi log tay.")

    open_trades = [t for t in trades if t.get("pnl") is None]
    if open_trades:
        findings.append(
            f"{len(open_trades)} lenh trong nhat ky chua dong - so sanh PnL chua tron ven")

    return {
        "date": day,
        "exchange": data["exchange"],
        "matched": not findings,
        "findings": findings,
        "journal": {"trades": len(trades), "realized_pnl": journal_pnl},
        "exchange_data": {
            "orders": len(data["order_ids"]),
            "fills": len(data["fills"]),
            **data["totals"],
        },
    }


# ---------------------------------------------------------------- admin

ADMIN_PATH = SRV.get("admin_path", "/admin")
ADMIN_SAVE_PATH = ADMIN_PATH.rstrip("/") + "/save"
ADMIN_TEST_PATH = ADMIN_PATH.rstrip("/") + "/test"


def _setup_ok(payload: dict[str, Any]) -> bool:
    """Truoc khi co mat khau, moi thao tac ghi phai kem setup token."""
    if STORE.has_auth():
        return True        # da qua Basic auth o middleware
    # strip(): copy tu khung log rat de dinh khoang trang hoac xuong dong o cuoi,
    # ma compare_digest so khop tung byte nen lech ngay.
    token = str(payload.get("setup_token") or "").strip()
    expected = STORE.setup_token()
    return bool(expected) and secrets.compare_digest(token, expected)


def _optional(payload: dict[str, Any], field: str) -> str | None:
    """O trong = giu nguyen gia tri cu (tra None), co chu = doi."""
    value = payload.get(field)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


@mcp.custom_route(ADMIN_PATH, methods=["GET"])
async def http_admin(request):
    from starlette.responses import HTMLResponse
    page = admin.render(
        STORE.masked(),
        settings_path=str(SETTINGS_FILE),
        save_path=ADMIN_SAVE_PATH,
        test_path=ADMIN_TEST_PATH,
        exchange_names=settings.EXCHANGE_NAMES,
        rules=_load_rules(),
        rules_history=_rules_history(),
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@mcp.custom_route(ADMIN_SAVE_PATH, methods=["POST"])
async def http_admin_save(request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "body khong phai JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body phai la object"}, status_code=400)

    if not _setup_ok(payload):
        return JSONResponse(
            {"error": "Setup token sai. Xem dong 'BAMCP SETUP TOKEN' trong log container."},
            status_code=403)

    changed: list[str] = []
    try:
        password = _optional(payload, "password")
        username = str(payload.get("username") or "").strip()

        if password:
            if password != str(payload.get("password2") or ""):
                return JSONResponse({"error": "Hai o mat khau khong khop"}, status_code=400)
            STORE.set_auth(username or STORE.username() or "bamcp", password)
            changed.append("mat khau API")
        elif username and username != STORE.username():
            if not STORE.has_auth():
                return JSONResponse(
                    {"error": "Lan dau phai dat ca username va mat khau"}, status_code=400)
            return JSONResponse(
                {"error": "Doi username thi phai nhap lai mat khau"}, status_code=400)

        STORE.set_exchange(
            enabled=bool(payload.get("exchange_enabled")),
            name=str(payload.get("exchange_name") or ""),
            symbol=str(payload.get("exchange_symbol") or ""),
            key=_optional(payload, "api_key"),
            secret=_optional(payload, "api_secret"),
            passphrase=_optional(payload, "api_passphrase"),
        )
        changed.append("cau hinh san")

        # Rule di qua dung ham ma tool update_rules dung: bat buoc co ly do,
        # ghi vao cung so lich su. Trang admin khong phai cua sau.
        raw_rules = payload.get("rules")
        if isinstance(raw_rules, dict) and raw_rules:
            numeric: dict[str, Any] = {}
            for key, value in raw_rules.items():
                try:
                    numeric[key] = float(str(value).strip())
                except (TypeError, ValueError):
                    return JSONResponse(
                        {"error": f"{key} phai la so, nhan duoc '{value}'"}, status_code=400)
            result = _apply_rule_changes(numeric, str(payload.get("rules_reason") or ""))
            if result["updated"]:
                changed.append(f"{len(result['changes'])} quy dinh")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse({
        "saved": True,
        "message": "Da luu: " + ", ".join(changed) + ". Co hieu luc ngay.",
        # Vua dat mat khau lan dau thi tai lai de trinh duyet hoi dang nhap
        "reload": bool(password),
        "state": STORE.masked(),
    })


@mcp.custom_route(ADMIN_TEST_PATH, methods=["POST"])
async def http_admin_test(request):
    """Goi that len san bang credential dang luu. Chi doc vi the, khong ghi gi."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not _setup_ok(payload if isinstance(payload, dict) else {}):
        return JSONResponse({"error": "Setup token sai"}, status_code=403)

    try:
        result = await _open_positions()
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)

    count = result["open_positions"]
    detail = "khong co vi the nao dang mo" if result["flat"] else f"{count} vi the dang mo"
    return JSONResponse({
        "ok": True,
        "message": f"Ket noi {result['exchange']} thanh cong - {detail} ({result['symbol']}).",
    })


# ---------------------------------------------------------------- health

@mcp.custom_route(SRV.get("health_path", "/healthz"), methods=["GET"])
async def http_health(request):
    """Public, khong can auth. Dung cho Docker HEALTHCHECK va proxy."""
    return JSONResponse({"status": "ok", "time": _now_iso()})


# ---------------------------------------------------------------- auth

class BasicAuthMiddleware:
    """HTTP Basic auth, viet duoi dang pure ASGI middleware.

    Khong dung BaseHTTPMiddleware vi transport streamable-http cua MCP dua tren SSE;
    BaseHTTPMiddleware boc response vao mot anyio task group va lam hong stream dai.
    """

    def __init__(self, app, store, realm: str,
                 public_paths: tuple[str, ...] = (),
                 setup_paths: tuple[str, ...] = ()):
        self.app = app
        self.store = store
        self.realm = realm
        self.public_paths = public_paths
        # Duong chi mo khi CHUA co mat khau nao - de con vao dat mat khau lan dau.
        # Ban than chung van doi setup token moi ghi duoc gi.
        self.setup_paths = setup_paths

    def _authorized(self, headers: list[tuple[bytes, bytes]]) -> bool:
        raw = b""
        for key, value in headers:
            if key.lower() == b"authorization":
                raw = value
                break
        if not raw.lower().startswith(b"basic "):
            return False
        try:
            decoded = base64.b64decode(raw[6:].strip(), validate=True)
        except Exception:
            return False
        user, sep, pwd = decoded.partition(b":")
        if not sep:
            return False
        return self.store.verify(raw, user, pwd)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.public_paths:
            await self.app(scope, receive, send)
            return
        # Chua cai dat gi: cho vao trang admin, moi duong khac van dong
        if not self.store.has_auth() and scope["path"] in self.setup_paths:
            await self.app(scope, receive, send)
            return

        if not self._authorized(scope.get("headers") or []):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": f'Basic realm="{self.realm}", charset="UTF-8"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_app():
    hosts = SRV.get("allowed_hosts") or []
    app = mcp.streamable_http_app(
        streamable_http_path=SRV["mcp_path"],
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=bool(hosts),
            allowed_hosts=hosts,
            allowed_origins=hosts,
        ),
    )
    _seed_settings()
    if AUTH_ENABLED:
        app.add_middleware(
            BasicAuthMiddleware,
            store=STORE,
            realm=AUTH_REALM,
            public_paths=(SRV["health_path"],),
            setup_paths=(ADMIN_PATH, ADMIN_SAVE_PATH, ADMIN_TEST_PATH),
        )

    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(scope):
        task = asyncio.create_task(_fetcher_loop()) if FETCH.get("enabled") else None
        try:
            async with inner(scope):
                yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app.router.lifespan_context = lifespan
    return app


if __name__ == "__main__":
    for directory in (KLINES_DIR, BIAS_DIR, JOURNAL_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    _seed_settings()

    if not AUTH_ENABLED:
        print("CANH BAO: auth dang TAT, server khong xac thuc.", file=sys.stderr)
    elif not STORE.has_auth():
        # Chua co mat khau: khong the chet vi nhu vay khong con duong nao dat mat khau.
        # Thay vao do mo trang admin va in token de vao dat.
        print("\n" + "=" * 68, file=sys.stderr)
        print("BAMCP CHUA DUOC CAI DAT - chua co mat khau nao.", file=sys.stderr)
        print(f"  Mo:   https://<domain>{ADMIN_PATH}", file=sys.stderr)
        print(f"  BAMCP SETUP TOKEN: {STORE.setup_token()}", file=sys.stderr)
        print("  Token giu nguyen qua cac lan khoi dong lai, va bi xoa", file=sys.stderr)
        print("  ngay khi ban dat xong mat khau.", file=sys.stderr)
        print("=" * 68 + "\n", file=sys.stderr)

    if _account_enabled():
        try:
            _exchange()      # dung thu adapter de bat loi cau hinh ngay luc khoi dong
            print(f"BAMCP account: {STORE.exchange()['name']} (read-only)", file=sys.stderr)
        except Exception as exc:
            # Khong exit: vao trang admin sua duoc, khac voi thoi con dung bien moi truong
            print(f"CANH BAO: cau hinh san chua dung - {exc}", file=sys.stderr)

    print(f"BAMCP data_root={DATA_ROOT} symbol={FETCH.get('symbol')} "
          f"market={FETCH.get('market')} fetcher={bool(FETCH.get('enabled'))}", file=sys.stderr)

    uvicorn.run(
        build_app(),
        host=SRV["host"],
        port=int(SRV["port"]),
        log_level=SRV.get("log_level", "info"),
        # Chay sau Caddy/nginx: tin X-Forwarded-* de log dung IP that
        proxy_headers=bool(SRV.get("behind_proxy", True)),
        forwarded_allow_ips=SRV.get("forwarded_allow_ips", "*"),
        timeout_graceful_shutdown=10,
    )
