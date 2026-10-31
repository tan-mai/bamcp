# TM - #M5-FETCH - Per-timeframe schedules + M5
from __future__ import annotations

import html
import json
from typing import Any

PAGE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BAMCP — Fetch schedules</title>
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
  .wrap { max-width:1080px; margin:0 auto; padding:32px 16px 64px; }
  h1 { font-size:21px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin:0 0 24px; }
  .sub a { color:var(--accent); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
    padding:20px; margin-bottom:18px; }
  .card h2 { font-size:15px; margin:0 0 6px; }
  label { display:block; font-size:13px; color:var(--muted); margin:12px 0 5px; }
  input[type=text], input[type=number], select { width:100%; padding:8px 10px;
    border:1px solid var(--line); border-radius:7px; background:var(--bg); color:var(--ink);
    font:inherit; font-size:14px; }
  select[multiple] { min-height:96px; }
  .row { display:flex; gap:12px; flex-wrap:wrap; } .row > * { flex:1; min-width:150px; }
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
  tr:last-child td { border-bottom:none; }
  td.act { text-align:right; white-space:nowrap; }
  td.act button { padding:3px 9px; font-size:12px; margin-left:4px; }
  .pill { display:inline-block; font-size:11px; padding:1px 8px; border-radius:99px;
    border:1px solid var(--line); white-space:nowrap; }
  .pill.ok { color:var(--ok); border-color:var(--ok); }
  .pill.err { color:var(--err); border-color:var(--err); }
  .small { font-size:12px; color:var(--muted); }
  ul.runs { margin:6px 0 0; padding-left:18px; font-size:13px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Fetch schedules</h1>
  <p class="sub">Mỗi khung một lịch kéo nến riêng, cron 6 trường có giây, giờ UTC.
    <a href="__ADMIN_PATH__">← cài đặt chung</a></p>
  <div id="msg"></div>
  __SETUP_BANNER__
  __MODE_WARNING__

  <div class="card">
    <h2>Nơi lưu nến</h2>
    <p class="hint">Đang dùng <strong>__STORE_KIND__</strong> (chọn ở __STORE_SOURCE__). Đổi ở đây có hiệu lực ngay,
      không cần restart. SQLite: <code>__SQLITE_PATH__</code></p>
    <div class="scroll"><table>
      <thead><tr><th>Cặp</th><th>Khung</th>__STORE_HEAD__</tr></thead>
      <tbody>__STORE_ROWS__</tbody>
    </table></div>
    <div class="actions">
      <button type="button" class="primary" id="b_store" data-kind="__STORE_TARGET__">__STORE_BUTTON__</button>
    </div>
  </div>

  <div class="card">
    <h2>Danh sách</h2>
    <p class="hint">Thay đổi có hiệu lực ngay. Scheduler tự đọc lại bảng mỗi __RELOAD__ giây.
      Xoá schedule không xoá nến đã lưu.</p>
    <div class="scroll"><table>
      <thead><tr><th>Bật</th><th>Tên</th><th>Khung</th><th>Symbols</th><th>Cron (UTC)</th>
        <th>Lần chạy kế tiếp</th><th>Lần chạy gần nhất</th><th></th></tr></thead>
      <tbody>__ROWS__</tbody>
    </table></div>
  </div>

  <div class="card">
    <h2 id="form_title">Thêm schedule</h2>
    <input type="hidden" id="f_id" value="">
    <div class="row">
      <div><label for="f_name">Tên</label><input type="text" id="f_name" placeholder="pull-5m"></div>
      <div><label for="f_tf">Khung</label><select id="f_tf">__TF_OPTIONS__</select></div>
      <div><label for="f_overlap">overlap_candles</label>
        <input type="number" id="f_overlap" min="0" max="1000" value="3"></div>
    </div>
    <label for="f_cron">Cron (giây phút giờ ngày tháng thứ — UTC)</label>
    <input type="text" id="f_cron" placeholder="5 */5 * * * *">
    <label for="f_symbols">Symbols (để trống = tất cả cặp đang bật)</label>
    <select id="f_symbols" multiple>__SYMBOL_OPTIONS__</select>
    <div class="hint">Giữ Ctrl / Cmd để chọn nhiều.</div>
    <div id="preview" class="small"></div>
    <div class="actions">
      <button type="button" id="b_preview">Xem 3 lần chạy kế tiếp</button>
      <button type="button" class="primary" id="b_save">Lưu</button>
      <button type="button" id="b_reset">Huỷ sửa</button>
    </div>
  </div>
  __SETUP_FIELD__
</div>
<script>
const ACTION = __ACTION_PATH__;
const SCHEDULES = __SCHEDULES_JSON__;
const WATCHER_ON = __WATCHER_ON__;
const RETENTION_5M = __RETENTION_5M__;
const $ = (id) => document.getElementById(id);

function say(text, ok) {
  const box = $("msg");
  box.textContent = text;
  box.className = ok ? "ok" : "err";
}

async function act(body, okText, button) {
  if (button) button.disabled = true;
  const token = $("setup_token");
  if (token) body.setup_token = token.value;
  try {
    const res = await fetch(ACTION, {method: "POST", headers: {"Content-Type": "application/json"},
                                     body: JSON.stringify(body)});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    if (button) button.disabled = false;
    return data;
  } catch (e) {
    say(e.message, false);
    if (button) button.disabled = false;
    return null;
  }
}

function formValue() {
  return {name: $("f_name").value.trim(), timeframe: $("f_tf").value,
          cron: $("f_cron").value.trim(), overlap_candles: Number($("f_overlap").value || 0),
          symbols: Array.from($("f_symbols").selectedOptions).map((o) => o.value)};
}

function showRuns(data) {
  const runs = (data.next_runs || []).map((r) => "<li>" + r.utc + " UTC · " + r.local + "</li>").join("");
  const warns = (data.warnings || []).map((w) => "<li>⚠ " + w + "</li>").join("");
  $("preview").innerHTML = "<ul class='runs'>" + runs + warns + "</ul>";
}

function watcherWarning(item, verb) {
  if (WATCHER_ON && item && item.timeframe === "5m") {
    return "\\n\\nCẢNH BÁO: watcher đang bật. " + verb + " schedule khung 5m thì watcher sẽ không còn nến mới.";
  }
  return "";
}

$("b_preview").addEventListener("click", async (e) => {
  const v = formValue();
  const data = await act({action: "preview", cron: v.cron, timeframe: v.timeframe}, "", e.target);
  if (data) { showRuns(data); say("Cron hợp lệ", true); }
});

$("b_save").addEventListener("click", async (e) => {
  const id = $("f_id").value;
  const body = id ? {action: "update", id: id, schedule: formValue()}
                  : {action: "create", schedule: formValue()};
  const data = await act(body, "", e.target);
  if (!data) return;
  const warns = (data.warnings || []).length ? "\\nCảnh báo: " + data.warnings.join("; ") : "";
  say("Đã lưu " + data.schedule.name + warns, true);
  setTimeout(() => location.reload(), warns ? 2500 : 600);
});

$("b_store").addEventListener("click", async (e) => {
  const kind = e.target.dataset.kind;
  const text = kind === "sqlite"
    ? "Chuyển sang SQLite?\\n\\nChép toàn bộ nến JSON vào SQLite, đối chiếu từng nến, khớp hết mới chuyển. "
      + "SQLite chỉ lưu nến đã đóng nên get_context không còn current_bar."
    : "Chuyển về JSON?\\n\\nChép nến từ SQLite về file JSON rồi mới chuyển. File JSON chỉ giữ theo retention"
      + (RETENTION_5M ? " (5m: " + RETENTION_5M + " nến gần nhất)" : "") + ", phần cũ hơn vẫn nằm trong SQLite.";
  if (!confirm(text)) return;
  say("Đang chép và đối chiếu nến…", true);
  const data = await act({action: "storage_switch", kind: kind}, "", e.target);
  if (!data) return;
  if (!data.ok) {
    say("Chưa chuyển — " + data.mismatched.length + " cặp×khung còn lệch sau khi chép: "
        + data.mismatched.map((m) => m.symbol + " " + m.tf).join(", "), false);
    return;
  }
  say("Đã chuyển sang " + data.kind + (data.copied ? " · chép " + data.copied.rows_written + " nến, "
      + data.copied.pairs + " cặp×khung" : ""), true);
  setTimeout(() => location.reload(), 1500);
});

$("b_reset").addEventListener("click", () => {
  $("f_id").value = ""; $("form_title").textContent = "Thêm schedule";
  $("f_name").value = ""; $("f_cron").value = ""; $("f_overlap").value = 3;
  Array.from($("f_symbols").options).forEach((o) => { o.selected = false; });
  $("preview").innerHTML = "";
});

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const item = SCHEDULES.find((s) => s.id === b.dataset.id);
  if (b.dataset.act === "edit") {
    $("f_id").value = item.id; $("form_title").textContent = "Sửa " + item.name;
    $("f_name").value = item.name; $("f_tf").value = item.timeframe; $("f_cron").value = item.cron;
    $("f_overlap").value = item.overlap_candles;
    Array.from($("f_symbols").options).forEach((o) => {
      o.selected = (item.symbols || []).includes(o.value);
    });
    $("preview").innerHTML = "";
    $("f_name").scrollIntoView({behavior: "smooth", block: "center"});
  } else if (b.dataset.act === "delete") {
    if (!confirm("Xoá schedule " + item.name + "? Nến đã lưu vẫn giữ nguyên." + watcherWarning(item, "Xoá"))) return;
    if (await act({action: "delete", id: item.id}, "", b)) { say("Đã xoá " + item.name, true); location.reload(); }
  } else if (b.dataset.act === "run") {
    say("Đang chạy " + item.name + "…", true);
    const data = await act({action: "run_now", id: item.id}, "", b);
    if (data) { say(item.name + ": " + data.result.status + (data.result.error ? " — " + data.result.error : "")
                    + " (" + data.result.duration_ms + " ms)", data.result.status !== "error");
                setTimeout(() => location.reload(), 1500); }
  }
});

