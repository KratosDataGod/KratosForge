// Pipelines (Preview): open a Fabric pipeline read-only, see what can run on this PC, and run it
// here. Create > Pipeline opens one from a repository this PC opened, a .DataPipeline folder or a
// pipeline JSON file. The page shows its graph, what each activity needs to run here, its runs and
// its definition; Run on this PC asks for parameters, mocks, notebooks and data steps, and the run
// view follows the run with Stop. The engine does the work (pipeline_service.py,
// docs/orchestration/local-runs.md); nothing here starts anything in Fabric.

const plPage = { area: "pipelines", pipeline: null, tab: "graph", selected: null, runId: null, run: null, runSelected: null };
// Run id -> the settings it started with, for "Run again with the same settings".
const plStarts = new Map();

// Problems the Run dialog clears without a mock: a notebook to pick, lakehouse reads to allow, a workspace
// or parameter to give. Every other problem means the activity needs a mock.
const PL_SETUP = new Set(["notebook_unmapped", "notebook_missing", "notebook_uncontained", "data_needs_engine",
  "data_workspace_unknown", "script_project_missing", "missing_parameter", "bad_parameter", "unknown_parameter"]);
const PL_NOTEBOOK_TYPES = new Set(["TridentNotebook", "Notebook"]);
const PL_DATA_TYPES = new Set(["Lookup", "GetMetadata", "Script"]);
const PL_RUNNING = new Set(["pending", "running"]);
const PL_MOCK = "\u0000mock";   // the notebook choice "Give it a mock instead"; no notebook is named this

// ---------- pure helpers (app/tests/ui.test.cjs runs these) ----------

// How an activity can run here: "here", "mock" (needs a mock) or "fabric" (Fabric only), from its
// registry entry and the problems a lenient check reported for it (by path).
function plVerdict(compat, problems) {
  if (compat?.classification === "unsupported") return "fabric";
  const own = (problems || []).filter((p) => p.path === compat?.path && p.code);
  return own.some((p) => !PL_SETUP.has(p.code)) ? "mock" : "here";
}

// Top-level activities in columns: each one column after the latest activity it depends on.
function plLayers(activities) {
  const byName = new Map((activities || []).map((a) => [a.name, a]));
  const depth = new Map();
  const seen = new Set();
  const of = (a) => {
    if (depth.has(a.name)) return depth.get(a.name);
    if (seen.has(a.name)) return 0;   // a cycle: validation reports it; draw it flat
    seen.add(a.name);
    const d = Math.max(-1, ...(a.depends_on || []).map((x) => (byName.has(x.activity) ? of(byName.get(x.activity)) : -1))) + 1;
    depth.set(a.name, d);
    return d;
  };
  const layers = [];
  for (const a of activities || []) (layers[of(a)] ||= []).push(a);
  return layers.filter(Boolean);
}

// "after Transform", "after Validate fails", "after A, B completes".
function plAfter(a) {
  const deps = a.depends_on || [];
  if (!deps.length) return "";
  const word = { Succeeded: "", Failed: " fails", Completed: " completes", Skipped: " is skipped" };
  return `after ${deps.map((d) => `${d.activity}${(d.conditions || ["Succeeded"]).map((c) => word[c] ?? ` ${c}`).join(" or")}`).join(", ")}`;
}

// The run's activity records as rows: each under its parent (ForEach iterations, If branches), and a
// child pipeline's activities under the Execute pipeline that started it.
function plTimelineRows(record, depth = 0) {
  const acts = record?.activities || [];
  const kids = new Map();
  for (const a of acts) {
    const key = a.parent || "";
    if (!kids.has(key)) kids.set(key, []);
    kids.get(key).push(a);
  }
  const rows = [];
  const iteration = (a) => { const m = /^iteration (\d+)$/.exec(a.label || ""); return m ? Number(m[1]) : -1; };
  const walk = (parent, d) => {
    const list = (kids.get(parent) || []).map((a, i) => [a, i]).sort(([a, i], [b, j]) => iteration(a) - iteration(b) || i - j).map(([a]) => a);
    for (const a of list) {
      rows.push({ act: a, depth: d });
      walk(a.id, d + 1);
      if (a.child_run) rows.push(...plTimelineRows(a.child_run, d + 1));
    }
  };
  walk("", depth);
  return rows;
}

// Where a bar sits on the run's time line, as percentages.
function plBar(act, start, end) {
  const t0 = Date.parse(start), t1 = Date.parse(end);
  const a = Date.parse(act.started), b = act.ended ? Date.parse(act.ended) : t1;
  if (![t0, t1, a, b].every(Number.isFinite) || t1 <= t0) return { left: 0, width: 0 };
  const left = Math.max(0, Math.min(100, ((a - t0) / (t1 - t0)) * 100));
  return { left, width: Math.max(0.5, Math.min(100 - left, ((b - a) / (t1 - t0)) * 100)) };
}

// The Run dialog's settings as kept in localStorage: no SecureString parameter values, and leave for
// notebook steps to reach the network and files is never remembered.
function plStorable(settings, declared) {
  const secure = new Set((declared || []).filter((x) => String(x.type || "").toLowerCase() === "securestring").map((x) => x.name));
  const parameters = Object.fromEntries(Object.entries(settings.parameters || {}).filter(([k]) => !secure.has(k)));
  return { ...settings, parameters, allow_uncontained: false };
}

// Time taken: "<0.1s" for a step that took next to nothing, "—" when nothing was measured.
function plTook(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") return "—";
  return Number(seconds) < 0.1 ? "<0.1s" : took(seconds);
}

// A parameter's value as typed: strings as they are, everything else as JSON.
function plParamValue(type, text) {
  if (String(type || "").toLowerCase() === "string" || String(type || "").toLowerCase() === "securestring") return text;
  if (text.trim() === "") return undefined;
  return JSON.parse(text);
}

function plParamText(type, value) {
  if (value === undefined) return "";
  return typeof value === "string" && ["string", "securestring"].includes(String(type || "").toLowerCase()) ? value : JSON.stringify(value);
}

// UTF-8 text as base64, in slices so a large file doesn't overflow the call stack.
function plBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

