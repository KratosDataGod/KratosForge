// The notebook Chart view's builder (Preview): a no-code chart of a cell's whole result, like
// Databricks' visualizations and Fabric's chart customization. The engine aggregates every row
// (frames.py chart, over notebook_frame_chart); this file only draws. The chart a cell shows is
// kept in its metadata (cell.meta.chart, by column name), so it comes back after a reload or a rerun.
// More charts of the same result (Databricks' "+ Visualization") are kept in cell.meta.charts, after it.

const CHART_KINDS = [["bar", "Bar"], ["stacked", "Stacked bar"], ["line", "Line"], ["area", "Area"], ["scatter", "Scatter"],
  ["pie", "Pie"], ["histogram", "Histogram"], ["box", "Box"]];
const CHART_AGGS = [["sum", "Sum"], ["avg", "Average"], ["count", "Count"], ["max", "Max"], ["min", "Min"]];
// Categorical slots in fixed order (validated for colour vision deficiency); "Other" is grey.
const CHART_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"];
const CHART_OTHER = "#8a8986";

function chartColor(name, i) { return name === "Other" ? CHART_OTHER : CHART_COLORS[i % CHART_COLORS.length]; }

const MAX_CHARTS = 10; // charts per cell: the first in meta.chart, the rest in meta.charts

// The saved chart number ``i`` of a cell (0 is meta.chart), and how many the cell has.
function savedChart(cell, i) { return i ? cell?.meta?.charts?.[i - 1] : cell?.meta?.chart; }
function chartCount(cell) { return 1 + (Array.isArray(cell?.meta?.charts) ? cell.meta.charts.length : 0); }

// A chart's view name: its title, else Chart, Chart 2, Chart 3... A title an earlier chart
// already has gets the chart's number too, so no two views read the same.
function chartName(cell, i) {
  const t = savedChart(cell, i)?.title;
  if (!t) return i ? `Chart ${i + 1}` : "Chart";
  const short = t.length > 24 ? `${t.slice(0, 23)}…` : t;
  const taken = Array.from({ length: i }, (_, j) => savedChart(cell, j)?.title).includes(t);
  return taken ? `${short} (${i + 1})` : short;
}

// "+ Chart": a new chart after the others, starting from the default for this result.
function addChart(o, numeric, cell) {
  cell.meta = cell.meta || {};
  cell.meta.charts = Array.isArray(cell.meta.charts) ? cell.meta.charts : [];
  if (!cell.meta.chart) cell.meta.chart = { ...chartConfig(o, numeric, cell, 0) };
  cell.meta.charts.push({ ...defaultChart(o, numeric) });
  if (typeof markDirty === "function") markDirty();
  return chartCount(cell) - 1;
}

function removeChart(o, cell, i) {
  if (!i || !Array.isArray(cell?.meta?.charts)) return;
  cell.meta.charts.splice(i - 1, 1);
  if (!cell.meta.charts.length) delete cell.meta.charts;
  if (o.charts) o.charts.splice(i, 1);
  if (typeof markDirty === "function") markDirty();
}

// The chart a table output starts with: the cell's saved chart if its columns still exist, else
// the first text column by the first number column.
function chartConfig(o, numeric, cell, i = 0) {
  const held = chartState(o, i);
  if (held.cfg) return held.cfg;
  const saved = savedChart(cell, i);
  const has = (c) => c === null || c === undefined || o.columns.includes(c);
  if (saved && has(saved.x) && has(saved.y) && has(saved.series)) return (held.cfg = { ...saved });
  return (held.cfg = defaultChart(o, numeric));
}

// What an output holds for its chart ``i``: the settings in use and the engine's last answer.
function chartState(o, i) {
  o.charts ||= [];
  return (o.charts[i] ||= {});
}

function defaultChart(o, numeric) {
  const nums = o.columns.filter((_, i) => numeric[i]);
  const cats = o.columns.filter((_, i) => !numeric[i]);
  const x = cats[0] || o.columns[0];
  return { kind: "bar", x, y: nums.find((c) => c !== x) || null, agg: nums.length ? "sum" : "count", series: null, title: "" };
}

