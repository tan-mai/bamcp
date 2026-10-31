# TM - #M5-FETCH - Per-timeframe schedules + M5
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

_MONTHS = {n: i for i, n in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
_DAYS = {n: i for i, n in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))}
_FIELDS = (
    ("giay", 0, 59, {}),
    ("phut", 0, 59, {}),
    ("gio", 0, 23, {}),
    ("ngay", 1, 31, {}),
    ("thang", 1, 12, _MONTHS),
    ("thu", 0, 7, _DAYS),
)
_WEEK_MS = 7 * 86_400_000
_MONDAY_MS = 4 * 86_400_000
_HORIZON_DAYS = 366 * 5


@dataclass(frozen=True)
class Cron:
    expr: str
    seconds: frozenset[int]
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    any_day: bool
    any_weekday: bool


def _value(text: str, low: int, high: int, names: dict[str, int], label: str) -> int:
    token = text.strip().upper()
    if token in names:
        return names[token]
    if not token.isdigit():
        raise ValueError(f"truong {label}: '{text}' khong phai so")
    value = int(token)
    if not low <= value <= high:
        raise ValueError(f"truong {label}: {value} ngoai khoang {low}-{high}")
    return value


def _field(text: str, low: int, high: int, names: dict[str, int], label: str) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"truong {label}: co muc rong")
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) <= 0:
                raise ValueError(f"truong {label}: buoc '{step_text}' khong hop le")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = _value(a, low, high, names, label), _value(b, low, high, names, label)
            if start > end:
                raise ValueError(f"truong {label}: khoang '{base}' nguoc")
        else:
            start = _value(base, low, high, names, label)
            end = high if step_text else start
        out.update(range(start, end + 1, step))
    return out


def parse(expr: str) -> Cron:
    parts = (expr or "").split()
    if len(parts) != 6:
        raise ValueError("cron phai co 6 truong: giay phut gio ngay thang thu (gio UTC)")
    values = [_field(p, lo, hi, names, label) for p, (label, lo, hi, names) in zip(parts, _FIELDS)]
    weekdays = {0 if d == 7 else d for d in values[5]}
    return Cron(" ".join(parts), frozenset(values[0]), frozenset(values[1]), frozenset(values[2]),
                frozenset(values[3]), frozenset(values[4]), frozenset(weekdays),
                parts[3] == "*", parts[5] == "*")


def _day_ok(spec: Cron, t: datetime) -> bool:
    dom = t.day in spec.days
    dow = ((t.weekday() + 1) % 7) in spec.weekdays
    if spec.any_day and spec.any_weekday:
        return True
    if spec.any_day:
        return dow
    if spec.any_weekday:
        return dom
    return dom or dow


def next_after(spec: Cron, after_ms: int) -> int:
    t = datetime.fromtimestamp(after_ms // 1000 + 1, UTC)
    limit = t + timedelta(days=_HORIZON_DAYS)
    while t < limit:
        if t.month not in spec.months:
            t = (t.replace(day=1, hour=0, minute=0, second=0) + timedelta(days=32)).replace(day=1)
            continue
        if not _day_ok(spec, t):
            t = t.replace(hour=0, minute=0, second=0) + timedelta(days=1)
            continue
        if t.hour not in spec.hours:
            t = t.replace(minute=0, second=0) + timedelta(hours=1)
            continue
        if t.minute not in spec.minutes:
            t = t.replace(second=0) + timedelta(minutes=1)
            continue
        later = [s for s in spec.seconds if s >= t.second]
        if not later:
            t = t.replace(second=0) + timedelta(minutes=1)
            continue
        return int(t.replace(second=min(later)).timestamp() * 1000)
    raise ValueError(f"cron '{spec.expr}' khong co lan chay nao trong {_HORIZON_DAYS} ngay toi")


def next_runs(spec: Cron, after_ms: int, count: int) -> list[int]:
    out: list[int] = []
    at = after_ms
    for _ in range(max(0, count)):
        at = next_after(spec, at)
        out.append(at)
    return out


def candle_open(at_ms: int, span_ms: int) -> int:
    if span_ms == _WEEK_MS:
        return (at_ms - _MONDAY_MS) // _WEEK_MS * _WEEK_MS + _MONDAY_MS
    return at_ms // span_ms * span_ms


def warnings(spec: Cron, span_ms: int, after_ms: int, *, sample: int = 24,
             tolerance_ms: int = 60_000) -> list[str]:
    if span_ms <= 0:
        return []
    runs = next_runs(spec, after_ms, sample)
    out: list[str] = []
    off = [r - candle_open(r, span_ms) for r in runs]
    if any(o > tolerance_ms for o in off):
        worst = max(off) // 1000
        out.append(f"cron khong bam moc dong nen cua khung: co lan chay tre {worst}s sau moc dong nen")
    gaps = [b - a for a, b in zip(runs, runs[1:])]
    if gaps and min(gaps) < span_ms:
        out.append(f"cron chay day hon can: cach nhau {min(gaps) // 1000}s, "
                   f"nen cua khung dai {span_ms // 1000}s")
    return out
