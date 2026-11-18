// The SQL page's result grid (editor parity item 8, approved mockup 2026-11-16): click a header to sort,
// Filter shows a box under each header, and both apply to every row of the run's result snapshot on
// this PC (sql_page sort and filters, sidecar/fabric_desktop/sql_view.py). Nothing runs the query again.

// A tab's grid state: { sort: [{ column, desc }], filters: { [column]: text }, filterOn, filterError }.
function sqlGridState(tab) {
  return (tab.grid ||= { sort: [], filters: {}, filterOn: false, filterError: null });
}

// A new run of the tab: a fresh grid that keeps the column layout (widths, hidden columns, order), used
// again when the new result has the same column names.
function sqlGridCarry(g) {
  if (!g?.names) return null;
  return { sort: [], filters: {}, filterOn: false, filterError: null, names: g.names, widths: g.widths, hidden: g.hidden, order: g.order };
}

// What sql_page is sent for the grid's sort and filters (nothing when neither is set).
function sqlGridQuery(g) {
  const filters = Object.entries(g?.filters || {}).filter(([, t]) => String(t).trim())
    .map(([c, t]) => ({ column: Number(c), text: String(t) }));
  return { ...(g?.sort?.length ? { sort: g.sort } : {}), ...(filters.length ? { filters } : {}) };
}

// A header click: ascending, then descending, then the snapshot's own order.
function sqlGridNextSort(sort, column) {
  const now = (sort || []).find((s) => s.column === column);
  if (!now) return [{ column, desc: false }];
  if (!now.desc) return [{ column, desc: true }];
  return [];
}

// The band's row count for a page: "3 of 5 rows (filtered) · sorted by orders".
function sqlGridCount(r, columns) {
  const all = r.all_rows ?? r.total;
  let text = r.total === all ? `${r.total.toLocaleString()} row${r.total === 1 ? "" : "s"}`
    : `${r.total.toLocaleString()} of ${all.toLocaleString()} row${all === 1 ? "" : "s"} (filtered)`;
  const s = r.sort?.[0];
  if (s && columns[s.column]) text += ` · sorted by ${columns[s.column].name}`;  // the header's arrow says which way
  return text;
}

