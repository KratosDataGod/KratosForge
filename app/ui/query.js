// The SQL query page for a lakehouse or, as the Warehouse page, a Fabric warehouse: tabs, the CodeMirror editor, running through the engine and
// paging results. Split out of app.js (HD-056). It loads before app.js, which registers renderQuery,
// and uses app.js helpers (h, rpc, state) only when its functions are called.

// ---------- SQL query page ----------

// SQL over one lakehouse's tables, run on this PC by the sidecar (DuckDB). The editor is sqleditor.js.
const QUERY_HINT = "-- Spark SQL runs on this PC. Press Ctrl+Enter to run.\n";
const SQL_AUTO_CHECK_LIMIT = 100000;  // characters; a longer query is checked when the person asks
const WAREHOUSE_HINT = "-- T-SQL runs in Fabric on this warehouse and uses capacity. Only a query that reads runs here.\n";
const sqlPage = { ref: null, tabs: [], active: 0, tables: null, cols: {}, open: {}, pending: {}, editor: null };
// Warehouses whose first run this session the person has confirmed (a warehouse run uses capacity).
const sqlWarehouseOk = new Set();

// WH-01: a warehouse ref is { kind: "warehouse" | "endpoint", workspace_id, workspace_name, item_id,
// name }. For "endpoint", item_id is the lakehouse's id, as sql_start's warehouse_ref takes it.
function isWarehouseRef(ref) {
  return ref?.kind === "warehouse" || ref?.kind === "endpoint";
}

function sqlRefKey(ref) {
  return isWarehouseRef(ref) ? `${ref.kind}.${ref.item_id}` : ref?.lakehouse_id;
}

function openSqlQuery(ref) {
  if (sqlRefKey(sqlPage.ref) !== sqlRefKey(ref)) {
    Object.assign(sqlPage, { ref, tabs: loadQueryTabs(ref), active: 0, tables: null, cols: {}, open: {}, pending: {},
      catalog: null, whOpen: null, whFilter: "" });
  }
  go("query");
}

// The Warehouse page (WH-01): the SQL query page with a warehouse or SQL analytics endpoint as its target.
function openWarehouseQuery(ref) {
  openSqlQuery(ref);
}

function queryTabsKey(ref) {
  return isWarehouseRef(ref) ? `fabric-desktop.sql.${sqlRefKey(ref)}` : `fabric-desktop.sql.${ref.lakehouse_id}`;
}

function loadQueryTabs(ref) {
  const wh = isWarehouseRef(ref);
  try {
    const saved = JSON.parse(localStorage.getItem(queryTabsKey(ref)));
    if (Array.isArray(saved) && saved.length) {
      const tabs = saved.map((t) => ({ name: String(t.name), sql: String(t.sql), dialect: sqlDialect(t.dialect) }));
      return wh ? tabs.map((t) => ({ ...t, dialect: ref.kind })) : tabs;
    }
  } catch { /* no saved tabs */ }
  return [wh ? { name: "Query 1", sql: WAREHOUSE_HINT, dialect: ref.kind } : { name: "Query 1", sql: QUERY_HINT, dialect: "spark" }];
}

function saveQueryTabs() {
  try {
    localStorage.setItem(queryTabsKey(sqlPage.ref),
      JSON.stringify(sqlPage.tabs.map((t) => ({ name: t.name, sql: t.sql, dialect: t.dialect || "spark" }))));
    return true;
  } catch { return false; /* storage unavailable: tabs last for this session */ }
}

// The SQL editor's completion provider for one lakehouse (the shape is described in sqleditor.js).
// Tables are the ones the page already listed; each table's columns are asked for once.
function lakehouseSqlProvider(p, ref, changed) {
  const key = (t) => sqlTableName(t.schema, t.name);
  return {
    id: `lakehouse:${ref.lakehouse_id}`,
    label: ref.name,
    tables: () => Object.entries(p.tables || {}).flatMap(([schema, names]) => names.map((name) => ({ schema, name }))),
    cachedColumns: (t) => p.cols[key(t)] || undefined,
    columns(t) {
      const k = key(t);
      if (p.cols[k]) return Promise.resolve(p.cols[k]);
      if (!p.pending[k]) {
        p.pending[k] = rpc("table_columns", { lakehouse_ref: ref, table: k })
          .then((cols) => { p.cols[k] = cols || []; changed(); return p.cols[k]; })
          .catch(() => { p.cols[k] = []; return []; });  // offline or no access: no column names, ask again after Refresh
      }
      return p.pending[k];
    },
  };
}

// A query that succeeds after running longer than this (engine seconds) gets a spark; quicker ones don't.
const SQL_SPARK_SECONDS = 2;

