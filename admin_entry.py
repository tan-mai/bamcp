# TM - #ENTRY-ALERT - Entry setups & alerts
from __future__ import annotations

import html
import json
from typing import Any

PAGE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BAMCP — Setups &amp; cảnh báo</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#16181d; --muted:#6b7280; --line:#e3e6ea;
    --accent:#1f6feb; --ok:#0f7b47; --err:#b4232c; --warn-bg:#fff8e6; --warn-line:#f0d089; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1115; --card:#171a20; --ink:#e6e8ec; --muted:#9aa3af; --line:#262b33;
      --accent:#4a90e2; --ok:#49c17e; --err:#ef6b73; --warn-bg:#2a2415; --warn-line:#5c4d20; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  .wrap { max-width:1180px; margin:0 auto; padding:32px 16px 64px; }
  h1 { font-size:21px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin:0 0 24px; }
  .sub a { color:var(--accent); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
    padding:20px; margin-bottom:18px; }
  .card h2 { font-size:15px; margin:0 0 6px; }
  label { display:block; font-size:13px; color:var(--muted); margin:12px 0 5px; }
  input[type=text], input[type=number], input[type=date], input[type=password], select, textarea {
    width:100%; padding:8px 10px; border:1px solid var(--line); border-radius:7px;
    background:var(--bg); color:var(--ink); font:inherit; font-size:14px; }
  textarea { font-family:ui-monospace,"Cascadia Code",Consolas,monospace; font-size:12.5px; min-height:60px; }
  select[multiple] { min-height:80px; }
  .row { display:flex; gap:12px; flex-wrap:wrap; } .row > * { flex:1; min-width:130px; }
  .hint { font-size:12px; color:var(--muted); margin-top:4px; }
  .actions { display:flex; gap:10px; align-items:center; margin-top:14px; flex-wrap:wrap; }
  button { font:inherit; font-size:13px; padding:7px 14px; border-radius:7px;
    border:1px solid var(--line); background:var(--card); color:var(--ink); cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.danger { color:var(--err); }
  button:disabled { opacity:.55; cursor:default; }
  #msg { position:sticky; top:0; z-index:2; font-size:13px; white-space:pre-wrap;
    padding:8px 12px; border-radius:8px; margin-bottom:14px; display:none; }
  #msg.ok { display:block; color:var(--ok); background:var(--card); border:1px solid var(--ok); }
  #msg.err { display:block; color:var(--err); background:var(--card); border:1px solid var(--err); }
  .warn { background:var(--warn-bg); border:1px solid var(--warn-line); border-radius:8px;
    padding:12px 14px; font-size:13px; margin-bottom:18px; }
  code { font-family:ui-monospace,"Cascadia Code",Consolas,monospace; font-size:12.5px; }
  .scroll { overflow-x:auto; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; font-weight:600; color:var(--muted); font-size:12px;
    padding:6px 8px 6px 0; border-bottom:1px solid var(--line); white-space:nowrap; }
  td { padding:7px 8px 7px 0; border-bottom:1px solid var(--line); vertical-align:top; }
  td.num { font-variant-numeric:tabular-nums; white-space:nowrap; }
  td.act { text-align:right; white-space:nowrap; }
  td.act button { padding:3px 9px; font-size:12px; margin-left:4px; }
  table.params input { padding:4px 6px; font-size:13px; }
  .pill { display:inline-block; font-size:11px; padding:1px 8px; border-radius:99px;
    border:1px solid var(--line); white-space:nowrap; }
  .pill.go { color:var(--ok); border-color:var(--ok); }
  .pill.no { color:var(--err); border-color:var(--err); }
  .small { font-size:12px; color:var(--muted); }
  tr.timeline td { background:var(--bg); }
