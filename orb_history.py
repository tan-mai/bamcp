"""Du lieu M5 lich su cho backtest ORB, tai tu data.binance.vision.

# TM - #ORB - ORB Enhancement

Tai MOT LAN roi cap nhat theo thang (BR-03). Khong dung M1.

  - Thang da xong: file zip theo thang  .../monthly/klines/<SYM>/5m/<SYM>-5m-YYYY-MM.zip
  - Thang dang chay: file zip theo ngay .../daily/klines/<SYM>/5m/<SYM>-5m-YYYY-MM-DD.zip
    (Binance chi dang file thang vao dau thang sau, nen thang hien tai phai ghep tu
    file ngay.) Khi file thang da co, file ngay cua thang do bi xoa.

Moi file zip deu kiem SHA256 voi file .CHECKSUM di kem truoc khi luu. Luu duoi
dang CSV da chuan hoa (open_time,open,high,low,close,volume,close_time - ms UTC)
de doc nhanh va khong phu thuoc dinh dang goc: file cua Binance luc co header luc
khong, spot tu 2025 doi sang micro giay.

Module nay dong bo (sync) - server goi qua asyncio.to_thread de khong chan vong
lap su kien.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx2 as httpx

BASE_URL = "https://data.binance.vision/data"
INTERVAL = "5m"
M5_MS = 5 * 60_000
UTC = timezone.utc


def _market_path(market: str) -> str:
    return "spot" if market == "spot" else "futures/um"


def month_url(symbol: str, month: str, market: str = "futures") -> str:
    return (f"{BASE_URL}/{_market_path(market)}/monthly/klines/{symbol}/{INTERVAL}/"
            f"{symbol}-{INTERVAL}-{month}.zip")


def day_url(symbol: str, day: str, market: str = "futures") -> str:
    return (f"{BASE_URL}/{_market_path(market)}/daily/klines/{symbol}/{INTERVAL}/"
            f"{symbol}-{INTERVAL}-{day}.zip")


def _to_ms(value: str) -> int:
    ts = int(float(value))
    # Spot tu 2025-01-01 dung micro giay; futures dung mili giay
    if ts > 100_000_000_000_000:
        ts //= 1000
    return ts


def parse_zip(payload: bytes) -> list[list[Any]]:
    """Doc CSV trong zip Binance -> [[open_time, o, h, l, c, v, close_time], ...]."""
    rows: list[list[Any]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            with zf.open(name) as fh:
                reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
                for row in reader:
                    if not row or not row[0].strip().lstrip("-").isdigit():
                        continue            # header (file moi co, file cu khong)
                    rows.append([_to_ms(row[0]), float(row[1]), float(row[2]),
                                 float(row[3]), float(row[4]), float(row[5]),
                                 _to_ms(row[6])])
    rows.sort(key=lambda r: r[0])
    return rows


class HistoryStore:
    """Kho M5 lich su cua MOT thu muc goc (moi cap mot thu muc con)."""

    def __init__(self, root: Path, market: str = "futures",
                 log: Callable[..., None] | None = None, timeout: float = 60.0):
        self.root = Path(root)
        self.market = market
        self.log = log or (lambda *a, **k: None)
        self.timeout = timeout

    def dir(self, symbol: str) -> Path:
        return self.root / symbol.upper() / INTERVAL

    def _month_file(self, symbol: str, month: str) -> Path:
        return self.dir(symbol) / f"{symbol}-{INTERVAL}-{month}.csv"

    def _day_file(self, symbol: str, day: str) -> Path:
        return self.dir(symbol) / f"{symbol}-{INTERVAL}-{day}.csv"

    # ------------------------------------------------------------ tai

    def _download(self, client: httpx.Client, url: str) -> bytes | None:
        """None = file chua co tren data.binance.vision (404)."""
        resp = client.get(url, timeout=self.timeout)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.content
        check = client.get(url + ".CHECKSUM", timeout=self.timeout)
        if check.status_code == 200 and check.text.strip():
            expected = check.text.split()[0].strip().lower()
            actual = hashlib.sha256(payload).hexdigest()
            if expected != actual:
                raise ValueError(f"sai checksum {url}: {actual} != {expected}")
        return payload

    @staticmethod
    def _write_csv(path: Path, rows: list[list[Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["open_time", "open", "high", "low", "close", "volume", "close_time"])
            writer.writerows(rows)
        tmp.replace(path)

    def ensure(self, symbol: str, start: date, end: date,
               today: date | None = None) -> dict[str, Any]:
        """Bao dam co du M5 tu `start` den `end` (ngay UTC). Chi tai phan con thieu.

        Thang da xong ma chua co file thang -> tai file thang (xoa file ngay cua
        thang do neu co). Thang hien tai -> tai tung file ngay con thieu, toi hom qua.
        """
        symbol = symbol.upper()
        today = today or datetime.now(UTC).date()
        end = min(end, today - timedelta(days=1))
        report: dict[str, Any] = {"symbol": symbol, "downloaded": [], "missing_remote": [],
                                  "errors": [], "from": start.isoformat(), "to": end.isoformat()}
        if end < start:
            return report
        current_month = today.strftime("%Y-%m")
        with httpx.Client(follow_redirects=True) as client:
            month = date(start.year, start.month, 1)
            while month <= end:
                label = month.strftime("%Y-%m")
                target = self._month_file(symbol, label)
                if label != current_month and not target.exists():
                    try:
                        payload = self._download(client, month_url(symbol, label, self.market))
                        if payload is None:
                            report["missing_remote"].append(label)
                        else:
                            self._write_csv(target, parse_zip(payload))
                            report["downloaded"].append(label)
                            for stale in self.dir(symbol).glob(f"{symbol}-{INTERVAL}-{label}-*.csv"):
                                stale.unlink(missing_ok=True)
                    except Exception as exc:
                        report["errors"].append(f"{label}: {type(exc).__name__}: {exc}")
                # Thang chua co file thang (dang chay, hoac Binance chua dang):
                # ghep tu file ngay
                if not target.exists():
                    day = max(month, start)
                    last = min(end, _month_end(month))
                    while day <= last:
                        label_day = day.isoformat()
                        day_target = self._day_file(symbol, label_day)
                        if not day_target.exists():
                            try:
                                payload = self._download(
                                    client, day_url(symbol, label_day, self.market))
                                if payload is None:
                                    report["missing_remote"].append(label_day)
                                else:
                                    self._write_csv(day_target, parse_zip(payload))
                                    report["downloaded"].append(label_day)
                            except Exception as exc:
                                report["errors"].append(
                                    f"{label_day}: {type(exc).__name__}: {exc}")
                        day += timedelta(days=1)
                month = _next_month(month)
        self.log("history_update", symbol=symbol, downloaded=len(report["downloaded"]),
                 missing_remote=report["missing_remote"][:10], errors=report["errors"][:5])
        return report

    def update(self, symbol: str, years: int, today: date | None = None) -> dict[str, Any]:
        """Cap nhat dinh ky: giu du `years` nam tinh den hom qua."""
        today = today or datetime.now(UTC).date()
        start = date(today.year - int(years), today.month, 1)
        return self.ensure(symbol, start, today - timedelta(days=1), today=today)

    # ------------------------------------------------------------ doc

    def files(self, symbol: str) -> list[Path]:
        folder = self.dir(symbol)
        if not folder.exists():
            return []
        return sorted(folder.glob(f"{symbol.upper()}-{INTERVAL}-*.csv"))

    def load(self, symbol: str, start_ms: int | None = None,
             end_ms: int | None = None) -> list[dict[str, Any]]:
        """Nen M5 trong [start_ms, end_ms). Chi mo file giao voi khoang can doc."""
        out: dict[int, dict[str, Any]] = {}
        for path in self.files(symbol):
            lo, hi = _file_span(path.stem)
            if lo is None:
                continue
            if (end_ms is not None and lo >= end_ms) or (start_ms is not None and hi <= start_ms):
                continue
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                next(reader, None)
                for row in reader:
                    if not row:
                        continue
                    ot = int(row[0])
                    if start_ms is not None and ot < start_ms:
                        continue
                    if end_ms is not None and ot >= end_ms:
                        continue
                    out[ot] = {"open_time": ot, "open": float(row[1]), "high": float(row[2]),
                               "low": float(row[3]), "close": float(row[4]),
                               "volume": float(row[5]), "close_time": int(row[6])}
        return [out[k] for k in sorted(out)]

    def status(self, symbol: str) -> dict[str, Any]:
        files = self.files(symbol)
        months = [p.stem.rsplit("-", 2)[-2] + "-" + p.stem.rsplit("-", 1)[-1]
                  for p in files if len(p.stem.split("-")) == 4]
        days = [p.stem.split(f"-{INTERVAL}-", 1)[1] for p in files
                if len(p.stem.split("-")) == 5]
        info: dict[str, Any] = {
            "symbol": symbol.upper(),
            "path": str(self.dir(symbol)),
            "exists": bool(files),
            "months": months,
            "daily_files": days,
            "first_month": months[0] if months else None,
            "last_month": months[-1] if months else None,
            "last_day": days[-1] if days else None,
        }
        if files:
            newest = max(files, key=lambda p: p.stat().st_mtime)
            info["updated_at_utc"] = datetime.fromtimestamp(
                newest.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return info


def _month_end(month: date) -> date:
    return _next_month(month) - timedelta(days=1)


def _next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def _file_span(stem: str) -> tuple[int | None, int | None]:
    """Khoang thoi gian (ms UTC) ma mot file CSV bao phu, suy tu ten file."""
    label = stem.split(f"-{INTERVAL}-", 1)[-1]
    try:
        if len(label) == 7:
            start = datetime.strptime(label, "%Y-%m").replace(tzinfo=UTC)
            end = datetime.combine(_next_month(start.date()), datetime.min.time(), UTC)
        else:
            start = datetime.strptime(label, "%Y-%m-%d").replace(tzinfo=UTC)
            end = start + timedelta(days=1)
    except ValueError:
        return None, None
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