// The grid's table: sortable headers that can be resized, an optional row of filter boxes, the page's rows,
// in the columns Columns... shows and in its order. on.sort(column) and on.filter(column, text) change the
// view (the caller reads the page again).
function sqlResultGrid(r, g, on) {
  const vis = sqlGridLayoutFor(g, r.columns);
  const sortOf = (i) => (r.sort || []).find((s) => s.column === i);
  const widths = h("style", {}, sqlGridWidthCss(g.widths, sqlGridId(g)));
  const head = vis.map((i) => {
    const c = r.columns[i];
    const s = sortOf(i);
    return h("th", { title: c.type, "data-col": String(i), "aria-sort": s ? (s.desc ? "descending" : "ascending") : "none" },
      h("button", { class: "sql-sort", type: "button", onclick: () => on.sort(i),
        title: `Sort by ${c.name} (${c.type}). Sorts every row of the result on this PC; nothing runs again.` },
      c.name, s ? h("span", { class: "sql-sort-mark", "aria-hidden": "true" }, s.desc ? " ▼" : " ▲") : null),
      sqlColumnResizer(c, i, g, () => {
        widths.textContent = sqlGridWidthCss(g.widths, g.id);
        table.classList.toggle("sized", Object.keys(g.widths).length > 0);
      }));
  });
  // A column's pending filter is kept on the grid's state: a redraw while a page loads makes new boxes,
  // and typing in a column's new box replaces what its old one had waiting.
  const timers = (g.filterTimers ||= {});
  const wait = (i, fn) => { clearTimeout(timers[i]); timers[i] = fn ? setTimeout(() => { delete timers[i]; fn(); }, 450) : 0; };
  const filterRow = g.filterOn ? h("tr", { class: "sql-filter-row" }, h("td", { class: "i" }),
    vis.map((i) => {
      const c = r.columns[i];
      const bad = g.filterError?.column === i;
      const box = h("input", { type: "text", class: "sql-filter", "data-column": String(i), value: g.filters[i] || "",
        placeholder: "Filter", "aria-label": `Filter ${c.name}`, spellcheck: "false", ...(bad ? { "aria-invalid": "true" } : {}),
        title: "Text to find, or an operator and a value (= != > >= < <=), or null or not null",
        oninput: () => wait(i, () => on.filter(i, box.value)),
        onkeydown: (e) => {
          if (e.key === "Enter") { wait(i, null); on.filter(i, box.value); }
          if (e.key === "Escape" && box.value) { e.stopPropagation(); box.value = ""; wait(i, null); on.filter(i, ""); }
        } });
      return h("td", { "data-col": String(i) }, box);
    })) : null;
  // A column a person sized keeps its width: the table no longer stretches its columns to fill the grid.
  const table = h("table", { tabindex: "0", "data-grid": g.id, class: `sql-result-table${Object.keys(g.widths).length ? " sized" : ""}`,
    "aria-label": "Results. Arrow keys move, Shift and an arrow selects, Ctrl+C copies, Enter opens a value, Shift+F10 shows more." },
    h("thead", {}, h("tr", {}, h("th", { class: "i" }), head), filterRow),
    h("tbody", {}, r.rows.map((row, k) => h("tr", { "data-r": String(k) }, h("td", { class: "i" }, String(r.offset + k + 1)),
      vis.map((i, c) => {
        const v = row[i];
        return h("td", { "data-c": String(c), "data-col": String(i), class: `${typeof v === "number" ? "n" : ""}${v === null ? " null" : ""}` }, cellText(v));
      })))));
  if (on.select) sqlGridWire(table, r, g, on, vis);
  const hidden = r.columns.length - vis.length;
  const note = hidden ? h("p", { class: "muted-text sql-grid-note sql-grid-hidden" }, sqlGridHiddenNote(r.columns.length, vis, g.filters)) : null;
  const empty = !r.rows.length && r.total === 0 && (r.all_rows ?? 0) > 0
    ? h("p", { class: "muted-text sql-grid-note" }, "No rows match the filters.") : null;
  const error = g.filterError ? h("p", { class: "sql-grid-error", role: "alert" }, g.filterError.message) : null;
  return [widths, table, empty, error, note].filter(Boolean);
}

// ---------- column widths, and Columns... to hide and reorder them ----------

// The line under a grid with hidden columns. A filter on a hidden column still narrows the rows, so it says so.
function sqlGridHiddenNote(count, vis, filters) {
  const hidden = count - vis.length;
  const shown = new Set(vis);
  const filtering = Object.entries(filters || {}).filter(([k, t]) => String(t ?? "").trim() && !shown.has(Number(k))).length;
  return `${hidden.toLocaleString()} column${hidden === 1 ? " is" : "s are"} hidden. Columns... shows ${hidden === 1 ? "it" : "them"}.` +
    (filtering === 1 ? " A filter on a hidden column still applies." : filtering ? ` Filters on ${filtering} hidden columns still apply.` : "");
}

// The columns the grid shows, as positions in the result, in their order. The layout (widths, hidden
// columns, order) is for one set of column names: a result with other columns starts again.
function sqlGridLayoutFor(g, columns) {
  const names = columns.map((c) => c.name);
  if (!g.names || g.names.length !== names.length || g.names.some((n, k) => n !== names[k])) {
    Object.assign(g, { names, widths: {}, hidden: [], order: null });
  }
  const order = g.order?.length === names.length ? g.order : names.map((_, k) => k);
  const hidden = new Set(g.hidden || []);
  const vis = order.filter((k) => !hidden.has(k));
  g.vis = vis.length ? vis : order;  // Columns... keeps at least one shown
  return g.vis;
}

// Each grid's own id, so the widths a person set in one table never reach another (a notebook shows many).
function sqlGridId(g) {
  sqlGridId.count = (sqlGridId.count || 0) + (g.id ? 0 : 1);
  return (g.id ||= `g${sqlGridId.count}`);
}

