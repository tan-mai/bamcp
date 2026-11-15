# TM - #NOTIFY-CHANNEL - Tách kênh thông báo theo cặp
"""Trang admin "Thông báo": kenh, ma tran route symbol x category, cai dat chung, nhat ky."""
from __future__ import annotations

import html
import json
from typing import Any

import admin_ui
import notify_logic
from admin_entry import SETUP_BANNER, SETUP_FIELD

CSS = """
  table.matrix th, table.matrix td { padding:6px 8px; text-align:center; }
  table.matrix th:first-child, table.matrix td:first-child { text-align:left; position:sticky; left:0;
    background:var(--surface); z-index:1; }
  table.matrix th { text-transform:none; letter-spacing:0; font-size:11px; white-space:normal; min-width:84px; }
  table.matrix td button { min-height:30px; width:100%; padding:3px 6px; font-size:11.5px; white-space:normal;
    line-height:1.3; }
  table.matrix td button.set { border-color:var(--accent); background:var(--accent-soft); color:var(--ink); }
  table.matrix td button.sel { box-shadow:0 0 0 3px var(--accent-soft); border-color:var(--accent); }
  .tg-only, .dc-only { display:none; }
  .show-telegram .tg-only, .show-discord .dc-only { display:block; }
  .swatch { display:inline-block; width:12px; height:12px; border-radius:3px; vertical-align:middle;
    border:1px solid var(--line-strong); }
  td.preview { font-family:var(--mono); font-size:11.5px; white-space:pre-wrap; max-width:420px; }
"""

