# TM - #BOOKMAP - Order flow Bookmap
"""Trang admin "Order Flow": bang cap (sua moi truong, them / xoa / bat tat), thoi han luu, trang thai collector
(tu lam moi 10 giay) va nut backfill aggTrades. Luu vao paths.orderflow_config_file; collector tu doc lai trong 30 giay.

Moi chu tu server dua vao DOM bang textContent / value, khong ghep HTML tu du lieu.
"""
from __future__ import annotations

import html
import json
from typing import Any

import admin_ui

CSS = """
  .of-table td { padding:6px 8px; }
  .of-table input[type=number], .of-table input[type=text], .of-table select { min-height:30px; padding:4px 6px;
    font-size:12.5px; min-width:74px; }
  .of-table input.sym { min-width:96px; text-transform:uppercase; font-family:var(--mono); }
  .of-table th { font-size:10.5px; }
  .of-table th small { display:block; text-transform:none; letter-spacing:0; font-weight:400; color:var(--muted); }
  .st-err { color:var(--err); font-weight:600; }
  .bar { height:6px; border-radius:99px; background:var(--surface-2); overflow:hidden; min-width:90px; }
  .bar i { display:block; height:100%; background:var(--accent); }
  #staleBox:empty { display:none; }
  .ret { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:0 14px; }
"""

