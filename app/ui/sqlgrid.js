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
  const table = h("table", {},
    h("thead", {}, h("tr", {}, h("th", { class: "i" }), head), filterRow),
    h("tbody", {}, r.rows.map((row, i) => h("tr", {}, h("td", { class: "i" }, String(r.offset + i + 1)),
      row.map((v) => h("td", { class: `${typeof v === "number" ? "n" : ""}${v === null ? " null" : ""}` }, cellText(v)))))));
  const empty = !r.rows.length && r.total === 0 && (r.all_rows ?? 0) > 0
    ? h("p", { class: "muted-text sql-grid-note" }, "No rows match the filters.") : null;
  const error = g.filterError ? h("p", { class: "sql-grid-error", role: "alert" }, g.filterError.message) : null;
  return [table, empty, error].filter(Boolean);
}
