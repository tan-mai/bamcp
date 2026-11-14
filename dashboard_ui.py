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


# TM - #DASH-LAYOUT - Bố cục dashboard & full page: chart la thanh phan chinh, sidebar tom tat ben phai,
# bang chi tiet xuong tab duoi chart. Phan dau trang va the boot dung chung cho trang chinh va full page.
def _head(title: str, assets: dict[str, str]) -> str:
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="{html.escape(assets['dashboard.css'])}">
</head>"""


def _tail(boot: dict[str, Any], assets: dict[str, str]) -> str:
    data = json.dumps(boot, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<script type="application/json" id="boot">{data}</script>
<script src="{html.escape(assets['lightweight-charts.js'])}"></script>
<script src="{html.escape(assets['orderflow.js'])}"></script>
<script src="{html.escape(assets['dashboard.js'])}"></script>
</body>
</html>"""


# Nut Reset zoom va menu lop: JS dung chung cho ca hai trang, sinh checkbox tu danh sach lop
_CHART_TOOLS = """<button type="button" class="btn sm" id="resetZoom" title="Về khung nhìn mặc định (phím 0)">⟲<span class="lbl"> Reset zoom</span></button>
  <div class="layer-wrap">
    <button type="button" class="btn sm" id="layerBtn" aria-expanded="false" aria-controls="layerMenu">Lớp ▾</button>
    <div class="layer-menu" id="layerMenu" role="group" aria-label="Lớp trên biểu đồ" hidden>
      <div id="layerList"></div>
      <!-- TM - #SCN-TOGGLE - Bat tat duong kich ban -->
      <div class="scn-chips" id="scnChips" role="group" aria-label="Từng kịch bản" hidden></div>
    </div>
  </div>"""

# TM - #BOOKMAP - Order flow Bookmap: chart nen o #chartMain; pane CVD phia duoi va cot 120px ben phai (volume
# profile + COB) chi hien khi lop tuong ung bat, chart nen gian ra khi tat
_CHART_BOX = ('<div class="chart" id="chart"><div class="chart-grid" id="chartGrid">'
              '<div class="chart-main" id="chartMain"></div>'
              '<canvas class="of-side" id="ofSide" aria-label="Volume profile và sổ lệnh" hidden></canvas>'
              '<div class="cvd-pane" id="cvdPane" hidden><span class="cvd-val" id="cvdVal"></span></div>'
              '</div><div class="popover" id="popover" role="dialog" aria-label="Chi tiết event" hidden></div>'
              '<div class="of-tip" id="ofTip" role="tooltip" hidden></div></div>')


