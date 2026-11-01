// The notebook's Engine picker (Preview, engines_service.py). Engine replaces Runs on in the toolbar,
// next to Run all. Python (this PC) is the notebook's own kernel and Fabric job is Run in Fabric, both
// as before (nb.compute). Any other engine runs cells through the engine's own session in the sidecar:
// DuckDB, Polars SQL and SQLite on this PC, or, after a confirm, Fabric Spark, a Databricks SQL
// warehouse or Databricks Connect. A cell the engine can't run is flagged before anything runs, with
// "Run this cell on Python" for that cell only. Nothing ever moves to a remote engine on its own.
// With Preview features hidden, the toolbar keeps Runs on (app.js computeMenu).

const ENGINE_KERNEL = "local-python";
let engineCatalogCache = null;
const engineStates = new Map();  // notebook name -> engine_state reply, for notebooks not on the kernel

async function engineCatalog() {
  engineCatalogCache ||= await rpc("engine_catalog");
  return engineCatalogCache;
}

function nbEngine(nb) {
  return engineStates.get(nb?.name) || { engine: ENGINE_KERNEL, status: "ready" };
}

// True when cells run on an engine session rather than the notebook's kernel.
function nbOnEngine(nb) {
  return previewShown() && nbEngine(nb).engine !== ENGINE_KERNEL;
}

// A small ⓘ: the explanation on request, never inside a menu item's text.
function engineInfo(text) {
  return h("span", { class: "engine-info", role: "img", "aria-label": text, title: text }, icon("info", 14));
}

// The toolbar: Engine, This PC or Remote, the Databricks connection, and its catalog.schema.
function engineToolbar(nb) {
  const st = nbEngine(nb);
  const where = nbWhere(nb);
  const onKernel = st.engine === ENGINE_KERNEL;
  const label = onKernel ? (where.fabric ? `Fabric job${where.short.includes("·") ? where.short.slice(6) : ""}` : "Python (this PC)")
    : st.status === "starting" ? `${st.label} (starting)` : st.label;
  const remote = onKernel ? where.fabric : st.remote;
  const button = h("button", { class: "tool outline engine-btn", "aria-haspopup": "menu", "aria-expanded": "false",
    "data-menu-key": "engine", title: "The engine that runs this notebook's cells",
    onclick: (e) => openEngineMenu(e.currentTarget, nb) },
  icon(remote ? "cloud" : "desktop", 16), h("span", { class: "engine-key" }, "Engine"), label,
  !onKernel && st.remote ? previewTag() : null, icon("chevronDown", 14));
  const badge = h("span", { class: `engine-where ${remote ? "remote" : "local"}` },
    remote ? `Remote · ${onKernel ? "uses Fabric capacity" : st.bills}` : "This PC");
  const conn = String(st.engine).startsWith("databricks-") ? nb.engineConnection : null;  // only beside a Databricks engine
  const connBtn = conn ? h("button", { class: "tool outline", "aria-haspopup": "menu", title: "The Databricks connection",
    onclick: (e) => pickEngineConnection(nb, e.currentTarget) }, h("span", { class: "engine-key" }, "Connection"), conn.label,
  icon("chevronDown", 14)) : null;
  const scope = conn && conn.catalog ? h("span", { class: "engine-scope mono", title: "Default catalog and schema" },
    conn.schema ? `${conn.catalog}.${conn.schema}` : conn.catalog) : null;
  return [button, badge, connBtn, scope].filter(Boolean);
}

// A menu row for one engine: its name, a Preview tag when remote, what it bills, and an ⓘ.
function engineRow(e, current, onPick) {
  const row = btn(e.label, e.location === "remote" ? "cloud" : "desktop", onPick, "tool engine-row",
    { "aria-checked": String(current), role: "menuitemradio" });
  if (e.location === "remote") row.append(previewTag());
  const note = (e.limitations || [])[0] || "";
  if (note) row.append(engineInfo(note));
  if (current) row.append(icon("ok", 14));
  if (e.bills) row.append(h("span", { class: "engine-bills" }, `Asks first · ${e.bills}`));
  return row;
}

