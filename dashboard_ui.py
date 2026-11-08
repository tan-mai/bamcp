# TM - #DASHBOARD - Setup dashboard
"""HTML cua dashboard: trang login va khung trang. Moi du lieu do static/dashboard/dashboard.js ve.

Khong dung layout, menu hay session cua admin. CSP chi cho script tu host nen khong co script
inline; du lieu khoi dong nam trong the JSON, trinh duyet khong chay the nay.
"""
from __future__ import annotations

import html
import json
from typing import Any

LOGIN_CSS = """
  :root { color-scheme:light dark; --bg:#f3f4f6; --surface:#fff; --ink:#111827; --muted:#6b7280;
    --line:#d1d5db; --accent:#2563eb; --err:#b91c1c; --err-soft:rgba(185,28,28,.08); }
  @media (prefers-color-scheme: dark) { :root { --bg:#0b0d12; --surface:#141820; --ink:#e5e7eb;
    --muted:#9ca3af; --line:#323a4a; --accent:#4f8ff7; --err:#f87171; --err-soft:rgba(248,113,113,.12); } }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; padding:16px; background:var(--bg);
    color:var(--ink); font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  form { width:100%; max-width:360px; padding:24px 20px; background:var(--surface);
    border:1px solid var(--line); border-radius:14px; }
  h1 { margin:0 0 4px; font-size:20px; font-weight:650; }
  p { margin:0 0 18px; color:var(--muted); font-size:14px; }
  label { display:block; margin:14px 0 6px; font-size:14px; font-weight:500; }
  input[type=text], input[type=password] { width:100%; min-height:46px; padding:10px 12px; font:inherit;
    color:var(--ink); background:transparent; border:1px solid var(--line); border-radius:9px; }
  input:focus { outline:2px solid var(--accent); outline-offset:1px; }
  .remember { display:flex; align-items:center; gap:10px; margin-top:16px; font-weight:400; }
  .remember input { width:20px; height:20px; accent-color:var(--accent); }
  button { width:100%; min-height:48px; margin-top:20px; font:inherit; font-weight:600; color:#fff;
    background:var(--accent); border:0; border-radius:9px; cursor:pointer; }
  .error { margin:0 0 6px; padding:10px 12px; color:var(--err); background:var(--err-soft);
    border-radius:9px; font-size:14px; }
"""


# TM - #ADMIN-LOGIN - Form dang nhap admin: trang admin dung lai form nay voi heading/note rieng
def login_page(*, action: str, next_url: str, error: str = "", username: str = "",
               remember_days: int = 30, heading: str = "Setup Dashboard",
               note: str = "Đăng nhập bằng tài khoản dashboard.") -> str:
    message = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BAMCP {html.escape(heading)} - Đăng nhập</title>
<link rel="icon" href="data:,">
<style>{LOGIN_CSS}</style>
</head>
<body>
<form method="post" action="{html.escape(action)}">
  <h1>{html.escape(heading)}</h1>
  <p>{html.escape(note)}</p>
  {message}
  <input type="hidden" name="next" value="{html.escape(next_url)}">
  <label for="username">Tên đăng nhập</label>
  <input type="text" id="username" name="username" value="{html.escape(username)}" required
         autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" autofocus>
  <label for="password">Mật khẩu</label>
  <input type="password" id="password" name="password" required autocomplete="current-password">
  <label class="remember"><input type="checkbox" name="remember" value="1"> Ghi nhớ {int(remember_days)} ngày</label>
  <button type="submit">Đăng nhập</button>
</form>
</body>
</html>"""


def app_page(boot: dict[str, Any], assets: dict[str, str]) -> str:
    data = json.dumps(boot, ensure_ascii=False).replace("<", "\\u003c")
    base = html.escape(str(boot["base"]))
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BAMCP Setup Dashboard</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="{html.escape(assets['dashboard.css'])}">
</head>
<body>
<header class="bar">
  <div class="bar-row">
    <strong class="logo">Setup Dashboard</strong>
    <select id="symbol" aria-label="Chọn cặp"></select>
    <input type="date" id="date" aria-label="Chọn ngày">
    <div class="seg" role="group" aria-label="Chế độ">
      <button type="button" data-mode="live">Live</button>
      <button type="button" data-mode="review">Review</button>
    </div>
    <span class="updated" id="updated" aria-live="polite"></span>
    <form class="logout" method="post" action="{base}/logout">
      <input type="hidden" name="csrf" value="{html.escape(str(boot['csrf']))}">
      <button type="submit" title="Đăng xuất {html.escape(str(boot['username']))}">Đăng xuất</button>
    </form>
  </div>
  <div class="banner" id="banner" role="alert" hidden></div>
</header>
<main>
  <section class="summary" id="summary" aria-label="Tóm tắt"></section>
  <div class="seg tabs" id="tabs" role="group" aria-label="Nội dung review" hidden>
    <button type="button" data-tab="plan">Kế hoạch</button>
    <button type="button" data-tab="stats">Thống kê</button>
  </div>
  <div class="layout" id="planView">
    <section class="panel chart-panel" id="chartPanel">
      <div class="chart-tools">
        <div class="seg" id="tfs" role="group" aria-label="Khung thời gian"></div>
        <select id="revision" aria-label="Revision kế hoạch" hidden></select>
        <label class="check"><input type="checkbox" id="levels"> Mức giá</label>
        <!-- TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR -->
        <span class="layers" role="group" aria-label="Lớp Wyckoff">
          <label class="check"><input type="checkbox" id="layerPhases"> Pha</label>
          <label class="check"><input type="checkbox" id="layerEvents"> Event</label>
          <label class="check"><input type="checkbox" id="layerLevels"> Creek/Ice</label>
          <label class="check"><input type="checkbox" id="showFailed"> Hiện event hỏng</label>
        </span>
      </div>
      <div class="chart" id="chart"><div class="popover" id="popover" role="dialog" aria-label="Chi tiết event" hidden></div></div>
      <p class="chart-note" id="chartNote"></p>
    </section>
    <aside class="side">
      <section class="panel" id="trPanel">
        <h2>TR khoá</h2>
        <div id="trCard"></div>
      </section>
      <section class="panel">
        <h2>Setup</h2>
        <div id="setups"></div>
      </section>
      <section class="panel" id="fillsPanel" hidden>
        <h2>Lệnh OKX trong ngày</h2>
        <div id="fills"></div>
      </section>
      <section class="panel">
        <h2>Timeline</h2>
        <ol class="timeline" id="timeline"></ol>
      </section>
    </aside>
  </div>
  <section class="panel" id="statsView" hidden>
    <h2>Thống kê review</h2>
    <div class="chart-tools">
      <div class="seg" id="ranges" role="group" aria-label="Khoảng thời gian">
        <button type="button" data-range="7">7 ngày</button>
        <button type="button" data-range="30">30 ngày</button>
        <button type="button" data-range="90">90 ngày</button>
      </div>
      <select id="groupBy" aria-label="Nhóm theo"></select>
    </div>
    <div id="stats"></div>
  </section>
</main>
<script type="application/json" id="boot">{data}</script>
<script src="{html.escape(assets['lightweight-charts.js'])}"></script>
<script src="{html.escape(assets['dashboard.js'])}"></script>
</body>
</html>"""
