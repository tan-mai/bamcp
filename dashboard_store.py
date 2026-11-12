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
import tr_logic  # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR


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
    # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
    out["levels"] = _loads(out.get("levels"), {})
    # TM - #TR-BRANCH - Diem bang chung xac suat tich luy / phan phoi
    out["prob_evidence"] = _loads(out.get("prob_evidence"), [])
    return out


# TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
EVENT_COLUMNS = ("id", "tr_id", "seq", "type", "t", "tf", "price", "side", "vol_ratio", "spread_ratio",
                 "close_pct", "status", "confirmed_by", "note", "created_by", "created_at", "updated_at")
EVENT_UPDATABLE = ("status", "note", "confirmed_by", "price")


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

    # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
    # TR la noi duy nhat luu cau truc Wyckoff: so do, pha, event. Setup chi lien ket toi TR.

    def _full(self, conn, row: Any) -> dict[str, Any]:
        out = _tr(row)
        if out.get("events_migrated"):
            out["events"] = [dict(r) for r in conn.execute(
                "SELECT * FROM tr_events WHERE tr_id = ? ORDER BY t, created_at", (out["id"],))]
        out["phases"] = [dict(r) for r in conn.execute(
            "SELECT * FROM tr_phases WHERE tr_id = ?", (out["id"],))]
        out.pop("events_migrated", None)
        return tr_logic.decorate_tr(out)  # type: ignore[return-value]

    def _one(self, sql: str, params: tuple) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
            return self._full(conn, row) if row else None

    def tr(self, tr_id: str) -> dict[str, Any]:
        out = self._one("SELECT * FROM trading_ranges WHERE id = ?", (str(tr_id).strip(),))
        if out is None:
            raise ValueError(f"khong tim thay TR {tr_id!r}")
        return out

    def tr_at(self, symbol: str, at_ms: int) -> dict[str, Any] | None:
        """TR dang hieu luc tai mot thoi diem. TR cu giu lai de review nen van doc duoc."""
        return self._one("SELECT * FROM trading_ranges WHERE symbol = ? AND created_at <= ? "
                         "ORDER BY created_at DESC, rowid DESC LIMIT 1", (symbol.upper(), int(at_ms)))

    def active_tr(self, symbol: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM trading_ranges WHERE symbol = ? AND superseded_by IS NULL "
                         "ORDER BY created_at DESC, rowid DESC LIMIT 1", (symbol.upper(),))

    def tr_schematics(self, tr_ids: list[str]) -> dict[str, str]:
        ids = sorted({i for i in tr_ids if i})
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        with self._conn() as conn:
            rows = conn.execute(f"SELECT id, schematic FROM trading_ranges WHERE id IN ({marks})", ids).fetchall()
        return {r["id"]: r["schematic"] for r in rows}

    def _write_plan(self, conn, tr_id: str, plan: dict[str, Any], now: int) -> None:
        fields = dict(plan.get("fields") or {})
        if "levels" in fields:
            fields["levels"] = json.dumps(fields["levels"]) if fields["levels"] else None
        # TM - #TR-BRANCH - Diem bang chung luu json; lich su xac suat ghi cung giao dich
        if "prob_evidence" in fields:
            fields["prob_evidence"] = json.dumps(fields["prob_evidence"], ensure_ascii=False)
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE trading_ranges SET {sets + ', ' if sets else ''}updated_at = ? WHERE id = ?",
                     (*fields.values(), now, tr_id))
        prob = plan.get("prob")
        if prob is not None:
            cur = conn.execute(
                "INSERT INTO tr_prob_history (tr_id, at, prob_accumulation, prob_distribution, evidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (tr_id, now, prob["prob_accumulation"], prob["prob_distribution"],
                 json.dumps(prob["evidence"], ensure_ascii=False)))
            plan["prob_history_id"] = int(cur.lastrowid)
        for event in plan.get("insert") or []:
            row = {**event, "tr_id": tr_id, "seq": plan["seq"][event["id"]]}
            conn.execute(f"INSERT INTO tr_events ({', '.join(EVENT_COLUMNS)}) "
                         f"VALUES ({', '.join('?' for _ in EVENT_COLUMNS)})", [row.get(k) for k in EVENT_COLUMNS])
        for change in plan.get("update") or []:
            values = {k: change[k] for k in EVENT_UPDATABLE if k in change}
            if values:
                conn.execute(f"UPDATE tr_events SET {', '.join(f'{k} = ?' for k in values)}, updated_at = ? "
                             "WHERE id = ? AND tr_id = ?", (*values.values(), now, change["id"], tr_id))
        for phase in plan.get("phases") or []:
            conn.execute(
                "INSERT INTO tr_phases (id, tr_id, phase, start_t, end_t, note, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(tr_id, phase) DO UPDATE SET "
                "start_t = excluded.start_t, end_t = excluded.end_t, note = excluded.note, "
                "updated_at = excluded.updated_at",
                (phase.get("id") or str(uuid.uuid4()), tr_id, phase["phase"], phase["start_t"],
                 phase["end_t"], phase.get("note"), now, now))
        self._renumber(conn, tr_id)

    @staticmethod
    def _renumber(conn, tr_id: str) -> None:
        rows = conn.execute("SELECT id FROM tr_events WHERE tr_id = ? ORDER BY t, created_at, type",
                            (tr_id,)).fetchall()
        for seq, row in enumerate(rows, start=1):
            conn.execute("UPDATE tr_events SET seq = ? WHERE id = ?", (seq, row["id"]))

    def create_tr(self, symbol: str, clean: dict[str, Any], plan: dict[str, Any], old_id: str | None,
                  old_status: str = "completed") -> dict[str, Any]:
        """Neo moi = TR moi. TR cu giu lai (superseded_by + status) de review ngay cu van doc duoc."""
        now = self.now_ms()
        tid = str(uuid.uuid4())
        with self._conn() as conn, conn:
            conn.execute(
                "INSERT INTO trading_ranges (id, symbol, timeframe, tr_high, tr_low, since, events, "
                "redraw_rule, superseded_by, created_at, updated_at, events_migrated) "
                "VALUES (?, ?, ?, ?, ?, ?, '[]', ?, NULL, ?, ?, 1)",
                (tid, symbol.upper(), clean["timeframe"], clean["tr_high"], clean["tr_low"], clean["since"],
                 clean.get("redraw_rule"), now, now))
            self._write_plan(conn, tid, plan, now)
            if old_id:
                conn.execute("UPDATE trading_ranges SET superseded_by = ?, status = ?, updated_at = ? WHERE id = ?",
                             (tid, old_status, now, old_id))
        return self.tr(tid)

    def apply_tr(self, tr_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        with self._conn() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._write_plan(conn, tr_id, plan, self.now_ms())
        return self.tr(tr_id)

    def delete_tr_event(self, tr_id: str, event: dict[str, Any], reason: str) -> list[str]:
        """Chi cho event tao nham (kiem tra 24h o tang tren). Ghi lai ban da xoa kem ly do."""
        now = self.now_ms()
        with self._conn() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM tr_events WHERE id = ? AND tr_id = ?", (event["id"], tr_id)).fetchone()
            if row is None:
                raise ValueError(f"khong tim thay event {event['id']!r} trong TR {tr_id}")
            linked = [r["id"] for r in conn.execute("SELECT id FROM tr_events WHERE confirmed_by = ?", (row["id"],))]
            conn.execute("UPDATE tr_events SET confirmed_by = NULL, updated_at = ? WHERE confirmed_by = ?",
                         (now, row["id"]))
            conn.execute("DELETE FROM tr_events WHERE id = ?", (row["id"],))
            conn.execute("INSERT INTO tr_event_deletions (tr_id, event, reason, deleted_at) VALUES (?, ?, ?, ?)",
                         (tr_id, json.dumps(dict(row), ensure_ascii=False), reason, now))
            conn.execute("UPDATE trading_ranges SET updated_at = ? WHERE id = ?", (now, tr_id))
            self._renumber(conn, tr_id)
        return linked

    # TM - #TR-BRANCH - Lich su xac suat gan plan revision
    def set_prob_revision(self, history_id: int | None, revision: int | None, day: str) -> None:
        if not history_id:
            return
        with self._conn() as conn, conn:
            conn.execute("UPDATE tr_prob_history SET plan_revision = ?, bias_date = ? WHERE id = ?",
                         (revision, day, int(history_id)))

    def prob_history(self, tr_id: str, start_ms: int | None = None,
                     end_ms: int | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT id, at, prob_accumulation, prob_distribution, bias_date, plan_revision "
               "FROM tr_prob_history WHERE tr_id = ?")
        params: list[Any] = [tr_id]
        if start_ms is not None:
            sql += " AND at >= ?"
            params.append(int(start_ms))
        if end_ms is not None:
            sql += " AND at <= ?"
            params.append(int(end_ms))
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY at, id", params)]

    def find_tr_event(self, symbol: str, event_id: str) -> tuple[str, str] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT e.id, e.tr_id FROM tr_events e JOIN trading_ranges r ON r.id = e.tr_id "
                               "WHERE e.id = ? AND r.symbol = ?", (event_id, symbol.upper())).fetchone()
        return (row["id"], row["tr_id"]) if row else None

    def legacy_trs(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM trading_ranges WHERE events_migrated = 0 ORDER BY created_at").fetchall()
        return [_tr(r) for r in rows]

    def migrate_tr_events(self, tr_id: str, events: list[dict[str, Any]]) -> int:
        """Chuyen event json cu thanh dong tr_events, mot giao dich moi TR, chay lai khong nhan doi."""
        with self._conn() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT events_migrated FROM trading_ranges WHERE id = ?", (tr_id,)).fetchone()
            if row is None or row[0]:
                return 0
            for event in tr_logic.number_events(events):
                conn.execute(f"INSERT OR IGNORE INTO tr_events ({', '.join(EVENT_COLUMNS)}) "
                             f"VALUES ({', '.join('?' for _ in EVENT_COLUMNS)})",
                             [{**event, "tr_id": tr_id}.get(k) for k in EVENT_COLUMNS])
            conn.execute("UPDATE trading_ranges SET events_migrated = 1 WHERE id = ?", (tr_id,))
            self._renumber(conn, tr_id)
        return len(events)

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

    def version(self) -> tuple[int, ...]:
        """Doi moi khi ke hoach doi (moi lan doi deu sinh snapshot): dung lam khoa cache.

        TM - #WYCKOFF-TR: them moc ghi TR/event, vi snapshot la phan phu, loi thi van phai bo cache.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT (SELECT COALESCE(MAX(rowid), 0) FROM plan_snapshots), "
                "(SELECT COALESCE(MAX(updated_at), 0) FROM trading_ranges), "
                "(SELECT COALESCE(MAX(updated_at), 0) FROM tr_events), "
                "(SELECT COUNT(*) FROM tr_events), "
                # TM - #TR-BRANCH - revision gan vao lich su xac suat sau snapshot
                "(SELECT COUNT(*) FROM tr_prob_history WHERE plan_revision IS NOT NULL)").fetchone()
        return tuple(int(v) for v in row)

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