// The style that holds the widths a person set in grid g: { [column]: px }, for that grid's table only.
function sqlGridWidthCss(widths, id) {
  const table = id ? `.sql-result-table[data-grid="${String(id).replace(/[^\w-]/g, "")}"]` : ".sql-result-table";
  return Object.entries(widths || {}).map(([k, w]) =>
    `${table} [data-col="${Number(k)}"] { width: ${Math.round(w)}px; min-width: ${Math.round(w)}px; max-width: ${Math.round(w)}px; }`).join("\n");
}

// The grid's column order, as positions in the result.
const sqlGridOrder = (g, columns) => (g.order?.length === columns.length ? g.order : columns.map((_, k) => k));

// A column moved up or down in Columns...: the new order.
function sqlGridMoveColumn(order, column, delta) {
  const out = [...order];
  const at = out.indexOf(column);
  const to = at + delta;
  if (at < 0 || to < 0 || to >= out.length) return out;
  out.splice(at, 1);
  out.splice(to, 0, column);
  return out;
}

// The handle at a header's right edge: drag to resize, double-click to fit the column again, or focus it
// and use the arrow keys.
function sqlColumnResizer(c, i, g, changed) {
  const set = (w) => {
    if (w === null) delete g.widths[i];
    else g.widths[i] = Math.max(40, Math.min(1200, w));
    changed();
  };
  const handle = h("span", { class: "sql-col-resize", role: "separator", "aria-orientation": "vertical", tabindex: "0",
    "aria-label": `Width of ${c.name}`, title: "Drag to resize. Double-click to fit. Arrow keys resize too.",
    onpointerdown: (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const start = e.clientX;
      const width = handle.parentElement.getBoundingClientRect().width;
      handle.setPointerCapture?.(e.pointerId);
      handle.onpointermove = (m) => set(width + m.clientX - start);
      handle.onpointerup = handle.onpointercancel = () => { handle.onpointermove = handle.onpointerup = handle.onpointercancel = null; };
    },
    ondblclick: (e) => { e.stopPropagation(); set(null); },
    onkeydown: (e) => {
      const now = g.widths[i] ?? handle.parentElement.getBoundingClientRect().width;
      if (e.key === "ArrowLeft") set(now - 16);
      else if (e.key === "ArrowRight") set(now + 16);
      else if (e.key === "Enter" || e.key === "Delete") set(null);
      else return;
      e.preventDefault();
      e.stopPropagation();
    } });
  return handle;
}

// The grid's right-click menu (the mockup's Copy as) at a cell: on.copy(format), on.open(), on.stats().
// Each item gives focus back to the table first.
function sqlGridMenu(td, table, on) {
  const item = (label, key, action, first) => h("button", { type: "button", class: `tool${first ? " sql-menu-group" : ""}`,
    ...(key ? { "aria-keyshortcuts": key.replace(/Ctrl/g, "Control") } : {}),
    onclick: () => { table?.focus({ preventScroll: true }); action(); } },
  h("span", {}, label), key ? h("span", { class: "sql-menu-key" }, key) : null);
  td.tabIndex = -1;  // the menu gives focus back to the cell when it closes
  showMenu(td, [
    item("Copy", "Ctrl+C", () => on.copy("cells")),
    item("Copy with headers", "Ctrl+Shift+C", () => on.copy("tsv")),
    item("Copy as CSV", null, () => on.copy("csv"), true),
    item("Copy as JSON", null, () => on.copy("json")),
    item("Copy as INSERT statements", null, () => on.copy("insert")),
    item("Open value", "Enter", on.open, true),
    item("Column statistics", null, on.stats)]);
}

// The toast after Copy (S23): the rows on this page, and (WR-225) how many the result has when it has more,
// since Copy never takes the other pages.
function sqlCopiedPageNote(r, as = "") {
  const n = r.rows.length;
  const copied = `Copied the ${n.toLocaleString()} row${n === 1 ? "" : "s"} on this page${as}`;
  return r.total > n ? `${copied}. The result has ${r.total.toLocaleString()} rows. Use Export to save them all.` : copied;
}