PAGE = """<div class="page-head">
  <h1>Thông báo</h1>
  <p class="sub">Mỗi cặp một kênh riêng (topic Telegram / channel Discord). Dòng đầu mỗi tin luôn có cặp, loại tin
    và giờ. Chưa có route nào thì mọi tin vào kênh mặc định.</p>
</div>
<div id="msg" data-msg="toast"></div>
__SETUP_BANNER__
__WARNINGS__

  <div class="card">
    <h2>Kênh</h2>
    <p class="hint">Mọi cài đặt kênh nhập ở trang này, không cần <code>.env</code>. Bot token / webhook URL lưu trên volume
      (<code>__ALERTS_FILE__</code>), không nằm trong DB và không hiện lại — chỉ 4 ký tự cuối. Kênh Telegram / Discord
      đã nhập ở bản trước được chép sang đây khi khởi động.</p>
    <div class="scroll"><table>
      <thead><tr><th>Tên</th><th>Loại</th><th>Đích</th><th>Bí mật</th><th>Lần gửi cuối</th><th></th></tr></thead>
      <tbody>__CHANNEL_ROWS__</tbody>
    </table></div>
    <h3 id="c_title">Thêm kênh</h3>
    <div id="c_form" class="show-telegram">
      <input type="hidden" id="c_id">
      <div class="row">
        <div><label for="c_name">Tên</label><input type="text" id="c_name" placeholder="TG BTC topic"></div>
        <div><label for="c_provider">Loại</label><select id="c_provider">
          <option value="telegram">Telegram</option><option value="discord">Discord</option></select></div>
        <div class="end pad"><label class="check"><input type="checkbox" id="c_enabled" checked> Bật</label></div>
      </div>
      <div class="tg-only">
      <label for="c_tg_value">Bot token <span class="small" id="c_tg_hint">· kênh mới để trống = dùng chung bot của kênh Telegram đã có</span></label>
      <input type="password" id="c_tg_value" autocomplete="off" placeholder="123456789:AA...">
      <div class="row">
        <div><label for="c_tg_chat">chat_id</label><input type="text" id="c_tg_chat" placeholder="-100xxxxxxxxxx"></div>
        <div><label for="c_tg_thread">Topic id (message_thread_id) <span class="small">· trống = không dùng topic</span></label>
          <input type="number" id="c_tg_thread"></div>
      </div></div>
      <div class="dc-only">
      <label for="c_dc_value">Webhook URL <span class="small" id="c_dc_hint">· bắt buộc với kênh mới</span></label>
      <input type="password" id="c_dc_value" autocomplete="off" placeholder="https://discord.com/api/webhooks/...">
      <div class="row">
        <div><label for="c_dc_user">username <span class="small">· trống = BAMCP · {cặp}</span></label>
          <input type="text" id="c_dc_user" placeholder="BAMCP · BTC"></div>
        <div><label for="c_dc_avatar">avatar_url</label><input type="text" id="c_dc_avatar" placeholder="https://..."></div>
      </div></div>
      <div class="actions">
        <button type="button" class="primary" id="b_channel">Lưu kênh</button>
        <button type="button" id="b_channel_new">Kênh mới</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Route</h2>
    <p class="hint">Thứ tự chọn: <b>cặp + loại</b> → <b>cặp + *</b> → <b>* + loại</b> → kênh mặc định; dừng ở cấp đầu tiên
      có route bật. Tin có mức &lt; <code>min_severity</code> của route bị bỏ (FILTERED), không rơi xuống cấp sau.
      Hàng <code>*</code> + cột <code>*</code> là kênh System (tin không gắn được cặp). Mức: low &lt; info &lt; high &lt; critical.</p>
    <div class="scroll"><table class="matrix"><thead><tr><th>Cặp \\ loại</th>__MATRIX_HEAD__</tr></thead>
      <tbody>__MATRIX_ROWS__</tbody></table></div>
    <h3 id="r_title">Chọn một ô để sửa</h3>
    <div class="row">
      <div><label for="r_symbol">Cặp</label><select id="r_symbol">__ROUTE_SYMBOLS__</select></div>
      <div><label for="r_category">Loại tin</label><select id="r_category">__ROUTE_CATEGORIES__</select></div>
      <div><label for="r_min">min_severity</label><select id="r_min">__SEVERITY_OPTIONS__</select></div>
      <div class="end pad"><label class="check"><input type="checkbox" id="r_digest"> Gom tin (digest)</label></div>
      <div class="end pad"><label class="check"><input type="checkbox" id="r_enabled" checked> Bật</label></div>
    </div>
    <label>Kênh (chọn nhiều = gửi song song)</label>
    <div class="row" id="r_channels">__ROUTE_CHANNELS__</div>
    <div class="actions">
      <button type="button" class="primary" id="b_route">Lưu ô</button>
      <button type="button" class="danger" id="b_route_clear">Xoá ô</button>
    </div>
  </div>

  <div class="card">
    <h2>Cài đặt chung</h2>
    <label>Kênh mặc định <span class="small">· nhận tin khi không có route, và tin FALLBACK khi một kênh lỗi</span></label>
    <div class="row">__DEFAULT_CHANNELS__</div>
    <div class="row">
      <div><label for="s_dedupe">dedupe_window_seconds</label><input type="number" id="s_dedupe" min="0" max="86400" value="__S_DEDUPE__"></div>
      <div><label for="s_digest">digest_minutes</label><input type="number" id="s_digest" min="1" max="1440" value="__S_DIGEST__"></div>
      <div><label for="s_quiet">Giờ yên lặng <span class="small">· vd 23:00-05:00, trống = tắt</span></label>
        <input type="text" id="s_quiet" value="__S_QUIET__" placeholder="23:00-05:00"></div>
      <div><label for="s_retention">log_retention_days</label><input type="number" id="s_retention" min="1" max="365" value="__S_RETENTION__"></div>
      <div><label for="s_tz">Giờ hiển thị trong tin</label><input type="text" id="s_tz" value="__S_TZ__" placeholder="trống = TZ của server"></div>
    </div>
    <div class="row" style="margin-top:12px">
      <div class="end pad"><label class="check"><input type="checkbox" id="s_info"__S_INFO__> Gom cả tin info (không chỉ low)</label></div>
      <div class="end pad"><label class="check"><input type="checkbox" id="s_digest_default"__S_DIGEST_DEFAULT__> Gom tin ở kênh mặc định</label></div>
    </div>
    <p class="hint">Trong giờ yên lặng chỉ tin <b>critical</b> gửi ngay; tin khác vào tin tóm tắt gửi lúc hết giờ. Tin high /
      critical không bao giờ bị gom. Tin tóm tắt đang chờ: __DIGEST_PENDING__</p>
    <label>Emoji / màu theo cặp</label>
    <div class="scroll"><table><thead><tr><th>Cặp</th><th>Emoji</th><th>Màu (#RRGGBB)</th><th></th></tr></thead>
      <tbody>__STYLE_ROWS__</tbody></table></div>
    <div class="actions">
      <button type="button" class="primary" id="b_settings">Lưu cài đặt</button>
      <button type="button" id="b_flush">Gửi tin tóm tắt ngay</button>
    </div>
  </div>

  <div class="card">
    <h2>Nhật ký</h2>
    <form class="row" method="get">
      <div><label for="q_symbol">Cặp</label><select id="q_symbol" name="symbol">__LOG_SYMBOLS__</select></div>
      <div><label for="q_status">Trạng thái</label><select id="q_status" name="status">__LOG_STATUSES__</select></div>
      <div class="end" style="flex:0 0 auto"><button type="submit">Lọc</button></div>
    </form>
    <div class="scroll tall" style="margin-top:16px"><table>
      <thead><tr><th>Lúc</th><th>Cặp</th><th>Loại</th><th>Mức</th><th>Kênh</th><th>Trạng thái</th><th>Nội dung / lỗi</th></tr></thead>
      <tbody>__LOG_ROWS__</tbody>
    </table></div>
    <p class="hint">200 dòng mới nhất, giữ __S_RETENTION__ ngày.</p>
  </div>
  __SETUP_FIELD__
<script>
const ACTION = __ACTION_PATH__;
const CHANNELS = __CHANNELS_JSON__;
const ROUTES = __ROUTES_JSON__;
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

function reload(ms) { setTimeout(() => location.reload(), ms || 700); }

// ---------------- kenh
function showProvider() { $("c_form").className = "show-" + $("c_provider").value; }
$("c_provider").addEventListener("change", showProvider);

function fillChannel(c) {
  $("c_id").value = c ? c.id : "";
  $("c_title").textContent = c ? "Sửa kênh " + c.name : "Thêm kênh";
  $("c_name").value = c ? c.name : "";
  $("c_provider").value = c ? c.provider : "telegram";
  $("c_enabled").checked = c ? c.enabled : true;
  $("c_tg_chat").value = c && c.telegram_chat_id || "";
  $("c_tg_thread").value = c && c.telegram_thread_id != null ? c.telegram_thread_id : "";
  $("c_dc_user").value = c && c.discord_username || "";
  $("c_dc_avatar").value = c && c.discord_avatar_url || "";
  $("c_tg_value").value = ""; $("c_dc_value").value = "";
  const kept = c && c.secret_set ? "· trống = giữ " + c.secret_mask : null;
  $("c_tg_hint").textContent = kept || "· kênh mới để trống = dùng chung bot của kênh Telegram đã có";
  $("c_dc_hint").textContent = kept || "· bắt buộc với kênh mới";
  showProvider();
}
$("b_channel_new").addEventListener("click", () => fillChannel(null));

$("b_channel").addEventListener("click", async (e) => {
  const provider = $("c_provider").value;
  const body = {action: "channel_save", id: $("c_id").value, name: $("c_name").value.trim(), provider: provider,
    enabled: $("c_enabled").checked, telegram_bot_token: $("c_tg_value").value.trim(),
    telegram_chat_id: $("c_tg_chat").value.trim(), telegram_thread_id: $("c_tg_thread").value.trim(),
    discord_webhook_url: $("c_dc_value").value.trim(), discord_username: $("c_dc_user").value.trim(),
    discord_avatar_url: $("c_dc_avatar").value.trim()};
  const data = await act(body, e.target);
  if (data) { say("Đã lưu kênh " + data.channel.name + " — bấm Gửi thử để kiểm tra", true); reload(1200); }
});

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-ch]");
  if (!b) return;
  const c = CHANNELS.find((x) => x.id === b.dataset.id);
  if (b.dataset.ch === "edit") { fillChannel(c); $("c_title").scrollIntoView({behavior: "smooth"}); return; }
  if (b.dataset.ch === "test") {
    say("Đang gửi thử qua " + c.name + "…", true);
    const data = await act({action: "channel_test", id: c.id}, b);
    if (data) { say(data.ok ? "Đã gửi tin thử qua " + c.name + " — kiểm tra kênh." : c.name + " lỗi: " + data.error, data.ok); reload(2500); }
  } else if (b.dataset.ch === "toggle") {
    if (await act({action: "channel_toggle", id: c.id, enabled: !c.enabled}, b)) reload();
  } else if (b.dataset.ch === "delete") {
    if (!confirm("Xoá kênh " + c.name + " và mọi route trỏ tới nó?")) return;
    const data = await act({action: "channel_delete", id: c.id}, b);
    if (data) { say("Đã xoá kênh, bỏ " + data.routes_removed + " route", true); reload(); }
  }
});

// ---------------- route
function cellRoutes(sym, cat) { return ROUTES.filter((r) => r.symbol === sym && r.category === cat); }

function pickCell(sym, cat) {
  document.querySelectorAll("table.matrix button.sel").forEach((x) => x.classList.remove("sel"));
  const btn = document.querySelector('table.matrix button[data-sym="' + sym + '"][data-cat="' + cat + '"]');
  if (btn) btn.classList.add("sel");
  $("r_symbol").value = sym; $("r_category").value = cat;
  loadCell();
}

function loadCell() {
  const rows = cellRoutes($("r_symbol").value, $("r_category").value);
  $("r_title").textContent = "Ô " + $("r_symbol").value + " + " + $("r_category").value + (rows.length ? "" : " (chưa có route)");
  const ids = new Set(rows.map((r) => r.channel_id));
  document.querySelectorAll("input[data-route-ch]").forEach((x) => { x.checked = ids.has(x.dataset.routeCh); });
  $("r_min").value = rows.length ? rows[0].min_severity : "low";
  $("r_digest").checked = rows.length ? rows[0].digest : false;
  $("r_enabled").checked = rows.length ? rows[0].enabled : true;
}
$("r_symbol").addEventListener("change", loadCell);
$("r_category").addEventListener("change", loadCell);
document.querySelectorAll("table.matrix button[data-sym]").forEach((b) =>
  b.addEventListener("click", () => pickCell(b.dataset.sym, b.dataset.cat)));
document.querySelectorAll("button[data-suggest]").forEach((b) =>
  b.addEventListener("click", () => { pickCell(b.dataset.suggest, "*"); $("r_title").scrollIntoView({behavior: "smooth"}); }));

async function saveCell(ids, button) {
  const data = await act({action: "route_save", symbol: $("r_symbol").value, category: $("r_category").value,
    channel_ids: ids, min_severity: $("r_min").value, digest: $("r_digest").checked,
    enabled: $("r_enabled").checked}, button);
  if (data) { say(ids.length ? "Đã lưu route" : "Đã xoá route của ô", true); reload(); }
}
$("b_route").addEventListener("click", (e) => {
  const ids = Array.from(document.querySelectorAll("input[data-route-ch]")).filter((x) => x.checked).map((x) => x.dataset.routeCh);
  if (!ids.length) { say("Chọn ít nhất một kênh (hoặc bấm Xoá ô)", false); return; }
  saveCell(ids, e.target);
});
$("b_route_clear").addEventListener("click", (e) => saveCell([], e.target));

// ---------------- cai dat chung
$("b_settings").addEventListener("click", async (e) => {
  const symbols = {};
  document.querySelectorAll("tr[data-style]").forEach((tr) => {
    symbols[tr.dataset.style] = {emoji: tr.querySelector("[data-k=emoji]").value.trim(),
                                 color: tr.querySelector("[data-k=color]").value.trim()};
  });
  const data = await act({action: "settings_save",
    default_channel_id: Array.from(document.querySelectorAll("input[data-default-ch]")).filter((x) => x.checked).map((x) => x.dataset.defaultCh),
    dedupe_window_seconds: Number($("s_dedupe").value), digest_minutes: Number($("s_digest").value),
    quiet_hours: $("s_quiet").value.trim(), log_retention_days: Number($("s_retention").value),
    display_timezone: $("s_tz").value.trim(), digest_include_info: $("s_info").checked,
    digest_default: $("s_digest_default").checked, symbols: symbols}, e.target);
  if (data) { say("Đã lưu cài đặt", true); reload(); }
});
$("b_flush").addEventListener("click", async (e) => {
  const data = await act({action: "digest_flush"}, e.target);
  if (data) say("Đã gửi " + data.sent + " tin tóm tắt", true);
});
</script>
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _options(values: list[str], selected: Any = None, labels: dict[str, str] | None = None) -> str:
    return "".join(f'<option value="{_e(v)}"{" selected" if v == selected else ""}>{_e((labels or {}).get(v, v))}</option>'
                   for v in values)


def _secret_cell(ch: dict[str, Any]) -> str:
    if not ch.get("secret_set"):
        return f'<span class="pill err">chưa nhập {_e(ch.get("secret_label"))}</span>'
    shared = ch.get("secret_shared_with") or []
    return (f'{_e(ch.get("secret_label"))} <span class="pill ok">{_e(ch.get("secret_mask"))}</span>'
            + (f'<div class="small">dùng chung với {_e(", ".join(shared))}</div>' if shared else ""))


def _channel_rows(channels: list[dict[str, Any]], defaults: list[str]) -> str:
    if not channels:
        return '<tr><td colspan="6" class="small">Chưa có kênh nào. Tin sẽ lỗi "chưa có kênh" cho tới khi thêm kênh và đặt kênh mặc định.</td></tr>'
    out = []
    for c in channels:
        if c["provider"] == "telegram":
            target = f'chat <code>{_e(c.get("telegram_chat_id"))}</code>' + (
                f' · topic <code>{_e(c.get("telegram_thread_id"))}</code>' if c.get("telegram_thread_id") else "")
        else:
            target = _e(c.get("discord_username") or "username theo cặp")
        status = c.get("last_status")
        if status == "ok":
            last = f'<span class="pill ok">OK</span> <span class="small">{_e(c.get("last_ok_at"))}</span>'
        elif status == "error":
            last = (f'<span class="pill err">lỗi</span> <span class="small">{_e(c.get("last_error_at"))}</span>'
                    f'<div class="small">{_e(c.get("last_error"))}</div>')
        else:
            last = '<span class="small">chưa gửi</span>'
        tags = (' <span class="pill go">mặc định</span>' if c["id"] in defaults else "") + (
            "" if c["enabled"] else ' <span class="pill">tắt</span>')
        out.append(
            f'<tr class="{"" if c["enabled"] else "off"}"><td><b>{_e(c["name"])}</b>{tags}</td>'
            f'<td>{_e(c["provider"])}</td><td class="small">{target}</td><td class="small">{_secret_cell(c)}</td>'
            f'<td>{last}</td><td class="act">'
            f'<button data-ch="test" data-id="{_e(c["id"])}">Gửi thử</button>'
            f'<button data-ch="edit" data-id="{_e(c["id"])}">Sửa</button>'
            f'<button data-ch="toggle" data-id="{_e(c["id"])}">{"Tắt" if c["enabled"] else "Bật"}</button>'
            f'<button class="danger" data-ch="delete" data-id="{_e(c["id"])}">Xoá</button></td></tr>')
    return "".join(out)


def _matrix(symbols: list[str], categories: list[str], routes: list[dict[str, Any]],
            names: dict[str, str]) -> tuple[str, str]:
    head = "".join(f"<th>{_e(c if c == '*' else notify_logic.CATEGORIES[c][1] + ' ' + c)}</th>" for c in categories)
    rows = []
    for sym in symbols:
        cells = [f"<td><b>{_e('* (System / mọi cặp)' if sym == '*' else sym)}</b></td>"]
        for cat in categories:
            hit = [r for r in routes if r["symbol"] == sym and r["category"] == cat]
            if hit:
                first = hit[0]
                text = ", ".join(names.get(r["channel_id"], "?") for r in hit)
                extra = (f" · ≥{first['min_severity']}" if first["min_severity"] != "low" else "") + (
                    " · gom" if first["digest"] else "") + ("" if first["enabled"] else " · tắt")
                label, css = text + extra, "set"
            else:
                label, css = "—", ""
            cells.append(f'<td><button class="{css}" data-sym="{_e(sym)}" data-cat="{_e(cat)}">{_e(label)}</button></td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return head, "".join(rows)


def _log_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="7" class="small">Chưa có dòng nào.</td></tr>'
    css = {"SENT": "ok", "FALLBACK": "warn", "FAILED": "err"}
    out = []
    for r in rows:
        text = _e(r.get("payload_preview")) + (f'<div class="small">{_e(r["error"])}</div>' if r.get("error") else "")
        out.append(f'<tr><td class="small">{_e(r.get("at"))}</td><td class="sym">{_e(r["symbol"])}</td>'
                   f'<td class="small">{_e(r["category"])}</td><td class="small">{_e(r["severity"])}</td>'
                   f'<td class="small">{_e(r.get("channel_name") or "—")}</td>'
                   f'<td><span class="pill {css.get(r["status"], "")}">{_e(r["status"])}</span></td>'
                   f'<td class="preview">{text}</td></tr>')
    return "".join(out)


def _warnings(view: dict[str, Any]) -> str:
    out = []
    settings = view["settings"]
    if not view["channels"]:
        out.append("Chưa có kênh nào — thêm kênh rồi đặt làm kênh mặc định.")
    elif not settings["default_channel_id"]:
        out.append("Chưa đặt kênh mặc định: tin không khớp route nào sẽ bị FAILED, và kênh lỗi không có nơi FALLBACK.")
    missing = [c["name"] for c in view["channels"] if not c.get("secret_set")]
    if missing:
        out.append(f"Kênh chưa nhập bot token / webhook URL (bấm Sửa để nhập): {', '.join(missing)}.")
    if not view.get("hub_running"):
        out.append("Bộ gửi tin chưa chạy (server chưa khởi động xong) — tin tóm tắt chưa được gửi.")
    html_out = "".join(f'<div class="warn">{_e(w)}</div>' for w in out)
    if view.get("suggest_symbols"):
        buttons = " ".join(f'<button type="button" data-suggest="{_e(s)}">Tạo route {_e(s)}</button>'
                           for s in view["suggest_symbols"])
        html_out += (f'<div class="warn">Cặp chưa có kênh / route riêng: <b>{_e(", ".join(view["suggest_symbols"]))}</b> — '
                     f'tin của cặp này đang theo route <code>*</code> hoặc kênh mặc định. {buttons}</div>')
    return html_out


def render(view: dict[str, Any], *, action_path: str, admin_path: str, setup_required: bool,
           nav: list[admin_ui.NavItem] | None = None) -> str:
    settings = view["settings"]
    channels = view["channels"]
    names = {c["id"]: c["name"] for c in channels}
    symbols = [notify_logic.WILDCARD] + list(view["symbols"])
    categories = [notify_logic.WILDCARD] + notify_logic.route_categories()
    head, matrix = _matrix(symbols, categories, view["routes"], names)
    defaults = settings["default_channel_id"]
    styles = dict(settings.get("symbols") or {})
    style_rows = "".join(
        f'<tr data-style="{_e(sym)}"><td class="sym">{_e(sym)}</td>'
        f'<td><input type="text" data-k="emoji" value="{_e((styles.get(sym) or {}).get("emoji"))}"></td>'
        f'<td><input type="text" data-k="color" value="{_e((styles.get(sym) or {}).get("color"))}"></td>'
        f'<td><span class="swatch" style="background:{_e((styles.get(sym) or {}).get("color") or "transparent")}"></span></td></tr>'
        for sym in dict.fromkeys(list(view["symbols"]) + list(styles)))
    pending = view.get("digest_pending") or []
    pending_text = (", ".join(f"{p['symbol']} ×{p['items']}" for p in pending) if pending else "không có")

    def boxes(attr: str, chosen: set[str]) -> str:
        if not channels:
            return '<div class="small">Chưa có kênh.</div>'
        return "".join(f'<div class="end pad" style="flex:0 0 auto"><label class="check"><input type="checkbox" '
                       f'{attr}="{_e(c["id"])}"{" checked" if c["id"] in chosen else ""}> {_e(c["name"])}</label></div>'
                       for c in channels)

    replacements = {
        "__SETUP_BANNER__": SETUP_BANNER if setup_required else "",
        "__SETUP_FIELD__": SETUP_FIELD if setup_required else "",
        "__WARNINGS__": _warnings(view),
        "__ALERTS_FILE__": _e(view.get("alerts_file")),
        "__CHANNEL_ROWS__": _channel_rows(channels, defaults),
        "__MATRIX_HEAD__": head,
        "__MATRIX_ROWS__": matrix,
        "__ROUTE_SYMBOLS__": _options(symbols, notify_logic.WILDCARD),
        "__ROUTE_CATEGORIES__": _options(categories, notify_logic.WILDCARD),
        "__SEVERITY_OPTIONS__": _options(list(notify_logic.SEVERITIES), "low"),
        "__ROUTE_CHANNELS__": boxes("data-route-ch", set()),
        "__DEFAULT_CHANNELS__": boxes("data-default-ch", set(defaults)),
        "__S_DEDUPE__": _e(settings["dedupe_window_seconds"]),
        "__S_DIGEST__": _e(settings["digest_minutes"]),
        "__S_QUIET__": _e(settings["quiet_hours"]),
        "__S_RETENTION__": _e(settings["log_retention_days"]),
        "__S_TZ__": _e(settings["display_timezone"]),
        "__S_INFO__": " checked" if settings["digest_include_info"] else "",
        "__S_DIGEST_DEFAULT__": " checked" if settings["digest_default"] else "",
        "__DIGEST_PENDING__": _e(pending_text),
        "__STYLE_ROWS__": style_rows,
        "__LOG_SYMBOLS__": _options([""] + symbols, view.get("log_symbol") or "", {"": "tất cả"}),
        "__LOG_STATUSES__": _options([""] + list(view["statuses"]), view.get("log_status") or "", {"": "tất cả"}),
        "__LOG_ROWS__": _log_rows(view.get("log") or []),
        "__ACTION_PATH__": _js(action_path),
        "__CHANNELS_JSON__": _js(channels),
        "__ROUTES_JSON__": _js(view["routes"]),
    }
    page = PAGE
    for needle, value in replacements.items():
        page = page.replace(needle, value)
    return admin_ui.page(title="Thông báo", active="notify", body=page, css=CSS,
                         nav=nav or admin_ui.fallback_nav(admin_path))