def app_page(boot: dict[str, Any], assets: dict[str, str]) -> str:
    base = html.escape(str(boot["base"]))
    return f"""{_head("BAMCP Setup Dashboard", assets)}
<body class="dash">
<header class="bar" id="bar">
  <div class="bar-row">
    <strong class="logo">Setup Dashboard</strong>
    <select id="symbol" aria-label="Chọn cặp"></select>
    <div class="bar-more" id="barMore">
      <input type="date" id="date" aria-label="Chọn ngày (bias_date)">
      <select id="trSel" aria-label="Chọn TR" hidden></select>
      <div class="seg" role="group" aria-label="Chế độ">
        <button type="button" data-mode="live">Live</button>
        <button type="button" data-mode="review">Review</button>
      </div>
      <button type="button" class="btn" id="refresh" title="Làm mới dữ liệu">↻ Làm mới</button>
      <span class="updated" id="updated" aria-live="polite"></span>
      <form class="logout" method="post" action="{base}/logout">
        <input type="hidden" name="csrf" value="{html.escape(str(boot['csrf']))}">
        <button type="submit" class="btn" title="Đăng xuất {html.escape(str(boot['username']))}">Đăng xuất</button>
      </form>
    </div>
    <a class="btn full-link" id="fullLink" href="{base}/full" target="_blank" rel="noopener"
       title="Mở dashboard full page ở tab mới">⤢<span class="lbl"> Mở full page</span></a>
    <button type="button" class="btn icon" id="sideToggle" aria-pressed="false" aria-controls="side"
            title="Thu / mở sidebar">◨</button>
    <button type="button" class="btn icon" id="menuBtn" aria-expanded="false" aria-controls="barMore"
            title="Menu">☰</button>
  </div>
  <div class="banner" id="banner" role="alert" hidden></div>
</header>
<main class="dash-main">
  <div class="layout" id="planView">
    <section class="panel chart-panel" id="chartPanel">
      <div class="chart-tools">
        <div class="seg" id="tfs" role="group" aria-label="Khung thời gian"></div>
        <select id="revision" aria-label="Revision kế hoạch" hidden></select>
        <span class="chart-note" id="chartNote"></span>
  {_CHART_TOOLS}
      </div>
      {_CHART_BOX}
    </section>
    <aside class="side" id="side" aria-label="Tóm tắt">
      <section class="summary" id="summary" aria-label="Bias, giá, vị thế"></section>
      <section class="panel" id="trPanel">
        <h2>Tóm tắt TR</h2>
        <div id="trCard"></div>
      </section>
      <section class="panel">
        <h2>Setup đang chạy</h2>
        <div id="sideSetups"></div>
      </section>
    </aside>
  </div>
  <nav class="tabbar" id="tabbar" role="tablist" aria-label="Chi tiết">
    <button type="button" role="tab" data-tab="setups" aria-controls="pane-setups">Setups chi tiết</button>
    <button type="button" role="tab" data-tab="events" aria-controls="pane-events">Sự kiện Wyckoff</button>
    <button type="button" role="tab" data-tab="plan" aria-controls="pane-plan">Kịch bản/Bias</button>
    <!-- TM - #OFF-PLAN - Lenh ngoai ke hoach (chi chu tai khoan) -->
    <button type="button" role="tab" data-tab="offplan" aria-controls="pane-offplan" hidden>Lệnh ngoài kế hoạch</button>
    <button type="button" role="tab" data-tab="stats" aria-controls="pane-stats" hidden>Thống kê</button>
  </nav>
  <section class="tabpane" id="pane-setups" role="tabpanel" hidden>
    <div class="pane-tools">
      <!-- TM - #TR-BRANCH - Setup PRUNED an mac dinh -->
      <button type="button" class="toggle" id="showPruned" aria-pressed="false" hidden></button>
    </div>
    <div class="pane-grid">
      <div id="setups"></div>
      <section class="panel">
        <h2>Timeline</h2>
        <div id="timeline"></div>
      </section>
    </div>
  </section>
  <section class="tabpane" id="pane-events" role="tabpanel" hidden>
    <div class="pane-grid">
      <section class="panel">
        <div class="panel-head"><h2>Sự kiện Wyckoff</h2><span class="pane-tools" id="eventTools"></span></div>
        <div id="eventList"></div>
      </section>
      <section class="panel">
        <h2>Bằng chứng xác suất</h2>
        <div id="evidence"></div>
      </section>
    </div>
  </section>
  <section class="tabpane" id="pane-plan" role="tabpanel" hidden>
    <div class="pane-grid">
      <section class="panel">
        <h2>Bias</h2>
        <div id="biasBox"></div>
      </section>
      <section class="panel">
        <h2>Kịch bản</h2>
        <div id="scenarioBox"></div>
      </section>
    </div>
  </section>
  <section class="tabpane" id="pane-offplan" role="tabpanel" hidden>
    <section class="panel">
      <h2>Lệnh ngoài kế hoạch</h2>
      <div id="offPlan"></div>
    </section>
    <section class="panel" id="fillsPanel" hidden>
      <h2>Lệnh OKX trong ngày</h2>
      <div id="fills"></div>
    </section>
  </section>
  <section class="tabpane" id="pane-stats" role="tabpanel" hidden>
    <section class="panel">
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
  </section>
</main>
{_tail(boot, assets)}"""


# TM - #DASH-LAYOUT - Bố cục dashboard & full page: chart 100vw x (100vh - thanh 40px), panel noi an mac dinh.
# Khong co thao tac ghi nao: khong o review, khong dang xuat.
def full_page(boot: dict[str, Any], assets: dict[str, str]) -> str:
    return f"""{_head("BAMCP Chart", assets)}
<body class="full">
<header class="fbar" id="bar">
  <select id="symbol" aria-label="Chọn cặp"></select>
  <select id="trSel" aria-label="Chọn TR" hidden></select>
  <select id="tfSel" aria-label="Khung thời gian"></select>
  <span class="fdate" id="fDate" hidden></span>
  <span class="pill run" id="fPhase" hidden></span>
  <span class="fprob" id="fProb" hidden></span>
  <span class="updated" id="updated" aria-live="polite"></span>
  <button type="button" class="btn sm" id="refresh" title="Làm mới (R)">↻<span class="lbl"> Làm mới</span></button>
  <button type="button" class="btn sm" id="panelBtn" aria-expanded="false" aria-controls="fPanel"
          title="Setup và sự kiện (S)">☰<span class="lbl"> Setup</span></button>
  {_CHART_TOOLS}
  <button type="button" class="btn sm" id="fsBtn" title="Toàn màn hình (F)">⛶<span class="lbl"> Toàn màn hình</span></button>
</header>
<main class="fmain">
  <div class="banner fbanner" id="banner" role="alert" hidden></div>
  {_CHART_BOX}
  <span class="chart-note fnote" id="chartNote"></span>
  <aside class="fpanel" id="fPanel" aria-label="Setup và sự kiện Wyckoff" hidden>
    <div class="panel-head">
      <h2>Setup &amp; sự kiện</h2>
      <button type="button" class="pop-close" id="panelClose" aria-label="Đóng (Esc)">×</button>
    </div>
    <h3>Setup</h3>
    <div id="sideSetups"></div>
    <h3>Sự kiện Wyckoff</h3>
    <div id="eventList"></div>
  </aside>
</main>
{_tail(boot, assets)}"""