// The toast after a copy of the grid's selection: "Copied 6 cells", "Copied 3 rows as CSV".
function sqlGridCopiedNote(format, block) {
  const cells = block.rows.length * block.columns.length;
  const as = { tsv: " with headers", csv: " as CSV", json: " as JSON", insert: " as INSERT statements" }[format] || "";
  return format === "cells" ? `Copied ${cells.toLocaleString()} cell${cells === 1 ? "" : "s"}`
    : `Copied ${block.rows.length.toLocaleString()} row${block.rows.length === 1 ? "" : "s"}${as}`;
}

// The Columns... dialog for a grid (the SQL page's results and a notebook's table): the list follows each
// change, and focus stays on a moved column's button. changed() redraws the grid.
function sqlColumnsDialog(columns, grid, changed) {
  // grid is the grid's state, or a function that gives the current one: a run that ends while the dialog is
  // open carries the layout to a new state, and changes go there. A result with other columns closes it.
  const current = () => {
    const g = typeof grid === "function" ? grid() : grid;
    const names = columns.map((c) => c.name);
    if (g.names && (g.names.length !== names.length || g.names.some((n, k) => n !== names[k]))) { closeDialog(); return null; }
    sqlGridLayoutFor(g, columns);
    return g;
  };
  let g = typeof grid === "function" ? grid() : grid;
  sqlGridLayoutFor(g, columns);  // opened on a state from other columns: it starts again for these
  const box = h("div", {});
  const apply = (fn) => { const now = current(); if (!now) return; g = now; fn(g); };
  const draw = (moved, dir) => {
    box.replaceChildren(sqlColumnsBody(columns, () => g, { change: (k, d, edit) => apply((now) => { edit(now); now.sel = null; draw(k, d); changed(); }) }));
    if (moved !== undefined) {
      const name = columns[moved].name;
      const again = box.querySelector(`button[aria-label="Move ${CSS.escape(name)} ${dir}"]`);
      (again && !again.disabled ? again : box.querySelector(`button[aria-label="Move ${CSS.escape(name)} ${dir === "up" ? "down" : "up"}"]`))?.focus();
    }
  };
  draw();
  showDialog("Columns", box, [
    btn("Show all", null, () => apply((now) => { Object.assign(now, { hidden: [], order: null, widths: {}, sel: null }); draw(); changed(); }), "tool outline",
      { title: "Every column, in the result's order, at its own width" }),
    btn("Done", null, closeDialog, "tool primary")]);
}

// Columns...: a check box to show each column, and Up and Down to move it. Changes apply to the grid at
// once (on.change()); on.reset() shows every column in the result's order, at its own width.
function sqlColumnsBody(columns, grid, on) {
  const g = typeof grid === "function" ? grid() : grid;
  const order = g.order?.length === columns.length ? g.order : columns.map((_, k) => k);
  const hidden = new Set(g.hidden || []);
  const shown = order.filter((k) => !hidden.has(k)).length;
  return h("div", { class: "sql-columns" },
    h("p", { class: "muted-text" }, "Hidden columns aren't shown or copied; sort, filters and Export still cover every column."),
    h("ul", { class: "sql-columns-list", "aria-label": "Columns" }, order.map((k, at) => {
      const c = columns[k];
      const on1 = !hidden.has(k);
      return h("li", {},
        h("label", {}, h("input", { type: "checkbox", checked: on1, disabled: on1 && shown === 1,
          onchange: (e) => on.change(undefined, undefined, (now) => { const off = new Set(now.hidden || []); if (e.target.checked) off.delete(k); else off.add(k); now.hidden = [...off]; }) }),
        h("span", {}, c.name), h("span", { class: "muted-text" }, c.type)),
        h("button", { type: "button", class: "tool small", "aria-label": `Move ${c.name} up`, disabled: at === 0,
          onclick: () => on.change(k, "up", (now) => { now.order = sqlGridMoveColumn(sqlGridOrder(now, columns), k, -1); }) }, "↑"),
        h("button", { type: "button", class: "tool small", "aria-label": `Move ${c.name} down`, disabled: at === order.length - 1,
          onclick: () => on.change(k, "down", (now) => { now.order = sqlGridMoveColumn(sqlGridOrder(now, columns), k, 1); }) }, "↓"));
    })));
}

