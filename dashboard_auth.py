# TM - #DASHBOARD - Setup dashboard
"""Tai khoan rieng cua dashboard, tach han khoi dang nhap cua admin.

Cookie chua token ngau nhien; DB chi luu SHA-256 cua token, nen doc duoc DB cung khong
dung lai duoc phien. Moi request deu doc lai vai tro va trang thai tai khoan, nen tat
tai khoan hoac doi quyen co hieu luc ngay.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

import sqlite_db

ROLES = ("owner", "viewer")
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
PASSWORD_MIN = 10
PASSWORD_MAX = 256       # chan mat khau cuc dai lam ton CPU cua argon2
TOUCH_EVERY_MS = 60_000  # keo dai phien toi da moi phut mot lan, de khong ghi DB moi request
LOG_KEEP_MS = 90 * 86_400_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clean_username(value: Any) -> str:
    name = str(value or "").strip()
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("username phai 3-32 ky tu, chi gom a-z 0-9 _ . -")
    return name


def clean_password(value: Any) -> str:
    password = str(value or "")
    if len(password) < PASSWORD_MIN:
        raise ValueError(f"mat khau phai tu {PASSWORD_MIN} ky tu tro len")
    if len(password) > PASSWORD_MAX:
        raise ValueError(f"mat khau toi da {PASSWORD_MAX} ky tu")
    return password


def random_password() -> str:
    return secrets.token_urlsafe(15)


class DashAuth:
    def __init__(self, path: Path, now_ms: Callable[[], int] | None = None,
                 hasher: PasswordHasher | None = None) -> None:
        self.path = Path(path)
        self.now_ms = now_ms or _now_ms
        self.hasher = hasher or PasswordHasher()
        self._dummy: str | None = None
        sqlite_db.migrate(self.path)

    def _conn(self):
        return closing(sqlite_db.connect(self.path))

    # ------------------------------------------------------------ tai khoan

    def users(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id, username, role, is_active, last_login_at, created_at, "
                                "updated_at FROM dashboard_users ORDER BY username").fetchall()
        return [dict(r) for r in rows]

    def user(self, ref: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT id, username, role, is_active, last_login_at, created_at, updated_at "
                               "FROM dashboard_users WHERE id = ? OR username = ?",
                               (str(ref), str(ref).strip().lower())).fetchone()
        if row is None:
            raise ValueError(f"khong tim thay tai khoan {ref!r}")
        return dict(row)

    def _audit(self, conn, actor: str, action: str, target: str, details: dict[str, Any] | None,
               ip: str | None) -> None:
        conn.execute("INSERT INTO dashboard_audit (at, actor, action, target, details, ip) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (self.now_ms(), actor, action, target,
                      json.dumps(details or {}, ensure_ascii=False), ip))

    def audit(self, actor: str, action: str, target: str, details: dict[str, Any] | None = None,
              ip: str | None = None) -> None:
        with self._conn() as conn, conn:
            self._audit(conn, actor, action, target, details, ip)

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM dashboard_audit ORDER BY id DESC LIMIT ?",
                                (int(limit),)).fetchall()
        return [{**dict(r), "details": json.loads(r["details"] or "{}")} for r in rows]

    def create_user(self, username: str, password: str, role: str, *, actor: str,
                    ip: str | None = None) -> dict[str, Any]:
        name = clean_username(username)
        role = str(role or "viewer").strip().lower()
        if role not in ROLES:
            raise ValueError(f"role phai la mot trong {list(ROLES)}")
        digest = self.hasher.hash(clean_password(password))
        now = self.now_ms()
        with self._conn() as conn, conn:
            if conn.execute("SELECT 1 FROM dashboard_users WHERE username = ?", (name,)).fetchone():
                raise ValueError(f"username {name} da ton tai")
            uid = str(uuid.uuid4())
            conn.execute("INSERT INTO dashboard_users (id, username, password_hash, role, is_active, "
                         "created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                         (uid, name, digest, role, now, now))
            self._audit(conn, actor, "create", name, {"role": role}, ip)
        return self.user(uid)

    def _guard_last_owner(self, conn, user: dict[str, Any], what: str) -> None:
        if user["role"] != "owner" or not user["is_active"]:
            return
        others = conn.execute("SELECT COUNT(*) FROM dashboard_users WHERE role = 'owner' AND "
                              "is_active = 1 AND id != ?", (user["id"],)).fetchone()[0]
        if not others:
            raise ValueError(f"khong the {what} owner cuoi cung")

    def set_role(self, ref: str, role: str, *, actor: str, ip: str | None = None) -> dict[str, Any]:
        user = self.user(ref)
        role = str(role or "").strip().lower()
        if role not in ROLES:
            raise ValueError(f"role phai la mot trong {list(ROLES)}")
        if role == user["role"]:
            return user
        with self._conn() as conn, conn:
            if role != "owner":
                self._guard_last_owner(conn, user, "ha quyen")
            conn.execute("UPDATE dashboard_users SET role = ?, updated_at = ? WHERE id = ?",
                         (role, self.now_ms(), user["id"]))
            conn.execute("DELETE FROM dashboard_sessions WHERE user_id = ?", (user["id"],))
            self._audit(conn, actor, "set_role", user["username"], {"from": user["role"], "to": role}, ip)
        return self.user(user["id"])

    def set_active(self, ref: str, active: bool, *, actor: str, ip: str | None = None) -> dict[str, Any]:
        user = self.user(ref)
        if bool(active) == bool(user["is_active"]):
            return user
        with self._conn() as conn, conn:
            if not active:
                self._guard_last_owner(conn, user, "tat")
                conn.execute("DELETE FROM dashboard_sessions WHERE user_id = ?", (user["id"],))
            conn.execute("UPDATE dashboard_users SET is_active = ?, updated_at = ? WHERE id = ?",
                         (int(bool(active)), self.now_ms(), user["id"]))
            self._audit(conn, actor, "enable" if active else "disable", user["username"], None, ip)
        return self.user(user["id"])

    def reset_password(self, ref: str, password: str, *, actor: str, ip: str | None = None) -> None:
        user = self.user(ref)
        digest = self.hasher.hash(clean_password(password))
        with self._conn() as conn, conn:
            conn.execute("UPDATE dashboard_users SET password_hash = ?, updated_at = ? WHERE id = ?",
                         (digest, self.now_ms(), user["id"]))
            conn.execute("DELETE FROM dashboard_sessions WHERE user_id = ?", (user["id"],))
            self._audit(conn, actor, "reset_password", user["username"], None, ip)

    def delete_user(self, ref: str, *, actor: str, ip: str | None = None) -> None:
        user = self.user(ref)
        with self._conn() as conn, conn:
            self._guard_last_owner(conn, user, "xoa")
            conn.execute("DELETE FROM dashboard_sessions WHERE user_id = ?", (user["id"],))
            conn.execute("DELETE FROM dashboard_users WHERE id = ?", (user["id"],))
            self._audit(conn, actor, "delete", user["username"], {"role": user["role"]}, ip)

    def ensure_owner(self, username: str, password: str, *, actor: str) -> tuple[dict[str, Any], bool]:
        """Lenh CLI du phong: tao moi hoac khoi phuc thanh owner dang hoat dong."""
        name = clean_username(username)
        with self._conn() as conn:
            exists = conn.execute("SELECT 1 FROM dashboard_users WHERE username = ?", (name,)).fetchone()
        if not exists:
            return self.create_user(name, password, "owner", actor=actor), True
        self.reset_password(name, password, actor=actor)
        self.set_active(name, True, actor=actor)
        return self.set_role(name, "owner", actor=actor), False

    # ------------------------------------------------------------ dang nhap

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        """Sai username cung chay argon2, de thoi gian phan hoi khong lo username nao ton tai."""
        name = str(username or "").strip().lower()
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM dashboard_users WHERE username = ?", (name,)).fetchone()
        if self._dummy is None:
            self._dummy = self.hasher.hash(secrets.token_urlsafe(16))
        digest = row["password_hash"] if row else self._dummy
        try:
            self.hasher.verify(digest, str(password or "")[:PASSWORD_MAX])
        except (Argon2Error, InvalidHashError):
            return None
        if row is None or not row["is_active"]:
            return None
        return self.user(row["id"])

    def locked_until(self, ip: str) -> int | None:
        with self._conn() as conn:
            row = conn.execute("SELECT locked_until FROM dashboard_login_guard WHERE ip = ?", (ip,)).fetchone()
        until = int(row[0]) if row and row[0] else 0
        return until if until > self.now_ms() else None

    def record_failure(self, ip: str, max_attempts: int, lockout_minutes: float) -> int | None:
        """Tinh lan sai lien tiep theo IP. Du nguong thi khoa va dem lai tu dau sau khi het khoa."""
        now = self.now_ms()
        with self._conn() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT failures FROM dashboard_login_guard WHERE ip = ?", (ip,)).fetchone()
            failures = int(row[0]) + 1 if row else 1
            locked = None
            if failures >= int(max_attempts):
                locked = now + int(float(lockout_minutes) * 60_000)
                failures = 0
            conn.execute("INSERT INTO dashboard_login_guard (ip, failures, locked_until, updated_at) "
                         "VALUES (?, ?, ?, ?) ON CONFLICT(ip) DO UPDATE SET failures = excluded.failures, "
                         "locked_until = excluded.locked_until, updated_at = excluded.updated_at",
                         (ip, failures, locked, now))
        return locked

    def clear_failures(self, ip: str) -> None:
        with self._conn() as conn, conn:
            conn.execute("DELETE FROM dashboard_login_guard WHERE ip = ?", (ip,))

    def log_login(self, username: str, ip: str, ok: bool, reason: str | None = None) -> None:
        now = self.now_ms()
        with self._conn() as conn, conn:
            conn.execute("INSERT INTO dashboard_login_log (at, username, ip, ok, reason) VALUES (?, ?, ?, ?, ?)",
                         (now, str(username or "")[:64], ip, int(bool(ok)), reason))
            conn.execute("DELETE FROM dashboard_login_log WHERE at < ?", (now - LOG_KEEP_MS,))

    def login_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM dashboard_login_log ORDER BY id DESC LIMIT ?",
                                (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ phien

    def create_session(self, user: dict[str, Any], *, ip: str | None, user_agent: str | None,
                       remember: bool, life_ms: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = self.now_ms()
        with self._conn() as conn, conn:
            conn.execute("DELETE FROM dashboard_sessions WHERE expires_at <= ?", (now,))
            conn.execute("INSERT INTO dashboard_sessions (id, user_id, expires_at, ip, user_agent, csrf_token, "
                         "remember, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (_token_id(token), user["id"], now + int(life_ms), ip,
                          str(user_agent or "")[:300], csrf, int(bool(remember)), now, now))
            conn.execute("UPDATE dashboard_users SET last_login_at = ? WHERE id = ?", (now, user["id"]))
        return token, csrf

    def session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT s.id, s.user_id, s.expires_at, s.csrf_token, s.remember, s.last_seen_at, "
                "u.username, u.role, u.is_active FROM dashboard_sessions s "
                "JOIN dashboard_users u ON u.id = s.user_id WHERE s.id = ?", (_token_id(token),)).fetchone()
        if row is None or not row["is_active"] or int(row["expires_at"]) <= self.now_ms():
            return None
        return dict(row)

    def touch(self, session: dict[str, Any], life_ms: int) -> int | None:
        """Han truot. Tra ve so ms song moi khi co gia han, de server dat lai cookie."""
        now = self.now_ms()
        if now - int(session["last_seen_at"]) < TOUCH_EVERY_MS:
            return None
        with self._conn() as conn, conn:
            conn.execute("UPDATE dashboard_sessions SET expires_at = ?, last_seen_at = ? WHERE id = ?",
                         (now + int(life_ms), now, session["id"]))
        return int(life_ms)

    def destroy(self, token: str) -> None:
        with self._conn() as conn, conn:
            conn.execute("DELETE FROM dashboard_sessions WHERE id = ?", (_token_id(token),))