// onRemove: called after chart ``i`` (not the first) is removed, so the output can show another view.
function renderChartBuilder(o, numeric, cell, i = 0, onRemove = null) {
  const cfg = chartConfig(o, numeric, cell, i);
  const held = chartState(o, i);
  const box = h("div", { class: "chart-box" });
  const redraw = () => box.replaceWith(renderChartBuilder(o, numeric, cell, i, onRemove));
  const change = (key, value) => {
    cfg[key] = value === "" ? null : value;
    delete held.data;
    if (cell) {
      cell.meta = cell.meta || {};
      if (i) cell.meta.charts[i - 1] = { ...cfg };
      else cell.meta.chart = { ...cfg };
      if (typeof markDirty === "function") markDirty();
    }
    if (key === "title" && onRemove) onRemove(i, "renamed");
    else redraw();
  };
  const select = (label, key, options, none) => h("label", { class: "chart-opt" }, label, h("select", {
    onchange: (e) => change(key, e.target.value), "aria-label": label },
  none ? h("option", { value: "", selected: cfg[key] === null || null }, none) : null,
  options.map(([v, text]) => h("option", { value: v, selected: cfg[key] === v || null }, text))));
  const cols = o.columns.map((c) => [c, c]);
  const numCols = o.columns.filter((_, i) => numeric[i]).map((c) => [c, c]);
  const needsX = cfg.kind !== "histogram";
  const usesAgg = ["bar", "stacked", "line", "area", "pie"].includes(cfg.kind);
  const usesSeries = ["bar", "stacked", "line", "area", "scatter"].includes(cfg.kind);
  const title = h("input", { type: "text", class: "chart-title-input", placeholder: "Chart title", value: cfg.title || "", "aria-label": "Chart title" });
  title.addEventListener("change", () => change("title", title.value));
  box.append(h("div", { class: "chart-opts" },
    select("Chart", "kind", CHART_KINDS),
    needsX ? select("X axis", "x", cols) : null,
    select("Values", "y", numCols.length ? numCols : cols, usesAgg ? "Row count" : null),
    usesAgg && cfg.y ? select("Aggregate", "agg", CHART_AGGS) : null,
    usesSeries ? select("Series", "series", cols.filter(([c]) => c !== cfg.x), "None") : null,
    title,
    !i && cell?.meta?.chart ? btn("Reset", "undo", () => { delete cell.meta.chart; delete held.cfg; delete held.data; if (typeof markDirty === "function") markDirty(); redraw(); }, "tool outline small") : null,
    i ? btn("Remove chart", "delete", () => { removeChart(o, cell, i); if (onRemove) onRemove(i, "removed"); }, "tool outline small") : null));
  const area = h("div", { class: "chart-area", role: "status" }, "Charting every row...");
  box.append(area);
  const draw = (d) => {
    if (d.gone) { area.textContent = "Run the cell again to chart this result: the kernel no longer holds it."; return; }
    area.replaceChildren(cfg.title ? h("div", { class: "chart-title" }, cfg.title) : null, chartSvg(d, cfg), chartLegend(d), chartNote(d));
  };
  if (held.data) draw(held.data);
  else {
    const idx = (c) => (c === null || c === undefined ? null : o.columns.indexOf(c));
    rpc("notebook_frame_chart", { frame: o.frame, kind: cfg.kind, x: needsX ? idx(cfg.x) : null, y: idx(cfg.y),
      agg: cfg.y ? cfg.agg : "count", series: usesSeries ? idx(cfg.series) : null })
      .then((d) => { if (!d.gone) held.data = d; draw(d); }, (err) => { area.textContent = `Could not draw the chart: ${friendlyError(err).text}`; });
  }
  return box;
}

function chartLegend(d) {
  const names = d.series?.map((s) => s.name).filter((n) => n !== null) || [];
  const scatterNames = d.points ? [...new Set(d.points.map((p) => p[2]).filter((n) => n !== null))].slice(0, 7) : [];
  const list = d.kind === "pie" ? d.categories.slice(0, 7).map(String) : names.length ? names : scatterNames;
  if (list.length < 2) return null;
  return h("ul", { class: "chart-legend", "aria-label": "Legend" }, list.map((n, i) =>
    h("li", {}, h("span", { class: "chart-swatch", style: `background:${chartColor(n, i)}` }), n)));
}

function chartNote(d) {
  const notes = [`From all ${d.rows.toLocaleString()} rows.`];
  if (d.truncated) notes.push("Showing the first 60 values of the X axis.");
  if (d.sampled) notes.push(`${d.points.length.toLocaleString()} of ${d.total.toLocaleString()} points, sampled evenly.`);
  return h("div", { class: "more" }, notes.join(" "));
}

