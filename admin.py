"""Trang admin: dat username/password cho API va credential san giao dich.

Trang nay khong bao gio nhan duoc gia tri secret tu server - chi nhan co/khong
va 4 ky tu cuoi cua API key. O nhap de trong = giu nguyen gia tri dang luu.
"""

from __future__ import annotations

import html
import json
from typing import Any

PAGE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BAMCP — Cài đặt</title>
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
  .wrap { max-width: 680px; margin: 0 auto; padding: 32px 20px 64px; }
  h1 { font-size: 21px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin: 0 0 24px; }
  .card { background: var(--card); border: 1px solid var(--line);
    border-radius: 10px; padding: 20px; margin-bottom: 18px; }
  .card h2 { font-size: 15px; margin: 0 0 16px; letter-spacing: .01em; }
  label { display:block; font-size:13px; color:var(--muted); margin:14px 0 5px; }
  label:first-of-type { margin-top: 0; }
  input[type=text], input[type=password], select {
    width:100%; padding:9px 11px; border:1px solid var(--line); border-radius:7px;
    background:var(--bg); color:var(--ink); font:inherit; font-size:14px; }
  input:focus, select:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  .row { display:flex; gap:14px; } .row > * { flex:1; }
  .hint { font-size:12px; color:var(--muted); margin-top:5px; }
  .check { display:flex; align-items:center; gap:9px; margin-bottom:4px; }
  .check input { width:16px; height:16px; accent-color:var(--accent); }
  .check label { margin:0; color:var(--ink); font-size:14px; }
  .actions { display:flex; gap:10px; align-items:center; margin-top:22px; }
  button { font:inherit; font-size:14px; padding:9px 18px; border-radius:7px;
    border:1px solid var(--line); background:var(--card); color:var(--ink); cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button:disabled { opacity:.55; cursor:default; }
  #msg { margin-top:16px; font-size:13px; white-space:pre-wrap; }
  #msg.ok { color:var(--ok); } #msg.err { color:var(--err); }
  .warn { background:var(--warn-bg); border:1px solid var(--warn-line);
    border-radius:8px; padding:13px 15px; font-size:13px; margin-bottom:18px; }
  code { font-family: ui-monospace,"Cascadia Code",Consolas,monospace; font-size:12.5px; }
  .meta { font-size:12px; color:var(--muted); margin-top:20px; }
  .hist { margin-top:16px; border-top:1px solid var(--line); padding-top:12px; }
  .hist h3 { font-size:12px; color:var(--muted); margin:0 0 8px; font-weight:600; }
  .hist ul { list-style:none; margin:0; padding:0; }
  .hist li { font-size:12px; color:var(--muted); padding:3px 0; }
  .hist b { color:var(--ink); font-weight:600; }
  table.syms { width:100%; border-collapse:collapse; }
  table.syms td { padding:7px 0; border-bottom:1px solid var(--line); font-size:14px; }
  table.syms tr:last-child td { border-bottom:none; }
  table.syms td.sym { font-weight:600; font-family:ui-monospace,Consolas,monospace; }
  table.syms td.st { color:var(--muted); font-size:12px; }
  table.syms td.act { text-align:right; white-space:nowrap; }
  table.syms button { padding:4px 10px; font-size:12px; margin-left:6px; }
  .off td.sym, .off td.st { opacity:.45; }
</style>
</head>
<body><div class="wrap">

<h1>BAMCP — Cài đặt</h1>
<p class="sub">Lưu vào <code>__SETTINGS_PATH__</code>, tồn tại qua mọi lần tạo lại container.</p>
__ORB_LINK__
__GANN_LINK__

__SETUP_BANNER__

<form id="f">
  <div class="card">
    <h2>Truy cập API</h2>
    <label for="username">Tên đăng nhập</label>
    <input type="text" id="username" name="username" value="__USERNAME__" autocomplete="username">
    <div class="row">
      <div>
        <label for="password">Mật khẩu mới</label>
        <input type="password" id="password" name="password" autocomplete="new-password"
               placeholder="__PW_PLACEHOLDER__">
      </div>
      <div>
        <label for="password2">Nhập lại</label>
        <input type="password" id="password2" name="password2" autocomplete="new-password">
      </div>
    </div>
    <div class="hint">Tối thiểu 8 ký tự. Để trống nếu không muốn đổi. Dùng cho Basic auth của <code>/mcp</code> và chính trang này.</div>
  </div>

  <div class="card">
    <h2>Tài khoản sàn</h2>
    <div class="check">
      <input type="checkbox" id="exchange_enabled" name="exchange_enabled" __EX_CHECKED__>
      <label for="exchange_enabled">Đọc dữ liệu tài khoản thật</label>
    </div>
    <div class="hint">Tắt thì mọi số liệu lệnh và PnL chỉ đến từ nhật ký bạn tự ghi.</div>

    <div class="row">
      <div>
        <label for="exchange_name">Sàn</label>
        <select id="exchange_name" name="exchange_name">__EX_OPTIONS__</select>
      </div>
      <div>
        <label for="exchange_symbol">Cặp giao dịch</label>
        <input type="text" id="exchange_symbol" name="exchange_symbol"
               value="__EX_SYMBOL__" placeholder="để trống = mặc định của sàn">
      </div>
    </div>

    <label for="api_key">API Key __KEY_HINT__</label>
    <input type="password" id="api_key" name="api_key" autocomplete="off"
           placeholder="__KEY_PLACEHOLDER__">

    <label for="api_secret">API Secret</label>
    <input type="password" id="api_secret" name="api_secret" autocomplete="off"
           placeholder="__SECRET_PLACEHOLDER__">

    <label for="api_passphrase">Passphrase <span class="hint" style="display:inline">— chỉ OKX</span></label>
    <input type="password" id="api_passphrase" name="api_passphrase" autocomplete="off"
           placeholder="__PASS_PLACEHOLDER__">

    <div class="hint">Key phải là <strong>read-only</strong>: tắt quyền trade, tắt quyền rút tiền, bật IP whitelist. Ba ô trên để trống nghĩa là giữ nguyên giá trị đang lưu.</div>
  </div>

  <div class="card">
    <h2>Cặp giao dịch</h2>
    <div class="hint" style="margin-bottom:14px">Mỗi cặp được pull tự động ở cả 5 khung (W, D, H4, H1, M15) theo cùng chu kỳ, và có bias riêng. Quy định giao dịch thì dùng chung cho tất cả các cặp.</div>

    <table class="syms"><tbody>__SYMBOL_ROWS__</tbody></table>

    <div class="row" style="margin-top:14px">
      <div style="flex:2">
        <label for="new_symbol">Thêm cặp mới</label>
        <input type="text" id="new_symbol" placeholder="ETHUSDT, ADAUSDT, SOLUSDT..."
               autocomplete="off" style="text-transform:uppercase">
      </div>
      <div style="flex:1;display:flex;align-items:flex-end">
        <button type="button" id="add_symbol" style="width:100%">Thêm</button>
      </div>
    </div>
    <div class="hint">Server hỏi Binance xem cặp có thật không trước khi thêm, rồi kéo dữ liệu ban đầu ngay — không phải chờ hết chu kỳ 15 phút.</div>
  </div>

  <div class="card">
    <h2>Quy định chung — áp cho mọi cặp</h2>
    <div class="hint" style="margin-bottom:14px">Tài khoản chỉ có một, nên ngân sách rủi ro cũng chỉ có một. Hết quota là hết, dù lệnh nằm ở cặp nào.</div>
    <div class="row">
      <div>
        <label for="max_trades_per_day">Số lệnh tối đa / ngày</label>
        <input type="text" inputmode="decimal" id="max_trades_per_day" value="__R_TRADES__">
      </div>
      <div>
        <label for="max_margin_per_trade">Margin tối đa / lệnh (USD)</label>
        <input type="text" inputmode="decimal" id="max_margin_per_trade" value="__R_MARGIN__">
      </div>
    </div>
    <div class="row">
      <div>
        <label for="daily_stop_loss">Dừng ngày khi PnL đạt (USD)</label>
        <input type="text" inputmode="decimal" id="daily_stop_loss" value="__R_STOP__">
        <div class="hint">Phải là số âm hoặc 0. Tính gộp PnL của tất cả các cặp.</div>
      </div>
      <div>
        <label for="swing_max_margin_per_trade">Margin tối đa swing (USD)</label>
        <input type="text" inputmode="decimal" id="swing_max_margin_per_trade" value="__R_SWMARGIN__">
        <div class="hint"><strong>0 = không giới hạn.</strong></div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Quy định theo từng cặp</h2>
    <div class="hint" style="margin-bottom:14px">Mấy rule này đo bằng <strong>điểm giá</strong>, mà điểm của mỗi cặp là một thứ khác nhau — 300 điểm trên BTC với 300 điểm trên ADA không nói lên cùng một điều. Vì vậy mỗi cặp giữ ngưỡng riêng.</div>

    <label for="rule_symbol">Đang sửa cặp</label>
    <select id="rule_symbol">__RULE_SYMBOL_OPTIONS__</select>
    <div class="hint" id="sr_note"></div>

    <div class="row" style="margin-top:14px">
      <div>
        <label for="sr_max_stop_points">SL tối đa (điểm)</label>
        <input type="text" inputmode="decimal" id="sr_max_stop_points">
      </div>
      <div>
        <label for="sr_min_take_profit_points">TP tối thiểu (điểm)</label>
        <input type="text" inputmode="decimal" id="sr_min_take_profit_points">
      </div>
    </div>
    <div class="row">
      <div>
        <label for="sr_swing_min_take_profit_points">Ngưỡng TP để tính là swing</label>
        <input type="text" inputmode="decimal" id="sr_swing_min_take_profit_points">
        <div class="hint">TP dưới mức này thì lệnh bị xếp về scalp.</div>
      </div>
      <div>
        <label for="sr_swing_max_stop_points">SL tối đa swing (điểm)</label>
        <input type="text" inputmode="decimal" id="sr_swing_max_stop_points">
      </div>
    </div>
    <div class="hint">Dán nhãn "swing" không lách được hạn mức: lệnh chỉ được hưởng bộ hạn mức swing khi TP thực sự đạt ngưỡng của <em>chính cặp đó</em>, nếu không nó tự động bị hạ về scalp và việc đó được ghi lại.</div>
    <div class="hint">Đổi được nhiều cặp trong một lần — chuyển cặp ở ô trên, sửa tiếp, rồi bấm Lưu một lần.</div>
  </div>

  <div class="card">
    <h2>Lý do &amp; lịch sử</h2>
    <label for="rules_reason">Lý do đổi</label>
    <input type="text" id="rules_reason" placeholder="bắt buộc khi có thay đổi — vd: siết lại sau tuần lỗ">
    <div class="hint">Mọi thay đổi đều vào sổ lịch sử kèm lý do và mốc thời gian, dù đổi ở đây hay qua Claude, dù là rule chung hay rule của một cặp. Đổi trong ngày đang giao dịch sẽ hiện lại trong <code>get_today_status</code>.</div>

    __RULES_HISTORY__
  </div>

  __SETUP_FIELD__

  <div class="actions">
    <button type="submit" class="primary" id="save">Lưu</button>
    <button type="button" id="test">Kiểm tra kết nối sàn</button>
  </div>
  <div id="msg"></div>
</form>

<p class="meta">Cập nhật lần cuối: __UPDATED__</p>

<script>
const GLOBAL_RULE_KEYS = __GLOBAL_RULE_KEYS__;
const SYMBOL_RULE_KEYS = __SYMBOL_RULE_KEYS__;
// Giá trị ĐÃ GIẢI của từng cặp: cặp chưa đặt riêng thì đây là giá trị mặc định
// nó đang kế thừa. So sánh với bảng này để biết ô nào người dùng thực sự đổi.
const SYMBOL_RULES = __SYMBOL_RULES_JSON__;
const SYMBOL_OVERRIDES = __SYMBOL_OVERRIDES_JSON__;
const $ = (id) => document.getElementById(id);
const msg = $("msg");

// Sửa cặp A, chuyển sang cặp B, sửa tiếp, rồi Lưu một lần -> phải giữ được cả hai.
const symbolEdits = {};
let activeSymbol = "";

function say(text, ok) {
  msg.textContent = text;
  msg.className = ok ? "ok" : "err";
}

function fillSymbolRules(sym) {
  activeSymbol = sym;
  const base = SYMBOL_RULES[sym] || {};
  const edits = symbolEdits[sym] || {};
  for (const key of SYMBOL_RULE_KEYS) {
    const el = $("sr_" + key);
    if (el) el.value = (key in edits) ? edits[key] : (base[key] ?? "");
  }
  const own = SYMBOL_OVERRIDES[sym] || [];
  $("sr_note").textContent = own.length
    ? sym + " đang đặt riêng: " + own.join(", ")
    : sym + " đang kế thừa toàn bộ giá trị mặc định.";
}

function captureSymbolRules() {
  if (!activeSymbol) return;
  const base = SYMBOL_RULES[activeSymbol] || {};
  const edits = {};
  for (const key of SYMBOL_RULE_KEYS) {
    const el = $("sr_" + key);
    if (!el) continue;
    const value = el.value.trim();
    if (value !== String(base[key] ?? "").trim()) edits[key] = value;
  }
  if (Object.keys(edits).length) symbolEdits[activeSymbol] = edits;
  else delete symbolEdits[activeSymbol];
}

function body() {
  captureSymbolRules();
  const data = {
    username: $("username").value,
    password: $("password").value,
    password2: $("password2").value,
    exchange_enabled: $("exchange_enabled").checked,
    exchange_name: $("exchange_name").value,
    exchange_symbol: $("exchange_symbol").value,
    api_key: $("api_key").value,
    api_secret: $("api_secret").value,
    api_passphrase: $("api_passphrase").value,
    rules: {},
    symbol_rules: symbolEdits,
    rules_reason: $("rules_reason").value,
  };
  // Chi gui rule nao thuc su doi so voi luc tai trang, de khoi ghi lich su rong
  for (const key of GLOBAL_RULE_KEYS) {
    const el = $(key);
    if (el && el.value.trim() !== el.defaultValue.trim()) {
      data.rules[key] = el.value.trim();
    }
  }
  const token = $("setup_token");
  if (token) data.setup_token = token.value;
  return data;
}

async function send(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  let out;
  try { out = await res.json(); } catch { out = {error: "server tra ve du lieu la"}; }
  return {ok: res.ok, out};
}

async function symbolAction(action, symbol) {
  const payload = {action: action, symbol: symbol};
  const token = $("setup_token");
  if (token) payload.setup_token = token.value;
  say(action === "add" ? "Đang kiểm tra cặp với Binance..." : "Đang xử lý...", true);
  const {ok, out} = await send("__SYMBOL_PATH__", payload);
  if (!ok) { say(out.error || "Không thực hiện được.", false); return; }
  say(out.message || "Xong.", true);
  setTimeout(() => location.reload(), 1000);
}

$("add_symbol").addEventListener("click", () => {
  const value = $("new_symbol").value.trim().toUpperCase();
  if (!value) { say("Nhập tên cặp trước.", false); return; }
  symbolAction("add", value);
});

$("new_symbol").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("add_symbol").click(); }
});