// ---------- opening ----------

function plSourceOf(pipeline) {
  return pipeline.source.path ? { path: pipeline.source.path } : { text: pipeline.source.text };
}

function plSaved(name) {
  try { return JSON.parse(localStorage.getItem(`fd.pipeline.${name}`) || "{}") || {}; } catch { return {}; }
}

function plSave(name, settings) {
  try { localStorage.setItem(`fd.pipeline.${name}`, JSON.stringify(settings)); } catch { /* this session only */ }
}

async function pipelineOpenDialog() {
  const list = h("div", { class: "pl-open-list" }, loading("Looking for pipelines in your repositories..."));
  const problem = h("p", { class: "error-text", hidden: true });
  const fail = (text) => { problem.textContent = text; problem.hidden = false; };
  const folder = h("input", { type: "file", webkitdirectory: true, hidden: true, "aria-label": "Choose a .DataPipeline folder" });
  folder.addEventListener("change", async () => {
    const files = [...(folder.files || [])].filter((f) => /(^|\/)[^/]+\.DataPipeline\/(pipeline-content\.json|\.platform)$/.test(f.webkitRelativePath || f.name)
      || ["pipeline-content.json", ".platform"].includes(f.name));
    const content = files.find((f) => f.name === "pipeline-content.json");
    if (!content) { fail("That folder has no pipeline-content.json. Choose the pipeline's .DataPipeline folder from a Fabric Git repository."); return; }
    const platform = files.find((f) => f.name === ".platform");
    const part = async (f) => ({ path: f.name, payload: plBase64(await f.text()), payloadType: "InlineBase64" });
    const parts = [await part(content)];
    if (platform) parts.push(await part(platform));
    const where = (content.webkitRelativePath || "").split("/").slice(0, -1).join("/") || "a folder you chose";
    await openPipeline({ text: JSON.stringify({ parts }) }, { label: where });
  });
  const file = h("input", { type: "file", accept: ".json,application/json", hidden: true, "aria-label": "Choose a pipeline JSON file" });
  file.addEventListener("change", async () => {
    const f = file.files?.[0];
    if (f) await openPipeline({ text: await f.text() }, { label: f.name });
  });
  const fabric = plWorkspaceSource(fail);
  showDialog("Open a pipeline", h("div", { class: "pl-open" },
    h("p", { class: "muted-text" }, "Pipelines open read-only. You edit them in Fabric or in your repository, and KratosForge checks and runs them here."),
    h("b", {}, "In your repositories"), list, problem,
    h("b", {}, "In a Fabric workspace ", previewTag()), fabric,
    h("div", { class: "pl-open-files" },
      btn("Choose a .DataPipeline folder", "folderOpen", () => folder.click(), "tool outline"),
      btn("Choose a pipeline JSON file", "file", () => file.click(), "tool outline"), folder, file)),
  [btn("Cancel", null, closeDialog, "tool outline")]);
  const found = await rpc("pipeline_list").catch((err) => { fill(list, h("p", { class: "muted-text" }, friendlyError(err).text)); return null; });
  if (!found || !list.isConnected) return;
  const lastRun = (r) => !r ? h("span", { class: "pill-s neu" }, "Never run here")
    : r.state === "succeeded" ? h("span", { class: "pill-s pass" }, "Last run succeeded")
    : PL_RUNNING.has(r.state) ? h("span", { class: "pill-s info" }, "Running")
    : h("span", { class: "pill-s fail" }, `Last run ${r.state}`);
  fill(list, found.pipelines.length
    ? h("div", { class: "pl-open-rows" }, found.pipelines.map((p) => h("button", { class: "pl-open-row", title: p.path,
      onclick: () => openPipeline({ path: p.path }, { label: `${p.repo} · ${p.relative}` }) },
      h("b", {}, p.name), h("span", { class: "muted-text" }, p.problem ? "Can't be read" : `${p.activity_count} activities · ${p.repo}`), lastRun(p.last_run))))
    : h("p", { class: "muted-text" }, "No .DataPipeline folders in the repositories this PC opened. Open a repository in Source control, or choose a folder or file below."));
}

// Data Factory DF-2: a workspace's pipelines, read with getDefinition. Reads only: no Fabric job, no
// capacity. The definition text holds SecureString defaults, so it stays in memory (plPage) and is
// never logged or kept in localStorage; only the chosen workspace id is remembered.
const PL_WORKSPACE_KEY = "fd.pipelines.workspace";

function plWorkspaceSource(fail) {
  const box = h("div", { class: "pl-open-fabric" });
  const rows = h("div", { class: "pl-open-list" });
  const listIn = async (ws) => {
    try { localStorage.setItem(PL_WORKSPACE_KEY, ws.id); } catch { /* this session only */ }
    fill(rows, loading(`Reading the pipelines in ${ws.displayName}...`));
    const found = await rpc("workspace_pipelines", { workspace_id: ws.id })
      .catch((err) => { fill(rows, h("p", { class: "muted-text" }, friendlyError(err).text)); return null; });
    if (!found || !rows.isConnected) return;
    fill(rows, found.pipelines.length
      ? h("div", { class: "pl-open-rows" }, found.pipelines.map((p) => h("button", { class: "pl-open-row", title: p.description || p.name,
        onclick: () => openWorkspacePipeline(ws, p, fail) },
        h("b", {}, p.name), h("span", { class: "muted-text" }, p.description || ws.displayName), h("span", { class: "pill-s neu" }, "In Fabric"))))
      : h("p", { class: "muted-text" }, `${ws.displayName} has no pipelines.`));
  };
  const show = async () => {
    fill(box, loading("Listing your workspaces..."));
    const workspaces = await loadWorkspaces();
    if (!box.isConnected) return;
    if (!workspaces.length) { fill(box, h("p", { class: "muted-text" }, "No workspaces to list. Sign in, or ask for access to a workspace.")); return; }
    let saved = "";
    try { saved = localStorage.getItem(PL_WORKSPACE_KEY) || ""; } catch { /* none */ }
    const pick = h("select", { "aria-label": "Workspace" },
      h("option", { value: "" }, "Choose a workspace"),
      workspaces.map((w) => h("option", { value: w.id, selected: w.id === saved || null }, w.displayName)));
    pick.addEventListener("change", () => {
      const ws = workspaces.find((w) => w.id === pick.value);
      if (ws) listIn(ws); else fill(rows);
    });
    fill(box, pick, rows);
    const ws = workspaces.find((w) => w.id === saved);
    if (ws) listIn(ws);
  };
  if (state.user) show();
  else fill(box, btn("Sign in to list workspace pipelines", "person", show, "tool outline"));
  return box;
}

