// TM - #DASHBOARD - Setup dashboard
// Trang chi doc: khong co thao tac dat, sua, huy lenh hay setup. Moi chu tu server dua vao DOM
// bang textContent, khong dung innerHTML, nen label, note khong the chen ma vao trang.
(function () {
  "use strict";

  const BOOT = JSON.parse(document.getElementById("boot").textContent);
  const API = BOOT.base + "/api";
  const OWNER = BOOT.role === "owner";
  const TF_LABEL = { "1d": "1D", "4h": "4H", "1h": "1H", "15m": "15M", "5m": "5M" };
  const SPAN = { "5m": 3e5, "15m": 9e5, "1h": 36e5, "4h": 144e5, "1d": 864e5 };
  const MAX_BARS = 500;
  const MAX_WHITESPACE = 400;
  const STATUS_CLASS = {
    ENTRY_VALID: "go", FILLED: "go", IN_ZONE: "run", TRIGGERED: "run", CONFIRMED: "run",
    MISSED: "stop", BLOCKED: "stop", INVALIDATED: "stop", EXPIRED: "stop", CANCELLED: "stop",
  };
  const TERMINAL = ["FILLED", "INVALIDATED", "EXPIRED", "CANCELLED"];
  const TRIGGER_LABEL = {
    save_bias: "Lưu bias", save_setups: "Lưu setup", save_scenarios: "Lưu kịch bản", cancel_setup: "Huỷ setup",
  };

  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);

  function loadPref(key, fallback) {
    try {
      const value = localStorage.getItem("bamcp.dash." + key);
      return value === null ? fallback : value === "1";
    } catch (e) { return fallback; }
  }
  function savePref(key, value) {
    try { localStorage.setItem("bamcp.dash." + key, value ? "1" : "0"); } catch (e) { /* trinh duyet chan */ }
  }

  const askedDate = params.get("date");
  const validDate = /^\d{4}-\d{2}-\d{2}$/.test(askedDate || "") ? askedDate : BOOT.today;
  const askedTf = params.get("tf");
  const S = {
    symbol: (params.get("symbol") || BOOT.default_symbol).toUpperCase(),
    date: validDate,
    mode: params.get("mode") === "review" || (!params.get("mode") && validDate < BOOT.today) ? "review" : "live",
    tf: SPAN[askedTf] ? askedTf : (SPAN[BOOT.default_tf] ? BOOT.default_tf : "1h"),
    revision: "origin",
    tab: "plan",
    range: 30,
    groupBy: "trigger_type",
    selected: null,
    showLevels: loadPref("levels", true),
    chart: null,
    openNotes: new Set(),
  };
  let chartTicket = 0;
  let listTicket = 0;
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
  function letter(code) { return String(code || "").split("-").pop(); }

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

  // ---------------------------------------------------------------- goi API

  function loginRedirect() {
    const next = BOOT.base + location.search;
    location.href = BOOT.base + "/login?next=" + encodeURIComponent(next);
  }

  async function api(path, query, options) {
    const search = new URLSearchParams();
    Object.entries(query || {}).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") search.set(key, value);
    });
    const url = API + path + (search.toString() ? "?" + search : "");
    const res = await fetch(url, { credentials: "same-origin", ...(options || {}) });
    if (res.status === 401) { loginRedirect(); throw new Error("Phiên đăng nhập đã hết"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
    return data;
  }

  function setBanner(text) {
    const box = $("banner");
    box.textContent = text || "";
    box.hidden = !text;
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
    };
  }
  let C = palette();

  const chart = LW.createChart(chartBox, {
    autoSize: true,
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

  function drawBands(ctx, size) {
    const d = overlay.data;
    if (!d) return;
    if (d.tr) {
      const top = yOf(d.tr.tr_high);
      const bottom = yOf(d.tr.tr_low);
      if (top !== null && bottom !== null) {
        const start = d.tr.since ? Math.max(0, xOf(d.tr.since) ?? 0) : 0;
        ctx.fillStyle = alpha(C.tr, 0.07);
        ctx.fillRect(start, top, size.width - start, bottom - top);
      }
    }
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
      if (y !== null) textBox(ctx, "TR khoá", start + 4, y + 10, C.tr, size);
    }
    d.zones.forEach((zone) => {
      const y = yOf(zone.high);
      if (y === null || (S.selected && S.selected !== zone.code) || zone.dim) return;
      textBox(ctx, letter(zone.code), Math.max(4, (xOf(zone.start) ?? 0) + 4), y + 9, zone.color, size);
    });
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
    ctx.restore();
  }

  // Cac muc cua ke hoach duoc keo vao thang gia neu khong qua xa nen dang xem, de vung va TR
  // luon thay duoc. Setup dang chon thi luon lay du vung + entry/SL/TP.
  function autoscaleLevels() {
    const d = overlay.data;
    if (!d) return [];
    const out = [];
    if (d.tr) out.push({ price: d.tr.tr_high }, { price: d.tr.tr_low });
    d.zones.forEach((zone) => {
      const force = zone.code === S.selected;
      if (zone.dim && !force) return;
      out.push({ price: zone.low, force }, { price: zone.high, force });
      if (force) zone.extra.forEach((price) => out.push({ price, force }));
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
    d.setups.forEach((setup) => {
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
    if (S.showLevels) {
      overlay.data.levels.forEach((level) => addLine(level.price, { color: alpha(C.neutral, 0.45), lineStyle: Dotted, axisLabelVisible: false }));
    }
    // Review la ngay da qua: vi the dang mo hom nay khong lien quan, ve len de hieu nham
    (S.mode === "live" ? (d.positions || {}).positions || [] : []).forEach((pos) => {
      addLine(pos.entry_price, { color: pos.side === "long" ? C.long : C.short, lineStyle: Dashed, lineWidth: 2,
                                 title: `Vị thế ${pos.side} ${num(pos.size, 4)}` });
    });
  }

  function buildMarkers(d, times, formingTime) {
    const span = SPAN[d.timeframe];
    const out = [];
    const snap = (ms) => Math.floor(ms / span) * span / 1000;
    (d.markers || []).forEach((m) => {
      const time = snap(m.t);
      if (!times.has(time)) return;
      const up = m.direction === "long";
      const base = { time, text: `${letter(m.code)} ${m.event}` };
      if (m.event === "IN_ZONE") out.push({ ...base, position: "inBar", shape: "circle", color: C.entry });
      else if (m.event === "TRIGGERED") out.push({ ...base, position: up ? "belowBar" : "aboveBar", shape: up ? "arrowUp" : "arrowDown", color: up ? C.long : C.short });
      else out.push({ ...base, position: "aboveBar", shape: "square", color: m.event === "EXPIRED" ? C.neutral : C.short });
    });
    ((d.tr || {}).events || []).forEach((event) => {
      const time = snap(event.t);
      if (!times.has(time)) return;
      const low = ["SC", "ST", "SPRING", "TEST", "LPS"].includes(event.type);
      out.push({ time, text: event.type, position: low ? "belowBar" : "aboveBar", shape: "circle", size: 0.6, color: C.tr });
    });
    if (formingTime) out.push({ time: formingTime, text: "đang chạy", position: "aboveBar", shape: "circle", size: 0, color: C.muted });
    return out.sort((a, b) => a.time - b.time);
  }

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
    if (S.showLevels) {
      Object.entries((d.bias || {}).key_levels || {}).forEach(([name, price]) => levels.push({ price: Number(price), label: name }));
      (d.htf_levels || []).forEach((level) => levels.push({
        price: level.price, label: `${level.tf === "1w" ? "W" : "D"} swing ${level.kind === "high" ? "đỉnh" : "đáy"}`,
      }));
    }
    overlay.data = {
      span,
      axisFirst: candles.length ? candles[0][0] : null,
      tr: d.tr,
      zones: d.setups.filter((s) => s.status !== "CANCELLED").map((s) => ({
        code: s.code, low: s.zone_low, high: s.zone_high, start: s.created_ms,
        color: s.direction === "long" ? C.long : C.short, dim: TERMINAL.includes(s.status),
        extra: [s.entry, s.stop, s.target, s.invalidate_level],
      })),
      levels,
      scenarios: d.scenarios.filter((s) => s.points && s.points.length >= 2).map((s) => ({
        kind: s.kind, label: s.label, points: s.points, color: scenarioColor(s, d.setups),
      })),
    };
  }

  function priceFormat(candles) {
    const price = candles.length ? candles[candles.length - 1][4] : 1000;
    const precision = price >= 1000 ? 1 : price >= 10 ? 2 : price >= 1 ? 3 : 5;
    return { type: "price", precision, minMove: Math.pow(10, -precision) };
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
    candleSeries.applyOptions({ priceFormat: priceFormat(candles) });
    candleSeries.setData(rows);
    volumeSeries.setData(volumes);
    candleSeries.setMarkers(buildMarkers(d, times, formingTime));
    drawPriceLines(d);
    S.chart = { data: d, barCount: rows.length };

    const shown = Math.min(Number(BOOT.default_bars) || 96, candles.length || 1);
    const edge = rows.length - 1 + 4;
    if (fit) {
      if (S.mode === "review" && candles.length) {
        const first = candles.findIndex((c) => c[0] >= d.day_start_ms);
        const from = Math.max(0, (first < 0 ? 0 : first) - Math.round(shown / 4));
        scale.setVisibleLogicalRange({ from, to: Math.max(from + 20, rows.length + 2) });
      } else {
        scale.setVisibleLogicalRange({ from: Math.max(0, edge - shown - whitespace), to: edge });
      }
    } else if (wasAtEdge) {
      const width = before.to - before.from;
      scale.setVisibleLogicalRange({ from: edge - width, to: edge });
    } else if (before) {
      // Giu nguyen cho dang xem: co nen moi thi cua so 500 nen truot, bu dung so nen da truot
      const shift = oldFirst !== null && candles.length ? (oldFirst - candles[0][0]) / span : 0;
      scale.setVisibleLogicalRange({ from: before.from + shift, to: before.to + shift });
    }
    overlay.refresh();

    const notes = [];
    if (!candles.length) notes.push(`Chưa có nến ${TF_LABEL[d.timeframe]} của ${d.symbol} trong kho.`);
    if (d.plan.source === "origin") notes.push(`Kế hoạch gốc, ghép từ R${d.plan.revisions_used.join(", R")}.`);
    if (d.plan.source === "revision") notes.push(`Đang xem kế hoạch revision ${d.plan.revision}.`);
    if (S.mode === "review" && d.plan.source === "live") notes.push("Ngày này chưa có snapshot, đang vẽ kế hoạch hiện tại.");
    $("chartNote").textContent = notes.join(" ");
  }

  function focusSetup(setup) {
    S.selected = S.selected === setup.code ? null : setup.code;
    document.querySelectorAll(".setup").forEach((card) => card.classList.toggle("on", card.dataset.code === S.selected));
    if (!S.chart) return;
    drawPriceLines(S.chart.data);
    if (S.selected) {
      const candles = S.chart.data.candles;
      const marks = (S.chart.data.markers || []).filter((m) => m.code === setup.code);
      const at = marks.length ? marks[marks.length - 1].t : setup.created_ms;
      let index = candles.findIndex((c) => c[0] >= at);
      if (index < 0) index = candles.length - 1;
      const scale = chart.timeScale();
      const range = scale.getVisibleLogicalRange();
      if (range && index >= 0) {
        const width = range.to - range.from;
        const right = Math.min(S.chart.barCount + 3, index + width * 0.6);
        scale.setVisibleLogicalRange({ from: right - width, to: right });
      }
      candleSeries.priceScale().applyOptions({ autoScale: true });
      if (window.innerWidth < 1024) $("chartPanel").scrollIntoView({ behavior: "smooth", block: "start" });
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

  function renderSummary(d) {
    const box = $("summary");
    const bias = d.bias;
    const tone = bias ? { long: "long", short: "short" }[bias.direction] || "neutral" : "neutral";
    const word = bias ? { long: "LONG", short: "SHORT", neutral: "ĐỨNG NGOÀI" }[bias.direction] || bias.direction : "Chưa có bias";
    const tiles = [
      tile("Bias", bias && bias.phase ? `${word} · Pha ${bias.phase}` : word, bias ? bias.summary : null, { tone, clamp: true }),
      tile("Giá M5 đã đóng", d.last_m5 ? num(d.last_m5.c) : "—", d.last_m5 ? `Nến ${hm(d.last_m5.t)}` : "Chưa có nến M5"),
      tile("TR khoá", d.tr ? `${num(d.tr.tr_low)} – ${num(d.tr.tr_high)}` : "—", d.tr && d.tr.since ? `Từ ${full(d.tr.since)}` : null),
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

  function renderSetups(rows, price) {
    const box = $("setups");
    if (!rows.length) {
      box.replaceChildren(el("p", { class: "empty" }, `Không có setup cho ${S.symbol} ngày ${S.date}.`));
      return;
    }
    box.replaceChildren(...rows.map((setup) => setupCard(setup, price)));
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
    return el("article", {
      class: `setup${S.selected === setup.code ? " on" : ""}${TERMINAL.includes(setup.status) ? " done" : ""}`,
      "data-code": setup.code,
      on: { click: () => focusSetup(setup) },
    },
    el("div", { class: "setup-head" },
      el("span", { class: "setup-code" }, setup.code),
      el("span", { class: "pill " + dir }, setup.direction.toUpperCase()),
      el("span", { class: "pill " + (STATUS_CLASS[setup.status] || "") }, setup.status),
      counter),
    el("dl", { class: "facts" },
      fact("Trigger", trigger, true),
      fact("Vùng", `${num(setup.zone_low)} – ${num(setup.zone_high)}`, true),
      fact("Entry", num(setup.entry)),
      fact("R:R", setup.rr === null || setup.rr === undefined ? "—" : num(setup.rr, 2)),
      fact("SL", num(setup.stop)),
      fact("TP", num(setup.target)),
      fact("Điều kiện huỷ", invalid, true),
      S.mode === "live" ? fact("Cách vùng", distanceText(setup, price), true) : null),
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
      main: `${e.code ? letter(e.code) + " · " : ""}${e.from_status || e.to_status ? `${e.from_status || "—"} → ${e.to_status || "—"}` : e.event}`,
      sub: [e.from_status || e.to_status ? e.event : null, e.price !== null && e.price !== undefined ? `giá ${num(e.price)}` : null,
            e.notify === "sent" ? "đã gửi tin" : e.notify === "error" ? "lỗi gửi tin" : null].filter(Boolean).join(" · "),
      fail: e.notify === "error",
    })).concat(snapshots.map((s) => ({
      at: s.created_at, main: `Kế hoạch R${s.revision}`, sub: TRIGGER_LABEL[s.trigger] || s.trigger, fail: false,
    })));
    rows.sort((a, b) => b.at - a.at);
    const box = $("timeline");
    if (!rows.length) {
      box.replaceChildren(el("li", { class: "empty", style: "display:block" }, "Chưa có sự kiện nào trong ngày."));
      return;
    }
    box.replaceChildren(...rows.map((row) => el("li", {},
      el("time", {}, hm(row.at)),
      el("div", {}, el("div", {}, row.main),
        row.sub ? el("div", { class: "sub" + (row.fail ? " fail" : "") }, row.sub) : null))));
  }

  function renderFills(block) {
    const panel = $("fillsPanel");
    panel.hidden = !(OWNER && S.mode === "review");
    if (panel.hidden) return;
    const box = $("fills");
    if (!block || !block.enabled) { box.replaceChildren(el("p", { class: "empty" }, "Chưa bật đọc tài khoản sàn.")); return; }
    if (block.error) { box.replaceChildren(el("p", { class: "empty" }, "Không đọc được lệnh từ sàn.")); return; }
    if (!block.positions.length) { box.replaceChildren(el("p", { class: "empty" }, "Không có lệnh nào trong ngày.")); return; }
    const hide = !("realized_pnl" in block.positions[0]);
    box.replaceChildren(el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Mở lúc", "Hướng", "Giá vào", "KL", hide ? null : "PnL", "Setup"].filter(Boolean).map((h) => el("th", {}, h)))),
      el("tbody", {}, block.positions.map((p) => el("tr", {},
        el("td", {}, p.carried_in ? "Từ hôm trước" : String(p.opened_at || "").slice(11, 16)),
        el("td", { class: p.side === "long" ? "long" : "short" }, p.side),
        el("td", {}, num(p.entry_price)),
        el("td", {}, num(p.qty, 4)),
        hide ? null : el("td", {}, num(p.realized_pnl, 2)),
        el("td", {}, p.setup_code ? letter(p.setup_code) : el("span", { class: "pill warn" }, "Không gắn setup"))))))));
  }

  function renderStats(data) {
    const box = $("stats");
    if (!data.total.setups) {
      box.replaceChildren(el("p", { class: "empty" }, `Không có setup nào từ ${data.from} đến ${data.to}.`));
      return;
    }
    const pct = (value) => (value === null || value === undefined ? "—" : `${num(value, 1)}%`);
    const line = (row, cls) => el("tr", { class: cls || null },
      el("td", {}, row.key === "total" ? "Tổng" : row.key),
      el("td", {}, String(row.setups)), el("td", {}, pct(row.trigger_rate)),
      el("td", {}, String(row.closed)), el("td", {}, pct(row.win_rate)),
      el("td", {}, row.avg_r === null || row.avg_r === undefined ? "—" : num(row.avg_r, 2)));
    box.replaceChildren(
      el("p", { class: "empty" }, `${data.symbol || "Mọi cặp"} · ${data.from} đến ${data.to}. Win rate tính trên lệnh đã đóng.`),
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
      $("stats").replaceChildren(el("p", { class: "empty" }, err.message));
    }
  }

  // ---------------------------------------------------------------- tai du lieu

  function syncUrl() {
    const search = new URLSearchParams({ symbol: S.symbol, date: S.date, mode: S.mode, tf: S.tf });
    history.replaceState(null, "", BOOT.base + "?" + search);
  }

  function renderRevisions(plan) {
    const box = $("revision");
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
    try {
      const data = await api("/chart", {
        symbol: S.symbol, date: S.date, tf: S.tf, bars: MAX_BARS,
        revision: review ? S.revision : "", include_forming: review ? "" : "1",
      });
      if (ticket !== chartTicket) return;
      renderChart(data, fit);
      renderSummary(data);
      renderRevisions(data.plan);
      $("updated").textContent = `Cập nhật lúc ${hm(Date.now())}`;
      const fresh = data.freshness || {};
      setBanner(fresh.stale ? `Dữ liệu đang trễ: nến M5 cuối cách đây ${num(fresh.m5_age_minutes, 0)} phút, nến ${TF_LABEL[S.tf]} cách đây ${num(fresh.last_closed_bar_age_minutes, 0)} phút.` : "");
    } catch (err) {
      if (ticket === chartTicket) setBanner(`Không tải được dữ liệu: ${err.message}`);
    }
  }

  async function loadLists() {
    const ticket = ++listTicket;
    const base = { symbol: S.symbol, date: S.date };
    try {
      const [setups, events, snapshots, fills] = await Promise.all([
        api("/setups", base),
        api("/events", base),
        api("/snapshots", base),
        S.mode === "review" && OWNER ? api("/fills", base) : Promise.resolve(null),
      ]);
      if (ticket !== listTicket) return;
      renderSetups(setups.setups, setups.price);
      renderTimeline(events.events, snapshots.snapshots);
      renderFills(fills);
    } catch (err) {
      if (ticket === listTicket) setBanner(`Không tải được dữ liệu: ${err.message}`);
    }
  }

  function load(fit) { return Promise.all([loadChart(fit), loadLists()]); }

  function schedule() {
    clearTimeout(pollTimer);
    const seconds = Math.max(5, Number(BOOT.poll_seconds) || 60);
    pollTimer = setTimeout(async () => {
      if (!document.hidden && S.mode === "live") await load(false);
      schedule();
    }, seconds * 1000);
  }

  function markButtons(root, attr, value) {
    root.querySelectorAll(`button[${attr}]`).forEach((b) => b.classList.toggle("on", b.getAttribute(attr) === String(value)));
  }

  function applyTab() {
    markButtons($("tabs"), "data-tab", S.tab);
    $("tabs").hidden = S.mode !== "review";
    const stats = S.mode === "review" && S.tab === "stats";
    $("planView").hidden = stats;
    $("statsView").hidden = !stats;
    if (stats) loadStats();
    else window.dispatchEvent(new Event("resize"));
  }

  function setMode(mode) {
    S.mode = mode;
    S.tab = "plan";
    S.revision = "origin";
    markButtons(document, "data-mode", S.mode);
    applyTab();
    syncUrl();
    load(true);
  }

  function setup() {
    const symbols = BOOT.symbols.includes(S.symbol) ? BOOT.symbols : [S.symbol, ...BOOT.symbols];
    $("symbol").replaceChildren(...symbols.map((s) => el("option", { value: s, selected: s === S.symbol }, s)));
    $("date").value = S.date;
    $("date").max = BOOT.today;
    $("tfs").replaceChildren(...BOOT.timeframes.map((tf) => el("button", { type: "button", "data-tf": tf }, TF_LABEL[tf] || tf)));
    $("groupBy").replaceChildren(...BOOT.group_by.map((g) => el("option", { value: g }, `Nhóm theo ${g}`)));
    $("levels").checked = S.showLevels;
    markButtons(document, "data-mode", S.mode);
    markButtons($("tfs"), "data-tf", S.tf);
    markButtons($("ranges"), "data-range", S.range);

    $("symbol").addEventListener("change", (e) => { S.symbol = e.target.value; S.selected = null; S.revision = "origin"; syncUrl(); load(true); });
    $("date").addEventListener("change", (e) => {
      if (!e.target.value) { e.target.value = S.date; return; }
      S.date = e.target.value;
      S.selected = null;
      S.revision = "origin";
      if (S.date < BOOT.today && S.mode === "live") setMode("review");
      else { syncUrl(); load(true); }
    });
    document.querySelectorAll("button[data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    $("tfs").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tf]");
      if (!b) return;
      S.tf = b.dataset.tf;
      markButtons($("tfs"), "data-tf", S.tf);
      syncUrl();
      loadChart(true);
    });
    $("revision").addEventListener("change", (e) => { S.revision = e.target.value; loadChart(true); });
    $("levels").addEventListener("change", (e) => {
      S.showLevels = e.target.checked;
      savePref("levels", S.showLevels);
      if (!S.chart) return;
      buildOverlay(S.chart.data);
      drawPriceLines(S.chart.data);
      overlay.refresh();
    });
    $("tabs").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) { S.tab = b.dataset.tab; applyTab(); }
    });
    $("ranges").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-range]");
      if (!b) return;
      S.range = Number(b.dataset.range);
      markButtons($("ranges"), "data-range", S.range);
      loadStats();
    });
    $("groupBy").addEventListener("change", (e) => { S.groupBy = e.target.value; loadStats(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && S.mode === "live") load(false); });

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

    applyTab();
    syncUrl();
    load(true);
    schedule();
  }

  setup();
})();
