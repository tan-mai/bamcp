# TM - #ENTRY-ALERT - Entry setups & alerts
from __future__ import annotations

import json
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import entry_logic
import sqlite_db

SETUP_FIELDS = ("id", "code", "symbol", "bias_date", "direction", "status", "zone_low",
                "zone_high", "trigger_type", "trigger_params", "confirm_type", "confirm_level",
                "entry", "stop", "target", "max_chase", "invalidate_level", "m15_filter",
                "expires_at", "tr_high", "tr_low", "tr_since", "state_data", "note",
                "created_by", "created_at", "updated_at", "htf", "invalidate_after_confirm",
                # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
                "tr_id", "event_ref", "expects", "phase",
                # TM - #TR-BRANCH - Nhanh kich ban TR
                "branch", "entry_event", "ref_event", "label")
EVENT_FIELDS = ("id", "setup_id", "at", "event", "from_status", "to_status", "tf",
                "candle_open_time", "price", "details", "notified", "notify_error")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _loads(text: Any) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _setup(row: Any) -> dict[str, Any]:
    out = {k: row[k] for k in SETUP_FIELDS}
    out["trigger_params"] = _loads(out["trigger_params"]) or {}
    out["state"] = _loads(out.pop("state_data")) or {}
    out["htf"] = _loads(out["htf"]) or {}
    out["m15_filter"] = bool(out["m15_filter"])
    # TM - #DASHBOARD - Setup dashboard
    out["invalidate_after_confirm"] = bool(out["invalidate_after_confirm"])
    return out


def _event(row: Any) -> dict[str, Any]:
    out = {k: row[k] for k in EVENT_FIELDS}
    out["details"] = _loads(out["details"]) or {}
    out["notified"] = bool(out["notified"])
    return out


