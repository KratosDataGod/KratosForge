// The warehouse's Monitoring tab (WH-19, Preview): Fabric's workspace monitoring for one warehouse.
// When a workspace admin turned on workspace monitoring, Fabric records every completed query in
// the WarehouseExecutions table of a read-only KQL database; the tab reads it (one small KQL query a
// view, as you) and builds the warehouse's monitoring views in Fabric: a KQL queryset next to that
// database with one tab per view. Building writes only that queryset, after a confirm, or without
// asking in a workspace you said yes to before (Build them for new warehouses ...).

const WH_MON_VIEWS = [
  ["slowest", "Slowest queries"], ["activity", "Activity"], ["durations", "Duration percentiles"],
  ["failures", "Failures"], ["people", "Who runs what"], ["alert", "Failure alert check"],
];
const WH_MON_WINDOWS = [["1h", "Last hour"], ["24h", "Last 24 hours"], ["7d", "Last 7 days"], ["30d", "Last 30 days"]];
const whMonAuto = new Set();  // warehouses whose automatic build was tried this session
const whMonAutoChecked = new Set();  // warehouses whose views were checked when the page opened them

const whMonKey = (ref) => sqlRefKey(ref);
const whMonQueryset = (name) => `${(String(name || "").trim().replace(/[^A-Za-z0-9_.-]/g, "_") || "warehouse").slice(0, 200)}_monitoring`;
// Names of the workspaces said yes to, kept on this PC so Settings can show them (the setting holds ids).
function whMonWorkspaceName(ws) {
  try { return JSON.parse(localStorage.getItem("fd.whmon.names") || "{}")[ws] || ws; } catch { return ws; }
}
function whMonRememberName(ws, name) {
  try {
    const names = JSON.parse(localStorage.getItem("fd.whmon.names") || "{}");
    names[String(ws).toLowerCase()] = name;
    localStorage.setItem("fd.whmon.names", JSON.stringify(names));
  } catch { /* the id shows instead */ }
}
const whMonAutoOn = (ws) => (state.settings?.wh_monitoring_auto?.value || []).map((w) => String(w).toLowerCase()).includes(String(ws).toLowerCase());
// Monitoring names a SQL analytics endpoint by its own id, not its lakehouse's.
async function whMonItemId(ref) {
  if (ref.kind !== "endpoint") return ref.item_id;
  const items = await rpc("list_items", { workspace_id: ref.workspace_id });
  return items.find((i) => i.type === "SQLEndpoint" && i.displayName === ref.name)?.id || "";
}

// Open the tab (the page's Monitoring button).
function whMonitorOpen(ref) {
  const p = sqlPage;
  if (p.whMonitor?.key !== whMonKey(ref)) p.whMonitor = { key: whMonKey(ref), view: "slowest", window: "24h" };
  p.whMonitorShown = true;
  p.whCompareShown = false;
  renderQuery();
  if (!p.whMonitor.status && !p.whMonitor.loading) whMonitorLoad(ref);
}

async function whMonitorLoad(ref) {
  const m = sqlPage.whMonitor;
  if (!m || m.key !== whMonKey(ref)) return;
  Object.assign(m, { loading: true, error: "" });
  whMonitorRedraw(ref);
  try {
    m.item = m.item || await whMonItemId(ref);
    m.status = await rpc("wh_monitoring_status", { workspace_id: ref.workspace_id });
    if (m.status.monitored) await Promise.all([whMonitorRead(ref, "summary"), whMonitorRead(ref, m.view)]);
  } catch (err) {
    m.error = String(err).replace(/^Error: /, "");
  }
  m.loading = false;
  whMonitorRedraw(ref);
  if (m.status?.monitored && m.status.auto) whMonitorEnsure(ref, m.status);
}

async function whMonitorRead(ref, view) {
  const m = sqlPage.whMonitor;
  const db = m.status.database;
  const out = await rpc("wh_monitoring_view", { database_workspace_id: db.workspace_id, database_id: db.item_id,
    warehouse_id: m.item, view, window: m.window });
  if (view === "summary") m.summary = out; else m.result = out;
}

function whMonitorRedraw(ref) {
  if (state.view === "query" && sqlPage.ref === ref && sqlPage.whMonitorShown) renderQuery();
}