async function openEngineMenu(anchor, nb) {
  if (!previewShown()) return showMenu(anchor, computeMenu(nb));
  const cat = await attempt(() => engineCatalog(), "Couldn't list the engines");
  if (!cat) return;
  const st = nbEngine(nb);
  const where = nbWhere(nb);
  const onKernel = st.engine === ENGINE_KERNEL;
  const engines = cat.engines.filter((e) => e.adapter && !e.kernel);
  const local = engines.filter((e) => e.location !== "remote");
  const remote = engines.filter((e) => e.location === "remote");
  const planned = cat.engines.filter((e) => !e.adapter);
  const env = ENV_LABEL[state.meta?.env] || "Dev";
  const kernelRow = (value, text, iconName, on) => {
    const row = btn(text, iconName, () => chooseKernel(nb, value), "tool engine-row", { role: "menuitemradio", "aria-checked": String(on) });
    if (on) row.append(icon("ok", 14));
    return row;
  };
  showMenu(anchor, [
    { info: "Engine for this notebook" },
    kernelRow("local", "Python (this PC)", "desktop", onKernel && where.choice === "local"),
    kernelRow("default", `Same as ${env} (${where.envFabric ? "Fabric job" : "Python (this PC)"})`, "sync", onKernel && where.choice === "default"),
    ...local.map((e) => engineRow(e, st.engine === e.id, () => chooseEngine(nb, e))),
    { info: "Remote" },
    (() => {
      const row = kernelRow("fabric", `Fabric job${nbLakehouse(nb) ? ` (${publishWorkspaceName(nb)})` : " (attach a lakehouse first)"}`, "cloud",
        onKernel && where.choice === "fabric");
      row.append(previewTag(), h("span", { class: "engine-bills" }, "Run all publishes and runs it · uses Fabric capacity"));
      return row;
    })(),
    ...remote.map((e) => engineRow(e, st.engine === e.id, () => chooseEngine(nb, e))),
    planned.length ? btn("More", "chevronRight", () => showMenu(anchor, [{ info: "Planned, not available in this version" },
      ...planned.map((e) => ({ info: `${e.label}: ${e.planned}` }))]), "tool engine-row", { role: "menuitem" }) : null,
    { info: "Engines on this PC start no Fabric job. Reading or writing OneLake from this PC still uses Fabric capacity." },
    // GD-05: the decision pattern behind this choice, shipped with the app (learn.js).
    previewShown() ? { label: "This PC or Fabric? How to choose", icon: "help", action: () => learnPatternDialog("DP-02") } : null,
    ...(onKernel && !where.fabric ? oneLakeWritesMenu(nb) : []),
    onKernel ? { label: "Restart kernel", icon: "restart", action: restartKernel } : null,
  ].filter(Boolean));
}

// Python (this PC), Fabric job or Same as the environment: the kernel, as Runs on chose it.
async function chooseKernel(nb, value) {
  const st = nbEngine(nb);
  if (st.engine !== ENGINE_KERNEL) {
    const ok = await confirmDialog(`Switch to Python (this PC)?`,
      `The ${st.label} session closes, and what it holds isn't carried over. Cells and outputs stay.`,
      "Switch and close session", { danger: true });
    if (!ok) return;
    await attempt(() => rpc("engine_select", { notebook: nb.name, engine: ENGINE_KERNEL, decision: "close" }), "Couldn't close the session");
    engineStates.delete(nb.name);
  }
  await setNotebookCompute(value);
  if (state.notebook === nb) renderNotebook();
}

// The Databricks connection for this notebook: one is used as it is; several are asked for.
async function pickEngineConnection(nb, anchor = null) {
  const found = await attempt(() => rpc("engine_connections"), "Couldn't read the Databricks connections");
  if (!found) return null;
  const conns = found.connections;
  if (!conns.length) {
    toast("There's no Databricks connection on this PC yet. Sign in with the Databricks CLI (databricks auth login --profile <name>), then choose the engine again.", true);
    return null;
  }
  if (conns.length === 1 && !anchor) return (nb.engineConnection = conns[0]);
  return new Promise((resolve) => {
    showMenu(anchor || document.querySelector(".engine-btn"), [{ info: "Databricks connection" },
      ...conns.map((c) => ({ label: `${c.label}${c.warning ? " (keeps a token in plain text)" : ""}`,
        icon: nb.engineConnection?.key === c.key ? "ok" : "plugConnected", action: () => { nb.engineConnection = c; resolve(c); if (anchor) renderNotebook(); } }))]);
  });
}