class SetupStore:
    def __init__(self, path: Path, now_ms: Callable[[], int] | None = None) -> None:
        self.path = Path(path)
        self.now_ms = now_ms or _now_ms
        sqlite_db.migrate(self.path)

    def _conn(self):
        return closing(sqlite_db.connect(self.path))

    def _next_code(self, conn, symbol: str, bias_date: str) -> str:
        prefix = f"{entry_logic.code_base(symbol)}-{bias_date.replace('-', '')}-"
        taken = {r[0] for r in conn.execute("SELECT code FROM setups WHERE code LIKE ?",
                                            (prefix + "%",))}
        index = 0
        while prefix + entry_logic.code_letters(index) in taken:
            index += 1
        return prefix + entry_logic.code_letters(index)

    # TM - #TR-BRANCH - Ma setup {SYM}-{yyyymmdd}-{L|S}-{ACC|DIST|RNG}-{pha}-{entry_event}-{n}
    @staticmethod
    def _next_numbered(conn, prefix: str) -> str:
        # So sanh bang substr, khong dung LIKE: '_' trong ST_B la ky tu dai dien cua LIKE
        taken = {r[0] for r in conn.execute("SELECT code FROM setups WHERE substr(code, 1, ?) = ?",
                                            (len(prefix), prefix))}
        number = 1
        while f"{prefix}{number}" in taken:
            number += 1
        return f"{prefix}{number}"

    def insert(self, symbol: str, clean: dict[str, Any], created_by: str) -> dict[str, Any]:
        now = self.now_ms()
        sid = str(uuid.uuid4())
        with self._conn() as conn, conn:
            # TM - #TR-BRANCH - giu khoa ghi tu luc chon so thu tu toi luc INSERT
            conn.execute("BEGIN IMMEDIATE")
            # Setup co ten tuong minh dung ma moi; ban ghi khong co branch (goi thang tu kho) giu ma cu
            code = (self._next_numbered(conn, entry_logic.code_prefix(symbol, clean))
                    if clean.get("branch") and clean.get("entry_event")
                    else self._next_code(conn, symbol, clean["bias_date"]))
            conn.execute(
                "INSERT INTO setups (id, code, symbol, bias_date, direction, status, zone_low, "
                "zone_high, trigger_type, trigger_params, confirm_type, confirm_level, entry, stop, "
                "target, max_chase, invalidate_level, m15_filter, expires_at, tr_high, tr_low, "
                "tr_since, state_data, note, created_by, created_at, updated_at, htf, "
                "invalidate_after_confirm, tr_id, event_ref, expects, phase, branch, entry_event, "
                "ref_event, label) VALUES "
                "(?, ?, ?, ?, ?, 'WAITING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, code, symbol.upper(), clean["bias_date"], clean["direction"],
                 clean["zone_low"], clean["zone_high"], clean["trigger_type"],
                 json.dumps(clean["trigger_params"]) if clean["trigger_params"] else None,
                 clean["confirm_type"], clean["confirm_level"], clean["entry"], clean["stop"],
                 clean["target"], clean["max_chase"], clean["invalidate_level"],
                 int(clean["m15_filter"]), clean["expires_at"], clean["tr_high"], clean["tr_low"],
                 clean["tr_since"], json.dumps({}), clean["note"], created_by, now, now,
                 json.dumps(clean["htf"]) if clean.get("htf") else None,
                 int(bool(clean.get("invalidate_after_confirm"))),
                 # TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
                 clean.get("tr_id"), clean.get("event_ref"), clean.get("expects"), clean.get("phase"),
                 # TM - #TR-BRANCH - Nhanh kich ban TR
                 clean.get("branch"), clean.get("entry_event"), clean.get("ref_event"), clean.get("label")))
            conn.execute(
                "INSERT INTO setup_events (setup_id, at, event, from_status, to_status, details) "
                "VALUES (?, ?, 'CREATED', NULL, 'WAITING', ?)",
                (sid, now, json.dumps({"created_by": created_by})))
        return self.get(sid)

    def get(self, ref: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM setups WHERE id = ? OR code = ?",
                               (ref, str(ref).upper())).fetchone()
        if row is None:
            raise ValueError(f"khong tim thay setup {ref!r}")
        return _setup(row)

    def list(self, *, symbol: str | None = None, status: str | None = None,
             bias_date: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM setups", []
        where = []
        if symbol:
            where.append("symbol = ?")
            params.append(symbol.upper())
        if bias_date:
            where.append("bias_date = ?")
            params.append(bias_date)
        statuses = [s.strip().upper() for s in (status or "").split(",") if s.strip()]
        if "ACTIVE" in statuses:
            statuses = [s for s in statuses if s != "ACTIVE"] + [
                s for s in entry_logic.STATUSES if s not in entry_logic.TERMINAL]
        if statuses:
            where.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY bias_date DESC, code"
        with self._conn() as conn:
            return [_setup(r) for r in conn.execute(sql, params).fetchall()]

    def active(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.list(symbol=symbol, status="ACTIVE")

    # TM - #TR-BRANCH - Nhanh kich ban TR
    def by_tr(self, tr_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM setups WHERE tr_id = ? ORDER BY created_at, code",
                                (tr_id,)).fetchall()
        return [_setup(r) for r in rows]

    def prune(self, setup: dict[str, Any], reason: str, details: dict[str, Any]) -> int | None:
        """Sang PRUNED (terminal) chi khi trang thai chua doi tu luc doc: watcher co the vua chuyen setup
        (vd sang FILLED). Chi them state.pruned, khong ghi de state watcher dang giu. Tra id event."""
        now = self.now_ms()
        mark = {"reason": reason, "at": now, **{k: details.get(k) for k in ("tr_id", "branch", "ref", "phase")}}
        with self._conn() as conn, conn:
            cur = conn.execute(
                "UPDATE setups SET status = 'PRUNED', updated_at = ?, "
                "state_data = json_set(COALESCE(NULLIF(state_data, ''), '{}'), '$.pruned', json(?)) "
                "WHERE id = ? AND status = ?",
                (now, json.dumps(mark, default=str), setup["id"], setup["status"]))
            if not cur.rowcount:
                return None
            cur = conn.execute(
                "INSERT INTO setup_events (setup_id, at, event, from_status, to_status, details) "
                "VALUES (?, ?, 'PRUNED', ?, 'PRUNED', ?)",
                (setup["id"], now, setup["status"], json.dumps({**details, "reason": reason}, default=str)))
            return int(cur.lastrowid)

    def note_event(self, setup: dict[str, Any], event: str, details: dict[str, Any]) -> int:
        """Ghi event khong doi trang thai (vd BRANCH_CONFLICT cua setup da FILLED)."""
        with self._conn() as conn, conn:
            cur = conn.execute(
                "INSERT INTO setup_events (setup_id, at, event, from_status, to_status, details) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (setup["id"], self.now_ms(), event, setup["status"], setup["status"],
                 json.dumps(details, default=str)))
            return int(cur.lastrowid)

    def apply(self, setup: dict[str, Any], status: str, state: dict[str, Any],
              events: list[dict[str, Any]], tf: str | None = None) -> list[int]:
        now = self.now_ms()
        ids: list[int] = []
        with self._conn() as conn, conn:
            conn.execute("UPDATE setups SET status = ?, state_data = ?, updated_at = ? WHERE id = ?",
                         (status, json.dumps(state, default=str), now, setup["id"]))
            for ev in events:
                cur = conn.execute(
                    "INSERT INTO setup_events (setup_id, at, event, from_status, to_status, tf, "
                    "candle_open_time, price, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (setup["id"], now, ev["event"], ev.get("from_status"), ev.get("to_status"),
                     tf, ev.get("candle_open_time"), ev.get("price"),
                     json.dumps(ev.get("details") or {}, default=str)))
                ids.append(int(cur.lastrowid))
        return ids

    # TM - #SETUP-PARAMS - Tham so setup theo cap
    def all_params(self) -> dict[str, dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT symbol, params FROM setup_params").fetchall()
        return {r["symbol"]: _loads(r["params"]) or {} for r in rows}

    def param_row(self, symbol: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM setup_params WHERE symbol = ?", (symbol.upper(),)).fetchone()
        return {**dict(row), "params": _loads(row["params"]) or {}} if row else None

    def seed_params(self, seed: dict[str, dict[str, Any]]) -> list[str]:
        """Chi ghi cap chua co dong nao: dong da co (ke ca rong) la lua chon cua nguoi dung."""
        now, added = self.now_ms(), []
        with self._conn() as conn, conn:
            for symbol, params in seed.items():
                cur = conn.execute(
                    "INSERT OR IGNORE INTO setup_params (symbol, params, source, created_at, updated_at) "
                    "VALUES (?, ?, 'seed', ?, ?)", (symbol.upper(), json.dumps(params), now, now))
                if cur.rowcount:
                    added.append(symbol.upper())
        return added

    def save_params(self, params: dict[str, dict[str, Any]], source: str) -> None:
        now = self.now_ms()
        with self._conn() as conn, conn:
            for symbol, values in params.items():
                conn.execute(
                    "INSERT INTO setup_params (symbol, params, source, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET params = excluded.params, "
                    "source = excluded.source, updated_at = excluded.updated_at",
                    (symbol.upper(), json.dumps(values), source, now, now))

    def system_event(self, event: str, details: dict[str, Any], price: float | None = None,
                     at: int | None = None) -> int:
        with self._conn() as conn, conn:
            cur = conn.execute(
                "INSERT INTO setup_events (setup_id, at, event, price, details) VALUES (NULL, ?, ?, ?, ?)",
                (int(at if at is not None else self.now_ms()), event, price,
                 json.dumps(details, default=str)))
            return int(cur.lastrowid)

    def last_system_at(self, event: str) -> int | None:
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(at) FROM setup_events WHERE setup_id IS NULL AND event = ?",
                               (event,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def seen_position(self, key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM setup_events WHERE details LIKE ? LIMIT 1",
                               (f'%"position_key": {json.dumps(key)}%',)).fetchone()
        return row is not None

    def has_candle(self, setup_id: str, candle_open_time: int) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM setup_events WHERE setup_id = ? AND candle_open_time = ? "
                               "LIMIT 1", (setup_id, int(candle_open_time))).fetchone()
        return row is not None

    def mark_notified(self, event_id: int, error: str | None) -> None:
        with self._conn() as conn, conn:
            conn.execute("UPDATE setup_events SET notified = ?, notify_error = ? WHERE id = ?",
                         (0 if error else 1, error, int(event_id)))

    def event(self, event_id: int) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM setup_events WHERE id = ?", (int(event_id),)).fetchone()
        if row is None:
            raise ValueError(f"khong tim thay event {event_id}")
        return _event(row)

    def last_events(self, setup_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not setup_ids:
            return {}
        marks = ", ".join("?" for _ in setup_ids)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM setup_events WHERE id IN (SELECT MAX(id) FROM setup_events "
                f"WHERE setup_id IN ({marks}) GROUP BY setup_id)", setup_ids).fetchall()
        return {r["setup_id"]: _event(r) for r in rows}

    # TM - #DASHBOARD - Setup dashboard
    def last_event_id(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM setup_events").fetchone()
        return int(row[0])

    def events_of(self, setup_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Toan bo event cua nhieu setup, moi setup theo thu tu xay ra."""
        out: dict[str, list[dict[str, Any]]] = {sid: [] for sid in setup_ids}
        if not setup_ids:
            return out
        marks = ", ".join("?" for _ in setup_ids)
        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM setup_events WHERE setup_id IN ({marks}) ORDER BY id",
                                setup_ids).fetchall()
        for row in rows:
            out[row["setup_id"]].append(_event(row))
        return out

    def events(self, *, setup_id: str | None = None, symbol: str | None = None,
               start_ms: int | None = None, end_ms: int | None = None,
               limit: int = 200) -> list[dict[str, Any]]:
        # TM - #TR-BRANCH - kem label, branch, tr_id cua setup
        sql = ("SELECT e.*, s.code AS code, s.symbol AS symbol, s.label AS label, s.branch AS branch, "
               "s.tr_id AS tr_id FROM setup_events e LEFT JOIN setups s ON s.id = e.setup_id")
        where, params = [], []
        if setup_id:
            where.append("e.setup_id = ?")
            params.append(setup_id)
        elif symbol:
            where.append("(s.symbol = ? OR (e.setup_id IS NULL AND e.details LIKE ?))")
            params.extend([symbol.upper(), f"%{symbol.upper()}%"])
        if start_ms is not None:
            where.append("e.at >= ?")
            params.append(int(start_ms))
        if end_ms is not None:
            where.append("e.at <= ?")
            params.append(int(end_ms))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY e.id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows[::-1]:
            item = _event(r)
            item["code"] = r["code"]
            item["symbol"] = r["symbol"]
            # TM - #TR-BRANCH - Nhanh kich ban TR
            item.update(label=r["label"], branch=r["branch"], tr_id=r["tr_id"])
            out.append(item)
        return out
