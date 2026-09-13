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
        self._write(data)

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
