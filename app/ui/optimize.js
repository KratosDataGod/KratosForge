// Optimize: what the organization could improve, what it fixed and what it could save, shared through
// one optimization lakehouse (sidecar optimize/ and optimize_service.py). Setting it up never
// blocks the page: until a lakehouse is connected, Cost and savings still shows this PC's figures.

const optimizePage = { tab: "overview", closed: false };

const OPTIMIZE_TABS = [["overview", "Overview"], ["opportunities", "Opportunities"], ["cost", "Cost and savings"]];

// A recommendation's state as people say it, and the moves offered from it (shared.TRANSITIONS).
const REC_STATE = { open: "Open", accepted: "Accepted", applied: "Applied", dismissed: "Dismissed", verified: "Verified", superseded: "Superseded" };
const REC_MOVES = {
  open: [["accepted", "Accept"], ["applied", "Mark applied"], ["dismissed", "Dismiss..."]],
  accepted: [["applied", "Mark applied"], ["dismissed", "Dismiss..."], ["open", "Reopen"]],
  applied: [["verified", "Mark verified"], ["open", "Reopen"]],
  dismissed: [["open", "Reopen"]],
  verified: [["open", "Reopen"]],
  superseded: [],
};

async function renderOptimize() {
  if (!OPTIMIZE_TABS.some(([id]) => id === optimizePage.tab)) optimizePage.tab = "overview";
  const band = h("div", { class: "optimize-band muted-text" }, "Reading Optimize's settings...");
  const body = h("div", { class: "optimize-body" });
  setView(h("div", { class: "page optimize" },
    h("div", { class: "page-head" }, h("div", {},
      h("h1", {}, "Optimize ", previewTag()),
      h("p", { class: "sub" }, "What your Fabric estate could do better, what was fixed and what it could save, shared with everyone on your optimization lakehouse. ",
        learnMore("optimize-preview")))),
    band,
    h("div", { class: "subtabs", role: "tablist", "aria-label": "Optimize views" }, OPTIMIZE_TABS.map(([id, label]) =>
      h("button", { class: `tab${optimizePage.tab === id ? " active" : ""}`, role: "tab", "aria-selected": optimizePage.tab === id ? "true" : "false",
        onclick: () => { optimizePage.tab = id; render(); } }, label))),
    body));
  // Cost and savings doesn't need the shared lakehouse: it reads this PC's runs.
  if (optimizePage.tab === "cost") body.append(costPanel());
  const status = await rpc("optimize_status").catch(() => null);
  if (state.view !== "optimize" || !band.isConnected) return;
  band.replaceWith(optimizeBand(status));
  if (optimizePage.tab === "cost") return;
  if (!status?.active) {
    fill(body, h("p", { class: "muted-text" }, status && !status.allowed
      ? "Your organization has turned off shared optimization lakehouses."
      : "Connect an optimization lakehouse to see what every Fabric Desktop by KratosBI installation found, and act on it together. Cost and savings works without one."));
    return;
  }
  fill(body, h("p", { class: "muted-text" }, "Reading shared results..."));
  const [findings, recs] = await Promise.all([
    rpc("optimize_findings"),
    rpc("optimize_recommendations", { include_closed: optimizePage.tab === "opportunities" && optimizePage.closed }),
  ]).catch((err) => { if (body.isConnected) fill(body, h("p", { class: "muted-text" }, friendlyError(err).text)); return [null, null]; });
  if (!findings || state.view !== "optimize" || !body.isConnected) return;
  const writable = status.active.access === "write";
  if (optimizePage.tab === "overview") fill(body, ...optimizeOverview(findings, recs, writable));
  else fill(body, ...optimizeOpportunities(findings, recs, writable));
}

// The line under the title: which lakehouse this PC shares results through, or how to set one up.
function optimizeBand(status) {
  if (!status) return h("div", { class: "optimize-band muted-text" }, "Optimize's settings can't be read until the local engine is running.");
  if (!status.allowed) return h("div", { class: "optimize-band muted-text" }, "Shared optimization lakehouses are turned off by your organization. Cost and savings still shows this PC's figures.");
  const c = status.active;
  if (!c) {
    return h("section", { class: "card pad optimize-setup" },
      h("div", { class: "grow" }, h("b", {}, "Share results with your team"),
        h("div", { class: "muted-text small" }, "One lakehouse holds what every installation finds, fixes and saves. Your organization usually needs only one. Nothing here waits for it.")),
      btn("Connect to a lakehouse", null, () => optimizeConnectFlow(), "tool outline"),
      btn("Create a lakehouse", "add", () => optimizeCreateFlow(), "tool primary"));
  }
  const access = { write: "Read and write", read: "Read only", none: "No access" }[c.access] || c.access;
  return h("div", { class: "optimize-band row wrap" },
    h("span", {}, h("span", { class: "muted-text" }, "Lakehouse "), h("b", {}, c.lakehouse_name || c.lakehouse_id),
      c.workspace_name ? h("span", { class: "muted-text" }, ` in ${c.workspace_name}`) : null),
    h("span", {}, h("span", { class: "muted-text" }, "Access "), access),
    h("span", { class: "spacer" }),
    btn("Lakehouses", "settings", () => { state.settingsTab = "general"; go("settings"); }, "tool link small"));
}

