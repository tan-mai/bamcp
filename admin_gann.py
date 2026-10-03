# TM - #GANN-TW - Gann Time Windows
"""Trang admin cho Gann time windows: config, pivot thu cong, su kien, tinh lai,
ket qua backtest gan nhat. Chi render HTML - moi thao tac ghi di qua POST JSON
do server xu ly (giong admin_orb).
"""

from __future__ import annotations

import html
import json
from typing import Any

# symbols KHONG nam o day: no co o bat/tat rieng (muc "Cap phan tich Gann").
SECTIONS = ("pivots", "projections", "scoring", "backtest", "context")
SECTION_HINTS = {
    "pivots": "Đổi khối này thì pivot tự tính lại ở lần gọi kế tiếp (cache theo dấu tay config).",
    "projections": "Danh sách cycle, trọng số, tolerance, bật/tắt từng loại chiếu.",
    "scoring": "Ngưỡng cửa sổ (min_score), gộp cửa sổ, số hit tối đa.",
    "backtest": "Tham số đo của backtest_time_windows.",
    "context": "Hai field Gann trong get_context. time_windows đang tắt vì backtest chưa thấy hiệu ứng.",
}

PAGE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BAMCP — Gann Time Windows</title>
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
  .card h2 { font-size: 15px; margin: 0 0 6px; }
  .card > .hint:first-of-type { margin-bottom: 14px; }
  label { display:block; font-size:13px; color:var(--muted); margin:12px 0 5px; }
  input[type=text], input[type=date], input[type=number], select, textarea {
    width:100%; padding:8px 10px; border:1px solid var(--line); border-radius:7px;
    background:var(--bg); color:var(--ink); font:inherit; font-size:14px; }
  textarea { font-family: ui-monospace,"Cascadia Code",Consolas,monospace; font-size:12.5px;
    min-height: 120px; resize: vertical; }
  input:focus, select:focus, textarea:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  .row { display:flex; gap:12px; flex-wrap:wrap; } .row > * { flex:1; min-width:140px; }
  .hint { font-size:12px; color:var(--muted); margin-top:4px; }
  .actions { display:flex; gap:10px; align-items:center; margin-top:14px; flex-wrap:wrap; }
  button { font:inherit; font-size:13px; padding:7px 14px; border-radius:7px;
    border:1px solid var(--line); background:var(--card); color:var(--ink); cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.danger { color:var(--err); }
  button:disabled { opacity:.55; cursor:default; }
  #msg { position: sticky; top: 0; z-index: 2; font-size:13px; white-space:pre-wrap;
    padding: 8px 12px; border-radius: 8px; margin-bottom: 14px; display:none; }
  #msg.ok { display:block; color:var(--ok); background:var(--card); border:1px solid var(--ok); }
  #msg.err { display:block; color:var(--err); background:var(--card); border:1px solid var(--err); }
  .warn { background:var(--warn-bg); border:1px solid var(--warn-line);
    border-radius:8px; padding:12px 14px; font-size:13px; margin-bottom:18px; }
  code { font-family: ui-monospace,"Cascadia Code",Consolas,monospace; font-size:12.5px; }
  .scroll { overflow-x:auto; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; font-weight:600; color:var(--muted); font-size:12px;
    padding:6px 8px 6px 0; border-bottom:1px solid var(--line); white-space:nowrap; }
  td { padding:7px 8px 7px 0; border-bottom:1px solid var(--line); vertical-align:top; }
  tr:last-child td { border-bottom:none; }
  td.num { font-variant-numeric: tabular-nums; white-space:nowrap; }
  td.act { text-align:right; white-space:nowrap; }
  td.act button { padding:3px 9px; font-size:12px; margin-left:4px; }
  .pill { display:inline-block; font-size:11px; padding:1px 8px; border-radius:99px;
    border:1px solid var(--line); white-space:nowrap; }
  .pill.ok { color:var(--ok); border-color:var(--ok); }
  .pill.err { color:var(--err); border-color:var(--err); }
  .small { font-size:12px; color:var(--muted); }
  details { margin-top:14px; border-top:1px solid var(--line); padding-top:10px; }
  summary { cursor:pointer; font-size:13px; font-weight:600; }
  .tabs { display:flex; gap:6px; flex-wrap:wrap; margin: 4px 0 12px; }
  .tabs button.on { border-color:var(--accent); color:var(--accent); font-weight:600; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Gann Time Windows</h1>
  <p class="sub">Pivot, cửa sổ thời gian, backtest. <a href="__ADMIN_PATH__">← cài đặt chung</a>
    · config đang chạy: <code>__CONFIG_VERSION__</code></p>
  <div id="msg"></div>
  __SETUP_BANNER__
  __CFG_WARNING__

  <div class="card">
    <h2>Cặp phân tích Gann</h2>
    <p class="hint">Bật cặp nào thì Claude mới phân tích Gann cho cặp đó (pivot, swing state,
      cửa sổ thời gian, backtest, hai field trong get_context). Tắt thì các tool Gann từ chối
      và chỉ ra chỗ bật ở đây. Có hiệu lực ngay, không cần restart.
      Cặp phải đang được theo dõi ở <a href="__ADMIN_PATH__">trang cài đặt chung</a> mới có dữ liệu nến.</p>
    <div class="scroll"><table>
      <thead><tr><th>Bật</th><th>Cặp</th><th>Nến 1d</th><th>Nến 1w</th><th></th></tr></thead>
      <tbody>__TOGGLE_ROWS__</tbody>
    </table></div>
    <p class="hint">Khung 1d chỉ có ~500 nến (khoảng 1.4 năm) tới khi tải lịch sử: nhờ Claude gọi
      <code>backfill_klines(timeframe="1d", symbol="...")</code>. Cặp niêm yết gần đây (vd XAUUSDT từ
      12/2025) thì đã đủ toàn bộ lịch sử sẵn, không cần.</p>
  </div>

  <div class="card">
    <h2>Pivot theo cặp</h2>
    <p class="hint">Pivot tự tính lại khi có nến D mới đóng, khi đổi config pivot hoặc pivot thủ công.
      Nút "Tính lại" dùng sau khi vừa backfill thêm lịch sử.</p>
    <div class="scroll"><table>
      <thead><tr><th>Cặp</th><th>Pivot 1w</th><th>Pivot 1d</th><th>Xu hướng 1d</th>
        <th>Tính lúc</th><th></th></tr></thead>
      <tbody>__SYMBOL_ROWS__</tbody>
    </table></div>
  </div>

  <div class="card">
    <h2>Pivot thủ công</h2>
    <p class="hint"><b>exclude</b> = bỏ pivot đó (áp cả quá khứ: bạn nói nó chưa từng là pivot).
      <b>pin</b> = ép bậc cho pivot đã có, hoặc thêm pivot mới — giá lấy từ high/low thật của nến.
      Lưu ở <code>__MANUAL_FILE__</code>, không bị ghi đè khi tính lại.</p>
    <div class="tabs" id="symtabs">__SYMBOL_TABS__</div>
    <div id="pivotlists">__PIVOT_LISTS__</div>
    <details>
      <summary>Thêm bằng tay (ngày không có trong danh sách trên)</summary>
      <div class="row">
        <div><label for="mp_symbol">Cặp</label><select id="mp_symbol">__SYMBOL_OPTIONS__</select></div>
        <div><label for="mp_tf">Khung</label><select id="mp_tf"><option>1d</option><option>1w</option></select></div>
        <div><label for="mp_action">Thao tác</label><select id="mp_action"><option>pin</option><option>exclude</option></select></div>
        <div><label for="mp_type">Loại</label><select id="mp_type"><option>low</option><option>high</option></select></div>
      </div>
      <div class="row">
        <div><label for="mp_date">Ngày của nến</label><input type="date" id="mp_date"></div>
        <div><label for="mp_level">Bậc (pin)</label><select id="mp_level"><option>major</option><option>intermediate</option><option>minor</option></select></div>
        <div style="flex:2"><label for="mp_note">Ghi chú</label><input type="text" id="mp_note" maxlength="200"></div>
      </div>
      <div class="hint">Khung 1w: ngày là thứ Hai mở tuần.</div>
      <div class="actions"><button class="primary" id="mp_add">Thêm</button></div>
    </details>
    <h2 style="margin-top:18px">Đang áp dụng</h2>
    <div class="scroll"><table>
      <thead><tr><th>Cặp</th><th>Khung</th><th>Thao tác</th><th>Loại</th><th>Ngày</th>
        <th>Bậc</th><th>Ghi chú</th><th></th></tr></thead>
      <tbody>__MANUAL_ROWS__</tbody>
    </table></div>
  </div>

  <div class="card">
    <h2>Sự kiện vĩ mô</h2>
    <p class="hint">FOMC, CPI, hết hạn quyền chọn… Cặp để trống = áp cho mọi cặp.
      Lưu ở <code>__EVENTS_FILE__</code>.</p>
    <div class="scroll"><table>
      <thead><tr><th>Ngày</th><th>Tên</th><th>Trọng số</th><th>Cặp</th><th></th></tr></thead>
      <tbody>__EVENT_ROWS__</tbody>
    </table></div>
    <div class="row">
      <div><label for="ev_date">Ngày</label><input type="date" id="ev_date"></div>
      <div style="flex:2"><label for="ev_name">Tên</label><input type="text" id="ev_name" maxlength="80" placeholder="CPI Mỹ"></div>
      <div><label for="ev_weight">Trọng số</label><input type="number" id="ev_weight" value="1" min="0" step="0.1"></div>
      <div style="flex:2"><label for="ev_symbols">Cặp (phẩy)</label><input type="text" id="ev_symbols" placeholder="để trống = mọi cặp"></div>
    </div>
    <div class="actions"><button class="primary" id="ev_add">Thêm sự kiện</button></div>
  </div>

  <div class="card">
    <h2>Backtest gần nhất</h2>
    __BACKTEST__
  </div>

  <div class="card">
    <h2>Config</h2>
    <p class="hint">Mỗi khối thay <b>cả khối</b> của <code>time_windows</code> trong config.yaml
      và có hiệu lực ngay ở lần gọi tool kế tiếp — không cần restart. "Về mặc định" xóa bản sửa,
      quay về config.yaml. <code>enabled</code> và <code>paths</code> chỉ sửa trong config.yaml.
      Lưu ở <code>__CONFIG_FILE__</code>.</p>
    __CONFIG_SECTIONS__
  </div>

  __SETUP_FIELD__
</div>

<script>
const ACTION_PATH = __ACTION_PATH__;
const $ = (id) => document.getElementById(id);

function say(text, ok) {
  const m = $("msg");
  m.textContent = text;
  m.className = ok ? "ok" : "err";
  if (!ok) window.scrollTo({top: 0, behavior: "smooth"});
}

async function send(payload) {
  const token = $("setup_token");
  if (token) payload.setup_token = token.value;
  const res = await fetch(ACTION_PATH, {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  let body = {};
  try { body = await res.json(); } catch (e) { /* than rong */ }
  if (!res.ok) throw new Error(body.error || ("HTTP " + res.status));
  return body;
}

async function act(payload, doneText, button) {
  if (button) button.disabled = true;
  try {
    const body = await send(payload);
    say((doneText || "Đã lưu") + (body.note ? "\\n" + body.note : ""), true);
    setTimeout(() => location.reload(), 700);
    return true;
  } catch (e) {
    say(e.message, false);
    if (button) button.disabled = false;
    return false;
  }
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const d = b.dataset;
  if (d.act === "recompute") {
    act({action: "recompute", symbol: d.symbol}, "Đã tính lại " + d.symbol, b);
  } else if (d.act === "pivot") {
    act({action: "pivot_add", symbol: d.symbol, timeframe: d.tf, op: d.op,
         type: d.type, date: d.date, level: d.level || "major", note: ""},
        (d.op === "exclude" ? "Đã bỏ " : "Đã ghim ") + d.type + " " + d.date, b);
  } else if (d.act === "pivot_delete") {
    act({action: "pivot_delete", symbol: d.symbol, timeframe: d.tf,
         index: Number(d.index)}, "Đã xóa", b);
  } else if (d.act === "event_delete") {
    act({action: "event_delete", index: Number(d.index)}, "Đã xóa sự kiện", b);
  } else if (d.act === "config_save") {
    let value;
    try { value = JSON.parse($("cfg_" + d.section).value); }
    catch (err) { say("Khối " + d.section + " không phải JSON hợp lệ: " + err.message, false); return; }
    act({action: "config_save", section: d.section, value: value}, "Đã lưu khối " + d.section, b);
  } else if (d.act === "config_reset") {
    act({action: "config_reset", section: d.section}, "Khối " + d.section + " về mặc định", b);
  } else if (d.act === "tab") {
    document.querySelectorAll("#symtabs button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll("#pivotlists > div").forEach((x) => {
      x.style.display = x.dataset.symbol === d.symbol ? "" : "none";
    });
  }
});

document.addEventListener("change", (e) => {
  const box = e.target.closest("input[data-toggle]");
  if (!box) return;
  box.disabled = true;
  act({action: "symbol_toggle", symbol: box.dataset.toggle, enabled: box.checked},
      (box.checked ? "Đã bật Gann cho " : "Đã tắt Gann cho ") + box.dataset.toggle, null)
    .then((saved) => {
      box.disabled = false;
      // Luu khong duoc thi tra o ve trang thai cu - khong de trang noi sai
      if (!saved) box.checked = !box.checked;
    });
});

$("mp_add").addEventListener("click", (e) => {
  act({action: "pivot_add", symbol: $("mp_symbol").value, timeframe: $("mp_tf").value,
       op: $("mp_action").value, type: $("mp_type").value, date: $("mp_date").value,
       level: $("mp_level").value, note: $("mp_note").value}, "Đã thêm pivot thủ công", e.target);
});

$("ev_add").addEventListener("click", (e) => {
  const symbols = $("ev_symbols").value.split(",").map((s) => s.trim().toUpperCase())
    .filter((s) => s);
  act({action: "event_add", date: $("ev_date").value, name: $("ev_name").value,
       weight: Number($("ev_weight").value || 1), symbols: symbols}, "Đã thêm sự kiện", e.target);
});
</script>
</body>
</html>
"""

SETUP_BANNER = """<div class="warn">
<strong>Chưa đặt mật khẩu.</strong> Mọi thao tác ghi cần <em>setup token</em> in trong log container
(ô ở cuối trang). Nên đặt mật khẩu ở trang cài đặt chung trước.
</div>"""

SETUP_FIELD = """<div class="card"><label for="setup_token">Setup token</label>
  <input type="text" id="setup_token" autocomplete="off" placeholder="dán chuỗi từ dòng BAMCP SETUP TOKEN"></div>"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _js(value: Any) -> str:
    """Nhung du lieu vao <script>. Chan "</script>" bang cach escape dau <."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return _e(value)


def _toggle_rows(rows: list[dict[str, Any]]) -> str:
    """Moi cap dang theo doi mot dong: o bat/tat + tinh trang du lieu 1d/1w."""
    if not rows:
        return '<tr><td colspan="5" class="small">Chưa theo dõi cặp nào.</td></tr>'
    out = []
    for row in rows:
        sym = _e(row["symbol"])
        checked = " checked" if row.get("gann") else ""
        notes = []
        if not row.get("tracked"):
            notes.append('<span class="pill err">chưa theo dõi</span> thêm ở trang cài đặt chung')
        elif not row.get("app_enabled"):
            notes.append('<span class="pill err">đang tắt ở trang chung</span> không kéo nến mới')
        if row.get("tracked") and not row.get("bars_1d"):
            notes.append('<span class="pill">chưa có nến</span> đợi một chu kỳ kéo dữ liệu')

        def bars(tf: str) -> str:
            count = row.get(f"bars_{tf}") or 0
            first = row.get(f"first_{tf}")
            return f"{count:,} từ {_e(first)}" if count else "—"

        out.append(
            f'<tr><td><input type="checkbox" data-toggle="{sym}"{checked} '
            f'aria-label="Bật Gann cho {sym}"></td>'
            f'<td><b>{sym}</b></td><td class="num">{bars("1d")}</td>'
            f'<td class="num">{bars("1w")}</td>'
            f'<td class="small">{" · ".join(notes)}</td></tr>')
    return "".join(out)


def _symbol_rows(symbols: list[dict[str, Any]]) -> str:
    if not symbols:
        return '<tr><td colspan="6" class="small">Chưa bật Gann cho cặp nào — bật ở mục trên.</td></tr>'
    out = []
    for row in symbols:
        sym = _e(row["symbol"])
        trend = row.get("trend") or "—"
        note = row.get("note") or row.get("error") or ""
        out.append(
            f'<tr><td><b>{sym}</b></td>'
            f'<td class="num">{_e(row.get("pivots_1w", "—"))}</td>'
            f'<td class="num">{_e(row.get("pivots_1d", "—"))}</td>'
            f'<td><span class="pill">{_e(trend)}</span>'
            f'<div class="small">{_e(note)}</div></td>'
            f'<td class="small">{_e((row.get("computed_at") or "")[:16].replace("T", " "))}</td>'
            f'<td class="act"><button data-act="recompute" data-symbol="{sym}">Tính lại</button></td></tr>')
    return "".join(out)


def _pivot_lists(symbols: list[dict[str, Any]]) -> str:
    """Pivot gan day cua tung cap, kem nut exclude/pin - khoi phai go ngay tay."""
    blocks = []
    for n, row in enumerate(symbols):
        sym = _e(row["symbol"])
        lines = []
        for p in row.get("recent") or []:
            attrs = (f'data-symbol="{sym}" data-tf="{_e(p["timeframe"])}" '
                     f'data-type="{_e(p["type"])}" data-date="{_e(p["date"])}"')
            source = '<span class="pill">tay</span>' if p.get("source") == "manual" else ""
            lines.append(
                f'<tr><td>{_e(p["timeframe"])}</td><td>{_e(p["type"])}</td>'
                f'<td>{_e(p["date"])}</td><td class="num">{_num(p.get("price"))}</td>'
                f'<td>{_e(p.get("level"))} {source}</td>'
                f'<td class="num">{_num(p.get("move_pct"))}%</td>'
                f'<td class="act">'
                f'<button data-act="pivot" data-op="pin" data-level="major" {attrs}>Ghim major</button>'
                f'<button class="danger" data-act="pivot" data-op="exclude" {attrs}>Bỏ</button>'
                f'</td></tr>')
        body = "".join(lines) or '<tr><td colspan="7" class="small">Chưa có pivot.</td></tr>'
        hidden = "" if n == 0 else ' style="display:none"'
        blocks.append(
            f'<div data-symbol="{sym}"{hidden}>'
            f'<div class="scroll"><table><thead><tr><th>Khung</th><th>Loại</th><th>Ngày</th>'
            f'<th>Giá</th><th>Bậc</th><th>Biên độ</th><th></th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>'
            f'<div class="hint">20 pivot 1d gần nhất + 10 pivot 1w gần nhất.</div></div>')
    return "".join(blocks)


def _symbol_tabs(symbols: list[dict[str, Any]]) -> str:
    out = []
    for n, row in enumerate(symbols):
        active = ' class="on"' if n == 0 else ""
        out.append(f'<button data-act="tab" data-symbol="{_e(row["symbol"])}"{active}>'
                   f'{_e(row["symbol"])}</button>')
    return "".join(out)


def _manual_rows(manual: dict[str, Any]) -> str:
    out = []
    for sym in sorted(manual):
        block = manual[sym] if isinstance(manual[sym], dict) else {}
        for tf in sorted(block):
            for i, entry in enumerate(block[tf] or []):
                if not isinstance(entry, dict):
                    continue
                op = str(entry.get("action") or "")
                out.append(
                    f'<tr><td>{_e(sym)}</td><td>{_e(tf)}</td>'
                    f'<td><span class="pill {"err" if op == "exclude" else "ok"}">{_e(op)}</span></td>'
                    f'<td>{_e(entry.get("type"))}</td><td>{_e(entry.get("date"))}</td>'
                    f'<td>{_e(entry.get("level") if op == "pin" else "")}</td>'
                    f'<td class="small">{_e(entry.get("note"))}</td>'
                    f'<td class="act"><button class="danger" data-act="pivot_delete" '
                    f'data-symbol="{_e(sym)}" data-tf="{_e(tf)}" data-index="{i}">Xóa</button></td></tr>')
    return "".join(out) or '<tr><td colspan="8" class="small">Chưa có pivot thủ công nào.</td></tr>'


def _event_rows(events: list[dict[str, Any]]) -> str:
    out = []
    for i, item in enumerate(events):
        scope = ", ".join(item.get("symbols") or []) or "mọi cặp"
        out.append(
            f'<tr><td>{_e(item.get("date"))}</td><td>{_e(item.get("name"))}</td>'
            f'<td class="num">{_e(item.get("weight", 1))}</td><td class="small">{_e(scope)}</td>'
            f'<td class="act"><button class="danger" data-act="event_delete" '
            f'data-index="{i}">Xóa</button></td></tr>')
    return "".join(out) or '<tr><td colspan="5" class="small">Chưa có sự kiện nào.</td></tr>'


def _backtest(result: dict[str, Any] | None) -> str:
    if not result:
        return ('<p class="small">Chưa có lần backtest nào. Nhờ Claude gọi '
                '<code>backtest_time_windows(symbol="BTCUSDT")</code>.</p>')
    if result.get("status") != "done":
        return (f'<p class="small">Lần gần nhất <code>{_e(result.get("run_id"))}</code>: '
                f'{_e(result.get("status"))} {_e(result.get("error") or "")}</p>')
    req = result.get("request") or {}
    head = (f'<p class="small"><code>{_e(result["run_id"])}</code> · {_e(result.get("symbol"))} · '
            f'{_e(req.get("start"))} → {_e(req.get("end"))} · {_e(result.get("days"))} ngày · '
            f'{_e(result.get("windows"))} cửa sổ phủ {_e(result.get("coverage_pct"))}% số ngày · '
            f'seed {_e(req.get("seed"))}, {_e(req.get("permutations"))} permutations</p>')

    def p_cell(p: Any) -> str:
        if p is None:
            return "—"
        cls = "ok" if p < 0.05 else ""
        return f'<span class="pill {cls}">{_num(p, 3)}</span>'

    overall = []
    for metric, row in (result.get("overall") or {}).items():
        effect = (f'lift {_num(row.get("lift"), 3)}' if row.get("lift") is not None
                  else f'delta {_num(row.get("delta"), 3)}')
        overall.append(
            f'<tr><td>{_e(metric)}</td><td class="num">{effect}</td>'
            f'<td class="num">{_num(row.get("mean_in"), 3)} / {_num(row.get("mean_out"), 3)}</td>'
            f'<td>{p_cell(row.get("p_value"))}</td><td>{p_cell(row.get("p_value_global"))}</td>'
            f'<td class="num">{_e(row.get("n_in"))} / {_e(row.get("n_out"))}</td></tr>')
    suggested = (result.get("suggested_weights") or {}).get("types") or {}
    types = []
    for name, row in (result.get("by_type") or {}).items():
        tip = suggested.get(name) or {}
        types.append(
            f'<tr><td>{_e(name)}</td><td class="num">{_num(row.get("lift"), 3)}</td>'
            f'<td>{p_cell(row.get("p_value"))}</td>'
            f'<td class="num">{_e(row.get("days_touched"))}</td>'
            f'<td class="num">{_num(tip.get("current"))} → {_num(tip.get("suggested"))}</td>'
            f'<td class="small">{_e(tip.get("reason"))}</td></tr>')
    cycles = []
    for days, row in (result.get("by_cycle_days") or {}).items():
        cycles.append(f'<tr><td>{_e(days)} ngày</td><td class="num">{_num(row.get("lift"), 3)}</td>'
                      f'<td>{p_cell(row.get("p_value"))}</td><td class="num">{_e(row.get("n_in"))}</td></tr>')
    return (
        head
        + '<div class="scroll"><table><thead><tr><th>Chỉ số</th><th>Hiệu ứng</th>'
          '<th>TB trong / ngoài</th><th>p (dịch ±)</th><th>p (toàn cục)</th><th>Mẫu trong / ngoài</th>'
          '</tr></thead><tbody>' + "".join(overall) + '</tbody></table></div>'
        + '<p class="hint">p (dịch ±): mỗi cửa sổ dịch ngẫu nhiên ±shift_days, giữ chế độ biến động — '
          'con số chính. p (toàn cục) nhỏ mà p (dịch ±) lớn nghĩa là hiệu ứng đến từ chế độ biến động, '
          'không phải từ thời điểm Gann.</p>'
        + '<h2 style="margin-top:16px">Theo loại chiếu (range_atr)</h2>'
        + '<div class="scroll"><table><thead><tr><th>Loại</th><th>Lift</th><th>p</th>'
          '<th>Ngày chạm</th><th>Trọng số đề xuất</th><th></th></tr></thead><tbody>'
        + "".join(types) + '</tbody></table></div>'
        + '<p class="hint">Đề xuất KHÔNG tự ghi vào config — sửa ở khối projections bên dưới nếu muốn.</p>'
        + '<details><summary>Theo cycle_days</summary><div class="scroll"><table><thead><tr>'
          '<th>Chu kỳ</th><th>Lift</th><th>p</th><th>Ngày chạm</th></tr></thead><tbody>'
        + "".join(cycles) + '</tbody></table></div>'
        + '<p class="hint">13 phép kiểm cùng lúc: vài cái p &lt; 0.05 có thể chỉ là ngẫu nhiên.</p></details>')


def _config_sections(config: dict[str, Any]) -> str:
    out = []
    for section in SECTIONS:
        info = config.get(section) or {}
        value = json.dumps(info.get("value"), ensure_ascii=False, indent=2)
        rows = min(28, max(4, value.count("\n") + 2))
        badge = ('<span class="pill ok">đã sửa</span>' if info.get("overridden")
                 else '<span class="pill">mặc định</span>')
        out.append(
            f'<details{" open" if section == "projections" else ""}>'
            f'<summary>{_e(section)} {badge}</summary>'
            f'<p class="hint">{_e(SECTION_HINTS.get(section, ""))}</p>'
            f'<textarea id="cfg_{_e(section)}" rows="{rows}" spellcheck="false">{_e(value)}</textarea>'
            f'<div class="actions">'
            f'<button class="primary" data-act="config_save" data-section="{_e(section)}">Lưu khối này</button>'
            + (f'<button data-act="config_reset" data-section="{_e(section)}">Về mặc định</button>'
               if info.get("overridden") else "")
            + '</div></details>')
    return "".join(out)


def render(view: dict[str, Any], *, action_path: str, admin_path: str,
           setup_required: bool) -> str:
    """view = server._gann_admin_view()."""
    symbols = view.get("symbols") or []
    options = "".join(f'<option>{_e(r["symbol"])}</option>' for r in symbols)
    warning = view.get("config_warning")
    replacements = {
        "__ADMIN_PATH__": _e(admin_path),
        "__CONFIG_VERSION__": _e(view.get("config_version")),
        "__SETUP_BANNER__": SETUP_BANNER if setup_required else "",
        "__SETUP_FIELD__": SETUP_FIELD if setup_required else "",
        "__CFG_WARNING__": f'<div class="warn">{_e(warning)}</div>' if warning else "",
        "__TOGGLE_ROWS__": _toggle_rows(view.get("toggles") or []),
        "__SYMBOL_ROWS__": _symbol_rows(symbols),
        "__SYMBOL_TABS__": _symbol_tabs(symbols),
        "__PIVOT_LISTS__": _pivot_lists(symbols),
        "__SYMBOL_OPTIONS__": options,
        "__MANUAL_ROWS__": _manual_rows(view.get("manual") or {}),
        "__MANUAL_FILE__": _e(view.get("manual_file")),
        "__EVENT_ROWS__": _event_rows(view.get("events") or []),
        "__EVENTS_FILE__": _e(view.get("events_file")),
        "__BACKTEST__": _backtest(view.get("backtest")),
        "__CONFIG_SECTIONS__": _config_sections(view.get("config") or {}),
        "__CONFIG_FILE__": _e(view.get("config_file")),
        "__ACTION_PATH__": _js(action_path),
    }
    page = PAGE
    for needle, value in replacements.items():
        page = page.replace(needle, value)
    return page
