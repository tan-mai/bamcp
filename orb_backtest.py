"""Backtest ORB tren M5 lich su.

# TM - #ORB - ORB Enhancement

Engine (simulate) la ham thuan: cung chuoi nen + cung config -> cung ket qua.
Lop BacktestManager lo phan I/O: tai M5 lich su neu thieu, chay engine trong
thread rieng, ghi CSV + result.json, va tra `status = running` neu qua lau.

Gia dinh mo phong (ghi ra trong ket qua de doc lai khong phai doan):
  - OR = gop 3 nen M5 dau phien (giong het nen M15 cua Binance ve High/Low).
  - ATR(period) H1 tinh tren 200 nen H1 gop tu M5, dong truoc gio mo - giong live.
  - close / retest: vao lenh o gia MO cua nen M5 ngay sau nen tin hieu.
    touch: vao lenh tai muc gia cham, ngay trong nen tin hieu; SL/TP xet tu chinh
    nen do (bao thu: SL cham trong nen do la thua).
  - SL va TP cung cham trong mot nen -> tinh SL (BR-09), dem vao sl_tp_same_bar.
  - Doi SL ve hoa von: co hieu luc tu nen SAU nen cham nguong.
  - Time exit: dong cua o gia dong cua cua nen M5 cham moc time exit.
  - Truot gia lam xau MOI lan khop (vao va ra); phi taker tinh ca hai chieu.
  - Size = risk_usd / khoang cach SL, lam tron xuong qty_step; vuot max_margin
    thi ha qty; qty = 0 hoac duoi min notional -> lenh khong vao duoc.
  - Moi phien toi da max_trades lenh/ngay (lenh sau chi xet sau khi lenh truoc
    dong); tat ca phien toi da max_orb_trades_per_day lenh theo or_date.
  - Bo loc bias KHONG ap dung (bias la nhan dinh tay moi ngay, khong co lich su).
"""

from __future__ import annotations

import asyncio
import csv
import json
import secrets
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import orb
from orb import H1_MS, M5_MS, iso_utc

UTC = timezone.utc
ATR_WARMUP_DAYS = 10         # 200 nen H1 ~ 8,3 ngay
DEFAULT_SPLIT = 0.7          # khong truyen split_date -> 70% dau la in-sample
SUMMARY_KEYS = ("trades", "win_rate", "avg_win_r", "avg_loss_r", "expectancy_r",
                "profit_factor", "max_drawdown_usd", "max_drawdown_pct",
                "max_losing_streak", "final_equity", "total_fees_usd")


