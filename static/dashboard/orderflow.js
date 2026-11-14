// TM - #BOOKMAP - Order flow Bookmap
// Lop order flow tren chart nen: heatmap thanh khoan (duoi nen), bong bong lenh lon, CVD (pane phu, dong bo truc
// thoi gian), volume profile + COB (cot 120px ben phai). dashboard.js goi BamcpOrderflow.create(ctx) va cac hook
// onChart / query / key; file nay khong sua state cua dashboard.js.
// Tat mot lop: khong goi API cua lop do, ngung nhan qua SSE / polling, giai phong du lieu va canvas.
(function () {
  "use strict";

  const TOP = ["heatmap", "bubbles", "cvd", "vp", "cob"];
  const ALL = ["heatmap", "bbo", "gaps", "bubbles", "buy", "sell", "cvd", "vp", "vplines", "cob"];
  const PARENT = { bbo: "heatmap", gaps: "heatmap", buy: "bubbles", sell: "bubbles", vplines: "vp" };
  const LABEL = {
    heatmap: "Heatmap thanh khoản", bbo: "Đường best bid/ask", gaps: "Tô vùng không có dữ liệu",
    bubbles: "Bong bóng", buy: "Mua (BUY)", sell: "Bán (SELL)", cvd: "CVD", vp: "Volume Profile",
    vplines: "Đường POC / VAH / VAL", cob: "COB (sổ lệnh hiện tại)",
  };
  const SHORTCUT = { h: "heatmap", b: "bubbles", c: "cvd", v: "vp", o: "cob" };
  const DEFAULTS = {
    main: { heatmap: false, bbo: false, gaps: true, bubbles: true, buy: true, sell: true, cvd: false, vp: true, vplines: true, cob: false },
    full: { heatmap: true, bbo: false, gaps: true, bubbles: true, buy: true, sell: true, cvd: true, vp: true, vplines: true, cob: true },
  };
  const TIP_NOT_ENABLED = "Symbol chưa bật Order Flow trên trang admin";
  const TIP_NO_DATA = "Collector chưa có dữ liệu cho symbol này";
  const TIP_NO_BOOK = "Chưa có dữ liệu sổ lệnh";
  const SIDE_W = 120;
  const POLL_MS = 5000;

  // Bang mau heatmap (dung chung bid / ask): xanh dam -> xanh duong -> cyan -> vang -> cam -> do -> trang
  const STOPS = [[0, [8, 18, 64]], [0.18, [20, 70, 200]], [0.36, [0, 200, 230]], [0.55, [250, 230, 40]],
                 [0.72, [255, 150, 20]], [0.88, [235, 40, 30]], [1, [255, 255, 255]]];
  const LUT = (() => {
    const out = new Uint8ClampedArray(256 * 3);
    for (let i = 0; i < 256; i += 1) {
      const t = i / 255;
      let k = 0;
      while (k < STOPS.length - 2 && t > STOPS[k + 1][0]) k += 1;
      const [t0, c0] = STOPS[k];
      const [t1, c1] = STOPS[k + 1];
      const f = (t - t0) / (t1 - t0 || 1);
      for (let j = 0; j < 3; j += 1) out[i * 3 + j] = c0[j] + (c1[j] - c0[j]) * f;
    }
    return out;
  })();

  function b64floats(text) {
    if (!text) return new Float32Array(0);
    const bin = atob(text);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return new Float32Array(bytes.buffer, 0, Math.floor(bytes.length / 4));
  }

  function create(ctx) {
    const { chart, series, el } = ctx;
    const FULL = ctx.full;
    const STORE_KEY = "bamcp.orderflow.layers." + (FULL ? "full" : "main");
    const root = ctx.root;
    const grid = document.getElementById("chartGrid");
    const side = document.getElementById("ofSide");
    const cvdBox = document.getElementById("cvdPane");
    const cvdVal = document.getElementById("cvdVal");
    const tip = document.getElementById("ofTip");

    // ------------------------------------------------------------ trang thai, luu tru
    const st = loadState();
    let info = null;                 // /api/orderflow/status cho symbol dang xem
    let infoSymbol = null;
    const data = { heatmap: null, bubbles: null, cvd: null, vp: null, cob: null };
    const keys = {};
    const inflight = {};
    let sse = null;
    let sseLayers = "";
    let sseRetryAt = 0;
    let pollTimer = null;
    let reloadTimer = null;
    let cvdChart = null;
    let cvdSeries = null;
    let syncing = false;
    let sideFrame = 0;
    let hidden = document.hidden;

    function loadState() {
      const def = DEFAULTS[FULL ? "full" : "main"];
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (e) { saved = null; }
      const out = { master: true, layers: { ...def }, contrast: 1, minQty: {}, anchor: "from" };
      if (saved && typeof saved === "object") {
        if (typeof saved.master === "boolean") out.master = saved.master;
        ALL.forEach((k) => { if (saved.layers && typeof saved.layers[k] === "boolean") out.layers[k] = saved.layers[k]; });
        if (Number(saved.contrast) > 0) out.contrast = Number(saved.contrast);
        if (saved.minQty && typeof saved.minQty === "object") out.minQty = saved.minQty;
        if (saved.anchor === "day_utc" || saved.anchor === "from") out.anchor = saved.anchor;
      }
      // Trang full page: ?of= thang localStorage. of= rong = tat ca nhom
      const asked = FULL ? ctx.params.get("of") : null;
      if (asked !== null) {
        const on = new Set(asked.split(",").map((s) => s.trim()).filter(Boolean));
        ALL.forEach((k) => { out.layers[k] = on.has(k); });
        out.master = TOP.some((k) => on.has(k));
      }
      return out;
    }
    function saveState() {
      try { localStorage.setItem(STORE_KEY, JSON.stringify(st)); } catch (e) { /* trinh duyet chan */ }
    }

    // Lop co hieu luc: nhom bat, lop me bat, chinh no bat, va symbol co du lieu cho lop do
    function available(key) {
      const top = PARENT[key] || key;
      if (!info || !info.enabled || !info.has_trades) return false;
      if ((top === "heatmap" || top === "cob") && !info.has_book) return false;
      return true;
    }
    function on(key) {
      const top = PARENT[key] || key;
      return st.master && st.layers[top] && (top === key || st.layers[key]) && available(key);
    }
    const sideOn = () => on("vp") || on("cob");
    const live = () => ctx.state().mode === "live" && !hidden;

    // ------------------------------------------------------------ menu checkbox
    let menuBox = null;
    const inputs = {};
    let allBox = null;
    const ctl = {};

    function mount(menu) {
      menuBox = el("div", { class: "of-group", role: "group", "aria-label": "Order Flow" });
      allBox = el("input", { type: "checkbox", id: "of-all" });
      allBox.addEventListener("change", () => setMaster(!st.master || !TOP.some((k) => st.layers[k])));
      menuBox.append(el("label", { class: "check of-parent", id: "of-allLabel" }, allBox, " Order Flow"));
      const tree = [["heatmap", ["bbo", "gaps"]], ["bubbles", ["buy", "sell"]], ["cvd", []], ["vp", ["vplines"]], ["cob", []]];
      const kids = el("div", { class: "of-kids" });
      tree.forEach(([top, subs]) => {
        const row = el("div", { class: "of-item" }, checkRow(top, true));
        const subBox = el("div", { class: "of-sub" }, subs.map((k) => checkRow(k, false)));
        if (top === "heatmap") {
          ctl.contrast = el("input", { type: "range", min: "0.3", max: "3", step: "0.1", value: String(st.contrast), "aria-label": "Độ tương phản heatmap" });
          ctl.contrast.addEventListener("input", () => { st.contrast = Number(ctl.contrast.value); saveState(); paintHeatmap(); requestDraw(); });
          ctl.contrastRow = el("label", { class: "of-ctl" }, "Độ tương phản", ctl.contrast);
          subBox.append(ctl.contrastRow);
        }
        if (top === "bubbles") {
          ctl.minQty = el("input", { type: "number", min: "0", step: "any", inputMode: "decimal", "aria-label": "min_qty bong bóng" });
          ctl.minQty.addEventListener("change", () => {
            const value = Number(ctl.minQty.value);
            const sym = ctx.state().symbol;
            if (ctl.minQty.value === "" || !(value >= 0)) delete st.minQty[sym]; else st.minQty[sym] = value;
            saveState();
            reload(["bubbles"], true);
            restartStream();
          });
          ctl.minQtyRow = el("label", { class: "of-ctl" }, "min_qty", ctl.minQty);
          subBox.append(ctl.minQtyRow);
        }
        if (top === "cvd") {
          ctl.anchor = el("select", { "aria-label": "Neo CVD" },
            el("option", { value: "from" }, "Vùng đang xem"), el("option", { value: "day_utc" }, "Ngày UTC"));
          ctl.anchor.value = st.anchor;
          ctl.anchor.addEventListener("change", () => { st.anchor = ctl.anchor.value; saveState(); reload(["cvd"], true); });
          ctl.anchorRow = el("label", { class: "of-ctl" }, "Neo", ctl.anchor);
          subBox.append(ctl.anchorRow);
        }
        row.append(subBox);
        kids.append(row);
      });
      menuBox.append(kids);
      ctx.menuNote = el("p", { class: "of-note", id: "ofNote" });
      menuBox.append(ctx.menuNote);
      menu.append(menuBox);
      renderMenu();
    }

    function checkRow(key, top) {
      const box = el("input", { type: "checkbox", "data-of": key });
      box.addEventListener("change", () => setLayer(key, box.checked));
      inputs[key] = box;
      const label = el("label", { class: "check" + (top ? " of-top" : "") }, box, " " + LABEL[key]);
      inputs[key + "Label"] = label;
      return label;
    }

    function renderMenu() {
      if (!menuBox) return;
      const groupOk = !!(info && info.enabled && info.has_trades);
      const groupTip = !info || !info.enabled ? TIP_NOT_ENABLED : !info.has_trades ? TIP_NO_DATA : "";
      const shown = TOP.filter((k) => st.layers[k]);
      allBox.checked = st.master && shown.length === TOP.length;
      allBox.indeterminate = st.master && shown.length > 0 && shown.length < TOP.length;
      allBox.disabled = !groupOk;
      document.getElementById("of-allLabel").title = groupTip;
      menuBox.classList.toggle("off", !groupOk);
      ALL.forEach((key) => {
        const top = PARENT[key] || key;
        const box = inputs[key];
        const label = inputs[key + "Label"];
        const bookMissing = (top === "heatmap" || top === "cob") && groupOk && !info.has_book;
        // Con mo khi me tat; bat lai thi con tro ve trang thai cu (gia tri checked khong doi)
        const disabled = !groupOk || bookMissing || !st.master || (PARENT[key] && !st.layers[top]);
        box.checked = st.layers[key];
        box.disabled = disabled;
        label.classList.toggle("dim", disabled);
        label.title = !groupOk ? groupTip : bookMissing ? TIP_NO_BOOK : "";
      });
      ctl.contrastRow.hidden = !on("heatmap");
      ctl.minQtyRow.hidden = !on("bubbles");
      ctl.anchorRow.hidden = !on("cvd");
      if (info && info.big_trade_min_qty !== undefined) {
        ctl.minQty.placeholder = String(info.big_trade_min_qty);
        const mine = st.minQty[ctx.state().symbol];
        ctl.minQty.value = mine === undefined ? "" : String(mine);
      }
      ctx.menuNote.textContent = info && info.enabled && info.stale && info.has_trades
        ? `Collector không có event ${info.no_event_sec ? info.no_event_sec + " giây" : "mới"} – đang hiện dữ liệu cũ.` : "";
    }

    function setMaster(value) {
      st.master = value;
      if (value && !TOP.some((k) => st.layers[k])) TOP.forEach((k) => { st.layers[k] = true; });
      changed();
    }
    function setLayer(key, value) {
      st.layers[key] = value;
      if (value && TOP.includes(key)) st.master = true;
      changed();
    }
    function toggleTop(key) {
      if (!available(key)) return false;
      if (!st.master) { st.master = true; st.layers[key] = true; } else st.layers[key] = !st.layers[key];
      changed();
      return true;
    }

    function changed() {
      saveState();
      renderMenu();
      applyLayout();
      // Lop vua tat: bo du lieu, huy request; lop vua bat: tai ngay cho vung dang xem
      TOP.forEach((k) => { if (!on(k)) drop(k); });
      reload(TOP.filter(on), false);
      restartStream();
      requestDraw();
      ctx.onChange();
    }

    function drop(key) {
      if (inflight[key]) { inflight[key].abort(); delete inflight[key]; }
      data[key] = null;
      keys[key] = null;
      if (key === "heatmap") { heat.canvas.width = 0; heat.canvas.height = 0; heat.values = null; }
    }

    // of= cho link full page / URL full page: danh sach khoa dang bat (nhom tat = rong)
    function query() {
      if (!st.master) return "";
      return ALL.filter((k) => st.layers[k] && (!PARENT[k] || st.layers[PARENT[k]])).join(",");
    }

    // ------------------------------------------------------------ bo cuc: pane CVD, cot phai
    function applyLayout() {
      const wantCvd = on("cvd");
      grid.classList.toggle("cvd-on", wantCvd);
      cvdBox.hidden = !wantCvd;
      grid.classList.toggle("side-on", sideOn());
      side.hidden = !sideOn();
      if (wantCvd && !cvdChart) makeCvd();
      if (!wantCvd && cvdChart) {
        cvdChart.remove();
        cvdChart = null;
        cvdSeries = null;
        cvdVal.textContent = "";
        chart.applyOptions({ rightPriceScale: { minimumWidth: 0 } });
      }
      // Luoi cua chart ve de len lop duoi (heatmap): an luoi khi heatmap bat, nhu Bookmap
      chart.applyOptions({ timeScale: { visible: !wantCvd },
                           grid: { vertLines: { visible: !on("heatmap") }, horzLines: { visible: !on("heatmap") } } });
      if (!sideOn()) clearSide();
    }

    function makeCvd() {
      const C = ctx.colors();
      cvdChart = ctx.LW.createChart(cvdBox, {
        width: Math.max(1, cvdBox.clientWidth), height: Math.max(1, cvdBox.clientHeight),
        layout: { background: { type: "solid", color: "transparent" }, textColor: C.muted, fontSize: 11 },
        grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
        rightPriceScale: { borderColor: C.line, scaleMargins: { top: 0.12, bottom: 0.08 } },
        timeScale: { borderColor: C.line, timeVisible: true, secondsVisible: false, rightOffset: 4,
                     tickMarkFormatter: ctx.tickFormatter },
        localization: ctx.localization,
        crosshair: { mode: ctx.LW.CrosshairMode.Normal },
        handleScale: true, handleScroll: true,
      });
      cvdSeries = cvdChart.addLineSeries({ color: C.entry, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
                                           priceFormat: { type: "volume" } });
      cvdSeries.createPriceLine({ price: 0, color: ctx.alpha(C.neutral, 0.5), lineWidth: 1, lineStyle: ctx.LW.LineStyle.Dotted, axisLabelVisible: false });
      cvdChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (syncing || !range) return;
        syncing = true;
        chart.timeScale().setVisibleLogicalRange(range);
        syncing = false;
      });
      new ResizeObserver(() => {
        if (!cvdChart) return;
        cvdChart.resize(Math.max(1, cvdBox.clientWidth), Math.max(1, cvdBox.clientHeight));
        syncScales();
      }).observe(cvdBox);
      renderCvd();
    }

    // Hai chart phai co cung do rong thang gia thi truc thoi gian moi thang hang
    function syncScales() {
      if (!cvdChart) return;
      const a = chart.priceScale("right").width();
      const b = cvdChart.priceScale("right").width();
      const w = Math.max(a, b, 60);
      chart.applyOptions({ rightPriceScale: { minimumWidth: w } });
      cvdChart.applyOptions({ rightPriceScale: { minimumWidth: w } });
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) { syncing = true; cvdChart.timeScale().setVisibleLogicalRange(range); syncing = false; }
    }

    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (cvdChart && range && !syncing) {
        syncing = true;
        cvdChart.timeScale().setVisibleLogicalRange(range);
        syncing = false;
      }
      scheduleReload();
    });

    // ------------------------------------------------------------ toa do
    const timeline = () => (ctx.data() ? ctx.data().data.candles : []);
    function spanMs() { const d = ctx.data(); return d ? ctx.span[d.data.timeframe] : 3600e3; }
    // x lien tuc cua mot moc ms: nen [T, T+span) chiem tu tam - spacing/2 toi tam + spacing/2
    function xAt(ms) {
      const candles = timeline();
      if (!candles.length) return null;
      const logical = (ms - candles[0][0]) / spanMs() - 0.5;
      // logicalToCoordinate chi nhan chi so nguyen (so le tra 0): noi suy giua hai chi so nguyen ke nhau
      const scale = chart.timeScale();
      const i = Math.floor(logical);
      const a = scale.logicalToCoordinate(i);
      const b = scale.logicalToCoordinate(i + 1);
      if (a === null || b === null) return null;
      return a + (logical - i) * (b - a);
    }
    const yAt = (price) => series.priceToCoordinate(price);
    function plotSize() {
      const w = ctx.box.clientWidth - chart.priceScale("right").width();
      const h = ctx.box.clientHeight - (chart.options().timeScale.visible === false ? 0 : chart.timeScale().height());
      return { w: Math.max(1, w), h: Math.max(1, h) };
    }
    function view() {
      const candles = timeline();
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!candles.length || !range) return null;
      const span = spanMs();
      const first = candles[0][0];
      const now = Date.now();
      const from = first + Math.floor(Math.max(0, range.from + 0.5)) * span;
      // edge: mep phai cua vung dang xem (khong cat theo gio hien tai) - chi phu thuoc zoom / pan, dung de tinh
      // buoc cot; neu tinh theo gio hien tai thi khoa yeu cau troi lien tuc va tai lai khi khong ai dong vao
      const edge = Math.max(from + span, first + Math.ceil(range.to + 0.5) * span);
      const size = plotSize();
      const top = series.coordinateToPrice(0);
      const bottom = series.coordinateToPrice(size.h);
      if (top === null || bottom === null) return null;
      return { from, edge, to: Math.max(from + span, Math.min(edge, now)), span, size,
               priceMin: Math.min(top, bottom), priceMax: Math.max(top, bottom) };
    }

    // ------------------------------------------------------------ tai du lieu
    function scheduleReload() {
      clearTimeout(reloadTimer);
      reloadTimer = setTimeout(() => reload(TOP.filter(on), false), 100);
    }

    // Lam tron theo pixel / bucket de pan nho khong goi lai: chi goi khi khoa doi. Khoa giong nhau = khong goi trung
    function paramsFor(layer, v) {
      const symbol = ctx.state().symbol;
      const step = Math.max(1, Math.round((v.edge - v.from) / v.size.w));
      const from = Math.floor(v.from / step) * step;
      // Lam tron mep phai len 30 cot: thoi gian troi khong doi khoa lien tuc, cot tuong lai de SSE lap vao
      const to = Math.ceil(v.to / (step * 30)) * step * 30;
      if (layer === "heatmap") {
        const pad = (v.priceMax - v.priceMin) * 0.15;
        const unit = Math.max(info.bucket_size, (v.priceMax - v.priceMin) / 40);
        return { symbol, from, to, price_min: Math.floor((v.priceMin - pad) / unit) * unit, price_max: Math.ceil((v.priceMax + pad) / unit) * unit,
                 max_cols: Math.min(4000, Math.max(50, Math.round(v.size.w))), max_rows: Math.min(400, Math.max(50, Math.round(v.size.h / 1.5))) };
      }
      if (layer === "bubbles") {
        const mine = st.minQty[symbol];
        return { symbol, from, to, min_qty: mine === undefined ? "" : mine };
      }
      if (layer === "cvd") {
        return { symbol, from: Math.floor(v.from / v.span) * v.span, to: Math.ceil(v.to / v.span) * v.span,
                 res: Math.round(v.span / 1000), anchor: st.anchor };
      }
      if (layer === "vp") {
        const size = info.bucket_size;
        const bucket = size * Math.max(1, Math.ceil((v.priceMax - v.priceMin) / (v.size.h / 3) / size));
        return { symbol, from, to, bucket };
      }
      if (layer === "cob") {
        const mid = (v.priceMax + v.priceMin) / 2;
        const pct = Math.min(info.depth_range_pct, Math.max(0.001, (v.priceMax - v.priceMin) / 2 / mid * 1.2));
        return { symbol, range_pct: Number(pct.toFixed(4)) };
      }
      return null;
    }

    const PATH = { heatmap: "/orderflow/heatmap", bubbles: "/orderflow/bubbles", cvd: "/orderflow/cvd",
                   vp: "/orderflow/volume-profile", cob: "/orderflow/orderbook" };

    function reload(layers, force) {
      const v = view();
      if (!v || !info) return;
      layers.forEach((layer) => {
        if (!on(layer)) return;
        const params = paramsFor(layer, v);
        const key = JSON.stringify(params);
        if (!force && key === keys[layer]) return;
        if (inflight[layer]) inflight[layer].abort();
        const controller = new AbortController();
        inflight[layer] = controller;
        keys[layer] = key;
        ctx.api(PATH[layer], params, { signal: controller.signal }).then((out) => {
          if (controller.signal.aborted || !on(layer)) return;
          delete inflight[layer];
          accept(layer, out, params);
        }).catch((err) => {
          if (controller.signal.aborted) return;
          delete inflight[layer];
          keys[layer] = null;
          ctx.menuNote && (ctx.menuNote.textContent = `Không tải được ${LABEL[layer]}: ${err.message}`);
        });
      });
    }

    function accept(layer, out, params) {
      if (layer === "heatmap") setHeatmap(out);
      else if (layer === "bubbles") data.bubbles = { rows: out, max: Math.max(1e-9, ...out.map((b) => b.qty)) };
      else if (layer === "cvd") { data.cvd = { points: out, anchor: params.anchor }; renderCvd(); }
      else if (layer === "vp") data.vp = out;
      else if (layer === "cob") data.cob = out;
      requestDraw();
    }

    // ------------------------------------------------------------ heatmap
    const heat = { canvas: document.createElement("canvas"), values: null, meta: null, p99: 1 };

    function setHeatmap(out) {
      if (!out.t || !out.t.length || !out.n_rows) { data.heatmap = { ...out, empty: true }; heat.values = null; return; }
      const bids = b64floats(out.bids);
      const asks = b64floats(out.asks);
      const n = out.n_cols * out.n_rows;
      const values = new Float32Array(n);
      for (let i = 0; i < n; i += 1) values[i] = (bids[i] || 0) + (asks[i] || 0);
      heat.values = values;
      // counts = so snapshot trong tung cot: snapshot moi qua SSE cong vao dung trong so trung binh
      heat.meta = { t0: out.t[0], step: out.step, nCols: out.n_cols, nRows: out.n_rows, base: out.base_price,
                    size: out.bucket_size, filled: Uint8Array.from(out.filled || []), bbo: out.bbo || [],
                    counts: Uint32Array.from(out.counts || out.filled || []) };
      data.heatmap = { gaps: out.gaps || [], coverage: out.coverage_start, empty: false };
      [heat.ref, heat.p99] = percentiles(values, [0.5, 0.99]);
      paintHeatmap();
    }

    function percentiles(values, qs) {
      const nz = [];
      for (let i = 0; i < values.length; i += 1) if (values[i] > 0) nz.push(values[i]);
      if (!nz.length) return qs.map(() => 1);
      nz.sort((a, b) => a - b);
      return qs.map((q) => nz[Math.min(nz.length - 1, Math.floor(q * nz.length))] || 1);
    }

    // Chuan hoa log theo percentile 99 cua vung dang xem, tinh tu muc trung vi: lenh co do lon thuong gap ra
    // xanh, chi lenh lon moi nong len. Do tuong phan doi mau ngay, khong goi lai API.
    function paintHeatmap() {
      const m = heat.meta;
      if (!heat.values || !m) return;
      const { nCols, nRows } = m;
      heat.canvas.width = nCols;
      heat.canvas.height = nRows;
      const c2 = heat.canvas.getContext("2d");
      const img = c2.createImageData(nCols, nRows);
      const px = img.data;
      const ref = Math.max(1e-12, heat.ref);
      const scale = 1 / Math.log1p(Math.max(heat.p99 / ref, 1.0001));
      const gamma = 1 / Math.max(0.1, st.contrast);
      for (let col = 0; col < nCols; col += 1) {
        if (!m.filled[col]) continue;
        const base = col * nRows;
        for (let row = 0; row < nRows; row += 1) {
          const v = heat.values[base + row];
          if (!(v > 0)) continue;
          let t = Math.min(1, Math.log1p(v / ref) * scale);
          t = Math.pow(t, gamma);
          const li = Math.round(t * 255) * 3;
          const o = ((nRows - 1 - row) * nCols + col) * 4;
          px[o] = LUT[li]; px[o + 1] = LUT[li + 1]; px[o + 2] = LUT[li + 2];
          px[o + 3] = Math.round(255 * Math.min(0.92, 0.25 + t));
        }
      }
      c2.putImageData(img, 0, 0);
    }

    // Snapshot moi qua SSE: cong vao cot cua luoi hien tai (trung binh), qua mep phai thi noi them cot
    function liveHeatmap(snaps) {
      const m = heat.meta;
      if (!m || !heat.values) return;
      let grew = false;
      snaps.forEach((s) => {
        const col = Math.floor((s.ts - m.t0) / m.step);
        if (col < 0) return;
        if (col >= m.nCols) {
          const extra = col - m.nCols + 1;
          if (extra > 400) return;
          const values = new Float32Array((m.nCols + extra) * m.nRows);
          values.set(heat.values);
          heat.values = values;
          const filled = new Uint8Array(m.nCols + extra); filled.set(m.filled); m.filled = filled;
          const counts = new Uint32Array(m.nCols + extra); counts.set(m.counts); m.counts = counts;
          for (let i = 0; i < extra; i += 1) m.bbo.push(null);
          m.nCols += extra;
          grew = true;
        }
        const bids = b64floats(s.bids);
        const asks = b64floats(s.asks);
        const k = Math.max(1, Math.round(m.size / s.bucket_size));
        const offset = Math.round((s.base_price - m.base) / s.bucket_size);
        const n = m.counts[col];
        const base = col * m.nRows;
        const fresh = new Float32Array(m.nRows);
        for (let i = 0; i < s.n; i += 1) {
          const row = Math.floor((offset + i) / k);
          if (row >= 0 && row < m.nRows) fresh[row] += (bids[i] || 0) + (asks[i] || 0);
        }
        for (let r = 0; r < m.nRows; r += 1) heat.values[base + r] = (heat.values[base + r] * n + fresh[r]) / (n + 1);
        const prev = m.bbo[col];
        m.bbo[col] = prev && n ? [(prev[0] * n + s.best_bid) / (n + 1), (prev[1] * n + s.best_ask) / (n + 1)] : [s.best_bid, s.best_ask];
        m.counts[col] = n + 1;
        m.filled[col] = 1;
      });
      if (grew || snaps.length) paintHeatmap();
    }

    let stripe = null;
    function stripePattern(c) {
      if (stripe) return stripe;
      const tile = document.createElement("canvas");
      tile.width = 8; tile.height = 8;
      const t = tile.getContext("2d");
      t.strokeStyle = "rgba(128,128,128,0.35)";
      t.lineWidth = 2;
      t.beginPath(); t.moveTo(0, 8); t.lineTo(8, 0); t.stroke();
      stripe = c.createPattern(tile, "repeat");
      return stripe;
    }

    function noDataSpans(width) {
      const d = data.heatmap;
      if (!d || !on("gaps")) return [];
      const spans = [];
      const v = view();
      if (!v) return spans;
      if (d.coverage === null || d.coverage === undefined) spans.push([v.from, v.to]);
      else if (d.coverage > v.from) spans.push([v.from, d.coverage]);
      (d.gaps || []).forEach((g) => spans.push(g));
      return spans.map(([s, e]) => [xAt(s), xAt(e)]).filter(([a, b]) => a !== null && b !== null && b > 0 && a < width)
        .map(([a, b]) => [Math.max(0, a), Math.min(width, b)]);
    }

    function drawBottom(c, size) {
      if (on("heatmap") && heat.values && heat.meta) {
        const m = heat.meta;
        const x0 = xAt(m.t0);
        const x1 = xAt(m.t0 + m.nCols * m.step);
        const yTop = yAt(m.base + m.nRows * m.size);
        const yBottom = yAt(m.base);
        if (x0 !== null && x1 !== null && yTop !== null && yBottom !== null && x1 > x0) {
          c.save();
          c.imageSmoothingEnabled = false;
          c.drawImage(heat.canvas, x0, yTop, x1 - x0, yBottom - yTop);
          c.restore();
        }
      }
      if (on("heatmap") && on("gaps")) {
        c.save();
        c.fillStyle = stripePattern(c);
        noDataSpans(size.width).forEach(([a, b]) => c.fillRect(a, 0, b - a, size.height));
        c.restore();
      }
    }

    // ------------------------------------------------------------ lop tren: bong bong, bbo, POC/VAH/VAL
    function bubbleShapes() {
      const d = data.bubbles;
      if (!on("bubbles") || !d) return [];
      const C = ctx.colors();
      const out = [];
      d.rows.forEach((b) => {
        if ((b.side === "BUY" && !on("buy")) || (b.side === "SELL" && !on("sell"))) return;
        const x = xAt(b.ts);
        const y = yAt(b.price);
        if (x === null || y === null) return;
        out.push({ x, y, r: Math.max(2, 20 * Math.sqrt(b.qty / d.max)), color: b.side === "BUY" ? C.long : C.short, b });
      });
      return out;
    }

    function drawTop(c, size) {
      const C = ctx.colors();
      if (on("heatmap") && on("bbo") && heat.meta) {
        const v = view();
        const deep = (v && v.to - v.from <= 6 * 3600e3) || ["5m", "1m", "3m"].includes(ctx.state().tf);
        if (deep) {
          const m = heat.meta;
          [[0, C.long], [1, C.short]].forEach(([idx, color]) => {
            c.strokeStyle = color;
            c.lineWidth = 1;
            c.beginPath();
            let pen = false;
            for (let col = 0; col < m.nCols; col += 1) {
              const pair = m.bbo[col];
              if (!pair) { pen = false; continue; }
              const x = xAt(m.t0 + (col + 0.5) * m.step);
              const y = yAt(pair[idx]);
              if (x === null || y === null) { pen = false; continue; }
              if (pen) c.lineTo(x, y); else c.moveTo(x, y);
              pen = true;
            }
            c.stroke();
          });
        }
      }
      if (on("vp") && on("vplines") && data.vp && data.vp.poc !== null) {
        [["POC", data.vp.poc, [], 2], ["VAH", data.vp.vah, [6, 4], 1], ["VAL", data.vp.val, [6, 4], 1]].forEach(([name, price, dash, width]) => {
          const y = yAt(price);
          if (y === null || y < 0 || y > size.height) return;
          c.strokeStyle = ctx.alpha(C.tr, name === "POC" ? 0.95 : 0.8);
          c.lineWidth = width;
          c.setLineDash(dash);
          c.beginPath(); c.moveTo(0, y); c.lineTo(size.width, y); c.stroke();
          c.setLineDash([]);
          c.font = "600 10px system-ui, sans-serif";
          c.fillStyle = ctx.alpha(C.tr, 0.95);
          c.textBaseline = "bottom";
          const text = `${name} ${ctx.num(price)}`;
          c.fillText(text, size.width - c.measureText(text).width - 6, y - 2);
        });
      }
      bubbleShapes().forEach((s) => {
        c.beginPath();
        c.arc(s.x, s.y, s.r, 0, Math.PI * 2);
        c.fillStyle = ctx.alpha(s.color, 0.7);
        c.fill();
        // Tren nen heatmap (vang / cam) bong bong de chim: vien toi cho tach khoi nen
        if (on("heatmap")) { c.strokeStyle = "rgba(0,0,0,0.75)"; c.lineWidth = 1.5; c.stroke(); }
        else if (s.r >= 6) { c.strokeStyle = ctx.alpha(s.color, 0.95); c.lineWidth = 1; c.stroke(); }
      });
      if (sideOn()) queueSide();
      // Thang gia doi (keo truc gia, autoscale) cung doi vung gia dang xem: kiem lai khoa sau 250ms yen lang
      clearTimeout(softTimer);
      softTimer = setTimeout(() => reload(["heatmap", "vp", "cob"].filter(on), false), 250);
    }
    let softTimer = null;

    const primitive = {
      paneViews() { return [bottomView, topView]; },
      updateAllViews() {},
      attached(args) { this.requestUpdate = args.requestUpdate; },
      detached() { this.requestUpdate = null; },
    };
    const bottomView = {
      zOrder() { return "bottom"; },
      renderer() { return { draw(target) { target.useMediaCoordinateSpace((s) => drawBottom(s.context, s.mediaSize)); } }; },
    };
    const topView = {
      zOrder() { return "top"; },
      renderer() { return { draw(target) { target.useMediaCoordinateSpace((s) => drawTop(s.context, s.mediaSize)); } }; },
    };
    series.attachPrimitive(primitive);
    function requestDraw() { if (primitive.requestUpdate) primitive.requestUpdate(); }

    // ------------------------------------------------------------ cot phai: volume profile + COB
    function clearSide() {
      const c = side.getContext("2d");
      c.clearRect(0, 0, side.width, side.height);
    }
    function queueSide() {
      if (sideFrame) return;
      sideFrame = requestAnimationFrame(() => { sideFrame = 0; drawSide(); });
    }
    function drawSide() {
      if (!sideOn()) return;
      const dpr = window.devicePixelRatio || 1;
      const h = side.clientHeight;
      if (side.width !== Math.round(SIDE_W * dpr) || side.height !== Math.round(h * dpr)) {
        side.width = Math.round(SIDE_W * dpr);
        side.height = Math.round(h * dpr);
      }
      const c = side.getContext("2d");
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, SIDE_W, h);
      const C = ctx.colors();
      const plotH = plotSize().h;
      const both = on("vp") && on("cob");
      const vpW = both ? SIDE_W / 2 : SIDE_W;
      c.save();
      c.beginPath(); c.rect(0, 0, SIDE_W, plotH); c.clip();
      if (on("vp") && data.vp) {
        const rows = data.vp.rows || [];
        const bucket = data.vp.bucket;
        const inView = rows.filter((r) => { const y = yAt(r.price); return y !== null && y > -50 && y < plotH + 50; });
        const max = Math.max(1e-9, ...inView.map((r) => r.total));
        inView.forEach((r) => {
          const y0 = yAt(r.price + bucket);
          const y1 = yAt(r.price);
          if (y0 === null || y1 === null) return;
          const hgt = Math.max(1, Math.abs(y1 - y0) - 0.5);
          const top = Math.min(y0, y1);
          const len = (r.total / max) * (vpW - 4);
          const buyLen = r.total ? len * (r.buy / r.total) : 0;
          c.fillStyle = ctx.alpha(C.long, 0.55);
          c.fillRect(2, top, buyLen, hgt);
          c.fillStyle = ctx.alpha(C.short, 0.55);
          c.fillRect(2 + buyLen, top, len - buyLen, hgt);
        });
        if (data.vp.poc !== null) {
          const y = yAt(data.vp.poc);
          if (y !== null) { c.fillStyle = C.tr; c.fillRect(0, y - 1, vpW, 2); }
        }
      }
      if (on("cob") && data.cob && data.cob.bids) {
        const left = both ? vpW : 0;
        const width = SIDE_W - left;
        const size = data.cob.bucket_size;
        const levels = [...data.cob.bids.map((r) => [r[0], r[1], C.long]), ...data.cob.asks.map((r) => [r[0], r[1], C.short])]
          .filter(([p]) => { const y = yAt(p); return y !== null && y > -20 && y < plotH + 20; });
        const max = Math.max(1e-9, ...levels.map((l) => l[1]));
        levels.forEach(([price, qty, color]) => {
          const y0 = yAt(price + size);
          const y1 = yAt(price);
          if (y0 === null || y1 === null) return;
          const len = (qty / max) * (width - 4);
          c.fillStyle = ctx.alpha(color, 0.6);
          c.fillRect(left + width - 2 - len, Math.min(y0, y1), len, Math.max(1, Math.abs(y1 - y0) - 0.5));
        });
        if (both) { c.fillStyle = ctx.alpha(C.neutral, 0.3); c.fillRect(vpW, 0, 1, plotH); }
      }
      c.restore();
      c.font = "10px system-ui, sans-serif";
      c.fillStyle = C.muted;
      c.textBaseline = "top";
      if (on("vp")) c.fillText("VP", 4, 4);
      if (on("cob")) c.fillText("COB", (both ? vpW : 0) + 4, 4);
    }

    // ------------------------------------------------------------ CVD
    function renderCvd() {
      if (!cvdChart || !cvdSeries) return;
      const st0 = ctx.data();
      if (!st0) return;
      const times = ctx.times();
      const byTime = new Map(((data.cvd && data.cvd.points) || []).map((p) => [p.t / 1000, p.cvd]));
      cvdSeries.setData(times.map((time) => (byTime.has(time) ? { time, value: byTime.get(time) } : { time })));
      const pts = (data.cvd && data.cvd.points) || [];
      const last = pts[pts.length - 1];
      cvdVal.textContent = last ? `CVD ${last.cvd >= 0 ? "+" : ""}${ctx.num(last.cvd, 2)}` : "CVD —";
      cvdVal.className = "cvd-val " + (last ? (last.cvd >= 0 ? "long" : "short") : "");
      requestAnimationFrame(syncScales);
    }

    // SSE / polling tra delta cac bucket moi nhat: thay / noi diem roi tinh lai cong don tu moc neo
    function liveCvd(rows) {
      const d = data.cvd;
      if (!d || !d.points) return;
      const pts = d.points;
      const base0 = pts.length ? pts[0].cvd - pts[0].delta : 0;
      rows.forEach((r) => {
        const i = pts.findIndex((p) => p.t === r.t);
        if (i >= 0) Object.assign(pts[i], { buy: r.buy, sell: r.sell, delta: r.delta });
        else if (!pts.length || r.t > pts[pts.length - 1].t) pts.push({ ...r, cvd: 0 });
      });
      let cvd = base0;
      let day = null;
      pts.forEach((p, i) => {
        const pd = p.t - (p.t % 864e5);
        if (d.anchor === "day_utc" && i > 0 && pd !== day) cvd = 0;
        day = pd;
        cvd += p.delta;
        p.cvd = cvd;
      });
      renderCvd();
    }

    // ------------------------------------------------------------ SSE, polling
    function streamLayers() {
      return ["heatmap", "bubbles", "cvd", "cob"].filter(on).join(",");
    }
    function stopStream() {
      if (sse) { sse.close(); sse = null; }
      sseLayers = "";
      clearInterval(pollTimer);
      pollTimer = null;
    }
    function restartStream() {
      const layers = live() ? streamLayers() : "";
      if (layers === sseLayers && (sse || pollTimer)) return;
      stopStream();
      if (!layers) return;
      sseLayers = layers;
      if (!window.EventSource || Date.now() < sseRetryAt) { startPolling(); return; }
      const symbol = ctx.state().symbol;
      const mine = st.minQty[symbol];
      const q = new URLSearchParams({ symbol, layers, res: String(Math.round(spanMs() / 1000)) });
      if (mine !== undefined) q.set("min_qty", String(mine));
      const v = view();
      if (v && on("cob")) q.set("range_pct", paramsFor("cob", v).range_pct);
      sse = new EventSource(`${ctx.apiBase}/orderflow/stream?${q}`);
      sse.addEventListener("tick", (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (err) { return; }
        if (msg.heatmap && on("heatmap")) liveHeatmap(msg.heatmap);
        if (msg.bubbles && on("bubbles") && data.bubbles) {
          data.bubbles.rows.push(...msg.bubbles);
          data.bubbles.max = Math.max(data.bubbles.max, ...msg.bubbles.map((b) => b.qty));
        }
        if (msg.cvd && on("cvd")) liveCvd(msg.cvd);
        if (msg.cob && on("cob") && data.cob) mergeCob(msg.cob);
        requestDraw();
      });
      const fail = () => {
        // Loi SSE (proxy chan, mat mang): chuyen sang polling 5 giay, 2 phut sau thu lai SSE
        if (!sse) return;
        sse.close();
        sse = null;
        sseRetryAt = Date.now() + 120e3;
        startPolling();
      };
      sse.addEventListener("error", fail);
      sse.addEventListener("end", fail);
    }
    function startPolling() {
      clearInterval(pollTimer);
      pollTimer = setInterval(() => {
        if (!live()) return;
        if (Date.now() >= sseRetryAt && window.EventSource) { sseLayers = ""; restartStream(); return; }
        reload(["heatmap", "bubbles", "cvd", "cob"].filter(on), true);
      }, POLL_MS);
    }
    function mergeCob(tick) {
      const keep = (rows, lo, hi) => rows.filter(([p]) => p < lo || p > hi);
      const span = (rows) => rows.length ? [Math.min(...rows.map((r) => r[0])), Math.max(...rows.map((r) => r[0]))] : null;
      const b = span(tick.bids);
      const a = span(tick.asks);
      data.cob = { ...data.cob, ts: tick.ts, mid: tick.mid, best_bid: tick.best_bid, best_ask: tick.best_ask,
        bids: [...(b ? keep(data.cob.bids, b[0], b[1]) : data.cob.bids), ...tick.bids].filter(([p]) => p <= tick.best_bid),
        asks: [...(a ? keep(data.cob.asks, a[0], a[1]) : data.cob.asks), ...tick.asks].filter(([p]) => p >= tick.best_ask - data.cob.bucket_size) };
    }

    // ------------------------------------------------------------ tooltip
    chart.subscribeCrosshairMove((param) => {
      if (!param.point) { tip.hidden = true; return; }
      const { x, y } = param.point;
      const hit = bubbleShapes().reverse().find((s) => (s.x - x) ** 2 + (s.y - y) ** 2 <= Math.max(16, s.r * s.r));
      let text = "";
      if (hit) {
        const b = hit.b;
        text = `${ctx.fmtTime(b.ts)} · ${b.side === "BUY" ? "Mua chủ động" : "Bán chủ động"}\nGiá ${ctx.num(b.price)} · KL ${ctx.num(b.qty, 4)}`;
      } else if (on("heatmap") && on("gaps") && noDataSpans(ctx.box.clientWidth).some(([a, b]) => x >= a && x <= b)) {
        text = "Không có dữ liệu sổ lệnh";
      }
      if (!text) { tip.hidden = true; return; }
      tip.textContent = text;
      tip.hidden = false;
      const w = tip.offsetWidth;
      tip.style.left = `${Math.max(4, Math.min(x + 14, ctx.box.clientWidth - w - 4))}px`;
      tip.style.top = `${Math.max(4, y - tip.offsetHeight - 10)}px`;
    });

    // ------------------------------------------------------------ hook cho dashboard.js
    let lastChartKey = "";
    function refreshInfo(force) {
      const symbol = ctx.state().symbol;
      if (!force && infoSymbol === symbol && info) return Promise.resolve();
      return ctx.api("/orderflow/status", { symbol }).then((out) => {
        if (symbol !== ctx.state().symbol) return;
        info = out.symbol || null;
        infoSymbol = symbol;
        renderMenu();
        applyLayout();
      }).catch(() => { /* order flow la phu: loi thi nhom mo */ });
    }

    // Moi lan dashboard ve lai chart (doi cap, khung, ngay, lam moi dinh ky)
    function onChart() {
      const s = ctx.state();
      const chartKey = `${s.symbol}|${s.tf}|${s.mode}|${s.date}`;
      const fresh = chartKey !== lastChartKey;
      if (fresh) {
        TOP.forEach(drop);
        stopStream();
        lastChartKey = chartKey;
      }
      refreshInfo(fresh || s.mode === "live").then(() => {
        TOP.forEach((k) => { if (!on(k)) drop(k); });
        applyLayout();
        if (cvdChart) renderCvd();
        // SSE dang chay thi khong can tai lai; khong co SSE thi lam moi theo nhip lam moi cua dashboard
        reload(TOP.filter(on), !fresh && !sse);
        restartStream();
        requestDraw();
      });
    }

    function key(k) {
      const layer = SHORTCUT[k];
      return layer ? toggleTop(layer) : false;
    }

    function visibility(isHidden) {
      hidden = isHidden;
      if (hidden) stopStream(); else restartStream();
    }

    function repaint() {
      stripe = null;
      if (cvdChart) {
        const C = ctx.colors();
        cvdChart.applyOptions({ layout: { textColor: C.muted }, grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } } });
        cvdSeries.applyOptions({ color: C.entry });
      }
      requestDraw();
    }

    applyLayout();
    return { mount, onChart, query, key, visibility, repaint, state: st };
  }

  window.BamcpOrderflow = { create };
})();
