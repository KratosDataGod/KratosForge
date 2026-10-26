// Data Factory dataflows (DF-7, Preview): the Dataflows tab beside Pipelines. Read-only: it lists a
// workspace's Dataflow Gen2 items, and for the one picked shows its Power Query (M) queries, where
// each loads, the connections it names and its refreshes in Fabric (workspace_dataflows,
// workspace_dataflow_definition, fabric_dataflow_refreshes). Dataflows run in Fabric only: Refresh in
// Fabric starts one through Workloads (dfRunInFabric), behind its confirm and the risk check.

const DF_FLOW_TABS = [["queries", "Queries (M)"], ["destinations", "Destinations"], ["refreshes", "Refresh history"]];
const dfFlowPage = { workspace: null, list: null, selected: null, detail: null, refreshes: null, tab: "queries" };
const DF_WORKSPACE_KEY = "fd.pipelines.workspace";  // the same workspace the open dialog remembers
const DF_REFRESH_PILL = { succeeded: ["pass", "Succeeded"], failed: ["fail", "Failed"], running: ["info", "Running"],
  pending: ["neu", "Not started"], cancelled: ["neu", "Cancelled"] };

function dfDestinationText(d) {
  if (!d) return "Not loaded anywhere";
  const where = [d.kind || "Unknown destination", [d.schema, d.table].filter(Boolean).join(".")].filter(Boolean).join(" · ");
  const how = d.update_method ? ` (${d.update_method.toLowerCase()}${d.new_table ? ", new table" : ""})` : "";
  return `Loads to ${where}${how}`;
}

function dfQueryCard(q) {
  return h("details", { class: "df-query" },
    h("summary", {}, h("b", {}, q.name), " ",
      q.destination ? h("span", { class: "pill-s info" }, dfDestinationText(q.destination))
        : h("span", { class: "pill-s neu" }, q.load_enabled ? "Loads (no destination set)" : "Not loaded")),
    h("pre", { class: "df-m" }, q.m));
}

function dfQueries(detail) {
  const shown = detail.queries.filter((q) => !q.hidden);
  const hidden = detail.queries.filter((q) => q.hidden);
  return h("div", { class: "df-queries" },
    shown.length ? shown.map(dfQueryCard) : h("p", { class: "muted-text" }, "No queries."),
    hidden.length ? h("details", { class: "df-hidden" }, h("summary", {}, `Hidden queries (${hidden.length}): destinations and helpers`),
      hidden.map(dfQueryCard)) : null);
}

function dfDestinations(detail, ws) {
  const loads = detail.queries.filter((q) => q.destination);
  const table = loads.length ? h("div", { class: "card table-wrap" }, h("table", { class: "list df-dest-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Query"), h("th", {}, "Destination"), h("th", {}, "Table"), h("th", {}, "Update"))),
    h("tbody", {}, loads.map((q) => h("tr", {},
      h("td", {}, q.name),
      h("td", {}, q.destination.kind || "Unknown",
        h("div", { class: "muted-text small" }, q.destination.workspace_id === ws.id ? ws.displayName : q.destination.workspace_id ? "Another workspace" : "")),
      h("td", {}, [q.destination.schema, q.destination.table].filter(Boolean).join(".") || "—"),
      h("td", {}, (q.destination.update_method || "—") + (q.destination.new_table ? " · new table" : "")))))))
    : h("p", { class: "muted-text" }, "No query loads anywhere: this dataflow only feeds other queries or items.");
  const conns = detail.connections.length ? h("ul", { class: "df-conns" }, detail.connections.map((c) =>
    h("li", {}, h("b", {}, c.kind || "Connection"), " ", h("span", { class: "muted-text" }, c.path))))
    : h("p", { class: "muted-text" }, "No connections named.");
  return h("div", {}, table, h("h3", {}, "Connections"), conns);
}

function dfRefreshes(rows) {
  if (!rows.length) return h("p", { class: "muted-text" }, "No refreshes in Fabric's history.");
  return h("div", { class: "card table-wrap" }, h("table", { class: "list df-refresh-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Status"), h("th", {}, "Started"), h("th", {}, "Took"), h("th", {}, "How"), h("th", {}, "Message"))),
    h("tbody", {}, rows.map((r) => {
      const [cls, label] = DF_REFRESH_PILL[r.state] || ["neu", r.native_status || "Unknown"];
      return h("tr", {}, h("td", {}, h("span", { class: `pill-s ${cls}` }, label)), h("td", {}, when(r.started)),
        h("td", {}, took(r.seconds) || "—"), h("td", {}, (r.invoke || "").toLowerCase() || "—"), h("td", { class: "muted-text" }, r.message || ""));
    }))));
}

