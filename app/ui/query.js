// The SQL query page for a lakehouse or, as the Warehouse page, a Fabric warehouse: tabs, the CodeMirror editor, running through the engine and
// paging results. Split out of app.js (HD-056). It loads before app.js, which registers renderQuery,
// and uses app.js helpers (h, rpc, state) only when its functions are called.

// ---------- SQL query page ----------

// SQL over one lakehouse's tables, run on this PC by the sidecar (DuckDB). The editor is sqleditor.js.
const QUERY_HINT = "-- Spark SQL runs on this PC. Press Ctrl+Enter to run.\n";
const SQL_AUTO_CHECK_LIMIT = 100000;  // characters; a longer query is checked when the person asks
const WAREHOUSE_HINT = "-- T-SQL runs in Fabric on this warehouse and uses capacity. It only reads unless you tick Allow changes.\n";
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
  sqlPracticePolicies = null;  // policies may have changed since the last visit
  if (sqlRefKey(sqlPage.ref) !== sqlRefKey(ref)) {
    Object.assign(sqlPage, { ref, tabs: loadQueryTabs(ref), active: 0, tables: null, cols: {}, open: {}, pending: {},
      catalog: null, whOpen: null, whFocus: null, whFilter: "" });
  }
  go("query");
}

// The Warehouse page (WH-01): the SQL query page with a warehouse or SQL analytics endpoint as its target.
function openWarehouseQuery(ref) {
  openSqlQuery(ref);
}

// SQL best practices (Preview): SQ001 to SQ010 (sqlcompat/practices.py) on T-SQL, read on this PC with
// the compatibility check as you type; nothing runs. The project's governance policies tune the rules
// (governance_practice_settings), read once per visit to the page.
const SQL_PRACTICE_TARGETS = ["warehouse", "endpoint", "sqldb"];
let sqlPracticePolicies = null;  // a promise of { settings, policies, note }

function sqlPracticeSettings() {
  if (!sqlPracticePolicies) {
    sqlPracticePolicies = rpc("governance_practice_settings", { kind: "sql" }).catch((err) => ({ settings: {}, policies: [],
      note: `The policies couldn't be read, so every rule is on as built: ${String(err.message || err).replace(/^Error: /, "")}` }));
  }
  return sqlPracticePolicies;
}

function sqlPracticeLabel(r) {
  const n = r?.findings?.length || 0;
  return n ? `Best practices · ${n}` : "Best practices";
}

// A finding's place in the text, in code points as the engine counts them: line and col start at 1.
function sqlPracticeOffset(text, line, col) {
  const lines = text.split("\n");
  let at = 0;
  for (let i = 0; i < Math.min(line - 1, lines.length); i++) at += [...lines[i]].length + 1;
  return at + Math.max(col - 1, 0);
}

function queryTabsKey(ref) {
  return isWarehouseRef(ref) ? `fabric-desktop.sql.${sqlRefKey(ref)}` : `fabric-desktop.sql.${ref.lakehouse_id}`;
}

function loadQueryTabs(ref) {
  const wh = isWarehouseRef(ref);
  try {
    const saved = JSON.parse(localStorage.getItem(queryTabsKey(ref)));
    if (Array.isArray(saved) && saved.length) {
      const tabs = saved.map((t) => ({ name: String(t.name), sql: String(t.sql), dialect: sqlDialect(t.dialect),
        ...(t.file && typeof t.file.name === "string" ? { file: t.file } : {}),
        ...(t.proj && typeof t.proj.path === "string" ? { proj: t.proj } : {}) }));
      return wh ? tabs.map((t) => ({ ...t, dialect: ref.kind })) : tabs;
    }
  } catch { /* no saved tabs */ }
  return [wh ? { name: "Query 1", sql: WAREHOUSE_HINT, dialect: ref.kind } : { name: "Query 1", sql: QUERY_HINT, dialect: "spark" }];
}