async function openWorkspacePipeline(ws, p, fail) {
  const res = await rpc("workspace_pipeline_definition", { workspace_id: ws.id, item_id: p.item_id, name: p.name })
    .catch((err) => { fail(`Could not read ${p.name} from Fabric: ${friendlyError(err).text}`); return null; });
  if (!res) return;
  await openPipeline({ text: res.text }, { label: `${ws.displayName} · Fabric workspace`,
    fabric: { type: "DataPipeline", workspace_id: ws.id, item_id: p.item_id } });
}

async function openPipeline(source, origin) {
  const imp = await attempt(() => rpc("pipeline_import", source), "Could not read the pipeline");
  if (!imp) return;
  if (!imp.name && !imp.ok) {
    toast(`Could not read the pipeline: ${imp.diagnostics?.[0]?.message || "it isn't a pipeline definition"}`, true);
    return;
  }
  closeDialog();
  plPage.pipeline = { source, origin, imp, compat: null, check: null };
  plPage.area = "pipelines";
  plPage.tab = "graph";
  plPage.selected = null;
  go("pipelines");
}

// The registry entries and a lenient check: the one with every Run dialog choice made, so what remains
// needs a mock.
async function plLoadChecks(p) {
  const src = plSourceOf(p);
  const [compat, check] = await Promise.all([rpc("pipeline_compatibility", src),
    rpc("pipeline_run_check", { ...src, notebooks: {}, read_lakehouses: true, allow_uncontained: true })]);
  p.compat = compat;
  p.check = check;
}

// ---------- the page ----------

const PL_TABS = [["graph", "Graph"], ["compat", "Compatibility"], ["runs", "Runs"], ["definition", "Definition"]];
const PL_PILL = { here: ["pass", "Runs here"], mock: ["wrn", "Needs a mock"], fabric: ["neu", "Fabric only"] };

function plPill(verdict) {
  const [cls, label] = PL_PILL[verdict];
  return h("span", { class: `pill-s ${cls}` }, label);
}

async function renderPipelines() {
  if (plPage.area === "connections") return renderDfConnections();
  if (plPage.area === "dataflows") return renderDfDataflows();
  const p = plPage.pipeline;
  if (!p) {
    setView(h("div", { class: "page" }, dfAreaTabs(), h("h1", {}, "Pipelines ", previewTag()),
      emptyState({ icon: "flow", title: "No pipeline open", text: "Open a Fabric pipeline to see what can run on this PC.",
        action: btn("Open a pipeline", "folderOpen", pipelineOpenDialog, "tool primary") })));
    return;
  }
  const body = h("div", { class: "pl-body" }, loading("Checking what can run here..."));
  setView(h("div", { class: "page pipelines" }, dfAreaTabs(),
    h("section", { class: "card pad pl-head" },
      h("div", { class: "pl-title" }, h("h1", {}, p.imp.name || "Pipeline", " ", previewTag()),
        h("p", { class: "muted-text" }, `From ${p.origin.label} · read-only here, edit it in Fabric or in your repository · `, learnMore("pipelines-preview"))),
      h("div", { class: "pl-actions" },
        p.origin.fabric ? btn("Open in Fabric", "open", () => openInFabric(fabricItemLink(p.origin.fabric)), "tool outline") : null,
        btn("Check again", "sync", () => { p.compat = null; render(); }, "tool outline"),
        btn("Run on this PC", "play", () => pipelineRunDialog(), "tool primary"))),
    body));
  if (!p.compat) {
    const ok = await attempt(() => plLoadChecks(p), "Could not check the pipeline");
    if (ok === undefined && !p.compat) { fill(body, h("p", { class: "muted-text" }, "The check didn't finish. Try Check again.")); return; }
  }
  if (state.view !== "pipelines" || plPage.pipeline !== p || !body.isConnected) return;
  const all = p.compat.activities || [];
  const problems = p.check.problems || [];
  const verdicts = new Map(all.map((a) => [a.path, plVerdict(a, problems)]));
  const n = (v) => [...verdicts.values()].filter((x) => x === v).length;
  const params = p.imp.parameters || [];
  const tabs = h("div", { class: "subtabs", role: "tablist", "aria-label": "Pipeline views" }, PL_TABS.map(([id, label]) =>
    h("button", { class: `tab${plPage.tab === id ? " active" : ""}`, role: "tab", "aria-selected": plPage.tab === id ? "true" : "false",
      onclick: () => { plPage.tab = id; render(); } }, label)));
  const counted = [["here", "run here", "runs here"], ["mock", "need a mock", "needs a mock"], ["fabric", "Fabric only", "Fabric only"]]
    .filter(([v]) => n(v)).map(([v, many, one]) => h("span", { class: `pill-s ${PL_PILL[v][0]}` }, `${n(v)} ${n(v) === 1 ? one : many}`));
  const summary = h("p", { class: "pl-summary" }, `${all.length} activities: `, counted,
    params.length ? h("span", { class: "muted-text" }, ` · Parameters: ${params.map((x) => `${x.name} (${x.type})`).join(", ")}`) : null);
  const main = h("div", { class: "pl-tab" });
  fill(body, tabs, summary, main, plHow());
  if (plPage.tab === "graph") fill(main, ...plGraph(p, verdicts, problems));
  else if (plPage.tab === "compat") fill(main, plCompatTable(p, verdicts, problems));
  else if (plPage.tab === "runs") plRunsTab(p, main);
  else plDefinitionTab(p, main);
}