class Market:
    """Chuoi M5 da sap xep + chuoi H1 gop tu M5, tra cuu theo moc thoi gian."""

    def __init__(self, bars: list[dict[str, Any]]):
        self.bars = bars
        self.pos = {b["open_time"]: i for i, b in enumerate(bars)}
        self.h1 = orb.aggregate(bars, M5_MS, H1_MS)
        self.h1_pos = {b["open_time"]: i for i, b in enumerate(self.h1)}

    def run(self, start: int, end: int) -> list[dict[str, Any]]:
        """Nen M5 lien tuc tu `start`, dung o cho thieu dau tien hoac khi cham `end`."""
        i = self.pos.get(start)
        out: list[dict[str, Any]] = []
        if i is None:
            return out
        expect = start
        while i < len(self.bars) and expect < end:
            bar = self.bars[i]
            if bar["open_time"] != expect:
                break
            out.append(bar)
            i += 1
            expect += M5_MS
        return out

    def atr(self, open_ms: int, period: int) -> float | None:
        last_open = (open_ms // H1_MS) * H1_MS - H1_MS
        j = self.h1_pos.get(last_open)
        if j is None or j < orb.ATR_WINDOW - 1:
            return None
        first = self.h1[j - orb.ATR_WINDOW + 1]
        if first["open_time"] != last_open - (orb.ATR_WINDOW - 1) * H1_MS:
            return None              # co lo trong 200 nen H1 -> khong tinh
        return orb.atr_wilder(self.h1[j - orb.ATR_WINDOW + 1:j + 1], period)


# ---------------------------------------------------------------- mo phong lenh

def _fill(price: float, direction: str, side: str, slip: float) -> float:
    """Gia khop sau truot gia. side = entry | exit. Luon lam xau di."""
    worse_up = (direction == "long") == (side == "entry")
    return price * (1 + slip) if worse_up else price * (1 - slip)


def _walk(market: Market, start_idx: int, direction: str, entry_fill: float, sl: float,
          tp: float, r_distance: float, params: dict[str, Any], time_exit_ms: int
          ) -> dict[str, Any] | None:
    """Di tung nen tu start_idx cho toi khi dong lenh."""
    bars = market.bars
    long = direction == "long"
    be_r = float(params["move_sl_to_be_at_r"])
    be_level = (entry_fill + (1 if long else -1) * be_r * r_distance) if be_r > 0 else None
    stop, be_armed, be_active = sl, False, False
    last = None
    i = start_idx
    expect = bars[i]["open_time"] if i < len(bars) else None
    while i < len(bars):
        bar = bars[i]
        if bar["open_time"] != expect:
            break                                   # lo du lieu giua lenh
        if be_armed and not be_active:
            stop, be_active = entry_fill, True
        hit_sl = bar["low"] <= stop if long else bar["high"] >= stop
        hit_tp = bar["high"] >= tp if long else bar["low"] <= tp
        if hit_sl:
            # Nen mo vuot qua SL (gap) thi khop o gia mo - con te hon SL
            price = min(stop, bar["open"]) if long else max(stop, bar["open"])
            return {"exit_ref": price, "reason": "breakeven" if be_active else "sl",
                    "same_bar": hit_tp, "bar": bar, "be_moved": be_active}
        if hit_tp:
            return {"exit_ref": tp, "reason": "tp", "same_bar": False, "bar": bar,
                    "be_moved": be_active}
        if be_level is not None and not be_armed:
            if (bar["high"] >= be_level) if long else (bar["low"] <= be_level):
                be_armed = True                     # hieu luc tu nen sau
        if bar["open_time"] + M5_MS >= time_exit_ms:
            return {"exit_ref": bar["close"], "reason": "time", "same_bar": False,
                    "bar": bar, "be_moved": be_active}
        last = bar
        i += 1
        expect += M5_MS
    if last is None:
        return None
    return {"exit_ref": last["close"], "reason": "data_end", "same_bar": False,
            "bar": last, "be_moved": be_active}


def _trade(market: Market, session: dict[str, Any], day: date, params: dict[str, Any],
           rng: dict[str, Any], window: list[dict[str, Any]], event: dict[str, Any],
           counters: Counter) -> dict[str, Any] | None:
    trigger = window[event["trigger_index"]]
    trigger_close = trigger["open_time"] + M5_MS
    direction = event["direction"]
    touch = params["entry_mode"] == "touch" and event["variant"] == "breakout"
    if touch:
        start_idx = market.pos[trigger["open_time"]]
        entry_ref = float(event["entry_price"])
        entry_ms = trigger["open_time"]
    else:
        start_idx = market.pos.get(trigger_close)
        if start_idx is None:
            counters["no_entry_bar"] += 1
            return None
        entry_ref = market.bars[start_idx]["open"]
        entry_ms = trigger_close

    sl = orb.stop_price(direction, rng["high"], rng["low"], params,
                        event.get("extreme") if event["variant"] == "reversal" else None)
    r_distance = (entry_ref - sl) if direction == "long" else (sl - entry_ref)
    if r_distance <= 0:
        counters["invalid_sl"] += 1               # gia da vuot qua SL luc vao
        return None
    tp_r = float(params["tp_r"])
    tp = entry_ref + tp_r * r_distance if direction == "long" else entry_ref - tp_r * r_distance

    slip = float(params["slippage_pct"]) / 100.0
    fee_rate = float(params["taker_fee_pct"]) / 100.0
    entry_fill = _fill(entry_ref, direction, "entry", slip)
    time_exit_ms = trigger_close + int(params["time_exit_minutes"]) * 60_000
    out = _walk(market, start_idx, direction, entry_fill, sl, tp, r_distance, params,
                time_exit_ms)
    if out is None:
        counters["no_entry_bar"] += 1
        return None
    exit_fill = _fill(out["exit_ref"], direction, "exit", slip)
    sign = 1 if direction == "long" else -1

    # Size nhu live (orb.size_position), roi ha qty neu vuot max_margin
    step = float(params["qty_step"])
    leverage = float(params["max_leverage"])
    sizing, _warnings = orb.size_position(entry_ref, r_distance, float(params["risk_usd"]), params)
    qty = sizing["qty"]
    margin_capped = False
    if qty * entry_ref / leverage > float(params["max_margin_usd"]):
        qty = orb.floor_step(float(params["max_margin_usd"]) * leverage / entry_ref, step)
        margin_capped = True
    notional = qty * entry_ref
    reason = None
    if qty <= 0:
        reason = "qty_below_step"
    elif notional < float(params["min_notional_usd"]):
        reason = "below_min_notional"

    r_ideal = (sign * (exit_fill - entry_fill) - (entry_fill + exit_fill) * fee_rate) / r_distance
    row: dict[str, Any] = {
        "session_id": session["session_id"],
        "or_date": day.isoformat(),
        "direction": direction,
        "variant": event["variant"],
        "signal_state": event["state"],
        "trigger_utc": iso_utc(trigger["open_time"]),
        "entry_ms": entry_ms,
        "entry_utc": iso_utc(entry_ms),
        "entry_ref": round(entry_ref, 2),
        "entry_fill": round(entry_fill, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "r_distance": round(r_distance, 2),
        "qty": qty,
        "notional": round(notional, 2),
        "margin": round(notional / leverage, 2),
        "margin_capped": margin_capped,
        "executable": reason is None,
        "not_executable_reason": reason,
        "exit_ms": out["bar"]["open_time"] + M5_MS,
        "exit_utc": iso_utc(out["bar"]["open_time"] + M5_MS),
        "exit_reason": out["reason"],
        "exit_ref": round(out["exit_ref"], 2),
        "exit_fill": round(exit_fill, 2),
        "sl_tp_same_bar": out["same_bar"],
        "be_moved": out["be_moved"],
        "pnl_usd": None,
        "fees_usd": None,
        "slippage_usd": None,
        "r_multiple": None,
        "r_ideal": round(r_ideal, 4),
        "or_high": rng["high"],
        "or_low": rng["low"],
        "or_atr_ratio": rng["size_atr_ratio"],
    }
    if reason is None:
        gross = sign * (exit_fill - entry_fill) * qty
        fees = (entry_fill + exit_fill) * qty * fee_rate
        slippage = qty * (abs(entry_fill - entry_ref) + abs(exit_fill - out["exit_ref"]))
        pnl = gross - fees
        row.update(pnl_usd=round(pnl, 4), fees_usd=round(fees, 4),
                   slippage_usd=round(slippage, 4),
                   r_multiple=round(pnl / (qty * r_distance), 4))
    return row


def _session_day(market: Market, session: dict[str, Any], day: date,
                 params: dict[str, Any], news: set[str], now_ms: int,
                 counters: Counter, gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ok, _reason = orb.trade_day_status(session, day)
    if not ok:
        counters["no_session"] += 1
        return []
    t = orb.session_times(session, day, params)
    if t["window_end"] > now_ms:
        counters["not_finished"] += 1
        return []
    counters["session_days"] += 1
    first = market.run(t["open"], t["or_end"])
    if len(first) < 3:
        counters["data_missing"] += 1
        gaps.append({"session_id": session["session_id"], "date": day.isoformat(),
                     "what": "or"})
        return []
    atr_value = market.atr(t["open"], int(params["atr_period"]))
    if atr_value is None:
        counters["data_missing"] += 1
        gaps.append({"session_id": session["session_id"], "date": day.isoformat(),
                     "what": "atr"})
        return []
    rng = orb.range_metrics(max(b["high"] for b in first), min(b["low"] for b in first),
                            atr_value, params)
    if rng["range_flag"] != "ok":
        counters[f"filtered_{rng['range_flag']}"] += 1
        return []
    if params["skip_news_days"] and day.isoformat() in news:
        counters["filtered_news_day"] += 1
        return []

    window = market.run(t["or_end"], t["window_end"])
    if len(window) < (t["window_end"] - t["or_end"]) // M5_MS:
        counters["window_partial"] += 1
        gaps.append({"session_id": session["session_id"], "date": day.isoformat(),
                     "what": "window", "bars": len(window)})
    events = orb.scan_signals(float(rng["high"]), float(rng["low"]), window, params)
    counters["both_sides_touched"] += sum(1 for e in events if e.get("warning"))
    rows: dict[int, dict[str, Any] | None] = {}      # cache theo vi tri su kien

    def row_for(n: int, event: dict[str, Any]) -> dict[str, Any] | None:
        if n not in rows:
            rows[n] = _trade(market, session, day, params, rng, window, event, counters)
            if rows[n] is not None:
                rows[n].update(day_cap=int(params["max_orb_trades_per_day"]),
                               real=False, ideal=False)
        return rows[n]

    # Hai luot: `real` - lenh khong vao duoc (qty/min notional) khong chiem suat,
    # nguoi dung se cho tin hieu sau; `ideal` - moi tin hieu deu vao duoc.
    for mode in ("real", "ideal"):
        taken = 0
        free_from = 0                  # tin hieu ke tiep phai dong sau khi lenh truoc dong
        for n, event in enumerate(events):
            if taken >= int(params["max_trades"]):
                break
            if not orb.actionable(event, params):
                continue
            if window[event["trigger_index"]]["open_time"] + M5_MS <= free_from:
                continue
            row = row_for(n, event)
            if row is None:
                continue
            if mode == "real" and not row["executable"]:
                row["real"] = True     # van ghi ra CSV de thay vi sao bo
                counters[f"not_executable_{row['not_executable_reason']}"] += 1
                continue
            row[mode] = True
            taken += 1
            free_from = row["exit_ms"]
    out = [r for r in rows.values() if r is not None]
    if not out:
        counters["no_signal"] += 1
    return out


# ---------------------------------------------------------------- thong ke

def metrics(trades: list[dict[str, Any]], initial_equity: float) -> dict[str, Any]:
    """Bo chi so chuan cho mot tap lenh (da vao duoc). Equity theo thu tu thoi diem dong."""
    rows = sorted(trades, key=lambda r: (r["exit_ms"], r["entry_ms"], r["session_id"]))
    equity = peak = float(initial_equity)
    max_dd = max_dd_pct = 0.0
    streak = worst = 0
    for row in rows:
        equity += row["pnl_usd"]
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd / peak * 100)
        streak = streak + 1 if row["pnl_usd"] <= 0 else 0
        worst = max(worst, streak)
    wins = [r for r in rows if r["pnl_usd"] > 0]
    losses = [r for r in rows if r["pnl_usd"] <= 0]
    gross_win = sum(r["pnl_usd"] for r in wins)
    gross_loss = -sum(r["pnl_usd"] for r in losses)
    n = len(rows)

    def avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 3) if values else None

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 2) if n else None,
        "avg_win_r": avg([r["r_multiple"] for r in wins]),
        "avg_loss_r": avg([r["r_multiple"] for r in losses]),
        "expectancy_r": avg([r["r_multiple"] for r in rows]),
        "total_r": round(sum(r["r_multiple"] for r in rows), 3),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "net_pnl_usd": round(equity - float(initial_equity), 4),
        "max_drawdown_usd": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "max_losing_streak": worst,
        "final_equity": round(equity, 4),
        "total_fees_usd": round(sum(r["fees_usd"] for r in rows), 4),
        "total_slippage_usd": round(sum(r["slippage_usd"] for r in rows), 4),
        "sl_tp_same_bar": sum(1 for r in rows if r["sl_tp_same_bar"]),
        "exits": dict(Counter(r["exit_reason"] for r in rows)),
        "by_variant": dict(Counter(r["variant"] for r in rows)),
    }


