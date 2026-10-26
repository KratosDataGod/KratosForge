// A quick chart of a SQL query's result (WH-08), on the SQL page's Chart tab, for a lakehouse,
// a local project or a warehouse. The rows are read from the run's snapshot a page at a time (no
// query runs again) up to SQL_CHART_ROWS, grouped here, and drawn with the notebook chart's
// chartSvg and chartLegend (charts.js), so the two look alike.

const SQL_CHART_ROWS = 50000;
const SQL_CHART_KINDS = CHART_KINDS.filter(([k]) => k !== "box");
const SQL_CHART_CATS = 60;  // X axis values drawn; the rest are left off and the note says so
const SQL_CHART_SERIES = 7; // series drawn; the rest are summed as "Other"

// Which columns hold numbers: every value that isn't NULL is a number (and there is one).
function sqlNumericColumns(rows, width) {
  return Array.from({ length: width }, (_, i) => {
    let seen = false;
    for (const r of rows) {
      if (r[i] === null || r[i] === undefined) continue;
      if (typeof r[i] !== "number") return false;
      seen = true;
    }
    return seen;
  });
}

function sqlDefaultChart(columns, numeric) {
  const names = columns.map((c) => c.name);
  const x = names.find((_, i) => !numeric[i]) ?? names[0];
  const y = names.find((n, i) => numeric[i] && n !== x) ?? null;
  return { kind: "bar", x, y, agg: y ? "sum" : "count", series: null };
}

// The chart's data, in the shape chartSvg draws: { kind, categories, series: [{name, values}] },
// { kind: "scatter", points }, or { kind: "histogram", counts, min, max }. Plus rows and truncated.
function sqlChartData(columns, rows, cfg) {
  const at = (name) => (name ? columns.findIndex((c) => c.name === name) : -1);
  const xi = at(cfg.x), yi = at(cfg.y), si = at(cfg.series);
  const out = { kind: cfg.kind, rows: rows.length, truncated: false };
  if (cfg.kind === "histogram") {
    const vals = rows.map((r) => r[yi]).filter((v) => typeof v === "number");
    if (!vals.length) return { ...out, counts: [], min: 0, max: 0 };
    let min = Infinity, max = -Infinity;  // a loop: spreading 50,000 values as arguments can overflow the stack
    for (const v of vals) { if (v < min) min = v; if (v > max) max = v; }
    const bins = Math.min(30, Math.max(1, Math.ceil(Math.sqrt(vals.length))));
    const counts = new Array(bins).fill(0);
    for (const v of vals) counts[Math.min(bins - 1, Math.floor(((v - min) / (max - min || 1)) * bins))]++;
    return { ...out, counts, min, max };
  }
  if (cfg.kind === "scatter") {
    return { ...out, points: rows.filter((r) => typeof r[xi] === "number" && typeof r[yi] === "number")
      .map((r) => [r[xi], r[yi], si >= 0 ? String(r[si] ?? "null") : null]) };
  }
  const seriesOf = (r) => (si >= 0 && cfg.kind !== "pie" ? String(r[si] ?? "null") : null);
  const totals = new Map();
  for (const r of rows) totals.set(seriesOf(r), (totals.get(seriesOf(r)) || 0) + 1);
  const kept = [...totals.keys()].slice(0, SQL_CHART_SERIES - (totals.size > SQL_CHART_SERIES ? 1 : 0));
  const name = (r) => (kept.includes(seriesOf(r)) ? seriesOf(r) : "Other");
  // The first SQL_CHART_CATS X values, in the order they come; past those only a count, for the note.
  const cats = [], shown = new Set(), seen = new Set();
  let extra = 0;
  const groups = new Map();  // "category\u0000series" -> [sum, count, max, min]
  for (const r of rows) {
    const c = r[xi] ?? null;
    if (!seen.has(c)) {
      seen.add(c);
      if (cats.length < SQL_CHART_CATS) { cats.push(c); shown.add(c); } else extra++;
    }
    if (!shown.has(c)) continue;
    const key = `${c}\u0000${name(r)}`;
    const g = groups.get(key) || [0, 0, -Infinity, Infinity];
    const v = yi >= 0 ? r[yi] : null;
    if (yi < 0 || cfg.agg === "count") g[1]++;
    else if (typeof v === "number") { g[0] += v; g[1]++; g[2] = Math.max(g[2], v); g[3] = Math.min(g[3], v); }
    groups.set(key, g);
  }
  const value = (g) => {
    if (!g || !g[1]) return null;
    if (yi < 0 || cfg.agg === "count") return g[1];
    return { sum: g[0], avg: g[0] / g[1], max: g[2], min: g[3] }[cfg.agg] ?? g[0];
  };
  const names = [...new Set(rows.map(name))];
  return { ...out, truncated: extra > 0, more: extra, categories: cats,
    series: names.map((n) => ({ name: n, values: cats.map((c) => value(groups.get(`${c}\u0000${n}`))) })) };
}

