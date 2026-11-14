# TM - #BOOKMAP - Order flow Bookmap
"""Cau hinh order flow: khoi `orderflow:` trong config.yaml la gia tri KHOI TAO, trang /admin/orderflow ghi
ban moi vao paths.orderflow_config_file (tren volume) va ban do thang. Giong alerts/dashboard.

Collector va server cung doc qua module nay. Collector so mtime cua ca hai file moi 30 giay: doi thi validate
lai, sai thi log loi va giu cau hinh cu (khong bao gio chay voi cau hinh hong).

Khong import server: collector la process rieng, chi can config.yaml + data_root.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

MARKETS = ("futures", "spot")
# Do phan giai footprint phai chia het 60 giay: luc het han duoc gop sang res 60s
FOOTPRINT_RES = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)

# Truong sua duoc tren trang admin, theo tung cap. (kieu, min, max, mac dinh)
SYMBOL_FIELDS: dict[str, tuple[type, float, float, Any]] = {
    "bucket_size": (float, 1e-8, 1e9, None),
    "depth_range_pct": (float, 0.001, 0.2, 0.03),
    "snapshot_interval_sec": (int, 1, 60, 2),
    "footprint_res_sec": (int, 1, 30, 5),
    "big_trade_min_qty": (float, 1e-8, 1e12, None),
    "bubble_merge_ms": (int, 0, 5000, 200),
    "wall_k": (float, 1.5, 100, 5),
    "absorption_min_qty": (float, 1e-8, 1e12, None),
}
# Truong chi phuc vu heatmap / so lenh: giai doan 1 an tren admin
BOOK_FIELDS = ("depth_range_pct", "snapshot_interval_sec", "wall_k", "absorption_min_qty")

RETENTION_DEFAULTS = {"heatmap_days": 14, "footprint_5s_days": 30, "footprint_60s_days": 365,
                      "big_trades_days": 180, "gaps_days": 365}

# Binance doi duong dan WebSocket USDS-M futures tu 2026-04-23: depth o /public, aggTrade o /market.
# Ket noi khong dinh tuyen (/ws, /stream tren host goc) khong con dung duoc.
ENDPOINTS_DEFAULT: dict[str, dict[str, Any]] = {
    "futures": {
        "ws_depth": "wss://fstream.binance.com/public/stream",
        "ws_trades": "wss://fstream.binance.com/market/stream",
        "rest": "https://fapi.binance.com",
        "depth_path": "/fapi/v1/depth", "depth_limit": 1000,
        "agg_path": "/fapi/v1/aggTrades",
        "vision": "https://data.binance.vision/data/futures/um/daily/aggTrades",
    },
    "spot": {
        "ws_depth": "wss://stream.binance.com:9443/stream",
        "ws_trades": "wss://stream.binance.com:9443/stream",
        "rest": "https://api.binance.com",
        "depth_path": "/api/v3/depth", "depth_limit": 5000,
        "agg_path": "/api/v3/aggTrades",
        "vision": "https://data.binance.vision/data/spot/daily/aggTrades",
    },
}

TECH_DEFAULTS = {
    "config_poll_sec": 30,       # so mtime config
    "heartbeat_sec": 10,         # ghi orderflow_status
    "stale_sec": 60,             # khong co event qua chung nay giay = canh bao do
    "reconnect_hours": 23,       # Binance tu ngat sau 24h: chu dong noi lai truoc moc do
    "retention_utc": "00:10",    # job don du lieu hang ngay
    "backfill_max_days": 90,
    "trade_gap_max_pages": 200,  # noi lai: lay bu aggTrade thieu qua REST toi da chung nay trang 1000 lenh
    "heatmap_agg_levels": [10, 60, 300],  # snapshot gop san (giay) cho heatmap khoang dai
    "log_max_mb": 10,
    "log_backups": 3,
}

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,30}$")


class ConfigError(ValueError):
    pass


def _num(value: Any, kind: type, low: float, high: float, name: str) -> Any:
    if isinstance(value, bool):
        raise ConfigError(f"{name} phai la so")
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise ConfigError(f"{name} phai la so, nhan duoc {value!r}") from None
    if number != number or number < low or number > high:
        raise ConfigError(f"{name} phai trong [{low:g}, {high:g}], nhan duoc {value!r}")
    if kind is int:
        if abs(number - round(number)) > 1e-9:
            raise ConfigError(f"{name} phai la so nguyen, nhan duoc {value!r}")
        return int(round(number))
    return number


def clean_symbol(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError("moi cap phai la object")
    symbol = str(raw.get("symbol") or "").strip().upper()
    if not _SYMBOL_RE.match(symbol):
        raise ConfigError(f"ten cap khong hop le: {raw.get('symbol')!r}")
    market = str(raw.get("market") or "futures").strip().lower()
    if market not in MARKETS:
        raise ConfigError(f"{symbol}: market phai la futures hoac spot, nhan duoc {market!r}")
    out: dict[str, Any] = {"symbol": symbol, "market": market,
                           "enabled": _bool(raw.get("enabled", True), f"{symbol}.enabled")}
    for key, (kind, low, high, default) in SYMBOL_FIELDS.items():
        value = raw.get(key)
        if value is None or value == "":
            if default is None:
                raise ConfigError(f"{symbol}: thieu {key}")
            value = default
        out[key] = _num(value, kind, low, high, f"{symbol}.{key}")
    if out["footprint_res_sec"] not in FOOTPRINT_RES:
        raise ConfigError(f"{symbol}.footprint_res_sec phai chia het 60 giay: {list(FOOTPRINT_RES)}")
    return out


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    raise ConfigError(f"{name} phai la true/false, nhan duoc {value!r}")


def clean_editable(raw: Any) -> dict[str, Any]:
    """Phan sua duoc tren admin: enabled, retention, symbols. Sai bat ky cho nao thi ConfigError, khong sua nua chung."""
    if not isinstance(raw, dict):
        raise ConfigError("cau hinh order flow phai la object")
    retention_raw = raw.get("retention") or {}
    if not isinstance(retention_raw, dict):
        raise ConfigError("retention phai la object")
    retention = {key: _num(retention_raw.get(key, default), int, 1, 3650, f"retention.{key}")
                 for key, default in RETENTION_DEFAULTS.items()}
    if retention["footprint_60s_days"] < retention["footprint_5s_days"]:
        raise ConfigError("retention.footprint_60s_days phai >= footprint_5s_days")
    symbols_raw = raw.get("symbols") or []
    if not isinstance(symbols_raw, list):
        raise ConfigError("symbols phai la danh sach")
    symbols, seen = [], set()
    for item in symbols_raw:
        row = clean_symbol(item)
        if row["symbol"] in seen:
            raise ConfigError(f"{row['symbol']} bi khai hai lan")
        seen.add(row["symbol"])
        symbols.append(row)
    return {"enabled": _bool(raw.get("enabled", True), "enabled"), "retention": retention, "symbols": symbols}


class OrderflowConfig:
    """Doc config.yaml + file admin, cache theo mtime. get() luon tra ban hop le gan nhat."""

    def __init__(self, config_path: Path | str, data_root: Path | str | None = None):
        self.config_path = Path(config_path)
        self._data_root_override = Path(data_root) if data_root else None
        self._key: tuple | None = None
        self._cfg: dict[str, Any] | None = None
        self.last_error: str = ""

    # ------------------------------------------------------------ duong dan
    def _yaml(self) -> dict[str, Any]:
        with self.config_path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _resolve(self, raw: dict[str, Any]) -> dict[str, Path]:
        paths = raw.get("paths") or {}
        root_text = os.environ.get("BAMCP_DATA_ROOT") or paths.get("data_root") or "./data"
        root = self._data_root_override or Path(root_text).expanduser()
        if not root.is_absolute():
            root = (self.config_path.parent / root).resolve()
        block = raw.get("orderflow") or {}

        def under(value: Any, default: str) -> Path:
            path = Path(str(value or default)).expanduser()
            return path if path.is_absolute() else root / path

        return {"data_root": root,
                "override": under(paths.get("orderflow_config_file"), "orderflow/config.json"),
                "db": under(block.get("db_path"), "orderflow.db"),
                "tmp": under(block.get("tmp_dir"), "orderflow/tmp"),
                "logs": under(block.get("log_dir"), "orderflow/logs")}

    @staticmethod
    def _mtime(path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    def paths(self) -> dict[str, Path]:
        return self._resolve(self._yaml())

    # ------------------------------------------------------------ doc
    def get(self, force: bool = False) -> dict[str, Any]:
        """Cau hinh hien hanh. File doi ma khong hop le thi giu ban cu, ghi loi vao last_error."""
        try:
            yaml_mtime = self._mtime(self.config_path)
            raw = self._yaml() if (force or self._cfg is None or self._key is None
                                   or self._key[0] != yaml_mtime) else None
            paths = self._resolve(raw) if raw is not None else self._cfg["paths"]
            key = (yaml_mtime, self._mtime(paths["override"]))
            if not force and self._cfg is not None and key == self._key:
                return self._cfg
            if raw is None:
                raw = self._yaml()
            cfg = self._build(raw, paths)
            self._cfg, self._key, self.last_error = cfg, key, ""
        except (ConfigError, OSError, ValueError, yaml.YAMLError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self._cfg is None:
                raise
            # Giu ban cu nhung nho mtime moi de khong doc lai lien tuc; sua file lan nua thi mtime doi
            with _suppress():
                self._key = (self._mtime(self.config_path), self._mtime(self._cfg["paths"]["override"]))
        return self._cfg

    def _build(self, raw: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
        block = copy.deepcopy(raw.get("orderflow") or {})
        seed = {"enabled": block.get("enabled", False), "retention": block.get("retention") or {},
                "symbols": block.get("symbols") or []}
        source = "config.yaml"
        if paths["override"].exists():
            with paths["override"].open("r", encoding="utf-8-sig") as fh:
                override = json.load(fh)
            if not isinstance(override, dict):
                raise ConfigError(f"{paths['override']} khong phai JSON object")
            seed.update({k: override[k] for k in ("enabled", "retention", "symbols") if k in override})
            source = str(paths["override"])
        editable = clean_editable(seed)
        endpoints = copy.deepcopy(ENDPOINTS_DEFAULT)
        for market, values in (block.get("endpoints") or {}).items():
            if market in endpoints and isinstance(values, dict):
                endpoints[market].update(values)
        tech = {**TECH_DEFAULTS, **{k: block[k] for k in TECH_DEFAULTS if k in block}}
        return {**editable, "endpoints": endpoints, "tech": tech, "paths": paths, "source": source}

    # ------------------------------------------------------------ tien ich
    def symbol(self, symbol: str) -> dict[str, Any] | None:
        symbol = str(symbol or "").upper()
        return next((s for s in self.get()["symbols"] if s["symbol"] == symbol), None)

    def active_symbols(self) -> list[dict[str, Any]]:
        cfg = self.get()
        return [s for s in cfg["symbols"] if s["enabled"]] if cfg["enabled"] else []

    def save_editable(self, raw: Any) -> dict[str, Any]:
        """Trang admin luu: validate truoc, ghi file tam roi doi ten (collector khong bao gio doc nua file)."""
        clean = clean_editable(raw)
        path = self.get()["paths"]["override"]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(clean, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)
        self.get(force=True)
        return clean


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True
