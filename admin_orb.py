"""Trang admin: quan ly phien ORB (muc 4.9).

# TM - #ORB - ORB Enhancement

Dung chung co che dang nhap cua trang admin hien co (Basic auth o middleware,
setup token khi chua co mat khau). Moi thao tac ghi di qua SessionStore - ghi
file atomic, ghi lich su, bao scheduler dung lai job ngay, khong can restart.

Phan xu ly (handle, preview_opens) tach khoi HTTP de test truc tiep.

# TM - #ORB-RULES - ORB Rule Set
Them muc "Rule ORB" (sua bo rule ORB theo defaults / cap / phien, xem truoc canh
bao rule_infeasible truoc khi luu, bat buoc ly do), news days va lich su rule ORB.
Luu di qua server._apply_orb_rule_changes (POST rules_path) - cung duong voi
update_rules(orb=true). Form phien khong con sua nhom rule (orb.RULE_PARAM_KEYS).
"""

from __future__ import annotations

import html
import json
from typing import Any, Callable
from zoneinfo import available_timezones

import orb
from orb import iso_utc, iso_vn


# ---------------------------------------------------------------- xu ly

def preview_opens(session: dict[str, Any], now_ms: int, params: dict[str, Any] | None = None,
                  count: int = 3) -> list[dict[str, Any]]:
    """count lan mo ke tiep, kem gio local va offset de soi DST."""
    out = []
    for ts in orb.next_opens(session, now_ms, count):
        local = orb.from_ms(ts).astimezone(orb.session_tz(session))
        row: dict[str, Any] = {
            "local": local.strftime("%a %Y-%m-%d %H:%M"),
            "utc_offset": local.strftime("%z")[:3] + ":" + local.strftime("%z")[3:],
            "open_utc": iso_utc(ts),
            "open_vn": iso_vn(ts),
        }
        if params:
            t = orb.session_times(session, local.date(), params)
            row.update(or_end_utc=iso_utc(t["or_end"]), window_end_utc=iso_utc(t["window_end"]),
                       window_end_vn=iso_vn(t["window_end"]))
        out.append(row)
    return out


def _keep_rule_overrides(data: dict[str, Any], original: dict[str, Any] | None
                         ) -> dict[str, Any]:
    """TM - #ORB-RULES - ORB Rule Set: form phien khong sua nhom rule ORB.

    Override rule dang luu cua phien duoc giu nguyen khi luu form; gui gia tri rule
    khac voi ban dang luu thi bao loi - rule doi qua muc Rule ORB (co ly do + history).
    """
    submitted = data.get("overrides")
    try:
        flat = orb.normalize_overrides(submitted, orb.SESSION_OVERRIDE_KEYS)
    except ValueError:
        return data                      # validate_session bao loi o dung o overrides
    kept = {}
    if original:
        kept = {k: v for k, v in orb.normalize_overrides(
            original.get("overrides"), orb.SESSION_OVERRIDE_KEYS).items()
            if k in orb.RULE_PARAM_KEYS}
    changed = sorted(k for k, v in flat.items() if k in orb.RULE_PARAM_KEYS and kept.get(k) != v)
    if changed:
        raise orb.SessionValidationError({"overrides": (
            f"{', '.join(changed)} thuoc bo rule ORB - sua o muc Rule ORB (pham vi phien, "
            "bat buoc ly do, ghi lich su)")})
    return {**data, "overrides": {**{k: v for k, v in flat.items()
                                     if k not in orb.RULE_PARAM_KEYS}, **kept}}


