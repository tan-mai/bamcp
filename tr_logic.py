# TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
"""Logic thuan cua TR khoa: danh muc su kien Wyckoff, kiem tra event va pha, so VSA, nhan,
marker, so sanh hai ban TR. Khong dong vao DB hay mang: server truyen ham doc nen vao.

Viec gan nhan la cua Claude (hoac Tan). O day chi kiem tra tinh hop le va tinh san so VSA,
khong tu phat hien su kien.
"""
from __future__ import annotations

import bisect
import math
import uuid
from dataclasses import dataclass
from typing import Any, Callable

SCHEMATICS = ("accumulation", "reaccumulation", "distribution", "redistribution", "undetermined")
TR_STATUSES = ("hypothesis", "confirmed", "failed", "completed")
OLD_TR_STATUSES = ("completed", "failed")
PHASES = ("A", "B", "C", "D", "E")
EVENT_STATUSES = ("tentative", "confirmed", "failed")
CREATORS = ("claude", "tan")
LEVEL_KEYS = ("creek", "ice", "ar_level", "st_level")
FAMILY = {"accumulation": "acc", "reaccumulation": "acc", "distribution": "dist", "redistribution": "dist"}
# type -> (ho so do, pha thong thuong, phia nen). Ban phan phoi cua AR/ST/TEST tach rieng (_D) de
# suy ra dung phia nen; y nghia van nhu ban tich luy.
EVENT_TYPES: dict[str, tuple[str, str, str]] = {
    "PS": ("acc", "A", "low"), "SC": ("acc", "A", "low"), "ST": ("acc", "A", "low"),
    "AR": ("acc", "A", "high"),
    "ST_B": ("acc", "B", "low"), "mSOW": ("acc", "B", "low"), "UA": ("acc", "B", "high"),
    "SPRING": ("acc", "C", "low"), "SHAKEOUT": ("acc", "C", "low"), "TEST": ("acc", "C", "low"),
    "SOS": ("acc", "D", "high"), "JAC": ("acc", "D", "high"),
    "LPS": ("acc", "D", "low"), "BU": ("acc", "D", "low"),
    "PSY": ("dist", "A", "high"), "BC": ("dist", "A", "high"), "ST_D": ("dist", "A", "high"),
    "AR_D": ("dist", "A", "low"),
    "UT": ("dist", "B", "high"), "mSOS": ("dist", "B", "high"),
    "UTAD": ("dist", "C", "high"), "TEST_D": ("dist", "C", "high"),
    "SOW": ("dist", "D", "low"), "LPSY": ("dist", "D", "high"),
}
_TYPE_LOOKUP = {name.upper(): name for name in EVENT_TYPES}
# Event SOS/SOW chi chac khi co nhip hoi giu duoc: LPS/LPSY sau no
CONFIRMS = {"SOS": "LPS", "SOW": "LPSY"}
EARLY_TYPES = ("PS", "PSY")      # duoc nam truoc since cua TR
BEFORE_SINCE_BARS = 3
PRICE_TOLERANCE = 0.001
VOL_PERIOD = 20
DELETE_WINDOW_MS = 24 * 3_600_000


@dataclass
class Ctx:
    """Nhung gi logic can tu server: doc nen, doi thoi gian, gio hien tai, nguoi ghi."""
    bars_at: Callable[[str, int], list[dict[str, Any]]]   # nen DA DONG co open_time <= t, toi da 21
    parse_time: Callable[[Any], int | None]
    timeframes: list[str]
    span_ms: Callable[[str], int]
    stamp: Callable[[int], str]
    now_ms: int
    created_by: str = "claude"


# ---------------------------------------------------------------- danh muc

def event_type(value: Any) -> str:
    name = _TYPE_LOOKUP.get(str(value or "").strip().upper())
    if name is None:
        raise ValueError(f"type phai la mot trong {list(EVENT_TYPES)}, nhan duoc {value!r}")
    return name


