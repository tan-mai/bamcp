"""Adapter doc du lieu tai khoan tu san giao dich.

CHI DOC. Khong co ham nao dat lenh, huy lenh, chuyen tien hay rut tien.
Ba san co ba kieu ky chu ky khac nhau, moi lop tu lo phan cua minh roi tra ve
cung mot dang du lieu da chuan hoa cho server.py dung.

Credential khong bao gio duoc log, khong bao gio nam trong thong bao loi.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import httpx2 as httpx


class ExchangeError(RuntimeError):
    """Loi khi goi san. Thong diep da duoc lam sach, khong chua key/secret."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _f(value: Any, default: float = 0.0) -> float:
    """San tra so duoi dang chuoi, va doi khi tra chuoi rong."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- base

class BaseExchange:
    """Giao dien chung. Ba phuong thuc, ba dang du lieu chuan hoa.

    positions()   -> [{symbol, side, size, entry_price, mark_price, unrealized_pnl, leverage}]
    fills()       -> [{trade_id, order_id, ts, side, price, qty, fee, fee_asset, realized_pnl}]
    settlements() -> [{ts, type, amount, asset}]   type: realized_pnl | commission | funding
    """

    name = "base"
    needs_passphrase = False

    def __init__(self, *, key: str, secret: str, passphrase: str = "",
                 base_url: str, symbol: str, timeout: float = 20.0,
                 recv_window: int = 5000, options: dict[str, Any] | None = None):
        self.key = key
        self.secret = secret.encode("utf-8")
        self.passphrase = passphrase
        self.base_url = base_url.rstrip("/")
        self.symbol = symbol
        self.timeout = float(timeout)
        self.recv_window = int(recv_window)
        self.options = options or {}

    async def positions(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def fills(self, client: httpx.AsyncClient, start_ms: int,
                    end_ms: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def settlements(self, client: httpx.AsyncClient, start_ms: int,
                          end_ms: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def _request(self, client: httpx.AsyncClient, url: str,
                       headers: dict[str, str]) -> Any:
        try:
            resp = await client.get(url, headers=headers, timeout=self.timeout)
        except Exception as exc:
            raise ExchangeError(f"{self.name}: khong goi duoc ({type(exc).__name__})") from None
        if resp.status_code in (401, 403):
            raise ExchangeError(
                f"{self.name}: {resp.status_code} - key sai, het han, "
                "hoac IP cua VPS chua duoc whitelist")
        if resp.status_code >= 400:
            raise ExchangeError(f"{self.name}: HTTP {resp.status_code} - {resp.text[:200]}")
        try:
            return resp.json()
        except Exception:
            raise ExchangeError(f"{self.name}: tra ve khong phai JSON") from None


# ---------------------------------------------------------------- binance

class BinanceFutures(BaseExchange):
    """Binance USDT-M Futures. Ky: HMAC-SHA256 hex cua query string."""

    name = "binance"

    def _signed_url(self, path: str, params: dict[str, Any]) -> str:
        params = {k: v for k, v in params.items() if v is not None}
        params["timestamp"] = _now_ms()
        params["recvWindow"] = self.recv_window
        query = urllib.parse.urlencode(params)
        signature = hmac.new(self.secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{self.base_url}{path}?{query}&signature={signature}"

    async def _get(self, client, path, params):
        url = self._signed_url(path, params)
        data = await self._request(client, url, {"X-MBX-APIKEY": self.key})
        if isinstance(data, dict) and data.get("code") not in (None, 200):
            raise ExchangeError(f"binance: {data.get('code')} - {data.get('msg')}")
        return data

    async def positions(self, client):
        rows = await self._get(client, "/fapi/v2/positionRisk", {"symbol": self.symbol})
        out = []
        for row in rows if isinstance(rows, list) else []:
            amount = _f(row.get("positionAmt"))
            if amount == 0:
                continue
            out.append({
                "symbol": row.get("symbol"),
                "side": "long" if amount > 0 else "short",
                "size": abs(amount),
                "entry_price": _f(row.get("entryPrice")),
                "mark_price": _f(row.get("markPrice")),
                "unrealized_pnl": _f(row.get("unRealizedProfit")),
                "leverage": _f(row.get("leverage")) or None,
            })
        return out

    async def fills(self, client, start_ms, end_ms):
        rows = await self._get(client, "/fapi/v1/userTrades", {
            "symbol": self.symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000})
        out = []
        for row in rows if isinstance(rows, list) else []:
            out.append({
                "trade_id": str(row.get("id")),
                "order_id": str(row.get("orderId")),
                "ts": int(_f(row.get("time"))),
                "side": str(row.get("side", "")).lower(),
                "price": _f(row.get("price")),
                "qty": _f(row.get("qty")),
                "fee": -abs(_f(row.get("commission"))),
                "fee_asset": row.get("commissionAsset"),
                "realized_pnl": _f(row.get("realizedPnl")),
            })
        return out

    _INCOME_MAP = {"REALIZED_PNL": "realized_pnl", "COMMISSION": "commission",
                   "FUNDING_FEE": "funding"}

    async def settlements(self, client, start_ms, end_ms):
        rows = await self._get(client, "/fapi/v1/income", {
            "symbol": self.symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000})
        out = []
        for row in rows if isinstance(rows, list) else []:
            kind = self._INCOME_MAP.get(row.get("incomeType"))
            if not kind:
                continue
            out.append({"ts": int(_f(row.get("time"))), "type": kind,
                        "amount": _f(row.get("income")), "asset": row.get("asset")})
        return out


# ---------------------------------------------------------------- bybit

class BybitV5(BaseExchange):
    """Bybit V5. Ky: HMAC-SHA256 hex cua (timestamp + api_key + recv_window + query)."""

    name = "bybit"

    def _headers(self, query: str) -> dict[str, str]:
        ts = str(_now_ms())
        recv = str(self.recv_window)
        payload = f"{ts}{self.key}{recv}{query}"
        signature = hmac.new(self.secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "X-BAPI-API-KEY": self.key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": signature,
        }

    async def _get(self, client, path, params):
        params = {k: v for k, v in params.items() if v is not None}
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{query}" if query else f"{self.base_url}{path}"
        data = await self._request(client, url, self._headers(query))
        if not isinstance(data, dict):
            raise ExchangeError("bybit: response khong phai object")
        if int(_f(data.get("retCode"), -1)) != 0:
            raise ExchangeError(f"bybit: {data.get('retCode')} - {data.get('retMsg')}")
        result = data.get("result") or {}
        return result.get("list") or []

    @property
    def _category(self) -> str:
        return str(self.options.get("category", "linear"))

    async def positions(self, client):
        rows = await self._get(client, "/v5/position/list",
                               {"category": self._category, "symbol": self.symbol})
        out = []
        for row in rows:
            size = _f(row.get("size"))
            if size == 0:
                continue
            side = str(row.get("side", "")).lower()
            out.append({
                "symbol": row.get("symbol"),
                "side": "long" if side == "buy" else "short",
                "size": size,
                "entry_price": _f(row.get("avgPrice")),
                "mark_price": _f(row.get("markPrice")),
                "unrealized_pnl": _f(row.get("unrealisedPnl")),
                "leverage": _f(row.get("leverage")) or None,
            })
        return out

    async def fills(self, client, start_ms, end_ms):
        rows = await self._get(client, "/v5/execution/list", {
            "category": self._category, "symbol": self.symbol,
            "startTime": start_ms, "endTime": end_ms, "limit": 100})
        out = []
        for row in rows:
            out.append({
                "trade_id": str(row.get("execId")),
                "order_id": str(row.get("orderId")),
                "ts": int(_f(row.get("execTime"))),
                "side": str(row.get("side", "")).lower(),
                "price": _f(row.get("execPrice")),
                "qty": _f(row.get("execQty")),
                "fee": -abs(_f(row.get("execFee"))),
                "fee_asset": row.get("feeCurrency"),
                "realized_pnl": _f(row.get("closedPnl")),
            })
        return out

    async def settlements(self, client, start_ms, end_ms):
        out: list[dict[str, Any]] = []
        closed = await self._get(client, "/v5/position/closed-pnl", {
            "category": self._category, "symbol": self.symbol,
            "startTime": start_ms, "endTime": end_ms, "limit": 100})
        for row in closed:
            out.append({"ts": int(_f(row.get("updatedTime"))), "type": "realized_pnl",
                        "amount": _f(row.get("closedPnl")), "asset": "USDT"})
        # Bybit khong co endpoint income rieng: phi nam trong execution list
        for fill in await self.fills(client, start_ms, end_ms):
            if fill["fee"]:
                out.append({"ts": fill["ts"], "type": "commission",
                            "amount": fill["fee"], "asset": fill["fee_asset"]})
        out.sort(key=lambda r: r["ts"])
        return out


# ---------------------------------------------------------------- okx

class OkxV5(BaseExchange):
    """OKX V5. Ky: base64(HMAC-SHA256(timestamp + method + requestPath)).

    Rieng OKX doi them passphrase ngoai key va secret.
    """

    name = "okx"
    needs_passphrase = True

    def _headers(self, request_path: str) -> dict[str, str]:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload = f"{ts}GET{request_path}"
        signature = base64.b64encode(
            hmac.new(self.secret, payload.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii")
        return {
            "OK-ACCESS-KEY": self.key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    async def _get(self, client, path, params):
        params = {k: v for k, v in params.items() if v is not None}
        query = urllib.parse.urlencode(params)
        request_path = f"{path}?{query}" if query else path
        data = await self._request(client, f"{self.base_url}{request_path}",
                                   self._headers(request_path))
        if not isinstance(data, dict):
            raise ExchangeError("okx: response khong phai object")
        if str(data.get("code", "")) != "0":
            raise ExchangeError(f"okx: {data.get('code')} - {data.get('msg')}")
        return data.get("data") or []

    @property
    def _inst_type(self) -> str:
        return str(self.options.get("inst_type", "SWAP"))

    async def positions(self, client):
        rows = await self._get(client, "/api/v5/account/positions",
                               {"instType": self._inst_type, "instId": self.symbol})
        out = []
        for row in rows:
            pos = _f(row.get("pos"))
            if pos == 0:
                continue
            pos_side = str(row.get("posSide", "net")).lower()
            if pos_side in ("long", "short"):
                side = pos_side
            else:                       # che do net: dau cua pos quyet dinh huong
                side = "long" if pos > 0 else "short"
            out.append({
                "symbol": row.get("instId"),
                "side": side,
                "size": abs(pos),
                "entry_price": _f(row.get("avgPx")),
                "mark_price": _f(row.get("markPx")),
                "unrealized_pnl": _f(row.get("upl")),
                "leverage": _f(row.get("lever")) or None,
            })
        return out

    async def fills(self, client, start_ms, end_ms):
        rows = await self._get(client, "/api/v5/trade/fills-history", {
            "instType": self._inst_type, "instId": self.symbol,
            "begin": start_ms, "end": end_ms, "limit": 100})
        out = []
        for row in rows:
            out.append({
                "trade_id": str(row.get("tradeId")),
                "order_id": str(row.get("ordId")),
                "ts": int(_f(row.get("ts"))),
                "side": str(row.get("side", "")).lower(),
                "price": _f(row.get("fillPx")),
                "qty": _f(row.get("fillSz")),
                "fee": _f(row.get("fee")),          # OKX da tra so am
                "fee_asset": row.get("feeCcy"),
                "realized_pnl": _f(row.get("fillPnl")),
            })
        return out

    # bills: type 2 = giao dich, type 8 = funding fee
    _BILL_TYPES = {"2": "realized_pnl", "8": "funding"}

    async def settlements(self, client, start_ms, end_ms):
        rows = await self._get(client, "/api/v5/account/bills", {
            "instType": self._inst_type, "instId": self.symbol,
            "begin": start_ms, "end": end_ms, "limit": 100})
        out = []
        for row in rows:
            kind = self._BILL_TYPES.get(str(row.get("type")))
            if not kind:
                continue
            ts = int(_f(row.get("ts")))
            asset = row.get("ccy")
            pnl = _f(row.get("pnl"))
            fee = _f(row.get("fee"))
            if pnl:
                out.append({"ts": ts, "type": kind, "amount": pnl, "asset": asset})
            if fee:
                out.append({"ts": ts, "type": "commission", "amount": fee, "asset": asset})
        out.sort(key=lambda r: r["ts"])
        return out


# ---------------------------------------------------------------- factory

ADAPTERS: dict[str, type[BaseExchange]] = {
    "binance": BinanceFutures,
    "bybit": BybitV5,
    "okx": OkxV5,
}

DEFAULT_BASE_URLS = {
    "binance": "https://fapi.binance.com",
    "bybit": "https://api.bybit.com",
    "okx": "https://www.okx.com",
}

# Cung mot cap BTC/USDT perp, ba san goi ba kieu
DEFAULT_SYMBOLS = {
    "binance": "BTCUSDT",
    "bybit": "BTCUSDT",
    "okx": "BTC-USDT-SWAP",
}


def build(exchange: str, *, key: str, secret: str, passphrase: str = "",
          symbol: str = "", base_url: str = "", timeout: float = 20.0,
          recv_window: int = 5000, options: dict[str, Any] | None = None) -> BaseExchange:
    name = (exchange or "").strip().lower()
    adapter = ADAPTERS.get(name)
    if adapter is None:
        raise ExchangeError(f"san khong ho tro: {exchange}. Cho phep: {sorted(ADAPTERS)}")
    if not key or not secret:
        raise ExchangeError(f"{name}: thieu API key hoac secret")
    if adapter.needs_passphrase and not passphrase:
        raise ExchangeError(f"{name}: thieu passphrase (OKX bat buoc co)")
    return adapter(
        key=key,
        secret=secret,
        passphrase=passphrase,
        base_url=base_url or DEFAULT_BASE_URLS[name],
        symbol=symbol or DEFAULT_SYMBOLS[name],
        timeout=timeout,
        recv_window=recv_window,
        options=options,
    )