const ruleSymbol = $("rule_symbol");
if (ruleSymbol) {
  ruleSymbol.addEventListener("change", () => {
    captureSymbolRules();          // giu lai phan vua sua truoc khi doi cap
    fillSymbolRules(ruleSymbol.value);
  });
  if (ruleSymbol.value) fillSymbolRules(ruleSymbol.value);
}

document.querySelectorAll("button[data-sym]").forEach((b) => {
  b.addEventListener("click", () => symbolAction(b.dataset.act, b.dataset.sym));
});

$("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pw = $("password").value, pw2 = $("password2").value;
  if (pw && pw !== pw2) { say("Hai ô mật khẩu không khớp.", false); return; }
  $("save").disabled = true;
  say("Đang lưu...", true);
  const {ok, out} = await send("__SAVE_PATH__", body());
  $("save").disabled = false;
  if (!ok) { say(out.error || "Lưu thất bại.", false); return; }
  say(out.message || "Đã lưu.", true);
  if (out.reload) setTimeout(() => location.reload(), 1200);
});

$("test").addEventListener("click", async () => {
  $("test").disabled = true;
  say("Đang gọi sàn...", true);
  const {ok, out} = await send("__TEST_PATH__", body());
  $("test").disabled = false;
  say(ok ? (out.message || "Kết nối được.") : (out.error || "Không kết nối được."), ok);
});
</script>
</div></body></html>
"""

SETUP_BANNER = """<div class="warn">
<strong>Chưa đặt mật khẩu.</strong> Trang này đang mở công khai cho tới khi bạn lưu lần đầu.
Dán <em>setup token</em> in trong log container vào ô cuối trang để lưu được.
</div>"""


SETUP_FIELD = """<div class="card">
  <h2>Setup token</h2>
  <label for="setup_token">Token in trong log khi container khởi động</label>
  <input type="text" id="setup_token" name="setup_token" autocomplete="off"
         placeholder="dán chuỗi từ dòng BAMCP SETUP TOKEN">
  <div class="hint">Chỉ cần ở lần lưu đầu tiên. Sau khi có mật khẩu, trang này yêu cầu đăng nhập.</div>
