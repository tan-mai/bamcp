# TM - #ADMIN-LOGIN - Form dang nhap admin
"""Phien dang nhap bang form cua cac trang admin. /mcp van la Basic auth, khong dung phien nay.

Cung cach voi dashboard_auth: cookie chua token ngau nhien, DB chi luu SHA-256 cua token, nen doc
duoc DB cung khong dung lai duoc phien. Moi phien gan voi dau van tay cua credential, nen mat khau
doi bang duong nao thi moi phien cu cung het hieu luc ngay.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

import sqlite_db


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AdminSessions:
    def __init__(self, path: Path, now_ms: Callable[[], int] | None = None) -> None:
        self.path = Path(path)
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        sqlite_db.migrate(self.path)

    def _conn(self):
        return closing(sqlite_db.connect(self.path))

    def create(self, fingerprint: str, life_ms: int) -> str:
        token = secrets.token_urlsafe(32)
        now = self.now_ms()
        with self._conn() as conn, conn:
            # Don luon phien da het han va phien cua mat khau cu
            conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ? OR auth_fp != ?", (now, fingerprint))
            conn.execute("INSERT INTO admin_sessions (id, auth_fp, expires_at, created_at) VALUES (?, ?, ?, ?)",
                         (_token_id(token), fingerprint, now + int(life_ms), now))
        return token

    def valid(self, token: str, fingerprint: str) -> bool:
        if not token:
            return False
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM admin_sessions WHERE id = ? AND auth_fp = ? AND expires_at > ?",
                               (_token_id(token), fingerprint, self.now_ms())).fetchone()
        return row is not None

    def destroy(self, token: str) -> None:
        with self._conn() as conn, conn:
            conn.execute("DELETE FROM admin_sessions WHERE id = ?", (_token_id(token),))