// Findings by fingerprint, so a recommendation can show what it is about.
function optimizeFindingIndex(findings) {
  return Object.fromEntries((findings || []).map((f) => [f.fingerprint, f]));
}

function optimizeOverview(findings, recs, writable) {
  const open = recs.filter((r) => r.state === "open" || r.state === "accepted");
  const bySev = (s) => findings.filter((f) => f.severity === s).length;
  const tile = (label, value, note) => h("div", { class: "cmp-stat" }, h("span", { class: "v" }, String(value)), h("span", { class: "k" }, label),
    note ? h("span", { class: "k" }, note) : null);
  return [
    h("div", { class: "cmp-stats" },
      tile("Problems found", findings.length, `${bySev("high")} high, ${bySev("medium")} medium`),
      tile("Open improvements", open.length, `${open.filter((r) => r.state === "accepted").length} accepted`),
      tile("Seen again and again", findings.filter((f) => f.occurrences > 1).length, "in more than one analysis")),
    h("section", { class: "card pad" },
      h("div", { class: "row" }, h("h2", { class: "grow" }, "Recommended improvements"),
        open.length > 5 ? btn(`All ${open.length}`, null, () => { optimizePage.tab = "opportunities"; render(); }, "tool link small") : null),
      open.length ? optimizeRecTable(open.slice(0, 5), optimizeFindingIndex(findings), writable)
        : h("p", { class: "muted-text" }, findings.length ? "Nothing open: every recommendation has been acted on."
          : "Nothing found yet. Results appear here once an installation records an analysis.")),
    h("p", { class: "muted-text small" }, "Cost figures are under Cost and savings, each badged Measured, Estimated, Scenario or Unknown with how it was worked out."),
  ];
}

function optimizeOpportunities(findings, recs, writable) {
  const index = optimizeFindingIndex(findings);
  return [
    h("div", { class: "row" },
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: optimizePage.closed ? true : null,
        onchange: (e) => { optimizePage.closed = e.target.checked; render(); } }), "Show dismissed and verified"),
      h("span", { class: "spacer" }),
      btn("How each fix works", "help", () => openXrayDoc(null), "tool link small")),
    recs.length ? optimizeRecTable(recs, index, writable)
      : h("p", { class: "muted-text" }, "No recommendations yet."),
  ];
}

function optimizeRecTable(recs, index, writable) {
  return h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["Improvement", "Impact", "Effort", "State", ""].map((c) => h("th", {}, c)))),
    h("tbody", {}, recs.map((r) => {
      const f = index[r.fingerprint] || {};
      return h("tr", {},
        h("td", {}, h("b", {}, f.title || r.kind), h("div", { class: "muted-text small" },
          [f.target, r.reason].filter(Boolean).join(" · ") || r.artifact_key)),
        h("td", {}, r.impact || ""), h("td", {}, r.effort || ""),
        h("td", {}, REC_STATE[r.state] || r.state, r.state_reason ? h("div", { class: "muted-text small" }, r.state_reason) : null),
        h("td", { class: "n" }, writable ? (REC_MOVES[r.state] || []).map(([to, label]) =>
          btn(label, null, () => optimizeMove(r, to), "tool outline small")) : null));
    })));
}

// Moving a recommendation on is shared with everyone on the lakehouse; dismissing asks why.
async function optimizeMove(rec, to) {
  let reason = "";
  if (to === "dismissed") {
    reason = await new Promise((resolve) => {
      const input = h("input", { maxlength: "500", placeholder: "For example: the table is retired next month" });
      showDialog("Dismiss this improvement?", h("div", {},
        h("p", { class: "text" }, "Everyone using this lakehouse sees it as dismissed, with your reason, and it stays dismissed when it's found again. Anyone can reopen it."),
        h("label", { class: "field" }, "Why", input)), [
        btn("Cancel", null, () => { closeDialog(); resolve(null); }, "tool outline"),
        btn("Dismiss", null, () => { closeDialog(); resolve(input.value.trim()); }, "tool primary")]);
    });
    if (reason === null) return;
  }
  const done = await attempt(() => rpc("optimize_set_recommendation", { recommendation_id: rec.recommendation_id, state: to, reason }),
    "Could not update the recommendation");
  if (done) { toast(`Marked ${(REC_STATE[to] || to).toLowerCase()}`); render(); }
}