// ---------- selection, Copy as, the value viewer and column statistics (approved mockup, "Copy as") ----------

// A selection is { a: {r, c}, f: {r, c} }: the anchor and the focused cell, rows on this page, columns by position.
function sqlGridRect(sel) {
  if (!sel) return null;
  return { r0: Math.min(sel.a.r, sel.f.r), r1: Math.max(sel.a.r, sel.f.r), c0: Math.min(sel.a.c, sel.f.c), c1: Math.max(sel.a.c, sel.f.c) };
}

// The band's note for a selection of more than one cell: "2 × 3 cells selected" (rows × columns).
function sqlGridSelectionNote(sel) {
  const x = sqlGridRect(sel);
  if (!x) return "";
  const rows = x.r1 - x.r0 + 1, cols = x.c1 - x.c0 + 1;
  return rows * cols > 1 ? `${rows.toLocaleString()} × ${cols.toLocaleString()} cells selected` : "";
}

// A key in the grid: the arrows move (Shift extends), Home and End go to the row's ends (Ctrl: the page's),
// Page Up and Page Down move 20 rows. Answers the new selection, or null for a key that isn't a move.
function sqlGridMove(sel, e, rows, cols) {
  if (!rows || !cols) return null;
  const f = sel?.f || { r: 0, c: 0 };
  let { r, c } = f;
  const ctrl = e.ctrlKey || e.metaKey;
  switch (e.key) {
    case "ArrowUp": r = ctrl ? 0 : r - 1; break;
    case "ArrowDown": r = ctrl ? rows - 1 : r + 1; break;
    case "ArrowLeft": c = ctrl ? 0 : c - 1; break;
    case "ArrowRight": c = ctrl ? cols - 1 : c + 1; break;
    case "Home": c = 0; if (ctrl) r = 0; break;
    case "End": c = cols - 1; if (ctrl) r = rows - 1; break;
    case "PageUp": r -= 20; break;
    case "PageDown": r += 20; break;
    default: return null;
  }
  const to = { r: Math.max(0, Math.min(rows - 1, r)), c: Math.max(0, Math.min(cols - 1, c)) };
  return { a: e.shiftKey && sel ? sel.a : to, f: to };
}

// The selected block of the page as columns and rows (the whole page when nothing is selected). vis is the
// columns the grid shows, in its order (sqlGridLayoutFor); a selection's columns are positions in it.
function sqlGridBlock(r, sel, vis = null) {
  const cols = vis || r.columns.map((_, k) => k);
  const x = sqlGridRect(sel) || { r0: 0, r1: r.rows.length - 1, c0: 0, c1: cols.length - 1 };
  const pick = cols.slice(x.c0, x.c1 + 1);
  return { columns: pick.map((k) => r.columns[k]), rows: r.rows.slice(x.r0, x.r1 + 1).map((row) => pick.map((k) => row[k])) };
}

// What a copy of the selection puts on the clipboard: "cells" (the values, tab-separated, as a spreadsheet
// takes them), "tsv" (with headers), "csv", "json" or "insert" (sqlRowsAs, sqleditor.js).
function sqlGridCopyText(format, r, sel, opts = {}) {
  const b = sqlGridBlock(r, sel, opts.vis);
  if (format === "cells") {
    if (b.rows.length === 1 && b.rows[0].length === 1) {  // one value: as it is, for pasting anywhere
      const v = b.rows[0][0];
      return v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    }
    return sqlTsv(b.rows.map((row) => row.map(sqlTsvCell)));
  }
  return sqlRowsAs(format, b.columns, b.rows, opts);
}

