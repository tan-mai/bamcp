# TM - #GANN-TW - Gann Time Windows
"""Do phu du lieu nen va loc theo khoang thoi gian. Logic thuan, khong I/O.

Tach rieng khoi server.py de test duoc bang du lieu dung tay, theo dung cach
module orb.py tach logic thuan khoi orb_runtime.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Bao nhieu khoang trong duoc liet ke chi tiet. Nhieu hon nua thi doc khong noi
# ma cung khong giup gi - con so gap_count da du de biet tinh hinh.
MAX_GAPS_LISTED = 20


def parse_time_bound(text: str, tz, *, end: bool = False) -> int | None:
    """'YYYY-MM-DD' hoac 'YYYY-MM-DD HH:MM' -> moc ms. Rong -> None.

    end=True thi 'YYYY-MM-DD' tro thanh cuoi ngay (23:59:59.999) de khoang
    [start, end] bao tron ca ngay cuoi - nguoi dung go den ngay nao thi mong
    doi ngay do nam trong ket qua.
    """
    text = (text or "").strip()
    if not text:
        return None
    for fmt, is_day in (("%Y-%m-%d %H:%M", False), ("%Y-%m-%d", True)):
        try:
            moment = datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
        if end and is_day:
            moment += timedelta(days=1, microseconds=-1)
        return int(moment.timestamp() * 1000)
    raise ValueError(
        f"moc thoi gian khong hop le: {text!r}. Dung 'YYYY-MM-DD' hoac "
        "'YYYY-MM-DD HH:MM'")


def slice_by_time(bars: list[dict[str, Any]], start_ms: int | None,
                  end_ms: int | None) -> list[dict[str, Any]]:
    """Loc nen theo open_time, hai dau inclusive."""
    if start_ms is None and end_ms is None:
        return bars
    out = []
    for bar in bars:
        ts = int(bar.get("open_time") or 0)
        if start_ms is not None and ts < start_ms:
            continue
        if end_ms is not None and ts > end_ms:
            continue
        out.append(bar)
    return out


def find_gaps(bars: list[dict[str, Any]], span_ms: int,
              limit: int = MAX_GAPS_LISTED) -> tuple[int, list[dict[str, Any]]]:
    """Tim cho thieu nen. Tra ve (tong so khoang, chi tiet toi da `limit` khoang).

    Hai nen lien tiep cach nhau hon mot span la co lo hong. Dung dung thuc, mot
    chut sai so cho phep vi moc nen tu san khong phai luc nao cung chan tuyet doi.
    """
    if span_ms <= 0 or len(bars) < 2:
        return 0, []
    tolerance = max(1, span_ms // 100)
    total = 0
    detail: list[dict[str, Any]] = []
    previous = int(bars[0].get("open_time") or 0)
    for bar in bars[1:]:
        current = int(bar.get("open_time") or 0)
        delta = current - previous
        if delta > span_ms + tolerance:
            total += 1
            if len(detail) < limit:
                detail.append({
                    "from_ms": previous,
                    "to_ms": current,
                    "missing_bars": max(1, round(delta / span_ms) - 1),
                })
        previous = current
    return total, detail


def expected_count(first_ms: int, last_ms: int, span_ms: int) -> int:
    """So nen le ra phai co trong khoang first..last neu khong thieu cai nao."""
    if span_ms <= 0 or last_ms < first_ms:
        return 0
    return int((last_ms - first_ms) // span_ms) + 1


def coverage(bars: list[dict[str, Any]], span_ms: int,
             retention: int | None) -> dict[str, Any]:
    """Tom tat do phu cua mot khung. Khong co nen nao thi tra ve khung rong."""
    if not bars:
        return {"count": 0, "first_bar_ms": None, "last_bar_ms": None,
                "expected_count": 0, "gap_count": 0, "gaps": [],
                "retention": retention}
    first_ms = int(bars[0].get("open_time") or 0)
    last_ms = int(bars[-1].get("open_time") or 0)
    gap_count, gaps = find_gaps(bars, span_ms)
    return {
        "count": len(bars),
        "first_bar_ms": first_ms,
        "last_bar_ms": last_ms,
        "expected_count": expected_count(first_ms, last_ms, span_ms),
        "gap_count": gap_count,
        "gaps": gaps,
        "retention": retention,
    }