function plHow() {
  return howItWorks("pipelines", {
    reads: "The pipeline's folder or file and the notebooks it runs (saved in KratosForge). Notebook steps read pinned versions of lakehouse tables; Lookup, Get Metadata and Script read them as they are now, and only when you allow it.",
    changes: "Nothing in Fabric starts. Notebook steps write only to a temporary folder in the lakehouse, deleted when the run ends unless you keep it. A Script step run in a local data project changes that project's own tables. Nothing else is written. OneLake reads and writes use a little capacity.",
    results: "Run records stay on this PC, under the Runs tab.",
    anchor: "pipelines-preview",
  });
}

function plGraph(p, verdicts, problems) {
  const byPath = new Map((p.compat.activities || []).map((a) => [a.path, a]));
  const node = (a) => {
    const entry = byPath.get(a.path) || {};
    const kids = Object.entries(a.children || {});
    return h("button", { class: `pl-node${plPage.selected === a.path ? " sel" : ""}${kids.length ? " box" : ""}`, "data-path": a.path,
      onclick: () => { plPage.selected = a.path; render(); } },
      h("b", {}, a.name), h("span", { class: "muted-text" }, entry.title || a.type),
      plAfter(a) ? h("span", { class: "muted-text small" }, plAfter(a)) : null,
      kids.map(([branch, list]) => h("span", { class: "pl-inner" }, h("span", { class: "muted-text small" }, plBranch(branch, a.case_values)),
        list.map((k) => h("span", { class: "pl-inner-row" }, k.name, " ", plPill(verdicts.get(k.path) || "here"))))),
      a.inactive ? h("span", { class: "pill-s neu" }, "Inactive") : plPill(verdicts.get(a.path) || "here"));
  };
  const layers = plLayers(p.imp.activities || []);
  const graph = h("section", { class: "card pad pl-graph", "aria-label": "Pipeline graph" },
    h("div", { class: "pl-layers" }, layers.flatMap((layer, i) => [i ? h("span", { class: "pl-arrow", "aria-hidden": "true" }, "→") : null,
      h("div", { class: "pl-layer" }, layer.map(node))])),
    h("p", { class: "muted-text" }, "Read-only. Each column runs after the one before it; click an activity for how it runs here."));
  const sel = byPath.get(plPage.selected);
  return [graph, sel ? plDetail(sel, verdicts.get(sel.path), problems) : null];
}

// A container's branch as people say it. Switch cases come from the engine as cases[0], cases[1] ...
// and read as their value, as in Fabric: Case "emea". A case with no text value reads Case 1.
function plBranch(branch, values = []) {
  const c = /^cases\[(\d+)\]$/.exec(branch);
  const value = c ? values[Number(c[1])] : null;
  return { activities: "Each item:", ifTrueActivities: "If true:", ifFalseActivities: "If false:", defaultActivities: "Default:" }[branch]
    || (c ? (typeof value === "string" ? `Case "${value}":` : `Case ${Number(c[1]) + 1}:`) : `${branch}:`);
}

function plDetail(a, verdict, problems) {
  const own = problems.filter((x) => x.path === a.path);
  const why = verdict === "fabric" ? `${a.title || a.type} runs only in Fabric. Give it a mock to run the rest here.`
    : own.length ? own.map((x) => x.message).join(" ")
    : `${a.title || a.type} runs on this PC.`;
  return h("section", { class: "card pad pl-detail" },
    h("div", { class: "pl-detail-head" }, h("h2", {}, a.name), plPill(verdict)),
    h("p", { class: "muted-text" }, [a.title || a.type, a.container ? `inside ${a.container}` : ""].filter(Boolean).join(" · ")),
    h("p", {}, why),
    (a.limitations || []).length ? h("ul", { class: "pl-notes" }, a.limitations.map((l) => h("li", { class: "muted-text" }, l))) : null,
    verdict !== "here" ? btn("Set its mock output", null, () => pipelineRunDialog(), "tool outline") : null);
}

function plCompatTable(p, verdicts, problems) {
  return h("table", { class: "list pl-compat" },
    h("thead", {}, h("tr", {}, h("th", {}, "Activity"), h("th", {}, "Type"), h("th", {}, "On this PC"), h("th", {}, "Why"))),
    h("tbody", {}, (p.compat.activities || []).map((a) => {
      const v = verdicts.get(a.path);
      const own = problems.filter((x) => x.path === a.path);
      return h("tr", {},
        h("td", {}, a.container ? h("span", { class: "muted-text" }, `${a.container} › `) : null, a.name),
        h("td", {}, a.title || a.type), h("td", {}, plPill(v)),
        h("td", { class: "muted-text" }, own.map((x) => x.message).join(" ") || (a.limitations || [])[0] || ""));
    })));
}

async function plRunsTab(p, main) {
  fill(main, loading("Reading this pipeline's runs..."));
  const out = await rpc("pipeline_runs", { limit: 200 }).catch((err) => { fill(main, h("p", { class: "muted-text" }, friendlyError(err).text)); return null; });
  if (!out || !main.isConnected) return;
  const runs = out.runs.filter((r) => r.pipeline === p.imp.name);
  fill(main, runs.length ? h("table", { class: "list" },
    h("thead", {}, h("tr", {}, h("th", {}, "Status"), h("th", {}, "Started"), h("th", { class: "n" }, "Took"), h("th", {}, "Activities"))),
    h("tbody", {}, runs.map((r) => h("tr", { class: "click", onclick: () => openPipelineRun(r.run_id) },
      h("td", {}, plStatePill(r.state, (r.options?.mocked || []).length)), h("td", {}, when(r.started)), h("td", { class: "n" }, plTook(r.seconds)),
      h("td", { class: "muted-text" }, `${r.counts?.succeeded || 0} succeeded, ${r.counts?.failed || 0} failed${(r.options?.mocked || []).length ? `, ${r.options.mocked.length} mocked` : ""}`)))))
    : emptyState({ icon: "history", title: "No runs here yet", text: "Run on this PC and its record shows here.",
      action: btn("Run on this PC", "play", () => pipelineRunDialog(), "tool primary") }));
}