</style>
</head>
<body>
<div class="wrap">
  <h1>Setups &amp; cảnh báo vào lệnh</h1>
  <p class="sub">Watcher xét setup trên mỗi nến M5 đã đóng và báo qua Discord / Telegram.
    <a href="__ADMIN_PATH__">← cài đặt chung</a> · <a href="__SCHEDULES_PATH__">lịch kéo nến</a></p>
  <div id="msg"></div>
  __SETUP_BANNER__
  __CFG_WARNING__

  <div class="card">
    <h2>Thông báo</h2>
    <p class="hint">Secret hiển thị dạng che; để trống ô thì giữ giá trị cũ. Lưu ở <code>__ALERTS_FILE__</code>,
      có hiệu lực ngay.</p>
    <div class="row">
      <div><label for="n_channel">Kênh</label><select id="n_channel">__CHANNEL_OPTIONS__</select></div>
      <div><label for="n_tz">Giờ hiển thị trong tin</label>
        <input type="text" id="n_tz" value="__DISPLAY_TZ__"></div>
    </div>
    <label for="n_webhook">Discord webhook URL <span class="small">__WEBHOOK_MASK__</span></label>
    <input type="password" id="n_webhook" autocomplete="off" placeholder="https://discord.com/api/webhooks/...">
    <div class="row">
      <div><label for="n_token">Telegram bot token <span class="small">__TOKEN_MASK__</span></label>
        <input type="password" id="n_token" autocomplete="off"></div>
      <div><label for="n_chat">Telegram chat id <span class="small">__CHAT_MASK__</span></label>
        <input type="password" id="n_chat" autocomplete="off"></div>
    </div>
    <div class="actions">
      <button type="button" class="primary" id="b_notify">Lưu thông báo</button>
      <button type="button" id="b_test">Gửi tin thử</button>
    </div>
  </div>

  <div class="card">
    <h2>Watcher</h2>
    <p class="hint">Chạy ở giây thứ 20 sau mỗi mốc 5 phút. Lần quét gần nhất: __LAST_SCAN__</p>
    <div class="row">
      <div><label><input type="checkbox" id="w_enabled"__W_ENABLED__> Bật watcher</label></div>
      <div><label for="w_deadline">plan_deadline_utc (HH:MM UTC)</label>
        <input type="text" id="w_deadline" value="__W_DEADLINE__"></div>
    </div>
    <label for="w_symbols">watch_symbols</label>
    <select id="w_symbols" multiple>__W_SYMBOL_OPTIONS__</select>
    <label>Tham số mặc định và override theo cặp (ô trống = kế thừa mặc định)</label>
    <div class="scroll"><table class="params"><thead><tr><th>Tham số</th><th>Mặc định</th>__PARAM_HEAD__</tr></thead>
      <tbody>__PARAM_ROWS__</tbody></table></div>
    <p class="hint">Thứ tự ưu tiên: trigger_params của setup → override theo cặp → mặc định.
      max_chase không có mặc định: watcher tự tính cho từng setup.</p>
    <div class="actions"><button type="button" class="primary" id="b_watcher">Lưu watcher</button></div>
  </div>

  <div class="card">
    <h2>Setups</h2>
    <form class="row" method="get">
      <div><label for="q_date">bias_date</label><input type="date" id="q_date" name="date" value="__Q_DATE__"></div>
      <div><label for="q_status">Trạng thái</label><select id="q_status" name="status">__STATUS_OPTIONS__</select></div>
      <div><label>&nbsp;</label><button type="submit">Lọc</button></div>
    </form>
    <div class="scroll"><table>
      <thead><tr><th>Code</th><th>Hướng</th><th>Trạng thái</th><th>Vùng</th><th>Entry / SL / TP</th>
        <th>Trigger</th><th>Hết hạn</th><th>Event gần nhất</th><th></th></tr></thead>
      <tbody id="setup_rows">__SETUP_ROWS__</tbody>
    </table></div>
  </div>

  <div class="card">
    <h2>Tạo setup tay</h2>
    <p class="hint">Đi qua đúng kiểm tra của save_setups (created_by = admin). Không huỷ setup cũ.</p>
    <div class="row">
      <div><label for="s_symbol">Cặp</label><select id="s_symbol">__SYMBOL_OPTIONS__</select></div>
      <div><label for="s_direction">Hướng</label><select id="s_direction"><option>long</option><option>short</option></select></div>
      <div><label for="s_trigger">trigger_type</label><select id="s_trigger">__TRIGGER_OPTIONS__</select></div>
      <div><label for="s_bias">bias_date</label><input type="date" id="s_bias" value="__TODAY__"></div>
    </div>
    <div class="row">
      <div><label for="s_zl">zone_low</label><input type="number" step="any" id="s_zl"></div>
      <div><label for="s_zh">zone_high</label><input type="number" step="any" id="s_zh"></div>
      <div><label for="s_entry">entry</label><input type="number" step="any" id="s_entry"></div>
      <div><label for="s_stop">stop</label><input type="number" step="any" id="s_stop"></div>
      <div><label for="s_target">target</label><input type="number" step="any" id="s_target"></div>
      <div><label for="s_inv">invalidate_level</label><input type="number" step="any" id="s_inv"></div>
    </div>
    <div class="row">
      <div><label for="s_ctype">confirm_type</label><select id="s_ctype"><option value="">không</option>
        <option>close_above</option><option>close_below</option></select></div>
      <div><label for="s_clevel">confirm_level</label><input type="number" step="any" id="s_clevel"></div>
      <div><label for="s_chase">max_chase (trống = tự tính)</label><input type="number" step="any" id="s_chase"></div>
      <div><label for="s_expires">expires_at (YYYY-MM-DD HH:MM, trống = hết ngày)</label><input type="text" id="s_expires"></div>
      <div><label><input type="checkbox" id="s_m15" checked> m15_filter</label></div>
    </div>
    <label for="s_params">trigger_params (JSON, tuỳ chọn)</label>
    <textarea id="s_params" placeholder='{"vol_mult": 1.8, "ref_low": 67100}'></textarea>
    <label for="s_note">Ghi chú</label><input type="text" id="s_note">
    <div class="actions"><button type="button" class="primary" id="b_create">Tạo setup</button></div>
  </div>
  __SETUP_FIELD__