async function dfOpenDataflow(ws, d) {
  dfFlowPage.selected = d;
  dfFlowPage.detail = null;
  dfFlowPage.refreshes = null;
  render();
  const params = { workspace_id: ws.id, item_id: d.item_id, name: d.name };
  const [detail, refreshes] = await Promise.all([
    rpc("workspace_dataflow_definition", params).catch((err) => ({ error: friendlyError(err).text })),
    rpc("fabric_dataflow_refreshes", params).catch((err) => ({ error: friendlyError(err).text }))]);
  if (dfFlowPage.selected !== d) return;
  dfFlowPage.detail = detail;
  dfFlowPage.refreshes = refreshes;
  render();
}

async function dfListDataflows(ws) {
  dfFlowPage.workspace = ws;
  dfFlowPage.list = null;
  dfFlowPage.selected = null;
  try { localStorage.setItem(DF_WORKSPACE_KEY, ws.id); } catch { /* this session only */ }
  render();
  const out = await rpc("workspace_dataflows", { workspace_id: ws.id }).catch((err) => ({ error: friendlyError(err).text, dataflows: [] }));
  if (dfFlowPage.workspace !== ws) return;
  dfFlowPage.list = out;
  render();
}

// Run in Fabric for a copy job or a dataflow: the Workloads action run_item, so it gets the same confirm,
// risk check and record as any other Fabric item (workload_preflight, then workload_act). Workloads lists
// the items of the workspaces this app works in; one outside them is offered a pin and a fresh read.
async function dfRunInFabric(ws, item, verb, done) {
  const w = { id: `fabric:${ws.id}:${item.item_id}` };
  const a = { action: "run_item", label: verb };
  let pre;
  try {
    pre = await rpc("workload_preflight", { id: w.id, action: "run_item", env: null });
  } catch (err) {
    const f = friendlyError(err);
    if (f.cancelled) return;
    if (!/isn't in the inventory/.test(f.text)) { toast(`Couldn't check that action: ${f.text}`, true, { details: f.details }); return; }
    showDialog(`${verb} ${item.name}?`, h("div", {},
      h("p", { class: "text" }, `Runs started here go through Monitor > Workloads, which asks first, runs the risk check and records each run. `,
        `It doesn't list ${item.name} yet: ${ws.displayName} isn't one of the workspaces this app works in, or ${item.name} is newer than its last read.`),
      h("p", { class: "muted-text small" }, `Pin and read again pins ${ws.displayName} on the Workspaces page (kept on this PC, nothing changes in Fabric) and reads its items again in the background.`)),
    [btn("Cancel", null, closeDialog, "tool outline"), btn("Pin and read again", null, async () => {
      closeDialog();
      const ok = await attempt(() => rpc("set_workspace_mark", { workspace_id: ws.id, mark: "pins", on: true }), "Could not pin the workspace");
      if (!ok) return;
      await attempt(() => rpc("workloads_list", { limit: 1, refresh: true }), "Could not read the workspace again");
      toast(`Reading the items in ${ws.displayName}. Try ${verb} again in a minute.`);
    }, "tool primary")]);
    return;
  }
  confirmWorkloadAction(w, a, verb, false, { pre, done });
}

function dfDetail(ws) {
  const d = dfFlowPage.selected;
  if (!d) return h("p", { class: "muted-text" }, "Pick a dataflow to see its queries.");
  const detail = dfFlowPage.detail;
  const tabs = h("div", { class: "subtabs", role: "tablist", "aria-label": "Dataflow views" }, DF_FLOW_TABS.map(([id, label]) =>
    h("button", { class: `tab${dfFlowPage.tab === id ? " active" : ""}`, role: "tab", "aria-selected": dfFlowPage.tab === id ? "true" : "false",
      onclick: () => { dfFlowPage.tab = id; render(); } }, label)));
  let body;
  if (!detail) body = loading(`Reading ${d.name} from Fabric...`);
  else if (dfFlowPage.tab === "refreshes") {
    body = dfFlowPage.refreshes.error ? h("p", { class: "error-text" }, `Could not read refreshes: ${dfFlowPage.refreshes.error}`)
      : dfRefreshes(dfFlowPage.refreshes.refreshes || []);
  } else if (detail.error) body = h("p", { class: "error-text" }, `Could not read ${d.name}: ${detail.error}`);
  else {
    body = h("div", {}, (detail.problems || []).map((p) => h("p", { class: "muted-text" }, p)),
      dfFlowPage.tab === "destinations" ? dfDestinations(detail, ws) : dfQueries(detail));
  }
  return h("div", { class: "df-detail" },
    h("div", { class: "df-detail-head" }, h("h2", {}, d.name), h("span", { class: "pill-s neu" }, "Runs in Fabric"),
      h("span", { class: "spacer" }),
      btn("Refresh in Fabric…", "play", () => dfRunInFabric(ws, d, "Refresh in Fabric", () => dfOpenDataflow(ws, d)), "tool primary"),
      btn("Open in Fabric", "open", () => openInFabric(fabricItemLink({ type: "Dataflow", workspace_id: ws.id, item_id: d.item_id })), "tool outline")),
    d.description ? h("p", { class: "muted-text" }, d.description) : null, tabs, body);
}

async function renderDfDataflows() {
  const body = h("div", { class: "df-flow-body" });
  setView(h("div", { class: "page df-dataflows" }, dfAreaTabs(),
    h("div", { class: "df-conn-head" }, h("h1", {}, "Dataflows ", previewTag())),
    withHowItWorks(body, howItWorks("df-dataflows", {
      reads: "The dataflows in the workspace you pick, the definition of the one you open (its Power Query queries, destinations and connections) and its refresh history. Reading starts no Fabric job.",
      changes: "Nothing until you press Refresh in Fabric, which starts the dataflow's refresh in Fabric after a confirm and the risk check: it uses capacity and writes to every destination the dataflow has. If Workloads doesn't list the workspace yet, Pin and read again pins it on this PC first. To change a query, Open in Fabric shows Fabric's editor inside the app.",
      results: "Shown here only, kept in memory while the page is open. Only the workspace you pick is remembered.",
      anchor: "data-factory-dataflows-preview",
    }))));
  if (!state.user) {
    fill(body, btn("Sign in to list dataflows", "person", async () => { await loadWorkspaces(); render(); }, "tool outline"));
    return;
  }
  const workspaces = await loadWorkspaces();
  if (state.view !== "pipelines" || plPage.area !== "dataflows" || !body.isConnected) return;
  if (!workspaces.length) { fill(body, h("p", { class: "muted-text" }, "No workspaces to list.")); return; }
  if (!dfFlowPage.workspace) {
    let saved = "";
    try { saved = localStorage.getItem(DF_WORKSPACE_KEY) || ""; } catch { /* none */ }
    const ws = workspaces.find((w) => w.id === saved);
    if (ws) { dfListDataflows(ws); return; }
  }
  const ws = dfFlowPage.workspace;
  const pick = h("select", { "aria-label": "Workspace" }, h("option", { value: "" }, "Choose a workspace"),
    workspaces.map((w) => h("option", { value: w.id, selected: ws && w.id === ws.id || null }, w.displayName)));
  pick.addEventListener("change", () => { const w = workspaces.find((x) => x.id === pick.value); if (w) dfListDataflows(w); });
  if (!ws) { fill(body, h("label", { class: "df-ws" }, "Workspace ", pick)); return; }
  const list = dfFlowPage.list;
  const rows = !list ? loading(`Reading the dataflows in ${ws.displayName}...`)
    : list.error ? h("p", { class: "error-text" }, `Could not list dataflows: ${list.error}`)
    : !list.dataflows.length ? h("p", { class: "muted-text" }, `${ws.displayName} has no dataflows.`)
    : h("div", { class: "df-flow-list", role: "list" }, list.dataflows.map((d) => h("button", {
      class: `df-flow-row${dfFlowPage.selected?.item_id === d.item_id ? " active" : ""}`, role: "listitem",
      onclick: () => dfOpenDataflow(ws, d) }, h("b", {}, d.name), d.description ? h("span", { class: "muted-text small" }, d.description) : null)));
  fill(body, h("label", { class: "df-ws" }, "Workspace ", pick),
    h("div", { class: "df-split" }, h("div", { class: "card df-flow-side" }, rows), h("div", { class: "card pad df-flow-main" }, dfDetail(ws))));
}
