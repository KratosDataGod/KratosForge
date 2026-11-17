// The SQL page's result grid (editor parity item 8, approved mockup 2026-11-16): click a header to sort,
// Filter shows a box under each header, and both apply to every row of the run's result snapshot on
// this PC (sql_page sort and filters, sidecar/fabric_desktop/sql_view.py). Nothing runs the query again.

// A tab's grid state: { sort: [{ column, desc }], filters: { [column]: text }, filterOn, filterError }.
function sqlGridState(tab) {
  return (tab.grid ||= { sort: [], filters: {}, filterOn: false, filterError: null });
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

// The grid's table: sortable headers, an optional row of filter boxes, the page's rows.
// on.sort(column) and on.filter(column, text) change the view; the caller reads the page again.
function sqlResultGrid(r, g, on) {
  const sortOf = (i) => (r.sort || []).find((s) => s.column === i);
  const head = r.columns.map((c, i) => {
    const s = sortOf(i);
    return h("th", { title: c.type, "aria-sort": s ? (s.desc ? "descending" : "ascending") : "none" },
      h("button", { class: "sql-sort", type: "button", onclick: () => on.sort(i),
        title: `Sort by ${c.name} (${c.type}). Sorts every row of the result on this PC; nothing runs again.` },
      c.name, s ? h("span", { class: "sql-sort-mark", "aria-hidden": "true" }, s.desc ? " ▼" : " ▲") : null));
  });
  // A column's pending filter is kept on the grid's state: a redraw while a page loads makes new boxes,
  // and typing in a column's new box replaces what its old one had waiting.
  const timers = (g.filterTimers ||= {});
  const wait = (i, fn) => { clearTimeout(timers[i]); timers[i] = fn ? setTimeout(() => { delete timers[i]; fn(); }, 450) : 0; };
  const filterRow = g.filterOn ? h("tr", { class: "sql-filter-row" }, h("td", { class: "i" }),
    r.columns.map((c, i) => {
      const bad = g.filterError?.column === i;
      const box = h("input", { type: "text", class: "sql-filter", "data-column": String(i), value: g.filters[i] || "",
        placeholder: "Filter", "aria-label": `Filter ${c.name}`, spellcheck: "false", ...(bad ? { "aria-invalid": "true" } : {}),
        title: "Text it contains, or = != > >= < <= and a value, or null / not null",
        oninput: () => wait(i, () => on.filter(i, box.value)),
        onkeydown: (e) => {
          if (e.key === "Enter") { wait(i, null); on.filter(i, box.value); }
          if (e.key === "Escape" && box.value) { e.stopPropagation(); box.value = ""; wait(i, null); on.filter(i, ""); }
        } });
      return h("td", {}, box);
    })) : null;
  const table = h("table", { tabindex: "0", class: "sql-result-table",
    "aria-label": "Results. Arrow keys move, Shift and an arrow selects, Ctrl+C copies, Enter opens a value, Shift+F10 shows more." },
    h("thead", {}, h("tr", {}, h("th", { class: "i" }), head), filterRow),
    h("tbody", {}, r.rows.map((row, i) => h("tr", { "data-r": String(i) }, h("td", { class: "i" }, String(r.offset + i + 1)),
      row.map((v, c) => h("td", { "data-c": String(c), class: `${typeof v === "number" ? "n" : ""}${v === null ? " null" : ""}` }, cellText(v)))))));
  if (on.select) sqlGridWire(table, r, g, on);
  const empty = !r.rows.length && r.total === 0 && (r.all_rows ?? 0) > 0
    ? h("p", { class: "muted-text sql-grid-note" }, "No rows match the filters.") : null;
  const error = g.filterError ? h("p", { class: "sql-grid-error", role: "alert" }, g.filterError.message) : null;
  return [table, empty, error].filter(Boolean);
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

// The selected block of the page as columns and rows (the whole page when nothing is selected).
function sqlGridBlock(r, sel) {
  const x = sqlGridRect(sel) || { r0: 0, r1: r.rows.length - 1, c0: 0, c1: r.columns.length - 1 };
  return { columns: r.columns.slice(x.c0, x.c1 + 1), rows: r.rows.slice(x.r0, x.r1 + 1).map((row) => row.slice(x.c0, x.c1 + 1)) };
}

// What a copy of the selection puts on the clipboard: "cells" (the values, tab-separated, as a spreadsheet
// takes them), "tsv" (with headers), "csv", "json" or "insert" (sqlRowsAs, sqleditor.js).
function sqlGridCopyText(format, r, sel, opts) {
  const b = sqlGridBlock(r, sel);
  if (format === "cells") {
    const cell = (v) => (v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));
    return b.rows.map((row) => row.map(cell).join("\t")).join("\n");
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
// on.copy(format), on.open(at), on.menu(td), on.close() for Esc.
function sqlGridWire(table, r, g, on) {
  const rows = r.rows.length, cols = r.columns.length;
  const set = (sel, scroll = true) => {
    g.sel = sel;
    sqlGridPaint(table, sel);
    if (scroll && sel) table.tBodies[0].rows[sel.f.r]?.cells[sel.f.c + 1]?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    on.select();
  };
  const cellOf = (at) => table.tBodies[0].rows[at.r]?.cells[at.c + 1];
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
    if (at && !at.row) on.open(at);
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
    else if (e.key === "Enter" && g.sel) on.open(g.sel.f);
    else if (((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu") && g.sel) on.menu(cellOf(g.sel.f));
    else if (e.key === "Escape" && on.close()) { /* closed the side panel */ } else return;
    e.preventDefault();
    e.stopPropagation();
  });
  table.addEventListener("focus", () => { if (!g.sel && rows && cols) set({ a: { r: 0, c: 0 }, f: { r: 0, c: 0 } }, false); });
  sqlGridPaint(table, g.sel);
}