</div>"""


def _history_block(history: list[dict[str, Any]]) -> str:
    """Vai lan doi rule gan nhat. Hien ngay tren trang de viec doi rule khong am tham.

    Dong nao co ten cap thi gan nhan cap do - nhin lai phan biet duoc "siet ca
    tai khoan" voi "chi siet mot cap".
    """
    if not history:
        return ""
    items = []
    for entry in reversed(history[-5:]):
        parts = ", ".join(
            f"{html.escape(k)} {info.get('from')} → {info.get('to')}"
            for k, info in (entry.get("changes") or {}).items()
        )
        when = html.escape(str(entry.get("at", ""))[:16].replace("T", " "))
        why = html.escape(str(entry.get("reason", "")))
        scope = html.escape(str(entry.get("symbol") or "")) or "chung"
        items.append(f"<li><b>{when}</b> <b>[{scope}]</b> — {parts}<br>{why}</li>")
    return ('<div class="hist"><h3>Thay đổi gần đây</h3><ul>'
            + "".join(items) + "</ul></div>")


def _num(value: Any) -> str:
    """So nguyen hien khong co .0 - doc de chiu hon trong o nhap."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return str(int(number)) if number == int(number) else str(number)


def _symbol_rows(symbols: list[dict[str, Any]], ready: dict[str, bool]) -> str:
    """Bang cac cap dang theo doi. Chi con mot cap thi an nut Bo - server cung
    tu choi, nhung an di thi nguoi dung khong phai thu moi biet."""
    if not symbols:
        return '<tr><td class="st">Chưa có cặp nào.</td></tr>'
    rows = []
    for item in symbols:
        sym = html.escape(item["symbol"])
        on = item["enabled"]
        has_data = ready.get(item["symbol"], False)
        status = ("đang theo dõi" if on else "đã tắt")
        if on and not has_data:
            status = "đang chờ dữ liệu về"
        toggle = "disable" if on else "enable"
        toggle_label = "Tắt" if on else "Bật"
        remove = ("" if len(symbols) <= 1 else
                  f'<button type="button" data-sym="{sym}" data-act="remove">Bỏ</button>')
        rows.append(
            f'<tr class="{"" if on else "off"}">'
            f'<td class="sym">{sym}</td>'
            f'<td class="st">{status}</td>'
            f'<td class="act">'
            f'<button type="button" data-sym="{sym}" data-act="{toggle}">{toggle_label}</button>'
            f'{remove}</td></tr>'
        )
    return "".join(rows)