function engineFacts(rows) {
  return h("dl", { class: "engine-facts" }, rows.filter(Boolean).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

// What a switch loses and keeps. Resolves "close", "keep" or null (cancelled).
function engineSwitchDialog(nb, from, to, transition) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (settled) return; settled = true; closeDialog(); resolve(v); };
    const vars = from === "Python (this PC)" && Array.isArray(state.variables) && state.variables.length
      ? state.variables.map((v) => v.name) : [];
    const lost = vars.length ? `Variables in this session: ${vars.slice(0, 3).join(", ")}${vars.length > 3 ? ` and ${vars.length - 3} more` : ""}`
      : `Variables and tables in the ${from} session`;
    const canKeep = transition.decisions.includes("keep");
    showDialog(`Switch to ${to}?`, h("div", { class: "engine-switch" },
      h("p", { class: "text" }, `${to} starts its own session. The ${from} session can't move to it.`),
      h("div", { class: "engine-switch-boxes" },
        h("div", { class: "engine-lost" }, h("h3", {}, "Lost"), h("ul", {}, h("li", {}, lost))),
        h("div", { class: "engine-kept" }, h("h3", {}, "Kept"), h("ul", {}, h("li", {}, "Cells"), h("li", {}, "Outputs")))),
      canKeep ? h("p", { class: "muted-text" }, `Keep both leaves the ${from} session open on this PC next to ${to}.`) : null), [
      btn("Cancel", null, () => done(null), "tool outline"),
      canKeep ? btn("Keep both", null, () => done("keep"), "tool outline") : null,
      btn("Switch and close session", null, () => done("close"), "tool danger-solid"),
    ].filter(Boolean), () => { if (!settled) { settled = true; resolve(null); } });
  });
}

// The remote confirm: the target, what the grant allows, the bill and the advisory risk check.
function engineRemoteDialog(nb, e, plan) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (settled) return; settled = true; closeDialog(); resolve(v); };
    const t = plan.target || {};
    const fabric = e.id === "fabric-spark";
    const name = t.name || t.id;
    const state_ = t.state ? ` · ${t.state.toLowerCase()}` : "";
    const risk = plan.risk || { outcome: "unknown", lines: [] };
    const riskWord = { pass: "No issues found.", warning: "Worth a look:", unknown: "Couldn't run part of it:" }[risk.outcome] || "";
    showDialog(`Use ${e.label}?`, h("div", { class: "engine-confirm" },
      h("p", { class: "text" }, "Cells in this notebook will run on remote compute, not on this PC.", " ", previewTag()),
      engineFacts([
        ["Engine", e.label],
        ["Target", h("span", {}, h("strong", {}, name), state_, t.host ? h("span", { class: "mono muted-text" }, ` ${t.host}`) : null)],
        ["Billing", fabric ? "Uses Fabric capacity while the Spark session runs."
          : `Starts billable compute (Databricks DBU).${/stopped|terminated/i.test(t.state || "") ? " It will start." : ""}`],
        plan.policy?.profile ? ["Connection", `${plan.policy.profile} · asks before remote compute`] : null,
        ["Allows", fabric ? `This notebook to start a Spark session on ${name} for ${plan.grant_minutes} minutes. It uses Fabric capacity until it closes.`
          : `This notebook to start ${name} for ${plan.grant_minutes} minutes. Each start bills DBUs until its auto-stop.`],
        ["Risk check", h("div", {}, h("div", {}, riskWord), h("ul", { class: "engine-risk" },
          risk.lines.map((l) => h("li", { "data-outcome": l.outcome }, l.text))),
        h("div", { class: "muted-text" }, "Advice only; it never blocks. When it can't check something it says \"couldn't run\"."))],
      ]),
      h("p", { class: "engine-note" }, icon("info", 14), "KratosForge never falls back to remote compute on its own.")), [
      btn("Cancel", null, () => done(false), "tool outline"),
      btn(`Allow for ${plan.grant_minutes} minutes`, null, () => done(true), "tool primary"),
    ], () => { if (!settled) { settled = true; resolve(false); } });
  });
}