// A value in full for the value viewer: JSON (a list, a struct, or text that is a JSON object or list)
// indented, anything else as it is.
function sqlValueText(v) {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "object") return JSON.stringify(v, null, 2);
  const t = String(v);
  if (/^\s*[[{]/.test(t)) {
    try { return JSON.stringify(JSON.parse(t), null, 2); } catch { /* not JSON: shown as it is */ }
  }
  return t;
}

// The value viewer: one cell's value in full, its type and length. on.copy(text), on.close().
function sqlValuePanel(r, at, on) {
  const col = r.columns[at.c];
  const v = r.rows[at.r]?.[at.c];
  const text = sqlValueText(v);
  const raw = v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
  const id = "sql-side-h";
  return h("aside", { class: "sql-side", "aria-labelledby": id },
    h("div", { class: "sql-side-head" }, h("h3", { id }, `${col.name}, row ${(r.offset + at.r + 1).toLocaleString()}`),
      h("button", { type: "button", class: "tool small", onclick: () => on.copy(raw) }, "Copy"),
      h("button", { type: "button", class: "tool small sql-side-close", "aria-label": "Close", title: "Close (Esc)", onclick: on.close }, "✕")),
    h("pre", { class: `sql-side-value${v === null || v === undefined ? " null" : ""}`, tabindex: "0" }, text),
    h("dl", { class: "sql-side-kv" }, h("dt", {}, "Type"), h("dd", {}, col.type),
      h("dt", {}, "Length"), h("dd", {}, v === null || v === undefined ? "Empty (null)" : `${[...raw].length.toLocaleString()} character${[...raw].length === 1 ? "" : "s"}`)),
    h("p", { class: "muted-text sql-side-note" }, "Long text and JSON show here in full, formatted. Esc closes."));
}

// Column statistics over every row of the result (sql_column_stats): s is the answer, or { busy } or { error }.
function sqlStatsPanel(col, s, on) {
  const fmt = (v) => (v === null || v === undefined ? "" : typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString() : (+v.toPrecision(8)).toLocaleString(undefined, { maximumFractionDigits: 6 })) : String(v));
  const id = "sql-side-h";
  const head = h("div", { class: "sql-side-head" }, h("h3", { id }, col.name),
    h("button", { type: "button", class: "tool small sql-side-close", "aria-label": "Close", title: "Close (Esc)", onclick: on.close }, "✕"));
  let body;
  if (s.busy) body = [h("p", { class: "muted-text", role: "status" }, "Reading every row of the result...")];
  else if (s.error) body = [h("p", { class: "sql-grid-error", role: "alert" }, s.error)];
  else {
    const kv = [["Rows", fmt(s.rows)], ["Empty (null)", fmt(s.missing)], ["Distinct", `≈ ${fmt(s.distinct)}`]];  // counted approximately
    if (s.min !== undefined) kv.push(["Min", fmt(s.min)], ["Max", fmt(s.max)]);
    if (s.mean !== undefined) kv.push(["Mean", fmt(s.mean)]);
    let picture = null;
    if (s.histogram) {
      const top = Math.max(...s.histogram, 1);
      picture = h("div", { class: "hist", role: "img", "aria-label": `Distribution of ${col.name} from ${fmt(s.min)} to ${fmt(s.max)}` },
        s.histogram.map((b) => h("span", { style: `height:${Math.round((b / top) * 100)}%`, title: b.toLocaleString() })));
    } else if (s.top?.length) {
      picture = h("ul", { class: "top-values", "aria-label": "Most common values" }, s.top.map(([v, n]) => {
        const t = v === null ? "null" : String(v);
        return h("li", {}, h("span", { title: t }, t.length > 24 ? `${t.slice(0, 23)}…` : t), h("b", {}, n.toLocaleString()));
      }));
    }
    body = [h("dl", { class: "sql-side-kv" }, kv.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])), picture,
      h("p", { class: "muted-text sql-side-note" }, "Over the whole result snapshot, not just the rows on screen.")];
  }
  return h("aside", { class: "sql-side", "aria-labelledby": id, "aria-busy": String(!!s.busy) }, head, ...body.filter(Boolean));
}

// Where a cell of the grid's body is: { r, c }, or { r, row: true } for a row's number, or null.
function sqlGridCellAt(td) {
  const tr = td?.closest?.("tr[data-r]");
  if (!tr || td.tagName !== "TD") return null;
  const r = Number(tr.dataset.r);
  return td.classList.contains("i") ? { r, row: true } : { r, c: Number(td.dataset.c) };
}