def _js(value: Any) -> str:
    """Nhung du lieu vao <script>. Chan "</script>" bang cach escape dau <."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def render(state: dict[str, Any], *, settings_path: str, save_path: str,
           test_path: str, symbol_path: str, exchange_names: tuple[str, ...],
           rules: dict[str, Any], rules_history: list[dict[str, Any]],
           symbols: list[dict[str, Any]], symbol_ready: dict[str, bool],
           symbol_rules: dict[str, dict[str, Any]],
           symbol_overrides: dict[str, list[str]],
           global_rule_keys: tuple[str, ...],
           symbol_rule_keys: tuple[str, ...],
           orb_path: str = "", gann_path: str = "") -> str:
    """Dung HTML tu trang thai da duoc che giau. Khong nhan secret that.

    `rules` la rule chung + mac dinh; `symbol_rules` la gia tri da giai cho tung
    cap. Hai bo di hai duong rieng vi chung duoc luu o hai cho khac nhau.
    """
    configured = bool(state.get("auth_configured"))
    current = state.get("exchange_name") or ""

    rule_symbols = [row["symbol"] for row in symbols if row["symbol"] in symbol_rules]
    rule_symbol_options = "".join(
        f'<option value="{html.escape(sym)}">{html.escape(sym)}</option>'
        for sym in rule_symbols
    ) or '<option value="">— chưa có cặp nào —</option>'
    # Chuoi de so sanh trong JS phai cung dinh dang voi cai hien trong o nhap,
    # nen dung chinh _num() chu khong de so tho.
    symbol_rules_js = {
        sym: {key: _num(symbol_rules[sym].get(key)) for key in symbol_rule_keys}
        for sym in rule_symbols
    }

    options = "".join(
        f'<option value="{html.escape(name)}"'
        f'{" selected" if name == current else ""}>{html.escape(name)}</option>'
        for name in exchange_names
    )
    if not current:
        options = '<option value="" selected>— chọn sàn —</option>' + options

    def placeholder(value: str) -> str:
        return "đã lưu, để trống nếu không đổi" if value else "chưa có"

    replacements = {
        "__SETTINGS_PATH__": html.escape(settings_path),
        "__SAVE_PATH__": html.escape(save_path),
        "__TEST_PATH__": html.escape(test_path),
        "__SETUP_BANNER__": "" if configured else SETUP_BANNER,
        "__SETUP_FIELD__": "" if configured else SETUP_FIELD,
        "__USERNAME__": html.escape(state.get("username") or ""),
        "__PW_PLACEHOLDER__": placeholder(state.get("password_set") or ""),
        "__EX_CHECKED__": "checked" if state.get("exchange_enabled") else "",
        "__EX_OPTIONS__": options,
        "__EX_SYMBOL__": html.escape(state.get("exchange_symbol") or ""),
        "__KEY_HINT__": (f'<span class="hint" style="display:inline">— '
                         f'{html.escape(state["key_hint"])}</span>'
                         if state.get("key_hint") else ""),
        "__KEY_PLACEHOLDER__": placeholder(state.get("key_hint") or ""),
        "__SECRET_PLACEHOLDER__": placeholder(state.get("secret_set") or ""),
        "__PASS_PLACEHOLDER__": placeholder(state.get("passphrase_set") or ""),
        "__UPDATED__": html.escape(state.get("updated_at") or "chưa bao giờ"),
        "__R_TRADES__": _num(rules.get("max_trades_per_day")),
        "__R_MARGIN__": _num(rules.get("max_margin_per_trade")),
        "__R_STOP__": _num(rules.get("daily_stop_loss")),
        "__R_SWMARGIN__": _num(rules.get("swing_max_margin_per_trade")),
        "__RULE_SYMBOL_OPTIONS__": rule_symbol_options,
        "__GLOBAL_RULE_KEYS__": _js(list(global_rule_keys)),
        "__SYMBOL_RULE_KEYS__": _js(list(symbol_rule_keys)),
        "__SYMBOL_RULES_JSON__": _js(symbol_rules_js),
        "__SYMBOL_OVERRIDES_JSON__": _js(
            {sym: symbol_overrides.get(sym, []) for sym in rule_symbols}),
        "__SYMBOL_PATH__": html.escape(symbol_path),
        "__SYMBOL_ROWS__": _symbol_rows(symbols, symbol_ready),
        "__RULES_HISTORY__": _history_block(rules_history),
        # TM - #ORB - ORB Enhancement: loi vao trang quan ly phien ORB
        "__ORB_LINK__": (f'<p class="sub">Phiên ORB: <a href="{html.escape(orb_path)}">'
                         f'quản lý phiên giao dịch ORB</a></p>' if orb_path else ""),
        # TM - #GANN-TW - Gann Time Windows: loi vao trang Gann
        "__GANN_LINK__": (f'<p class="sub">Gann: <a href="{html.escape(gann_path)}">'
                          f'pivot, cửa sổ thời gian, backtest</a></p>' if gann_path else ""),
    }

    page = PAGE
    for needle, value in replacements.items():
        page = page.replace(needle, value)
    return page