function saveQueryTabs() {
  try {
    localStorage.setItem(queryTabsKey(sqlPage.ref),
      JSON.stringify(sqlPage.tabs.map((t) => ({ name: t.name, sql: t.sql, dialect: t.dialect || "spark", ...(t.file ? { file: t.file } : {}),
        ...(t.proj ? { proj: t.proj } : {}) }))));
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

// WH-08: the Messages view: what the last run did, from its status. Never the SQL.
function sqlMessages(ex, tab, target) {
  if (!ex) return h("p", { class: "muted-text", style: "padding:12px 16px" }, tab.error ? tab.error.replace(/^Error: /, "") : "No query has run in this tab yet.");
  const time = (s) => (s ? timeText(s * 1000, { seconds: true }) : "");
  const lines = [`Target: ${target}`];
  if (ex.created) lines.push(`Queued at ${time(ex.created)}`);
  if (ex.started) lines.push(`Started at ${time(ex.started)}`);
  if (ex.ended) lines.push(`Ended at ${time(ex.ended)} after ${ex.seconds} s`);
  lines.push(`State: ${SQL_STATE[ex.state]?.[1] || ex.state}${ex.state === "completed" ? ` · ${(ex.rows || 0).toLocaleString()} row${ex.rows === 1 ? "" : "s"}` +
    ` · ${(ex.columns?.length || 0).toLocaleString()} column${ex.columns?.length === 1 ? "" : "s"}` : ""}`);
  if (ex.change) lines.push(`Change: ${(ex.affected || 0).toLocaleString()} row${ex.affected === 1 ? "" : "s"} affected · ${ex.committed ? "committed" : "nothing kept"}`);
  if (ex.refused) lines.push(`Refused before it ran: statement ${ex.refused.statement} ${ex.refused.label}`);
  if (ex.error) lines.push(`Message: ${ex.error}`);
  if (ex.time_limit_minutes) lines.push(`Time limit: ${ex.time_limit_minutes} minutes`);
  return h("pre", { class: "sql-messages" }, lines.join("\n"));
}

// The rows the Chart view draws: the run's snapshot, a page at a time, up to SQL_CHART_ROWS.
async function sqlChartRows(execution) {
  const rows = [];
  let columns = [], total = 0;
  for (let offset = 0; rows.length < SQL_CHART_ROWS;) {
    const page = await rpc("sql_page", { execution, offset });
    if (page.rerun_needed) throw new Error(page.message);
    ({ columns, total } = page);
    rows.push(...page.rows);
    if (!page.has_next || !page.rows.length) break;
    offset += page.rows.length;
  }
  return { columns, rows: rows.slice(0, SQL_CHART_ROWS), total };
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
  const provider = wh ? warehouseSqlProvider(ref) : lakehouseSqlProvider(p, ref, () => { if (here()) editor.refresh(); });
  const editor = p.editor = createSqlEditor({
    doc: tab.sql,
    dialect: tab.dialect,
    provider,
    on: {
      change: setSql,
      run: () => run(),
      save: () => {
        if (tab.proj) { whProjectSave(tab).then((ok) => { if (ok) { saveQueryTabs(); p.whProjectView?.refresh(); } }); return; }
        const ok = saveQueryTabs();
        toast(ok ? `Saved ${tab.name}` : "Could not save the query: this PC's app storage is unavailable", !ok);
      },
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
      if (!text.trim()) tab.compat = tab.practices = null;
      if (here()) drawCompat();
      return null;
    }
    tab.checkLong = false;
    const failed = (err) => ({ failed: String(err).replace(/^Error: /, "") });
    const practices = async () => {
      if (!SQL_PRACTICE_TARGETS.includes(dialect)) return null;
      const pol = await sqlPracticeSettings();
      try {
        const out = await rpc("sql_practices", { query: text, target: dialect, settings: pol.settings || {} });
        await learnPrepareFindings(out.findings, (f) => text.split("\n")[f.line - 1]);  // learn.js: for Dismiss
        await learnPrepareFixes(out.findings, text, dialect, provider);  // learn.js: Try the fix
        return { ...out, settings: pol.settings || {}, policies: pol.policies || [], note: pol.note || "" };
      } catch (err) {
        return failed(err);
      }
    };
    // Practices draw when they arrive, so the compatibility result never waits for them.
    practices().then((bp) => {
      if (!here() || editor.text() !== text) return;
      tab.practices = bp;
      drawCompat();
    });
    const report = await rpc("sql_analyze", { query: text, target: dialect }).catch(failed);
    if (!here() || editor.text() !== text) return null;
    tab.compat = report;
    tab.compatText = text;
    drawCompat();
    return report.failed ? null : report;
  }

  // SQL-003: each run is a managed query on this PC (sql_exec.py): queued, then running in a process
  // of its own, with Stop and the time limit in Settings > Advanced > SQL queries. Results come a page at a time from
  // the run's snapshot, so a large result is never loaded whole. On the Warehouse page (WH-01) the
  // same managed run sends one query that reads to the warehouse (sql_start target "warehouse").
  async function run(query = editor.chosen(), allow = []) {
    if (!query.trim() || tab.running) return;
    if (!runsHere()) {
      toast(`${SQL_DIALECTS[tab.dialect].label} can't run on this PC. Pick Spark SQL or DuckDB to run here.`, true);
      return;
    }
    // WH-10b: with Allow changes ticked, ask the engine what the script does first; a change has its own confirm.
    const whRef = wh ? { workspace_id: ref.workspace_id, item_id: ref.item_id, kind: ref.kind } : null;
    let change = false;
    if (wh && whChangesTicked(ref)) {
      let plan;
      try {
        plan = await rpc("sql_start", { query, target: "warehouse", dialect: "tsql", warehouse_ref: whRef, allow: ["warehouse_write"], dry_run: true });
      } catch (err) {
        Object.assign(tab, { error: String(err), errorRetry: () => run(query), result: null, exec: null });
        sqlPage.redraw?.();
        return;
      }
      if (plan.refused) {
        Object.assign(tab, { error: `Statement ${plan.refused.statement} was refused (${plan.refused.reason}), so nothing was sent. A warehouse change can't do this.`,
          errorRetry: null, result: null, exec: null });
        sqlPage.redraw?.();
        return;
      }
      if (plan.permission === "warehouse_write") {
        if (!(await whChangeConfirm(ref, plan))) return;
        change = true;
      }
    }
    if (wh && !change && !(await warehouseRunOk(ref))) return;
    if (wh && !sqlPage.catalog) loadCatalog();
    tab.running = true;
    const startedAt = Date.now();
    Object.assign(tab, { error: null, errorRetry: null, result: null, exec: null, ranNotice: "", chart: null });
    drawResults();
    let status;
    try {
      status = await rpc("sql_start", wh
        ? { query, target: "warehouse", dialect: "tsql", warehouse_ref: whRef,
          ...(change ? { allow: ["warehouse_write"], confirm: true, confirm_name: ref.name } : {}) }
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
      if (wh && !change && status.refused && whChangesAllowed()) tab.error += " To change the warehouse, tick Allow changes at the top of the page.";
    }
    if (change && status?.state === "completed") {
      toast(`Changed ${ref.name}: ${(status.affected || 0).toLocaleString()} row${status.affected === 1 ? "" : "s"} affected, committed.`);
      if (sqlPage.catalog) loadCatalog();  // tables, views or procedures may have changed
    }
    // WH-07: each run that reached the engine goes into this PC's history for the warehouse.
    if (wh && status?.state) {
      sqlHistoryAdd(ref, { at: startedAt, sql: query, state: status.state, seconds: status.seconds ?? null, rows: status.rows ?? null,
        change: change || undefined, affected: change ? status.affected ?? null : undefined,
        error: status.state === "completed" ? undefined : sqlHistoryError(status.error) });
      if (p.whAside === "history") p.whRedraw?.();
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
      loadTables();  // read again, so the explorer lists the new table instead of "Loading tables..."
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
  function loadTables() {
    drawTables();
    rpc("list_tables", { workspace_id: ref.workspace_id, lakehouse_id: ref.lakehouse_id })
      .then((t) => { p.tables = t; })
      .catch((err) => { p.tables = {}; toast(`Could not list tables: ${friendlyError(err).text}`, true); })
      .then(() => { if (state.view === "query" && p.ref === ref) { drawTables(); if (here()) { editor.refresh(); editor.recheck(); } } });
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
    else if (st === "queued") msg = "Waiting for another query to finish (Settings > Advanced > SQL queries sets how many run at once)";
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
    // Messages and Chart are Preview: on the Warehouse page the whole page already is, so they carry no
    // tag of their own there; elsewhere they carry one and go when preview features are hidden.
    const extra = [...(wh || previewShown() ? ["messages", "chart"] : []), ...(wh ? ["plan"] : [])];
    const view = extra.includes(tab.view) ? tab.view : "results";
    const viewTab = (id, label) => {
      const b = h("button", { role: "tab", class: `sql-view${view === id ? " on" : ""}`, "aria-selected": String(view === id),
        onclick: () => { tab.view = id; drawResults(); } }, label);
      return id === "results" || wh ? b : preview("sqlResultViews", b);
    };
    band.replaceChildren(...[h("div", { class: "sql-views", role: "tablist", "aria-label": "Result views" },
      viewTab("results", "Results"), extra.length ? viewTab("messages", "Messages") : null, extra.length ? viewTab("chart", "Chart") : null,
      wh ? viewTab("plan", "Plan") : null),
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
    if (view === "plan") {
      grid.replaceChildren(tab.planning ? h("p", { class: "muted-text", style: "padding:12px 16px" }, "Asking Fabric for the estimated plan...")
        : sqlPlanView(tab.plan));
    } else if (view === "messages") {
      grid.replaceChildren(sqlMessages(ex, tab, wh ? `${ref.name}${ref.workspace_name ? ` (${ref.workspace_name})` : ""} · in Fabric` : `${ref.name} · on this PC`));
    } else if (view === "chart") {
      grid.replaceChildren(r && r.columns.length && ex?.state === "completed"
        ? sqlChartView(tab.chart ||= {}, () => sqlChartRows(ex.id))
        : h("p", { class: "muted-text", style: "padding:12px 16px" }, "Run a query to chart its result. The chart reads the rows already on this PC; nothing runs again."));
    } else if (tab.error && tab.errorRetry) {
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
  const closeTab = (i) => { p.tabs.splice(i, 1); p.active = Math.min(p.active, p.tabs.length - 1); saveQueryTabs(); renderQuery(); };
  const addTab = () => { p.whCompareShown = p.whMonitorShown = false; p.tabs.push({ name: nextTabName(), sql: wh ? WAREHOUSE_HINT : QUERY_HINT, dialect: tab.dialect }); p.active = p.tabs.length - 1; saveQueryTabs(); renderQuery(); };
  // WH-12: the compare view, when one was made for this warehouse, has a tab of its own.
  const compare = wh && p.whCompare?.key === sqlRefKey(ref) ? p.whCompare : null;
  const compareOn = !!(compare && p.whCompareShown);
  // WH-19: the Monitoring tab, once opened for this warehouse.
  const monitor = wh && p.whMonitor?.key === sqlRefKey(ref) ? p.whMonitor : null;
  const monitorOn = !!(monitor && p.whMonitorShown);
  // A tablist holds only tabs (#905): each wrapper is presentational, and its × is for the mouse; with the
  // keyboard, Delete on the tab closes it. New query sits beside the list, not in it.
  // Delete and × ask first when the query has SQL of its own (keyClose); after Delete, focus goes to the tab now selected.
  const sqlTab = (cls, on, label, iconName, select, close, extra = {}, keyClose = close) => h("div", { class: `sql-tab${cls}${on ? " on" : ""}`, role: "none" },
    h("button", { class: "name", role: "tab", "aria-selected": String(on), onclick: select,
      ...(close ? { "aria-keyshortcuts": "Delete", onkeydown: async (e) => {
        if (e.key !== "Delete") return;
        e.preventDefault();
        if (await keyClose() === false) return;
        document.querySelector(".sql-tabs [aria-selected=true]")?.focus();
      } } : {}),
      ...extra }, icon(iconName, 14), label),
    close ? h("button", { class: "close", "aria-hidden": "true", tabindex: "-1", title: `Close ${label} (Delete)`, onclick: () => keyClose() }, "×") : null);
  const tabs = h("div", { class: "sql-tabbar" },
    h("div", { class: "sql-tabs", role: "tablist", "aria-label": "Queries" },
      p.tabs.map((t, i) => sqlTab("", i === p.active && !compareOn && !monitorOn, t.name, "code",
        () => { p.active = i; p.whCompareShown = p.whMonitorShown = false; renderQuery(); },
        p.tabs.length > 1 ? () => closeTab(i) : null,
        { ondblclick: async () => {
          const name = await askText("Rename query", "Name", t.name, "Rename");
          if (name) { t.name = name; saveQueryTabs(); renderQuery(); }
        } },
        async () => {
          const sql = (t.sql || "").trim();
          if (sql && sql !== QUERY_HINT.trim() && sql !== WAREHOUSE_HINT.trim()
            && !(await confirmDialog(`Close ${t.name}?`, "Its SQL is discarded. It isn't saved anywhere else.", "Close query", { danger: true }))) return false;
          closeTab(i);
        })),
      compare ? sqlTab(" wh-cmp-tab", compareOn, "Compare", "branchCompare",
        () => { p.whCompareShown = true; p.whMonitorShown = false; renderQuery(); },
        () => { p.whCompare = null; p.whCompareShown = false; renderQuery(); }) : null,
      monitor ? sqlTab(" wh-mon-tab", monitorOn, "Monitoring", "monitor",
        () => { p.whMonitorShown = true; p.whCompareShown = false; renderQuery(); },
        () => { p.whMonitor = null; p.whMonitorShown = false; renderQuery(); }) : null),
    h("button", { class: "sql-tab add", "aria-label": "New query", onclick: addTab }, "+"));

  const dialectPick = h("select", { class: "sql-dialect", "aria-label": "SQL dialect",
    title: "The SQL you are writing and the engine it's checked against. It sets highlighting, suggestions, formatting and the compatibility check; only Spark SQL and DuckDB run on this PC.",
    onchange: (e) => { tab.dialect = sqlDialect(e.target.value); saveQueryTabs(); editor.setDialect(tab.dialect); drawActions(); drawCompat(); } },
  Object.entries(SQL_DIALECTS).map(([id, d]) => h("option", { value: id, selected: id === tab.dialect }, d.label)));
  const actions = h("div", { class: "sql-actions" });
  // The compatibility check as a chip beside Format SQL (Cleaner KratosForge): the counts at a glance.
  // Its panel beside the editor opens when the chip is pressed, or by itself when the check finds an
  // error or a warning; closing it holds until the findings change.
  // Compatibility and Best practices share the panel; each chip opens it on its own tab, or closes it.
  const showPane = (pane) => {
    const open = compatOpen() && (tab.sqlPane || "compat") === pane;
    tab.sqlPane = pane;
    tab.compatShown = { open: !open, sig: compatSig() };
    drawCompat();
  };
  const compatChip = h("button", { class: "sql-compat-chip", "aria-controls": "sql-compat", title: "Compatibility: open or close the check's findings",
    onclick: () => showPane("compat") });
  const practicesChip = h("button", { class: "sql-practices-chip", "aria-controls": "sql-compat", "data-practices": true,
    title: "Best practices: what may be slow or fragile even when it runs. Read on this PC; nothing runs.",
    onclick: () => showPane("practices") });
  // The findings to show (learn.js): dismissed ones leave the list and the counts; a rule a policy names stays.
  function practiceSplit(r) {
    const ctx = { where: tab.file ? `${tab.file.place}/${tab.file.name}` : `${ref?.lakehouse_id || ref?.name || ""}/${tab.name}`, required: (id) => !!r?.settings?.[id]?.enabled, redraw: () => drawCompat(), fix: fixActions };
    const { shown, hidden } = learnSplitFindings(r?.findings, ctx.where, ctx.required);
    const counts = Object.fromEntries(["Error", "Warning", "Info"].map((s) => [s, shown.filter((f) => f.severity === s).length]));
    return { ctx, shown, hidden, counts };
  }
  // Try the fix (learn.js, GD-02). Apply is one edit in the editor, undone with Ctrl+Z. Compare runs
  // the statement as written and as fixed on the warehouse or SQL analytics endpoint, one after the
  // other, after a confirm each time: both only read, and both use capacity. A lakehouse tab's T-SQL
  // doesn't run on this PC, so it has no Compare.
  const fixActions = {
    where: "in Fabric",
    apply(f) {
      const text = editor.text();
      if (text !== tab.compatText) { toast("The SQL changed since this check. The findings update in a moment."); return false; }
      editor.view.dispatch({ changes: { from: sqlUtf16Offset(text, f.fix.start), to: sqlUtf16Offset(text, f.fix.end), insert: f.fix.text } });
      editor.view.focus();
      toast(`Applied the fix for ${f.rule_id}. Ctrl+Z undoes it.`);
      return true;
    },
    canCompare: (f) => wh && !!f.fix.compare,
    whyNoCompare: (f) => (!wh ? "Compare runs on a warehouse or SQL analytics endpoint's page; T-SQL doesn't run on this PC."
      : "Compare needs a statement that only reads, such as a SELECT or a view's query."),
    async compare(f) {
      const text = editor.text();
      if (text !== tab.compatText) throw new Error("The SQL changed since this check. Try the fix again once the findings update.");
      const ok = await confirmDialog(`Run both versions in Fabric on ${ref.name}?`,
        `Compare runs the query as written, then as fixed, on the ${ref.kind === "endpoint" ? "SQL analytics endpoint" : "warehouse"}. ` +
        "Both only read, and both use capacity. Nothing in the editor changes until you Apply.", "Run both");
      if (!ok) return null;
      const job = fixActions.job = { id: null, stopped: false };
      // Stop, or leaving this tab, ends the run in Fabric (both use capacity) and polls no more.
      const one = async (query) => {
        let st = await rpc("sql_start", { query, target: "warehouse", dialect: "tsql",
          warehouse_ref: { workspace_id: ref.workspace_id, item_id: ref.item_id, kind: ref.kind } });
        job.id = st.id;
        let wait = 150;
        while (["queued", "running", "cancelling"].includes(st.state)) {
          if (!job.stopped && !here()) fixActions.stop();
          if (job.stopped) { rpc("sql_cancel", { execution: st.id }).catch(() => {}); return null; }
          await new Promise((r) => setTimeout(r, wait));
          wait = Math.min(1000, wait * 1.5);
          st = await rpc("sql_status", { execution: st.id });
        }
        if (job.stopped) return null;
        if (st.state !== "completed") throw new Error(st.error || `The query ${SQL_STATE[st.state]?.[1].toLowerCase() || st.state}.`);
        return st;
      };
      try {
        const before = await one(f.fix.compare.before);
        const after = before && await one(f.fix.compare.after);
        return after ? await rpc("learn_fix_compare", { before: before.id, after: after.id }) : null;
      } finally {
        if (fixActions.job === job) fixActions.job = null;
      }
    },
    stop() {
      const job = fixActions.job;
      if (!job || job.stopped) return;
      job.stopped = true;
      if (job.id) rpc("sql_cancel", { execution: job.id }).catch(() => {});
    },
    job: null,
  };
  function drawPracticesChip() {
    const r = tab.practices;
    practicesChip.hidden = !SQL_PRACTICE_TARGETS.includes(tab.dialect) || !previewShown();  // Preview: hidden with the previews
    const { shown, counts } = practiceSplit(r);
    const worst = counts.Error ? "error" : counts.Warning ? "warning" : "";
    practicesChip.className = `sql-practices-chip${worst ? ` ${worst}` : ""}`;
    practicesChip.setAttribute("aria-expanded", String(compatOpen() && tab.sqlPane === "practices"));
    practicesChip.replaceChildren(r?.failed ? "Best practices: check failed" : sqlPracticeLabel({ findings: shown }), previewTag(), icon("chevronDown", 14));
  }
  // The engine's outcome decides the chip, so SQL it couldn't verify or didn't check never reads as clean.
  const compatStatus = () => {
    const r = tab.compat;
    if (!r || r.failed) return "";
    if (r.outcome?.status) return r.outcome.status;
    return r.counts.error ? "known_problems" : r.counts.warning ? "review" : "no_known_problems";
  };
  const compatSig = () => (tab.compat?.counts ? `${compatStatus()}/${tab.compat.counts.error}/${tab.compat.counts.warning}`
    : tab.compat?.failed ? "failed" : "");
  const compatOpen = () => {
    if (tab.compatShown && tab.compatShown.sig === compatSig()) return tab.compatShown.open;
    return !!(tab.compatLong || tab.compat?.failed || ["known_problems", "review", "unverified"].includes(compatStatus()));
  };
  function drawCompatChip() {
    const r = tab.compat;
    const c = r?.counts;
    const n = (k, one, many) => `${c[k].toLocaleString()} ${c[k] === 1 ? one : many}`;
    const status = compatStatus();
    const [cls, text] = tab.compatLong ? ["", "Check SQL"] : !r ? ["", "Compatibility"] : r.failed ? ["error", "Check failed"]
      : status === "known_problems" ? ["error", [n("error", "error", "errors"), c.warning ? n("warning", "warning", "warnings") : ""].filter(Boolean).join(", ")]
        : status === "review" ? ["warning", n("warning", "warning", "warnings")]
          : status === "unverified" ? ["unverified", "Not verified"]
            : status === "not_checked" ? ["", editor.text().trim() ? "Not checked" : "Compatibility"]
              : ["ok", "No known problems"];
    compatChip.className = `sql-compat-chip${cls ? ` ${cls}` : ""}`;
    compatChip.setAttribute("aria-expanded", String(compatOpen()));
    compatChip.replaceChildren(text, previewShown() ? previewTag() : null, icon("chevronDown", 14));
  }
  // WH-09: Fabric's estimated plan for the query (or the selection), without running it. It has a
  // run of its own, so the tab's results stay as they were.
  async function estimatedPlan(query = editor.chosen()) {
    if (!query.trim() || tab.planning) return;
    if (!(await warehouseRunOk(ref))) return;
    Object.assign(tab, { planning: true, plan: null, view: "plan" });
    drawResults();
    try {
      let status = await rpc("sql_start", { query, target: "warehouse", dialect: "tsql",
        warehouse_ref: { workspace_id: ref.workspace_id, item_id: ref.item_id, kind: ref.kind }, estimated_plan: true });
      let wait = 150;
      while (["queued", "running", "cancelling"].includes(status.state)) {
        await new Promise((r) => setTimeout(r, wait));
        wait = Math.min(1000, wait * 1.5);
        status = await rpc("sql_status", { execution: status.id });
      }
      if (status.state !== "completed") tab.plan = { error: status.error || `The estimated plan ${status.state}.` };
      else {
        const page = await rpc("sql_page", { execution: status.id, offset: 0 });
        tab.plan = { xml: String(page.rows?.[0]?.[0] ?? ""), at: new Date() };
      }
    } catch (err) {
      tab.plan = { error: String(err) };
    } finally {
      tab.planning = false;
      sqlPage.redraw?.();
    }
  }
  function drawActions() {
    if (wh) {
      actions.replaceChildren(btn("Run", "play", () => run(), "tool primary"),
        btn("Estimated plan", "flowchart", () => estimatedPlan(), "tool outline",
          { title: "How Fabric would run the query (or the selection), without running it. Uses a little capacity." }),
        h("span", { class: "muted-text" }, "Ctrl+Enter, or select text to run just that"),
        h("span", { class: "sql-sep", "aria-hidden": "true" }),
        h("span", { class: "sql-target-note" }, SQL_DIALECTS[tab.dialect].label),
        btn("Format SQL", null, formatSql, "tool outline", { title: "Format the selection, or the whole query (Shift+Alt+F)" }),
        compatChip, practicesChip);
      return;
    }
    const local = runsHere();
    actions.replaceChildren(btn("Run", "play", () => run(), "tool primary", { disabled: !local }),
      h("span", { class: "muted-text" }, local ? "Ctrl+Enter, or select text to run just that"
        : `${SQL_DIALECTS[tab.dialect].short} is formatted here, not run`),
      h("span", { class: "sql-sep", "aria-hidden": "true" }),
      dialectPick,
      btn("Format SQL", null, formatSql, "tool outline", { title: "Format the selection, or the whole query (Shift+Alt+F)" }),
      compatChip, practicesChip,
      h("span", { class: "spacer" }),
      btn("Save as table", "save", saveAsTable, "tool outline", { disabled: !local }),
      btn("Open in notebook", "notebook", openInNotebook, "tool outline", { disabled: tab.dialect !== "spark" }));
  }
  drawActions();

  // The Compatibility panel beside the editor: the last check's outcome and findings, newest SQL first.
  const compat = h("aside", { class: "sql-compat", id: "sql-compat", "aria-label": "Compatibility" });
  const compatBody = h("div", { class: "sql-compat-body" });
  compat.append(compatBody);
  async function exportCompat() {
    const out = await attempt(() => rpc("sql_compat_export", { query: editor.text(), target: tab.dialect, name: tab.name }),
      "Could not export the report");
    if (out) toast(`Saved the compatibility report to ${out.path}`);
  }
  function drawCompat() {
    compat.hidden = !compatOpen();
    drawCompatChip();
    drawPracticesChip();
    if (practicesChip.hidden) tab.sqlPane = "compat";
    if (tab.sqlPane === "practices") { drawPractices(); return; }
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
  // The Best practices tab of the panel: findings worst first, each with Line n to select it.
  function drawPractices() {
    const r = tab.practices;
    const head = h("div", { class: "sql-compat-head" }, h("b", {}, "Best practices"), previewShown() ? previewTag() : null);
    const intro = h("p", { class: "muted-text sql-compat-note" },
      "Read on this PC as you type; nothing runs. What may be slow or fragile even when it runs. It never changes the compatibility result.");
    if (!r) {
      compatBody.replaceChildren(head, intro, h("p", { class: "muted-text" }, "Write T-SQL to check it."));
      return;
    }
    if (r.failed) {
      compatBody.replaceChildren(head, h("p", { class: "output error" }, `Could not check the SQL: ${r.failed}`));
      return;
    }
    const chip = (n, label, cls) => h("span", { class: `sql-chip ${cls}` }, `${n.toLocaleString()} ${label}${n === 1 || label === "info" ? "" : "s"}`);
    const { ctx, shown, hidden, counts } = practiceSplit(r);
    const list = shown.map((f) => h("div", { class: "sql-practice", "data-rule": f.rule_id },
      h("div", {}, h("b", { class: `sql-practice-sev ${f.severity.toLowerCase()}` }, f.severity), ` · ${f.rule}`),
      h("div", { class: "muted-text" }, f.description),
      h("div", {}, h("button", { class: "link-btn", onclick: () => {
        const text = editor.text();
        if (text !== tab.compatText) { toast("The SQL changed since this check. The findings update in a moment."); return; }
        const at = sqlPracticeOffset(text, f.line, f.col);
        editor.reveal(sqlUtf16Offset(text, at), sqlUtf16Offset(text, at + 1));
      } }, `Line ${f.line}`), h("span", { class: "muted-text" }, ` · ${f.rule_id} · ${f.category}${f.url ? " · " : ""}`),
      f.url ? docLink(f.url, `About ${f.rule_id}`) : null), learnFindingActions(f, ctx)));
    compatBody.replaceChildren(...[head, intro,
      h("div", { class: "sql-chips" }, chip(counts.Error, "error", "error"), chip(counts.Warning, "warning", "warning"), chip(counts.Info, "info", "information")),
      r.note ? h("p", { class: "muted-text" }, r.note) : null,
      list.length ? h("div", { class: "sql-findings" }, list) : h("p", { class: "muted-text" }, "Nothing to look at. Every rule that's on passes."),
      learnDismissedBlock(hidden, ctx),
      h("p", { class: "muted-text sql-compat-note" }, r.policies.length
        ? `Tuned by ${r.policies.join(", ")}: a rule a policy turns off isn't shown.`
        : "No policy for this project names a SQL rule, so every rule is on as built. A governance policy can name them in Checked by.")]
      .filter(Boolean));
  }
  drawCompat();

  const edit = h("div", { class: "sql-edit" });
  edit.append(editor.view.dom);
  // A new tab holding text from the explorer's Script as or a template (WH-03, WH-04).
  const openTab = (name, text) => {
    p.whCompareShown = p.whMonitorShown = false;
    p.tabs.push({ name, sql: text, dialect: tab.dialect });
    p.active = p.tabs.length - 1;
    saveQueryTabs();
    renderQuery();
  };
  // WH-02: the object explorer, from the warehouse's catalog. It lists by itself once runs on this
  // warehouse are confirmed this session; before that it waits for List objects.
  // Once the catalog is in, check again: Try the fix needs column types (learnPrepareFixes).
  const loadCatalog = () => whLoadCatalog(ref, () => {
    if (state.view === "query" && p.ref === ref) p.whRedraw?.();
    if (here() && p.catalog?.state === "done") editor.recheck();
  });
  const whTree = wh ? whExplorer(ref, {
    load: async () => { if (await warehouseRunOk(ref)) loadCatalog(); },
    query: (name) => queryTable(name),
    insert: (text) => insertAtCursor(text),
    open: (name, text) => openTab(name, text),
  }) : null;
  // WH-06: saved and shared queries, on the explorer's Queries tab. A saved query opens in its own
  // tab, or in the tab that already holds it.
  const openSaved = (q) => {
    p.whCompareShown = p.whMonitorShown = false;
    const at = p.tabs.findIndex((t) => t.file?.place === q.place && t.file?.name === q.name);
    if (at < 0) p.tabs.push({ name: q.title, sql: q.sql, dialect: tab.dialect,
      file: { place: q.place, name: q.name, place_label: q.place_label, title: q.title, description: q.description || "",
        warehouse: q.warehouse || "", version: q.version || "" } });
    p.active = at < 0 ? p.tabs.length - 1 : at;
    saveQueryTabs();
    renderQuery();
  };
  const whSaved = wh ? whQueries(ref, { open: openSaved }) : null;
  const openInsight = (name, text) => {
    p.whCompareShown = p.whMonitorShown = false;
    p.tabs.push({ name, sql: text, dialect: tab.dialect, autorun: true });
    p.active = p.tabs.length - 1;
    saveQueryTabs();
    renderQuery();
  };
  const whRuns = wh ? whHistory(ref, { open: (name, text) => openTab(name, text), insight: openInsight }) : null;
  const saveQuery = async (asNew) => {
    const file = await saveSqlQuery(ref, tab, asNew);
    if (!file) return;
    if (asNew || !tab.file) tab.name = file.title;
    tab.file = file;
    saveQueryTabs();
    await sqlLoadQueries();
    if (p.ref === ref && state.view === "query") renderQuery();
  };
  // WH-11: the warehouse's project in Git, on the explorer's Project tab. A file opens in its own
  // tab, or in the tab that already holds it; Save writes it back.
  const openProjectFile = async (f, project) => {
    p.whCompareShown = p.whMonitorShown = false;
    const at = p.tabs.findIndex((t) => t.proj?.repo === project.repo && t.proj?.folder === project.folder && t.proj?.path === f.path);
    if (at < 0) {
      const file = await attempt(() => rpc("wh_project_read", { repo: project.repo, folder: project.folder, path: f.path }), "Could not open the file");
      if (!file) return;
      p.tabs.push({ name: f.path.slice(f.path.lastIndexOf("/") + 1).replace(/\.sql$/i, ""), sql: file.text, dialect: tab.dialect,
        proj: { repo: project.repo, folder: project.folder, path: f.path, version: file.version } });
    }
    p.active = at < 0 ? p.tabs.length - 1 : at;
    saveQueryTabs();
    renderQuery();
  };
  const whProj = wh && ref.kind === "warehouse" ? (p.whProjectView = whProject(ref, { open: openProjectFile })) : null;
  const saveFile = async () => { if (await whProjectSave(tab)) { saveQueryTabs(); whProj?.refresh(); } };
  const asideView = wh && ["queries", "history", ...(whProj ? ["project"] : [])].includes(p.whAside) ? p.whAside : "explorer";
  const asideTab = (id, label) => h("button", { role: "tab", class: `sql-view${asideView === id ? " on" : ""}`, "aria-selected": String(asideView === id),
    onclick: () => { p.whAside = id; renderQuery(); } }, label);
  p.whRedraw = wh ? () => { whTree.draw(); whSaved.draw(); whRuns.draw(); whProj?.draw(); if (here()) editor.refresh(); } : null;
  const fabricLink = wh ? fabricItemLink({ type: ref.kind === "endpoint" ? "Lakehouse" : "Warehouse", workspace_id: ref.workspace_id, item_id: ref.item_id }) : null;
  setView(wh ? h("div", { class: "sql-page warehouse-page" },
    h("div", { class: "ribbon" }, h("div", { class: "toolbar" },
      h("div", { class: "split" }, btn("New SQL query", "add", addTab),
        h("button", { class: "tool split-more", "aria-label": "More ways to start a query", title: "More ways to start a query",
          onclick: (e) => showMenu(e.currentTarget, [{ label: "SQL from a template", icon: "document",
            action: () => openSqlTemplates(ref, openTab, { draft: () => editor.text() }) }]) }, icon("chevronDown", 14))),
      tab.proj ? btn("Save file", "save", saveFile, "tool", { title: `Save to ${tab.proj.path} in ${tab.proj.folder} (Ctrl+S). Commit it in Source control.` })
        : btn("Save query", "save", () => saveQuery(false), "tool", { title: tab.file ? `Save to ${tab.file.name} in ${tab.file.place === "mine" ? "My queries" : tab.file.place_label}` : "Save this tab as a .sql file in My queries or a Git repository" }),
      tab.file && !tab.proj ? btn("Save as new", null, () => saveQuery(true), "tool") : null,
      btn("Open in Fabric", "open", () => openInFabric(fabricLink)),
      btn("Monitoring", "monitor", () => whMonitorOpen(ref), "tool", { title: "Fabric's workspace monitoring for this warehouse, and its monitoring views (Preview)" }),
      h("span", { class: "spacer" }),
      whChangesSwitch(ref, () => renderQuery()),
      h("span", { class: `sql-capacity-note${whChangesTicked(ref) ? " changes" : ""}`, role: "note",
        title: whChangesTicked(ref) ? `Queries run in Fabric on ${ref.name} and use capacity. A change runs only after it shows what it changes and you confirm.`
          : `Queries run in Fabric on ${ref.name} and use capacity. Only a query that reads runs here.` },
        whChangesTicked(ref) ? "Runs in Fabric · uses capacity · changes after you confirm" : "Runs in Fabric · uses capacity · reads only"),
      learnMore("warehouse-queries-preview"))),
    h("div", { class: "nb-body" },
      h("aside", { class: "explorer" }, h("div", { class: "explorer-head sql-views", role: "tablist", "aria-label": "Explorer views" },
        asideTab("explorer", "Explorer"), asideTab("queries", "Queries"), asideTab("history", "History"), whProj ? asideTab("project", "Project") : null),
        asideView === "queries" ? whSaved.el : asideView === "history" ? whRuns.el : asideView === "project" ? whProj.el : null,
        asideView !== "explorer" ? null : h("div", { class: "tree-row", style: "margin:4px 8px 0;font-weight:600;cursor:default" },
          itemIcon(ref.kind === "endpoint" ? "SQLEndpoint" : "Warehouse", 16), h("span", { class: "label" }, ref.name), previewTag()),
        asideView !== "explorer" ? null : h("div", { class: "tree-row muted", style: "cursor:default" }, ref.kind === "endpoint" ? "SQL analytics endpoint" : "Warehouse",
          ref.workspace_name ? ` · ${ref.workspace_name}` : ""),
        asideView !== "explorer" ? null : whTree.el,
        h("div", { class: "warehouse-how" }, howItWorks("warehouse.query", {
          reads: "The warehouse's SQL connection, from Fabric, then the rows your query asks for. The explorer reads the warehouse's catalog views (sys.objects, sys.columns) the same way. It signs in as you, with a token for Azure SQL.",
          changes: "Nothing, until you tick Allow changes. Then a script that changes the warehouse (DDL, DML, EXEC) runs only after it shows what it changes and you confirm, typing the warehouse's name in Prod; it runs in one transaction, so a failure keeps nothing.",
          results: "A snapshot of the rows on this PC, read a page at a time, with Copy and Export to Downloads. The query text stays on this PC.",
          more: [["Capacity", "The query runs in Fabric, so it uses capacity on the warehouse's workspace."],
            ["Your templates", "Saving, importing or deleting a template of your own writes or removes a .sql file in My templates (the app's data folder) or in the sql-templates folder of a Git repository you opened. Export writes a new file in Downloads."],
            ["Saved queries", "Save query writes the tab to a .sql file in My queries (the app's data folder) or in the sql-queries folder of a Git repository you opened; Delete removes that file. Nothing is committed for you."],
            ["Project", "The Project tab reads and writes .sql files in a <warehouse>.Warehouse folder of a Git repository you opened, in Fabric's Git layout. Import from the warehouse and Compare run catalog queries in Fabric. Import writes new files and, only if you tick it, replaces files that differ; Compare writes nothing unless you take the warehouse's version of an object. Nothing is committed and no file is removed. Deploy changes the warehouse: it previews each change and runs only the ones you tick, each as a confirmed change; Test and Prod deploy only committed, pushed files."],
            ["Monitoring", "Reads the workspace's monitoring KQL database (Fabric's WarehouseExecutions table), one small KQL query a view, as you; it uses capacity at Eventhouse rates. Build views creates or updates a KQL queryset per warehouse in the workspace, after you confirm, or without asking in a workspace where you ticked that; other tabs in it stay. Who runs what lists each person's sign-in name with their queries and CPU time."],
            ["History", "Each run here is kept with its SQL in the app's storage on this PC (up to 200 per warehouse) until you clear it. Query insights run a query on the warehouse's queryinsights views in Fabric."]],
          anchor: "warehouse-queries-preview" }))),
      h("section", { class: "sql-main" }, tabs,
        monitorOn ? whMonitorView(ref, { insight: openInsight })
          : compareOn ? whCompareView(ref, { open: openProjectFile })
          : h("div", { class: "sql-card" }, actions, h("div", { class: "sql-editor-row" }, edit, compat), band, grid))))
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
  if (wh) whMonitorAutoCheck(ref);
  if (wh && !p.envRead) { p.envRead = true; whEnvRead(); }
  if (!p.tables && !wh) loadTables();
  editor.focus();
}

// The query tabs' own key, listed in the Keyboard shortcuts dialog while the SQL page is open (sqlTab above).
registerShortcuts("SQL query", [
  { keys: ["Delete"], label: "On a query tab: close it (asks first when it has SQL of its own)", when: () => !!document.querySelector(".sql-tabs") },
]);