// Mark the selected cells, and the focused one, on the table as it is (no redraw).
function sqlGridPaint(table, sel) {
  const x = sqlGridRect(sel);
  for (const tr of table.tBodies[0]?.rows || []) {
    const r = Number(tr.dataset.r);
    for (const td of tr.cells) {
      if (td.classList.contains("i")) continue;
      const c = Number(td.dataset.c);
      td.classList.toggle("sel", !!x && r >= x.r0 && r <= x.r1 && c >= x.c0 && c <= x.c1);
      td.classList.toggle("cur", !!sel && sel.f.r === r && sel.f.c === c);
    }
  }
}

// The grid's mouse and keys: click selects a cell (Shift+click or a drag, a block; a row's number, the
// row), Ctrl+A the page, the arrows move. Ctrl+C copies the values, Ctrl+Shift+C with headers, Enter or a
// double-click opens the value, right-click or Shift+F10 shows Copy as. on.select() after a change,
// on.copy(format), on.open({ r, c }) with c the result's column, on.menu(td), on.close() for Esc.
function sqlGridWire(table, r, g, on, vis = r.columns.map((_, k) => k)) {
  const rows = r.rows.length, cols = vis.length;
  const set = (sel, scroll = true) => {
    g.sel = sel;
    sqlGridPaint(table, sel);
    if (scroll && sel) cellOf(sel.f)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    on.select();
  };
  // A cell by its place on screen (a notebook's table has no row-number column).
  const cellOf = (at) => table.tBodies[0].rows[at.r]?.querySelector(`td[data-c="${at.c}"]`);
  let dragging = false;
  table.addEventListener("mousedown", (e) => {
    const at = sqlGridCellAt(e.target.closest("td"));
    if (!at || e.button !== 0) return;
    e.preventDefault();  // no text selection; the table takes focus instead
    table.focus({ preventScroll: true });
    if (at.row) set({ a: { r: at.r, c: 0 }, f: { r: at.r, c: cols - 1 } }, false);
    else set({ a: e.shiftKey && g.sel ? g.sel.a : at, f: at }, false);
    dragging = !at.row;
  });
  table.addEventListener("mouseover", (e) => {
    if (!dragging || !(e.buttons & 1)) { dragging = false; return; }
    const at = sqlGridCellAt(e.target.closest("td"));
    if (at && !at.row && g.sel && (at.r !== g.sel.f.r || at.c !== g.sel.f.c)) set({ a: g.sel.a, f: at }, false);
  });
  table.addEventListener("mouseup", () => { dragging = false; });
  table.addEventListener("dblclick", (e) => {
    const at = sqlGridCellAt(e.target.closest("td"));
    if (at && !at.row) on.open({ r: at.r, c: vis[at.c] });
  });
  table.addEventListener("contextmenu", (e) => {
    const td = e.target.closest("td");
    const at = sqlGridCellAt(td);
    if (!at || at.row) return;
    e.preventDefault();
    const x = sqlGridRect(g.sel);
    if (!x || at.r < x.r0 || at.r > x.r1 || at.c < x.c0 || at.c > x.c1) set({ a: at, f: at }, false);
    on.menu(td);
  });
  table.addEventListener("keydown", (e) => {
    if (e.target !== table) return;  // a filter box or a header's button
    const ctrl = e.ctrlKey || e.metaKey;
    const move = sqlGridMove(g.sel, e, rows, cols);
    if (move) set(move);
    else if (ctrl && e.key.toLowerCase() === "a" && !e.shiftKey) set({ a: { r: 0, c: 0 }, f: { r: rows - 1, c: cols - 1 } }, false);
    else if (ctrl && e.key.toLowerCase() === "c") on.copy(e.shiftKey ? "tsv" : "cells");
    else if (e.key === "Enter" && g.sel) on.open({ r: g.sel.f.r, c: vis[g.sel.f.c] });
    else if (((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu") && g.sel) on.menu(cellOf(g.sel.f));
    else if (e.key === "Escape" && on.close()) { /* closed the side panel */ } else return;
    e.preventDefault();
    e.stopPropagation();
  });
  table.addEventListener("focus", () => { if (!g.sel && rows && cols) set({ a: { r: 0, c: 0 }, f: { r: 0, c: 0 } }, false); });
  sqlGridPaint(table, g.sel);
}