// ---------- create, connect and manage lakehouses ----------

async function optimizeCreateFlow() {
  const workspaces = await loadWorkspaces();
  if (!workspaces?.length) { toast("Sign in and open a workspace first", true); return; }
  const ws = await pickWorkspace(workspaces, { title: "Create an optimization lakehouse",
    note: "Pick a workspace everyone who shares results can reach, such as your platform workspace. Readers need Read all data on the lakehouse; writers need Contributor on the workspace." });
  if (!ws) return;
  const status = await rpc("optimize_status").catch(() => ({ default_name: "FabricDesktop_Optimization" }));
  const name = await new Promise((resolve) => {
    const input = h("input", { value: status.default_name, spellcheck: "false" });
    showDialog("Create an optimization lakehouse", h("div", {},
      h("label", { class: "field" }, "Name", input),
      h("p", { class: "text" }, `This creates a lakehouse in ${ws.displayName}, adds Optimize's tables (all named fdopt_) and connects this PC. It uses a little OneLake storage and capacity as results are written.`)), [
      btn("Cancel", null, () => { closeDialog(); resolve(null); }, "tool outline"),
      btn("Create", null, () => { closeDialog(); resolve(input.value.trim()); }, "tool primary")]);
  });
  if (!name) return;
  toast(`Creating ${name}...`);
  const res = await attempt(() => rpc("optimize_create", { workspace_id: ws.id, name, workspace_name: ws.displayName }), "Could not create the lakehouse");
  if (res) { toast(`${name} is ready: ${res.access.message}`); render(); }
}

async function optimizeConnectFlow() {
  const workspaces = await loadWorkspaces();
  if (!workspaces?.length) { toast("Sign in and open a workspace first", true); return; }
  const ws = await pickWorkspace(workspaces, { title: "Connect to an optimization lakehouse" });
  if (!ws) return;
  const found = await attempt(() => rpc("optimize_discover", { workspace_id: ws.id }), "Could not list the lakehouses");
  if (!found) return;
  const ours = found.filter((l) => l.optimize);
  const others = found.filter((l) => !l.optimize);
  const pick = await new Promise((resolve) => {
    const row = (l) => h("tr", {},
      h("td", {}, h("b", {}, l.lakehouse_name), l.error ? h("div", { class: "muted-text small" }, l.error)
        : !l.optimize && l.other_tables ? h("div", { class: "muted-text small" }, `${l.other_tables} other tables, left untouched`) : null),
      h("td", { class: "n" }, l.error ? null : btn("Connect", null, () => { closeDialog(); resolve(l); }, l.optimize ? "tool primary small" : "tool outline small")));
    showDialog(`Lakehouses in ${ws.displayName}`, h("div", { class: "scroll" },
      ours.length ? h("table", { class: "list" }, h("tbody", {}, ours.map(row)))
        : h("p", { class: "text" }, "No optimization lakehouse in this workspace yet."),
      others.length ? h("details", {}, h("summary", {}, `Other lakehouses (${others.length})`),
        h("p", { class: "muted-text small" }, "Optimize can add its own tables beside the ones already there. It asks first and never changes theirs."),
        h("table", { class: "list" }, h("tbody", {}, others.map(row)))) : null),
    [btn("Cancel", null, () => { closeDialog(); resolve(null); }, "tool outline")]);
  });
  if (!pick) return;
  const args = { workspace_id: ws.id, lakehouse_id: pick.lakehouse_id, workspace_name: ws.displayName, lakehouse_name: pick.lakehouse_name };
  let res = await attempt(() => rpc("optimize_connect", args), "Could not connect");
  if (res?.needs_confirmation) {
    const i = res.inspection;
    const ok = await confirmDialog(`Set up Optimize in ${pick.lakehouse_name}?`,
      `${i.message} This adds ${i.adds.length} table${i.adds.length === 1 ? "" : "s"} named fdopt_ and changes nothing else.`, "Set up and connect");
    if (!ok) return;
    res = await attempt(() => rpc("optimize_connect", { ...args, initialize: true }), "Could not set it up");
  }
  if (res?.connected) { toast(res.access.message); render(); }
}

