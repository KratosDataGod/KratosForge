// The Variables pane's table, as in Fabric's and Databricks' variable explorers: filter by name or
// type, sort by name, type or memory, and open a data frame to see its columns and types, or a
// list, tuple, set or dict to see its items. Everything is read from the kernel without running
// code; secrets stay hidden. Clicking a row still adds a cell that displays the variable.

const nbVars = { filter: "", sort: "name", desc: false, open: new Set(), detail: new Map(), rows: null };

const VAR_SORTS = {
  name: (a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()),
  type: (a, b) => a.type.localeCompare(b.type) || VAR_SORTS.name(a, b),
  // Biggest first; unknown sizes last whichever way round.
  memory: (a, b) => (a.memory == null) - (b.memory == null) || (b.memory || 0) - (a.memory || 0) || VAR_SORTS.name(a, b),
};

// The rows the table shows: filtered on name or type, then sorted.
function variableRows(rows, { filter = "", sort = "name", desc = false } = {}) {
  const q = filter.trim().toLowerCase();
  const shown = rows.filter((r) => !q || r.name.toLowerCase().includes(q) || r.type.toLowerCase().includes(q));
  shown.sort(VAR_SORTS[sort] || VAR_SORTS.name);
  if (desc) shown.reverse();
  return shown;
}

function variableMemory(bytes) {
  return sizeText(bytes);  // app.js
}

function variableTable(rows) {
  if (!rows.length) return h("div", { class: "muted-text", style: "padding:8px" }, "No variables yet. Run a cell to see them here.");
  if (rows !== nbVars.rows) { nbVars.rows = rows; nbVars.detail.clear(); }  // new values: open again from the kernel
  const body = h("div", { class: "vars-body" });
  const redraw = () => body.replaceChildren(...variableBody(rows, redraw));
  redraw();
  return h("div", { class: "vars-wrap" },
    h("input", { class: "vars-filter", type: "search", placeholder: "Filter by name or type", "aria-label": "Filter variables",
      value: nbVars.filter, oninput: (e) => { nbVars.filter = e.target.value; redraw(); } }),
    body);
}

function variableBody(rows, redraw) {
  const shown = variableRows(rows, nbVars);
  const head = (key, label) => h("th", { "aria-sort": nbVars.sort === key ? (nbVars.desc ? "descending" : "ascending") : "none" },
    h("button", { class: "vars-sort", title: `Sort by ${label.toLowerCase()}`, onclick: () => {
      nbVars.desc = nbVars.sort === key ? !nbVars.desc : false;
      nbVars.sort = key;
      redraw();
    } }, label, nbVars.sort === key ? (nbVars.desc ? " ▾" : " ▴") : ""));
  const trs = [];
  for (const r of shown) {
    const open = r.expandable && nbVars.open.has(r.name);
    trs.push(h("tr", { title: "Click to display it in a new cell", class: "click", onclick: () => insertCell(`display(${r.name})`) },
      h("td", { class: "name" },
        r.expandable ? h("button", { class: "vars-open", "aria-expanded": String(open), "aria-label": `${open ? "Close" : "Open"} ${r.name}`,
          title: open ? "Close" : "Show what's inside", onclick: (e) => { e.stopPropagation(); toggleVariable(r.name, redraw); } },
        icon(open ? "chevronDown" : "chevronRight", 12)) : h("span", { class: "vars-open-space" }),
        r.name),
      h("td", {}, r.type),
      h("td", { title: r.memory != null ? `About ${variableMemory(r.memory)} in memory` : "" }, r.size || variableMemory(r.memory)),
      h("td", { class: "val" }, r.value)));
    if (open) trs.push(h("tr", { class: "vars-detail" }, h("td", { colspan: "4" }, variableDetail(r.name))));
  }
  const total = rows.reduce((t, r) => t + (r.memory || 0), 0);
  return [
    shown.length ? h("table", { class: "vars" },
      h("thead", {}, h("tr", {}, head("name", "Name"), head("type", "Type"), head("memory", "Size"), h("th", {}, "Value"))),
      h("tbody", {}, trs))
      : h("div", { class: "muted-text", style: "padding:8px" }, "No variable matches the filter."),
    h("div", { class: "vars-foot muted-text" },
      shown.length === rows.length ? `${rows.length} variable${rows.length === 1 ? "" : "s"}` : `${shown.length} of ${rows.length} variables`,
      total ? `, about ${variableMemory(total)} in memory` : ""),
  ];
}

async function toggleVariable(name, redraw) {
  if (nbVars.open.has(name)) { nbVars.open.delete(name); redraw(); return; }
  nbVars.open.add(name);
  redraw();
  if (nbVars.detail.has(name)) return;
  let detail;
  try {
    detail = await rpc("kernel_variable_detail", { name });
  } catch (err) {
    detail = { name, error: String(err.message || err) };
  }
  nbVars.detail.set(name, detail);
  if (nbVars.open.has(name)) redraw();
}

function variableDetail(name) {
  const d = nbVars.detail.get(name);
  const note = (text) => h("div", { class: "muted-text vars-note" }, text);
  if (!d) return note("Loading...");
  if (d.error) return note(`Couldn't read it: ${d.error}`);
  if (d.missing) return note("This variable is gone. Refresh the pane.");
  if (d.hidden) return note("Hidden: it looks like a secret.");
  if (d.columns) {
    return h("div", {}, note(`${d.columns.length} column${d.columns.length === 1 ? "" : "s"}`),
      h("table", { class: "vars inner" }, h("tbody", {}, d.columns.map((c) => h("tr", {},
        h("td", { class: "name" }, c.name), h("td", { class: "val" }, c.type))))));
  }
  if (d.items) {
    return h("div", {},
      h("table", { class: "vars inner" }, h("tbody", {}, d.items.map((it) => h("tr", {},
        h("td", { class: "name" }, it.key), h("td", {}, it.type), h("td", { class: "val" }, it.value))))),
      d.total > d.items.length ? note(`Showing the first ${d.items.length} of ${d.total}.`) : null);
  }
  return note("Nothing more to show.");
}