function chartSvg(d, cfg) {
  const W = 720, H = 300, L = 60, R = 16, T = 12, B = 56;
  const ns = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, text) => {
    const n = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
    if (text != null) n.textContent = text;
    return n;
  };
  const tip = (node, text) => { node.append(el("title", {}, text)); return node; };
  const fmt = (v) => (v === null || v === undefined ? "" : Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : `${+Number(v).toFixed(2)}`);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img",
    "aria-label": cfg.title || `${cfg.kind} chart of ${cfg.y || "row count"}${cfg.x && cfg.kind !== "histogram" ? ` by ${cfg.x}` : ""}` });
  const empty = () => { svg.append(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "chart-label" }, "Nothing to chart")); return svg; };
  const plotW = W - L - R, plotH = H - T - B;
  const axis = (lo, hi) => {
    const span = hi - lo || 1;
    const y = (v) => T + plotH * (1 - (v - lo) / span);
    for (let k = 0; k <= 4; k++) {
      const v = lo + (span * k) / 4;
      svg.append(el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "chart-grid" }),
        el("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", class: "chart-label" }, fmt(v)));
    }
    return y;
  };
  const xLabels = (labels, step) => {
    const every = Math.ceil(labels.length / 14);
    labels.forEach((t, i) => {
      if (i % every) return;
      const s = String(t ?? "null");
      svg.append(el("text", { x: L + step * (i + 0.5), y: H - B + 16, "text-anchor": "middle", class: "chart-label" }, s.length > 12 ? `${s.slice(0, 11)}…` : s));
    });
  };

  if (d.kind === "histogram") {
    if (!d.counts.some(Boolean)) return empty();
    const y = axis(0, Math.max(...d.counts));
    const step = plotW / d.counts.length, width = (d.max - d.min) / d.counts.length;
    d.counts.forEach((n, i) => {
      const lo = d.min + width * i;
      svg.append(tip(el("rect", { x: L + step * i + 1, y: y(n), width: Math.max(1, step - 2), height: Math.max(0, y(0) - y(n)), rx: 2, fill: CHART_COLORS[0] }),
        `${fmt(lo)} to ${fmt(lo + width)}: ${n.toLocaleString()} rows`));
    });
    svg.append(el("text", { x: L, y: H - 8, class: "chart-label" }, fmt(d.min)),
      el("text", { x: W - R, y: H - 8, "text-anchor": "end", class: "chart-label" }, fmt(d.max)));
    return svg;
  }
  if (d.kind === "scatter") {
    if (!d.points.length) return empty();
    const xs = d.points.map((p) => p[0]), ys = d.points.map((p) => p[1]);
    const xMin = Math.min(...xs), xSpan = Math.max(...xs) - xMin || 1;
    const y = axis(Math.min(0, ...ys), Math.max(0, ...ys));
    const order = [...new Set(d.points.map((p) => p[2]))];
    d.points.forEach(([px, py, s]) => svg.append(tip(el("circle", { cx: L + (plotW * (px - xMin)) / xSpan, cy: y(py), r: 4,
      fill: chartColor(s, Math.max(0, order.indexOf(s))), stroke: "#fff", "stroke-width": 1 }), `${s !== null ? `${s}: ` : ""}${fmt(px)}, ${fmt(py)}`)));
    svg.append(el("text", { x: L, y: H - 8, class: "chart-label" }, fmt(xMin)),
      el("text", { x: W - R, y: H - 8, "text-anchor": "end", class: "chart-label" }, fmt(xMin + xSpan)));
    return svg;
  }
  if (d.kind === "box") {
    if (!d.boxes.length) return empty();
    const y = axis(Math.min(...d.boxes.map((b) => b.min)), Math.max(...d.boxes.map((b) => b.max)));
    const step = plotW / d.boxes.length, bw = Math.max(4, step * 0.5);
    d.boxes.forEach((b, i) => {
      const cx = L + step * (i + 0.5);
      const g = tip(el("g", {}), `${b.x}: min ${fmt(b.min)}, Q1 ${fmt(b.q1)}, median ${fmt(b.median)}, Q3 ${fmt(b.q3)}, max ${fmt(b.max)} (${b.n.toLocaleString()} rows)`);
      g.append(el("line", { x1: cx, x2: cx, y1: y(b.max), y2: y(b.min), stroke: CHART_COLORS[0], "stroke-width": 2 }),
        el("rect", { x: cx - bw / 2, y: y(b.q3), width: bw, height: Math.max(1, y(b.q1) - y(b.q3)), rx: 2, fill: "#e9eef7", stroke: CHART_COLORS[0], "stroke-width": 2 }),
        el("line", { x1: cx - bw / 2, x2: cx + bw / 2, y1: y(b.median), y2: y(b.median), stroke: "#12264a", "stroke-width": 2 }));
      svg.append(g);
    });
    xLabels(d.boxes.map((b) => b.x), step);
    return svg;
  }
  const cats = d.categories;
  if (!cats.length) return empty();
  if (d.kind === "pie") {
    let slices = cats.map((c, i) => [c, d.series[0].values[i] || 0]).filter(([, v]) => v > 0);
    if (slices.length > 7) slices = [...slices.slice(0, 6), ["Other", slices.slice(6).reduce((a, [, v]) => a + v, 0)]];
    const total = slices.reduce((a, [, v]) => a + v, 0);
    if (!total) return empty();
    const cx = W / 2, cy = H / 2, r = H / 2 - 16;
    let a0 = -Math.PI / 2;
    slices.forEach(([c, v], i) => {
      const a1 = a0 + (2 * Math.PI * v) / total;
      const large = a1 - a0 > Math.PI ? 1 : 0;
      const p = (a) => `${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`;
      const path = slices.length === 1 ? `M${cx - r},${cy}a${r},${r} 0 1,0 ${2 * r},0a${r},${r} 0 1,0 ${-2 * r},0`
        : `M${cx},${cy}L${p(a0)}A${r},${r} 0 ${large} 1 ${p(a1)}Z`;
      svg.append(tip(el("path", { d: path, fill: chartColor(String(c), i), stroke: "#fff", "stroke-width": 2 }),
        `${c ?? "null"}: ${fmt(v)} (${((100 * v) / total).toFixed(1)}%)`));
      a0 = a1;
    });
    return svg;
  }
  const series = d.series;
  const stacked = d.kind === "stacked" || d.kind === "area" && series.length > 1;
  const sums = cats.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0));
  const all = stacked ? sums : series.flatMap((s) => s.values.filter((v) => v !== null));
  const negs = series.flatMap((s) => s.values.filter((v) => v !== null && v < 0));
  const y = axis(Math.min(0, ...negs, ...(stacked ? [] : all)), Math.max(0, ...all));
  const step = plotW / cats.length;
  const label = (c, s, v) => `${c ?? "null"}${s.name !== null ? `, ${s.name}` : ""}: ${fmt(v)}`;
  if (d.kind === "bar" || d.kind === "stacked") {
    const groupW = Math.max(2, step * 0.75);
    const base = cats.map(() => 0);
    series.forEach((s, si) => {
      s.values.forEach((v, i) => {
        if (v === null) return;
        let x0, w, top, bottom;
        if (stacked) {
          x0 = L + step * i + (step - groupW) / 2; w = groupW;
          top = y(base[i] + Math.max(v, 0)); bottom = y(base[i]); base[i] += Math.max(v, 0);
        } else {
          w = Math.max(2, groupW / series.length - 2); x0 = L + step * i + (step - groupW) / 2 + si * (w + 2);
          top = y(Math.max(v, 0)); bottom = y(Math.min(v, 0));
        }
        svg.append(tip(el("rect", { x: x0, y: top, width: w, height: Math.max(1, bottom - top - (stacked ? 2 : 0)), rx: 2, fill: chartColor(s.name, si) }), label(cats[i], s, v)));
      });
    });
  } else {
    const base = cats.map(() => 0);
    series.forEach((s, si) => {
      const color = chartColor(s.name, si);
      const pts = s.values.map((v, i) => (v === null ? null : [L + step * (i + 0.5), stacked ? (base[i] += v) : v]));
      const line = pts.filter(Boolean).map(([px, v], k) => `${k ? "L" : "M"}${px},${y(v)}`).join(" ");
      if (d.kind === "area" && line) {
        const first = pts.find(Boolean), last = [...pts].reverse().find(Boolean);
        svg.append(el("path", { d: `${line}L${last[0]},${y(0)}L${first[0]},${y(0)}Z`, fill: color, "fill-opacity": 0.18 }));
      }
      if (line) svg.append(el("path", { d: line, fill: "none", stroke: color, "stroke-width": 2 }));
      pts.forEach((p, i) => { if (p) svg.append(tip(el("circle", { cx: p[0], cy: y(p[1]), r: 4, fill: color, stroke: "#fff", "stroke-width": 2 }), label(cats[i], s, s.values[i]))); });
    });
  }
  xLabels(cats, step);
  return svg;
}
