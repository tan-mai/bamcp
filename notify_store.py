# TM - #NOTIFY-CHANNEL - Tách kênh thông báo theo cặp
from __future__ import annotations

import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import notify_logic
import sqlite_db

CHANNEL_COLUMNS = ("id", "name", "provider", "telegram_bot_token_ref", "telegram_chat_id", "telegram_thread_id",
                   "discord_webhook_ref", "discord_username", "discord_avatar_url", "enabled", "last_ok_at",
                   "last_error", "last_error_at", "created_at", "updated_at")
CHANNEL_EDITABLE = ("name", "provider", "telegram_bot_token_ref", "telegram_chat_id", "telegram_thread_id",
                    "discord_webhook_ref", "discord_username", "discord_avatar_url", "enabled")
ROUTE_COLUMNS = ("id", "symbol", "category", "channel_id", "min_severity", "digest", "enabled", "created_at",
                 "updated_at")
LOG_COLUMNS = ("id", "symbol", "category", "severity", "channel_id", "dedupe_key", "status", "error",
               "payload_preview", "created_at")
PREVIEW = 200


def _now_ms() -> int:
    return int(time.time() * 1000)


def _channel(row: Any) -> dict[str, Any]:
    out = {k: row[k] for k in CHANNEL_COLUMNS}
    out["enabled"] = bool(out["enabled"])
    return out


def _route(row: Any) -> dict[str, Any]:
    out = {k: row[k] for k in ROUTE_COLUMNS}
    out["digest"], out["enabled"] = bool(out["digest"]), bool(out["enabled"])
    return out


