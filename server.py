"""BAMCP - Binance Analysis MCP server."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import csv
import functools
import hashlib
import inspect
import json
import os
import secrets
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2 as httpx
import uvicorn
import yaml

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

import admin       # module cuc bo: trang cai dat
import admin_gann  # TM - #GANN-TW - Gann Time Windows: trang admin Gann
import exchanges   # module cuc bo: adapter doc tai khoan san
import gann_backtest  # TM - #GANN-TW - Gann Time Windows: backtest walk-forward
import gann_pivots  # TM - #GANN-TW - Gann Time Windows: pivot swing chart & trang thai song
import gann_windows  # TM - #GANN-TW - Gann Time Windows: chieu thoi gian & cua so
import klines_coverage  # TM - #GANN-TW - Gann Time Windows: do phu & loc theo thoi gian
import settings    # module cuc bo: luu credential xuong volume
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
)

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
    path = _kline_path(symbol, tf)
    if not path.exists():
        raise FileNotFoundError(
            f"Chua co du lieu {symbol} khung {tf}. Doi mot chu ky pull, "
            f"hoac goi refresh_data({symbol!r}).")
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
FETCH_LOCK = asyncio.Lock()


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

            path = _kline_path(symbol, timeframe)
            existing = _read_json(path, [])
            if not isinstance(existing, list):
                existing = []
            # TM - #GANN-TW - Gann Time Windows: retention theo tung khung
            merged = _merge_bars(existing, rows, _retention_for(timeframe))
            _write_json(path, merged)
            return {"ok": True, "fetched": len(rows), "total": len(merged),
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
        async with gate:
            # TM - #ORB - ORB Enhancement: ghi nguon goi de doi chieu request M5
            return (symbol, tf, await _fetch_one(client, symbol, tf, source=source))

    async with FETCH_LOCK:
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
    path = _kline_path(symbol, timeframe)
    try:
        stamp = int(path.stat().st_mtime * 1000)
        return _normalize_bars(_read_json(path, [])), stamp
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
    """Keo mot khung cho scheduler ORB. Dung chung FETCH_LOCK voi fetcher nen
    de hai ben khong ghi de cung mot file cung luc."""
    async with FETCH_LOCK:
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


def _evaluate_trade(*, symbol: str, side: str, entry: float, stop: float,
                    target: float, margin_usd: float, trade_type: str,
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
        "symbol": symbol,
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
            "symbol": symbol,
            "max_margin_per_trade": max_margin or "khong gioi han",
            "max_stop_points": max_stop,
            "min_take_profit_points": min_tp,
            # Nhac lai cai gi tu dau, de doc lai nhat ky cu khong phai doan
            "max_stop_points_scope": f"rieng {symbol}",
            "min_take_profit_points_scope": f"rieng {symbol}",
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
    for entry in STORE.symbols(DEFAULT_SYMBOL):
        frames = []
        for tf in KL["timeframes"]:
            path = _kline_path(entry["symbol"], tf)
            item = {"timeframe": tf, "exists": path.exists()}
            if path.exists():
                item["updated_at"] = datetime.fromtimestamp(
                    path.stat().st_mtime, TZ).strftime("%Y-%m-%d %H:%M:%S")
            frames.append(item)
        rows.append({
            "symbol": entry["symbol"],
            "enabled": entry["enabled"],
            "ready": all(f["exists"] for f in frames),
            "timeframes": frames,
        })
    return {
        "data_root": str(DATA_ROOT),
        "market": FETCH.get("market"),
        "default_symbol": _resolve_symbol(),
        "symbols_tracked": len(rows),
        "fetcher_enabled": bool(FETCH.get("enabled")),
        "interval_seconds": FETCH.get("interval_seconds"),
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
    for tf in _on_demand_timeframes():
        path = _kline_path(ORB_SYMBOL, tf)
        item: dict[str, Any] = {"symbol": ORB_SYMBOL, "timeframe": tf,
                                "exists": path.exists(),
                                "pulled_by": "refresh_data / ORB watch (khong theo fetcher nen)"}
        if path.exists():
            item["updated_at"] = datetime.fromtimestamp(
                path.stat().st_mtime, TZ).strftime("%Y-%m-%d %H:%M:%S")
        out.append(item)
    return out

@_tool_with_reasons()
def get_klines(timeframe: str, symbol: str = "", limit: int = 0,
               include_forming: bool = False, start: str = "",
               end: str = "") -> dict[str, Any]:
    """Lay nen OHLCV tho cua mot khung thoi gian.

    timeframe: mot trong cac khung khai bao o config (vd 1w, 1d, 4h, 1h, 15m).
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
        for tf in KL["timeframes"]:
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
                          max_requests: int = 40) -> dict[str, Any]:
    """Keo lich su cu ve cho day, lui dan den khi het du lieu tren san.

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

    retention = _retention_for(tf)
    path = _kline_path(sym, tf)
    warnings: list[str] = []
    if retention:
        warnings.append(
            f"khung {tf} dang gioi han {retention} nen - backfill xong se bi cat "
            f"lai o lan fetch sau. Dat retention['{tf}'] = null trong config truoc.")

    limit = int(FETCH.get("fetch_limit", 500))
    requests_made = 0
    added_total = 0

    async with FETCH_LOCK:
        async with httpx.AsyncClient() as client:
            for _ in range(max(1, int(max_requests))):
                existing = _read_json(path, [])
                if not isinstance(existing, list):
                    existing = []
                before = len(existing)
                oldest = min((int(r[0]) for r in existing
                              if isinstance(r, (list, tuple)) and r), default=None)

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

                merged = _merge_bars(existing, rows, retention)
                _write_json(path, merged)
                added = len(merged) - before
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
    """
    day = date or _today()
    sym = _resolve_symbol(symbol)
    direction = direction.strip().lower()
    if direction not in ("long", "short", "neutral"):
        raise ValueError("direction phai la long, short hoac neutral")

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
    return {"saved": True, "symbol": sym, "date": day, "file": str(path),
            "revisions": len(history)}