// Build without asking, in a workspace you said yes to: once a session per warehouse.
async function whMonitorEnsure(ref, status) {
  const key = whMonKey(ref);
  if (whMonAuto.has(key) || !status.build_allowed || status.querysets?.[whMonQueryset(ref.name)]) return;
  whMonAuto.add(key);
  const item = await whMonItemId(ref).catch(() => "");
  if (!item) return;
  const db = status.database;
  const out = await rpc("wh_monitoring_build", { workspace_id: ref.workspace_id, database_workspace_id: db.workspace_id,
    database_id: db.item_id, warehouses: [{ id: item, name: ref.name }], confirm: true }).catch((err) => ({ error: String(err) }));
  const r = out.results?.[0];
  if (r && r.state !== "failed") {
    toast(`Built monitoring views for ${ref.name} in Fabric (${r.queryset}), as you asked for this workspace.`);
    if (sqlPage.whMonitor?.key === key) { sqlPage.whMonitor.status = null; whMonitorLoad(ref); }
  } else {
    toast(`Could not build monitoring views for ${ref.name}: ${r?.error || out.error || "unknown error"}`, true);
  }
}

// When the page opens a warehouse in a workspace you said yes to, its views are checked quietly.
function whMonitorAutoCheck(ref) {
  if (!whMonAutoOn(ref.workspace_id) || whMonAutoChecked.has(whMonKey(ref))) return;
  whMonAutoChecked.add(whMonKey(ref));  // once a session: each check reads Fabric and the monitoring database
  rpc("wh_monitoring_status", { workspace_id: ref.workspace_id })
    .then((status) => { if (status.monitored && status.auto) whMonitorEnsure(ref, status); })
    .catch(() => {});
}

// The build confirm: this workspace's warehouses and endpoints without views, all ticked.
async function whMonitorBuild(ref) {
  const m = sqlPage.whMonitor;
  const db = m.status.database;
  const items = await attempt(() => rpc("list_items", { workspace_id: ref.workspace_id }), "Could not list the warehouses");
  if (!items) return;
  const built = m.status.querysets || {};
  const all = items.filter((i) => ["Warehouse", "SQLEndpoint"].includes(i.type))
    .map((i) => ({ id: i.id, name: i.displayName, kind: i.type === "SQLEndpoint" ? "SQL analytics endpoint" : "warehouse",
      exists: !!built[whMonQueryset(i.displayName)] }))
    .sort((a, b) => (b.id === m.item) - (a.id === m.item) || a.name.localeCompare(b.name));
  const boxes = all.map((w) => h("input", { type: "checkbox", checked: w.id === m.item || !w.exists || null, "aria-label": w.name }));
  const auto = h("input", { type: "checkbox" });  // automatic writes are opt-in
  const go = btn("Build", null, () => finish(true), "tool primary");
  const update = () => {
    const n = boxes.filter((b) => b.checked).length;
    go.disabled = !n;
    go.textContent = n ? `Build ${n} queryset${n === 1 ? "" : "s"}` : "Build";
  };
  boxes.forEach((b) => { b.onchange = update; });
  let done;
  const answer = new Promise((resolve) => { done = resolve; });
  const finish = (ok) => { closeDialog(); done(ok); };
  update();
  showDialog(`Build monitoring views in ${ref.workspace_name || "this workspace"}?`, h("div", { class: "wh-mon-build" },
    h("p", {}, "Each ticked warehouse gets a KQL queryset with six views over WarehouseExecutions, filtered to it, reading ",
      h("code", {}, db.name), "."),
    h("table", { class: "wh-mon-table" }, h("tbody", {}, all.map((w, i) => h("tr", {},
      h("td", {}, h("label", {}, boxes[i], ` ${w.name}`), w.kind === "SQL analytics endpoint" ? h("span", { class: "muted-text" }, " (SQL analytics endpoint)") : null),
      h("td", { class: "muted-text" }, w.exists ? `update: ${whMonQueryset(w.name)} (your own tabs stay)` : `new: ${whMonQueryset(w.name)}`))))),
    h("p", { class: "muted-text" }, "Writes: one KQL queryset item per ticked warehouse in this workspace. Nothing changes in the warehouses or the monitoring database. Opening a view runs a small KQL query, billed at Eventhouse rates."),
    h("label", { class: "check" }, auto, " Build them for new warehouses in this workspace without asking again (Settings > Advanced lists these workspaces)")),
  [btn("Not now", null, () => finish(false), "tool outline"), go]);
  if (!(await answer)) return;
  const picked = all.filter((_, i) => boxes[i].checked).map((w) => ({ id: w.id, name: w.name }));
  const out = await attempt(() => rpc("wh_monitoring_build", { workspace_id: ref.workspace_id, database_workspace_id: db.workspace_id,
    database_id: db.item_id, warehouses: picked, confirm: true, auto: auto.checked }), "Could not build the monitoring views");
  if (!out) return;
  if (auto.checked) { whMonRememberName(ref.workspace_id, ref.workspace_name || ref.workspace_id); await loadSettings(); }
  const failed = out.results.filter((r) => r.state === "failed");
  if (failed.length) toast(`Built ${out.results.length - failed.length} of ${out.results.length}. ${failed.map((r) => `${r.queryset}: ${r.error}`).join(" ")}`, true);
  else toast(`Built monitoring views for ${out.results.length} warehouse${out.results.length === 1 ? "" : "s"} in Fabric.`);
  m.status = null;
  whMonitorLoad(ref);
}