async function plDefinitionTab(p, main) {
  fill(main, loading("Reading the definition..."));
  const out = await rpc("pipeline_export", { ...plSourceOf(p), format: "pipeline_content" }).catch((err) => ({ ok: false, diagnostics: [{ message: friendlyError(err).text }] }));
  if (!main.isConnected) return;
  if (!out.ok) { fill(main, h("p", { class: "muted-text" }, out.diagnostics?.[0]?.message || "The definition can't be shown.")); return; }
  fill(main, h("section", { class: "card pad" },
    h("div", { class: "pl-detail-head" }, h("span", { class: "muted-text" }, "pipeline-content.json, as Fabric stores it"),
      btn("Copy", "document", () => attempt(() => navigator.clipboard.writeText(out.text).then(() => toast("Copied the definition.")), "Could not copy"), "tool outline small")),
    h("pre", { class: "pl-json" }, out.text)));
}

// A run's state; a run that had mocks never reads as a plain success (it wasn't all real).
function plStatePill(s, mocked = 0) {
  if (s === "succeeded" && mocked) return h("span", { class: "pill-s wrn" }, "Succeeded with mocks");
  const map = { succeeded: ["pass", "Succeeded"], failed: ["fail", "Failed"], cancelled: ["neu", "Cancelled"], interrupted: ["fail", "Interrupted"],
    running: ["info", "Running"], pending: ["info", "Waiting"], skipped: ["neu", "Skipped"], refused: ["fail", "Refused"] };
  const [cls, label] = map[s] || ["neu", s || "Unknown"];
  return h("span", { class: `pill-s ${cls}` }, label);
}

// A record's label as people say it: "iteration 2", "If true", "case B".
function plLabel(label) {
  return { ifTrueActivities: "If true", ifFalseActivities: "If false", defaultActivities: "Default" }[label] || label;
}

// ---------- Run on this PC ----------