@mcp.tool()
def get_rules(symbol: str = "", history_limit: int = 10) -> dict[str, Any]:
    """Doc bo quy dinh dang co hieu luc va lich su thay doi.

    symbol: dien ten cap de xem dung bo rule ap cho cap do (SL/TP/nguong swing
      cua no). Bo trong = rule chung + gia tri mac dinh cho cap chua dat rieng.
    history_limit: so lan doi gan nhat can xem, 0 = xem tat ca.
    """
    sym = _resolve_symbol(symbol) if symbol else ""
    doc = _rules_doc()
    history = doc["history"]
    return {
        "symbol": sym or None,
        "rules": _load_rules(sym),
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
      swing_min_take_profit_points, swing_max_stop_points.

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
) -> dict[str, Any]:
    """Ghi mot lenh vua vao. Tra ve canh bao neu pham rule, nhung van ghi de nhat ky dung thuc te.

    symbol: bo trong = cap mac dinh. Nguong SL toi da, TP toi thieu va nguong
      swing duoc lay theo DUNG CAP NAY. Quota lenh/ngay, margin va daily_stop_loss
      thi dung chung cho moi cap.
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
    }
    trades.append(trade)
    _write_json(path, trades)
    out = {
        "logged": True,
        "trade": trade,
        "rule_violations": violations,
        "rules_changed_today": _changed_today(_rules_history(), day),
    }
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
      nguong swing) lay theo dung cap nay, nen cham cung mot bo so tren hai cap
      khac nhau co the ra hai ket qua khac nhau - do la co y.
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


async def _account_day_one(client, adapter, day: str, symbol: str) -> dict[str, Any]:
    """Fill + settlement cua MOT cap trong mot ngay."""
    start_ms, end_ms = _day_window_ms(day)
    fills = await adapter.fills(client, start_ms, end_ms, symbol)
    settlements = await adapter.settlements(client, start_ms, end_ms, symbol)
    for row in fills:
        row["time"] = _stamp(row["ts"])
        row["symbol"] = symbol
    for row in settlements:
        row["time"] = _stamp(row["ts"])
        row["symbol"] = symbol

    # Gom vi the RIENG tung cap: khoi luong cong don cua BTC va ETH khong the
    # tron vao mot chuoi, tron vao la ranh gioi vi the sai hoan toan.
    positions = _group_fills_into_positions(fills)
    for group in positions:
        group["symbol"] = symbol
        group["position_id"] = f"{symbol}-{group['position_id']}"
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

    symbol: bo trong = tat ca cac cap trong danh sach o trang admin.
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

    return {
        "date": day,
        "exchange": data["exchange"],
        "matched": not findings,
        "findings": findings,
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


# ---------------------------------------------------------------- admin

ADMIN_PATH = SRV.get("admin_path", "/admin")
ADMIN_SAVE_PATH = ADMIN_PATH.rstrip("/") + "/save"
ADMIN_TEST_PATH = ADMIN_PATH.rstrip("/") + "/test"
ADMIN_SYMBOL_PATH = ADMIN_PATH.rstrip("/") + "/symbols"


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
    ready = {
        row["symbol"]: all(_kline_path(row["symbol"], tf).exists()
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
        # TM - #ORB - ORB Enhancement
        orb_path=ADMIN_ORB_PATH if ORB_ENABLED else "",
        # TM - #GANN-TW - Gann Time Windows
        gann_path=ADMIN_GANN_PATH if GANN_ENABLED else "",
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
        # Vua dat mat khau lan dau thi tai lai de trinh duyet hoi dang nhap.
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
                             admin_path=ADMIN_PATH, setup_required=not STORE.has_auth())
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
            setup_paths=(ADMIN_PATH, ADMIN_SAVE_PATH, ADMIN_TEST_PATH, ADMIN_SYMBOL_PATH,
                         # TM - #ORB - ORB Enhancement
                         ADMIN_ORB_PATH, ADMIN_ORB_ACTION_PATH,
                         # TM - #ORB-RULES - ORB Rule Set
                         ADMIN_ORB_RULES_PATH,
                         # TM - #GANN-TW - Gann Time Windows
                         ADMIN_GANN_PATH, ADMIN_GANN_ACTION_PATH),
        )

    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(scope):
        task = asyncio.create_task(_fetcher_loop()) if FETCH.get("enabled") else None
        # TM - #ORB - ORB Enhancement: scheduler ORB song cung vong doi app nhu fetcher
        orb_task = (asyncio.create_task(ORB_SCHEDULER.run_forever())
                    if ORB_SCHEDULER is not None else None)
        try:
            async with inner(scope):
                yield
        finally:
            for running in (task, orb_task):
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