const whMonCell = (v, type) => {
  if (v === null || v === undefined || v === "") return "";
  if (/datetime/i.test(type || "")) return dateTimeText(v, { seconds: true, empty: String(v) });
  if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return typeof v === "object" ? JSON.stringify(v) : String(v);
};
const whMonSecs = (ms) => (ms === null || ms === undefined || ms === "" ? "–" : `${(Number(ms) / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })} s`);
// A Query insights tab for one query: an OperationId is the request's distributed_statement_id.
const whMonInsightSql = (op) => "-- This query in the warehouse's own record (Query insights). It can take up to 15 minutes to show.\n" +
  `SELECT distributed_statement_id, start_time, end_time, login_name, status, total_elapsed_time_ms,\n  allocated_cpu_time_ms, command\nFROM queryinsights.exec_requests_history\nWHERE distributed_statement_id = '${op}';\n`;

// The tab, in place of the editor. actions.insight(name, sql) opens and runs a query tab.
function whMonitorView(ref, actions) {
  const m = sqlPage.whMonitor;
  const s = m.status;
  const head = h("div", { class: "wh-mon-head" }, h("b", {}, "Monitoring"), previewTag(), h("span", { class: "spacer" }),
    btn("Refresh", "sync", () => { m.status = null; whMonitorLoad(ref); }, "tool outline small", { disabled: m.loading || null }),
    learnMore("warehouse-monitoring-preview"));
  if (m.loading && !s) return h("div", { class: "wh-mon" }, head, h("p", { class: "muted-text wh-note" }, "Looking for workspace monitoring…"));
  if (m.error && !s?.monitored) return h("div", { class: "wh-mon" }, head, h("p", { class: "error-text", role: "alert" }, `Could not read monitoring: ${m.error}`));
  if (!s) return h("div", { class: "wh-mon" }, head);
  if (!s.monitored) {
    return h("div", { class: "wh-mon" }, head, h("div", { class: "wh-mon-off" },
      h("b", {}, `Workspace monitoring is off for ${ref.workspace_name || "this workspace"}`),
      h("p", {}, "Fabric records every completed query on its warehouses once a workspace admin turns on monitoring: Workspace settings > Monitoring. It adds a read-only Eventhouse to the workspace, billed at Eventhouse rates, and keeps 30 days."),
      h("p", {}, "When it's on, this tab shows it and builds this warehouse's monitoring views. Until then, Query insights (on the History tab) reads the warehouse's own record of queries."),
      s.problems?.length ? h("p", { class: "muted-text small" }, `While looking: ${s.problems.join(" ")}`) : null,
      h("div", { class: "wh-mon-row" }, btn("Open the workspace in Fabric", "open", () => openInFabric(fabricItemLink({ workspace_id: ref.workspace_id }))),
        btn("Check again", "sync", () => { m.status = null; whMonitorLoad(ref); }, "tool outline"))));
  }
  const sum = m.summary?.rows?.[0] || [];
  const col = (name) => sum[(m.summary?.columns || []).findIndex((c) => c.name === name)];
  const queries = Number(col("Queries") || 0);
  const failed = Number(col("Failed") || 0);
  const tile = (label, value, note, warn) => h("div", { class: `wh-mon-tile${warn ? " warn" : ""}` }, h("span", {}, label), h("b", {}, value), h("small", {}, note));
  const windowName = WH_MON_WINDOWS.find(([k]) => k === m.window)?.[1].toLowerCase() || m.window;
  const r = m.result;
  const opAt = (r?.columns || []).findIndex((c) => c.name === "OperationId");
  const table = !r ? h("p", { class: "muted-text wh-note" }, "Reading…")
    : r.note ? h("p", { class: "muted-text wh-note" }, r.note)
      : !r.rows.length ? h("p", { class: "muted-text wh-note" }, m.view === "alert" ? "Nothing to alert on: fewer than 10 queries or under 20% failing in the last 15 minutes." : "Nothing recorded in this window.")
        : h("div", { class: "wh-mon-scroll" }, h("table", { class: "wh-mon-table" },
          h("thead", {}, h("tr", {}, r.columns.map((c) => h("th", {}, c.name)))),
          h("tbody", {}, r.rows.map((row) => h("tr", {}, row.map((v, i) => h("td", { class: typeof v === "number" ? "num" : "" },
            whMonCell(v, r.columns[i]?.type),
            i === opAt && /^[0-9A-Za-z-]{8,64}$/.test(String(v || "")) ? btn("Query insights", null, () => actions.insight("Query insights", whMonInsightSql(v)), "tool outline small") : null)))))),
          r.truncated ? h("p", { class: "muted-text small" }, "The first 500 rows.") : null);
  const qname = whMonQueryset(ref.name);
  const builtId = s.querysets?.[qname];
  const pick = (opts, value, label, change) => h("select", { "aria-label": label, onchange: (e) => change(e.target.value) },
    opts.map(([k, name]) => h("option", { value: k, selected: k === value }, name)));
  const reread = async (both) => {
    m.result = null;
    renderQuery();
    try { await Promise.all([whMonitorRead(ref, m.view), both ? whMonitorRead(ref, "summary") : null]); } catch (err) { m.result = { columns: [], rows: [], note: `Could not read it: ${String(err).replace(/^Error: /, "")}` }; }
    whMonitorRedraw(ref);
  };
  return h("div", { class: "wh-mon" }, head,
    h("p", { class: "wh-mon-on", role: "status" }, `Workspace monitoring is on. Read from `, h("code", {}, s.database.name),
      s.database.last_seen ? `, last query seen ${dateTimeText(s.database.last_seen)}` : "", `. Completed queries on ${ref.name}, ${windowName}.`),
    h("div", { class: "wh-mon-tiles" },
      tile("Queries", queries.toLocaleString(), `completed, ${windowName}`),
      tile("Typical time (P50)", whMonSecs(col("P50DurationMs")), `P95 ${whMonSecs(col("P95DurationMs"))}`),
      tile("Failed", queries ? `${Math.round((failed / queries) * 1000) / 10}%` : "–", `${failed.toLocaleString()} queries`, failed > 0),
      tile("CPU time", whMonSecs(col("CpuTimeMs")), "for comparison, not billing")),
    h("div", { class: "wh-mon-grid" },
      h("section", { class: "wh-mon-card" }, h("div", { class: "wh-mon-row" },
        pick(WH_MON_VIEWS, m.view, "View", (v) => { m.view = v; reread(false); }),
        pick(WH_MON_WINDOWS, m.window, "Window", (v) => { m.window = v; reread(true); })), table),
      h("section", { class: "wh-mon-card side" },
        h("b", {}, "Monitoring views in Fabric"),
        builtId ? h("p", {}, "KQL queryset ", h("code", {}, qname), " in this workspace: a tab per view, each filtered to ", ref.name, ". Anyone in the workspace can open it, pin it to a dashboard or set an alert from it.")
          : h("p", {}, `Not built yet. Building adds a KQL queryset, ${qname}, to this workspace, with a tab per view.`),
        h("ul", { class: "muted-text small" }, WH_MON_VIEWS.map(([, name]) => h("li", {}, name))),
        h("p", { class: "muted-text small" }, "Who runs what lists each person's sign-in name with their queries and CPU time, as Fabric records them."),
        h("div", { class: "wh-mon-row" },
          s.build_allowed ? btn(builtId ? "Update views…" : "Build views…", null, () => whMonitorBuild(ref), builtId ? "tool outline small" : "tool primary small")
            : h("span", { class: "muted-text small" }, "Your organization turned off building monitoring views."),
          builtId ? btn("Open in Fabric", "open", () => openInFabric(fabricItemLink({ type: "KQLQueryset", workspace_id: ref.workspace_id, item_id: builtId })), "tool outline small") : null),
        h("p", { class: "muted-text small" }, s.auto ? "New warehouses in this workspace get theirs without asking (you said yes when you built them)."
          : "Tick “without asking again” when you build, and new warehouses in this workspace get theirs when the app opens them."))));
}