PAGE = """<div class="page-head">
  <h1>Order Flow</h1>
  <p class="sub">Heatmap thanh khoản, bong bóng, CVD, volume profile, COB kiểu Bookmap. Collector chạy thành process
    riêng (<code>bamcp-orderflow</code>), ghi vào <code>__DB_PATH__</code>.</p>
</div>
<div id="msg" data-msg="toast"></div>
__SETUP_BANNER__
__CONFIG_ERROR__

<div class="card">
  <h2>Trạng thái collector <span class="small" id="stAt"></span></h2>
  <p class="hint">Đọc từ bảng <code>orderflow_status</code>, tự làm mới mỗi 10 giây. Đỏ = không có event quá
    __STALE__ giây (collector dừng, mất mạng, hoặc chưa chạy). Dung lượng DB: <b id="dbSize">—</b>.</p>
  <div id="staleBox" class="warn"></div>
  <div class="scroll"><table>
    <thead><tr><th>Cặp</th><th>Kết nối</th><th>Event cuối</th><th>Snapshot cuối</th><th>Mức giá trong sổ</th>
      <th>Resync hôm nay</th><th>Lệnh khớp từ</th><th>Sổ lệnh từ</th><th>Lỗi gần nhất</th></tr></thead>
    <tbody id="stRows"><tr><td colspan="9" class="small">Đang tải…</td></tr></tbody>
  </table></div>
</div>

<div class="card">
  <h2>Cấu hình <span class="pill" id="srcPill">__SOURCE__</span></h2>
  <p class="hint">Lưu vào <code>__CONFIG_FILE__</code>. Collector kiểm tra file mỗi 30 giây: bật / tắt cặp thì
    subscribe / unsubscribe, đổi tham số thì có hiệu lực từ snapshot kế tiếp, không cần restart.
    Giá trị sai thì không lưu (collector cũng giữ cấu hình cũ nếu file bị sửa tay sai).</p>
  <label class="check"><input type="checkbox" id="ofEnabled"> Bật order flow (tắt = collector ngừng theo dõi mọi cặp)</label>
  <div class="scroll" style="margin-top:12px"><table class="of-table">
    <thead><tr><th>Bật</th><th>Cặp</th><th>Market</th>
      <th>bucket_size<small>giá / bucket</small></th>
      <th>depth_range_pct<small>±, vd 0.03</small></th>
      <th>snapshot_interval_sec</th>
      <th>footprint_res_sec<small>chia hết 60</small></th>
      <th>big_trade_min_qty<small>base asset</small></th>
      <th>bubble_merge_ms</th>
      <th>wall_k<small>× median</small></th>
      <th>absorption_min_qty</th><th></th></tr></thead>
    <tbody id="symRows"></tbody>
  </table></div>
  <div class="actions"><button type="button" id="addRow">+ Thêm cặp</button></div>
  <h3>Thời hạn lưu (ngày)</h3>
  <div class="ret" id="retBox"></div>
  <p class="hint">Job dọn dữ liệu chạy 00:10 UTC hằng ngày trong collector. Footprint res nhỏ hết hạn được gộp sang
    res 60s trước khi xoá. Sau khi xoá chạy <code>PRAGMA incremental_vacuum</code>.</p>
  <div class="actions"><button type="button" class="primary" id="saveCfg">Lưu cấu hình</button></div>
</div>

<div class="card">
  <h2>Backfill aggTrades</h2>
  <p class="hint">Tính lại footprint và bong bóng từ aggTrades lịch sử: ngày cũ tải file daily data.binance.vision,
    hôm nay dùng REST (tôn trọng rate limit). Ngày UTC, tối đa __MAX_DAYS__ ngày mỗi lần. Chạy lại không nhân đôi.
    <b>Heatmap không backfill được</b> (Binance không có lịch sử sổ lệnh miễn phí).</p>
  <div class="row">
    <div><label for="bfSym">Cặp</label><select id="bfSym"></select></div>
    <div><label for="bfFrom">Từ ngày (UTC)</label><input type="date" id="bfFrom"></div>
    <div><label for="bfTo">Đến ngày (UTC)</label><input type="date" id="bfTo"></div>
    <div class="end"><button type="button" class="primary w100" id="bfRun">Chạy backfill</button></div>
  </div>
  <div class="scroll" style="margin-top:14px"><table>
    <thead><tr><th>Tạo lúc</th><th>Cặp</th><th>Khoảng</th><th>Trạng thái</th><th>Tiến độ</th><th>Ghi chú</th></tr></thead>
    <tbody id="jobRows"></tbody>
  </table></div>
</div>
__SETUP_FIELD__
<script>
const ACTION = __ACTION_PATH__;
const VIEW = __VIEW_JSON__;
const $ = (id) => document.getElementById(id);
const FIELDS = VIEW.fields;
const TZ = VIEW.tz;

function say(text, ok) { const box = $("msg"); box.textContent = text; box.className = ok ? "ok" : "err"; }

async function act(body, button) {
  if (button) button.disabled = true;
  const token = $("setup_token");
  if (token) body.setup_token = token.value;
  try {
    const res = await fetch(ACTION, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  } catch (e) { say(e.message, false); return null; }
  finally { if (button) button.disabled = false; }
}

function node(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (v === null || v === undefined || v === false) return;
    if (k === "class") n.className = v; else if (k in n) n[k] = v; else n.setAttribute(k, v);
  });
  kids.flat().forEach((c) => { if (c !== null && c !== undefined && c !== false) n.append(c); });
  return n;
}
const fmt = new Intl.DateTimeFormat("vi-VN", {timeZone: TZ, hour12: false, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit"});
const when = (ms) => (ms ? fmt.format(ms) : "—");
function ago(ms) {
  if (!ms) return "";
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  return s < 90 ? s + " giây trước" : s < 5400 ? Math.round(s / 60) + " phút trước" : Math.round(s / 3600) + " giờ trước";
}

// ---------------------------------------------------------------- bang cap
function symRow(s) {
  const tr = node("tr");
  tr.append(node("td", {}, node("input", {type: "checkbox", checked: s.enabled !== false, "data-k": "enabled"})));
  tr.append(node("td", {}, node("input", {type: "text", class: "sym", value: s.symbol || "", "data-k": "symbol", placeholder: "BTCUSDT"})));
  const market = node("select", {"data-k": "market"}, ["futures", "spot"].map((m) => node("option", {value: m, selected: (s.market || "futures") === m}, m)));
  tr.append(node("td", {}, market));
  FIELDS.forEach((f) => {
    tr.append(node("td", {}, node("input", {type: "number", step: "any", min: String(f.min), max: String(f.max),
      value: s[f.key] === undefined || s[f.key] === null ? (f.default ?? "") : String(s[f.key]), "data-k": f.key})));
  });
  const del = node("button", {type: "button", class: "danger", textContent: "Xoá"});
  del.addEventListener("click", () => { if (confirm("Xoá cặp " + (s.symbol || "mới") + " khỏi order flow? Dữ liệu đã lưu vẫn giữ.")) tr.remove(); });
  tr.append(node("td", {class: "act"}, del));
  return tr;
}

function readConfig() {
  const symbols = [...$("symRows").querySelectorAll("tr")].map((tr) => {
    const out = {};
    tr.querySelectorAll("[data-k]").forEach((el) => {
      const k = el.dataset.k;
      out[k] = el.type === "checkbox" ? el.checked : el.value.trim();
    });
    out.symbol = (out.symbol || "").toUpperCase();
    return out;
  });
  const retention = {};
  $("retBox").querySelectorAll("input").forEach((el) => { retention[el.dataset.k] = el.value.trim(); });
  return {enabled: $("ofEnabled").checked, retention, symbols};
}

function renderConfig(cfg) {
  $("ofEnabled").checked = !!cfg.enabled;
  $("symRows").replaceChildren(...cfg.symbols.map(symRow));
  $("retBox").replaceChildren(...Object.entries(cfg.retention).map(([k, v]) =>
    node("div", {}, node("label", {textContent: k, htmlFor: "ret_" + k}),
      node("input", {type: "number", min: "1", max: "3650", step: "1", id: "ret_" + k, value: String(v), "data-k": k}))));
  $("bfSym").replaceChildren(...cfg.symbols.map((s) => node("option", {value: s.symbol, textContent: s.symbol})));
}

$("addRow").addEventListener("click", () => {
  const defaults = {enabled: true, market: "futures"};
  FIELDS.forEach((f) => { if (f.default !== null) defaults[f.key] = f.default; });
  const row = symRow(defaults);
  $("symRows").append(row);
  row.querySelector("input.sym").focus();
});

$("saveCfg").addEventListener("click", async (e) => {
  const data = await act({action: "save", config: readConfig()}, e.target);
  if (!data) return;
  renderConfig(data.config);
  $("srcPill").textContent = "trang này";
  say("Đã lưu. Collector áp dụng trong ≤ 30 giây.", true);
  refreshStatus();
});

// ---------------------------------------------------------------- trang thai
function renderStatus(st) {
  $("dbSize").textContent = st.db_size_mb === null ? "chưa có file" : st.db_size_mb + " MB";
  $("stAt").textContent = "· cập nhật " + fmt.format(Date.now());
  const stale = [];
  $("stRows").replaceChildren(...(st.rows.length ? st.rows.map((r) => {
    const bad = r.enabled && r.stale;
    if (bad) stale.push(r.symbol + (r.no_event_sec ? " (" + r.no_event_sec + " giây)" : " (chưa có event)"));
    const conn = !r.enabled ? node("span", {class: "pill", textContent: "tắt"})
      : r.collector_live ? node("span", {class: "pill ok", textContent: r.book_synced ? "đang chạy" : "đang đồng bộ sổ"})
      : node("span", {class: "pill err", textContent: "mất kết nối"});
    return node("tr", {class: r.enabled ? "" : "off"},
      node("td", {class: "sym", textContent: r.symbol + (r.market ? " · " + r.market : "")}),
      node("td", {}, conn),
      node("td", {class: bad ? "st-err" : ""}, when(r.last_event_ts), node("div", {class: "small", textContent: ago(r.last_event_ts)})),
      node("td", {}, when(r.last_snapshot_ts)),
      node("td", {class: "num", textContent: String(r.book_levels || 0)}),
      node("td", {class: "num", textContent: String(r.resyncs_today || 0)}),
      node("td", {class: "small", textContent: when(r.trades_from)}),
      node("td", {class: "small", textContent: when(r.book_from)}),
      node("td", {class: "small", textContent: r.last_error || ""}));
  }) : [node("tr", {}, node("td", {colSpan: 9, class: "small", textContent: "Chưa khai cặp nào."}))]));
  $("staleBox").textContent = stale.length ? "Không có event quá " + st.stale_sec + " giây: " + stale.join(", ")
    + ". Kiểm tra service bamcp-orderflow (systemctl status bamcp-orderflow / docker compose logs bamcp-orderflow)." : "";
  $("jobRows").replaceChildren(...(st.jobs.length ? st.jobs.map((j) => node("tr", {},
    node("td", {class: "small", textContent: when(j.created_at)}),
    node("td", {class: "sym", textContent: j.symbol}),
    node("td", {class: "small", textContent: j.from_date + " → " + j.to_date}),
    node("td", {}, node("span", {class: "pill " + ({done: "ok", failed: "err", running: "warn"}[j.status] || ""), textContent: j.status})),
    node("td", {}, node("div", {class: "bar"}, node("i", {style: "width:" + Math.round((j.progress || 0) * 100) + "%"})),
      node("div", {class: "small", textContent: Math.round((j.progress || 0) * 100) + "%"})),
    node("td", {class: "small", textContent: j.message || ""})))
    : [node("tr", {}, node("td", {colSpan: 6, class: "small", textContent: "Chưa chạy backfill nào."}))]));
}

async function refreshStatus() {
  const data = await act({action: "status"}, null);
  if (data) renderStatus(data);
}

$("bfRun").addEventListener("click", async (e) => {
  const body = {action: "backfill", symbol: $("bfSym").value, from_date: $("bfFrom").value, to_date: $("bfTo").value};
  if (!body.symbol || !body.from_date || !body.to_date) { say("Chọn cặp và khoảng ngày", false); return; }
  const data = await act(body, e.target);
  if (!data) return;
  say((data.note ? data.note + ": " : "Đã khởi chạy backfill ") + data.symbol + " " + data.from_date + " → " + data.to_date, true);
  refreshStatus();
});

renderConfig(VIEW.config);
renderStatus(VIEW.status);
$("bfTo").value = VIEW.today_utc; $("bfTo").max = VIEW.today_utc; $("bfFrom").max = VIEW.today_utc;
setInterval(() => { if (!document.hidden) refreshStatus(); }, 10000);
</script>
"""

