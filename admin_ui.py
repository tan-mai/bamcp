"""Khung chung cho cac trang admin: CSS, thanh dieu huong, vung thong bao.

Moi trang chi viet phan than va script rieng; render() cua trang goi page() de
boc vao. Server truyen `nav` (danh sach trang dang bat); test goi render truc
tiep khong co nav thi thanh dieu huong chi con link ve trang cai dat chung.

Vung thong bao danh dau bang thuoc tinh data-msg chu khong bang class, vi
say() cua cac trang ghi de className thanh "ok" / "err":
  data-msg="toast"  - noi goc duoi phai, bam vao de dong
  data-msg="inline" - nam ngay duoi nut bam
"""

from __future__ import annotations

import html

NavItem = tuple[str, str, str]          # (key, nhan, duong dan)

CSS = """
  :root {
    color-scheme: light dark;
    --bg:#f3f4f6; --surface:#fff; --surface-2:#f8f9fb; --field:#fff;
    --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --line-strong:#d1d5db;
    --accent:#2563eb; --accent-soft:rgba(37,99,235,.10);
    --ok:#047857; --ok-soft:rgba(4,120,87,.10);
    --err:#b91c1c; --err-soft:rgba(185,28,28,.08);
    --warn:#92400e; --warn-soft:#fffbeb; --warn-line:#fcd34d;
    --shadow:0 1px 2px rgba(16,24,40,.05);
    --shadow-lg:0 10px 30px rgba(16,24,40,.14);
    --mono:ui-monospace,"Cascadia Code",Consolas,monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0b0d12; --surface:#141820; --surface-2:#191e28; --field:#0f131a;
      --ink:#e5e7eb; --muted:#9ca3af; --line:#232936; --line-strong:#323a4a;
      --accent:#4f8ff7; --accent-soft:rgba(79,143,247,.14);
      --ok:#34d399; --ok-soft:rgba(52,211,153,.12);
      --err:#f87171; --err-soft:rgba(248,113,113,.12);
      --warn:#fbbf24; --warn-soft:rgba(251,191,36,.07); --warn-line:rgba(251,191,36,.35);
      --shadow:none; --shadow-lg:0 10px 30px rgba(0,0,0,.5);
    }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }

  /* thanh dieu huong */
  .topbar { position:sticky; top:0; z-index:10; background:var(--surface);
    border-bottom:1px solid var(--line); }
  .topbar-in { max-width:1160px; margin:0 auto; padding:0 20px; height:52px;
    display:flex; align-items:center; gap:28px; }
  .brand { display:flex; align-items:center; gap:8px; font-weight:700; letter-spacing:.02em;
    color:var(--ink); white-space:nowrap; }
  .brand:hover { text-decoration:none; }
  .brand i { width:22px; height:22px; border-radius:6px; background:var(--accent);
    display:inline-grid; place-items:center; color:#fff; font:700 12px/1 system-ui; font-style:normal; }
  .nav { display:flex; gap:2px; overflow-x:auto; scrollbar-width:none; }
  .nav a { padding:6px 12px; border-radius:7px; color:var(--muted); font-weight:500;
    white-space:nowrap; }
  .nav a:hover { color:var(--ink); background:var(--surface-2); text-decoration:none; }
  .nav a.on { color:var(--accent); background:var(--accent-soft); }

  /* bo cuc */
  .wrap { max-width:1160px; margin:0 auto; padding:28px 20px 96px; }
  .page-head { margin-bottom:22px; }
  .page-head h1 { font-size:22px; line-height:1.25; margin:0 0 6px; font-weight:650; }
  .sub { color:var(--muted); font-size:13px; margin:0; }
  .cols { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 18px; align-items:start; }
  @media (max-width: 920px) { .cols { grid-template-columns:minmax(0,1fr); } }
  @media (max-width: 560px) {
    .wrap { padding:20px 12px 96px; }
    .topbar-in { padding:0 12px; gap:12px; }
    .card { padding:16px; }
  }

  /* the */
  .card { background:var(--surface); border:1px solid var(--line); border-radius:12px;
    padding:20px 22px; margin-bottom:18px; box-shadow:var(--shadow); }
  .card h2 { font-size:15px; font-weight:650; margin:0 0 4px; display:flex; align-items:center;
    gap:8px; flex-wrap:wrap; }
  .card h3 { font-size:13px; font-weight:650; margin:20px 0 8px; }
  .card > .hint:first-of-type, .card > p.hint, .desc { margin:0 0 14px; }
  .desc { font-size:13px; color:var(--muted); }
  :is(.desc, .card > p.hint) + label,
  :is(.desc, .card > p.hint) + .row > * > label:first-child { margin-top:0; }

  /* form */
  label { display:block; font-size:12.5px; font-weight:500; color:var(--muted); margin:14px 0 6px; }
  input[type=text], input[type=password], input[type=number], input[type=date],
  input[type=time], select, textarea {
    width:100%; min-height:36px; padding:7px 10px; border:1px solid var(--line-strong);
    border-radius:8px; background:var(--field); color:var(--ink); font:inherit; font-size:14px;
    transition:border-color .12s, box-shadow .12s; }
  input::placeholder, textarea::placeholder { color:var(--muted); opacity:.7; }
  input:focus, select:focus, textarea:focus { outline:none; border-color:var(--accent);
    box-shadow:0 0 0 3px var(--accent-soft); }
  input:disabled, select:disabled { opacity:.55; cursor:not-allowed; }
  textarea { font-family:var(--mono); font-size:12.5px; resize:vertical; }
  select[multiple] { padding:4px; min-height:88px; }
  select[multiple] option { padding:5px 8px; border-radius:5px; margin:1px 0; }
  select[multiple] option:checked { background:linear-gradient(var(--accent-soft),var(--accent-soft));
    color:var(--ink); font-weight:600; }
  input[type=checkbox] { width:16px; height:16px; margin:0; accent-color:var(--accent); cursor:pointer; }
  label.check, .check label { display:inline-flex; align-items:center; gap:8px; margin:0;
    color:var(--ink); font-size:14px; font-weight:400; cursor:pointer; }
  .check { margin:2px 0 4px; }
  .row { display:flex; gap:0 16px; flex-wrap:wrap; }
  .row > * { flex:1 1 150px; min-width:0; }
  .row > .end { display:flex; align-items:flex-end; }
  .hint { font-size:12px; color:var(--muted); margin-top:6px; }
  .small { font-size:12px; color:var(--muted); }

  /* nut */
  .actions { display:flex; gap:8px; align-items:center; margin-top:18px; flex-wrap:wrap; }
  button { font:inherit; font-size:13.5px; font-weight:500; min-height:36px; padding:6px 14px;
    border-radius:8px; border:1px solid var(--line-strong); background:var(--surface);
    color:var(--ink); cursor:pointer; white-space:nowrap; transition:background .12s, border-color .12s; }
  button:hover { background:var(--surface-2); }
  button:focus-visible { outline:none; box-shadow:0 0 0 3px var(--accent-soft); border-color:var(--accent); }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.primary:hover { filter:brightness(1.08); }
  button.danger { color:var(--err); }
  button.danger:hover { background:var(--err-soft); border-color:var(--err); }
  button:disabled { opacity:.5; cursor:default; }
  .w100 { width:100%; }

  /* bang */
  .scroll { overflow-x:auto; border:1px solid var(--line); border-radius:10px; }
  .scroll + .hint, .scroll + p.hint { margin-top:10px; }
  .hint + .scroll { margin-top:12px; }
  .scroll.tall { max-height:460px; overflow:auto; }
  .scroll.tall th { position:sticky; top:0; z-index:1; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; font-weight:600; color:var(--muted); font-size:11.5px; letter-spacing:.03em;
    text-transform:uppercase; padding:9px 12px; background:var(--surface-2);
    border-bottom:1px solid var(--line); white-space:nowrap; }
  td { padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
  tbody tr:last-child > td { border-bottom:none; }
  tbody tr:hover > td { background:color-mix(in srgb, var(--surface-2) 60%, transparent); }
  td.id, td.sym { font-family:var(--mono); font-weight:600; }
  td.num { font-variant-numeric:tabular-nums; white-space:nowrap; }
  td.act { text-align:right; white-space:nowrap; }
  td.act button { min-height:28px; padding:3px 10px; font-size:12.5px; margin-left:4px; }
  td input[type=text], td select { min-height:32px; padding:5px 8px; font-size:13px; }
  .off td:not(.act) { opacity:.5; }
  td table th { background:transparent; }
  th .pill { text-transform:none; letter-spacing:0; }

  /* nhan, hop canh bao, so lieu */
  .pill { display:inline-flex; align-items:center; font-size:11.5px; font-weight:500; line-height:1.6;
    padding:0 8px; border-radius:99px; border:1px solid var(--line); background:var(--surface-2);
    color:var(--muted); white-space:nowrap; vertical-align:middle; }
  .pill.ok, .pill.go { color:var(--ok); background:var(--ok-soft); border-color:transparent; }
  .pill.err, .pill.no { color:var(--err); background:var(--err-soft); border-color:transparent; }
  .pill.warn { color:var(--warn); background:var(--warn-soft); border-color:var(--warn-line); }
  .warn { background:var(--warn-soft); border:1px solid var(--warn-line); border-radius:10px;
    padding:12px 14px; font-size:13px; margin-bottom:18px; }
  .warn ul { margin:6px 0 0; padding-left:18px; }
  .card .warn { margin:14px 0; }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:4px 0 16px; }
  .stat { background:var(--surface-2); border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
  .stat b { display:block; font-size:18px; font-weight:650; font-variant-numeric:tabular-nums; }
  .stat span { font-size:12px; color:var(--muted); }
  .stat.bad b { color:var(--err); }
  code { font-family:var(--mono); font-size:12px; background:var(--surface-2);
    border:1px solid var(--line); border-radius:4px; padding:0 4px; overflow-wrap:anywhere; }
  td code { overflow-wrap:normal; white-space:nowrap; }

  /* gap mo, tab */
  details { margin-top:16px; border-top:1px solid var(--line); padding-top:12px; }
  summary { cursor:pointer; font-size:13px; font-weight:600; padding:2px 0; }
  summary:hover { color:var(--accent); }
  details[open] > summary { margin-bottom:6px; }
  .tabs { display:inline-flex; gap:2px; flex-wrap:wrap; padding:3px; margin:0 0 12px;
    background:var(--surface-2); border:1px solid var(--line); border-radius:9px; }
  .tabs button { min-height:28px; padding:3px 12px; border:none; background:transparent; color:var(--muted); }
  .tabs button.on { background:var(--surface); color:var(--ink); font-weight:600;
    box-shadow:0 1px 2px rgba(0,0,0,.12); }

  /* lich su */
  .hist ul { list-style:none; margin:0; padding:0; }
  .hist li { font-size:12.5px; color:var(--muted); padding:7px 0; border-bottom:1px dashed var(--line); }
  .hist li:last-child { border-bottom:none; }
  .hist b { color:var(--ink); font-weight:600; }
  .hist details { margin:2px 0 0; padding:0; border:none; }
  .hist summary { font-size:12px; font-weight:500; color:var(--accent); }

  /* thong bao */
  [data-msg] { font-size:13px; white-space:pre-wrap; border-radius:8px; }
  [data-msg]:empty { display:none; }
  [data-msg].ok { color:var(--ok); } [data-msg].err { color:var(--err); }
  [data-msg="inline"] { margin-top:12px; }
  [data-msg="toast"] { position:fixed; right:20px; bottom:20px; z-index:20; max-width:min(460px, calc(100vw - 40px));
    padding:12px 16px; background:var(--surface); border:1px solid var(--line-strong);
    border-left:4px solid currentColor; box-shadow:var(--shadow-lg); cursor:pointer; }
  .actionbar { position:sticky; bottom:16px; z-index:5; display:flex; gap:8px 12px; align-items:center;
    flex-wrap:wrap; padding:10px 14px; background:var(--surface); border:1px solid var(--line-strong);
    border-radius:12px; box-shadow:var(--shadow-lg); }
  .actionbar [data-msg] { margin:0; flex:1 1 240px; }
  .actionbar .meta { margin-left:auto; font-size:12px; color:var(--muted); }
"""