// Before the first warehouse run of this app session (a query or the explorer's catalog), ask: it
// runs in Fabric and uses capacity. Resolves true to go ahead.
async function warehouseRunOk(ref) {
  if (sqlWarehouseOk.has(sqlRefKey(ref))) return true;
  const ok = await confirmDialog(`Run this query in Fabric on ${ref.name}?`,
    `It runs on the ${ref.kind === "endpoint" ? "SQL analytics endpoint" : "warehouse"} in ${ref.workspace_name || "its workspace"}, ` +
    "as you, and uses capacity. Only a query that reads is sent; anything that would change the warehouse is refused before Fabric is asked. " +
    "This asks once per warehouse each time the app starts.", "Run in Fabric");
  if (ok) sqlWarehouseOk.add(sqlRefKey(ref));
  return ok;
}

// A managed run's state on the Results band: [pill class, label].
const SQL_STATE = { queued: ["busy", "Queued"], running: ["busy", "Running"], cancelling: ["busy", "Stopping"],
  completed: ["ok", "Succeeded"], failed: ["bad", "Failed"], cancelled: ["", "Stopped"], timed_out: ["bad", "Timed out"] };

// Follow a run until it finishes. Polls quickly at first, then once a second; stops following when
// the tab starts another run.
async function followRun(tab, status) {
  let wait = 150;
  while (["queued", "running", "cancelling"].includes(status.state)) {
    await new Promise((r) => setTimeout(r, wait));
    wait = Math.min(1000, wait * 1.5);
    const next = await rpc("sql_status", { execution: status.id });
    if (tab.exec?.id !== status.id) return null;
    tab.exec = status = next;
    sqlPage.redraw?.();
  }
  return status;
}

async function loadPage(tab, offset) {
  try {
    const page = await rpc("sql_page", { execution: tab.exec.id, offset });
    if (page.rerun_needed) { tab.result = null; tab.error = page.message; tab.errorRetry = null; return; }
    Object.assign(tab, { result: page, error: null, errorRetry: null });
  } catch (err) {
    tab.error = String(err);
    tab.errorRetry = () => loadPage(tab, offset).then(() => sqlPage.redraw?.());  // reads the same page again
  }
}

function cellText(v) {
  if (v === null || v === undefined) return "NULL";
  return typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 6 }) : String(v);
}