SETUP_BANNER = """<div class="warn"><strong>Chưa đặt mật khẩu.</strong> Mọi thao tác ghi cần <em>setup token</em>
in trong log container (ô ở cuối trang).</div>"""

SETUP_FIELD = """<div class="card"><label for="setup_token">Setup token</label>
  <input type="text" id="setup_token" autocomplete="off" placeholder="dán chuỗi từ dòng BAMCP SETUP TOKEN"></div>"""


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str).replace("<", "\\u003c")


def render(view: dict[str, Any], *, action_path: str, admin_path: str, setup_required: bool,
           nav: list[admin_ui.NavItem] | None = None) -> str:
    error = view.get("config_error")
    replacements = {
        "__SETUP_BANNER__": SETUP_BANNER if setup_required else "",
        "__SETUP_FIELD__": SETUP_FIELD if setup_required else "",
        "__CONFIG_ERROR__": (f'<div class="warn"><strong>File cấu hình đang sai, đang dùng bản hợp lệ gần nhất:</strong> '
                             f'{html.escape(str(error))}</div>' if error else ""),
        "__DB_PATH__": html.escape(str(view.get("db_path") or "")),
        "__CONFIG_FILE__": html.escape(str(view.get("config_file") or "")),
        "__SOURCE__": "trang này" if view.get("source") != "config.yaml" else "config.yaml",
        "__STALE__": html.escape(str((view.get("status") or {}).get("stale_sec", 60))),
        "__MAX_DAYS__": html.escape(str(view.get("max_days", 90))),
        "__ACTION_PATH__": _js(action_path),
        "__VIEW_JSON__": _js({k: view.get(k) for k in ("config", "status", "fields", "today_utc", "tz")}),
    }
    page = PAGE
    for needle, value in replacements.items():
        page = page.replace(needle, value)
    return admin_ui.page(title="Order Flow", active="orderflow", body=page, css=CSS,
                         nav=nav or admin_ui.fallback_nav(admin_path))