</div>
<script>
const ACTION = __ACTION_PATH__;
const PARAM_KEYS = __PARAM_KEYS__;
const OVERRIDE_SYMBOLS = __OVERRIDE_SYMBOLS__;
const $ = (id) => document.getElementById(id);

function say(text, ok) { const b = $("msg"); b.textContent = text; b.className = ok ? "ok" : "err"; }

async function act(body, button) {
  if (button) button.disabled = true;
  const token = $("setup_token");
  if (token) body.setup_token = token.value;
  try {
    const res = await fetch(ACTION, {method: "POST", headers: {"Content-Type": "application/json"},
                                     body: JSON.stringify(body)});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  } catch (e) { say(e.message, false); return null; }
  finally { if (button) button.disabled = false; }
}

function cell(v) { return v === null || v === undefined || v === "" ? "" : String(v); }
function num(id) { const v = $(id).value.trim(); return v === "" ? null : Number(v); }

$("b_notify").addEventListener("click", async (e) => {
  const data = await act({action: "notify_save", channel: $("n_channel").value,
    display_timezone: $("n_tz").value.trim(), discord_webhook_url: $("n_webhook").value.trim(),
    telegram_bot_token: $("n_token").value.trim(), telegram_chat_id: $("n_chat").value.trim()}, e.target);
  if (data) { say("Đã lưu thông báo", true); setTimeout(() => location.reload(), 700); }
});

$("b_test").addEventListener("click", async (e) => {
  say("Đang gửi…", true);
  const data = await act({action: "notify_test"}, e.target);
  if (data) say(data.error ? "Gửi lỗi: " + data.error : "Đã gửi tin thử qua " + data.channel, !data.error);
});

$("b_watcher").addEventListener("click", async (e) => {
  const defaults = {}, overrides = {};
  OVERRIDE_SYMBOLS.forEach((s) => { overrides[s] = {}; });
  document.querySelectorAll("table.params input[data-key]").forEach((box) => {
    const key = box.dataset.key, sym = box.dataset.symbol;
    let value = box.type === "checkbox" ? box.checked : box.value.trim();
    if (box.type === "text" && value === "") return;
    if (box.dataset.kind === "bool" && box.type === "text") value = ["1", "true", "yes", "co"].includes(value.toLowerCase());
    else if (box.type === "text") value = Number(value);
    if (sym) overrides[sym][key] = value; else defaults[key] = value;
  });
  const data = await act({action: "watcher_save", enabled: $("w_enabled").checked,
    plan_deadline_utc: $("w_deadline").value.trim(),
    watch_symbols: Array.from($("w_symbols").selectedOptions).map((o) => o.value),
    defaults: defaults, symbol_overrides: overrides}, e.target);
  if (data) { say("Đã lưu watcher", true); setTimeout(() => location.reload(), 700); }
});