def side_of(kind: str) -> str:
    return EVENT_TYPES.get(kind, ("", "", "low"))[2]


def schematic_allows(schematic: str, kind: str) -> bool:
    family = FAMILY.get(schematic)
    return family is None or kind not in EVENT_TYPES or EVENT_TYPES[kind][0] == family


def usual_phase(kind: str) -> str | None:
    return EVENT_TYPES[kind][1] if kind in EVENT_TYPES else None


def family_of(schematic: str | None) -> str | None:
    return FAMILY.get(schematic or "")


def _choice(value: Any, allowed: tuple[str, ...], name: str) -> str:
    text = str(value or "").strip().lower()
    if text not in allowed:
        raise ValueError(f"{name} phai la mot trong {list(allowed)}, nhan duoc {value!r}")
    return text


def clean_schematic(value: Any) -> str:
    return _choice(value, SCHEMATICS, "schematic")


def clean_tr_status(value: Any) -> str:
    return _choice(value, TR_STATUSES, "status")


def clean_event_status(value: Any) -> str:
    return _choice(value, EVENT_STATUSES, "status")


def _price(value: Any, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} phai la so, nhan duoc {value!r}") from None
    if not math.isfinite(out) or out <= 0:
        raise ValueError(f"{name} phai > 0")
    return out


def clean_levels(raw: Any, current: dict[str, Any] | None = None) -> dict[str, float]:
    """Gop vao levels dang co. Gia tri null xoa muc do."""
    if not isinstance(raw, dict):
        raise ValueError("levels phai la object {creek?, ice?, ar_level?, st_level?}")
    unknown = sorted(set(raw) - set(LEVEL_KEYS))
    if unknown:
        raise ValueError(f"levels co khoa khong ho tro: {unknown}; chi nhan {list(LEVEL_KEYS)}")
    out = dict(current or {})
    for key, value in raw.items():
        if value is None or value == "":
            out.pop(key, None)
        else:
            out[key] = _price(value, f"levels.{key}")
    return out


def clean_event_ref(value: Any) -> str | None:
    """'TEST#3' hoac 'TEST'. Setup co the giao dich mot event CHUA xay ra nen khong doi phai co that."""
    text = str(value or "").strip()
    if not text:
        return None
    kind, _, number = text.partition("#")
    name = event_type(kind)
    if not number:
        return name
    if not number.strip().isdigit() or int(number) < 1:
        raise ValueError(f"event_ref dang TYPE#n voi n >= 1, nhan duoc {value!r}")
    return f"{name}#{int(number)}"


def ref_type(ref: str | None) -> str | None:
    return str(ref).split("#", 1)[0] if ref else None


def ref_label(ref: str | None) -> str | None:
    return str(ref).replace("#", " ") if ref else None


# ---------------------------------------------------------------- VSA

def vsa(bars: list[dict[str, Any]], period: int = VOL_PERIOD) -> dict[str, float | None]:
    """So VSA cua nen cuoi trong `bars` so voi `period` nen TRUOC no (cung cach tinh SMA cua watcher)."""
    bar = bars[-1]
    high, low, close, volume = (float(bar[k]) for k in ("high", "low", "close", "volume"))
    spread = high - low
    out: dict[str, float | None] = {
        "vol_ratio": None, "spread_ratio": None,
        "close_pct": round((close - low) / spread * 100, 1) if spread > 0 else None,
    }
    prior = bars[-1 - period:-1]
    if len(prior) == period:
        avg_vol = sum(float(b["volume"]) for b in prior) / period
        avg_spread = sum(float(b["high"]) - float(b["low"]) for b in prior) / period
        out["vol_ratio"] = round(volume / avg_vol, 2) if avg_vol > 0 else None
        out["spread_ratio"] = round(spread / avg_spread, 2) if avg_spread > 0 else None
    return out


# ---------------------------------------------------------------- dang chuan cua TR