# Bam vao toast thi dong; khong dung toi logic rieng cua trang nao.
SCRIPT = """<script>
document.addEventListener("click", (e) => {
  const box = e.target.closest('[data-msg="toast"]');
  if (box) box.textContent = "";
});
</script>"""


def fallback_nav(admin_path: str) -> list[NavItem]:
    """Khi khong co nav tu server: chi link ve trang cai dat chung."""
    return [("admin", "Cài đặt", admin_path)] if admin_path else []


def _nav(nav: list[NavItem], active: str) -> str:
    on = ' class="on" aria-current="page"'
    return "".join(
        f'<a href="{html.escape(path)}"{on if key == active else ""}>{html.escape(label)}</a>'
        for key, label, path in nav)


def page(*, title: str, active: str, nav: list[NavItem], body: str, css: str = "") -> str:
    """Boc phan than trang vao khung chung. `body` da duoc escape tu phia trang."""
    home = next((path for key, _label, path in nav if key == "admin"), "")
    brand = (f'<a class="brand" href="{html.escape(home)}"><i>B</i>BAMCP</a>' if home
             else '<span class="brand"><i>B</i>BAMCP</span>')
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BAMCP — {html.escape(title)}</title>
<style>{CSS}{css}</style>
</head>
<body>
<header class="topbar"><div class="topbar-in">{brand}<nav class="nav">{_nav(nav, active)}</nav></div></header>
<main class="wrap">
{body}
</main>
{SCRIPT}
</body>
</html>"""
