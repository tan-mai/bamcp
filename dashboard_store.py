# TM - #DASHBOARD - Setup dashboard
"""Luu ke hoach cho dashboard: kich ban, TR khoa, snapshot bat bien, ket qua review."""
from __future__ import annotations

import json
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import entry_logic
import sqlite_db


def _now_ms() -> int:
    return int(time.time() * 1000)


def _loads(text: Any, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _scenario(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["setup_codes"] = _loads(out["setup_codes"], [])
    out["points"] = _loads(out["points"], [])
    return out


def _tr(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["events"] = _loads(out["events"], [])
    return out


def _review(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["exchange_fill_ids"] = _loads(out["exchange_fill_ids"], [])
    return out


class PlanStore:
    """plan_snapshots chi co them va doc: khong co ham sua hay xoa, review dua vao do."""

    def __init__(self, path: Path, now_ms: Callable[[], int] | None = None) -> None:
        self.path = Path(path)
        self.now_ms = now_ms or _now_ms
        sqlite_db.migrate(self.path)

    def _conn(self):
        return closing(sqlite_db.connect(self.path))

    # ------------------------------------------------------------ kich ban

    def insert_scenario(self, symbol: str, day: str, clean: dict[str, Any]) -> dict[str, Any]:
        now = self.now_ms()
        sid = str(uuid.uuid4())
        prefix = f"{entry_logic.code_base(symbol)}-{day.replace('-', '')}-S"
        with self._conn() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            taken = {r[0] for r in conn.execute("SELECT code FROM scenarios WHERE code LIKE ?",
                                                (prefix + "%",))}
            index = 1
            while f"{prefix}{index}" in taken:
                index += 1
            conn.execute(
                "INSERT INTO scenarios (id, symbol, bias_date, code, kind, label, setup_codes, points, "
                "condition, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (sid, symbol.upper(), day, f"{prefix}{index}", clean["kind"], clean["label"],
                 json.dumps(clean["setup_codes"]), json.dumps(clean["points"]),
                 clean["condition"], now, now))
        return self.scenario(sid)

    def scenario(self, ref: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM scenarios WHERE id = ? OR code = ?",
                               (ref, str(ref).strip().upper())).fetchone()
        if row is None:
            raise ValueError(f"khong tim thay kich ban {ref!r}")
        return _scenario(row)

    def scenarios(self, symbol: str, day: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM scenarios WHERE symbol = ? AND bias_date = ? "
                                "ORDER BY created_at, code", (symbol.upper(), day)).fetchall()
        return [_scenario(r) for r in rows]

    def set_scenario_status(self, ref: str, status: str) -> dict[str, Any]:
        row = self.scenario(ref)
        with self._conn() as conn, conn:
            conn.execute("UPDATE scenarios SET status = ?, updated_at = ? WHERE id = ?",
                         (status, self.now_ms(), row["id"]))
        return self.scenario(row["id"])

    def cancel_active(self, symbol: str, day: str) -> list[str]:
        codes = [s["code"] for s in self.scenarios(symbol, day) if s["status"] == "active"]
        with self._conn() as conn, conn:
            conn.execute("UPDATE scenarios SET status = 'cancelled', updated_at = ? "
                         "WHERE symbol = ? AND bias_date = ? AND status = 'active'",
                         (self.now_ms(), symbol.upper(), day))
        return codes

    def mark_linked(self, symbol: str, day: str, setup_code: str, status: str) -> list[str]:
        changed = []
        for row in self.scenarios(symbol, day):
            if row["status"] == "active" and setup_code in row["setup_codes"]:
                self.set_scenario_status(row["id"], status)
                changed.append(row["code"])
        return changed

    # ------------------------------------------------------------ TR khoa

    def tr_at(self, symbol: str, at_ms: int) -> dict[str, Any] | None:
        """TR dang hieu luc tai mot thoi diem. TR cu giu lai de review nen van doc duoc."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM trading_ranges WHERE symbol = ? AND created_at <= ? "
                               "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                               (symbol.upper(), int(at_ms))).fetchone()
        return _tr(row) if row else None

    def active_tr(self, symbol: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM trading_ranges WHERE symbol = ? AND superseded_by IS NULL "
                               "ORDER BY created_at DESC, rowid DESC LIMIT 1", (symbol.upper(),)).fetchone()
        return _tr(row) if row else None

    def replace_tr(self, symbol: str, clean: dict[str, Any], old_id: str | None) -> dict[str, Any]:
        now = self.now_ms()
        tid = str(uuid.uuid4())
        with self._conn() as conn, conn:
            conn.execute(
                "INSERT INTO trading_ranges (id, symbol, timeframe, tr_high, tr_low, since, events, "
                "redraw_rule, superseded_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (tid, symbol.upper(), clean["timeframe"], clean["tr_high"], clean["tr_low"], clean["since"],
                 json.dumps(clean["events"]), clean["redraw_rule"], now, now))
            if old_id:
                conn.execute("UPDATE trading_ranges SET superseded_by = ?, updated_at = ? WHERE id = ?",
                             (tid, now, old_id))
        return self.active_tr(symbol)  # type: ignore[return-value]

    # ------------------------------------------------------------ snapshot

    def add_snapshot(self, symbol: str, day: str, trigger: str, payload: dict[str, Any]) -> int:
        with self._conn() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT COALESCE(MAX(revision), 0) FROM plan_snapshots "
                               "WHERE symbol = ? AND bias_date = ?", (symbol.upper(), day)).fetchone()
            revision = int(row[0]) + 1
            conn.execute(
                "INSERT INTO plan_snapshots (id, symbol, bias_date, revision, trigger, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), symbol.upper(), day, revision, trigger,
                 json.dumps(payload, ensure_ascii=False, default=str), self.now_ms()))
        return revision

    def snapshots(self, symbol: str, day: str, *, payload: bool = False) -> list[dict[str, Any]]:
        columns = "revision, trigger, created_at" + (", payload" if payload else "")
        with self._conn() as conn:
            rows = conn.execute(f"SELECT {columns} FROM plan_snapshots WHERE symbol = ? AND bias_date = ? "
                                "ORDER BY revision", (symbol.upper(), day)).fetchall()
        out = [dict(r) for r in rows]
        if payload:
            for row in out:
                row["payload"] = _loads(row["payload"], {})
        return out

    def snapshot(self, symbol: str, day: str, revision: int) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT revision, trigger, created_at, payload FROM plan_snapshots "
                               "WHERE symbol = ? AND bias_date = ? AND revision = ?",
                               (symbol.upper(), day, int(revision))).fetchone()
        if row is None:
            raise ValueError(f"khong co revision {revision} cua {symbol.upper()} ngay {day}")
        return {**dict(row), "payload": _loads(row["payload"], {})}

    def version(self) -> int:
        """Doi moi khi ke hoach doi (moi lan doi deu sinh snapshot): dung lam khoa cache."""
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM plan_snapshots").fetchone()
        return int(row[0])

    # ------------------------------------------------------------ review

    def reviews(self, setup_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not setup_ids:
            return {}
        marks = ", ".join("?" for _ in setup_ids)
        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM setup_reviews WHERE setup_id IN ({marks})",
                                setup_ids).fetchall()
        return {r["setup_id"]: _review(r) for r in rows}

    def set_auto(self, setup_id: str, outcome: str | None) -> dict[str, Any]:
        """Ghi auto_outcome. Nguoi da gan outcome thi giu nguyen outcome cua nguoi do."""
        with self._conn() as conn, conn:
            conn.execute(
                "INSERT INTO setup_reviews (id, setup_id, outcome, auto_outcome, exchange_fill_ids) "
                "VALUES (?, ?, ?, ?, '[]') ON CONFLICT(setup_id) DO UPDATE SET "
                "auto_outcome = excluded.auto_outcome, outcome = CASE WHEN reviewed_at IS NULL "
                "THEN excluded.outcome ELSE outcome END",
                (str(uuid.uuid4()), setup_id, outcome, outcome))
        return self.reviews([setup_id])[setup_id]

    def save_review(self, setup_id: str, *, outcome: str | None, r_multiple: float | None,
                    note: str | None, reviewed_by: str,
                    fill_ids: list[str] | None = None) -> dict[str, Any]:
        """Chi ghi de truong co truyen. auto_outcome khong bao gio bi dong vao o day."""
        current = self.reviews([setup_id]).get(setup_id) or {}
        values = (
            outcome if outcome is not None else current.get("outcome"),
            r_multiple if r_multiple is not None else current.get("r_multiple"),
            note if note is not None else current.get("note"),
            json.dumps(fill_ids if fill_ids is not None else current.get("exchange_fill_ids") or []),
            reviewed_by, self.now_ms(),
        )
        with self._conn() as conn, conn:
            conn.execute(
                "INSERT INTO setup_reviews (id, setup_id, outcome, r_multiple, note, exchange_fill_ids, "
                "reviewed_by, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(setup_id) DO UPDATE SET "
                "outcome = excluded.outcome, r_multiple = excluded.r_multiple, note = excluded.note, "
                "exchange_fill_ids = excluded.exchange_fill_ids, reviewed_by = excluded.reviewed_by, "
                "reviewed_at = excluded.reviewed_at",
                (str(uuid.uuid4()), setup_id, *values))
        return self.reviews([setup_id])[setup_id]