def number_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """seq = thu tu theo thoi gian trong TR (1..n); n = thu tu trong cung type, dung cho 'TEST#2'."""
    ordered = sorted((dict(e) for e in events),
                     key=lambda e: (int(e["t"]), int(e.get("created_at") or 0), e["type"]))
    counts: dict[str, int] = {}
    for seq, event in enumerate(ordered, start=1):
        counts[event["type"]] = counts.get(event["type"], 0) + 1
        event["seq"], event["n"] = seq, counts[event["type"]]
    for event in ordered:
        event["ref"] = f"{event['type']}#{event['n']}"
        event["label"] = event["type"] if counts[event["type"]] == 1 else f"{event['type']} {event['n']}"
    return ordered


def _normalize_event(raw: dict[str, Any], timeframe: str) -> dict[str, Any]:
    kind = _TYPE_LOOKUP.get(str(raw.get("type") or "").upper(), str(raw.get("type") or ""))
    return {
        "id": raw.get("id"), "type": kind, "t": int(raw["t"]), "tf": raw.get("tf") or timeframe,
        "price": float(raw["price"]), "side": raw.get("side") or side_of(kind),
        "vol_ratio": raw.get("vol_ratio"), "spread_ratio": raw.get("spread_ratio"),
        "close_pct": raw.get("close_pct"), "status": raw.get("status") or "tentative",
        "confirmed_by": raw.get("confirmed_by"), "note": raw.get("note"),
        "created_by": raw.get("created_by"), "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
    }


def _phase_key(phase: dict[str, Any]) -> int:
    return PHASES.index(phase["phase"]) if phase.get("phase") in PHASES else len(PHASES)


def phases_at(phases: list[dict[str, Any]], t: int) -> list[str]:
    """Cac pha chua moc t. Hai dau deu tinh: event o bien (ST ket thuc A, mo B) thuoc ca hai pha."""
    return [p["phase"] for p in phases
            if int(p["start_t"]) <= t and (p.get("end_t") is None or t <= int(p["end_t"]))]


def _group_of(event: dict[str, Any], phases: list[dict[str, Any]]) -> str | None:
    inside = phases_at(phases, event["t"])
    usual = usual_phase(event["type"])
    if usual in inside:
        return usual
    return inside[-1] if inside else None


def decorate_tr(tr: dict[str, Any] | None) -> dict[str, Any] | None:
    """Dang day du cua TR. Doc duoc ca TR va snapshot cu (events chi co {type, t, price})."""
    if not tr:
        return tr
    out = dict(tr)
    timeframe = str(tr.get("timeframe") or "15m")
    out["schematic"] = tr.get("schematic") or "undetermined"
    out["status"] = tr.get("status") or ("completed" if tr.get("superseded_by") else "hypothesis")
    out["levels"] = dict(tr.get("levels") or {})
    out["note"] = tr.get("note")
    phases = sorted(({"phase": p["phase"], "start_t": int(p["start_t"]),
                      "end_t": None if p.get("end_t") is None else int(p["end_t"]),
                      "note": p.get("note"), "id": p.get("id")} for p in tr.get("phases") or []),
                    key=_phase_key)
    out["phases"] = phases
    out["current_phase"] = next((p["phase"] for p in reversed(phases) if p["end_t"] is None), None)
    events = number_events([_normalize_event(e, timeframe) for e in tr.get("events") or []])
    labels = {e["id"]: e["label"] for e in events if e["id"]}
    for event in events:
        if not event["id"]:
            event["id"] = f"legacy-{event['seq']}"
        event["phase"] = _group_of(event, phases)
        event["confirmed_by_label"] = labels.get(event.get("confirmed_by"))
    out["events"] = events
    return out


def virtual_tr(clean: dict[str, Any]) -> dict[str, Any]:
    """TR chua luu: dung de kiem tra event/pha truoc khi tao, de khong bao gio tao mot nua."""
    return decorate_tr({"id": None, "timeframe": clean["timeframe"], "tr_high": clean["tr_high"],
                        "tr_low": clean["tr_low"], "since": clean["since"], "events": [], "phases": []})


