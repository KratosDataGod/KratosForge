// Data Factory copy jobs (DF-6, Preview): the Copy jobs tab beside Pipelines. Read-only: it lists a
// workspace's copy jobs, and for the one picked shows where it copies from and to, each table, whether
// a run on this PC could take it, and its runs in Fabric (workspace_copy_jobs,
// workspace_copy_job_definition, fabric_copy_job_runs). Run in Fabric starts one through Workloads
// (dfRunInFabric), behind its confirm and the risk check. Running one on this PC comes with the
// pipeline copy engine (DF-3).

const DF_COPY_TABS = [["tables", "Tables"], ["runs", "Run history"]];
const dfCopyPage = { workspace: null, list: null, selected: null, detail: null, runs: null, tab: "tables" };

function dfEndpointText(end, ws) {
  if (!end || !(end.type || end.connection_type)) return "Not set";
  const kind = end.connection_type || end.type;
  if (end.item_id) {
    const where = end.workspace_id && end.workspace_id !== ws.id ? "another workspace" : ws.displayName;
    return `${kind} in ${where}${end.root_folder ? ` (${end.root_folder})` : ""}`;
  }
  return [kind, end.database].filter(Boolean).join(" · ");
}

function dfSideText(side) {
  return [side.schema, side.table].filter(Boolean).join(".") || side.folder || "—";
}

function dfCopyTables(detail) {
  if (!detail.tables.length) return h("p", { class: "muted-text" }, "No tables in this copy job.");
  return h("div", { class: "card table-wrap" }, h("table", { class: "list df-copy-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Source"), h("th", {}, "Destination"), h("th", {}, "Write"), h("th", {}, "Incremental"))),
    h("tbody", {}, detail.tables.map((t) => h("tr", {},
      h("td", {}, dfSideText(t.source)),
      h("td", {}, dfSideText(t.destination), t.table_option ? h("div", { class: "muted-text small" }, t.table_option === "autoCreate" ? "Creates the table" : t.table_option) : null),
      h("td", {}, t.write_behavior || "—", t.keys.length ? h("div", { class: "muted-text small" }, `Keys: ${t.keys.join(", ")}`) : null),
      h("td", {}, t.read_method || (t.incremental_columns.length ? "Incremental" : "Full"),
        t.incremental_columns.length ? h("div", { class: "muted-text small" }, `On ${t.incremental_columns.join(", ")}`) : null))))));
}

function dfCopyRuns(rows) {
  if (!rows.length) return h("p", { class: "muted-text" }, "No runs in Fabric's history.");
  return h("div", { class: "card table-wrap" }, h("table", { class: "list df-copy-runs" },
    h("thead", {}, h("tr", {}, h("th", {}, "Status"), h("th", {}, "Started"), h("th", {}, "Took"), h("th", {}, "How"), h("th", {}, "Message"))),
    h("tbody", {}, rows.map((r) => {
      const [cls, label] = DF_REFRESH_PILL[r.state] || ["neu", r.native_status || "Unknown"];
      return h("tr", {}, h("td", {}, h("span", { class: `pill-s ${cls}` }, label)), h("td", {}, when(r.started)),
        h("td", {}, took(r.seconds) || "—"), h("td", {}, (r.invoke || "").toLowerCase() || "—"), h("td", { class: "muted-text" }, r.message || ""));
    }))));
}

function dfCopySummary(detail, ws) {
  const mode = detail.job_mode === "CDC" ? "Change data capture (CDC)" : detail.job_mode || "—";
  const conns = [detail.source, detail.destination].map((e) => e.connection_id).filter(Boolean);
  const check = detail.local_check || { possible: false, reasons: [] };
  return h("div", { class: "df-copy-summary" },
    h("dl", { class: "df-kv" },
      h("dt", {}, "Source"), h("dd", {}, dfEndpointText(detail.source, ws)),
      h("dt", {}, "Destination"), h("dd", {}, dfEndpointText(detail.destination, ws)),
      h("dt", {}, "Mode"), h("dd", {}, mode),
      h("dt", {}, "Timeout"), h("dd", {}, detail.timeout || "—"),
      h("dt", {}, "Connections"), h("dd", {}, conns.length ? `${conns.length} (see the Connections tab)` : "None (OneLake only)")),
    h("p", { class: "muted-text small df-local-check" }, check.possible
      ? "Both ends are lakehouses, so a run on this PC will be able to take it once local copy jobs arrive. It will write to a sandbox, never the real table. Run in Fabric writes to the real destination."
      : check.reasons.join(" ")));
}

async function dfOpenCopyJob(ws, c) {
  dfCopyPage.selected = c;
  dfCopyPage.detail = null;
  dfCopyPage.runs = null;
  render();
  const params = { workspace_id: ws.id, item_id: c.item_id, name: c.name };
  const [detail, runs] = await Promise.all([
    rpc("workspace_copy_job_definition", params).catch((err) => ({ error: friendlyError(err).text })),
    rpc("fabric_copy_job_runs", params).catch((err) => ({ error: friendlyError(err).text }))]);
  if (dfCopyPage.selected !== c) return;
  dfCopyPage.detail = detail;
  dfCopyPage.runs = runs;
  render();
}

async function dfListCopyJobs(ws) {
  dfCopyPage.workspace = ws;
  dfCopyPage.list = null;
  dfCopyPage.selected = null;
  try { localStorage.setItem(DF_WORKSPACE_KEY, ws.id); } catch { /* this session only */ }
  render();
  const out = await rpc("workspace_copy_jobs", { workspace_id: ws.id }).catch((err) => ({ error: friendlyError(err).text, copy_jobs: [] }));
  if (dfCopyPage.workspace !== ws) return;
  dfCopyPage.list = out;
  render();
}

