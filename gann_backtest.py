# TM - #GANN-TW - Gann Time Windows
"""Backtest walk-forward cho cua so thoi gian. Logic thuan, khong I/O.

Cau hoi duy nhat: nhung ngay nam trong cua so co that su bien dong manh hon,
hay gan buoc ngoat hon, nhung ngay khac khong - va neu co thi co phai do dem
chu ky dung, hay chi vi trung hop.

WALK-FORWARD, KHONG LOOKAHEAD: diem cua ngay t tinh voi as_of = t - lead_days,
chi bang pivot da biet luc do (gann_pivots.live_pivots). Co assert trong code:
pivot nao biet sau as_of thi dung ngay, khong im lang chay tiep.

Ket qua cua ngay t (range_atr, volume_z, pivot_near) DUOC phep dung du lieu
sau t - do la do dau ra, khong phai du bao.

HAI GIA THUYET NULL cho permutation test:
  local  - moi cua so dich ngau nhien +- shift_days. Giu nguyen che do bien dong
           cua tung giai doan, chi xao tron THOI DIEM chinh xac. Day la p_value
           chinh, vi no tra loi dung cau hoi "dem chu ky co trung ngay khong".
  global - dat lai cua so bat ky dau trong ca giai doan. Lan lon voi che do
           bien dong: pivot day dac trong giai doan giat manh, cua so dem tu
           pivot gan day cung day dac theo, ma bien dong thi keo dai - nen
           global co the bao "co y nghia" ca khi thoi diem khong dung gi.
Global nho ma local lon nghia la hieu ung den tu che do bien dong, khong phai
tu Gann.
"""

from __future__ import annotations

import random
import statistics
from datetime import date, timedelta
from typing import Any, Callable

import gann_pivots
import gann_windows

METRICS = ("range_atr", "volume_z", "pivot_near")
PRIMARY = "range_atr"
# volume_z la z-score, trung binh ~0 - chia cho no ra so vo nghia. Voi no chi
# bao delta = in - out. Hai chi so con lai duong nen lift = in / out co nghia.
RATIO_METRICS = ("range_atr", "pivot_near")
# It mau hon muc nay thi khong de xuat doi trong so - lift tu 10 ngay la nhieu.
MIN_SAMPLES = 30
# Thu dat lai mot cua so khong chong len cai khac toi da bay nhieu lan, het
# thi de nguyen cho cu. Giu dung so cua so va do dai - dieu kien cua BR.
PLACE_TRIES = 50


# ---------------------------------------------------------------- ket qua tung ngay

def outcome_metrics(bars: list[dict[str, Any]], bar_dates: list[str],
                    pivot_dates: set[str], cfg: dict[str, Any]
                    ) -> list[dict[str, float | None]]:
    """range_atr, volume_z, pivot_near cho tung nen 1d.

    range_atr  = (high - low) / ATR tinh den ngay t-1 (khong tinh ngay t vao
                 mau so, khong thi ngay bien dong manh tu ha diem chinh no).
    volume_z   = z-score volume so voi volume_lookback ngay TRUOC.
    pivot_near = 1 neu co pivot (bac >= intermediate, theo ban do pivot CUOI
                 CUNG - nhin lai sau) trong +-pivot_near_days.
    """
    atr_period = int(cfg.get("atr_period") or 20)
    lookback = int(cfg.get("volume_lookback") or 20)
    near = int(cfg.get("pivot_near_days") or 2)
    atr = gann_pivots.atr_series(bars, atr_period)
    volumes = [float(b.get("volume") or 0.0) for b in bars]
    out: list[dict[str, float | None]] = []
    for i, bar in enumerate(bars):
        prev_atr = atr[i - 1] if i >= 1 else None
        span = float(bar["high"]) - float(bar["low"])
        range_atr = round(span / prev_atr, 4) if prev_atr else None

        volume_z = None
        if i >= lookback:
            window = volumes[i - lookback:i]
            mean = sum(window) / lookback
            std = statistics.pstdev(window)
            if std > 0:
                volume_z = round((volumes[i] - mean) / std, 4)

        today = date.fromisoformat(bar_dates[i])
        hit = any((today + timedelta(days=k)).isoformat() in pivot_dates
                  for k in range(-near, near + 1))
        out.append({"range_atr": range_atr, "volume_z": volume_z,
                    "pivot_near": 1.0 if hit else 0.0})
    return out


# ---------------------------------------------------------------- walk-forward