async function pipelineRunDialog(again = null) {
  const p = plPage.pipeline;
  if (!p) return;
  if (!p.compat) await attempt(() => plLoadChecks(p), "Could not check the pipeline");
  if (!p.compat) return;
  const saved = again || plSaved(p.imp.name);
  const all = p.compat.activities || [];
  const lenient = new Map(all.map((a) => [a.path, plVerdict(a, p.check.problems)]));
  const s = {
    parameters: { ...(saved.parameters || {}) }, mocks: { ...(saved.mocks || {}) }, notebooks: { ...(saved.notebooks || {}) },
    read_lakehouses: !!saved.read_lakehouses, workspace_id: saved.workspace_id || null, sql_projects: { ...(saved.sql_projects || {}) },
    allow_uncontained: !!saved.allow_uncontained, keep_outputs: !!saved.keep_outputs,
  };
  // Every activity that needs a mock gets one; a remembered mock stays for an activity that still exists.
  for (const a of all) if (lenient.get(a.path) !== "here" && !s.mocks[a.name]) s.mocks[a.name] = { status: "Succeeded", output: {} };
  for (const name of Object.keys(s.mocks)) if (!all.some((a) => a.name === name)) delete s.mocks[name];
  // A data step runs only with lakehouse reads on, or a Script in a project; otherwise it has a mock.
  const dataActs = all.filter((a) => PL_DATA_TYPES.has(a.type) && lenient.get(a.path) === "here");
  const dataRuns = (a) => s.read_lakehouses || (a.type === "Script" && !!s.sql_projects[a.name]);
  const syncData = () => {
    for (const a of dataActs) {
      if (dataRuns(a)) delete s.mocks[a.name];
      else if (!s.mocks[a.name]) s.mocks[a.name] = { status: "Succeeded", output: {} };
    }
  };
  syncData();
  const [notebooks, projects] = await Promise.all([
    all.some((a) => PL_NOTEBOOK_TYPES.has(a.type)) ? rpc("list_notebooks").catch(() => []) : [],
    all.some((a) => a.type === "Script") ? rpc("list_local_projects").catch(() => []) : []]);
  if (!state.workspaces && all.some((a) => PL_DATA_TYPES.has(a.type))) state.workspaces = await rpc("list_workspaces").catch(() => null);

  const problem = h("p", { class: "error-text", hidden: true });
  const status = h("div", { class: "pl-check" }, loading("Checking...", "", { live: false }));
  const runBtn = btn("Run", "play", () => start(), "tool primary", { disabled: true });
  const mockBox = h("div", { class: "pl-mocks" });
  const dataBox = h("div", { class: "pl-section" });
  let checkNo = 0;
  let lastCheck = null;

  const payload = () => {
    const params = {};
    for (const [k, v] of Object.entries(s.parameters)) if (v !== undefined) params[k] = v;
    return { ...plSourceOf(p), parameters: params, mocks: s.mocks,
      notebooks: Object.fromEntries(Object.entries(s.notebooks).filter(([, v]) => v)),
      read_lakehouses: s.read_lakehouses, workspace_id: s.workspace_id || null,
      sql_projects: Object.fromEntries(Object.entries(s.sql_projects).filter(([, v]) => v)),
      allow_uncontained: s.allow_uncontained };
  };
  const check = async () => {
    const no = ++checkNo;
    runBtn.disabled = true;
    fill(status, loading("Checking...", "", { live: false }));
    const out = await rpc("pipeline_run_check", payload()).catch((err) => ({ ok: false, problems: [{ message: friendlyError(err).text }] }));
    if (no !== checkNo || !status.isConnected) return;
    lastCheck = out;
    // A new activity that can't run here gets a mock of its own.
    let added = false;
    for (const x of out.problems || []) {
      if (x.activity && x.code && !PL_SETUP.has(x.code) && !s.mocks[x.activity] && all.some((a) => a.name === x.activity)) {
        s.mocks[x.activity] = { status: "Succeeded", output: {} };
        added = true;
      }
    }
    if (added) { drawMocks(); check(); return; }
    drawData();
    runBtn.disabled = !out.ok;
    fill(status, out.ok
      ? h("span", {}, h("span", { class: "pill-s pass" }, "Checked"), " Every activity can run here or has a mock.")
      : h("div", {}, h("span", { class: "pill-s wrn" }, "Not yet"), h("ul", { class: "pl-notes" }, (out.problems || []).map((x) => h("li", {}, x.message)))),
    h("p", { class: "muted-text" }, "Starts no Fabric job. Notebook steps read pinned table versions from OneLake and write only to a temporary folder in the lakehouse, deleted when the run ends. Data steps, when allowed, read lakehouse tables as they are now. A Script run in a local data project changes that project's tables. Nothing else is written. OneLake reads and writes use a little capacity."));
  };
  let timer = null;
  const later = () => { clearTimeout(timer); timer = setTimeout(check, 300); };

  // Parameters.
  const paramRows = (p.imp.parameters || []).map((x) => {
    const has = Object.prototype.hasOwnProperty.call(s.parameters, x.name);
    const secure = String(x.type || "").toLowerCase() === "securestring";
    const input = h("input", { class: "mono", type: secure ? "password" : "text", "aria-label": x.name,
      value: plParamText(x.type, has ? s.parameters[x.name] : x.default === "(hidden)" ? undefined : x.default) });
    const why = h("span", { class: "error-text small", hidden: true });
    if (!has && x.default !== undefined && x.default !== "(hidden)") s.parameters[x.name] = x.default;
    input.addEventListener("input", () => {
      try { s.parameters[x.name] = plParamValue(x.type, input.value); why.hidden = true; }
      catch { why.textContent = `Not valid ${x.type} (write it as JSON).`; why.hidden = false; return; }
      later();
    });
    return h("label", { class: "pl-field" }, h("span", {}, x.name, " ", h("span", { class: "muted-text small" }, x.type)), input, why);
  });

  const drawMocks = () => {
    const names = Object.keys(s.mocks);
    fill(mockBox, names.length ? names.map((name) => {
      const a = all.find((x) => x.name === name);
      const m = s.mocks[name];
      const sel = h("select", { "aria-label": `${name} ends` }, ["Succeeded", "Failed"].map((v) => h("option", { value: v, selected: (m.status || "Succeeded") === v }, v)));
      sel.addEventListener("change", () => { m.status = sel.value; later(); });
      const out = h("textarea", { class: "mono", rows: 3, "aria-label": `${name} output` }, JSON.stringify(m.output || {}, null, 1));
      const why = h("span", { class: "error-text small", hidden: true });
      out.addEventListener("input", () => {
        try { m.output = JSON.parse(out.value || "{}"); why.hidden = true; later(); }
        catch { why.textContent = "Write the output as JSON."; why.hidden = false; }
      });
      const reason = (p.check.problems || []).find((x) => x.path === a?.path && !PL_SETUP.has(x.code));
      return h("div", { class: "pl-mock" },
        h("div", {}, h("b", {}, name), h("span", { class: "muted-text" }, ` · ${a?.title || a?.type || ""}`),
          reason ? h("div", { class: "muted-text small" }, reason.message) : null),
        h("label", { class: "pl-field" }, h("span", {}, "It ends"), sel),
        h("label", { class: "pl-field" }, h("span", {}, "With output"), out, why),
        lenient.get(a?.path) === "here" && !PL_DATA_TYPES.has(a?.type) && !PL_NOTEBOOK_TYPES.has(a?.type)
          ? btn("Run it instead", null, () => { delete s.mocks[name]; drawMocks(); later(); }, "tool outline small") : null);
    }) : h("p", { class: "muted-text" }, "None: every activity can run here."));
  };

  const drawData = () => {
    const data = dataActs;
    if (!data.length) { fill(dataBox); return; }
    const reads = h("input", { type: "checkbox", checked: s.read_lakehouses });
    reads.addEventListener("change", () => { s.read_lakehouses = reads.checked; syncData(); drawMocks(); later(); });
    const needsWs = (lastCheck?.problems || []).some((x) => x.code === "data_workspace_unknown") || s.workspace_id;
    let ws = null;
    if (needsWs) {
      const sel = h("select", { "aria-label": "Workspace" }, h("option", { value: "" }, "Choose the pipeline's workspace"),
        (state.workspaces || []).map((w) => h("option", { value: w.id, selected: s.workspace_id === w.id }, w.displayName)));
      sel.addEventListener("change", () => { s.workspace_id = sel.value || null; later(); });
      ws = h("label", { class: "pl-field" }, h("span", {}, "Workspace"), sel,
        h("span", { class: "muted-text small" }, "A lakehouse connection doesn't name its workspace, so say which one the pipeline is in."));
    }
    const scripts = data.filter((a) => a.type === "Script").map((a) => {
      const sel = h("select", { "aria-label": `${a.name} runs in` }, h("option", { value: "" }, "The lakehouse (read-only)"),
        (projects || []).map((pr) => h("option", { value: pr.id, selected: s.sql_projects[a.name] === pr.id }, `Local data project: ${pr.name}`)));
      sel.addEventListener("change", () => { s.sql_projects[a.name] = sel.value; syncData(); drawMocks(); later(); });
      return h("label", { class: "pl-field" }, h("span", {}, a.name, " ", h("span", { class: "muted-text small" }, "Script runs in")), sel);
    });
    fill(dataBox, h("b", {}, "Data steps ", h("span", { class: "muted-text small" }, "Lookup, Get Metadata, Script")),
      h("label", { class: "pl-check-row" }, reads, "Let data steps read lakehouses ", h("span", { class: "muted-text small" }, "(off: they need a mock)")),
      ws, scripts,
      scripts.length ? h("p", { class: "muted-text small" }, "A Script that changes tables, or needs the SQL page's confirmation (DROP, TRUNCATE, DELETE or UPDATE without WHERE), is refused here; give it a mock.") : null);
  };

  const nbActs = all.filter((a) => PL_NOTEBOOK_TYPES.has(a.type));
  const nbBox = nbActs.length ? h("div", { class: "pl-section" }, h("b", {}, "Notebook steps"),
    nbActs.map((a) => {
      const mocked = !!s.mocks[a.name];
      const sel = h("select", { "aria-label": `${a.name} runs notebook` }, h("option", { value: "" }, "Choose a notebook saved in KratosForge"),
        (notebooks || []).map((n) => h("option", { value: n.name, selected: !mocked && s.notebooks[a.name] === n.name }, n.name)),
        h("option", { value: PL_MOCK, selected: mocked }, "Give it a mock instead"));
      sel.addEventListener("change", () => {
        if (sel.value === PL_MOCK) s.mocks[a.name] = s.mocks[a.name] || { status: "Succeeded", output: {} };
        else { delete s.mocks[a.name]; s.notebooks[a.name] = sel.value; }
        drawMocks();
        later();
      });
      return h("label", { class: "pl-field" }, h("span", {}, a.name), sel);
    }),
    plCheckbox("Let notebook steps reach the network and files on this PC", s.allow_uncontained, (v) => { s.allow_uncontained = v; later(); }),
    plCheckbox("Keep what notebook steps wrote, to look at after the run", s.keep_outputs, (v) => { s.keep_outputs = v; })) : null;

  const start = async () => {
    runBtn.disabled = true;
    const body = { ...payload(), keep_outputs: s.keep_outputs };
    const out = await attempt(() => rpc("pipeline_run_start", body), "Could not start the run");
    if (!out) { runBtn.disabled = false; return; }
    if (!out.ok) {
      problem.textContent = (out.problems || []).map((x) => x.message).join(" ");
      problem.hidden = false;
      runBtn.disabled = false;
      return;
    }
    const settings = { parameters: s.parameters, mocks: s.mocks, notebooks: s.notebooks, read_lakehouses: s.read_lakehouses,
      workspace_id: s.workspace_id, sql_projects: s.sql_projects, allow_uncontained: s.allow_uncontained, keep_outputs: s.keep_outputs };
    // Kept on this PC for next time without secure parameters, and without leave to reach the network
    // and files, which is asked again each run. Run again in this session keeps both.
    plSave(p.imp.name, plStorable(settings, p.imp.parameters));
    plStarts.set(out.run_id, settings);
    plPulse = out.run_id;
    closeDialog();
    logActivity({ id: `pipeline:${out.run_id}`, status: "running", title: `${p.imp.name} · Pipeline run`, text: "Running on this PC" });
    openPipelineRun(out.run_id);
  };

  drawMocks();
  drawData();
  showDialog(`Run ${p.imp.name} on this PC`, h("div", { class: "pl-run-dialog" },
    paramRows.length ? h("div", { class: "pl-section" }, h("b", {}, "Parameters"), paramRows,
      h("span", { class: "muted-text small" }, "Filled with the pipeline's defaults, or the values of the last run here.")) : null,
    h("div", { class: "pl-section" }, h("b", {}, "Activities that need a mock"), mockBox),
    dataBox, nbBox, status, problem),
  [btn("Cancel", null, closeDialog, "tool outline"), runBtn], () => clearTimeout(timer));
  check();
}