function dfCopyDetail(ws) {
  const c = dfCopyPage.selected;
  if (!c) return h("p", { class: "muted-text" }, "Pick a copy job to see what it copies.");
  const detail = dfCopyPage.detail;
  const tabs = h("div", { class: "subtabs", role: "tablist", "aria-label": "Copy job views" }, DF_COPY_TABS.map(([id, label]) =>
    h("button", { class: `tab${dfCopyPage.tab === id ? " active" : ""}`, role: "tab", "aria-selected": dfCopyPage.tab === id ? "true" : "false",
      onclick: () => { dfCopyPage.tab = id; render(); } }, label)));
  let body;
  if (!detail) body = loading(`Reading ${c.name} from Fabric...`);
  else if (detail.error) body = h("p", { class: "error-text" }, `Could not read ${c.name}: ${detail.error}`);
  else {
    const view = dfCopyPage.tab === "runs"
      ? (dfCopyPage.runs.error ? h("p", { class: "error-text" }, `Could not read runs: ${dfCopyPage.runs.error}`) : dfCopyRuns(dfCopyPage.runs.runs || []))
      : dfCopyTables(detail);
    body = h("div", {}, (detail.problems || []).map((p) => h("p", { class: "muted-text" }, p)), dfCopySummary(detail, ws), tabs, view);
  }
  return h("div", { class: "df-detail" },
    h("div", { class: "df-detail-head" }, h("h2", {}, c.name),
      detail && detail.local_check ? h("span", { class: `pill-s ${detail.local_check.possible ? "info" : "neu"}` },
        detail.local_check.possible ? "OneLake only" : "Runs in Fabric") : null,
      h("span", { class: "spacer" }),
      btn("Run in Fabric…", "play", () => dfRunInFabric(ws, c, "Run in Fabric", () => dfOpenCopyJob(ws, c)), "tool outline"),
      btn("Open in Fabric", "open", () => openInFabric(fabricItemLink({ type: "CopyJob", workspace_id: ws.id, item_id: c.item_id })), "tool outline")),
    c.description ? h("p", { class: "muted-text" }, c.description) : null, body);
}

async function renderDfCopyJobs() {
  const body = h("div", { class: "df-copy-body" });
  setView(h("div", { class: "page df-copy-jobs" }, dfAreaTabs(),
    h("div", { class: "df-conn-head" }, h("h1", {}, "Copy jobs ", previewTag())),
    withHowItWorks(body, howItWorks("df-copy-jobs", {
      reads: "The copy jobs in the workspace you pick, the definition of the one you open (its source, destination and tables) and its run history. Reading starts no Fabric job.",
      changes: "Nothing until you press Run in Fabric, which starts the copy job in Fabric after a confirm and the risk check: it uses capacity and copies each of its tables into the destination. Running one on this PC comes later. If Workloads doesn't list the workspace yet, Pin and read again pins it on this PC first.",
      results: "Shown here only, kept in memory while the page is open. Only the workspace you pick is remembered.",
      anchor: "data-factory-copy-jobs-preview",
    }))));
  if (!state.user) {
    fill(body, btn("Sign in to list copy jobs", "person", async () => { await loadWorkspaces(); render(); }, "tool outline"));
    return;
  }
  const workspaces = await loadWorkspaces();
  if (state.view !== "pipelines" || plPage.area !== "copyjobs" || !body.isConnected) return;
  if (!workspaces.length) { fill(body, h("p", { class: "muted-text" }, "No workspaces to list.")); return; }
  if (!dfCopyPage.workspace) {
    let saved = "";
    try { saved = localStorage.getItem(DF_WORKSPACE_KEY) || ""; } catch { /* none */ }
    const ws = workspaces.find((w) => w.id === saved);
    if (ws) { dfListCopyJobs(ws); return; }
  }
  const ws = dfCopyPage.workspace;
  const pick = h("select", { "aria-label": "Workspace" }, h("option", { value: "" }, "Choose a workspace"),
    workspaces.map((w) => h("option", { value: w.id, selected: ws && w.id === ws.id || null }, w.displayName)));
  pick.addEventListener("change", () => { const w = workspaces.find((x) => x.id === pick.value); if (w) dfListCopyJobs(w); });
  if (!ws) { fill(body, h("label", { class: "df-ws" }, "Workspace ", pick)); return; }
  const list = dfCopyPage.list;
  const rows = !list ? loading(`Reading the copy jobs in ${ws.displayName}...`)
    : list.error ? h("p", { class: "error-text" }, `Could not list copy jobs: ${list.error}`)
    : !list.copy_jobs.length ? h("p", { class: "muted-text" }, `${ws.displayName} has no copy jobs.`)
    : h("div", { class: "df-flow-list", role: "list" }, list.copy_jobs.map((c) => h("button", {
      class: `df-flow-row${dfCopyPage.selected?.item_id === c.item_id ? " active" : ""}`, role: "listitem",
      onclick: () => dfOpenCopyJob(ws, c) }, h("b", {}, c.name), c.description ? h("span", { class: "muted-text small" }, c.description) : null)));
  fill(body, h("label", { class: "df-ws" }, "Workspace ", pick),
    h("div", { class: "df-split" }, h("div", { class: "card df-flow-side" }, rows), h("div", { class: "card pad df-flow-main" }, dfCopyDetail(ws))));
}