def same_anchors(tr: dict[str, Any], clean: dict[str, Any]) -> bool:
    """Cung hai neo gia va since. TR khai trong setup co the khong co since: coi la khac."""
    try:
        return (int(tr["since"]) == int(clean["since"])
                and all(math.isclose(float(tr[k]), float(clean[k]), rel_tol=1e-9) for k in ("tr_high", "tr_low")))
    except (KeyError, TypeError, ValueError):
        return False


# ---------------------------------------------------------------- dau vao cua save_scenarios

def validate_tr_input(raw: Any, *, parse_time: Callable[[Any], int | None],
                      timeframes: list[str]) -> dict[str, Any]:
    """Tham so tr cua save_scenarios. Event nhan ca dang cu {type, t, price} lan dang moi."""
    if not isinstance(raw, dict):
        raise ValueError("tr phai la object")
    timeframe = str(raw.get("timeframe") or "15m").strip().lower()
    if timeframe not in timeframes:
        raise ValueError(f"tr.timeframe phai la mot trong {timeframes}")
    high, low = _price(raw.get("tr_high"), "tr_high"), _price(raw.get("tr_low"), "tr_low")
    if low >= high:
        raise ValueError("tr_low phai nho hon tr_high")
    since = _moment(raw.get("since"), parse_time, "tr.since")
    events = raw.get("events") or []
    if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
        raise ValueError("tr.events phai la list cac object {type, t, price, ...}")
    phases = raw.get("phases")
    if phases is not None and not isinstance(phases, list):
        raise ValueError("tr.phases phai la list")
    out = {"timeframe": timeframe, "tr_high": high, "tr_low": low, "since": since,
           "redraw_rule": str(raw.get("redraw_rule") or "").strip() or None,
           "events": events, "phases": phases or [], "fields": {}}
    if raw.get("schematic"):
        out["fields"]["schematic"] = clean_schematic(raw["schematic"])
    if raw.get("status"):
        out["fields"]["status"] = clean_tr_status(raw["status"])
    if raw.get("levels") is not None:
        out["fields"]["levels"] = raw["levels"]
    if raw.get("note") is not None:
        out["fields"]["note"] = str(raw["note"]).strip() or None
    if out["redraw_rule"]:
        out["fields"]["redraw_rule"] = out["redraw_rule"]
    return out


