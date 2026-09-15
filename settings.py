"""Luu cau hinh nhay cam xuong file trong volume, thay cho bien moi truong.

File nam o <data_root>/settings.json nen song sot qua moi lan tao lai container.

Ba nguyen tac:
  1. Mat khau API luu duoi dang hash PBKDF2, khong bao gio luu plaintext.
  2. Secret cua san BUOC phai luu duoc doc lai (de ky request), nen la plaintext
     tren dia - khong co cach nao khac neu server phai tu khoi dong lai ma khong
     co nguoi go mat khau. Bu lai: file chmod 600 va khong bao gio tra ve HTTP.
  3. Khong ham nao trong module nay tra secret ra ngoai. Chi co masked() de hien thi.

Thu tu uu tien khi doc: settings.json > bien moi truong > config.yaml.
Da luu qua trang admin thi bien moi truong khong con tac dung nua - neu khong,
bam Luu xong ma khong thay gi doi la kieu loi kho hieu nhat.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

PBKDF2_ROUNDS = 200_000
MASK = "•" * 8          # hien thi cho biet "da co gia tri", khong lo gia tri

EXCHANGE_NAMES = ("binance", "bybit", "okx")


def _hash_password(password: str, salt: bytes | None = None) -> dict[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return {"salt": salt.hex(), "hash": digest.hex(), "rounds": str(PBKDF2_ROUNDS)}


class SettingsStore:
    """Doc/ghi settings.json. Mot tien trinh, mot luong ghi - khong can khoa."""

    def __init__(self, path: Path, tz):
        self.path = path
        self.tz = tz
        self._cache: dict[str, Any] | None = None
        # Header Authorization da xac thuc gan nhat. Tranh chay PBKDF2 moi request.
        self._verified_header: bytes = b""

    # ------------------------------------------------------------ doc / ghi

    def load(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        data: dict[str, Any] = {}
        if self.path.exists():
            with contextlib.suppress(Exception):
                with self.path.open("r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    data = loaded
        self._cache = data
        return data

    def _write(self, data: dict[str, Any]) -> None:
        data["updated_at"] = datetime.now(self.tz).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        with contextlib.suppress(OSError):     # Windows khong ho tro day du
            os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        self._cache = data
        self._verified_header = b""            # credential co the vua doi

    # ------------------------------------------------------------ auth

    def has_auth(self) -> bool:
        auth = self.load().get("auth") or {}
        return bool(auth.get("username") and auth.get("hash"))

    def username(self) -> str:
        return str((self.load().get("auth") or {}).get("username") or "")

    def set_auth(self, username: str, password: str) -> None:
        username = username.strip()
        if not username:
            raise ValueError("username khong duoc de trong")
        if len(password) < 8:
            raise ValueError("mat khau phai tu 8 ky tu tro len")
        data = self.load()
        data["auth"] = {"username": username, **_hash_password(password)}
        data.pop("setup_token", None)      # het viec, khong giu lai lam gi
        self._write(data)

    def setup_token(self) -> str:
        """Token dung de dat mat khau lan dau. Rong khi da co mat khau.

        Sinh MOT LAN roi luu xuong file, nen no giu nguyen qua moi lan khoi dong
        lai container. Neu sinh moi moi lan boot thi dong token trong log cu chet
        ngay khi container restart - nguoi dung dan token dung ma van bi tu choi.
        """
        if self.has_auth():
            return ""
        data = self.load()
        token = str(data.get("setup_token") or "")
        if not token:
            token = secrets.token_urlsafe(32)
            data["setup_token"] = token
            self._write(data)
        return token

    def verify(self, header_value: bytes, username: bytes, password: bytes) -> bool:
        """So khop credential Basic auth voi hash da luu.

        header_value chi dung lam khoa cache de khoi chay PBKDF2 moi request.
        """
        if self._verified_header and secrets.compare_digest(header_value, self._verified_header):
            return True
        auth = self.load().get("auth") or {}
        stored_user = str(auth.get("username") or "")
        stored_hash = str(auth.get("hash") or "")
        stored_salt = str(auth.get("salt") or "")
        if not (stored_user and stored_hash and stored_salt):
            return False
        try:
            rounds = int(auth.get("rounds") or PBKDF2_ROUNDS)
        except (TypeError, ValueError):
            rounds = PBKDF2_ROUNDS

        digest = hashlib.pbkdf2_hmac("sha256", password, bytes.fromhex(stored_salt), rounds)
        ok_user = secrets.compare_digest(username, stored_user.encode("utf-8"))
        ok_pass = secrets.compare_digest(digest.hex(), stored_hash)
        if ok_user and ok_pass:
            self._verified_header = bytes(header_value)
            return True
        return False

    # ------------------------------------------------------------ exchange

    def exchange(self) -> dict[str, Any]:
        ex = self.load().get("exchange") or {}
        return {
            "enabled": bool(ex.get("enabled")),
            "name": str(ex.get("name") or "").strip().lower(),
            "symbol": str(ex.get("symbol") or "").strip(),
            "key": str(ex.get("key") or ""),
            "secret": str(ex.get("secret") or ""),
            "passphrase": str(ex.get("passphrase") or ""),
        }

    def has_exchange_credentials(self) -> bool:
        ex = self.exchange()
        return bool(ex["key"] and ex["secret"])

    def set_exchange(self, *, enabled: bool, name: str, symbol: str = "",
                     key: str | None = None, secret: str | None = None,
                     passphrase: str | None = None) -> None:
        """key/secret/passphrase = None nghia la GIU NGUYEN gia tri cu.

        Form admin gui ve o trong khi nguoi dung khong doi secret, nen phai phan
        biet "de trong" voi "xoa di". Muon xoa thi truyen chuoi rong.
        """
        name = (name or "").strip().lower()
        if name and name not in EXCHANGE_NAMES:
            raise ValueError(f"san khong ho tro: {name}. Cho phep: {list(EXCHANGE_NAMES)}")

        data = self.load()
        current = self.exchange()
        data["exchange"] = {
            "enabled": bool(enabled),
            "name": name or current["name"],
            "symbol": symbol.strip(),
            "key": current["key"] if key is None else key.strip(),
            "secret": current["secret"] if secret is None else secret.strip(),
            "passphrase": (current["passphrase"] if passphrase is None
                           else passphrase.strip()),
        }
        self._write(data)

    # ------------------------------------------------------------ cap giao dich

    def symbols(self, seed: str = "") -> list[dict[str, Any]]:
        """Danh sach cap dang theo doi. Lan dau lay tu config de khong mat BTC."""
        rows = self.load().get("symbols")
        if not isinstance(rows, list) or not rows:
            if not seed:
                return []
            rows = [{"symbol": seed.upper(), "enabled": True, "added_at": ""}]
            data = self.load()
            data["symbols"] = rows
            self._write(data)
        return [
            {"symbol": str(r.get("symbol", "")).upper(),
             "enabled": bool(r.get("enabled", True)),
             "added_at": str(r.get("added_at", ""))}
            for r in rows if r.get("symbol")
        ]

    def enabled_symbols(self, seed: str = "") -> list[str]:
        return [r["symbol"] for r in self.symbols(seed) if r["enabled"]]

    def has_symbol(self, symbol: str) -> bool:
        return symbol.upper() in {r["symbol"] for r in self.symbols()}

    def add_symbol(self, symbol: str) -> None:
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("cap giao dich khong duoc de trong")
        if not symbol.isalnum():
            raise ValueError(f"ten cap khong hop le: {symbol}")
        if self.has_symbol(symbol):
            raise ValueError(f"{symbol} da co trong danh sach")
        data = self.load()
        rows = list(data.get("symbols") or [])
        rows.append({"symbol": symbol, "enabled": True,
                     "added_at": datetime.now(self.tz).isoformat(timespec="seconds")})
        data["symbols"] = rows
        self._write(data)

    def set_symbol_enabled(self, symbol: str, enabled: bool) -> None:
        symbol = symbol.strip().upper()
        data = self.load()
        rows = list(data.get("symbols") or [])
        for row in rows:
            if str(row.get("symbol", "")).upper() == symbol:
                row["enabled"] = bool(enabled)
                data["symbols"] = rows
                self._write(data)
                return
        raise ValueError(f"khong tim thay {symbol}")

    def remove_symbol(self, symbol: str) -> None:
        """Bo khoi danh sach theo doi. KHONG xoa file du lieu da pull ve -
        them lai cap do sau nay thi lich su van con nguyen."""
        symbol = symbol.strip().upper()
        data = self.load()
        rows = [r for r in (data.get("symbols") or [])
                if str(r.get("symbol", "")).upper() != symbol]
        if len(rows) == len(data.get("symbols") or []):
            raise ValueError(f"khong tim thay {symbol}")
        if not rows:
            raise ValueError("phai giu lai it nhat mot cap")
        data["symbols"] = rows
        self._write(data)

    # ------------------------------------------------------------ hien thi

    def masked(self) -> dict[str, Any]:
        """Trang thai an toan de gui ra HTTP. Khong chua gia tri secret nao."""
        ex = self.exchange()
        return {
            "auth_configured": self.has_auth(),
            "username": self.username(),
            "password_set": MASK if self.has_auth() else "",
            "exchange_enabled": ex["enabled"],
            "exchange_name": ex["name"],
            "exchange_symbol": ex["symbol"],
            # Chi lo 4 ky tu cuoi cua key de doi chieu voi trang san, du de nhan ra
            # minh dang dung key nao ma khong du de tai su dung.
            "key_hint": (f"...{ex['key'][-4:]}" if len(ex["key"]) > 4
                         else (MASK if ex["key"] else "")),
            "secret_set": MASK if ex["secret"] else "",
            "passphrase_set": MASK if ex["passphrase"] else "",
            "updated_at": self.load().get("updated_at", ""),
        }