def ideal_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Theo R, bo qua lam tron qty va min notional: cho biet ban than chien luoc
    co loi khong, tach khoi van de tai khoan nho."""
    rs = [r["r_ideal"] for r in trades]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    return {
        "signals": len(rs),
        "win_rate": round(len(wins) / len(rs) * 100, 2) if rs else None,
        "avg_win_r": round(sum(wins) / len(wins), 3) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 3) if losses else None,
        "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
        "total_r": round(sum(rs), 3),
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
        "sl_tp_same_bar": sum(1 for r in trades if r["sl_tp_same_bar"]),
    }


def _cap_per_day(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    per_day: Counter = Counter()
    kept, dropped = [], 0
    for row in sorted(rows, key=lambda r: (r["entry_ms"], r["session_id"])):
        if per_day[row["or_date"]] >= row["day_cap"]:
            dropped += 1
            continue
        per_day[row["or_date"]] += 1
        kept.append(row)
    return kept, dropped


def simulate(bars: list[dict[str, Any]], sessions: list[dict[str, Any]],
             cfg: dict[str, Any], *, from_day: date, to_day: date,
             split_day: date | None = None, overrides: dict[str, Any] | None = None,
             news_days: list[str] | tuple[str, ...] = (), initial_equity: float = 100.0,
             now_ms: int | None = None) -> dict[str, Any]:
    if to_day < from_day:
        raise ValueError("to_date phai sau from_date")
    if not sessions:
        raise ValueError("khong co phien nao de backtest")
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if split_day is None:
        split_day = from_day + timedelta(days=int((to_day - from_day).days * DEFAULT_SPLIT))
    market = Market(bars)
    news = set(news_days)
    counters: Counter = Counter()
    gaps: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    params_by_session: dict[str, dict[str, Any]] = {}
    for session in sessions:
        params = orb.effective_params(cfg, session, overrides)
        params_by_session[session["session_id"]] = params
        day = from_day
        while day <= to_day:
            signals.extend(_session_day(market, session, day, params, news, now_ms,
                                        counters, gaps))
            day += timedelta(days=1)

    trades, dropped = _cap_per_day([r for r in signals if r["real"] and r["executable"]])
    counters["day_limit_skipped"] = dropped
    ideal, _ = _cap_per_day([r for r in signals if r["ideal"]])

    equity = float(initial_equity)
    for row in sorted(trades, key=lambda r: (r["exit_ms"], r["entry_ms"], r["session_id"])):
        equity += row["pnl_usd"]
        row["equity_after"] = round(equity, 4)
    counted = {id(r) for r in trades}
    in_ideal = {id(r) for r in ideal}
    for row in signals:
        row["sample"] = "in_sample" if row["or_date"] < split_day.isoformat() else "out_of_sample"
        row["counted"] = id(row) in counted
        row["counted_ideal"] = id(row) in in_ideal

    split_iso = split_day.isoformat()
    summary = metrics(trades, initial_equity)
    summary["in_sample"] = metrics([r for r in trades if r["or_date"] < split_iso],
                                   initial_equity)
    summary["out_of_sample"] = metrics([r for r in trades if r["or_date"] >= split_iso],
                                       initial_equity)
    summary["by_session"] = {
        sid: metrics([r for r in trades if r["session_id"] == sid], initial_equity)
        for sid in params_by_session}
    summary["ideal_sizing"] = {
        "note": ("R cua moi tin hieu da mo phong, bo qua lam tron qty va min notional - "
                 "de danh gia chien luoc tach khoi gioi han cua tai khoan nho"),
        "all": ideal_metrics(ideal),
        "in_sample": ideal_metrics([r for r in ideal if r["or_date"] < split_iso]),
        "out_of_sample": ideal_metrics([r for r in ideal if r["or_date"] >= split_iso]),
        "by_session": {sid: ideal_metrics([r for r in ideal if r["session_id"] == sid])
                       for sid in params_by_session},
    }
    summary["counters"] = dict(sorted(counters.items()))
    return {
        "period": {"from": from_day.isoformat(), "to": to_day.isoformat(),
                   "split_date": split_iso,
                   "bars_m5": len(bars),
                   "first_bar_utc": iso_utc(bars[0]["open_time"]) if bars else None,
                   "last_bar_utc": iso_utc(bars[-1]["open_time"]) if bars else None},
        "initial_equity": float(initial_equity),
        "params_by_session": params_by_session,
        "summary": summary,
        "data_gaps": gaps[:50],
        "data_gaps_total": len(gaps),
        "trades": sorted(signals, key=lambda r: (r["entry_ms"], r["session_id"])),
        "assumptions": [
            "OR = 3 nen M5 dau phien; ATR H1 gop tu M5, 200 nen dong truoc gio mo",
            "close/retest vao o gia mo nen sau tin hieu; touch vao tai muc cham",
            "SL va TP cung cham trong mot nen -> tinh SL",
            "truot gia lam xau ca luc vao va luc ra; phi taker hai chieu",
            "doi SL ve hoa von co hieu luc tu nen sau",
            "khong ap bo loc bias",
            "equity cua in_sample / out_of_sample / by_session deu bat dau tu initial_equity",
        ],
    }


# ---------------------------------------------------------------- I/O

TRADE_COLUMNS = ("session_id", "or_date", "sample", "counted", "counted_ideal",
                 "direction", "variant",
                 "signal_state", "trigger_utc", "entry_utc", "entry_ref", "entry_fill",
                 "sl", "tp", "r_distance", "qty", "notional", "margin", "margin_capped",
                 "executable", "not_executable_reason", "exit_utc", "exit_reason",
                 "exit_ref", "exit_fill", "sl_tp_same_bar", "be_moved", "pnl_usd",
                 "fees_usd", "slippage_usd", "r_multiple", "r_ideal", "equity_after",
                 "or_high", "or_low", "or_atr_ratio")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    tmp.replace(path)


class BacktestManager:
    """Chay backtest, luu ket qua theo run_id duoi out_root/<run_id>/."""

    def __init__(self, out_root: Path, *, symbol: str, history: Any, history_years: int,
                 load_live_m5: Callable[[str], list[dict[str, Any]]],
                 log: Callable[..., None], now_fn: Callable[[], int]):
        self.out_root = Path(out_root)
        self.symbol = symbol
        self.history = history
        self.history_years = int(history_years)
        self.load_live_m5 = load_live_m5
        self.log = log
        self.now = now_fn
        self._tasks: set[asyncio.Task] = set()

    def _dir(self, run_id: str) -> Path:
        return self.out_root / run_id

    def result(self, run_id: str) -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
            raise ValueError("run_id khong hop le")
        path = self._dir(run_id) / "result.json"
        if not path.exists():
            raise ValueError(f"khong co backtest '{run_id}'")
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def runs(self, limit: int = 10) -> list[str]:
        if not self.out_root.exists():
            return []
        return sorted((p.name for p in self.out_root.iterdir() if p.is_dir()),
                      reverse=True)[:limit]

    def load_bars(self, from_day: date, to_day: date) -> tuple[list[dict[str, Any]], dict]:
        start = from_day - timedelta(days=ATR_WARMUP_DAYS)
        end = to_day + timedelta(days=2)       # phien toi muon + time exit qua nua dem
        today = datetime.fromtimestamp(self.now() / 1000, UTC).date()
        report = self.history.ensure(self.symbol, start, end, today=today)
        start_ms = int(datetime.combine(start, datetime.min.time(), UTC).timestamp() * 1000)
        end_ms = int(datetime.combine(end + timedelta(days=1), datetime.min.time(),
                                      UTC).timestamp() * 1000)
        bars = {b["open_time"]: b for b in self.history.load(self.symbol, start_ms, end_ms)}
        # Hom nay chua co file ngay tren data.binance.vision: bu bang file M5 live
        now = self.now()
        for bar in self.load_live_m5(self.symbol):
            if start_ms <= bar["open_time"] < end_ms and bar["open_time"] + M5_MS <= now:
                bars.setdefault(bar["open_time"], bar)
        return [bars[k] for k in sorted(bars)], report

    def execute(self, run_id: str, request: dict[str, Any], sessions: list[dict[str, Any]],
                cfg: dict[str, Any], news_days: list[str]) -> dict[str, Any]:
        started = time.perf_counter()
        folder = self._dir(run_id)
        try:
            from_day, to_day = request["from_day"], request["to_day"]
            bars, report = self.load_bars(from_day, to_day)
            result = simulate(bars, sessions, cfg, from_day=from_day, to_day=to_day,
                              split_day=request.get("split_day"),
                              overrides=request.get("overrides"), news_days=news_days,
                              initial_equity=request["initial_equity"], now_ms=self.now())
            trades = result.pop("trades")
            self._write_csv(folder, trades, request["initial_equity"])
            elapsed = round(time.perf_counter() - started, 2)
            out = {
                "run_id": run_id,
                "status": "done",
                "request": _jsonable(request),
                "sessions": [s["session_id"] for s in sessions],
                "symbol": self.symbol,
                **result,
                "history": {k: report.get(k) for k in ("from", "to", "downloaded",
                                                       "missing_remote", "errors")},
                "files": {"trades_csv": str(folder / "trades.csv"),
                          "equity_csv": str(folder / "equity.csv"),
                          "result_json": str(folder / "result.json")},
                "elapsed_seconds": elapsed,
                "finished_utc": iso_utc(self.now()),
            }
            out["history"]["downloaded"] = len(report.get("downloaded") or [])
            _write_json(folder / "result.json", out)
            self.log("backtest", run_id=run_id, status="done", elapsed_seconds=elapsed,
                     sessions=out["sessions"], request=_jsonable(request),
                     trades=out["summary"]["trades"])
            return out
        except Exception as exc:
            elapsed = round(time.perf_counter() - started, 2)
            out = {"run_id": run_id, "status": "error", "request": _jsonable(request),
                   "error": f"{type(exc).__name__}: {exc}", "elapsed_seconds": elapsed}
            _write_json(folder / "result.json", out)
            self.log("backtest", run_id=run_id, status="error", error=out["error"],
                     elapsed_seconds=elapsed)
            print(f"BAMCP backtest {run_id} loi: {out['error']}", file=sys.stderr)
            return out

    @staticmethod
    def _write_csv(folder: Path, trades: list[dict[str, Any]], initial_equity: float) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "trades.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(TRADE_COLUMNS)
            for row in trades:
                writer.writerow([row.get(c) for c in TRADE_COLUMNS])
        counted = sorted((r for r in trades if r.get("counted")),
                         key=lambda r: (r["exit_ms"], r["entry_ms"], r["session_id"]))
        with (folder / "equity.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(("time_utc", "trade_no", "session_id", "pnl_usd", "equity"))
            writer.writerow(("", 0, "", "", initial_equity))
            for n, row in enumerate(counted, 1):
                writer.writerow((row["exit_utc"], n, row["session_id"], row["pnl_usd"],
                                 row["equity_after"]))

    async def run(self, request: dict[str, Any], sessions: list[dict[str, Any]],
                  cfg: dict[str, Any], news_days: list[str],
                  wait_seconds: float = 55.0) -> dict[str, Any]:
        stamp = datetime.fromtimestamp(self.now() / 1000, UTC).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{stamp}-{secrets.token_hex(3)}"
        _write_json(self._dir(run_id) / "result.json", {
            "run_id": run_id, "status": "running", "request": _jsonable(request),
            "sessions": [s["session_id"] for s in sessions],
            "started_utc": iso_utc(self.now())})
        self.log("backtest", run_id=run_id, status="started", request=_jsonable(request),
                 sessions=[s["session_id"] for s in sessions])
        task = asyncio.create_task(asyncio.to_thread(
            self.execute, run_id, request, sessions, cfg, news_days))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        done, _pending = await asyncio.wait({task}, timeout=wait_seconds)
        if done:
            return task.result()
        return {"run_id": run_id, "status": "running",
                "note": ("Backtest dang chay nen (qua 60 giay, thuong do lan dau phai tai "
                         "M5 lich su). Goi get_backtest_result(run_id) sau."),
                "files": {"result_json": str(self._dir(run_id) / "result.json")}}


def _jsonable(request: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in request.items()}