def split_merge(tr: dict[str, Any], events: list[dict[str, Any]], parse_time: Callable[[Any], int | None]
                ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gui lai ca danh sach event (dang cu) khong duoc nhan doi: trung type + t la event da co."""
    known = {(e["type"], int(e["t"])): e for e in tr["events"]}
    adds, updates = [], []
    for raw in events:
        try:
            key = (event_type(raw.get("type")), _moment(raw.get("t"), parse_time, "t"))
        except ValueError:
            adds.append(raw)          # de plan_changes bao loi dung cho
            continue
        old = known.get(key)
        if old is None:
            adds.append(raw)
            continue
        change: dict[str, Any] = {"id": old["id"]}
        if raw.get("status") and str(raw["status"]).strip().lower() != old["status"]:
            change["status"] = raw["status"]
        if raw.get("note") is not None and (str(raw["note"]).strip() or None) != old.get("note"):
            change["note"] = raw["note"]
        if raw.get("price") is not None:
            try:
                if not math.isclose(float(raw["price"]), float(old["price"]), rel_tol=1e-9):
                    change["price"] = raw["price"]
            except (TypeError, ValueError):
                change["price"] = raw["price"]
        if len(change) > 1:
            updates.append(change)
    return adds, updates


# ---------------------------------------------------------------- kiem tra thay doi

def _moment(value: Any, parse_time: Callable[[Any], int | None], name: str) -> int:
    try:
        moment = parse_time(value)
    except ValueError:
        moment = None
    if moment is None:
        raise ValueError(f"{name} khong hop le: {value!r}")
    return int(moment)


def resolve_ref(ref: Any, *pools: list[dict[str, Any]]) -> dict[str, Any]:
    """id cua event, 'TYPE#n' hoac 'TYPE' (khi chi co mot). Pool dau duoc uu tien: ban truoc lan goi,
    de 'TEST#2' van tro dung event Claude da doc du lan goi nay them mot TEST som hon."""
    text = str(ref or "").strip()
    if not text:
        raise ValueError("thieu id hoac ref cua event")
    for pool in pools:
        hit = next((e for e in pool if e.get("id") == text), None)
        if hit is not None:
            return hit
    kind, _, number = text.partition("#")
    try:
        name = event_type(kind)
    except ValueError:
        raise ValueError(f"khong tim thay event {text!r}") from None
    for pool in pools:
        same = [e for e in pool if e["type"] == name]
        if number:
            hit = next((e for e in same if str(e.get("n")) == number.strip()), None)
            if hit is not None:
                return hit
        elif len(same) == 1:
            return same[0]
        elif len(same) > 1:
            raise ValueError(f"co {len(same)} event {name}, ghi ro {name}#n")
    raise ValueError(f"khong tim thay event {text!r}")


def _check_candle(kind: str, tf: str, t: int, price: float, tr: dict[str, Any], ctx: Ctx,
                  warnings: list[str], label: str) -> list[dict[str, Any]]:
    span = ctx.span_ms(tf)
    if kind not in EARLY_TYPES and t < int(tr["since"]) - BEFORE_SINCE_BARS * span:
        raise ValueError(f"{label}: t {ctx.stamp(t)} nam truoc since {ctx.stamp(int(tr['since']))} qua "
                         f"{BEFORE_SINCE_BARS} nen {tf} (chi PS/PSY duoc nam truoc since)")
    bars = ctx.bars_at(tf, t)
    if not bars or int(bars[-1]["open_time"]) != t:
        raise ValueError(f"{label}: t {ctx.stamp(t)} khong phai gio mo cua mot nen {tf} da dong")
    bar = bars[-1]
    low, high = float(bar["low"]), float(bar["high"])
    if not low <= price <= high:
        raise ValueError(f"{label}: price {price:.10g} nam ngoai nen {tf} {ctx.stamp(t)} "
                         f"[{low:.10g}, {high:.10g}]")
    extreme = low if side_of(kind) == "low" else high
    if extreme and abs(price - extreme) / extreme > PRICE_TOLERANCE:
        warnings.append(f"{label}: price {price:.10g} khac {'day' if extreme == low else 'dinh'} nen "
                        f"{extreme:.10g} qua {PRICE_TOLERANCE * 100:g}%")
    return bars


def _new_event(raw: Any, tr: dict[str, Any], ctx: Ctx, warnings: list[str], label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: moi event phai la object {{type, t, price, ...}}")
    kind = event_type(raw.get("type"))
    label = f"{label} {kind}"
    tf = str(raw.get("tf") or tr["timeframe"]).strip().lower()
    if tf not in ctx.timeframes:
        raise ValueError(f"{label}: tf phai la mot trong {ctx.timeframes}")
    t = _moment(raw.get("t"), ctx.parse_time, f"{label}: t")
    price = _price(raw.get("price"), f"{label}: price")
    bars = _check_candle(kind, tf, t, price, tr, ctx, warnings, label)
    creator = str(raw.get("created_by") or ctx.created_by).strip().lower()
    if creator not in CREATORS:
        raise ValueError(f"{label}: created_by phai la mot trong {list(CREATORS)}")
    return {"id": str(uuid.uuid4()), "type": kind, "t": t, "tf": tf, "price": price,
            "side": side_of(kind), **vsa(bars),
            "status": clean_event_status(raw["status"]) if raw.get("status") else "tentative",
            "confirmed_by": None, "note": str(raw.get("note") or "").strip() or None,
            "created_by": creator, "created_at": ctx.now_ms, "updated_at": ctx.now_ms,
            "_confirmed_ref": raw.get("confirmed_by")}


def _phase_time(value: Any, ctx: Ctx, pools: tuple[list[dict[str, Any]], ...], name: str) -> int:
    if isinstance(value, dict):
        if "event" not in value:
            raise ValueError(f"{name} dang object phai la {{event: '<id | TYPE#n>'}}")
        return int(resolve_ref(value["event"], *pools)["t"])
    return _moment(value, ctx.parse_time, name)


def merge_phases(current: list[dict[str, Any]], updates: list[Any], ctx: Ctx,
                 pools: tuple[list[dict[str, Any]], ...]) -> list[dict[str, Any]]:
    """Upsert theo pha. Pha truoc dang mo tu dong tai start cua pha sau. Pha phai lien theo thu tu
    A -> E va khong chong nhau; chi mot pha duoc de end = null."""
    phases = {p["phase"]: dict(p) for p in current}
    seen: set[str] = set()
    for index, raw in enumerate(updates):
        if not isinstance(raw, dict):
            raise ValueError(f"phases[{index}] phai la object {{phase, start, end?, note?}}")
        letter = str(raw.get("phase") or "").strip().upper()
        if letter not in PHASES:
            raise ValueError(f"phases[{index}].phase phai la mot trong {list(PHASES)}")
        if letter in seen:
            raise ValueError(f"pha {letter} khai hai lan trong mot lan goi")
        seen.add(letter)
        row = phases.get(letter) or {"id": str(uuid.uuid4()), "phase": letter, "start_t": None,
                                     "end_t": None, "note": None}
        if raw.get("start") is not None:
            row["start_t"] = _phase_time(raw["start"], ctx, pools, f"pha {letter}.start")
        elif row["start_t"] is None:
            raise ValueError(f"pha {letter} moi can start")
        if "end" in raw:
            row["end_t"] = None if raw["end"] is None else _phase_time(raw["end"], ctx, pools, f"pha {letter}.end")
        if "note" in raw:
            row["note"] = str(raw.get("note") or "").strip() or None
        phases[letter] = row
    ordered = sorted(phases.values(), key=_phase_key)
    for before, after in zip(ordered, ordered[1:]):
        if before["end_t"] is None:
            before["end_t"] = after["start_t"]
    for phase in ordered:
        if phase["end_t"] is not None and phase["end_t"] <= phase["start_t"]:
            raise ValueError(f"pha {phase['phase']}: end {ctx.stamp(phase['end_t'])} phai sau start "
                             f"{ctx.stamp(phase['start_t'])}")
    for before, after in zip(ordered, ordered[1:]):
        if after["start_t"] < before["end_t"]:
            raise ValueError(f"pha {after['phase']} (start {ctx.stamp(after['start_t'])}) chong len hoac "
                             f"dung truoc pha {before['phase']} (end {ctx.stamp(before['end_t'])})")
    return ordered


def plan_changes(tr: dict[str, Any], ctx: Ctx, *, fields: dict[str, Any] | None = None,
                 add_events: list[Any] | None = None, update_events: list[Any] | None = None,
                 phases: list[Any] | None = None) -> dict[str, Any]:
    """Kiem tra MOI thay doi tren mot TR (da decorate) va tra ve ke hoach ghi. Co loi la tu choi
    ca lan goi, liet ke het loi; khong ghi mot nua."""
    fields = dict(fields or {})
    errors: list[str] = []
    warnings: list[str] = []
    before = tr["events"]
    out_fields: dict[str, Any] = {}
    if "schematic" in fields:
        out_fields["schematic"] = clean_schematic(fields["schematic"])
    if "status" in fields:
        out_fields["status"] = clean_tr_status(fields["status"])
    if "levels" in fields:
        out_fields["levels"] = clean_levels(fields["levels"], tr.get("levels"))
    for key in ("note", "redraw_rule"):
        if key in fields:
            out_fields[key] = str(fields[key] or "").strip() or None
    out_fields = {k: v for k, v in out_fields.items() if v != tr.get(k)}
    schematic = out_fields.get("schematic", tr["schematic"])

    inserted: list[dict[str, Any]] = []
    keys = {(e["type"], int(e["t"])) for e in before}
    for index, raw in enumerate(add_events or []):
        try:
            event = _new_event(raw, tr, ctx, warnings, f"add_events[{index}]")
            if (event["type"], event["t"]) in keys:
                raise ValueError(f"add_events[{index}] {event['type']}: da co {event['type']} tai "
                                 f"{ctx.stamp(event['t'])} - doi trang thai qua update_events")
            keys.add((event["type"], event["t"]))
            inserted.append(event)
        except ValueError as exc:
            errors.append(str(exc))

    after = number_events([dict(e) for e in before] + inserted)
    by_id = {e["id"]: e for e in after}
    pools = (before, after)
    touched: set[str] = {e["id"] for e in inserted}
    changes: dict[str, dict[str, Any]] = {}

    def link(event_id: str, ref: Any, label: str) -> None:
        if ref in (None, ""):
            return
        target = resolve_ref(ref, *pools)
        if target["id"] == event_id:
            raise ValueError(f"{label}: confirmed_by khong the la chinh no")
        changes.setdefault(event_id, {})["confirmed_by"] = target["id"]

    for event in inserted:
        try:
            link(event["id"], event.pop("_confirmed_ref", None), f"add_events {event['type']}")
        except ValueError as exc:
            errors.append(str(exc))
    for index, raw in enumerate(update_events or []):
        label = f"update_events[{index}]"
        try:
            if not isinstance(raw, dict):
                raise ValueError(f"{label} phai la object {{id | ref, status?, note?, confirmed_by?, price?}}")
            target = by_id[resolve_ref(raw.get("id") or raw.get("ref"), *pools)["id"]]
            label = f"{label} {target['label']}"
            change = changes.setdefault(target["id"], {})
            if raw.get("status"):
                change["status"] = clean_event_status(raw["status"])
            if "note" in raw:
                change["note"] = str(raw.get("note") or "").strip() or None
            if raw.get("price") is not None:
                price = _price(raw["price"], f"{label}: price")
                _check_candle(target["type"], target["tf"], int(target["t"]), price, tr, ctx, warnings, label)
                change["price"] = price
            if "confirmed_by" in raw:
                if raw["confirmed_by"] in (None, ""):
                    change["confirmed_by"] = None
                else:
                    link(target["id"], raw["confirmed_by"], label)
            touched.add(target["id"])
        except ValueError as exc:
            errors.append(str(exc))

    updates = []
    inserted_ids = {e["id"] for e in inserted}
    for event_id, change in changes.items():
        event = by_id[event_id]
        diff = {k: v for k, v in change.items() if event.get(k) != v}
        event.update(diff)
        if diff and event_id not in inserted_ids:
            updates.append({"id": event_id, **diff})
    for event in after:
        event.pop("_confirmed_ref", None)
    # Ban ghi la ban da danh so va da gop update cung lan goi (vd confirmed_by cua event moi)
    inserted = [by_id[e["id"]] for e in inserted]

    for event in after:
        if event["status"] != "failed" and not schematic_allows(schematic, event["type"]):
            errors.append(f"{event['label']} ({event['type']}) khong khop schematic {schematic}")

    new_phases = None
    if phases:
        try:
            new_phases = merge_phases(tr["phases"], phases, ctx, pools)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("; ".join(errors))

    final_phases = new_phases if new_phases is not None else tr["phases"]
    check = after if new_phases is not None else [e for e in after if e["id"] in touched]
    for event in check:
        inside = phases_at(final_phases, int(event["t"]))
        usual = usual_phase(event["type"])
        if event["status"] != "failed" and inside and usual and usual not in inside:
            warnings.append(f"{event['label']} ({ctx.stamp(int(event['t']))}) nam trong pha "
                            f"{'/'.join(inside)}, thuong thuoc pha {usual}")
    for event in after:
        follow = CONFIRMS.get(event["type"])
        if follow and event["status"] == "confirmed" and not any(
                e["type"] == follow and e["status"] != "failed" and int(e["t"]) > int(event["t"]) for e in after):
            warnings.append(f"{event['label']} confirmed nhung chua co {follow} sau no")

    phases_changed = new_phases is not None and _phase_rows(new_phases) != _phase_rows(tr["phases"])
    return {
        "fields": out_fields, "insert": inserted, "update": updates,
        "phases": new_phases if phases_changed else None,
        "seq": {e["id"]: e["seq"] for e in after},
        "warnings": list(dict.fromkeys(warnings)),
        "changed": bool(out_fields or inserted or updates or phases_changed),
    }


def _phase_rows(phases: list[dict[str, Any]]) -> list[tuple]:
    return [(p["phase"], p["start_t"], p["end_t"], p.get("note")) for p in phases]


def deletable(event: dict[str, Any], now_ms: int) -> bool:
    return int(event.get("created_at") or 0) >= now_ms - DELETE_WINDOW_MS


# ---------------------------------------------------------------- bieu do va review

def bar_open(t: int, opens: list[int], span: int) -> int:
    """Gio mo cua nen (khung dang ve) chua moc t: event M15 22:00 -> nen H1 22:00, nen H4 19:00 (gio VN)."""
    index = bisect.bisect_right(opens, t) - 1
    if index >= 0 and t < opens[index] + span:
        return opens[index]
    return t // span * span if span else t


def markers(tr: dict[str, Any] | None, opens: list[int], span: int) -> list[dict[str, Any]]:
    if not tr:
        return []
    return [{"kind": "wyckoff", "t_bar": bar_open(int(e["t"]), opens, span), "t": int(e["t"]),
             "price": e["price"], "side": e["side"], "label": e["label"], "type": e["type"],
             "status": e["status"], "event_id": e["id"], "tf": e.get("tf")}
            for e in tr.get("events") or []]


def _brief(event: dict[str, Any]) -> dict[str, Any]:
    return {k: event.get(k) for k in ("id", "type", "label", "t", "price", "status", "phase", "vol_ratio")}


def tr_diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """Event them / doi trang thai / bien mat giua hai ban TR, khop theo (type, t) vi snapshot cu
    khong co id. TR ve lai (neo khac) thi moi event cua TR moi tinh la them."""
    old, new = decorate_tr(before), decorate_tr(after)
    if new is None:
        return {"redrawn": False, "added": [], "status_changed": [], "removed": [], "phases_changed": []}
    redrawn = bool(old) and (old.get("id") != new.get("id") or not same_anchors(old, new))
    known = {} if old is None or redrawn else {(e["type"], e["t"]): e for e in old["events"]}
    current = {(e["type"], e["t"]) for e in new["events"]}
    old_phases = {} if old is None or redrawn else {p["phase"]: p for p in old["phases"]}
    return {
        "redrawn": redrawn,
        "added": [_brief(e) for e in new["events"] if (e["type"], e["t"]) not in known],
        "status_changed": [{**_brief(e), "from": known[(e["type"], e["t"])]["status"], "to": e["status"]}
                           for e in new["events"] if (e["type"], e["t"]) in known
                           and known[(e["type"], e["t"])]["status"] != e["status"]],
        "removed": [_brief(e) for key, e in known.items() if key not in current],
        "phases_changed": [{"phase": p["phase"], "start_t": p["start_t"], "end_t": p["end_t"],
                            "was": ({"start_t": old_phases[p["phase"]]["start_t"],
                                     "end_t": old_phases[p["phase"]]["end_t"]}
                                    if p["phase"] in old_phases else None)}
                           for p in new["phases"]
                           if p["phase"] not in old_phases
                           or (old_phases[p["phase"]]["start_t"], old_phases[p["phase"]]["end_t"])
                           != (p["start_t"], p["end_t"])],
    }