// The Chart tab: settings over the chart. load() resolves to { columns, rows, total } (the first
// SQL_CHART_ROWS rows); held keeps the settings and rows while the tab is open.
function sqlChartView(held, load) {
  const box = h("div", { class: "chart-box sql-chart" });
  const area = h("div", { class: "chart-area", role: "status" }, "Reading the result...");
  function draw() {
    const { columns, rows, total } = held.data;
    const numeric = sqlNumericColumns(rows, columns.length);
    const cfg = held.cfg ||= sqlDefaultChart(columns, numeric);
    const change = (key, value) => { cfg[key] = value === "" ? null : value; draw(); };
    const select = (label, key, options, none) => h("label", { class: "chart-opt" }, label, h("select", {
      "aria-label": label, onchange: (e) => change(key, e.target.value) },
    none ? h("option", { value: "", selected: cfg[key] === null || null }, none) : null,
    options.map(([v, text]) => h("option", { value: v, selected: cfg[key] === v || null }, text))));
    const cols = columns.map((c) => [c.name, c.name]);
    const nums = columns.filter((_, i) => numeric[i]).map((c) => [c.name, c.name]);
    const usesAgg = ["bar", "stacked", "line", "area", "pie"].includes(cfg.kind);
    const usesSeries = ["bar", "stacked", "line", "area", "scatter"].includes(cfg.kind);
    if (cfg.kind === "histogram" || cfg.kind === "scatter") cfg.y ||= nums[0]?.[0] ?? null;
    const d = sqlChartData(columns, rows, cfg);
    const notes = [`From ${rows.length === total ? "all" : "the first"} ${rows.length.toLocaleString()} rows${rows.length === total ? "" : ` of ${total.toLocaleString()}`}.`];
    if (d.truncated) notes.push(`Showing the first ${SQL_CHART_CATS} of ${(SQL_CHART_CATS + d.more).toLocaleString()} values of the X axis.`);
    box.replaceChildren(h("div", { class: "chart-opts" },
      select("Chart", "kind", SQL_CHART_KINDS),
      cfg.kind !== "histogram" ? select("X axis", "x", cfg.kind === "scatter" ? nums : cols) : null,
      select("Values", "y", nums.length ? nums : cols, usesAgg ? "Row count" : null),
      usesAgg && cfg.y ? select("Aggregate", "agg", CHART_AGGS) : null,
      usesSeries ? select("Series", "series", cols.filter(([c]) => c !== cfg.x), "None") : null),
    h("div", { class: "chart-area" }, chartSvg(d, cfg), chartLegend(d), h("div", { class: "more" }, notes.join(" "))));
  }
  box.append(area);
  if (held.data) draw();
  else {
    load().then((data) => { held.data = data; draw(); },
      (err) => { area.textContent = `Could not read the result: ${String(err).replace(/^Error: /, "")}`; });
  }
  return box;
}
