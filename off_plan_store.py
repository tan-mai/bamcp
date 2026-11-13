# TM - #OFF-PLAN - Canh bao lenh ngoai ke hoach
from __future__ import annotations

import json
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import off_plan_logic
import sqlite_db

JSON_FIELDS = {"flags": [], "candidate_setups": [], "tr_snapshot": None, "rule_check": None, "explain": {},
               "orders": []}
COLUMNS = ("id", "code", "position_id", "symbol", "side", "trade_date", "opened_at", "closed_at", "entry_avg",
           "qty", "margin_usd", "leverage", "exchange_sl", "exchange_tp", "classification", "flags",
           "matched_setup_id", "candidate_setups", "tr_snapshot", "rule_check", "rule_set", "explain", "orders",
           "realized_pnl", "fee", "r_multiple", "review_status", "tan_reason", "claude_assessment", "verdict",
           "lesson", "add_to_plan", "notified", "notify_error", "created_at", "updated_at")
REVIEW_FIELDS = ("tan_reason", "claude_assessment", "verdict", "lesson", "add_to_plan")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _loads(text: Any, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _row(row: Any) -> dict[str, Any]:
    out = {k: row[k] for k in COLUMNS}
    for key, default in JSON_FIELDS.items():
        out[key] = _loads(out[key], default)
    out["notified"] = bool(out["notified"])
    out["add_to_plan"] = None if out["add_to_plan"] is None else bool(out["add_to_plan"])
    return out


def _value(key: str, value: Any) -> Any:
    if key in JSON_FIELDS:
        return json.dumps(value if value is not None else JSON_FIELDS[key], default=str, ensure_ascii=False)
    if key in ("notified", "add_to_plan") and value is not None:
        return int(bool(value))
    return value


class OffPlanStore:
    def __init__(self, path: Path, now_ms: Callable[[], int] | None = None) -> None:
        self.path = Path(path)
        self.now_ms = now_ms or _now_ms
        sqlite_db.migrate(self.path)

    def _conn(self):
        return closing(sqlite_db.connect(self.path))

    # ---------------------------------------------------------- ghi

    def insert(self, fields: dict[str, Any]) -> dict[str, Any]:
        now = self.now_ms()
        row = {k: fields.get(k) for k in COLUMNS}
        row.update(id=str(uuid.uuid4()), created_at=now, updated_at=now,
                   review_status=fields.get("review_status") or "PENDING", notified=bool(fields.get("notified")))
        keys = [k for k in COLUMNS if row.get(k) is not None or k in JSON_FIELDS]
        with self._conn() as conn, conn:
            conn.execute(f"INSERT INTO off_plan_trades ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
                         [_value(k, row.get(k)) for k in keys])
        return self.get(row["id"])

    def update(self, row_id: str, **fields: Any) -> dict[str, Any]:
        keys = [k for k in fields if k in COLUMNS and k not in ("id", "created_at")]
        if keys:
            with self._conn() as conn, conn:
                conn.execute(f"UPDATE off_plan_trades SET {', '.join(f'{k} = ?' for k in keys)}, updated_at = ? "
                             "WHERE id = ?", [_value(k, fields[k]) for k in keys] + [self.now_ms(), row_id])
        return self.get(row_id)

    def assign_code(self, row_id: str) -> str:
        """Cap ma OP-{yyyymmdd}-{SYM}-{n} neu chua co. n dem theo cap + ngay mo vi the."""
        with self._conn() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT code, symbol, trade_date FROM off_plan_trades WHERE id = ?",
                               (row_id,)).fetchone()
            if row is None:
                raise ValueError(f"khong tim thay vi the {row_id!r}")
            if row["code"]:
                return row["code"]
            prefix = off_plan_logic.code_prefix(row["symbol"], row["trade_date"])
            taken = {r[0] for r in conn.execute("SELECT code FROM off_plan_trades WHERE substr(code, 1, ?) = ?",
                                                (len(prefix), prefix))}
            number = 1
            while f"{prefix}{number}" in taken:
                number += 1
            code = f"{prefix}{number}"
            conn.execute("UPDATE off_plan_trades SET code = ?, updated_at = ? WHERE id = ?",
                         (code, self.now_ms(), row_id))
        return code

    def add_event(self, row_id: str, event: str, details: dict[str, Any] | None = None,
                  at: int | None = None) -> str:
        eid = str(uuid.uuid4())
        with self._conn() as conn, conn:
            conn.execute("INSERT INTO off_plan_events (id, off_plan_id, event, details, at) VALUES (?, ?, ?, ?, ?)",
                         (eid, row_id, event, json.dumps(details or {}, default=str, ensure_ascii=False),
                          int(at if at is not None else self.now_ms())))
        return eid

    def mark_event(self, event_id: str, error: str | None) -> None:
        with self._conn() as conn, conn:
            conn.execute("UPDATE off_plan_events SET notified = ?, notify_error = ? WHERE id = ?",
                         (0 if error else 1, error, event_id))

    # ---------------------------------------------------------- doc

    def get(self, ref: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM off_plan_trades WHERE id = ? OR code = ?",
                               (str(ref), str(ref).strip().upper())).fetchone()
        if row is None:
            raise ValueError(f"khong tim thay lenh {ref!r}")
        return _row(row)

    def find_position(self, ref: str, today: str) -> dict[str, Any]:
        """id, ma OP, hoac position_id kieu get_fills (BTCUSDT-P1). position_id lap lai moi ngay: uu tien
        hom nay, roi ngay gan nhat."""
        text = str(ref or "").strip()
        if not text:
            raise ValueError("position_id la bat buoc")
        try:
            return self.get(text)
        except ValueError:
            pass
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM off_plan_trades WHERE position_id = ? "
                               "ORDER BY trade_date = ? DESC, opened_at DESC LIMIT 1",
                               (text.upper(), today)).fetchone()
        if row is None:
            raise ValueError(f"khong tim thay vi the {text!r} (dung position_id cua get_fills hoac ma OP)")
        return _row(row)

    def match(self, symbol: str, side: str, opened_at: int, first_order: str | None = None,
              tolerance: int = off_plan_logic.MATCH_TOLERANCE_MS) -> dict[str, Any] | None:
        """Dong da co cho vi the nay: cung cap, cung huong, gio mo lech trong tolerance. Biet lenh mo dau
        thi phai trung lenh do (hai vi the mo sat nhau khong bi gop)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM off_plan_trades WHERE symbol = ? AND side = ? AND opened_at BETWEEN ? AND ? "
                                "ORDER BY ABS(opened_at - ?)",
                                (symbol.upper(), side, int(opened_at) - tolerance, int(opened_at) + tolerance,
                                 int(opened_at))).fetchall()
        for raw in rows:
            row = _row(raw)
            if first_order and row["orders"] and row["orders"][0] != first_order:
                continue
            return row
        return None

    def open_rows(self, symbol: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM off_plan_trades WHERE closed_at IS NULL", []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol.upper())
        with self._conn() as conn:
            return [_row(r) for r in conn.execute(sql + " ORDER BY opened_at", params).fetchall()]

    def day_rows(self, day: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM off_plan_trades WHERE trade_date = ? ORDER BY opened_at",
                                (day,)).fetchall()
        return [_row(r) for r in rows]

    def closed_between(self, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM off_plan_trades WHERE closed_at BETWEEN ? AND ? ORDER BY closed_at",
                                (int(start_ms), int(end_ms))).fetchall()
        return [_row(r) for r in rows]

    def list(self, *, from_date: str | None = None, to_date: str | None = None, symbol: str | None = None,
             coded_only: bool = False) -> list[dict[str, Any]]:
        where, params = [], []
        if from_date:
            where.append("trade_date >= ?")
            params.append(from_date)
        if to_date:
            where.append("trade_date <= ?")
            params.append(to_date)
        if symbol:
            where.append("symbol = ?")
            params.append(symbol.upper())
        if coded_only:
            where.append("code IS NOT NULL")
        sql = "SELECT * FROM off_plan_trades" + (" WHERE " + " AND ".join(where) if where else "")
        with self._conn() as conn:
            return [_row(r) for r in conn.execute(sql + " ORDER BY opened_at", params).fetchall()]

    def events(self, row_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {rid: [] for rid in row_ids}
        if not row_ids:
            return out
        marks = ", ".join("?" for _ in row_ids)
        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM off_plan_events WHERE off_plan_id IN ({marks}) ORDER BY at, rowid",
                                row_ids).fetchall()
        for r in rows:
            out[r["off_plan_id"]].append({"id": r["id"], "event": r["event"], "at": int(r["at"]),
                                          "details": _loads(r["details"], {}), "notified": bool(r["notified"]),
                                          "notify_error": r["notify_error"]})
        return out
