// TM - #DASHBOARD - Setup dashboard
// Trang chi doc: khong co thao tac dat, sua, huy lenh hay setup. Moi chu tu server dua vao DOM
// bang textContent, khong dung innerHTML, nen label, note khong the chen ma vao trang.
// TM - #DASH-LAYOUT - Bố cục dashboard & full page: mot file cho trang chinh (BOOT.view "main") va full page
// o tab rieng (BOOT.view "full"). Code ve chart giu nguyen; chi doi container, them lop bat/tat, resize theo
// container, reset zoom, phim tat, dong bo URL va tu lam moi dung khi tab an.
(function () {
  "use strict";

  const BOOT = JSON.parse(document.getElementById("boot").textContent);
  const API = BOOT.base + "/api";
  const OWNER = BOOT.role === "owner";
  const FULL = BOOT.view === "full";  // TM - #DASH-LAYOUT
  const TF_LABEL = { "1d": "1D", "4h": "4H", "1h": "1H", "15m": "15M", "5m": "5M" };
  const SPAN = { "5m": 3e5, "15m": 9e5, "1h": 36e5, "4h": 144e5, "1d": 864e5 };
  const MAX_BARS = 500;
  const MAX_WHITESPACE = 400;
  const STATUS_CLASS = {
    ENTRY_VALID: "go", FILLED: "go", IN_ZONE: "run", TRIGGERED: "run", CONFIRMED: "run",
    MISSED: "stop", BLOCKED: "stop", INVALIDATED: "stop", EXPIRED: "stop", CANCELLED: "stop",
    PRUNED: "stop",  // TM - #TR-BRANCH
  };
  const TERMINAL = ["FILLED", "INVALIDATED", "EXPIRED", "CANCELLED", "PRUNED"];
  // TM - #TR-BRANCH - Nhanh kich ban TR
  const BRANCH_WORDS = { accumulation: ["tăng", "tích luỹ"], distribution: ["giảm", "phân phối"] };
  const SYSTEM_EVENT_LABEL = { TR_BRANCH: "TR xác định nhánh · loại setup ngược nhánh" };
  const TRIGGER_LABEL = {
    save_bias: "Lưu bias", save_setups: "Lưu setup", save_scenarios: "Lưu kịch bản", cancel_setup: "Huỷ setup",
    // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
    update_tr: "Cập nhật TR", delete_tr_event: "Xoá event TR",
  };
  // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
  const SCHEMATIC_LABEL = {
    accumulation: "Tích luỹ", reaccumulation: "Tái tích luỹ", distribution: "Phân phối",
    redistribution: "Tái phân phối", undetermined: "Chưa xác định",
  };
  const FAMILY = { accumulation: "acc", reaccumulation: "acc", distribution: "dist", redistribution: "dist" };
  const TR_STATUS_LABEL = { hypothesis: "giả thuyết", confirmed: "đã xác nhận", failed: "thất bại", completed: "đã hoàn tất" };
  const EVENT_STATUS_LABEL = { tentative: "chưa chắc", confirmed: "đã xác nhận", failed: "hỏng" };
  // TM - #DASH-LAYOUT - Bố cục dashboard & full page
  // Lop tren chart: [khoa trong ?layers=, nhan, mac dinh]. Setup PRUNED va event hong an mac dinh nhu truoc.
  const LAYERS = [
    ["tr", "Biên TR", true], ["phase", "Pha", true], ["event", "Sự kiện Wyckoff", true],
    ["zone", "Vùng setup", true], ["levels", "Entry/SL/TP", true], ["trades", "Marker lệnh (gồm off-plan)", true],
    ["pruned", "Setup đã PRUNED", false], ["creek", "Creek/Ice", true], ["scn", "Kịch bản", true],
    ["keylv", "Mức giá bias/HTF", true], ["failed", "Event hỏng", false],
  ];
  // Khoa localStorage truoc #DASH-LAYOUT: lop nguoi dung da tat thi van tat
  const LEGACY_PREF = { phase: "phases", event: "events", creek: "creek", scn: "scenarios", keylv: "levels" };
  const TABS = ["setups", "events", "plan", "offplan", "stats"];
  const PAGE_ROWS = 100;

  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);

  // TM - #SCN-TOGGLE - Bat tat duong kich ban
  function loadHiddenScn() {
    try { return new Set(JSON.parse(localStorage.getItem("bamcp.dash.hiddenScn") || "[]")); } catch (e) { return new Set(); }
  }
  function saveHiddenScn() {
    try { localStorage.setItem("bamcp.dash.hiddenScn", JSON.stringify([...S.hiddenScn].slice(-200))); } catch (e) { /* trinh duyet chan */ }
  }

  function loadPref(key, fallback) {
    try {
      const value = localStorage.getItem("bamcp.dash." + key);
      return value === null ? fallback : value === "1";
    } catch (e) { return fallback; }
  }
  function savePref(key, value) {
    try { localStorage.setItem("bamcp.dash." + key, value ? "1" : "0"); } catch (e) { /* trinh duyet chan */ }
  }

  // TM - #DASH-LAYOUT - ?layers= co mat thi la danh sach lop BAT (lop khong ghi la tat); khong co thi theo localStorage
  function loadLayers() {
    const out = {};
    const asked = params.get("layers");
    if (asked !== null) {
      const on = new Set(asked.split(",").map((s) => s.trim()).filter(Boolean));
      LAYERS.forEach(([key]) => { out[key] = on.has(key); });
      return out;
    }
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem("bamcp.dash.layers") || "null"); } catch (e) { saved = null; }
    LAYERS.forEach(([key, , fallback]) => {
      if (saved && typeof saved[key] === "boolean") out[key] = saved[key];
      else out[key] = LEGACY_PREF[key] ? loadPref(LEGACY_PREF[key], fallback) : fallback;
    });
    return out;
  }
  function saveLayers() {
    try { localStorage.setItem("bamcp.dash.layers", JSON.stringify(S.layers)); } catch (e) { /* trinh duyet chan */ }
  }
  const layerQuery = () => LAYERS.filter(([key]) => S.layers[key]).map(([key]) => key).join(",");

  function loadTab() {
    try {
      const tab = localStorage.getItem("bamcp.dash.tab");
      return TABS.includes(tab) ? tab : "setups";
    } catch (e) { return "setups"; }
  }
  function saveTab() {
    try { localStorage.setItem("bamcp.dash.tab", S.tab); } catch (e) { /* trinh duyet chan */ }
  }

  const askedDate = params.get("date");
  const validDate = /^\d{4}-\d{2}-\d{2}$/.test(askedDate || "") ? askedDate : BOOT.today;
  const askedTf = params.get("tf");
  const askedRevision = params.get("revision");
  const S = {
    symbol: (params.get("symbol") || BOOT.default_symbol).toUpperCase(),
    date: validDate,
    mode: params.get("mode") === "review" || (!params.get("mode") && validDate < BOOT.today) ? "review" : "live",
    tf: SPAN[askedTf] ? askedTf : (SPAN[BOOT.default_tf] ? BOOT.default_tf : "1h"),
    revision: /^(origin|\d+)$/.test(askedRevision || "") ? askedRevision : "origin",
    // TM - #DASH-LAYOUT - TR dang chon: "" la TR cua ke hoach ngay (TR dang khoa)
    trId: (params.get("tr_id") || "").trim(),
    trs: null,
    tab: loadTab(),
    range: 30,
    groupBy: "trigger_type",
    selected: null,
    chart: null,
    openNotes: new Set(),
    // TM - #DASH-LAYOUT - Lop tren chart, nho theo trinh duyet (gom lop Wyckoff, kich ban, PRUNED cu)
    layers: loadLayers(),
    // TM - #SCN-TOGGLE - Bat tat duong kich ban
    hiddenScn: loadHiddenScn(),
    markerIndex: {},
    popover: null,
    trFilter: false,
    trChanges: null,
    trLoading: null,
    setupRows: null,
    // TM - #DASH-LAYOUT - Du lieu rieng cua tung tab duoi chart, tai khi tab mo; moi lan lam moi xoa sach
    lists: {},
    pages: {},
    flash: null,
    doneOpen: false,
    sideCollapsed: loadPref("sideCollapsed", false),
    failed: false,
  };
  S.doneOpen = S.mode === "review";
  if (S.tab === "offplan" && !OWNER) S.tab = "setups";
  let chartTicket = 0;
  let listTicket = 0;
  let listGen = 0;
  let pollTimer = null;

  // ---------------------------------------------------------------- dinh dang

  function makeTime(options) { return new Intl.DateTimeFormat("vi-VN", { timeZone: BOOT.tz, hour12: false, ...options }); }
  const F_HM = makeTime({ hour: "2-digit", minute: "2-digit" });
  const F_DM = makeTime({ day: "2-digit", month: "2-digit" });
  const F_FULL = makeTime({ day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  const hm = (ms) => F_HM.format(ms);
  const full = (ms) => F_FULL.format(ms);

  function num(value, digits) {
    if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
    const n = Number(value);
    const places = digits !== undefined ? digits : Math.abs(n) >= 1000 ? 2 : Math.abs(n) >= 1 ? 3 : 6;
    return n.toLocaleString("en-US", { maximumFractionDigits: places });
  }
  function signed(value) {
    const n = Number(value);
    return (n > 0 ? "+" : "") + num(n, 2);
  }
  // TM - #TR-BRANCH - Ma moi BTC-20261112-S-DIST-D-LPSY-1 -> "S-DIST-D-LPSY-1" (bo cap + ngay, van
  // duy nhat trong ngay); ma cu BTC-20261101-A -> "A".
  function letter(code) {
    const parts = String(code || "").split("-");
    return parts.length > 3 ? parts.slice(2).join("-") : parts.pop();
  }
  // TM - #DASH-LAYOUT - Setup PRUNED theo lop "Setup đã PRUNED" (danh sach lan chart)
  const shownSetups = (setups) => (S.layers.pruned ? setups : setups.filter((s) => s.status !== "PRUNED"));

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === null || value === undefined || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "on") Object.entries(value).forEach(([name, fn]) => node.addEventListener(name, fn));
      else if (key in node && key !== "list") node[key] = value;
      else node.setAttribute(key, value);
    });
    children.flat().forEach((child) => {
      if (child === null || child === undefined || child === false) return;
      node.append(child);
    });
    return node;
  }
  const empty = (text) => el("p", { class: "empty" }, text);

  // TM - #DASH-LAYOUT - Bang > 100 dong: chia trang 100 dong, rerender chi ve lai dung bang do
  function paged(key, rows, renderRows, rerender) {
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_ROWS));
    const page = Math.min(S.pages[key] || 0, pages - 1);
    const body = renderRows(rows.slice(page * PAGE_ROWS, (page + 1) * PAGE_ROWS));
    if (pages === 1) return [body];
    const go = (n) => { S.pages[key] = n; rerender(); };
    return [body, el("div", { class: "pager" },
      el("button", { type: "button", class: "btn sm", disabled: page === 0, on: { click: () => go(page - 1) } }, "‹ Trước"),
      el("span", {}, `Trang ${page + 1}/${pages} · ${rows.length} dòng`),
      el("button", { type: "button", class: "btn sm", disabled: page >= pages - 1, on: { click: () => go(page + 1) } }, "Sau ›"))];
  }

  // ---------------------------------------------------------------- goi API, URL

  function loginRedirect() {
    // TM - #DASH-LAYOUT - Quay lai dung trang dang xem (trang chinh hay full page) kem du query
    const next = location.pathname + location.search;
    location.href = BOOT.base + "/login?next=" + encodeURIComponent(next);
  }

  async function api(path, query, options) {
    const search = new URLSearchParams();
    Object.entries(query || {}).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") search.set(key, value);
    });
    const url = API + path + (search.toString() ? "?" + search : "");
    let res;
    try {
      res = await fetch(url, { credentials: "same-origin", ...(options || {}) });
    } catch (e) {
      throw Object.assign(new Error("Mất kết nối"), { status: 0 });
    }
    if (res.status === 401) { loginRedirect(); throw Object.assign(new Error("Phiên đăng nhập đã hết"), { status: 401 }); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.error || "HTTP " + res.status), { status: res.status });
    return data;
  }

  // TM - #DASH-LAYOUT - Query de doc va bookmark: null la bo, dau phay giu nguyen (?layers=tr,phase)
  function qs(obj) {
    return Object.entries(obj).filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => `${k}=${encodeURIComponent(v).replace(/%2C/gi, ",")}`).join("&");
  }
  function fullQuery() {
    const review = S.mode === "review";
    return qs({ symbol: S.symbol, date: S.date, tr_id: S.trId || null, layers: layerQuery(), tf: S.tf,
                mode: review ? "review" : null, revision: review && S.revision !== "origin" ? S.revision : null });
  }
  function syncUrl() {
    if (FULL) {
      history.replaceState(null, "", `${BOOT.base}/full?${fullQuery()}`);
      return;
    }
    const review = S.mode === "review";
    history.replaceState(null, "", `${BOOT.base}?${qs({ symbol: S.symbol, date: S.date, mode: S.mode, tf: S.tf,
      tr_id: S.trId || null, revision: review && S.revision !== "origin" ? S.revision : null })}`);
    $("fullLink").href = `${BOOT.base}/full?${fullQuery()}`;
  }

  // ---------------------------------------------------------------- banner, tu lam moi

  // TM - #DASH-LAYOUT - Loi ket noi uu tien hon canh bao du lieu tre; loi thi giu du lieu cu tren chart
  const bannerText = { stale: "", error: "" };
  let retryAt = 0;
  let retryTick = null;
  const pollSeconds = () => Math.max(5, Number(BOOT.poll_seconds) || 60);

  function paintBanner() {
    const box = $("banner");
    const text = bannerText.error || bannerText.stale;
    box.textContent = text;
    box.hidden = !text;
  }

  function connectionLost(err) {
    if (err && err.status === 401) return;
    S.failed = true;
    retryAt = Date.now() + pollSeconds() * 1000;
    clearInterval(retryTick);
    const paint = () => {
      const left = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
      bannerText.error = err && err.status >= 400 && err.status < 500
        ? `Không tải được dữ liệu: ${err.message} – thử lại sau ${left}s`
        : `Mất kết nối – thử lại sau ${left}s`;
      paintBanner();
    };
    paint();
    retryTick = setInterval(paint, 1000);
  }

  function connectionOk() {
    S.failed = false;
    clearInterval(retryTick);
    retryTick = null;
    bannerText.error = "";
    paintBanner();
  }

  // Chi hen lan sau khi tab dang hien; Live lam moi theo chu ky, Review chi thu lai khi vua loi
  function schedule() {
    clearTimeout(pollTimer);
    pollTimer = null;
    if (document.hidden || (S.mode !== "live" && !S.failed)) return;
    pollTimer = setTimeout(() => { pollTimer = null; refresh(false); }, pollSeconds() * 1000);
  }

  function track(promises) {
    return Promise.allSettled([].concat(promises)).then((results) => {
      const bad = results.find((r) => r.status === "rejected");
      if (bad) connectionLost(bad.reason); else connectionOk();
      schedule();
    });
  }

  // ---------------------------------------------------------------- bieu do

  const LW = window.LightweightCharts;
  const chartBox = $("chart");
  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function alpha(color, a) {
    const hex = color.replace("#", "");
    const full6 = hex.length === 3 ? hex.split("").map((c) => c + c).join("") : hex;
    const n = parseInt(full6, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  function palette() {
    return {
      surface: token("--surface"), ink: token("--ink"), muted: token("--muted"), grid: token("--grid"), line: token("--line-strong"),
      long: token("--long"), short: token("--short"), entry: token("--entry"), invalidate: token("--invalidate"),
      tr: token("--tr"), neutral: token("--neutral"),
      offplan: token("--offplan"),  // TM - #OFF-PLAN
    };
  }
  let C = palette();

  // TM - #DASH-LAYOUT - Kich thuoc lay tu container (ResizeObserver ben duoi), khong co dinh bang px
  const chart = LW.createChart(chartBox, {
    width: Math.max(1, Math.floor(chartBox.clientWidth)),
    height: Math.max(1, Math.floor(chartBox.clientHeight)),
    layout: { background: { type: "solid", color: "transparent" }, textColor: C.muted, fontSize: 11 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    crosshair: { mode: LW.CrosshairMode.Normal },
    rightPriceScale: { borderColor: C.line, scaleMargins: { top: 0.06, bottom: 0.2 } },
    timeScale: {
      borderColor: C.line, timeVisible: true, secondsVisible: false, rightOffset: 4, barSpacing: 7,
      tickMarkFormatter: (time, type) => {
        const ms = time * 1000;
        return type <= 2 ? F_DM.format(ms) : hm(ms);
      },
    },
    localization: { locale: "vi-VN", timeFormatter: (time) => full(time * 1000) },
  });
  const candleSeries = chart.addCandlestickSeries({
    upColor: C.long, downColor: C.short, wickUpColor: C.long, wickDownColor: C.short, borderVisible: false,
    autoscaleInfoProvider: (original) => {
      const res = original();
      if (!res || !res.priceRange) return res;
      let low = res.priceRange.minValue;
      let high = res.priceRange.maxValue;
      const reach = (high - low) * 1.5;
      const baseLow = low;
      const baseHigh = high;
      autoscaleLevels().forEach((level) => {
        if (level.force || (level.price > baseLow - reach && level.price < baseHigh + reach)) {
          low = Math.min(low, level.price);
          high = Math.max(high, level.price);
        }
      });
      return { priceRange: { minValue: low, maxValue: high }, margins: res.margins };
    },
  });
  const volumeSeries = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });

  // TM - #DASH-LAYOUT - Resize debounce 100ms. Giu nguyen doan nen dang xem khi doi kich thuoc cua so, thu /
  // mo sidebar hay xoay man hinh: cung cac nen do, chi rong / hep hon.
  let resizeTimer = null;
  function fitChartBox() {
    resizeTimer = null;
    const width = Math.floor(chartBox.clientWidth);
    const height = Math.floor(chartBox.clientHeight);
    if (!width || !height) return;
    const scale = chart.timeScale();
    const range = scale.getVisibleLogicalRange();
    chart.resize(width, height);
    if (range) scale.setVisibleLogicalRange(range);
    if (S.popover) placePopover();
  }
  new ResizeObserver(() => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fitChartBox, 100);
  }).observe(chartBox);

  // Vung, TR, kich ban ve bang primitive de nam trong chu ky ve cua chart: keo, zoom hay doi
  // thang gia deu khop, khong lech nhu lop canvas dat de len tren.
  const overlay = {
    data: null,
    paneViews() { return [paneView]; },
    updateAllViews() {},
    attached(args) { this.requestUpdate = args.requestUpdate; },
    detached() { this.requestUpdate = null; },
    refresh() { if (this.requestUpdate) this.requestUpdate(); },
  };
  const paneView = {
    zOrder() { return "normal"; },
    renderer() {
      return {
        drawBackground(target) { target.useMediaCoordinateSpace((scope) => drawBands(scope.context, scope.mediaSize)); },
        draw(target) { target.useMediaCoordinateSpace((scope) => drawLines(scope.context, scope.mediaSize)); },
      };
    },
  };
  candleSeries.attachPrimitive(overlay);

  const yOf = (price) => candleSeries.priceToCoordinate(price);

  // Toa do x cua mot moc thoi gian, ke ca moc nam ngoai du lieu (ngoai suy theo khoang cach nen)
  function xOf(ms) {
    const d = overlay.data;
    if (!d || !d.axisFirst) return null;
    const scale = chart.timeScale();
    const snapped = Math.floor(ms / d.span) * d.span;
    const direct = scale.timeToCoordinate(snapped / 1000);
    if (direct !== null) return direct;
    const a = scale.timeToCoordinate(d.axisFirst / 1000);
    const b = scale.timeToCoordinate((d.axisFirst + d.span) / 1000);
    if (a === null || b === null) return null;
    return a + ((snapped - d.axisFirst) / d.span) * (b - a);
  }

  // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
  // Dai pha: tu mep trai nen mo pha toi mep trai nen mo pha sau; pha dang chay keo toi het nen cuoi.
  function phaseSpans(size) {
    const d = overlay.data;
    if (!d || !S.layers.phase || !d.tr || !(d.tr.phases || []).length || d.lastTime === null) return [];
    const half = (chart.timeScale().options().barSpacing || 6) / 2;
    const lastX = xOf(d.lastTime);
    return d.tr.phases.map((phase, index) => {
      const start = xOf(phase.start_t);
      const open = phase.end_t === null || phase.end_t === undefined;
      const end = open ? (lastX === null ? null : lastX + half) : xOf(phase.end_t) - half;
      if (start === null || end === null) return null;
      const x1 = Math.max(0, start - half);
      const x2 = Math.min(size.width, end);
      return x2 > x1 ? { phase: phase.phase, x1, x2, shade: index % 2 } : null;
    }).filter(Boolean);
  }

  function drawBands(ctx, size) {
    const d = overlay.data;
    if (!d) return;
    // Hai pha lien ke xen ke hai muc xam, khong dung mau rieng cho tung pha
    phaseSpans(size).forEach((span) => {
      ctx.fillStyle = alpha(C.neutral, span.shade ? 0.12 : 0.05);
      ctx.fillRect(span.x1, 0, span.x2 - span.x1, size.height);
    });
    if (d.tr && S.layers.tr) {  // TM - #DASH-LAYOUT - lop Bien TR
      const top = yOf(d.tr.tr_high);
      const bottom = yOf(d.tr.tr_low);
      if (top !== null && bottom !== null) {
        const start = d.tr.since ? Math.max(0, xOf(d.tr.since) ?? 0) : 0;
        ctx.fillStyle = alpha(C.tr, 0.07);
        ctx.fillRect(start, top, size.width - start, bottom - top);
      }
    }
    if (!S.layers.zone) return;  // TM - #DASH-LAYOUT - lop Vung setup
    d.zones.forEach((zone) => {
      const top = yOf(zone.high);
      const bottom = yOf(zone.low);
      if (top === null || bottom === null) return;
      const start = Math.max(0, xOf(zone.start) ?? 0);
      const on = S.selected === zone.code;
      const strength = on ? 0.32 : S.selected ? 0.07 : zone.dim ? 0.06 : 0.16;
      ctx.fillStyle = alpha(zone.color, strength);
      ctx.fillRect(start, top, size.width - start, bottom - top);
    });
  }

  function textBox(ctx, text, x, y, color, size) {
    ctx.font = "600 11px system-ui, sans-serif";
    const width = ctx.measureText(text).width + 8;
    const left = Math.max(2, Math.min(x, size.width - width - 2));
    const top = Math.max(2, Math.min(y - 8, size.height - 18));
    ctx.fillStyle = alpha(C.surface.startsWith("#") ? C.surface : "#ffffff", 0.82);
    ctx.fillRect(left, top, width, 16);
    ctx.fillStyle = color;
    ctx.textBaseline = "middle";
    ctx.fillText(text, left + 4, top + 8);
  }

  function drawArrow(ctx, from, to, color) {
    const angle = Math.atan2(to.y - from.y, to.x - from.x);
    const length = 10;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(to.x, to.y);
    ctx.lineTo(to.x - length * Math.cos(angle - 0.45), to.y - length * Math.sin(angle - 0.45));
    ctx.lineTo(to.x - length * Math.cos(angle + 0.45), to.y - length * Math.sin(angle + 0.45));
    ctx.closePath();
    ctx.fill();
  }

  function drawLines(ctx, size) {
    const d = overlay.data;
    if (!d) return;
    ctx.save();
    if (d.tr) {
      const start = d.tr.since ? Math.max(0, xOf(d.tr.since) ?? 0) : 0;
      if (S.layers.tr) {  // TM - #DASH-LAYOUT - lop Bien TR
        ctx.strokeStyle = alpha(C.tr, 0.85);
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 4]);
        [d.tr.tr_high, d.tr.tr_low].forEach((price) => {
          const y = yOf(price);
          if (y === null) return;
          ctx.beginPath();
          ctx.moveTo(start, y);
          ctx.lineTo(size.width, y);
          ctx.stroke();
        });
        ctx.setLineDash([]);
        const y = yOf(d.tr.tr_high);
        // TM - #WYCKOFF-TR: dinh TR ngoai khung nhin thi khong ghim nhan len mep tren (de len nhan pha)
        if (y !== null && y >= 0 && y <= size.height) textBox(ctx, "TR khoá", start + 4, y + 10, C.tr, size);
      }
      // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR: creek / ice net dut co nhan, tu since
      if (S.layers.creek) {
        [["creek", "Creek"], ["ice", "Ice"]].forEach(([key, name]) => {
          const price = (d.tr.levels || {})[key];
          const ly = price === null || price === undefined ? null : yOf(Number(price));
          if (ly === null) return;
          ctx.strokeStyle = alpha(C.tr, 0.95);
          ctx.lineWidth = 1.25;
          ctx.setLineDash([2, 3]);
          ctx.beginPath();
          ctx.moveTo(start, ly);
          ctx.lineTo(size.width, ly);
          ctx.stroke();
          ctx.setLineDash([]);
          textBox(ctx, `${name} ${num(price)}`, size.width, ly - 9, C.tr, size);
        });
      }
    }
    phaseSpans(size).forEach((span) => {
      if (span.x2 - span.x1 >= 14) textBox(ctx, span.phase, span.x1 + 3, 11, C.muted, size);
    });
    if (S.layers.zone) {
      d.zones.forEach((zone) => {
        const y = yOf(zone.high);
        if (y === null || (S.selected && S.selected !== zone.code) || zone.dim) return;
        textBox(ctx, letter(zone.code), Math.max(4, (xOf(zone.start) ?? 0) + 4), y + 9, zone.color, size);
      });
    }
    ctx.font = "10px system-ui, sans-serif";
    ctx.fillStyle = C.muted;
    ctx.textBaseline = "bottom";
    let lastLabelY = -100;
    d.levels.map((level) => ({ label: level.label, y: yOf(level.price) }))
      .filter((level) => level.y !== null && level.y >= 0 && level.y <= size.height)
      .sort((a, b) => a.y - b.y)
      .forEach((level) => {
        if (level.y - lastLabelY < 12) return;
        lastLabelY = level.y;
        ctx.fillText(level.label, 4, level.y - 1);
      });
    d.scenarios.forEach((scenario) => {
      const points = scenario.points
        .map((p) => ({ x: xOf(p.t), y: yOf(p.price) }))
        .filter((p) => p.x !== null && p.y !== null);
      if (points.length < 2) return;
      ctx.strokeStyle = scenario.color;
      ctx.lineWidth = scenario.kind === "main" ? 2 : 1.5;
      ctx.setLineDash([2, 4]);
      ctx.lineCap = "round";
      ctx.beginPath();
      points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.stroke();
      ctx.setLineDash([]);
      const last = points[points.length - 1];
      drawArrow(ctx, points[points.length - 2], last, scenario.color);
      textBox(ctx, scenario.label, last.x - 8 - Math.min(180, scenario.label.length * 6), last.y - 13, scenario.color, size);
    });
    // TM - #DASH-LAYOUT - Vien dam 2 giay quanh vung setup vua chon (ke ca khi lop Vung setup dang tat)
    const flash = S.flash && d.zones.find((z) => z.code === S.flash);
    if (flash) {
      const top = yOf(flash.high);
      const bottom = yOf(flash.low);
      if (top !== null && bottom !== null) {
        const start = Math.max(0, xOf(flash.start) ?? 0);
        ctx.strokeStyle = flash.color;
        ctx.lineWidth = 3;
        ctx.strokeRect(start + 1.5, top, Math.max(4, size.width - start - 3), Math.max(3, bottom - top));
      }
    }
    ctx.restore();
  }

  // Cac muc cua ke hoach duoc keo vao thang gia neu khong qua xa nen dang xem, de vung va TR
  // luon thay duoc. Setup dang chon thi luon lay du vung + entry/SL/TP.
  function autoscaleLevels() {
    const d = overlay.data;
    if (!d) return [];
    const out = [];
    if (d.tr && S.layers.tr) out.push({ price: d.tr.tr_high }, { price: d.tr.tr_low });
    // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
    if (d.tr && S.layers.creek) {
      ["creek", "ice"].forEach((key) => { if ((d.tr.levels || {})[key]) out.push({ price: Number(d.tr.levels[key]) }); });
    }
    d.zones.forEach((zone) => {
      const force = zone.code === S.selected;
      if (!force && (zone.dim || !S.layers.zone)) return;
      out.push({ price: zone.low, force }, { price: zone.high, force });
      if (force && S.layers.levels) zone.extra.forEach((price) => { if (price !== null && price !== undefined) out.push({ price, force }); });
    });
    d.scenarios.forEach((s) => s.points.forEach((p) => out.push({ price: p.price })));
    return out;
  }

  let priceLines = [];
  function clearPriceLines() {
    priceLines.forEach((line) => candleSeries.removePriceLine(line));
    priceLines = [];
  }
  function addLine(price, options) {
    if (price === null || price === undefined) return;
    priceLines.push(candleSeries.createPriceLine({ price: Number(price), lineWidth: 1, axisLabelVisible: true, ...options }));
  }

  function drawPriceLines(d) {
    clearPriceLines();
    const Solid = LW.LineStyle.Solid;
    const Dashed = LW.LineStyle.Dashed;
    const Dotted = LW.LineStyle.Dotted;
    // TM - #DASH-LAYOUT - lop Entry/SL/TP; PRUNED theo lop "Setup đã PRUNED"
    (S.layers.levels ? shownSetups(d.setups) : []).forEach((setup) => {
      if (setup.status === "CANCELLED") return;
      const on = S.selected === setup.code;
      const faded = (S.selected && !on) || TERMINAL.includes(setup.status);
      const tint = (color) => (faded ? alpha(color, 0.3) : color);
      const tag = letter(setup.code);
      const shown = !faded;
      addLine(setup.entry, { color: tint(C.entry), lineStyle: Solid, title: tag + " entry", axisLabelVisible: shown, lineWidth: on ? 2 : 1 });
      addLine(setup.stop, { color: tint(C.short), lineStyle: Solid, title: tag + " SL", axisLabelVisible: shown, lineWidth: on ? 2 : 1 });
      addLine(setup.target, { color: tint(C.long), lineStyle: Solid, title: tag + " TP", axisLabelVisible: shown, lineWidth: on ? 2 : 1 });
      addLine(setup.invalidate_level, { color: tint(C.invalidate), lineStyle: Dashed, title: tag + " huỷ", axisLabelVisible: shown });
    });
    if (S.layers.keylv) {
      overlay.data.levels.forEach((level) => addLine(level.price, { color: alpha(C.neutral, 0.45), lineStyle: Dotted, axisLabelVisible: false }));
    }
    // Review la ngay da qua: vi the dang mo hom nay khong lien quan, ve len de hieu nham
    // TM - #DASH-LAYOUT - vi the thuoc lop Marker lenh
    (S.mode === "live" && S.layers.trades ? (d.positions || {}).positions || [] : []).forEach((pos) => {
      addLine(pos.entry_price, { color: pos.side === "long" ? C.long : C.short, lineStyle: Dashed, lineWidth: 2,
                                 title: `Vị thế ${pos.side} ${num(pos.size, 4)}` });
    });
  }

  function buildMarkers(d, times, formingTime) {
    const span = SPAN[d.timeframe];
    const out = [];
    const snap = (ms) => Math.floor(ms / span) * span / 1000;
    // TM - #TR-BRANCH - marker cua setup PRUNED an cung the setup
    const hidden = new Set(d.setups.filter((s) => !shownSetups([s]).length).map((s) => s.code));
    (d.markers || []).forEach((m) => {
      // TM - #OFF-PLAN - Diem vao lenh ngoai ke hoach: marker cam, chi chu tai khoan nhan duoc
      if (m.kind === "off_plan") {
        if (!S.layers.trades) return;  // TM - #DASH-LAYOUT - lop Marker lenh gom off-plan
        const at = snap(m.t);
        if (!times.has(at)) return;
        const up = m.side === "long";
        out.push({ time: at, position: up ? "belowBar" : "aboveBar", shape: up ? "arrowUp" : "arrowDown",
                   color: C.offplan, text: `${String(m.code).replace(/^OP-\d{8}-/, "OP ")} ${m.classification}` });
        return;
      }
      if (m.kind === "wyckoff" || hidden.has(m.code) || !S.layers.trades) return;
      const time = snap(m.t);
      if (!times.has(time)) return;
      const up = m.direction === "long";
      const base = { time, text: `${letter(m.code)} ${m.event}` };
      if (m.event === "IN_ZONE") out.push({ ...base, position: "inBar", shape: "circle", color: C.entry });
      else if (m.event === "TRIGGERED") out.push({ ...base, position: up ? "belowBar" : "aboveBar", shape: up ? "arrowUp" : "arrowDown", color: up ? C.long : C.short });
      else out.push({ ...base, position: "aboveBar", shape: "square", color: m.event === "EXPIRED" ? C.neutral : C.short });
    });
    out.push(...wyckoffMarkers(d, times));
    if (formingTime) out.push({ time: formingTime, text: "đang chạy", position: "aboveBar", shape: "circle", size: 0, color: C.muted });
    return out.sort((a, b) => a.time - b.time);
  }

  // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR
  // Event day duoi nen (mui ten len), event dinh tren nen (mui ten xuong). Cung mot nen thi
  // Lightweight Charts tu xep chong theo chieu doc. Mau theo so do; confirmed dam, tentative nhat
  // kem "?", failed xam gach ngang va an mac dinh.
  const strike = (text) => Array.from(String(text)).map((ch) => ch + "̶").join("");

  function wyckoffColor(tr, status) {
    const family = FAMILY[(tr || {}).schematic];
    const base = family === "acc" ? C.long : family === "dist" ? C.short : C.neutral;
    if (status === "failed") return alpha(C.neutral, 0.6);
    return status === "confirmed" ? base : alpha(base, 0.5);
  }

  function wyckoffMarkers(d, times) {
    S.markerIndex = {};
    if (!S.layers.event) return [];
    const events = new Map(((d.tr || {}).events || []).map((e) => [e.id, e]));
    const out = [];
    (d.markers || []).forEach((m) => {
      if (m.kind !== "wyckoff" || (m.status === "failed" && !S.layers.failed)) return;
      const time = m.t_bar / 1000;
      if (!times.has(time)) return;
      const id = "w:" + m.event_id;
      S.markerIndex[id] = { marker: m, event: events.get(m.event_id) || m };
      const low = m.side === "low";
      out.push({
        id, time, color: wyckoffColor(d.tr, m.status),
        text: m.status === "failed" ? strike(m.label) : m.label + (m.status === "tentative" ? "?" : ""),
        position: low ? "belowBar" : "aboveBar", shape: low ? "arrowUp" : "arrowDown",
      });
    });
    return out;
  }

  // ---------------------------------------------------------------- popover event
  const pop = $("popover");
  const ratio = (value) => (value === null || value === undefined ? "—" : `${num(value, 2)}×`);

  function hidePopover() {
    S.popover = null;
    pop.hidden = true;
  }

  // Neo vao nen va gia cua marker, nen keo / zoom thi popover di theo
  function placePopover() {
    const item = S.popover && S.markerIndex[S.popover];
    if (!item) { hidePopover(); return; }
    const x = chart.timeScale().timeToCoordinate(item.marker.t_bar / 1000);
    const y = candleSeries.priceToCoordinate(Number(item.marker.price));
    const width = chartBox.clientWidth;
    const height = chartBox.clientHeight;
    if (x === null || y === null || x < 0 || x > width) { pop.hidden = true; return; }
    pop.hidden = false;
    const w = pop.offsetWidth;
    const h = pop.offsetHeight;
    pop.style.left = `${Math.max(4, Math.min(x + 12, width - w - 4))}px`;
    pop.style.top = `${y + 14 + h > height ? Math.max(4, y - h - 14) : y + 14}px`;
  }

  function openPopover(id) {
    const item = S.markerIndex[id];
    if (!item) { hidePopover(); return; }
    const e = item.event;
    const confirmed = e.confirmed_by_label || null;
    const rows = [
      ["Thời gian", full(e.t) + (e.tf ? ` · ${TF_LABEL[e.tf] || e.tf}` : "")],
      ["Giá", num(e.price)],
      ["Vol / SMA20", ratio(e.vol_ratio)],
      ["Spread / TB20", ratio(e.spread_ratio)],
      ["Đóng cửa", e.close_pct === null || e.close_pct === undefined ? "—" : `${num(e.close_pct, 1)}%`],
      ["Trạng thái", EVENT_STATUS_LABEL[e.status] || e.status || "—"],
      e.phase ? ["Pha", e.phase] : null,
      confirmed ? ["Xác nhận bởi", confirmed] : null,
    ].filter(Boolean);
    // TM - #DASH-LAYOUT - loc null: event khong co note thi replaceChildren(null) in ra chu "null"
    pop.replaceChildren(...[
      el("div", { class: "pop-head" }, el("strong", {}, e.label || e.type),
        el("button", { type: "button", class: "pop-close", "aria-label": "Đóng", on: { click: hidePopover } }, "×")),
      el("dl", { class: "pop-facts" }, rows.map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v)))),
      e.note ? el("p", { class: "pop-note" }, e.note) : null].filter(Boolean));
    S.popover = id;
    placePopover();
  }

  chart.subscribeClick((param) => {
    const id = param.hoveredObjectId;
    if (id && S.markerIndex[id]) openPopover(id);
    else hidePopover();
  });
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => { if (S.popover) placePopover(); });

  function scenarioColor(scenario, setups) {
    const linked = setups.find((s) => scenario.setup_codes.includes(s.code));
    const points = scenario.points;
    const rising = linked ? linked.direction === "long" : points[points.length - 1].price >= points[0].price;
    const mainUp = scenario.kind === "main" ? rising : !rising;
    return mainUp ? C.long : C.short;
  }

  function buildOverlay(d) {
    const span = SPAN[d.timeframe];
    const candles = d.candles;
    const levels = [];
    if (S.layers.keylv) {
      Object.entries((d.bias || {}).key_levels || {}).forEach(([name, price]) => levels.push({ price: Number(price), label: name }));
      (d.htf_levels || []).forEach((level) => levels.push({
        price: level.price, label: `${level.tf === "1w" ? "W" : "D"} swing ${level.kind === "high" ? "đỉnh" : "đáy"}`,
      }));
    }
    overlay.data = {
      span,
      axisFirst: candles.length ? candles[0][0] : null,
      // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR: pha dang chay keo toi nen cuoi
      lastTime: candles.length ? candles[candles.length - 1][0] : null,
      tr: d.tr,
      zones: shownSetups(d.setups).filter((s) => s.status !== "CANCELLED").map((s) => ({  // TM - #TR-BRANCH
        code: s.code, low: s.zone_low, high: s.zone_high, start: s.created_ms,
        color: s.direction === "long" ? C.long : C.short, dim: TERMINAL.includes(s.status),
        extra: [s.entry, s.stop, s.target, s.invalidate_level],
      })),
      levels,
      // TM - #SCN-TOGGLE - Bat tat duong kich ban
      scenarios: !S.layers.scn ? [] : d.scenarios.filter((s) => s.points && s.points.length >= 2 && !S.hiddenScn.has(s.code)).map((s) => ({
        kind: s.kind, label: s.label, points: s.points, color: scenarioColor(s, d.setups),
      })),
    };
  }

  // TM - #SCN-TOGGLE - Bat tat duong kich ban
  function toggleScenario(code) {
    if (S.hiddenScn.has(code)) S.hiddenScn.delete(code); else S.hiddenScn.add(code);
    saveHiddenScn();
    if (S.chart) renderChart(S.chart.data, false);
    if (!FULL && S.tab === "plan") renderPlanPane();
  }

  function renderScnChips(d) {
    const box = $("scnChips");
    const list = S.layers.scn ? d.scenarios.filter((s) => s.points && s.points.length >= 2) : [];
    box.hidden = !list.length;
    box.replaceChildren(...list.map((s) => {
      const on = !S.hiddenScn.has(s.code);
      const color = scenarioColor(s, d.setups);
      return el("button", {
        type: "button", class: "toggle scn-chip", title: s.label || s.code,
        "aria-pressed": on ? "true" : "false",
        style: on ? `color:${color};border-color:${color}` : "",
        on: { click: () => toggleScenario(s.code) },
      }, String(s.label || s.code).split(" · ")[0]);
    }));
  }

  function priceFormat(candles) {
    const price = candles.length ? candles[candles.length - 1][4] : 1000;
    const precision = price >= 1000 ? 1 : price >= 10 ? 2 : price >= 1 ? 3 : 5;
    return { type: "price", precision, minMove: Math.pow(10, -precision) };
  }

  // TM - #DASH-LAYOUT - Khung nhin mac dinh: dung cho lan ve dau va nut Reset zoom
  function defaultRange() {
    const st = S.chart;
    if (!st) return null;
    const d = st.data;
    const candles = d.candles;
    const shown = Math.min(Number(BOOT.default_bars) || 96, candles.length || 1);
    const edge = st.barCount - 1 + 4;
    if (S.mode === "review" && candles.length) {
      const first = candles.findIndex((c) => c[0] >= d.day_start_ms);
      const from = Math.max(0, (first < 0 ? 0 : first) - Math.round(shown / 4));
      return { from, to: Math.max(from + 20, st.barCount + 2) };
    }
    return { from: Math.max(0, edge - shown - st.whitespace), to: edge };
  }

  function resetZoom() {
    const range = defaultRange();
    if (!range) return;
    chart.timeScale().setVisibleLogicalRange(range);
    candleSeries.priceScale().applyOptions({ autoScale: true });
    overlay.refresh();
  }

  function renderChart(d, fit) {
    C = palette();
    const span = SPAN[d.timeframe];
    const candles = d.candles;
    const spike = new Set(d.vol_spike || []);
    const rows = [];
    const volumes = [];
    const times = new Set();
    let formingTime = null;
    candles.forEach((c) => {
      const time = c[0] / 1000;
      times.add(time);
      const up = c[4] >= c[1];
      const color = up ? C.long : C.short;
      const row = { time, open: c[1], high: c[2], low: c[3], close: c[4] };
      if (!c[6]) {
        formingTime = time;
        Object.assign(row, { color: alpha(color, 0.42), wickColor: alpha(color, 0.42) });
      }
      rows.push(row);
      volumes.push({ time, value: c[5], color: alpha(color, !c[6] ? 0.2 : spike.has(c[0]) ? 1 : 0.32) });
    });
    // Kich ban co diem trong tuong lai: can khoang trong tren truc thoi gian de ve toi do
    let whitespace = 0;
    if (candles.length) {
      const lastT = candles[candles.length - 1][0];
      const future = Math.max(lastT, ...d.scenarios.flatMap((s) => (s.points || []).map((p) => p.t)));
      for (let t = lastT + span; t <= future + span && whitespace < MAX_WHITESPACE; t += span) {
        rows.push({ time: t / 1000 });
        whitespace += 1;
      }
    }
    const scale = chart.timeScale();
    const before = fit ? null : scale.getVisibleLogicalRange();
    const wasAtEdge = before && S.chart ? before.to >= S.chart.barCount - 2 : false;
    const oldFirst = S.chart && S.chart.data.candles.length ? S.chart.data.candles[0][0] : null;

    buildOverlay(d);
    renderScnChips(d);  // TM - #SCN-TOGGLE - Bat tat duong kich ban
    candleSeries.applyOptions({ priceFormat: priceFormat(candles) });
    candleSeries.setData(rows);
    volumeSeries.setData(volumes);
    candleSeries.setMarkers(buildMarkers(d, times, formingTime));
    drawPriceLines(d);
    S.chart = { data: d, barCount: rows.length, whitespace };

    const edge = rows.length - 1 + 4;
    if (fit) {
      scale.setVisibleLogicalRange(defaultRange());
    } else if (wasAtEdge) {
      const width = before.to - before.from;
      scale.setVisibleLogicalRange({ from: edge - width, to: edge });
    } else if (before) {
      // Giu nguyen cho dang xem: co nen moi thi cua so 500 nen truot, bu dung so nen da truot
      const shift = oldFirst !== null && candles.length ? (oldFirst - candles[0][0]) / span : 0;
      scale.setVisibleLogicalRange({ from: before.from + shift, to: before.to + shift });
    }
    overlay.refresh();
    // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR: du lieu moi thi lam moi popover dang mo
    if (S.popover) {
      const open = S.popover;
      requestAnimationFrame(() => (S.markerIndex[open] ? openPopover(open) : hidePopover()));
    }

    const notes = [];
    if (!candles.length) notes.push(`Chưa có nến ${TF_LABEL[d.timeframe]} của ${d.symbol} trong kho.`);
    if (d.plan.source === "origin") notes.push(`Kế hoạch gốc, ghép từ R${d.plan.revisions_used.join(", R")}.`);
    if (d.plan.source === "revision") notes.push(`Đang xem kế hoạch revision ${d.plan.revision}.`);
    if (S.mode === "review" && d.plan.source === "live") notes.push("Ngày này chưa có snapshot, đang vẽ kế hoạch hiện tại.");
    setChartNote(notes.join(" "));
  }

  function setChartNote(text) {
    const box = $("chartNote");
    box.textContent = text;
    box.title = text;
  }

  // TM - #DASH-LAYOUT - Click setup (sidebar, bang, panel): zoom toi vung setup, vien dam 2 giay
  let flashTimer = null;
  function flashZone(code) {
    S.flash = code;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { S.flash = null; overlay.refresh(); }, 2000);
  }

  function zoomToSetup(code) {
    const st = S.chart;
    const candles = st ? st.data.candles : [];
    if (!candles.length) return;
    const rows = S.setupRows ? S.setupRows.rows : [];
    const setup = st.data.setups.find((s) => s.code === code) || rows.find((s) => s.code === code);
    if (!setup) return;
    const indexAt = (ms) => {
      const i = candles.findIndex((c) => c[0] >= ms);
      return i < 0 ? candles.length - 1 : i;
    };
    const marks = (st.data.markers || []).filter((m) => m.code === code);
    let from = indexAt(setup.created_ms ?? candles[0][0]);
    let to = TERMINAL.includes(setup.status) && marks.length
      ? Math.max(from, indexAt(marks[marks.length - 1].t)) : candles.length - 1;
    if (to - from < 30) {
      const mid = (from + to) / 2;
      from = mid - 15;
      to = mid + 15;
    }
    const pad = Math.max(4, Math.round((to - from) * 0.15));
    chart.timeScale().setVisibleLogicalRange({ from: from - pad, to: Math.min(st.barCount + 3, to + pad) });
    candleSeries.priceScale().applyOptions({ autoScale: true });
  }

  // Chart dang khuat (cuon xuong bang duoi, hay panel full page che tren dien thoai) thi dua chart ra
  function revealChart() {
    if (FULL) {
      if (window.innerWidth < 768) setPanel(false);
      return;
    }
    const box = $("chartPanel").getBoundingClientRect();
    const top = $("bar").getBoundingClientRect().bottom;
    if (box.top < top - 1 || box.bottom > window.innerHeight + 1) {
      $("chartPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function focusSetup(setup) {
    S.selected = S.selected === setup.code ? null : setup.code;
    document.querySelectorAll("[data-code]").forEach((card) => card.classList.toggle("on", card.dataset.code === S.selected));
    if (!S.chart) return;
    drawPriceLines(S.chart.data);
    if (S.selected) {
      zoomToSetup(setup.code);
      flashZone(setup.code);
      revealChart();
    }
    overlay.refresh();
  }

  // ---------------------------------------------------------------- the tom tat

  function tile(key, value, sub, cls) {
    return el("div", { class: "tile" + (cls && cls.clamp ? " clamp" : "") },
      el("span", { class: "k" }, key),
      el("span", { class: "v " + ((cls && cls.tone) || "") }, value),
      sub ? el("span", { class: "s", title: sub }, sub) : null);
  }

  const biasTone = (bias) => (bias ? { long: "long", short: "short" }[bias.direction] || "neutral" : "neutral");
  const biasWord = (bias) => (bias ? { long: "LONG", short: "SHORT", neutral: "ĐỨNG NGOÀI" }[bias.direction] || bias.direction : "Chưa có bias");

  function renderSummary(d) {
    const box = $("summary");
    if (!box) return;
    const bias = d.bias;
    const word = biasWord(bias);
    const tiles = [
      tile("Bias", bias && bias.phase ? `${word} · Pha ${bias.phase}` : word, bias ? bias.summary : null, { tone: biasTone(bias), clamp: true }),
      tile("Giá M5 đã đóng", d.last_m5 ? num(d.last_m5.c) : "—", d.last_m5 ? `Nến ${hm(d.last_m5.t)}` : "Chưa có nến M5"),
    ];
    if (OWNER && S.mode === "live") tiles.push(positionTile(d.positions));
    box.replaceChildren(...tiles);
  }

  function positionTile(block) {
    if (!block || !block.enabled) return tile("Vị thế mở", "—", "Chưa bật đọc tài khoản sàn");
    if (block.error) return tile("Vị thế mở", "—", "Không đọc được từ sàn");
    const rows = block.positions || [];
    if (!rows.length) return tile("Vị thế mở", "Không có", null);
    const summary = rows.map((p) => `${p.side} ${num(p.size, 4)} @ ${num(p.entry_price)}`).join(" · ");
    if (block.pnl_hidden) return tile("Vị thế mở", "••••", summary);
    const pnl = Number(block.unrealized_pnl);
    return tile("PnL chưa chốt", signed(pnl), summary, { tone: pnl > 0 ? "long" : pnl < 0 ? "short" : "neutral" });
  }

  // ---------------------------------------------------------------- the TR
  // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR

  function duration(ms) {
    const minutes = Math.max(0, Math.round(ms / 60000));
    const days = Math.floor(minutes / 1440);
    const hours = Math.floor((minutes % 1440) / 60);
    if (days) return `${days} ngày${hours ? ` ${hours} giờ` : ""}`;
    if (hours) return `${hours} giờ${minutes % 60 ? ` ${minutes % 60} phút` : ""}`;
    return `${minutes % 60} phút`;
  }

  // Cuon bieu do toi nen cua event va mo popover neu marker dang hien
  function focusEvent(event) {
    if (!S.chart) return;
    const d = S.chart.data;
    const span = SPAN[d.timeframe];
    const marker = (d.markers || []).find((m) => m.kind === "wyckoff" && m.event_id === event.id);
    const target = marker ? marker.t_bar : Math.floor(event.t / span) * span;
    const index = d.candles.findIndex((c) => c[0] === target);
    if (index < 0) {
      setChartNote(`Nến của ${event.label} (${full(event.t)}) nằm ngoài cửa sổ đang tải — đổi sang khung lớn hơn.`);
      return;
    }
    const scale = chart.timeScale();
    const range = scale.getVisibleLogicalRange();
    const width = range ? range.to - range.from : 80;
    scale.setVisibleLogicalRange({ from: index - width / 2, to: index + width / 2 });
    revealChart();  // TM - #DASH-LAYOUT
    requestAnimationFrame(() => { if (S.markerIndex["w:" + event.id]) openPopover("w:" + event.id); });
  }

  function eventRow(event) {
    const tentative = event.status === "tentative";
    return el("li", {
      class: `tr-ev ${event.status || ""}`, tabIndex: 0, role: "button",
      "aria-label": `${event.label} ${full(event.t)}`,
      on: {
        click: () => focusEvent(event),
        keydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); focusEvent(event); } },
      },
    },
    el("time", {}, full(event.t)),
    el("div", {}, el("span", { class: "name" }, event.label + (tentative ? "?" : "")),
      el("span", { class: "sub" }, `${num(event.price)} · vol ${ratio(event.vol_ratio)}`)),
    el("span", { class: "pill " + (event.status === "confirmed" ? "go" : event.status === "failed" ? "stop" : "") },
      EVENT_STATUS_LABEL[event.status] || event.status || "—"));
  }

  // TM - #DASH-LAYOUT - Danh sach event theo pha, chia trang khi > 100 event
  function eventList(tr, key, rerender) {
    const shown = (tr.events || []).filter((e) => S.layers.failed || e.status !== "failed");
    const hidden = (tr.events || []).length - shown.length;
    const tail = [];
    if (!shown.length) tail.push(el("li", { class: "empty" }, "Chưa có event."));
    if (hidden) tail.push(el("li", { class: "empty" }, `Ẩn ${hidden} event hỏng — bật "Hiện event hỏng" để xem.`));
    if (!shown.length) return el("ol", { class: "tr-events" }, tail);
    return el("div", {}, paged(key, shown, (rows) => {
      const items = [];
      let group;
      rows.forEach((event) => {
        const phase = event.phase || "";
        if (phase !== group) {
          group = phase;
          items.push(el("li", { class: "tr-group" }, phase ? `Pha ${phase}` : "Ngoài pha"));
        }
        items.push(eventRow(event));
      });
      return el("ol", { class: "tr-events" }, items, tail);
    }, rerender));
  }

  function changeList(changes) {
    if (!changes) return empty("Đang tải thay đổi…");
    const rows = [];
    if (changes.redrawn) rows.push(el("li", { class: "tr-group" }, "TR đã vẽ lại trong ngày"));
    if (changes.added.length) rows.push(el("li", { class: "tr-group" }, "Event thêm"), ...changes.added.map(eventRow));
    if (changes.status_changed.length) {
      rows.push(el("li", { class: "tr-group" }, "Đổi trạng thái"),
        ...changes.status_changed.map((e) => eventRow({ ...e, label: `${e.label}: ${EVENT_STATUS_LABEL[e.from] || e.from} → ${EVENT_STATUS_LABEL[e.to] || e.to}` })));
    }
    if (changes.removed.length) rows.push(el("li", { class: "tr-group" }, "Không còn"), ...changes.removed.map(eventRow));
    if (!rows.length) rows.push(el("li", { class: "empty" }, "Không có event thêm hay đổi trạng thái trong ngày."));
    const span = changes.from_revision ? `R${changes.from_revision} → R${changes.to_revision}` : "chưa có snapshot";
    return el("div", {}, empty(`So sánh ${span}.`), el("ol", { class: "tr-events" }, rows));
  }

  async function loadTrChanges(key) {
    S.trLoading = key;
    let data;
    try {
      data = await api("/tr-changes", { symbol: S.symbol, date: S.date });
    } catch (err) {
      data = { error: err.message };
    }
    if (S.trLoading === key) S.trLoading = null;
    if (`${S.symbol}|${S.date}` !== key) return;
    S.trChanges = { key, data };
    if (S.tab === "events") renderEventsPane();
  }

  // TM - #TR-BRANCH - Banner nhanh: "Pha C: UTAD#1 → Nhánh giảm (phân phối)"
  function branchBanner(tr) {
    const by = tr.branch_by;
    if (!tr.branch || !by || !BRANCH_WORDS[tr.branch]) {
      return el("p", { class: "branch none" }, "Chưa xác định nhánh — TR trung tính tới khi có sự kiện pha C (không có pha C thì pha D).");
    }
    const [word, family] = BRANCH_WORDS[tr.branch];
    const source = by.by === "event" ? `Pha ${by.phase}: ${by.ref || by.type}` : `Sơ đồ ${SCHEMATIC_LABEL[by.schematic] || by.schematic}`;
    return el("p", { class: "branch " + (tr.branch === "accumulation" ? "long" : "short") }, `${source} → Nhánh ${word} (${family})`);
  }

  // TM - #TR-BRANCH - Thanh % tich luy / phan phoi
  const hasProb = (tr) => tr && tr.prob_accumulation !== null && tr.prob_accumulation !== undefined
    && tr.prob_distribution !== null && tr.prob_distribution !== undefined;

  function probBar(tr) {
    const acc = tr.prob_accumulation;
    const dist = tr.prob_distribution;
    const part = (cls, pct) => {
      const node = el("span", { class: cls });
      node.style.width = `${pct}%`;
      return node;
    };
    return [
      el("div", { class: "prob-legend" }, el("span", { class: "long" }, `Tích luỹ ${acc}%`), el("span", { class: "short" }, `Phân phối ${dist}%`)),
      el("div", { class: "prob-bar", role: "img", "aria-label": `Tích luỹ ${acc}%, phân phối ${dist}%` }, part("acc", acc), part("dist", dist)),
    ];
  }

  // Danh sach bang chung va dien bien trong ngay (tab Su kien Wyckoff)
  function probBlock(tr, history) {
    if (!hasProb(tr)) return el("p", { class: "empty prob" }, "Chưa có điểm bằng chứng tích luỹ / phân phối.");
    const items = (tr.prob_evidence || []).map((e) => el("li", {},
      el("span", {}, (BOOT.prob_factors || {})[e.factor] || e.factor),
      el("span", { class: "pill " + (e.side === "acc" ? "long" : "short") }, e.side === "acc" ? "tích luỹ" : "phân phối"),
      el("span", { class: "w" }, `+${num(e.weight, 2)}`),
      e.note ? el("span", { class: "note" }, e.note) : null));
    const moves = history.filter((h) => h.prob_accumulation !== null && h.prob_accumulation !== undefined);
    const trail = moves.length > 1 ? el("p", { class: "prob-caption" }, "Trong ngày: " + moves.map((h) =>
      `${hm(h.at)} TL ${h.prob_accumulation}% / PP ${h.prob_distribution}%${h.plan_revision ? ` (R${h.plan_revision})` : ""}`).join(" → ")) : null;
    return el("div", { class: "prob" }, probBar(tr),
      items.length ? el("ul", { class: "prob-list" }, items) : null,
      el("p", { class: "prob-caption" }, "Điểm bằng chứng, chưa hiệu chỉnh thống kê."),
      trail);
  }

  // TM - #DASH-LAYOUT - Tom tat TR o sidebar: so do, pha, nhanh, xac suat TL/PP, bien TR
  function renderTr(d) {
    renderFullBar(d);
    const box = $("trCard");
    if (!box) return;
    const tr = d.tr;
    if (!tr) {
      box.replaceChildren(empty(`Chưa có TR khoá cho ${S.symbol}.`));
      return;
    }
    const family = FAMILY[tr.schematic];
    const current = (tr.phases || []).find((p) => p.phase === tr.current_phase && (p.end_t === null || p.end_t === undefined));
    const until = S.mode === "live" ? Date.now() : Math.min(Date.now(), d.day_start_ms + 86400000);
    const head = el("div", { class: "tr-head" },
      el("span", { class: "pill " + (family === "acc" ? "long" : family === "dist" ? "short" : "") },
        `${SCHEMATIC_LABEL[tr.schematic] || tr.schematic} · ${TR_STATUS_LABEL[tr.status] || tr.status}`),
      tr.current_phase ? el("span", { class: "pill run" },
        `Pha ${tr.current_phase}${current ? ` · ${duration(until - current.start_t)}` : ""}`) : null,
      tr.source === "setup" ? el("span", { class: "pill warn" }, "Khai trong setup") : null);
    const levels = tr.levels || {};
    const facts = el("dl", { class: "facts" },
      fact("TR high", num(tr.tr_high)), fact("TR low", num(tr.tr_low)),
      levels.creek ? fact("Creek", num(levels.creek)) : null, levels.ice ? fact("Ice", num(levels.ice)) : null,
      fact("Từ", tr.since ? full(tr.since) : "—", true));
    // TM - #TR-BRANCH - loc null: replaceChildren(null) in ra chu "null"
    box.replaceChildren(...[head, branchBanner(tr),
      hasProb(tr) ? el("div", { class: "prob" }, probBar(tr)) : empty("Chưa có xác suất tích luỹ / phân phối."),
      facts].filter(Boolean));
  }

  // TM - #DASH-LAYOUT - Thanh manh full page: pha, xac suat TL/PP, ngay dang xem
  function renderFullBar(d) {
    if (!FULL) return;
    const tr = d.tr;
    const phase = $("fPhase");
    const branch = tr && tr.branch && BRANCH_WORDS[tr.branch] ? ` · nhánh ${BRANCH_WORDS[tr.branch][0]}` : "";
    phase.hidden = !(tr && tr.current_phase);
    phase.textContent = tr && tr.current_phase ? `Pha ${tr.current_phase}${branch}` : "";
    const prob = $("fProb");
    prob.hidden = !hasProb(tr);
    prob.replaceChildren(...(hasProb(tr) ? [el("span", { class: "long" }, `TL ${tr.prob_accumulation}%`), " / ",
      el("span", { class: "short" }, `PP ${tr.prob_distribution}%`)] : []));
    const day = $("fDate");
    const past = S.date !== BOOT.today || S.mode === "review";
    day.hidden = !past;
    day.textContent = past ? `${S.date.split("-").reverse().join("/")}${S.mode === "review" ? " · Review" : ""}` : "";
    document.title = `${S.symbol} ${TF_LABEL[S.tf] || S.tf} · BAMCP Chart`;
  }

  // ---------------------------------------------------------------- the setup

  function distanceText(setup, price) {
    if (price === null || price === undefined) return "—";
    if (price >= setup.zone_low && price <= setup.zone_high) return "Trong vùng";
    const gap = price > setup.zone_high ? price - setup.zone_high : setup.zone_low - price;
    return `${num(gap)} (${num((gap / price) * 100, 2)}%)`;
  }

  function fact(key, value, wide) {
    return el("div", { class: wide ? "wide" : null }, el("dt", {}, key), el("dd", {}, value));
  }

  // TM - #DASH-LAYOUT - The gon o sidebar / panel full page: click -> zoom chart toi vung setup
  function setupMini(setup, price) {
    const dir = setup.direction === "long" ? "long" : "short";
    const done = TERMINAL.includes(setup.status);
    const go = () => focusSetup(setup);
    return el("article", {
      class: `setup mini${S.selected === setup.code ? " on" : ""}${done ? " done" : ""}${setup.status === "PRUNED" ? " pruned" : ""}`,
      "data-code": setup.code, tabIndex: 0, role: "button",
      "aria-label": `${setup.label || setup.code} — xem trên biểu đồ`,
      on: { click: go, keydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } } },
    },
    el("div", { class: "setup-head" },
      el("span", { class: "mini-name" }, setup.label || letter(setup.code)),
      el("span", { class: "pill " + dir }, String(setup.direction).toUpperCase()),
      el("span", { class: "pill " + (STATUS_CLASS[setup.status] || "") }, setup.status)),
    el("div", { class: "mini-line" },
      `Vùng ${num(setup.zone_low)}–${num(setup.zone_high)} · E ${num(setup.entry)} · SL ${num(setup.stop)} · TP ${num(setup.target)}`),
    price !== undefined && S.mode === "live" && !done ? el("div", { class: "mini-sub" }, `Cách vùng: ${distanceText(setup, price)}`) : null);
  }

  function renderSideSetups(rows, price) {
    const box = $("sideSetups");
    if (!box) return;
    const shown = shownSetups(rows);
    const active = shown.filter((s) => !TERMINAL.includes(s.status));
    const done = shown.filter((s) => TERMINAL.includes(s.status));
    const pruned = rows.length - shown.length;
    const items = [];
    if (!active.length) items.push(empty(rows.length ? "Không có setup đang chạy." : `Không có setup cho ${S.symbol} ngày ${S.date}.`));
    items.push(...active.map((s) => setupMini(s, price)));
    if (done.length) {
      items.push(el("details", { class: "mini-done", open: S.doneOpen, on: { toggle: (e) => { S.doneOpen = e.target.open; } } },
        el("summary", {}, `Đã kết thúc (${done.length})`), done.map((s) => setupMini(s, price))));
    }
    if (pruned) items.push(el("p", { class: "mini-sub" }, `Ẩn ${pruned} setup đã loại — bật lớp "Setup đã PRUNED" để xem.`));
    box.replaceChildren(...items);
  }

  function renderSetups(rows, price) {
    const box = $("setups");
    // TM - #TR-BRANCH - Setup PRUNED an mac dinh, nut "Hien setup da loai (n)"
    const pruned = rows.filter((s) => s.status === "PRUNED").length;
    const toggle = $("showPruned");
    toggle.hidden = !pruned;
    toggle.textContent = `${S.layers.pruned ? "Ẩn" : "Hiện"} setup đã loại (${pruned})`;
    toggle.setAttribute("aria-pressed", S.layers.pruned ? "true" : "false");
    const shown = shownSetups(rows);
    if (!shown.length) {
      box.replaceChildren(empty(pruned
        ? `Không còn setup theo dõi cho ${S.symbol} ngày ${S.date} — ${pruned} setup đã loại vì ngược nhánh TR.`
        : `Không có setup cho ${S.symbol} ngày ${S.date}.`));
      return;
    }
    box.replaceChildren(...paged("setups", shown, (slice) => el("div", { class: "setup-grid" }, slice.map((setup) => setupCard(setup, price))),
      () => renderSetups(rows, price)));
  }

  function setupCard(setup, price) {
    const dir = setup.direction === "long" ? "long" : "short";
    const invalid = num(setup.invalidate_level) + (setup.invalidate_after_confirm
      ? (setup.state && setup.state.confirm_reached ? " (đã confirm)" : " (sau confirm)") : "");
    const trigger = setup.trigger_type + (setup.confirm_type ? ` → ${setup.confirm_type} ${num(setup.confirm_level)}` : "");
    const counter = setup.htf_counter && setup.htf_counter.length
      ? el("span", { class: "pill warn" }, `NGƯỢC ${setup.htf_counter.join(", ")}`) : null;
    const note = setup.note ? el("details", {
      open: S.openNotes.has(setup.code),
      on: {
        click: (e) => e.stopPropagation(),
        toggle: (e) => { if (e.target.open) S.openNotes.add(setup.code); else S.openNotes.delete(setup.code); },
      },
    }, el("summary", {}, "Ghi chú"), el("p", {}, setup.note)) : null;
    // TM - #WYCKOFF-TR - Wyckoff phases and events on locked TR: "Pha C · giao dich TEST 3 · ky vong LPS"
    const wyckoff = [setup.phase ? `Pha ${setup.phase}` : null,
      setup.event_ref ? `giao dịch ${String(setup.event_ref).replace("#", " ")}` : null,
      setup.expects ? `kỳ vọng ${setup.expects}` : null].filter(Boolean).join(" · ");
    // TM - #TR-BRANCH - Ten tuong minh lam tieu de, ly do loai voi setup PRUNED
    const pruned = setup.status === "PRUNED" ? (setup.state || {}).pruned || {} : null;
    return el("article", {
      class: `setup${S.selected === setup.code ? " on" : ""}${TERMINAL.includes(setup.status) ? " done" : ""}${pruned ? " pruned" : ""}`,
      "data-code": setup.code,
      on: { click: () => focusSetup(setup) },
    },
    setup.label ? el("div", { class: "setup-label" }, setup.label) : null,
    el("div", { class: "setup-head" },
      el("span", { class: "setup-code" }, setup.code),
      el("span", { class: "pill " + dir }, setup.direction.toUpperCase()),
      el("span", { class: "pill " + (STATUS_CLASS[setup.status] || "") }, setup.status),
      counter),
    el("dl", { class: "facts" },
      wyckoff ? fact("Wyckoff", wyckoff, true) : null,
      fact("Trigger", trigger, true),
      fact("Vùng", `${num(setup.zone_low)} – ${num(setup.zone_high)}`, true),
      fact("Entry", num(setup.entry)),
      fact("R:R", setup.rr === null || setup.rr === undefined ? "—" : num(setup.rr, 2)),
      fact("SL", num(setup.stop)),
      fact("TP", num(setup.target)),
      fact("Điều kiện huỷ", invalid, true),
      S.mode === "live" ? fact("Cách vùng", distanceText(setup, price), true) : null),
    pruned ? el("p", { class: "pruned-reason" },
      `Đã loại: ${pruned.reason || "ngược nhánh TR"}${pruned.at_ms ? ` · ${full(pruned.at_ms)}` : ""}`) : null,
    note,
    S.mode === "review" ? reviewBox(setup) : null);
  }

  function reviewBox(setup) {
    const review = setup.review || {};
    const auto = el("div", { class: "review-auto" }, `Hệ thống tính: ${review.auto_outcome || "chưa kết luận"}`);
    const stop = { on: { click: (e) => e.stopPropagation() } };
    if (!OWNER) {
      return el("div", { class: "review", ...stop }, auto,
        el("div", { class: "review-auto" }, `Kết quả: ${review.outcome || "—"}` +
          (review.r_multiple !== null && review.r_multiple !== undefined ? ` · R ${num(review.r_multiple, 2)}` : "")),
        review.note ? el("div", { class: "review-auto" }, review.note) : null);
    }
    const outcome = el("select", { "aria-label": `Kết quả ${setup.code}` },
      el("option", { value: "" }, "Kết quả…"),
      BOOT.outcomes.map((name) => el("option", { value: name, selected: name === review.outcome }, name)));
    const rValue = el("input", {
      type: "number", step: "0.01", placeholder: "R thực tế", "aria-label": `R thực tế ${setup.code}`,
      value: review.r_multiple === null || review.r_multiple === undefined ? "" : String(review.r_multiple),
    });
    const noteBox = el("textarea", { placeholder: "Ghi chú", "aria-label": `Ghi chú review ${setup.code}`, value: review.note || "" });
    const message = el("span", { class: "review-msg" }, review.reviewed_at ? `Đã lưu ${review.reviewed_at} · ${review.reviewed_by}` : "");
    const save = el("button", {
      type: "button",
      on: {
        click: async (e) => {
          e.target.disabled = true;
          message.className = "review-msg";
          message.textContent = "Đang lưu…";
          try {
            const out = await api("/reviews", null, {
              method: "POST",
              headers: { "Content-Type": "application/json", "X-CSRF-Token": BOOT.csrf },
              body: JSON.stringify({ setup_code: setup.code, outcome: outcome.value || null,
                                     r_multiple: rValue.value === "" ? null : rValue.value, note: noteBox.value }),
            });
            setup.review = out.review;
            message.textContent = `Đã lưu ${out.review.reviewed_at} · ${out.review.reviewed_by}`;
          } catch (err) {
            message.className = "review-msg err";
            message.textContent = err.message;
          } finally { e.target.disabled = false; }
        },
      },
    }, "Lưu");
    return el("div", { class: "review", ...stop }, auto,
      el("div", { class: "review-row" }, outcome, rValue), noteBox,
      el("div", { class: "review-save", style: "margin-top:8px" }, save, message));
  }

  // ---------------------------------------------------------------- timeline, lenh, thong ke

  function renderTimeline(events, snapshots) {
    const rows = events.map((e) => ({
      at: e.at,
      main: `${e.code ? letter(e.code) + " · " : ""}${e.from_status || e.to_status ? `${e.from_status || "—"} → ${e.to_status || "—"}` : SYSTEM_EVENT_LABEL[e.event] || e.event}`,
      sub: [e.from_status || e.to_status ? e.event : null, e.price !== null && e.price !== undefined ? `giá ${num(e.price)}` : null,
            e.notify === "sent" ? "đã gửi tin" : e.notify === "error" ? "lỗi gửi tin" : null].filter(Boolean).join(" · "),
      fail: e.notify === "error",
    })).concat(snapshots.map((s) => ({
      at: s.created_at, main: `Kế hoạch R${s.revision}`, sub: TRIGGER_LABEL[s.trigger] || s.trigger, fail: false,
    })));
    rows.sort((a, b) => b.at - a.at);
    const box = $("timeline");
    if (!rows.length) {
      box.replaceChildren(empty("Chưa có sự kiện nào trong ngày."));
      return;
    }
    box.replaceChildren(...paged("timeline", rows, (slice) => el("ol", { class: "timeline" }, slice.map((row) => el("li", {},
      el("time", {}, hm(row.at)),
      el("div", {}, el("div", {}, row.main),
        row.sub ? el("div", { class: "sub" + (row.fail ? " fail" : "") }, row.sub) : null)))),
    () => renderTimeline(events, snapshots)));
  }

  function renderFills(block) {
    const panel = $("fillsPanel");
    panel.hidden = !(OWNER && S.mode === "review");
    if (panel.hidden) return;
    const box = $("fills");
    if (!block || !block.enabled) { box.replaceChildren(empty("Chưa bật đọc tài khoản sàn.")); return; }
    if (block.error) { box.replaceChildren(empty("Không đọc được lệnh từ sàn.")); return; }
    if (!block.positions.length) { box.replaceChildren(empty("Không có lệnh nào trong ngày.")); return; }
    const hide = !("realized_pnl" in block.positions[0]);
    box.replaceChildren(...paged("fills", block.positions, (rows) => el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Mở lúc", "Hướng", "Giá vào", "KL", hide ? null : "PnL", "Setup"].filter(Boolean).map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.map((p) => el("tr", {},
        el("td", {}, p.carried_in ? "Từ hôm trước" : String(p.opened_at || "").slice(11, 16)),
        el("td", { class: p.side === "long" ? "long" : "short" }, p.side),
        el("td", {}, num(p.entry_price)),
        el("td", {}, num(p.qty, 4)),
        hide ? null : el("td", {}, num(p.realized_pnl, 2)),
        el("td", {}, p.setup_code ? letter(p.setup_code) : el("span", { class: "pill warn" }, "Không gắn setup"))))))),
    () => renderFills(block)));
  }

  // TM - #OFF-PLAN - Bang lenh ngoai ke hoach trong ngay (moi cap)
  const REVIEW_LABEL = { PENDING: "chưa đánh giá", REVIEWED: "đã đánh giá" };

  function renderOffPlan(block) {
    const box = $("offPlan");
    if (!block) { box.replaceChildren(empty("Không đọc được lệnh ngoài kế hoạch.")); return; }
    if (!block.trades.length) {
      box.replaceChildren(empty(block.enabled ? "Không có lệnh ngoài kế hoạch trong ngày." : "position_watcher đang tắt."));
      return;
    }
    const hide = !("realized_pnl" in block.trades[0]);
    box.replaceChildren(...paged("offplan", block.trades, (rows) => el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Mã", "Cặp", "Hướng", "Phân loại", "Cờ", hide ? null : "PnL", "Review"].filter(Boolean).map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.map((t) => el("tr", { title: t.reason || "" },
        el("td", {}, el("div", {}, String(t.code).replace(/^OP-\d{8}-/, "")), el("div", { class: "sub" }, hm(t.opened_ms))),
        el("td", {}, t.symbol),
        el("td", { class: t.side === "long" ? "long" : "short" }, t.side),
        el("td", {}, el("span", { class: "pill " + (t.classification === "IN_PLAN" ? "warn" : "offplan") }, t.classification)),
        el("td", { class: "flags" }, (t.flags || []).join(" · ") || "—"),
        hide ? null : el("td", {}, t.closed ? num(t.realized_pnl, 2) : "đang mở"),
        el("td", {}, t.verdict || REVIEW_LABEL[t.review_status] || t.review_status)))))),
    () => renderOffPlan(block)));
  }

  function renderStats(data) {
    const box = $("stats");
    if (!data.total.setups) {
      box.replaceChildren(empty(`Không có setup nào từ ${data.from} đến ${data.to}.`));
      return;
    }
    const pct = (value) => (value === null || value === undefined ? "—" : `${num(value, 1)}%`);
    const line = (row, cls) => el("tr", { class: cls || null },
      el("td", {}, row.key === "total" ? "Tổng" : row.key),
      el("td", {}, String(row.setups)), el("td", {}, pct(row.trigger_rate)),
      el("td", {}, String(row.closed)), el("td", {}, pct(row.win_rate)),
      el("td", {}, row.avg_r === null || row.avg_r === undefined ? "—" : num(row.avg_r, 2)));
    box.replaceChildren(
      empty(`${data.symbol || "Mọi cặp"} · ${data.from} đến ${data.to}. Win rate tính trên lệnh đã đóng.`),
      el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, ["Nhóm", "Setup", "Tỉ lệ trigger", "Đã đóng", "Win rate", "R TB"].map((h) => el("th", {}, h)))),
        el("tbody", {}, data.groups.map((row) => line(row)), line(data.total, "total")))));
  }

  async function loadStats() {
    const end = new Date(BOOT.today + "T00:00:00Z");
    const start = new Date(end.getTime() - (S.range - 1) * 86400000);
    try {
      renderStats(await api("/review-stats", {
        symbol: S.groupBy === "symbol" ? "" : S.symbol,
        from: start.toISOString().slice(0, 10), to: BOOT.today, group_by: S.groupBy,
      }));
    } catch (err) {
      $("stats").replaceChildren(empty(err.message));
      throw err;
    }
  }

  // ---------------------------------------------------------------- tab duoi chart (trang chinh)
  // TM - #DASH-LAYOUT - Chi ve tab dang mo. Tab can API rieng (timeline, lenh ngoai ke hoach, thong ke) chi goi
  // khi tab do mo; moi lan lam moi xoa du lieu cu cua tab, mo lai tab thi tai lai.

  const tabVisible = (tab) => (tab === "offplan" ? OWNER : tab === "stats" ? S.mode === "review" : true);
  const loadingText = () => empty("Đang tải…");

  function renderSetupsPane() {
    if (S.setupRows) renderSetups(S.setupRows.rows, S.setupRows.price);
    else if (!$("setups").childElementCount) $("setups").replaceChildren(loadingText());
    const timeline = S.lists.timeline;
    if (timeline && timeline.error) $("timeline").replaceChildren(empty(timeline.error));
    else if (timeline) renderTimeline(timeline.events, timeline.snapshots);
    else if (!$("timeline").childElementCount) $("timeline").replaceChildren(loadingText());
  }

  function renderEventsPane() {
    const d = S.chart && S.chart.data;
    if (!d) { $("eventList").replaceChildren(loadingText()); return; }
    const tr = d.tr;
    const review = S.mode === "review";
    // replaceChildren(null) in ra chu "null": loc truoc
    $("eventTools").replaceChildren(...[
      el("label", { class: "check" }, el("input", {
        type: "checkbox", checked: S.layers.failed, on: { change: (e) => setLayer("failed", e.target.checked) },
      }), " Hiện event hỏng"),
      review && tr ? el("button", {
        type: "button", class: "toggle", "aria-pressed": S.trFilter ? "true" : "false",
        on: { click: () => { S.trFilter = !S.trFilter; if (S.trFilter) S.trChanges = null; renderEventsPane(); } },
      }, "Thay đổi TR trong ngày") : null].filter(Boolean));
    if (!tr) {
      $("eventList").replaceChildren(empty(`Chưa có TR khoá cho ${S.symbol}.`));
      $("evidence").replaceChildren(empty("Chưa có TR."));
      return;
    }
    const key = `${S.symbol}|${S.date}`;
    if (review && S.trFilter && (!S.trChanges || S.trChanges.key !== key) && S.trLoading !== key) loadTrChanges(key);
    const changes = S.trChanges && S.trChanges.key === key ? S.trChanges.data : null;
    $("eventList").replaceChildren(review && S.trFilter
      ? (changes && changes.error ? empty(changes.error) : changeList(changes))
      : eventList(tr, "events", renderEventsPane));
    $("evidence").replaceChildren(...[probBlock(tr, d.prob_history || []),
      tr.redraw_rule ? el("p", { class: "tr-text" }, el("b", {}, "Vẽ lại khi: "), tr.redraw_rule) : null,
      tr.note ? el("p", { class: "tr-text" }, el("b", {}, "Nhận định: "), tr.note) : null].filter(Boolean));
  }

  function renderPlanPane() {
    const d = S.chart && S.chart.data;
    if (!d) { $("biasBox").replaceChildren(loadingText()); return; }
    const bias = d.bias;
    if (!bias) {
      $("biasBox").replaceChildren(empty(`Chưa có bias cho ${S.symbol} ngày ${S.date}.`));
    } else {
      const levels = Object.entries(bias.key_levels || {});
      const updated = typeof bias.updated_at === "number" ? full(bias.updated_at)
        : String(bias.updated_at || "").replace("T", " ").slice(0, 16);
      $("biasBox").replaceChildren(...[
        el("p", { class: "bias-dir " + biasTone(bias) }, biasWord(bias) + (bias.phase ? ` · Pha ${bias.phase}` : "")),
        bias.summary ? el("p", { class: "tr-text" }, bias.summary) : null,
        el("dl", { class: "facts" },
          bias.action_zone ? fact("Vùng hành động", bias.action_zone, true) : null,
          updated ? fact("Cập nhật", updated, true) : null,
          levels.map(([name, price]) => fact(name, num(price)))),
      ].filter(Boolean));
    }
    const list = d.scenarios || [];
    if (!list.length) { $("scenarioBox").replaceChildren(empty("Không có kịch bản đang hiệu lực.")); return; }
    $("scenarioBox").replaceChildren(el("ul", { class: "scn-list" }, list.map((s) => {
      const drawable = s.points && s.points.length >= 2;
      const on = drawable && !S.hiddenScn.has(s.code);
      const color = drawable ? scenarioColor(s, d.setups) : C.neutral;
      const dot = el("span", { class: "scn-dot" });
      dot.style.background = color;
      return el("li", {},
        dot,
        el("div", {},
          el("div", { class: "scn-label" }, s.label || s.code),
          el("div", { class: "sub" }, [s.kind === "main" ? "Kịch bản chính" : "Kịch bản phụ", s.status,
            (s.setup_codes || []).map(letter).join(", ") || null, `${(s.points || []).length} điểm`].filter(Boolean).join(" · ")),
          s.condition ? el("div", { class: "sub" }, s.condition) : null),
        drawable ? el("button", {
          type: "button", class: "toggle", "aria-pressed": on ? "true" : "false", disabled: !S.layers.scn,
          title: S.layers.scn ? "" : "Lớp Kịch bản đang tắt",
          on: { click: () => toggleScenario(s.code) },
        }, on ? "Ẩn" : "Hiện") : null);
    })));
  }

  function renderOffPlanPane() {
    const data = S.lists.offplan;
    if (!data) {
      if (!$("offPlan").childElementCount) $("offPlan").replaceChildren(loadingText());
      return;
    }
    if (data.error) { $("offPlan").replaceChildren(empty(data.error)); return; }
    renderOffPlan(data.offPlan);
    renderFills(data.fills);
  }

  function renderTab() {
    if (FULL) return;
    if (S.tab === "setups") renderSetupsPane();
    else if (S.tab === "events") renderEventsPane();
    else if (S.tab === "plan") renderPlanPane();
    else if (S.tab === "offplan") renderOffPlanPane();
  }

  function applyTab() {
    if (FULL) return;
    if (!tabVisible(S.tab)) S.tab = "setups";
    document.querySelectorAll("#tabbar [data-tab]").forEach((b) => {
      const on = b.dataset.tab === S.tab;
      b.hidden = !tabVisible(b.dataset.tab);
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });
    TABS.forEach((tab) => { $("pane-" + tab).hidden = tab !== S.tab; });
    renderTab();
  }

  // Du lieu rieng cua tab: undefined = chua tai, null = dang tai
  async function loadTabData(tab) {
    if (FULL) return;
    const gen = listGen;
    const base = { symbol: S.symbol, date: S.date };
    if (tab === "setups" && S.lists.timeline === undefined) {
      S.lists.timeline = null;
      try {
        const [events, snapshots] = await Promise.all([api("/events", base), api("/snapshots", base)]);
        if (gen !== listGen) return;
        S.lists.timeline = { events: events.events, snapshots: snapshots.snapshots };
      } catch (err) {
        if (gen !== listGen) return;
        S.lists.timeline = { error: `Không tải được timeline: ${err.message}` };
        throw err;
      } finally {
        if (gen === listGen && S.tab === "setups") renderSetupsPane();
      }
    } else if (tab === "offplan" && OWNER && S.lists.offplan === undefined) {
      S.lists.offplan = null;
      try {
        const [offPlan, fills] = await Promise.all([
          // TM - #OFF-PLAN - Lenh ngoai ke hoach: ca live lan review, chi chu tai khoan
          api("/off-plan", { date: S.date }).catch((err) => { if (err.status === 0) throw err; return null; }),
          S.mode === "review" ? api("/fills", base) : Promise.resolve(null),
        ]);
        if (gen !== listGen) return;
        S.lists.offplan = { offPlan, fills };
      } catch (err) {
        if (gen !== listGen) return;
        S.lists.offplan = { error: `Không tải được lệnh: ${err.message}` };
        throw err;
      } finally {
        if (gen === listGen && S.tab === "offplan") renderOffPlanPane();
      }
    } else if (tab === "stats" && S.mode === "review") {
      await loadStats();
    }
  }

  function selectTab(tab) {
    if (!tabVisible(tab)) return;
    S.tab = tab;
    saveTab();
    applyTab();
    loadTabData(tab).catch(() => { /* loi da ghi vao tab */ });
  }

  // ---------------------------------------------------------------- full page: panel noi

  function setPanel(open) {
    if (!FULL) return;
    $("fPanel").hidden = !open;
    $("panelBtn").setAttribute("aria-expanded", open ? "true" : "false");
    $("panelBtn").classList.toggle("on", open);
    if (open) renderPanel();
  }

  function renderPanel() {
    if (!FULL || $("fPanel").hidden) return;
    const d = S.chart && S.chart.data;
    if (!d) { $("sideSetups").replaceChildren(loadingText()); return; }
    renderSideSetups(d.setups);
    $("eventList").replaceChildren(d.tr ? eventList(d.tr, "panel-events", renderPanel) : empty(`Chưa có TR khoá cho ${S.symbol}.`));
  }

  function toggleFullscreen() {
    if (!document.fullscreenEnabled) return;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else document.documentElement.requestFullscreen().catch(() => {});
  }

  // ---------------------------------------------------------------- lop, TR, sidebar

  function setLayerMenu(open) {
    $("layerMenu").hidden = !open;
    $("layerBtn").setAttribute("aria-expanded", open ? "true" : "false");
  }

  function renderLayerMenu() {
    $("layerList").replaceChildren(...LAYERS.map(([key, label]) => el("label", { class: "check" },
      el("input", { type: "checkbox", id: "layer-" + key, checked: S.layers[key], on: { change: (e) => setLayer(key, e.target.checked) } }),
      " " + label)));
  }

  // Doi lop: ve lai chart (giu khung nhin), danh sach phu thuoc lop, va URL / link full page
  function setLayer(key, on) {
    S.layers[key] = on;
    saveLayers();
    const box = $("layer-" + key);
    if (box) box.checked = on;
    if (S.chart) renderChart(S.chart.data, false);
    renderSetupViews();
    if (!FULL && (S.tab === "events" || S.tab === "plan")) renderTab();
    syncUrl();
  }

  function renderSetupViews() {
    if (FULL) { renderPanel(); return; }
    if (!S.setupRows) return;
    renderSideSetups(S.setupRows.rows, S.setupRows.price);
    if (S.tab === "setups") renderSetups(S.setupRows.rows, S.setupRows.price);
  }

  // TM - #DASH-LAYOUT - O chon TR: chi hien khi cap co hon mot TR (hoac URL dang ghim mot TR)
  let trsReq = null;
  function loadTrs(force) {
    if (trsReq && trsReq.symbol === S.symbol) return trsReq.promise;
    if (!force && S.trs && S.trs.symbol === S.symbol) return Promise.resolve();
    const symbol = S.symbol;
    const promise = api("/trs", { symbol })
      .then((out) => { if (symbol === S.symbol) { S.trs = out; renderTrSelect(); } })
      .catch(() => { /* o chon TR la phu: loi thi giu nguyen */ })
      .finally(() => { if (trsReq && trsReq.promise === promise) trsReq = null; });
    trsReq = { symbol, promise };
    return promise;
  }

  function renderTrSelect() {
    const box = $("trSel");
    const ready = S.trs && S.trs.symbol === S.symbol;
    const list = ready ? S.trs.trs : [];
    box.hidden = list.length < 2 && !S.trId;
    const option = (t) => el("option", { value: t.id, selected: t.id === S.trId },
      `${num(t.tr_low)}–${num(t.tr_high)} · từ ${F_DM.format(t.since)} · ${t.id === S.trs.active_id ? "đang khoá" : TR_STATUS_LABEL[t.status] || t.status}`);
    box.replaceChildren(el("option", { value: "", selected: !S.trId }, "TR khoá (mặc định)"), ...list.map(option));
  }

  function applySide() {
    if (FULL) return;
    $("planView").classList.toggle("side-collapsed", S.sideCollapsed);
    $("sideToggle").setAttribute("aria-pressed", S.sideCollapsed ? "true" : "false");
    $("sideToggle").title = S.sideCollapsed ? "Mở sidebar" : "Thu sidebar";
  }

  function setMenu(open) {
    if (FULL) return;
    $("bar").classList.toggle("open", open);
    $("menuBtn").setAttribute("aria-expanded", open ? "true" : "false");
  }

  // ---------------------------------------------------------------- tai du lieu

  function renderRevisions(plan) {
    const box = $("revision");
    if (!box) return;
    box.hidden = S.mode !== "review";
    if (box.hidden) return;
    const options = [el("option", { value: "origin", selected: S.revision === "origin" }, "Kế hoạch gốc")];
    plan.revisions.forEach((r) => options.push(el("option", { value: String(r.revision), selected: String(S.revision) === String(r.revision) },
      `R${r.revision} · ${TRIGGER_LABEL[r.trigger] || r.trigger} · ${full(r.created_at)}`)));
    box.replaceChildren(...options);
  }

  // Doi khung hay revision chi tai lai chart: the setup giu nguyen de khong mat ghi chu dang go
  async function loadChart(fit) {
    const ticket = ++chartTicket;
    const review = S.mode === "review";
    let data;
    try {
      data = await api("/chart", {
        symbol: S.symbol, date: S.date, tf: S.tf, bars: MAX_BARS,
        revision: review ? S.revision : "", include_forming: review ? "" : "1",
        tr_id: S.trId,  // TM - #DASH-LAYOUT
      });
    } catch (err) {
      if (ticket !== chartTicket) return;
      // TM - #DASH-LAYOUT - URL bookmark tro toi TR / revision khong con hop le: quay ve mac dinh, khong lap loi
      if (err.status === 400 && (S.trId || (review && S.revision !== "origin"))) {
        S.trId = "";
        S.revision = "origin";
        syncUrl();
        if (S.trs) renderTrSelect();
        return loadChart(fit);
      }
      throw err;
    }
    if (ticket !== chartTicket) return;
    renderChart(data, fit);
    renderSummary(data);
    renderTr(data);  // TM - #WYCKOFF-TR - the TR theo dung revision dang xem
    renderRevisions(data.plan);
    if (FULL) renderPanel();
    else if (S.tab === "events" || S.tab === "plan") renderTab();
    $("updated").textContent = `Cập nhật ${hm(Date.now())}`;
    const fresh = data.freshness || {};
    bannerText.stale = fresh.stale ? `Dữ liệu đang trễ: nến M5 cuối cách đây ${num(fresh.m5_age_minutes, 0)} phút, nến ${TF_LABEL[S.tf]} cách đây ${num(fresh.last_closed_bar_age_minutes, 0)} phút.` : "";
    paintBanner();
    // TR moi ve trong ngay chua co trong o chon thi lay lai danh sach
    const id = data.tr && data.tr.id;
    if (id && S.trs && S.trs.symbol === S.symbol && !S.trs.trs.some((t) => t.id === id)) loadTrs(true);
  }

  async function loadLists() {
    if (FULL) return;
    const ticket = ++listTicket;
    try {
      const setups = await api("/setups", { symbol: S.symbol, date: S.date });
      if (ticket !== listTicket) return;
      S.setupRows = { rows: setups.setups, price: setups.price };
      renderSetupViews();
    } catch (err) {
      if (ticket === listTicket) throw err;
    }
  }

  // Mot lan lam moi: moi API goi dung mot lan (chart, setup, va du lieu cua tab dang mo)
  function load(fit) {
    listGen += 1;
    S.lists = {};
    return [loadChart(fit), loadLists(), loadTabData(S.tab)];
  }

  function refresh(fit) {
    clearTimeout(pollTimer);
    pollTimer = null;
    return track(load(fit));
  }

  function markButtons(root, attr, value) {
    root.querySelectorAll(`button[${attr}]`).forEach((b) => b.classList.toggle("on", b.getAttribute(attr) === String(value)));
  }

  function resetView() {
    S.selected = null;
    S.pages = {};
    S.setupRows = null;
    hidePopover();
  }

  function setMode(mode) {
    S.mode = mode;
    S.revision = "origin";
    S.trFilter = false;  // TM - #WYCKOFF-TR - bo loc thay doi TR chi co o review
    S.doneOpen = mode === "review";
    markButtons(document, "data-mode", S.mode);
    applyTab();
    syncUrl();
    refresh(true);
  }

  function setTf(tf) {
    S.tf = tf;
    if ($("tfs")) markButtons($("tfs"), "data-tf", S.tf);
    if ($("tfSel")) $("tfSel").value = S.tf;
    syncUrl();
    track(loadChart(true));
  }

  function setSymbol(symbol) {
    S.symbol = symbol;
    S.revision = "origin";
    S.trId = "";
    resetView();
    renderTrSelect();
    loadTrs();
    syncUrl();
    refresh(true);
  }

  function escape() {
    if (!$("layerMenu").hidden) { setLayerMenu(false); return; }
    if (S.popover) { hidePopover(); return; }
    if (FULL && !$("fPanel").hidden) { setPanel(false); return; }
    if (FULL && document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
    if (!FULL) setMenu(false);
  }

  // TM - #DASH-LAYOUT - Chieu cao that cua top bar va thanh tab: chart lay phan con lai cua man hinh
  function measure() {
    const root = document.documentElement.style;
    root.setProperty("--bar-h", `${$("bar").offsetHeight}px`);
    if ($("tabbar")) root.setProperty("--tabs-h", `${$("tabbar").offsetHeight}px`);
  }

  function setup() {
    const symbols = BOOT.symbols.includes(S.symbol) ? BOOT.symbols : [S.symbol, ...BOOT.symbols];
    $("symbol").replaceChildren(...symbols.map((s) => el("option", { value: s, selected: s === S.symbol }, s)));
    $("symbol").addEventListener("change", (e) => setSymbol(e.target.value));
    renderLayerMenu();
    renderTrSelect();

    // Dung chung hai trang: lop, reset zoom, chon TR, lam moi, Esc
    $("layerBtn").addEventListener("click", () => setLayerMenu($("layerMenu").hidden));
    // Chip kich ban ve lai ngay trong menu: chan click o day de menu khong tu dong
    $("layerBtn").parentElement.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", () => { if (!$("layerMenu").hidden) setLayerMenu(false); });
    $("resetZoom").addEventListener("click", resetZoom);
    $("trSel").addEventListener("change", (e) => {
      S.trId = e.target.value;
      hidePopover();
      syncUrl();
      track(loadChart(true));
    });
    $("refresh").addEventListener("click", () => refresh(false));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { escape(); return; }
      // TM - #DASH-LAYOUT - Phim tat full page: F toan man hinh, R lam moi, S panel setup, 0 reset zoom
      if (!FULL || e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
      if (e.target.closest && e.target.closest("input, select, textarea")) return;
      const key = e.key.toLowerCase();
      if (key === "f") toggleFullscreen();
      else if (key === "r") refresh(false);
      else if (key === "s") setPanel($("fPanel").hidden);
      else if (key === "0") resetZoom();
      else return;
      e.preventDefault();
    });
    // Tab an: khong goi API. Hien lai: lam moi ngay (Live, hoac dang loi ket noi)
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        clearTimeout(pollTimer);
        pollTimer = null;
        return;
      }
      if (S.mode === "live" || S.failed) refresh(false);
    });
    new ResizeObserver(measure).observe($("bar"));

    if (FULL) {
      $("tfSel").replaceChildren(...BOOT.timeframes.map((tf) => el("option", { value: tf, selected: tf === S.tf }, TF_LABEL[tf] || tf)));
      $("tfSel").addEventListener("change", (e) => setTf(e.target.value));
      $("panelBtn").addEventListener("click", () => setPanel($("fPanel").hidden));
      $("panelClose").addEventListener("click", () => setPanel(false));
      $("fsBtn").hidden = !document.fullscreenEnabled;
      $("fsBtn").addEventListener("click", toggleFullscreen);
      document.addEventListener("fullscreenchange", () => $("fsBtn").classList.toggle("on", !!document.fullscreenElement));
    } else {
      $("date").value = S.date;
      $("date").max = BOOT.today;
      $("tfs").replaceChildren(...BOOT.timeframes.map((tf) => el("button", { type: "button", "data-tf": tf }, TF_LABEL[tf] || tf)));
      $("groupBy").replaceChildren(...BOOT.group_by.map((g) => el("option", { value: g }, `Nhóm theo ${g}`)));
      markButtons(document, "data-mode", S.mode);
      markButtons($("tfs"), "data-tf", S.tf);
      markButtons($("ranges"), "data-range", S.range);
      new ResizeObserver(measure).observe($("tabbar"));

      $("date").addEventListener("change", (e) => {
        if (!e.target.value) { e.target.value = S.date; return; }
        S.date = e.target.value;
        S.revision = "origin";
        resetView();
        if (S.date < BOOT.today && S.mode === "live") setMode("review");
        else { syncUrl(); refresh(true); }
      });
      document.querySelectorAll("button[data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
      $("tfs").addEventListener("click", (e) => {
        const b = e.target.closest("button[data-tf]");
        if (b) setTf(b.dataset.tf);
      });
      $("revision").addEventListener("change", (e) => { S.revision = e.target.value; syncUrl(); track(loadChart(true)); });
      // TM - #TR-BRANCH - Hien / an setup PRUNED ca o danh sach lan tren bieu do (chung lop "Setup đã PRUNED")
      $("showPruned").addEventListener("click", () => setLayer("pruned", !S.layers.pruned));
      $("tabbar").addEventListener("click", (e) => {
        const b = e.target.closest("button[data-tab]");
        if (b) selectTab(b.dataset.tab);
      });
      $("tabbar").addEventListener("keydown", (e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        const tabs = TABS.filter(tabVisible);
        const next = tabs[(tabs.indexOf(S.tab) + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        selectTab(next);
        document.querySelector(`#tabbar [data-tab="${next}"]`).focus();
      });
      $("ranges").addEventListener("click", (e) => {
        const b = e.target.closest("button[data-range]");
        if (!b) return;
        S.range = Number(b.dataset.range);
        markButtons($("ranges"), "data-range", S.range);
        loadStats().catch(() => {});
      });
      $("groupBy").addEventListener("change", (e) => { S.groupBy = e.target.value; loadStats().catch(() => {}); });
      $("sideToggle").addEventListener("click", () => {
        S.sideCollapsed = !S.sideCollapsed;
        savePref("sideCollapsed", S.sideCollapsed);
        applySide();
      });
      $("menuBtn").addEventListener("click", () => setMenu(!$("bar").classList.contains("open")));
      applySide();
    }

    // Doi sang/toi khi dang mo trang
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      C = palette();
      chart.applyOptions({
        layout: { textColor: C.muted },
        grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
        rightPriceScale: { borderColor: C.line }, timeScale: { borderColor: C.line },
      });
      candleSeries.applyOptions({ upColor: C.long, downColor: C.short, wickUpColor: C.long, wickDownColor: C.short });
      if (S.chart) renderChart(S.chart.data, false);
    });

    measure();
    syncUrl();
    applyTab();  // truoc refresh: tab khong hop le (vd Thong ke o Live) ve "setups" roi moi tai du lieu tab
    loadTrs();
    refresh(true);
  }

  setup();
})();