function plCheckbox(label, checked, onChange) {
  const box = h("input", { type: "checkbox", checked });
  box.addEventListener("change", () => onChange(box.checked));
  return h("label", { class: "pl-check-row" }, box, label);
}

// ---------- a run ----------

function openPipelineRun(runId) {
  plPage.runId = runId;
  plPage.run = null;
  plPage.runSelected = null;
  go("pipelineRun");
}

const plFollowing = new Set();
// Energy Flow (KF-MO-004, motion.js pipelineFlow): one per run, kept across the run view's redraws so
// an activity that changes state settles or fails once, not on every poll. plPulse is the run just started
// from the Run dialog: the next draw of the run view plays its Pulse, and any draw clears it, so a run
// whose view never drew (left at once, or its status couldn't be read) doesn't pulse later.
let plFlow = null;
let plPulse = null;
function plRunFlow(id) {
  if (plFlow?.id !== id) plFlow = { id, flow: pipelineFlow(document.createDocumentFragment()) };
  return plFlow.flow;
}

async function renderPipelineRun() {
  const id = plPage.runId;
  if (!id) { go("pipelines"); return; }
  if (!plPage.run || plPage.run.run_id !== id) {
    setView(h("div", { class: "page" }, loading("Reading the run...")));
    const run = await attempt(() => rpc("pipeline_run_status", { run_id: id }), "Could not read the run");
    if (!run || plPage.runId !== id) return;
    plPage.run = run;
    if (state.view !== "pipelineRun") return;
  }
  plDrawRun(learnMore("pipelines-preview"));
  if (PL_RUNNING.has(plPage.run.state)) plFollow(id);
}

async function plFollow(id) {
  if (plFollowing.has(id)) return;
  plFollowing.add(id);
  const end = await pollRun(() => rpc("pipeline_run_status", { run_id: id }), { every: 1000, onStatus: (now) => {
    if (plPage.runId === id) {
      plPage.run = now;
      if (state.view === "pipelineRun") renderPipelineRun();
    }
    return PL_RUNNING.has(now.state);
  } });
  plFollowing.delete(id);
  const r = plPage.runId === id ? plPage.run : null;
  // The Anvil Strike when the run ends well while you watch; a run with mocks isn't a real success,
  // so it gets none. A failure gets the ember. An opened run that had already ended plays nothing.
  const head = state.view === "pipelineRun" && r ? document.querySelector(".pl-flow .flow-where") : null;
  if (head && r.state === "succeeded" && !(r.options?.mocked || []).length) forgeEvent("pipeline.succeeded", head);
  if (head && r.state === "failed") forgeEvent("pipeline.failed", head);
  const text = end.stopped === "lost" ? `Lost track of the run: ${end.message}` : r ? `${plStateWord(r.state)} in ${took(r.seconds)}.` : "Ended.";
  logActivity({ id: `pipeline:${id}`, status: r?.state === "succeeded" ? "ok" : "error", title: `${r?.pipeline || "Pipeline"} · Pipeline run`, text });
}

