(function () {
  "use strict";

  const DATA_URL = "dashboard.json";
  const SCHEMA_VERSION = "1.0";
  const REFRESH_MS = 15000;
  const REQUEST_TIMEOUT_MS = 10000;
  // Visual progress calibration only; readings and the dynamic chart remain uncapped.
  const SOLAR_PROGRESS_MAX_KW = 18.0;
  const SEGMENT_COUNT = 20;
  const TIME_ZONE = "Europe/Zurich";
  const DASH = "—";
  const EMPTY_COMPARISON = "Noch keine Vergleichsdaten verfügbar";
  const OVERALL_STATES = ["fresh", "degraded", "stale", "missing"];
  const COMPONENT_STATES = Object.freeze({
    solar_data: ["fresh", "stale", "offline"], weather: ["fresh", "cached", "missing", "invalid"],
    battery: ["available", "missing"], heat: ["available", "missing"], chart: ["available", "missing"],
    insight: ["available", "missing"], historical_comparison: ["available", "missing"]
  });
  const BATTERY_DIRECTIONS = ["charging", "discharging", "idle", "unavailable"];
  const GRID_DIRECTIONS = ["exporting", "importing", "idle", "unavailable"];
  const COMPARISON_DIRECTIONS = ["higher", "lower", "similar"];

  const batteryLabels = Object.freeze({ charging: "Batterie lädt", discharging: "Batterie liefert", idle: "Batterie inaktiv", unavailable: "Batterie nicht verfügbar" });
  const gridLabels = Object.freeze({ exporting: "Einspeisung", importing: "Netzbezug", idle: "Kein Netzfluss", unavailable: "Netz nicht verfügbar" });
  const componentMap = Object.freeze({
    solar_data: ["solar", "house-consumption", "grid-flow", "today"], battery: ["battery", "battery-flow"],
    heat: ["heat"], weather: ["weather"], chart: ["chart"], insight: ["insight"], historical_comparison: ["historical-comparison"]
  });
  const numberFormats = {
    decimal: new Intl.NumberFormat("de-CH", { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
    integer: new Intl.NumberFormat("de-CH", { maximumFractionDigits: 0 })
  };
  const dateFormat = new Intl.DateTimeFormat("de-CH", { weekday: "long", day: "2-digit", month: "long", year: "numeric", timeZone: TIME_ZONE });
  const timeFormat = new Intl.DateTimeFormat("de-CH", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: TIME_ZONE });
  const chartTimeFormat = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: TIME_ZONE });

  function record(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function finite(value) { return typeof value === "number" && Number.isFinite(value); }
  function nullableNumber(value) { return value === null || finite(value); }
  function nullableString(value) { return value === null || typeof value === "string"; }
  function nonEmptyString(value) { return typeof value === "string" && value.trim().length > 0; }
  function validInstant(value) { if (!nonEmptyString(value)) return null; const date = new Date(value); return Number.isNaN(date.getTime()) ? null : date; }
  function isoInstant(value) { return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && validInstant(value) !== null; }
  function isoDate(value) { if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false; const date = validInstant(`${value}T12:00:00Z`); return date !== null && date.toISOString().slice(0, 10) === value; }
  function clockTime(value) { return typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value); }
  function formatNumber(value, kind) { return finite(value) ? numberFormats[kind].format(value) : DASH; }
  function formatDate(value) { const date = isoDate(value) ? validInstant(`${value}T12:00:00Z`) : validInstant(value); return date ? dateFormat.format(date).toLocaleUpperCase("de-CH") : DASH; }
  function formatTime(value) { if (clockTime(value)) return value; const date = validInstant(value); return date ? timeFormat.format(date) : DASH; }
  function formatAge(value) { return finite(value) && value >= 0 ? `${Math.round(value)} s` : DASH; }
  function segmentCount(value, maximum) { if (!finite(value) || !finite(maximum) || maximum <= 0) return 0; return Math.max(0, Math.min(SEGMENT_COUNT, Math.round(value / maximum * SEGMENT_COUNT))); }
  function includes(values, value) { return values.includes(value); }

  function validFlow(flow, directions) {
    return record(flow) && nullableNumber(flow.power_kw) && nullableNumber(flow.magnitude_kw) && includes(directions, flow.direction);
  }
  function validHistorical(value) {
    if (value === null) return true;
    return record(value) && nonEmptyString(value.type) && nonEmptyString(value.statement) && includes(COMPARISON_DIRECTIONS, value.direction) &&
      nullableNumber(value.difference_percent) && nullableNumber(value.comparison_value_kwh) && nullableNumber(value.baseline_kwh) &&
      nullableString(value.reference_period) && (value.reference_day === null || isoDate(value.reference_day)) && (value.comparison_day === null || isoDate(value.comparison_day)) &&
      (value.baseline_days === null || (Number.isInteger(value.baseline_days) && value.baseline_days >= 0));
  }
  function validChartRow(row) {
    return record(row) && isoInstant(row.start_at) && isoInstant(row.end_at) &&
      validInstant(row.end_at).getTime() > validInstant(row.start_at).getTime() && nullableNumber(row.solar_power_kw) &&
      nullableNumber(row.house_consumption_kw) && nullableNumber(row.battery_power_kw) &&
      nullableNumber(row.battery_state_of_charge_percent) &&
      (row.battery_state_of_charge_percent === null ||
       (row.battery_state_of_charge_percent >= 0 && row.battery_state_of_charge_percent <= 100)) &&
      Number.isInteger(row.sample_count) && row.sample_count >= 0;
  }
  function validPayload(payload) {
    if (!record(payload) || payload.schema_version !== SCHEMA_VERSION || !isoInstant(payload.generated_at)) return false;
    const data = payload.data, status = payload.status, header = payload.header, live = payload.live, today = payload.today, chart = payload.chart, insight = payload.insight;
    if (!record(data) || !(data.timestamp === null || isoInstant(data.timestamp)) || !(data.latest_sample_at === null || isoInstant(data.latest_sample_at)) || !(data.age_seconds === null || (finite(data.age_seconds) && data.age_seconds >= 0))) return false;
    if (!record(status) || !includes(OVERALL_STATES, status.overall) || !includes(["fresh", "stale", "missing"], status.freshness) || !Array.isArray(status.affected_components) || !status.affected_components.every(value => Object.hasOwn(componentMap, value)) || !record(status.components)) return false;
    if (!Object.keys(componentMap).every(key => includes(COMPONENT_STATES[key], status.components[key]))) return false;
    if (!record(header) || !isoDate(header.local_date) || !isoInstant(header.local_time) || !nullableString(header.weather_condition) || !nullableNumber(header.sunshine_hours) || !(header.sunrise === null || clockTime(header.sunrise)) || !(header.sunset === null || clockTime(header.sunset))) return false;
    if (!record(live) || ![live.solar_power_kw, live.house_consumption_kw, live.heat_power_kw, live.battery_state_of_charge_percent].every(nullableNumber) || !validFlow(live.battery_flow, BATTERY_DIRECTIONS) || !validFlow(live.grid_flow, GRID_DIRECTIONS)) return false;
    if (!record(today) || ![today.yield_kwh, today.self_consumption_percent, today.co2_avoided_kg].every(nullableNumber)) return false;
    if (!record(chart) || chart.interval_minutes !== 5 || !isoDate(chart.local_date) || !Array.isArray(chart.series) || !chart.series.every(validChartRow)) return false;
    if (!record(insight) || !nullableString(insight.fact_id) || !nullableString(insight.family) || !nullableString(insight.line_1) || !nullableString(insight.line_2) || !(insight.selection_hour === null || isoInstant(insight.selection_hour)) || typeof insight.persisted !== "boolean") return false;
    return validHistorical(payload.historical_comparison);
  }

  function chartModel(series) {
    const points = series.map(row => {
      const date = validInstant(row.start_at); const parts = chartTimeFormat.formatToParts(date); const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
      return { minute: Number(values.hour) * 60 + Number(values.minute), time: date.getTime(), solar: row.solar_power_kw, house: row.house_consumption_kw, battery: row.battery_state_of_charge_percent };
    }).sort((a, b) => a.time - b.time);
    const values = [0]; points.forEach(point => [point.solar, point.house].forEach(value => { if (finite(value)) values.push(value); }));
    const maximum = Math.max(...values, 1), minimum = Math.min(...values, 0); const raw = Math.max(maximum - minimum, 1); const step = Math.pow(10, Math.floor(Math.log10(raw))) / 2;
    return { points, top: Math.ceil(maximum / step) * step, bottom: Math.floor(minimum / step) * step };
  }
  function pathSequences(model, key, x, y) {
    const sequences = []; let current = [], previous = null;
    model.points.forEach(point => { const value = point[key]; if (!finite(value)) { if (current.length) sequences.push(current); current = []; previous = null; return; } if (previous !== null && point.time - previous > 10 * 60 * 1000) { if (current.length) sequences.push(current); current = []; } current.push([x(point.minute), y(value)]); previous = point.time; });
    if (current.length) sequences.push(current); return sequences;
  }
  function buildViewModel(payload) {
    if (!validPayload(payload)) return null;
    const batteryDirection = payload.live.battery_flow.direction, gridDirection = payload.live.grid_flow.direction, comparison = payload.historical_comparison;
    const fields = {
      "header.local_date": formatDate(payload.header.local_date), "header.local_time": formatTime(payload.header.local_time), "header.sunshine_hours": formatNumber(payload.header.sunshine_hours, "decimal"),
      "header.sunrise": formatTime(payload.header.sunrise), "header.sunset": formatTime(payload.header.sunset), "live.solar_power_kw": formatNumber(payload.live.solar_power_kw, "decimal"),
      "live.house_consumption_kw": formatNumber(payload.live.house_consumption_kw, "decimal"), "live.heat_power_kw": formatNumber(payload.live.heat_power_kw, "decimal"),
      "live.battery_state_of_charge_percent": formatNumber(payload.live.battery_state_of_charge_percent, "integer"), "live.battery_flow.direction": batteryLabels[batteryDirection],
      "live.battery_flow.magnitude_kw": formatNumber(payload.live.battery_flow.magnitude_kw, "decimal"), "live.grid_flow.direction": gridLabels[gridDirection],
      "live.grid_flow.magnitude_kw": formatNumber(payload.live.grid_flow.magnitude_kw, "decimal"), "today.yield_kwh": formatNumber(payload.today.yield_kwh, "decimal"),
      "today.self_consumption_percent": formatNumber(payload.today.self_consumption_percent, "integer"), "today.co2_avoided_kg": formatNumber(payload.today.co2_avoided_kg, "decimal"),
      "insight.line_1": payload.insight.line_1 || DASH, "insight.line_2": payload.insight.line_2 || DASH, "historical_comparison.statement": comparison ? comparison.statement : EMPTY_COMPARISON
    };
    const states = {};
    Object.entries(componentMap).forEach(([source, targets]) => { const componentState = payload.status.affected_components.includes(source) ? "degraded" : (["available", "cached"].includes(payload.status.components[source]) ? "fresh" : payload.status.components[source]); targets.forEach(target => { states[target] = componentState; }); });
    if (!comparison) states["historical-comparison"] = "missing";
    return { payload, fields, states, overall: payload.status.overall, statusLabel: payload.status.overall === "fresh" ? `LIVE · ${formatAge(payload.data.age_seconds)}` : payload.status.overall === "degraded" ? `EINGESCHRÄNKT · ${formatAge(payload.data.age_seconds)}` : payload.status.overall === "stale" ? `VERALTET · STAND ${formatTime(payload.generated_at)}` : "DATEN NICHT VERFÜGBAR", directions: { battery: batteryDirection, grid: gridDirection, heat: states.heat === "fresh" && payload.live.heat_power_kw !== null && payload.live.heat_power_kw >= 0.05 ? "active" : "idle", comparison: comparison ? comparison.direction : "unavailable" }, segments: { solar: segmentCount(payload.live.solar_power_kw, SOLAR_PROGRESS_MAX_KW), battery: segmentCount(payload.live.battery_state_of_charge_percent, 100) }, chart: chartModel(payload.chart.series) };
  }

  function chartGeometry(container) {
    return { width: Math.max(240, Math.round(container.clientWidth || 360)), height: Math.max(120, Math.round(container.clientHeight || 150)), fontSize: 11 };
  }
  function chartTimeTicks(width) {
    return width < 300 ? [0, 480, 960, 1440] : [0, 360, 720, 1080, 1440];
  }
  function chartLayout(container, model) {
    const geometry = chartGeometry(container);
    const axisValues = Array.from({ length: 5 }, (_, index) => model.top - (model.top - model.bottom) * index / 4);
    const axisLabels = axisValues.map(value => `${numberFormats.decimal.format(value)} kW`);
    const batteryAxisValues = [100, 75, 50, 25, 0];
    const batteryAxisLabels = batteryAxisValues.map(value => `${value} %`);
    const labelWidth = Math.max(...axisLabels.map(label => label.length * geometry.fontSize * 0.62));
    const batteryLabelWidth = Math.max(...batteryAxisLabels.map(label => label.length * geometry.fontSize * 0.62));
    const left = Math.ceil(labelWidth + 14);
    const right = Math.ceil(batteryLabelWidth + 14);
    return {
      ...geometry, axisValues, axisLabels, batteryAxisValues, batteryAxisLabels,
      labelWidth, batteryLabelWidth, left, labelX: left - 7,
      labelStart: left - 7 - labelWidth, right,
      batteryLabelX: geometry.width - right + 7,
      batteryLabelEnd: geometry.width - right + 7 + batteryLabelWidth,
      top: 12, bottom: 26
    };
  }
  function renderChart(container, model) {
    container.replaceChildren();
    if (!model.points.some(point => finite(point.solar) || finite(point.house) || finite(point.battery))) { const message = document.createElement("span"); message.textContent = "Tagesverlauf nicht verfügbar"; container.appendChild(message); container.setAttribute("aria-label", message.textContent); return; }
    const NS = "http://www.w3.org/2000/svg", geometry = chartLayout(container, model), { width, height, left, right, top, bottom } = geometry;
    const svg = document.createElementNS(NS, "svg"); svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.setAttribute("preserveAspectRatio", "xMidYMid meet"); svg.setAttribute("aria-hidden", "true");
    const x = minute => left + minute / 1440 * (width - left - right);
    const y = value => top + (model.top - value) / (model.top - model.bottom) * (height - top - bottom);
    const batteryY = value => top + (100 - value) / 100 * (height - top - bottom);
    function add(name, attrs, text) { const node = document.createElementNS(NS, name); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); if (name === "text") node.setAttribute("font-size", `${geometry.fontSize}px`); if (text !== undefined) node.textContent = text; svg.appendChild(node); return node; }
    geometry.axisValues.forEach((value, index) => {
      add("line", { x1: left, x2: width - right, y1: y(value), y2: y(value), class: Math.abs(value) < .0001 ? "chart-zero" : "chart-grid" });
      add("text", { x: geometry.labelX, y: y(value) + 4, "text-anchor": "end", class: "chart-axis" }, geometry.axisLabels[index]);
      add("text", { x: geometry.batteryLabelX, y: y(value) + 4, "text-anchor": "start", class: "chart-axis chart-axis-battery" }, geometry.batteryAxisLabels[index]);
    });
    chartTimeTicks(width).forEach(minute => add("text", { x: x(minute), y: height - 7, "text-anchor": minute === 0 ? "start" : minute === 1440 ? "end" : "middle", class: "chart-axis" }, `${String(minute / 60).padStart(2, "0")}:00`));
    pathSequences(model, "solar", x, y).forEach(sequence => { if (sequence.length > 1) add("path", { d: `M${sequence.map(pair => pair.join(",")).join(" L")} L${sequence.at(-1)[0]},${y(0)} L${sequence[0][0]},${y(0)} Z`, class: "chart-area-solar" }); });
    [["solar", "chart-line-solar", y], ["house", "chart-line-house", y], ["battery", "chart-line-battery", batteryY]].forEach(([key, css, scale]) => pathSequences(model, key, x, scale).forEach(sequence => { if (sequence.length === 1) add("circle", { cx: sequence[0][0], cy: sequence[0][1], r: 2.5, class: `chart-line ${css}` }); else add("path", { d: `M${sequence.map(pair => pair.join(",")).join(" L")}`, class: `chart-line ${css}` }); }));
    container.appendChild(svg); container.setAttribute("aria-label", "Tagesverlauf für Solarleistung, Hausverbrauch und Batteriestand");
  }
  function applyViewModel(root, view) {
    Object.entries(view.fields).forEach(([path, value]) => { const node = root.querySelector(`[data-field="${path}"]`); node.textContent = value; });
    Object.entries(view.states).forEach(([component, value]) => root.querySelector(`[data-component="${component}"]`).setAttribute("data-state", value));
    root.querySelector('[data-component="battery-flow"]').setAttribute("data-direction", view.directions.battery); root.querySelector('[data-component="grid-flow"]').setAttribute("data-direction", view.directions.grid); root.querySelector('[data-component="heat"]').setAttribute("data-direction", view.directions.heat); root.querySelector('[data-component="historical-comparison"]').setAttribute("data-direction", view.directions.comparison);
    [["live.solar_power_kw", view.segments.solar], ["live.battery_state_of_charge_percent", view.segments.battery]].forEach(([field, count]) => root.querySelectorAll(`[data-segments-for="${field}"] [data-segment]`).forEach((node, index) => index < count ? node.setAttribute("data-active", "true") : node.removeAttribute("data-active")));
    renderChart(root.querySelector('[data-field="chart.series"]'), view.chart); applyConnectionState(root, view.overall, view.statusLabel); root.setAttribute("aria-busy", "false");
  }
  function applyConnectionState(root, state, label) { const pill = root.querySelector('[data-component="data-status"]'), footer = root.querySelector('[data-component="system-status"]'), globalState = state === "degraded" ? "fresh" : state; pill.lastChild.textContent = label; pill.setAttribute("data-state", state); root.setAttribute("data-state", globalState); footer.setAttribute("data-state", globalState); footer.querySelector('[data-field="data.timestamp"]').textContent = label; root.setAttribute("aria-busy", "false"); }
  function applyInitialOffline(root) {
    root.querySelectorAll('[data-segment]').forEach(node => node.removeAttribute("data-active"));
    const chart = root.querySelector('[data-field="chart.series"]'); chart.replaceChildren(); const message = document.createElement("span"); message.textContent = "Tagesverlauf nicht verfügbar"; chart.appendChild(message); chart.setAttribute("aria-label", message.textContent);
    applyConnectionState(root, "offline", "OFFLINE");
  }

  function createController(root, options) {
    const settings = options || {}, fetcher = settings.fetcher || window.fetch.bind(window), schedule = settings.schedule || window.setTimeout.bind(window), cancel = settings.cancel || window.clearTimeout.bind(window), apply = settings.apply || applyViewModel, connection = settings.connection || applyConnectionState, initialOffline = settings.initialOffline || applyInitialOffline;
    let timer = null, running = false, lastGood = null, stopped = false;
    async function refresh() {
      if (running || stopped) return false; running = true; root.setAttribute("aria-busy", "true"); const controller = new AbortController(); const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      try { const response = await fetcher(DATA_URL, { cache: "no-store", signal: controller.signal }); if (!response.ok) throw new Error(`HTTP ${response.status}`); const payload = await response.json(); const view = buildViewModel(payload); if (!view) throw new Error("Ungültiges Dashboard-Payload"); apply(root, view); lastGood = view; }
      catch (_error) { if (lastGood) connection(root, "offline", `OFFLINE · STAND ${formatTime(lastGood.payload.generated_at)}`); else initialOffline(root); }
      finally { window.clearTimeout(timeout); running = false; if (!stopped) timer = schedule(refresh, REFRESH_MS); }
      return true;
    }
    function visible() { if (document.visibilityState === "visible") { if (timer !== null) cancel(timer); timer = null; refresh(); } }
    document.addEventListener("visibilitychange", visible); function stop() { stopped = true; if (timer !== null) cancel(timer); document.removeEventListener("visibilitychange", visible); }
    return { refresh, stop, isRunning: () => running, lastGood: () => lastGood };
  }

  const api = Object.freeze({ DATA_URL, SCHEMA_VERSION, REFRESH_MS, REQUEST_TIMEOUT_MS, SOLAR_PROGRESS_MAX_KW, SEGMENT_COUNT, TIME_ZONE, formatNumber, formatDate, formatTime, formatAge, segmentCount, validPayload, chartModel, pathSequences, chartGeometry, chartTimeTicks, chartLayout, buildViewModel, createController });
  if (typeof window !== "undefined") window.SolarDashboard = api;
  if (typeof document !== "undefined") { const root = document.querySelector('[data-component="dashboard"]'); if (root) createController(root).refresh(); }
}());