def walk_forward(records_1d: list[dict[str, Any]], records_1w: list[dict[str, Any]],
                 days: list[date], lead_days: int, end_of_day_ms: Callable[[date], int],
                 cfg: dict[str, Any], events: list[dict[str, Any]], symbol: str,
                 margin_ms: int, week_span_ms: int) -> list[dict[str, Any]]:
    """Diem cua tung ngay, moi ngay tinh nhu dang dung o cuoi ngay t - lead_days.

    Dung lai dung gann_windows.projections / score_days ma get_time_windows
    dung - tu viet lai mot ban "nhanh hon" la tu tao ra mot thu khac de do.
    Record phai co san khoa 'date' (ngay cua nen, theo mui gio cua he thong).
    """
    tolerance = int((cfg.get("projections") or {}).get("tolerance_days") or 0)
    out = []
    for day in days:
        as_of = day - timedelta(days=int(lead_days))
        as_of_ms = end_of_day_ms(as_of)
        pivots = gann_windows.merge_timeframes(
            gann_pivots.live_pivots(records_1d, as_of_ms),
            gann_pivots.live_pivots(records_1w, as_of_ms),
            margin_ms, week_span_ms)
        for pivot in pivots:
            # 9.4: khong duoc dung pivot xac nhan sau t - lead_days. known_ms la
            # luc nen xac nhan DONG, muon hon confirmed_at - kiem ca hai.
            if (int(pivot["confirmed_at_ms"]) > as_of_ms
                    or int(pivot.get("known_ms") or 0) > as_of_ms):
                raise AssertionError(
                    f"lookahead: ngay {day} (as_of {as_of}) dung pivot {pivot['type']} "
                    f"{pivot.get('date')} biet luc {pivot.get('known_ms')} > {as_of_ms}")
        targets = gann_windows.projections(
            pivots, as_of, day - timedelta(days=tolerance),
            day + timedelta(days=tolerance), cfg, events, symbol)
        hits = gann_windows.score_days(targets, day, day, tolerance).get(day, [])
        out.append({
            "date": day,
            "score": round(sum(h["score"] for h in hits), 2),
            "hits": hits,
            "pivots_known": len(pivots),
        })
    return out


# ---------------------------------------------------------------- doan & thong ke

def runs_from_flags(flags: list[bool]) -> list[tuple[int, int]]:
    """Mang True/False -> cac doan [a, b] lien tiep True (chi so, ca hai dau)."""
    runs = []
    start = None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs


class Series:
    """Tong cong don cua mot chi so, de tinh tong tren mot doan trong O(1).

    Permutation lap hang tram lan, moi lan vai tram doan; quet lai ca chuoi moi
    lan la cham vo ich. Ngay thieu so lieu (None) khong tinh vao tong lan dem.
    """

    def __init__(self, values: list[float | None]):
        self.values = values
        self.sums = [0.0]
        self.counts = [0]
        for value in values:
            self.sums.append(self.sums[-1] + (value if value is not None else 0.0))
            self.counts.append(self.counts[-1] + (1 if value is not None else 0))

    def total(self) -> tuple[float, int]:
        return self.sums[-1], self.counts[-1]

    def over(self, runs: list[tuple[int, int]]) -> tuple[float, int]:
        s = c = 0
        for a, b in runs:
            s += self.sums[b + 1] - self.sums[a]
            c += self.counts[b + 1] - self.counts[a]
        return s, c


def delta(series: Series, runs: list[tuple[int, int]]) -> float | None:
    """Trung binh trong doan tru trung binh ngoai doan - thong ke cua permutation.

    Voi tong va so mau co dinh, delta va lift (in/out) tang giam cung nhau, nen
    dung delta cho moi chi so ma p-value van la p-value cua lift.
    """
    s_in, n_in = series.over(runs)
    s_all, n_all = series.total()
    n_out = n_all - n_in
    if n_in == 0 or n_out == 0:
        return None
    return s_in / n_in - (s_all - s_in) / n_out


def describe(series: Series, runs: list[tuple[int, int]], metric: str) -> dict[str, Any]:
    """Trung binh, trung vi, lift (hoac delta), so mau - cho ket qua quan sat."""
    inside = [False] * len(series.values)
    for a, b in runs:
        for i in range(a, b + 1):
            inside[i] = True
    vin = [v for v, f in zip(series.values, inside) if f and v is not None]
    vout = [v for v, f in zip(series.values, inside) if not f and v is not None]
    if not vin or not vout:
        return {"n_in": len(vin), "n_out": len(vout), "lift": None, "delta": None}
    mean_in, mean_out = sum(vin) / len(vin), sum(vout) / len(vout)
    row = {
        "n_in": len(vin), "n_out": len(vout),
        "mean_in": round(mean_in, 4), "mean_out": round(mean_out, 4),
        "median_in": round(statistics.median(vin), 4),
        "median_out": round(statistics.median(vout), 4),
        "delta": round(mean_in - mean_out, 4),
        "lift": None,
    }
    if metric in RATIO_METRICS and mean_out > 0:
        row["lift"] = round(mean_in / mean_out, 4)
    return row


