# TM - #DASHBOARD - Setup dashboard
"""Trang admin "Dashboard accounts": tai khoan, cau hinh, audit log, lich su dang nhap."""
from __future__ import annotations

import html
import json
from typing import Any

import admin_ui

CSS = """
  td.act select { width:auto; min-height:28px; padding:2px 6px; font-size:12.5px; display:inline-block; }
  .once { margin-top:12px; padding:12px 14px; border:1px solid var(--warn-line); background:var(--warn-soft);
    border-radius:10px; font-size:13px; }
  .once code { font-size:14px; user-select:all; }
"""

PAGE = """<div class="page-head">
  <h1>Dashboard accounts</h1>
  <p class="sub">Tài khoản đăng nhập <a href="__DASH_PATH__">__DASH_PATH__</a>, tách riêng khỏi mật khẩu admin.
    Tắt tài khoản, đổi mật khẩu hoặc đổi vai trò sẽ đăng xuất ngay mọi phiên của tài khoản đó.</p>
</div>
<div id="msg" data-msg="toast"></div>
__SETUP_BANNER__

  <div class="card">
    <h2>Tài khoản</h2>
    <div class="scroll"><table>
      <thead><tr><th>Username</th><th>Vai trò</th><th>Trạng thái</th><th>Đăng nhập cuối</th><th>Ngày tạo</th><th></th></tr></thead>
      <tbody>__USER_ROWS__</tbody>
    </table></div>
    <div id="once_reset" class="once" hidden></div>
  </div>

  <div class="card">
    <h2>Tạo tài khoản</h2>
    <p class="hint">Username 3–32 ký tự, chỉ gồm a-z 0-9 _ . -. Mật khẩu từ 10 ký tự. Mặc định là viewer.</p>
    <div class="row">
      <div><label for="c_user">Username</label><input type="text" id="c_user" autocomplete="off"></div>
      <div><label for="c_pass">Mật khẩu</label><input type="password" id="c_pass" autocomplete="new-password"></div>
      <div><label for="c_role">Vai trò</label><select id="c_role"><option value="viewer">viewer</option><option value="owner">owner</option></select></div>
    </div>
    <div class="actions">
      <button type="button" class="primary" id="b_create">Tạo tài khoản</button>
      <button type="button" id="b_generate">Tạo với mật khẩu ngẫu nhiên</button>
    </div>
    <div id="once_create" class="once" hidden></div>
  </div>

  <div class="card">
    <h2>Cấu hình dashboard</h2>
    <p class="hint">Lưu ở <code>__CONFIG_FILE__</code>, có hiệu lực ngay. <code>enabled</code> tắt thì mọi route trả 404 và tool không trả link.</p>
    <div class="check"><label class="check"><input type="checkbox" id="k_enabled"__ENABLED__> enabled</label></div>
    <label for="k_base_url">base_url</label><input type="text" id="k_base_url" value="__BASE_URL__">
    <div class="row">
      <div><label for="k_session_hours">session_hours</label><input type="number" id="k_session_hours" min="1" value="__SESSION_HOURS__"></div>
      <div><label for="k_remember_days">remember_days</label><input type="number" id="k_remember_days" min="1" value="__REMEMBER_DAYS__"></div>
      <div><label for="k_login_max_attempts">login_max_attempts</label><input type="number" id="k_login_max_attempts" min="1" value="__MAX_ATTEMPTS__"></div>
      <div><label for="k_login_lockout_minutes">login_lockout_minutes</label><input type="number" id="k_login_lockout_minutes" min="1" value="__LOCKOUT__"></div>
    </div>
    <div class="row">
      <div><label for="k_poll_seconds">poll_seconds</label><input type="number" id="k_poll_seconds" min="5" value="__POLL__"></div>
      <div><label for="k_vol_spike_mult">vol_spike_mult</label><input type="number" id="k_vol_spike_mult" min="0.1" step="0.1" value="__VOL_MULT__"></div>
      <div><label for="k_default_tf">default_tf</label><select id="k_default_tf">__TF_OPTIONS__</select></div>
      <div><label for="k_default_bars">default_bars</label><input type="number" id="k_default_bars" min="10" max="500" value="__DEFAULT_BARS__"></div>
    </div>
    <div class="check" style="margin-top:14px"><label class="check"><input type="checkbox" id="k_hide_pnl"__HIDE_PNL__> hide_pnl (ẩn số PnL với cả owner)</label></div>
    <div class="actions"><button type="button" class="primary" id="b_config">Lưu cấu hình</button></div>
  </div>

  <!-- TM - #TR-BRANCH - Thang diem xac suat tich luy / phan phoi -->
  <div class="card">
    <h2>Thang điểm xác suất TR</h2>
    <p class="hint">Trọng số mặc định của từng yếu tố khi Claude gửi <code>prob_evidence</code> qua <code>update_tr</code>
      mà bỏ trống <code>weight</code>. Xác suất tích luỹ = Σ trọng số bên acc / Σ trọng số × 100. Đây là điểm bằng chứng,
      chưa hiệu chỉnh thống kê. Ô trống = trọng số mặc định. Chỉ áp dụng cho lần gửi sau, không tính lại TR đã lưu.</p>
    <div class="scroll"><table>
      <thead><tr><th>Yếu tố</th><th>factor</th><th>Mặc định</th><th>Trọng số</th></tr></thead>
      <tbody>__WEIGHT_ROWS__</tbody>
    </table></div>
    <div class="actions"><button type="button" class="primary" id="b_weights">Lưu thang điểm</button></div>
  </div>

  <div class="card">
    <h2>Audit log</h2>
    <div class="scroll tall"><table>
      <thead><tr><th>Lúc</th><th>Ai</th><th>Thao tác</th><th>Đối tượng</th><th>Chi tiết</th><th>IP</th></tr></thead>
      <tbody>__AUDIT_ROWS__</tbody>
    </table></div>
  </div>

  <div class="card">
    <h2>Lịch sử đăng nhập</h2>
    <div class="scroll tall"><table>
      <thead><tr><th>Lúc</th><th>Username</th><th>IP</th><th>Kết quả</th></tr></thead>
      <tbody>__LOGIN_ROWS__</tbody>
    </table></div>
  </div>
  __SETUP_FIELD__
<script>
const ACTION = __ACTION_PATH__;
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

function reloadSoon(text) { say(text, true); setTimeout(() => location.reload(), 700); }

function showOnce(id, name, password) {
  const box = $(id);
  box.textContent = "";
  box.append("Mật khẩu của " + name + " (chỉ hiện một lần, sao chép ngay): ");
  const code = document.createElement("code");
  code.textContent = password;
  box.append(code);
  box.hidden = false;
}

async function create(button, generate) {
  const data = await act({action: "create", username: $("c_user").value.trim(), password: $("c_pass").value,
                          role: $("c_role").value, generate: generate}, button);
  if (!data) return;
  if (data.generated_password) { showOnce("once_create", data.username, data.generated_password); say("Đã tạo " + data.username, true); }
  else reloadSoon("Đã tạo " + data.username);
}

$("b_create").addEventListener("click", (e) => create(e.target, false));
$("b_generate").addEventListener("click", (e) => create(e.target, true));

$("b_config").addEventListener("click", async (e) => {
  const config = {enabled: $("k_enabled").checked, hide_pnl: $("k_hide_pnl").checked,
                  base_url: $("k_base_url").value.trim(), default_tf: $("k_default_tf").value};
  ["session_hours", "remember_days", "login_max_attempts", "login_lockout_minutes", "poll_seconds",
   "vol_spike_mult", "default_bars"].forEach((key) => { config[key] = $("k_" + key).value; });
  if (await act({action: "config_save", config: config}, e.target)) reloadSoon("Đã lưu cấu hình");
});

// TM - #TR-BRANCH - Thang diem xac suat: chi gui prob_weights, cac cau hinh khac giu nguyen
$("b_weights").addEventListener("click", async (e) => {
  const weights = {};
  document.querySelectorAll("input[data-weight]").forEach((box) => { weights[box.dataset.weight] = box.value.trim(); });
  if (await act({action: "config_save", config: {prob_weights: weights}}, e.target)) reloadSoon("Đã lưu thang điểm");
});

document.addEventListener("change", async (e) => {
  const box = e.target.closest("select[data-role]");
  if (!box) return;
  const data = await act({action: "set_role", id: box.dataset.role, role: box.value}, box);
  if (data) reloadSoon("Đã đổi vai trò, các phiên của tài khoản đã bị đăng xuất");
  else box.value = box.dataset.current;
});

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = b.dataset.id, name = b.dataset.name;
  if (b.dataset.act === "delete") {
    if (confirm("Xoá tài khoản " + name + "?") && await act({action: "delete", id: id}, b)) reloadSoon("Đã xoá " + name);
  } else if (b.dataset.act === "toggle") {
    const active = b.dataset.active !== "1";
    if (await act({action: "set_active", id: id, active: active}, b)) reloadSoon(active ? "Đã bật " + name : "Đã tắt " + name);
  } else if (b.dataset.act === "reset") {
    const password = prompt("Mật khẩu mới cho " + name + " (từ 10 ký tự). Để trống = sinh ngẫu nhiên.");
    if (password === null) return;
    const data = await act({action: "reset_password", id: id, password: password, generate: !password}, b);
    if (!data) return;
    if (data.generated_password) { showOnce("once_reset", name, data.generated_password); say("Đã đặt lại mật khẩu " + name, true); }
    else reloadSoon("Đã đặt lại mật khẩu " + name);
  }
});
</script>
"""