async function chooseEngine(nb, e) {
  const st = nbEngine(nb);
  if (st.engine === e.id && st.status !== "error") return;
  const params = { notebook: nb.name, engine: e.id };
  if (e.platform === "databricks") {
    const conn = nb.engineConnection || await pickEngineConnection(nb);
    if (!conn) return;
    params.connection = conn.key;
  }
  if (e.id === "fabric-spark") {
    const lh = nbLakehouse(nb);
    if (lh) Object.assign(params, { workspace_id: lh.workspace_id, lakehouse_id: lh.lakehouse_id, lakehouse_name: lh.name || "" });
  }
  const plan = await attempt(() => rpc("engine_prepare", { ...params, cells: nb.cells.filter((c) => c.type === "code").map((c) => c.source) }),
    `Couldn't prepare ${e.label}`);
  if (!plan) return;
  let decision = null;
  if (plan.transition?.needed) {
    const from = st.engine === ENGINE_KERNEL ? "Python (this PC)" : st.label;
    decision = await engineSwitchDialog(nb, from, e.label, plan.transition);
    if (!decision) return;
  }
  let confirmed = false;
  if (e.location === "remote" && plan.confirm) {
    confirmed = await engineRemoteDialog(nb, e, plan);
    if (!confirmed) return;
  }
  // From the kernel, "close" clears its variables, as Restart kernel does.
  if (st.engine === ENGINE_KERNEL && decision === "close") await restartKernel();
  const started = await attempt(() => rpc("engine_select", { ...params, decision: st.engine === ENGINE_KERNEL ? null : decision, confirmed }),
    `Couldn't start ${e.label}`);
  if (!started) return;
  engineStates.set(nb.name, { engine: e.id, label: e.label, remote: e.location === "remote", bills: e.bills, status: "starting" });
  if (state.notebook === nb) renderNotebook();
  for (;;) {
    const now = await attempt(() => rpc("engine_state", { notebook: nb.name }), "Couldn't read the engine's state");
    if (!now) return;
    engineStates.set(nb.name, now);
    if (now.status !== "starting") {
      if (now.status === "error") {
        engineStates.delete(nb.name);
        await rpc("engine_close", { notebook: nb.name }).catch(() => null);
        toast(`${e.label} didn't start: ${now.error}`, true);
      } else {
        toast(`${nb.name} now runs on ${e.label}.${plan.cells?.length ? ` ${plan.cells.length} cell${plan.cells.length === 1 ? "" : "s"} can't run on it or run translated; they're flagged before running.` : ""}`);
      }
      if (state.notebook === nb) renderNotebook();
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

// One cell on the notebook's engine. A cell the engine can't run is flagged, and nothing runs.
async function runEngineCell(cell, nb) {
  const st = nbEngine(nb);
  if (st.status !== "ready") {
    toast(st.status === "starting" ? `${st.label} is still starting.` : `${st.label} isn't ready: ${st.error || "choose it again"}.`, true);
    return false;
  }
  cell.engineFlag = null;
  const started = await attempt(() => rpc("engine_start_cell", { notebook: nb.name, source: cell.source }), "Couldn't run the cell");
  if (!started) return false;
  if (started.status === "unsupported") {
    cell.engineFlag = { reason: started.reason, engine: st.label };
    if (state.notebook?.cells.includes(cell)) updateResults(cell);
    return false;
  }
  setKernel("busy", nb);
  nb.engineRunning = true;
  cell.status = "running";
  cell.outputs = [];
  cell.seconds = 0;
  if (state.notebook?.cells.includes(cell)) updateResults(cell);
  let out;
  for (;;) {
    out = await rpc("engine_poll_cell", { notebook: nb.name, wait: 0.4 }).catch((err) => ({ status: "error",
      outputs: [{ type: "error", ename: "Engine error", evalue: String(err) }], seconds: cell.seconds }));
    if (out.status !== "running") break;
    cell.seconds = out.seconds;
    if (state.notebook?.cells.includes(cell)) updateResults(cell);
  }
  cell.status = out.status === "ok" ? "ok" : out.status === "cancelled" ? "cancelled" : "error";
  cell.outputs = out.outputs || [];
  cell.seconds = out.seconds || 0;
  cell.ranOn = st.label;
  cell.rows = out.rows ?? null;
  cell.finishedAt = timeText(new Date());
  nb.engineRunning = false;
  setKernel("idle", nb);
  if (state.notebook?.cells.includes(cell)) updateResults(cell);
  if (state.notebook?.dirty) markDirty();
  return cell.status === "ok";
}

// The flag on a cell the engine can't run: why, and Run this cell on Python for this cell only.
function engineFlagPanel(cell) {
  const f = cell.engineFlag;
  return h("div", { class: "engine-flag", role: "status" }, icon("warning", 16), h("span", {}, f.reason),
    btn("Run this cell on Python", "play", () => runCellOnKernel(cell), "tool outline",
      { title: "Runs this one cell in the notebook's Python kernel on this PC; the notebook stays on its engine." }));
}

async function runCellOnKernel(cell) {
  cell.engineFlag = null;
  cell.onKernel = true;
  return runCell(cell);
}

async function cancelEngineCell(nb) {
  const res = await attempt(() => rpc("engine_cancel", { notebook: nb.name }), "Could not cancel");
  if (res && !res.cancelled) toast("Nothing was running on the engine.");
}

registerFeature({ id: "engines", preview: true, commands: [
  { label: "Choose the notebook's engine", icon: "desktop", keywords: "engine runs on databricks duckdb spark warehouse connect",
    run: () => { const b = document.querySelector(".engine-btn"); if (b) b.click(); else toast("Open a notebook first."); } },
] });