def place_local(runs: list[tuple[int, int]], size: int, shift: int,
                rng: random.Random) -> list[tuple[int, int]]:
    """Moi doan dich ngau nhien +-shift, khong chong len nhau, giu do dai.

    Dat theo thu tu ngau nhien; doan nao thu PLACE_TRIES lan van chong thi
    nam yen cho cu - so doan va do dai luon dung nhu ban dau.
    """
    order = list(range(len(runs)))
    rng.shuffle(order)
    taken = [False] * size
    placed: list[tuple[int, int] | None] = [None] * len(runs)

    def free(a: int, b: int) -> bool:
        return 0 <= a and b < size and not any(taken[a:b + 1])

    for idx in order:
        a, b = runs[idx]
        length = b - a
        spot = None
        for _ in range(PLACE_TRIES):
            offset = rng.randint(-shift, shift)
            na = min(max(0, a + offset), size - 1 - length)
            if free(na, na + length):
                spot = (na, na + length)
                break
        if spot is None:
            # Het luot thu: lan ra hai phia tu cho cu, lay cho trong gan nhat.
            # Quet tu dau chuoi thi cac doan bi don ve dau giai doan - lech.
            for distance in range(0, size):
                for na in (a - distance, a + distance):
                    if free(na, na + length):
                        spot = (na, na + length)
                        break
                if spot is not None:
                    break
        if spot is None:
            continue
        for i in range(spot[0], spot[1] + 1):
            taken[i] = True
        placed[idx] = spot
    return sorted(p for p in placed if p is not None)


def place_global(runs: list[tuple[int, int]], size: int,
                 rng: random.Random) -> list[tuple[int, int]]:
    """Dat lai cac doan bat ky dau trong ca giai doan, giu so doan va do dai.

    Chia so ngay trong thanh len(runs)+1 khoang ngau nhien (stars and bars),
    roi xep cac doan theo thu tu xao tron vao giua.
    """
    lengths = [b - a + 1 for a, b in runs]
    rng.shuffle(lengths)
    free = size - sum(lengths)
    if free < 0 or not lengths:
        return list(runs)
    cuts = sorted(rng.sample(range(free + len(lengths)), len(lengths)))
    out = []
    pos = 0
    prev = -1
    for length, cut in zip(lengths, cuts):
        pos += cut - prev - 1
        out.append((pos, pos + length - 1))
        pos += length
        prev = cut
    return out


def p_value(series: Series, runs: list[tuple[int, int]], observed: float | None,
            permutations: int, rng: random.Random, null: str,
            shift: int) -> float | None:
    """P(delta ngau nhien >= delta quan sat), mot phia: gia thuyet la cua so
    bien dong MANH HON, khong phai khac di. Cong 1 ca tu va mau de khong bao
    gio ra p = 0 voi so lan thu huu han.
    """
    if observed is None or not runs or permutations <= 0:
        return None
    size = len(series.values)
    hits = 0
    for _ in range(permutations):
        moved = (place_local(runs, size, shift, rng) if null == "local"
                 else place_global(runs, size, rng))
        value = delta(series, moved)
        if value is not None and value >= observed:
            hits += 1
    return round((hits + 1) / (permutations + 1), 4)


# ---------------------------------------------------------------- tong hop

def _touch_runs(scored: list[dict[str, Any]],
                key: Callable[[dict[str, Any]], Any]) -> dict[Any, list[tuple[int, int]]]:
    """Nhom ngay theo loai chieu (hoac theo cycle_days) cham vao ngay do."""
    groups: dict[Any, list[bool]] = {}
    for i, row in enumerate(scored):
        for hit in row["hits"]:
            name = key(hit)
            if name is None:
                continue
            flags = groups.setdefault(name, [False] * len(scored))
            flags[i] = True
    return {name: runs_from_flags(flags) for name, flags in groups.items()}


def _suggest(current: float, row: dict[str, Any], p: float | None) -> dict[str, Any]:
    """De xuat trong so tu lift cua chi so chinh. KHONG tu ghi vao config.

    Quy tac co y don gian de doc lai duoc:
      it mau (< MIN_SAMPLES)       -> giu nguyen
      p >= 0.10 (khong khac ngau nhien) -> giam mot nua
      p < 0.10 va lift > 1          -> nhan voi lift (toi da x2)
      con lai (lift <= 1)           -> ve 0
    """
    lift = row.get("lift")
    if row.get("n_in", 0) < MIN_SAMPLES or lift is None:
        return {"current": current, "suggested": current,
                "reason": f"it mau ({row.get('n_in', 0)} ngay) - giu nguyen"}
    if p is None or p >= 0.10:
        return {"current": current, "suggested": round(current * 0.5, 2),
                "reason": f"khong khac ngau nhien (p={p}) - giam mot nua"}
    if lift > 1:
        return {"current": current, "suggested": round(current * min(lift, 2.0), 2),
                "reason": f"lift {lift}, p={p}"}
    return {"current": current, "suggested": 0.0,
            "reason": f"lift {lift} <= 1 du p={p} - khong giup gi"}