$("b_create").addEventListener("click", async (e) => {
  let params = {};
  const raw = $("s_params").value.trim();
  if (raw) { try { params = JSON.parse(raw); } catch (err) { say("trigger_params không phải JSON: " + err.message, false); return; } }
  const setup = {direction: $("s_direction").value, trigger_type: $("s_trigger").value,
    bias_date: $("s_bias").value, zone_low: num("s_zl"), zone_high: num("s_zh"), entry: num("s_entry"),
    stop: num("s_stop"), target: num("s_target"), invalidate_level: num("s_inv"),
    confirm_type: $("s_ctype").value || null, confirm_level: num("s_clevel"), max_chase: num("s_chase"),
    expires_at: $("s_expires").value.trim() || null, m15_filter: $("s_m15").checked,
    trigger_params: params, note: $("s_note").value.trim()};
  const data = await act({action: "setup_create", symbol: $("s_symbol").value, setup: setup}, e.target);
  if (!data) return;
  if (data.rejected.length) { say("Bị từ chối: " + data.rejected[0].error, false); return; }
  const s = data.saved[0];
  say("Đã tạo " + s.code + " · max_chase " + s.max_chase_effective + " · check_trade "
      + (s.check_trade.would_pass ? "pass" : "fail: " + s.check_trade.rule_violations.join("; ")), true);
  setTimeout(() => location.reload(), 1500);
});

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  if (b.dataset.act === "cancel") {
    const reason = prompt("Lý do huỷ " + b.dataset.code + "?");
    if (!reason) return;
    if (await act({action: "setup_cancel", id: b.dataset.id, reason: reason}, b)) {
      say("Đã huỷ " + b.dataset.code, true); setTimeout(() => location.reload(), 700);
    }
  } else if (b.dataset.act === "timeline") {
    const row = b.closest("tr");
    if (row.nextElementSibling && row.nextElementSibling.classList.contains("timeline")) {
      row.nextElementSibling.remove(); return;
    }
    const data = await act({action: "setup_events", id: b.dataset.id}, b);
    if (!data) return;
    const tr = document.createElement("tr");
    tr.className = "timeline";
    const td = document.createElement("td");
    td.colSpan = 9;
    const table = document.createElement("table");
    table.innerHTML = "<thead><tr><th>Lúc</th><th>Event</th><th>Từ → sang</th><th>Nến</th><th>Giá</th><th>Tin</th><th>Chi tiết</th></tr></thead>";
    const body = document.createElement("tbody");
    data.events.forEach((ev) => {
      const r = document.createElement("tr");
      const tin = ev.notify_error ? "lỗi: " + ev.notify_error : (ev.notified ? "đã gửi" : "");
      [ev.at, ev.event, cell(ev.from_status) + " → " + cell(ev.to_status), cell(ev.candle), cell(ev.price),
       tin, JSON.stringify(ev.details)].forEach((v) => {
        const c = document.createElement("td"); c.textContent = v; c.className = "small"; r.appendChild(c);
      });
      body.appendChild(r);
    });
    table.appendChild(body);
    td.appendChild(table);
    tr.appendChild(td);
    row.after(tr);
  }
});
</script>
</body>
</html>
"""

SETUP_BANNER = """<div class="warn">
<strong>Chưa đặt mật khẩu.</strong> Mọi thao tác ghi cần <em>setup token</em> in trong log container
(ô ở cuối trang).
</div>"""

SETUP_FIELD = """<div class="card"><label for="setup_token">Setup token</label>
  <input type="text" id="setup_token" autocomplete="off" placeholder="dán chuỗi từ dòng BAMCP SETUP TOKEN"></div>"""

GO = ("ENTRY_VALID",)
STOPPED = ("BLOCKED", "MISSED", "INVALIDATED", "EXPIRED", "CANCELLED")


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _num(value: Any) -> str:
    if value is None:
        return "—"
    value = float(value)
    return f"{value:.2f}".rstrip("0").rstrip(".") if abs(value) >= 100 else f"{value:.6g}"


def _options(values: list[str], selected: Any = None, labels: dict[str, str] | None = None) -> str:
    chosen = selected if isinstance(selected, (list, tuple, set)) else [selected]
    return "".join(f'<option value="{_e(v)}"{" selected" if v in chosen else ""}>'
                   f'{_e((labels or {}).get(v, v))}</option>' for v in values)


def _setup_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="9" class="small">Không có setup nào khớp bộ lọc.</td></tr>'
    out = []
    for s in rows:
        css = "go" if s["status"] in GO else "no" if s["status"] in STOPPED else ""
        last = s.get("last_event") or {}
        trigger = s["trigger_type"] + (f' → {s["confirm_type"]} {_num(s["confirm_level"])}'
                                       if s.get("confirm_type") else "")
        chase = (s.get("state") or {}).get("max_chase")
        cancel = ("" if s["terminal"] else
                  f'<button class="danger" data-act="cancel" data-id="{_e(s["id"])}" '
                  f'data-code="{_e(s["code"])}">Huỷ</button>')
        out.append(
            "<tr>"
            f'<td><b>{_e(s["code"])}</b><div class="small">{_e(s["created_by"])} · {_e(s["created_at"])}</div></td>'
            f'<td>{_e(s["direction"])}</td><td><span class="pill {css}">{_e(s["status"])}</span></td>'
            f'<td class="num">{_num(s["zone_low"])}–{_num(s["zone_high"])}</td>'
            f'<td class="num">{_num(s["entry"])} / {_num(s["stop"])} / {_num(s["target"])}'
            f'<div class="small">max_chase {_num(chase if chase is not None else s.get("max_chase"))}'
            f' · inv {_num(s["invalidate_level"])}</div></td>'
            f'<td>{_e(trigger)}</td><td class="small">{_e(s["expires_at"])}</td>'
            f'<td class="small">{_e(last.get("event"))} {_e(last.get("at"))}'
            f'{" · lỗi tin" if last.get("notify_error") else ""}</td>'
            f'<td class="act"><button data-act="timeline" data-id="{_e(s["id"])}">Timeline</button>{cancel}</td>'
            "</tr>")
    return "".join(out)


def _param_rows(keys: list[str], defaults: dict[str, Any], overrides: dict[str, dict[str, Any]],
                symbols: list[str]) -> str:
    out = []
    for key in keys:
        value = defaults.get(key)
        if isinstance(value, bool):
            first = f'<input type="checkbox" data-key="{_e(key)}"{" checked" if value else ""}>'
        else:
            first = f'<input type="text" data-key="{_e(key)}" value="{_e(value)}">'
        cells = [f"<td><code>{_e(key)}</code></td><td>{first}</td>"]
        for sym in symbols:
            ov = (overrides.get(sym) or {}).get(key)
            kind = "bool" if isinstance(value, bool) else "num"
            cells.append(f'<td><input type="text" data-key="{_e(key)}" data-symbol="{_e(sym)}" '
                         f'data-kind="{kind}" value="{_e("" if ov is None else str(ov).lower() if isinstance(ov, bool) else ov)}"></td>')
        out.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(out)


def render(view: dict[str, Any], *, action_path: str, admin_path: str, schedules_path: str,
           setup_required: bool) -> str:
    notify = view["notify"]
    watcher = view["watcher"]
    override_symbols = view["override_symbols"]
    scan = view.get("last_scan") or {}
    scan_text = (f'{scan.get("at_text")} · cặp {", ".join(scan.get("symbols") or []) or "—"}'
                 + (f' · lỗi {scan["errors"]}' if scan.get("errors") else "")) if scan else "chưa quét"
    replacements = {
        "__ADMIN_PATH__": _e(admin_path),
        "__SCHEDULES_PATH__": _e(schedules_path),
        "__SETUP_BANNER__": SETUP_BANNER if setup_required else "",
        "__SETUP_FIELD__": SETUP_FIELD if setup_required else "",
        "__CFG_WARNING__": "".join(f'<div class="warn">{_e(w)}</div>' for w in view.get("warnings") or []),
        "__ALERTS_FILE__": _e(view.get("alerts_file")),
        "__CHANNEL_OPTIONS__": _options(["discord", "telegram", "both"], notify.get("channel"),
                                        {"discord": "Discord", "telegram": "Telegram", "both": "Cả hai"}),
        "__DISPLAY_TZ__": _e(notify.get("display_timezone")),
        "__WEBHOOK_MASK__": _e(notify.get("webhook_mask")),
        "__TOKEN_MASK__": _e(notify.get("token_mask")),
        "__CHAT_MASK__": _e(notify.get("chat_mask")),
        "__LAST_SCAN__": _e(scan_text),
        "__W_ENABLED__": " checked" if watcher.get("enabled") else "",
        "__W_DEADLINE__": _e(watcher.get("plan_deadline_utc")),
        "__W_SYMBOL_OPTIONS__": _options(view["symbols"], watcher.get("watch_symbols") or []),
        "__PARAM_HEAD__": "".join(f"<th>{_e(s)}</th>" for s in override_symbols),
        "__PARAM_ROWS__": _param_rows(view["param_keys"], watcher.get("defaults") or {},
                                      watcher.get("symbol_overrides") or {}, override_symbols),
        "__Q_DATE__": _e(view.get("date")),
        "__STATUS_OPTIONS__": _options([""] + ["active"] + view["statuses"], view.get("status"),
                                       {"": "tất cả", "active": "đang chạy"}),
        "__SETUP_ROWS__": _setup_rows(view.get("setups") or []),
        "__SYMBOL_OPTIONS__": _options(view["symbols"]),
        "__TRIGGER_OPTIONS__": _options(view["triggers"]),
        "__TODAY__": _e(view.get("today")),
        "__ACTION_PATH__": _js(action_path),
        "__PARAM_KEYS__": _js(view["param_keys"]),
        "__OVERRIDE_SYMBOLS__": _js(override_symbols),
    }
    page = PAGE
    for needle, value in replacements.items():
        page = page.replace(needle, value)
    return page
