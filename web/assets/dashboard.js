(function () {
  "use strict";

  const DATA_URL = "dashboard.json";
  const SCHEMA_VERSION = "1.0";
  const REFRESH_MS = 15000;
  const REQUEST_TIMEOUT_MS = 10000;
  const SOLAR_CAPACITY_KWP = 21.78;
  const SEGMENT_COUNT = 20;
  const TIME_ZONE = "Europe/Zurich";
  const DASH = "—";

  const batteryLabels = Object.freeze({
    charging: "Batterie lädt", discharging: "Batterie liefert",
    idle: "Batterie inaktiv", unavailable: "Batterie nicht verfügbar"
  });
  const gridLabels = Object.freeze({
    exporting: "Einspeisung", importing: "Netzbezug",
    idle: "Kein Netzfluss", unavailable: "Netz nicht verfügbar"
  });
  const componentMap = Object.freeze({
    solar_data: ["solar", "house-consumption", "grid-flow", "today"],
    battery: ["battery", "battery-flow"], heat: ["heat"],
    weather: ["weather"], chart: ["chart"], insight: ["insight"],
    historical_comparison: ["historical-comparison"]
  });
  const numberFormats = {
    decimal: new Intl.NumberFormat("de-CH", { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
    integer: new Intl.NumberFormat("de-CH", { maximumFractionDigits: 0 })
  };
  const dateFormat = new Intl.DateTimeFormat("de-CH", {
    weekday: "long", day: "2-digit", month: "long", year: "numeric", timeZone: TIME_ZONE
  });
  const timeFormat = new Intl.DateTimeFormat("de-CH", {
    hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: TIME_ZONE
  });

  function finite(value) { return typeof value === "number" && Number.isFinite(value); }
  function formatNumber(value, kind) { return finite(value) ? numberFormats[kind].format(value) : DASH; }
  function validDate(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? null : date; }
  function formatDate(value) { const date = validDate(value); return date ? dateFormat.format(date).toLocaleUpperCase("de-CH") : DASH; }
  function formatTime(value) { if (typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value)) return value; const date = validDate(value); return date ? timeFormat.format(date) : DASH; }
  function segmentCount(value, maximum) {
    if (!finite(value) || !finite(maximum) || maximum <= 0) return 0;
    return Math.max(0, Math.min(SEGMENT_COUNT, Math.round(value / maximum * SEGMENT_COUNT)));
  }
  function safeDirection(value, labels) { return Object.prototype.hasOwnProperty.call(labels, value) ? value : "unavailable"; }
  function get(object, path) { return path.split(".").reduce((value, key) => value == null ? undefined : value[key], object); }
  function validPayload(payload) {
    return Boolean(payload && payload.schema_version === SCHEMA_VERSION &&
      payload.data && payload.header && payload.live && payload.today &&
      payload.status && typeof payload.status.overall === "string" &&
      payload.status.components && payload.chart && Array.isArray(payload.chart.series) &&
      payload.insight && (payload.historical_comparison === null ||
        typeof payload.historical_comparison === "object"));
  }

  function chartModel(series) {
    const rows = Array.isArray(series) ? series : [];
    const points = rows.map(row => {
      const date = validDate(row.start_at || row.start || row.timestamp);
      if (!date) return null;
      const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: TIME_ZONE }).formatToParts(date);
      const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
      return { minute: Number(values.hour) * 60 + Number(values.minute), time: date.getTime(), solar: row.solar_power_kw, house: row.house_consumption_kw, battery: row.battery_power_kw };
    }).filter(Boolean).sort((a, b) => a.time - b.time);
    const candidates = [0];
    points.forEach(point => [point.solar, point.house, point.battery].forEach(value => { if (finite(value)) candidates.push(value); }));
    const maximum = Math.max(...candidates, 1);
    const minimum = Math.min(...candidates, 0);
    const step = Math.pow(10, Math.floor(Math.log10(Math.max(maximum - minimum, 1)))) / 2;
    const top = Math.ceil(maximum / step) * step;
    const bottom = Math.floor(minimum / step) * step;
    return { points, top, bottom: bottom === top ? 0 : bottom };
  }

  function pathSequences(model, key, x, y) {
    const sequences = []; let current = []; let previous = null;
    model.points.forEach(point => {
      const value = point[key];
      if (!finite(value)) { if (current.length) sequences.push(current); current = []; previous = null; return; }
      if (previous !== null && point.time - previous > 10 * 60 * 1000) { if (current.length) sequences.push(current); current = []; }
      current.push([x(point.minute), y(value)]); previous = point.time;
    });
    if (current.length) sequences.push(current);
    return sequences;
  }

  function renderChart(container, series, state) {
    while (container.firstChild) container.removeChild(container.firstChild);
    const model = chartModel(series);
    if (!model.points.length || !model.points.some(point => finite(point.solar) || finite(point.house) || finite(point.battery))) {
      const message = document.createElement("span"); message.textContent = state === "loading" ? "Daten werden geladen" : "Tagesverlauf nicht verfügbar";
      container.appendChild(message); container.setAttribute("aria-label", message.textContent); return;
    }
    const NS = "http://www.w3.org/2000/svg", width = 720, height = 250, left = 38, right = 8, top = 10, bottom = 25;
    const svg = document.createElementNS(NS, "svg"); svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    const x = minute => left + minute / 1440 * (width - left - right);
    const y = value => top + (model.top - value) / (model.top - model.bottom) * (height - top - bottom);
    function element(name, attrs, text) { const node = document.createElementNS(NS, name); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); if (text !== undefined) node.textContent = text; svg.appendChild(node); return node; }
    [model.top, 0, model.bottom].filter((v, i, a) => a.indexOf(v) === i).forEach(value => {
      element("line", { x1: left, x2: width - right, y1: y(value), y2: y(value), class: value === 0 ? "chart-zero" : "chart-grid" });
      element("text", { x: left - 5, y: y(value) + 3, "text-anchor": "end", class: "chart-axis" }, `${numberFormats.decimal.format(value)} kW`);
    });
    [0, 360, 720, 1080, 1440].forEach(minute => element("text", { x: x(minute), y: height - 5, "text-anchor": minute === 0 ? "start" : minute === 1440 ? "end" : "middle", class: "chart-axis" }, `${String(minute / 60).padStart(2, "0")}:00`));
    pathSequences(model, "solar", x, y).forEach(sequence => {
      if (sequence.length > 1) element("path", { d: `M${sequence.map(pair => pair.join(",")).join(" L")} L${sequence[sequence.length - 1][0]},${y(0)} L${sequence[0][0]},${y(0)} Z`, class: "chart-area-solar" });
    });
    [["solar", "chart-line-solar"], ["house", "chart-line-house"], ["battery", "chart-line-battery"]].forEach(([key, css]) => pathSequences(model, key, x, y).forEach(sequence => {
      if (sequence.length === 1) element("circle", { cx: sequence[0][0], cy: sequence[0][1], r: 2, class: `chart-line ${css}` });
      else element("path", { d: `M${sequence.map(pair => pair.join(",")).join(" L")}`, class: `chart-line ${css}` });
    }));
    container.appendChild(svg); container.setAttribute("aria-label", "Tagesverlauf für Solar, Hausverbrauch und Batteriefluss");
  }

  function createController(root, options) {
    const fetcher = options && options.fetcher || window.fetch.bind(window);
    const schedule = options && options.schedule || window.setTimeout.bind(window);
    let timer = null, running = false, lastGood = null, stopped = false;
    const statusPill = root.querySelector('[data-component="data-status"]');
    const footer = root.querySelector('[data-component="system-status"]');
    function textField(path, value) { const node = root.querySelector(`[data-field="${path}"]`); if (node) { node.textContent = value; node.closest(".placeholder")?.classList.toggle("placeholder", value === DASH); } }
    function componentState(value) { return ["fresh", "degraded", "stale", "missing", "offline"].includes(value) ? value : (["available", "cached"].includes(value) ? "fresh" : "missing"); }
    function state(component, value) { const node = root.querySelector(`[data-component="${component}"]`); if (node) node.setAttribute("data-state", componentState(value)); }
    function statusText(value, timestamp) {
      const at = formatTime(timestamp);
      const labels = { fresh: `LIVE · ${at}`, degraded: `LIVE · EINGESCHRÄNKT · ${at}`, stale: `VERALTET · ${at}`, missing: "DATEN NICHT VERFÜGBAR", offline: lastGood ? `OFFLINE · LETZTER STAND ${at}` : "OFFLINE" };
      statusPill.lastChild.textContent = labels[value] || labels.offline;
      statusPill.setAttribute("data-state", value); root.setAttribute("data-state", value); state("system-status", value);
      footer.querySelector('[data-field="data.timestamp"]').textContent = labels[value] || labels.offline;
    }
    function updateSegments(field, count) { root.querySelectorAll(`[data-segments-for="${field}"] [data-segment]`).forEach((node, index) => { if (index < count) node.setAttribute("data-active", "true"); else node.removeAttribute("data-active"); }); }
    function render(payload) {
      textField("header.local_date", formatDate(payload.header.local_date)); textField("header.local_time", formatTime(payload.header.local_time));
      textField("header.sunshine_hours", formatNumber(payload.header.sunshine_hours, "decimal")); textField("header.sunrise", formatTime(payload.header.sunrise)); textField("header.sunset", formatTime(payload.header.sunset));
      [["live.solar_power_kw", "decimal"], ["live.house_consumption_kw", "decimal"], ["live.heat_power_kw", "decimal"], ["live.battery_state_of_charge_percent", "integer"], ["today.yield_kwh", "decimal"], ["today.self_consumption_percent", "integer"], ["today.co2_avoided_kg", "decimal"]].forEach(([path, kind]) => textField(path, formatNumber(get(payload, path), kind)));
      const batteryDirection = safeDirection(get(payload, "live.battery_flow.direction"), batteryLabels); const gridDirection = safeDirection(get(payload, "live.grid_flow.direction"), gridLabels);
      textField("live.battery_flow.direction", batteryLabels[batteryDirection]); textField("live.grid_flow.direction", gridLabels[gridDirection]);
      textField("live.battery_flow.magnitude_kw", formatNumber(get(payload, "live.battery_flow.magnitude_kw"), "decimal")); textField("live.grid_flow.magnitude_kw", formatNumber(get(payload, "live.grid_flow.magnitude_kw"), "decimal"));
      root.querySelector('[data-component="battery-flow"]').setAttribute("data-direction", batteryDirection); root.querySelector('[data-component="grid-flow"]').setAttribute("data-direction", gridDirection);
      updateSegments("live.solar_power_kw", segmentCount(payload.live.solar_power_kw, SOLAR_CAPACITY_KWP)); updateSegments("live.battery_state_of_charge_percent", segmentCount(payload.live.battery_state_of_charge_percent, 100));
      textField("insight.line_1", typeof payload.insight?.line_1 === "string" ? payload.insight.line_1 : DASH); textField("insight.line_2", typeof payload.insight?.line_2 === "string" ? payload.insight.line_2 : DASH);
      const comparison = payload.historical_comparison; const comparisonNode = root.querySelector('[data-component="historical-comparison"]');
      const comparisonDirection = comparison && ["higher", "lower", "similar"].includes(comparison.direction) ? comparison.direction : "unavailable";
      textField("historical_comparison.statement", comparison && typeof comparison.statement === "string" ? comparison.statement : DASH); comparisonNode.setAttribute("data-direction", comparisonDirection);
      const overall = ["fresh", "degraded", "stale", "missing"].includes(payload.status.overall) ? payload.status.overall : "missing";
      Object.entries(componentMap).forEach(([source, targets]) => targets.forEach(target => state(target, payload.status.components?.[source] || (source === "historical_comparison" && !comparison ? "missing" : overall))));
      if (!comparison) state("historical-comparison", "missing");
      renderChart(root.querySelector('[data-field="chart.series"]'), payload.chart?.series, payload.status.components?.chart || overall);
      statusText(overall, payload.data.timestamp || payload.generated_at); root.setAttribute("aria-busy", "false");
    }
    async function refresh() {
      if (running || stopped) return false; running = true; root.setAttribute("aria-busy", "true");
      const controller = new AbortController(); const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      try {
        const response = await fetcher(DATA_URL, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        if (!validPayload(payload)) throw new Error("Unsupported schema or payload");
        render(payload); lastGood = payload;
      } catch (_error) {
        statusText("offline", lastGood && (lastGood.data.timestamp || lastGood.generated_at)); root.setAttribute("aria-busy", "false");
        if (!lastGood) { updateSegments("live.solar_power_kw", 0); updateSegments("live.battery_state_of_charge_percent", 0); renderChart(root.querySelector('[data-field="chart.series"]'), [], "offline"); }
      } finally {
        window.clearTimeout(timeout); running = false; if (!stopped) timer = schedule(refresh, REFRESH_MS);
      }
      return true;
    }
    function visible() { if (document.visibilityState === "visible") { if (timer !== null) window.clearTimeout(timer); timer = null; refresh(); } }
    document.addEventListener("visibilitychange", visible);
    function stop() { stopped = true; if (timer !== null) window.clearTimeout(timer); document.removeEventListener("visibilitychange", visible); }
    return { refresh, stop, render, isRunning: () => running, lastGood: () => lastGood };
  }

  const api = Object.freeze({ DATA_URL, SCHEMA_VERSION, REFRESH_MS, REQUEST_TIMEOUT_MS, SOLAR_CAPACITY_KWP, SEGMENT_COUNT, TIME_ZONE, validPayload, formatNumber, formatDate, formatTime, segmentCount, safeDirection, chartModel, pathSequences, createController });
  if (typeof window !== "undefined") window.SolarDashboard = api;
  if (typeof document !== "undefined") {
    const root = document.querySelector('[data-component="dashboard"]');
    if (root) { const controller = createController(root); controller.refresh(); }
  }
}());