document.addEventListener("change", async (e) => {
  const box = e.target.closest("input[data-toggle]");
  if (!box) return;
  const item = SCHEDULES.find((s) => s.id === box.dataset.toggle);
  if (!box.checked && WATCHER_ON && item.timeframe === "5m"
      && !confirm("Tắt " + item.name + "?" + watcherWarning(item, "Tắt"))) { box.checked = true; return; }
  box.disabled = true;
  const data = await act({action: "toggle", id: item.id, enabled: box.checked}, "", null);
  box.disabled = false;
  if (!data) { box.checked = !box.checked; return; }
  say((box.checked ? "Đã bật " : "Đã tắt ") + item.name, true);
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


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _status(item: dict[str, Any]) -> str:
    status = item.get("last_status")
    if not status:
        return '<span class="small">chưa chạy</span>'
    css = {"ok": "ok", "error": "err"}.get(status, "")
    out = (f'<span class="pill {css}">{_e(status)}</span> {_e(item.get("last_run_text"))}'
           f' <span class="small">{_e(item.get("last_duration_ms"))} ms</span>')
    if item.get("last_error"):
        out += f'<div class="small">{_e(item["last_error"])}</div>'
    return out


def _rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<tr><td colspan="8" class="small">Chưa có schedule nào.</td></tr>'
    out = []
    for item in items:
        checked = " checked" if item["enabled"] else ""
        symbols = ", ".join(item["symbols"]) if item["symbols"] else "tất cả"
        nxt = item.get("next_run_text") or "—"
        out.append(
            "<tr>"
            f'<td><input type="checkbox" data-toggle="{_e(item["id"])}"{checked}></td>'
            f'<td>{_e(item["name"])}</td><td><code>{_e(item["timeframe"])}</code></td>'
            f'<td class="small">{_e(symbols)}</td><td><code>{_e(item["cron"])}</code>'
            f'<div class="small">overlap {_e(item["overlap_candles"])}</div></td>'
            f'<td class="small">{_e(nxt)}</td><td>{_status(item)}</td>'
            f'<td class="act"><button data-act="run" data-id="{_e(item["id"])}">Chạy ngay</button>'
            f'<button data-act="edit" data-id="{_e(item["id"])}">Sửa</button>'
            f'<button class="danger" data-act="delete" data-id="{_e(item["id"])}">Xoá</button></td>'
            "</tr>")
    return "".join(out)


# TM - #KL-SQL - Kline storage SQLite
STORE_NAMES = {"json": "JSON", "sqlite": "SQLite"}


def _store_head(kind: str) -> str:
    active = ' <span class="pill ok">đang dùng</span>'
    return "".join(f"<th>{name}{active if key == kind else ''}</th>"
                   for key, name in STORE_NAMES.items())


def _store_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="4" class="small">Chưa có nến nào.</td></tr>'
    out = []
    for row in rows:
        cells = "".join(
            f'<td>{_e(row[key]["count"])} nến<div class="small">cuối {_e(row[key]["last"])}</div></td>'
            if row.get(key) else '<td class="small">—</td>' for key in STORE_NAMES)
        out.append(f'<tr><td>{_e(row["symbol"])}</td><td><code>{_e(row["tf"])}</code></td>{cells}</tr>')
    return "".join(out)


def render(view: dict[str, Any], *, action_path: str, admin_path: str,
           setup_required: bool) -> str:
    items = view.get("schedules") or []
    storage = view.get("storage") or {}
    mode_warning = ""
    if view.get("mode") != "schedules":
        mode_warning = ('<div class="warn">fetcher.mode đang là <code>interval</code>: vòng lặp cũ '
                        'đang chạy, các schedule dưới đây chỉ được lưu, không tự chạy. '
                        'Nút "Chạy ngay" vẫn dùng được.</div>')
    elif not view.get("running"):
        mode_warning = ('<div class="warn">Scheduler chưa chạy (fetcher.enabled = false hoặc server '
                        'chưa khởi động xong).</div>')
    replacements = {
        "__ADMIN_PATH__": _e(admin_path),
        "__SETUP_BANNER__": SETUP_BANNER if setup_required else "",
        "__SETUP_FIELD__": SETUP_FIELD if setup_required else "",
        "__MODE_WARNING__": mode_warning,
        "__RELOAD__": _e(view.get("reload_seconds")),
        "__ROWS__": _rows(items),
        "__TF_OPTIONS__": "".join(f'<option>{_e(tf)}</option>' for tf in view.get("supported") or []),
        "__SYMBOL_OPTIONS__": "".join(f'<option>{_e(s)}</option>' for s in view.get("symbols") or []),
        "__ACTION_PATH__": _js(action_path),
        "__SCHEDULES_JSON__": _js([{k: i[k] for k in ("id", "name", "timeframe", "symbols", "cron",
                                                       "overlap_candles", "enabled")} for i in items]),
        "__WATCHER_ON__": _js(bool(view.get("watcher_enabled"))),
        "__STORE_KIND__": _e(STORE_NAMES.get(storage.get("kind"), storage.get("kind"))),
        "__STORE_SOURCE__": ("trang này, lưu trong settings.json" if storage.get("source") == "settings.json"
                             else "config.yaml, storage.kline_store"),
        "__SQLITE_PATH__": _e(storage.get("sqlite_path")),
        "__STORE_HEAD__": _store_head(storage.get("kind")),
        "__STORE_ROWS__": _store_rows(storage.get("rows") or []),
        "__STORE_TARGET__": "json" if storage.get("kind") == "sqlite" else "sqlite",
        "__STORE_BUTTON__": "Chuyển về JSON" if storage.get("kind") == "sqlite" else "Chuyển sang SQLite",
        "__RETENTION_5M__": _js(storage.get("retention_5m")),
    }
    page = PAGE
    for needle, value in replacements.items():
        page = page.replace(needle, value)
    return page
