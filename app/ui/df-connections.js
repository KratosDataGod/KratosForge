// Data Factory connections (DF-5, Preview): the Connections tab beside Pipelines. Read-only: it lists
// the connections the signed-in person can use and what uses each one in each environment's workspace,
// from the pipeline and copy job definitions there (fabric_connections, a background scan). A
// reference to a connection nobody here can use is shown first: that breaks a run or a deploy.
// KratosForge never creates or changes a connection; Manage connections opens Fabric's own page.

const DF_MANAGE_CONNECTIONS = "https://app.fabric.microsoft.com/groups/me/gateways?experience=fabric-developer";
const DF_AREAS = [["pipelines", "Pipelines"], ["connections", "Connections"]];
const dfConnPage = { refresh: false, timer: null };

// The tabs at the top of the Pipelines page. Copy jobs and Dataflows come with their stories (DF-6, DF-7).
function dfAreaTabs() {
  const area = plPage.area || "pipelines";
  return h("div", { class: "subtabs df-areas", role: "tablist", "aria-label": "Data Factory" }, DF_AREAS.map(([id, label]) =>
    h("button", { class: `tab${area === id ? " active" : ""}`, role: "tab", "aria-selected": area === id ? "true" : "false",
      onclick: () => { plPage.area = id; render(); } }, label)));
}

const DF_CONNECTIVITY = { ShareableCloud: "Cloud", PersonalCloud: "Cloud (personal)", OnPremisesGateway: "On-premises gateway",
  OnPremisesGatewayPersonal: "Personal gateway", VirtualNetworkGateway: "Virtual network gateway", Automatic: "Automatic" };

function dfEnvLabel(ws) {
  return ws.env ? ws.env[0].toUpperCase() + ws.env.slice(1) : ws.name || ws.workspace_name;
}

function dfUsedBy(used) {
  if (!used.length) return h("span", { class: "muted-text" }, "Nothing in these workspaces");
  return h("div", { class: "df-used" }, used.map((u) => h("div", {},
    h("button", { class: "name-link", title: "Open in Fabric",
      onclick: () => openInFabric(fabricItemLink({ type: u.item_type === "pipeline" ? "DataPipeline" : "CopyJob", workspace_id: u.workspace_id, item_id: u.item_id })) },
    u.item_name), h("span", { class: "muted-text small" }, ` · ${u.item_type}`))));
}

// One cell per environment: is it used there, and can it be used (a missing connection can't).
function dfEnvCell(row, ws, missing) {
  const n = row.used_by.filter((u) => u.workspace_id === ws.id).length;
  if (!n) return h("td", { class: "muted-text" }, "—");
  return h("td", {}, missing ? h("span", { class: "pill-s fail" }, "Missing") : h("span", { class: "pill-s pass" }, `Used by ${n}`));
}

async function renderDfConnections() {
  clearTimeout(dfConnPage.timer);
  const body = h("div", { class: "df-conn-body" }, loading("Reading connections and what uses them..."));
  setView(h("div", { class: "page df-connections" }, dfAreaTabs(),
    h("div", { class: "df-conn-head" }, h("h1", {}, "Connections ", previewTag()),
      h("div", { class: "pl-actions" },
        btn("Refresh", "sync", () => { dfConnPage.refresh = true; render(); }, "tool outline"),
        btn("Manage connections in Fabric", "open", () => openInFabric(DF_MANAGE_CONNECTIONS), "tool primary"))),
    withHowItWorks(body, howItWorks("df-connections", {
      reads: "The connections you can use (names, types and gateways, never credentials), and the pipeline and copy job definitions in each environment's workspace, to see which connections they name. No Fabric job starts and no capacity is used.",
      changes: "Nothing. KratosForge never stores, creates or changes a connection or its credentials. Manage connections in Fabric opens Fabric's own page to add or fix one.",
      results: "Shown here only, kept in memory until Refresh.",
      anchor: "data-factory-connections-preview",
    }))));
  const refresh = dfConnPage.refresh;
  dfConnPage.refresh = false;
  const out = await rpc("fabric_connections", refresh ? { refresh: true } : {})
    .catch((err) => ({ pending: false, error: friendlyError(err).text, connections: [], missing: [] }));
  if (state.view !== "pipelines" || plPage.area !== "connections" || !body.isConnected) return;
  if (out.pending) {
    dfConnPage.timer = setTimeout(() => { if (state.view === "pipelines" && plPage.area === "connections") render(); }, 2500);
    return;
  }
  if (out.error) { fill(body, h("p", { class: "error-text" }, `Could not read connections: ${out.error}`)); return; }
  const envs = out.workspaces || [];
  const missing = out.missing || [];
  const notes = [`As of ${new Date(out.scanned).toLocaleTimeString()} · ${out.items} pipelines and copy jobs read in ${envs.map((w) => `${w.name}${w.env ? ` (${dfEnvLabel(w)})` : ""}`).join(", ") || "no workspaces"}`];
  if (out.missed) notes.push(`${out.missed} couldn't be read`);
  if (out.skipped) notes.push(`${out.skipped} more not read (limit reached)`);
  const alert = missing.length ? h("div", { class: "df-alert", role: "alert" },
    h("b", {}, missing.length === 1 ? "1 connection is missing." : `${missing.length} connections are missing.`), " ",
    missing.map((m) => `${[...new Set(m.used_by.map((u) => u.item_name))].join(", ")} in ${[...new Set(m.used_by.map((u) => dfEnvLabel(u)))].join(", ")} use${m.used_by.length === 1 ? "s" : ""} a connection you can't use (deleted, or not shared with you), so ${m.used_by.length === 1 ? "it fails when you run it" : "they fail when you run them"}.`).join(" ")) : null;
  const rows = [...missing.map((m) => ({ ...m, missing: true })), ...(out.connections || [])];
  const table = rows.length ? h("div", { class: "card table-wrap" }, h("table", { class: "list df-conn-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Connection"), h("th", {}, "Type"), h("th", {}, "Gateway"), h("th", {}, "Used by"),
      envs.map((w) => h("th", {}, dfEnvLabel(w))))),
    h("tbody", {}, rows.map((r) => h("tr", { class: r.missing ? "df-missing" : "" },
      h("td", {}, r.missing ? h("b", {}, `Unknown connection ${r.id.slice(0, 8)}`) : h("b", {}, r.name),
        h("div", { class: "muted-text small" }, r.missing ? "Not in the connections you can use" : r.path)),
      h("td", {}, r.type || "—"),
      h("td", {}, r.missing ? "—" : DF_CONNECTIVITY[r.connectivity] || r.connectivity || "—"),
      h("td", {}, dfUsedBy(r.used_by)),
      envs.map((w) => dfEnvCell(r, w, r.missing))))))) : h("p", { class: "muted-text" }, "No connections you can use, and nothing here names one.");
  fill(body, alert, h("p", { class: "muted-text small" }, notes.join(" · ")), table);
}