class NotifyStore:
    def __init__(self, path: Path, now_ms: Callable[[], int] | None = None) -> None:
        self.path = Path(path)
        self.now_ms = now_ms or _now_ms
        sqlite_db.migrate(self.path)

    def _conn(self):
        return closing(sqlite_db.connect(self.path))

    # ---------------------------------------------------------- kenh

    def channels(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM notify_channels ORDER BY created_at, name").fetchall()
        return [_channel(r) for r in rows]

    def channel(self, channel_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM notify_channels WHERE id = ?", (str(channel_id),)).fetchone()
        if row is None:
            raise ValueError(f"khong co kenh {channel_id!r}")
        return _channel(row)

    def save_channel(self, fields: dict[str, Any], channel_id: str | None = None) -> dict[str, Any]:
        now = self.now_ms()
        values = {k: fields.get(k) for k in CHANNEL_EDITABLE if k in fields}
        if "enabled" in values:
            values["enabled"] = int(bool(values["enabled"]))
        with self._conn() as conn, conn:
            if channel_id:
                if conn.execute("SELECT 1 FROM notify_channels WHERE id = ?", (channel_id,)).fetchone() is None:
                    raise ValueError(f"khong co kenh {channel_id!r}")
                keys = list(values)
                conn.execute(f"UPDATE notify_channels SET {', '.join(f'{k} = ?' for k in keys)}, updated_at = ? "
                             "WHERE id = ?", [values[k] for k in keys] + [now, channel_id])
            else:
                channel_id = str(uuid.uuid4())
                row = {"id": channel_id, "enabled": 1, **values, "created_at": now, "updated_at": now}
                keys = list(row)
                conn.execute(f"INSERT INTO notify_channels ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
                             [row[k] for k in keys])
        return self.channel(channel_id)

    def delete_channel(self, channel_id: str) -> dict[str, Any]:
        channel = self.channel(channel_id)
        with self._conn() as conn, conn:
            routes = conn.execute("DELETE FROM notify_routes WHERE channel_id = ?", (channel_id,)).rowcount
            conn.execute("DELETE FROM notify_channels WHERE id = ?", (channel_id,))
        return {"channel": channel, "routes_removed": routes}

    def mark_ok(self, channel_id: str) -> None:
        with self._conn() as conn, conn:
            conn.execute("UPDATE notify_channels SET last_ok_at = ? WHERE id = ?", (self.now_ms(), channel_id))

    def mark_error(self, channel_id: str, error: str) -> None:
        with self._conn() as conn, conn:
            conn.execute("UPDATE notify_channels SET last_error = ?, last_error_at = ? WHERE id = ?",
                         (str(error)[:500], self.now_ms(), channel_id))

    # ---------------------------------------------------------- route

    def routes(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM notify_routes ORDER BY symbol, category, created_at").fetchall()
        return [_route(r) for r in rows]

    def set_cell(self, symbol: str, category: str, channel_ids: list[str], *, min_severity: str = "low",
                 digest: bool = False, enabled: bool = True) -> list[dict[str, Any]]:
        """Thay toan bo route cua mot o (symbol, category). channel_ids rong = xoa o do."""
        if min_severity not in notify_logic.RANK:
            raise ValueError(f"min_severity phai la mot trong {list(notify_logic.SEVERITIES)}")
        known = {c["id"] for c in self.channels()}
        missing = [c for c in channel_ids if c not in known]
        if missing:
            raise ValueError(f"khong co kenh {missing}")
        now = self.now_ms()
        with self._conn() as conn, conn:
            conn.execute("DELETE FROM notify_routes WHERE symbol = ? AND category = ?", (symbol, category))
            for cid in dict.fromkeys(channel_ids):
                conn.execute("INSERT INTO notify_routes (id, symbol, category, channel_id, min_severity, digest, "
                             "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (str(uuid.uuid4()), symbol, category, cid, min_severity, int(bool(digest)),
                              int(bool(enabled)), now, now))
        return [r for r in self.routes() if r["symbol"] == symbol and r["category"] == category]

    def add_route(self, symbol: str, category: str, channel_id: str, *, min_severity: str = "low",
                  digest: bool = False) -> None:
        now = self.now_ms()
        with self._conn() as conn, conn:
            conn.execute("INSERT OR IGNORE INTO notify_routes (id, symbol, category, channel_id, min_severity, digest, "
                         "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                         (str(uuid.uuid4()), symbol, category, channel_id, min_severity, int(bool(digest)), now, now))

    # ---------------------------------------------------------- nhat ky

    def log(self, *, symbol: str, category: str, severity: str, status: str, channel_id: str | None = None,
            dedupe_key: str | None = None, error: str | None = None, preview: str = "") -> str:
        row_id = str(uuid.uuid4())
        with self._conn() as conn, conn:
            conn.execute("INSERT INTO notify_log (id, symbol, category, severity, channel_id, dedupe_key, status, "
                         "error, payload_preview, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (row_id, symbol, category, severity, channel_id, dedupe_key, status,
                          (str(error)[:500] if error else None), (preview or "")[:PREVIEW], self.now_ms()))
        return row_id

    def logs(self, *, symbol: str = "", status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM notify_log WHERE 1 = 1", []
        if symbol:
            sql += " AND symbol = ?"
            args.append(symbol)
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [{k: r[k] for k in LOG_COLUMNS} for r in rows]

    def counts(self, start_ms: int, end_ms: int) -> dict[str, dict[str, int]]:
        """{symbol: {status: so dong}} trong [start, end). Tin tom tat (DIGEST) khong tinh vao SENT."""
        with self._conn() as conn:
            rows = conn.execute("SELECT symbol, status, COUNT(*) AS n FROM notify_log WHERE created_at >= ? AND "
                                "created_at < ? AND category != ? GROUP BY symbol, status",
                                (int(start_ms), int(end_ms), notify_logic.DIGEST)).fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["symbol"], {})[r["status"]] = int(r["n"])
        return out

    def prune(self, before_ms: int) -> int:
        with self._conn() as conn, conn:
            return conn.execute("DELETE FROM notify_log WHERE created_at < ?", (int(before_ms),)).rowcount