def run(scored: list[dict[str, Any]], outcomes: list[dict[str, Any]],
        cfg: dict[str, Any], permutations: int, seed: int, shift_days: int,
        excluded: list[bool] | None = None) -> dict[str, Any]:
    """Tu diem tung ngay + ket qua tung ngay -> bao cao lift, p-value, de xuat.

    excluded[i] = True thi ngay i bi bo khoi ca hai phia trong/ngoai (dung cho
    tuy chon loai tru ngay co su kien).
    """
    scoring = cfg.get("scoring") or {}
    projection_cfg = cfg.get("projections") or {}
    threshold = float(scoring.get("min_score") or 0.0)
    gap = int(scoring.get("merge_gap_days") or 0)
    size = len(scored)
    skip = excluded or [False] * size

    series = {m: Series([None if skip[i] else outcomes[i][m] for i in range(size)])
              for m in METRICS}

    # Cua so: dung lai merge_windows cua tool that, roi doi ngay -> chi so
    day_hits = {row["date"]: row["hits"] for row in scored if row["hits"]}
    index_of = {row["date"].isoformat(): i for i, row in enumerate(scored)}
    windows = gann_windows.merge_windows(day_hits, threshold, gap, 1)
    win_runs = sorted((index_of[w["from"]], index_of[w["to"]]) for w in windows
                      if w["from"] in index_of and w["to"] in index_of)

    rng = random.Random(int(seed))
    overall: dict[str, Any] = {}
    for metric in METRICS:
        row = describe(series[metric], win_runs, metric)
        observed = delta(series[metric], win_runs)
        row["p_value"] = p_value(series[metric], win_runs, observed, permutations,
                                 rng, "local", shift_days)
        row["p_value_global"] = p_value(series[metric], win_runs, observed,
                                        permutations, rng, "global", shift_days)
        overall[metric] = row

    # Tung loai chieu va tung cycle_days, do RIENG tung cai: ngay nao loai do
    # cham vao (bat ke tong diem co qua nguong hay khong). Do qua cua so thi
    # vong lap - cua so phu thuoc chinh cai trong so dang can hieu chinh.
    by_type = {}
    for name, runs in sorted(_touch_runs(scored, lambda h: h.get("type")).items()):
        row = describe(series[PRIMARY], runs, PRIMARY)
        row["p_value"] = p_value(series[PRIMARY], runs, delta(series[PRIMARY], runs),
                                 permutations, rng, "local", shift_days)
        row["days_touched"] = sum(b - a + 1 for a, b in runs)
        by_type[name] = row

    by_cycle = {}
    cycle_runs = _touch_runs(scored, lambda h: h.get("days") if h.get("type") == "cycle"
                             else None)
    for days in sorted(cycle_runs):
        runs = cycle_runs[days]
        row = describe(series[PRIMARY], runs, PRIMARY)
        row["p_value"] = p_value(series[PRIMARY], runs, delta(series[PRIMARY], runs),
                                 permutations, rng, "local", shift_days)
        row["days_touched"] = sum(b - a + 1 for a, b in runs)
        by_cycle[str(days)] = row

    suggested_types = {}
    for name, row in by_type.items():
        current = float((projection_cfg.get(name) or {}).get("weight") or 0.0)
        suggested_types[name] = _suggest(current, row, row.get("p_value"))
    cycle_weights = (projection_cfg.get("cycle") or {}).get("days") or {}
    suggested_cycles = {}
    for days, row in by_cycle.items():
        current = float(cycle_weights.get(int(days), cycle_weights.get(days, 0.0)) or 0.0)
        suggested_cycles[days] = _suggest(current, row, row.get("p_value"))

    in_days = sum(b - a + 1 for a, b in win_runs)
    return {
        "days": size,
        "days_excluded": sum(1 for f in skip if f),
        "windows": len(win_runs),
        "days_in_windows": in_days,
        "coverage_pct": round(in_days / size * 100, 2) if size else None,
        "primary_metric": PRIMARY,
        "overall": overall,
        "by_type": by_type,
        "by_cycle_days": by_cycle,
        "suggested_weights": {
            "note": ("De xuat tu lift cua range_atr, KHONG tu ghi vao config. Nhieu "
                     "phep kiem cung luc (moi loai, moi cycle_days) thi vai cai p < 0.05 "
                     "co the chi la ngau nhien - dung so mau va p_value_global de can."),
            "types": suggested_types,
            "cycle_days": suggested_cycles,
        },
        "seed": int(seed),
        "permutations": int(permutations),
        "shift_days": int(shift_days),
        "_window_runs": win_runs,
    }