def handle(payload: dict[str, Any], *, sessions: Any, now_ms: int,
           params_for: Callable[[dict[str, Any]], dict[str, Any]] | None = None
           ) -> tuple[int, dict[str, Any]]:
    """Mot thao tac tu trang admin. Tra ve (HTTP status, body JSON).

    action: preview | save | enable | disable | delete
      preview/save: {"session": {...}, "original_id": "" (them moi) | "<id>" (sua)}
      enable/disable/delete: {"session_id": "<id>"}; delete them "confirm": "<id>"
    """
    action = str(payload.get("action") or "").strip().lower()

    def params(session: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return params_for(session) if params_for else None
        except ValueError:
            return None

    try:
        if action in ("preview", "save"):
            data = payload.get("session")
            if not isinstance(data, dict):
                raise ValueError("session phai la object")
            original_id = str(payload.get("original_id") or "").strip().lower() or None
            original = sessions.get(original_id) if original_id else None
            data = _keep_rule_overrides(data, original)   # TM - #ORB-RULES - ORB Rule Set
            if action == "preview":
                if original_id and original is None:
                    raise orb.SessionValidationError(
                        {"session_id": f"khong tim thay phien '{original_id}'"})
                clean = orb.validate_session(data, existing_ids=sessions.ids(),
                                             original=original)
                return 200, {"ok": True, "session": clean,
                             "opens": preview_opens(clean, now_ms, params(clean))}
            clean = sessions.save(data, original_id=original_id)
            verb = "Da cap nhat" if original_id else "Da them"
            state = "dang bat" if clean["enabled"] else "dang tat"
            return 200, {"ok": True, "session": clean,
                         "opens": preview_opens(clean, now_ms, params(clean)),
                         "message": f"{verb} phien {clean['session_id']} ({state}). "
                                    "Lich job da duoc dung lai, khong can restart."}

        sid = str(payload.get("session_id") or "").strip().lower()
        if not sid:
            raise ValueError("thieu session_id")
        if action in ("enable", "disable"):
            clean = sessions.set_enabled(sid, action == "enable")
            message = (f"Da bat phien {sid}." if action == "enable" else
                       f"Da tat phien {sid}. Job va watch (neu dang chay) duoc go ngay.")
            return 200, {"ok": True, "session": clean, "message": message}
        if action == "delete":
            if str(payload.get("confirm") or "").strip().lower() != sid:
                raise ValueError("Xoa phien can xac nhan: gui confirm = session_id")
            sessions.delete(sid)
            return 200, {"ok": True,
                         "message": f"Da xoa phien {sid}. Journal, state va ket qua backtest "
                                    "cu cua phien van giu nguyen."}
        raise ValueError("action phai la preview, save, enable, disable hoac delete")
    except orb.SessionValidationError as exc:
        return 400, {"error": "Du lieu chua hop le - xem loi o tung o.", "errors": exc.errors}
    except ValueError as exc:
        return 400, {"error": str(exc)}


# ---------------------------------------------------------------- HTML

PAGE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BAMCP — Phiên ORB</title>
<style>
  :root {
    --bg: #f6f7f9; --card: #fff; --ink: #16181d; --muted: #6b7280;
    --line: #e3e6ea; --accent: #1f6feb; --ok: #0f7b47; --err: #b4232c;
    --warn-bg: #fff8e6; --warn-line: #f0d089;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1115; --card: #171a20; --ink: #e6e8ec; --muted: #9aa3af;
      --line: #262b33; --accent: #4a90e2; --ok: #49c17e; --err: #ef6b73;
      --warn-bg: #2a2415; --warn-line: #5c4d20;
    }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  .wrap { max-width: 980px; margin: 0 auto; padding: 32px 16px 64px; }
  h1 { font-size: 21px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin: 0 0 24px; }
  .sub a { color: var(--accent); }
  .card { background: var(--card); border: 1px solid var(--line);
    border-radius: 10px; padding: 20px; margin-bottom: 18px; }
  .card h2 { font-size: 15px; margin: 0 0 16px; letter-spacing: .01em; }
  label { display:block; font-size:13px; color:var(--muted); margin:14px 0 5px; }
  input[type=text], input[type=time], input[type=password], select {
    width:100%; padding:9px 11px; border:1px solid var(--line); border-radius:7px;
    background:var(--bg); color:var(--ink); font:inherit; font-size:14px; }
  input:focus, select:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  input:disabled { opacity:.6; }
  .row { display:flex; gap:14px; flex-wrap:wrap; } .row > * { flex:1; min-width:180px; }
  .hint { font-size:12px; color:var(--muted); margin-top:5px; }
  .ferr { font-size:12px; color:var(--err); margin-top:4px; min-height:0; }
  .days { display:flex; flex-wrap:wrap; gap:6px 14px; }
  .days label, .check label { display:inline-flex; align-items:center; gap:6px; margin:0;
    color:var(--ink); font-size:14px; }
  .check { margin-top:16px; }
  input[type=checkbox] { width:16px; height:16px; accent-color:var(--accent); }
  .actions { display:flex; gap:10px; align-items:center; margin-top:22px; flex-wrap:wrap; }
  button { font:inherit; font-size:14px; padding:9px 18px; border-radius:7px;
    border:1px solid var(--line); background:var(--card); color:var(--ink); cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.danger { color:var(--err); }
  button:disabled { opacity:.55; cursor:default; }
  #msg { margin-top:16px; font-size:13px; white-space:pre-wrap; }
  #msg.ok { color:var(--ok); } #msg.err { color:var(--err); }
  .warn { background:var(--warn-bg); border:1px solid var(--warn-line);
    border-radius:8px; padding:13px 15px; font-size:13px; margin-bottom:18px; }
  code { font-family: ui-monospace,"Cascadia Code",Consolas,monospace; font-size:12.5px; }
  .scroll { overflow-x:auto; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; font-weight:600; color:var(--muted); font-size:12px;
    padding:6px 8px 6px 0; border-bottom:1px solid var(--line); white-space:nowrap; }
  td { padding:8px 8px 8px 0; border-bottom:1px solid var(--line); vertical-align:top; }
  tr:last-child td { border-bottom:none; }
  td.id { font-family:ui-monospace,Consolas,monospace; font-weight:600; }
  td.act { text-align:right; white-space:nowrap; }
  td.act button { padding:4px 10px; font-size:12px; margin-left:4px; }
  .off td:not(.act) { opacity:.5; }
  .pill { display:inline-block; font-size:11px; padding:1px 8px; border-radius:99px;
    border:1px solid var(--line); white-space:nowrap; }
  .pill.ok { color:var(--ok); border-color:var(--ok); }
  .pill.err { color:var(--err); border-color:var(--err); }
  .small { font-size:12px; color:var(--muted); }
  details { margin-top:18px; border-top:1px solid var(--line); padding-top:12px; }
  summary { cursor:pointer; font-size:13px; font-weight:600; }
  #preview { margin-top:18px; }
  .hist li { font-size:12px; color:var(--muted); padding:3px 0; }
  .hist ul { list-style:none; margin:0; padding:0; }
  .hist b { color:var(--ink); }
  /* TM - #ORB-RULES - ORB Rule Set */
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px;
    margin:14px 0 4px; }
  .stat { border:1px solid var(--line); border-radius:8px; padding:9px 11px; }
  .stat b { display:block; font-size:16px; }
  .stat span { font-size:12px; color:var(--muted); }
  .stat.bad b { color:var(--err); }
  table.rules td { vertical-align:middle; }
  table.rules input, table.rules select { padding:6px 9px; font-size:13px; min-width:110px; }
  table.rules tr.lock td { opacity:.55; }
  td.eff { white-space:nowrap; }
  td.eff.changed { color:var(--accent); font-weight:600; }
  .warn ul { margin:6px 0 0; padding-left:18px; }
  .okbox { font-size:13px; color:var(--ok); margin-top:10px; }
  #r_msg, #n_msg { margin-top:12px; font-size:13px; white-space:pre-wrap; }
  #r_msg.ok, #n_msg.ok { color:var(--ok); } #r_msg.err, #n_msg.err { color:var(--err); }
</style>
</head>
<body><div class="wrap">

<h1>BAMCP — Phiên ORB</h1>
<p class="sub">Lưu vào <code>__STORE_PATH__</code>. <a href="__ADMIN_PATH__">← Cài đặt chung</a>
 · Giờ server lúc tải trang: __NOW__</p>

__SETUP_BANNER__

__RULES_CARD__

<div class="card">
  <h2>Danh sách phiên</h2>
  <div class="scroll"><table>
    <thead><tr><th>Phiên</th><th>Timezone</th><th>Giờ mở</th><th>Mở kế tiếp (UTC / VN)</th>
    <th>Trạng thái</th><th>Job</th><th>Lần chạy gần nhất</th><th></th></tr></thead>
    <tbody>__ROWS__</tbody>
  </table></div>
  <div class="hint">Tắt phiên thì scheduler gỡ job ngay; nếu phiên đang ở giai đoạn theo dõi M5 thì dừng luôn. Xóa phiên không xóa journal hay kết quả backtest cũ.</div>
</div>

<form id="f" class="card" autocomplete="off">
  <h2 id="form_title">Thêm phiên</h2>
  <div class="row">
    <div>
      <label for="session_id">session_id</label>
      <input type="text" id="session_id" placeholder="vd: ldn, ny, tokyo">
      <div class="hint">Chữ thường, số, <code>_</code>. Không sửa được sau khi tạo.</div>
      <div class="ferr" id="e_session_id"></div>
    </div>
    <div>
      <label for="name">Tên</label>
      <input type="text" id="name" maxlength="50" placeholder="vd: London">
      <div class="ferr" id="e_name"></div>
    </div>
  </div>
  <div class="row">
    <div>
      <label for="timezone">Timezone (IANA)</label>
      <input type="text" id="timezone" list="tzlist" placeholder="Europe/London">
      <datalist id="tzlist">__TZ_OPTIONS__</datalist>
      <div class="ferr" id="e_timezone"></div>
    </div>
    <div>
      <label for="open_time">Giờ mở (giờ local của phiên)</label>
      <input type="time" id="open_time" step="900" value="08:00">
      <div class="hint">Phút phải là 00, 15, 30 hoặc 45 để trùng mốc nến M15.</div>
      <div class="ferr" id="e_open_time"></div>
    </div>
  </div>
  <label>Ngày giao dịch</label>
  <div class="days">__DAY_BOXES__</div>
  <div class="ferr" id="e_trade_days"></div>
  <div class="row">
    <div>
      <label for="holiday_calendar">Lịch nghỉ lễ</label>
      <select id="holiday_calendar">
        <option value="none">none — không nghỉ</option>
        <option value="US">US — NYSE</option>
        <option value="UK">UK — LSE / bank holiday</option>
      </select>
      <div class="ferr" id="e_holiday_calendar"></div>
    </div>
    <div>
      <label for="trade_window_minutes">Trade window (phút)</label>
      <input type="text" inputmode="numeric" id="trade_window_minutes" placeholder="__DEF_WINDOW__ (giá trị chung)">
      <div class="hint">15–720, bội số của 15. Để trống = dùng giá trị chung.</div>
      <div class="ferr" id="e_trade_window_minutes"></div>
    </div>
    <div>
      <label for="max_trades">Số lệnh tối đa / phiên</label>
      <input type="text" inputmode="numeric" id="max_trades" placeholder="__DEF_MAX__ (giá trị chung)">
      <div class="ferr" id="e_max_trades"></div>
    </div>
  </div>
  <div class="check"><label><input type="checkbox" id="enabled" checked> Bật phiên</label></div>

  <details id="ov_box">
    <summary>Ghi đè tham số kỹ thuật entry / exit / filters (tùy chọn)</summary>
    <div class="hint">Ô để trống = dùng giá trị chung trong config (hiện trong ô). Rule ORB của phiên
    (OR/ATR, tp_r, buffer, bias, tin...) sửa ở mục <b>Rule ORB</b>, phạm vi "Phiên".</div>
    <div class="row">__OVERRIDE_FIELDS__</div>
    <div class="ferr" id="e_overrides"></div>
  </details>

  __SETUP_FIELD__

  <div id="preview"></div>

  <div class="actions">
    <button type="submit" class="primary" id="save">Lưu</button>
    <button type="button" id="btn_preview">Xem trước 3 lần mở</button>
    <button type="button" id="btn_new">Thêm phiên mới</button>
  </div>
  <div id="msg"></div>
</form>

<div class="card hist">
  <h2>Thay đổi gần đây</h2>
  __HISTORY__
</div>

__NEWS_CARD__

__RULES_HISTORY__

<script>
const SESSIONS = __SESSIONS_JSON__;
const OVERRIDE_KEYS = __OVERRIDE_KEYS__;
const DAYS = __DAYS__;
const ACTION_PATH = __ACTION_PATH__;
const RULES = __RULES_JSON__;          // TM - #ORB-RULES - ORB Rule Set
const RULES_PATH = __RULES_PATH__;
const FIELDS = ["session_id", "name", "timezone", "open_time", "trade_days",
                "holiday_calendar", "trade_window_minutes", "max_trades", "overrides"];
const $ = (id) => document.getElementById(id);
let originalId = "";

function say(text, ok) { $("msg").textContent = text; $("msg").className = ok ? "ok" : "err"; }
function clearErrors() { for (const f of FIELDS) { const el = $("e_" + f); if (el) el.textContent = ""; } }
function showErrors(errors) {
  clearErrors();
  for (const [field, text] of Object.entries(errors || {})) {
    const el = $("e_" + field) || $("e_overrides");
    el.textContent = text;
    if (field === "overrides") $("ov_box").open = true;
  }
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function formData() {
  const overrides = {};
  for (const key of OVERRIDE_KEYS) {
    const v = $("ov_" + key).value.trim();
    if (v !== "") overrides[key] = v;
  }
  return {
    session_id: $("session_id").value.trim(),
    name: $("name").value.trim(),
    timezone: $("timezone").value.trim(),
    open_time: $("open_time").value.trim(),
    trade_days: DAYS.filter((d) => $("d_" + d).checked),
    holiday_calendar: $("holiday_calendar").value,
    trade_window_minutes: $("trade_window_minutes").value.trim(),
    max_trades: $("max_trades").value.trim(),
    enabled: $("enabled").checked,
    overrides: overrides,
  };
}

function fill(s) {
  originalId = s ? s.session_id : "";
  $("form_title").textContent = s ? "Sửa phiên " + s.session_id : "Thêm phiên";
  $("session_id").value = s ? s.session_id : "";
  $("session_id").disabled = !!s;
  $("name").value = s ? s.name : "";
  $("timezone").value = s ? s.timezone : "";
  $("open_time").value = s ? s.open_time : "08:00";
  for (const d of DAYS) $("d_" + d).checked = s ? s.trade_days.includes(d) : !["Sat", "Sun"].includes(d);
  $("holiday_calendar").value = s ? s.holiday_calendar : "none";
  $("trade_window_minutes").value = s && s.trade_window_minutes != null ? s.trade_window_minutes : "";
  $("max_trades").value = s && s.max_trades != null ? s.max_trades : "";
  $("enabled").checked = s ? s.enabled : true;
  const ov = (s && s.overrides) || {};
  for (const key of OVERRIDE_KEYS) $("ov_" + key).value = key in ov ? String(ov[key]) : "";
  $("ov_box").open = Object.keys(ov).length > 0;
  clearErrors(); $("preview").innerHTML = ""; say("", true);
}

async function send(payload, path) {
  const token = $("setup_token");
  if (token) payload.setup_token = token.value;
  const res = await fetch(path || ACTION_PATH, {method: "POST", headers: {"Content-Type": "application/json"},
                                                body: JSON.stringify(payload)});
  let out;
  try { out = await res.json(); } catch { out = {error: "server tra ve du lieu la"}; }
  return {ok: res.ok, out};
}

function renderPreview(opens) {
  if (!opens || !opens.length) { $("preview").innerHTML = '<div class="hint">Không có lần mở nào trong 400 ngày tới.</div>'; return; }
  const rows = opens.map((o) => "<tr><td>" + esc(o.local) + " <span class='small'>(UTC" + esc(o.utc_offset) + ")</span></td><td>"
    + esc(o.open_utc) + "</td><td>" + esc(o.open_vn) + "</td><td>" + esc(o.window_end_vn || "") + "</td></tr>").join("");
  $("preview").innerHTML = "<label>3 lần mở kế tiếp — kiểm tra DST</label><div class='scroll'><table><thead><tr>"
    + "<th>Giờ local</th><th>UTC</th><th>Giờ VN</th><th>Hết trade window (VN)</th></tr></thead><tbody>"
    + rows + "</tbody></table></div>";
}

async function preview(quiet) {
  const {ok, out} = await send({action: "preview", session: formData(), original_id: originalId});
  if (!ok) { showErrors(out.errors); if (!quiet) say(out.error || "Không xem trước được.", false); $("preview").innerHTML = ""; return; }
  clearErrors(); renderPreview(out.opens); if (!quiet) say("", true);
}

let timer = null;
function previewSoon() { clearTimeout(timer); timer = setTimeout(() => preview(true), 400); }
for (const id of ["timezone", "open_time", "holiday_calendar", "trade_window_minutes"]) {
  $(id).addEventListener("change", previewSoon);
}
for (const d of DAYS) $("d_" + d).addEventListener("change", previewSoon);

$("btn_preview").addEventListener("click", () => preview(false));
$("btn_new").addEventListener("click", () => fill(null));

$("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("save").disabled = true; say("Đang lưu...", true);
  const {ok, out} = await send({action: "save", session: formData(), original_id: originalId});
  $("save").disabled = false;
  if (!ok) { showErrors(out.errors); say(out.error || "Lưu thất bại.", false); return; }
  clearErrors(); renderPreview(out.opens); say(out.message || "Đã lưu.", true);
  setTimeout(() => location.reload(), 1500);
});

document.querySelectorAll("button[data-act]").forEach((b) => {
  b.addEventListener("click", async () => {
    const sid = b.dataset.sid, act = b.dataset.act;
    if (act === "edit") { fill(SESSIONS[sid]); $("f").scrollIntoView({behavior: "smooth"}); return; }
    const payload = {action: act, session_id: sid};
    if (act === "delete") {
      if (!confirm("Xóa phiên " + sid + "?\\nJournal và kết quả backtest cũ vẫn được giữ nguyên.")) return;
      payload.confirm = sid;
    }
    say("Đang xử lý...", true);
    const {ok, out} = await send(payload);
    if (!ok) { say(out.error || "Không thực hiện được.", false); return; }
    say(out.message || "Xong.", true);
    setTimeout(() => location.reload(), 1000);
  });
});

// TM - #ORB-RULES - ORB Rule Set: muc Rule ORB + news days
if (RULES) {
  const SRC = {override: "tạm", session: "phiên", symbol: "cặp", defaults: "defaults"};
  const KEYS = Object.keys(RULES.fields);
  const scope = () => RULES.scopes.find((s) => s.key === $("r_scope").value) || RULES.scopes[0];
  const fmt = (v) => v === undefined || v === null ? "" : (typeof v === "boolean" ? (v ? "true" : "false") : String(v));
  const rsay = (text, ok) => { $("r_msg").textContent = text; $("r_msg").className = ok ? "ok" : "err"; };
  const warnBox = (list, title) => list && list.length
    ? '<div class="warn"><strong>' + esc(title) + '</strong><ul>' + list.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul></div>"
    : "";

  function inherited(sc) {
    // gia tri lop duoi (khi o nay de trong)
    if (sc.layer === "session") {
      const sym = RULES.scopes.find((s) => s.key === "symbol:" + RULES.symbol);
      return sym ? sym.resolved : null;
    }
    if (sc.layer === "symbol") {
      const def = RULES.scopes.find((s) => s.key === "defaults");
      return def ? def.resolved : null;
    }
    return null;
  }
  function range(f) {
    if (f.kind === "bool") return "true / false";
    const lo = f.min !== undefined && f.min > -1e6 ? (f.min_open ? "> " : "≥ ") + f.min : "";
    const hi = f.max !== undefined && f.max < 1e6 ? (f.max_open ? "< " : "≤ ") + f.max : "";
    return [lo, hi].filter(Boolean).join(", ");
  }
  function effCell(key, resolved, changed) {
    const v = resolved && key in resolved.values ? fmt(resolved.values[key]) : "chưa đặt";
    const src = resolved && resolved.sources[key] ? " (" + (SRC[resolved.sources[key]] || resolved.sources[key]) + ")" : "";
    const td = $("re_" + key);
    td.innerHTML = esc(v) + '<span class="small">' + esc(src) + "</span>";
    td.className = "eff" + (changed ? " changed" : "");
  }
  function renderRows() {
    const sc = scope(), inh = inherited(sc);
    const rows = [];
    for (const key of KEYS) {
      const f = RULES.fields[key];
      const lock = sc.layer === "session" && !f.session;
      const base = inh && key in inh.values ? "kế thừa: " + fmt(inh.values[key]) : (sc.layer === "defaults" ? (f.required_default ? "bắt buộc" : "chưa đặt") : "chưa đặt");
      let input;
      if (f.kind === "bool") {
        input = '<select id="rf_' + key + '"' + (lock ? " disabled" : "") + '><option value="">' + esc(base) + '</option><option value="true">true</option><option value="false">false</option></select>';
      } else {
        input = '<input type="text" inputmode="decimal" id="rf_' + key + '"' + (lock ? " disabled" : "") + ' placeholder="' + esc(base) + '">';
      }
      rows.push('<tr class="' + (lock ? "lock" : "") + '"><td>' + esc(f.label) + '<div class="small"><code>' + esc(key) + "</code> "
        + esc(range(f)) + (lock ? " · chỉ đặt theo cặp / defaults" : "") + "</div></td><td>" + input + '</td><td class="eff" id="re_' + key + '"></td></tr>');
    }
    $("r_rows").innerHTML = rows.join("");
    for (const key of KEYS) {
      const el = $("rf_" + key);
      el.value = fmt(sc.values[key]);
      el.addEventListener("input", previewSoon);
      el.addEventListener("change", previewSoon);
      effCell(key, sc.resolved, false);
    }
    $("r_preview").innerHTML = "";
    rsay("", true);
  }
  function changes() {
    const sc = scope(), out = {};
    for (const key of KEYS) {
      const el = $("rf_" + key);
      if (el.disabled) continue;
      const now = el.value.trim(), before = fmt(sc.values[key]);
      if (now === before) continue;
      if (now !== "" && before !== "" && !isNaN(now) && Number(now) === Number(before)) continue;
      out[key] = now === "" ? null : now;
    }
    return out;
  }
  async function preview(quiet) {
    const sc = scope(), ch = changes();
    if (!Object.keys(ch).length) {
      for (const key of KEYS) effCell(key, sc.resolved, false);
      $("r_preview").innerHTML = ""; if (!quiet) rsay("Chưa đổi trường nào.", true);
      return true;
    }
    const {ok, out} = await send({action: "preview", scope: sc.key, changes: ch}, RULES_PATH);
    if (!ok) { $("r_preview").innerHTML = ""; rsay(out.error || "Không xem trước được.", false); return false; }
    for (const key of KEYS) effCell(key, out.resolved, key in (out.changes || {}));
    $("r_preview").innerHTML = out.warnings && out.warnings.length
      ? warnBox(out.warnings, "Sau khi lưu sẽ có cảnh báo:")
      : '<div class="okbox">Xem trước: không có cảnh báo rule_infeasible.</div>';
    rsay("", true);
    return true;
  }
  let rtimer = null;
  function previewSoon() { clearTimeout(rtimer); rtimer = setTimeout(() => preview(true), 400); }

  async function save(payload, doneText) {
    if (!$("r_reason").value.trim()) { rsay("Nhập lý do thay đổi trước khi lưu.", false); $("r_reason").focus(); return; }
    payload.reason = $("r_reason").value.trim();
    $("r_save").disabled = true; rsay("Đang lưu...", true);
    const {ok, out} = await send(payload, RULES_PATH);
    $("r_save").disabled = false;
    if (!ok) { rsay(out.error || "Lưu thất bại.", false); return; }
    if (!out.updated) { rsay("Không có gì thay đổi.", true); return; }
    const warn = out.warnings && out.warnings.length ? "\\nCảnh báo: " + out.warnings.join("\\n") : "";
    rsay((doneText || "Đã lưu rule ORB, có hiệu lực ngay.") + warn, true);
    setTimeout(() => location.reload(), warn ? 3500 : 1200);
  }

  $("r_scope").addEventListener("change", renderRows);
  $("r_preview_btn").addEventListener("click", () => preview(false));
  $("r_save").addEventListener("click", async () => {
    const ch = changes();
    if (!Object.keys(ch).length) { rsay("Chưa đổi trường nào.", true); return; }
    if (!(await preview(true))) return;
    save({action: "save", scope: scope().key, changes: ch});
  });
  $("r_toggle").addEventListener("click", () => {
    const next = !RULES.enabled;
    if (!next && !confirm("Tắt ORB?\\nScheduler không mở watch, check_orb_signal và check_trade(strategy=ORB) báo ORB đang tắt.")) return;
    save({action: "save", scope: "defaults", changes: {enabled: next}},
         next ? "Đã bật ORB." : "Đã tắt ORB.");
  });
  renderRows();

  const nsay = (text, ok) => { $("n_msg").textContent = text; $("n_msg").className = ok ? "ok" : "err"; };
  async function news(payload) {
    nsay("Đang lưu...", true);
    const {ok, out} = await send(payload, RULES_PATH);
    if (!ok) { nsay(out.error || "Không lưu được.", false); return; }
    nsay("Đã lưu danh sách ngày tin.", true);
    setTimeout(() => location.reload(), 900);
  }
  $("n_add").addEventListener("click", () => {
    const day = $("n_date").value;
    if (!day) { nsay("Chọn ngày.", false); return; }
    news({action: "news_add", date: day, note: $("n_note").value.trim()});
  });
  document.querySelectorAll("button[data-news]").forEach((b) => {
    b.addEventListener("click", () => {
      if (confirm("Bỏ ngày " + b.dataset.news + " khỏi danh sách ngày tin?")) news({action: "news_delete", date: b.dataset.news});
    });
  });
}
</script>
</div></body></html>
"""

SETUP_BANNER = """<div class="warn">
<strong>Chưa đặt mật khẩu.</strong> Mọi thao tác ghi cần <em>setup token</em> in trong log container
(ô ở cuối form). Nên đặt mật khẩu ở trang cài đặt chung trước.
</div>"""

SETUP_FIELD = """<label for="setup_token">Setup token</label>
  <input type="text" id="setup_token" autocomplete="off" placeholder="dán chuỗi từ dòng BAMCP SETUP TOKEN">"""


def _js(value: Any) -> str:
    """Nhung du lieu vao <script>. Chan "</script>" bang cach escape dau <."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _short(ts: str | None) -> str:
    return (ts or "")[:16].replace("T", " ")


def _rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="8" class="small">Chưa có phiên nào.</td></tr>'
    out = []
    for r in rows:
        sid = _e(r["session_id"])
        on = r["enabled"]
        job = r.get("job_status") or "-"
        job_cls = {"scheduled": "ok", "error": "err"}.get(job, "")
        job_html = f'<span class="pill {job_cls}">{_e(job)}</span>'
        if r.get("last_error"):
            job_html += f'<div class="small" title="{_e(r["last_error"])}">{_e(str(r["last_error"])[:80])}</div>'
        watch = r.get("watch") or {}
        current = r.get("current") or {}
        cur = ""
        if current.get("state") or watch.get("active"):
            cur = (f'<div class="small">{_e(current.get("or_date"))}: {_e(current.get("state") or "-")}'
                   f'{" · watch M5" if watch.get("active") else ""}</div>')
        toggle = "disable" if on else "enable"
        out.append(
            f'<tr class="{"" if on else "off"}">'
            f'<td class="id">{sid}<div class="small" style="font-family:inherit;font-weight:400">{_e(r["name"])}</div></td>'
            f'<td>{_e(r["timezone"])}<div class="small">{_e(r.get("holiday_calendar"))} · '
            f'{_e(",".join(r.get("trade_days") or []))}</div></td>'
            f'<td>{_e(r["open_time"])}<div class="small">window {_e(r.get("trade_window_minutes"))}′'
            f' · max {_e(r.get("max_trades"))}</div></td>'
            f'<td>{_e(_short(r.get("next_open_utc")))}Z<div class="small">{_e(_short(r.get("next_open_vn")))} VN</div></td>'
            f'<td><span class="pill {"ok" if on else ""}">{"bật" if on else "tắt"}</span>{cur}</td>'
            f'<td>{job_html}</td>'
            f'<td class="small">{_e(_short(r.get("last_run_utc")) or "—")}{"Z" if r.get("last_run_utc") else ""}</td>'
            f'<td class="act"><button type="button" data-act="edit" data-sid="{sid}">Sửa</button>'
            f'<button type="button" data-act="{toggle}" data-sid="{sid}">{"Tắt" if on else "Bật"}</button>'
            f'<button type="button" class="danger" data-act="delete" data-sid="{sid}">Xóa</button></td>'
            f'</tr>')
    return "".join(out)


def _history(history: list[dict[str, Any]]) -> str:
    if not history:
        return '<div class="small">Chưa có thay đổi nào.</div>'
    items = []
    for h in reversed(history[-12:]):
        what = _e(h.get("action"))
        target = _e(h.get("session_id") or ", ".join(h.get("session_ids") or []))
        changes = h.get("changes") or {}
        if h.get("action") == "update":
            detail = ", ".join(f"{_e(k)} {_e(v.get('from'))} → {_e(v.get('to'))}"
                               for k, v in changes.items())
        else:
            detail = ""
        items.append(f"<li><b>{_e(_short(h.get('at_utc')))}Z</b> <b>{what}</b> {target}"
                     f"{' — ' + detail if detail else ''}</li>")
    return "<ul>" + "".join(items) + "</ul>"


# TM - #ORB-RULES - ORB Rule Set ------------------------------------------------

def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _warnings_box(warnings: list[str], title: str) -> str:
    if not warnings:
        return ""
    return (f'<div class="warn"><strong>{_e(title)}</strong><ul>'
            + "".join(f"<li>{_e(w)}</li>" for w in warnings) + "</ul></div>")


def _rules_card(view: dict[str, Any]) -> str:
    status = view.get("status") or {}
    market = view.get("market") or {}
    rules = status.get("rules") or {}
    on = bool(view.get("enabled"))
    cap = rules.get("max_trades_per_day")
    stop = rules.get("daily_stop_loss")

    def stat(value: Any, label: str, bad: bool = False) -> str:
        return (f'<div class="stat{" bad" if bad else ""}"><b>{_e(_fmt(value))}</b>'
                f'<span>{_e(label)}</span></div>')

    stats = "".join([
        stat(f"{status.get('trades_taken', 0)} / {_fmt(cap)}", f"Lệnh ORB ngày {status.get('or_date', '')}"),
        stat(status.get("trades_remaining"), "Còn lại", status.get("trades_remaining") == 0),
        stat(status.get("realized_pnl"), f"PnL ORB (stop {_fmt(stop)})", bool(status.get("daily_stop_hit"))),
        stat("có" if status.get("can_trade") else "không", "Vào lệnh ORB được", not status.get("can_trade")),
        stat(market.get("atr_h1"), "ATR H1 hiện tại"),
        stat(market.get("price"), "Giá gần nhất"),
    ])
    missing = status.get("missing_rules") or []
    options = "".join(f'<option value="{_e(s["key"])}">{_e(s["label"])}</option>'
                      for s in view.get("scopes") or [])
    return f"""<div class="card" id="rules_card">
  <h2>Rule ORB <span class="pill {"ok" if on else "err"}">{"đang bật" if on else "đang tắt"}</span></h2>
  <div class="small">Bộ rule riêng cho lệnh ORB, tách khỏi rule scalp/swing — chỉ <code>max_margin_per_trade</code>
  dùng chung. Lưu trong <code>{_e(view.get("rules_file"))}</code> (khối <code>orb</code>); sửa ở đây hay qua
  <code>update_rules(orb=true)</code> đều ghi lịch sử và có hiệu lực ngay, không cần restart.</div>
  <div class="stats">{stats}</div>
  {_warnings_box(missing, "Thiếu rule bắt buộc — lệnh ORB sẽ bị chặn:")}
  {_warnings_box(view.get("warnings") or [], "Cảnh báo cấu hình:")}
  <div class="row">
    <div>
      <label for="r_scope">Phạm vi</label>
      <select id="r_scope">{options}</select>
      <div class="hint">Ưu tiên từng trường: phiên &gt; cặp &gt; defaults. Ô trống = kế thừa lớp dưới
      (giá trị kế thừa hiện mờ trong ô). Ngưỡng chấm lệnh và sizing chỉ đặt theo cặp / defaults.</div>
    </div>
  </div>
  <div class="scroll"><table class="rules">
    <thead><tr><th>Trường</th><th>Giá trị ở phạm vi này</th><th>Đang áp dụng</th></tr></thead>
    <tbody id="r_rows"></tbody>
  </table></div>
  <label for="r_reason">Lý do thay đổi (bắt buộc)</label>
  <input type="text" id="r_reason" maxlength="300" placeholder="vd: nới min_rr sau khi backtest 2 năm">
  <div id="r_preview"></div>
  <div class="actions">
    <button type="button" class="primary" id="r_save">Lưu rule</button>
    <button type="button" id="r_preview_btn">Xem trước</button>
    <button type="button" class="{"danger" if on else ""}" id="r_toggle">{"Tắt ORB" if on else "Bật ORB"}</button>
  </div>
  <div id="r_msg"></div>
</div>"""


def _news_card(view: dict[str, Any]) -> str:
    entries = view.get("news_days") or []
    if entries:
        rows = "".join(
            f'<tr><td class="id">{_e(e["date"])}</td><td>{_e(e.get("note"))}</td>'
            f'<td class="act"><button type="button" class="danger" data-news="{_e(e["date"])}">Bỏ</button></td></tr>'
            for e in entries)
    else:
        rows = '<tr><td colspan="3" class="small">Chưa có ngày tin nào.</td></tr>'
    return f"""<div class="card" id="news_card">
  <h2>Ngày tin lớn (news days)</h2>
  <div class="small">Ngày theo lịch của phiên (or_date). Khi <code>skip_news_days</code> bật, phiên của ngày này
  bị lọc <code>news_day</code> và không có plan. Lưu trong <code>{_e(view.get("news_file"))}</code>.</div>
  <div class="scroll"><table><thead><tr><th>Ngày</th><th>Ghi chú</th><th></th></tr></thead>
  <tbody>{rows}</tbody></table></div>
  <div class="row">
    <div><label for="n_date">Thêm ngày</label><input type="text" id="n_date" placeholder="YYYY-MM-DD" pattern="\\d{{4}}-\\d{{2}}-\\d{{2}}"></div>
    <div><label for="n_note">Ghi chú</label><input type="text" id="n_note" maxlength="200" placeholder="vd: FOMC, CPI"></div>
  </div>
  <div class="actions"><button type="button" id="n_add">Thêm ngày tin</button></div>
  <div id="n_msg"></div>
</div>"""


def _rules_history(view: dict[str, Any]) -> str:
    history = view.get("history") or []
    if not history:
        body = '<div class="small">Chưa có thay đổi rule ORB nào.</div>'
    else:
        items = []
        for h in history:
            where = h.get("session_id") and f"phiên {h['session_id']}" or \
                h.get("symbol") and f"cặp {h['symbol']}" or h.get("layer") or ""
            if h.get("field"):
                detail = f"{_e(h['field'])} {_e(_fmt(h.get('from')))} → {_e(_fmt(h.get('to')))}"
            else:
                detail = f"{len(h.get('changes') or {})} trường"
            items.append(
                f"<li><b>{_e(_short(h.get('at')))}</b> <span class=\"pill\">{_e(h.get('source'))}</span> "
                f"{_e(where)} — {detail} <span class=\"small\">· {_e(h.get('reason'))}</span></li>")
        body = "<ul>" + "".join(items) + "</ul>"
    return f"""<div class="card hist" id="rules_history">
  <h2>Lịch sử rule ORB</h2>
  {body}
</div>"""


def _override_fields(global_params: dict[str, Any]) -> str:
    parts = []
    for key in orb.SESSION_FORM_KEYS:     # TM - #ORB-RULES - ORB Rule Set: bo nhom rule
        section, _name, kind, rule = orb.PARAM_SPEC[key]
        current = global_params.get(key)
        label = f'<label for="ov_{key}">{_e(key)} <span class="small">({_e(section)})</span></label>'
        if kind in ("choice", "bool"):
            choices = list(rule) if kind == "choice" else ["true", "false"]
            shown = str(current).lower() if isinstance(current, bool) else current
            opts = f'<option value="">chung: {_e(shown)}</option>' + "".join(
                f'<option value="{_e(c)}">{_e(c)}</option>' for c in choices)
            field = f'<select id="ov_{key}">{opts}</select>'
        else:
            low, high = rule
            field = (f'<input type="text" inputmode="decimal" id="ov_{key}" '
                     f'placeholder="chung: {_e(current)}" title="{low:g} – {high:g}">')
        parts.append(f"<div>{label}{field}</div>")
    return "".join(parts)


def render(*, rows: list[dict[str, Any]], sessions: list[dict[str, Any]],
           history: list[dict[str, Any]], action_path: str, admin_path: str,
           store_path: str, global_params: dict[str, Any], setup_required: bool,
           now_ms: int, rules_view: dict[str, Any] | None = None,
           rules_path: str = "") -> str:
    """rows = OrbService.list_sessions(True); sessions = ban goc de do vao form sua.
    TM - #ORB-RULES - ORB Rule Set: rules_view = server._orb_rules_view() (None -> an
    muc Rule ORB), rules_path = POST luu rule / news days."""
    tz_options = "".join(f'<option value="{_e(z)}">' for z in sorted(available_timezones())
                         if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/")))
    day_boxes = "".join(
        f'<label><input type="checkbox" id="d_{d}"{"" if d in ("Sat", "Sun") else " checked"}> {d}</label>'
        for d in orb.DAY_NAMES)
    replacements = {
        "__STORE_PATH__": _e(store_path),
        "__ADMIN_PATH__": _e(admin_path),
        "__NOW__": _e(f"{iso_utc(now_ms)} / {iso_vn(now_ms)}"),
        "__SETUP_BANNER__": SETUP_BANNER if setup_required else "",
        "__SETUP_FIELD__": SETUP_FIELD if setup_required else "",
        "__ROWS__": _rows(rows),
        "__TZ_OPTIONS__": tz_options,
        "__DAY_BOXES__": day_boxes,
        "__DEF_WINDOW__": _e(global_params.get("trade_window_minutes")),
        "__DEF_MAX__": _e(global_params.get("max_trades")),
        "__OVERRIDE_FIELDS__": _override_fields(global_params),
        "__HISTORY__": _history(history),
        "__SESSIONS_JSON__": _js({s["session_id"]: s for s in sessions}),
        "__OVERRIDE_KEYS__": _js(list(orb.SESSION_FORM_KEYS)),
        "__DAYS__": _js(list(orb.DAY_NAMES)),
        "__ACTION_PATH__": _js(action_path),
        # TM - #ORB-RULES - ORB Rule Set
        "__RULES_CARD__": _rules_card(rules_view) if rules_view else "",
        "__NEWS_CARD__": _news_card(rules_view) if rules_view else "",
        "__RULES_HISTORY__": _rules_history(rules_view) if rules_view else "",
        "__RULES_JSON__": _js({k: rules_view.get(k) for k in (
            "enabled", "symbol", "scopes", "fields")} if rules_view else None),
        "__RULES_PATH__": _js(rules_path),
    }
    page = PAGE
    for needle, value in replacements.items():
        page = page.replace(needle, value)
    return page
