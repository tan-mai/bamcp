# TM - #BOOKMAP - Order flow Bookmap
"""Logic thuan cua order flow: dong bo so lenh, gom bucket, footprint, bong bong, volume profile, CVD,
heatmap, tuong thanh khoan va hap thu. Khong I/O, khong mang - test chay bang du lieu gia lap.

Quy uoc: gia bucket = floor(price / bucket_size) * bucket_size (bien duoi cua bucket). Thoi gian epoch ms UTC.
Lenh khop: (agg_id, ts, price, qty, is_buyer_maker); is_buyer_maker = True la BAN chu dong.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Iterator

EPS = 1e-9


def bucket_index(price: float, size: float) -> int:
    return int(math.floor(price / size + EPS))


def bucket_of(price: float, size: float) -> float:
    return round(bucket_index(price, size) * size, 10)


def round_price(price: float | None, size: float) -> float | None:
    """Lam tron gia hien thi theo so le cua bucket_size (10 -> 0 le, 0.5 -> 1 le, 0.01 -> 2 le)."""
    if price is None:
        return None
    decimals = max(0, -int(math.floor(math.log10(size) + EPS))) if size < 1 else 0
    if size < 1 and abs(round(size * 10 ** decimals) - size * 10 ** decimals) > EPS:
        decimals += 1
    return round(float(price), decimals)


# ---------------------------------------------------------------- so lenh cuc bo

class BookSync:
    """Dong bo so lenh theo dung thuat toan Binance.

    futures: bo event u < lastUpdateId; event dau phai U <= lastUpdateId <= u; moi event sau pu == u truoc.
    spot:    event dau phai U <= lastUpdateId + 1 <= u; moi event sau U == u truoc + 1.
    on_event / on_snapshot tra "buffered" | "ok" | "resync"; "resync" thi nguoi goi ghi gap, reset() roi lay snapshot moi.
    """

    MAX_BUFFER = 20_000

    def __init__(self, market: str = "futures"):
        self.market = market
        self.reset()

    def reset(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.buffer: list[dict[str, Any]] = []
        self.last_update_id: int | None = None
        self.last_u: int | None = None
        self.loaded = False        # da nap snapshot
        self.synced = False        # da ap event dau tien hop le
        self.last_event_ts: int | None = None
        self.reason = ""

    def on_event(self, ev: dict[str, Any]) -> str:
        if not self.loaded:
            self.buffer.append(ev)
            if len(self.buffer) > self.MAX_BUFFER:
                del self.buffer[: len(self.buffer) - self.MAX_BUFFER]
            return "buffered"
        return self._process(ev)

    def on_snapshot(self, last_update_id: int, bids: Iterable[Iterable[Any]], asks: Iterable[Iterable[Any]]) -> str:
        self.bids = {float(p): float(q) for p, q in bids if float(q) > 0}
        self.asks = {float(p): float(q) for p, q in asks if float(q) > 0}
        self.last_update_id = int(last_update_id)
        self.loaded = True
        self.synced = False
        self.last_u = None
        pending, self.buffer = self.buffer, []
        for ev in pending:
            status = self._process(ev)
            if status == "resync":
                return status
        return "ok"

    def _process(self, ev: dict[str, Any]) -> str:
        first_id, final_id = int(ev["U"]), int(ev["u"])
        lid = self.last_update_id
        if not self.synced:
            if self.market == "futures":
                if final_id < lid:
                    return "ok"   # cu hon snapshot: bo
                if not (first_id <= lid <= final_id):
                    self.reason = f"event dau U={first_id} u={final_id} khong phu lastUpdateId={lid}"
                    return "resync"
            else:
                if final_id <= lid:
                    return "ok"
                if not (first_id <= lid + 1 <= final_id):
                    self.reason = f"event dau U={first_id} u={final_id} khong phu lastUpdateId+1={lid + 1}"
                    return "resync"
            self.synced = True
        else:
            if self.market == "futures":
                prev = ev.get("pu")
                if prev is None or int(prev) != self.last_u:
                    self.reason = f"pu={prev} khac u truoc={self.last_u}"
                    return "resync"
            elif first_id != (self.last_u or 0) + 1:
                self.reason = f"U={first_id} khac u truoc + 1 = {(self.last_u or 0) + 1}"
                return "resync"
        self._apply(self.bids, ev.get("b") or [])
        self._apply(self.asks, ev.get("a") or [])
        self.last_u = final_id
        if ev.get("E") is not None:
            self.last_event_ts = int(ev["E"])
        return "ok"

    @staticmethod
    def _apply(side: dict[float, float], levels: Iterable[Iterable[Any]]) -> None:
        for price, qty in levels:
            p, q = float(price), float(qty)
            if q == 0:
                side.pop(p, None)   # xoa muc khong ton tai la binh thuong
            else:
                side[p] = q

    def best(self) -> tuple[float | None, float | None]:
        bid = max(self.bids) if self.bids else None
        ask = min(self.asks) if self.asks else None
        return bid, ask

    def levels(self) -> int:
        return len(self.bids) + len(self.asks)

    def prune(self, mid: float, range_pct: float) -> int:
        """Cat muc ngoai mid +- range_pct (collector goi voi depth_range_pct x 1.5)."""
        low, high = mid * (1 - range_pct), mid * (1 + range_pct)
        drop_b = [p for p in self.bids if p < low or p > high]
        drop_a = [p for p in self.asks if p < low or p > high]
        for p in drop_b:
            del self.bids[p]
        for p in drop_a:
            del self.asks[p]
        return len(drop_b) + len(drop_a)


def bucketize(bids: dict[float, float], asks: dict[float, float], *, mid: float, range_pct: float,
              bucket_size: float) -> dict[str, Any]:
    """Snapshot heatmap: hai mang float lien tiep tu base_price (bucket thap nhat), n_buckets phan tu."""
    low, high = mid * (1 - range_pct), mid * (1 + range_pct)
    base_idx = bucket_index(low, bucket_size)
    n = bucket_index(high, bucket_size) - base_idx + 1
    out_b, out_a = [0.0] * n, [0.0] * n
    for side, out in ((bids, out_b), (asks, out_a)):
        for price, qty in side.items():
            if low <= price <= high:
                i = bucket_index(price, bucket_size) - base_idx
                if 0 <= i < n:
                    out[i] += qty
    return {"base_price": round(base_idx * bucket_size, 10), "bucket_size": bucket_size, "n_buckets": n,
            "bids": out_b, "asks": out_a}


class SnapshotAgg:
    """Gop snapshot theo cua so res_sec (canh theo epoch): trung binh tung bucket tren hop cac dai gia, kem n_snapshots.
    add() tra cac cua so vua dong; close_all() dong cua so dang mo."""

    def __init__(self, res_sec: int):
        self.res_sec = res_sec
        self.res_ms = res_sec * 1000
        self.win: dict[str, Any] | None = None

    def add(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        np = _np()
        start = snap["ts"] - snap["ts"] % self.res_ms
        size = snap["bucket_size"]
        out = []
        if self.win is not None and (self.win["ts"] != start or abs(self.win["size"] - size) > EPS):
            out.append(self._close())
        b = np.asarray(snap["bids"], dtype=np.float64)
        a = np.asarray(snap["asks"], dtype=np.float64)
        lo = bucket_index(snap["base_price"] + size / 2, size)
        hi = lo + len(b)
        if self.win is None:
            self.win = {"ts": start, "size": size, "n": 0, "lo": lo, "b": np.zeros(len(b)), "a": np.zeros(len(b)),
                        "mid": 0.0, "bb": 0.0, "ba": 0.0}
        w = self.win
        w_hi = w["lo"] + len(w["b"])
        if lo < w["lo"] or hi > w_hi:                 # gia troi ra ngoai dai dang gop: noi rong mang
            new_lo, new_hi = min(lo, w["lo"]), max(hi, w_hi)
            pad = (w["lo"] - new_lo, new_hi - w_hi)
            w["b"], w["a"], w["lo"] = np.pad(w["b"], pad), np.pad(w["a"], pad), new_lo
        off = lo - w["lo"]
        w["b"][off: off + len(b)] += b
        w["a"][off: off + len(a)] += a
        w["n"] += 1
        w["mid"] += snap["mid"]
        w["bb"] += snap["best_bid"]
        w["ba"] += snap["best_ask"]
        return out

    def close_all(self) -> list[dict[str, Any]]:
        return [self._close()] if self.win is not None else []

    def _close(self) -> dict[str, Any]:
        w, self.win = self.win, None
        n = w["n"]
        return {"res_sec": self.res_sec, "ts": w["ts"], "n_snapshots": n, "mid": w["mid"] / n,
                "best_bid": w["bb"] / n, "best_ask": w["ba"] / n, "bucket_size": w["size"],
                "base_price": round(w["lo"] * w["size"], 10), "n_buckets": len(w["b"]),
                "bids": (w["b"] / n).tolist(), "asks": (w["a"] / n).tolist()}


# ---------------------------------------------------------------- lenh khop

class Footprint:
    """Cong buy/sell vol va so lenh theo (ts_floor(res), price_bucket). drain() tra phan moi tu lan truoc."""

    def __init__(self) -> None:
        self.cells: dict[tuple[int, int, float], list[float]] = {}

    def add(self, ts: int, price: float, qty: float, sell: bool, res_sec: int, bucket_size: float) -> None:
        res_ms = res_sec * 1000
        key = (res_sec, (ts // res_ms) * res_ms, bucket_of(price, bucket_size))
        cell = self.cells.get(key)
        if cell is None:
            cell = self.cells[key] = [0.0, 0.0, 0, 0]
        if sell:
            cell[1] += qty
            cell[3] += 1
        else:
            cell[0] += qty
            cell[2] += 1

    def drain(self) -> dict[int, list[tuple[int, float, float, float, int, int]]]:
        out: dict[int, list[tuple[int, float, float, float, int, int]]] = defaultdict(list)
        for (res, ts, pb), (bv, sv, bc, sc) in self.cells.items():
            out[res].append((ts, pb, bv, sv, int(bc), int(sc)))
        self.cells = {}
        return out


class BubbleMerger:
    """Gop cac aggTrade CUNG PHIA lien tiep trong bubble_merge_ms (tinh tu lenh dau nhom) thanh mot lenh.
    Gia la VWAP cua nhom. add() / flush() tra cac nhom da dong (chua loc min_qty)."""

    def __init__(self, merge_ms: int):
        self.merge_ms = merge_ms
        self.group: dict[str, Any] | None = None

    def add(self, agg_id: int, ts: int, price: float, qty: float, sell: bool) -> list[dict[str, Any]]:
        side = "SELL" if sell else "BUY"
        g = self.group
        if g is not None and g["side"] == side and ts - g["ts"] <= self.merge_ms:
            g["qty"] += qty
            g["notional"] += price * qty
            g["n_trades"] += 1
            g["last_agg_id"] = agg_id
            return []
        out = [self._close()] if g is not None else []
        self.group = {"ts": ts, "side": side, "qty": qty, "notional": price * qty, "n_trades": 1,
                      "first_agg_id": agg_id, "last_agg_id": agg_id}
        return out

    def flush(self, now_ms: int | None = None) -> list[dict[str, Any]]:
        """Dong nhom dang mo neu da qua cua so (now_ms) hoac dong han (now_ms=None)."""
        if self.group is None:
            return []
        if now_ms is not None and now_ms - self.group["ts"] <= self.merge_ms:
            return []
        return [self._close()]

    def _close(self) -> dict[str, Any]:
        g, self.group = self.group, None
        qty = g.pop("qty")
        notional = g.pop("notional")
        return {**g, "qty": qty, "price": notional / qty if qty else 0.0}


def big_trades_from(trades: Iterable[tuple[int, int, float, float, bool]], merge_ms: int,
                    min_qty: float) -> list[dict[str, Any]]:
    """Ban offline cua BubbleMerger cho backfill va test."""
    merger = BubbleMerger(merge_ms)
    out: list[dict[str, Any]] = []
    for agg_id, ts, price, qty, sell in trades:
        out.extend(g for g in merger.add(agg_id, ts, price, qty, sell) if g["qty"] >= min_qty)
    out.extend(g for g in merger.flush() if g["qty"] >= min_qty)
    return out


# ---------------------------------------------------------------- volume profile

def rebucket_profile(rows: Iterable[tuple[float, float, float]], bucket: float) -> list[dict[str, float]]:
    agg: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for price, buy, sell in rows:
        cell = agg[bucket_index(price, bucket)]
        cell[0] += buy
        cell[1] += sell
    return [{"price": round(i * bucket, 10), "buy": v[0], "sell": v[1], "total": v[0] + v[1]}
            for i, v in sorted(agg.items())]


def value_area(rows: list[dict[str, float]], bucket: float, pct: float = 0.7) -> dict[str, float | None]:
    """POC = bucket co tong khoi luong lon nhat (bang nhau lay bucket thap hon). Value area: bat dau tu POC,
    moi buoc mo rong sang phia co bucket ke tiep lon hon (bang nhau thi len tren) toi khi >= pct tong.
    Tra poc = giua bucket POC, vah = bien tren bucket cao nhat, val = bien duoi bucket thap nhat cua vung."""
    if not rows:
        return {"poc": None, "vah": None, "val": None}
    # Lap day cac bucket trong (khong co lenh) de mo rong vung theo dung thu tu gia
    first, last = bucket_index(rows[0]["price"], bucket), bucket_index(rows[-1]["price"], bucket)
    vols = [0.0] * (last - first + 1)
    for r in rows:
        vols[bucket_index(r["price"], bucket) - first] += r["total"]
    total = sum(vols)
    poc = max(range(len(vols)), key=lambda i: (vols[i], -i))
    lo = hi = poc
    acc = vols[poc]
    target = pct * total
    while acc < target - EPS and (lo > 0 or hi < len(vols) - 1):
        up = vols[hi + 1] if hi + 1 < len(vols) else -1.0
        down = vols[lo - 1] if lo > 0 else -1.0
        if up >= down:
            hi += 1
            acc += up
        else:
            lo -= 1
            acc += down
    return {"poc": round((first + poc) * bucket + bucket / 2, 10), "vah": round((first + hi + 1) * bucket, 10),
            "val": round((first + lo) * bucket, 10)}


def volume_nodes(rows: list[dict[str, float]], top: int = 3) -> dict[str, list[dict[str, float]]]:
    """HVN = dinh cuc bo cua profile (lam muot 3 bucket), LVN = day cuc bo nam giua hai dinh."""
    if len(rows) < 3:
        return {"hvn": [], "lvn": []}
    totals = [r["total"] for r in rows]
    smooth = [sum(totals[max(0, i - 1): i + 2]) / len(totals[max(0, i - 1): i + 2]) for i in range(len(totals))]
    peaks = [i for i in range(1, len(smooth) - 1) if smooth[i] >= smooth[i - 1] and smooth[i] > smooth[i + 1]]
    troughs = [i for i in range(1, len(smooth) - 1) if smooth[i] <= smooth[i - 1] and smooth[i] < smooth[i + 1]]
    if peaks:
        troughs = [i for i in troughs if peaks[0] < i < peaks[-1]]
    hvn = sorted(peaks, key=lambda i: -smooth[i])[:top]
    lvn = sorted(troughs, key=lambda i: smooth[i])[:top]
    pick = lambda i: {"price": rows[i]["price"], "volume": round(rows[i]["total"], 6)}  # noqa: E731
    return {"hvn": [pick(i) for i in hvn], "lvn": [pick(i) for i in lvn]}


# ---------------------------------------------------------------- CVD

def cvd_points(rows: list[tuple[int, float, float, float, float]], *, base: float = 0.0,
               day_anchor: bool = False, day_base: dict[int, float] | None = None) -> list[dict[str, float]]:
    """rows (t, buy, sell, high_bucket, low_bucket) -> [{t, buy, sell, delta, cvd}]. day_anchor: ve 0 dau moi ngay
    UTC; day_base[day_start] la delta tu dau ngay toi moc from (chi ngay dau tien can)."""
    out, cvd, day = [], base, None
    for t, buy, sell, *_ in rows:
        if day_anchor:
            d = t - t % 86_400_000
            if d != day:
                cvd = (day_base or {}).get(d, 0.0) if day is None else 0.0
                day = d
        delta = buy - sell
        cvd += delta
        out.append({"t": t, "buy": buy, "sell": sell, "delta": delta, "cvd": cvd})
    return out


def divergence(points: list[dict[str, float]], highs: list[float], lows: list[float]) -> dict[str, Any]:
    """Phan ky don gian: doan cuoi (1/4 chuoi) gia tao dinh / day moi so voi phan truoc ma CVD khong xac nhan."""
    n = len(points)
    if n < 8:
        return {"type": None, "note": "chuoi qua ngan de xet phan ky (can >= 8 diem)"}
    k = max(2, n // 4)
    cvd = [p["cvd"] for p in points]
    old_h, new_h = max(highs[:-k]), max(highs[-k:])
    old_l, new_l = min(lows[:-k]), min(lows[-k:])
    old_c_h, new_c_h = max(cvd[:-k]), max(cvd[-k:])
    old_c_l, new_c_l = min(cvd[:-k]), min(cvd[-k:])
    if new_h > old_h and new_c_h <= old_c_h:
        return {"type": "bearish", "note": f"gia tao dinh moi ({new_h:g} > {old_h:g}) trong {k} diem cuoi nhung CVD "
                f"khong vuot dinh cu ({new_c_h:.4g} <= {old_c_h:.4g}): luc mua chu dong yeu di"}
    if new_l < old_l and new_c_l >= old_c_l:
        return {"type": "bullish", "note": f"gia tao day moi ({new_l:g} < {old_l:g}) trong {k} diem cuoi nhung CVD "
                f"khong thung day cu ({new_c_l:.4g} >= {old_c_l:.4g}): luc ban chu dong yeu di"}
    return {"type": None, "note": "khong thay phan ky: CVD di cung chieu dinh / day cua gia"}


# ---------------------------------------------------------------- heatmap (numpy)

def _np():
    import numpy  # noqa: PLC0415 - chi server can numpy, collector khong
    return numpy


def heatmap_grid(snaps: Iterable[tuple], *, start: int, end: int, step: int, price_min: float, price_max: float,
                 bucket_size: float, max_rows: int, unpack) -> dict[str, Any]:
    """Downsample snapshot thanh luoi n_cols x n_rows. Moi cot = trung binh cac snapshot trong buoc;
    qua max_rows bucket thi gop k bucket thanh mot (cong so luong). snaps: (ts, base_price, size, n, bids, asks,
    best_bid, best_ask[, weight]); weight = so snapshot ma dong do dai dien (dong gop san 10s / 60s / 300s), mac dinh 1.
    Snapshot co bucket_size khac (doi config) bi bo qua."""
    np = _np()
    n_cols = max(1, int(math.ceil((end - start) / step)))
    raw_rows = bucket_index(price_max, bucket_size) - bucket_index(price_min, bucket_size) + 1
    k = max(1, int(math.ceil(raw_rows / max_rows)))
    out_size = round(bucket_size * k, 10)
    grid_base_idx = bucket_index(price_min, out_size) * k        # theo don vi bucket goc, chia het cho k
    n_rows = (bucket_index(price_max, bucket_size) - grid_base_idx) // k + 1
    acc_b = np.zeros((n_cols, n_rows), dtype=np.float64)
    acc_a = np.zeros((n_cols, n_rows), dtype=np.float64)
    counts = np.zeros(n_cols, dtype=np.int64)
    bbo = np.zeros((n_cols, 2), dtype=np.float64)
    for row in snaps:
        ts, base, size, n, bids, asks, best_bid, best_ask = row[:8]
        weight = row[8] if len(row) > 8 else 1
        if abs(size - bucket_size) > EPS * max(1.0, bucket_size):
            continue
        col = int((ts - start) // step)
        if col < 0 or col >= n_cols:
            continue
        counts[col] += weight
        bbo[col, 0] += best_bid * weight
        bbo[col, 1] += best_ask * weight
        off = bucket_index(base, bucket_size) - grid_base_idx   # bucket goc dau tien cua snapshot trong luoi
        lo, hi = max(0, -off), min(n, n_rows * k - off)          # phan cua snapshot nam trong luoi
        if hi <= lo:
            continue
        b = np.frombuffer(unpack(bids), dtype=np.float32)[lo:hi]
        a = np.frombuffer(unpack(asks), dtype=np.float32)[lo:hi]
        if weight != 1:
            b = b * weight
            a = a * weight
        if k == 1:
            acc_b[col, off + lo: off + hi] += b
            acc_a[col, off + lo: off + hi] += a
        else:
            rows = (np.arange(lo, hi, dtype=np.int64) + off) // k
            acc_b[col] += np.bincount(rows, weights=b, minlength=n_rows)[:n_rows]
            acc_a[col] += np.bincount(rows, weights=a, minlength=n_rows)[:n_rows]
    div = np.maximum(counts, 1)[:, None]
    bids_out = (acc_b / div).astype(np.float32).ravel()
    asks_out = (acc_a / div).astype(np.float32).ravel()
    # Cat 16 bit thap cua mantissa (con ~3 chu so co nghia): mau heatmap khong can hon, payload nen gon hon nhieu
    for arr in (bids_out, asks_out):
        view = arr.view(np.uint32)
        view &= np.uint32(0xFFFF0000)
    filled = counts > 0
    bbo_out = [[round(float(bbo[i, 0] / counts[i]), 10), round(float(bbo[i, 1] / counts[i]), 10)]
               if filled[i] else None for i in range(n_cols)]
    return {"t": [start + i * step for i in range(n_cols)], "step": step, "base_price": round(grid_base_idx * bucket_size, 10),
            "bucket_size": out_size, "n_rows": n_rows, "n_cols": n_cols, "filled": [bool(x) for x in filled],
            "counts": [int(x) for x in counts], "bids": bids_out, "asks": asks_out, "bbo": bbo_out}


# ---------------------------------------------------------------- tuong thanh khoan, hap thu

def _snap_matrix(snaps: list[dict[str, Any]], bucket_size: float):
    """Dua snapshot ve chung mot luoi gia. Tra (ts, mid, base_idx, B, A) voi B/A[t, j] = qty tai bucket base_idx + j."""
    np = _np()
    snaps = [s for s in snaps if abs(s["bucket_size"] - bucket_size) <= EPS * max(1.0, bucket_size)]
    if not snaps:
        return None
    lows = [bucket_index(s["base_price"], bucket_size) for s in snaps]
    highs = [lo + s["n_buckets"] for lo, s in zip(lows, snaps)]
    base, top = min(lows), max(highs)
    B = np.zeros((len(snaps), top - base), dtype=np.float64)
    A = np.zeros_like(B)
    for t, (lo, s) in enumerate(zip(lows, snaps)):
        off = lo - base
        B[t, off: off + s["n_buckets"]] = np.asarray(s["bids"], dtype=np.float64)
        A[t, off: off + s["n_buckets"]] = np.asarray(s["asks"], dtype=np.float64)
    ts = np.array([s["ts"] for s in snaps], dtype=np.int64)
    mid = np.array([s["mid"] for s in snaps], dtype=np.float64)
    return ts, mid, base, B, A


def _runs(index: Any, max_missing: int) -> list[tuple[int, int]]:
    """Chia day chi so tang dan thanh cac doan, cho phep dut toi da max_missing snapshot."""
    out, s0, prev = [], None, None
    for i in index:
        i = int(i)
        if s0 is None:
            s0 = prev = i
        elif i - prev > max_missing + 1:
            out.append((s0, prev))
            s0 = prev = i
        else:
            prev = i
    if s0 is not None:
        out.append((s0, prev))
    return out


def _traded(fp_by_bucket: dict[int, list[tuple[int, float, float]]], j: int, t0: int, t1: int, side: str,
            res_ms: int) -> float:
    """Khoi luong khop vao bucket j trong [t0, t1]: tuong bid bi ban chu dong an (sell_vol), tuong ask bi mua an."""
    total = 0.0
    for ts, buy, sell in fp_by_bucket.get(j, ()):
        if ts + res_ms > t0 and ts <= t1:
            total += sell if side == "BID" else buy
    return total


def detect_walls(snaps: list[dict[str, Any]], fp_rows: Iterable[tuple[int, float, float, float]], *,
                 bucket_size: float, wall_k: float, min_persist_min: float, range_pct: float,
                 include_inactive: bool = False, res_ms: int = 5000, max_missing: int = 2,
                 pull_window_ms: int = 30_000, pull_drop: float = 0.7, near_pct: float = 0.003,
                 pull_traded_max: float = 0.3, consumed_traded_min: float = 0.5) -> list[dict[str, Any]]:
    """Muc 9.1: tuong = bucket qty >= wall_k x median(qty cac bucket cung phia trong range), ton tai lien tuc
    >= min_persist_min phut (dut toi da 2 snapshot). Trang thai ACTIVE / PULLED / CONSUMED / FADED.
    fp_rows: (ts, price_bucket, buy_vol, sell_vol) footprint res nho."""
    np = _np()
    built = _snap_matrix(snaps, bucket_size)
    if built is None:
        return []
    ts, mid, base, B, A = built
    T, N = B.shape
    prices = (np.arange(N) + base) * bucket_size
    in_range = np.abs(prices[None, :] - mid[:, None]) <= (range_pct * mid)[:, None]
    fp: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    for t, pb, buy, sell in fp_rows:
        fp[bucket_index(pb, bucket_size) - base].append((int(t), float(buy), float(sell)))
    last_mid = float(mid[-1])
    out: list[dict[str, Any]] = []
    for side, M in (("BID", B), ("ASK", A)):
        thr = np.full(T, np.inf)
        for t in range(T):
            vals = M[t][in_range[t] & (M[t] > 0)]
            if vals.size >= 3:
                thr[t] = wall_k * float(np.median(vals))
        mask = (M >= thr[:, None]) & in_range & (M > 0)
        for j in np.nonzero(mask.any(axis=0))[0]:
            for i0, i1 in _runs(np.nonzero(mask[:, j])[0], max_missing):
                persisted = (ts[i1] - ts[i0]) / 60000.0
                if persisted + EPS < min_persist_min:
                    continue
                price = float(prices[j])
                active = i1 >= T - 1 - max_missing
                status, detail = "ACTIVE", {}
                if not active:
                    q_before = float(M[max(i0, i1 - 2): i1 + 1, j].mean())
                    win = np.nonzero((ts > ts[i1]) & (ts <= ts[i1] + pull_window_ms))[0]
                    q_fast = float(M[win, j].min()) if win.size else q_before
                    fast = q_before > 0 and (q_before - q_fast) >= pull_drop * q_before
                    t_end = int(ts[win[-1]]) if win.size else int(ts[min(T - 1, i1 + 1)])
                    if not fast:
                        later = np.nonzero((ts > ts[i1]) & (ts <= ts[i1] + 5 * pull_window_ms))[0]
                        q_after = float(M[later, j].min()) if later.size else q_before
                        t_end = int(ts[later[-1]]) if later.size else t_end
                    else:
                        q_after = q_fast
                    decrease = max(0.0, q_before - q_after)
                    traded = _traded(fp, int(j), int(ts[i1]), t_end, side, res_ms)
                    drop_i = int(win[0]) if win.size else min(T - 1, i1 + 1)
                    near = abs(float(mid[drop_i]) - price) / price <= near_pct
                    if decrease > 0 and traded >= consumed_traded_min * decrease:
                        status = "CONSUMED"
                    elif fast and near and traded < pull_traded_max * decrease:
                        status = "PULLED"
                    else:
                        status = "FADED"
                    detail = {"ended_at": int(ts[i1]), "qty_before_drop": round(q_before, 6),
                              "qty_after_drop": round(q_after, 6), "traded_at_level": round(traded, 6)}
                    if not include_inactive:
                        continue
                if abs(price - last_mid) / last_mid > range_pct:
                    continue
                out.append({"price": price, "side": side, "qty_now": round(float(M[T - 1, j]), 6),
                            "qty_avg": round(float(M[i0: i1 + 1, j].mean()), 6), "first_seen": int(ts[i0]),
                            "persisted_min": round(persisted, 1),
                            "distance_pct": round((price - last_mid) / last_mid * 100, 3), "status": status, **detail})
    order = {"ACTIVE": 0, "PULLED": 1, "CONSUMED": 2, "FADED": 3}
    out.sort(key=lambda w: (order[w["status"]], -w["qty_avg"]))
    return out


def detect_absorption(fp_rows: Iterable[tuple[int, float, float, float]], snaps: list[dict[str, Any]], *,
                      bucket_size: float, min_qty: float, start: int, end: int, data_end: int,
                      window_ms: int = 60_000, step_ms: int = 5000, max_displacement: int = 2,
                      keep_ratio: float = 0.5, outcome_ms: int = 900_000) -> list[dict[str, Any]]:
    """Muc 9.2: cua so truot 60s. BULLISH_ABSORPTION: ban chu dong tai vung +-1 bucket >= min_qty, gia thap nhat
    khong xuong qua 2 bucket duoi vung, bid tai vung cuoi cua so >= 50% dau cua so (hoac duoc nap lai).
    BEARISH_ABSORPTION doi xung voi mua chu dong / ask / gia cao nhat."""
    built = _snap_matrix(snaps, bucket_size)
    if built is None:
        raise ValueError("chua co du lieu so lenh (heatmap) trong khoang nay")
    ts_s, _mid, base, B, A = built
    import bisect
    snap_ts = [int(x) for x in ts_s]
    cells: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    times: list[int] = []
    for t, pb, buy, sell in fp_rows:
        t = int(t)
        cells[t].append((bucket_index(pb, bucket_size), float(buy), float(sell)))
    times = sorted(cells)

    def book(at: int, M, j0: int, j1: int) -> float | None:
        i = bisect.bisect_right(snap_ts, at) - 1
        if i < 0:
            return None
        lo, hi = max(0, j0 - base), min(M.shape[1], j1 - base + 1)
        return float(M[i, lo:hi].sum()) if hi > lo else 0.0

    found: list[dict[str, Any]] = []
    for w0 in range(start - start % step_ms, end, step_ms):
        w1 = w0 + window_ms
        lo_i, hi_i = bisect.bisect_left(times, w0), bisect.bisect_left(times, w1)
        if lo_i == hi_i:
            continue
        sells: dict[int, float] = defaultdict(float)
        buys: dict[int, float] = defaultdict(float)
        lo_j, hi_j = None, None
        for t in times[lo_i:hi_i]:
            for j, buy, sell in cells[t]:
                sells[j] += sell
                buys[j] += buy
                lo_j = j if lo_j is None else min(lo_j, j)
                hi_j = j if hi_j is None else max(hi_j, j)
        for label, agg, M in (("BULLISH_ABSORPTION", sells, B), ("BEARISH_ABSORPTION", buys, A)):
            if not agg:
                continue
            zone = {j: agg.get(j - 1, 0.0) + agg.get(j, 0.0) + agg.get(j + 1, 0.0) for j in agg}
            j = max(zone, key=lambda k: (zone[k], -k))
            qty = zone[j]
            if qty < min_qty:
                continue
            if label == "BULLISH_ABSORPTION":
                displacement = max(0, (j - 1) - lo_j)
            else:
                displacement = max(0, hi_j - (j + 1))
            if displacement > max_displacement:
                continue
            before, after = book(w0, M, j - 1, j + 1), book(w1, M, j - 1, j + 1)
            if before is None or after is None or max(before, after) <= 0 or after < keep_ratio * before:
                continue
            found.append({"ts": w1, "j": j, "side_label": label, "aggressor_qty": qty,
                          "price_displacement_buckets": displacement, "wall_qty_before": before,
                          "wall_qty_after": after})
    # Cua so chong nhau tai cung vung (+-1 bucket) la mot su kien: giu cua so manh nhat
    events: list[dict[str, Any]] = []
    for ev in sorted(found, key=lambda e: (e["side_label"], e["ts"])):
        prev = events[-1] if events else None
        if (prev and prev["side_label"] == ev["side_label"] and abs(prev["j"] - ev["j"]) <= 1
                and ev["ts"] - prev["_last"] <= window_ms):
            prev["_last"] = ev["ts"]
            if ev["aggressor_qty"] > prev["aggressor_qty"]:
                ev["_last"] = ev["ts"]
                events[-1] = ev
            continue
        ev["_last"] = ev["ts"]
        events.append(ev)
    out = []
    for ev in sorted(events, key=lambda e: e["ts"]):
        price = round(ev["j"] * bucket_size, 10)
        outcome = None
        if ev["ts"] + outcome_ms <= data_end:
            after = [j for t in times[bisect.bisect_right(times, ev["ts"]): bisect.bisect_right(times, ev["ts"] + outcome_ms)]
                     for j, _b, _s in cells[t]]
            if after:
                high = (max(after) + 1) * bucket_size
                low = min(after) * bucket_size
                center = price + bucket_size / 2
                up, down = high - center, center - low
                fav, adv = (up, down) if ev["side_label"] == "BULLISH_ABSORPTION" else (down, up)
                outcome = {"max_favorable": round(max(0.0, fav), 10), "max_adverse": round(max(0.0, adv), 10)}
        out.append({"ts": ev["ts"], "price": price, "side_label": ev["side_label"],
                    "aggressor_qty": round(ev["aggressor_qty"], 6),
                    "price_displacement_buckets": ev["price_displacement_buckets"],
                    "wall_qty_before": round(ev["wall_qty_before"], 6), "wall_qty_after": round(ev["wall_qty_after"], 6),
                    "outcome_15m": outcome})
    return out


def imbalance(snap: dict[str, Any], pct: float) -> dict[str, float] | None:
    """(bid - ask) / (bid + ask) trong +-pct quanh mid cua mot snapshot da gom bucket."""
    mid, size, base = snap["mid"], snap["bucket_size"], snap["base_price"]
    low, high = mid * (1 - pct), mid * (1 + pct)
    bid = ask = 0.0
    for i, (b, a) in enumerate(zip(snap["bids"], snap["asks"])):
        price = base + i * size
        if price + size > low and price <= high:
            bid += b
            ask += a
    if bid + ask <= 0:
        return None
    return {"bid_qty": round(bid, 6), "ask_qty": round(ask, 6), "imbalance": round((bid - ask) / (bid + ask), 4)}


def iter_chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i: i + size]