SETUP_BANNER = """<div class="warn">
<strong>Chưa đặt mật khẩu admin.</strong> Mọi thao tác ghi cần <em>setup token</em> in trong log container
(ô ở cuối trang).
</div>"""

SETUP_FIELD = """<div class="card"><label for="setup_token">Setup token</label>
  <input type="text" id="setup_token" autocomplete="off" placeholder="dán chuỗi từ dòng BAMCP SETUP TOKEN"></div>"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _user_rows(users: list[dict[str, Any]]) -> str:
    if not users:
        return '<tr><td colspan="6" class="small">Chưa có tài khoản nào. Tạo một owner ở dưới.</td></tr>'
    out = []
    for user in users:
        active = bool(user["is_active"])
        role_box = "".join(f'<option value="{r}"{" selected" if r == user["role"] else ""}>{r}</option>'
                           for r in ("viewer", "owner"))
        attrs = f'data-id="{_e(user["id"])}" data-name="{_e(user["username"])}"'
        out.append(
            f'<tr{"" if active else " class=off"}><td class="id">{_e(user["username"])}</td>'
            f'<td><select data-role="{_e(user["id"])}" data-current="{_e(user["role"])}" '
            f'aria-label="Vai trò của {_e(user["username"])}" style="width:auto">{role_box}</select></td>'
            f'<td><span class="pill {"ok" if active else "no"}">{"active" if active else "disabled"}</span></td>'
            f'<td class="small">{_e(user.get("last_login")) or "—"}</td><td class="small">{_e(user.get("created"))}</td>'
            f'<td class="act"><button data-act="reset" {attrs}>Đặt lại mật khẩu</button>'
            f'<button data-act="toggle" data-active="{1 if active else 0}" {attrs}>{"Tắt" if active else "Bật"}</button>'
            f'<button class="danger" data-act="delete" {attrs}>Xoá</button></td></tr>')
    return "".join(out)


def _audit_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="6" class="small">Chưa có thay đổi nào.</td></tr>'
    return "".join(
        f'<tr><td class="small">{_e(r["when"])}</td><td>{_e(r["actor"])}</td><td>{_e(r["action"])}</td>'
        f'<td>{_e(r["target"])}</td><td class="small">{_e(json.dumps(r["details"], ensure_ascii=False)) if r["details"] else ""}</td>'
        f'<td class="small">{_e(r["ip"])}</td></tr>' for r in rows)


def _login_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="4" class="small">Chưa có lần đăng nhập nào.</td></tr>'
    return "".join(
        f'<tr><td class="small">{_e(r["when"])}</td><td>{_e(r["username"])}</td><td class="small">{_e(r["ip"])}</td>'
        f'<td><span class="pill {"ok" if r["ok"] else "no"}">{"thành công" if r["ok"] else _e(r["reason"]) or "thất bại"}</span></td></tr>'
        for r in rows)


# TM - #TR-BRANCH - Thang diem xac suat tich luy / phan phoi
def _weight_rows(factors: list[dict[str, Any]], weights: dict[str, Any]) -> str:
    out = []
    for factor in factors:
        key, default = factor["key"], float(factor["default"])
        current = float(weights.get(key, default))
        out.append(
            f'<tr><td>{_e(factor["label"])}</td><td class="id">{_e(key)}</td><td class="num">{default:g}</td>'
            f'<td><input type="number" min="0.1" max="100" step="0.5" data-weight="{_e(key)}" '
            f'aria-label="Trọng số {_e(key)}" value="{current:g}" style="width:90px"></td></tr>')
    return "".join(out)


def render(view: dict[str, Any], *, action_path: str, admin_path: str, setup_required: bool,
           nav: list[admin_ui.NavItem] | None = None) -> str:
    cfg = view["config"]
    replacements = {
        "__DASH_PATH__": _e(view["dashboard_path"]),
        "__SETUP_BANNER__": SETUP_BANNER if setup_required else "",
        "__SETUP_FIELD__": SETUP_FIELD if setup_required else "",
        "__USER_ROWS__": _user_rows(view["users"]),
        "__CONFIG_FILE__": _e(view["config_file"]),
        "__ENABLED__": " checked" if cfg.get("enabled") else "",
        "__HIDE_PNL__": " checked" if cfg.get("hide_pnl") else "",
        "__BASE_URL__": _e(cfg.get("base_url")),
        "__SESSION_HOURS__": _e(cfg.get("session_hours")),
        "__REMEMBER_DAYS__": _e(cfg.get("remember_days")),
        "__MAX_ATTEMPTS__": _e(cfg.get("login_max_attempts")),
        "__LOCKOUT__": _e(cfg.get("login_lockout_minutes")),
        "__POLL__": _e(cfg.get("poll_seconds")),
        "__VOL_MULT__": _e(cfg.get("vol_spike_mult")),
        "__DEFAULT_BARS__": _e(cfg.get("default_bars")),
        "__TF_OPTIONS__": "".join(f'<option{" selected" if t == cfg.get("default_tf") else ""}>{_e(t)}</option>'
                                  for t in view["timeframes"]),
        "__AUDIT_ROWS__": _audit_rows(view["audit"]),
        # TM - #TR-BRANCH - Thang diem xac suat
        "__WEIGHT_ROWS__": _weight_rows(view.get("prob_factors") or [], cfg.get("prob_weights") or {}),
        "__LOGIN_ROWS__": _login_rows(view["logins"]),
        "__ACTION_PATH__": json.dumps(action_path).replace("<", "\\u003c"),
    }
    page = PAGE
    for needle, value in replacements.items():
        page = page.replace(needle, value)
    return admin_ui.page(title="Dashboard accounts", active="dashboard", body=page, css=CSS,
                         nav=nav or admin_ui.fallback_nav(admin_path))