// Settings > Optimize lakehouses. With one lakehouse it is one line.
async function optimizeSettingsSection() {
  const s = await rpc("optimize_status").catch(() => null);
  if (!s) return null;
  const section = h("section", { class: "card pad" }, h("h2", {}, "Optimize lakehouses ", previewTag()));
  if (!s.allowed) {
    section.append(h("p", { class: "muted-text small" }, "Your organization has turned off shared optimization lakehouses (allow_optimize_lakehouse in its policy file)."));
    return section;
  }
  const access = { write: "Read and write", read: "Read only", none: "No access" };
  const line = (c) => h("div", { class: "setting" },
    h("div", { class: "setting-text" }, h("b", {}, c.lakehouse_name || c.lakehouse_id, s.active?.lakehouse_id === c.lakehouse_id && s.connections.length > 1 ? " (active)" : ""),
      h("div", { class: "muted-text small" }, [c.workspace_name, access[c.access] || c.access, c.schema_version ? `schema ${c.schema_version}` : null].filter(Boolean).join(" · "))),
    h("div", { class: "setting-control row" },
      s.active?.lakehouse_id === c.lakehouse_id ? null : btn("Switch to", null, async () => {
        if (await attempt(() => rpc("optimize_switch", { lakehouse_id: c.lakehouse_id }), "Could not switch")) render();
      }, "tool outline small"),
      btn("Check", null, async () => {
        const res = await attempt(() => rpc("optimize_connect", { workspace_id: c.workspace_id, lakehouse_id: c.lakehouse_id,
          workspace_name: c.workspace_name, lakehouse_name: c.lakehouse_name }), "Could not check the lakehouse");
        if (res?.needs_confirmation) toast(res.inspection.message);
        else if (res) { toast(res.access.message); render(); }
      }, "tool outline small"),
      btn("Disconnect...", null, async () => {
        if (!(await confirmDialog(`Disconnect ${c.lakehouse_name || "this lakehouse"}?`,
          "This PC stops writing to it. Its data stays, and everyone else connected keeps using it. You can connect again at any time.", "Disconnect"))) return;
        if (await attempt(() => rpc("optimize_disconnect", { lakehouse_id: c.lakehouse_id }), "Could not disconnect")) render();
      }, "tool outline small")));
  const label = h("input", { value: s.label || "", maxlength: "80", placeholder: "For example: Build server" });
  section.append(
    h("p", { class: "muted-text small" }, "This PC writes what it finds, fixes and saves to the active lakehouse. ", learnMore("optimize-preview")),
    ...(s.connections.length ? s.connections.map(line) : [h("p", { class: "muted-text small" }, "Not connected to an optimization lakehouse.")]),
    h("div", { class: "row" },
      btn("Connect to a lakehouse", null, () => optimizeConnectFlow(), "tool outline"),
      btn("Create a lakehouse", "add", () => optimizeCreateFlow(), "tool outline")),
    h("details", {}, h("summary", {}, "Advanced"),
      settingRow("Installation ID", "A random id made on this PC, not tied to you or your account. Everyone who can read the lakehouse sees it on the rows this PC writes.",
        h("div", { class: "row" }, h("span", { class: "mono" }, s.installation_id), btn("New installation ID", null, async () => {
          if (!(await confirmDialog("Use a new installation ID?", "Rows already written keep the old ID. New rows use the new one.", "Use a new ID"))) return;
          if (await attempt(() => rpc("optimize_new_installation_id"), "Could not reset the ID")) render();
        }, "tool outline small"))),
      settingRow("Name this installation", "Optional. Everyone with access to the lakehouse sees it.",
        h("div", { class: "row" }, label, btn("Save", null, async () => {
          if (await attempt(() => rpc("optimize_set_label", { label: label.value.trim() }), "Could not save the name")) toast("Saved");
        }, "tool outline small")))));
  return section;
}

registerFeature({
  id: "optimize", preview: true,
  rail: { label: "Optimize", icon: "gauge", order: 60 },
  views: { optimize: renderOptimize },
  commands: [
    { label: "Optimize", icon: "gauge", keywords: "improvements recommendations findings savings", preview: true, run: () => { optimizePage.tab = "overview"; go("optimize"); } },
    { label: "Cost and savings", icon: "gauge", keywords: "cost capacity cu spend saved avoided monitor", preview: true, run: () => { optimizePage.tab = "cost"; go("optimize"); } },
    { label: "Create an optimization lakehouse", icon: "add", keywords: "optimize shared lakehouse set up", preview: true, run: () => optimizeCreateFlow() },
    { label: "Connect to an optimization lakehouse", icon: "onelake", keywords: "optimize shared lakehouse set up", preview: true, run: () => optimizeConnectFlow() },
  ],
});