function renderQuery() {
  const p = sqlPage;
  const ref = p.ref;
  if (!ref) return go("onelake");
  if (!p.tabs[p.active]) p.active = 0;
  const tab = p.tabs[p.active];
  const wh = isWarehouseRef(ref);
  tab.dialect = wh ? ref.kind : sqlDialect(tab.dialect);
  const here = () => state.view === "query" && p.ref === ref && p.tabs[p.active] === tab;
  const runsHere = () => wh || SQL_DIALECTS[tab.dialect].runsHere;
  const where = wh ? "in Fabric" : "on this PC";

  const setSql = (text) => { tab.sql = text; saveQueryTabs(); };
  p.editor?.destroy();
  const editor = p.editor = createSqlEditor({
    doc: tab.sql,
    dialect: tab.dialect,
    provider: wh ? warehouseSqlProvider(ref) : lakehouseSqlProvider(p, ref, () => { if (here()) editor.refresh(); }),
    on: {
      change: setSql,
      run: () => run(),
      save: () => { const ok = saveQueryTabs(); toast(ok ? `Saved ${tab.name}` : "Could not save the query: this PC's app storage is unavailable", !ok); },
      format: () => formatSql(),
    },
    check: (text, dialect) => checkSql(text, dialect),
  });
  const insertAtCursor = (text) => editor.insert(text);

  // SQL-002: the compatibility check, run by the editor shortly after typing stops. It reads the SQL
  // with the chosen engine's documented rules; nothing runs and nothing is sent to Fabric.
  async function checkSql(text, dialect) {
    tab.compatLong = text.length > SQL_AUTO_CHECK_LIMIT && !tab.checkLong;
    if (!text.trim() || tab.running || tab.compatLong) {
      if (!text.trim()) tab.compat = null;
      if (here()) drawCompat();
      return null;
    }
    tab.checkLong = false;
    let report;
    try {
      report = await rpc("sql_analyze", { query: text, target: dialect });
    } catch (err) {
      report = { failed: String(err).replace(/^Error: /, "") };
    }
    if (!here() || editor.text() !== text) return null;
    tab.compat = report;
    tab.compatText = text;
    drawCompat();
    return report.failed ? null : report;
  }

  // SQL-003: each run is a managed query on this PC (sql_exec.py): queued, then running in a process
  // of its own, with Stop and the time limit in Settings › SQL. Results come a page at a time from
  // the run's snapshot, so a large result is never loaded whole. On the Warehouse page (WH-01) the
  // same managed run sends one query that reads to the warehouse (sql_start target "warehouse").
  async function run(query = editor.chosen(), allow = []) {
    if (!query.trim() || tab.running) return;
    if (!runsHere()) {
      toast(`${SQL_DIALECTS[tab.dialect].label} can't run on this PC. Pick Spark SQL or DuckDB to run here.`, true);
      return;
    }
    if (wh && !(await warehouseRunOk(ref))) return;
    if (wh && !sqlPage.catalog) loadCatalog();
    tab.running = true;
    Object.assign(tab, { error: null, errorRetry: null, result: null, exec: null, ranNotice: "" });
    drawResults();
    let status;
    try {
      status = await rpc("sql_start", wh
        ? { query, target: "warehouse", dialect: "tsql", warehouse_ref: { workspace_id: ref.workspace_id, item_id: ref.item_id, kind: ref.kind } }
        : { query, lakehouse_ref: ref, dialect: tab.dialect, allow });
      Object.assign(tab, { exec: status, ranSql: query, ranDialect: tab.dialect });
      status = await followRun(tab, status);
    } catch (err) {
      tab.error = String(err);
      tab.errorRetry = () => run(query);  // runs it again through the same checks, including any confirmation
    }
    if (status?.state === "completed") await loadPage(tab, 0);
    tab.running = false;
    if (status?.refused?.permission === "local_mutation" && !allow.length) {
      sqlPage.redraw?.();
      const ok = await confirmDialog("Let this query change its own session?",
        `Statement ${status.refused.statement} ${status.refused.label}: a temp view, a table made in the query, a cache. ` +
        "Only this run's own DuckDB session on this PC changes; no lakehouse table, file or Fabric item does. Nothing has run yet.", "Run");
      if (ok) return run(query, ["local_mutation"]);
    }
    if (status?.state === "completed") {
      tab.ranNotice = wh ? "" : localNotice(query);
      if (tab.exportAfter) { const fmt = tab.exportAfter; delete tab.exportAfter; exportResult(fmt); }
    } else if (status && !tab.errorRetry) {  // a failed engine call keeps its own message and Retry
      tab.error = status.error || `The query ${SQL_STATE[status.state]?.[1].toLowerCase() || status.state}.`;
    }
    sqlPage.redraw?.();
    if (here()) {
      editor.recheck();
      runFx(status, allow);
    }
  }

  // KF-MO-005: a spark on the Results pill when a query that took a while succeeds, the ember when one
  // fails; a stop, a quick query or a confirmation the person declined plays nothing. Uses the
  // engine's own seconds, and runs after the redraw, which replaces the pill. The pill already
  // says the outcome to a screen reader, so nothing extra is announced.
  function runFx(status, allow) {
    const pill = band.querySelector(".sql-pill");
    if (status?.state === "completed" && !tab.error && status.seconds > SQL_SPARK_SECONDS) forgeEvent("sql.succeeded", pill);
    else if (["failed", "timed_out"].includes(status?.state)
      && !(status.refused?.permission === "local_mutation" && !allow.length)) forgeEvent("sql.failed", pill);
  }

  async function stop() {
    if (!tab.exec) return;
    const out = await attempt(() => rpc("sql_cancel", { execution: tab.exec.id }), "Could not stop the query");
    if (out) { tab.exec = out; sqlPage.redraw?.(); }
  }

  async function goPage(offset) {
    if (!tab.result || tab.paging) return;
    tab.paging = true;
    await loadPage(tab, Math.max(0, offset));
    tab.paging = false;
    sqlPage.redraw?.();
  }

  // Export ▾ CSV or Parquet: streamed from the run's snapshot to a new file in Downloads.
  async function exportResult(format) {
    if (!tab.exec || tab.exporting) return;
    const out = await attempt(() => rpc("sql_export", { execution: tab.exec.id, format, name: tab.name }), "Could not export");
    if (!out) return;
    if (out.rerun_needed) {
      const again = await confirmDialog("Run the query again?",
        `${out.message} Running it again reads the tables as they are now, so the rows can differ.`, "Run again");
      if (again && tab.ranSql) { tab.exportAfter = format; run(tab.ranSql); }
      return;
    }
    tab.exporting = out;
    sqlPage.redraw?.();
    let st = out;
    while (st.state === "running") {
      await new Promise((r) => setTimeout(r, 300));
      try { st = await rpc("sql_export_status", { export: out.id }); } catch (err) { st = { ...st, state: "failed", error: String(err) }; }
      tab.exporting = st;
      sqlPage.redraw?.();
    }
    tab.exporting = null;
    sqlPage.redraw?.();
    if (st.state === "completed") toast(`Saved ${st.rows.toLocaleString()} rows to ${st.path}`);
    else if (st.state === "cancelled") toast("Export stopped. No file was left behind.");
    else toast(`Could not export: ${st.error}`, true);
  }

  // After a run on this PC: say when the check found differences from Fabric, without blocking the run.
  function localNotice(query) {
    const r = tab.compat;
    if (!r?.diagnostics || tab.compatText === undefined || !tab.compatText.includes(query.trim())) return "";
    const local = r.diagnostics.filter((d) => d.category === "local" && d.rule !== "LOC-008").length;
    const engine = tab.dialect === "duckdb" ? "No Fabric engine runs DuckDB SQL as written"
      : `${local} finding${local === 1 ? "" : "s"} say Fabric Spark may give different results`;
    if (tab.dialect !== "duckdb" && !local) return "";
    return `Ran on this PC with DuckDB, not in Fabric. ${engine}; see Compatibility. A warning doesn't mean the query is wrong.`;
  }

  // Format the selection, or everything. The engine leaves the SQL alone and says why when it can't keep its meaning.
  async function formatSql() {
    const { from, to } = editor.selection();
    const whole = to <= from;
    const text = whole ? editor.text() : editor.text().slice(from, to);
    if (!text.trim()) return;
    const out = await attempt(() => rpc("sql_format", { query: text, dialect: tab.dialect }), "Could not format the SQL");
    if (!out || !here()) return;
    if (out.warning) toast(out.warning, true);
    else if (out.changed && editor.text().slice(whole ? 0 : from, whole ? undefined : to) === text) {
      editor.replace(out.sql, whole ? 0 : from, whole ? editor.text().length : to);
    }
  }

  async function saveAsTable() {
    const query = editor.chosen();
    if (!query.trim()) return;
    const name = await askText("Save as table", `Table name in ${ref.name}, written with the full query result`, "query_result", "Save");
    if (!name) return;
    toast(`Writing ${name}...`);
    let out = await attempt(() => rpc("save_sql", { query, lakehouse_ref: ref, table: name, dialect: tab.dialect }), "Could not save the table");
    if (out?.exists) {
      const replace = await confirmDialog("Replace the table?",
        `${ref.name} already has a table called ${name}. Saving replaces all of its rows with this query's result.`, "Replace", { danger: true, typeToConfirm: name });
      out = replace && await attempt(() => rpc("save_sql", { query, lakehouse_ref: ref, table: name, mode: "overwrite", dialect: tab.dialect }),
        "Could not save the table");
    }
    if (out) {
      toast(out.message);
      delete state.tree[treeKey(ref, "tables")];
      p.tables = null;
      if (here()) drawTables();
    }
  }

  async function openInNotebook() {
    const query = editor.chosen().trim();
    await newNotebook(ref);
    if (!state.notebook) return;
    const cell = state.notebook.cells[0];
    setSource(cell, cell.source + `\ndf = lakehouse.sql("""\n${query.replace(/"""/g, '\\"\\"\\"')}\n""")\ndf\n`);
    markDirty();
    renderNotebook();
  }

  async function copyRows() {
    const r = tab.result;
    if (!r) return;
    const lines = [r.columns.map((c) => c.name), ...r.rows.map((row) => row.map((v) => (v === null ? "" : String(v))))];
    const copied = await attempt(() => navigator.clipboard.writeText(lines.map((l) => l.join("\t")).join("\n")).then(() => true), "Could not copy");
    if (copied) toast(`Copied the ${r.rows.length.toLocaleString()} rows on this page`);
  }

  // Explorer: click a table to query it, a column to type its name.
  const tablesBox = h("div", { class: "explorer-content" });
  function queryTable(name) {
    const text = tab.dialect === "spark" || tab.dialect === "duckdb" ? `SELECT *\nFROM ${name}\nLIMIT 100;` : `SELECT TOP (100) *\nFROM ${name};`;
    if (!tab.sql.trim() || tab.sql === QUERY_HINT || tab.sql === WAREHOUSE_HINT) {
      editor.replace(text);
      run(text);
    } else {
      p.tabs.push({ name: nextTabName(), sql: text, dialect: tab.dialect, autorun: true });
      p.active = p.tabs.length - 1;
      saveQueryTabs();
      renderQuery();
    }
  }
  async function toggleColumns(t, name) {
    p.open[name] = !p.open[name];
    if (p.open[name] && !p.cols[name]) {
      drawTables();
      const cols = await lakehouseSqlProvider(p, ref, () => {}).columns(t);
      if (!cols.length && !p.cols[name]?.length) { p.open[name] = false; delete p.cols[name]; delete p.pending[name]; }
    }
    if (state.view === "query" && p.ref === ref) { drawTables(); if (here()) editor.refresh(); }
  }
  function drawTables() {
    if (!p.tables) {
      tablesBox.replaceChildren(h("div", { class: "tree-row muted" }, "Loading tables..."));
      return;
    }
    const rows = [];
    for (const [schema, names] of Object.entries(p.tables)) {
      const qualify = Object.keys(p.tables).length > 1 || schema !== "dbo";
      if (qualify) rows.push(h("div", { class: "tree-row muted", style: "padding-left:6px" }, schema));
      if (!names.length) rows.push(h("div", { class: "tree-row muted", style: "padding-left:28px" }, "No tables"));
      for (const t of names) {
        const name = sqlTableName(schema, t);
        const open = !!p.open[name];
        rows.push(h("div", { class: "sql-table-row" },
          h("button", { class: "tree-row chev", "aria-label": open ? `Hide columns of ${t}` : `Show columns of ${t}`,
            style: `padding-left:${qualify ? 22 : 6}px`, onclick: () => toggleColumns({ schema, name: t }, name) }, chevron(open)),
          h("button", { class: "tree-row", title: `Query ${name}`, onclick: () => queryTable(sqlTableName(schema, t, tab.dialect)) },
            icon("table", 15), h("span", { class: "label" }, t))));
        if (open) {
          if (!p.cols[name]) rows.push(h("div", { class: "tree-row muted", style: "padding-left:52px" }, "Loading..."));
          for (const c of p.cols[name] || []) {
            rows.push(h("button", { class: "tree-row sql-col", title: `Insert ${c.name}`,
              onclick: () => insertAtCursor(sqlQuoteIdent(c.name, tab.dialect)) },
            h("span", { class: "label" }, c.name), h("span", { class: "hint" }, c.type)));
          }
        }
      }
    }
    tablesBox.replaceChildren(...rows);
  }

  const band = h("div", { class: "sql-band" });
  const grid = h("div", { class: "sql-grid" });
  function drawResults() {
    const r = tab.result;
    const ex = tab.exec;
    const busy = tab.running;
    const st = busy && !ex ? "queued" : ex?.state;
    const pill = st ? SQL_STATE[st] : ["", "Not run"];
    let msg = "Press Run to see results";
    if (st === "queued" && !ex) msg = wh ? "Starting..." : "Starting on this PC...";
    else if (st === "queued" && wh) msg = "Waiting to start: finding the warehouse's SQL connection in Fabric, or another query is still running";
    else if (st === "queued") msg = "Waiting for another query to finish (Settings › SQL sets how many run at once)";
    else if (st === "completed" && !r && busy) msg = "Reading the first page...";
    else if (st === "running" || st === "cancelling") {
      msg = `${ex.seconds} s ${where}${ex.rows ? ` · ${ex.rows.toLocaleString()} rows so far` : ""}`;
    } else if (r) {
      const last = r.offset + r.rows.length;
      msg = `${r.total.toLocaleString()} row${r.total === 1 ? "" : "s"} · ${ex.seconds} s ${where}`;
      if (r.total > r.rows.length) msg += ` · rows ${(r.offset + 1).toLocaleString()} to ${last.toLocaleString()}`;
    }
    const x = tab.exporting;
    const exportMenu = h("details", { class: "sql-export" },
      h("summary", { class: `tool small${!r || x ? " disabled" : ""}`, "aria-disabled": String(!r || !!x),
        onclick: (e) => { if (!r || x) e.preventDefault(); } }, "Export ▾"),
      h("div", { class: "sql-export-menu", role: "menu" },
        h("button", { role: "menuitem", onclick: (e) => { e.target.closest("details").open = false; exportResult("csv"); } }, "CSV"),
        h("button", { role: "menuitem", onclick: (e) => { e.target.closest("details").open = false; exportResult("parquet"); } }, "Parquet")));
    band.replaceChildren(...[h("span", { style: "font-weight:600" }, "Results"),
      h("span", { class: `sql-pill ${pill[0]}` }, pill[1]), h("span", { class: "muted-text sql-run-msg" }, tab.error ? "" : msg),
      ex && (st === "running" || st === "queued") ? btn("Stop", "stop", stop, "tool small", { title: "Stop this query. Only its own process ends." }) : null,
      tab.ranNotice && r && !busy ? h("span", { class: "sql-local-note", role: "note" }, tab.ranNotice) : null,
      h("span", { class: "spacer" }),
      x ? h("span", { class: "muted-text sql-export-progress", role: "status" },
        `Exporting ${x.format.toUpperCase()}: ${x.rows.toLocaleString()} of ${x.total.toLocaleString()} rows`) : null,
      x ? btn("Cancel", null, () => rpc("sql_export_cancel", { export: x.id }).catch(() => {}), "tool small") : null,
      r && (r.has_previous || r.has_next) ? btn("‹ Previous", null, () => goPage(r.offset - r.limit), "tool small",
        { disabled: !r.has_previous || tab.paging, "aria-label": "Previous page" }) : null,
      r && (r.has_previous || r.has_next) ? btn("Next ›", null, () => goPage(r.offset + r.limit), "tool small",
        { disabled: !r.has_next || tab.paging, "aria-label": "Next page" }) : null,
      btn("Copy", null, copyRows, "tool small", { disabled: !r, title: "Copy the rows on this page" }),
      exportMenu].filter(Boolean));
    if (tab.error && tab.errorRetry) {
      // The engine call itself failed (UX-011): a sentence, Retry and the raw text under Details.
      grid.replaceChildren(h("div", { class: "output error" }, errorPanel(tab.error, { retry: tab.errorRetry })));
    } else if (tab.error) {
      // The query's own error ("Binder Error: ..."), or why it stopped, is what the person needs to read.
      grid.replaceChildren(h("div", { class: "output error" }, h("pre", {}, tab.error.replace(/^Error: /, ""))));
    } else if (r && r.columns.length) {
      grid.replaceChildren(h("table", {},
        h("thead", {}, h("tr", {}, h("th", { class: "i" }), r.columns.map((c) => h("th", { title: c.type }, c.name)))),
        h("tbody", {}, r.rows.map((row, i) => h("tr", {}, h("td", { class: "i" }, String(r.offset + i + 1)),
          row.map((v) => h("td", { class: `${typeof v === "number" ? "n" : ""}${v === null ? " null" : ""}` }, cellText(v))))))));
    } else {
      grid.replaceChildren(h("p", { class: "muted-text", style: "padding:12px 16px" },
        r ? "The query ran and returned no columns."
          : wh ? "Write T-SQL as you would in Fabric's editor. It runs as you, with your permissions on the warehouse."
            : "Tables are named as in Fabric, so SELECT * FROM sales works here too."));
    }
  }
  sqlPage.redraw = () => { if (here()) drawResults(); };

  const nextTabName = () => {
    let n = p.tabs.length + 1;
    while (p.tabs.some((t) => t.name === `Query ${n}`)) n++;
    return `Query ${n}`;
  };
  const addTab = () => { p.tabs.push({ name: nextTabName(), sql: wh ? WAREHOUSE_HINT : QUERY_HINT, dialect: tab.dialect }); p.active = p.tabs.length - 1; saveQueryTabs(); renderQuery(); };
  const tabs = h("div", { class: "sql-tabs", role: "tablist", "aria-label": "Queries" },
    p.tabs.map((t, i) => h("div", { class: `sql-tab${i === p.active ? " on" : ""}` },
      h("button", { class: "name", role: "tab", "aria-selected": String(i === p.active),
        onclick: () => { p.active = i; renderQuery(); },
        ondblclick: async () => {
          const name = await askText("Rename query", "Name", t.name, "Rename");
          if (name) { t.name = name; saveQueryTabs(); renderQuery(); }
        } }, icon("code", 14), t.name),
      p.tabs.length > 1 ? h("button", { class: "close", "aria-label": `Close ${t.name}`,
        onclick: () => { p.tabs.splice(i, 1); p.active = Math.min(p.active, p.tabs.length - 1); saveQueryTabs(); renderQuery(); } }, "×") : null)),
    h("button", { class: "sql-tab add", "aria-label": "New query", onclick: addTab }, "+"));

  const dialectPick = h("select", { class: "sql-dialect", "aria-label": "SQL dialect",
    title: "The SQL you are writing and the engine it's checked against. It sets highlighting, suggestions, formatting and the compatibility check; only Spark SQL and DuckDB run on this PC.",
    onchange: (e) => { tab.dialect = sqlDialect(e.target.value); saveQueryTabs(); editor.setDialect(tab.dialect); drawActions(); } },
  Object.entries(SQL_DIALECTS).map(([id, d]) => h("option", { value: id, selected: id === tab.dialect }, d.label)));
  const actions = h("div", { class: "sql-actions" });
  function drawActions() {
    if (wh) {
      actions.replaceChildren(btn("Run", "play", () => run(), "tool primary"),
        h("span", { class: "muted-text" }, "Ctrl+Enter, or select text to run just that"),
        h("span", { class: "sql-sep", "aria-hidden": "true" }),
        h("span", { class: "sql-target-note" }, SQL_DIALECTS[tab.dialect].label),
        btn("Format SQL", null, formatSql, "tool outline", { title: "Format the selection, or the whole query (Shift+Alt+F)" }),
        h("span", { class: "spacer" }),
        h("span", { class: "sql-capacity-note", role: "note" }, "Runs in Fabric · uses capacity · reads only"));
      return;
    }
    const local = runsHere();
    actions.replaceChildren(btn("Run", "play", () => run(), "tool primary", { disabled: !local }),
      h("span", { class: "muted-text" }, local ? "Ctrl+Enter, or select text to run just that"
        : `${SQL_DIALECTS[tab.dialect].short} is formatted here, not run`),
      h("span", { class: "sql-sep", "aria-hidden": "true" }),
      dialectPick,
      btn("Format SQL", null, formatSql, "tool outline", { title: "Format the selection, or the whole query (Shift+Alt+F)" }),
      h("span", { class: "spacer" }),
      btn("Save as table", "save", saveAsTable, "tool outline", { disabled: !local }),
      btn("Open in notebook", "notebook", openInNotebook, "tool outline", { disabled: tab.dialect !== "spark" }));
  }
  drawActions();

  // The Compatibility panel beside the editor: the last check's outcome and findings, newest SQL first.
  const compat = h("aside", { class: "sql-compat", "aria-label": "Compatibility" });
  const compatBody = h("div", { class: "sql-compat-body" });
  compat.append(compatBody);
  async function exportCompat() {
    const out = await attempt(() => rpc("sql_compat_export", { query: editor.text(), target: tab.dialect, name: tab.name }),
      "Could not export the report");
    if (out) toast(`Saved the compatibility report to ${out.path}`);
  }
  function drawCompat() {
    const r = tab.compat;
    const label = SQL_DIALECTS[tab.dialect].label;
    const head = h("div", { class: "sql-compat-head" }, h("b", {}, "Compatibility"), previewShown() ? previewTag() : null,
      btn("Export JSON", null, exportCompat, "tool outline small", { disabled: !r || !!r.failed || !editor.text().trim(),
        title: "Save this report, with the SQL it checked, as a JSON file in Downloads" }));
    if (tab.compatLong) {
      compatBody.replaceChildren(head, h("p", { class: "muted-text" },
        `This query is long (${editor.text().length.toLocaleString()} characters), so it's checked when you ask.`),
      btn("Check now", null, () => { tab.checkLong = true; editor.recheck(); }, "tool outline small"));
      return;
    }
    if (!r) {
      compatBody.replaceChildren(head, h("p", { class: "muted-text" },
        `Write SQL to check it against ${label}. Nothing runs and nothing is sent to Fabric.`));
      return;
    }
    if (r.failed) {
      compatBody.replaceChildren(head, h("p", { class: "output error" }, `Could not check the SQL: ${r.failed}`));
      return;
    }
    const c = r.counts;
    const chip = (n, one, many, cls) => h("span", { class: `sql-chip ${cls}` }, `${n.toLocaleString()} ${n === 1 ? one : many}`);
    const list = r.diagnostics.map((d) => h("button", { class: `sql-finding ${d.severity}`,
      title: `${d.message} ${d.action}`,
      onclick: () => {
        const text = editor.text();
        if (text === tab.compatText) editor.reveal(sqlUtf16Offset(text, d.from), sqlUtf16Offset(text, d.to));
        tab.compatPicked = d;
        drawCompat();
      } },
    h("b", {}, SQL_SEVERITY_LABEL[d.severity]), ` · Ln ${d.line} · ${d.title}`));
    const picked = tab.compatPicked && r.diagnostics.includes(tab.compatPicked) ? tab.compatPicked : null;
    compatBody.replaceChildren(...[head,
      h("div", { class: "muted-text" }, `${label} · rules ${r.rule_version} · ${r.statements.toLocaleString()} statement${r.statements === 1 ? "" : "s"}`),
      h("div", { class: "sql-chips" }, chip(c.error, "error", "errors", "error"), chip(c.warning, "warning", "warnings", "warning"),
        chip(c.information, "note", "notes", "information"), chip(c.unverified, "unverified", "unverified", "unverified")),
      h("div", { class: `sql-outcome ${r.outcome.status}`, role: "status" }, r.outcome.text),
      picked ? h("div", { class: "sql-finding-detail" },
        h("b", {}, `${picked.rule} · ${picked.title}`), h("div", {}, picked.message), h("div", {}, picked.action),
        h("div", { class: "muted-text" }, `Confidence: ${picked.confidence} · `,
          h("a", { href: "#", onclick: (e) => { e.preventDefault(); attempt(() => invoke("open_link", { url: picked.doc }), "Could not open the link"); } },
            "Source"))) : null,
      list.length ? h("div", { class: "sql-findings" }, list) : null,
      r.truncated ? h("p", { class: "muted-text" }, `Showing the first ${r.diagnostics.length.toLocaleString()} findings; the counts include all of them.`) : null,
      h("p", { class: "muted-text sql-compat-note" }, "Static check: the SQL was read, not run. No findings isn't proof it runs in Fabric.")]
      .filter(Boolean));
  }
  drawCompat();

  const edit = h("div", { class: "sql-edit" });
  edit.append(editor.view.dom);
  // WH-02: the object explorer, from the warehouse's catalog. It lists by itself once runs on this
  // warehouse are confirmed this session; before that it waits for List objects.
  const loadCatalog = () => whLoadCatalog(ref, () => { if (state.view === "query" && p.ref === ref) p.whRedraw?.(); });
  const whTree = wh ? whExplorer(ref, {
    load: async () => { if (await warehouseRunOk(ref)) loadCatalog(); },
    query: (name) => queryTable(name),
    insert: (text) => insertAtCursor(text),
    open: (name, text) => {
      p.tabs.push({ name, sql: text, dialect: tab.dialect });
      p.active = p.tabs.length - 1;
      saveQueryTabs();
      renderQuery();
    },
  }) : null;
  p.whRedraw = wh ? () => { whTree.draw(); if (here()) editor.refresh(); } : null;
  const fabricLink = wh ? fabricItemLink({ type: ref.kind === "endpoint" ? "Lakehouse" : "Warehouse", workspace_id: ref.workspace_id, item_id: ref.item_id }) : null;
  setView(wh ? h("div", { class: "sql-page warehouse-page" },
    h("div", { class: "ribbon" }, h("div", { class: "toolbar" },
      btn("New SQL query", "add", addTab),
      btn("Open in Fabric", "open", () => openInFabric(fabricLink)),
      h("span", { class: "spacer" }),
      h("span", { class: "muted-text" }, `Queries run in Fabric on ${ref.name} and use capacity. Only a query that reads runs here. `,
        learnMore("warehouse-queries-preview")))),
    h("div", { class: "nb-body" },
      h("aside", { class: "explorer" }, h("div", { class: "explorer-head" }, "Explorer"),
        h("div", { class: "tree-row", style: "margin:4px 8px 0;font-weight:600;cursor:default" },
          itemIcon(ref.kind === "endpoint" ? "SQLEndpoint" : "Warehouse", 16), h("span", { class: "label" }, ref.name), previewTag()),
        h("div", { class: "tree-row muted", style: "cursor:default" }, ref.kind === "endpoint" ? "SQL analytics endpoint" : "Warehouse",
          ref.workspace_name ? ` · ${ref.workspace_name}` : ""),
        whTree.el,
        h("div", { class: "warehouse-how" }, howItWorks("warehouse.query", {
          reads: "The warehouse's SQL connection, from Fabric, then the rows your query asks for. The explorer reads the warehouse's catalog views (sys.objects, sys.columns) the same way. It signs in as you, with a token for Azure SQL.",
          changes: "Nothing. Only a single statement that reads is sent; DDL, DML, EXEC and variables are refused before Fabric is asked.",
          results: "A snapshot of the rows on this PC, read a page at a time, with Copy and Export to Downloads. The query text stays on this PC.",
          more: [["Capacity", "The query runs in Fabric, so it uses capacity on the warehouse's workspace."]],
          anchor: "warehouse-queries-preview" }))),
      h("section", { class: "sql-main" }, tabs,
        h("div", { class: "sql-card" }, actions, h("div", { class: "sql-editor-row" }, edit, compat), band, grid))))
    : h("div", { class: "sql-page" },
    h("div", { class: "ribbon" }, h("div", { class: "toolbar" },
      btn("New SQL query", "add", addTab),
      btn("New notebook", "notebook", () => newNotebook(ref)),
      btn("Refresh", "sync", () => { p.tables = null; p.cols = {}; p.pending = {}; renderQuery(); }),
      h("span", { class: "spacer" }),
      h("span", { class: "muted-text" }, "Queries run on this PC with DuckDB, so no Fabric job starts. Reading OneLake still uses some capacity. ", learnMore("sql-queries")))),
    h("div", { class: "nb-body" },
      h("aside", { class: "explorer" }, h("div", { class: "explorer-head" }, "Explorer"),
        h("div", { class: "tree-row", style: "margin:4px 8px 0;font-weight:600;cursor:default" }, icon("lakehouse", 16),
          h("span", { class: "label" }, ref.name)),
        tablesBox),
      h("section", { class: "sql-main" }, tabs,
        h("div", { class: "sql-card" }, actions, h("div", { class: "sql-editor-row" }, edit, compat), band, grid)))));
  drawResults();
  drawTables();
  if (tab.autorun) { delete tab.autorun; run(tab.sql); }
  if (wh && !p.catalog && sqlWarehouseOk.has(sqlRefKey(ref))) loadCatalog();
  if (!p.tables && !wh) {
    rpc("list_tables", { workspace_id: ref.workspace_id, lakehouse_id: ref.lakehouse_id })
      .then((t) => { p.tables = t; })
      .catch((err) => { p.tables = {}; toast(`Could not list tables: ${err}`, true); })
      .then(() => { if (state.view === "query" && p.ref === ref) { drawTables(); if (here()) editor.refresh(); } });
  }
  editor.focus();
}
