"""BAMCP - Binance Analysis MCP server."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import csv
import functools
import gzip  # TM - #DASHBOARD - Setup dashboard
import hashlib
import inspect
import json
import math
import os
import secrets
import sys
import threading
import time
import uuid  # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
from collections import OrderedDict  # TM - #DASHBOARD - Setup dashboard
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode  # TM - #DASHBOARD - Setup dashboard
from zoneinfo import ZoneInfo

import httpx2 as httpx
import uvicorn
import yaml

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

import admin       # module cuc bo: trang cai dat
import admin_auth  # TM - #ADMIN-LOGIN - Form dang nhap admin
import admin_gann  # TM - #GANN-TW - Gann Time Windows: trang admin Gann
import admin_dashboard  # TM - #DASHBOARD - Setup dashboard
import admin_entry  # TM - #ENTRY-ALERT - Entry setups & alerts
import admin_schedules  # TM - #M5-FETCH - Per-timeframe schedules + M5
import cron_utc  # TM - #M5-FETCH - Per-timeframe schedules + M5
import dashboard_auth  # TM - #DASHBOARD - Setup dashboard
import dashboard_logic  # TM - #DASHBOARD - Setup dashboard
import dashboard_store  # TM - #DASHBOARD - Setup dashboard
import dashboard_ui  # TM - #DASHBOARD - Setup dashboard
import entry_logic  # TM - #ENTRY-ALERT - Entry setups & alerts
import entry_store  # TM - #ENTRY-ALERT - Entry setups & alerts
import entry_watcher  # TM - #ENTRY-ALERT - Entry setups & alerts
import exchanges   # module cuc bo: adapter doc tai khoan san
import fetch_schedules  # TM - #M5-FETCH - Per-timeframe schedules + M5
import gann_backtest  # TM - #GANN-TW - Gann Time Windows: backtest walk-forward
import gann_levels  # TM - #GANN-PL - Gann Price Levels: Square of 9, quat, cham/phan ung
import gann_pivots  # TM - #GANN-TW - Gann Time Windows: pivot swing chart & trang thai song
import gann_windows  # TM - #GANN-TW - Gann Time Windows: chieu thoi gian & cua so
import klines_coverage  # TM - #GANN-TW - Gann Time Windows: do phu & loc theo thoi gian
import kline_store  # TM - #KL-SQL - Kline storage SQLite
import notifier  # TM - #ENTRY-ALERT - Entry setups & alerts
import off_plan_logic  # TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
import off_plan_store  # TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
import off_plan_watcher  # TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
import settings    # module cuc bo: luu credential xuong volume
import tr_logic  # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
# TM - #ORB - ORB Enhancement
import admin_orb     # module cuc bo: trang quan ly phien ORB
import orb           # module cuc bo: logic ORB thuan (OR, tin hieu, sizing)
import orb_backtest  # module cuc bo: backtest ORB tren M5 lich su
import orb_history   # module cuc bo: M5 lich su tu data.binance.vision
import orb_runtime   # module cuc bo: session store, state theo ngay, scheduler ORB
import orb_rules     # TM - #ORB-RULES - ORB Rule Set: bo rule ORB rieng (logic thuan)
from orb import iso_utc, iso_vn

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
# TM - #GANN-TW - Gann Time Windows: retention rieng tung khung
RETENTION: dict[str, Any] = CFG.get("retention") or {}
FETCH = CFG.get("fetcher", {"enabled": False})
# TM - #KL-SQL - Kline storage SQLite
STORAGE: dict[str, Any] = CFG.get("storage") or {}
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
# TM - #KL-SQL - Kline storage SQLite
SQLITE_PATH = Path(str(STORAGE.get("sqlite_path") or "bamcp.db")).expanduser()
if not SQLITE_PATH.is_absolute():
    SQLITE_PATH = DATA_ROOT / SQLITE_PATH
KLINE_STORE_KIND = str(STORAGE.get("kline_store") or "json").strip().lower()
if KLINE_STORE_KIND not in ("json", "sqlite"):
    raise SystemExit(f"storage.kline_store phai la json hoac sqlite, nhan duoc {KLINE_STORE_KIND!r}")

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


# ---------------------------------------------------------------- symbols

DEFAULT_SYMBOL = str(FETCH.get("symbol") or "BTCUSDT").upper()


def _symbols() -> list[str]:
    """Cac cap dang bat. Luon co it nhat mot cap."""
    return STORE.enabled_symbols(DEFAULT_SYMBOL) or [DEFAULT_SYMBOL]


def _resolve_symbol(symbol: str = "") -> str:
    """Bo trong = cap dau tien dang bat. Giu duoc cach goi cu khong co symbol."""
    if not symbol:
        active = _symbols()
        return active[0] if active else DEFAULT_SYMBOL
    symbol = symbol.strip().upper()
    known = {r["symbol"] for r in STORE.symbols(DEFAULT_SYMBOL)}
    if symbol not in known:
        raise ValueError(
            f"chua theo doi cap {symbol}. Them no trong trang admin truoc, "
            f"hoac chon mot trong: {sorted(known)}")
    return symbol


def _kline_dir(symbol: str) -> Path:
    return KLINES_DIR / symbol.upper()


def _bias_dir(symbol: str) -> Path:
    return BIAS_DIR / symbol.upper()


def _migrate_flat_layout() -> None:
    """Chuyen bo cuc cu (mot cap) sang bo cuc moi (nhieu cap).

    Ban cu dat file thang trong klines/ va bias/. Bo cuc moi tach theo cap:
    klines/<SYMBOL>/4h.json. Chay mot lan, im lang neu khong co gi de chuyen.
    """
    moved = 0
    target = _kline_dir(DEFAULT_SYMBOL)
    for tf in KL["timeframes"]:
        old = KLINES_DIR / PATHS["kline_filename"].format(timeframe=tf)
        if old.is_file():
            target.mkdir(parents=True, exist_ok=True)
            old.replace(target / old.name)
            moved += 1

    bias_target = _bias_dir(DEFAULT_SYMBOL)
    for old in BIAS_DIR.glob("*.json") if BIAS_DIR.exists() else []:
        if old.is_file():
            bias_target.mkdir(parents=True, exist_ok=True)
            old.replace(bias_target / old.name)
            moved += 1

    if moved:
        print(f"BAMCP: da chuyen {moved} file sang bo cuc theo cap "
              f"({DEFAULT_SYMBOL})", file=sys.stderr)


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

# HAI PHAM VI.
#
# Rule ve VON va KY LUAT NGAY dung CHUNG cho ca tai khoan: ban chi co mot tai
# khoan, nen them cap giao dich khong duoc phep nhan ngan sach rui ro len.
# Het quota la het, du cham vao cap nao.
#
# Rule do bang DIEM thi thuoc TUNG CAP: 300 diem tren BTC (~0.3%) va 300 diem
# tren ADA khong phai cung mot thu. Dung chung mot con so cho moi cap la bat
# BTC choi qua chat con cac cap gia thap thi khong co rao nao ca.
GLOBAL_RULE_KEYS = (
    "max_trades_per_day",
    "max_margin_per_trade",
    "daily_stop_loss",
    "swing_max_margin_per_trade",
)

SYMBOL_RULE_KEYS = (
    "max_stop_points",
    "min_take_profit_points",
    "swing_min_take_profit_points",
    "swing_max_stop_points",
    # TM - #MIN-RR - min_rr theo cap: R:R toi thieu. Khong do bang diem nhung van
    # theo cap - moi cap mot nhip gia, mot ty le dang de choi rieng.
    "min_rr",
)

# TM - #MIN-RR - min_rr theo cap: mac dinh khi cap chua dat rieng. Dat ca trong
# code phong config.yaml cu (BAMCP_CONFIG tro ra ngoai image) chua co key nay -
# thieu thi _evaluate_trade khong cham duoc lenh nao.
DEFAULT_MIN_RR = 1.5
RULES_SEED.setdefault("min_rr", DEFAULT_MIN_RR)

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

# TM - #MIN-RR - min_rr theo cap: phai > 0, khong chi >= 0 - min_rr = 0 la tat
# rule ma khong noi ra.
_RULE_POSITIVE = ("min_rr",)

# TM - #ORB-RULES - ORB Rule Set: rules.json co the bi ghi tu nhieu duong (tool,
# admin, migrate lan dau) - khoa de khong mat thay doi / khong migrate hai lan.
_RULES_LOCK = threading.RLock()


def _rules_doc() -> dict[str, Any]:
    """Doc nguyen file rules.json, chuan hoa thanh ba khoi: values / symbols / history.

    `values` = rule chung + gia tri MAC DINH cho cac rule theo cap. Cap nao chua
    duoc dat rieng thi ke thua tu day, nen file cua ban cu (chi co `values`) van
    chay dung: moi cap deu nhan dung bo so truoc kia.

    `symbols` = {"ETHUSDT": {chi cac key thuoc SYMBOL_RULE_KEYS}}. Chi luu phan
    DAT RIENG, khong sao chep ca bo - de sau nay doi mac dinh thi cap nao chua
    dat rieng van duoc keo theo.
    """
    stored = _read_json(RULES_FILE, None)
    if not isinstance(stored, dict):
        stored = {}

    values = stored.get("values")
    if not isinstance(values, dict):
        values = {}

    raw_symbols = stored.get("symbols")
    if not isinstance(raw_symbols, dict):
        raw_symbols = {}
    symbols: dict[str, dict[str, Any]] = {}
    for sym, override in raw_symbols.items():
        if not isinstance(override, dict):
            continue
        kept = {k: v for k, v in override.items() if k in SYMBOL_RULE_KEYS}
        if kept:
            symbols[str(sym).strip().upper()] = kept

    history = stored.get("history")
    if not isinstance(history, list):
        history = []

    # TM - #ORB-RULES - ORB Rule Set: khoi `orb` (bo rule ORB rieng). Chua co thi
    # migrate MOT LAN tu config.yaml, ghi lai ngay kem mot dong history (BR muc 6).
    if not isinstance(stored.get("orb"), dict):
        with _RULES_LOCK:
            again = _read_json(RULES_FILE, None)
            if isinstance(again, dict) and isinstance(again.get("orb"), dict):
                return _rules_doc()
            orb_cfg = CFG.get("orb") or {}
            block, row = orb_rules.migrate(
                orb_cfg, str(orb_cfg.get("symbol") or DEFAULT_SYMBOL).upper(), at=_now_iso())
            history = history + [row]
            stored = {**stored, "values": values, "symbols": symbols,
                      "updated_at": _now_iso(), "history": history, "orb": block}
            _write_json(RULES_FILE, stored)
            print("BAMCP: da tao khoi orb trong rules.json (migrate tu config.yaml)",
                  file=sys.stderr)

    return {"values": {**RULES_SEED, **values}, "symbols": symbols,
            "history": history,
            # TM - #ORB-RULES - ORB Rule Set
            "stored_values": values,
            "orb": orb_rules.normalize_block(stored["orb"])}


def _load_rules(symbol: str = "") -> dict[str, Any]:
    """Bo rule co hieu luc, doc moi lan goi va khong cache.

    symbol bo trong = rule chung + gia tri mac dinh cua cac rule theo cap.
    Dien symbol = ban day du de cham mot lenh tren dung cap do.
    """
    doc = _rules_doc()
    if not symbol:
        return doc["values"]
    return {**doc["values"], **doc["symbols"].get(symbol.strip().upper(), {})}


def _symbol_overrides() -> dict[str, dict[str, Any]]:
    """Cac cap dang dat rieng, kem dung nhung key duoc dat."""
    return _rules_doc()["symbols"]


def _rules_history(limit: int = 0) -> list[dict[str, Any]]:
    history = _rules_doc()["history"]
    return history[-limit:] if limit else history


def _validate_rule_changes(changes: dict[str, Any], symbol: str = "") -> dict[str, float]:
    """Tra ve int cho gia tri nguyen, float cho gia tri le.

    Co symbol thi chi nhan rule thuoc pham vi cap. Doi rule chung ma kem symbol
    la bao loi chu khong am tham ghi vao mot cap - nguoi dung tuong minh vua siet
    quota ma thuc ra khong siet gi ca la kieu hong te nhat.
    """
    if not changes:
        raise ValueError("changes rong, khong co gi de doi")

    clean: dict[str, float] = {}
    for key, raw in changes.items():
        if key not in RULES_SEED:
            raise ValueError(
                f"rule khong ton tai: {key}. Cho phep: {sorted(RULES_SEED)}")
        if symbol and key not in SYMBOL_RULE_KEYS:
            raise ValueError(
                f"{key} dung chung cho moi cap - goi lai khong kem symbol. "
                f"Rule dat rieng theo cap: {sorted(SYMBOL_RULE_KEYS)}")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{key} phai la so, nhan duoc {type(raw).__name__}")
        value = float(raw)
        # TM - #MIN-RR - min_rr theo cap
        if key in _RULE_POSITIVE and value <= 0:
            raise ValueError(f"{key} phai > 0, nhan duoc {value}")
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


def _apply_rule_changes(changes: dict[str, Any], reason: str,
                        symbol: str = "", source: str = "mcp") -> dict[str, Any]:
    """Duong DUY NHAT de doi rule. Ca tool update_rules lan trang admin deu di qua day.

    Co mot cua thi lich su khong bao gio thung: khong co cach nao doi rule ma
    khong de lai dau, du doi tu Claude hay tu trinh duyet.

    symbol bo trong  -> ghi vao `values`: rule chung, va mac dinh cho moi cap
                        chua dat rieng.
    symbol co gia tri -> ghi vao `symbols[SYM]`: chi cap do doi, cac cap khac
                        khong bi dong toi.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("reason la bat buoc - ghi ro vi sao doi rule")

    # Cap phai dang duoc theo doi. Go sai ten thi bao ngay, con hon ghi mot bo
    # rule cho mot cap khong ton tai roi thac mac sao no khong co tac dung.
    sym = _resolve_symbol(symbol) if symbol else ""

    clean = _validate_rule_changes(changes, sym)
    current = _load_rules(sym)
    diff = {k: {"from": current.get(k), "to": v}
            for k, v in clean.items() if current.get(k) != v}
    if not diff:
        return {"updated": False, "reason": "gia tri moi trung gia tri cu",
                "scope": sym or "chung", "symbol": sym or None, "rules": current}

    with _RULES_LOCK:
        doc = _rules_doc()
        history = doc["history"]
        entry: dict[str, Any] = {"at": _now_iso(), "changes": diff, "reason": reason,
                                 # TM - #ORB-RULES - ORB Rule Set: nguon admin / mcp (BR-ORB-13)
                                 "source": source}
        if sym:
            entry["symbol"] = sym
        history.append(entry)

        values = doc["values"]
        symbols = doc["symbols"]
        if sym:
            symbols[sym] = {**symbols.get(sym, {}), **clean}
        else:
            values = {**values, **clean}

        _write_json(RULES_FILE, {
            "values": values,
            "symbols": symbols,
            "updated_at": _now_iso(),
            "history": history,
            "orb": doc["orb"],      # TM - #ORB-RULES - ORB Rule Set: giu khoi orb
        })
    return {
        "updated": True,
        "scope": sym or "chung",
        "symbol": sym or None,
        "changes": diff,
        "reason": reason,
        "rules": {**values, **symbols.get(sym, {})} if sym else values,
        "changed_today": len(_changed_today(history, _today())),
    }


# ---------------------------------------------------------------- rule ORB
# TM - #ORB-RULES - ORB Rule Set
#
# Khoi `orb` cua rules.json: bo rule rieng cho lenh ORB, tach khoi rule
# scalp/swing (BR-ORB-01). Ba lop, xet tung truong: override cua phien (chi nhom
# orb_rules.SESSION_FIELDS, luu trong session store) > symbol_overrides.<SYM> >
# defaults. Moi thay doi di qua _apply_orb_rule_changes - tool
# update_rules(orb=true) lan trang /admin/orb - nen history khong bao gio thung.

ORB_RULE_SOURCES = ("mcp", "admin")


def _orb_rule_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not sym:
        return ""
    # Cap ORB dang chay luon hop le; cap khac thi phai dang duoc theo doi
    return sym if sym == ORB_SYMBOL else _resolve_symbol(sym)


def _orb_session_values(session_values: dict[str, dict[str, Any]] | None,
                        session: dict[str, Any]) -> dict[str, Any]:
    """Override rule cua phien: ban dang xem truoc (neu co) hoac ban dang luu."""
    sid = session["session_id"]
    if session_values and sid in session_values:
        return session_values[sid]
    return orb_runtime.OrbService.session_rule_values(session)


def _orb_consistency(block: dict[str, Any], symbol: str = "",
                     session_values: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Rang buoc giua cac truong (min < max OR/ATR, time_exit <= cua so phien) tren
    moi to hop se dung that: tung cap co override + cap ORB x tung phien."""
    errors: list[str] = []
    symbols = {ORB_SYMBOL, *block["symbol_overrides"], *([symbol] if symbol else [])}
    for sym in sorted(symbols):
        for err in orb_rules.consistency(orb_rules.resolve(block, sym)["values"]):
            errors.append(f"{sym}: {err}")
    if ORB_SESSIONS is not None and ORB_SVC is not None:
        for session in ORB_SESSIONS.all(include_disabled=True):
            resolved = orb_rules.resolve(block, ORB_SYMBOL,
                                         session_values=_orb_session_values(session_values, session))
            for err in orb_rules.consistency(resolved["values"], ORB_SVC.window_minutes(session)):
                errors.append(f"phien {session['session_id']}: {err}")
    return list(dict.fromkeys(errors))


def _orb_warnings(block: dict[str, Any] | None = None,
                  session_values: dict[str, dict[str, Any]] | None = None,
                  snapshot: dict[str, Any] | None = None) -> list[str]:
    """Canh bao rule_infeasible / liq_unsafe theo ATR H1 + gia hien tai (BR-ORB-11):
    cho cap ORB, roi tung phien dang bat co override rule (tien to [session_id])."""
    if ORB_SVC is None:
        return []
    block = block if block is not None else ORB_SVC.rules_block()
    snap = snapshot or ORB_SVC.market_snapshot()
    atr, price, costs = snap.get("atr_h1"), snap.get("price"), ORB_SVC.costs()
    if not atr:
        return []
    out = orb_rules.infeasible(orb_rules.resolve(block, ORB_SYMBOL)["values"], atr, price, costs)
    for session in ORB_SESSIONS.all(include_disabled=False):
        values = _orb_session_values(session_values, session)
        if not values:
            continue
        resolved = orb_rules.resolve(block, ORB_SYMBOL, session_values=values)["values"]
        for warning in orb_rules.infeasible(resolved, atr, price, costs):
            if warning not in out:
                out.append(f"[{session['session_id']}] {warning}")
    return out


def _orb_rule_path(layer: str, field: str, symbol: str, session_id: str) -> str:
    if field == "enabled":
        return "orb.enabled"
    if layer == "symbol":
        return f"orb.symbol_overrides.{symbol}.{field}"
    if layer == "session":
        return f"orb.sessions.{session_id}.{field}"
    return f"orb.defaults.{field}"


def _apply_orb_rule_changes(changes: dict[str, Any], reason: str, symbol: str = "",
                            session_id: str = "", source: str = "mcp",
                            dry_run: bool = False) -> dict[str, Any]:
    """Duong DUY NHAT de doi rule ORB (BR-ORB-13): update_rules(orb=true) va /admin/orb.

    symbol bo trong + session_id bo trong -> orb.defaults (va cong tac orb.enabled)
    symbol                                -> orb.symbol_overrides.<SYMBOL>
    session_id                            -> override cua phien (chi SESSION_FIELDS)
    Gia tri None / "" = bo override o lop do (ke thua lop duoi). Moi truong doi la
    mot dong history. dry_run = chi xem truoc (bo rule da giai + canh bao), khong ghi.
    """
    if source not in ORB_RULE_SOURCES:
        raise ValueError(f"source phai la mot trong {list(ORB_RULE_SOURCES)}")
    reason = (reason or "").strip()
    if not reason and not dry_run:
        raise ValueError("reason la bat buoc - ghi ro vi sao doi rule ORB")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("changes rong, khong co gi de doi")
    sid = (session_id or "").strip().lower()
    sym = _orb_rule_symbol(symbol)
    if sid and sym:
        raise ValueError("chon mot pham vi: symbol HOAC session_id, khong ca hai")
    layer = "session" if sid else "symbol" if sym else "defaults"
    session = None
    if sid:
        if ORB_SESSIONS is None:
            raise ValueError("module ORB dang tat - khong co phien de dat override")
        session = ORB_SESSIONS.get(sid)
        if not session:
            raise ValueError(f"khong co phien '{sid}'. Dang co: {ORB_SESSIONS.ids()}")

    with _RULES_LOCK:
        doc = _rules_doc()
        block = copy.deepcopy(doc["orb"])
        if session is not None:
            current = orb_runtime.OrbService.session_rule_values(session)
        elif sym:
            current = dict(block["symbol_overrides"].get(sym, {}))
        else:
            current = dict(block["defaults"])
        layer_values = dict(current)
        enabled = block["enabled"]
        for key, raw in changes.items():
            key = str(key).strip()
            if key == "enabled":
                if layer != "defaults":
                    raise ValueError("orb.enabled la cong tac chung - doi khong kem "
                                     "symbol / session_id")
                enabled = orb_rules.coerce_bool("enabled", raw)
                continue
            if key not in orb_rules.FIELDS:
                raise ValueError(f"truong ORB khong ho tro: {key}. Cho phep: "
                                 f"{['enabled', *orb_rules.FIELDS]}")
            if layer == "session" and key not in orb_rules.SESSION_FIELDS:
                raise ValueError(f"orb.{key} khong dat theo phien duoc (chi theo cap hoac "
                                 f"defaults). Phien chi ghi de: {list(orb_rules.SESSION_FIELDS)}")
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                if layer == "defaults" and key in orb_rules.DEFAULTS:
                    raise ValueError(f"orb.defaults.{key} khong xoa duoc - truong nay "
                                     "phai luon co gia tri mac dinh")
                layer_values.pop(key, None)
                continue
            layer_values[key] = orb_rules.coerce(key, raw)

        diff: dict[str, dict[str, Any]] = {}
        if enabled != block["enabled"]:
            diff["enabled"] = {"from": block["enabled"], "to": enabled}
        for key in orb_rules.FIELDS:
            if current.get(key) != layer_values.get(key):
                diff[key] = {"from": current.get(key), "to": layer_values.get(key)}

        block["enabled"] = enabled
        if layer == "defaults":
            block["defaults"] = layer_values
        elif layer == "symbol":
            if layer_values:
                block["symbol_overrides"][sym] = layer_values
            else:
                block["symbol_overrides"].pop(sym, None)
        preview_sessions = {sid: layer_values} if sid else None
        errors = _orb_consistency(block, sym, preview_sessions)
        if errors:
            raise ValueError("rule ORB khong hop le: " + "; ".join(errors))

        resolved = orb_rules.resolve(block, sym or ORB_SYMBOL,
                                     session_values=layer_values if sid else None,
                                     session_id=sid or None)
        base = {"layer": layer, "symbol": sym or None, "session_id": sid or None,
                "changes": diff, "resolved": resolved,
                "warnings": _orb_warnings(block, preview_sessions)}
        if dry_run:
            return {"updated": False, "dry_run": True, **base}
        if not diff:
            return {"updated": False, "reason": "gia tri moi trung gia tri cu", **base}

        at = _now_iso()
        rows = []
        for field, change in diff.items():
            row: dict[str, Any] = {
                "at": at, "scope": "orb", "layer": layer, "field": field,
                "from": change["from"], "to": change["to"],
                "changes": {_orb_rule_path(layer, field, sym, sid): change},
                "reason": reason, "source": source}
            if sym:
                row["symbol"] = sym
            if sid:
                row["session_id"] = sid
            rows.append(row)
        if session is not None:
            # Override rule cua phien nam trong session store (cung cho voi tham so
            # ky thuat cua phien); validator cua OrbService kiem them min < max.
            flat = orb.normalize_overrides(session.get("overrides"), orb.SESSION_OVERRIDE_KEYS)
            flat = {k: v for k, v in flat.items() if k not in orb.RULE_PARAM_KEYS}
            ORB_SESSIONS.save({**session, "overrides": {**flat, **layer_values}},
                              original_id=sid)
        history = doc["history"] + rows
        _write_json(RULES_FILE, {
            "values": doc["stored_values"],
            "symbols": doc["symbols"],
            "updated_at": at,
            "history": history,
            "orb": block,
        })
    ORB_LOG("rules_change", scope="orb", layer=layer, symbol=sym or None,
            session_id=sid or None, fields=list(diff), source=source)
    if ORB_SCHEDULER is not None:
        ORB_SCHEDULER.wake()       # vd bat/tat orb.enabled -> lich watch doi ngay
    return {"updated": True, **base, "reason": reason, "source": source,
            "history_rows": len(rows),
            "changed_today": len(_changed_today(history, _today()))}


# ---------------------------------------------------------------- klines

def _on_demand_timeframes() -> list[str]:
    """Khung chi keo khi goi thu cong hoac trong giai doan watch ORB (vd 5m).

    Fetcher nen khong bao gio keo cac khung nay - BR-03.
    """
    # TM - #ORB - ORB Enhancement
    return [str(tf).strip().lower() for tf in (KL.get("on_demand_timeframes") or [])
            if str(tf).strip().lower() not in KL["timeframes"]]


def _validate_timeframe(timeframe: str) -> str:
    tf = timeframe.strip().lower()
    # TM - #ORB - ORB Enhancement: nhan them khung on-demand (5m)
    allowed = list(KL["timeframes"]) + _on_demand_timeframes()
    # TM - #M5-FETCH - Per-timeframe schedules + M5
    allowed += [t for t in SUPPORTED_TIMEFRAMES if t not in allowed]
    if tf not in allowed:
        raise ValueError(f"timeframe khong hop le: {timeframe}. Cho phep: {allowed}")
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


def _load_bars(symbol: str, timeframe: str) -> list[dict[str, float]]:
    sym = _resolve_symbol(symbol)
    tf = _validate_timeframe(timeframe)
    # TM - #KL-SQL - Kline storage SQLite
    bars = KLINE_STORE.get_klines(sym, tf)
    if not bars:
        raise FileNotFoundError(
            f"Chua co du lieu {symbol} khung {tf}. Doi mot chu ky pull, "
            f"hoac goi refresh_data({symbol!r}).")
    return bars


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


def _build_context(symbol: str, timeframe: str) -> dict[str, Any]:
    all_bars = _load_bars(symbol, timeframe)
    if not all_bars:
        return {"symbol": symbol, "timeframe": timeframe, "error": "khong co du lieu"}

    bars, forming = _split_closed(all_bars, timeframe)
    if not bars:
        return {"symbol": symbol, "timeframe": timeframe, "error": "chua co nen nao dong"}

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
        "symbol": symbol,
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
# TM - #M5-FETCH - Per-timeframe schedules + M5
FETCH_MODE = str(FETCH.get("mode") or "schedules").strip().lower()
if FETCH_MODE not in ("schedules", "interval"):
    raise SystemExit(f"fetcher.mode phai la schedules hoac interval, nhan duoc {FETCH_MODE!r}")
SUPPORTED_TIMEFRAMES: list[str] = [str(t).strip() for t in (
    FETCH.get("supported_timeframes") or ["5m", *KL["timeframes"]])]
_FETCH_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _fetch_lock(symbol: str, timeframe: str) -> asyncio.Lock:
    return _FETCH_LOCKS.setdefault((symbol.upper(), timeframe), asyncio.Lock())


@contextlib.asynccontextmanager
async def _fetch_locks(keys: list[tuple[str, str]]):
    async with contextlib.AsyncExitStack() as stack:
        for symbol, timeframe in sorted({(s.upper(), t) for s, t in keys}):
            await stack.enter_async_context(_fetch_lock(symbol, timeframe))
        yield


def _binance_url() -> str:
    key = "base_url_spot" if FETCH.get("market") == "spot" else "base_url_futures"
    return FETCH[key]


def _kline_path(symbol: str, timeframe: str) -> Path:
    return _kline_dir(symbol) / PATHS["kline_filename"].format(timeframe=timeframe)


# TM - #GANN-TW - Gann Time Windows
def _retention_for(timeframe: str) -> int | None:
    """So nen toi da giu cho mot khung. None = khong cat.

    Khung co khai trong `retention` thi theo khai bao do (ke ca null); khong khai
    thi theo klines.max_history nhu truoc. Pivot Gann can toan bo lich su 1d/1w
    tu 2019-09 (~2600 nen D), ma max_history mac dinh la 1000 - de nguyen la
    moi lan fetch se cat mat phan backfill.
    """
    tf = timeframe.strip().lower()
    if tf in RETENTION:
        value = RETENTION[tf]
        return None if value is None else max(1, int(value))
    return int(FETCH.get("max_history", 1000)) or None


def _merge_bars(old: list[Any], new: list[Any], cap: int | None) -> list[Any]:
    """Gop theo openTime, nen moi ghi de nen cu cung moc thoi gian."""
    merged: dict[int, Any] = {int(r[0]): r for r in old if isinstance(r, (list, tuple)) and r}
    for row in new:
        merged[int(row[0])] = row
    ordered = [merged[k] for k in sorted(merged)]
    return ordered[-cap:] if cap else ordered


# TM - #KL-SQL - Kline storage SQLite
KLINE_JSON = kline_store.JsonKlineStore(
    KLINES_DIR, path_fn=_kline_path, normalize=_normalize_bars, merge=_merge_bars,
    retention=_retention_for, read_json=_read_json, write_json=_write_json,
    timeframes=lambda: list(KL["timeframes"]) + _on_demand_timeframes())
_KLINE_SQLITE: kline_store.SqliteKlineStore | None = None


def _kline_sqlite() -> kline_store.SqliteKlineStore:
    global _KLINE_SQLITE
    if _KLINE_SQLITE is None:
        _KLINE_SQLITE = kline_store.SqliteKlineStore(SQLITE_PATH, span_ms=_tf_ms)
    return _KLINE_SQLITE


def _kline_store_for(kind: str) -> kline_store.IKlineStore:
    return _kline_sqlite() if kind == "sqlite" else KLINE_JSON


KLINE_STORE: kline_store.IKlineStore = _kline_store_for(STORE.kline_store() or KLINE_STORE_KIND)


def _kline_coverage(symbol: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    return {(c["symbol"], c["tf"]): c for c in KLINE_STORE.get_coverage(symbol)}


def _kline_migrate(action: str) -> dict[str, Any]:
    action = (action or "").strip().lower()
    if action == "import":
        return {"action": action, "sqlite_path": str(SQLITE_PATH),
                **kline_store.import_json(KLINE_JSON, _kline_sqlite())}
    if action == "verify":
        return {"action": action, "sqlite_path": str(SQLITE_PATH),
                **kline_store.verify(KLINE_JSON, _kline_sqlite(), span_ms=_tf_ms)}
    raise ValueError("action phai la import hoac verify")


def _kline_sync(kind: str) -> dict[str, Any]:
    sqlite = _kline_sqlite()
    copied = (kline_store.import_json(KLINE_JSON, sqlite) if kind == "sqlite"
              else kline_store.export_json(sqlite, KLINE_JSON))
    report = kline_store.verify(KLINE_JSON, sqlite, span_ms=_tf_ms)
    if kind == "sqlite":
        bad = [p for p in report["pairs"] if p["missing_in_dst"] or p["ohlcv_mismatch"]]
    else:
        bad = [p for p in report["pairs"] if p["count"][1] and (
            p["ohlcv_mismatch"] or p["last_open_time"][0] != p["last_open_time"][1])]
    return {"ok": not bad, "copied": {"pairs": copied["pairs"], "rows_written": copied["rows_written"]},
            "mismatched": [{k: p[k] for k in ("symbol", "tf", "count", "last_open_time",
                                              "missing_in_dst", "ohlcv_mismatch")} for p in bad]}


async def _kline_switch(kind: str) -> dict[str, Any]:
    global KLINE_STORE
    kind = str(kind or "").strip().lower()
    if kind not in ("json", "sqlite"):
        raise ValueError("kline_store phai la json hoac sqlite")
    if kind == KLINE_STORE.kind:
        return {"ok": True, "kind": kind, "changed": False}
    keys = [(c["symbol"], c["tf"]) for c in KLINE_STORE.get_coverage()]
    async with _fetch_locks(keys):
        result = await asyncio.to_thread(_kline_sync, kind)
        if not result["ok"]:
            return {**result, "kind": KLINE_STORE.kind, "changed": False}
        STORE.set_kline_store(kind)
        KLINE_STORE = _kline_store_for(kind)
    print(f"BAMCP kline_store -> {kind}", file=sys.stderr)
    return {**result, "kind": kind, "changed": True}


async def _fetch_one(client: httpx.AsyncClient, symbol: str,
                     timeframe: str, limit: int | None = None,
                     source: str = "manual") -> dict[str, Any]:
    params = {
        "symbol": symbol,
        "interval": timeframe,
        # TM - #ORB - ORB Enhancement: watch ORB chi can vai chuc nen M5
        "limit": int(limit or FETCH["fetch_limit"]),
    }
    # TM - #ORB - ORB Enhancement: moi request M5 deu de lai dau vet (TC-31)
    if timeframe == "5m":
        ORB_LOG("m5_request", symbol=symbol, limit=params["limit"], source=source)
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

            # TM - #KL-SQL - Kline storage SQLite
            stored = KLINE_STORE.upsert(symbol, timeframe, rows)
            return {"ok": True, "fetched": len(rows), "total": stored["total"],
                    "at": _now_iso()}
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:          # khong ngu sau lan thu cuoi
                await asyncio.sleep(2 ** attempt)
    return {"ok": False, "error": str(last_exc), "at": _now_iso()}


async def probe_symbol(symbol: str) -> None:
    """Hoi Binance xem cap nay co that khong. Nem ValueError neu khong.

    Dung luc them cap trong trang admin: thay vi giu mot danh sach cung se lac
    hau, hoi thang san. Cap nao Binance co la dung duoc.
    """
    symbol = symbol.strip().upper()
    params = {"symbol": symbol, "interval": "1h", "limit": 1}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(_binance_url(), params=params,
                                    timeout=float(FETCH.get("request_timeout", 20)))
        except Exception as exc:
            raise ValueError(f"khong goi duoc Binance ({type(exc).__name__})") from None
    if resp.status_code >= 400:
        raise ValueError(
            f"Binance khong co cap {symbol} tren thi truong "
            f"{FETCH.get('market', 'futures')} (HTTP {resp.status_code})")
    rows = resp.json()
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"Binance tra ve du lieu rong cho {symbol}")


async def fetch_all(symbols: list[str] | None = None,
                    timeframes: list[str] | None = None,
                    source: str = "manual") -> dict[str, Any]:
    """Mot lan pull duy nhat tai mot thoi diem, du goi tu MCP tool hay vong lap nen.

    Chay song song nhung co gioi han: them nhieu cap ma ban het cung luc thi de
    dinh rate limit cua Binance.
    """
    target_symbols = [s.upper() for s in (symbols or _symbols())]
    target_tfs = [tf.lower() for tf in (timeframes or KL["timeframes"])]
    gate = asyncio.Semaphore(int(FETCH.get("max_concurrent_requests", 8)))

    async def one(client, symbol, tf):
        # TM - #M5-FETCH - Per-timeframe schedules + M5
        async with _fetch_lock(symbol, tf), gate:
            # TM - #ORB - ORB Enhancement: ghi nguon goi de doi chieu request M5
            return (symbol, tf, await _fetch_one(client, symbol, tf, source=source))

    async with httpx.AsyncClient() as client:
        done = await asyncio.gather(*[
            one(client, s, tf) for s in target_symbols for tf in target_tfs
        ])

    results: dict[str, dict[str, Any]] = {s: {} for s in target_symbols}
    failed: list[str] = []
    for symbol, tf, outcome in done:
        results[symbol][tf] = outcome
        if not outcome.get("ok"):
            failed.append(f"{symbol}/{tf}")

    FETCH_STATE["last_run"] = _now_iso()
    FETCH_STATE["results"] = results
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
            await fetch_all(source="fetcher")    # TM - #ORB - ORB Enhancement
        except asyncio.CancelledError:
            raise                      # tat server: de no thoat that
        except Exception as exc:
            FETCH_STATE["last_error"] = f"{type(exc).__name__}: {exc}"
            print(f"BAMCP fetcher loi: {FETCH_STATE['last_error']}", file=sys.stderr)


# TM - #M5-FETCH - Per-timeframe schedules + M5
SCHEDULE_PAGE_LIMIT = 1000
SCHEDULE_MAX_PAGES = 20
SCHEDULE_LATE_RETRIES = 3
SCHEDULE_LATE_DELAY = 3.0


async def _binance_rows(client: httpx.AsyncClient, params: dict[str, Any],
                        source: str) -> list[Any]:
    if params.get("interval") == "5m":
        ORB_LOG("m5_request", symbol=params["symbol"], limit=params["limit"], source=source)
    retries = max(1, int(FETCH.get("retry", 3)))
    for attempt in range(retries):
        try:
            resp = await client.get(_binance_url(), params=params,
                                    timeout=float(FETCH["request_timeout"]))
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list):
                raise ValueError("Binance tra ve du lieu khong phai list")
            return rows
        except Exception:
            if attempt >= retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    return []


async def _pull_since_latest(client: httpx.AsyncClient, symbol: str, timeframe: str,
                             overlap: int, source: str = "schedule") -> dict[str, Any]:
    span = _tf_ms(timeframe)
    fetched = 0
    newest = None
    expected = None
    for attempt in range(SCHEDULE_LATE_RETRIES + 1):
        now = int(time.time() * 1000)
        expected = cron_utc.candle_open(now, span) - span
        latest = KLINE_STORE.get_latest_open_time(symbol, timeframe)
        params: dict[str, Any] = {"symbol": symbol, "interval": timeframe,
                                  "limit": int(FETCH["fetch_limit"])}
        if latest is not None:
            params.update(limit=SCHEDULE_PAGE_LIMIT,
                          startTime=max(0, latest - max(0, int(overlap)) * span))
        rows: list[Any] = []
        for _ in range(SCHEDULE_MAX_PAGES):
            batch = await _binance_rows(client, params, source)
            rows.extend(batch)
            if latest is None or len(batch) < params["limit"]:
                break
            last_open = int(batch[-1][0])
            if last_open + span > now:
                break
            params = {**params, "startTime": last_open + span}
        if rows:
            KLINE_STORE.upsert(symbol, timeframe, rows)
            fetched += len(rows)
        closed = [int(r[0]) for r in rows if int(r[6]) <= now]
        newest = max(closed + ([latest] if latest is not None else []), default=None)
        if newest is not None and newest >= expected:
            return {"ok": True, "fetched": fetched, "newest_open_time": newest}
        if attempt < SCHEDULE_LATE_RETRIES:
            await asyncio.sleep(SCHEDULE_LATE_DELAY)
    return {"ok": False, "fetched": fetched, "newest_open_time": newest,
            "error": f"nen mo luc {_stamp(expected)} chua co tren Binance sau "
                     f"{SCHEDULE_LATE_RETRIES} lan thu"}


async def _run_schedule(item: dict[str, Any]) -> dict[str, Any]:
    tf = item["timeframe"]
    active = _symbols()
    targets = [s for s in (item["symbols"] or active) if s in active]
    skipped: list[str] = []
    errors: list[str] = []
    gate = asyncio.Semaphore(int(FETCH.get("max_concurrent_requests", 8)))

    async def one(client, symbol):
        lock = _fetch_lock(symbol, tf)
        if lock.locked():
            skipped.append(symbol)
            return
        async with lock, gate:
            try:
                out = await _pull_since_latest(client, symbol, tf, item["overlap_candles"])
            except Exception as exc:
                errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
                return
            if not out["ok"]:
                errors.append(f"{symbol}: {out['error']}")

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*[one(client, s) for s in targets])
    if errors:
        return {"status": "error", "error": "; ".join(errors)}
    if skipped:
        return {"status": "skipped", "error": "luot truoc chua xong: " + ", ".join(skipped)}
    return {"status": "ok", "error": None}


def _schedule_done(item: dict[str, Any], result: dict[str, Any]) -> None:
    FETCH_STATE["last_run"] = _now_iso()
    if result["status"] == "error":
        FETCH_STATE["last_error"] = f"{item['name']} ({_now_iso()}): {result.get('error')}"
        print(f"BAMCP schedule {item['name']} loi: {result.get('error')}", file=sys.stderr)


SCHEDULES = fetch_schedules.ScheduleStore(SQLITE_PATH, supported=lambda: SUPPORTED_TIMEFRAMES,
                                          span_ms=_tf_ms)
FETCH_SCHEDULER = fetch_schedules.FetchScheduler(
    SCHEDULES, _run_schedule, reload_seconds=float(FETCH.get("reload_seconds", 30)),
    on_done=_schedule_done, catch_up=bool(FETCH.get("run_on_start", True)))


def _schedules_for(schedules: list[dict[str, Any]], symbol: str,
                   timeframe: str) -> list[dict[str, Any]]:
    return [{"name": s["name"], "enabled": s["enabled"], "cron": s["cron"],
             "last_run_at": _stamp(s["last_run_at"]) if s["last_run_at"] else None,
             "last_status": s["last_status"]}
            for s in schedules
            if s["timeframe"] == timeframe and (not s["symbols"] or symbol in s["symbols"])]


# ---------------------------------------------------------------- ORB
# TM - #ORB - ORB Enhancement
#
# Logic nam o orb*.py, day chi noi cac ham doc/ghi san co cua server vao do:
# file kline live, bias, journal, fetcher. Tham so chung o section `orb` trong
# config.yaml; danh sach phien o session store do trang admin quan ly (BR-12).

ORB_CFG: dict[str, Any] = CFG.get("orb") or {}
if _env("BAMCP_ORB_ENABLED"):
    ORB_CFG["enabled"] = _env("BAMCP_ORB_ENABLED").lower() in ("1", "true", "yes", "on")
ORB_ENABLED = bool(ORB_CFG.get("enabled"))


def _orb_path(value: Any, default: str) -> Path:
    """Duong dan trong section orb: tuong doi thi tinh tu data_root."""
    path = Path(str(value or default)).expanduser()
    return path if path.is_absolute() else DATA_ROOT / path


ORB_DATA_CFG = ORB_CFG.get("data") or {}
ORB_SESSIONS_FILE = _orb_path(ORB_CFG.get("sessions_store_path"), "orb/sessions.json")
ORB_NEWS_FILE = _orb_path((ORB_CFG.get("filters") or {}).get("news_days_file"),
                          "orb/news_days.json")
ORB_HISTORY_DIR = _orb_path(ORB_DATA_CFG.get("m5_history_path"), "orb/history")
ORB_BACKTEST_DIR = _orb_path(ORB_DATA_CFG.get("backtest_output_path"), "orb/backtests")
ORB_STATE_DIR = DATA_ROOT / "orb" / "state"
ORB_LOG_DIR = DATA_ROOT / "orb" / "logs"
# Dong skip de rieng: de vao journal chinh thi quota lenh/ngay va
# reconcile_journal se dem nham mot lan bo phien thanh mot lenh.
ORB_SKIPS_DIR = JOURNAL_DIR / "orb_skips"
ORB_SYMBOL = str(ORB_CFG.get("symbol") or DEFAULT_SYMBOL).upper()
ORB_CFG["symbol"] = ORB_SYMBOL
ORB_HISTORY_YEARS = int(ORB_DATA_CFG.get("history_years") or 2)

ORB_LOG = orb_runtime.EventLog(ORB_LOG_DIR)


def _orb_load_live(symbol: str, timeframe: str) -> tuple[list[dict[str, Any]], int | None]:
    """Nen trong file kline live + luc file duoc ghi (ms). Khong co file thi rong."""
    # TM - #KL-SQL - Kline storage SQLite
    try:
        bars = KLINE_STORE.get_klines(symbol, timeframe)
        return bars, (KLINE_STORE.written_at(symbol, timeframe) if bars else None)
    except FileNotFoundError:
        return [], None
    except (OSError, ValueError) as exc:
        ORB_LOG("data_error", symbol=symbol, timeframe=timeframe, error=str(exc))
        return [], None


def _orb_bias(symbol: str, day: str) -> dict[str, Any] | None:
    """Bias da luu bang save_bias - cung nguon voi get_bias."""
    data = _read_json(_bias_dir(symbol) / f"{day}.json", None)
    return data if isinstance(data, dict) else None


def _journal_rows(days: list[str]) -> list[dict[str, Any]]:
    """Cac dong journal cua nhieu ngay, kem ngay cua file (_journal_date)."""
    rows: list[dict[str, Any]] = []
    for day in days:
        data = _read_json(JOURNAL_DIR / f"{day}.json", [])
        for row in data if isinstance(data, list) else []:
            if isinstance(row, dict):
                rows.append({**row, "_journal_date": day})
    return rows


def _is_orb(row: dict[str, Any]) -> bool:
    return str(row.get("strategy") or "").upper() == "ORB"


def _orb_today(day: str) -> dict[str, Any]:
    """Quota + daily stop ORB rieng cua mot ngay (BR-ORB-05/06/09).

    TM - #ORB-RULES - ORB Rule Set. Dem tu nhat ky theo or_date: journal chia
    theo ngay cua server con or_date theo ngay cua phien, nen doc ca cac file
    quanh ngay do (cung cach voi OrbService._orb_rows).
    """
    resolved = orb_rules.resolve(_rules_doc()["orb"], ORB_SYMBOL)
    values = resolved["values"]
    d = orb.parse_date(day)
    rows = [r for r in _journal_rows([(d + timedelta(days=k)).isoformat() for k in (-1, 0, 1, 2)])
            if _is_orb(r) and str(r.get("or_date") or "") == day]
    stats = orb_rules.day_stats(rows)
    cap, stop = values.get("max_trades_per_day"), values.get("daily_stop_loss")
    remaining = max(0, int(cap) - stats["trades"]) if cap is not None else 0
    stop_hit = stop is not None and stats["pnl"] <= float(stop)
    missing = orb_rules.missing_errors(resolved, orb_rules.SCORE_FIELDS)
    enabled = bool(ORB_ENABLED and resolved["enabled"])
    return {
        "symbol": ORB_SYMBOL,
        "or_date": day,
        "counted_by": "or_date",
        "enabled": enabled,
        "module_enabled": ORB_ENABLED,
        "trades_taken": stats["trades"],
        "trades_remaining": remaining,
        "realized_pnl": stats["pnl"],
        "daily_stop_hit": stop_hit,
        "can_trade": bool(enabled and not missing and remaining > 0 and not stop_hit),
        "missing_rules": missing,
        "rules": values,
        "sources": resolved["sources"],
    }


async def _orb_fetch(symbol: str, timeframe: str, limit: int | None,
                     source: str) -> dict[str, Any]:
    """Keo mot khung cho scheduler ORB. Dung chung khoa (cap, khung) voi fetcher nen
    de hai ben khong ghi de cung mot file cung luc."""
    # TM - #M5-FETCH - Per-timeframe schedules + M5
    async with _fetch_lock(symbol, timeframe):
        async with httpx.AsyncClient() as client:
            return await _fetch_one(client, symbol, timeframe, limit=limit, source=source)


ORB_HISTORY = orb_history.HistoryStore(
    ORB_HISTORY_DIR, market=str(FETCH.get("market") or "futures"), log=ORB_LOG)


async def _orb_history_update() -> dict[str, Any]:
    return await asyncio.to_thread(ORB_HISTORY.update, ORB_SYMBOL, ORB_HISTORY_YEARS)


ORB_SESSIONS: orb_runtime.SessionStore | None = None
ORB_SVC: orb_runtime.OrbService | None = None
ORB_SCHEDULER: orb_runtime.OrbScheduler | None = None
ORB_BACKTEST: orb_backtest.BacktestManager | None = None

if ORB_ENABLED:
    ORB_SESSIONS = orb_runtime.SessionStore(
        ORB_SESSIONS_FILE, ORB_CFG.get("default_sessions"), ORB_LOG)
    ORB_SVC = orb_runtime.OrbService(
        cfg=ORB_CFG, sessions=ORB_SESSIONS,
        states=orb_runtime.StateStore(ORB_STATE_DIR), log=ORB_LOG,
        load_live=_orb_load_live, load_history=ORB_HISTORY.load, get_bias=_orb_bias,
        journal_rows=_journal_rows, skips_dir=ORB_SKIPS_DIR, news_file=ORB_NEWS_FILE, tz=TZ,
        # TM - #ORB-RULES - ORB Rule Set: rule ORB doc lai tu rules.json moi lan dung
        rules_fn=lambda: _rules_doc()["orb"], shared_rules_fn=_load_rules)
    ORB_SCHEDULER = orb_runtime.OrbScheduler(
        ORB_SVC, fetch=_orb_fetch, history_update=_orb_history_update,
        history_ready=lambda: bool(ORB_HISTORY.files(ORB_SYMBOL)))
    # Luu phien / log_trade / skip -> scheduler tinh lai lich ngay, khong cho vong sau
    ORB_SVC.notify = ORB_SCHEDULER.wake
    ORB_SESSIONS.listeners.append(ORB_SCHEDULER.wake)
    ORB_BACKTEST = orb_backtest.BacktestManager(
        ORB_BACKTEST_DIR, symbol=ORB_SYMBOL, history=ORB_HISTORY,
        history_years=ORB_HISTORY_YEARS,
        load_live_m5=lambda symbol: _orb_load_live(symbol, "5m")[0],
        log=ORB_LOG, now_fn=orb_runtime.now_ms)


def _orb() -> orb_runtime.OrbService:
    if ORB_SVC is None:
        raise ValueError("ORB dang tat: bat `orb.enabled` trong config.yaml "
                         "(hoac BAMCP_ORB_ENABLED=true) roi restart.")
    return ORB_SVC


def _journal_filter(trades: list[dict[str, Any]], strategy: str = "",
                    session_id: str = "") -> dict[str, Any]:
    """Loc nhat ky theo strategy / phien, kem PnL tach theo strategy va phien (BR-10).

    Dong cu chua co strategy duoc tinh la OTHER.
    """
    strategy = (strategy or "").strip().upper()
    sid = (session_id or "").strip().lower()
    if strategy and strategy not in orb.STRATEGIES:
        raise ValueError(f"strategy phai la mot trong {list(orb.STRATEGIES)}")

    def strat(row: dict[str, Any]) -> str:
        return str(row.get("strategy") or "OTHER").upper()

    picked = [t for t in trades
              if (not strategy or strat(t) == strategy)
              and (not sid or str(t.get("session_id") or "").lower() == sid)]

    def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        closed = [float(t["pnl"]) for t in rows if t.get("pnl") is not None]
        return {"trades": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
                "wins": sum(1 for p in closed if p > 0),
                "losses": sum(1 for p in closed if p < 0),
                "realized_pnl": round(sum(closed), 2)}

    by_strategy: dict[str, list[dict[str, Any]]] = {}
    by_session: dict[str, list[dict[str, Any]]] = {}
    for row in picked:
        by_strategy.setdefault(strat(row), []).append(row)
        if row.get("session_id"):
            by_session.setdefault(str(row["session_id"]), []).append(row)
    return {
        "filter": {"strategy": strategy or None, "session_id": sid or None},
        **summary(picked),
        "by_strategy": {k: summary(v) for k, v in sorted(by_strategy.items())},
        "by_session": {k: summary(v) for k, v in sorted(by_session.items())},
        "rows": picked,
    }


TRADE_TYPES = ("scalp", "swing")


def _evaluate_trade(*, symbol: str, side: str, entry: float, stop: float | None,
                    target: float | None, margin_usd: float, trade_type: str,
                    trades: list[dict[str, Any]], rules: dict[str, Any],
                    realized: float) -> dict[str, Any]:
    """Cham mot lenh theo bo rule dang hieu luc. Khong ghi gi.

    `rules` phai la bo da giai theo dung cap (_load_rules(symbol)): nguong diem
    cua SL/TP va nguong swing khac nhau giua cac cap, con quota va daily stop
    thi dung chung.

    Hai bo han muc: scalp (chat) va swing (rong). Nhung KHONG duoc tu dan nhan
    swing de lach - lenh chi duoc huong han muc swing khi TP thuc su dat nguong
    swing_min_take_profit_points cua CHINH CAP DO. Neu khong, no bi ha xuong
    scalp va ghi lai dieu do. Cai nhan la he qua cua con so, khong phai y muon.

    min_rr cua cap ap cho ca scalp lan swing. Lenh ORB khong di qua ham nay -
    no cham bang orb.min_rr (orb_rules.score).

    TM - #OFF-PLAN: vi the that tren san co the chua co SL/TP. stop / target = None thi bo qua
    cac rule can con so do (SL toi da, TP toi thieu, R:R), van cham quota, daily stop, margin.
    """
    side = side.strip().lower()
    if side not in ("long", "short"):
        raise ValueError("side phai la long hoac short")
    trade_type = (trade_type or "scalp").strip().lower()
    if trade_type not in TRADE_TYPES:
        raise ValueError(f"trade_type phai la {' hoac '.join(TRADE_TYPES)}")

    stop_points = None if stop is None else round(abs(float(entry) - float(stop)), 2)
    target_points = None if target is None else round(abs(float(target) - float(entry)), 2)
    rr = (round(target_points / stop_points, 2)
          if stop_points and target_points is not None else None)
    swing_threshold = float(rules["swing_min_take_profit_points"])
    min_rr = float(rules["min_rr"])

    violations: list[str] = []
    demoted = False

    if trade_type == "swing" and target_points is not None and target_points < swing_threshold:
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
    if stop_points is not None and stop_points > max_stop:
        violations.append(f"SL {stop_points} > {prefix}max_stop_points ({max_stop:g})")
    if target_points is not None and target_points < min_tp:
        violations.append(f"TP {target_points} < {prefix}min_take_profit_points ({min_tp:g})")
    # TM - #MIN-RR - min_rr theo cap: so bang chinh con so 'rr' tra ra (lam tron 2
    # chu so), de khong bao "R:R 1.5 < min_rr 1.5" vi sai so float (0.3 / 0.2 =
    # 1.4999...). SL = gia vao thi khong co R:R - bo qua nhu orb_rules.score.
    if rr is not None and rr < min_rr:
        violations.append(f"R:R {rr:g} < min_rr {min_rr:g}")
    # max_margin = 0 nghia la khong gioi han (chi dung cho swing)
    if max_margin > 0 and float(margin_usd) > max_margin:
        violations.append(f"margin {margin_usd} > {prefix}max_margin_per_trade ({max_margin:g})")

    return {
        "symbol": symbol,
        "side": side,
        "trade_type": trade_type,
        "demoted_to_scalp": demoted,
        "entry": float(entry),
        "stop": None if stop is None else float(stop),
        "target": None if target is None else float(target),
        "stop_points": stop_points,
        "target_points": target_points,
        "rr": rr,
        "margin_usd": float(margin_usd),
        "limits_applied": {
            "symbol": symbol,
            "max_margin_per_trade": max_margin or "khong gioi han",
            "max_stop_points": max_stop,
            "min_take_profit_points": min_tp,
            # TM - #MIN-RR - min_rr theo cap
            "min_rr": min_rr,
            # Nhac lai cai gi tu dau, de doc lai nhat ky cu khong phai doan
            "max_stop_points_scope": f"rieng {symbol}",
            "min_take_profit_points_scope": f"rieng {symbol}",
            "min_rr_scope": f"rieng {symbol}",
            "max_margin_per_trade_scope": "chung moi cap",
        },
        "rule_violations": violations,
    }


# ------------------------------------------------- Gann time windows (pivot)

# TM - #GANN-TW - Gann Time Windows
# Ban goc tu config.yaml. enabled va paths chi doc tu day (doi thi restart);
# cac khoi con lai sua duoc tren trang admin - doc qua _gann_cfg().
GANN_YAML: dict[str, Any] = CFG.get("time_windows") or {}
GANN_ENABLED = bool(GANN_YAML.get("enabled"))
GANN_PATHS: dict[str, Any] = GANN_YAML.get("paths") or {}
# Chi hai khung nay co pivot. Khung nho hon khong co y nghia Gann o day: cua so
# thoi gian do bang ngay, ma mot nen 4h thi khong dinh duoc moc ngay nao ca.
GANN_TIMEFRAMES = ("1w", "1d")
GANN_MANUAL_FILE = DATA_ROOT / str(
    GANN_PATHS.get("manual_pivots") or "time_windows/manual_pivots.json")
GANN_EVENTS_FILE = DATA_ROOT / str(
    GANN_PATHS.get("events") or "time_windows/events.json")
GANN_BACKTEST_DIR = DATA_ROOT / str(
    GANN_PATHS.get("backtests") or "time_windows/backtests")
GANN_CONFIG_FILE = DATA_ROOT / str(
    GANN_PATHS.get("config") or "time_windows/config.json")
# Khoi config sua duoc tren trang admin. Moi khoi thay CA KHOI, khong tron tung
# truong: cai dang thay tren trang la dung cai dang chay, khong co gi lan tu yaml.
GANN_EDITABLE = ("symbols", "pivots", "projections", "scoring", "backtest", "context")
_GANN_CFG_CACHE: dict[str, Any] = {"mtime": None, "cfg": None, "warning": None}
# Tien to run_id, de get_backtest_result biet doc ket qua o dau (ORB hay Gann)
GANN_RUN_PREFIX = "tw-"
GANN_BACKTEST_TASKS: set[asyncio.Task] = set()
# Truong noi bo cua tang tinh toan, khong dua ra ngoai tool.
GANN_INTERNAL = ("index", "level_locked")
# Truong bo khi tra ra tool, de giu output duoi nguong 8 KB cua muc 7:
#   timeframe - da nam o envelope, khong lap lai tung dong
#   time_ms / confirmed_at_ms - nen 1d/1w luon mo 07:00 nen ngay la du dinh danh;
#     epoch ms van nam trong file cache cho Phase 2 tinh chieu thoi gian
GANN_OMIT = GANN_INTERNAL + ("timeframe", "time_ms", "confirmed_at_ms",
                             "known_ms", "retracted_ms", "major_from_ms",
                             "major_until_ms")


def _gann_cfg() -> dict[str, Any]:
    """Config time_windows dang hieu luc: config.yaml, de tung khoi bang file admin.

    Doc lai khi file doi - day la cho 9.5 "sua tren admin, lan goi sau phan
    anh ngay, khong restart". KHONG sua dict tra ve: no la ban dung chung, can
    sua thi deepcopy.

    Khoa cache la (mtime, kich thuoc). Rieng mtime khong du: tren Windows dong
    ho ghi file nhay theo nhip vai ms, hai lan ghi sat nhau (bam hai o lien
    tiep) ra CUNG mtime va cache tra ban cu. Vi vay moi lan chinh process nay
    ghi file deu goi _gann_cfg_invalidate(); khoa (mtime, size) chi con lo cho
    truong hop sua tay file tren may chu.
    """
    try:
        stat = GANN_CONFIG_FILE.stat()
        mtime = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        mtime = None
    if _GANN_CFG_CACHE["cfg"] is not None and _GANN_CFG_CACHE["mtime"] == mtime:
        return _GANN_CFG_CACHE["cfg"]
    cfg = copy.deepcopy(GANN_YAML)
    warning = None
    if mtime is not None:
        try:
            override = _read_json(GANN_CONFIG_FILE, {})
        except Exception as exc:
            # File hong thi chay bang config.yaml va bao ra, khong lam sap tool
            override = {}
            warning = f"{GANN_CONFIG_FILE.name} loi ({type(exc).__name__}) - dang dung config.yaml"
            print(f"CANH BAO: {warning}", file=sys.stderr)
        if isinstance(override, dict):
            for key in GANN_EDITABLE:
                if key in override:
                    cfg[key] = override[key]
    _GANN_CFG_CACHE.update(mtime=mtime, cfg=cfg, warning=warning)
    return cfg


def _tool_with_reasons(**options: Any):
    """Nhu @mcp.tool(), nhung loi nghiep vu (ValueError, FileNotFoundError) toi duoc Claude.

    SDK MCP 2.x chi chuyen nguyen van thong diep cua ToolError; moi exception
    khac bi coi la SAP - Claude chi thay "Error executing tool <ten>", con cau
    huong dan ("Gann dang tat cho ETHUSDT, bat o /admin/gann") nam lai trong log
    server. Mat cau do thi Claude chi con doan.

    Tra ve ham GOC (khong boc) de code goi truc tiep van nhu cu; chi ban dang ky
    voi MCP la ban boc. Loi khac (AssertionError, loi lap trinh) van la sap.
    """
    register = mcp.tool(**options)

    def decorator(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapped(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except (ValueError, FileNotFoundError) as exc:
                    raise ToolError(str(exc)) from exc
        else:
            @functools.wraps(fn)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                try:
                    return fn(*args, **kwargs)
                except (ValueError, FileNotFoundError) as exc:
                    raise ToolError(str(exc)) from exc
        register(wrapped)
        return fn

    return decorator


def _gann_cfg_invalidate() -> None:
    """Goi ngay sau moi lan ghi/xoa file config admin - xem _gann_cfg()."""
    _GANN_CFG_CACHE.update(mtime=None, cfg=None)


def _gann_require() -> None:
    if not GANN_ENABLED:
        raise ValueError(
            "Module Gann time windows dang tat. Bat time_windows.enabled trong "
            "config roi restart.")


def _gann_wanted() -> list[str]:
    """Cap nguoi dung BAT Gann (khoi symbols), dung thu tu, khong trung."""
    out: list[str] = []
    for item in _gann_cfg().get("symbols") or []:
        sym = str(item).strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out


def _gann_symbols() -> list[str]:
    """Cap dang phan tich Gann: bat o trang /admin/gann VA dang duoc theo doi.

    Danh sach rong nghia la KHONG cap nao - khong phai "moi cap". Bat/tat la
    tung o mot tren trang admin; neu rong = tat ca thi bo het dau tick lai
    thanh bat het.
    """
    active = _symbols()
    return [s for s in _gann_wanted() if s in active]


def _gann_symbol(symbol: str = "") -> str:
    """Cap cho tool Gann. Cap dang tat thi bao ro bat o dau, khong tu chay.

    Bo trong = cap dau tien dang bat Gann (khong phai cap mac dinh cua ca he
    thong: cap do co the dang tat Gann).
    """
    if not symbol:
        enabled = _gann_symbols()
        if not enabled:
            raise ValueError(
                f"Chua bat Gann cho cap nao. Bat o trang {ADMIN_GANN_PATH} "
                "(muc 'Cap phan tich Gann') - co hieu luc ngay, khong can restart.")
        return enabled[0]
    sym = _resolve_symbol(symbol)
    if sym in _gann_symbols():
        return sym
    if sym in _gann_wanted():
        raise ValueError(
            f"{sym} da bat Gann nhung dang TAT o trang cai dat chung ({ADMIN_PATH}) nen "
            "khong co du lieu nen moi. Bat cap do o trang cai dat chung truoc.")
    raise ValueError(
        f"Gann dang tat cho {sym}. Bat o trang {ADMIN_GANN_PATH} (muc 'Cap phan tich "
        f"Gann') - co hieu luc ngay, khong can restart. Dang bat: {_gann_symbols() or 'khong cap nao'}.")


def _gann_pivot_path(symbol: str) -> Path:
    tpl = str(GANN_PATHS.get("pivots") or "time_windows/pivots/{symbol}.json")
    return DATA_ROOT / tpl.format(symbol=symbol.upper())


def _bar_date(bar: dict[str, float]) -> str:
    """Ngay cua nen theo gio VN. Nen D mo 07:00 nen phai doi qua TZ, khong cat chuoi."""
    ts = bar.get("open_time", 0)
    if not ts:
        return ""
    if ts > 10_000_000_000:
        ts = ts / 1000
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d")


def _gann_manual(symbol: str, timeframe: str) -> list[dict[str, Any]]:
    """Pivot thu cong cho mot cap/khung. File dang {symbol: {timeframe: [...]}}."""
    doc = _read_json(GANN_MANUAL_FILE, {})
    if not isinstance(doc, dict):
        return []
    block = doc.get(symbol.upper()) or {}
    if not isinstance(block, dict):
        return []
    rows = block.get(timeframe) or []
    return [r for r in rows if isinstance(r, dict)]


def _gann_fingerprint(symbol: str) -> dict[str, Any]:
    """Dau tay cua moi thu lam doi ket qua pivot.

    Cache chi dung lai khi ca bon thu nay y nguyen: nen moi dong, config doi,
    pivot thu cong doi, hay mui gio doi - deu phai tinh lai.
    """
    last: dict[str, Any] = {}
    for tf in GANN_TIMEFRAMES:
        try:
            closed, _ = _split_closed(_load_bars(symbol, tf), tf)
            last[tf] = closed[-1]["open_time"] if closed else None
        except Exception:
            last[tf] = None
    manual = _gann_manual(symbol, "1d") + _gann_manual(symbol, "1w")
    return {
        "last_closed": last,
        "config": json.dumps(_gann_cfg().get("pivots") or {}, sort_keys=True, default=str),
        "manual": json.dumps(manual, sort_keys=True, default=str),
        "timezone": str(TZ),
    }


def _gann_public(pivot: dict[str, Any]) -> dict[str, Any]:
    """Pivot dang tra ra ngoai: bo truong noi bo, them ngay cho nguoi doc."""
    out = {k: v for k, v in pivot.items() if k not in GANN_OMIT}
    out["date"] = _bar_date({"open_time": pivot["time_ms"]})
    out["confirmed_date"] = _bar_date({"open_time": pivot["confirmed_at_ms"]})
    # source chi ghi ra khi la pivot thu cong: "auto" lap lai 30 dong khong noi
    # them dieu gi, con "manual" thi phai thay ngay.
    if out.get("source") == "auto":
        out.pop("source", None)
    return out


def _gann_compute(symbol: str) -> dict[str, Any]:
    """Tinh lai pivot cho ca hai khung. 1w truoc, vi 1d can no de xep bac major."""
    warnings: list[str] = []
    frames: dict[str, Any] = {}
    majors: list[dict[str, Any]] = []
    for tf in GANN_TIMEFRAMES:
        try:
            closed, _ = _split_closed(_load_bars(symbol, tf), tf)
        except Exception as exc:
            warnings.append(f"{tf}: khong doc duoc nen - {exc}")
            frames[tf] = {"pivots": [], "bars": 0}
            continue
        if len(closed) < 2:
            warnings.append(f"{tf}: chi co {len(closed)} nen da dong, chua tinh duoc pivot")
            frames[tf] = {"pivots": [], "bars": len(closed)}
            continue
        dates = [_bar_date(b) for b in closed]
        records, notes = gann_pivots.build(
            closed, dates, tf, _gann_cfg().get("pivots") or {}, _tf_ms(tf),
            manual=_gann_manual(symbol, tf),
            major_pivots=majors)
        warnings.extend(f"{tf}: {n}" for n in notes)
        if tf == gann_pivots.MAJOR_TIMEFRAME:
            majors = records
        # Luu CA pivot da bi bo (co retracted_ms): Phase 2/3 can biet "tai ngay X
        # thi ban do pivot trong nhu the nao", ma pivot bi bo nam trong ban do do.
        frames[tf] = {
            "pivots": [{k: v for k, v in p.items() if k not in GANN_INTERNAL}
                       for p in records],
            "live": len(gann_pivots.live_pivots(records)),
            "bars": len(closed),
            "first_bar_ms": closed[0]["open_time"],
            "last_bar_ms": closed[-1]["open_time"],
        }
    return {
        "symbol": symbol,
        "computed_at": _now_iso(),
        "fingerprint": _gann_fingerprint(symbol),
        "timeframes": frames,
        "warnings": warnings,
    }


def _gann_doc(symbol: str, force: bool = False) -> dict[str, Any]:
    """Pivot tu cache, tinh lai khi can. Tinh lai mat vai giay nen khong lam bua."""
    path = _gann_pivot_path(symbol)
    if not force:
        cached = _read_json(path, None)
        if (isinstance(cached, dict)
                and cached.get("fingerprint") == _gann_fingerprint(symbol)):
            return cached
    doc = _gann_compute(symbol)
    _write_json(path, doc)
    return doc


def _gann_records(symbol: str, timeframe: str,
                  force: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(record cua mot khung, ca doc). Khung khong duoc ho tro thi bao loi ngay."""
    tf = timeframe.strip().lower()
    if tf not in GANN_TIMEFRAMES:
        raise ValueError(
            f"timeframe khong co pivot Gann: {timeframe}. Cho phep: {list(GANN_TIMEFRAMES)}")
    doc = _gann_doc(symbol, force=force)
    return (doc["timeframes"].get(tf) or {}).get("pivots") or [], doc


def _gann_frame(symbol: str, timeframe: str, as_of_ms: int | None = None,
                force: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(pivot con hieu luc tai as_of_ms, ca doc). as_of_ms None = hien tai."""
    records, doc = _gann_records(symbol, timeframe, force=force)
    return gann_pivots.live_pivots(records, as_of_ms), doc


# TM - #GANN-TW - Gann Time Windows
def _gann_events() -> tuple[list[dict[str, Any]], list[str]]:
    """Su kien vi mo nhap tay. File dang [{date, name, weight, symbols}]."""
    doc = _read_json(GANN_EVENTS_FILE, [])
    if isinstance(doc, dict):          # cho phep boc trong {"events": [...]}
        doc = doc.get("events") or []
    if not isinstance(doc, list):
        return [], [f"{GANN_EVENTS_FILE.name} khong phai dang list - bo qua"]
    rows, warnings = [], []
    for item in doc:
        if not isinstance(item, dict) or not str(item.get("date") or "").strip():
            warnings.append(f"bo qua su kien khong hop le: {item!r}")
            continue
        rows.append(item)
    return rows, warnings


def _gann_compact_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Hit dang gon cho tool, de output duoi nguong 8 KB cua BR muc 7.

    Pivot gom thanh mot chuoi "low 2026-07-01 major" - Claude doc van ro, ma
    mot object long nhau ton 5 dong moi hit. Ngay chieu toi (date) bo voi hit
    co pivot vi suy ra duoc: date = peak - offset. Hit khong co pivot (seasonal,
    event) thi giu date, vi do chinh la thong tin cua no.
    Logic thuan (gann_windows) van tra object day du cho backtest va trang admin.
    """
    out = dict(hit)
    ref = out.pop("pivot", None)
    if ref:
        out.pop("date", None)
        out["pivot"] = f"{ref.get('type')} {ref.get('date')} {ref.get('level')}"
    return out


def _gann_config_version() -> str:
    """Dau tay cua config time_windows, de doc ket qua cu con biet no sinh ra tu dau."""
    raw = json.dumps(_gann_cfg(), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _gann_as_of(text: str) -> tuple[date, int]:
    """(ngay dung tai, moc ms cuoi ngay do). Rong = hien tai.

    Lay CUOI ngay vi "dung tai ngay X" nghia la da biet moi nen dong trong ngay
    X. Nen 1d mo 07:00 nen nen cua ngay X-1 dong vao 07:00 ngay X - dung bang
    moc nay thi no da duoc tinh, con nen dang chay cua ngay X thi chua.
    """
    raw = (text or "").strip()
    if not raw:
        now = datetime.now(TZ)
        return now.date(), int(now.timestamp() * 1000)
    try:
        day = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"as_of khong hop le: {text!r}. Dung 'YYYY-MM-DD'") from exc
    end = datetime(day.year, day.month, day.day, tzinfo=TZ) + timedelta(
        days=1, microseconds=-1)
    return day, int(end.timestamp() * 1000)


def _gann_window_pivots(symbol: str, as_of_ms: int
                        ) -> tuple[list[dict[str, Any]], list[str]]:
    """Pivot dem chu ky tu do, da biet tai as_of, moi buoc ngoat DUNG MOT LAN.

    Cach gop 1d/1w nam o gann_windows.merge_timeframes - backtest dung chung.
    """
    warnings: list[str] = []
    frames: dict[str, list[dict[str, Any]]] = {}
    for tf in GANN_TIMEFRAMES:
        try:
            pivots, doc = _gann_frame(symbol, tf, as_of_ms)
        except Exception as exc:
            warnings.append(f"{tf}: {exc}")
            pivots, doc = [], {}
        warnings.extend(doc.get("warnings") or [])
        frames[tf] = pivots

    out = gann_windows.merge_timeframes(
        frames.get("1d") or [], frames.get("1w") or [], _gann_margin_ms(),
        gann_pivots.MAJOR_SPAN_MS)
    for pivot in out:
        pivot["date"] = _bar_date({"open_time": pivot["time_ms"]})
    return out, warnings


def _gann_margin_ms() -> int:
    pivots = _gann_cfg().get("pivots") or {}
    return int(pivots.get("major_merge_days") or 3) * 86_400_000


# TM - #GANN-TW - Gann Time Windows
def _gann_end_of_day_ms(day: date) -> int:
    end = datetime(day.year, day.month, day.day, tzinfo=TZ) + timedelta(
        days=1, microseconds=-1)
    return int(end.timestamp() * 1000)


def _gann_backtest_execute(run_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Chay backtest (dong bo, trong thread). Ghi result.json + daily.csv.

    Moi du lieu dau vao (record pivot, nen, su kien) da duoc doc san o tool, de
    thread nay chi doc trong bo nho - khong tranh ghi file cache voi tool khac.
    """
    started = time.perf_counter()
    folder = GANN_BACKTEST_DIR / run_id
    try:
        bars, dates = request["bars"], request["dates"]
        start, end = request["start"], request["end"]
        index_of = {d: i for i, d in enumerate(dates)}
        first = next(i for i, d in enumerate(dates) if d >= start.isoformat())
        last = max(i for i, d in enumerate(dates) if d <= end.isoformat())
        days = [date.fromisoformat(d) for d in dates[first:last + 1]]

        cfg = copy.deepcopy(request["cfg"])
        backtest_cfg = cfg.get("backtest") or {}
        events = request["events"]
        tolerance = int((cfg.get("projections") or {}).get("tolerance_days") or 0)
        excluded = [False] * len(days)
        if request["exclude_events"]:
            # Do rieng phan Gann: tat chieu su kien VA bo ngay quanh su kien khoi
            # ca hai phia - neu khong, lift co the den tu CPI/FOMC chu khong tu Gann.
            cfg.setdefault("projections", {}).setdefault("event", {})["enabled"] = False
            near = set()
            for item in events:
                moment = gann_windows._as_date(item.get("date"))
                if moment is None:
                    continue
                for k in range(-tolerance, tolerance + 1):
                    near.add((moment + timedelta(days=k)).isoformat())
            excluded = [d.isoformat() in near for d in days]
            events = []

        scored = gann_backtest.walk_forward(
            request["records_1d"], request["records_1w"], days,
            int(request["lead_days"]), _gann_end_of_day_ms, cfg, events,
            request["symbol"], _gann_margin_ms(), gann_pivots.MAJOR_SPAN_MS)

        final = gann_windows.merge_timeframes(
            gann_pivots.live_pivots(request["records_1d"]),
            gann_pivots.live_pivots(request["records_1w"]),
            _gann_margin_ms(), gann_pivots.MAJOR_SPAN_MS)
        pivot_dates = {p["date"] for p in final if p.get("level") in ("major", "intermediate")}
        # Tinh ket qua tren CA chuoi nen roi moi cat: ATR va volume can lich su
        # truoc ngay start.
        outcomes = gann_backtest.outcome_metrics(bars, dates, pivot_dates,
                                                 backtest_cfg)[first:last + 1]

        report = gann_backtest.run(
            scored, outcomes, cfg, int(request["permutations"]), int(request["seed"]),
            int(backtest_cfg.get("shift_days") or 30), excluded)
        win_runs = report.pop("_window_runs")
        in_window = [False] * len(days)
        for a, b in win_runs:
            for i in range(a, b + 1):
                in_window[i] = True

        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "daily.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(("date", "score", "in_window", "excluded", "range_atr",
                             "volume_z", "pivot_near", "types", "pivots_known"))
            for i, row in enumerate(scored):
                types = sorted({h["type"] for h in row["hits"]})
                writer.writerow((row["date"].isoformat(), row["score"], int(in_window[i]),
                                 int(excluded[i]), outcomes[i]["range_atr"],
                                 outcomes[i]["volume_z"], outcomes[i]["pivot_near"],
                                 "|".join(types), row["pivots_known"]))

        out = {
            "run_id": run_id,
            "status": "done",
            "kind": "time_windows",
            "symbol": request["symbol"],
            "request": {k: request[k] for k in ("lead_days", "permutations", "seed",
                                                "exclude_events")}
            | {"start": start.isoformat(), "end": end.isoformat()},
            "config_version": request["config_version"],
            **report,
            "files": {"daily_csv": str(folder / "daily.csv"),
                      "result_json": str(folder / "result.json")},
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "finished_at": _now_iso(),
        }
        _write_json(folder / "result.json", out)
        return out
    except Exception as exc:
        out = {"run_id": run_id, "status": "error", "kind": "time_windows",
               "error": f"{type(exc).__name__}: {exc}",
               "elapsed_seconds": round(time.perf_counter() - started, 2)}
        _write_json(folder / "result.json", out)
        print(f"BAMCP backtest {run_id} loi: {out['error']}", file=sys.stderr)
        return out


def _gann_backtest_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Ban tom tat de tool tra ve (< 8 KB). Day du: get_backtest_result(run_id)."""
    if result.get("status") != "done":
        return result

    def brief(row: dict[str, Any]) -> dict[str, Any]:
        keep = ("lift", "delta", "p_value", "p_value_global", "n_in", "n_out",
                "days_touched")
        return {k: row[k] for k in keep if k in row}

    return {
        "run_id": result["run_id"],
        "status": "done",
        "symbol": result["symbol"],
        "request": result["request"],
        "days": result["days"],
        "windows": result["windows"],
        "coverage_pct": result["coverage_pct"],
        "overall": {m: brief(r) for m, r in result["overall"].items()},
        "by_type": {t: brief(r) for t, r in result["by_type"].items()},
        "by_cycle_days": {d: {k: r.get(k) for k in ("lift", "p_value", "n_in")}
                          for d, r in result["by_cycle_days"].items()},
        "elapsed_seconds": result["elapsed_seconds"],
        "note": ("p_value: dich cua so +-shift_days (giu che do bien dong) - so chinh. "
                 "p_value_global: dat lai bat ky dau - nho hon nhieu ma p_value lon thi "
                 "hieu ung den tu che do bien dong, khong phai tu thoi diem Gann. "
                 "Trung binh/trung vi, suggested_weights: get_backtest_result(run_id)."),
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
    """Liet ke cac cap dang theo doi va trang thai du lieu tung khung.

    Goi cai nay truoc khi phan tich mot cap la chua tung nhac toi, de biet no
    da co du lieu chua.
    """
    rows = []
    # TM - #KL-SQL - Kline storage SQLite
    coverage = _kline_coverage()
    # TM - #M5-FETCH - Per-timeframe schedules + M5
    schedules = SCHEDULES.list()
    for entry in STORE.symbols(DEFAULT_SYMBOL):
        frames = []
        for tf in SUPPORTED_TIMEFRAMES:
            cov = coverage.get((entry["symbol"], tf))
            item = {"timeframe": tf, "exists": bool(cov and cov["count"])}
            if item["exists"] and cov["updated_ms"]:
                item["updated_at"] = datetime.fromtimestamp(
                    cov["updated_ms"] / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S")
            # TM - #M5-FETCH - Per-timeframe schedules + M5
            item["schedules"] = (_schedules_for(schedules, entry["symbol"], tf)
                                 if FETCH_MODE == "schedules" else [])
            frames.append(item)
        rows.append({
            "symbol": entry["symbol"],
            "enabled": entry["enabled"],
            "ready": all(f["exists"] for f in frames if f["timeframe"] in KL["timeframes"]),
            "timeframes": frames,
        })
    return {
        "data_root": str(DATA_ROOT),
        "market": FETCH.get("market"),
        "default_symbol": _resolve_symbol(),
        "symbols_tracked": len(rows),
        "fetcher_enabled": bool(FETCH.get("enabled")),
        # TM - #M5-FETCH - Per-timeframe schedules + M5
        "interval_seconds": FETCH.get("interval_seconds") if FETCH_MODE == "interval" else None,
        "last_fetch": FETCH_STATE["last_run"],
        "last_fetch_error": FETCH_STATE["last_error"],
        "symbols": rows,
        # TM - #ORB - ORB Enhancement: khung chi keo theo yeu cau + M5 lich su
        "on_demand_timeframes": _orb_on_demand_view(),
        "orb_m5_history": ORB_HISTORY.status(ORB_SYMBOL) if ORB_ENABLED else None,
    }


def _orb_on_demand_view() -> list[dict[str, Any]]:
    """Khung on-demand (5m): fetcher nen khong keo, chi refresh_data / watch ORB."""
    out = []
    # TM - #KL-SQL - Kline storage SQLite
    coverage = _kline_coverage(ORB_SYMBOL)
    for tf in _on_demand_timeframes():
        cov = coverage.get((ORB_SYMBOL, tf))
        item: dict[str, Any] = {"symbol": ORB_SYMBOL, "timeframe": tf,
                                "exists": bool(cov and cov["count"]),
                                # TM - #M5-FETCH - Per-timeframe schedules + M5
                                "pulled_by": ("fetch schedules (xem list_timeframes) + ORB watch"
                                              if FETCH_MODE == "schedules" and tf in SUPPORTED_TIMEFRAMES
                                              else "refresh_data / ORB watch (khong theo fetcher nen)")}
        if item["exists"] and cov["updated_ms"]:
            item["updated_at"] = datetime.fromtimestamp(
                cov["updated_ms"] / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S")
        out.append(item)
    return out

@_tool_with_reasons()
def get_klines(timeframe: str, symbol: str = "", limit: int = 0,
               include_forming: bool = False, start: str = "",
               end: str = "") -> dict[str, Any]:
    """Lay nen OHLCV tho cua mot khung thoi gian.

    timeframe: mot trong cac khung khai bao o config (vd 1w, 1d, 4h, 1h, 15m, 5m).
    symbol: bo trong = cap mac dinh (cap dau tien dang bat).
    limit: so nen gan nhat, 0 = dung default_limit trong config.
    include_forming: mac dinh False, chi tra nen DA DONG. Bat True thi nen dang chay
      duoc them o cuoi voi is_closed=false - chi de biet gia hien tai, khong doc VSA tu no.
    start, end: TM - #GANN-TW. 'YYYY-MM-DD' hoac 'YYYY-MM-DD HH:MM', bao gom ca
      hai dau. Loc theo khoang TRUOC, roi limit lay N nen CUOI cua khoang. Bo
      trong ca hai thi hanh vi y het truoc day.
    """
    sym = _resolve_symbol(symbol)
    tf = _validate_timeframe(timeframe)
    closed, forming = _split_closed(_load_bars(sym, tf), tf)

    # TM - #GANN-TW - Gann Time Windows: loc theo khoang thoi gian
    start_ms = klines_coverage.parse_time_bound(start, TZ)
    end_ms = klines_coverage.parse_time_bound(end, TZ, end=True)
    if start_ms is not None and end_ms is not None and start_ms > end_ms:
        raise ValueError(f"start ({start}) phai truoc end ({end})")
    ranged = bool(start_ms is not None or end_ms is not None)
    if ranged:
        closed = klines_coverage.slice_by_time(closed, start_ms, end_ms)
        # Nen dang chay nam ngoai khoang thi khong dinh kem
        if forming is not None:
            keep = klines_coverage.slice_by_time([forming], start_ms, end_ms)
            forming = keep[0] if keep else None

    n = int(limit) if limit else int(KL["default_limit"])
    n = max(1, min(n, int(KL["max_limit"])))

    keys = ("open", "high", "low", "close", "volume")
    rows = [{"time": _bar_time(b), "is_closed": True, **{k: b[k] for k in keys}}
            for b in closed[-n:]]
    attached = bool(include_forming and forming)
    if attached:
        rows.append({"time": _bar_time(forming), "is_closed": False,
                     **{k: forming[k] for k in keys}})

    out = {
        "symbol": sym,
        "timeframe": tf,
        "count": len(rows),
        "closed_bars": len(rows) - (1 if attached else 0),
        "forming_bar_included": attached,
        "note": "Chi nen co is_closed=true moi duoc dung de danh gia VSA.",
        "bars": rows,
    }
    if ranged:
        # Noi ro khoang da loc va con bao nhieu nen bi limit cat bot, de nguoi
        # goi biet minh dang nhin mot phan hay toan bo khoang.
        out["range"] = {"start": start or None, "end": end or None,
                        "bars_in_range": len(closed),
                        "truncated_by_limit": max(0, len(closed) - n)}
    return out


# TM - #GANN-TW - Gann Time Windows
@_tool_with_reasons()
def get_data_coverage(symbol: str = "") -> dict[str, Any]:
    """Do phu du lieu nen: co tu bao gio den bao gio, thieu cho nao.

    symbol: bo trong = moi cap dang bat.
    Goi cai nay truoc khi phan tich lich su dai, de biet du lieu co du khong -
    thay vi keo hang nghin nen ve roi tu doan.
    """
    targets = [_resolve_symbol(symbol)] if symbol else _symbols()
    rows = []
    for sym in targets:
        frames = []
        # TM - #M5-FETCH - Per-timeframe schedules + M5
        for tf in SUPPORTED_TIMEFRAMES:
            span = _tf_ms(tf)
            try:
                bars = _load_bars(sym, tf)
            except Exception as exc:
                frames.append({"timeframe": tf, "error": str(exc)})
                continue
            info = klines_coverage.coverage(bars, span, _retention_for(tf))
            frames.append({
                "timeframe": tf,
                "count": info["count"],
                "first_bar": _bar_time({"open_time": info["first_bar_ms"]})
                if info["first_bar_ms"] else None,
                "last_bar": _bar_time({"open_time": info["last_bar_ms"]})
                if info["last_bar_ms"] else None,
                "expected_count": info["expected_count"],
                "gap_count": info["gap_count"],
                "gaps": [
                    {"from": _bar_time({"open_time": g["from_ms"]}),
                     "to": _bar_time({"open_time": g["to_ms"]}),
                     "missing_bars": g["missing_bars"]}
                    for g in info["gaps"]
                ],
                "retention": info["retention"],
            })
        rows.append({"symbol": sym, "timeframes": frames})
    return {"generated_at": _now_iso(), "symbols": rows}


# TM - #GANN-TW - Gann Time Windows
@_tool_with_reasons()
async def backfill_klines(timeframe: str, symbol: str = "",
                          max_requests: int = 40, days: int = 0) -> dict[str, Any]:
    """Keo lich su cu ve cho day, lui dan den khi het du lieu tren san.

    days: TM - #M5-FETCH. > 0 thi chi lui toi N ngay truoc (vd backfill 90 ngay
      khung 5m: timeframe="5m", days=90, max_requests=60). 0 = lui het lich su.

    BR goi day la "script backfill", nhung VPS khong co shell nen lam thanh tool
    de goi tu Claude.

    Idempotent: gop theo openTime nen chay lai khong tao nen trung, chi lap cho
    thieu. Dung lai khi san khong tra them nen cu hon nua, hoac het max_requests.

    CHU Y: khung nao con bi retention cat (xem get_data_coverage) thi backfill
    xong se bi cat lai o lan fetch sau. Dat retention cho khung do ve null truoc.
    """
    sym = _resolve_symbol(symbol)
    tf = _validate_timeframe(timeframe)
    span = _tf_ms(tf)
    if span <= 0:
        raise ValueError(f"khong biet do dai nen cua khung {tf}")

    # TM - #KL-SQL - Kline storage SQLite
    retention = _retention_for(tf) if KLINE_STORE.kind == "json" else None
    warnings: list[str] = []
    if retention:
        warnings.append(
            f"khung {tf} dang gioi han {retention} nen - backfill xong se bi cat "
            f"lai o lan fetch sau. Dat retention['{tf}'] = null trong config truoc.")

    limit = int(FETCH.get("fetch_limit", 500))
    requests_made = 0
    added_total = 0
    # TM - #M5-FETCH - Per-timeframe schedules + M5
    floor_ms = int(time.time() * 1000) - int(days) * 86_400_000 if int(days or 0) > 0 else None

    async with _fetch_lock(sym, tf):
        async with httpx.AsyncClient() as client:
            for _ in range(max(1, int(max_requests))):
                # TM - #KL-SQL - Kline storage SQLite
                cov = KLINE_STORE.get_coverage(sym, tf)
                before = cov[0]["count"] if cov else 0
                oldest = cov[0]["first_open_time"] if cov else None
                # TM - #M5-FETCH - Per-timeframe schedules + M5
                if floor_ms is not None and oldest is not None and oldest <= floor_ms:
                    break

                params = {"symbol": sym, "interval": tf, "limit": limit}
                if oldest is not None:
                    # endTime inclusive: lui them mot span de khong xin lai dung
                    # cay nen cu nhat dang co
                    params["endTime"] = oldest - 1

                try:
                    resp = await client.get(_binance_url(), params=params,
                                            timeout=float(FETCH["request_timeout"]))
                    resp.raise_for_status()
                    rows = resp.json()
                except Exception as exc:
                    warnings.append(f"dung som: {type(exc).__name__}: {exc}")
                    break
                requests_made += 1
                if not isinstance(rows, list) or not rows:
                    break        # het lich su

                # TM - #KL-SQL - Kline storage SQLite
                added = KLINE_STORE.upsert(sym, tf, rows)["total"] - before
                added_total += added
                if added <= 0:
                    break        # san khong con nen cu hon -> da cham day

    bars = _load_bars(sym, tf)
    info = klines_coverage.coverage(bars, span, retention)
    return {
        "symbol": sym,
        "timeframe": tf,
        "requests_made": requests_made,
        "bars_added": added_total,
        "count": info["count"],
        "first_bar": _bar_time({"open_time": info["first_bar_ms"]})
        if info["first_bar_ms"] else None,
        "last_bar": _bar_time({"open_time": info["last_bar_ms"]})
        if info["last_bar_ms"] else None,
        "gap_count": info["gap_count"],
        "retention": retention,
        "warnings": warnings,
    }


# TM - #KL-SQL - Kline storage SQLite
@_tool_with_reasons()
async def migrate_klines(action: str) -> dict[str, Any]:
    """Chuyen nen JSON sang SQLite va doi chieu hai ben (rollout storage.kline_store).

    action:
      import - chep moi nen da dong tu file JSON vao SQLite. Chay lai bao nhieu lan
        cung khong sinh trung (upsert theo symbol, tf, open_time).
      verify - voi moi symbol x tf: so so nen, open_time dau/cuoi va OHLCV tung nen
        giua JSON (chi nen da dong) va SQLite. ok = true khi mismatches = 0.
    Cung viec voi lenh: python kline_migrate.py import|verify
    """
    # TM - #M5-FETCH - Per-timeframe schedules + M5
    keys = [(c["symbol"], c["tf"]) for c in KLINE_JSON.get_coverage()]
    async with _fetch_locks(keys):
        return await asyncio.to_thread(_kline_migrate, action)


@mcp.tool()
async def refresh_data(symbol: str = "", timeframes: list[str] | None = None,
                       history: bool = False) -> dict[str, Any]:
    """Keo du lieu moi nhat tu Binance ngay lap tuc, khong cho den chu ky tiep theo.

    symbol: bo trong = TAT CA cac cap dang bat. Dien ten de chi keo mot cap.
    Dung khi can gia moi nhat truoc luc tim entry.
    timeframes: bo trong = cac khung thuong. Khung on-demand (vd "5m") chi duoc keo
      khi ghi ro o day - fetcher nen khong bao gio keo M5.
    history: True = tai/cap nhat M5 lich su (data.binance.vision) cho backtest ORB.
      Lan dau co the mat vai phut.
    """
    symbols = [_resolve_symbol(symbol)] if symbol else _symbols()
    targets = [_validate_timeframe(tf) for tf in (timeframes or KL["timeframes"])]
    out: dict[str, Any] = {
        "refreshed_at": _now_iso(),
        "symbols": symbols,
        # TM - #ORB - ORB Enhancement: ghi nguon de phan biet voi M5 do watch ORB keo
        "results": await fetch_all(symbols, targets, source="refresh_data"),
    }
    if history:
        # TM - #ORB - ORB Enhancement
        if not ORB_ENABLED:
            raise ValueError("M5 lich su chi dung cho ORB - bat orb.enabled truoc.")
        report = await _orb_history_update()
        out["orb_history"] = {
            **{k: report.get(k) for k in ("from", "to", "missing_remote", "errors")},
            "downloaded": len(report.get("downloaded") or []),
            "status": ORB_HISTORY.status(ORB_SYMBOL),
        }
    return out


@mcp.tool()
def get_context(symbol: str = "", timeframes: list[str] | None = None) -> dict[str, Any]:
    """Tom tat da khung: bien range, vi tri gia trong range, spread/volume cho VSA, swing gan nhat.

    symbol: bo trong = cap mac dinh. Moi lan goi chi phan tich MOT cap.
    timeframes: bo trong = lay tat ca khung trong config.
    """
    sym = _resolve_symbol(symbol)
    targets = timeframes or KL["timeframes"]
    result = {}
    for tf in targets:
        try:
            result[tf.lower()] = _build_context(sym, tf.lower())
        except Exception as exc:
            result[tf.lower()] = {"error": str(exc)}
    # TM - #GANN-TW - Gann Time Windows: swing_state / time_windows_next_7d
    return {"symbol": sym, "generated_at": _now_iso(), "contexts": result,
            **_gann_context(sym)}


# TM - #GANN-TW - Gann Time Windows
def _gann_context(sym: str) -> dict[str, Any]:
    """Hai field Gann gon cho get_context (FR-4.1). Tat co thi tra {} - field bien mat.

    swing_state: cac truong chinh cua get_swing_state 1d, bo mang nhip hoi.
    time_windows_next_7d: chi khi context.time_windows bat - xem config.
    Loi o day khong duoc lam hong get_context: bao trong field, khong nem ra.
    """
    if not GANN_ENABLED:
        return {}
    ctx = _gann_cfg().get("context") or {}
    if not ctx.get("include_in_get_context") or sym not in _gann_symbols():
        return {}
    out: dict[str, Any] = {}
    try:
        state = _gann_swing_state(sym, "1d")
        leg = state.get("current_leg") or {}
        pivot = leg.get("from_pivot") or {}
        out["swing_state"] = {
            "timeframe": "1d",
            "trend": state["trend"],
            "current_leg": {
                "direction": leg.get("direction"), "bars": leg.get("bars"),
                "amplitude_pct": leg.get("amplitude_pct"),
                "from": f"{pivot.get('type')} {pivot.get('date')} {pivot.get('price')}",
            } if leg else None,
            "max_correction_bars": state["max_correction_bars"],
            "max_correction_depth": state["max_correction_depth"],
            "time_overbalanced": state["time_overbalanced"],
            "price_overbalanced": state["price_overbalanced"],
            "note": state["note"],
        }
    except Exception as exc:
        out["swing_state"] = {"error": str(exc)}
    if ctx.get("time_windows"):
        try:
            data = _gann_time_windows(sym, int(ctx.get("horizon_days") or 7), 0, "",
                                      int(ctx.get("max_windows") or 3))
            cap = max(1, int(ctx.get("max_hits") or 3))
            out["time_windows_next_7d"] = {
                "windows": [{**{k: w[k] for k in ("from", "to", "peak", "score")},
                             "hits": [_gann_hit_text(h) for h in w["hits"][:cap]]}
                            for w in data["windows"]],
                "note": "Gia thuyet ve THOI DIEM, khong noi huong gia. Chua kiem chung.",
            }
        except Exception as exc:
            out["time_windows_next_7d"] = {"error": str(exc)}
    return out


def _gann_hit_text(hit: dict[str, Any]) -> str:
    """Mot hit thanh mot dong chu - get_context can gon hon get_time_windows."""
    label = {"cycle": f"cycle {hit.get('days')}d",
             "anniversary": f"anniversary {hit.get('years')}y",
             "swing_duration": f"swing x{hit.get('ratio')} ({hit.get('days')}d)",
             "range_square": f"range_square {hit.get('days')}d"}.get(
        hit.get("type"), f"{hit.get('type')} {hit.get('name') or ''}".strip())
    source = f" <- {hit['pivot']}" if hit.get("pivot") else ""
    return f"{label}{source} ({hit.get('score')})"


@mcp.tool()
def get_bias(symbol: str = "", date: str = "") -> dict[str, Any]:
    """Doc bias da luu cho MOT cap. date bo trong = hom nay, symbol bo trong = cap mac dinh."""
    sym = _resolve_symbol(symbol)
    day = date or _today()
    data = _read_json(_bias_dir(sym) / f"{day}.json", None)
    if data is None:
        return {"symbol": sym, "date": day, "found": False,
                "message": f"Chua co bias cho {sym} ngay nay. Chay buoc D/H4 roi save_bias."}
    return {"symbol": sym, "date": day, "found": True, **data}


@mcp.tool()
def save_bias(
    direction: str,
    summary: str,
    symbol: str = "",
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
    symbol: bo trong = cap mac dinh. Moi cap co bias rieng.
    TM - #WYCKOFF-TR: phase bo trong = lay current_phase cua TR khoa (goi update_tr TRUOC save_bias).
    Khai phase khac current_phase thi van luu nhung tra warnings.
    """
    day = date or _today()
    sym = _resolve_symbol(symbol)
    direction = direction.strip().lower()
    if direction not in ("long", "short", "neutral"):
        raise ValueError("direction phai la long, short hoac neutral")
    # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR: pha cua bias an theo TR khoa
    warnings: list[str] = []
    current = ((PLANS.active_tr(sym) if day == _today() else PLANS.tr_at(sym, _day_window_ms(day)[1]))
               or {}).get("current_phase")
    phase = str(phase or "").strip()
    if not phase and current:
        phase = current
    elif phase and current and phase.upper() != current:
        warnings.append(f"phase {phase} khac current_phase {current} cua TR khoa - pha doi thi goi "
                        "update_tr truoc save_bias")

    path = _bias_dir(sym) / f"{day}.json"
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
    # TM - #DASHBOARD - Setup dashboard
    return {"saved": True, "symbol": sym, "date": day, "file": str(path),
            "revisions": len(history), "plan_revision": _plan_snapshot(sym, day, "save_bias"),
            # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
            "phase": phase, "warnings": warnings,
            **_dash_link(sym, day)}


@mcp.tool()
def get_rules(symbol: str = "", history_limit: int = 10) -> dict[str, Any]:
    """Doc bo quy dinh dang co hieu luc va lich su thay doi.

    symbol: dien ten cap de xem dung bo rule ap cho cap do (SL/TP/nguong swing/
      min_rr cua no). Bo trong = rule chung + gia tri mac dinh cho cap chua dat rieng.
    history_limit: so lan doi gan nhat can xem, 0 = xem tat ca.

    Khoi 'min_rr' = R:R toi thieu dang ap cho cap, kem nguon: "symbol" (cap dat
    rieng) hoac "default" (ke thua mac dinh).
    """
    sym = _resolve_symbol(symbol) if symbol else ""
    doc = _rules_doc()
    history = doc["history"]
    rules = _load_rules(sym)
    return {
        "symbol": sym or None,
        "rules": rules,
        # TM - #MIN-RR - min_rr theo cap: gia tri dang ap + no den tu dau
        "min_rr": {
            "value": rules["min_rr"],
            "source": "symbol" if "min_rr" in doc["symbols"].get(sym, {}) else "default",
            "applies_to": "moi lenh khong phai ORB (lenh ORB cham bang orb.min_rr)",
        },
        "scope": {
            "chung_moi_cap": list(GLOBAL_RULE_KEYS),
            "rieng_tung_cap": list(SYMBOL_RULE_KEYS),
            # TM - #ORB-RULES - ORB Rule Set
            "orb": {
                "defaults": ["enabled", *orb_rules.FIELDS],
                "symbol_overrides": list(orb_rules.FIELDS),
                "session": list(orb_rules.SESSION_FIELDS),
                "note": ("lenh strategy=ORB cham bang bo rule nay, khong dung rule "
                         "scalp/swing; chi max_margin_per_trade la dung chung"),
            },
        },
        # TM - #ORB-RULES - ORB Rule Set: bo rule ORB luu va ban da giai cho cap duoc hoi
        "orb": doc["orb"],
        "orb_resolved": orb_rules.resolve(doc["orb"], sym or ORB_SYMBOL),
        # Cap chua dat rieng thi an theo bo nay
        "defaults_for_symbols": {k: doc["values"][k] for k in SYMBOL_RULE_KEYS},
        "symbol_overrides": doc["symbols"],
        "defaults": RULES_SEED,
        "file": str(RULES_FILE),
        "changes_total": len(history),
        "changed_today": _changed_today(history, _today()),
        "history": history[-history_limit:] if history_limit else history,
    }


@mcp.tool()
def update_rules(changes: dict[str, Any], reason: str,
                 symbol: str = "", orb: bool = False,
                 session_id: str = "") -> dict[str, Any]:
    """Doi quy dinh giao dich. Co hieu luc NGAY cho moi lan goi tool sau do.

    changes: chi dien rule muon doi, vd {"max_trades_per_day": 2}.
      Rule khong nhac den thi giu nguyen.
    reason: bat buoc. Vi sao doi. Duoc ghi vao lich su cung moc thoi gian,
      va hien lai trong get_today_status neu doi trong ngay dang giao dich.
    symbol: bo trong = doi rule CHUNG (quota lenh/ngay, margin, daily_stop_loss),
      dong thoi la mac dinh cho cap chua dat rieng. Dien ten cap = chi doi rule
      cua rieng cap do, va chi duoc doi max_stop_points, min_take_profit_points,
      swing_min_take_profit_points, swing_max_stop_points, min_rr.

    min_rr: R:R toi thieu (TP / SL tinh tu gia vao), so > 0, mac dinh 1.5. Ap cho
      moi lenh KHONG phai ORB: {"min_rr": 2} + symbol = chi cap do; bo trong
      symbol = doi mac dinh cho moi cap chua dat rieng. Khac orb.min_rr (orb = true).

    Doi rule chung ma van dien symbol thi tool bao loi - khong am tham ghi nham
    pham vi.

    orb = true: doi bo rule ORB rieng (khoi `orb` trong rules.json), vd
      {"min_rr": 2} + symbol="BTCUSDT". Bo trong symbol = orb.defaults (va cong
      tac {"enabled": false}); session_id = override cua phien (chi min/max_or_atr_ratio,
      tp_r, buffer_pct, move_sl_to_be_at_r, time_exit_minutes, use_bias_filter,
      allow_reversal, skip_news_days). Gia tri null = bo override (ke thua lop duoi).
      Truong: max_stop_points, min_take_profit_points, min_rr, max_trades_per_day
      (nguyen >= 0), daily_stop_loss (<= 0), margin_usd, leverage, maint_margin_pct,
      liq_safety_pct + cac truong phien o tren. Moi truong doi la mot dong history.
    """
    # TM - #ORB-RULES - ORB Rule Set: tham so `orb` che module orb trong ham nay
    if orb:
        return _apply_orb_rule_changes(changes, reason, symbol=symbol,
                                       session_id=session_id, source="mcp")
    if session_id:
        raise ValueError("session_id chi dung voi orb = true (override rule ORB cua phien)")
    return _apply_rule_changes(changes, reason, symbol)


@mcp.tool()
async def get_today_status(date: str = "", symbol: str = "", strategy: str = "",
                           session_id: str = "") -> dict[str, Any]:
    """Kiem tra quota lenh va PnL trong ngay truoc khi vao lenh moi.

    Quota lenh, margin va daily_stop_loss dung CHUNG cho moi cap - het la het,
    du ban dang nhin cap nao.

    symbol: dien ten cap de khoi 'rules' tra ve dung nguong SL/TP/swing cua cap
      do. Bo trong = rule chung + gia tri mac dinh.
    strategy / session_id: loc them nhat ky theo chien luoc (ORB/WYCKOFF/OTHER)
      va phien ORB - ket qua o khoi 'filtered'.

    Lenh ORB co quota + daily stop RIENG (rules.orb): trades_taken / realized_pnl /
    daily_stop_hit / can_trade chi tinh lenh KHONG phai ORB; phan ORB o
    orb_trades_taken / orb_trades_remaining / orb_realized_pnl / orb_daily_stop_hit /
    can_trade_orb (dem theo or_date).

    Khi account.enabled = true, khoi 'exchange' chua so THAT lay tu san va
    'can_trade' duoc tinh theo so that do, khong phai theo nhat ky tu khai.

    TM - #OFF-PLAN: khoi 'off_plan' {count, codes, pending_review, pending_codes, pnl, open,
    by_classification, in_plan_breach} - lenh ngoai ke hoach hom nay. count >= 2 thi noi ro va
    nhac Tan can nhac dung giao dich.
    """
    day = date or _today()
    sym = _resolve_symbol(symbol) if symbol else ""
    # TM - #ORB - ORB Enhancement: loc sai thi bao truoc khi goi san
    filtered = (_journal_filter([], strategy, session_id)
                if (strategy or session_id) else None)
    rules = _load_rules(sym)
    trades = _read_json(JOURNAL_DIR / f"{day}.json", [])
    closed = [t for t in trades if t.get("pnl") is not None]
    # TM - #ORB-RULES - ORB Rule Set: phan chung loai tru lenh ORB (BR-ORB-09)
    general = [t for t in trades if not _is_orb(t)]
    orb_in_journal = [t for t in trades if _is_orb(t)]
    orb_journal_pnl = round(sum(float(t["pnl"]) for t in orb_in_journal
                                if t.get("pnl") is not None), 2)
    journal_pnl = round(sum(float(t["pnl"]) for t in general if t.get("pnl") is not None), 2)
    edits = _changed_today(_rules_history(), day)
    orb_today = _orb_today(day)

    # Mac dinh: chi co nhat ky tu khai
    trades_counted = len(general)
    pnl_counted = journal_pnl
    source = "journal"
    exchange_block: dict[str, Any] | None = None

    if _account_enabled():
        try:
            data = await _account_day(day)
            positions = await _open_positions()
            counts = data["position_counts"]
            exchange_block = {
                "exchange": data["exchange"],
                # Mot vi the = mot lenh, du TP tung phan hay SL cat lam nhieu manh
                "positions_opened_today": counts["opened_today"],
                "positions_closed_today": counts["closed_today"],
                "positions_carried_in": counts["carried_in"],
                "orders": len(data["order_ids"]),
                "fills": len(data["fills"]),
                "open_positions": positions["open_positions"],
                # Cap nao dang gop vao con so nay, va cap nao doc khong duoc
                "symbols": data["symbols"],
                "errors": data["errors"],
                "by_symbol": {s: d["totals"] for s, d in data["per_symbol"].items()},
                **data["totals"],
            }
            # San la nguon su that. Lenh quen log van tinh vao quota.
            # Vi the mang tu hom truoc sang khong tinh - da tinh vao quota hom do roi.
            # TM - #ORB-RULES - ORB Rule Set: san khong biet lenh nao la ORB -> tru
            # phan ORB theo nhat ky ra khoi so san
            trades_counted = max(len(general), counts["opened_today"] - len(orb_in_journal), 0)
            pnl_counted = (round(float(data["totals"]["net"]) - orb_journal_pnl, 2)
                           if orb_journal_pnl else data["totals"]["net"])
            source = "exchange"
        except Exception as exc:
            # Mat ket noi san khong duoc lam hong buoc kiem tra ky luat
            exchange_block = {"error": str(exc),
                              "note": "khong doc duoc san, dang dung so tu nhat ky"}

    remaining = max(0, int(rules["max_trades_per_day"]) - trades_counted)
    stop_hit = pnl_counted <= float(rules["daily_stop_loss"])

    extra: dict[str, Any] = {}
    if filtered is not None:
        # TM - #ORB - ORB Enhancement: chi them khoa moi, khoa cu giu nguyen
        extra["filtered"] = _journal_filter(trades, strategy, session_id)
        if filtered["filter"]["strategy"] in (None, "ORB"):
            skips = _read_json(ORB_SKIPS_DIR / f"{day}.json", [])
            sid = filtered["filter"]["session_id"]
            extra["orb_skips"] = [s for s in (skips if isinstance(skips, list) else [])
                                  if not sid or s.get("session_id") == sid]
        extra["filtered_note"] = ("PnL o 'filtered' lay tu nhat ky tu khai - san khong "
                                  "biet lenh nao thuoc strategy nao.")

    return {
        "date": day,
        "symbol": sym or None,
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
        # TM - #ORB-RULES - ORB Rule Set: quota + daily stop ORB rieng, theo or_date
        "orb_trades_taken": orb_today["trades_taken"],
        "orb_trades_remaining": orb_today["trades_remaining"],
        "orb_realized_pnl": orb_today["realized_pnl"],
        "orb_daily_stop_hit": orb_today["daily_stop_hit"],
        "can_trade_orb": orb_today["can_trade"],
        "orb_status": orb_today,
        "rules": {**rules, "orb": orb_today["rules"]},
        # Quota va daily stop khong tach theo cap: chi mot tai khoan, mot ngan sach
        "rules_scope": {
            "chung_moi_cap": list(GLOBAL_RULE_KEYS),
            "rieng_tung_cap": list(SYMBOL_RULE_KEYS),
            "orb": list(orb_rules.FIELDS),
            "note": ("quota lenh/ngay va daily_stop_loss dung chung cho tat ca "
                     "cac cap; SL/TP/nguong swing thi theo tung cap. Lenh ORB co "
                     "quota/daily stop rieng (rules.orb), khong tinh vao phan chung"),
        },
        # Rule bi doi trong chinh ngay dang giao dich la tin hieu dang de y
        "rules_changed_today": edits,
        "exchange": exchange_block,
        "trades": trades,
        # TM - #OFF-PLAN - Lenh ngoai ke hoach hom nay (vi the that do position_watcher ghi)
        "off_plan": _op_summary(day),
        **extra,
    }


@mcp.tool()
def log_trade(
    side: str,
    entry: float,
    stop: float,
    target: float,
    margin_usd: float,
    symbol: str = "",
    trade_type: str = "scalp",
    setup: str = "",
    date: str = "",
    strategy: str = "OTHER",
    variant: str = "",
    session_id: str = "",
    or_date: str = "",
    or_high: float = 0.0,
    or_low: float = 0.0,
    setup_id: str = "",
) -> dict[str, Any]:
    """Ghi mot lenh vua vao. Tra ve canh bao neu pham rule, nhung van ghi de nhat ky dung thuc te.

    setup_id: TM - #ENTRY-ALERT. id hoac code cua setup (save_setups) ma lenh nay vao
      theo - setup chuyen sang FILLED va gui tin. Setup phai cung cap.
      TM - #OFF-PLAN: vi the that vua bi position_watcher danh ngoai ke hoach (mo trong 1 gio quanh
      luc log) thi duoc gan vao setup nay va phan loai lai - ket qua o khoi 'off_plan'.

    symbol: bo trong = cap mac dinh. Nguong SL toi da, TP toi thieu, nguong
      swing va min_rr duoc lay theo DUNG CAP NAY. Quota lenh/ngay, margin va
      daily_stop_loss thi dung chung cho moi cap.
    R:R (TP / SL tu gia vao) < min_rr cua cap -> them vi pham "R:R <x> < min_rr <y>"
      (cung kiem tra voi check_trade; lenh ORB dung orb.min_rr).
    trade_type: scalp (mac dinh, han muc chat) hoac swing (han muc rong hon).
      Khai swing ma TP khong dat nguong swing_min_take_profit_points CUA CAP DO
      thi lenh tu dong bi ha xuong han muc scalp - dan nhan khong lach duoc.
    strategy: ORB | WYCKOFF | OTHER (mac dinh OTHER).
    Chi cho strategy = ORB:
      session_id (bat buoc), variant (breakout | retest | reversal),
      or_date (ngay cua phien, bo trong = phien dang chay),
      or_high / or_low (bo trong = lay OR he thong da tinh).
      Cham bang bo rule ORB rieng (rules.orb, rule_set = "orb") cung ham voi
      check_trade, kem trade window, max_trades cua phien, range bi loc, phien da
      skip. Ghi xong thi watch cua phien dung (taken).
    """
    day = date or _today()
    sym = _resolve_symbol(symbol)
    # TM - #ORB - ORB Enhancement: kiem tra truoc khi ghi de input sai khong de lai dong rac
    tag = _strategy_fields(strategy, variant, session_id, or_date, or_high, or_low)
    # TM - #ENTRY-ALERT - Entry setups & alerts
    linked = SETUPS.get(setup_id.strip()) if (setup_id or "").strip() else None
    if linked and linked["symbol"] != sym:
        raise ValueError(f"setup {linked['code']} la cua {linked['symbol']}, khong phai {sym}")
    if linked and linked["status"] in entry_logic.TERMINAL:
        raise ValueError(f"setup {linked['code']} da ket thuc ({linked['status']})")
    path = JOURNAL_DIR / f"{day}.json"
    trades = _read_json(path, [])
    # TM - #ORB-RULES - ORB Rule Set: cung mot ham cham voi check_trade (BR-ORB-10)
    verdict, orb_checks, rules = _score_trade(
        tag, sym, side=side, entry=entry, stop=stop, target=target,
        margin_usd=margin_usd, trade_type=trade_type, trades=trades)
    violations = verdict["rule_violations"]
    if orb_checks:
        tag["or_date"] = orb_checks["or_date"]
        rng = orb_checks["opening_range"]
        tag["or_high"] = tag["or_high"] if tag["or_high"] is not None else rng.get("high")
        tag["or_low"] = tag["or_low"] if tag["or_low"] is not None else rng.get("low")

    trade = {
        "id": len(trades) + 1,
        "symbol": sym,
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
        # Chup lai rule dang hieu luc luc vao lenh - la bo DA GIAI cho cap nay,
        # tuc rule chung da tron voi rule rieng cua cap. Doi rule ve sau khong
        # sua duoc nhat ky cu, nen doc lai van biet luc do choi theo luat nao.
        "rules_at_entry": rules,
        "limits_applied": verdict["limits_applied"],
        "demoted_to_scalp": verdict["demoted_to_scalp"],
        "rule_violations": violations,
        # TM - #ORB - ORB Enhancement
        **tag,
        # TM - #ORB-RULES - ORB Rule Set
        **({"rule_set": "orb"} if orb_checks else {}),
        # TM - #ENTRY-ALERT - Entry setups & alerts
        **({"setup_id": linked["id"], "setup_code": linked["code"]} if linked else {}),
    }
    trades.append(trade)
    _write_json(path, trades)
    out = {
        "logged": True,
        "trade": trade,
        "rule_violations": violations,
        "rules_changed_today": _changed_today(_rules_history(), day),
    }
    # TM - #ENTRY-ALERT - Entry setups & alerts
    if linked:
        out["setup"] = _setup_finish(linked, "FILLED", "FILLED",
                                     {"trade_id": trade["id"], "source": "log_trade",
                                      "journal_date": day}, price=trade["entry"])
        # TM - #OFF-PLAN - vi the da bi danh ngoai ke hoach ma log voi setup -> phan loai lai theo setup do.
        # Phan phu: loi o day khong duoc lam hong viec ghi nhat ky da xong.
        try:
            linked_position = _op_link_log_trade(sym, verdict["side"], linked["id"])
            if linked_position:
                out["off_plan"] = linked_position
        except Exception as exc:
            out["off_plan_error"] = f"{type(exc).__name__}: {exc}"
    if orb_checks:
        # TM - #ORB - ORB Enhancement: da vao lenh thi dung watch phien (BR-04)
        out["orb_checks"] = orb_checks
        out["orb_watch"] = _orb().mark_taken(
            orb_checks["session_id"], orb_checks["or_date"], {**trade, "_journal_date": day})
    return out


def _strategy_fields(strategy: str, variant: str, session_id: str, or_date: str,
                     or_high: float, or_low: float) -> dict[str, Any]:
    """Chuan hoa cac truong strategy cua log_trade (muc 4.6).

    Lenh khong phai ORB chi mang them 'strategy'; truong ORB ma di kem strategy
    khac la nham lan - bao loi thay vi ghi am tham.
    """
    # TM - #ORB - ORB Enhancement
    name = (strategy or "OTHER").strip().upper()
    if name not in orb.STRATEGIES:
        raise ValueError(f"strategy phai la mot trong {list(orb.STRATEGIES)}")
    variant = (variant or "").strip().lower()
    if name != "ORB":
        if variant or session_id or or_date or or_high or or_low:
            raise ValueError("variant/session_id/or_date/or_high/or_low chi dung cho "
                             "strategy = ORB")
        return {"strategy": name}
    if not (session_id or "").strip():
        raise ValueError("strategy = ORB bat buoc co session_id")
    if variant and variant not in orb.VARIANTS:
        raise ValueError(f"variant phai la mot trong {list(orb.VARIANTS)}")
    if or_date:
        orb.parse_date(or_date)
    if (or_high or or_low) and not (float(or_high) > float(or_low) > 0):
        raise ValueError("or_high phai lon hon or_low va ca hai > 0")
    return {
        "strategy": name,
        "variant": variant or None,
        "session_id": session_id.strip().lower(),
        "or_date": or_date or None,
        "or_high": float(or_high) if or_high else None,
        "or_low": float(or_low) if or_low else None,
    }


def _score_trade(tag: dict[str, Any], symbol: str, *, side: str, entry: float,
                 stop: float, target: float, margin_usd: float, trade_type: str,
                 trades: list[dict[str, Any]]
                 ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    """Chon bo rule theo strategy va cham - MOT ham cho check_trade va log_trade.

    TM - #ORB-RULES - ORB Rule Set (BR-ORB-02, BR-ORB-10):
      strategy = ORB -> rule ORB rieng (rules.orb) + quy tac phien, qua
                        OrbService.score_trade (check_orb_signal dung cung ham do).
      con lai        -> rule scalp/swing cua cap; quota va daily stop chung chi
                        dem lenh KHONG phai ORB (BR-ORB-09).
    Tra ve (verdict, orb_checks | None, bo rule chup vao nhat ky).
    """
    if tag.get("strategy") == "ORB":
        svc = _orb()
        verdict = svc.score_trade(tag["session_id"], tag.get("or_date") or "", side=side,
                                  entry=entry, stop=stop, target=target,
                                  margin_usd=margin_usd, trade_type=trade_type, symbol=symbol)
        checks = verdict.pop("orb_checks")
        verdict.pop("would_pass", None)
        session = svc.resolve(tag["session_id"], include_disabled=True)[0]
        resolved = svc.rules_for(session, symbol)
        rules = {"rule_set": "orb", "values": resolved["values"],
                 "sources": resolved["sources"],
                 "max_margin_per_trade": verdict["limits_applied"]["max_margin_per_trade"]}
        return verdict, checks, rules
    general = [t for t in trades if not _is_orb(t)]
    realized = round(sum(float(t["pnl"]) for t in general if t.get("pnl") is not None), 2)
    rules = _load_rules(symbol)
    verdict = _evaluate_trade(
        symbol=symbol, side=side, entry=entry, stop=stop, target=target,
        margin_usd=margin_usd, trade_type=trade_type, trades=general,
        rules=rules, realized=realized,
    )
    return verdict, None, rules


@mcp.tool()
def check_trade(side: str, entry: float, stop: float, target: float,
                margin_usd: float, symbol: str = "", trade_type: str = "scalp",
                date: str = "", strategy: str = "OTHER", session_id: str = "",
                or_date: str = "") -> dict[str, Any]:
    """Cham thu mot lenh theo rule ma KHONG ghi vao nhat ky.

    Dung truoc khi bam lenh: xem no duoc xep scalp hay swing, han muc nao ap
    dung, co pham rule gi khong. Muon ghi that thi goi log_trade voi cung tham so.

    symbol: bo trong = cap mac dinh. Nguong diem (SL toi da, TP toi thieu,
      nguong swing) va min_rr lay theo dung cap nay, nen cham cung mot bo so tren
      hai cap khac nhau co the ra hai ket qua khac nhau - do la co y.
    R:R = TP / SL tinh tu gia vao (truong 'rr'). R:R < min_rr cua cap ->
      would_pass = false, vi pham "R:R <x> < min_rr <y>" (ap ca scalp lan swing).
    strategy = ORB (can session_id): cham bang bo rule ORB rieng (rules.orb):
      limits_applied lay tu rule ORB, rule_set = "orb", kiem them min_rr,
      orb.daily_stop_loss, orb.max_trades_per_day (dem theo or_date) va SL trong
      vung an toan thanh ly; trade_type bi bo qua (khong co swing / demote). Kem
      trade window, max_trades cua phien, range bi loc, phien da skip - khoi
      'orb_checks'. Chi max_margin_per_trade lay tu rule chung.
    """
    day = date or _today()
    sym = _resolve_symbol(symbol)
    # TM - #ORB - ORB Enhancement
    tag = _strategy_fields(strategy, "", session_id, or_date, 0.0, 0.0)
    trades = _read_json(JOURNAL_DIR / f"{day}.json", [])
    # TM - #ORB-RULES - ORB Rule Set: cung mot ham cham voi log_trade (BR-ORB-10)
    verdict, orb_checks, _rules = _score_trade(
        tag, sym, side=side, entry=entry, stop=stop, target=target,
        margin_usd=margin_usd, trade_type=trade_type, trades=trades)
    out = {
        "date": day,
        "would_pass": not verdict["rule_violations"],
        "logged": False,
        **verdict,
    }
    if orb_checks:
        # TM - #ORB - ORB Enhancement
        out["strategy"] = "ORB"
        out["orb_checks"] = orb_checks
    return out


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
    # TM - #ENTRY-ALERT - Entry setups & alerts
    if cfg["name"] == "okx":
        options = {**options, "symbol_map": {str(k).upper(): v for k, v in (
            _alerts_cfg()["watcher"].get("okx_symbol_map") or {}).items()}}
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


_SIZE_EPS = 1e-8


def _group_fills_into_positions(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gop fill thanh VI THE. Mot vi the = mot lan mo -> dong, ke ca khi TP tung
    phan hay bi SL cat lam nhieu manh.

    Vi sao phai dung lai: ca ba san deu khong tra ve position id trong lich su
    fill. TP tung phan sinh ra nhieu order_id khac nhau nhung van la MOT lenh
    theo cach hieu cua nguoi giao dich - dem theo order_id se thoi phong so lenh
    trong ngay va lam quota het som gia.

    Cach nhan biet: fill co realized_pnl khac 0 la fill DONG bot vi the; bang 0
    la fill MO hoac them vao. Cong don khoi luong co dau, ve 0 la dong vi the.

    Fill dau ngay ma da co realized_pnl nghia la vi the duoc mo tu HOM TRUOC -
    danh dau carried_in, va no khong tinh vao quota hom nay.
    """
    ordered = sorted(fills, key=lambda f: int(f.get("ts") or 0))
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    size = 0.0

    def _open(carried: bool) -> dict[str, Any]:
        return {"position_id": f"P{len(groups) + 1}", "carried_in": carried,
                "closed": False, "fills": []}

    def _close(group: dict[str, Any]) -> None:
        group["closed"] = True
        groups.append(group)

    for index, fill in enumerate(ordered):
        reducing = abs(float(fill.get("realized_pnl") or 0.0)) > 0
        qty = float(fill.get("qty") or 0.0)
        delta = qty if str(fill.get("side", "")).lower() == "buy" else -qty

        if current is None:
            current = _open(carried=reducing)
            size = 0.0

        fill["position_id"] = current["position_id"]
        current["fills"].append(fill)
        previous = size
        size += delta

        if current["carried_in"]:
            # Khong biet vi the mang sang lon bao nhieu nen size khong dang tin.
            # Dong nhom khi het chuoi fill dong, tuc fill ke tiep la fill mo.
            nxt = ordered[index + 1] if index + 1 < len(ordered) else None
            if nxt is None or abs(float(nxt.get("realized_pnl") or 0.0)) == 0:
                _close(current)
                current = None
                size = 0.0
        elif abs(size) < _SIZE_EPS:
            _close(current)
            current = None
            size = 0.0
        elif previous != 0 and (previous > 0) != (size > 0):
            # Dao chieu trong mot fill: dong vi the cu, mo vi the moi ngay
            _close(current)
            current = _open(carried=False)
            fill["position_id"] = current["position_id"]
            current["fills"].append(fill)

    if current is not None:          # con mo den cuoi ngay
        groups.append(current)

    for group in groups:
        rows = group["fills"]
        opening = [f for f in rows if abs(float(f.get("realized_pnl") or 0.0)) == 0]
        group["side"] = ("long" if str(opening[0].get("side", "")).lower() == "buy"
                         else "short") if opening else (
            "short" if str(rows[0].get("side", "")).lower() == "sell" else "long")
        group["fill_count"] = len(rows)
        group["order_ids"] = sorted({f["order_id"] for f in rows if f.get("order_id")})
        group["opened_at"] = rows[0].get("time") or ""
        group["closed_at"] = rows[-1].get("time") or "" if group["closed"] else ""
        group["realized_pnl"] = round(
            sum(float(f.get("realized_pnl") or 0.0) for f in rows), 4)
        group["fee"] = round(sum(float(f.get("fee") or 0.0) for f in rows), 4)
    return groups


def _position_counts(groups: list[dict[str, Any]]) -> dict[str, int]:
    """Chi vi the MO trong ngay moi tinh vao quota. Vi the mang tu hom truoc sang
    da tinh vao quota cua hom do roi - tinh lai la phat nguoi dung hai lan."""
    return {
        "opened_today": sum(1 for g in groups if not g["carried_in"]),
        "closed_today": sum(1 for g in groups if g["closed"]),
        "carried_in": sum(1 for g in groups if g["carried_in"]),
        "still_open": sum(1 for g in groups if not g["closed"]),
    }


# Cap khong niem yet tren san se loi moi lan goi. Nho lai trong mot luc de
# khong lam cham get_today_status - vi du XAUUSDT co tren Binance nhung khong
# co tren OKX. Quen sau TTL de neu san niem yet them thi tu nhan ra.
_SYMBOL_MISS: dict[str, float] = {}
_SYMBOL_MISS_TTL = 1800.0


def _symbol_recently_failed(key: str) -> bool:
    at = _SYMBOL_MISS.get(key)
    return bool(at and (time.time() - at) < _SYMBOL_MISS_TTL)


def _fill_positions(fills: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    """Gom fill cua MOT cap thanh vi the, position_id dang {SYMBOL}-P{n} (get_fills va #OFF-PLAN dung chung).

    Gom vi the RIENG tung cap: khoi luong cong don cua BTC va ETH khong the
    tron vao mot chuoi, tron vao la ranh gioi vi the sai hoan toan.
    """
    for row in fills:
        row["time"] = _stamp(row["ts"])
        row["symbol"] = symbol
    positions = _group_fills_into_positions(fills)
    for group in positions:
        group["symbol"] = symbol
        group["position_id"] = f"{symbol}-{group['position_id']}"
    return positions


async def _account_day_one(client, adapter, day: str, symbol: str) -> dict[str, Any]:
    """Fill + settlement cua MOT cap trong mot ngay."""
    start_ms, end_ms = _day_window_ms(day)
    fills = await adapter.fills(client, start_ms, end_ms, symbol)
    settlements = await adapter.settlements(client, start_ms, end_ms, symbol)
    for row in settlements:
        row["time"] = _stamp(row["ts"])
        row["symbol"] = symbol
    # TM - #OFF-PLAN - tach phan gom vi the ra ham dung chung
    positions = _fill_positions(fills, symbol)
    return {
        "symbol": symbol,
        "market_symbol": adapter.market_symbol(symbol),
        "fills": fills,
        "settlements": settlements,
        "positions": positions,
        "position_counts": _position_counts(positions),
        "totals": _summarize(settlements),
    }


async def _account_day(day: str, symbols: list[str] | None = None) -> dict[str, Any]:
    """Gop du lieu tai khoan cua NHIEU cap trong mot ngay.

    Danh sach cap lay tu trang admin - them cap o do la tu dong duoc doc luon,
    khong phai khai lai o dau.

    Cap nao san khong niem yet thi ghi vao 'errors' roi di tiep, khong lam hong
    ca lan goi. Mot cap loi khong duoc keo theo bon cap con lai.
    """
    adapter = _exchange()
    targets = [s.upper() for s in (symbols or _symbols())]
    gate = asyncio.Semaphore(int(ACCOUNT.get("max_concurrent_requests", 4)))

    async def one(client, symbol):
        cache_key = f"{adapter.name}:{adapter.market_symbol(symbol)}"
        if _symbol_recently_failed(cache_key):
            return symbol, None, "bo qua tam thoi - lan truoc san khong co cap nay"
        async with gate:
            try:
                return symbol, await _account_day_one(client, adapter, day, symbol), None
            except Exception as exc:
                _SYMBOL_MISS[cache_key] = time.time()
                return symbol, None, f"{type(exc).__name__}: {exc}"

    async with httpx.AsyncClient() as client:
        done = await asyncio.gather(*[one(client, s) for s in targets])

    per_symbol: dict[str, Any] = {}
    errors: dict[str, str] = {}
    fills: list[dict[str, Any]] = []
    settlements: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    for symbol, data, error in done:
        if error:
            errors[symbol] = error
            continue
        per_symbol[symbol] = data
        fills.extend(data["fills"])
        settlements.extend(data["settlements"])
        positions.extend(data["positions"])

    counts = {"opened_today": 0, "closed_today": 0, "carried_in": 0, "still_open": 0}
    for data in per_symbol.values():
        for key in counts:
            counts[key] += data["position_counts"][key]

    return {
        "date": day,
        "exchange": adapter.name,
        "symbols": sorted(per_symbol),
        "errors": errors,
        "per_symbol": per_symbol,
        "fills": fills,
        "order_ids": sorted({f["order_id"] for f in fills if f.get("order_id")}),
        "positions": positions,
        "position_counts": counts,
        "settlements": settlements,
        "totals": _summarize(settlements),
    }


async def _open_positions(symbols: list[str] | None = None) -> dict[str, Any]:
    """Vi the dang mo tren tat ca cac cap dang theo doi."""
    adapter = _exchange()
    targets = [s.upper() for s in (symbols or _symbols())]
    gate = asyncio.Semaphore(int(ACCOUNT.get("max_concurrent_requests", 4)))

    async def one(client, symbol):
        cache_key = f"{adapter.name}:{adapter.market_symbol(symbol)}"
        if _symbol_recently_failed(cache_key):
            return symbol, [], None
        async with gate:
            try:
                rows = await adapter.positions(client, symbol)
                for row in rows:
                    row["tracked_symbol"] = symbol
                return symbol, rows, None
            except Exception as exc:
                _SYMBOL_MISS[cache_key] = time.time()
                return symbol, [], f"{type(exc).__name__}: {exc}"

    async with httpx.AsyncClient() as client:
        done = await asyncio.gather(*[one(client, s) for s in targets])

    positions: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for symbol, rows, error in done:
        if error:
            errors[symbol] = error
        positions.extend(rows)

    return {
        "exchange": adapter.name,
        "symbols_checked": targets,
        "errors": errors,
        "checked_at": _now_iso(),
        "open_positions": len(positions),
        "positions": positions,
        "flat": not positions,
    }

@mcp.tool()
async def get_positions(symbol: str = "") -> dict[str, Any]:
    """Vi the dang mo THAT TREN SAN, tren MOI cap dang theo doi.

    symbol: bo trong = tat ca cac cap trong danh sach o trang admin. OKX doi ma cap
      theo watcher.okx_symbol_map (vd ETHUSDT -> ETH-USDT-SWAP), TM - #ENTRY-ALERT.
    Day la su that, khong phai nhat ky tu khai.
    """
    return await _open_positions([_resolve_symbol(symbol)] if symbol else None)


@mcp.tool()
async def get_fills(date: str = "", symbol: str = "") -> dict[str, Any]:
    """Lenh da khop trong ngay, lay thang tu san, GOM THEO VI THE.

    symbol: bo trong = tat ca cac cap dang theo doi.
    Mot vi the co the gom nhieu fill: mo, TP tung phan, SL. Chung cung
    position_id. Dem so lenh trong ngay phai dem vi the, khong dem fill hay order.
    """
    data = await _account_day(date or _today(),
                              [_resolve_symbol(symbol)] if symbol else None)
    counts = data["position_counts"]
    return {
        "date": data["date"],
        "exchange": data["exchange"],
        "symbols": data["symbols"],
        "errors": data["errors"],
        "positions_opened_today": counts["opened_today"],
        "positions_carried_in": counts["carried_in"],
        "positions_still_open": counts["still_open"],
        "fill_count": len(data["fills"]),
        "note": ("Mot vi the = mot lenh. carried_in = mo tu hom truoc, "
                 "khong tinh vao quota hom nay."),
        "positions": [
            {k: v for k, v in group.items() if k != "fills"} | {
                "fills": [{"time": f["time"], "side": f["side"], "price": f["price"],
                           "qty": f["qty"], "fee": f["fee"],
                           "realized_pnl": f["realized_pnl"]} for f in group["fills"]]
            }
            for group in data["positions"]
        ],
    }


@mcp.tool()
async def get_account_pnl(date: str = "", symbol: str = "", strategy: str = "",
                          session_id: str = "") -> dict[str, Any]:
    """PnL THUC trong ngay, tach rieng lai/lo, phi giao dich va funding.

    symbol: bo trong = cong gop tat ca cac cap dang theo doi.
    Khac realized_pnl trong get_today_status o cho so nay lay tu sao ke cua san
    nen da bao gom phi va funding - thuong xau hon so tu khai.
    strategy / session_id: them khoi 'journal_filtered' - PnL theo strategy/phien
      lay tu NHAT KY, vi san khong biet lenh nao thuoc strategy nao.
    """
    day = date or _today()
    symbols = [_resolve_symbol(symbol)] if symbol else None
    if strategy or session_id:
        # TM - #ORB - ORB Enhancement: loc theo strategy van tra duoc khi san loi/tat
        rows = _read_json(JOURNAL_DIR / f"{day}.json", [])
        if symbols:
            rows = [r for r in rows if str(r.get("symbol") or DEFAULT_SYMBOL) == symbols[0]]
        journal = _journal_filter(rows, strategy, session_id)
        note = ("journal_filtered lay tu nhat ky tu khai (khong gom phi/funding that). "
                "San khong tach duoc PnL theo strategy.")
        try:
            data = await _account_day(day, symbols)
        except Exception as exc:
            return {"date": day, "exchange_error": str(exc),
                    "journal_filtered": journal, "journal_note": note}
        out = _account_pnl_view(data)
        out.update(journal_filtered=journal, journal_note=note)
        return out
    data = await _account_day(day, symbols)
    return _account_pnl_view(data)


def _account_pnl_view(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": data["date"],
        "exchange": data["exchange"],
        "symbols": data["symbols"],
        "errors": data["errors"],
        "totals": data["totals"],
        "by_symbol": {s: d["totals"] for s, d in data["per_symbol"].items()},
        "note": "net = realized_pnl + commission + funding. Phi va funding la so am.",
        "settlements": data["settlements"],
    }


@mcp.tool()
async def reconcile_journal(date: str = "") -> dict[str, Any]:
    """Doi chieu nhat ky tu ghi voi du lieu that tren san. Chi ra cho lech.

    Chay cuoi ngay, hoac bat cu luc nao nghi minh quen log mot lenh.
    TM - #OFF-PLAN: off_plan_pending = vi the ngoai ke hoach (ma OP-...) cua ngay chua danh gia.
    """
    day = date or _today()
    trades = _read_json(JOURNAL_DIR / f"{day}.json", [])
    journal_pnl = round(
        sum(float(t["pnl"]) for t in trades if t.get("pnl") is not None), 2)

    data = await _account_day(day)
    exchange_net = data["totals"]["net"]

    findings = []
    # Doi chieu theo VI THE, khong theo order: TP tung phan sinh nhieu order
    # nhung nguoi dung chi coi do la mot lenh, va nhat ky cung ghi mot dong.
    opened = data["position_counts"]["opened_today"]
    if opened > len(trades):
        findings.append(
            f"san ghi nhan {opened} vi the mo trong ngay nhung nhat ky chi co "
            f"{len(trades)} - co lenh chua log")
    elif len(trades) > opened:
        findings.append(
            f"nhat ky co {len(trades)} lenh nhung san chi thay {opened} vi the "
            "mo trong ngay - co lenh log nhung khong khop")

    gap = round(journal_pnl - exchange_net, 2)
    if abs(gap) >= float(ACCOUNT.get("pnl_tolerance", 0.5)):
        findings.append(
            f"PnL lech {gap}: nhat ky {journal_pnl} vs san {exchange_net}. "
            "Phan lon do phi giao dich va funding khong duoc ghi khi log tay.")

    open_trades = [t for t in trades if t.get("pnl") is None]
    if open_trades:
        findings.append(
            f"{len(open_trades)} lenh trong nhat ky chua dong - so sanh PnL chua tron ven")

    # TM - #OFF-PLAN - Vi the ngoai ke hoach chua review. Khong tinh vao 'matched': day la viec con no,
    # khong phai lech so giua nhat ky va san.
    pending = [r for r in OP_STORE.day_rows(day) if r.get("code") and r["review_status"] == "PENDING"]

    return {
        "date": day,
        "exchange": data["exchange"],
        "matched": not findings,
        "findings": findings,
        "off_plan_pending": [{"code": r["code"], "position_id": r["position_id"], "symbol": r["symbol"],
                              "side": r["side"], "classification": r["classification"], "flags": r["flags"],
                              "realized_pnl": r["realized_pnl"], "review_prompt": f"đánh giá lệnh {r['code']}"}
                             for r in pending],
        "journal": {"trades": len(trades), "realized_pnl": journal_pnl},
        "exchange_data": {
            "symbols": data["symbols"],
            "errors": data["errors"],
            "positions_opened_today": data["position_counts"]["opened_today"],
            "positions_carried_in": data["position_counts"]["carried_in"],
            "orders": len(data["order_ids"]),
            "fills": len(data["fills"]),
            **data["totals"],
            "by_symbol": {s: d["totals"] for s, d in data["per_symbol"].items()},
        },
    }


# ---------------------------------------------------------------- ORB tools
# TM - #ORB - ORB Enhancement

def _orb_as_of() -> dict[str, Any]:
    now = _orb().now()
    return {"as_of_utc": iso_utc(now), "as_of_vn": iso_vn(now), "symbol": ORB_SYMBOL}


@mcp.tool()
def list_orb_sessions(include_disabled: bool = False) -> dict[str, Any]:
    """Cac phien ORB: gio mo ke tiep (UTC + gio VN), job co dang chay khong, watch M5.

    include_disabled: True = liet ke ca phien dang tat.
    Them/sua/tat phien o trang admin (admin_path), khong can restart.
    """
    svc = _orb()
    return {**_orb_as_of(), "admin_path": ADMIN_ORB_PATH,
            "sessions": svc.list_sessions(include_disabled)}


@mcp.tool()
def get_opening_range(session_id: str = "", date: str = "") -> dict[str, Any]:
    """Opening Range = nen M15 DAU TIEN cua phien (da dong), kem ATR va co loc range.

    session_id: bo trong = moi phien dang bat.
    date: ngay theo TIMEZONE CUA PHIEN (YYYY-MM-DD). Bo trong = phien gan nhat
      (phien hom qua neu trade window cua no con chua het).
    status: no_session | pending | forming | set | data_missing.
    range_flag: ok | too_narrow | too_wide (so voi ATR H1).
    """
    svc = _orb()
    now = svc.now()
    day = orb.parse_date(date) if date else None
    rows = []
    for session in svc.resolve(session_id, include_disabled=bool(session_id)):
        rows.append(svc.opening_range(session, day or svc.default_day(session, now), now))
    return {**_orb_as_of(), "sessions": rows}


@mcp.tool()
def check_orb_signal(session_id: str = "", as_of: str = "",
                     risk_usd: float = 0.0) -> dict[str, Any]:
    """Trang thai tin hieu ORB + ke hoach lenh (entry/SL/TP/qty) neu co.

    session_id: bo trong = moi phien dang bat.
    as_of: thoi diem UTC (ISO) de xem lai qua khu. Bo trong = bay gio.
    risk_usd: KHONG con dung (BR-ORB-15) - qty = orb.margin_usd x orb.leverage / entry.
      Truyen vao chi nhan lai mot dong warnings.
    plan.rule_check: ket qua cham plan bang rule ORB (cung ham voi check_trade);
      plan truot rule hoac cham daily stop ORB -> filters.rules = fail, blocked_by
      chua "rules". orb.enabled = false -> data_status = orb_disabled.
    state: WAITING_OPEN, FORMING, RANGE_SET, BREAKOUT_LONG/SHORT,
      FAILED_BREAKOUT_LONG/SHORT, FILTERED, EXPIRED, SKIPPED, TAKEN.
    Chi doc du lieu da co - khong keo M5 (scheduler lo viec do, BR-03).
    Danh gia VSA cua trigger_candle la viec cua Claude, tool chi tra so.
    """
    svc = _orb()
    now = svc.now()
    at = orb.parse_utc(as_of) if as_of else now
    if at > now + 1000:
        raise ValueError(f"as_of o tuong lai ({iso_utc(at)} > {iso_utc(now)})")
    rows = []
    for session in svc.resolve(session_id, include_disabled=bool(session_id)):
        rows.append(svc.signal(session, svc.default_day(session, at), at,
                               risk_usd=float(risk_usd) if risk_usd else None))
    return {"as_of_utc": iso_utc(at), "as_of_vn": iso_vn(at), "symbol": ORB_SYMBOL,
            "replay": at < now - 1000, "sessions": rows,
            "note": "BAMCP khong dat lenh. Vao lenh thi check_trade -> dat tay -> "
                    "log_trade(strategy=ORB, session_id); bo thi skip_orb_session."}


@mcp.tool()
def skip_orb_session(session_id: str, reason: str) -> dict[str, Any]:
    """Bo phien ORB hom nay (vd gia da chay xa vung entry). Dung pull M5 cua phien.

    reason: bat buoc, ghi ro vi sao bo. Duoc luu vao journal ORB (variant=skipped)
      de thong ke so lan bo lo. Phien da TAKEN/da dung thi tra trang thai hien tai.
    """
    return _orb().skip(session_id, reason)


@mcp.tool()
async def backtest_orb(from_date: str, to_date: str, session_ids: list[str] | None = None,
                       split_date: str = "", overrides: dict[str, Any] | None = None,
                       initial_equity: float = 100.0,
                       rules_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Backtest ORB tren M5 lich su, tra ve ban tom tat (khong tra du lieu tho).

    from_date / to_date: YYYY-MM-DD (ngay cua phien).
    session_ids: bo trong = moi phien dang bat.
    split_date: moc chia in-sample / out-of-sample (mac dinh 70% dau la in-sample).
    overrides: ghi de tham so, vd {"entry_mode": "retest", "tp_r": 2}.
    rules_override: thu bo rule ORB khac ma khong sua rules.json, vd
      {"min_take_profit_points": 900, "max_or_atr_ratio": 1.2} (BR-ORB-14).
    Moi lenh gia lap duoc cham bang rule ORB hien hanh: ket qua co rules_used,
    rejected_by_rule (dem theo rule) va stopped_days (ngay cham daily stop ORB).
    Lan dau phai tai M5 lich su nen co the > 60 giay: khi do tra status=running
    kem run_id, goi get_backtest_result(run_id) sau.
    """
    svc = _orb()
    from_day, to_day = orb.parse_date(from_date), orb.parse_date(to_date)
    today = datetime.fromtimestamp(svc.now() / 1000, ZoneInfo("UTC")).date()
    if to_day < from_day:
        raise ValueError("to_date phai sau from_date")
    if to_day > today:
        raise ValueError(f"to_date khong duoc o tuong lai (hom nay UTC: {today})")
    if (to_day - from_day).days > 5 * 366:
        raise ValueError("khoang backtest toi da 5 nam")
    split_day = orb.parse_date(split_date) if split_date else None
    if split_day and not from_day < split_day <= to_day:
        raise ValueError("split_date phai nam trong (from_date, to_date]")
    if float(initial_equity) <= 0:
        raise ValueError("initial_equity phai > 0")
    flat = orb.normalize_overrides(overrides)
    if session_ids:
        sessions = [svc.resolve(sid, include_disabled=True)[0] for sid in session_ids]
    else:
        sessions = svc.resolve()
    if not sessions:
        raise ValueError("khong co phien ORB nao dang bat de backtest")
    # TM - #ORB-RULES - ORB Rule Set: rules_override chi ap cho lan chay nay
    if rules_override is not None and not isinstance(rules_override, dict):
        raise ValueError("rules_override phai la object {truong: gia tri}")
    ro = {str(k).strip(): orb_rules.coerce(str(k).strip(), v)
          for k, v in (rules_override or {}).items()} or None
    for session in sessions:
        svc.params(session, flat, rules_override=ro)   # sai thi bao ngay, truoc khi tai du lieu
        errors = orb_rules.consistency(svc.rules_for(session, override=ro)["values"],
                                       svc.window_minutes(session))
        if errors:
            raise ValueError(f"rules_override khong hop le cho phien {session['session_id']}: "
                             + "; ".join(errors))
    request = {"from_day": from_day, "to_day": to_day, "split_day": split_day,
               "overrides": flat, "initial_equity": float(initial_equity),
               "rules_override": ro}
    return await ORB_BACKTEST.run(
        request, sessions, ORB_CFG, svc.news_days(), rules=svc.rules_block(),
        max_margin_per_trade=_load_rules(ORB_SYMBOL).get("max_margin_per_trade"))


@mcp.tool()
def get_backtest_result(run_id: str) -> dict[str, Any]:
    """Ket qua mot lan backtest_orb hoac backtest_time_windows (run_id bat dau
    bang 'tw-'). status: running | done | error."""
    # TM - #GANN-TW - Gann Time Windows: run cua time windows doc o thu muc rieng,
    # va khong doi ORB phai bat
    rid = str(run_id or "").strip()
    if rid.startswith(GANN_RUN_PREFIX):
        if not rid or "/" in rid or "\\" in rid or ".." in rid:
            raise ValueError("run_id khong hop le")
        path = GANN_BACKTEST_DIR / rid / "result.json"
        if not path.exists():
            recent = sorted((p.name for p in GANN_BACKTEST_DIR.iterdir() if p.is_dir()),
                            reverse=True)[:10] if GANN_BACKTEST_DIR.exists() else []
            raise ValueError(f"khong co backtest '{rid}'. Cac run gan day: {recent}")
        return _read_json(path, {})
    _orb()
    try:
        return ORB_BACKTEST.result(run_id)
    except ValueError as exc:
        raise ValueError(f"{exc}. Cac run gan day: {ORB_BACKTEST.runs(10)}") from None


@mcp.tool()
def get_orb_config() -> dict[str, Any]:
    """Cau hinh ORB dang ap dung (read-only): tham so chung, tham so da giai cho tung
    phien (sau override), duong dan du lieu.

    Rule ORB (quota, SL/TP/R:R, daily stop, sizing, min/max_or_atr_ratio, tham so
    chien luoc) nam trong rules.json - khoi orb_rules / orb_resolved; moi phien co
    'rules' voi {value, source} (session / symbol / defaults). Sua o /admin/orb
    hoac update_rules(orb=true). Tham so ky thuat (khung, ATR, scheduler, phi)
    van o config.yaml. warnings: field yaml cu bi bo qua (deprecated) va cau hinh
    khong bao gio dat (rule_infeasible / liq_unsafe) theo ATR H1 hien tai."""
    svc = _orb()
    sessions = svc.resolve(include_disabled=True)
    # TM - #ORB-RULES - ORB Rule Set
    block = svc.rules_block()
    base = svc.rules_for()
    snapshot = svc.market_snapshot()

    def session_rules(session: dict[str, Any]) -> dict[str, Any]:
        resolved = svc.rules_for(session)
        return {f: {"value": resolved["values"].get(f), "source": resolved["sources"].get(f)}
                for f in orb_rules.SESSION_FIELDS}

    return {
        **_orb_as_of(),
        "enabled": bool(ORB_ENABLED and block["enabled"]),
        "module_enabled": ORB_ENABLED,
        "orb_rules_enabled": block["enabled"],
        "config": ORB_CFG,
        "orb_rules": block,
        "orb_resolved": base,
        "max_orb_trades_per_day": base["values"].get("max_trades_per_day"),
        "max_orb_trades_per_day_source": (
            f"rules.json orb ({base['sources'].get('max_trades_per_day') or 'chua dat'})"),
        "effective_defaults": svc.params(),
        "sessions": [{"session_id": s["session_id"], "enabled": s["enabled"],
                      "overrides": s["overrides"], "params": svc.params(s),
                      "rules": session_rules(s)}
                     for s in sessions],
        "market": snapshot,
        "warnings": orb_rules.deprecated_fields(ORB_CFG) + _orb_warnings(block, snapshot=snapshot),
        "news_days": svc.news_days(),
        "paths": {"sessions": str(ORB_SESSIONS_FILE), "news_days": str(ORB_NEWS_FILE),
                  "rules": str(RULES_FILE),
                  "m5_history": str(ORB_HISTORY_DIR), "backtests": str(ORB_BACKTEST_DIR),
                  "state": str(ORB_STATE_DIR), "logs": str(ORB_LOG_DIR),
                  "skips": str(ORB_SKIPS_DIR)},
        "admin_path": ADMIN_ORB_PATH,
        "note": ("Moi gio deu tinh theo timezone cua tung phien (tu xu ly DST), hien thi "
                 "UTC + gio VN. Phi va truot gia da tinh vao R."),
    }


# -------------------------------------------------------- Gann time windows tools

# TM - #GANN-TW - Gann Time Windows
@_tool_with_reasons()
def get_pivots(symbol: str = "", timeframe: str = "1d", level: str = "",
               since: str = "", limit: int = 30) -> dict[str, Any]:
    """Pivot swing chart Gann - dinh/day da duoc xac nhan dao chieu.

    timeframe: 1w (pivot major) hoac 1d.
    level: major | intermediate | minor. Bo trong = tat ca.
    since: 'YYYY-MM-DD', chi lay pivot tu ngay nay tro di.
    Tra pivot MOI NHAT TRUOC.

    Day KHONG phai swing fractal cua get_context: pivot o day chi duoc tinh la
    pivot sau khi co du nen dao chieu, nen confirmed_at luon muon hon time. Do
    la gia tri cua no - no la cai ma luc do thuc su da biet.
    """
    _gann_require()
    sym = _gann_symbol(symbol)
    pivots, doc = _gann_frame(sym, timeframe)

    want = level.strip().lower()
    if want and want not in gann_pivots.LEVELS:
        raise ValueError(f"level khong hop le: {level}. Cho phep: {list(gann_pivots.LEVELS)}")
    since_ms = klines_coverage.parse_time_bound(since, TZ)

    rows = [p for p in pivots
            if (not want or p.get("level") == want)
            and (since_ms is None or p["time_ms"] >= since_ms)]
    rows.sort(key=lambda p: p["time_ms"], reverse=True)
    total = len(rows)
    cap = max(1, min(int(limit or 30), 200))
    return {
        "symbol": sym,
        "timeframe": timeframe.strip().lower(),
        "level": want or "all",
        "computed_at": doc.get("computed_at"),
        "matched": total,
        "returned": min(total, cap),
        "pivots": [_gann_public(p) for p in rows[:cap]],
        "warnings": doc.get("warnings") or [],
    }


# TM - #GANN-TW - Gann Time Windows
@_tool_with_reasons()
def get_swing_state(symbol: str = "", timeframe: str = "1d") -> dict[str, Any]:
    """Xu huong, chan dang chay, va overbalance thoi gian/gia theo Gann.

    time_overbalanced = nhip hoi hien tai DAI hon moi nhip hoi truoc trong cung
    xu huong. price_overbalanced = SAU hon moi nhip truoc. Gann coi do la dau
    hieu xu huong doi, khong phai mot nhip hoi binh thuong nua.

    Dang chay cung chieu xu huong thi ca hai la false - khong co gi de so.
    Doc 'note' truoc, no gom ca ket luan trong mot cau.
    """
    _gann_require()
    return _gann_swing_state(_gann_symbol(symbol), timeframe)


def _gann_swing_state(sym: str, timeframe: str) -> dict[str, Any]:
    """Than cua get_swing_state - get_context dung chung."""
    pivots, doc = _gann_frame(sym, timeframe)
    tf = timeframe.strip().lower()
    try:
        closed, _ = _split_closed(_load_bars(sym, tf), tf)
    except Exception as exc:
        raise ValueError(f"khong doc duoc nen {tf} cua {sym}: {exc}") from exc

    state = gann_pivots.swing_state(pivots, closed, tf)
    # Doi epoch ms sang ngay, giong get_pivots: nen 1d/1w luon mo 07:00 nen ngay
    # la du dinh danh, con epoch ms thi khong ai doc duoc bang mat.
    leg = state.get("current_leg")
    if leg:
        pivot = leg["from_pivot"]
        pivot["date"] = _bar_date({"open_time": pivot.pop("time_ms")})
    for item in state.get("corrections_in_trend") or []:
        item["from_date"] = _bar_date({"open_time": item.pop("from_ms")})
        item["to_date"] = _bar_date({"open_time": item.pop("to_ms")})
    return {
        "symbol": sym,
        "computed_at": doc.get("computed_at"),
        "pivot_count": len(pivots),
        "last_closed_bar": _bar_time(closed[-1]) if closed else None,
        **state,
        "warnings": doc.get("warnings") or [],
    }


# TM - #GANN-TW - Gann Time Windows
@_tool_with_reasons()
def get_time_windows(symbol: str = "", horizon_days: int = 30, min_score: float = 0,
                     as_of: str = "", max_windows: int = 5) -> dict[str, Any]:
    """Cua so thoi gian Gann: nhung ngay ma nhieu phep dem chu ky cung tro vao.

    Tu moi pivot da xac nhan, dem ra cac chu ky (cycle), ky niem nam
    (anniversary), moc theo mua (seasonal), do dai chan song lap lai
    (swing_duration), cong su kien vi mo nhap tay (event). Ngay nao nhieu phep
    dem cung roi vao thi diem cao.

    horizon_days: nhin truoc bao nhieu ngay tinh tu as_of.
    min_score: 0 = dung nguong trong config.
    as_of: 'YYYY-MM-DD' = tinh nhu dang dung o cuoi ngay do, chi dung pivot luc
      do da biet. Bo trong = hien tai. Dung de kiem tra va backtest.

    CHU Y: day la GIA THUYET ve thoi diem, khong noi gia se di huong nao. Chua
    duoc kiem chung cho toi khi co backtest_time_windows - dung dung mot minh
    cua so nao de de xuat vao lenh.
    """
    _gann_require()
    return _gann_time_windows(_gann_symbol(symbol), horizon_days, min_score, as_of,
                              max_windows)


def _gann_time_windows(sym: str, horizon_days: int = 30, min_score: float = 0,
                       as_of: str = "", max_windows: int = 5) -> dict[str, Any]:
    """Than cua get_time_windows - get_context dung chung."""
    day, as_of_ms = _gann_as_of(as_of)
    horizon = max(1, min(int(horizon_days or 30), 365))
    pivots, warnings = _gann_window_pivots(sym, as_of_ms)
    events, event_warnings = _gann_events()
    result = gann_windows.build(
        pivots, day, horizon, _gann_cfg(), events, sym,
        min_score=float(min_score or 0) or None,
        max_windows=max(1, min(int(max_windows or 5), 20)))
    for window in result["windows"]:
        window["hits"] = [_gann_compact_hit(h) for h in window["hits"]]
    return {
        "symbol": sym,
        **result,
        "pivots_used": len(pivots),
        "config_version": _gann_config_version(),
        "warnings": warnings + event_warnings
        + ([_GANN_CFG_CACHE["warning"]] if _GANN_CFG_CACHE.get("warning") else []),
    }


# TM - #GANN-TW - Gann Time Windows
@_tool_with_reasons()
async def backtest_time_windows(symbol: str = "", start: str = "", end: str = "",
                                lead_days: int = 1, permutations: int = 200,
                                seed: int = 42, exclude_events: bool = False
                                ) -> dict[str, Any]:
    """Kiem chung cua so thoi gian Gann tren lich su - walk-forward, khong lookahead.

    Voi moi ngay t trong [start, end]: tinh diem nhu dang dung o cuoi ngay
    t - lead_days (chi pivot da xac nhan luc do), roi so ngay trong cua so voi
    ngay ngoai cua so tren 3 chi so: range_atr (bien do / ATR), volume_z,
    pivot_near (co buoc ngoat that gan do khong).

    start/end: 'YYYY-MM-DD'. Bo trong = tu nen 1d dau tien + warmup_days den nen
      da dong gan nhat.
    permutations: so lan xao tron cho permutation test (p-value).
    seed: cung seed thi cung ket qua.
    exclude_events: True = tat chieu su kien va bo ngay quanh su kien, de do
      RIENG phan Gann.

    Tra ban tom tat; neu chay qua ~55 giay thi tra status=running kem run_id -
    goi get_backtest_result(run_id) sau. Ket qua co suggested_weights nhung
    KHONG tu ghi vao config.
    """
    _gann_require()
    sym = _gann_symbol(symbol)
    lead = int(lead_days)
    if lead < 1:
        raise ValueError("lead_days phai >= 1: diem cua ngay t chi duoc tinh tu du lieu truoc t")
    perms = int(permutations)
    if not 0 <= perms <= 2000:
        raise ValueError("permutations phai trong [0, 2000]")

    records_1d, _ = _gann_records(sym, "1d")
    records_1w, _ = _gann_records(sym, "1w")
    for record in records_1d + records_1w:
        record["date"] = _bar_date({"open_time": record["time_ms"]})
    closed, _ = _split_closed(_load_bars(sym, "1d"), "1d")
    if len(closed) < 60:
        raise ValueError(f"{sym} chi co {len(closed)} nen 1d - can backfill_klines truoc")
    dates = [_bar_date(b) for b in closed]

    cfg_snapshot = copy.deepcopy(_gann_cfg())
    warmup = int((cfg_snapshot.get("backtest") or {}).get("warmup_days") or 120)
    first_day = date.fromisoformat(dates[0]) + timedelta(days=warmup)
    last_day = date.fromisoformat(dates[-1])
    try:
        start_day = date.fromisoformat(start) if start.strip() else first_day
        end_day = date.fromisoformat(end) if end.strip() else last_day
    except ValueError as exc:
        raise ValueError("start/end dung dang 'YYYY-MM-DD'") from exc
    start_day = max(start_day, date.fromisoformat(dates[0]) + timedelta(days=lead))
    end_day = min(end_day, last_day)
    if end_day <= start_day:
        raise ValueError(f"khoang trong: {start_day} -> {end_day} (du lieu 1d: "
                         f"{dates[0]} -> {dates[-1]})")

    events, _ = _gann_events()
    request = {
        "symbol": sym, "start": start_day, "end": end_day, "lead_days": lead,
        "permutations": perms, "seed": int(seed), "exclude_events": bool(exclude_events),
        "records_1d": records_1d, "records_1w": records_1w, "bars": closed,
        "dates": dates, "events": events, "config_version": _gann_config_version(),
        "cfg": cfg_snapshot,
    }
    stamp = datetime.now(TZ).strftime("%Y%m%dT%H%M%S")
    run_id = f"{GANN_RUN_PREFIX}{stamp}-{secrets.token_hex(3)}"
    _write_json(GANN_BACKTEST_DIR / run_id / "result.json",
                {"run_id": run_id, "status": "running", "kind": "time_windows",
                 "symbol": sym, "started_at": _now_iso()})
    task = asyncio.create_task(asyncio.to_thread(_gann_backtest_execute, run_id, request))
    GANN_BACKTEST_TASKS.add(task)
    task.add_done_callback(GANN_BACKTEST_TASKS.discard)
    done, _ = await asyncio.wait({task}, timeout=55.0)
    if done:
        return _gann_backtest_summary(task.result())
    return {"run_id": run_id, "status": "running",
            "note": "Backtest dang chay nen. Goi get_backtest_result(run_id) sau."}


# TM - #GANN-TW - Gann Time Windows
@_tool_with_reasons()
def recompute_pivots(symbol: str = "") -> dict[str, Any]:
    """Tinh lai pivot tu dau va ghi lai cache.

    Binh thuong khong can goi: cache tu het hieu luc khi co nen moi dong, khi
    doi config, hay khi doi pivot thu cong. Goi khi vua backfill them lich su cu,
    hoac khi muon chac chan.

    symbol bo trong = moi cap dang bat Gann o trang /admin/gann.
    """
    _gann_require()
    targets = [_gann_symbol(symbol)] if symbol else _gann_symbols()
    rows = []
    for sym in targets:
        doc = _gann_doc(sym, force=True)
        rows.append({
            "symbol": sym,
            "path": str(_gann_pivot_path(sym)),
            "timeframes": {
                tf: {"pivots": block.get("live", 0),
                     "da_bo": len(block.get("pivots") or []) - block.get("live", 0),
                     "bars": block.get("bars", 0),
                     "first_bar": _bar_time({"open_time": block.get("first_bar_ms") or 0}),
                     "last_bar": _bar_time({"open_time": block.get("last_bar_ms") or 0})}
                for tf, block in (doc.get("timeframes") or {}).items()
            },
            "warnings": doc.get("warnings") or [],
        })
    return {"recomputed_at": _now_iso(), "symbols": rows}


# --------------------------------------------------------- Gann price levels

# TM - #GANN-PL - Gann Price Levels
PL_YAML: dict[str, Any] = CFG.get("gann_price_levels") or {}


def _pl_cfg() -> dict[str, Any]:
    """Khoi gann_price_levels dang hieu luc. KHONG sua dict tra ve."""
    return PL_YAML


def _pl_config_version() -> str:
    """Hash cua RIENG khoi gann_price_levels (R4)."""
    raw = json.dumps(_pl_cfg(), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


class _PLInputError(ValueError):
    """Tham so sai: tool tra {"error", "hint"} thay vi nem ra MCP (R6)."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


def _pl_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, (ValueError, FileNotFoundError)):
        hint = getattr(exc, "hint", "") or "kiem tra lai tham so theo mo ta cua tool"
        return {"error": str(exc), "hint": hint}
    # Loi lap trinh: van khong nem ra MCP (R6), nhung de lai traceback trong log
    import traceback
    traceback.print_exc()
    return {"error": f"loi noi bo: {type(exc).__name__}: {exc}",
            "hint": "xem log server (docker compose logs)"}


def _pl_tool(**options: Any):
    """Nhu @mcp.tool(), nhung moi loi thanh JSON {"error", "hint"} - khong nem ra MCP."""
    register = mcp.tool(**options)

    def decorator(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapped(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    return _pl_error(exc)
        else:
            @functools.wraps(fn)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    return _pl_error(exc)
        register(wrapped)
        return fn

    return decorator


def _pl_context(symbol: str, as_of: str) -> dict[str, Any]:
    """Moi thu cac tool gia Gann can, cat dung tai as_of (R1, R2).

    Nen: chi nen 1d DA DONG truoc cuoi ngay as_of. Pivot: pivot da biet tai as_of
    tu kho cua get_pivots (known_ms <= as_of, chat hon confirmed_at <= as_of: nen
    xac nhan chi dong sau mot khung). Swing state: chinh ham cua get_swing_state,
    tinh tren dung tap nen va pivot do.
    """
    _gann_require()
    try:
        sym = _gann_symbol(symbol)
    except ValueError as exc:
        raise _PLInputError(str(exc), f"bat cap o trang {ADMIN_GANN_PATH}, hoac doi symbol") from exc
    try:
        day, as_of_ms = _gann_as_of(as_of)
    except ValueError as exc:
        raise _PLInputError(str(exc), "as_of dang 'YYYY-MM-DD'; bo trong = hien tai") from exc
    cfg = _pl_cfg()
    warnings: list[str] = []
    try:
        raw = _load_bars(sym, "1d")
    except FileNotFoundError as exc:
        raw = []
        warnings.append(str(exc))
    span = _tf_ms("1d")
    bars = [b for b in raw if (b.get("close_time") or b["open_time"] + span - 1) <= as_of_ms]
    dates = [_bar_date(b) for b in bars]
    period = int(cfg.get("atr_period") or 14)
    atr, used = gann_levels.atr_value(bars, period)
    if bars and used < period:
        warnings.append(f"ATR tinh tren {used} nen, it hon atr_period {period}")

    if str(cfg.get("pivot_timeframe") or "1d") == "1w":
        weekly, _ = _gann_frame(sym, "1w", as_of_ms)
        pivots = [{**p, "date": _bar_date({"open_time": p["time_ms"]})} for p in weekly]
        pivot_warnings: list[str] = []
    else:
        pivots, pivot_warnings = _gann_window_pivots(sym, as_of_ms)
    warnings.extend(pivot_warnings)
    daily, _ = _gann_frame(sym, "1d", as_of_ms)
    state = gann_pivots.swing_state(daily, bars, "1d") if bars else None
    ref = bars[-1] if bars else None
    decimals = gann_levels.tick_decimals(sym, cfg.get("tick_decimals"),
                                         float(ref["close"]) if ref else 0.0)
    return {
        "symbol": sym, "day": day, "as_of_ms": as_of_ms, "bars": bars, "dates": dates,
        "atr": atr, "ref_price": float(ref["close"]) if ref else None,
        "ref_bar_time": _bar_time(ref) if ref else None, "decimals": decimals,
        "pivots": pivots, "swing": state,
        "leg": gann_levels.leg_extremes(state, bars, dates),
        "warnings": warnings,
    }


def _pl_head(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cac field goc chung cua moi tool (R4), tru note/warnings."""
    dec = ctx["decimals"]
    return {
        "symbol": ctx["symbol"],
        "as_of": ctx["day"].isoformat(),
        "ref_price": round(ctx["ref_price"], dec) if ctx["ref_price"] is not None else None,
        "ref_bar_time": ctx["ref_bar_time"],
        "atr": round(ctx["atr"], dec) if ctx["atr"] is not None else None,
    }


def _pl_tail(note: str, warnings: list[str]) -> dict[str, Any]:
    seen: list[str] = []
    for item in warnings:
        if item not in seen:
            seen.append(item)
    return {"note": note, "warnings": seen, "config_version": _pl_config_version(),
            "computed_at": _now_iso()}


def _pl_source_text(src: dict[str, Any]) -> str:
    """Mot nguon cua zone thanh mot dong - object 5 truong ton ~170 byte moi nguon
    khi in JSON, 12 zone x 4 nguon la vuot nguong ~8 KB. Module thuan van tra
    object day du cho backtest.
    """
    text = f"{src['anchor']} {src['deg']:+d} = {src['price']} ({src['score']})"
    if src.get("harmonic_of"):
        text += f" dieu hoa voi {src['harmonic_of']}"
    return text


@_pl_tool()
def get_sq9_levels(symbol: str = "", as_of: str = "", anchors: str = "auto",
                   step_deg: int = 0, max_rotations: int = 0, range_pct: float = 0,
                   max_zones: int = 12) -> dict[str, Any]:
    """Vung gia Square of 9 quanh gia hien tai: da gop, cham diem, kem lich su phan ung.

    Moi anchor A sinh muc L = (sqrt(A) + theta/180)^2, theta buoc step_deg. Muc
    gan nhau gop thanh zone; diem zone = trong so bac anchor x trong so goc. Moi
    zone kem so lan gia da cham, da bat lai (reactions), da pha (breaks).

    symbol: bo trong = cap dau tien dang bat Gann.
    as_of: 'YYYY-MM-DD' = tinh nhu dang dung o cuoi ngay do. Bo trong = hien tai.
    anchors: "auto" (pivot gan day + dau/dinh chan song dang chay) hoac danh sach
      gia cach nhau dau phay, vd "87385.1,74909.4".
    step_deg: 45 | 90 | 180 | 360. 0 = theo config.
    max_rotations, range_pct: 0 = theo config. max_zones: so zone toi da tra ve.

    Doc 'note' truoc. nearest_above/below = zone gan gia nhat moi phia;
    strongest_above/below = zone diem cao nhat moi phia; zones = thang gia giam dan.
    Warning "anchor dieu hoa" nghia la hai anchor sinh cung mot day muc - zone
    trung nhau do cau truc, KHONG phai hoi tu doc lap.

    CHU Y: day la GIA THUYET ve moc gia, khong noi gia se di huong nao. Chua duoc
    kiem chung cho toi khi co backtest_price_levels - khong dung mot minh de de
    xuat vao lenh.
    """
    cfg = _pl_cfg()
    sq = cfg.get("sq9") or {}
    step = int(step_deg or sq.get("step_deg") or 45)
    if step not in gann_levels.SQ9_STEPS:
        raise _PLInputError(f"step_deg khong hop le: {step_deg}",
                            "chi nhan 45, 90, 180, 360; 0 = theo config")
    rotations = int(max_rotations or sq.get("max_rotations") or 3)
    if not 1 <= rotations <= 50:
        raise _PLInputError(f"max_rotations khong hop le: {max_rotations}", "1..50; 0 = theo config")
    window = float(range_pct or sq.get("range_pct") or 8)
    if not 0 < window <= 50:
        raise _PLInputError(f"range_pct khong hop le: {range_pct}", "(0, 50]; 0 = theo config")
    cap = int(max_zones or 12)
    if not 1 <= cap <= 50:
        raise _PLInputError(f"max_zones khong hop le: {max_zones}", "1..50")

    manual: list[float] = []
    text = (anchors or "").strip()
    if text.lower() not in ("", "auto"):
        try:
            manual = [float(x) for x in text.split(",") if x.strip()]
        except ValueError as exc:
            raise _PLInputError(f"anchors khong hop le: {anchors!r}",
                                'dung "auto" hoac gia cach nhau dau phay, vd "87385.1,74909.4"') from exc
        if not manual or any(x <= 0 for x in manual):
            raise _PLInputError(f"anchors khong hop le: {anchors!r}", "moi gia phai > 0")

    ctx = _pl_context(symbol, as_of)
    head = _pl_head(ctx)
    params = {"step_deg": step, "max_rotations": rotations, "range_pct": window}
    warnings = list(ctx["warnings"])
    if not ctx["bars"]:
        warnings.append("khong co nen 1d da dong truoc as_of")
        return {**head, **params, "anchors_used": [], "zones": [],
                **_pl_tail("Chua co du lieu nen 1d de tinh Square of 9.", warnings)}

    weights = {str(k): float(v) for k, v in (cfg.get("level_weights") or {}).items()}
    if manual:
        chosen = [{"price": p, "type": None, "date": None, "level": "intermediate",
                   "source": "manual"} for p in manual]
    else:
        chosen = gann_levels.select_anchors(ctx["pivots"], ctx["leg"], ctx["day"],
                                            sq.get("anchors") or {}, weights)
    if len(chosen) < 2:
        warnings.append(f"chi co {len(chosen)} anchor - it hon 2, zone khong so sanh duoc")

    dec = ctx["decimals"]
    result = gann_levels.sq9_analysis(
        anchors=chosen, bars=ctx["bars"], dates=ctx["dates"], ref_price=ctx["ref_price"],
        atr=ctx["atr"], cfg=sq, reaction=cfg.get("reaction") or {}, level_weights=weights,
        step=step, max_rotations=rotations, range_pct=window, max_zones=cap, decimals=dec)
    warnings.extend(result.pop("warnings"))
    note = result.pop("note")
    for zone in result["zones"]:
        zone["sources"] = [_pl_source_text(src) for src in zone["sources"]]
    used = [{"price": a["price"], "type": a["type"], "date": a["date"], "level": a["level"],
             "source": a["source"], "sqrt": round(math.sqrt(a["price"]), 4)} for a in chosen]
    return {**head, **params, "anchors_used": used, **result, **_pl_tail(note, warnings)}


# ---------------------------------------------------------------- entry setups
# TM - #ENTRY-ALERT - Entry setups & alerts

ALERTS_YAML: dict[str, Any] = {"notify": CFG.get("notify") or {}, "watcher": CFG.get("watcher") or {},
                               # TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
                               "off_plan": CFG.get("off_plan") or {}}
# TM - #OFF-PLAN - Mac dinh cua khoi off_plan. channel "" = dung kenh notify.channel.
OFF_PLAN_DEFAULTS: dict[str, Any] = {
    "enabled": True, "poll_seconds": 15, "no_stop_grace_seconds": 60, "revenge_window_minutes": 30,
    "channel": "", "notify_classifications": list(off_plan_logic.NOTIFY_KINDS),
}
ALERTS_FILE = DATA_ROOT / str(PATHS.get("alerts_config_file") or "alerts/config.json")
ALERT_PARAM_DEFAULTS: dict[str, Any] = {
    "vol_mult": 2.0, "sma_period": 20, "body_pct": 0.6, "wick_pct": 0.6,
    "retest_low_vol_mult": 0.8, "confirm_within": 3, "entry_valid_candles": 3, "m15_filter": True,
    "rejection_test_max_vol": 0.8, "rejection_stopping_min_vol": 2.0,
}
_ALERTS_CACHE: dict[str, Any] = {"key": None, "cfg": None}
_ALERTS_LOCK = threading.Lock()


def _alerts_cfg() -> dict[str, Any]:
    try:
        stat = ALERTS_FILE.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    if _ALERTS_CACHE["cfg"] is not None and _ALERTS_CACHE["key"] == key:
        return _ALERTS_CACHE["cfg"]
    cfg = copy.deepcopy(ALERTS_YAML)
    if key is not None:
        try:
            override = _read_json(ALERTS_FILE, {})
        except Exception as exc:
            override = {}
            print(f"CANH BAO: {ALERTS_FILE.name} loi ({type(exc).__name__}) - dang dung config.yaml",
                  file=sys.stderr)
        for block in ("notify", "watcher", "off_plan"):  # TM - #OFF-PLAN
            if isinstance(override, dict) and isinstance(override.get(block), dict):
                cfg[block] = override[block]
    # TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
    cfg["off_plan"] = {**OFF_PLAN_DEFAULTS, **(cfg.get("off_plan") or {})}
    watcher = cfg["watcher"]
    watcher["defaults"] = {**ALERT_PARAM_DEFAULTS, **(watcher.get("defaults") or {})}
    # TM - #SETUP-PARAMS - Tham so theo cap nam trong SQLite, khong con doc tu file
    watcher["symbol_overrides"] = SETUPS.all_params()
    watcher["watch_symbols"] = [str(s).upper() for s in (watcher.get("watch_symbols") or [])]
    _ALERTS_CACHE.update(key=key, cfg=cfg)
    return cfg


def _alerts_save(block: str, value: dict[str, Any]) -> None:
    with _ALERTS_LOCK:
        doc = _read_json(ALERTS_FILE, {}) if ALERTS_FILE.exists() else {}
        doc = doc if isinstance(doc, dict) else {}
        doc[block] = value
        _write_json(ALERTS_FILE, doc)
        _ALERTS_CACHE.update(key=None, cfg=None)


def _parse_moment(value: Any, *, end: bool = False) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        number = int(float(value))
        return number * 1000 if number < 10_000_000_000 else number
    return klines_coverage.parse_time_bound(str(value).replace("T", " ")[:16], TZ, end=end)


def _setup_check(symbol: str, side: str, price: float, stop: float,
                 target: float) -> dict[str, Any]:
    verdict = check_trade(side=side, entry=price, stop=stop, target=target, margin_usd=0.0,
                          symbol=symbol)
    trades = _read_json(JOURNAL_DIR / f"{_today()}.json", [])
    taken = len([t for t in trades if isinstance(t, dict) and not _is_orb(t)])
    return {"would_pass": verdict["would_pass"], "rule_violations": verdict["rule_violations"],
            "rr": verdict["rr"], "trade_type": verdict["trade_type"],
            "quota": f"{taken}/{int(_load_rules(symbol)['max_trades_per_day'])}"}


async def _setup_positions(symbol: str) -> list[dict[str, Any]] | None:
    if not _account_enabled():
        return None
    return (await _open_positions([symbol]))["positions"]


# TM - #SETUP-PARAMS - Ghi tham so setup vao SQLite luc khoi dong
def _seed_setup_params() -> list[str]:
    """Ghi cap chua co dong nao trong setup_params. Khong ghi de dong da co.

    Gia tri tu config.yaml (setup_params). Neu cap da duoc chinh tay o ban truoc (alerts/config.json)
    thi cac tham so do duoc giu, vi do la lua chon cua nguoi dung, khong phai mac dinh.
    """
    seed = {str(k).upper(): v or {} for k, v in (CFG.get("setup_params") or {}).items()}
    try:
        saved = _read_json(ALERTS_FILE, {}) if ALERTS_FILE.exists() else {}
        legacy = ((saved.get("watcher") or {}).get("symbol_overrides") or {}) if isinstance(saved, dict) else {}
    except Exception as exc:
        print(f"CANH BAO: khong doc duoc {ALERTS_FILE.name} ({type(exc).__name__})", file=sys.stderr)
        legacy = {}
    legacy = {str(k).upper(): v or {} for k, v in legacy.items()}
    clean: dict[str, dict[str, Any]] = {}
    for symbol in sorted({*seed, *legacy}):
        try:
            clean[symbol] = _alert_params({**seed.get(symbol, {}), **legacy.get(symbol, {})})
        except ValueError as exc:
            print(f"CANH BAO: bo qua setup_params cua {symbol} - {exc}", file=sys.stderr)
    added = SETUPS.seed_params(clean)
    if added:
        _ALERTS_CACHE.update(key=None, cfg=None)
    return added


# TM - #ENTRY-CLOSED - Keo lai nen vua dong truoc khi watcher xet, de dung gia dong da chot
async def _watcher_refresh(symbol: str, timeframe: str) -> None:
    async with _fetch_lock(symbol, timeframe):
        async with httpx.AsyncClient() as client:
            out = await _pull_since_latest(client, symbol, timeframe, 1, "watcher")
    if not out["ok"]:
        raise RuntimeError(out["error"])


SETUPS = entry_store.SetupStore(SQLITE_PATH)
WATCHER = entry_watcher.EntryWatcher(
    SETUPS, klines=lambda: KLINE_STORE, cfg=_alerts_cfg,
    min_rr=lambda symbol: float(_load_rules(symbol)["min_rr"]),
    check=_setup_check, positions=_setup_positions, tz=TZ, span_ms=_tf_ms,
    refresh=_watcher_refresh,
    # TM - #DASHBOARD - Setup dashboard
    link=lambda setup: _dash_url(setup["symbol"], setup["bias_date"]),
    # TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach thay cho UNPLANNED_POSITION khi dang bat
    unplanned=lambda: not _op_active())


def _setup_view(setup: dict[str, Any], last: dict[str, Any] | None = None) -> dict[str, Any]:
    state = setup.get("state") or {}
    out = {k: setup[k] for k in ("id", "code", "symbol", "bias_date", "direction", "status",
                                 "zone_low", "zone_high", "trigger_type", "trigger_params",
                                 "confirm_type", "confirm_level", "entry", "stop", "target",
                                 "max_chase", "invalidate_level", "m15_filter", "tr_high",
                                 "tr_low", "note", "created_by")}
    out["expires_at"] = _stamp(setup["expires_at"])
    out["tr_since"] = _stamp(setup["tr_since"]) if setup.get("tr_since") else None
    out["created_at"] = _stamp(setup["created_at"])
    out["terminal"] = setup["status"] in entry_logic.TERMINAL
    # TM - #ENTRY-HTF - Huong khung lon va nhan setup nguoc
    out["htf"] = setup.get("htf") or {}
    out["htf_counter"] = entry_logic.htf_counter(setup["direction"], setup.get("htf"))
    # TM - #ENTRY-VOL - Dieu kien volume dang co hieu luc cua setup nay
    out["volume_rule"] = entry_logic.volume_rule(setup["trigger_type"],
                                                 WATCHER.params_for(setup["symbol"], setup))
    # TM - #DASHBOARD - Setup dashboard
    out["invalidate_after_confirm"] = bool(setup.get("invalidate_after_confirm"))
    # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
    out.update({k: setup.get(k) for k in ("tr_id", "event_ref", "expects", "phase")})
    # TM - #TR-BRANCH - Nhanh kich ban va ten tuong minh. Setup cu chua co label thi ghep tu truong dang co.
    out.update({k: setup.get(k) for k in ("branch", "entry_event", "ref_event")})
    out["label"] = setup.get("label") or entry_logic.setup_label(setup)
    out["state"] = {k: state[k] for k in ("max_chase", "x_max", "min_rr", "last_price",
                                          "came_from", "zone_alerted", "confirm_count",
                                          "entry_valid_count", "trigger", "confirm",
                                          "confirm_reached") if k in state}
    if state.get("last_candle"):
        out["state"]["last_candle"] = _stamp(state["last_candle"])
    if state.get("triggered_at"):
        out["state"]["triggered_at"] = _stamp(state["triggered_at"])
    if isinstance(state.get("pruned"), dict):
        pruned = state["pruned"]
        out["state"]["pruned"] = {**pruned, "at": _stamp(pruned["at"]) if pruned.get("at") else None,
                                  "at_ms": pruned.get("at")}
    if last:
        out["last_event"] = _event_view(last)
    return out


def _event_view(event: dict[str, Any]) -> dict[str, Any]:
    out = {k: event.get(k) for k in ("id", "event", "from_status", "to_status", "price",
                                     "details", "notified", "notify_error")}
    out["at"] = _stamp(event["at"])
    out["candle"] = _stamp(event["candle_open_time"]) if event.get("candle_open_time") else None
    if event.get("code") or event.get("symbol"):
        out["code"], out["symbol"] = event.get("code"), event.get("symbol")
        # TM - #TR-BRANCH - Nhanh kich ban TR
        out.update(label=event.get("label"), branch=event.get("branch"))
    return out


def _setup_finish(setup: dict[str, Any], status: str, event: str,
                  details: dict[str, Any], price: float | None = None) -> dict[str, Any]:
    if setup["status"] in entry_logic.TERMINAL:
        raise ValueError(f"setup {setup['code']} da ket thuc ({setup['status']})")
    ids = SETUPS.apply(setup, status, setup["state"], [{
        "event": event, "from_status": setup["status"], "to_status": status, "price": price,
        "candle_open_time": None, "details": details}])
    WATCHER.notify_threadsafe(ids[-1])
    return {"id": setup["id"], "code": setup["code"], "status": status, "event_id": ids[-1]}


# TM - #ENTRY-HTF - Huong khung lon cua setup
def _setup_trends(symbol: str) -> dict[str, str | None]:
    """Huong W/D/H4 tu swing cua nen da dong, cung nguon voi get_context.

    Server tu tinh, khong nhan tu nguoi goi: nhan la he qua cua con so, khong phai y muon.
    Thieu du lieu hoac loi thi de None, khong duoc chan viec luu setup.
    """
    out: dict[str, str | None] = {}
    for key, timeframe, _ in entry_logic.HTF_FRAMES:
        try:
            ctx = _build_context(symbol, timeframe)
            last = ctx.get("last_closed_bar")
            out[key] = entry_logic.swing_trend(
                [s["price"] for s in ctx["swing_highs"]], [s["price"] for s in ctx["swing_lows"]],
                float(last["close"])) if last else None
        except Exception as exc:
            print(f"BAMCP htf {symbol} {timeframe}: {type(exc).__name__}: {exc}", file=sys.stderr)
            out[key] = None
    return out


# TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
def _setup_tr_link(symbol: str, raw: dict[str, Any], clean: dict[str, Any],
                   active: dict[str, Any] | None) -> list[str]:
    """Setup chi lien ket toi TR, khong chep lai cau truc Wyckoff. Pha lay tu TR luc tao setup;
    tr_high/tr_low/tr_since lay tu TR lien ket, khai khac thi canh bao."""
    ref = str(raw.get("tr_id") or "").strip()
    tr = active
    if ref:
        tr = PLANS.tr(ref)
        if tr["symbol"] != symbol:
            raise ValueError(f"tr_id {ref} la TR cua {tr['symbol']}, khong phai {symbol}")
    text = str(raw.get("event_ref") or "").strip()
    known = next((e for e in (tr or {}).get("events") or [] if e["id"] == text), None) if text else None
    event_ref = known["ref"] if known else tr_logic.clean_event_ref(text)
    expects = tr_logic.event_type(raw["expects"]) if str(raw.get("expects") or "").strip() else None
    clean.update(tr_id=tr["id"] if tr else None, phase=tr["current_phase"] if tr else None,
                 event_ref=event_ref, expects=expects)
    # TM - #TR-BRANCH - event_ref la mot lan cu the cua entry_event: khac loai thi canh bao
    warnings = []
    if event_ref and clean.get("entry_event") and tr_logic.ref_type(event_ref) != clean["entry_event"]:
        warnings.append(f"event_ref {event_ref} khac entry_event {clean['entry_event']}")
    if tr is None:
        return warnings
    # TM - #TR-BRANCH - TR da xac dinh nhanh thi khong nhan setup nguoc nhanh: luu vao cung khong con nghia
    if tr.get("branch") and clean["direction"] == tr_logic.COUNTER_DIRECTION[tr["branch"]]:
        raise ValueError(f"{tr_logic.branch_reason(tr['branch_by'])} - setup {clean['direction']} nguoc nhanh, "
                         f"khong luu (TR {tr['id']})")
    ref_event = clean.get("ref_event")
    if ref_event and "#" in ref_event and not any(e["ref"] == ref_event for e in tr["events"]):
        warnings.append(f"ref_event {ref_event} chua co tren TR")
    for key, source, show in (("tr_high", "tr_high", lambda v: f"{float(v):.10g}"),
                              ("tr_low", "tr_low", lambda v: f"{float(v):.10g}"),
                              ("tr_since", "since", lambda v: _stamp(int(v))[:16])):
        given = clean.get(key)
        if given is not None and not math.isclose(float(given), float(tr[source]), rel_tol=1e-9):
            warnings.append(f"{key} {show(given)} khac TR lien ket {show(tr[source])} - dung so cua TR")
        clean[key] = tr[source]
    for name, kind in (("event_ref", tr_logic.ref_type(event_ref)), ("expects", expects)):
        if kind and not tr_logic.schematic_allows(tr["schematic"], kind):
            warnings.append(f"{name} {kind} khong khop schematic {tr['schematic']} cua TR")
    return warnings


def _save_setups(symbol: str, setups: list[dict[str, Any]], replace: bool,
                 created_by: str) -> dict[str, Any]:
    if not isinstance(setups, list):
        raise ValueError("setups phai la list")
    min_rr = float(_load_rules(symbol)["min_rr"])
    params = WATCHER.params_for(symbol)
    now = int(time.time() * 1000)
    # TM - #DASHBOARD - Setup dashboard: invalidate_level da bi pha thi setup se huy ngay nen gio dau
    price = _last_price(symbol)
    warnings = [] if price is not None or not setups else [
        f"chua co gia dong cua {symbol} - bo qua kiem tra invalidate_level theo gia hien tai"]
    # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
    active_tr = PLANS.active_tr(symbol) if setups else None
    cleaned, rejected = [], []
    for index, raw in enumerate(setups):
        try:
            clean = entry_logic.validate(
                raw, min_rr=min_rr, now_ms=now, parse_time=_parse_moment,
                default_expiry=lambda day: _parse_moment(day, end=True),
                default_day=_today(), default_m15=bool(params.get("m15_filter", True)))
            # TM - #TR-BRANCH - Nhanh kich ban va ten tuong minh: sai la tu choi kem ly do
            clean.update(entry_logic.validate_name(raw, clean["direction"]))
            if (price is not None and not clean["invalidate_after_confirm"]
                    and entry_logic.invalidate_broken(clean["direction"], clean["invalidate_level"], price)):
                raise ValueError(f"invalidate_level {clean['invalidate_level']:.10g} da bi pha tai gia "
                                 f"hien tai {price:.10g} - setup se huy ngay")
            # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
            link_warnings = _setup_tr_link(symbol, raw, clean, active_tr)
            warnings.extend(f"setup[{index}]: {w}" for w in link_warnings)
            # TM - #TR-BRANCH - label bo trong thi server sinh (can phase cua TR nen dat sau khi lien ket)
            clean["label"] = clean["label"] or entry_logic.setup_label({**clean, "symbol": symbol})
            cleaned.append((index, clean))
        except ValueError as exc:
            rejected.append({"index": index, "error": str(exc)})
    replaced = []
    plan_days: set[str] = set()
    if replace and (cleaned or not setups):
        for old in SETUPS.active(symbol):
            _setup_finish(old, "CANCELLED", "CANCELLED",
                          {"reason": "thay boi save_setups(replace=true)", "by": created_by})
            replaced.append(old["code"])
            plan_days.add(old["bias_date"])
    saved = []
    trends = _setup_trends(symbol) if cleaned else {}
    for index, clean in cleaned:
        clean["htf"] = {**trends, "bias": (_orb_bias(symbol, clean["bias_date"]) or {}).get("direction")}
        row = SETUPS.insert(symbol, clean, created_by)
        verdict = check_trade(side=clean["direction"], entry=clean["entry"], stop=clean["stop"],
                              target=clean["target"], margin_usd=0.0, symbol=symbol)
        saved.append({"index": index, "id": row["id"], "code": row["code"], "status": row["status"],
                      "bias_date": row["bias_date"], "expires_at": _stamp(row["expires_at"]),
                      "x_max": round(clean["x_max"], 4),
                      "max_chase_effective": round(clean["max_chase_effective"], 4),
                      "htf": row["htf"],
                      "htf_counter": entry_logic.htf_counter(row["direction"], row["htf"]),
                      # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
                      **{k: row[k] for k in ("tr_id", "phase", "event_ref", "expects")},
                      # TM - #TR-BRANCH - Nhanh kich ban TR
                      **{k: row[k] for k in ("label", "branch", "entry_event", "ref_event")},
                      "check_trade": {k: verdict[k] for k in ("would_pass", "rule_violations",
                                                              "rr", "trade_type")}})
    # TM - #DASHBOARD - Setup dashboard
    plan_days.update(s["bias_date"] for s in saved)
    revisions = {day: _plan_snapshot(symbol, day, "save_setups") for day in sorted(plan_days)}
    link_day = saved[0]["bias_date"] if saved else (max(plan_days) if plan_days else "")
    return {"symbol": symbol, "min_rr": min_rr, "saved": saved, "rejected": rejected,
            "replaced": replaced, "ids": [s["id"] for s in saved],
            "codes": [s["code"] for s in saved], "warnings": warnings,
            "plan_revisions": revisions, **_dash_link(symbol, link_day)}


@_tool_with_reasons()
def save_setups(symbol: str, setups: list[dict[str, Any]], replace: bool = False) -> dict[str, Any]:
    """Luu ke hoach vao lenh thanh cac setup co cau truc - watcher se canh tren nen M5 da dong.

    symbol: cap cua MOI setup trong lan goi. replace=true: huy moi setup CHUA KET THUC
      cua cap nay truoc (dung sau buoi phan tich bias moi ngay).
    Moi setup (object):
      direction long|short; zone_low < zone_high; entry; stop (long: duoi entry, short:
      tren); target; invalidate_level (long: duoi entry); trigger_type: spring|sos|
      close_above (long), utad|sow|close_below (short), rejection|retest (ca hai).
      Tuy chon: confirm_type close_above|close_below + confirm_level; max_chase (bo trong
      = tu tinh tu SL/TP/min_rr); trigger_params; m15_filter; expires_at ('YYYY-MM-DD HH:MM'
      gio VN hoac epoch ms, bo trong = het ngay bias_date); bias_date (bo trong = hom nay);
      tr_high, tr_low, tr_since; note (chi de doc, code khong kiem tra dieu kien ghi trong note).
    trigger_params chi nhan tham so ma trigger do dung, khai thua bi tu choi:
      spring: vol_mult, sma_period, ref_low | utad: vol_mult, sma_period, ref_high
      sos, sow: vol_mult, sma_period, body_pct, level
      retest: retest_low_vol_mult, sma_period, level | close_above, close_below: level
      rejection: wick_pct, sma_period, min_vol_mult, max_vol_mult
      Moi trigger nhan them entry_valid_candles; co confirm_type thi nhan them confirm_within.
    rejection BAT BUOC khai max_vol_mult (test, vol thap) va/hoac min_vol_mult (stopping
      action, vol cao), la boi so cua SMA volume M5. Lay so tu get_setup_params cua dung cap.
    Setup co x_max <= 0 (khong dat min_rr ngay tai entry) bi tu choi kem ly do. Moi setup
    hop le duoc cham check_trade dang thu. Tra ve id + code + label.
    Server tu luu htf {w, d, h4, bias}: huong W/D/H4 tinh tu swing nen da dong (up/down/range)
    va bias cua bias_date. htf_counter liet ke cac khung nguoc huong setup - khong chan luu,
    chi gan nhan trong tin va trong ket qua.
    TM - #DASHBOARD: invalidate_level da bi pha tai gia hien tai (long: gia <= muc, short: gia >=
    muc) thi setup bi tu choi. Setup co confirm_type thi invalidate_after_confirm mac dinh true:
    chi xet invalidate_level sau khi co nen dong dat confirm, nen setup breakout khong bi huy som.
    Tra them dashboard_url va plan_revisions (snapshot ke hoach moi ngay). Goi tiep save_scenarios.
    TM - #WYCKOFF-TR: tuy chon tr_id (bo trong = TR dang hieu luc), event_ref (event setup dang giao
    dich, vd "TEST#3" - co the chua xay ra), expects (event ky vong neu setup dung, vd "LPS").
    Server tu ghi phase = current_phase cua TR luc tao setup. tr_high/tr_low/tr_since lay tu TR lien
    ket; khai khac TR thi bi thay bang so cua TR va tra warnings.
    TM - #TR-BRANCH: moi setup BAT BUOC khai
      branch: accumulation (chi long) | distribution (chi short) | range (ca hai, fade bien trong pha B).
      entry_event: su kien vao lenh. Long: SPRING SHAKEOUT TEST SOS JAC LPS BU ST_B.
        Short: UT UTAD TEST_D SOW LPSY ST_D. Khac huong setup thi bi tu choi.
      Tuy chon: ref_event (su kien tham chieu, vd "SOW#1", "SC#1"); label (bo trong = server sinh
        "BTC 12/11 · SHORT · PHÂN PHỐI · Pha D · LPSY sau SOW#1 · 72400–72740").
    Ma setup: {SYM}-{yyyymmdd}-{L|S}-{ACC|DIST|RNG}-{pha}-{entry_event}-{n}, vd BTC-20261112-S-DIST-D-LPSY-1
      (pha = current_phase cua TR, X khi chua co). TR lien ket da xac dinh nhanh (xem update_tr) thi
      setup nguoc nhanh bi tu choi.
    TM - #OFF-PLAN: hai cau trong note duoc position_watcher doc (lenh ngoai ke hoach):
      "Gợi ý vốn 80% hạn mức" (ti le so voi max_margin_per_trade -> co SIZE_OVER_PLAN khi margin that vuot) va
      "Chia entry: 1/2 ..., 1/2 chờ retest LPSY 70635–70800" (lenh them trong vung retest la hop le).
    """
    return _save_setups(_resolve_symbol(symbol), setups, bool(replace), "claude")


# TM - #SETUP-PARAMS - Tham so setup theo cap
@_tool_with_reasons()
def get_setup_params(symbol: str = "") -> dict[str, Any]:
    """Tham so setup dang ap dung cho MOT cap. Luu trong SQLite, sua o trang /admin/alerts.

    Goi truoc save_setups de lay dung so cua cap. 'source' cho biet tung tham so lay tu dau:
    symbol (dat rieng cho cap) hay default. 'rejection_trigger_params' la trigger_params de
    khai cho setup rejection: 'test' khi can vol thap, 'stopping' khi can vol cao.
    """
    sym = _resolve_symbol(symbol)
    params = WATCHER.params_for(sym)
    row = SETUPS.param_row(sym)
    stored = (row or {}).get("params") or {}
    return {
        "symbol": sym, "params": params,
        "source": {k: "symbol" if k in stored else "default" for k in params},
        "rejection_trigger_params": {
            "test": {"max_vol_mult": params["rejection_test_max_vol"]},
            "stopping": {"min_vol_mult": params["rejection_stopping_min_vol"]},
        },
        "stored_by": (row or {}).get("source"),
        "updated_at": _stamp(row["updated_at"]) if row else None,
    }


@_tool_with_reasons()
def get_setups(symbol: str = "", status: str = "", date: str = "") -> dict[str, Any]:
    """Danh sach setup kem trang thai hien tai va event gan nhat.

    status: mot hoac nhieu trang thai cach nhau dau phay, hoac 'active' (chua ket thuc).
    date: bias_date 'YYYY-MM-DD'. Bo trong ca status lan date = setup dang chay + setup cua hom nay.
    TM - #TR-BRANCH: moi setup co label, branch, entry_event, ref_event. PRUNED = bi loai vi nguoc nhanh
    TR (ly do o state.pruned.reason). trs: {tr_id: {branch, branch_by, prob_accumulation,
    prob_distribution, ...}} cua cac TR ma setup lien ket.
    """
    sym = _resolve_symbol(symbol) if symbol else None
    if status or date:
        rows = SETUPS.list(symbol=sym, status=status or None, bias_date=date or None)
    else:
        seen: dict[str, dict[str, Any]] = {}
        for row in SETUPS.active(sym) + SETUPS.list(symbol=sym, bias_date=_today()):
            seen.setdefault(row["id"], row)
        rows = sorted(seen.values(), key=lambda r: (r["bias_date"], r["code"]), reverse=True)
    last = SETUPS.last_events([r["id"] for r in rows])
    return {"count": len(rows), "watcher_enabled": _watcher_active(),
            "setups": [_setup_view(r, last.get(r["id"])) for r in rows],
            # TM - #TR-BRANCH - nhanh va xac suat cua cac TR ma setup lien ket
            "trs": _tr_briefs([r.get("tr_id") for r in rows])}


@_tool_with_reasons()
def cancel_setup(id: str, reason: str) -> dict[str, Any]:
    """Huy DUNG MOT setup (id hoac code). Khong dong vao setup khac cung huong. Bat buoc ly do."""
    if not (reason or "").strip():
        raise ValueError("reason la bat buoc")
    # TM - #DASHBOARD - Setup dashboard
    return _cancel_setup(id.strip(), reason.strip(), "claude")


# TM - #DASHBOARD - Setup dashboard
def _cancel_setup(ref: str, reason: str, by: str) -> dict[str, Any]:
    setup = SETUPS.get(ref)
    out = _setup_finish(setup, "CANCELLED", "CANCELLED", {"reason": reason, "by": by})
    return {**out, "plan_revision": _plan_snapshot(setup["symbol"], setup["bias_date"], "cancel_setup"),
            **_dash_link(setup["symbol"], setup["bias_date"])}


@_tool_with_reasons()
def get_setup_events(setup_id: str = "", symbol: str = "", date: str = "") -> dict[str, Any]:
    """Lich su chuyen trang thai va tin da gui (notified / notify_error).

    setup_id: id hoac code. symbol: moi setup cua cap + canh bao he thong nhac cap do.
    date: 'YYYY-MM-DD' (gio VN) - loc theo luc xay ra event. Toi da 200 event moi nhat.
    TM - #TR-BRANCH: event kem label, branch cua setup. PRUNED = loai vi nguoc nhanh TR (details.reason);
    BRANCH_CONFLICT = setup da FILLED nguoc nhanh TR; TR_BRANCH = tin tong hop cua mot lan loai.
    trs: nhanh va xac suat cua cac TR lien quan.
    """
    sid = SETUPS.get(setup_id.strip())["id"] if setup_id else None
    start = end = None
    if date:
        start, end = _day_window_ms(date)
    rows = SETUPS.events(setup_id=sid, symbol=_resolve_symbol(symbol) if symbol else None,
                         start_ms=start, end_ms=end)
    tr_ids = [e.get("tr_id") or (e.get("details") or {}).get("tr_id") for e in rows]
    return {"count": len(rows), "events": [_event_view(e) for e in rows], "trs": _tr_briefs(tr_ids)}


# ---------------------------------------------------------------- setup dashboard
# TM - #DASHBOARD - Setup dashboard
# Tool MCP va API cua trang goi chung cac ham o day, khong viet logic hai lan.

DASH_PATH = "/setup-dashboard"
DASH_COOKIE = "bamcp_dash"
DASH_TFS = ("1d", "4h", "1h", "15m", "5m")
DASH_FILE = DATA_ROOT / str(PATHS.get("dashboard_config_file") or "dashboard/config.json")
DASH_DEFAULTS: dict[str, Any] = {
    "enabled": False, "base_url": "", "session_hours": 12, "remember_days": 30,
    "login_max_attempts": 5, "login_lockout_minutes": 15, "poll_seconds": 60,
    "vol_spike_mult": 2.0, "default_tf": "1h", "default_bars": 96, "hide_pnl": False,
    # TM - #TR-BRANCH - Thang diem xac suat tich luy / phan phoi, sua o /admin/dashboard-accounts
    "prob_weights": tr_logic.default_weights(),
}
DASH_EXCHANGE_TTL = 30.0
DASH_CHART_CACHE_SIZE = 64
CHART_BIAS_KEYS = ("direction", "summary", "phase", "key_levels", "action_zone", "updated_at")
POSITION_KEYS = ("side", "size", "entry_price", "mark_price", "unrealized_pnl", "leverage", "opened_ms")

PLANS = dashboard_store.PlanStore(SQLITE_PATH)
DASH_AUTH = dashboard_auth.DashAuth(SQLITE_PATH)
_DASH_CFG_CACHE: dict[str, Any] = {"key": None, "cfg": None}
_DASH_CHART_CACHE: OrderedDict[tuple, tuple[tuple, dict[str, Any]]] = OrderedDict()
_DASH_EXCHANGE_CACHE: dict[tuple, tuple[float, Any]] = {}


def _dash_cfg() -> dict[str, Any]:
    """Config.yaml la gia tri khoi tao; sua o trang admin thi file tren volume thang."""
    try:
        stat = DASH_FILE.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    if _DASH_CFG_CACHE["cfg"] is not None and _DASH_CFG_CACHE["key"] == key:
        return _DASH_CFG_CACHE["cfg"]
    cfg = {**DASH_DEFAULTS, **(CFG.get("dashboard") or {})}
    if key is not None:
        try:
            override = _read_json(DASH_FILE, {})
        except Exception as exc:
            override = {}
            print(f"CANH BAO: {DASH_FILE.name} loi ({type(exc).__name__}) - dang dung config.yaml",
                  file=sys.stderr)
        if isinstance(override, dict):
            cfg.update({k: v for k, v in override.items() if k in DASH_DEFAULTS})
    # TM - #TR-BRANCH - Thieu factor thi lay mac dinh; file hong thi dung mac dinh, khong lam sap tool
    try:
        cfg["prob_weights"] = tr_logic.clean_weights(cfg.get("prob_weights") or {})
    except ValueError as exc:
        print(f"CANH BAO: dashboard.prob_weights loi ({exc}) - dang dung thang diem mac dinh", file=sys.stderr)
        cfg["prob_weights"] = tr_logic.default_weights()
    _DASH_CFG_CACHE.update(key=key, cfg=cfg)
    return cfg


def _dash_clean_cfg(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("config phai la object")
    out = dict(_dash_cfg())

    def number(name: str, low: float, high: float, whole: bool = True) -> None:
        if name not in raw:
            return
        try:
            value = float(raw[name])
        except (TypeError, ValueError):
            raise ValueError(f"{name} phai la so, nhan duoc {raw[name]!r}") from None
        if not low <= value <= high or (whole and value != int(value)):
            raise ValueError(f"{name} phai tu {low:g} den {high:g}" + (" va la so nguyen" if whole else ""))
        out[name] = int(value) if whole else value

    for flag in ("enabled", "hide_pnl"):
        if flag in raw:
            out[flag] = raw[flag] in (True, 1, "1", "true", "on")
    number("session_hours", 1, 720)
    number("remember_days", 1, 365)
    number("login_max_attempts", 1, 100)
    number("login_lockout_minutes", 1, 1440)
    number("poll_seconds", 5, 3600)
    number("vol_spike_mult", 0.1, 100, whole=False)
    number("default_bars", 10, dashboard_logic.MAX_BARS)
    if "default_tf" in raw:
        if raw["default_tf"] not in DASH_TFS:
            raise ValueError(f"default_tf phai la mot trong {list(DASH_TFS)}")
        out["default_tf"] = raw["default_tf"]
    if "base_url" in raw:
        base = str(raw["base_url"] or "").strip().rstrip("/")
        if base and not base.startswith(("https://", "http://")):
            raise ValueError("base_url phai bat dau bang https://")
        out["base_url"] = base
    # TM - #TR-BRANCH - Thang diem xac suat
    if "prob_weights" in raw:
        out["prob_weights"] = tr_logic.clean_weights(raw["prob_weights"])
    return out


def _dash_url(symbol: str, day: str = "", mode: str = "live", tf: str = "") -> str | None:
    cfg = _dash_cfg()
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not cfg["enabled"] or not base:
        return None
    query = urlencode({"symbol": symbol, "date": day or _today(), "mode": mode,
                       "tf": tf or cfg["default_tf"]})
    return f"{base}?{query}"


def _dash_link(symbol: str, day: str = "", mode: str = "live", tf: str = "") -> dict[str, str]:
    url = _dash_url(symbol, day, mode, tf)
    return {"dashboard_url": url} if url else {}


def _dash_day(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return _today()
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"ngay phai dang YYYY-MM-DD, nhan duoc {text!r}") from None


def _last_closed(symbol: str, timeframe: str) -> dict[str, Any] | None:
    closed, _ = _split_closed(KLINE_STORE.get_klines(symbol, timeframe, limit=3), timeframe)
    if not closed:
        return None
    bar = closed[-1]
    return {"t": int(bar["open_time"]), "o": bar["open"], "h": bar["high"], "l": bar["low"],
            "c": bar["close"], "v": bar["volume"]}


def _last_price(symbol: str) -> float | None:
    """Gia dong moi nhat, cung nguon nen da dong watcher dang xet."""
    bars = [(bar["t"] + _tf_ms(tf), float(bar["c"])) for tf in ("5m", "15m", "1h")
            if (bar := _last_closed(symbol, tf))]
    return max(bars)[1] if bars else None


def _dash_event(event: dict[str, Any]) -> dict[str, Any]:
    """Khong tra details va noi dung loi gui tin: co the chua vi the, webhook hoac token."""
    return {"id": event.get("id"), "event": event["event"], "from_status": event.get("from_status"),
            "to_status": event.get("to_status"), "price": event.get("price"),
            "t": event.get("candle_open_time"), "at": int(event["at"]), "code": event.get("code"),
            "notify": "error" if event.get("notify_error") else "sent" if event.get("notified") else None,
            "repeat": bool((event.get("details") or {}).get("repeat"))}


def _dash_setup(setup: dict[str, Any], last: dict[str, Any] | None = None) -> dict[str, Any]:
    out = _setup_view(setup)
    verdict = check_trade(side=setup["direction"], entry=setup["entry"], stop=setup["stop"],
                          target=setup["target"], margin_usd=0.0, symbol=setup["symbol"])
    out.update(rr=verdict["rr"], created_ms=int(setup["created_at"]), expires_ms=int(setup["expires_at"]),
               last_event=_dash_event(last) if last else None)
    return out


def _plan_state(symbol: str, day: str) -> dict[str, Any]:
    """Ke hoach dang hieu luc cua mot ngay: cung mot dang cho chart va cho snapshot."""
    rows = SETUPS.list(symbol=symbol, bias_date=day)
    last = SETUPS.last_events([r["id"] for r in rows])
    bias = _orb_bias(symbol, day)
    tr = PLANS.tr_at(symbol, _day_window_ms(day)[1])
    if tr is None:
        # Setup luu truoc khi co save_scenarios van khai TR ngay trong setup
        declared = next((r for r in rows if r.get("tr_high") and r.get("tr_low")), None)
        if declared:
            # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR: cung dang day du nhu TR khoa
            tr = tr_logic.decorate_tr({
                "timeframe": "15m", "tr_high": declared["tr_high"], "tr_low": declared["tr_low"],
                "since": declared.get("tr_since"), "events": [], "redraw_rule": None, "source": "setup"})
    return {"bias": {k: v for k, v in bias.items() if k != "history"} if bias else None,
            "setups": [_dash_setup(r, last.get(r["id"])) for r in rows],
            "scenarios": [s for s in PLANS.scenarios(symbol, day) if s["status"] != "cancelled"],
            "tr": tr}


def _plan_snapshot(symbol: str, day: str, trigger: str) -> int | None:
    """Snapshot la phan phu: loi thi ghi log, khong duoc lam hong viec luu chinh da xong."""
    try:
        payload = {**_plan_state(symbol, day),
                   "last_closed_bar": {tf: _last_closed(symbol, tf) for tf in DASH_TFS}}
        return PLANS.add_snapshot(symbol, day, trigger, payload)
    except Exception as exc:
        print(f"BAMCP snapshot {symbol} {day} loi: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def _chart_revision(value: Any) -> int | str | None:
    if value in (None, "", 0, "0"):
        return None
    if str(value).strip().lower() == "origin":
        return "origin"
    try:
        revision = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"revision phai la so hoac 'origin', nhan duoc {value!r}") from None
    if revision < 1:
        raise ValueError("revision bat dau tu 1")
    return revision


def _forming_bar(symbol: str, tf: str, now: int) -> dict[str, Any] | None:
    """Kho SQLite khong luu nen chua dong: ghep nen dang chay tu cac nen M5 da dong."""
    span, five = _tf_ms(tf), _tf_ms("5m")
    if tf not in DASH_TFS or span <= five:
        return None
    closed, _ = _split_closed(KLINE_STORE.get_klines(symbol, "5m", limit=span // five), "5m")
    return dashboard_logic.forming_from_lower(closed, now // span * span)


def _chart_htf(symbol: str, price: float | None) -> list[dict[str, Any]]:
    swings: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for tf in ("1w", "1d"):
        try:
            swings[tf] = _build_context(symbol, tf)
        except (FileNotFoundError, ValueError):
            continue
    return dashboard_logic.htf_levels(swings, price)


def _chart_markers(setups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = SETUPS.events_of([s["id"] for s in setups])
    out = []
    for setup in setups:
        for event in events.get(setup["id"]) or []:
            if event["event"] in dashboard_logic.MARKER_EVENTS and not (event.get("details") or {}).get("repeat"):
                # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR: kind tach marker setup / Wyckoff
                out.append({"kind": "setup", "t": int(event.get("candle_open_time") or event["at"]),
                            "event": event["event"], "code": setup["code"], "direction": setup["direction"],
                            "price": event.get("price")})
    return sorted(out, key=lambda m: m["t"])


def _chart_build(symbol: str, tf: str, bars: int, day: str, include_forming: bool,
                 revision: int | str | None) -> dict[str, Any]:
    cfg = _dash_cfg()
    now = int(time.time() * 1000)
    start_ms, end_ms = _day_window_ms(day)
    past = day < _today()
    # Ngay da qua: nen toi het ngay bias + 24h de thay dien bien that
    until = end_ms + 86_400_000 if past else None
    period = dashboard_logic.VOL_PERIOD
    closed, forming = _split_closed(
        KLINE_STORE.get_klines(symbol, tf, limit=bars + period + 2, end_time=until), tf)
    if include_forming and forming is None and not past:
        forming = _forming_bar(symbol, tf, now)
    mult = float(cfg["vol_spike_mult"])
    window = closed[-bars:]
    averages, spikes = dashboard_logic.volume_stats(closed, bars, mult, period=period)
    candles = [[int(b["open_time"]), b["open"], b["high"], b["low"], b["close"], b["volume"], True]
               for b in window]
    if include_forming and forming is not None:
        candles.append([int(forming["open_time"]), forming["open"], forming["high"], forming["low"],
                        forming["close"], forming["volume"], False])
        tail = closed[-period:]
        averages.append(round(sum(float(b["volume"]) for b in tail) / period, 4)
                        if len(tail) == period else None)

    source, used = "live", None
    plan = None
    if revision == "origin":
        plan = dashboard_logic.origin_plan(PLANS.snapshots(symbol, day, payload=True))
        if plan is not None:
            source, used = "origin", plan["revisions_used"]
    elif revision is not None:
        plan = PLANS.snapshot(symbol, day, int(revision))["payload"]
        source = "revision"
    if plan is None:
        plan = _plan_state(symbol, day)
        plan["scenarios"] = [s for s in plan["scenarios"] if s["status"] == "active"]
    setups = plan.get("setups") or []
    bias = plan.get("bias")
    last_m5 = _last_closed(symbol, "5m")
    price = float(last_m5["c"]) if last_m5 else (float(window[-1]["close"]) if window else None)
    # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
    # Snapshot cu chi co events {type, t, price}: decorate_tr bu seq, side, nhan, trang thai.
    tr = tr_logic.decorate_tr(plan.get("tr"))
    markers = sorted(_chart_markers(setups) + tr_logic.markers(tr, [int(c[0]) for c in candles], _tf_ms(tf)),
                     key=lambda m: m["t"])
    # TM - #TR-BRANCH - Xac suat thay doi the nao trong ngay (lay theo TR dang ve, ke ca khi xem revision)
    prob_history = PLANS.prob_history(tr["id"], start_ms, end_ms) if tr and tr.get("id") else []
    return {
        "symbol": symbol, "timeframe": tf, "date": day, "time_zone": str(TZ),
        "day_start_ms": start_ms, "candle_columns": ["t", "o", "h", "l", "c", "v", "is_closed"],
        "candles": candles, "vol_avg": averages, "vol_spike": spikes, "vol_spike_mult": mult,
        "bias": {k: bias.get(k) for k in CHART_BIAS_KEYS} if bias else None,
        "setups": setups, "scenarios": plan.get("scenarios") or [], "tr": tr, "prob_history": prob_history,
        "htf_levels": _chart_htf(symbol, price), "markers": markers, "last_m5": last_m5,
        "plan": {"source": source, "revision": revision if source == "revision" else None,
                 "revisions_used": used, "revisions": PLANS.snapshots(symbol, day)},
        **_dash_link(symbol, day, "review" if revision is not None else "live", tf),
    }


def _chart_core(symbol: str, tf: str, bars: int, day: str, include_forming: bool,
                revision: int | str | None) -> dict[str, Any]:
    """Cache toi khi co nen moi dong hoac ke hoach / trang thai setup doi."""
    key = (symbol, tf, bars, day, include_forming, revision)
    cfg = _dash_cfg()
    stamp = (KLINE_STORE.kind, KLINE_STORE.get_latest_open_time(symbol, tf), KLINE_STORE.written_at(symbol, tf),
             KLINE_STORE.get_latest_open_time(symbol, "5m"), int(time.time() * 1000) // (_tf_ms(tf) or 1),
             SETUPS.last_event_id(), PLANS.version(), _today(), cfg["vol_spike_mult"], cfg["enabled"],
             cfg["base_url"], cfg["default_tf"])
    hit = _DASH_CHART_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        _DASH_CHART_CACHE.move_to_end(key)
        return hit[1]
    data = _chart_build(symbol, tf, bars, day, include_forming, revision)
    _DASH_CHART_CACHE[key] = (stamp, data)
    while len(_DASH_CHART_CACHE) > DASH_CHART_CACHE_SIZE:
        _DASH_CHART_CACHE.popitem(last=False)
    return data


def _chart_freshness(core: dict[str, Any], now: int) -> dict[str, Any]:
    """Tinh moi lan goi, khong cache: nguon nen ngung chay thi van phai bao duoc."""
    closed = [c for c in core["candles"] if c[6]]
    span = _tf_ms(core["timeframe"])
    last_end = closed[-1][0] + span if closed else None
    m5 = core.get("last_m5")
    m5_end = m5["t"] + _tf_ms("5m") if m5 else None
    live = core["date"] >= _today()
    stale = live and any(end is not None and now - end > 2 * width
                         for end, width in ((last_end, span), (m5_end, _tf_ms("5m"))))
    return {"last_closed_bar_age_minutes": round((now - last_end) / 60000, 1) if last_end else None,
            "expected_bar_minutes": round(span / 60000) if span else None,
            "m5_age_minutes": round((now - m5_end) / 60000, 1) if m5_end else None,
            "stale": bool(stale)}


async def _dash_exchange(key: tuple, loader) -> Any:
    """Polling khong duoc dung rate limit cua san: moi khoa chi goi that mot lan moi 30 giay."""
    now = time.monotonic()
    hit = _DASH_EXCHANGE_CACHE.get(key)
    if hit is not None and now - hit[0] < DASH_EXCHANGE_TTL:
        return hit[1]
    try:
        value = await loader()
    except Exception as exc:
        print(f"BAMCP dashboard san {key[0]} loi: {type(exc).__name__}", file=sys.stderr)
        value = None
    if len(_DASH_EXCHANGE_CACHE) > 128:
        _DASH_EXCHANGE_CACHE.clear()
    _DASH_EXCHANGE_CACHE[key] = (now, value)
    return value


async def _dash_positions(symbol: str) -> dict[str, Any]:
    """Chi doc. Loi san khong tra nguyen van cho client: chuoi loi co the chua URL da ky."""
    if not _account_enabled():
        return {"enabled": False, "positions": [], "unrealized_pnl": None}
    data = await _dash_exchange(("positions", symbol), lambda: _open_positions([symbol]))
    if data is None or data.get("errors"):
        return {"enabled": True, "positions": [], "unrealized_pnl": None, "error": "khong doc duoc vi the tu san"}
    rows = [{k: row.get(k) for k in POSITION_KEYS} for row in data["positions"]]
    total = round(sum(float(r.get("unrealized_pnl") or 0) for r in rows), 4)
    if _dash_cfg()["hide_pnl"]:
        for row in rows:
            row.pop("unrealized_pnl", None)
        return {"enabled": True, "positions": rows, "unrealized_pnl": None, "pnl_hidden": True}
    return {"enabled": True, "positions": rows, "unrealized_pnl": total}


async def _chart_data(symbol: str, timeframe: str, bars: Any, day: Any, include_forming: bool,
                      revision: Any, *, positions: bool = True) -> dict[str, Any]:
    tf = _validate_timeframe(str(timeframe or "1h"))
    try:
        count = int(bars)
    except (TypeError, ValueError):
        raise ValueError(f"bars phai la so, nhan duoc {bars!r}") from None
    count = max(1, min(count, dashboard_logic.MAX_BARS))
    core = _chart_core(symbol, tf, count, _dash_day(day), bool(include_forming), _chart_revision(revision))
    out = {**core, "freshness": _chart_freshness(core, int(time.time() * 1000))}
    if positions:
        out["positions"] = await _dash_positions(symbol)
        # TM - #OFF-PLAN - Marker cam diem vao lenh ngoai ke hoach. Chi chu tai khoan (lo vi the), khong
        # vao cache chung cua chart. Khong sua list cua core: core dang nam trong cache.
        extra = _op_markers(symbol, core["date"])
        if extra:
            out["markers"] = sorted(core["markers"] + extra, key=lambda m: m["t"])
    return out


@_tool_with_reasons()
async def get_chart_data(symbol: str = "", timeframe: str = "1h", bars: int = 96, date: str = "",
                         include_forming: bool = False, revision: int | str | None = None) -> dict[str, Any]:
    """Mot lan goi du du lieu de ve bieu do: nen + bias + setup + kich ban + TR khoa + muc HTF + vi the.

    Dung cai nay khi can ve bieu do, thay vi ghep nhieu tool. Cung nguon voi trang dashboard.
    bars: toi da 500. date: ngay bias (bo trong = hom nay); ngay da qua thi nen toi het ngay do + 24h.
    candles: [t, o, h, l, c, v, is_closed], t la epoch ms gio mo nen. include_forming=true them nen
      dang chay is_closed=false: chi de biet gia, khong doc VSA tu no.
    vol_avg: SMA20 volume cua 20 nen TRUOC moi nen, cung thu tu voi candles. vol_spike: t cua nen da
      dong co vol >= dashboard.vol_spike_mult x vol_avg.
    revision: so revision thi bias/setup/kich ban/TR lay tu snapshot do (nen van la that); 'origin' =
      ke hoach goc, moi phan lay ban dau tien co noi dung. Bo trong = ke hoach dang hieu luc.
    plan.revisions liet ke cac snapshot cua ngay. dashboard_url tro dung symbol/date/tf.
    TM - #WYCKOFF-TR: tr co schematic, status, current_phase, levels {creek, ice, ...}, phases
      [{phase, start_t, end_t}] va events [{id, seq, ref "TEST#2", label, type, t, tf, price, side,
      vol_ratio, spread_ratio, close_pct, status, confirmed_by, note, phase}]. TR cu (superseded) chi
      ra khi date la ngay TR do con hieu luc.
    markers: kind "setup" = event watcher {t, event, code}; kind "wyckoff" = moi event cua TR
      {t_bar, t, price, side, label, status, event_id}; t_bar la gio mo cua nen thuoc timeframe dang
      xem chua t cua event (vd event M15 22:00 -> nen H1 22:00, nen H4 19:00).
    TM - #TR-BRANCH: tr.branch (accumulation | distribution | null) + tr.branch_by {by: event|schematic,
      ref, phase, ...}; tr.prob_accumulation, tr.prob_distribution, tr.prob_evidence. prob_history: cac lan
      xac suat doi trong ngay {at, prob_accumulation, prob_distribution, plan_revision}. Setup co label,
      branch; PRUNED la setup bi loai vi nguoc nhanh TR (dashboard an mac dinh).
    """
    return await _chart_data(_resolve_symbol(symbol), timeframe, bars, date, include_forming, revision)


def _scenario_anchor(symbol: str, tf: str) -> tuple[int, int]:
    span = _tf_ms(tf)
    if not span:
        raise ValueError(f"offset co tf khong hop le: {tf!r}")
    bar = _last_closed(symbol, tf)
    return (bar["t"] if bar else int(time.time() * 1000) // span * span - span), span


# ---------------------------------------------------------------- TR khoa: pha va su kien Wyckoff
# TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
# TR la noi duy nhat luu cau truc Wyckoff. Claude gan nhan; server chi kiem tra va tinh so VSA.

_TR_LOCK = threading.Lock()


def _tr_bars(symbol: str, tf: str, t: int) -> list[dict[str, Any]]:
    """Nen DA DONG co open_time <= t, kem 20 nen truoc de tinh so VSA."""
    rows = KLINE_STORE.get_klines(symbol, tf, limit=tr_logic.VOL_PERIOD + 1, end_time=int(t))
    return _split_closed(rows, tf)[0]


def _tr_ctx(symbol: str, created_by: str = "claude") -> tr_logic.Ctx:
    return tr_logic.Ctx(bars_at=lambda tf, t: _tr_bars(symbol, tf, t), parse_time=_parse_moment,
                        timeframes=list(SUPPORTED_TIMEFRAMES), span_ms=_tf_ms,
                        stamp=lambda ms: _stamp(int(ms))[:16], now_ms=int(time.time() * 1000),
                        created_by=created_by,
                        # TM - #TR-BRANCH - Thang diem xac suat dang ap dung
                        prob_weights=_dash_cfg()["prob_weights"])


def _migrate_tr_events() -> int:
    """Chuyen event json cu sang tr_events (status tentative, created_by claude), tinh lai seq, side
    va so VSA tu nen. Chay luc khoi dong va truoc moi lan ghi TR; TR da chuyen thi bo qua."""
    moved = 0
    for tr in PLANS.legacy_trs():
        span = _tf_ms(tr["timeframe"])
        rows = []
        for raw in tr["events"]:
            try:
                kind, t, price = tr_logic.event_type(raw.get("type")), int(raw["t"]), float(raw["price"])
            except (KeyError, TypeError, ValueError):
                print(f"BAMCP: bo qua event TR {tr['id']} khong doc duoc: {raw!r}", file=sys.stderr)
                continue
            bars = _tr_bars(tr["symbol"], tr["timeframe"], t)
            # Event cu chua duoc kiem tra gio mo nen: lay nen chua moc t
            inside = bool(bars) and int(bars[-1]["open_time"]) <= t < int(bars[-1]["open_time"]) + span
            numbers = tr_logic.vsa(bars) if inside else {"vol_ratio": None, "spread_ratio": None,
                                                         "close_pct": None}
            rows.append({"id": str(uuid.uuid4()), "type": kind, "t": t, "tf": tr["timeframe"], "price": price,
                         "side": tr_logic.side_of(kind), **numbers, "status": "tentative", "confirmed_by": None,
                         "note": None, "created_by": "claude", "created_at": int(tr["created_at"]),
                         "updated_at": int(tr["created_at"])})
        moved += PLANS.migrate_tr_events(tr["id"], rows)
    return moved


def _tr_target(symbol: str, tr_id: str) -> dict[str, Any]:
    if str(tr_id or "").strip():
        tr = PLANS.tr(tr_id)
        if tr["symbol"] != symbol:
            raise ValueError(f"TR {tr['id']} la cua {tr['symbol']}, khong phai {symbol}")
        return tr
    tr = PLANS.active_tr(symbol)
    if tr is None:
        raise ValueError(f"{symbol} chua co TR khoa - tao TR qua save_scenarios(tr={{...}}) truoc")
    return tr


def _tr_report(plan: dict[str, Any], tr: dict[str, Any]) -> dict[str, Any]:
    """Event moi / doi trong lan goi nay, de bao cao ghi ro (theo nhan sau khi danh so lai)."""
    by_id = {e["id"]: e for e in tr["events"]}
    added = [{k: by_id[e["id"]].get(k) for k in ("id", "ref", "label", "type", "t", "price", "status",
                                                 "vol_ratio", "spread_ratio", "close_pct")}
             for e in plan["insert"] if e["id"] in by_id]
    changed = [{"id": u["id"], "ref": by_id[u["id"]]["ref"], "label": by_id[u["id"]]["label"],
                **{k: v for k, v in u.items() if k != "id"}} for u in plan["update"] if u["id"] in by_id]
    return {"added": added, "updated": changed,
            "fields": sorted(plan["fields"]), "phases_changed": plan["phases"] is not None}


# ---------------------------------------------------------------- nhanh kich ban TR
# TM - #TR-BRANCH - Nhanh kich ban TR: loai setup nguoc nhanh khi TR xac dinh nhanh

BRANCH_EVENT_TYPES = (*tr_logic.BRANCH_C, *tr_logic.BRANCH_D)
PROB_NOTE = "diem bang chung, chua hieu chinh thong ke"


def _setup_brief(setup: dict[str, Any]) -> dict[str, Any]:
    return {"code": setup["code"], "label": setup.get("label") or entry_logic.setup_label(setup),
            "direction": setup["direction"], "status": setup["status"], "bias_date": setup["bias_date"]}


def _tr_brief(tr: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "symbol", "timeframe", "schematic", "status", "current_phase", "branch", "branch_by",
            "prob_accumulation", "prob_distribution")
    return {k: tr.get(k) for k in keys}


def _tr_briefs(tr_ids: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for tid in dict.fromkeys(str(i) for i in tr_ids if i):
        try:
            out[tid] = _tr_brief(PLANS.tr(tid))
        except ValueError:
            continue
    return out


def _filled_open(setups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Setup FILLED ma review chua ghi lenh da dong (tp/sl/be/thoat tay): coi nhu con vi the."""
    filled = [s for s in setups if s["status"] == "FILLED"]
    reviews = PLANS.reviews([s["id"] for s in filled])
    closed = dashboard_logic.CLOSED_OUTCOMES
    return [s for s in filled if (reviews.get(s["id"]) or {}).get("outcome") not in closed
            and (reviews.get(s["id"]) or {}).get("auto_outcome") not in closed]


def _tr_branch_apply(symbol: str, before: dict[str, Any] | None, tr: dict[str, Any],
                     plan: dict[str, Any]) -> dict[str, Any]:
    """Goi ngay sau moi lan ghi TR (update_tr, save_scenarios). Nhanh doi sang tang/giam thi moi setup
    CHUA KET THUC cua TR nguoc nhanh (ke ca branch=range) sang PRUNED; setup da FILLED giu nguyen, chi
    canh bao. Mot tin tong hop cho ca lan loai. Event pha C/D vua chuyen failed thi khong tu khoi phuc
    setup da loai, chi tra warning."""
    old, new, info = (before or {}).get("branch"), tr.get("branch"), tr.get("branch_by")
    out: dict[str, Any] = {"branch": new, "branch_by": info, "changed": old != new,
                           "reason": tr_logic.branch_reason(info) if info else None,
                           "pruned": [], "conflicts": [], "warnings": [], "event_id": None, "days": set()}
    if not tr.get("id"):
        return out
    rows = SETUPS.by_tr(tr["id"])
    if new and new != old:
        counter = tr_logic.COUNTER_DIRECTION[new]
        details = {"tr_id": tr["id"], "branch": new, "ref": info.get("ref") or f"schematic {info.get('schematic')}",
                   "phase": info.get("phase"), "by": info.get("by")}
        for setup in rows:
            if setup["direction"] != counter or setup["status"] in entry_logic.TERMINAL:
                continue
            if SETUPS.prune(setup, out["reason"], details) is not None:
                out["pruned"].append(_setup_brief(setup))
                out["days"].add(setup["bias_date"])
        for setup in _filled_open([s for s in rows if s["direction"] == counter]):
            SETUPS.note_event(setup, "BRANCH_CONFLICT", {**details, "reason": out["reason"]})
            out["conflicts"].append(_setup_brief(setup))
            out["days"].add(setup["bias_date"])
        if out["conflicts"]:
            out["warnings"].append(f"vi the nguoc nhanh TR: {len(out['conflicts'])} setup FILLED "
                                   f"({', '.join(s['code'] for s in out['conflicts'])}) giu nguyen trang thai - "
                                   "can nhac dong lenh")
        if out["pruned"] or out["conflicts"]:
            out["event_id"] = SETUPS.system_event("TR_BRANCH", {
                "symbol": symbol, "tr_id": tr["id"], "branch": new, "reason": out["reason"],
                "pruned": out["pruned"], "conflicts": out["conflicts"],
                "link": _dash_url(symbol, _today(), tf=tr.get("timeframe") or "")})
    by_id = {e["id"]: e for e in tr.get("events") or []}
    failed = [by_id[u["id"]]["ref"] for u in plan.get("update") or []
              if u.get("status") == "failed" and by_id.get(u["id"], {}).get("type") in BRANCH_EVENT_TYPES]
    if failed:
        pruned = [s for s in SETUPS.by_tr(tr["id"]) if s["status"] == "PRUNED"]
        state = (f"TR van o nhanh {tr_logic.BRANCH_WORDS[new][0]} theo {tr_logic.branch_source(info)}" if new
                 else "TR ve trung tinh (chua xac dinh nhanh)")
        text = f"{', '.join(failed)} chuyen failed - {state}"
        if pruned:
            text += (f"; {len(pruned)} setup da PRUNED KHONG tu khoi phuc ({', '.join(s['code'] for s in pruned)})"
                     " - can thi luu lai qua save_setups")
        out["warnings"].append(text)
    return out


def _branch_idle(tr: dict[str, Any]) -> dict[str, Any]:
    """Cung dang ket qua _tr_branch_apply cho lan goi khong ghi gi."""
    info = tr.get("branch_by")
    return {"branch": tr.get("branch"), "branch_by": info, "changed": False,
            "reason": tr_logic.branch_reason(info) if info else None,
            "pruned": [], "conflicts": [], "warnings": [], "event_id": None}


def _branch_view(result: dict[str, Any]) -> dict[str, Any]:
    return {k: (sorted(v) if isinstance(v, set) else v) for k, v in result.items() if k != "days"}


def _prob_view(tr: dict[str, Any]) -> dict[str, Any]:
    evidence = tr.get("prob_evidence") or []
    return {"accumulation": tr.get("prob_accumulation"), "distribution": tr.get("prob_distribution"),
            "evidence": evidence, "weight_acc": sum(e["weight"] for e in evidence if e["side"] == "acc"),
            "weight_dist": sum(e["weight"] for e in evidence if e["side"] == "dist"), "note": PROB_NOTE}


def _tr_written(symbol: str, day: str, trigger: str, tr: dict[str, Any], plan: dict[str, Any] | None,
                branch: dict[str, Any] | None) -> int | None:
    """Sau khi ghi TR (ngoai khoa): snapshot ke hoach cua ngay va cac ngay co setup vua bi loai, gan
    revision vao lich su xac suat, roi gui tin tong hop."""
    revision = _plan_snapshot(symbol, day, trigger)
    for other in sorted((branch or {}).get("days") or set()):
        if other != day:
            _plan_snapshot(symbol, other, trigger)
    PLANS.set_prob_revision((plan or {}).get("prob_history_id"), revision, day)
    if branch and branch.get("event_id"):
        WATCHER.notify_threadsafe(branch["event_id"])
    return revision


def _save_tr(symbol: str, raw: dict[str, Any], old_status: str
             ) -> tuple[dict[str, Any], bool, list[str], dict[str, Any] | None, dict[str, Any] | None]:
    """tr cua save_scenarios. Cung neo (tr_high, tr_low, since) -> gop vao TR dang hieu luc nhu
    update_tr, gui lai event da co khong nhan doi. Neo khac -> TR moi, TR cu superseded + old_status.
    TM - #TR-BRANCH: tra them plan va ket qua xet nhanh (setup nguoc nhanh da loai)."""
    clean = tr_logic.validate_tr_input(raw, parse_time=_parse_moment, timeframes=list(SUPPORTED_TIMEFRAMES))
    if old_status not in tr_logic.OLD_TR_STATUSES:
        raise ValueError(f"old_tr_status phai la mot trong {list(tr_logic.OLD_TR_STATUSES)}")
    _migrate_tr_events()
    with _TR_LOCK:
        current = PLANS.active_tr(symbol)
        ctx = _tr_ctx(symbol)
        if current is not None and tr_logic.same_anchors(current, clean):
            adds, updates = tr_logic.split_merge(current, clean["events"], _parse_moment)
            plan = tr_logic.plan_changes(current, ctx, fields=clean["fields"], add_events=adds,
                                         update_events=updates, phases=clean["phases"])
            warnings = list(plan["warnings"])
            if clean["timeframe"] != current["timeframe"]:
                warnings.append(f"timeframe {clean['timeframe']} khac TR dang hieu luc - giu "
                                f"{current['timeframe']} (cung neo nen van la TR cu)")
            if not plan["changed"]:
                return current, False, warnings, None, None
            tr = PLANS.apply_tr(current["id"], plan)
            branch = _tr_branch_apply(symbol, current, tr, plan)
            return tr, True, warnings + branch["warnings"], plan, branch
        plan = tr_logic.plan_changes(tr_logic.virtual_tr(clean), ctx, fields=clean["fields"],
                                     add_events=clean["events"], phases=clean["phases"])
        created = PLANS.create_tr(symbol, clean, plan, current["id"] if current else None, old_status)
        branch = _tr_branch_apply(symbol, None, created, plan)
        return created, True, list(plan["warnings"]) + branch["warnings"], plan, branch


def _update_tr(symbol: str, tr_id: str, fields: dict[str, Any], phases: Any, add_events: Any,
               update_events: Any, anchors: dict[str, Any], created_by: str = "claude") -> dict[str, Any]:
    for name, value in (("phases", phases), ("add_events", add_events), ("update_events", update_events)):
        if value is not None and not isinstance(value, list):
            raise ValueError(f"{name} phai la list")
    _migrate_tr_events()
    with _TR_LOCK:
        tr = _tr_target(symbol, tr_id)
        moved = []
        for name, source in (("tr_high", "tr_high"), ("tr_low", "tr_low")):
            value = anchors.get(name)
            if value is not None and not math.isclose(float(value), float(tr[source]), rel_tol=1e-9):
                moved.append(name)
        if anchors.get("since") not in (None, "") and _parse_moment(anchors["since"]) != int(tr["since"]):
            moved.append("since")
        if moved:
            raise ValueError(f"update_tr khong doi duoc {', '.join(moved)} - doi neo la ve lai TR, "
                             "goi save_scenarios(tr={...}) voi neo moi")
        plan = tr_logic.plan_changes(tr, _tr_ctx(symbol, created_by), fields=fields, add_events=add_events,
                                     update_events=update_events, phases=phases)
        # TM - #TR-BRANCH - Ghi xong thi xet nhanh ngay trong khoa, de hai lan ghi khong cung loai mot setup
        branch = None
        if plan["changed"]:
            before = tr
            tr = PLANS.apply_tr(tr["id"], plan)
            branch = _tr_branch_apply(symbol, before, tr, plan)
    day = _today()
    revision = _tr_written(symbol, day, "update_tr", tr, plan, branch) if plan["changed"] else None
    return {"symbol": symbol, "changed": plan["changed"], **_tr_report(plan, tr),
            "warnings": plan["warnings"] + (branch["warnings"] if branch else []), "tr": tr,
            # TM - #TR-BRANCH - Nhanh kich ban TR va xac suat tich luy / phan phoi
            "branch": _branch_view(branch or _branch_idle(tr)), "prob": _prob_view(tr),
            "plan_revision": revision, **_dash_link(symbol, day, tf=tr["timeframe"])}


@_tool_with_reasons()
def update_tr(symbol: str, tr_id: str = "", schematic: str = "", status: str = "",
              levels: dict[str, float | None] | None = None, note: str | None = None,
              phases: list[dict[str, Any]] | None = None, add_events: list[dict[str, Any]] | None = None,
              update_events: list[dict[str, Any]] | None = None, tr_high: float | None = None,
              tr_low: float | None = None, since: str | int | None = None,
              prob_evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Cap nhat cau truc Wyckoff cua TR khoa: so do, trang thai, muc, pha va su kien. Goi MOT lan
    voi moi thay doi, TRUOC save_bias (bias.phase an theo current_phase).

    tr_id: bo trong = TR dang hieu luc cua symbol. Chua co TR thi tao qua save_scenarios.
    schematic: accumulation | reaccumulation | distribution | redistribution | undetermined.
      Giu undetermined toi khi co bang chung (SPRING/SOS -> accumulation; UTAD/SOW -> distribution).
    status: hypothesis | confirmed | failed | completed. note: nhan dinh tong the ve TR.
    levels: {creek?, ice?, ar_level?, st_level?} gop vao muc dang co, null = xoa muc.
    add_events: [{type, t, price, tf?, status?, note?, confirmed_by?}]. t = gio mo cua nen DA DONG
      cua tf (mac dinh = khung TR), price nam trong [low, high] cua nen do. Server tu tinh side, seq,
      vol_ratio (vol / SMA20 20 nen truoc), spread_ratio, close_pct. Mac dinh status tentative.
      Tich luy: PS SC ST AR | ST_B mSOW UA | SPRING SHAKEOUT TEST | SOS JAC LPS BU.
      Phan phoi: PSY BC ST_D AR_D | UT mSOS | UTAD TEST_D | SOW LPSY. TR undetermined nhan moi type.
    update_events: [{id | ref: "TEST#2", status?, note?, confirmed_by?, price?}]. Gan sai thi
      status=failed kem note, khong xoa (delete_tr_event chi cho event tao nham trong 24h).
    phases: [{phase: A-E, start, end?, note?}] upsert theo pha. start/end nhan epoch ms,
      'YYYY-MM-DD HH:MM' gio VN, hoac {event: "<id | TYPE#n>"} de lay dung t cua event. Mo pha moi thi
      pha truoc tu dong tai start cua pha moi. Pha khong chong nhau, theo thu tu A -> E.
    tr_high, tr_low, since: KHONG doi duoc o day (truyen khac TR la bi tu choi) - doi neo = ve lai TR
      qua save_scenarios.
    Co loi nao la tu choi ca lan goi, liet ke het loi. Canh bao (khong tu choi): type nam ngoai pha
    thuong gap, price lech day/dinh nen > 0.1%, SOS/SOW confirmed ma chua co LPS/LPSY sau no.
    Tra ve TR day du, added/updated (nhan sau khi danh so), warnings, plan_revision, dashboard_url.
    TM - #TR-BRANCH - Nhanh kich ban:
      TR trung tinh (branch null) toi khi co event pha C confirmed: SPRING/SHAKEOUT -> nhanh tang
      (accumulation), UTAD -> nhanh giam (distribution). Khong co pha C thi SOS/JAC hoac SOW confirmed.
      Doi schematic sang ho tich luy / phan phoi cung xac dinh nhanh (kem canh bao neu chua co event).
      Nhanh vua xac dinh hoac doi: moi setup CHUA KET THUC cua TR nguoc nhanh (ke ca branch=range) sang
      PRUNED, watcher ngung theo doi, gui MOT tin tong hop. Setup da FILLED giu nguyen, chi canh bao vi
      the nguoc nhanh. Event pha C/D sau do chuyen failed: KHONG tu khoi phuc, doc warnings va luu lai
      setup can thiet qua save_setups. Ket qua: branch {branch, branch_by, reason, pruned, conflicts}.
    prob_evidence: [{factor, side: acc|dist, weight?, note?}] thay TOAN BO danh sach (gui [] = xoa).
      factor theo thang diem (bo trong weight = lay trong so trang admin): htf_trend 2 (xu huong D/H4
      truoc TR), phase_b_lows 1, phase_b_highs 1 (cau truc day / dinh pha B), test_highs 1, test_lows 1
      (hanh vi test dinh / day), phase_cd_event 3 (SPRING/SOS -> acc, UTAD/SOW -> dist da confirmed).
      Server tinh prob_accumulation = Σacc / Σ x 100 (lam tron), prob_distribution = 100 - acc, luu lich
      su gan plan revision. Day la diem bang chung, chua hieu chinh thong ke - noi ro khi bao cao.
    """
    fields: dict[str, Any] = {}
    if str(schematic or "").strip():
        fields["schematic"] = schematic
    if str(status or "").strip():
        fields["status"] = status
    if levels is not None:
        fields["levels"] = levels
    if note is not None:
        fields["note"] = note
    # TM - #TR-BRANCH - Diem bang chung xac suat
    if prob_evidence is not None:
        fields["prob_evidence"] = prob_evidence
    return _update_tr(_resolve_symbol(symbol), tr_id, fields, phases, add_events, update_events,
                      {"tr_high": tr_high, "tr_low": tr_low, "since": since})


def _delete_tr_event(symbol: str, event_id: str, reason: str) -> dict[str, Any]:
    if not str(reason or "").strip():
        raise ValueError("reason la bat buoc")
    _migrate_tr_events()
    with _TR_LOCK:
        found = PLANS.find_tr_event(symbol, str(event_id or "").strip())
        tr = PLANS.tr(found[1]) if found else _tr_target(symbol, "")
        event = tr_logic.resolve_ref(event_id, tr["events"])
        now = int(time.time() * 1000)
        if not tr_logic.deletable(event, now):
            raise ValueError(f"{event['label']} tao luc {_stamp(int(event.get('created_at') or 0))[:16]}, qua 24h "
                             f"khong xoa duoc - dung update_tr(update_events=[{{ref: '{event['ref']}', "
                             "status: 'failed', note: '<ly do>'}])")
        unlinked = PLANS.delete_tr_event(tr["id"], event, reason.strip())
        tr = PLANS.tr(tr["id"])
    day = _today()
    labels = {e["id"]: e["label"] for e in tr["events"]}
    return {"symbol": symbol, "deleted": {k: event.get(k) for k in ("id", "ref", "label", "type", "t", "price")},
            "confirmed_by_cleared": [labels.get(i, i) for i in unlinked], "tr": tr,
            "plan_revision": _plan_snapshot(symbol, day, "delete_tr_event"),
            **_dash_link(symbol, day, tf=tr["timeframe"])}


@_tool_with_reasons()
def delete_tr_event(symbol: str, event_id: str, reason: str) -> dict[str, Any]:
    """Xoa han mot event TR tao NHAM (sai cap, sai nen...). Chi xoa duoc event tao trong 24h; sau
    24h, hoac event gan sai nhan, dung update_tr voi status=failed kem note.

    event_id: id hoac ref "TYPE#n" (ref tinh tren TR dang hieu luc). reason bat buoc, duoc luu lai.
    Event nao dang confirmed_by toi event bi xoa thi bo lien ket do.
    """
    return _delete_tr_event(_resolve_symbol(symbol), event_id, reason)


def _tr_changes(symbol: str, day: str) -> dict[str, Any]:
    """Event them / doi trang thai giua revision 1 va revision cuoi cua ngay (review)."""
    snaps = PLANS.snapshots(symbol, day, payload=True)
    first, last = (snaps[0], snaps[-1]) if snaps else ({}, {})
    diff = tr_logic.tr_diff((first.get("payload") or {}).get("tr"), (last.get("payload") or {}).get("tr"))
    return {"symbol": symbol, "date": day, "from_revision": first.get("revision"),
            "to_revision": last.get("revision"), **diff}


def _save_scenarios(symbol: str, scenarios: Any, replace: bool, day: str,
                    tr: dict[str, Any] | None, old_tr_status: str = "completed") -> dict[str, Any]:
    if not isinstance(scenarios, list):
        raise ValueError("scenarios phai la list")
    # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR: TR loi thi tu choi truoc khi luu gi
    # TM - #TR-BRANCH - kem plan (lich su xac suat) va ket qua xet nhanh
    tr_out, tr_changed, tr_warnings, tr_plan, tr_branch = (
        _save_tr(symbol, tr, str(old_tr_status or "completed").strip().lower()) if tr
        else (None, False, [], None, None))
    known = {s["code"] for s in SETUPS.list(symbol=symbol)}
    fresh, updates, rejected = [], [], []
    for index, raw in enumerate(scenarios):
        try:
            if isinstance(raw, dict) and str(raw.get("code") or "").strip():
                status = str(raw.get("status") or "").strip().lower()
                if status not in dashboard_logic.SCENARIO_STATUSES:
                    raise ValueError(f"status phai la mot trong {list(dashboard_logic.SCENARIO_STATUSES)}")
                row = PLANS.scenario(str(raw["code"]))
                if row["symbol"] != symbol:
                    raise ValueError(f"kich ban {row['code']} la cua {row['symbol']}, khong phai {symbol}")
                updates.append((row, status))
            else:
                fresh.append((index, dashboard_logic.validate_scenario(
                    raw, parse_time=_parse_moment, anchor=lambda tf: _scenario_anchor(symbol, tf),
                    setup_exists=known.__contains__)))
        except ValueError as exc:
            rejected.append({"index": index, "error": str(exc)})
    cancelled = PLANS.cancel_active(symbol, day) if replace and (fresh or not scenarios) else []
    last_close = _last_price(symbol)
    saved, warnings = [], []
    for index, clean in fresh:
        row = PLANS.insert_scenario(symbol, day, clean)
        saved.append({"index": index, "code": row["code"], "kind": row["kind"]})
        warning = dashboard_logic.start_warning(row["code"], clean["points"], last_close)
        if warning:
            warnings.append(warning)
    updated = [{"code": row["code"], "status": PLANS.set_scenario_status(row["id"], status)["status"]}
               for row, status in updates]
    # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
    warnings.extend(f"tr: {w}" for w in tr_warnings)
    changed = bool(saved or updated or cancelled or tr_changed)
    # TM - #TR-BRANCH - snapshot ca ngay co setup vua bi loai, gan revision cho lich su xac suat, gui tin
    revision = (_tr_written(symbol, day, "save_scenarios", tr_out, tr_plan, tr_branch) if tr_changed
                else _plan_snapshot(symbol, day, "save_scenarios") if changed else None)
    return {"symbol": symbol, "bias_date": day, "codes": [s["code"] for s in saved], "saved": saved,
            "updated": updated, "cancelled": cancelled, "rejected": rejected, "warnings": warnings,
            "tr": tr_out, "plan_revision": revision,
            **({"branch": _branch_view(tr_branch or _branch_idle(tr_out)), "prob": _prob_view(tr_out)}
               if tr_out else {}),
            **_dash_link(symbol, day)}


@_tool_with_reasons()
def save_scenarios(symbol: str, scenarios: list[dict[str, Any]], replace: bool = True, bias_date: str = "",
                   tr: dict[str, Any] | None = None, old_tr_status: str = "completed") -> dict[str, Any]:
    """Luu kich ban gia va TR khoa cua mot ngay. Goi ngay sau save_setups.

    scenarios: moi kich ban {kind: main|alt, label, setup_codes: [...], points: [...], condition}.
      points >= 2 diem {t, price, note?} theo thoi gian tang dan. t la epoch ms, 'YYYY-MM-DD HH:MM'
      gio VN, hoac offset {bars, tf} tinh tu nen da dong cuoi luc luu, vd {"bars": 4, "tf": "1h"}.
      setup_codes phai la setup co that cua cap nay. Diem dau cach gia dong cuoi > 2% chi canh bao.
      Doi trang thai kich ban da co: {code, status: active|played_out|failed|cancelled}.
    replace=true: kich ban active cua ngay chuyen sang cancelled truoc khi luu.
    bias_date: bo trong = hom nay.
    tr: {timeframe, tr_high, tr_low, since, redraw_rule, schematic?, status?, levels?, note?, phases?,
      events: [{type, t, price, tf?, status?, note?}]}. TM - #WYCKOFF-TR:
      - Cung neo tr_high, tr_low, since voi TR dang hieu luc -> gop vao TR do nhu update_tr, KHONG tao
        TR moi; event trung type + t la event da co (gui lai ca danh sach khong bi nhan doi).
      - Neo khac -> TR moi; TR cu superseded_by va status = old_tr_status (completed | failed).
      - Event moi kiem tra nhu add_events cua update_tr (nen da dong, price trong nen, khop schematic).
      Neo khong doi thi khong can truyen lai tr: them/sua event, pha qua update_tr.
      TM - #TR-BRANCH: tr nhan them prob_evidence nhu update_tr; ghi TR xong cung xet nhanh va loai setup
      nguoc nhanh nhu update_tr (ket qua o branch, prob).
    Tra ve codes, warnings, rejected, tr (day du), dashboard_url.
    """
    return _save_scenarios(_resolve_symbol(symbol), scenarios, bool(replace), _dash_day(bias_date), tr,
                           old_tr_status)


def _review_bars(symbol: str, window: tuple[int, int | None]) -> list[dict[str, Any]]:
    after, until = window
    five = _tf_ms("5m")
    end = until if until is not None else int(time.time() * 1000)
    limit = min(20_000, max(0, end - after) // five + 3)
    closed, _ = _split_closed(KLINE_STORE.get_klines(symbol, "5m", limit=limit, end_time=end), "5m")
    # Kho khong con nen tu luc trigger thi khong ket luan: thieu nen de ra ket qua sai
    if not closed or int(closed[0]["open_time"]) > after + five:
        return []
    return closed


def _review_sync(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Tinh auto_outcome cho setup da ket thuc, luu mot lan. FILLED chua cham TP/SL thi tinh lai sau."""
    reviews = PLANS.reviews([r["id"] for r in rows])
    pending = [r for r in rows if r["status"] in entry_logic.TERMINAL
               and (r["id"] not in reviews
                    or (reviews[r["id"]]["auto_outcome"] is None and r["status"] == "FILLED"))]
    if not pending:
        return reviews
    events = SETUPS.events_of([r["id"] for r in pending])
    for setup in pending:
        history = events[setup["id"]]
        window = dashboard_logic.trigger_window(setup, history)
        bars = _review_bars(setup["symbol"], window) if window else []
        if window and not bars:
            continue
        outcome = dashboard_logic.auto_outcome(setup, history, bars)
        if outcome is None and setup["status"] == "FILLED":
            continue
        reviews[setup["id"]] = PLANS.set_auto(setup["id"], outcome)
    return reviews


def _review_view(review: dict[str, Any] | None) -> dict[str, Any] | None:
    if not review:
        return None
    return {"outcome": review.get("outcome"), "auto_outcome": review.get("auto_outcome"),
            "r_multiple": review.get("r_multiple"), "exchange_fill_ids": review.get("exchange_fill_ids") or [],
            "note": review.get("note"), "reviewed_by": review.get("reviewed_by"),
            "reviewed_at": _stamp(review["reviewed_at"]) if review.get("reviewed_at") else None}


async def _dash_fills(symbol: str, day: str) -> dict[str, Any]:
    """Vi the tren san trong ngay, kem setup_code; None = lenh khong gan setup nao."""
    if not _account_enabled():
        return {"enabled": False, "date": day, "positions": []}
    data = await _dash_exchange(("fills", symbol, day), lambda: _account_day(day, [symbol]))
    if data is None or data.get("errors"):
        return {"enabled": True, "date": day, "positions": [], "error": "khong doc duoc lenh tu san"}
    groups = []
    for group in data["positions"]:
        opening = [f for f in group["fills"] if abs(float(f.get("realized_pnl") or 0)) == 0]
        qty = sum(float(f.get("qty") or 0) for f in opening)
        groups.append({
            "position_id": group["position_id"], "side": group["side"], "closed": group["closed"],
            "carried_in": group["carried_in"], "fill_count": group["fill_count"],
            "order_ids": group["order_ids"], "opened_at": group["opened_at"], "closed_at": group["closed_at"],
            "opened_ms": int(group["fills"][0]["ts"]) if group["fills"] else None,
            "qty": round(qty, 8),
            "entry_price": round(sum(float(f["price"]) * float(f["qty"]) for f in opening) / qty, 8) if qty else None,
            "realized_pnl": group["realized_pnl"], "fee": group["fee"]})
    setups = SETUPS.list(symbol=symbol, bias_date=day)
    ids = [s["id"] for s in setups]
    reviews = PLANS.reviews(ids)
    explicit = {s["code"]: reviews[s["id"]]["exchange_fill_ids"] for s in setups if s["id"] in reviews}
    links = dashboard_logic.link_positions(groups, setups, SETUPS.events_of(ids), explicit)
    hide = _dash_cfg()["hide_pnl"]
    for group in groups:
        group["setup_code"] = links.get(group["position_id"])
        if hide:
            group.pop("realized_pnl")
            group.pop("fee")
    return {"enabled": True, "date": day, "positions": groups,
            "unlinked": sum(1 for g in groups if not g["setup_code"])}


async def _save_review(setup_code: str, outcome: Any, r_multiple: Any, note: Any,
                       reviewed_by: str) -> dict[str, Any]:
    setup = SETUPS.get(str(setup_code or "").strip())
    _review_sync([setup])
    label = str(outcome).strip().lower() if outcome not in (None, "") else None
    if label is not None and label not in dashboard_logic.OUTCOMES:
        raise ValueError(f"outcome phai la mot trong {list(dashboard_logic.OUTCOMES)}")
    r_value = None
    if r_multiple not in (None, ""):
        try:
            r_value = float(r_multiple)
        except (TypeError, ValueError):
            raise ValueError(f"r_multiple phai la so, nhan duoc {r_multiple!r}") from None
        if not math.isfinite(r_value):
            raise ValueError("r_multiple khong hop le")
    text = str(note).strip() if note is not None else None
    if label is None and r_value is None and text is None:
        raise ValueError("can it nhat mot trong outcome, r_multiple, note")
    # Lien ket lenh san la phan them: san loi hoac dang tat thi van luu duoc review
    fills = await _dash_fills(setup["symbol"], setup["bias_date"])
    fill_ids = [oid for group in fills["positions"] if group.get("setup_code") == setup["code"]
                for oid in group.get("order_ids") or []]
    review = PLANS.save_review(setup["id"], outcome=label, r_multiple=r_value, note=text,
                               reviewed_by=reviewed_by, fill_ids=fill_ids or None)
    status = dashboard_logic.scenario_status_for(review.get("outcome"))
    linked = PLANS.mark_linked(setup["symbol"], setup["bias_date"], setup["code"], status) if status else []
    return {"setup_code": setup["code"], "review": _review_view(review), "scenarios_updated": linked,
            **_dash_link(setup["symbol"], setup["bias_date"], "review")}


@_tool_with_reasons()
async def save_review(setup_code: str, outcome: str = "", r_multiple: float | None = None,
                      note: str | None = None) -> dict[str, Any]:
    """Gan ket qua review cho mot setup (id hoac code). Chi ghi de truong co truyen.

    outcome: tp_hit | sl_hit | be | manual_exit | not_triggered | missed | invalidated.
    auto_outcome do he thong tinh tu nen M5 sau TRIGGERED va giu nguyen du outcome bi ghi de.
    r_multiple: R thuc te neu co lenh. Lenh san khop voi setup duoc tu luu vao exchange_fill_ids.
    outcome tp_hit -> kich ban active gan voi setup sang played_out; sl_hit, invalidated -> failed.
    """
    return await _save_review(setup_code, outcome, r_multiple, note, "claude")


def _review_stats(symbol: str, from_date: str, to_date: str, group_by: str) -> dict[str, Any]:
    to_day = _dash_day(to_date)
    from_day = (_dash_day(from_date) if str(from_date or "").strip() else
                (datetime.strptime(to_day, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d"))
    if from_day > to_day:
        raise ValueError(f"from ({from_day}) phai truoc to ({to_day})")
    sym = _resolve_symbol(symbol) if str(symbol or "").strip() else None
    setups = [s for s in SETUPS.list(symbol=sym) if from_day <= s["bias_date"] <= to_day]
    reviews = _review_sync(setups)
    events = SETUPS.events_of([s["id"] for s in setups])
    # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR: schematic lay tu TR lien ket
    schematics = PLANS.tr_schematics([s.get("tr_id") for s in setups])
    rows = [{"symbol": s["symbol"], "trigger_type": s["trigger_type"], "direction": s["direction"],
             "phase": s.get("phase"), "event_ref_type": tr_logic.ref_type(s.get("event_ref")),
             "expects": s.get("expects"), "schematic": schematics.get(s.get("tr_id") or ""),
             # TM - #TR-BRANCH - Nhanh kich ban cua setup
             "branch": s.get("branch"), "entry_event": s.get("entry_event"),
             "triggered": any(e["event"] == "TRIGGERED" for e in events[s["id"]]),
             "outcome": (reviews.get(s["id"]) or {}).get("outcome"),
             "r_multiple": (reviews.get(s["id"]) or {}).get("r_multiple")} for s in setups]
    return {"symbol": sym, "from": from_day, "to": to_day, "group_by": group_by,
            **dashboard_logic.review_stats(rows, group_by),
            # TM - #OFF-PLAN - Trong ke hoach vs ngoai ke hoach tren vi the that
            "plan_compare": _op_compare(from_day, to_day, sym)}


@_tool_with_reasons()
def get_review_stats(symbol: str = "", from_date: str = "", to_date: str = "",
                     group_by: str = "trigger_type") -> dict[str, Any]:
    """Thong ke setup theo bias_date: so setup, ti le trigger, win rate, R trung binh.

    symbol: bo trong = moi cap. to_date bo trong = hom nay; from_date bo trong = 30 ngay truoc to_date.
    group_by: trigger_type | direction | symbol. win_rate = thang / lenh da dong (tp_hit, sl_hit, be,
    manual_exit); thang la tp_hit, hoac be/manual_exit co R duong. avg_r chi tinh setup co r_multiple.
    TM - #WYCKOFF-TR: group_by them phase (pha luc tao setup) | event_ref_type (vd TEST) | expects |
    schematic (cua TR lien ket). Nhom cheo bang dau phay, vd "direction,event_ref_type,phase" = win
    rate cua long TEST trong pha C. Setup khong co gia tri nam o nhom "chua_co".
    TM - #TR-BRANCH: group_by them branch (accumulation | distribution | range) | entry_event.
    TM - #OFF-PLAN: plan_compare = vi the THAT trong ke hoach vs ngoai ke hoach (so lenh, win rate, tong PnL
    chua tru phi, R trung binh), them by_classification va by_verdict (lenh ngoai ke hoach da review).
    """
    return _review_stats(symbol, from_date, to_date, group_by)


# ---------------------------------------------------------------- lenh ngoai ke hoach
# TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
# position_watcher doi chieu moi vi the moi tren san voi ke hoach setup. Chi canh bao va ghi nhan:
# khong chan lenh, khong dong lenh (chan lenh la ticket gate OKX rieng).

OP_STORE = off_plan_store.OffPlanStore(SQLITE_PATH)
OP_LOG_WINDOW_MS = 3_600_000     # log_trade cach gio mo vi the trong 1 gio thi coi la cung mot lenh
OP_VIEW_KEYS = ("code", "position_id", "symbol", "side", "trade_date", "entry_avg", "qty", "margin_usd",
                "leverage", "exchange_sl", "exchange_tp", "classification", "flags", "candidate_setups",
                "tr_snapshot", "rule_check", "rule_set", "realized_pnl", "fee", "r_multiple", "review_status",
                "tan_reason", "claude_assessment", "verdict", "lesson", "add_to_plan", "notified", "notify_error")
IN_PLAN = off_plan_logic.IN_PLAN


def _op_cfg() -> dict[str, Any]:
    return _alerts_cfg()["off_plan"]


def _op_active() -> bool:
    return bool(_op_cfg().get("enabled")) and _account_enabled()


def _op_day(ms: int) -> str:
    return off_plan_logic.local_day(ms, TZ)


def _op_prev_day(day: str) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


async def _op_read(symbol: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    """Vi the dang mo + fill tu start_ms gom theo vi the (cung ham voi get_fills). Loi thi nem ra:
    watcher bo qua cap do trong lan poll, khong doan."""
    adapter = _exchange()
    async with httpx.AsyncClient() as client:
        positions = await adapter.positions(client, symbol)
        fills = await adapter.fills(client, start_ms, end_ms, symbol)
    return {"positions": positions, "groups": _fill_positions(fills, symbol)}


async def _op_stops(symbol: str) -> list[dict[str, Any]] | None:
    adapter = _exchange()
    async with httpx.AsyncClient() as client:
        return await adapter.stops(client, symbol)


def _op_setups(symbol: str, at: int) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Setup cung cap: bias_date = ngay mo vi the, hoac setup ngay khac con han tai luc do."""
    day = _op_day(at)
    seen: dict[str, dict[str, Any]] = {}
    for row in (SETUPS.list(symbol=symbol, bias_date=day) + SETUPS.list(symbol=symbol, bias_date=_op_prev_day(day))
                + SETUPS.active(symbol)):
        seen.setdefault(row["id"], row)
    rows = [r for r in seen.values() if int(r["created_at"]) <= at
            and (r["bias_date"] == day or int(r["expires_at"]) >= at)]
    return rows, SETUPS.events_of([r["id"] for r in rows])


def _op_candidates(symbol: str, at: int, min_rr: float) -> list[dict[str, Any]]:
    rows, events = _op_setups(symbol, at)
    out = []
    for setup in rows:
        status = off_plan_logic.status_at(setup, events[setup["id"]], at)
        if status and status != "CANCELLED":
            out.append(off_plan_logic.candidate(setup, status, min_rr))
    return out


def _op_forced(ref: str, symbol: str, side: str, at: int, min_rr: float) -> dict[str, Any]:
    """Setup Tan gan tay (log_trade / reclassify_position). Trang thai lay tai luc mo vi the, bo qua
    FILLED (do chinh vi the nay). Setup luu sau luc mo thi lay trang thai ngay truoc khi FILLED."""
    setup = SETUPS.get(str(ref).strip())
    if setup["symbol"] != symbol:
        raise ValueError(f"setup {setup['code']} la cua {setup['symbol']}, vi the la {symbol}")
    if setup["direction"] != side:
        raise ValueError(f"setup {setup['code']} la {setup['direction']}, vi the la {side}")
    events = SETUPS.events_of([setup["id"]])[setup["id"]]
    status = off_plan_logic.status_at(setup, events, at, ignore_filled=True)
    if status is None:
        filled = next((int(e["at"]) for e in events if e["event"] == "FILLED"), None)
        moment = filled - 1 if filled else int(time.time() * 1000)
        status = off_plan_logic.status_at(setup, events, moment, ignore_filled=True) or "WAITING"
    return off_plan_logic.candidate(setup, status, min_rr)


def _op_journal_setup(symbol: str, side: str, at: int) -> str | None:
    """log_trade(setup_id=...) ghi trong 1 gio quanh luc mo vi the -> lay dung setup do, bo auto-match."""
    day = _op_day(at)
    used = {r["matched_setup_id"] for r in OP_STORE.day_rows(day) if (r.get("explain") or {}).get("forced_by")}
    best: tuple[int, str] | None = None
    for trade in _read_json(JOURNAL_DIR / f"{day}.json", []):
        if not isinstance(trade, dict) or not trade.get("setup_id") or trade["setup_id"] in used:
            continue
        if str(trade.get("symbol") or DEFAULT_SYMBOL) != symbol or trade.get("side") != side:
            continue
        try:
            logged = int(datetime.fromisoformat(str(trade.get("opened_at"))).timestamp() * 1000)
        except ValueError:
            continue
        gap = abs(logged - at)
        if gap <= OP_LOG_WINDOW_MS and (best is None or gap < best[0]):
            best = (gap, trade["setup_id"])
    return best[1] if best else None


def _op_tr(symbol: str, at: int) -> dict[str, Any] | None:
    tr = PLANS.tr_at(symbol, at)
    if not tr:
        return None
    return {"tr_id": tr.get("id"), "timeframe": tr.get("timeframe"), "phase": tr.get("current_phase"),
            "schematic": tr.get("schematic"), "branch": tr.get("branch"),
            "prob_accumulation": tr.get("prob_accumulation"), "prob_distribution": tr.get("prob_distribution")}


def _op_orb(symbol: str, side: str, at: int) -> dict[str, Any] | None:
    """Vi the khop phien ORB dang chay (trong trade window, dung huong pha vo, chua skip) -> IN_PLAN.
    Khong khop thi tra ly do ORB de ghi kem NO_PLAN. ORB tat / khac cap -> None."""
    if not ORB_ENABLED or symbol != ORB_SYMBOL:
        return None
    try:
        svc = _orb()
        if not svc.enabled():
            return None
        reason = None
        for session in svc.resolve():
            sid = session["session_id"]
            day = svc.default_day(session, at)
            checks = svc.orb_checks(sid, day.isoformat(), at=at)
            if checks["opening_range"].get("status") == "no_session":
                continue
            direction = svc.signal(session, day, at).get("direction")
            if checks["in_window"] and not checks["skipped"] and direction == side:
                return {"match": True, "session_id": sid, "or_date": checks["or_date"],
                        "reason": f"phiên {sid} phá {direction} trong trade window"}
            window = f"{str(checks['window_vn']['from'])[11:16]}–{str(checks['window_vn']['to'])[11:16]}"
            if not checks["in_window"]:
                why = f"phiên {sid} ngoài trade window ({window})"
            elif checks["skipped"]:
                why = f"phiên {sid} đã skip"
            elif direction is None:
                why = f"phiên {sid} chưa có tín hiệu phá vỡ"
            else:
                why = f"phiên {sid} phá {direction}, ngược hướng vị thế"
            reason = reason or why
        return {"match": False, "reason": reason} if reason else None
    except Exception as exc:
        print(f"BAMCP off-plan ORB {symbol}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def _op_rule_check(symbol: str, side: str, entry: float, sl: float | None, tp: float | None,
                   margin: float | None, prior: int, realized: float, orb_match: dict[str, Any] | None,
                   at: int) -> dict[str, Any]:
    """Cung ham cham voi check_trade, voi gia vao, SL/TP va margin THAT tren san."""
    if orb_match and orb_match.get("match"):
        if sl is None or tp is None:
            return {"rule_set": "orb", "stop": sl, "target": tp, "margin_usd": margin, "violations": [],
                    "notes": ["chưa có SL/TP trên sàn - chưa chấm rule ORB"]}
        verdict = _orb().score_trade(orb_match["session_id"], orb_match["or_date"], side=side, entry=entry,
                                     stop=sl, target=tp, margin_usd=float(margin or 0.0), symbol=symbol, at=at)
        return {"rule_set": "orb", "stop": sl, "target": tp, "rr": verdict.get("rr"), "margin_usd": margin,
                "violations": list(verdict["rule_violations"]), "notes": []}
    rules = _load_rules(symbol)
    target_points = abs(float(tp) - entry) if tp is not None else None
    trade_type = ("swing" if target_points is not None
                  and target_points >= float(rules["swing_min_take_profit_points"]) else "scalp")
    verdict = _evaluate_trade(symbol=symbol, side=side, entry=entry, stop=sl, target=tp,
                              margin_usd=float(margin or 0.0), trade_type=trade_type, trades=[{}] * prior,
                              rules=rules, realized=realized)
    notes = []
    if sl is None:
        notes.append("chưa có SL trên sàn - bỏ qua rule SL và R:R")
    if tp is None:
        notes.append("chưa có TP trên sàn - bỏ qua rule TP và R:R")
    if not margin:
        notes.append("chưa đọc được margin - bỏ qua rule margin")
    return {"rule_set": "general", "trade_type": verdict["trade_type"], "stop": sl, "target": tp, "rr": verdict["rr"],
            "margin_usd": margin, "trades_before": prior, "realized_before": realized,
            "violations": verdict["rule_violations"], "limits_applied": verdict["limits_applied"], "notes": notes}


def _op_revenge(at: int, row_id: str | None) -> dict[str, Any] | None:
    window = float(_op_cfg().get("revenge_window_minutes") or 0) * 60_000
    if window <= 0:
        return None
    losses = [r for r in OP_STORE.closed_between(int(at - window), at)
              if r["id"] != row_id and (r["realized_pnl"] or 0) + (r["fee"] or 0) < 0]
    if not losses:
        return None
    last = max(losses, key=lambda r: r["closed_at"])
    # Phut theo dong ho (09:26 - 09:05 = 21), cach Tan doc gio tren tin
    return {"ref": last["code"] or last["position_id"] or f"{last['symbol']} {last['side']}",
            "position_id": last["position_id"], "closed_at": _stamp(last["closed_at"]),
            "minutes": at // 60_000 - int(last["closed_at"]) // 60_000, "pnl": last["realized_pnl"]}


def _op_size(matched: dict[str, Any] | None, margin: float | None, rule: dict[str, Any],
             symbol: str) -> dict[str, Any] | None:
    """margin that vuot "Gợi ý vốn N% hạn mức" ghi trong note setup khop."""
    if not matched or not matched.get("capital_pct") or not margin:
        return None
    rules = _load_rules(symbol)
    swing = rule.get("trade_type") == "swing" and float(rules["swing_max_margin_per_trade"]) > 0
    limit = float(rules["swing_max_margin_per_trade"] if swing else rules["max_margin_per_trade"])
    if limit <= 0:
        return None
    suggested = round(limit * float(matched["capital_pct"]) / 100, 2)
    if float(margin) <= suggested * (1 + off_plan_logic.SIZE_TOLERANCE):
        return None
    return {"setup_code": matched["code"], "capital_pct": float(matched["capital_pct"]), "limit": limit,
            "suggested": suggested, "margin_usd": float(margin)}


def _op_brief(cand: dict[str, Any]) -> dict[str, Any]:
    return {k: cand.get(k) for k in ("id", "code", "label", "direction", "status")}


def _op_snapshot(cand: dict[str, Any]) -> dict[str, Any]:
    return {k: cand.get(k) for k in ("id", "code", "label", "direction", "status", "zone_low", "zone_high", "entry",
                                     "stop", "target", "max_chase", "capital_pct", "split_entry", "retest_zone")}


def _op_classify(pos: dict[str, Any], forced: str | None = None) -> dict[str, Any]:
    """Phan loai + co cho mot vi the. pos: {symbol, side, entry, opened_at, margin_usd?, sl?, tp?, row_id?,
    forced_by?}. Moi so lieu ke hoach lay TAI LUC MO vi the. NO_STOP do watcher gan (can thoi gian cho)."""
    symbol, side = str(pos["symbol"]).upper(), pos["side"]
    at, entry = int(pos["opened_at"]), float(pos["entry"])
    day = _op_day(at)
    min_rr = float(_load_rules(symbol)["min_rr"])
    cands = _op_candidates(symbol, at, min_rr)
    forced_by = pos.get("forced_by") or ("reclassify" if forced else None)
    if not forced:
        forced = _op_journal_setup(symbol, side, at)
        forced_by = "log_trade" if forced else None
    forced_cand = _op_forced(forced, symbol, side, at, min_rr) if forced else None
    result = off_plan_logic.classify(side, entry, symbol, cands, forced=forced_cand)
    orb_info = None
    if result["classification"] != IN_PLAN and forced_cand is None:
        orb_info = _op_orb(symbol, side, at)
        if orb_info and orb_info.get("match"):
            result = {"classification": IN_PLAN, "setup": None, "reason": f"khớp ORB: {orb_info['reason']}",
                      "listing": [], "listing_title": None}
    matched = result["setup"]
    others = [r for r in OP_STORE.day_rows(day) if r["id"] != pos.get("row_id") and int(r["opened_at"]) < at]
    prior = [r for r in others if r.get("rule_set") != "orb"]
    realized = round(sum((r["realized_pnl"] or 0) + (r["fee"] or 0) for r in prior
                         if r["closed_at"] and int(r["closed_at"]) <= at), 2)
    rule = _op_rule_check(symbol, side, entry, pos.get("sl"), pos.get("tp"), pos.get("margin_usd"), len(prior),
                          realized, orb_info, at)
    kind = result["classification"]
    nth = 1 + sum(1 for r in others if r["classification"] != IN_PLAN) if kind != IN_PLAN else 0
    revenge = _op_revenge(at, pos.get("row_id"))
    size = _op_size(matched, pos.get("margin_usd"), rule, symbol)
    tr = _op_tr(symbol, at)
    flags = off_plan_logic.build_flags(classification=kind, side=side, violations=rule["violations"], tr=tr,
                                       nth=nth, revenge=revenge, size_over=size)
    snapshot = [_op_snapshot(c) for c in cands]
    if forced_cand is not None and all(c["id"] != forced_cand["id"] for c in cands):
        snapshot.append(_op_snapshot(forced_cand))
    explain = {"reason": result["reason"], "listing": [_op_brief(c) for c in result["listing"]],
               "listing_title": result["listing_title"], "nth": nth, "revenge": revenge, "size": size,
               "forced_by": forced_by, "entry": entry,
               "orb": orb_info, "orb_reason": (orb_info or {}).get("reason") if kind == "NO_PLAN" else None}
    return {"classification": kind, "matched_setup_id": matched["id"] if matched else None,
            "candidates": snapshot, "tr": tr, "rule_check": rule, "flags": flags, "explain": explain,
            "rule_set": "orb" if orb_info and orb_info.get("match") else "general", "forced_by": forced_by}


def _op_check_add(row: dict[str, Any], leg: dict[str, Any]) -> dict[str, Any]:
    """Phan them hop le khi setup khop co ke hoach chia entry ("Chia entry: ... retest LPSY a–b" trong note)
    va gia them nam trong vung retest do, hoac gia them khop dung mot setup khac dang IN_PLAN."""
    symbol, side, at, price = row["symbol"], row["side"], int(leg["at"]), float(leg["price"])
    min_rr = float(_load_rules(symbol)["min_rr"])
    cands = _op_candidates(symbol, at, min_rr)
    matched = None
    if row.get("matched_setup_id"):
        try:
            matched = _op_forced(row["matched_setup_id"], symbol, side, at, min_rr)
        except ValueError:
            matched = None
    reasons = []
    if matched and matched["split_entry"]:
        zone = matched["retest_zone"] or [matched["zone_low"], matched["zone_high"]]
        where = "vùng retest" if matched["retest_zone"] else "vùng setup (note không ghi vùng retest)"
        text = f"{where} {off_plan_logic.price(zone[0])}–{off_plan_logic.price(zone[1])} của {matched['code']}"
        if zone[0] <= price <= zone[1]:
            return {"valid": True, "reason": f"giá thêm {off_plan_logic.price(price)} trong {text} (kế hoạch chia entry)",
                    "setup_code": matched["code"]}
        reasons.append(f"giá thêm {off_plan_logic.price(price)} ngoài {text}")
    elif matched:
        reasons.append(f"{matched['code']} không có kế hoạch chia entry")
    others = [c for c in cands if not matched or c["id"] != matched["id"]]
    result = off_plan_logic.classify(side, price, symbol, others)
    if result["classification"] == IN_PLAN:
        return {"valid": True, "reason": f"phần thêm {result['reason']}", "setup_code": result["setup"]["code"]}
    reasons.append(f"phần thêm: {result['reason']}")
    return {"valid": False, "reason": "; ".join(reasons), "setup_code": matched["code"] if matched else None}


def _op_reclassify(row: dict[str, Any], setup_ref: str, by: str) -> dict[str, Any]:
    explain = row.get("explain") or {}
    info = _op_classify({"symbol": row["symbol"], "side": row["side"], "entry": explain.get("entry", row["entry_avg"]),
                         "opened_at": row["opened_at"], "margin_usd": row.get("margin_usd"), "sl": row["exchange_sl"],
                         "tp": row["exchange_tp"], "row_id": row["id"], "forced_by": by}, forced=setup_ref)
    flags = [f for f in off_plan_logic.FLAGS
             if f in set(info["flags"]) | {f for f in row["flags"] if f in off_plan_logic.KEEP_FLAGS}]
    new_explain = {**info["explain"], **{k: explain[k] for k in ("late_minutes",) if k in explain}}
    before = row["classification"]
    row = OP_STORE.update(row["id"], classification=info["classification"], matched_setup_id=info["matched_setup_id"],
                          candidate_setups=info["candidates"], tr_snapshot=info["tr"], rule_check=info["rule_check"],
                          rule_set=info["rule_set"], flags=flags, explain=new_explain)
    if off_plan_logic.alertable(row) and not row["code"]:
        row = OP_STORE.update(row["id"], code=OP_STORE.assign_code(row["id"]))
    setup = SETUPS.get(setup_ref)
    OP_STORE.add_event(row["id"], "RECLASSIFIED", {"from": before, "to": row["classification"], "by": by,
                                                   "setup_code": setup["code"], "reason": new_explain.get("reason")})
    return row


def _op_link_log_trade(symbol: str, side: str, setup_id: str) -> dict[str, Any] | None:
    """log_trade(setup_id) cho vi the watcher da phan loai: gan vi the vao setup do va phan loai lai."""
    now = int(time.time() * 1000)
    rows = [r for r in OP_STORE.list(from_date=_op_prev_day(_today()), symbol=symbol)
            if r["side"] == side and abs(int(r["opened_at"]) - now) <= OP_LOG_WINDOW_MS
            and not ((r.get("explain") or {}).get("forced_by"))]
    if not rows:
        return None
    row = min(rows, key=lambda r: abs(int(r["opened_at"]) - now))
    before = row["classification"]
    if row["matched_setup_id"] == setup_id and before == IN_PLAN:
        return {"code": row["code"], "position_id": row["position_id"], "classification": before,
                "reclassified": False}
    row = _op_reclassify(row, setup_id, "log_trade")
    return {"code": row["code"], "position_id": row["position_id"], "from": before,
            "classification": row["classification"], "flags": row["flags"], "reclassified": True}


def _op_view(row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out = {k: row.get(k) for k in OP_VIEW_KEYS}
    explain = row.get("explain") or {}
    out.update(opened_at=_stamp(row["opened_at"]), opened_ms=int(row["opened_at"]),
               closed_at=_stamp(row["closed_at"]) if row.get("closed_at") else None,
               status="closed" if row.get("closed_at") else "open", reason=explain.get("reason"),
               explain=explain, review_prompt=f"đánh giá lệnh {row['code']}" if row.get("code") else None)
    if row.get("closed_at") and row.get("r_multiple") is None:
        out["r_note"] = "không có SL nên không tính được R" if row.get("exchange_sl") is None else "chưa tính được R"
    if row.get("matched_setup_id"):
        try:
            out["matched_setup"] = _setup_brief(SETUPS.get(row["matched_setup_id"]))
        except ValueError:
            out["matched_setup"] = None
    if events is not None:
        out["events"] = [{"event": e["event"], "at": _stamp(e["at"]), "details": e["details"],
                          "notified": e["notified"], "notify_error": e["notify_error"]} for e in events]
    return out


def _op_stat(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r.get("closed_at")]
    wins = sum(1 for r in closed if (r.get("realized_pnl") or 0) > 0)
    rs = [float(r["r_multiple"]) for r in closed if r.get("r_multiple") is not None]
    return {"trades": len(rows), "closed": len(closed), "wins": wins,
            "win_rate": round(100 * wins / len(closed), 1) if closed else None,
            "pnl": off_plan_logic.round2(sum(float(r.get("realized_pnl") or 0) for r in closed)),
            "fee": off_plan_logic.round2(sum(float(r.get("fee") or 0) for r in closed)),
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None, "r_count": len(rs)}


def _op_compare(from_day: str, to_day: str, symbol: str | None) -> dict[str, Any]:
    """Trong ke hoach vs ngoai ke hoach tu vi the that (position_watcher), theo classification va verdict."""
    rows = OP_STORE.list(from_date=from_day, to_date=to_day, symbol=symbol)
    inside = [r for r in rows if r["classification"] == IN_PLAN]
    outside = [r for r in rows if r["classification"] != IN_PLAN]

    def grouped(key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in outside if key == "verdict" else rows:
            groups.setdefault(str(row.get(key) or dashboard_logic.NO_GROUP), []).append(row)
        return [{"key": k, **_op_stat(v)} for k, v in sorted(groups.items())]

    return {"in_plan": _op_stat(inside), "off_plan": _op_stat(outside),
            "by_classification": grouped("classification"), "by_verdict": grouped("verdict"),
            "note": ("Vị thế thật do position_watcher ghi (#OFF-PLAN). pnl = realized_pnl chưa trừ phí, win = "
                     "pnl > 0, avg_r chỉ tính lệnh có SL trên sàn. by_verdict chỉ gồm lệnh ngoài kế hoạch.")}


def _op_summary(day: str) -> dict[str, Any]:
    rows = OP_STORE.day_rows(day)
    outside = [r for r in rows if r["classification"] != IN_PLAN]
    coded = [r for r in rows if r.get("code")]
    pending = [r["code"] for r in coded if r["review_status"] == "PENDING"]
    by_class: dict[str, int] = {}
    for row in outside:
        by_class[row["classification"]] = by_class.get(row["classification"], 0) + 1
    return {"enabled": _op_active(), "count": len(outside), "codes": [r["code"] for r in outside if r.get("code")],
            "pending_review": len(pending), "pending_codes": pending,
            "pnl": off_plan_logic.round2(sum(float(r.get("realized_pnl") or 0) for r in outside if r.get("closed_at"))),
            "open": sum(1 for r in outside if not r.get("closed_at")), "by_classification": by_class,
            "in_plan_breach": [r["code"] for r in coded if r["classification"] == IN_PLAN],
            "note": "Lệnh ngoài kế hoạch hôm nay - mở Claude: \"đánh giá lệnh <mã>\" để review."}


def _op_link(row: dict[str, Any]) -> str | None:
    return _dash_url(row["symbol"], row["trade_date"])


OP_WATCHER = off_plan_watcher.OffPlanWatcher(
    OP_STORE, cfg=_op_cfg, notify_cfg=lambda: _alerts_cfg()["notify"], active=_op_active, symbols=_symbols,
    read=_op_read, stops=_op_stops, classify=_op_classify, check_add=_op_check_add, tz=TZ,
    display_tz=lambda: WATCHER.display_tz(), link=_op_link)


def _op_markers(symbol: str, day: str) -> list[dict[str, Any]]:
    """Marker cam diem vao lenh ngoai ke hoach tren chart. Chi chu tai khoan (lo vi the)."""
    return [{"kind": "off_plan", "t": int(r["opened_at"]), "price": (r.get("explain") or {}).get("entry", r["entry_avg"]),
             "side": r["side"], "code": r["code"], "classification": r["classification"]}
            for r in OP_STORE.day_rows(day) if r["symbol"] == symbol and r.get("code")]


@_tool_with_reasons()
def get_off_plan_trades(date: str = "", status: str = "", symbol: str = "", code: str = "") -> dict[str, Any]:
    """Lenh ngoai ke hoach (co ma OP-...) do position_watcher bat: phan loai, co, setup ung vien, TR, ket qua.

    date: 'YYYY-MM-DD' ngay mo vi the (bo trong = hom nay). code: ma OP-... (bo qua date).
    status: PENDING | REVIEWED | open | closed hoac ma phan loai (COUNTER_PLAN...), nhieu gia tri cach dau phay.
    symbol: loc theo cap.
    classification: IN_PLAN (chi co ma khi pham rule / thieu SL) | EARLY (setup chua trigger) | CHASE (vuot
      max_chase hoac setup da MISSED) | OUT_OF_ZONE | COUNTER_PLAN (ke hoach chi co huong nguoc lai) | NO_PLAN
      | PRUNED_SETUP (chi khop setup da ket thuc). Phan loai theo trang thai setup TAI LUC MO vi the.
    flags: NO_STOP, REVENGE, RULE_BREACH, AGAINST_TR_BRANCH, REPEAT_OFF_PLAN, SIZE_OVER_PLAN, ADD_OFF_PLAN.
    candidate_setups: anh chup setup luc phan loai; tr_snapshot: TR luc do; rule_check: ket qua ham check_trade
    voi gia vao, SL/TP, margin that. realized_pnl chua tru phi (fee rieng); r_multiple null khi khong co SL.

    QUY TRINH khi Tan nhan "danh gia lenh OP-...":
    1. get_off_plan_trades(code=...) lay lenh; get_setups(date=trade_date) + get_chart_data(symbol, "15m",
       date=trade_date) lay ke hoach va TR luc do.
    2. Keo nen M15 (>= 24 nen truoc gio vao) va M5 quanh gio vao bang get_klines - chi doc nen DA DONG tai
       thoi diem vao lenh, khong dung nen sau do de bien minh.
    3. Chua co tan_reason thi hoi Tan ly do vao lenh, ghi tan_reason.
    4. Cham theo checklist vao lenh M15: co vung khong, co trigger khong, khop su kien Wyckoff nao, R:R, SL;
       so voi cac setup trong ke hoach (candidate_setups).
    5. Ket luan verdict (valid_unplanned | discipline_breach | execution_error | misclassified) + lesson.
       valid_unplanned thi de xuat cach bo sung loai setup do vao quy trinh phan tich (add_to_plan=true).
       He thong phan loai sai thi reclassify_position(position_id, setup_id).
    6. review_off_plan_trade(code, ...) luu lai.
    """
    sym = _resolve_symbol(symbol) if str(symbol or "").strip() else None
    if str(code or "").strip():
        rows = [OP_STORE.get(code)]
    else:
        day = _dash_day(date)
        rows = OP_STORE.list(from_date=day, to_date=day, symbol=sym, coded_only=True)
    wanted = {s.strip().upper() for s in str(status or "").split(",") if s.strip()}
    if wanted:
        rows = [r for r in rows if wanted & {r["review_status"], r["classification"],
                                             "CLOSED" if r.get("closed_at") else "OPEN"}]
    events = OP_STORE.events([r["id"] for r in rows])
    return {"count": len(rows), "watcher_enabled": _op_active(),
            "trades": [_op_view(r, events[r["id"]]) for r in rows],
            "verdicts": list(off_plan_logic.VERDICTS)}


@_tool_with_reasons()
def review_off_plan_trade(code: str, tan_reason: str | None = None, claude_assessment: str | None = None,
                          verdict: str | None = None, lesson: str | None = None,
                          add_to_plan: bool | None = None) -> dict[str, Any]:
    """Luu danh gia cho mot lenh OP-.... Chi ghi de truong co truyen; co verdict thi review_status = REVIEWED.

    verdict: valid_unplanned (co hoi hop le chua co trong ke hoach) | discipline_breach (vi pham ky luat) |
      execution_error (loi thao tac) | misclassified (he thong phan loai sai - goi reclassify_position).
    add_to_plan: co nen bo sung loai setup nay vao quy trinh phan tich khong.
    """
    row = OP_STORE.get(str(code or "").strip())
    changes: dict[str, Any] = {}
    for key, value in (("tan_reason", tan_reason), ("claude_assessment", claude_assessment), ("lesson", lesson)):
        if value is not None:
            changes[key] = str(value).strip() or None
    if verdict is not None and str(verdict).strip():
        label = str(verdict).strip().lower()
        if label not in off_plan_logic.VERDICTS:
            raise ValueError(f"verdict phai la mot trong {list(off_plan_logic.VERDICTS)}")
        changes.update(verdict=label, review_status="REVIEWED")
    if add_to_plan is not None:
        changes["add_to_plan"] = bool(add_to_plan)
    if not changes:
        raise ValueError("can it nhat mot truong: tan_reason, claude_assessment, verdict, lesson, add_to_plan")
    row = OP_STORE.update(row["id"], **changes)
    OP_STORE.add_event(row["id"], "REVIEWED", {k: v for k, v in changes.items()})
    return {"code": row["code"], "review_status": row["review_status"], "trade": _op_view(row)}


@_tool_with_reasons()
def reclassify_position(position_id: str, setup_id: str) -> dict[str, Any]:
    """He thong phan loai sai: gan vi the vao setup roi phan loai lai (event RECLASSIFIED).

    position_id: dang get_fills (BTCUSDT-P1, uu tien hom nay) hoac ma OP-.... setup_id: id hoac code setup,
    phai cung cap va cung huong voi vi the. Trang thai setup lay tai luc mo vi the: setup chua trigger thi
    van ra EARLY - gan tay khong bien lenh vao som thanh trong ke hoach. Khong doi trang thai setup
    (muon setup sang FILLED thi dung log_trade(setup_id=...)).
    """
    row = OP_STORE.find_position(position_id, _today())
    before = row["classification"]
    row = _op_reclassify(row, str(setup_id or "").strip(), "reclassify")
    return {"position_id": row["position_id"], "code": row["code"], "from": before,
            "classification": row["classification"], "trade": _op_view(row)}


# ---------------------------------------------------------------- admin

ADMIN_PATH = SRV.get("admin_path", "/admin")
ADMIN_SAVE_PATH = ADMIN_PATH.rstrip("/") + "/save"
ADMIN_TEST_PATH = ADMIN_PATH.rstrip("/") + "/test"
ADMIN_SYMBOL_PATH = ADMIN_PATH.rstrip("/") + "/symbols"
# TM - #ADMIN-LOGIN - Form dang nhap admin
ADMIN_LOGIN_PATH = ADMIN_PATH.rstrip("/") + "/login"
ADMIN_LOGOUT_PATH = ADMIN_PATH.rstrip("/") + "/logout"


def _admin_nav() -> list[tuple[str, str, str]]:
    """Thanh dieu huong chung cua cac trang admin. Module dang tat thi an trang cua no."""
    items = [("admin", "Cài đặt", ADMIN_PATH)]
    if ORB_ENABLED:
        items.append(("orb", "Phiên ORB", ADMIN_ORB_PATH))
    if GANN_ENABLED:
        items.append(("gann", "Gann", ADMIN_GANN_PATH))
    items.append(("schedules", "Kéo nến", ADMIN_SCHEDULES_PATH))
    items.append(("alerts", "Vào lệnh", ADMIN_ALERTS_PATH))
    # TM - #DASHBOARD - Setup dashboard
    items.append(("dashboard", "Dashboard accounts", ADMIN_DASH_PATH))
    # TM - #ADMIN-LOGIN - Form dang nhap admin
    if AUTH_ENABLED and STORE.has_auth():
        items.append(("logout", "Đăng xuất", ADMIN_LOGOUT_PATH))
    return items


def _setup_ok(payload: dict[str, Any]) -> bool:
    """Truoc khi co mat khau, moi thao tac ghi phai kem setup token."""
    if STORE.has_auth():
        return True        # da qua dang nhap o middleware
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


def _numeric_rules(raw: dict[str, Any]) -> dict[str, float]:
    """Form gui so duoi dang chuoi. Doi sang so truoc khi dua vao _apply_rule_changes.

    Nem ValueError de di chung mot duong bao loi voi phan validate con lai.
    """
    numeric: dict[str, float] = {}
    for key, value in raw.items():
        try:
            numeric[key] = float(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{key} phai la so, nhan duoc '{value}'") from None
    return numeric


@mcp.custom_route(ADMIN_PATH, methods=["GET"])
async def http_admin(request):
    from starlette.responses import HTMLResponse
    tracked = STORE.symbols(DEFAULT_SYMBOL)
    # TM - #KL-SQL - Kline storage SQLite
    coverage = _kline_coverage()
    ready = {
        row["symbol"]: all((coverage.get((row["symbol"], tf)) or {}).get("count")
                           for tf in KL["timeframes"])
        for row in tracked
    }
    doc = _rules_doc()
    # Gia tri DA GIAI cho tung cap: cap chua dat rieng thi hien gia tri mac dinh
    # dang ke thua, de o nhap khong bao gio trong va nguoi dung thay ngay no dang
    # chay theo so nao.
    symbol_rules = {
        row["symbol"]: {k: doc["symbols"].get(row["symbol"], {}).get(k, doc["values"][k])
                        for k in SYMBOL_RULE_KEYS}
        for row in tracked
    }
    page = admin.render(
        STORE.masked(),
        settings_path=str(SETTINGS_FILE),
        save_path=ADMIN_SAVE_PATH,
        test_path=ADMIN_TEST_PATH,
        symbol_path=ADMIN_SYMBOL_PATH,
        exchange_names=settings.EXCHANGE_NAMES,
        rules=doc["values"],
        rules_history=_rules_history(),
        symbols=tracked,
        symbol_ready=ready,
        symbol_rules=symbol_rules,
        symbol_overrides={s: sorted(o) for s, o in doc["symbols"].items()},
        global_rule_keys=GLOBAL_RULE_KEYS,
        symbol_rule_keys=SYMBOL_RULE_KEYS,
        initial_timeframes=KL["timeframes"],
        nav=_admin_nav(),
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
        reason = str(payload.get("rules_reason") or "")

        # Gom cac lo rule can ghi. Rule chung va rule tung cap la nhung lan ghi
        # rieng biet, nen phai soat het ca lo TRUOC khi ghi lo dau tien - khong
        # thi mot so go sai o cap cuoi se de lai nua chung da ghi, nua bao loi.
        batches: list[tuple[dict[str, Any], str]] = []

        raw_rules = payload.get("rules")
        if isinstance(raw_rules, dict) and raw_rules:
            batches.append((_numeric_rules(raw_rules), ""))

        raw_symbol_rules = payload.get("symbol_rules")
        if isinstance(raw_symbol_rules, dict):
            for sym, raw in raw_symbol_rules.items():
                if not isinstance(raw, dict) or not raw:
                    continue
                batches.append((_numeric_rules(raw), str(sym)))

        for numeric, sym in batches:
            _validate_rule_changes(numeric, _resolve_symbol(sym) if sym else "")
        if batches and not reason.strip():
            raise ValueError("reason la bat buoc - ghi ro vi sao doi rule")

        # Rule theo cap: moi cap mot lan ghi, moi lan mot dong lich su rieng
        # gan ten cap - nhin lai van biet siet cap nao.
        rules_touched = False
        for numeric, sym in batches:
            result = _apply_rule_changes(numeric, reason, symbol=sym, source="admin")
            if not result["updated"]:
                continue
            rules_touched = True
            changed.append(
                f"{len(result['changes'])} quy dinh chung" if not sym else
                f"{len(result['changes'])} quy dinh cho {result['symbol']}")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse({
        "saved": True,
        "message": "Da luu: " + ", ".join(changed) + ". Co hieu luc ngay.",
        # Vua dat hoac doi mat khau thi tai lai: phien cu het hieu luc, trang chuyen ve form dang nhap.
        # Doi rule cung tai lai: trang giu ban sao rule theo cap trong JS, khong
        # nap lai thi lan luu ke tiep so sanh voi gia tri cu va gui thua.
        "reload": bool(password) or rules_touched,
        "state": STORE.masked(),
    })




@mcp.custom_route(ADMIN_SYMBOL_PATH, methods=["POST"])
async def http_admin_symbols(request):
    """Them / bat tat / bo mot cap giao dich.

    Them thi hoi Binance truoc xem cap co that khong - khong giu danh sach cung
    vi no se lac hau, va bao loi ngay con hon de nguoi dung cho mai khong thay
    du lieu ve.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "body khong phai JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body phai la object"}, status_code=400)
    if not _setup_ok(payload):
        return JSONResponse({"error": "Setup token sai"}, status_code=403)

    action = str(payload.get("action") or "").strip().lower()
    symbol = str(payload.get("symbol") or "").strip().upper()

    try:
        if action == "add":
            if STORE.has_symbol(symbol):
                raise ValueError(f"{symbol} da co trong danh sach")
            await probe_symbol(symbol)          # kiem chung voi Binance truoc
            STORE.add_symbol(symbol)
            # Keo ngay de nguoi dung khong phai cho het mot chu ky 15 phut
            await fetch_all([symbol], KL["timeframes"])
            message = f"Da them {symbol} va keo du lieu ban dau."
        elif action in ("enable", "disable"):
            STORE.set_symbol_enabled(symbol, action == "enable")
            message = f"Da {'bat' if action == 'enable' else 'tat'} {symbol}."
        elif action == "remove":
            STORE.remove_symbol(symbol)
            message = f"Da bo {symbol} khoi danh sach. File du lieu van giu nguyen."
        else:
            raise ValueError("action phai la add, enable, disable hoac remove")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)

    return JSONResponse({"ok": True, "message": message,
                         "symbols": STORE.symbols(DEFAULT_SYMBOL)})


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

# TM - #ORB - ORB Enhancement: trang quan ly phien ORB (BR-12)
ADMIN_ORB_PATH = ADMIN_PATH.rstrip("/") + "/orb"
ADMIN_ORB_ACTION_PATH = ADMIN_ORB_PATH + "/sessions"
# TM - #ORB-RULES - ORB Rule Set: phan "Rule ORB" + news days tren trang /admin/orb
ADMIN_ORB_RULES_PATH = ADMIN_ORB_PATH + "/rules"


def _news_entries() -> list[dict[str, str]]:
    """File news days: [{date, note}]. Dang cu (list chuoi / {"dates": [...]}) van doc duoc."""
    raw = _read_json(ORB_NEWS_FILE, [])
    if isinstance(raw, dict):
        raw = raw.get("dates") or raw.get("days") or []
    out: dict[str, dict[str, str]] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            day, note = str(item.get("date") or "")[:10], str(item.get("note") or "")
        else:
            day, note = str(item)[:10], ""
        if day:
            out[day] = {"date": day, "note": note}
    return [out[k] for k in sorted(out)]


def _news_change(action: str, day: str, note: str = "") -> dict[str, Any]:
    day = orb.parse_date(day).isoformat()
    with _RULES_LOCK:
        entries = {e["date"]: e for e in _news_entries()}
        if action == "news_add":
            entries[day] = {"date": day, "note": str(note or "").strip()[:200]}
        elif day in entries:
            entries.pop(day)
        else:
            raise ValueError(f"ngay {day} khong co trong danh sach news days")
        _write_json(ORB_NEWS_FILE, [entries[k] for k in sorted(entries)])
    ORB_LOG("news_days", action=action, date=day)
    if ORB_SCHEDULER is not None:
        ORB_SCHEDULER.wake()
    return {"ok": True, "date": day, "news_days": _news_entries()}


def _orb_rules_view() -> dict[str, Any]:
    """Du lieu cho phan Rule ORB: gia tri tung lop + ban da giai + trang thai hom nay."""
    svc = _orb()
    doc = _rules_doc()
    block = doc["orb"]
    symbols = sorted({ORB_SYMBOL, *block["symbol_overrides"]})
    scopes = [{"key": "defaults", "label": "orb.defaults (moi cap)", "layer": "defaults",
               "values": block["defaults"],
               "resolved": orb_rules.resolve({**block, "symbol_overrides": {}}, ORB_SYMBOL)}]
    for sym in symbols:
        scopes.append({"key": f"symbol:{sym}", "label": f"Cap {sym}", "layer": "symbol",
                       "symbol": sym, "values": block["symbol_overrides"].get(sym, {}),
                       "resolved": orb_rules.resolve(block, sym)})
    for session in ORB_SESSIONS.all(include_disabled=True):
        sid = session["session_id"]
        scopes.append({"key": f"session:{sid}", "label": f"Phien {sid} ({session['name']})",
                       "layer": "session", "session_id": sid,
                       "values": svc.session_rule_values(session),
                       "resolved": svc.rules_for(session)})
    today = _orb_today(_today())
    snapshot = svc.market_snapshot()
    history = [h for h in doc["history"] if h.get("scope") == "orb"][-30:]
    return {
        "enabled": block["enabled"],
        "symbol": ORB_SYMBOL,
        "scopes": scopes,
        "fields": {k: {**v, "session": k in orb_rules.SESSION_FIELDS,
                       "required_default": k in orb_rules.DEFAULTS}
                   for k, v in orb_rules.FIELDS.items()},
        "status": today,
        "market": snapshot,
        "warnings": orb_rules.deprecated_fields(ORB_CFG) + _orb_warnings(block, snapshot=snapshot),
        "history": list(reversed(history)),
        "news_days": _news_entries(),
        "news_file": str(ORB_NEWS_FILE),
        "rules_file": str(RULES_FILE),
    }


def _orb_admin_page() -> str:
    svc = _orb()
    return admin_orb.render(
        rows=svc.list_sessions(include_disabled=True),
        sessions=ORB_SESSIONS.all(include_disabled=True),
        history=ORB_SESSIONS.history(20),
        action_path=ADMIN_ORB_ACTION_PATH,
        admin_path=ADMIN_PATH,
        store_path=str(ORB_SESSIONS_FILE),
        global_params=svc.params(),
        setup_required=not STORE.has_auth(),
        now_ms=svc.now(),
        # TM - #ORB-RULES - ORB Rule Set
        rules_view=_orb_rules_view(),
        rules_path=ADMIN_ORB_RULES_PATH,
        nav=_admin_nav(),
    )


def _orb_rules_action(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /admin/orb/rules: preview | save | news_add | news_delete."""
    action = str(payload.get("action") or "")
    try:
        if action in ("news_add", "news_delete"):
            return 200, _news_change(action, str(payload.get("date") or ""),
                                     str(payload.get("note") or ""))
        if action not in ("preview", "save"):
            return 400, {"error": f"action khong ho tro: {action!r}"}
        scope = str(payload.get("scope") or "defaults")
        kind, _, name = scope.partition(":")
        if kind not in ("defaults", "symbol", "session"):
            return 400, {"error": f"scope khong hop le: {scope!r}"}
        changes = payload.get("changes")
        if not isinstance(changes, dict):
            return 400, {"error": "changes phai la object"}
        result = _apply_orb_rule_changes(
            changes, str(payload.get("reason") or ""),
            symbol=name if kind == "symbol" else "",
            session_id=name if kind == "session" else "",
            source="admin", dry_run=action == "preview")
        return 200, {"ok": True, **result}
    except orb.SessionValidationError as exc:
        return 400, {"error": str(exc), "errors": exc.errors}
    except ValueError as exc:
        return 400, {"error": str(exc)}


@mcp.custom_route(ADMIN_ORB_PATH, methods=["GET"])
async def http_admin_orb(request):
    from starlette.responses import HTMLResponse
    if not ORB_ENABLED:
        return HTMLResponse(
            "<p>ORB dang tat. Bat <code>orb.enabled</code> trong config.yaml roi restart.</p>",
            status_code=404, headers={"Cache-Control": "no-store"})
    page = await asyncio.to_thread(_orb_admin_page)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@mcp.custom_route(ADMIN_ORB_ACTION_PATH, methods=["POST"])
async def http_admin_orb_sessions(request):
    if not ORB_ENABLED:
        return JSONResponse({"error": "ORB dang tat"}, status_code=404)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "body khong phai JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body phai la object"}, status_code=400)
    if not _setup_ok(payload):
        return JSONResponse({"error": "Setup token sai"}, status_code=403)
    status, body = await asyncio.to_thread(
        admin_orb.handle, payload, sessions=ORB_SESSIONS, now_ms=ORB_SVC.now(),
        params_for=ORB_SVC.params)
    return JSONResponse(body, status_code=status)


@mcp.custom_route(ADMIN_ORB_RULES_PATH, methods=["POST"])
async def http_admin_orb_rules(request):
    """TM - #ORB-RULES - ORB Rule Set: luu rule ORB / news days tu trang admin."""
    if not ORB_ENABLED:
        return JSONResponse({"error": "ORB dang tat"}, status_code=404)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "body khong phai JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body phai la object"}, status_code=400)
    if not _setup_ok(payload):
        return JSONResponse({"error": "Setup token sai"}, status_code=403)
    status, body = await asyncio.to_thread(_orb_rules_action, payload)
    return JSONResponse(body, status_code=status)


# ---------------------------------------------------------------- admin Gann

# TM - #GANN-TW - Gann Time Windows
ADMIN_GANN_PATH = ADMIN_PATH.rstrip("/") + "/gann"
ADMIN_GANN_ACTION_PATH = ADMIN_GANN_PATH + "/action"
_GANN_ADMIN_LOCK = threading.Lock()
GANN_LEVELS = ("major", "intermediate", "minor")


def _gann_num(value: Any, name: str, low: float = 0.0, high: float | None = None,
              integer: bool = False) -> float | int:
    """Ep kieu + kiem khoang cho mot so trong config. Sai thi bao ten truong."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} phai la so, dang la {value!r}")
    if integer and int(value) != value:
        raise ValueError(f"{name} phai la so nguyen, dang la {value!r}")
    if value < low or (high is not None and value > high):
        raise ValueError(f"{name} = {value} nam ngoai khoang [{low}, {high if high is not None else 'vo cung'}]")
    return int(value) if integer else float(value)


def _gann_per_tf(value: Any, name: str, **limits: Any) -> None:
    """Truong dang mot so, hoac {1d: so, 1w: so}."""
    if isinstance(value, dict):
        for tf, item in value.items():
            if tf not in GANN_TIMEFRAMES:
                raise ValueError(f"{name}: khung {tf!r} khong co pivot (chi {list(GANN_TIMEFRAMES)})")
            _gann_num(item, f"{name}.{tf}", **limits)
    else:
        _gann_num(value, name, **limits)


def _gann_validate_section(section: str, value: Any) -> Any:
    """Kiem mot khoi config truoc khi ghi. Sai thi ValueError noi ro truong nao.

    Kiem tung truong co nghia, roi chay thu: mot khoi sai cau truc ma lot qua
    thi moi lan goi tool sau do deu vo - tot hon la bao ngay tren trang admin.
    """
    if section == "symbols":
        if not isinstance(value, list) or not all(isinstance(s, str) and s.strip() for s in value):
            raise ValueError("symbols phai la list ten cap, vd [\"BTCUSDT\", \"ETHUSDT\"]")
        return [s.strip().upper() for s in value]
    if not isinstance(value, dict):
        raise ValueError(f"khoi {section} phai la object {{...}}")

    if section == "pivots":
        _gann_per_tf(value.get("swing_bars", 2), "swing_bars", low=1, high=10, integer=True)
        _gann_num(value.get("atr_period", 14), "atr_period", low=1, high=200, integer=True)
        _gann_per_tf(value.get("min_move_pct", 0), "min_move_pct", high=100)
        _gann_per_tf(value.get("min_move_atr", 0), "min_move_atr", high=100)
        _gann_num(value.get("intermediate_move_pct", 0), "intermediate_move_pct", high=100)
        _gann_num(value.get("major_merge_days", 3), "major_merge_days", high=30, integer=True)
    elif section == "projections":
        _gann_num(value.get("tolerance_days", 0), "tolerance_days", high=15, integer=True)
        for key in ("max_pivot_age_days", "level_weights"):
            block = value.get(key) or {}
            if not isinstance(block, dict):
                raise ValueError(f"{key} phai la object {{major: .., intermediate: .., minor: ..}}")
            for level, item in block.items():
                if level not in GANN_LEVELS:
                    raise ValueError(f"{key}: bac {level!r} khong ton tai")
                _gann_num(item, f"{key}.{level}", integer=key == "max_pivot_age_days")
        for kind in gann_windows.PROJECTION_TYPES:
            block = value.get(kind)
            if block is None:
                continue
            if not isinstance(block, dict):
                raise ValueError(f"{kind} phai la object")
            if "enabled" in block and not isinstance(block["enabled"], bool):
                raise ValueError(f"{kind}.enabled phai la true/false")
            if "weight" in block:
                _gann_num(block["weight"], f"{kind}.weight", high=100)
        days = (value.get("cycle") or {}).get("days") or {}
        if not isinstance(days, dict):
            raise ValueError("cycle.days phai la object {so_ngay: trong_so}")
        for step, weight in days.items():
            try:
                count = int(step)
            except (TypeError, ValueError):
                raise ValueError(f"cycle.days: {step!r} khong phai so ngay") from None
            if count <= 0 or count > 3650:
                raise ValueError(f"cycle.days: {count} ngay nam ngoai [1, 3650]")
            _gann_num(weight, f"cycle.days.{step}", high=100)
        anniversary = value.get("anniversary") or {}
        bad = [x for x in anniversary.get("levels") or [] if x not in GANN_LEVELS]
        if bad:
            raise ValueError(f"anniversary.levels co bac khong ton tai: {bad}")
        if "max_years" in anniversary:
            _gann_num(anniversary["max_years"], "anniversary.max_years", low=1, high=30, integer=True)
        for text in (value.get("seasonal") or {}).get("dates") or []:
            try:
                datetime.strptime(f"2024-{text}", "%Y-%m-%d")
            except (TypeError, ValueError):
                raise ValueError(f"seasonal.dates: {text!r} khong phai 'MM-DD'") from None
        swing = value.get("swing_duration") or {}
        for ratio in swing.get("ratios") or []:
            _gann_num(ratio, "swing_duration.ratios", low=0.01, high=20)
        if "legs" in swing:
            _gann_num(swing["legs"], "swing_duration.legs", low=1, high=20, integer=True)
        for sym, scale in ((value.get("range_square") or {}).get("scale_factor") or {}).items():
            _gann_num(scale, f"range_square.scale_factor.{sym}", low=1e-9)
    elif section == "scoring":
        _gann_num(value.get("min_score", 0), "min_score", high=1000)
        _gann_num(value.get("merge_gap_days", 0), "merge_gap_days", high=30, integer=True)
        _gann_num(value.get("max_hits_per_window", 8), "max_hits_per_window", low=1, high=50,
                  integer=True)
    elif section == "backtest":
        _gann_num(value.get("atr_period", 20), "atr_period", low=1, high=200, integer=True)
        _gann_num(value.get("volume_lookback", 20), "volume_lookback", low=2, high=200, integer=True)
        _gann_num(value.get("pivot_near_days", 2), "pivot_near_days", high=30, integer=True)
        _gann_num(value.get("shift_days", 30), "shift_days", low=1, high=365, integer=True)
        _gann_num(value.get("warmup_days", 120), "warmup_days", high=2000, integer=True)
    elif section == "context":
        for flag in ("include_in_get_context", "time_windows"):
            if flag in value and not isinstance(value[flag], bool):
                raise ValueError(f"context.{flag} phai la true/false")
        _gann_num(value.get("horizon_days", 7), "horizon_days", low=1, high=60, integer=True)
        _gann_num(value.get("max_windows", 3), "max_windows", low=1, high=10, integer=True)
        _gann_num(value.get("max_hits", 3), "max_hits", low=1, high=10, integer=True)

    # Chay thu voi khoi moi tren du lieu gia: loi cau truc nao lot qua thi lo o day
    trial = {**copy.deepcopy(_gann_cfg()), section: value}
    day = date(2025, 1, 1)
    fake = [{"type": "low", "date": "2024-10-01", "level": "major", "price": 100.0,
             "duration_bars": 10, "span_days": 1, "timeframe": "1d"}]
    gann_windows.build(fake, day, 30, trial, [], "BTCUSDT", min_score=0.01)
    if section == "pivots":
        bars = [{"open_time": i * 86_400_000, "open": 100.0 + i % 7, "high": 102.0 + i % 7,
                 "low": 98.0 + i % 7, "close": 100.0 + i % 7, "volume": 1.0,
                 "close_time": (i + 1) * 86_400_000 - 1} for i in range(60)]
        gann_pivots.build(bars, [f"d{i}" for i in range(60)], "1d", value, 86_400_000)
    return value


def _gann_toggle(symbol: str, enabled: Any) -> tuple[int, dict[str, Any]]:
    """Bat/tat Gann cho mot cap. Ghi khoi symbols cua file config admin.

    Bat thi cap phai dang duoc theo doi (co trong trang cai dat chung) - khong
    thi khong co nen nao de tinh. Tat thi khong doi gi ca: cap da go khoi he
    thong ma con nam trong danh sach Gann van phai tat duoc.
    """
    sym = symbol.strip().upper()
    if not sym:
        return 400, {"error": "thieu symbol"}
    if not isinstance(enabled, bool):
        return 400, {"error": "enabled phai la true/false"}
    known = [r["symbol"] for r in STORE.symbols(DEFAULT_SYMBOL)]
    if enabled and sym not in known:
        return 400, {"error": f"{sym} chua duoc theo doi. Them cap o trang cai dat chung "
                              f"({ADMIN_PATH}) truoc, roi bat Gann."}
    with _GANN_ADMIN_LOCK:
        wanted = [s for s in _gann_wanted() if s != sym]
        if enabled:
            wanted.append(sym)
        # Giu thu tu theo trang cai dat chung - cap dau tien la mac dinh cua tool
        order = {s: i for i, s in enumerate(known)}
        wanted.sort(key=lambda s: order.get(s, len(order)))
        override = _read_json(GANN_CONFIG_FILE, {}) if GANN_CONFIG_FILE.exists() else {}
        if not isinstance(override, dict):
            override = {}
        override["symbols"] = wanted
        _write_json(GANN_CONFIG_FILE, override)
        _gann_cfg_invalidate()
    note = (f"Da bat Gann cho {sym}." if enabled else f"Da tat Gann cho {sym}.")
    return 200, {"ok": True, "symbols": _gann_symbols(), "note": note
                 + " Co hieu luc ngay, khong can restart."}


def _gann_toggle_rows() -> list[dict[str, Any]]:
    """Moi cap dang theo doi (va cap lo nam trong danh sach Gann) kem tinh trang du lieu."""
    rows = []
    known = STORE.symbols(DEFAULT_SYMBOL)
    wanted = _gann_wanted()
    names = [r["symbol"] for r in known] + [s for s in wanted
                                             if s not in {r["symbol"] for r in known}]
    app_on = {r["symbol"]: r["enabled"] for r in known}
    for sym in names:
        row: dict[str, Any] = {"symbol": sym, "gann": sym in wanted,
                               "tracked": sym in app_on, "app_enabled": app_on.get(sym, False)}
        for tf in GANN_TIMEFRAMES:
            try:
                closed, _ = _split_closed(_load_bars(sym, tf), tf)
            except Exception:
                closed = []
            row[f"bars_{tf}"] = len(closed)
            row[f"first_{tf}"] = _bar_date(closed[0]) if closed else None
        rows.append(row)
    return rows


def _gann_admin_view() -> dict[str, Any]:
    """Du lieu cho trang admin Gann. Loi cua tung cap nam trong dong cua cap do."""
    symbols = []
    for sym in _gann_symbols():
        row: dict[str, Any] = {"symbol": sym}
        try:
            doc = _gann_doc(sym)
            row["computed_at"] = doc.get("computed_at")
            recent = []
            for tf, keep in (("1d", 20), ("1w", 10)):
                live = gann_pivots.live_pivots((doc["timeframes"].get(tf) or {}).get("pivots") or [])
                row[f"pivots_{tf}"] = len(live)
                for p in live[-keep:]:
                    recent.append({"timeframe": tf, "type": p["type"],
                                   "date": _bar_date({"open_time": p["time_ms"]}),
                                   "price": p["price"], "level": p.get("level"),
                                   "move_pct": p.get("move_pct"), "source": p.get("source")})
            recent.sort(key=lambda p: (p["date"], p["timeframe"]), reverse=True)
            row["recent"] = recent
            state = _gann_swing_state(sym, "1d")
            row["trend"], row["note"] = state.get("trend"), state.get("note")
        except Exception as exc:
            row["error"] = str(exc)
        symbols.append(row)

    override = {}
    try:
        override = _read_json(GANN_CONFIG_FILE, {}) if GANN_CONFIG_FILE.exists() else {}
    except Exception:
        override = {}
    current = _gann_cfg()
    config = {section: {"value": current.get(section),
                        "overridden": isinstance(override, dict) and section in override}
              for section in GANN_EDITABLE}

    latest = None
    if GANN_BACKTEST_DIR.exists():
        for folder in sorted((p for p in GANN_BACKTEST_DIR.iterdir() if p.is_dir()),
                             reverse=True):
            try:
                latest = _read_json(folder / "result.json", None)
            except Exception:
                latest = None
            if latest:
                break

    manual = _read_json(GANN_MANUAL_FILE, {}) if GANN_MANUAL_FILE.exists() else {}
    events, _ = _gann_events()
    return {
        "symbols": symbols,
        "toggles": _gann_toggle_rows(),
        "admin_path": ADMIN_PATH,
        "manual": manual if isinstance(manual, dict) else {},
        "manual_file": str(GANN_MANUAL_FILE),
        "events": events,
        "events_file": str(GANN_EVENTS_FILE),
        "config": config,
        "config_file": str(GANN_CONFIG_FILE),
        "config_version": _gann_config_version(),
        "config_warning": _GANN_CFG_CACHE.get("warning"),
        "backtest": latest,
    }


def _gann_admin_action(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /admin/gann/action. Moi thao tac ghi file theo kieu tam + rename."""
    action = str(payload.get("action") or "")
    try:
        with _GANN_ADMIN_LOCK:
            if action in ("config_save", "config_reset"):
                section = str(payload.get("section") or "")
                if section not in GANN_EDITABLE:
                    return 400, {"error": f"khoi {section!r} khong sua duoc tren trang nay"}
                override = _read_json(GANN_CONFIG_FILE, {}) if GANN_CONFIG_FILE.exists() else {}
                if not isinstance(override, dict):
                    override = {}
                if action == "config_save":
                    override[section] = _gann_validate_section(section, payload.get("value"))
                else:
                    override.pop(section, None)
                if override:
                    _write_json(GANN_CONFIG_FILE, override)
                elif GANN_CONFIG_FILE.exists():
                    GANN_CONFIG_FILE.unlink()
                _gann_cfg_invalidate()
                return 200, {"ok": True, "config_version": _gann_config_version(),
                             "note": "Co hieu luc ngay o lan goi tool ke tiep."}

            if action in ("pivot_add", "pivot_delete"):
                raw_symbol = str(payload.get("symbol") or "").strip().upper()
                # Xoa thi khong doi cap con duoc theo doi: dong pivot cu cua mot
                # cap da go khoi he thong van phai xoa duoc
                sym = _resolve_symbol(raw_symbol) if action == "pivot_add" else raw_symbol
                if not sym:
                    return 400, {"error": "thieu symbol"}
                tf = str(payload.get("timeframe") or "").strip().lower()
                if tf not in GANN_TIMEFRAMES:
                    return 400, {"error": f"khung {tf!r} khong co pivot"}
                doc = _read_json(GANN_MANUAL_FILE, {}) if GANN_MANUAL_FILE.exists() else {}
                if not isinstance(doc, dict):
                    doc = {}
                rows = doc.setdefault(sym, {}).setdefault(tf, [])
                if action == "pivot_add":
                    op = str(payload.get("op") or "").strip().lower()
                    kind = str(payload.get("type") or "").strip().lower()
                    day = str(payload.get("date") or "").strip()
                    if op not in ("pin", "exclude") or kind not in ("high", "low"):
                        return 400, {"error": "op phai la pin/exclude, type phai la high/low"}
                    try:
                        date.fromisoformat(day)
                    except ValueError:
                        return 400, {"error": f"ngay {day!r} khong dung dang YYYY-MM-DD"}
                    entry = {"action": op, "type": kind, "date": day}
                    if op == "pin":
                        level = str(payload.get("level") or "major").strip().lower()
                        if level not in GANN_LEVELS:
                            return 400, {"error": f"bac {level!r} khong ton tai"}
                        entry["level"] = level
                    note = str(payload.get("note") or "").strip()[:200]
                    if note:
                        entry["note"] = note
                    # Cung (ngay, loai) thi thay the - khong de hai lenh mau thuan cung luc
                    rows[:] = [r for r in rows if not (isinstance(r, dict)
                               and r.get("date") == day and r.get("type") == kind)]
                    rows.append(entry)
                else:
                    index = int(payload.get("index", -1))
                    if not 0 <= index < len(rows):
                        return 400, {"error": "khong co dong pivot thu cong nay (trang da cu?)"}
                    rows.pop(index)
                    if not rows:
                        doc[sym].pop(tf, None)
                    if not doc[sym]:
                        doc.pop(sym, None)
                _write_json(GANN_MANUAL_FILE, doc)
                return 200, {"ok": True, "note": "Pivot tu tinh lai o lan goi ke tiep."}

            if action in ("event_add", "event_delete"):
                raw = _read_json(GANN_EVENTS_FILE, []) if GANN_EVENTS_FILE.exists() else []
                rows = raw.get("events") if isinstance(raw, dict) else raw
                rows = rows if isinstance(rows, list) else []
                if action == "event_add":
                    day = str(payload.get("date") or "").strip()
                    name = str(payload.get("name") or "").strip()[:80]
                    try:
                        date.fromisoformat(day)
                    except ValueError:
                        return 400, {"error": f"ngay {day!r} khong dung dang YYYY-MM-DD"}
                    if not name:
                        return 400, {"error": "su kien can co ten"}
                    weight = _gann_num(payload.get("weight", 1), "weight", high=100)
                    symbols = [str(s).strip().upper() for s in payload.get("symbols") or []
                               if str(s).strip()]
                    rows.append({"date": day, "name": name, "weight": weight,
                                 "symbols": symbols})
                    rows.sort(key=lambda r: str(r.get("date") or ""))
                else:
                    # Danh sach tren trang la ban da loc (bo dong hong) - xoa theo
                    # chinh danh sach do de chi so khop voi cai nguoi dung thay
                    valid, _ = _gann_events()
                    index = int(payload.get("index", -1))
                    if not 0 <= index < len(valid):
                        return 400, {"error": "khong co su kien nay (trang da cu?)"}
                    target = valid[index]
                    rows = [r for r in rows if r is not target and r != target]
                _write_json(GANN_EVENTS_FILE, rows)
                return 200, {"ok": True}

        if action == "symbol_toggle":
            return _gann_toggle(str(payload.get("symbol") or ""), payload.get("enabled"))

        if action == "recompute":
            sym = _gann_symbol(str(payload.get("symbol") or ""))
            doc = _gann_doc(sym, force=True)
            counts = {tf: block.get("live", 0)
                      for tf, block in (doc.get("timeframes") or {}).items()}
            return 200, {"ok": True, "pivots": counts,
                         "note": f"pivot 1w {counts.get('1w', 0)}, 1d {counts.get('1d', 0)}"}
        return 400, {"error": f"action khong ho tro: {action!r}"}
    except ValueError as exc:
        return 400, {"error": str(exc)}


@mcp.custom_route(ADMIN_GANN_PATH, methods=["GET"])
async def http_admin_gann(request):
    """TM - #GANN-TW - Gann Time Windows: trang quan ly pivot, su kien, config."""
    from starlette.responses import HTMLResponse
    if not GANN_ENABLED:
        return HTMLResponse(
            "<p>Gann time windows dang tat. Bat <code>time_windows.enabled</code> "
            "trong config.yaml roi restart.</p>",
            status_code=404, headers={"Cache-Control": "no-store"})
    view = await asyncio.to_thread(_gann_admin_view)
    page = admin_gann.render(view, action_path=ADMIN_GANN_ACTION_PATH,
                             admin_path=ADMIN_PATH, setup_required=not STORE.has_auth(),
                             nav=_admin_nav())
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@mcp.custom_route(ADMIN_GANN_ACTION_PATH, methods=["POST"])
async def http_admin_gann_action(request):
    """TM - #GANN-TW - Gann Time Windows: luu config / pivot thu cong / su kien."""
    if not GANN_ENABLED:
        return JSONResponse({"error": "Gann time windows dang tat"}, status_code=404)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "body khong phai JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body phai la object"}, status_code=400)
    if not _setup_ok(payload):
        return JSONResponse({"error": "Setup token sai"}, status_code=403)
    status, body = await asyncio.to_thread(_gann_admin_action, payload)
    return JSONResponse(body, status_code=status)


# TM - #M5-FETCH - Per-timeframe schedules + M5
ADMIN_SCHEDULES_PATH = ADMIN_PATH.rstrip("/") + "/schedules"
ADMIN_SCHEDULES_ACTION_PATH = ADMIN_SCHEDULES_PATH + "/action"


def _watcher_active() -> bool:
    # TM - #ENTRY-ALERT - Entry setups & alerts
    return bool(_alerts_cfg()["watcher"].get("enabled"))


def _run_time(ms: int | None) -> dict[str, str] | None:
    if not ms:
        return None
    return {"utc": datetime.fromtimestamp(ms / 1000, cron_utc.UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "local": datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S %Z")}


def _run_text(ms: int | None) -> str:
    when = _run_time(ms)
    return f"{when['utc']} UTC · {when['local']}" if when else ""


def _schedules_view() -> dict[str, Any]:
    now = int(time.time() * 1000)
    rows = []
    for item in SCHEDULES.list():
        due = FETCH_SCHEDULER.next_due.get(item["id"]) if FETCH_SCHEDULER.running else None
        if due is None and item["enabled"]:
            with contextlib.suppress(ValueError):
                due = cron_utc.next_after(cron_utc.parse(item["cron"]), now)
        rows.append({**item, "next_run_text": _run_text(due) if item["enabled"] else "tắt",
                     "last_run_text": _run_text(item["last_run_at"])})
    return {"schedules": rows, "supported": SUPPORTED_TIMEFRAMES,
            "symbols": [r["symbol"] for r in STORE.symbols(DEFAULT_SYMBOL)],
            "mode": FETCH_MODE, "running": FETCH_SCHEDULER.running,
            "reload_seconds": FETCH.get("reload_seconds", 30),
            "watcher_enabled": _watcher_active(), "storage": _kline_storage_view()}


# TM - #KL-SQL - Kline storage SQLite
def _kline_storage_view() -> dict[str, Any]:
    stores = {"json": {(c["symbol"], c["tf"]): c for c in KLINE_JSON.get_coverage()},
              "sqlite": {(c["symbol"], c["tf"]): c for c in _kline_sqlite().get_coverage()}}
    order = {tf: i for i, tf in enumerate(SUPPORTED_TIMEFRAMES)}
    keys = sorted(set(stores["json"]) | set(stores["sqlite"]),
                  key=lambda k: (k[0], order.get(k[1], len(order)), k[1]))
    rows = []
    for key in keys:
        row = {"symbol": key[0], "tf": key[1]}
        for name, cov in stores.items():
            item = cov.get(key)
            row[name] = ({"count": item["count"],
                          "last": (_run_time(item["last_open_time"]) or {}).get("local")}
                         if item else None)
        rows.append(row)
    return {"kind": KLINE_STORE.kind, "source": "settings.json" if STORE.kline_store() else "config.yaml",
            "sqlite_path": str(SQLITE_PATH), "retention_5m": _retention_for("5m"), "rows": rows}


async def _schedules_action(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "").strip().lower()
    try:
        if action == "preview":
            out = SCHEDULES.preview(str(payload.get("cron") or ""),
                                    str(payload.get("timeframe") or ""), 3)
            return 200, {**out, "next_runs": [_run_time(t) for t in out["next_runs"]]}
        if action in ("create", "update"):
            data = payload.get("schedule")
            if not isinstance(data, dict):
                raise ValueError("thieu schedule")
            item, warns = (SCHEDULES.create(data) if action == "create"
                           else SCHEDULES.update(str(payload.get("id") or ""), data))
            FETCH_SCHEDULER.wake()
            return 200, {"ok": True, "schedule": item, "warnings": warns}
        if action == "toggle":
            item = SCHEDULES.set_enabled(str(payload.get("id") or ""), bool(payload.get("enabled")))
            FETCH_SCHEDULER.wake()
            return 200, {"ok": True, "schedule": item}
        if action == "delete":
            item = SCHEDULES.delete(str(payload.get("id") or ""))
            FETCH_SCHEDULER.wake()
            return 200, {"ok": True, "deleted": item["name"]}
        if action == "run_now":
            item = SCHEDULES.get(str(payload.get("id") or ""))
            result = await FETCH_SCHEDULER.execute(item)
            return 200, {"ok": True, "result": result}
        # TM - #KL-SQL - Kline storage SQLite
        if action == "storage_switch":
            return 200, await _kline_switch(str(payload.get("kind") or ""))
        return 400, {"error": f"action khong ho tro: {action!r}"}
    except ValueError as exc:
        return 400, {"error": str(exc)}


@mcp.custom_route(ADMIN_SCHEDULES_PATH, methods=["GET"])
async def http_admin_schedules(request):
    from starlette.responses import HTMLResponse
    page = admin_schedules.render(_schedules_view(), action_path=ADMIN_SCHEDULES_ACTION_PATH,
                                  admin_path=ADMIN_PATH, setup_required=not STORE.has_auth(),
                                  nav=_admin_nav())
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@mcp.custom_route(ADMIN_SCHEDULES_ACTION_PATH, methods=["POST"])
async def http_admin_schedules_action(request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "body khong phai JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body phai la object"}, status_code=400)
    if not _setup_ok(payload):
        return JSONResponse({"error": "Setup token sai"}, status_code=403)
    status, body = await _schedules_action(payload)
    return JSONResponse(body, status_code=status)


# TM - #ENTRY-ALERT - Entry setups & alerts
ADMIN_ALERTS_PATH = ADMIN_PATH.rstrip("/") + "/alerts"
ADMIN_ALERTS_ACTION_PATH = ADMIN_ALERTS_PATH + "/action"


def _mask(value: Any) -> str:
    text = str(value or "")
    if not text:
        return "(chưa đặt)"
    return f"(đã đặt …{text[-4:]})" if len(text) > 8 else "(đã đặt)"


def _alerts_view(date_text: str, status: str) -> dict[str, Any]:
    cfg = _alerts_cfg()
    notify, watcher = cfg["notify"], cfg["watcher"]
    tracked = [r["symbol"] for r in STORE.symbols(DEFAULT_SYMBOL)]
    symbols = tracked + [s for s in watcher["watch_symbols"] if s not in tracked]
    rows = SETUPS.list(bias_date=date_text or (None if status else _today()), status=status or None)
    if not date_text and not status:
        seen = {r["id"]: r for r in rows}
        for row in SETUPS.active():
            seen.setdefault(row["id"], row)
        rows = sorted(seen.values(), key=lambda r: (r["bias_date"], r["code"]), reverse=True)
    last = SETUPS.last_events([r["id"] for r in rows])
    scan = dict(WATCHER.last_scan)
    if scan.get("at"):
        scan["at_text"] = _stamp(scan["at"])
    warnings = []
    if not WATCHER.running:
        warnings.append("Watcher chưa chạy (server chưa khởi động xong).")
    if "5m" not in {s["timeframe"] for s in SCHEDULES.list() if s["enabled"]} and watcher.get("enabled"):
        warnings.append("Không có schedule 5m nào đang bật — watcher sẽ không có nến mới.")
    return {
        "notify": {"channel": notify.get("channel") or "discord",
                   "display_timezone": notify.get("display_timezone") or str(TZ),
                   "webhook_mask": _mask((notify.get("discord") or {}).get("webhook_url")),
                   "token_mask": _mask((notify.get("telegram") or {}).get("bot_token")),
                   "chat_mask": _mask((notify.get("telegram") or {}).get("chat_id"))},
        "watcher": watcher, "symbols": symbols,
        "override_symbols": sorted(set(watcher["watch_symbols"]) | set(watcher["symbol_overrides"])),
        "param_keys": list(entry_logic.PARAM_KEYS), "statuses": list(entry_logic.STATUSES),
        "triggers": list(entry_logic.TRIGGERS), "date": date_text, "status": status,
        "today": _today(), "setups": [_setup_view(r, last.get(r["id"])) for r in rows],
        "last_scan": scan, "alerts_file": str(ALERTS_FILE), "warnings": warnings,
        # TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
        "off_plan": {**cfg["off_plan"], "account_enabled": _account_enabled(), "running": OP_WATCHER.running,
                     "kinds": list(off_plan_logic.NOTIFY_KINDS), "channels": list(notifier.CHANNELS),
                     "last_poll": {**OP_WATCHER.last_poll, "at_text": _stamp(OP_WATCHER.last_poll["at"])}
                     if OP_WATCHER.last_poll.get("at") else {}},
    }


def _alert_param(key: str, value: Any) -> Any:
    if key == "m15_filter":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "co")
        return bool(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} phai la so, nhan duoc {value!r}") from None
    if key in ("sma_period", "confirm_within", "entry_valid_candles"):
        if number < 1 or number != int(number):
            raise ValueError(f"{key} phai la so nguyen >= 1")
        return int(number)
    if number <= 0:
        raise ValueError(f"{key} phai > 0")
    return number


def _alert_params(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("tham so phai la object")
    unknown = sorted(set(raw) - set(entry_logic.PARAM_KEYS))
    if unknown:
        raise ValueError(f"tham so khong ho tro: {unknown}")
    return {k: _alert_param(k, v) for k, v in raw.items() if v is not None and v != ""}


# TM - #OFF-PLAN - Kiem tra khoi off_plan tu trang admin truoc khi ghi
def _off_plan_clean(payload: dict[str, Any]) -> dict[str, Any]:
    def whole(name: str, low: int, high: int) -> int:
        try:
            value = float(payload.get(name))
        except (TypeError, ValueError):
            raise ValueError(f"{name} phai la so, nhan duoc {payload.get(name)!r}") from None
        if not low <= value <= high or value != int(value):
            raise ValueError(f"{name} phai la so nguyen tu {low} den {high}")
        return int(value)

    channel = str(payload.get("channel") or "").strip().lower()
    if channel and channel not in notifier.CHANNELS:
        raise ValueError(f"channel phai de trong (dung kenh chung) hoac mot trong {list(notifier.CHANNELS)}")
    kinds = payload.get("notify_classifications")
    if not isinstance(kinds, list):
        raise ValueError("notify_classifications phai la list")
    unknown = sorted(set(map(str, kinds)) - set(off_plan_logic.NOTIFY_KINDS))
    if unknown:
        raise ValueError(f"notify_classifications khong ho tro: {unknown}")
    return {"enabled": payload.get("enabled") in (True, 1, "1", "true", "on"),
            "poll_seconds": whole("poll_seconds", 5, 300),
            "no_stop_grace_seconds": whole("no_stop_grace_seconds", 0, 3600),
            "revenge_window_minutes": whole("revenge_window_minutes", 0, 1440),
            "channel": channel,
            "notify_classifications": [k for k in off_plan_logic.NOTIFY_KINDS if k in set(map(str, kinds))]}


async def _alerts_action(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "").strip().lower()
    cfg = _alerts_cfg()
    try:
        if action == "notify_save":
            channel = str(payload.get("channel") or "").strip().lower()
            if channel not in notifier.CHANNELS:
                raise ValueError(f"channel phai la mot trong {list(notifier.CHANNELS)}")
            tz_name = str(payload.get("display_timezone") or "").strip() or str(TZ)
            try:
                ZoneInfo(tz_name)
            except Exception:
                raise ValueError(f"display_timezone khong hop le: {tz_name}") from None
            block = copy.deepcopy(cfg["notify"])
            block.update(channel=channel, display_timezone=tz_name)
            discord, telegram = dict(block.get("discord") or {}), dict(block.get("telegram") or {})
            if _optional(payload, "discord_webhook_url"):
                discord["webhook_url"] = _optional(payload, "discord_webhook_url")
            if _optional(payload, "telegram_bot_token"):
                telegram["bot_token"] = _optional(payload, "telegram_bot_token")
            if _optional(payload, "telegram_chat_id"):
                telegram["chat_id"] = _optional(payload, "telegram_chat_id")
            block.update(discord=discord, telegram=telegram)
            _alerts_save("notify", block)
            return 200, {"ok": True}
        if action == "notify_test":
            # target = discord / telegram: thu rieng mot kenh bat ke notify.channel dang chon
            target = str(payload.get("target") or cfg["notify"].get("channel") or "discord").strip().lower()
            if target not in notifier.CHANNELS:
                raise ValueError(f"target phai la mot trong {list(notifier.CHANNELS)}")
            alert = notifier.system_alert("TEST", {"at": int(time.time() * 1000), "channel": target},
                                          WATCHER.display_tz())
            # Khong retry: sai webhook / token thi bao ngay, khong treo nut ~40s
            error = await notifier.deliver({**cfg["notify"], "channel": target}, alert, backoff=())
            return 200, {"ok": not error, "error": error, "channel": target}
        if action == "watcher_save":
            block = copy.deepcopy(cfg["watcher"])
            deadline = str(payload.get("plan_deadline_utc") or "00:30").strip()
            try:
                hh, mm = (int(x) for x in deadline.split(":"))
                if not (0 <= hh < 24 and 0 <= mm < 60):
                    raise ValueError
            except ValueError:
                raise ValueError("plan_deadline_utc phai dang HH:MM") from None
            overrides = payload.get("symbol_overrides") or {}
            if not isinstance(overrides, dict):
                raise ValueError("symbol_overrides phai la object")
            block.update(
                enabled=bool(payload.get("enabled")), plan_deadline_utc=f"{hh:02d}:{mm:02d}",
                watch_symbols=sorted({str(s).strip().upper() for s in payload.get("watch_symbols") or []
                                      if str(s).strip()}),
                defaults={**ALERT_PARAM_DEFAULTS, **_alert_params(payload.get("defaults") or {})})
            # TM - #SETUP-PARAMS - Tham so theo cap ghi vao SQLite. Kiem tra het roi moi ghi
            pair_params = {str(k).upper(): _alert_params(v) for k, v in overrides.items()}
            block.pop("symbol_overrides", None)
            SETUPS.save_params(pair_params, "admin")
            _alerts_save("watcher", block)
            return 200, {"ok": True}
        # TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
        if action == "off_plan_save":
            _alerts_save("off_plan", _off_plan_clean(payload))
            return 200, {"ok": True}
        if action == "setup_cancel":
            reason = str(payload.get("reason") or "").strip()
            if not reason:
                raise ValueError("reason la bat buoc")
            # TM - #DASHBOARD - Setup dashboard
            return 200, _cancel_setup(str(payload.get("id") or ""), reason, "admin")
        if action == "setup_create":
            sym = _resolve_symbol(str(payload.get("symbol") or ""))
            return 200, _save_setups(sym, [payload.get("setup") or {}], False, "admin")
        if action == "setup_events":
            sid = SETUPS.get(str(payload.get("id") or ""))["id"]
            return 200, {"events": [_event_view(e) for e in SETUPS.events(setup_id=sid)]}
        return 400, {"error": f"action khong ho tro: {action!r}"}
    except ValueError as exc:
        return 400, {"error": str(exc)}


@mcp.custom_route(ADMIN_ALERTS_PATH, methods=["GET"])
async def http_admin_alerts(request):
    from starlette.responses import HTMLResponse
    date_text = str(request.query_params.get("date") or "").strip()
    status = str(request.query_params.get("status") or "").strip()
    page = admin_entry.render(_alerts_view(date_text, status), action_path=ADMIN_ALERTS_ACTION_PATH,
                              admin_path=ADMIN_PATH, schedules_path=ADMIN_SCHEDULES_PATH,
                              setup_required=not STORE.has_auth(), nav=_admin_nav())
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@mcp.custom_route(ADMIN_ALERTS_ACTION_PATH, methods=["POST"])
async def http_admin_alerts_action(request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "body khong phai JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body phai la object"}, status_code=400)
    if not _setup_ok(payload):
        return JSONResponse({"error": "Setup token sai"}, status_code=403)
    status, body = await _alerts_action(payload)
    return JSONResponse(body, status_code=status)


@mcp.custom_route(SRV.get("health_path", "/healthz"), methods=["GET"])
async def http_health(request):
    """Public, khong can auth. Dung cho Docker HEALTHCHECK va proxy."""
    return JSONResponse({"status": "ok", "time": _now_iso()})


# TM - #DASHBOARD - Setup dashboard: quan ly tai khoan tren trang admin (qua Basic auth nhu moi trang admin)
ADMIN_DASH_PATH = ADMIN_PATH.rstrip("/") + "/dashboard-accounts"
ADMIN_DASH_ACTION_PATH = ADMIN_DASH_PATH + "/action"


def _dash_accounts_view() -> dict[str, Any]:
    def when(ms: Any) -> str:
        return _stamp(int(ms)) if ms else ""

    return {
        "users": [{**u, "last_login": when(u["last_login_at"]), "created": when(u["created_at"])}
                  for u in DASH_AUTH.users()],
        "audit": [{**a, "when": when(a["at"])} for a in DASH_AUTH.audit_log()],
        "logins": [{**row, "when": when(row["at"])} for row in DASH_AUTH.login_log()],
        "config": _dash_cfg(), "timeframes": list(DASH_TFS), "dashboard_path": DASH_PATH,
        "config_file": str(DASH_FILE),
        # TM - #TR-BRANCH - Thang diem xac suat tich luy / phan phoi
        "prob_factors": [{"key": key, "label": label, "default": weight}
                         for key, (label, weight) in tr_logic.PROB_FACTORS.items()],
    }


def _dash_accounts_action(payload: dict[str, Any], ip: str | None) -> tuple[int, dict[str, Any]]:
    action = str(payload.get("action") or "").strip().lower()
    actor = STORE.username() or "setup-token"
    ref = str(payload.get("id") or "")
    try:
        if action == "create":
            generated = dashboard_auth.random_password() if payload.get("generate") else None
            user = DASH_AUTH.create_user(str(payload.get("username") or ""),
                                         generated or str(payload.get("password") or ""),
                                         str(payload.get("role") or "viewer"), actor=actor, ip=ip)
            return 200, {"ok": True, "username": user["username"], "generated_password": generated}
        if action == "set_role":
            DASH_AUTH.set_role(ref, str(payload.get("role") or ""), actor=actor, ip=ip)
            return 200, {"ok": True}
        if action == "reset_password":
            generated = dashboard_auth.random_password() if payload.get("generate") else None
            DASH_AUTH.reset_password(ref, generated or str(payload.get("password") or ""), actor=actor, ip=ip)
            return 200, {"ok": True, "generated_password": generated}
        if action == "set_active":
            DASH_AUTH.set_active(ref, bool(payload.get("active")), actor=actor, ip=ip)
            return 200, {"ok": True}
        if action == "delete":
            DASH_AUTH.delete_user(ref, actor=actor, ip=ip)
            return 200, {"ok": True}
        if action == "config_save":
            clean = _dash_clean_cfg(payload.get("config"))
            before = _dash_cfg()
            _write_json(DASH_FILE, clean)
            _DASH_CFG_CACHE.update(key=None, cfg=None)
            DASH_AUTH.audit(actor, "config_save", "dashboard",
                            {k: clean[k] for k in clean if clean[k] != before.get(k)}, ip)
            return 200, {"ok": True}
        return 400, {"error": f"action khong ho tro: {action!r}"}
    except ValueError as exc:
        return 400, {"error": str(exc)}


@mcp.custom_route(ADMIN_DASH_PATH, methods=["GET"])
async def http_admin_dashboard(request):
    from starlette.responses import HTMLResponse
    page = admin_dashboard.render(_dash_accounts_view(), action_path=ADMIN_DASH_ACTION_PATH,
                                  admin_path=ADMIN_PATH, setup_required=not STORE.has_auth(), nav=_admin_nav())
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@mcp.custom_route(ADMIN_DASH_ACTION_PATH, methods=["POST"])
async def http_admin_dashboard_action(request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "body khong phai JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body phai la object"}, status_code=400)
    if not _setup_ok(payload):
        return JSONResponse({"error": "Setup token sai"}, status_code=403)
    # Hash argon2 ton CPU, khong chay tren event loop
    status, body = await asyncio.to_thread(_dash_accounts_action, payload, _dash_ip(request))
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- setup dashboard: HTTP
# TM - #DASHBOARD - Setup dashboard
# Duong nay khong qua Basic auth (xem public_prefixes o build_app): dashboard co dang nhap rieng,
# nen phien admin khong vao duoc day va cookie o day cung khong mo duoc trang admin.

DASH_STATIC = Path(__file__).parent / "static"
DASH_ASSETS = {
    "dashboard.js": ("dashboard/dashboard.js", "text/javascript; charset=utf-8"),
    "dashboard.css": ("dashboard/dashboard.css", "text/css; charset=utf-8"),
    "lightweight-charts.js": ("vendor/lightweight-charts/lightweight-charts.standalone.production.js",
                              "text/javascript; charset=utf-8"),
}
DASH_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; "
                                "form-action 'self'; frame-ancestors 'none'"),
}
DASH_LOGIN_BODY_MAX = 8192
_DASH_ASSET_CACHE: dict[str, tuple[int, str, bytes, bytes]] = {}


def _dash_ip(request) -> str:
    return request.client.host if request.client else "unknown"


def _dash_secure(response):
    response.headers.update(DASH_HEADERS)
    return response


def _dash_json(body: Any, status: int = 200):
    return _dash_secure(JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"}))


def _dash_life_ms(remember: Any) -> int:
    cfg = _dash_cfg()
    return (int(cfg["remember_days"]) * 86_400_000 if remember
            else int(cfg["session_hours"]) * 3_600_000)


def _dash_set_cookie(response, token: str, life_ms: int) -> None:
    response.set_cookie(DASH_COOKIE, token, max_age=life_ms // 1000, path=DASH_PATH, secure=True,
                        httponly=True, samesite="lax")


def _dash_login_url(target: str = "") -> str:
    url = f"{DASH_PATH}/login"
    return f"{url}?next={quote(target, safe='')}" if target else url


def _dash_guard(*, page: bool = False, owner: bool = False):
    """Dang ky route dashboard: 404 khi tat, kiem tra phien, quyen va CSRF ngay phia server."""
    def decorator(fn):
        async def endpoint(request):
            if not _dash_cfg()["enabled"]:
                return _dash_json({"error": "not found"}, 404)
            token = request.cookies.get(DASH_COOKIE) or ""
            session = DASH_AUTH.session(token)
            if session is None:
                if not page:
                    return _dash_json({"error": "unauthorized"}, 401)
                from starlette.responses import RedirectResponse
                target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
                return _dash_secure(RedirectResponse(_dash_login_url(target), status_code=302))
            if owner and session["role"] != "owner":
                return _dash_json({"error": "forbidden"}, 403)
            if request.method == "POST" and not secrets.compare_digest(
                    request.headers.get("x-csrf-token") or "", session["csrf_token"]):
                return _dash_json({"error": "csrf"}, 403)
            try:
                response = await fn(request, session)
            except (ValueError, FileNotFoundError) as exc:
                response = _dash_json({"error": str(exc)}, 400)
            life = DASH_AUTH.touch(session, _dash_life_ms(session["remember"]))
            if life:
                _dash_set_cookie(response, token, life)
            return _dash_secure(response)
        endpoint.__name__ = fn.__name__
        return endpoint
    return decorator


def _dash_route(path: str, methods: tuple[str, ...] = ("GET",), *, page: bool = False, owner: bool = False):
    def decorator(fn):
        mcp.custom_route(DASH_PATH + path, methods=list(methods))(_dash_guard(page=page, owner=owner)(fn))
        return fn
    return decorator


def _dash_asset(name: str) -> tuple[int, str, bytes, bytes] | None:
    item = DASH_ASSETS.get(name)
    if item is None:
        return None
    path = DASH_STATIC / item[0]
    mtime = path.stat().st_mtime_ns
    hit = _DASH_ASSET_CACHE.get(name)
    if hit is not None and hit[0] == mtime:
        return hit
    raw = path.read_bytes()
    entry = (mtime, hashlib.sha256(raw).hexdigest()[:12], raw, gzip.compress(raw))
    _DASH_ASSET_CACHE[name] = entry
    return entry


def _dash_asset_url(name: str) -> str:
    return f"{DASH_PATH}/static/{name}?v={_dash_asset(name)[1]}"


def _dash_symbol(request) -> str:
    return _resolve_symbol(str(request.query_params.get("symbol") or ""))


def _dash_for_role(session: dict[str, Any], setup: dict[str, Any]) -> dict[str, Any]:
    """Viewer khong thay vi the: gia khop lay tu san, lenh OKX trong review, va moi ghi chu,
    vi ghi chu la chu tu do, thuc te co nhac vi the that (vd note setup A ngay 07/11)."""
    if session["role"] == "owner":
        return setup
    out = {k: v for k, v in setup.items() if k != "note"}
    last = out.get("last_event")
    if last and last["event"] == "FILLED":
        out["last_event"] = {**last, "price": None}
    if out.get("review"):
        out["review"] = {k: v for k, v in out["review"].items() if k not in ("exchange_fill_ids", "note")}
    return out


# TM - #DASH-LAYOUT - Bố cục dashboard & full page: trang chinh va full page dung chung mot bo boot
def _dash_boot(session: dict[str, Any], view: str) -> dict[str, Any]:
    cfg = _dash_cfg()
    return {"base": DASH_PATH, "view": view, "role": session["role"], "username": session["username"],
            "csrf": session["csrf_token"], "tz": str(TZ), "today": _today(),
            "poll_seconds": cfg["poll_seconds"], "default_tf": cfg["default_tf"],
            "default_bars": cfg["default_bars"], "default_symbol": _resolve_symbol(""), "symbols": _symbols(),
            "timeframes": list(DASH_TFS), "outcomes": list(dashboard_logic.OUTCOMES),
            "group_by": list(dashboard_logic.GROUP_BY),
            # TM - #TR-BRANCH - Nhan cua factor trong danh sach bang chung
            "prob_factors": {key: label for key, (label, _) in tr_logic.PROB_FACTORS.items()}}


@_dash_route("", page=True)
async def http_dash_page(request, session):
    from starlette.responses import HTMLResponse
    page = dashboard_ui.app_page(_dash_boot(session, "main"), {name: _dash_asset_url(name) for name in DASH_ASSETS})
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


# TM - #DASH-LAYOUT - Bố cục dashboard & full page: chart chiem ca cua so, mo o tab rieng. Chung cookie va
# guard voi trang chinh: chua dang nhap thi ve form login voi next = URL day du query, viewer mo duoc.
@_dash_route("/full", page=True)
async def http_dash_full(request, session):
    from starlette.responses import HTMLResponse
    page = dashboard_ui.full_page(_dash_boot(session, "full"), {name: _dash_asset_url(name) for name in DASH_ASSETS})
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@_dash_route("/static/{name}")
async def http_dash_static(request, session):
    from starlette.responses import Response
    asset = _dash_asset(request.path_params["name"])
    if asset is None:
        return _dash_json({"error": "not found"}, 404)
    headers = {"Cache-Control": "private, max-age=604800, immutable", "ETag": f'"{asset[1]}"',
               "Vary": "Accept-Encoding"}
    if "gzip" in (request.headers.get("accept-encoding") or ""):
        headers["Content-Encoding"] = "gzip"
        body = asset[3]
    else:
        body = asset[2]
    return Response(body, media_type=DASH_ASSETS[request.path_params["name"]][1], headers=headers)


@_dash_route("/api/symbols")
async def http_dash_symbols(request, session):
    return _dash_json({"symbols": _symbols(), "default": _resolve_symbol("")})


# TM - #DASH-LAYOUT - Bố cục dashboard & full page
def _dash_pick_tr(data: dict[str, Any], tr_id: Any) -> dict[str, Any]:
    """O chon TR: ve TR khac TR cua ke hoach ngay. Chi thay khoi tr, marker Wyckoff va lich su xac suat;
    nen, setup, kich ban giu nguyen. Lam tren ban sao: data["markers"] co the dang nam trong cache chart."""
    tr_id = str(tr_id or "").strip()
    if not tr_id or (data.get("tr") or {}).get("id") == tr_id:
        return data
    tr = _tr_target(data["symbol"], tr_id)
    opens = [int(c[0]) for c in data["candles"]]
    markers = [m for m in data["markers"] if m.get("kind") != "wyckoff"]
    markers += tr_logic.markers(tr, opens, _tf_ms(data["timeframe"]))
    start_ms, end_ms = _day_window_ms(data["date"])
    return {**data, "tr": tr, "markers": sorted(markers, key=lambda m: m["t"]),
            "prob_history": PLANS.prob_history(tr["id"], start_ms, end_ms)}


@_dash_route("/api/trs")
async def http_dash_trs(request, session):
    """TR cua mot cap, moi nhat truoc. TR la thong tin moi tai khoan deu xem duoc."""
    symbol = _dash_symbol(request)
    rows = PLANS.tr_list(symbol)
    active = next((r["id"] for r in rows if not r["superseded_by"]), None)
    return _dash_json({"symbol": symbol, "active_id": active, "trs": rows})


@_dash_route("/api/chart")
async def http_dash_chart(request, session):
    query = request.query_params
    owner = session["role"] == "owner"
    data = await _chart_data(_dash_symbol(request), query.get("tf") or _dash_cfg()["default_tf"],
                             query.get("bars") or _dash_cfg()["default_bars"], query.get("date"),
                             query.get("include_forming") in ("1", "true"), query.get("revision"),
                             positions=owner)
    data = _dash_pick_tr(data, query.get("tr_id"))  # TM - #DASH-LAYOUT
    if not owner:
        data["setups"] = [_dash_for_role(session, s) for s in data["setups"]]
        data["scenarios"] = [{**s, "points": [{k: v for k, v in p.items() if k != "note"} for p in s["points"]]}
                             for s in data["scenarios"]]
    return _dash_json(data)


@_dash_route("/api/setups")
async def http_dash_setups(request, session):
    symbol, day = _dash_symbol(request), _dash_day(request.query_params.get("date"))
    rows = SETUPS.list(symbol=symbol, bias_date=day)
    reviews = _review_sync(rows)
    last = SETUPS.last_events([r["id"] for r in rows])
    setups = [_dash_for_role(session, {**_dash_setup(r, last.get(r["id"])),
                                       "review": _review_view(reviews.get(r["id"]))}) for r in rows]
    return _dash_json({"symbol": symbol, "date": day, "watcher_enabled": _watcher_active(),
                       "price": _last_price(symbol), "setups": setups})


@_dash_route("/api/events")
async def http_dash_events(request, session):
    symbol, day = _dash_symbol(request), _dash_day(request.query_params.get("date"))
    start, end = _day_window_ms(day)
    rows = [_dash_event(e) for e in SETUPS.events(symbol=symbol, start_ms=start, end_ms=end)]
    if session["role"] != "owner":
        rows = [{**e, "price": None if e["event"] == "FILLED" else e["price"]}
                for e in rows if e["event"] != "UNPLANNED_POSITION"]
    return _dash_json({"symbol": symbol, "date": day, "events": rows[::-1]})


@_dash_route("/api/snapshots")
async def http_dash_snapshots(request, session):
    symbol, day = _dash_symbol(request), _dash_day(request.query_params.get("date"))
    return _dash_json({"symbol": symbol, "date": day, "snapshots": PLANS.snapshots(symbol, day)})


@_dash_route("/api/positions", owner=True)
async def http_dash_positions(request, session):
    symbol = _dash_symbol(request)
    return _dash_json({"symbol": symbol, **await _dash_positions(symbol)})


@_dash_route("/api/fills", owner=True)
async def http_dash_fills(request, session):
    symbol = _dash_symbol(request)
    return _dash_json({"symbol": symbol, **await _dash_fills(symbol, _dash_day(request.query_params.get("date")))})


# TM - #OFF-PLAN - Bang lenh ngoai ke hoach theo ngay (moi cap). Chi chu tai khoan: lo vi the va PnL.
@_dash_route("/api/off-plan", owner=True)
async def http_dash_off_plan(request, session):
    day = _dash_day(request.query_params.get("date"))
    hide = _dash_cfg()["hide_pnl"]
    rows = []
    for row in OP_STORE.list(from_date=day, to_date=day, coded_only=True):
        item = {"code": row["code"], "symbol": row["symbol"], "side": row["side"],
                "classification": row["classification"], "flags": row["flags"], "opened_ms": int(row["opened_at"]),
                "closed": bool(row.get("closed_at")), "entry": (row.get("explain") or {}).get("entry", row["entry_avg"]),
                "review_status": row["review_status"], "verdict": row.get("verdict"),
                "reason": (row.get("explain") or {}).get("reason")}
        if not hide:
            item.update(realized_pnl=row.get("realized_pnl"), fee=row.get("fee"), r_multiple=row.get("r_multiple"))
        rows.append(item)
    return _dash_json({"date": day, "enabled": _op_active(), "trades": rows})


@_dash_route("/api/reviews", methods=("POST",), owner=True)
async def http_dash_reviews(request, session):
    try:
        payload = await request.json()
    except Exception:
        raise ValueError("body khong phai JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("body phai la object")
    return _dash_json(await _save_review(payload.get("setup_code"), payload.get("outcome"),
                                         payload.get("r_multiple"), payload.get("note"), session["username"]))


@_dash_route("/api/review-stats")
async def http_dash_review_stats(request, session):
    query = request.query_params
    data = _review_stats(query.get("symbol") or "", query.get("from") or "", query.get("to") or "",
                         query.get("group_by") or "trigger_type")
    # TM - #OFF-PLAN - plan_compare la vi the va PnL that: viewer khong duoc thay, hide_pnl thi bo so tien
    if session["role"] != "owner":
        data.pop("plan_compare", None)
    elif _dash_cfg()["hide_pnl"]:
        for block in [data["plan_compare"]["in_plan"], data["plan_compare"]["off_plan"],
                      *data["plan_compare"]["by_classification"], *data["plan_compare"]["by_verdict"]]:
            block.pop("pnl", None)
            block.pop("fee", None)
    return _dash_json(data)


# TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
@_dash_route("/api/tr-changes")
async def http_dash_tr_changes(request, session):
    """Bo loc "thay doi TR trong ngay" cua review. Pha, event, the TR moi tai khoan deu xem duoc."""
    symbol, day = _dash_symbol(request), _dash_day(request.query_params.get("date"))
    return _dash_json(_tr_changes(symbol, day))


@mcp.custom_route(DASH_PATH + "/login", methods=["GET", "POST"])
async def http_dash_login(request):
    from starlette.responses import HTMLResponse, RedirectResponse
    cfg = _dash_cfg()
    if not cfg["enabled"]:
        return _dash_json({"error": "not found"}, 404)

    def form_page(status: int = 200, error: str = "", next_url: str = "", username: str = ""):
        return _dash_secure(HTMLResponse(
            dashboard_ui.login_page(action=f"{DASH_PATH}/login", next_url=next_url, error=error,
                                    username=username, remember_days=int(cfg["remember_days"])),
            status_code=status, headers={"Cache-Control": "no-store"}))

    token = request.cookies.get(DASH_COOKIE) or ""
    if request.method == "GET":
        target = dashboard_logic.safe_next(request.query_params.get("next"), DASH_PATH)
        if DASH_AUTH.session(token):
            return _dash_secure(RedirectResponse(target, status_code=302))
        return form_page(next_url=target)

    body = await request.body()
    if len(body) > DASH_LOGIN_BODY_MAX:
        return _dash_json({"error": "body qua lon"}, 413)
    form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)

    def field(name: str) -> str:
        return (form.get(name) or [""])[0]

    target = dashboard_logic.safe_next(field("next"), DASH_PATH)
    username, ip = field("username").strip(), _dash_ip(request)
    if DASH_AUTH.locked_until(ip):
        DASH_AUTH.log_login(username, ip, False, "locked")
        return form_page(429, f"Tạm khoá đăng nhập từ địa chỉ này do sai quá nhiều lần "
                              f"(tối đa {cfg['login_lockout_minutes']} phút).", target, username)
    user = await asyncio.to_thread(DASH_AUTH.authenticate, username, field("password"))
    if user is None:
        locked = DASH_AUTH.record_failure(ip, int(cfg["login_max_attempts"]), float(cfg["login_lockout_minutes"]))
        DASH_AUTH.log_login(username, ip, False, "bad_credentials")
        if locked:
            return form_page(429, f"Sai quá {cfg['login_max_attempts']} lần, tạm khoá "
                                  f"{cfg['login_lockout_minutes']} phút.", target, username)
        return form_page(401, "Sai tên đăng nhập hoặc mật khẩu.", target, username)
    DASH_AUTH.clear_failures(ip)
    if token:
        DASH_AUTH.destroy(token)
    remember = bool(field("remember"))
    life = _dash_life_ms(remember)
    new_token, _ = DASH_AUTH.create_session(user, ip=ip, user_agent=request.headers.get("user-agent"),
                                            remember=remember, life_ms=life)
    DASH_AUTH.log_login(user["username"], ip, True)
    response = RedirectResponse(target, status_code=303)
    _dash_set_cookie(response, new_token, life)
    return _dash_secure(response)


@mcp.custom_route(DASH_PATH + "/logout", methods=["POST"])
async def http_dash_logout(request):
    from starlette.responses import RedirectResponse
    if not _dash_cfg()["enabled"]:
        return _dash_json({"error": "not found"}, 404)
    token = request.cookies.get(DASH_COOKIE) or ""
    session = DASH_AUTH.session(token)
    if session is not None:
        body = await request.body()
        form = parse_qs(body[:DASH_LOGIN_BODY_MAX].decode("utf-8", "replace"))
        if not secrets.compare_digest((form.get("csrf") or [""])[0], session["csrf_token"]):
            return _dash_json({"error": "csrf"}, 403)
        DASH_AUTH.destroy(token)
    response = RedirectResponse(_dash_login_url(), status_code=303)
    response.delete_cookie(DASH_COOKIE, path=DASH_PATH, secure=True, httponly=True, samesite="lax")
    return _dash_secure(response)


# ---------------------------------------------------------------- admin: dang nhap bang form
# TM - #ADMIN-LOGIN - Form dang nhap admin
# Chi /admin dung phien cookie; /mcp van la Basic auth. Hai ben chung mot mat khau trong settings.json.

ADMIN_COOKIE = "bamcp_admin"
ADMIN_COOKIE_PATH = ADMIN_PATH.rstrip("/")
ADMIN_SESSIONS = admin_auth.AdminSessions(SQLITE_PATH)


def _admin_gate(scope):
    """Cua vao cac trang admin. None la cho qua, con lai la response tra ngay cho trinh duyet."""
    from starlette.requests import Request
    from starlette.responses import RedirectResponse
    request = Request(scope)
    read_only = request.method in ("GET", "HEAD")
    if not ADMIN_SESSIONS.valid(request.cookies.get(ADMIN_COOKIE) or "", STORE.auth_fingerprint()):
        if read_only:
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return _dash_secure(RedirectResponse(f"{ADMIN_LOGIN_PATH}?next={quote(target, safe='')}", status_code=302))
        # Khong kem WWW-Authenticate de trinh duyet khong bat popup. Cac trang admin hien thang truong error.
        return JSONResponse({"error": "Phiên đăng nhập đã hết hạn. Tải lại trang để đăng nhập lại."}, status_code=401)
    # Cookie tu gui kem theo request nen phai chan trang la gui ngam. Form cross-site khong dat duoc
    # Content-Type nay ma khong qua preflight. So ca gia tri, vi "text/plain; x=application/json" van la form thuong.
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not read_only and content_type != "application/json":
        return JSONResponse({"error": "can Content-Type application/json"}, status_code=415)
    return None


@mcp.custom_route(ADMIN_LOGIN_PATH, methods=["GET", "POST"])
async def http_admin_login(request):
    from starlette.responses import HTMLResponse, RedirectResponse
    # Chua co mat khau hoac da tat auth thi khong co gi de dang nhap
    if not (AUTH_ENABLED and STORE.has_auth()):
        return RedirectResponse(ADMIN_PATH, status_code=302)
    max_attempts = int(AUTH.get("login_max_attempts", 5))
    lockout_minutes = float(AUTH.get("login_lockout_minutes", 15))
    remember_days = int(AUTH.get("remember_days", 30))

    def form_page(status: int = 200, error: str = "", next_url: str = "", username: str = ""):
        page = dashboard_ui.login_page(action=ADMIN_LOGIN_PATH, next_url=next_url, error=error, username=username,
                                       remember_days=remember_days, heading="Admin",
                                       note="Đăng nhập bằng tài khoản quản trị BAMCP.")
        return _dash_secure(HTMLResponse(page, status_code=status, headers={"Cache-Control": "no-store"}))

    token = request.cookies.get(ADMIN_COOKIE) or ""
    fingerprint = STORE.auth_fingerprint()
    if request.method != "POST":
        target = dashboard_logic.safe_next(request.query_params.get("next"), ADMIN_PATH)
        if ADMIN_SESSIONS.valid(token, fingerprint):
            return _dash_secure(RedirectResponse(target, status_code=302))
        return form_page(next_url=target)

    body = await request.body()
    if len(body) > DASH_LOGIN_BODY_MAX:
        return JSONResponse({"error": "body qua lon"}, status_code=413)
    form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)

    def field(name: str) -> str:
        return (form.get(name) or [""])[0]

    target = dashboard_logic.safe_next(field("next"), ADMIN_PATH)
    username = field("username").strip()
    # Tien to rieng: sai ben nay khong khoa ben dashboard, dang nhap dung ben kia cung khong xoa so lan sai ben nay.
    guard_key = f"admin:{_dash_ip(request)}"
    locked_message = f"Sai quá nhiều lần, tạm khoá đăng nhập từ địa chỉ này tối đa {lockout_minutes:g} phút."
    if DASH_AUTH.locked_until(guard_key):
        return form_page(429, locked_message, target, username)
    # PBKDF2 ton CPU, khong chay tren event loop
    if not await asyncio.to_thread(STORE.check_credentials, username.encode("utf-8"), field("password").encode("utf-8")):
        if DASH_AUTH.record_failure(guard_key, max_attempts, lockout_minutes):
            return form_page(429, locked_message, target, username)
        return form_page(401, "Sai tên đăng nhập hoặc mật khẩu.", target, username)
    DASH_AUTH.clear_failures(guard_key)
    if token:
        ADMIN_SESSIONS.destroy(token)
    life_ms = (remember_days * 86_400_000 if field("remember")
               else int(AUTH.get("session_hours", 12)) * 3_600_000)
    response = RedirectResponse(target, status_code=303)
    # Secure theo scheme thuc te (sau proxy thi lay tu X-Forwarded-Proto): chay http://127.0.0.1 ma ep Secure
    # thi co trinh duyet bo cookie, dang nhap dung van bi day ve form.
    response.set_cookie(ADMIN_COOKIE, ADMIN_SESSIONS.create(fingerprint, life_ms), max_age=life_ms // 1000,
                        path=ADMIN_COOKIE_PATH, secure=request.url.scheme == "https", httponly=True, samesite="lax")
    return _dash_secure(response)


@mcp.custom_route(ADMIN_LOGOUT_PATH, methods=["POST"])
async def http_admin_logout(request):
    from starlette.responses import RedirectResponse
    response = RedirectResponse(ADMIN_LOGIN_PATH, status_code=303)
    # Khong co cookie thi khong lam gi: form cross-site khong gui kem cookie SameSite=Lax,
    # nen trang la khong dang xuat ho duoc.
    token = request.cookies.get(ADMIN_COOKIE)
    if token:
        ADMIN_SESSIONS.destroy(token)
        response.delete_cookie(ADMIN_COOKIE, path=ADMIN_COOKIE_PATH, secure=request.url.scheme == "https",
                               httponly=True, samesite="lax")
    return _dash_secure(response)


# ---------------------------------------------------------------- auth

class BasicAuthMiddleware:
    """HTTP Basic auth, viet duoi dang pure ASGI middleware. Rieng /admin di qua session_gate.

    Khong dung BaseHTTPMiddleware vi transport streamable-http cua MCP dua tren SSE;
    BaseHTTPMiddleware boc response vao mot anyio task group va lam hong stream dai.
    """

    def __init__(self, app, store, realm: str,
                 public_paths: tuple[str, ...] = (),
                 setup_paths: tuple[str, ...] = (),
                 public_prefixes: tuple[str, ...] = (),
                 session_prefix: str = "",
                 session_gate=None):
        self.app = app
        self.store = store
        self.realm = realm
        self.public_paths = public_paths
        # TM - #DASHBOARD - Setup dashboard: nhanh co dang nhap rieng, tu kiem tra phien
        self.public_prefixes = public_prefixes
        # Duong chi mo khi CHUA co mat khau nao - de con vao dat mat khau lan dau.
        # Ban than chung van doi setup token moi ghi duoc gi.
        self.setup_paths = setup_paths
        # TM - #ADMIN-LOGIN - Form dang nhap admin: nhanh nay khong nhan Basic. Neu nhan thi trinh
        # duyet da luu credential tu popup cu se vao thang, khong thay form va khong dang xuat duoc.
        self.session_prefix = session_prefix
        self.session_gate = session_gate

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

    def _own_login(self, path: str) -> bool:
        return ".." not in path and any(path == p or path.startswith(p + "/") for p in self.public_prefixes)

    # TM - #ADMIN-LOGIN - Form dang nhap admin
    def _uses_session(self, path: str) -> bool:
        prefix = self.session_prefix
        return bool(prefix) and ".." not in path and (path == prefix or path.startswith(prefix + "/"))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.public_paths or self._own_login(scope["path"]):
            await self.app(scope, receive, send)
            return
        # Chua cai dat gi: cho vao trang admin, moi duong khac van dong
        if not self.store.has_auth() and scope["path"] in self.setup_paths:
            await self.app(scope, receive, send)
            return

        # TM - #ADMIN-LOGIN - Form dang nhap admin
        if self._uses_session(scope["path"]):
            response = self.session_gate(scope)
            if response is None:
                await self.app(scope, receive, send)
            else:
                await response(scope, receive, send)
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
            # TM - #ADMIN-LOGIN - Form dang nhap admin
            public_paths=(SRV["health_path"], ADMIN_LOGIN_PATH, ADMIN_LOGOUT_PATH),
            session_prefix=ADMIN_COOKIE_PATH,
            session_gate=_admin_gate,
            setup_paths=(ADMIN_PATH, ADMIN_SAVE_PATH, ADMIN_TEST_PATH, ADMIN_SYMBOL_PATH,
                         # TM - #ORB - ORB Enhancement
                         ADMIN_ORB_PATH, ADMIN_ORB_ACTION_PATH,
                         # TM - #ORB-RULES - ORB Rule Set
                         ADMIN_ORB_RULES_PATH,
                         # TM - #GANN-TW - Gann Time Windows
                         ADMIN_GANN_PATH, ADMIN_GANN_ACTION_PATH,
                         # TM - #M5-FETCH - Per-timeframe schedules + M5
                         ADMIN_SCHEDULES_PATH, ADMIN_SCHEDULES_ACTION_PATH,
                         # TM - #ENTRY-ALERT - Entry setups & alerts
                         ADMIN_ALERTS_PATH, ADMIN_ALERTS_ACTION_PATH,
                         # TM - #DASHBOARD - Setup dashboard
                         ADMIN_DASH_PATH, ADMIN_DASH_ACTION_PATH),
            # TM - #DASHBOARD - Setup dashboard
            public_prefixes=(DASH_PATH,),
        )

    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(scope):
        # TM - #M5-FETCH - Per-timeframe schedules + M5
        if FETCH.get("seed_defaults", True):
            seeded = SCHEDULES.seed_defaults()
            if seeded:
                print(f"BAMCP: da tao {seeded} fetch schedule mac dinh", file=sys.stderr)
        # TM - #SETUP-PARAMS - Ghi tham so setup truoc khi watcher chay
        added = _seed_setup_params()
        if added:
            print(f"BAMCP: da luu setup_params vao SQLite cho {', '.join(added)}", file=sys.stderr)
        # TM - #WYCKOFF-TR - Chuyen event json cu cua TR sang tr_events (can nen de tinh VSA)
        try:
            moved = _migrate_tr_events()
            if moved:
                print(f"BAMCP: da chuyen {moved} event TR sang tr_events", file=sys.stderr)
        except Exception as exc:
            print(f"CANH BAO: chuyen event TR loi ({type(exc).__name__}: {exc})", file=sys.stderr)
        task = (asyncio.create_task(FETCH_SCHEDULER.run_forever() if FETCH_MODE == "schedules"
                                    else _fetcher_loop()) if FETCH.get("enabled") else None)
        # TM - #ORB - ORB Enhancement: scheduler ORB song cung vong doi app nhu fetcher
        orb_task = (asyncio.create_task(ORB_SCHEDULER.run_forever())
                    if ORB_SCHEDULER is not None else None)
        # TM - #ENTRY-ALERT - Entry setups & alerts
        watch_task = asyncio.create_task(WATCHER.run_forever())
        # TM - #OFF-PLAN - position_watcher, chay doc lap voi watcher setup M5
        op_task = asyncio.create_task(OP_WATCHER.run_forever())
        try:
            async with inner(scope):
                yield
        finally:
            for running in (task, orb_task, watch_task, op_task):
                if running:
                    running.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await running

    app.router.lifespan_context = lifespan
    return app


if __name__ == "__main__":
    for directory in (KLINES_DIR, BIAS_DIR, JOURNAL_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    # TM - #ORB - ORB Enhancement
    if ORB_ENABLED:
        for directory in (ORB_STATE_DIR, ORB_LOG_DIR, ORB_SKIPS_DIR, ORB_HISTORY_DIR,
                          ORB_BACKTEST_DIR, ORB_SESSIONS_FILE.parent):
            directory.mkdir(parents=True, exist_ok=True)
    _migrate_flat_layout()

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
    # TM - #KL-SQL - Kline storage SQLite
    print(f"BAMCP kline_store={KLINE_STORE.kind} sqlite_path={SQLITE_PATH}", file=sys.stderr)
    # TM - #M5-FETCH - Per-timeframe schedules + M5
    print(f"BAMCP fetcher mode={FETCH_MODE} admin={ADMIN_SCHEDULES_PATH}", file=sys.stderr)
    # TM - #ENTRY-ALERT - Entry setups & alerts
    print(f"BAMCP watcher enabled={_watcher_active()} channel={_alerts_cfg()['notify'].get('channel')} "
          f"admin={ADMIN_ALERTS_PATH}", file=sys.stderr)
    # TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
    print(f"BAMCP off-plan enabled={_op_active()} poll={_op_cfg().get('poll_seconds')}s", file=sys.stderr)
    # TM - #ORB - ORB Enhancement
    if ORB_ENABLED:
        print(f"BAMCP ORB: symbol={ORB_SYMBOL} sessions={ORB_SESSIONS.ids()} "
              f"admin={ADMIN_ORB_PATH} m5_history={'co' if ORB_HISTORY.files(ORB_SYMBOL) else 'chua tai'}",
              file=sys.stderr)
        # TM - #ORB-RULES - ORB Rule Set: field yaml cu bi bo qua (BR-ORB-18)
        for warning in orb_rules.deprecated_fields(ORB_CFG):
            print(f"CANH BAO: {warning}", file=sys.stderr)
            ORB_LOG("deprecated", warning=warning)
    else:
        print("BAMCP ORB: tat (orb.enabled = false)", file=sys.stderr)

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