function plStateWord(s) {
  return { succeeded: "Succeeded", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" }[s] || s;
}

// The run view; help is the link to its section of the guide.
function plDrawRun(help) {
  const r = plPage.run;
  const running = PL_RUNNING.has(r.state);
  const end = r.ended || new Date().toISOString();
  const rows = plTimelineRows(r);
  const params = r.parameters && !Array.isArray(r.parameters) ? Object.entries(r.parameters).map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join(", ") : "";
  const mocked = r.options?.mocked || [];
  const settings = plStarts.get(r.run_id);
  const canAgain = !running && plPage.pipeline?.imp?.name === r.pipeline && settings;
  const sel = rows.find((x) => x.act.id === plPage.runSelected)?.act;
  const flow = plRunFlow(r.run_id);
  const row = ({ act, depth }) => {
    const bar = plBar(act, r.started, end);
    const color = { succeeded: "ok", failed: "bad", cancelled: "off", skipped: "off", running: "run" }[act.state] || "off";
    return h("button", { class: `pl-row${act.id === plPage.runSelected ? " sel" : ""}`, style: `padding-left:${12 + depth * 20}px`,
      onclick: () => { plPage.runSelected = act.id; renderPipelineRun(); } },
      h("span", { class: "pl-row-name" }, act.name, act.label ? h("span", { class: "muted-text small" }, ` · ${plLabel(act.label)}`) : null,
        act.wait_on_completion === false ? h("span", { class: "muted-text small" }, " · didn't wait") : null),
      act.executed_by === "mock" ? h("span", { class: "pill-s neu" }, "Mocked") : plStatePill(act.state),
      h("span", { class: "pl-track" }, act.started ? h("span", { class: `pl-bar ${color}`, style: `left:${bar.left}%;width:${bar.width}%` }) : null),
      h("span", { class: "n" }, act.started ? plTook(act.seconds) : "—"));
  };
  setView(h("div", { class: "page pipelines" }, dfAreaTabs(),
    h("section", { class: "card pad pl-head" },
      h("div", { class: "pl-title" }, h("h1", {}, r.pipeline, " ", plStatePill(r.state, mocked.length), " ", previewTag()),
        h("p", { class: "muted-text" }, [`Run ${r.run_id.slice(0, 8)} on this PC`, r.started ? `started ${new Date(r.started).toLocaleTimeString()}` : "",
          took(r.seconds ?? (Date.now() - Date.parse(r.started)) / 1000) + (running ? " so far" : ""), params,
          mocked.length ? `${mocked.length} mock${mocked.length === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · "), " · ", help)),
      h("div", { class: "pl-actions" },
        plPage.pipeline?.imp?.name === r.pipeline ? btn("Back to the pipeline", null, () => go("pipelines"), "tool outline") : null,
        btn("Run again with the same settings", "restart", () => pipelineRunDialog(settings), "tool outline",
          { disabled: !canAgain, title: canAgain ? null : running ? "Wait for this run to end" : "Open this run's pipeline and start it from this app to run it again" }),
        running ? btn("Stop", "stop", () => plStop(r.run_id), "tool danger") : null)),
    h("section", { class: "card pad pl-flow", "aria-label": "Activity flow" }, flow.el),
    h("div", { class: "pl-run-body" },
      h("section", { class: "card pl-timeline", "aria-label": "Activity timeline" },
        h("div", { class: "pl-row head" }, h("span", {}, "Activity"), h("span", {}, "Status"), h("span", {}, "Time line"), h("span", { class: "n" }, "Took")),
        rows.length ? rows.map(row) : h("p", { class: "muted-text pad" }, "Nothing has started yet."),
        (r.problems || []).length ? h("ul", { class: "pl-notes pad" }, r.problems.map((x) => h("li", { class: "error-text" }, x.message))) : null,
        h("ul", { class: "pl-notes pad" }, (r.notes || []).map((n) => h("li", { class: "muted-text small" }, n)))),
      sel ? plActDetail(sel) : h("section", { class: "card pad pl-detail" }, h("p", { class: "muted-text" }, "Click an activity for its output and error.")))));
  // After the view is on the page: effects only play over elements that are there.
  flow.update(r);
  const pulse = plPulse === r.run_id;
  plPulse = null;
  if (pulse && running) forgeEvent("pipeline.started", document.querySelector(".pl-actions .tool.danger"));
}

function plActDetail(a) {
  const out = a.output === undefined ? null : a.output;
  return h("section", { class: "card pad pl-detail" },
    h("div", { class: "pl-detail-head" }, h("h2", {}, a.name), a.executed_by === "mock" ? h("span", { class: "pill-s neu" }, "Mocked") : plStatePill(a.state)),
    h("p", { class: "muted-text" }, [a.type, plLabel(a.label || ""), a.attempts > 1 ? `${a.attempts} attempts` : "", a.started ? plTook(a.seconds) : ""].filter(Boolean).join(" · ")),
    a.error ? h("div", {}, h("b", {}, "Error"), h("pre", { class: "pl-json" }, JSON.stringify(a.error, null, 2))) : null,
    h("div", {}, h("b", {}, "Output"), out === null ? h("p", { class: "muted-text" }, PL_RUNNING.has(a.state) ? "Appears when the step ends." : "None.")
      : h("pre", { class: "pl-json" }, JSON.stringify(out, null, 2))),
    h("p", { class: "muted-text small" }, "Secure inputs and outputs show as hidden, as in Fabric."));
}

async function plStop(id) {
  const out = await attempt(() => rpc("pipeline_run_cancel", { run_id: id }), "Could not stop the run");
  if (out) toast(out.cancelled ? "Stopping the run: nothing new starts and running steps stop." : "The run had already ended.");
}

registerFeature({
  id: "pipelines", preview: true,
  views: { pipelines: renderPipelines, pipelineRun: renderPipelineRun },
  commands: [
    { label: "Open a pipeline", icon: "flow", keywords: "pipeline fabric data factory run local datapipeline", run: () => pipelineOpenDialog() },
    { label: "Data Factory connections", icon: "flow", keywords: "connection gateway fabric data factory used by missing",
      run: () => { plPage.area = "connections"; go("pipelines"); } },
    { label: "Dataflows", icon: "flow", keywords: "dataflow gen2 power query m fabric data factory refresh",
      run: () => { plPage.area = "dataflows"; go("pipelines"); } },
  ],
});
