// Metadata framework screens: entities, layers, surrogate keys, source systems, runs, environments and promote.
// Every table is a file in the metadata project; these screens read and edit those files
// through the sidecar, which also runs the gates and the engine.

state.meta = {
  project: null,       // summary from meta_project
  projectTab: null,    // the Project tab last open, where Project opens again
  tab: "home",         // home | catalog | requests | overview | entities | layers | keys | sources | runs | health | model | tests | exceptions | environments | domains | products | links
  env: (() => { try { return localStorage.getItem("fd.env") || "dev"; } catch { return "dev"; } })(),  // set from the top bar
  entity: null,        // open entity detail
  layersDraft: null,   // unsaved layer edits
  runs: null,
  keyTable: null,      // selected key table on the Surrogate keys tab
  source: null,        // selected source system id, or "new"
  sourceEnv: "dev",    // which environment's connection the form shows
  sourceDraft: null,   // unsaved edits to the selected source system
};

const ENV_LABEL = { dev: "Dev", test: "Test", prod: "Prod" };
const LAYER_COLORS = ["#8a5a2b", "#5f6b78", "#b08900", "#5c2e91", "#0e6f8a", "#a3420f", "#3b7d23", "#2b3a8a", "#8a5300", "#6b3fa0"];
const DRIFT_LABEL = { evolve: "Keep new columns", ignore_new: "Ignore new columns", strict: "Stop on new columns" };
const MODE_LABEL = { full: "Full reload", incremental: "Incremental", scd1: "Type 1 dimension", scd2: "Type 2 dimension" };

function layerColor(name) {
  const layers = state.meta.project?.layers || [];
  const i = layers.findIndex((l) => l.name === name);
  return LAYER_COLORS[(i < 0 ? 0 : i) % LAYER_COLORS.length];
}

function layerChip(name) {
  return h("span", { class: "layer-chip", style: `background:${layerColor(name)}` }, name);
}

function statusPill(status) {
  const cls = { ok: "pass", failed: "fail", needs_decision: "wrn", skipped: "neu", unavailable: "neu", running: "info" }[status] || "neu";
  const label = { ok: "Succeeded", failed: "Failed", needs_decision: "Needs a decision", skipped: "Skipped", running: "Running" }[status] || status;
  return h("span", { class: `pill-s ${cls}` }, label);
}

function gateList(errors) {
  return h("ul", { class: "gate-list" }, errors.map((e) => h("li", {}, e)));
}

// Gate errors arrive as "Blocked by the save gate:\n- a\n- b".
function showGateError(err, title = "That was blocked") {
  const text = String(err);
  const lines = text.split("\n");
  if (lines.length > 1 && lines[0].startsWith("Blocked by")) {
    showDialog(title, h("div", { class: "scroll" }, h("p", { class: "text" }, lines[0]),
      gateList(lines.slice(1).map((l) => l.replace(/^- /, "")))), [btn("Close", null, closeDialog, "tool primary")]);
  } else {
    const f = friendlyError(text);
    toast(f.text, true, { title, details: f.details });
  }
}

async function metaCall(method, params, title, onError) {
  try {
    return await rpc(method, params);
  } catch (err) {
    onError?.(err);
    showGateError(err, title);
    return undefined;
  }
}

async function loadMetaProject() {
  const p = await metaCall("meta_project", {});
  if (p) state.meta.project = p;
  return p;
}

// Data mesh's tabs, in groups so new features join a group instead of a long row.
// [group id, label, [[tab id, label, preview feature id]], section]. The "mesh" section is the mesh
// for everyone: Home (mesh-home.js) as each person sees it, Products (platform.js) every team's products, Requests (mesh-requests.js) everyone's
// inbox, Domains (platform.js) the organization's domains with the Map and the open project's own
// domain and workspaces. The "project" section is the open project's editor, behind one Project tab.
const META_TAB_GROUPS = [
  ["home", "Home", [["home", "Home", "mesh"]], "mesh"],
  ["catalog", "Products", [["catalog", "Products", "mesh"]], "mesh"],
  ["requests", "Requests", [["requests", "Requests", "mesh"]], "mesh"],
  ["domains", "Domains", [["domains", "Domains", "mesh"], ["overview", "Map", "mesh"], ["workspaces", "This project", "mesh"]], "mesh"],
  ["design", "Design", [["entities", "Entities"], ["layers", "Layers"], ["keys", "Surrogate keys"], ["sources", "Source systems"]], "project"],
  ["runs", "Runs", [["runs", "Runs"]], "project"],
  ["quality", "Quality", [["tests", "Tests", "tests"], ["health", "Table health", "health"], ["exceptions", "Exceptions"]], "project"],
  ["publish", "Products and links", [["products", "Data products", "mesh"], ["links", "Links", "mesh"]], "project"],
  ["deploy", "Deploy", [["environments", "Environments and promote"], ["model", "Semantic model", "model"]], "project"],
];

// The tabs that show the whole mesh rather than the open project, so they show without one.
const META_NO_PROJECT = ["home", "catalog", "requests", "domains", "overview"];

// The Preview feature every tab in a group shares, so the group's tab carries the one tag; null when
// any tab in it isn't in preview or they differ.
function metaGroupPreview(tabs) {
  const ids = new Set(tabs.map(([, , previewId]) => previewId || null));
  const [id] = ids;
  return ids.size === 1 && id && PREVIEW_FEATURES[id] ? id : null;
}

function metaGroupOf(tab) {
  return META_TAB_GROUPS.find(([, , tabs]) => tabs.some(([id]) => id === tab)) || META_TAB_GROUPS[0];
}

function goMeta(tab) {
  state.meta.tab = tab || state.meta.tab;
  go("metadata");
}

// ---------- one layout for every tab ----------
// Every Data mesh tab is laid out the same way (Chris, 2026-10-27): the title with one context line and
// the tab's own buttons top right (the main one last), the tabs, any banner, then the view row (the
// tab's views as one switcher on the left, its search, filters and Refresh on the right) and the
// working area in white cards. The page draws the places; a tab fills its parts with meshChrome.
const meshSlots = {};

// The places a tab fills, made fresh with each page. Context starts as the tab's default line.
function meshSlotsFor(tab, context) {
  Object.assign(meshSlots, {
    context: h("p", { class: "sub mesh-context" }, ...[context ?? MESH_CONTEXT[tab] ?? ""].flat()),
    actions: h("div", { class: "head-actions" }),
    banners: h("div", { class: "mesh-banners" }),
    views: h("div", { class: "row tight mesh-views" }),
    tools: h("div", { class: "row tight mesh-tools" }),
  });
  return meshSlots;
}

// Fill a tab's parts of the page: context (its one line under the title), actions (its buttons, top
// right), banners, views (its switcher) and tools (search and filters, left of Refresh). A part left
// out stays as it is; null empties it.
function meshChrome(parts) {
  for (const [slot, nodes] of Object.entries(parts)) {
    if (nodes !== undefined && meshSlots[slot]?.isConnected) meshSlots[slot].replaceChildren(...[nodes].flat().filter(Boolean));
  }
}

// The one line under the title for each of the mesh's own tabs; Home says which project and domain.
const MESH_CONTEXT = {
  catalog: "Every team's data products, from the platform's catalog",
  requests: "Requests for data, access and changes, and who decides them",
  domains: "Your organization's domains", overview: "Your organization's domains", workspaces: "Your organization's domains",
};

function meshPage({ intro, context, actions, tabs, banners, groupSeg, body }) {
  const slots = meshSlotsFor(state.meta.tab, context);
  if (actions) slots.actions.replaceChildren(...actions.filter(Boolean));
  // Living Experience (home-hero.js, Preview): Chris's header art with the page's name on its sky. The
  // tab's line and its buttons (up to three, and they change by tab) stay under it, buttons at the right.
  const hero = typeof pageHero === "function" ? pageHero("datamesh", "Data mesh") : null;
  return h("div", { class: hero ? "page mesh-page living-head" : "page mesh-page" },
    hero ? [hero, h("div", { class: "page-head" }, h("div", {}, slots.context, intro), slots.actions)]
      : h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Data mesh"), slots.context, intro), slots.actions),
    ...tabs,
    ...banners, slots.banners,
    h("div", { class: "row mesh-view-row" }, groupSeg, slots.views, h("span", { class: "spacer" }), slots.tools,
      btn("Refresh", "sync", () => render(), "tool outline mesh-refresh")),
    body);
}

// ---------- main page ----------

async function renderMetadata() {
  const hero = typeof pageHero === "function" ? pageHero("datamesh", "Data mesh") : null;
  setView(h("div", { class: hero ? "page living-head" : "page" }, hero || h("h1", {}, "Data mesh"), h("p", { class: "muted-text" }, "Loading the metadata project...")));
  const p = await loadMetaProject();
  if (state.view !== "metadata") return;
  // The mesh's own tabs need no project, so they show even when the project couldn't be read.
  if (!p) return META_NO_PROJECT.includes(state.meta.tab) && previewShown() ? renderMetaNoProject({}) : undefined;
  if (!p.path) return META_NO_PROJECT.includes(state.meta.tab) && previewShown() ? renderMetaNoProject(p) : renderMetaSetup(p);
  renderEnvBar();  // the top bar's environment picker waits for the project
  const m = state.meta;
  // A hidden preview tab falls back to its group's first tab.
  const visible = (tabs) => tabs.filter(([, , previewId]) => !previewId || previewShown());
  if (!visible(metaGroupOf(m.tab)[2]).some(([id]) => id === m.tab)) m.tab = visible(metaGroupOf(m.tab)[2])[0]?.[0] || "entities";
  const group = metaGroupOf(m.tab);
  const inProject = group[3] === "project";
  if (inProject) m.projectTab = m.tab;  // Project opens where you left it
  const shownGroups = (section) => META_TAB_GROUPS.filter(([, , tabs, sec]) => sec === section && visible(tabs).length);
  const groupTab = ([id, label, tabs]) => h("button", { class: `tab${group[0] === id ? " active" : ""}`, role: "tab", "aria-selected": String(group[0] === id),
    onclick: () => goMeta(visible(tabs)[0][0]) },
    label, metaGroupPreview(tabs) ? previewTag() : null);
  const tabLabel = (label, previewId) => {
    const node = h("span", {}, label);
    return previewId && !metaGroupPreview(group[2]) ? preview(previewId, node) : node;
  };
  const body = h("div", { class: "meta-body" });
  // Policy exceptions about to end come from the platform, so their banner fills in when it answers.
  const policyEnding = h("div", { class: "gov-ending-slot" });
  setView(meshPage({
    context: inProject ? ["Every table is a file in ", h("span", { class: "mono" }, p.path),
      ". Metadata version ", h("span", { class: "mono" }, p.version), ". ", learnMore("data-mesh")] : undefined,
    intro: inProject ? null : pageIntro("Your organization's data products, the requests on them and who owns what. ", learnMore("data-mesh")),
    actions: inProject ? [
      btn("New entity", "add", newEntityDialog, "tool outline"),
      preview("schedules", btn("Schedule", "history", () => scheduleMetaRuns(m.env), "tool outline",
        { title: `Run every entity in ${ENV_LABEL[m.env]} on a schedule, on this PC or in Fabric` })),
      fabricMarked(inFabric(p, m.env), btn(inFabric(p, m.env) ? "Run all in Fabric" : "Run all", "play", () => runEntities(null), "tool primary",
        { disabled: p.errors.length ? true : null }))] : null,
    tabs: [h("div", { class: "subtabs meta-groups", role: "tablist", "aria-label": "Data mesh" }, shownGroups("mesh").map(groupTab),
      h("button", { class: `tab${inProject ? " active" : ""}`, role: "tab", "aria-selected": String(inProject), onclick: () => goMeta(m.projectTab || "entities") }, "Project")),
    inProject ? h("div", { class: "subtabs meta-project-groups", role: "tablist", "aria-label": "Project" }, shownGroups("project").map(groupTab)) : null],
    // Problems and ending exceptions show on every tab: runs and promote stay blocked until they're fixed.
    banners: [problemsBanner(p.errors), exceptionBanner(p), policyEnding],
    groupSeg: visible(group[2]).length > 1 ? h("div", { class: "seg meta-tabs", role: "group", "aria-label": group[1] }, visible(group[2]).map(([id, label, previewId]) =>
      h("button", { class: m.tab === id ? "on" : null, "aria-pressed": String(m.tab === id), onclick: () => goMeta(id) }, tabLabel(label, previewId)))) : null,
    body,
  }));
  const render = { home: renderMeshHomeTab, catalog: renderMeshCatalogTab, requests: renderMeshRequestsTab, overview: renderMeshOverviewTab, entities: renderEntitiesTab, layers: renderLayersTab, keys: renderKeysTab, sources: renderSourcesTab,
    runs: renderRunsTab, health: renderHealthTab, model: renderModelTab, tests: renderTestsTab, environments: renderEnvTab,
    domains: renderMeshOrgDomainsTab, workspaces: renderMeshDomainsTab, products: renderMeshProductsTab, links: renderMeshLinksTab,
    exceptions: renderExceptionsTab }[m.tab];
  raiseExceptionNotices(p);
  raisePolicyExceptionNotices(policyEnding);
  await render(body, p);
}

// With no metadata project open, the mesh's own tabs still show: a CDO or on-call engineer needs no
// project. The project's tabs wait for one.
function renderMetaNoProject(p) {
  const body = h("div", { class: "meta-body" });
  const tab = state.meta.tab;
  const shown = (tabs) => tabs.filter(([id]) => META_NO_PROJECT.includes(id));
  const groups = META_TAB_GROUPS.filter(([, , tabs]) => shown(tabs).length);
  const group = metaGroupOf(tab);
  setView(meshPage({
    intro: pageIntro("Your organization's data products, the requests on them and who owns what. ", learnMore("data-mesh")),
    // Set up a project is the page's own button, before the tab's so the tab's primary stays last.
    actions: null,
    tabs: [h("div", { class: "subtabs meta-groups", role: "tablist", "aria-label": "Data mesh" }, groups.map(([id, label, tabs]) =>
      h("button", { class: `tab${group[0] === id ? " active" : ""}`, role: "tab", "aria-selected": String(group[0] === id), onclick: () => goMeta(shown(tabs)[0][0]) },
        label, metaGroupPreview(tabs) ? previewTag() : null)))],
    banners: [],
    groupSeg: shown(group[2]).length > 1 ? h("div", { class: "seg meta-tabs", role: "group", "aria-label": group[1] }, shown(group[2]).map(([id, label]) =>
      h("button", { class: tab === id ? "on" : null, "aria-pressed": String(tab === id), onclick: () => goMeta(id) }, label))) : null,
    body,
  }));
  if (p.presets) {
    const wrap = h("div", { class: "head-actions" });
    meshSlots.actions.replaceWith(wrap);
    wrap.append(btn("Set up a project", "add", () => renderMetaSetup(p), "tool outline"), meshSlots.actions);
  }
  return { home: renderMeshHomeTab, catalog: renderMeshCatalogTab, requests: renderMeshRequestsTab, domains: renderMeshOrgDomainsTab,
    overview: renderMeshOverviewTab }[tab](body, p);
}

// Where an environment's runs execute: "local" (this PC) or "fabric" (the workspace's capacity).
function inFabric(p, env) {
  return (p.environments?.[env] || {}).compute === "fabric";
}

function problemsBanner(errors) {
  if (!errors.length) return null;
  return h("div", { class: "problems" },
    h("div", { class: "row" }, icon("warning", 18),
      h("b", {}, `${errors.length} problem${errors.length === 1 ? "" : "s"} to fix`),
      h("span", { class: "muted-text" }, "Runs and promote stay blocked until the project passes the save gate.")),
    gateList(errors));
}

const RULE_LABEL = {
  owner: "Needs an owner", custom_code_owner: "Custom code needs an owner",
  custom_code_test: "Custom code needs a test", key_not_null: "Key can't be empty",
  key_unique: "Key must be unique", surrogate_keys: "Surrogate keys",
};
const EXCEPTION_STATUS = {
  active: ["pass", "In force"], permanent: ["neu", "No end date"], expiring: ["wrn", "About to end"],
  expired: ["fail", "Ended"], invalid: ["fail", "Check the date"],
};

// One activity item when an exception is about to end, has ended, or its date doesn't parse.
// The id includes the day, so the bell shows it once a day rather than on every refresh.
function exceptionNotice(row, day) {
  if (!row || (row.status !== "expiring" && row.status !== "expired" && row.status !== "invalid")) return null;
  const expiring = row.status === "expiring";
  return {
    id: `exception:${row.entity}:${row.rule}:${row.expires}:${day}`,
    status: expiring ? "info" : "failed",
    notice: expiring,
    title: expiring ? "An approved exception is about to end" : "An approved exception needs attention",
    text: row.notice,
  };
}

function raiseExceptionNotices(p) {
  const day = new Date().toISOString().slice(0, 10);
  for (const row of p.exception_notices || []) {
    const item = exceptionNotice(row, day);
    if (item) logActivity(item);
  }
}

function exceptionBanner(p) {
  const rows = (p.exception_notices || []).filter((r) => r.status === "expiring");
  if (!rows.length) return null;
  return h("div", { class: "problems" },
    h("div", { class: "row" }, icon("warning", 18),
      h("b", {}, `${rows.length} approved exception${rows.length === 1 ? " is" : "s are"} about to end`),
      h("span", { class: "spacer" }),
      btn("Review", null, () => goMeta("exceptions"), "tool outline")),
    h("ul", { class: "gate-list" }, rows.map((r) => h("li", {}, r.notice))));
}

function renderExceptionsTab(body, p) {
  const rows = p.exception_notices || [];
  if (!rows.length) {
    body.replaceChildren(emptyState({ title: "No approved exceptions",
      text: ["When a rule genuinely doesn't fit, add it to exceptions.toml with the table, the rule, why, who approved it and when it ends. One that is about to end shows here and in the bell. ",
        learnMore("approved-exceptions")] }));
    return;
  }
  body.replaceChildren(
    h("p", { class: "muted-text" }, "Approved exceptions to a save-gate rule. The owner is the table's owner. ", learnMore("approved-exceptions")),
    h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Table", "Rule", "Owner", "Reason", "Approver", "Ends", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows.map((r) => {
        const [cls, label] = EXCEPTION_STATUS[r.status] || ["neu", r.status];
        return h("tr", {},
          h("td", {}, h("span", { class: "mono" }, r.entity || "—")),
          h("td", {}, RULE_LABEL[r.rule] || r.rule || "—"),
          h("td", {}, r.owner || h("span", { class: "muted-text" }, "No owner")),
          h("td", {}, r.reason || h("span", { class: "muted-text" }, "No reason")),
          h("td", {}, r.approver || h("span", { class: "muted-text" }, "No approver")),
          h("td", {}, r.expires === "permanent" ? "No end date" : r.expires),
          h("td", {}, h("span", { class: `pill-s ${cls}` }, label)));
      }))));
}

function renderMetaSetup(p) {
  let preset = "3";
  const path = h("input", { id: "meta-path", value: p.default_path, "aria-label": "Folder" });
  const sample = h("input", { type: "checkbox", id: "meta-sample", checked: true });
  const presetButtons = Object.entries(p.presets).map(([id, layers]) => h("button", {
    class: `choice${id === preset ? " selected" : ""}`, "data-preset": id,
    onclick: (e) => {
      preset = id;
      e.currentTarget.parentElement.querySelectorAll(".choice").forEach((c) => c.classList.toggle("selected", c.dataset.preset === id));
      sample.disabled = id !== "3";
    } },
  h("span", { class: "main" }, h("span", { class: "n" }, `${id} layers`),
    h("span", { class: "w" }, layers.map((l) => l.name).join(" → ")))));
  const openPath = h("input", { id: "meta-open-path", placeholder: "C:\\Users\\you\\KratosForge\\metadata", "aria-label": "Folder with a layers.toml" });
  setView(h("div", { class: "page narrow" },
    h("h1", {}, "Set up a metadata project"),
    pageIntro("A metadata project is a folder you keep in Git. Each table is one file that says where its data comes from, how to clean and check it, and how to load it. Each layer gets its own lakehouse, so layers never mix."),
    h("div", { class: "card pad" },
      h("h2", { class: "flush" }, "Create a new project"),
      h("label", { class: "field" }, "Folder", folderField(path, "the new project's folder")),
      h("div", { class: "field" }, "Layers (you can add, remove and reorder them later)", h("div", { class: "preset-row" }, presetButtons)),
      h("label", { class: "check" }, sample, "Add the sample tables (customers and orders, Bronze to Gold) so you can try it"),
      h("div", { class: "row end" }, btn("Create project", "add", async () => {
        const res = await metaCall("meta_init", { path: path.value.trim(), preset, sample: sample.checked && preset === "3" });
        if (res) { state.meta.project = res; toast("Project created"); render(); }
      }, "tool primary"))),
    h("div", { class: "card pad" },
      h("h2", { class: "flush" }, "Or open an existing project"),
      h("label", { class: "field" }, "Folder with a layers.toml", folderField(openPath, "the project's folder")),
      h("div", { class: "row end" }, btn("Open", "folderOpen", async () => {
        const res = await metaCall("meta_open", { path: openPath.value.trim() });
        if (res) { state.meta.project = res; render(); }
      }, "tool outline")))));
}

// ---------- entities ----------

function lastRuns(runs) {
  const last = {};
  for (const r of runs || []) if (!last[r.entity]) last[r.entity] = r;
  return last;
}

async function renderEntitiesTab(body, p) {
  const runs = await attempt(() => rpc("meta_runs", { env: state.meta.env, limit: 300 })) || [];
  const last = lastRuns(runs);
  const flow = h("div", { class: "flow" }, p.layers.map((l, i) => [i ? h("span", { class: "arrow", "aria-hidden": "true" }, "→") : null,
    h("span", { class: "layer-chip big", style: `background:${layerColor(l.name)}` }, l.name,
      h("span", { class: "count" }, l.entities))]));
  const shown = p.entities.filter((e) => matches(`${e.name} ${e.owner} ${e.source}`));
  body.replaceChildren(flow, shown.length ? h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["Entity", "Layer", "Source", "Load", "Rules", "Custom code", `Last run (${ENV_LABEL[state.meta.env]})`, "Owner"]
      .map((c) => h("th", {}, c)))),
    h("tbody", {}, shown.map((e) => {
      const r = last[e.name];
      const rules = [e.clean && `${e.clean} clean`, e.derive && `${e.derive} derive`, `${e.checks} check${e.checks === 1 ? "" : "s"}`].filter(Boolean).join(" · ");
      return h("tr", { class: "click", onclick: () => openEntity(e.name) },
        h("td", {}, h("span", { class: "item" }, icon("table", 18), h("span", { class: "mono" }, e.name))),
        h("td", {}, layerChip(e.layer)),
        h("td", { class: "mono small" }, e.source),
        h("td", {}, MODE_LABEL[e.mode] || e.mode, e.watermark ? h("span", { class: "muted-text" }, ` · ${e.watermark}`) : null),
        h("td", {}, rules),
        h("td", {}, e.custom.length ? e.custom.map((c) => h("span", { class: "pill-s cus" }, c)) : h("span", { class: "muted-text" }, "none")),
        h("td", {}, r ? [statusPill(r.status), h("span", { class: "muted-text" }, " ", when(r.started_at))]
          : h("span", { class: "muted-text" }, "never")),
        h("td", {}, e.owner));
    }))) : emptyState({ title: "No entities yet",
    text: "Add one from a template. It starts out meeting your layer's standards.",
    action: btn("New entity", "add", newEntityDialog, "tool primary") }));
}

function newEntityDialog(preset) {
  const p = state.meta.project;
  const template = h("select", { id: "ne-template" }, Object.entries(p.templates).map(([id, d]) => h("option", { value: id }, d)));
  const layer = h("select", { id: "ne-layer" }, p.layers.map((l) => h("option", { value: l.name }, `${l.name} (${l.lakehouse})`)));
  const table = h("input", { id: "ne-table", placeholder: "orders" });
  const source = h("input", { id: "ne-source", list: "ne-entities", placeholder: "landing/orders/*.csv" });
  const keys = h("input", { id: "ne-keys", placeholder: "order_id" });
  const list = h("datalist", { id: "ne-entities" }, p.entities.map((e) => h("option", { value: e.name })));
  const hint = h("p", { class: "text" });
  // From the landing zone: pick a source and entity the central team lands, instead of typing a path.
  const landingSource = h("select", { "aria-label": "Landing zone source" });
  const landingEntity = h("select", { "aria-label": "Landing zone entity" });
  const landingPick = h("div", { class: "row", hidden: true },
    h("label", { class: "field grow" }, "Landing zone source", landingSource), h("label", { class: "field grow" }, "Entity", landingEntity));
  let landing = null;
  const fillLanding = () => {
    const src = (landing?.sources || []).find((x) => x.name === landingSource.value);
    landingEntity.replaceChildren(...(src?.entities || []).filter((e) => e.name).map((e) => h("option", { value: e.name }, `${e.name} (${e.files} file${e.files === 1 ? "" : "s"})`)));
    const ent = landingEntity.value;
    if (src && ent) {
      source.value = `${src.name}/${ent}/*.csv`;
      table.value = `${src.name}_${ent}`;
    }
  };
  landingSource.addEventListener("change", fillLanding);
  landingEntity.addEventListener("change", () => {
    source.value = `${landingSource.value}/${landingEntity.value}/*.csv`;
    table.value = `${landingSource.value}_${landingEntity.value}`;
  });
  const loadLanding = async () => {
    landing = landing || await attempt(() => rpc("platform_landing", { env: "dev" }), "Could not read the landing zone");
    const sources = (landing?.sources || []).filter((x) => x.entities.some((e) => e.name));
    landingSource.replaceChildren(...sources.map((x) => h("option", { value: x.name }, x.title && x.title !== x.name ? `${x.name} (${x.title})` : x.name)));
    if (!sources.length) hint.textContent = "Nothing has landed yet. Type the path, like sap/customer/*.csv, or ask your central team to land the source.";
    fillLanding();
  };
  const sync = () => {
    const t = template.value;
    landingPick.hidden = t !== "landing";
    source.placeholder = t === "files" ? "landing/orders/*.csv (under this layer's lakehouse Files)"
      : t === "landing" ? "sap/customer/*.csv (in the platform's LH_Landing)" : "an entity, like silver.orders";
    hint.textContent = t === "files" ? "Columns are read from the first matching file in Dev, if there is one."
      : t === "landing" ? "Reads the raw files your central team lands for everyone. Columns come from the newest file in Dev's landing zone."
        : t === "custom" ? "Creates transforms/<table>.py and a test for it. The source is optional."
          : "Columns are copied from the source entity's contract.";
    if (["dim1", "dim2", "fact"].includes(t) && !table.value) table.placeholder = t === "fact" ? "fact_sales" : "dim_customer";
    if (t === "landing") { layer.value = p.layers[0]?.name || layer.value; loadLanding(); }
  };
  template.addEventListener("change", sync);
  if (preset === "landing" && p.templates.landing) template.value = "landing";
  sync();
  showDialog("New entity", h("div", { class: "scroll" },
    h("label", { class: "field" }, "Template", template),
    landingPick,
    h("div", { class: "row" }, h("label", { class: "field grow" }, "Layer", layer), h("label", { class: "field grow" }, "Table name", table)),
    h("label", { class: "field" }, "Source", source, list),
    h("label", { class: "field" }, "Key columns (comma separated)", keys),
    hint), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Create", null, async () => {
      if (!table.value.trim()) return toast("Give the table a name", true);
      const res = await metaCall("meta_new_entity", { template: template.value, name: `${layer.value}.${table.value.trim()}`,
        source: source.value.trim(), keys: keys.value.split(",").map((k) => k.trim()).filter(Boolean) }, "Couldn't create the entity");
      if (res) { closeDialog(); toast(`Created ${res.name}`); openEntity(res.name); }
    }, "tool primary"),
  ]);
}

// ---------- entity detail ----------

async function openEntity(name) {
  state.meta.entity = { name, loading: true };
  go("entity");
}

function stepsFor(e) {
  const steps = [["Read", "framework"], ["Contract", "framework"]];
  if (e.surrogate_keys?.length) steps.push([`Surrogate keys · ${e.surrogate_keys.length}`, "framework"]);
  if (e.clean) steps.push([`Clean · ${e.clean}`, "framework"]);
  if (e.derive) steps.push([`Derive · ${e.derive}`, "expression"]);
  for (const c of e.custom) steps.push([c, "your code"]);
  steps.push([`Checks · ${e.checks}`, "framework"], [`Write · ${MODE_LABEL[e.mode] || e.mode}`, "framework"], ["Log", "framework"]);
  return steps;
}

async function renderEntity() {
  const name = state.meta.entity?.name;
  setView(h("div", { class: "page" }, h("p", { class: "muted-text" }, `Loading ${name}...`)));
  const e = await metaCall("meta_entity", { name, env: state.meta.env });
  if (state.view !== "entity" || !e) return;
  state.meta.entity = e;
  if (!state.meta.project) { await loadMetaProject(); renderEnvBar(); }
  const editor = h("textarea", { class: "toml", id: "entity-toml", spellcheck: "false", "aria-label": "Entity file (TOML)",
    "aria-description": "Tab types four spaces. Press Esc, then Tab, to move on; Shift+Tab always goes back.",
    "data-esc-tab": "" });  // the accessibility audit leaves it with Esc, then Tab
  editor.value = e.text;
  const dirty = () => editor.value !== e.text;
  const errBox = h("div", {}, e.errors.length ? h("div", { class: "problems" }, h("b", {}, "This entity doesn't pass the save gate yet"), gateList(e.errors)) : null);

  const save = async () => {
    if (previewShown() && !(await confirmContractChange(e.name, editor.value))) return;
    const res = await metaCall("meta_save_entity", { name: e.name, text: editor.value }, "Not saved");
    if (!res) return;
    toast(res.errors.length ? `Saved with ${res.errors.length} problem${res.errors.length === 1 ? "" : "s"} to fix` : "Saved. It passes the save gate.");
    if (res.name !== e.name) state.meta.entity = { name: res.name };
    render();
  };
  // Tab indents; Esc, then Tab, moves focus on, as in the code editors, so Tab never traps focus here (#928).
  let leaving = false;
  editor.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === "s") { ev.preventDefault(); save(); }
    if (ev.key === "Escape") { leaving = true; return; }
    if (ev.key === "Tab" && !ev.shiftKey && !leaving) { ev.preventDefault(); editor.setRangeText("    ", editor.selectionStart, editor.selectionEnd, "end"); }
    if (ev.key !== "Shift") leaving = false;
  });
  editor.addEventListener("blur", () => { leaving = false; });
  editor.addEventListener("mousedown", () => { leaving = false; });  // a click after Esc goes back to typing

  const cols = Object.entries(e.columns).map(([c, t]) => h("div", { class: "col-row" },
    h("span", { class: "mono" }, c), h("span", { class: "muted-text mono" }, t),
    e.keys.includes(c) ? h("span", { class: "pill-s neu" }, "key") : null,
    (e.surrogate_keys || []).some((k) => k.column === c) ? h("span", { class: "pill-s info" }, "LH_Keys") : null,
    e.derived.includes(c) ? h("span", { class: "pill-s pass" }, "derived") : null,
    e.hook_adds.includes(c) ? h("span", { class: "pill-s cus" }, "hook") : null));

  // Runs of this table in every environment; the current one is the fallback while they load.
  const runLines = (list) => list.length ? list.slice(0, 6).map((r) => runLine(r, () => showRun(r.run_id, r.env)))
    : [h("p", { class: "muted-text" }, e.runs?.[0]?.message || "Not run yet.")];
  const recentRuns = h("div", { class: "card pad" },
    h("div", { class: "row" }, h("b", { class: "grow" }, "Recent runs"), btn("All in Monitor", null, () => go("monitor"), "tool link small")),
    ...runLines((e.runs || []).filter((r) => r.run_id).map((r) => ({ ...r, env: r.env || state.meta.env }))));
  rpc("meta_runs_all", { limit: 300 }).then((all) => {
    const mine = (all.runs || []).filter((r) => r.entity === e.name);
    if (mine.length && recentRuns.isConnected) recentRuns.replaceChildren(recentRuns.firstChild, ...runLines(mine));
  }).catch(() => {});
  setView(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", {},
        h("div", { class: "row" }, h("button", { class: "tool link", onclick: () => goMeta("entities") }, "Data mesh"), h("span", { class: "muted-text" }, "/")),
        h("h1", { class: "row" }, h("span", { class: "mono" }, e.name), layerChip(e.layer)),
        h("p", { class: "sub" }, `${MODE_LABEL[e.mode] || e.mode}${e.watermark ? ` on ${e.watermark}` : ""} · owner ${e.owner || "not set"} · `,
          h("span", { class: "mono" }, e.path), " ", learnMore("data-mesh"))),
      h("div", { class: "head-actions" },
        btn("Delete", "delete", async () => {
          if (!(await confirmDialog(`Delete ${e.name}?`, "The entity file is removed. Its Delta table stays in the lakehouse until you delete it there.", "Delete", { danger: true }))) return;
          const res = await metaCall("meta_delete_entity", { name: e.name }, "Not deleted");
          if (res) { toast(`Deleted ${e.name}`); goMeta("entities"); }
        }, "tool outline"),
        btn("Profile", "dataBarVertical", () => profileEntity(e.name), "tool outline"),
        btn("Compare", "flow", () => compareDialog(e.name), "tool outline"),
        preview("history", btn("History", "history", () => historyDialog(e.name), "tool outline", { title: "Every version of the table; restore an earlier one" })),
        btn("Rebuild with downstream", "sync", () => runEntities([e.name], { cascade: true }), "tool outline"),
        fabricMarked(inFabric(state.meta.project, state.meta.env), btn(inFabric(state.meta.project, state.meta.env) ? `Run in Fabric (${ENV_LABEL[state.meta.env]})` : `Run in ${ENV_LABEL[state.meta.env]}`, "play",
          () => (dirty() ? save() : Promise.resolve()).then(() => runEntities([e.name])), "tool primary")))),
    h("div", { class: "card pad" },
      h("div", { class: "row" }, h("b", {}, "What happens on every run"), h("span", { class: "muted-text" }, "Grey steps are the framework. Purple is your code, at a fixed point.")),
      h("div", { class: "steps" }, stepsFor(e).map(([label, kind], i) => [i ? h("span", { class: "arrow", "aria-hidden": "true" }, "→") : null,
        h("span", { class: `step ${kind === "your code" ? "custom" : kind === "expression" ? "expr" : ""}` },
          h("span", { class: "n" }, label), h("span", { class: "k" }, kind))]))),
    h("div", { class: "entity-grid" },
      h("div", { class: "card pad grow" },
        h("div", { class: "row" }, h("b", {}, "Config"), h("span", { class: "muted-text" }, `metadata/entities/${e.name}.toml`),
          h("span", { class: "spacer" }), btn("Save", "save", save, "tool primary small")),
        errBox, editor),
      h("div", { class: "side" },
        h("div", { class: "card pad" }, h("b", {}, "Contract"), h("div", { class: "cols" }, cols)),
        h("div", { class: "card pad" }, h("div", { class: "row wrap lineage-head" }, h("b", { class: "grow" }, "Lineage"),
          preview("lineage", btn("Columns", "flow", () => columnLineage(e.name), "tool link small")),
          preview("lineage", btn("Impact", null, () => impactDialog(e.name), "tool link small", { title: "What a change to one of its columns would break" }))),
          h("div", { class: "lineage3" },
            h("div", {}, h("span", { class: "muted-text small" }, "Reads"),
              e.reads.length ? e.reads.map((r) => h("button", { class: "node", onclick: () => openEntity(r) }, entityName(r)))
                : h("span", { class: "node plain" }, e.source || "no source"),
              shortcutLineage(e)),
            h("div", {}, h("span", { class: "muted-text small" }, "This table"), h("span", { class: "node self" }, entityName(e.name))),
            h("div", {}, h("span", { class: "muted-text small" }, "Feeds"),
              e.impact.length ? e.impact.map((r) => h("button", { class: "node", onclick: () => openEntity(r) }, entityName(r)))
                : h("span", { class: "muted-text small" }, "Nothing reads it yet")))),
        driftCard(e),
        freshnessCard(e),
        recentRuns)),
    aboutCard(e),
    quarantineCard(e),
    keysCard(e),
    e.preview ? h("div", { class: "card pad" }, h("b", {}, `Data in ${ENV_LABEL[state.meta.env]} · ${e.preview.total} rows`), dataTable(e.preview)) : null));
}

// The MERGE that starts this entity's template notebook.
function keysCard(e) {
  if (!e.surrogate_keys?.length) return null;
  return h("div", { class: "card pad" },
    h("div", { class: "row wrap" }, h("b", {}, "Cell 1: surrogate keys"),
      h("span", { class: "muted-text" }, e.surrogate_keys.map((k) => `${k.column} from sk.${k.table} on ${k.business_key.join(" + ")}`).join(" · ")),
      h("span", { class: "spacer" }),
      e.keys_sql ? btn("Copy", null, () => copyText(e.keys_sql, "Key SQL"), "tool outline small") : null,
      e.keys_sql ? btn("Create template notebook", "notebook", async () => {
        const res = await metaCall("meta_key_notebook", { name: e.name, env: state.meta.env }, "No notebook created");
        if (res) { toast(`Created ${res.name}`); openNotebook(res.name); }
      }, "tool primary small") : null),
    e.keys_sql ? h("pre", { class: "sql-block" }, e.keys_sql)
      : e.keys_from ? h("p", { class: "muted-text" }, `Keys come from ${e.keys_from}, so there's no MERGE into LH_Keys to copy. Each run of this entity takes its keys from ${e.keys_from}.`)
      : h("p", { class: "muted-text" }, "Fix the problems above to see the MERGE."));
}

// opts.danger: the action deletes or overwrites data, so it is drawn red and Cancel has focus (UX-018).
// opts.typeToConfirm: the action stays off until exactly this text is typed, for the worst overwrites.
// opts.typeLabel says what to type when "Type <text> to confirm" isn't the right words.
// opts.items: a list under the text, such as the reasons for the confirm.
// opts.reason: { label, placeholder, max } asks why in a required box; the dialog then resolves with
// the trimmed text instead of true.
// Escape, the close button and a click outside count as Cancel.
function confirmDialog(title, text, okLabel = "OK", opts = {}) {
  return new Promise((resolve) => {
    let settled = false;
    let why = null;
    const done = (v) => { if (settled) return; settled = true; closeDialog(); resolve(v && why ? why.value.trim() : v); };
    const cancel = btn(opts.cancelLabel || "Cancel", null, () => done(false), "tool outline");
    const ok = btn(okLabel, null, () => done(true), opts.danger ? "tool danger-solid" : "tool primary");
    let typed = null;
    if (opts.reason) {
      ok.disabled = true;
      why = h("textarea", { rows: 3, maxlength: opts.reason.max || 500, placeholder: opts.reason.placeholder || "",
        "aria-label": opts.reason.label || "Reason", oninput: (e) => { ok.disabled = !e.target.value.trim(); } });
      typed = h("label", { class: "field" }, opts.reason.label || "Reason", why);
    }
    if (opts.typeToConfirm) {
      ok.disabled = true;
      typed = h("label", { class: "field" }, opts.typeLabel || `Type ${opts.typeToConfirm} to confirm`,
        h("input", { autocomplete: "off", spellcheck: "false",
          oninput: (e) => { ok.disabled = e.target.value.trim() !== opts.typeToConfirm; },
          onkeydown: (e) => { if (e.key === "Enter" && !ok.disabled) done(true); } }));
    }
    // opts.after: a closing sentence under the list, such as what can't be undone.
    showDialog(title, h("div", {}, h("p", { class: "text" }, text), opts.items?.length ? gateList(opts.items) : null,
      opts.after ? h("p", { class: "text" }, opts.after) : null, typed), [cancel, ok],
      () => { if (!settled) { settled = true; resolve(false); } });
    if (opts.danger && (!typed || why)) cancel.focus();
  });
}

function dataTable(preview) {
  return h("div", { class: "output" }, h("table", {},
    h("thead", {}, h("tr", {}, preview.columns.map((c) => h("th", {}, c)))),
    h("tbody", {}, preview.rows.slice(0, 50).map((r) => h("tr", {}, preview.columns.map((c) => h("td", {}, r[c] === null ? "null" : String(r[c]))))))));
}

// ---------- running ----------

async function runEntities(names, opts = {}) {
  // The button that started it, for the Pulse once the sign-in and risk check let the run start (Run all on the page head otherwise).
  const trigger = document.activeElement?.closest?.("button");
  const p = state.meta.project;
  let label = names ? names.join(", ") : `all ${p.entities.length} entities`;
  const env = state.meta.env;
  if (inFabric(p, env) && !state.user && !(await signIn())) return;
  const go = await metaRiskGate(p, names, env, label);
  if (!go) return;
  const only = go === true ? names : go;
  if (only !== names) label = only.join(", ");  // only those that can start
  if (inFabric(p, env)) {
    toast(`Sending ${label} to Fabric for ${ENV_LABEL[env]}...`);
  } else {
    toast(`Running ${label} in ${ENV_LABEL[env]}...`);
  }
  forgeEvent("mesh.run.started", trigger?.isConnected ? trigger : document.querySelector(".page-head .head-actions .tool.primary"));
  const results = await metaCall("meta_run", { names: only, env, backfill: opts.backfill || null, cascade: !!opts.cascade }, "Run blocked");
  if (!results) return;
  if (results[0]?.status === "submitted") return watchMetaFabricRun(results[0], only ? label : "Run all");
  const decision = results.find((r) => r.status === "needs_decision");
  logRunActivity(`${only ? label : "Run all"} · ${ENV_LABEL[env]}`, results, "on this PC", env);
  showRunResults(results, decision);
  metaRunFx(results, metaResultsHead());
  render();
}

// One effect for the whole batch (motion.js), never one per table: the Anvil Strike when every table
// succeeded, the ember when any failed. A batch waiting on a decision plays nothing; so does a stop.
function metaRunFx(results, anchor) {
  if (results.some((r) => r.status === "failed")) forgeEvent("mesh.run.failed", anchor);
  else if (results.length && results.every((r) => r.status === "ok")) forgeEvent("mesh.run.succeeded", anchor);
}

// The results dialog's heading ("3 of 3 succeeded"), where the batch's effect plays.
function metaResultsHead() {
  return document.querySelector("#dialog-root .dialog-head");
}

// The risk check (FINOPS-004) for the entities in the run, read in one batch, shown as one dialog
// when anything should stop or slow it. Resolves true to run as asked, a list of names to run only
// those (the rest are blocked, or read from one that is), or false. When nothing can start there is
// no way to run.
async function metaRiskGate(p, names, env, label) {
  const wid = (n) => `meta:${p.project?.name || "project"}:${n}`;
  const gate = await rpc("meta_risk_gate", { names, env }).catch(() => null);
  if (!gate) return true;
  const found = gate.checks.filter((c) => c.result === "warning" || c.result === "blocked").map((c) => [c.entity, c]);
  const held = Object.entries(gate.held || {});
  if (!found.length && !held.length) return true;
  const stopped = gate.checks.length - gate.run.length;
  const none = !gate.run.length;
  return new Promise((resolve) => {
    const boxes = new Map(found.map(([n, r]) => [n, riskAckBox(r)]));
    const go_ = async () => {
      for (const [n, r] of found.filter(([n]) => gate.run.includes(n))) {
        if (!(await riskProceed({ id: wid(n), action: "run", env }, r, boxes.get(n)))) return;
      }
      closeDialog();
      resolve(stopped ? gate.run : true);
    };
    showDialog(`Run ${label} in ${ENV_LABEL[env]}?`, h("div", { class: "wl-confirm" },
      found.map(([n, r]) => [gate.checks.length > 1 ? h("h3", {}, n) : null, riskPanel(r, { box: boxes.get(n) })]),
      held.length ? h("p", { class: "text" }, `Waiting because they read from one that can't start: ${held.map(([n, ups]) => `${n} (reads ${ups.join(", ")})`).join("; ")}.`) : null,
      stopped && !none ? h("p", { class: "text" }, `${stopped} of ${gate.checks.length} won't start; the other ${gate.run.length} run if you go ahead.`) : null,
      h("p", { class: "muted-text small" }, (found[0]?.[1] || gate.checks[0])?.note || "")),
    none
      ? [btn("Close", null, () => { closeDialog(); resolve(false); }, "tool primary")]
      : [btn("Cancel", null, () => { closeDialog(); resolve(false); }, "tool outline"),
        btn(stopped ? `Run the other ${gate.run.length}` : "Run anyway", null, go_, "tool primary")]);
  });
}

// One tray entry for a finished batch: how many succeeded, and the results to reopen.
function logRunActivity(title, results, where, env, id) {
  const ok = results.filter((r) => r.status === "ok").length;
  const bad = results.find((r) => r.status === "failed" || r.status === "needs_decision");
  return logActivity({ id, env, title, status: bad ? "failed" : "ok", results, monitor: true,
    text: `${ok} of ${results.length} succeeded ${where}.${bad ? ` ${bad.entity}: ${runReason(bad)}` : ""}`, at: new Date().toISOString() });
}

// A run on Fabric capacity: say it started, then show the results when the runner has saved them.
async function watchMetaFabricRun(run, label) {
  const id = `fabric:${run.job_id}`;
  const title = `${label} · ${ENV_LABEL[run.env]}`;
  logActivity({ id, env: run.env, request: run.request_id, status: "running", title, text: "In Fabric. You can leave this page; the bell follows it.", monitor: true });
  toast(`${ENV_LABEL[run.env]} is running in Fabric. The bell follows it until it finishes.`);
  render();
  for (let wait = 10; ; wait = Math.min(wait * 1.5, 60)) {
    await new Promise((r) => setTimeout(r, wait * 1000));
    const res = await attempt(() => rpc("meta_fabric_result", { request_id: run.request_id }), "Couldn't follow the Fabric run");
    if (!res) {  // the bell would otherwise show it running for good; how it ended isn't known, so not "failed"
      logActivity({ id, status: "info", text: "The app stopped following this run. Monitor shows how it ended." });
      return;
    }
    if (!["Completed", "Failed", "Cancelled", "Deduped"].includes(res.status)) continue;
    const here = state.view === "metadata" || state.view === "entity";
    if (res.results) {
      logRunActivity(title, res.results, "in Fabric", run.env, id);
      if (here) showRunResults(res.results, res.results.find((r) => r.status === "needs_decision"));
      else toast(`${title} finished in Fabric. The bell has the results.`, false);
      metaRunFx(res.results, here ? metaResultsHead() : document.getElementById("toast"));
    } else {
      logActivity({ id, status: "failed", text: res.message || `The run ${res.status.toLowerCase()} before it saved any results.` });
      if (here) showDialog(`The Fabric run ${res.status.toLowerCase()}`, h("div", { class: "scroll" },
        h("p", { class: "text" }, res.message || "The runner stopped before it saved any results."),
        h("p", { class: "muted-text" }, "Open it from Monitor to see the notebook's output in Fabric.")),
      [btn("Close", null, closeDialog, "tool primary")]);
      if (here && res.status === "Failed") forgeEvent("mesh.run.failed", metaResultsHead());
    }
    if (here) render();
    return;
  }
}

function showRunResults(results, decision) {
  const rows = results.map((r) => h("div", { class: "result-row" },
    statusPill(r.status), h("span", { class: "mono" }, r.entity),
    h("span", { class: "muted-text" }, r.status === "ok" ? `${r.rows_written} rows written${r.quarantined ? `, ${r.quarantined} quarantined` : ""}`
      : r.status === "needs_decision" ? "Waiting for your choice below" : r.message),
    r.maintenance === "queued" ? h("span", { class: "pill-s info", title: "Fabric's table maintenance job: OPTIMIZE with V-Order, then VACUUM" }, "Optimize queued") : null,
    r.maintenance === "Failed" ? h("span", { class: "pill-s wrn", title: r.maintenance_message }, "Optimize not started") : null,
    r.maintenance === "skipped" ? h("span", { class: "pill-s wrn", title: r.maintenance_message }, "Not optimized (capacity warning)") : null,
    r.run_id && r.status !== "needs_decision" ? btn("Details", null, () => showRun(r.run_id), "tool link small") : null));
  const actions = [btn("Close", null, closeDialog, decision ? "tool outline" : "tool primary")];
  let body = h("div", { class: "scroll" }, rows);
  if (decision) {
    const opts = decision.options;
    body = h("div", { class: "scroll" }, rows, h("div", { class: "decision" },
      h("b", {}, `${decision.entity} needs a decision`), h("p", { class: "text" }, decision.message),
      opts.includes("none") ? h("button", { class: "choice", onclick: () => { closeDialog(); runEntities([decision.entity], { backfill: "none" }); } },
        h("span", { class: "main" }, h("span", { class: "n" }, "Apply to new rows only"),
          h("span", { class: "w" }, "Rows already loaded keep the old logic. _run_id shows which rows used which version."))) : null,
      opts.includes("full") ? h("button", { class: "choice", onclick: () => { closeDialog(); runEntities([decision.entity], { cascade: true }); } },
        h("span", { class: "main" }, h("span", { class: "n" }, "Rebuild it and everything downstream"),
          h("span", { class: "w" }, "Reprocesses the full source, then every table that reads it, in order.")), h("span", { class: "tag" }, "Recommended")) : null,
      opts.includes("migrate") ? h("button", { class: "choice", onclick: () => { closeDialog(); runEntities([decision.entity], { backfill: "migrate" }); } },
        h("span", { class: "main" }, h("span", { class: "n" }, "Migrate to Type 2"),
          h("span", { class: "w" }, "Keeps every surrogate key and starts each row's history at 1900-01-01."))) : null));
  }
  const ok = results.filter((r) => r.status === "ok").length;
  showDialog(decision ? "One table needs a decision" : `${ok} of ${results.length} succeeded`, body, actions);
}

async function showRun(runId, env = state.meta.env) {
  const r = await metaCall("meta_run_detail", { run_id: runId, env });
  if (!r) return;
  showDialog(`${r.entity} · ${dateTimeText(r.started_at)}`, h("div", { class: "scroll" },
    h("div", { class: "row wrap" }, statusPill(r.status), h("span", { class: "muted-text" }, `Run ${r.run_id} · ${ranOn(r)}${r.job_id ? ` · job ${r.job_id}` : ""}${r.attempt > 1 ? ` · try ${r.attempt}` : ""} · metadata ${r.metadata} · ${r.seconds}s`)),
    r.message ? h("p", { class: "text" }, r.message) : null,
    h("table", { class: "mini" }, h("thead", {}, h("tr", {}, ["Step", "Rows", "Time", "Result"].map((c) => h("th", {}, c)))),
      h("tbody", {}, r.steps.map((s) => h("tr", {}, h("td", {}, s.name), h("td", { class: "n" }, s.rows), h("td", { class: "n" }, `${s.seconds}s`), h("td", {}, s.detail))))),
    r.dq.length ? [h("b", {}, "Checks"), h("table", { class: "mini" }, h("tbody", {}, r.dq.map((d) => h("tr", {},
      h("td", {}, d.check), h("td", {}, d.failed ? h("span", { class: "pill-s " + (d.action === "stop" ? "fail" : "wrn") }, `${d.failed} failed`) : h("span", { class: "pill-s pass" }, "passed")),
      h("td", { class: "muted-text" }, d.action)))))] : null), [btn("Close", null, closeDialog, "tool primary")]);
}

async function renderRunsTab(body) {
  const runs = await attempt(() => rpc("meta_runs", { env: state.meta.env, limit: 200 })) || [];
  const shown = runs.filter((r) => matches(`${r.entity} ${r.status} ${r.run_id}`));
  body.replaceChildren(shown.length ? h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["Status", "Entity", "Ran on", "When", "Rows read", "Written", "Quarantined", "Took", "Metadata"].map((c) => h("th", {}, c)))),
    h("tbody", {}, shown.map((r) => h("tr", { class: "click", onclick: () => showRun(r.run_id) },
      h("td", {}, statusPill(r.status)),
      h("td", {}, h("div", { class: "mono" }, r.entity), r.backfill ? h("div", { class: "muted-text small" }, `backfill: ${r.backfill}`) : null,
        runReason(r) ? h("div", { class: "reason" }, runReason(r)) : null),
      h("td", {}, ranOn(r)), h("td", {}, when(r.started_at)),
      h("td", { class: "n" }, r.rows_read), h("td", { class: "n" }, r.rows_written), h("td", { class: "n" }, r.quarantined),
      h("td", { class: "n" }, took(r.seconds)), h("td", { class: "mono" }, r.metadata))))) :
    h("p", { class: "muted-text" }, `No runs in ${ENV_LABEL[state.meta.env]} yet.`));
}

// ---------- layers ----------

function renderLayersTab(body, p) {
  const m = state.meta;
  if (!m.layersDraft) m.layersDraft = p.layers.map((l) => ({ ...l }));
  const L = m.layersDraft;
  const counts = Object.fromEntries(p.layers.map((l) => [l.name, l.entities]));
  const changed = JSON.stringify(L.map(stripLayer)) !== JSON.stringify(p.layers.map(stripLayer));
  const rerender = () => renderLayersTab(body, p);
  const move = (i, d) => { const j = i + d; if (j < 0 || j >= L.length) return; [L[i], L[j]] = [L[j], L[i]]; rerender(); };
  body.replaceChildren(
    h("div", { class: "flow" }, L.map((l, i) => [i ? h("span", { class: "arrow", "aria-hidden": "true" }, "→") : null,
      h("span", { class: "layer-chip big", style: `background:${LAYER_COLORS[i % LAYER_COLORS.length]}` }, l.name)])),
    h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["#", "Layer", "Lakehouse in each environment", "Workspace", "Can read from", "Schema drift", "Required checks", "Surrogate keys", "Keep history", "Entities", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, L.map((l, i) => h("tr", {},
        h("td", {}, i + 1),
        h("td", {}, h("b", {}, l.name), l.description ? h("div", { class: "muted-text" }, l.description) : null),
        h("td", { class: "mono small" }, `LH_${l.name[0].toUpperCase()}${l.name.slice(1)}`),
        h("td", {}, h("select", { "aria-label": "Workspace", title: "Which workspace of each environment holds this layer's lakehouse (Data mesh > Domains > This project)",
          onchange: (ev) => { l.workspace = ev.target.value; rerender(); } },
        h("option", { value: "" }, "Environment's own"),
        (p.designations || []).map((d) => h("option", { value: d.id, selected: l.workspace === d.id ? true : null }, d.label)))),
        h("td", {}, i === 0 ? h("span", { class: "muted-text" }, "Files and sources") :
          h("select", { "aria-label": "Can read from", onchange: (ev) => { l.reads_from = ev.target.value ? [ev.target.value] : []; rerender(); } },
            h("option", { value: "" }, "Any earlier layer"),
            L.slice(0, i).map((o) => h("option", { value: o.name, selected: l.reads_from?.[0] === o.name ? true : null }, `Only ${o.name}`)))),
        h("td", {}, h("select", { "aria-label": "Schema drift", onchange: (ev) => { l.drift = ev.target.value; rerender(); } },
          Object.entries(DRIFT_LABEL).map(([id, label]) => h("option", { value: id, selected: l.drift === id ? true : null }, label)))),
        h("td", {}, ["key_not_null", "key_unique"].map((c) => h("label", { class: "check inline" },
          h("input", { type: "checkbox", checked: l.required_checks.includes(c) ? true : null,
            onchange: (ev) => { l.required_checks = ev.target.checked ? [...new Set([...l.required_checks, c])] : l.required_checks.filter((x) => x !== c); rerender(); } }),
          c === "key_not_null" ? "Key not null" : "Key unique"))),
        h("td", {}, h("label", { class: "check inline" },
          h("input", { type: "checkbox", checked: l.require_keys ? true : null, onchange: (ev) => { l.require_keys = ev.target.checked; rerender(); } }),
          "Required")),
        h("td", {}, h("label", { class: "row tight", title: "How long earlier versions can be restored. VACUUM keeps their files this long; Fabric needs at least 7 days." },
          h("input", { type: "number", min: "7", max: "3650", step: "1", class: "days", value: l.retention_days ?? 7, "aria-label": "Days of history",
            onchange: (ev) => { l.retention_days = Number(ev.target.value); rerender(); } }), "days")),
        h("td", {}, counts[l.name] || h("span", { class: "muted-text" }, "none")),
        h("td", {}, h("span", { class: "row tight" },
          h("button", { class: "tool small", title: "Move up", onclick: () => move(i, -1) }, icon("up", 14)),
          h("button", { class: "tool small", title: "Move down", onclick: () => move(i, 1) }, icon("down", 14)),
          h("button", { class: "tool small danger", title: counts[l.name] ? "Has entities" : "Remove layer", disabled: counts[l.name] ? true : null,
            onclick: () => { if (L.length <= 1) return toast("A project needs at least one layer. Add another layer before you remove this one.", true); L.splice(i, 1); rerender(); } }, "Remove"))))))),
    h("div", { class: "row" },
      btn("Add layer", "add", async () => {
        if (L.length >= 10) return toast("A project can have at most 10 layers. Remove one before you add another.", true);
        const name = await askText("Add a layer", "Name (lowercase, like platinum)", "", "Add");
        if (!name) return;
        L.push({ name: name.toLowerCase(), drift: "strict", required_checks: ["key_unique"], reads_from: [], description: "", retention_days: 7 });
        rerender();
      }, "tool outline"),
      h("span", { class: "spacer" }),
      changed ? btn("Discard changes", null, () => { m.layersDraft = null; rerender(); }, "tool outline") : null,
      btn("Save layers", "save", async () => {
        const res = await metaCall("meta_set_layers", { layers: L.map(stripLayer) }, "Layers not saved");
        if (res) { state.meta.project = res; m.layersDraft = null; toast("Layers saved"); render(); }
      }, "tool primary", { disabled: changed ? null : true })),
    h("div", { class: "info-cards" },
      infoCard("No mixing", "A table writes only to its own layer's lakehouse. It reads from earlier layers, or from its own layer when that makes no loop, like a fact reading its dimensions. Reading a later layer is always rejected."),
      infoCard("Lakehouses are created for you", "Each layer is a schema-enabled lakehouse named LH_<Layer> in every environment. Dev keeps them on this PC; Test and Prod get them on the first promote."),
      infoCard("Each layer has its own policy", "Schema drift and required checks are set per layer, so a lenient Bronze and a strict Gold can sit in one flow. Reordering is refused if a table would end up reading a later layer."),
      infoCard("Surrogate keys can be required", "Tick Required on a layer, like silver, and every table in it must take its keys from LH_Keys with a [[keys]] block."),
      infoCard("Undo a bad load", "Every run is a new Delta version. Keep history sets how many days back a table can be restored from its History; Optimize keeps that much when it vacuums.")));
}

function stripLayer(l) {
  return { name: l.name, drift: l.drift, required_checks: [...l.required_checks].sort(), reads_from: l.reads_from || [],
    description: l.description || "", require_keys: !!l.require_keys, retention_days: Number(l.retention_days ?? 7),
    workspace: l.workspace || "" };
}

function infoCard(title, text) {
  return h("div", { class: "card pad info" }, h("b", {}, title), h("p", { class: "muted-text" }, text));
}

// ---------- environments and promote ----------

async function renderEnvTab(body, p) {
  const envs = p.environments;
  const checks = {};
  const card = (env) => {
    const cfg = envs[env] || {};
    const local = (cfg.target || (env === "dev" ? "local" : "onelake")) === "local";
    const wsName = cfg.workspace_name || cfg.workspace_id;
    const lakehouses = p.layers.map((l) => l.lakehouse).concat("LH_Meta").join(" · ");
    const onWorkspace = !local && !!cfg.workspace_id;
    const where = local
      ? (wsName ? `Offline: tables stay in the project's .local folder. Switch Tables in back to Workspace to use ${wsName}.` : "Tables stay in the project's .local folder on this PC until you assign a workspace.")
      : !wsName ? `Assign a workspace before ${ENV_LABEL[env]} can run. You can still run it on this PC.`
        : cfg.compute === "fabric" ? `Fabric runs it on ${wsName}'s capacity and writes its lakehouses.`
          : `This PC runs it, reading and writing ${wsName}'s lakehouses in OneLake.`;
    return h("div", { class: "card pad env-card" },
      h("div", { class: "row" }, h("b", {}, ENV_LABEL[env]), h("span", { class: "spacer" }),
        btn(wsName ? "Change" : "Assign workspace", null, () => pickEnvWorkspace(env, p), "tool link small")),
      h("div", { class: "env-ws" }, icon("workspace", 16),
        wsName ? h("b", { title: cfg.workspace_id || null }, wsName) : h("span", { class: "muted-text" }, "No workspace yet")),
      h("div", { class: "muted-text small" }, lakehouses),
      h("div", { class: "compute-row" }, h("span", { class: "muted-text small" }, "Tables in"),
        h("div", { class: "seg" },
          h("button", { class: onWorkspace ? "on" : null, disabled: !cfg.workspace_id ? true : null,
            title: !cfg.workspace_id ? `Assign a workspace to ${ENV_LABEL[env]} first.` : null,
            onclick: !onWorkspace && cfg.workspace_id ? () => setEnv(env, { target: "onelake" }) : null }, "Workspace"),
          h("button", { class: local ? "on" : null, onclick: local ? null : () => setEnv(env, { target: "local" }) }, "This PC only"))),
      computeChoice(env, local, cfg.compute === "fabric"),
      capacitySlots[env] = h("div"),
      h("p", { class: "muted-text small" }, where));
  };
  // Each workspace's capacity, from the last reading (FINOPS-002); never signs in.
  const capacitySlots = {};
  rpc("capacity_health").then((doc) => {
    for (const e of doc.environments || []) capacitySlots[e.env]?.replaceWith((capacityLine(e) && preview("capacity", capacityLine(e))) || h("div"));
  }).catch(() => {});
  const promoteRow = (source, target) => {
    const box = h("div", { class: "muted-text" }, "Checking the promote gate...");
    attempt(() => rpc("meta_promote_check", { source, target })).then((res) => {
      if (!res) return;
      checks[target] = res;
      const verdict = res.errors.length ? h("div", { class: "problems" }, h("b", {}, `${res.errors.length} thing${res.errors.length === 1 ? "" : "s"} to do first`), gateList(res.errors))
        : h("div", { class: "pass-box" }, icon("ok", 16), `Ready: metadata ${res.version} passed every check in ${ENV_LABEL[source]}${res.tests?.passed ? `, and all ${res.tests.passed} test${res.tests.passed === 1 ? "" : "s"} passed` : ""}.`);
      // Notes don't block: an unreachable catalog, a breaking version, files no pushed commit holds.
      box.replaceWith(res.notes?.length ? h("div", {}, verdict, h("div", { class: "card pad warn-box" }, gateList(res.notes))) : verdict);
    });
    return h("div", { class: "card pad" },
      h("div", { class: "row" }, previewMarked("promote", h("b", {}, `Promote ${ENV_LABEL[source]} to ${ENV_LABEL[target]}`)), h("span", { class: "spacer" }),
        btn(`Promote to ${ENV_LABEL[target]}`, "publish", async () => {
          if (!state.user && !(await signIn())) return;
          // Prod, or a workspace a project on this PC named Prod (WR-251): the promote waits for the workspace's
          // name typed (WR-250), as a Prod write does. The gate check says which name; Prod's is known without it.
          const prodName = checks[target]?.confirm_name || (target === "prod" ? (envs.prod?.workspace_name || envs.prod?.workspace_id || "Prod") : "");
          if (prodName && !(await confirmDialog(`Promote to ${ENV_LABEL[target]}?`,
            target === "prod" ? `This puts ${ENV_LABEL[source]}'s metadata on ${prodName}, the Prod workspace, and its next runs use it.`
              : `This puts ${ENV_LABEL[source]}'s metadata on ${prodName}, and its next runs use it. ${prodName} is another project's Prod workspace on this PC.`,
            "Promote", { danger: true, typeToConfirm: prodName }))) return;
          toast(`Promoting to ${ENV_LABEL[target]}...`);
          let cancelled = false;
          const res = await metaCall("meta_promote", { source, target, ...(prodName ? { confirm_name: prodName } : {}) }, "Promote blocked", (err) => { cancelled = friendlyError(err).cancelled; });
          // Anvil Strike on the message once the target is on the new version; the ember beside the
          // error when the promote fails, and nothing for a cancelled sign-in (motion.js).
          if (res) {
            toast(`${ENV_LABEL[target]} is now on metadata ${res.version}`, false, { log: true });
            render();
            forgeEvent("promote.succeeded", document.getElementById("toast"));
          } else if (!cancelled) forgeEvent("promote.failed", document.getElementById("toast"));
        }, "tool primary small")),
      h("p", { class: "muted-text" }, "Needs the project to pass the save gate, its tests to pass, and every table's last run here to be green on this exact metadata version. Lakehouses that don't exist yet are created."),
      box, promoteImpact(source, target), lastRelease(target, render));
  };
  body.replaceChildren(h("div", { class: "env-cards" }, ["dev", "test", "prod"].map(card)),
    promoteRow("dev", "test"), promoteRow("test", "prod"), deltaFeaturesCard(p));
}

// ---------- Delta table features at publish (Preview) ----------

// Each table's delta_features, as the engine names them (delta_profile.TABLE_CHOICES); "" is no setting,
// which is the modern profile.
const DELTA_CHOICES = [
  ["", "Default: deletion vectors and column mapping (modern)"],
  ["every_reader", "Every reader: no deletion vectors or column mapping"],
  ["deletion_vectors", "Allow deletion vectors only"],
  ["column_mapping", "Allow column mapping by name only"],
];

// What each choice allows, for the typed confirm's title.
const DELTA_ALLOWS = { "": "deletion vectors and column mapping", deletion_vectors: "deletion vectors", column_mapping: "column mapping" };

// "a", "a and b", "a, b and c".
function andList(items) {
  return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function deltaFeaturesCard(p) {
  if (!previewShown()) return null;
  const tables = (p.entities || []).filter((e) => e.delta);
  return h("div", { class: "card pad" },
    previewMarked("deltaPerTable", h("b", {}, "Delta table features at publish")),
    h("p", { class: "muted-text" }, "A run that publishes a table to OneLake uses only the Delta features you allow here. "
      + "If the table already uses a feature you didn't allow, the run writes nothing, so change the setting first."),
    tables.length ? h("div", { class: "delta-tables" }, tables.map((e) => deltaFeaturesRow(e)))
      : h("p", { class: "muted-text" }, "This project has no tables yet. Add an entity to choose its features."));
}

function deltaFeaturesRow(e) {
  const note = h("div");
  const current = e.delta.features || "";
  const select = h("select", { onchange: () => chooseDeltaFeatures(e, select, note) },
    DELTA_CHOICES.map(([value, label]) => h("option", { value, selected: value === current ? true : null }, label)));
  return h("div", { class: "row wrap" },
    h("span", { class: "mono grow" }, e.name),
    h("label", { class: "field" }, "Delta features", select),
    h("span", { class: "muted-text small" }, `Checked against ${andList(e.delta.reader_names)}.`),
    e.delta.problems?.length ? deltaRefusedNote(e.delta.problems[0]) : null,
    note);
}

// Saves a table's choice. A choice one of its readers can't read asks for its full name first, and
// the engine refuses it without that name (delta_profile.confirm_choice).
async function chooseDeltaFeatures(e, select, note) {
  const choice = select.value;
  const reasons = e.delta.reasons[choice || "default"] || [];
  let confirmName = null;
  if (reasons.length) {
    const ok = await confirmDialog(`Allow ${DELTA_ALLOWS[choice] || "this"} on ${e.name}?`,
      `These readers of ${e.name} can't read it with this choice, or no source says they can. It applies to this table only.`, "Allow it",
      { danger: true, typeToConfirm: e.name, typeLabel: `Type ${e.name} to allow it`, items: reasons });
    if (!ok) { select.value = e.delta.features || ""; return; }
    confirmName = e.name;
  }
  try {
    const res = await rpc("meta_set_delta_features", { name: e.name, delta_features: choice || null, confirm_name: confirmName });
    state.meta.project = res;
    e.delta = (res.entities || []).find((x) => x.name === e.name)?.delta || e.delta;
    note.replaceChildren();
    toast(`Saved. Runs that publish ${e.name} now use this choice.`);
  } catch (err) {
    select.value = e.delta.features || "";
    note.replaceChildren(deltaRefusedNote(friendlyError(err).text));
  }
}

function deltaRefusedNote(text) {
  return h("div", { class: "problems" }, h("p", {}, text), docLink("delta-reader-matrix.md#each-tables-own-choice-preview", "See the reader matrix"));
}

// This PC or Fabric runs the environment's entities. Fabric can only reach tables in OneLake.
function computeChoice(env, local, fabric) {
  const option = (value, label, on) => h("button", {
    class: on ? "on" : null, disabled: value === "fabric" && local ? true : null,
    title: value === "fabric" && local ? `${ENV_LABEL[env]} keeps its tables on this PC only. Switch Tables in to Workspace first.` : null,
    onclick: on ? null : () => setCompute(env, value),
  }, label);
  return h("div", { class: "compute-row" }, h("span", { class: "muted-text small" }, "Runs on"),
    h("div", { class: "seg" }, option("local", "This PC", !fabric), preview("fabricRun", option("fabric", "Fabric", fabric)) || (fabric ? option("fabric", "Fabric", fabric) : null)),
    preview("schedules", btn("Schedules", "history", () => scheduleMetaRuns(env), "tool link small")));
}

async function setCompute(env, compute) {
  if (compute === "fabric" && !state.user && !(await signIn())) return;
  const res = await metaCall("meta_set_compute", { env, compute });
  if (!res) return;
  state.meta.project = res;
  toast(compute === "fabric" ? `${ENV_LABEL[env]} now runs in Fabric. The first run uploads the project to LH_Meta and publishes a runner notebook.`
    : `${ENV_LABEL[env]} now runs on this PC.`);
  render();
}

function scheduleMetaRuns(env) {
  const p = state.meta.project;
  const fabric = inFabric(p, env);
  openScheduleDialog({
    title: `Schedule ${ENV_LABEL[env]} runs`, what: `${ENV_LABEL[env]} runs`,
    mode: fabric ? "fabric" : "pc",
    pc: { target: { kind: "meta", env }, query: { kind: "meta", env },
          intro: `This PC runs every entity in ${ENV_LABEL[env]}, in layer order, from the project files as they are when it runs.` },
    fabric: {
      unavailable: fabric ? "" : `${ENV_LABEL[env]} runs on this PC. Set Runs on to Fabric in Environments to schedule it there.`,
      intro: `Fabric runs every entity in ${ENV_LABEL[env]}, in layer order, on its own capacity, using the project as it was last uploaded (each run or promote from this app uploads it).`,
      adding: `Uploading the project and adding the schedule for ${ENV_LABEL[env]}...`,
      added: "Scheduled. Fabric runs every entity on its own capacity, even when this PC is off.",
      list: () => rpc("meta_schedules", { env }),
      add: (schedule) => rpc("meta_add_schedule", { env, schedule }),
      remove: (id) => rpc("meta_delete_schedule", { env, schedule_id: id }),
    },
  });
}

// Every environment has a workspace, whether this PC or Fabric runs it: pick one, or create one.
async function pickEnvWorkspace(env, p) {
  const current = p.environments?.[env]?.workspace_id;
  const list = h("div", { class: "scroll" }, h("p", { class: "muted-text" }, state.user ? "Loading workspaces..." : "Sign in to pick a Fabric workspace."));
  showDialog(`Workspace for ${ENV_LABEL[env]}`, list, [btn("Cancel", null, closeDialog, "tool outline")]);
  if (!state.user && !(await signIn())) return;
  const workspaces = await loadWorkspaces();
  const taken = Object.fromEntries(["dev", "test", "prod"].filter((e) => e !== env && p.environments?.[e]?.workspace_id)
    .map((e) => [p.environments[e].workspace_id, ENV_LABEL[e]]));
  list.replaceChildren(
    h("p", { class: "muted-text" }, `${ENV_LABEL[env]}'s lakehouses (one per layer, plus LH_Meta) live in this workspace, whether this PC or Fabric runs it.`),
    h("button", { class: "choice", onclick: () => createEnvWorkspace(env, p) }, icon("add", 20),
      h("span", { class: "main" }, h("span", { class: "n" }, "Create a new workspace"), h("span", { class: "w" }, "Made with your Fabric sign-in, on a capacity you pick"))),
    ...workspaces.map((w) => h("button", { class: `choice${w.id === current ? " selected" : ""}`,
      onclick: () => setEnv(env, { workspace_id: w.id, workspace_name: w.displayName }) },
    icon("workspace", 20), h("span", { class: "main" }, h("span", { class: "n" }, w.displayName),
      h("span", { class: "w" }, taken[w.id] ? `Also used by ${taken[w.id]}` : "One lakehouse per layer in this workspace")),
    w.id === current ? h("span", { class: "tag" }, "Current") : null)));
}

function suggestedWorkspaceName(env, p) {
  const parts = (p.path || "").split(/[\\/]/).filter(Boolean);
  let base = parts.pop() || "Fabric Desktop";
  if (base.toLowerCase() === "metadata" && parts.length) base = parts.pop();
  return `${base} - ${ENV_LABEL[env]}`;
}

async function createEnvWorkspace(env, p) {
  const name = h("input", { value: suggestedWorkspaceName(env, p) });
  const capacity = h("select", {}, h("option", { value: "" }, "Loading capacities..."));
  const note = h("p", { class: "muted-text small" }, "Lakehouses need a Fabric capacity, so pick an active one.");
  const create = async () => {
    if (!name.value.trim()) { name.focus(); return; }
    toast(`Creating ${name.value.trim()}...`);
    const res = await metaCall("meta_create_workspace", { env, name: name.value.trim(), capacity_id: capacity.value }, "Workspace not created");
    if (!res) return;
    closeDialog();
    state.workspaces = null;  // the new one shows up in every picker
    state.meta.project = res;
    toast(`Created ${res.created.displayName} and assigned it to ${ENV_LABEL[env]}. Its lakehouses are created on the first ${env === "dev" ? "run" : "promote"}.`, false, { log: true });
    render();
  };
  showDialog(`New workspace for ${ENV_LABEL[env]}`, h("div", {},
    h("label", { class: "field" }, "Name", name),
    h("label", { class: "field" }, "Capacity", capacity), note),
  [btn("Back", null, () => pickEnvWorkspace(env, p), "tool outline"), previewMarked("createWorkspace", btn("Create workspace", "add", create, "tool primary"))]);
  name.select();
  const caps = (await attempt(() => rpc("list_capacities"), "Could not list capacities")) || [];
  capacity.replaceChildren(...caps.map((c) => h("option", { value: c.id },
    `${c.displayName}${c.sku ? ` (${c.sku})` : ""}${c.state && c.state !== "Active" ? ` - ${c.state.toLowerCase()}` : ""}`)),
  h("option", { value: "" }, "No capacity (assign one in Fabric later)"));
  if (!caps.length) note.textContent = "No Fabric capacity found for your account. The workspace is created without one; assign a capacity in Fabric before its lakehouses can be made.";
}

async function setEnv(env, cfg) {
  closeDialog();
  const res = await metaCall("meta_set_environment", { env, ...cfg });
  if (!res) return;
  state.meta.project = res;
  if (cfg.workspace_name) toast(`${ENV_LABEL[env]} now uses ${cfg.workspace_name}.`);
  else if (cfg.target === "local") toast(`${ENV_LABEL[env]} now keeps its tables on this PC only.`);
  else if (cfg.target === "onelake") toast(`${ENV_LABEL[env]} now reads and writes its workspace's lakehouses.`);
  render();
}

// ---------- surrogate keys ----------

const CONNECTION_LABEL = {
  connection_type: "Connection type", server_host: "Server or host", port: "Port", database_name: "Database or catalog",
  schema_name: "Schema", base_url: "Base URL (APIs)", file_path: "File path or container", auth_method: "Authentication",
  tenant_id: "Tenant id", username: "Username or client id", key_vault_url: "Key Vault URL", secret_name: "Secret name",
  fabric_connection_id: "Fabric connection id", gateway_id: "Gateway", extra_options: "Extra options (JSON)",
};
const AUTH_METHODS = ["", "Entra ID (your sign-in)", "Service principal", "SQL login", "OAuth client", "API key", "None"];

function keysWhere(k) {
  if (k.local) return h("span", {}, "This PC, ", h("span", { class: "mono" }, k.path));
  return k.workspace_name || k.workspace_id ? h("span", {}, "Workspace ", h("b", {}, k.workspace_name || k.workspace_id))
    : h("span", { class: "muted-text" }, "No Keys workspace yet");
}

async function renderKeysTab(body) {
  const m = state.meta;
  const k = await metaCall("meta_keys", { env: m.env });
  if (!k || state.view !== "metadata" || m.tab !== "keys") return;
  const tables = k.key_tables;
  if (!tables.find((t) => t.name === m.keyTable)) m.keyTable = tables[0]?.name || null;
  const sel = tables.find((t) => t.name === m.keyTable);
  const rerender = () => renderKeysTab(body);

  const head = h("div", { class: "card pad" }, h("div", { class: "keys-head" },
    h("span", { class: "item" }, icon("database", 24),
      h("span", {}, h("b", {}, `${k.lakehouse} in ${ENV_LABEL[k.env]}`),
        h("div", { class: "muted-text" }, "One table per business key. Every run MERGEs new business keys here first, then puts the key on every row."),
        h("div", { class: "muted-text small" }, keysWhere(k)))),
    k.local ? null : btn(k.workspace_id ? "Change Keys workspace" : "Pick Keys workspace", "workspace", () => pickKeysWorkspace(k.env), "tool outline small"),
    previewMarked("syncKeys", btn("Sync to LH_Keys", "sync", async () => {
      if (!k.local && !state.user && !(await signIn())) return;
      const res = await metaCall("meta_keys_sync", { env: k.env }, "Not synced");
      if (res) {
        if (res.missing?.length) toast(`Not in the platform yet: ${res.missing.map((n) => `sk.${n}`).join(", ")}. Request them in Platform > Set up > Business keys.`, true);
        else toast(res.platform ? `Every key table is in the platform${res.created.length ? `; created ${res.created.map((n) => `sk.${n}`).join(", ")}` : ""}`
          : `Synced: ${res.config.source_system} source systems${res.created.length ? `, created ${res.created.map((n) => `sk.${n}`).join(", ")}` : ""}`);
        rerender();
      }
    }, "tool outline small")),
    addKeyTableButton(k, rerender, "tool primary small")));

  const problems = [...k.errors, ...(k.problem ? [k.problem] : [])];
  const list = tables.length ? h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["Key table", "Surrogate key", "Business key", "Created", "Keys", "Next key", "Used by"].map((c) => h("th", {}, c)))),
    h("tbody", {}, tables.map((t) => h("tr", { class: `click${t.name === m.keyTable ? " picked" : ""}`, onclick: () => { m.keyTable = t.name; rerender(); } },
      h("td", {}, h("span", { class: "item" }, icon("table", 18), h("b", { class: "mono" }, t.table))),
      h("td", { class: "mono" }, `${t.sk} INT`),
      h("td", { class: "mono" }, t.bk),
      h("td", { class: "mono" }, "create_date"),
      h("td", { class: "n" }, t.created ? t.keys.toLocaleString() : h("span", { class: "muted-text" }, "not created")),
      h("td", { class: "n mono" }, t.created ? t.next.toLocaleString() : "1"),
      h("td", {}, t.used_by.length ? `${t.used_by.length} table${t.used_by.length === 1 ? "" : "s"}` : h("span", { class: "muted-text" }, "none"))))))
    : emptyState({ title: "No key tables yet",
      text: k.central_key_tables ? "Use the platform's key table for each business key, like customer or product."
        : "Add one per business key, like customer or product. Each gets its own table in LH_Keys.",
      action: addKeyTableButton(k, rerender, "tool primary") });

  body.replaceChildren(...[head, keysPlatformCard(k, rerender),
    problems.length ? h("div", { class: "problems" }, h("b", {}, "Fix these first"), gateList(problems)) : null,
    h("div", { class: "keys-grid" }, list, sel ? keyTableDetail(sel, k, rerender) : null)].filter(Boolean));
}

function keyTableDetail(t, k, rerender) {
  const input = h("input", { "aria-label": "Business key", placeholder: "Business key, like ABC" });
  const answer = h("p", { class: "muted-text" });
  const find = async () => {
    if (!input.value.trim()) return;
    const res = await metaCall("meta_key_lookup", { table: t.name, value: input.value, env: k.env });
    if (res === undefined) return;
    answer.replaceChildren(res ? h("span", {}, h("span", { class: "mono" }, res.bk), " is ", h("b", { class: "mono" }, String(res.sk)),
      `, created ${dateTimeText(res.create_date)}.`) : `No ${t.bk} ${input.value.trim()} yet. The first run that sees it gives it a key.`);
  };
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") find(); });
  return h("div", { class: "card pad side-panel" },
    h("div", { class: "row" }, h("h2", { class: "flush mono" }, t.table), h("span", { class: "spacer" }),
      btn("Delete", "delete", async () => {
        if (!(await confirmDialog(`Delete key table ${t.name}?`, "The definition file is removed. The table stays in LH_Keys, so no key is lost.", "Delete", { danger: true }))) return;
        const res = await metaCall("meta_delete_key_table", { name: t.name, env: k.env }, "Not deleted");
        if (res) { toast(`Deleted ${t.name}`); state.meta.keyTable = null; rerender(); }
      }, "tool outline small", { disabled: t.used_by.length ? true : null, title: t.used_by.length ? "Tables still use it" : "Delete" })),
    h("p", { class: "muted-text small" }, t.description || "", t.description ? " · " : "", "File ", h("span", { class: "mono" }, `metadata/keys/${t.name}.toml`),
      t.owner ? `, owner ${t.owner}` : ""),
    h("b", {}, "Attributes"),
    h("table", { class: "mini" }, h("thead", {}, h("tr", {}, ["Column", "Type", "Rule"].map((c) => h("th", {}, c)))),
      h("tbody", {}, t.columns.map((c) => h("tr", {}, h("td", { class: "mono" }, c.name), h("td", { class: "mono" }, c.type), h("td", {}, c.rule))))),
    h("p", { class: "muted-text small" }, "-1 is the Unknown member, for rows with no business key."),
    h("b", {}, "Look up a business key"),
    h("div", { class: "row" }, input, btn("Find", "search", find, "tool outline small")),
    answer,
    h("b", {}, "Used by"),
    t.used_by.length ? h("div", { class: "row wrap" }, t.used_by.map((n) => h("button", { class: "tool link mono", onclick: () => openEntity(n) }, n)))
      : h("p", { class: "muted-text" }, "No table uses it yet. Add this to an entity:"),
    h("pre", { class: "sql-block" }, `[[keys]]\ntable = "${t.name}"\nbusiness_key = "${t.name}_id"`));
}

// Keys in the platform: a project uses the platform's key tables, it doesn't make its own.
// With platform keys there is one key system: the project uses the platform's key tables and never makes its own.
function addKeyTableButton(k, done, cls) {
  return k.central_key_tables ? btn("Use a key table", "add", () => useKeyTableDialog(k, done), cls)
    : btn("New key table", "add", () => newKeyTableDialog(done), cls);
}

function useKeyTableDialog(k, done) {
  const have = new Set(k.key_tables.map((t) => t.name));
  const free = k.central_key_tables.filter((t) => !have.has(t.name));
  const pick = h("select", {}, free.map((t) => h("option", { value: t.name }, `sk.${t.name}${t.description ? `: ${t.description}` : ""}`)));
  showDialog("Use a key table", h("div", {},
    h("p", { class: "text" }, "Keys come from the platform, one key system for every team. Pick the business key this project needs; the platform's admins add new ones."),
    free.length ? h("label", { class: "field" }, "Key table", pick) : h("p", { class: "muted-text" }, "This project already uses every key table the platform has.")), [
    btn("Request a new one", null, () => { closeDialog(); requestDialog("key_table", "", done); }, "tool outline"),
    btn("Cancel", null, closeDialog, "tool outline"),
    free.length ? btn("Use it", null, async () => {
      const t = free.find((x) => x.name === pick.value);
      const res = await metaCall("meta_new_key_table", { name: t.name, description: t.description || "", env: state.meta.env }, "Not added");
      if (res) { closeDialog(); state.meta.keyTable = t.name; toast(`This project now uses sk.${t.name}`); done(); }
    }, "tool primary") : null].filter(Boolean));
}

function newKeyTableDialog(done) {
  const name = h("input", { placeholder: "customer" });
  const description = h("input", { placeholder: "A customer, from any source" });
  const preview = h("p", { class: "muted-text" });
  const sync = () => {
    const n = name.value.trim().toLowerCase() || "customer";
    preview.textContent = `Creates sk.${n} in LH_Keys with ${n}_sk (INT), ${n}_bk (unique) and create_date.`;
  };
  name.addEventListener("input", sync);
  sync();
  showDialog("New key table", h("div", { class: "scroll" },
    h("label", { class: "field" }, "Business key name", name),
    h("label", { class: "field" }, "Description", description), preview), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Create", null, async () => {
      if (!name.value.trim()) return toast("Give the key table a name", true);
      const res = await metaCall("meta_new_key_table", { name: name.value.trim(), description: description.value.trim(), env: state.meta.env }, "Not created");
      if (res) { closeDialog(); state.meta.keyTable = name.value.trim().toLowerCase(); toast(`Created sk.${state.meta.keyTable}`); done(); }
    }, "tool primary"),
  ]);
}

async function pickKeysWorkspace(env) {
  const list = h("div", { class: "scroll" }, h("p", { class: "muted-text" }, state.user ? "Loading workspaces..." : "Sign in to pick a Fabric workspace."));
  showDialog(`Keys workspace for ${ENV_LABEL[env]}`, list, [btn("Cancel", null, closeDialog, "tool outline")]);
  if (!state.user && !(await signIn())) return;
  const workspaces = await loadWorkspaces();
  list.replaceChildren(h("p", { class: "muted-text" }, "LH_Keys is created in this workspace on the next sync or promote."),
    ...workspaces.map((w) => h("button", { class: "choice", onclick: async () => {
      closeDialog();
      const res = await metaCall("meta_set_keys_workspace", { env, workspace_id: w.id, workspace_name: w.displayName });
      if (res) render();
    } }, icon("workspace", 20), h("span", { class: "main" }, h("span", { class: "n" }, w.displayName), h("span", { class: "w" }, "Holds LH_Keys")))));
}

// ---------- source systems ----------

async function renderSourcesTab(body) {
  const m = state.meta;
  const k = await metaCall("meta_keys", { env: m.env, platform: previewShown() });
  if (!k || state.view !== "metadata" || m.tab !== "sources") return;
  const systems = k.source_systems;
  // With the platform (Preview), its list is the one list of source systems: a project reads
  // sources from it and keeps only its own connections (UX-009).
  const plat = previewShown() ? k.platform_sources : null;
  if (m.source !== "new" && !systems.find((s) => s.id === m.source)) m.source = systems[0]?.id ?? "new";
  const rerender = () => renderSourcesTab(body);
  const nextId = Math.max(0, ...systems.map((s) => s.id)) + 1;
  const blank = { id: nextId, code: "", name: "", system_type: "", owner: "", description: "", active: true,
    connections: Object.fromEntries(["dev", "test", "prod"].map((env) => [env, Object.fromEntries(k.connection_fields.map((f) => [f, ""]))])) };
  const current = m.source === "new" ? blank : systems.find((s) => s.id === m.source);
  // Unsaved edits survive switching the environment shown, until another source system is picked.
  if (!m.sourceDraft || m.sourceDraft.key !== String(m.source)) m.sourceDraft = { key: String(m.source), draft: JSON.parse(JSON.stringify(current)) };
  const draft = m.sourceDraft.draft;
  const pick = (id) => { m.source = id; m.sourceDraft = null; rerender(); };

  const item = (s) => h("button", { class: `source-item${s.id === m.source ? " picked" : ""}`, onclick: () => pick(s.id) },
    h("b", {}, s.code), h("span", { class: "muted-text small" }, `${s.system_type || "Type not set"} · id ${s.id}${s.active ? "" : " · inactive"}`));
  const own = systems.filter((s) => !s.platform);
  const list = plat ? h("div", { class: "card source-list" },
    h("b", { class: "source-head" }, "Sources this project reads"),
    systems.some((s) => s.platform) ? h("span", { class: "muted-text small source-group" }, "From the platform") : null,
    systems.filter((s) => s.platform).map(item),
    own.length ? h("span", { class: "muted-text small source-group" }, "Only in this project") : null,
    own.map(item),
    h("button", { class: "source-item add", onclick: () => readSourceDialog(plat, m.env, pick) },
      icon("add", 16), "Add a source this project reads"))
    : h("div", { class: "card source-list" }, systems.map(item),
      h("button", { class: `source-item add${m.source === "new" ? " picked" : ""}`, onclick: () => pick("new") },
        icon("add", 16), "New source system"));
  // Sources made before the platform's list existed move there with their ids, like Move keys.
  const moveCard = plat && own.length ? h("div", { class: "card pad" }, h("div", { class: "row" }, icon("database", 18),
    h("span", { class: "grow" }, h("b", {}, `${own.map((s) => s.code).join(", ")} ${own.length === 1 ? "is" : "are"} only in this project`),
      h("div", { class: "muted-text small" }, "Move sources to the platform copies them to its one list with the same ids, so every project reads them alike. Connections stay here.")),
    btn("Move sources to the platform", "databaseArrowRight", () => moveSourcesToPlatform(own, m.env, rerender), "tool outline small"))) : null;
  const fromPlatform = !!(plat && current?.platform);

  const field = (label, value, onInput, attrs = {}) => {
    const input = h("input", { value: value ?? "", ...attrs });
    input.addEventListener("input", () => onInput(input.value));
    return h("label", { class: "field" }, label, input);
  };
  const select = (label, value, options, onChange) => h("label", { class: "field" }, label,
    h("select", { onchange: (ev) => onChange(ev.target.value) },
      options.map((o) => h("option", { value: o, selected: o === value ? true : null }, o || "Not set"))));

  const conn = draft.connections[m.sourceEnv];
  const connFields = k.connection_fields.map((f) => f === "auth_method"
    ? select(CONNECTION_LABEL[f], conn[f], AUTH_METHODS.includes(conn[f]) ? AUTH_METHODS : [...AUTH_METHODS, conn[f]], (v) => { conn[f] = v; })
    : field(CONNECTION_LABEL[f] || f, conn[f], (v) => { conn[f] = v; }));

  const save = async () => {
    if (!draft.code.trim()) return toast("Give the source system a code, like CRM", true);
    const res = await metaCall("meta_save_source_system", { system: draft, original_id: m.source === "new" ? null : current.id, env: m.env }, "Not saved");
    if (res) { toast(`Saved ${draft.code}`); pick(Number(draft.id)); }
  };

  const form = h("div", { class: "card pad source-form" },
    h("div", { class: "row" }, h("h2", { class: "flush" }, m.source === "new" ? "New source system" : current.code), h("span", { class: "spacer" }),
      m.source === "new" ? null : btn(fromPlatform ? "Stop reading" : "Delete", "delete", async () => {
        if (!(await confirmDialog(fromPlatform ? `Stop reading ${current.code}?` : `Delete ${current.code}?`,
          `It is removed from source_systems.toml${fromPlatform ? " with this project's connection; the platform keeps the source" : ""}. Sync to remove it from LH_Keys too.`,
          fromPlatform ? "Stop reading" : "Delete", { danger: !fromPlatform }))) return;
        const res = await metaCall("meta_delete_source_system", { id: current.id, env: m.env }, "Not deleted");
        if (res) { toast(`Deleted ${current.code}`); pick(null); }
      }, "tool outline small"),
      btn(fromPlatform ? "Save connection" : "Save", "save", save, "tool primary small")),
    h("b", {}, "General"),
    fromPlatform ? h("div", { class: "row" },
      h("p", { class: "muted-text small grow" }, "Name, id, owner and type are kept once for every project in Platform > Set up > Landing zone > Sources. Only the connection is this project's own."),
      btn("Edit in Platform", "building", () => { platformPage.source = current.platform.name; goPlatform("landing"); }, "tool link small")) : null,
    h("div", { class: "form-grid" },
      field("Source system id (never changes)", draft.id, (v) => { draft.id = v; }, { type: "number", min: "1", readonly: m.source === "new" ? null : true }),
      field("Code", draft.code, (v) => { draft.code = v; }, { placeholder: "CRM", readonly: fromPlatform || null }),
      field("Name", draft.name, (v) => { draft.name = v; }, { placeholder: "Dynamics 365 Sales", readonly: fromPlatform || null }),
      fromPlatform ? field("System type", draft.system_type, () => {}, { readonly: true }) : select("System type", draft.system_type, ["", ...k.system_types], (v) => { draft.system_type = v; }),
      field("Owner", draft.owner, (v) => { draft.owner = v; }, { readonly: fromPlatform || null }),
      fromPlatform ? field("Active", draft.active ? "Yes" : "No", () => {}, { readonly: true }) : select("Active", draft.active ? "Yes" : "No", ["Yes", "No"], (v) => { draft.active = v === "Yes"; })),
    field("Description", draft.description, (v) => { draft.description = v; }, { readonly: fromPlatform || null }),
    h("div", { class: "row" }, h("b", {}, "Connection"), h("span", { class: "spacer" }),
      h("div", { class: "seg", role: "group", "aria-label": "Environment" }, ["dev", "test", "prod"].map((env) =>
        h("button", { class: env === m.sourceEnv ? "on" : "", onclick: () => { m.sourceEnv = env; rerender(); } }, ENV_LABEL[env])))),
    h("div", { class: "form-grid" }, connFields),
    h("div", { class: "pass-box" }, icon("shield", 16),
      "Passwords and keys never go here. Give the Key Vault URL and the secret's name; the notebook reads the secret when it runs."),
    h("p", { class: "muted-text small" }, "Sync writes these to LH_Keys as config.source_system and config.source_connection, one row per source per environment, so notebooks and pipelines look up connections by code."));

  const problems = [...k.errors, ...(plat ? k.source_problems : [])];
  body.replaceChildren(...[
    problems.length ? h("div", { class: "problems" }, h("b", {}, "Fix these first"), gateList(problems)) : null,
    previewShown() && k.platform_problem ? h("p", { class: "fail-text small" }, k.platform_problem) : null,
    moveCard,
    h("div", { class: "sources-grid" }, list, form), varlibCard()].filter(Boolean));
}

// Pick one of the platform's sources for this project to read. New sources are added on the
// Platform page by its admins (or requested there).
function readSourceDialog(plat, env, pick) {
  const left = plat.filter((p) => !p.read);
  showDialog("Add a source this project reads", h("div", {},
    h("p", { class: "text" }, "The platform keeps one list of source systems for every project, each with one id. Pick one; you then give this project's connection in each environment."),
    left.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Source", "Type", "Owner", "Id", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, left.map((p) => h("tr", {},
        h("td", {}, h("b", {}, p.code), h("div", { class: "muted-text small" }, p.title)),
        h("td", {}, p.system_type || h("span", { class: "muted-text" }, "not set")),
        h("td", {}, p.owner || h("span", { class: "muted-text" }, "not set")),
        h("td", { class: "mono" }, p.source_id ?? ""),
        h("td", {}, btn("Read", "add", async () => {
          const res = await metaCall("meta_source_read", { name: p.name, env }, "Not added");
          if (res) { closeDialog(); toast(`This project now reads ${p.code}`); pick(p.source_id); }
        }, "tool outline small"))))))
      : h("p", { class: "muted-text" }, plat.length ? "This project already reads every source in the platform." : "The platform has no sources yet."),
    h("p", { class: "muted-text small" }, "Missing one? Platform admins add sources on Platform > Set up > Landing zone; anyone can request one there.")), [
    btn("Open Platform > Set up > Landing zone", "building", () => { closeDialog(); goPlatform("landing"); }, "tool outline"),
    btn("Close", null, closeDialog, "tool primary")]);
}

async function moveSourcesToPlatform(own, env, done) {
  if (!(await confirmDialog("Move sources to the platform?",
    `Copies ${own.map((s) => `${s.code} (id ${s.id})`).join(", ")} to the platform's list with the same ids. Their connections stay in this project. A code or id the platform already uses for another source is reported, not changed. Needs a platform admin.`,
    "Move"))) return;
  const res = await metaCall("meta_sources_move", { env }, "Not moved");
  if (!res) return;
  if (res.conflicts.length) {
    showDialog("Some sources weren't moved", h("div", {},
      res.moved.length ? h("p", { class: "text" }, `Moved ${res.moved.join(", ")}.`) : null,
      h("ul", { class: "plain" }, res.conflicts.map((c) => h("li", {}, c))),
      h("p", { class: "muted-text small" }, "Rows already loaded keep this project's ids, so nothing was renumbered. Ask a platform admin which id stays.")),
      [btn("Close", null, closeDialog, "tool primary")]);
  } else toast(res.moved.length ? `Moved ${res.moved.join(", ")} to the platform` : "Already in the platform");
  done();
}

// The entity file editor's own keys, listed in the Keyboard shortcuts dialog on an entity page (handled in renderEntity).
registerShortcuts("Entity page", [
  { keys: ["Ctrl+S"], label: "In the entity file: save it", when: () => state.view === "entity" },
  { keys: ["Tab"], label: "In the entity file: type four spaces", when: () => state.view === "entity" },
  { keys: ["Esc"], label: "In the entity file: the next Tab moves on instead of typing spaces (Shift+Tab always goes back)", when: () => state.view === "entity" },
]);

registerFeature({
  id: "metadata", rail: { label: "Data mesh", icon: "metadata", order: 50, also: ["entity"] },
  views: { metadata: renderMetadata, entity: renderEntity },
  commands: [
    ...META_TAB_GROUPS.flatMap(([, , tabs]) => tabs.map(([id, label, previewId]) => ({
      label: `Data mesh: ${label}`, icon: "metadata", keywords: "metadata medallion", preview: !!previewId, run: () => goMeta(id) }))),
    { label: "New entity", icon: "add", keywords: "metadata table create", run: () => { goMeta("entities"); newEntityDialog(); },
      when: () => !!state.meta?.project?.path },
    { label: "New entity from the landing zone", icon: "folderOpen", keywords: "bronze raw files platform", preview: true,
      run: () => { goMeta("entities"); newEntityDialog("landing"); },
      when: () => !!state.meta?.project?.path },
    { label: "Run all entities", icon: "play", keywords: "metadata run", run: () => { goMeta("runs"); runEntities(null); },
      when: () => !!state.meta?.project?.path },
  ],
});

// ---------- table health ----------

function shortDate(iso) {
  return dateTimeText(iso, { empty: "never" });
}

// The workspaces whose capacity has a warning, for the toast after Optimize skipped their tables.
function maintainWarnedText(check, count) {
  const names = (check?.workspaces || []).filter((w) => w.result === "warning").map((w) => w.workspace_name || "its workspace");
  if (!names.length) return `${count} table${count === 1 ? "" : "s"} weren't optimized, because their capacity check has a warning.`;
  const where = names.length === 1 ? names[0] : `${names.length} workspaces`;
  const why = names.length === 1 ? "its capacity has" : "their capacities have";
  if (count === 1) return `The table on ${where} wasn't optimized, because ${why} a warning.`;
  return `The ${count} tables on ${where} weren't optimized, because ${why} a warning.`;
}

// Optimize all in Fabric when a workspace's capacity check is Warning: say which tables it holds and
// what the warning is, and start them only once the box is ticked. The others can start on their own.
// The engine checks again and records the acceptance before it starts anything (meta_optimize risk_ids).
function maintainWarningDialog(check, start) {
  const all = check.workspaces.filter((w) => w.result !== "blocked");
  const warned = all.filter((w) => w.result === "warning");
  const count = (ws) => ws.reduce((n, w) => n + w.tables.length, 0);
  const total = count(all), clear = total - count(warned);
  const plural = (n) => `${n} table${n === 1 ? "" : "s"}`;
  const box = h("input", { type: "checkbox", onchange: () => { every.disabled = !box.checked; } });
  const every = btn(`Optimize all ${total}`, null, () => { closeDialog(); start(Object.fromEntries(warned.map((w) => [w.workspace_id, w.risk_id]))); }, "tool primary");
  every.disabled = true;
  const some = clear ? btn(`Optimize the ${clear} without warnings`, null, () => { closeDialog(); start(null); }, "tool outline") : null;
  const panel = (w) => h("div", { class: "risk-panel warning" },
    h("div", { class: "risk-head" }, h("span", { class: "pill-s wrn" }, "Warning"), h("b", {}, `${w.workspace_name || w.workspace_id}: ${plural(w.tables.length)}`)),
    h("div", {}, w.title + (w.detail ? `. ${w.detail}` : "")));
  const others = all.filter((w) => w.result !== "warning");
  showDialog(`Optimize ${plural(total)} in Fabric?`, h("div", { class: "wl-confirm" },
    h("p", { class: "text" }, "Fabric's table maintenance job runs OPTIMIZE with V-Order, then VACUUM keeping each layer's history days, on the workspace's capacity."),
    warned.map(panel),
    h("label", { class: "risk-ack" }, box, "I've looked at these and want to run anyway"),
    others.length ? h("p", { class: "muted-text small" }, others.map((w) => `${w.workspace_name || w.workspace_id}: ${plural(w.tables.length)}, capacity check ${w.result === "pass" ? "passed" : "not known"}.`).join(" ")) : null),
  [btn("Cancel", null, closeDialog, "tool outline"), some, every].filter(Boolean));
}

async function renderHealthTab(body) {
  const m = state.meta;
  body.replaceChildren(h("p", { class: "muted-text" }, "Reading every table's Delta log..."));
  if (m.env !== "dev" && !state.user && !(await signIn())) return;
  const hl = await metaCall("meta_health", { env: m.env });
  if (!hl || state.view !== "metadata" || m.tab !== "health") return;
  const run = async (entities, riskIds, check) => {
    toast(hl.local ? "Compacting on this PC..." : "Starting Fabric's table maintenance...");
    const res = await metaCall("meta_optimize", { env: m.env, entities, risk_ids: riskIds || null }, "Optimize failed");
    if (!res) return;
    const warned = res.filter((r) => r.status === "Failed" && r.warning);
    const failed = res.filter((r) => r.status === "Failed" && !r.warning);
    const queued = res.length - warned.length - failed.length;
    const jobs = `${queued} maintenance job${queued === 1 ? "" : "s"}`;
    if (hl.local) toast(failed.length ? `${failed.length} of ${res.length} didn't start: ${failed[0].message}` : `Compacted ${res.length} table${res.length === 1 ? "" : "s"}`, failed.length > 0);
    else if (warned.length) {
      const parts = [queued ? `Queued ${jobs} in Fabric.` : "", maintainWarnedText(check, warned.length),
        failed.length ? `${failed.length} didn't start: ${failed[0].message}` : ""];
      toast(parts.filter(Boolean).join(" "), failed.length > 0);
    }
    else toast(failed.length ? `${failed.length} of ${res.length} didn't start: ${failed[0].message}` : `Queued ${jobs} in Fabric`, failed.length > 0);
    renderHealthTab(body);
  };
  // A capacity Warning asks first, as X-ray does (Chris, 2026-11-18); Blocked, Pass and Unknown start as before.
  const optimize = async (entities) => {
    if (hl.local) return run(entities);
    const check = await metaCall("meta_optimize_check", { env: m.env, entities }, "Couldn't check the capacity");
    if (!check) return;
    if (!check.workspaces.some((w) => w.result === "warning")) return run(entities, null, check);
    maintainWarningDialog(check, (riskIds) => run(entities, riskIds, check));
  };
  const needs = hl.tables.filter((t) => t.status === "needs_maintenance");
  const unreadable = hl.tables.filter((t) => t.status === "unreadable").length;
  const head = h("div", { class: "card pad" },
    h("div", { class: "row wrap" },
      h("b", {}, hl.local ? `${ENV_LABEL[m.env]} tables on this PC` : `${ENV_LABEL[m.env]} tables in OneLake`),
      needs.length || !unreadable ? h("span", { class: "muted-text" }, needs.length ? `${needs.length} of ${hl.tables.length} would benefit from maintenance` : "Every table is in good shape") : null,
      unreadable ? h("span", { class: "error-text" }, unreadable === 1
        ? "1 table couldn't be read. Its row below says why and how to fix it."
        : `${unreadable} tables couldn't be read. Their rows below say why and how to fix them.`) : null,
      h("span", { class: "spacer" }),
      btn("Refresh", "sync", () => renderHealthTab(body), "tool outline small"),
      btn(hl.local ? "Compact all" : "Optimize all in Fabric", "flash", () => optimize(null), "tool primary small")),
    h("p", { class: "muted-text" }, hl.local
      ? "Optimize here compacts small files and removes versions older than 7 days with delta-rs. V-Order is added in Fabric, after promote."
      : "Optimize runs Fabric's table maintenance job on the workspace's capacity: OPTIMIZE with V-Order (what Direct Lake and the SQL endpoint read fastest), then VACUUM with 7 days' retention. Tables written from this PC have no V-Order until it runs."),
    hl.local ? null : h("label", { class: "check" },
      h("input", { type: "checkbox", checked: hl.optimize_after_run ? true : null, onchange: async (ev) => {
        if (ev.target.checked) {
          const check = await metaCall("meta_optimize_check", { env: m.env }, "Couldn't check the capacity");
          const warned = check?.workspaces.find((w) => w.result === "warning");
          if (!check || (warned && !(await confirmDialog(`Optimize after every ${ENV_LABEL[m.env]} run?`,
            `${warned.title}. After each run, Optimize starts only while the capacity check passes or is unknown. While it shows this warning, the run's tables are listed as "Not optimized (capacity warning)", and you can optimize them from table health once you've looked at the warning.`,
            "Turn on")))) { ev.target.checked = false; return; }
        }
        const res = await metaCall("meta_set_maintenance", { env: m.env, optimize_after_run: ev.target.checked });
        if (res) toast(ev.target.checked ? "Every table a run writes gets optimized in Fabric afterwards" : "Runs no longer optimize afterwards");
      } }),
      `Optimize every table a ${ENV_LABEL[m.env]} run writes, right after the run`));
  const rows = hl.tables.filter((t) => matches(`${t.entity} ${t.layer}`));
  const list = h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["Table", "Layer", "Files", "Size", "Avg file", hl.local ? null : "V-Order", "Versions since VACUUM", "Last optimize", "Advice", ""]
      .filter((c) => c !== null).map((c) => h("th", {}, c)))),
    h("tbody", {}, rows.map((t) => h("tr", {},
      h("td", {}, h("span", { class: "mono" }, t.entity)),
      h("td", {}, t.layer ? layerChip(t.layer) : h("span", { class: "muted-text" }, "run metadata")),
      t.exists ? [
        h("td", { class: "n" }, t.files), h("td", { class: "n" }, sizeText(t.bytes)), h("td", { class: "n" }, sizeText(t.avg_file_bytes)),
        hl.local ? null : h("td", {}, t.v_ordered ? h("span", { class: "pill-s pass" }, "Yes") : h("span", { class: "pill-s wrn" }, "No")),
        h("td", { class: "n" }, t.versions_since_vacuum), h("td", {}, shortDate(t.last_optimize)),
        h("td", {}, t.advice.length ? h("ul", { class: "gate-list" }, t.advice.map((a) => h("li", {}, a))) : h("span", { class: "pill-s pass" }, "Healthy")),
        h("td", {}, btn(hl.local ? "Compact" : "Optimize", null, () => optimize([t.entity]), "tool link small")),
      ] : h("td", { colspan: hl.local ? 8 : 9, class: t.status === "unreadable" ? "error-text" : "muted-text" }, t.message || "Not written in this environment yet")))));
  const jobs = hl.maintenance || [];
  body.replaceChildren(head, list, jobs.length ? h("div", { class: "card pad" }, h("b", {}, "Recent maintenance jobs in Fabric"),
    h("table", { class: "mini" }, h("tbody", {}, jobs.map((j) => h("tr", {},
      h("td", {}, statusPill(fabricStatus(j.status))), h("td", { class: "mono" }, j.table),
      h("td", {}, shortDate(j.started_at)), h("td", { class: "muted-text" }, j.message || "")))))) : null);
}

// ---------- Direct Lake semantic model ----------

async function renderModelTab(body) {
  const m = state.meta;
  if (m.env === "dev" && (state.meta.project?.environments?.dev?.target || "local") === "local") {
    body.replaceChildren(infoCard("Pick Test or Prod",
      "A Direct Lake semantic model reads its tables straight from OneLake, and Dev keeps its tables on this PC. Switch the environment above to Test or Prod, where promote has put the tables in Fabric."));
    return;
  }
  body.replaceChildren(h("p", { class: "muted-text" }, "Reading the layer's tables and relationships..."));
  if (!state.user && !(await signIn())) return;
  const plan = await metaCall("meta_model_plan", { env: m.env, layer: m.modelLayer || null }, "Semantic model not checked");
  if (state.view !== "metadata" || m.tab !== "model") return;
  if (!plan) {  // whether the model exists isn't known, so neither Create nor Update is offered
    body.replaceChildren(h("p", { class: "muted-text" }, "Couldn't check the semantic model in Fabric, so Create and Update are off until it can be read. Open this tab again to retry."));
    return;
  }
  const layer = h("select", { onchange: (e) => { m.modelLayer = e.target.value; renderModelTab(body); } },
    plan.layers.map((l) => h("option", { value: l, selected: l === plan.layer ? true : null }, l)));
  const name = h("input", { value: m.modelName || plan.name, oninput: (e) => { m.modelName = e.target.value; } });
  const result = h("div", {});
  const build = async (replace = false) => {
    toast(`Building ${name.value} in ${plan.workspace_name || "the workspace"}...`);
    let cancelled = false;
    const res = await metaCall("meta_build_model", { env: m.env, layer: plan.layer, name: name.value, replace }, "Model not built",
      (err) => { cancelled = friendlyError(err).cancelled; });
    if (!res) {  // the ember beside the error (a gate's list opens as a dialog over it); a cancelled sign-in plays nothing
      if (!cancelled) forgeEvent("model.build.failed", document.getElementById("toast"));  // motion.js
      return;
    }
    if (res.confirm) {  // the model exists: nothing is written until the user says to replace it
      const c = res.confirm;
      if (await confirmDialog(`Replace ${c.name}?`,
        `${c.name} already exists in ${c.workspace_name || plan.workspace_name || "the workspace"}. Its definition will be replaced by this layer's tables and relationships; measures, tables and relationships added to it in Fabric are lost.`,
        `Replace ${c.name}`, { danger: true })) build(true);
      return;
    }
    toast(`${res.created ? "Created" : "Updated"} ${res.name}`);
    result.replaceChildren(h("div", { class: "pass-box" }, icon("ok", 16),
      `${res.created ? "Created" : "Updated"} ${res.name}: ${res.tables.length} tables, ${res.relationships} relationship${res.relationships === 1 ? "" : "s"}.`,
      res.skipped.length ? ` Not written yet, so left out: ${res.skipped.join(", ")}.` : "",
      res.columns_left_out?.length ? ` Direct Lake can't read these column types, so they were left out: ${res.columns_left_out.map((c) => `${c.table}.${c.column} (${c.type})`).join(", ")}.` : "",
      res.relationships_left_out?.length ? ` Relationships left out because their column isn't in the model: ${res.relationships_left_out.join(", ")}.` : "",
      res.endpoint_refreshed ? "" : " The SQL endpoint refresh didn't run; if a table is missing, refresh the lakehouse in Fabric and build again.",
      res.url ? btn("Open in Fabric", "open", () => openInFabric(res.url), "tool link small") : null));
    forgeEvent("model.built", result.firstChild);  // Forge Spark once Fabric has the model (motion.js)
  };
  body.replaceChildren(
    h("div", { class: "card pad" },
      h("div", { class: "row wrap" }, h("b", {}, "Direct Lake semantic model"), h("span", { class: "spacer" }),
        plan.existing ? btn("Open in Fabric", "open", () => openInFabric(plan.existing.url), "tool outline small") : null,
        btn(plan.existing ? "Update model" : "Create model", "cube", () => build(), "tool primary small")),
      h("p", { class: "muted-text" }, `Reads ${plan.lakehouse} in ${plan.workspace_name || plan.workspace_id} through its SQL analytics endpoint, with no import and no refresh schedule. Relationships come from the metadata. Surrogate keys and the framework's _run_id and _loaded_at columns are hidden; every fact gets a row count measure and a total measure for each numeric column, and no column sums on its own. Updating keeps every column's lineage tag, so reports built on the model keep working.`),
      h("div", { class: "row wrap" }, h("label", { class: "field" }, "Layer", layer), h("label", { class: "field grow" }, "Model name", name)),
      result),
    directLakeCard(plan, () => build()),
    dictionaryCard(plan),
    ossieCard(plan),
    semanticViewCard(plan),
    modelBpaCard(plan),
    h("div", { class: "entity-grid" },
      h("div", { class: "card pad grow" }, h("b", {}, `Tables (${plan.tables.length})`),
        h("table", { class: "mini" }, h("thead", {}, h("tr", {}, ["Table", "Kind", "Delta table", "Entity"].map((c) => h("th", {}, c)))),
          h("tbody", {}, plan.tables.map((t) => h("tr", {}, h("td", { class: "mono" }, t.name),
            h("td", {}, h("span", { class: `pill-s ${t.kind === "dimension" ? "info" : "neu"}` }, t.kind)),
            h("td", { class: "mono" }, t.source), h("td", {}, h("button", { class: "tool link mono", onclick: () => openEntity(t.entity) }, t.entity))))))),
      h("div", { class: "side" }, h("div", { class: "card pad" }, h("b", {}, `Relationships (${plan.relationships.length})`),
        plan.relationships.length ? h("table", { class: "mini" }, h("tbody", {}, plan.relationships.map((r) => h("tr", {},
          h("td", {}, h("div", { class: "rel-cell" }, h("span", { class: "mono" }, `${r.from_table}.${r.from_column}`),
            h("span", { class: "mono" }, `→ ${r.to_table}.${r.to_column}`),
            h("span", { class: "muted-text" }, r.active ? r.why : `inactive: ${r.why}`)))))))
          : h("p", { class: "muted-text" }, "None found. Facts relate to a dimension through a [[lookup]] block, or a column named like the dimension's surrogate key.")))));
}

// ---------- Data dictionary (model_dictionary_service.py) ----------

const DICTIONARY_MAX_BYTES = 5 * 1024 * 1024;
const DICTIONARY_SHOWN = 12;

// Every table, column and measure of the layer's model with its description; null when preview features are hidden.
function dictionaryCard(plan) {
  const title = preview("dataDictionary", h("b", {}, "Data dictionary "));
  if (!title) return null;
  const body = h("div", { class: "dict-body" }, h("p", { class: "muted-text" }, "Reading the descriptions..."));
  const load = async () => {
    const out = await metaCall("meta_model_dictionary", { layer: plan.layer }, "Data dictionary not read");
    if (!out) { fill(body); return; }
    fill(body, dictionaryTable(out));
  };
  load();
  const exportCsv = btn("Export CSV", "arrowDownload", async () => {
    const res = await metaCall("meta_model_dictionary_export", { layer: plan.layer }, "Not exported");
    if (res) toast(`Saved ${res.rows.toLocaleString()} rows to ${res.path}${res.notes?.length ? `. ${res.notes.join(" ")}` : ""}`);
  }, "tool primary small");
  const save = btn("Save to the project", "save", async () => {
    const res = await metaCall("meta_model_dictionary_save", { layer: plan.layer }, "Not saved");
    if (res) toast(`Saved ${res.path}: it shows in Changes, ready to commit`);
  }, "tool outline small");
  return h("div", { class: "card pad dict-card" },
    h("div", { class: "row wrap" }, title, h("span", { class: "spacer" }),
      btn("Import descriptions...", "arrowUpload", () => dictionaryImportDialog(plan, load), "tool outline small"), save, exportCsv),
    h("p", { class: "muted-text" }, `Every table, column and measure in ${plan.name}, with the description people see in the catalog. Export it to edit in Excel, then import it back: the descriptions are saved to the catalog, and Update model writes them into the model, where they show in Power BI's field list.`),
    body,
    howItWorks("dataDictionary", {
      reads: "The metadata project and the catalog (what the About card saved, else the [catalog] text in each entity's file).",
      changes: "Export writes a new CSV in Downloads. Save to the project writes semantic/" + plan.name + ".dictionary.csv for Git. Import saves descriptions and classifications to the catalog after you see every change; a catalog in Fabric uses capacity to save. Nothing in Fabric's model changes until Update model.",
      results: "Update model writes the descriptions into the model's TMDL.",
      more: [["Where the idea comes from", "Elegant BI's data dictionary and description scripts for Tabular Editor (MIT), written here for the metadata project."]],
      anchor: "data-dictionary",
    }));
}

function dictionaryTable(out) {
  const c = out.counts;
  const missing = c.columns - c.columns_described;
  const rows = (items) => items.map((x) => h("tr", {},
    h("td", { class: "mono" }, x.table), h("td", { class: x.name ? "mono" : "muted-text" }, x.name || "(table)"),
    h("td", {}, h("span", { class: `pill-s ${x.kind === "measure" ? "info" : "neu"}` }, x.kind[0].toUpperCase() + x.kind.slice(1))),
    h("td", { class: "mono" }, x.type),
    h("td", { class: x.description ? null : "muted-text" }, x.description || h("i", {}, "No description")),
    h("td", {}, x.classification ? x.classification[0].toUpperCase() + x.classification.slice(1) : "")));
  const tbody = h("tbody", {}, rows(out.items.slice(0, DICTIONARY_SHOWN)));
  const more = out.items.length > DICTIONARY_SHOWN ? btn(`Show all ${out.items.length} rows`, null, (e) => {
    fill(tbody, ...rows(out.items));
    e.currentTarget.remove();
  }, "tool link small") : null;
  return h("div", { class: "dict-out" },
    h("div", { class: "row wrap dict-counts" },
      h("span", {}, h("b", {}, String(c.columns_described)), ` of ${c.columns} columns described`),
      h("span", {}, h("b", {}, String(c.tables_described)), ` of ${c.tables} tables described`),
      h("span", {}, h("b", {}, String(c.measures)), " measures, described for you"),
      missing ? h("span", { class: "pill-s wrn" }, `${missing} column${missing === 1 ? " has" : "s have"} no description`) : null),
    out.problem ? h("p", { class: "warn-text" }, `The catalog couldn't be read, so this shows the text in Git only (${out.problem}).`) : null,
    h("table", { class: "mini" }, h("thead", {}, h("tr", {}, ["Table", "Column or measure", "Kind", "Type", "Description", "Classification"].map((x) => h("th", {}, x)))), tbody),
    more);
}

// Pick an edited dictionary, see every change, then save.
function dictionaryImportDialog(plan, reload) {
  const picked = { text: "", name: "" };
  const input = h("input", { type: "file", accept: ".csv,text/csv", "aria-label": "Data dictionary CSV" });
  const out = h("div", { class: "dict-preview" });
  const go = btn("Save changes", "save", null, "tool primary");
  go.disabled = true;
  input.onchange = async () => {
    const f = input.files[0];
    go.disabled = true;
    if (!f) return;
    if (f.size > DICTIONARY_MAX_BYTES) { fill(out, h("p", { class: "error-text" }, "The file is larger than 5 MB, so it isn't a data dictionary.")); return; }
    Object.assign(picked, { text: await f.text(), name: f.name });
    fill(out, h("p", { class: "muted-text" }, "Reading the file..."));
    const res = await metaCall("meta_model_dictionary_preview", { text: picked.text, layer: plan.layer }, "Not read");
    if (!res) { fill(out); return; }
    const field = { description: "Description", classification: "Classification" };
    const cap = (t) => (/^(public|internal|confidential|restricted)$/.test(t) ? t[0].toUpperCase() + t.slice(1) : t);
    fill(out,
      h("div", { class: "row wrap" }, h("span", { class: "pill-s pass" }, `${res.changes.length} to change`),
        h("span", { class: "pill-s neu" }, `${res.unchanged} unchanged`),
        res.skipped.length ? h("span", { class: "pill-s wrn" }, `${res.skipped.length} skipped`) : null),
      res.changes.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Table or column", "What", "Now", "From the file"].map((x) => h("th", {}, x)))),
        h("tbody", {}, res.changes.map((r) => h("tr", {}, h("td", { class: "mono" }, r.object), h("td", {}, field[r.field]),
          h("td", { class: r.now ? null : "muted-text" }, r.now ? cap(r.now) : h("i", {}, "none")), h("td", {}, cap(r.new))))))
        : h("p", { class: "muted-text" }, "Nothing changes: the catalog already has this text."),
      res.skipped.length ? h("div", { class: "card pad warn-box" }, h("b", {}, `Skipped (${res.skipped.length})`),
        h("ul", { class: "gate-list" }, res.skipped.map((x) => h("li", {}, h("span", { class: "mono" }, x.object), `, row ${x.row}: ${x.reason}`)))) : null,
      res.problem ? h("p", { class: "error-text" }, `The catalog can't be reached, so nothing can be saved now (${res.problem}).`) : null);
    go.textContent = `Save ${res.changes.length} change${res.changes.length === 1 ? "" : "s"}`;
    go.disabled = !res.changes.length || !!res.problem;
  };
  go.onclick = async () => {
    go.disabled = true;
    const res = await metaCall("meta_model_dictionary_import", { text: picked.text, layer: plan.layer, confirmed: true }, "Not saved");
    if (!res) { go.disabled = false; return; }
    closeDialog();
    toast(`Saved ${res.changes.length} change${res.changes.length === 1 ? "" : "s"} from ${picked.name} to the catalog. Update model writes them into the model.`);
    reload();
  };
  showDialog("Import descriptions", h("div", { class: "scroll dict-import" },
    h("label", { class: "field" }, "Edited data dictionary (CSV)", input),
    out,
    h("p", { class: "muted-text" }, "Takes descriptions and classifications for tables and columns. A blank cell leaves the text as it is; measure rows are skipped, since their descriptions are written for you. Saves to the catalog, as the About card does; the model in Fabric doesn't change until you Update model.")),
  [btn("Cancel", null, closeDialog, "tool outline"), go]);
}

// ---------- Direct Lake fallback risk (model_directlake_service.py) ----------

const DL_SEVERITY = { fail: ["Would fail", "fail"], warn: ["Falls back", "wrn"], info: ["Info", "info"] };
const DL_RISK = { will: "fail", might: "wrn", within: "pass", not_checked: "neu" };
// The guardrail findings are shown table by table in the risk table; the other checks stay in their own list.
const DL_IN_RISK = new Set(["Parquet files", "Row groups", "Rows", "Model size", "Table missing"]);

// Will the model fall back to DirectQuery, or fail, before people query it; null when preview features are hidden.
// Reads only. Picking another capacity after a check judges the same numbers again without reading anything.
function directLakeCard(plan, updateModel) {
  const title = preview("directLakeCheck", h("b", {}, "Direct Lake fallback risk "));
  if (!title) return null;
  const m = state.meta;
  const result = h("div", { class: "dl-result", "aria-live": "polite" });
  const model = h("select", { "aria-label": "Model to check" },
    h("option", { value: plan.existing ? plan.name : "" }, plan.existing ? `${plan.name} (this layer's model)` : "Reading the workspace's models..."));
  const sku = h("select", { "aria-label": "Capacity limits" }, h("option", { value: "" }, "Reading the capacity..."));
  const behavior = h("select", { "aria-label": "Fallback setting", disabled: true }, h("option", {}, "Read from the model when you check"));
  const keys = h("input", { type: "checkbox" });
  const checkBtn = btn("Check now", "search", () => run(), "tool primary small");
  let last = null;
  metaCall("meta_model_ossie_models", { env: m.env }, "Models not listed").then((res) => {
    if (!res) return;
    fill(model, ...(res.models.length ? res.models.map((x) => h("option", { value: x.name, selected: x.name === plan.name ? true : null },
      x.name === plan.name ? `${x.name} (this layer's model)` : x.name)) : [h("option", { value: "" }, "No semantic models in this workspace yet")]));
  });
  metaCall("meta_model_directlake_setup", { env: m.env }, "Capacity not read").then((res) => {
    if (!res) return;
    const known = res.sku;
    fill(sku, ...(known ? [] : [h("option", { value: "" }, res.capacity_sku ? `${res.capacity_sku} (no Direct Lake limits known)` : "Pick the capacity")]),
      ...res.skus.map((x) => h("option", { value: x, selected: x === known ? true : null }, x === known ? `${x} (this workspace's capacity)` : x)));
  });
  sku.addEventListener("change", () => {
    if (last?.prediction?.by_sku?.[sku.value]) fill(result, directLakeResult(last, plan, updateModel, sku.value));
  });
  const run = async () => {
    if (!model.value) { toast("Pick a model to check"); return; }
    checkBtn.disabled = true;
    fill(result, h("p", { class: "muted-text" }, `Reading ${model.value}'s definition and its Delta tables...`));
    const out = await metaCall("meta_model_directlake_check", { env: m.env, name: model.value, sku: sku.value, keys: keys.checked }, "Not checked");
    checkBtn.disabled = false;
    last = out;
    if (!out) { fill(result); return; }
    if (out.prediction) fill(behavior, h("option", {}, `${out.prediction.behavior_label} (from the model)`));
    fill(result, directLakeResult(out, plan, updateModel, sku.value));
  };
  return h("div", { class: "card pad dl-card" },
    h("div", { class: "row wrap" }, title, h("span", { class: "spacer" }), checkBtn),
    h("p", { class: "muted-text dl-predicted" }, "Predicted from metadata on this PC. Fabric hasn't run a query."),
    h("div", { class: "row wrap dl-controls" }, h("label", { class: "field" }, "Model", model), h("label", { class: "field" }, "Limits for capacity", sku),
      h("label", { class: "field" }, "Fallback setting", behavior)),
    h("label", { class: "check" }, keys, " Also check relationship keys: unique, and a match for every row (reads those columns from OneLake)"),
    result,
    howItWorks("directLakeCheck", {
      reads: "The model's definition from Fabric (getDefinition, read only), each table's Delta log, its recent commits and Parquet footers from OneLake, and both key columns of each relationship when you tick the box. It starts no Fabric job and runs no query; reading from OneLake uses capacity.",
      changes: "Nothing. Fixes open where you make them: Update model, or Table health to compact a table.",
      results: "Shown here only.",
      more: [["Where the limits come from", "Microsoft's Direct Lake guardrails for the capacity you pick. The idea comes from Microsoft's semantic-link-labs (MIT) and Elegant BI's blank row finder for Tabular Editor (MIT); the check is written for this PC and needs no notebook."],
        ["The forecast", "From each table's recent commits in its Delta log: how fast its Parquet files, row groups and rows grow, and when that passes the limit. It needs at least 3 commits over 7 days, and says how many it used."]],
      anchor: "direct-lake-fallback-risk",
    }));
}

function directLakeFix(f, plan, updateModel) {
  if (f.fix === "update_model") return btn("Update model", "cube", updateModel, "tool link small");
  if (f.fix === "compact") return btn("Compact in Table health", "open", () => goMeta("health"), "tool link small");
  const entity = plan.tables.find((t) => t.name === f.table)?.entity;
  if (f.fix === "show_rows" && entity) return btn("Open the table", "open", () => openEntity(entity), "tool link small");
  return h("span", { class: "muted-text" }, "");
}

// 412 million, 1.5 billion, 48,000: the mockup's way of saying a Direct Lake number.
function dlNum(n) {
  if (n === null || n === undefined) return "";
  if (n >= 1e9) return `${(n / 1e9).toFixed(n % 1e9 ? 1 : 0)} billion`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} million`;
  return Number(n).toLocaleString("en-US");
}

const dlLevel = (have, limit) => (have === null || have === undefined || !limit ? "" : have > limit ? "over" : have >= limit * 0.9 ? "near" : "");
const dlGb = (bytes) => `${Math.round(bytes / 1024 ** 3)} GB`;

function directLakeResult(out, plan, updateModel, pickedSku) {
  if (!out.direct_lake) return h("p", { class: "muted-text" }, `${out.name} has no Direct Lake tables, so there's nothing to check.`);
  const p = out.prediction;
  const picked = (p.by_sku && p.by_sku[pickedSku]) || p;
  const nc = p.read_problem === "no_signin";
  const others = out.findings.filter((f) => !DL_IN_RISK.has(f.check));
  return h("div", { class: "dl-out" },
    nc ? h("div", { class: "dl-banner", role: "status" }, "The tables weren't read, because there's no OneLake sign-in on this PC. Sign in to OneLake in Settings, then check again.") : null,
    directLakeVerdict(p, picked),
    directLakeTiles(picked, nc),
    directLakeRisk(picked, out, nc),
    nc ? null : h("p", { class: "muted-text small" }, "Select a table to see each check, its evidence and the fix."),
    others.length ? h("div", { class: "dl-others" }, h("b", {}, `Other checks (${others.length})`), h("table", { class: "mini" },
      h("thead", {}, h("tr", {}, ["Severity", "Table", "Check", "What we found", "Fix"].map((x) => h("th", {}, x)))),
      h("tbody", {}, others.map((f) => h("tr", {},
        h("td", {}, h("span", { class: `pill-s ${DL_SEVERITY[f.severity][1]}` }, DL_SEVERITY[f.severity][0])),
        h("td", { class: "mono" }, f.table || "Model"), h("td", {}, f.check), h("td", {}, f.message),
        h("td", {}, directLakeFix(f, plan, updateModel))))))) : null,
    p.not_checked.length ? h("details", { class: "dl-unseen" }, h("summary", {}, `What this can't see from this PC (${p.not_checked.length})`),
      h("ul", { class: "gate-list" }, [...p.not_checked, ...p.notes].map((x) => h("li", {}, x)))) : null,
    h("div", { class: "dl-how" }, h("b", {}, "How it's judged."),
      ` Will fall back means a table is past one of ${picked.sku || "the capacity"}'s Direct Lake limits, or its source is a view. Might fall back means it's within 10% of a limit. Not checked means it couldn't be read. The limits are Microsoft's Direct Lake guardrails for the capacity you pick. Nothing is sent to Fabric and nothing is changed.`),
    h("p", { class: "muted-text small" }, `Checked ${relTime(out.checked_at)}.`));
}

// The model's verdict, the table that decides it, and the smallest capacity that fits today and at the horizon.
function directLakeVerdict(p, picked) {
  const v = picked.model;
  const w = p.skus;
  const fits = (s) => (s ? s : "none");
  return h("div", { class: "dl-verdict" },
    h("div", {}, h("span", { class: `pill-s ${DL_RISK[v.verdict]}` }, v.label), " ", h("span", {}, v.message)),
    h("p", { class: "muted-text small" },
      `Smallest capacity within limits: ${fits(w.smallest_today)} today, ${fits(w.smallest_horizon)} in ${w.horizon_days} days at the current rate.`,
      w.every_sku.length ? ` ${w.every_sku.join(", ")} ${picked.will_label.toLowerCase()} on every capacity, because a view or the endpoint's security doesn't depend on the limits.` : ""));
}

function directLakeTiles(picked, nc) {
  const c = picked.counts;
  const tables = (n) => (n === 1 ? "table" : "tables");
  const tile = (label, n, cls, sub) => h("div", { class: "dl-tile" }, h("span", { class: "muted-text" }, label), h("b", { class: cls }, String(n)), h("span", { class: "muted-text" }, sub));
  const s = picked.size;
  const level = s.bytes !== null && s.limit ? dlLevel(s.bytes, s.limit) : "";
  const size = s.bytes === null || s.bytes === undefined
    ? [h("b", { class: "dl-size" }, "Not read"), h("span", { class: "muted-text" }, nc ? "Needs a OneLake sign-in" : "No table was read")]
    : s.limit ? [h("b", { class: "dl-size" }, h("span", { class: level ? `${level}-text` : "" }, dlGb(s.bytes)), ` of ${dlGb(s.limit)}`),
      h("span", { class: "muted-text" }, level === "over" ? "Past the limit" : `Within the limit, ${dlGb(s.limit - s.bytes)} left`)]
      : [h("b", { class: "dl-size" }, `${dlGb(s.bytes)}, no limit on ${picked.sku}`), h("span", { class: "muted-text" }, "Within the limit")];
  return h("div", { class: "dl-tiles" },
    tile(picked.will_label, c.will, "fail-text", tables(c.will)),
    tile("Might fall back", c.might, "warn-text", `${tables(c.might)}, near a limit`),
    tile("Within limits", c.within, "pass-text", tables(c.within)),
    h("div", { class: "dl-tile" }, h("span", { class: "muted-text" }, "Model size on OneLake"), ...size));
}

// One row a table, with its checks, evidence, forecast and fix under it.
function directLakeRisk(picked, out, nc) {
  const reasons = Object.fromEntries(out.unread.map((u) => [u.table, u.reason]));
  const cell = (t, key, limit) => {
    if (t.view || (t.causes || []).includes("missing")) return h("td", { class: "n muted-text" }, "n/a");
    if (!t.read || t[key] === null || t[key] === undefined) return h("td", { class: "n muted-text" }, "Not read");
    const lv = dlLevel(t[key], limit);
    return h("td", { class: "n" }, h("span", { class: lv ? `${lv}-text dl-strong` : "" }, (key === "row_groups" && t.row_groups_estimated ? "about " : "") + dlNum(t[key])),
      limit ? h("span", { class: "dl-lim" }, `of ${dlNum(limit)}`) : null);
  };
  const limits = picked.limits || {};
  const body = h("tbody", {});
  picked.tables.forEach((t, i) => {
    const id = `dl-detail-${i}`;
    const open = !nc && t.status === "will";
    const label = t.status === "will" ? picked.will_label : { might: "Might fall back", within: "Within limits", not_checked: "Not checked" }[t.status];
    const detail = h("tr", { class: "dl-detail", id, hidden: open ? null : true }, h("td", { colspan: 5 }, directLakeChecks(t, reasons[t.table])));
    const row = h("tr", { class: open ? "dl-open" : "" });
    const toggle = h("button", { class: "dl-toggle", "aria-expanded": open ? "true" : "false", "aria-controls": id,
      onclick: () => {
        const now = toggle.getAttribute("aria-expanded") !== "true";
        toggle.setAttribute("aria-expanded", String(now));
        detail.hidden = !now;
        row.classList.toggle("dl-open", now);
      } }, h("span", { class: "dl-caret", "aria-hidden": "true" }), h("b", {}, t.table));
    row.append(h("td", {}, toggle, t.view ? h("span", { class: "muted-text small" }, " view") : null),
      h("td", {}, h("span", { class: `pill-s ${DL_RISK[t.status]}` }, label)),
      cell(t, "rows", limits.rows), cell(t, "files", limits.files), cell(t, "row_groups", limits.row_groups));
    body.append(row, detail);
  });
  return h("table", { class: "mini dl-risk" },
    h("thead", {}, h("tr", {}, h("th", {}, "Table"), h("th", {}, "Risk"), h("th", { class: "n" }, "Rows"), h("th", { class: "n" }, "Parquet files"), h("th", { class: "n" }, "Row groups"))),
    body);
}

function directLakeChecks(t, reason) {
  const chk = (label, evidence, message, fix) => h("div", { class: "dl-chk" }, h("b", {}, label), h("span", {}, evidence),
    h("span", { class: "muted-text" }, message), fix);
  if (!t.read) return h("div", { class: "dl-checks" }, chk("Not read", "", reason || t.reason || "Its Delta table wasn't read.", null));
  if (!t.checks.length) return h("div", { class: "dl-checks" }, chk("Not checked", "", t.reason || "Pick the capacity to compare it with.", null));
  const fix = (c) => (c.fix_text ? h("span", { class: "dl-fix" }, `Fix: ${c.fix_text}`,
    c.fix === "compact" ? [" ", btn("Compact in Table health", null, () => goMeta("health"), "tool small dl-fixbtn")] : null) : null);
  return h("div", { class: "dl-checks" },
    t.checks.map((c) => chk(c.check,
      "have" in c && c.have !== null ? [h("span", { class: c.level === "over" || c.level === "near" ? `${c.level}-text dl-strong` : "" },
        (c.about ? "about " : "") + dlNum(c.have)), ` of ${dlNum(c.limit)}`] : c.evidence,
      c.message, fix(c))),
    t.forecast && t.forecast.status !== "no_history" ? chk("Forecast", t.forecast.commits ? `${t.forecast.commits} commits read` : "No history", t.forecast.message, null) : null);
}

// ---------- Apache Ossie: export and import a semantic model (model_ossie_service.py) ----------

const OSSIE_MAX_BYTES = 20 * 1024 * 1024;
const OSSIE_CHANGE = { add: ["Add", "pass"], change: ["Change", "info"], remove: ["Remove", "fail"], needs_dax: ["Needs DAX", "wrn"] };

// The Open Semantic Interchange card under the Direct Lake one; null when preview features are hidden.
function ossieCard(plan) {
  const title = preview("ossie", h("b", {}, "Open Semantic Interchange (Apache Ossie) "));
  if (!title) return null;
  const m = state.meta;
  const result = h("div", { class: "ossie-result" });
  // Values say where the model is read from: "fabric:<name>" in the environment's workspace, "git:<folder>" in
  // the project's folder (semantic/<name>.SemanticModel, read on this PC with no Fabric call).
  const model = h("select", { "aria-label": "Model to export" },
    h("option", { value: plan.existing ? `fabric:${plan.name}` : "" }, plan.existing ? `${plan.name} (this layer's model)` : "Reading the models..."));
  const sql = h("input", { type: "checkbox", checked: true });
  Promise.all([
    rpc("meta_model_ossie_models", { env: m.env }).catch((err) => ({ error: err.message || String(err) })),
    rpc("meta_model_ossie_folders", {}).catch(() => ({ folders: [] })),
  ]).then(([fab, git]) => {
    const keep = model.value || `fabric:${plan.name}`;
    const opt = (value, label) => h("option", { value, selected: value === keep ? true : null }, label);
    const inFabric = fab.error ? [h("option", { value: "", disabled: true }, fab.error)]
      : fab.models.length ? fab.models.map((x) => opt(`fabric:${x.name}`, x.name === plan.name ? `${x.name} (this layer's model)` : x.name))
        : [h("option", { value: "", disabled: true }, "No semantic models in this workspace yet")];
    model.replaceChildren(
      h("optgroup", { label: fab.workspace_name ? `In Fabric (${fab.workspace_name})` : "In Fabric" }, inFabric),
      git.folders.length ? h("optgroup", { label: "In Git, on this PC" }, git.folders.map((f) => opt(`git:${f.path}`, `${f.name} (${f.path})`))) : null);
    if (!model.value) model.value = git.folders.length ? `git:${git.folders[0].path}` : "";
    // The engine lacks a package the converter needs (PyYAML): only this card is off, with the reason.
    if (git.unavailable) {
      importBtn.disabled = exportBtn.disabled = tmdlBtn.disabled = true;
      fill(result, h("div", { class: "warn-box" }, icon("warning", 16), git.unavailable));
    }
  });
  const exportModel = async () => {
    if (!model.value) { toast("Pick a model to export", true); return; }
    const [where, ...rest] = model.value.split(":");
    const target = rest.join(":");
    fill(result, h("p", { class: "muted-text" }, where === "git"
      ? `Reading ${target} on this PC and converting it...` : `Reading ${target} from Fabric and converting it on this PC...`));
    const out = where === "git"
      ? await metaCall("meta_model_ossie_export_folder", { path: target, translate_dax: sql.checked }, "Not exported")
      : await metaCall("meta_model_ossie_export", { env: m.env, name: target, translate_dax: sql.checked }, "Not exported");
    if (!out) { fill(result); return; }
    fill(result, ossieExportResult(out));
  };
  const importBtn = btn("Import from Ossie...", "arrowUpload", () => ossieImportDialog(plan, result), "tool outline small");
  const tmdlBtn = btn("Ossie file to TMDL...", "document", () => ossieToTmdlDialog(result), "tool outline small");
  const exportBtn = btn("Export to Ossie", "arrowDownload", exportModel, "tool primary small");
  return h("div", { class: "card pad ossie-card" },
    h("div", { class: "row wrap" }, title, h("span", { class: "spacer" }),
      importBtn, tmdlBtn, exportBtn),
    h("p", { class: "muted-text" }, "Ossie is the open format Snowflake, dbt, Databricks and others read for semantic models. Export reads the model's definition from Fabric, or its TMDL kept in Git, and converts it on this PC; nothing in Fabric changes. A model sent to Ossie and back comes back the same, because Power BI details travel in the file's Power BI section."),
    h("div", { class: "row wrap" }, h("label", { class: "field" }, "Model", model),
      h("label", { class: "check" }, sql, "Also write simple measures as portable SQL")),
    result,
    howItWorks("ossie", {
      reads: "The model's definition from Fabric (getDefinition, read only), its TMDL under semantic/ in the project's folder (no Fabric call), or the Ossie file you pick.",
      changes: "Export changes nothing until you save the file into the metadata project's folder. Ossie file to TMDL writes TMDL files under semantic/ in the project's folder only when you say so. Import writes the model's definition in Fabric (or creates a new model) only after you confirm; no refresh runs. Save TMDL to Git writes the model's TMDL files under semantic/ in the project's folder.",
      results: "Files under semantic/ in the metadata project, ready to commit in Source control.",
      more: [["How it converts", "On this PC, with the Apache Ossie Microsoft converter. Ossie file to TMDL and TMDL to Ossie don't call Fabric at all. No AI is involved, and nothing leaves this PC except the Fabric calls above."]],
    }));
}

function ossieReportList(rows) {
  return rows.length ? h("ul", { class: "gate-list" }, rows.map((r) => h("li", {}, h("span", { class: "mono" }, r.object), ` ${r.message}`))) : null;
}

function ossieExportResult(out) {
  const c = out.counts;
  const kept = out.report.filter((r) => r.kind === "kept");
  const lost = out.report.filter((r) => r.kind === "lost");
  const path = h("input", { class: "mono", value: out.path, "aria-label": "Save in the project as" });
  const save = btn("Save to Git", "save", async () => {
    const res = await metaCall("meta_model_ossie_save", { path: path.value.trim(), text: out.yaml }, "Not saved");
    if (res) toast(`Saved ${res.path}. It's in Source control, ready to commit.`);
  }, "tool primary small");
  const copy = btn("Copy", "copyArrowRight", () => attempt(() => navigator.clipboard.writeText(out.yaml).then(() => toast("Copied the Ossie YAML")), "Could not copy"), "tool outline small");
  return h("div", { class: "ossie-export" },
    h("div", { class: "pass-box" }, icon("ok", 16),
      `Converted ${out.model}: ${c.datasets} datasets, ${c.fields} fields, ${c.metrics} measures (${c.portable_metrics} also as SQL), ${c.relationships} relationship${c.relationships === 1 ? "" : "s"}.`,
      kept.length ? ` ${kept.length} thing${kept.length === 1 ? "" : "s"} only Power BI understands ${kept.length === 1 ? "is" : "are"} kept in the Power BI section.` : ""),
    h("div", { class: "ossie-groups" },
      h("div", { class: "card pad" }, h("b", {}, "Portable (any Ossie tool)"),
        h("p", { class: "muted-text" }, `Tables, columns, keys, ${c.relationships} relationships, descriptions, and ${c.portable_metrics} measure${c.portable_metrics === 1 ? "" : "s"} as SQL.`)),
      h("div", { class: "card pad" }, h("b", {}, `Kept for Fabric only (${kept.length})`),
        ossieReportList(kept) || h("p", { class: "muted-text" }, "Nothing: the whole model is portable.")),
      h("div", { class: "card pad" }, h("b", {}, `Not converted (${lost.length})`),
        ossieReportList(lost) || h("p", { class: "muted-text" }, "Nothing. Every object is either portable or kept."))),
    out.round_trip ? h("div", {}, h("b", {}, "Round-trip check "),
      out.round_trip.length
        ? h("div", { class: "warn-box" }, icon("warning", 16), "Reading this file back doesn't give the same model: ", ossieReportList(out.round_trip))
        : h("span", { class: "muted-text" }, "Reading this file back gives the same model.")) : null,
    h("pre", { class: "ossie-yaml mono" }, out.yaml.split("\n").slice(0, 40).join("\n") + (out.yaml.split("\n").length > 40 ? "\n..." : "")),
    h("div", { class: "row wrap" }, h("label", { class: "field grow" }, "Save in the project as", path), save, copy));
}

// Pick an Ossie file, choose update, replace or new, see every change, then confirm.
function ossieImportDialog(plan, result) {
  const m = state.meta;
  const picked = { text: "", name: "" };
  const input = h("input", { type: "file", accept: ".yaml,.yml,.json", "aria-label": "Ossie file" });
  const projectPath = h("input", { class: "mono", placeholder: "semantic/model.ossie.yaml", "aria-label": "Ossie file in the project" });
  const mode = h("select", { "aria-label": "Into" },
    plan.existing ? h("option", { value: "update", selected: true }, `Update ${plan.name} (keeps its roles and hierarchies)`) : null,
    plan.existing ? h("option", { value: "replace" }, `Replace ${plan.name}`) : null,
    h("option", { value: "new", selected: plan.existing ? null : true }, `New model in ${plan.workspace_name || "the workspace"}`));
  const name = h("input", { placeholder: "The name in the file", "aria-label": "New model name" });
  const nameField = h("label", { class: "field grow", hidden: !!plan.existing }, "New model name", name);
  const out = h("div", { class: "ossie-preview" });
  const keep = new Map();  // removals the person unticked "Keep it" on
  const go = btn("Import", "arrowUpload", null, "tool primary");
  go.disabled = true;
  const target = () => (mode.value === "new" ? name.value.trim() : plan.name);
  const refresh = async () => {
    go.disabled = true;
    nameField.hidden = mode.value !== "new";
    if (!picked.text) return;
    fill(out, h("p", { class: "muted-text" }, "Checking what changes..."));
    const res = await metaCall("meta_model_ossie_preview", { env: m.env, text: picked.text, name: target(), mode: mode.value, layer: plan.layer }, "Not checked");
    if (!res) { fill(out); return; }
    keep.clear();
    const lost = res.report.filter((r) => r.kind === "lost");
    fill(out,
      h("b", {}, res.exists ? `What changes in ${res.name}` : `What ${res.name} gets`),
      res.changes.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Change", "Object", "Detail"].map((c) => h("th", {}, c)))),
        h("tbody", {}, res.changes.map((r) => {
          const [label, cls] = OSSIE_CHANGE[r.change] || [r.change, "neu"];
          const kept = res.removable.includes(r.object) ? h("label", { class: "check" },
            h("input", { type: "checkbox", checked: true, onchange: (e) => { if (e.target.checked) keep.delete(r.object); else keep.set(r.object, true); } }), "Keep it") : null;
          return h("tr", { class: r.change === "needs_dax" ? "warn-row" : null },
            h("td", {}, h("span", { class: `pill-s ${cls}` }, label)), h("td", { class: "mono" }, r.object),
            h("td", {}, r.change === "remove" ? h("span", {}, res.removable.includes(r.object) ? "Not in the Ossie file. " : "", kept) : r.detail));
        }))) : h("p", { class: "muted-text" }, "Nothing changes: the model already matches the file."),
      lost.length ? h("div", { class: "card pad warn-box" }, h("b", {}, `Can't carry over (${lost.length})`), ossieReportList(lost)) : null);
    go.textContent = `${{ update: "Update", replace: "Replace", new: "Create" }[mode.value]} ${res.name}...`;
    go.disabled = false;
  };
  input.onchange = async () => {
    const f = input.files[0];
    if (!f) return;
    if (f.size > OSSIE_MAX_BYTES) { fill(out, h("p", { class: "error-text" }, "The file is larger than 20 MB, so it isn't an Ossie model.")); return; }
    Object.assign(picked, { text: await f.text(), name: f.name });
    projectPath.value = "";
    refresh();
  };
  const readProject = btn("Read", null, async () => {
    const res = await metaCall("meta_model_ossie_read", { path: projectPath.value.trim() }, "Not read");
    if (!res) return;
    Object.assign(picked, { text: res.text, name: res.path });
    input.value = "";
    refresh();
  }, "tool outline small");
  mode.onchange = refresh;
  name.onchange = refresh;
  go.onclick = async () => {
    const params = { env: m.env, text: picked.text, name: target(), mode: mode.value, layer: plan.layer, remove: [...keep.keys()] };
    const ask = await metaCall("meta_model_ossie_import", params, "Not imported");
    if (!ask?.confirm) return;
    const c = ask.confirm, n = c.counts || {};
    const parts = [n.add && `${n.add} added`, n.change && `${n.change} changed`, n.remove && `${n.remove} removed`].filter(Boolean).join(", ") || "no changes";
    const blanks = n.needs_dax ? ` ${n.needs_dax} measure${n.needs_dax === 1 ? " is" : "s are"} BLANK() until someone writes ${n.needs_dax === 1 ? "it" : "them"} in DAX.` : "";
    const ok = await confirmDialog(`${{ update: "Update", replace: "Replace", new: "Create" }[c.mode]} ${c.name}?`,
      c.mode === "replace"
        ? `${c.name} in ${c.workspace_name || "the workspace"} becomes exactly what ${picked.name} holds (${parts}). Measures, roles and hierarchies added in Fabric that the file doesn't have are lost.${blanks} No refresh runs.`
        : c.mode === "new"
          ? `Creates ${c.name} in ${c.workspace_name || "the workspace"} from ${picked.name}.${blanks} No refresh runs.`
          : `${c.name} in ${c.workspace_name || "the workspace"} gets ${parts} from ${picked.name}. Its roles, hierarchies and the measures you kept stay as they are.${blanks} No refresh runs.`,
      `${{ update: "Update", replace: "Replace", new: "Create" }[c.mode]} ${c.name}`, { danger: c.mode === "replace" });
    if (!ok) return;
    toast(`Writing ${c.name} in Fabric...`);
    const res = await metaCall("meta_model_ossie_import", { ...params, confirmed: true }, "Not imported");
    if (res) fill(result, ossieImportResult(res, picked.name));
  };
  showDialog("Import from Ossie", h("div", { class: "scroll ossie-import" },
    h("div", { class: "row wrap" }, h("label", { class: "field grow" }, "Ossie file on this PC", input),
      h("label", { class: "field grow" }, "Or in the project", h("span", { class: "row tight" }, projectPath, readProject))),
    h("div", { class: "row wrap" }, h("label", { class: "field grow" }, "Into", mode), nameField),
    out,
    h("p", { class: "muted-text" }, "Writes the model definition only. No refresh runs.")),
  [btn("Cancel", null, closeDialog, "tool outline"), go]);
}

// The Round-trip check: what the TMDL will hold, by kind, with nothing dropped unlisted (PB-110 mockup).
function ossieRoundTrip(res) {
  const c = res.counts || {};
  const kept = res.report.filter((r) => r.kind === "kept");
  const lost = [...res.report.filter((r) => r.kind === "lost"),
    ...(res.changes || []).filter((r) => r.change === "needs_dax").map((r) => ({ object: r.object, message: "its SQL has no DAX equivalent, so the TMDL measure has no expression and shows blank until you add DAX" }))];
  const total = (c.tables || 0) + (c.columns || 0) + (c.measures || 0) + (c.relationships || 0);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  return h("div", { class: "ossie-groups ossie-roundtrip", role: "list" },
    h("div", { class: "card pad", role: "listitem" }, h("b", {}, `Kept (${total})`),
      h("p", { class: "muted-text" }, `${plural(c.tables || 0, "table")}, ${plural(c.columns || 0, "column")}, ${plural(c.relationships || 0, "relationship")}, ${plural(c.measures || 0, "measure")}, descriptions.`)),
    h("div", { class: "card pad", role: "listitem" }, h("b", {}, `Kept in the Power BI section (${kept.length})`),
      ossieReportList(kept) || h("p", { class: "muted-text" }, "None.")),
    h("div", { class: `card pad${lost.length ? " warn-box" : ""}`, role: "listitem" }, h("b", {}, `Can't be carried (${lost.length})`),
      ossieReportList(lost) || h("p", { class: "muted-text" }, "None. Nothing was dropped.")));
}

// Ossie file to TMDL in this project, on this PC with no Fabric call (meta_model_ossie_to_tmdl, PB-110).
function ossieToTmdlDialog(result) {
  const picked = { text: "", name: "" };
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const input = h("input", { type: "file", accept: ".yaml,.yml,.json", "aria-label": "Ossie file" });
  const projectPath = h("input", { class: "mono", placeholder: "semantic/orders.ossie.yaml", "aria-label": "Ossie file in the project" });
  const folder = h("input", { placeholder: "The name in the file", "aria-label": "Model folder name" });
  const where = h("span", { class: "mono muted-text ossie-folder" });
  const out = h("div", { class: "ossie-preview" });
  const keep = new Map();  // removals the person unticked "Keep it" on
  const go = btn("Write TMDL", "save", null, "tool primary");
  go.disabled = true;
  let last = null;
  const refresh = async () => {
    go.disabled = true;
    last = null;
    if (!picked.text) return;
    fill(out, h("p", { class: "muted-text" }, "Converting on this PC..."));
    let res;
    try {
      res = (await rpc("meta_model_ossie_to_tmdl", { text: picked.text, name: folder.value.trim(), remove: [...keep.keys()] })).confirm;
    } catch (err) {
      fill(where);
      fill(out, h("div", { class: "warn-box", role: "alert" }, icon("warning", 16),
        h("span", {}, h("b", {}, "Can't convert this file, so nothing was written. "), err.message || String(err))));
      return;
    }
    last = res;
    if (!folder.value.trim()) folder.placeholder = res.name;
    fill(where, `${res.folder}/ `, h("b", { class: res.exists ? "error-text" : null }, res.exists ? "already there" : "(new)"));
    const removable = res.removable.filter((o) => !res.changes.some((r) => r.object === o));
    const rows = [...res.changes, ...removable.map((o) => ({ change: "remove", object: o, detail: "" })), ...[...keep.keys()].filter((o) => !removable.includes(o)).map((o) => ({ change: "remove", object: o, detail: "" }))];
    const n = (k) => res.changes.filter((r) => r.change === k).length;
    fill(out,
      res.exists ? h("b", {}, `What changes in ${res.folder}/`) : h("p", { class: "counts" }, h("b", {}, "From the file: "),
        `${plural(res.counts.tables, "table")}, ${plural(res.counts.columns, "column")}, ${plural(res.counts.measures, "measure")}, ${plural(res.counts.relationships, "relationship")}.`),
      res.exists ? (rows.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Change", "Object", "Detail"].map((c) => h("th", {}, c)))),
        h("tbody", {}, rows.map((r) => {
          const [label, cls] = OSSIE_CHANGE[r.change] || [r.change, "neu"];
          const kept = r.change === "remove" ? h("label", { class: "check" },
            h("input", { type: "checkbox", checked: !keep.has(r.object), onchange: (e) => { if (e.target.checked) keep.delete(r.object); else keep.set(r.object, true); refresh(); } }), "Keep it") : null;
          return h("tr", { class: r.change === "needs_dax" ? "warn-row" : null },
            h("td", {}, h("span", { class: `pill-s ${cls}` }, label)), h("td", { class: "mono" }, r.object),
            h("td", {}, r.change === "remove" ? h("span", {}, "Not in the Ossie file. ", kept) : r.detail));
        }))) : h("p", { class: "muted-text" }, "Nothing changes, because the TMDL already matches the file.")) : null,
      res.exists ? h("p", { class: "muted-text counts" }, `${n("add")} added, ${n("change")} changed, ${keep.size} removed (${removable.length - keep.size} kept). ${res.unchanged} file${res.unchanged === 1 ? "" : "s"} unchanged.`) : null,
      h("p", {}, h("b", {}, "Round-trip check "), h("span", { class: "muted-text" }, "What the TMDL will hold. Nothing is dropped without being listed here.")),
      ossieRoundTrip(res),
      res.removed.length ? h("p", { class: "muted-text" }, `Removes ${res.removed.length} TMDL file${res.removed.length === 1 ? "" : "s"} for what you chose to take out: `, h("span", { class: "mono" }, res.removed.join(", "))) : null);
    go.textContent = res.exists ? `Replace TMDL in ${res.name}...` : "Write TMDL";
    go.disabled = false;
  };
  input.onchange = async () => {
    const f = input.files[0];
    if (!f) return;
    if (f.size > OSSIE_MAX_BYTES) { fill(out, h("p", { class: "error-text" }, "The file is larger than 20 MB, so it isn't an Ossie model.")); return; }
    Object.assign(picked, { text: await f.text(), name: f.name });
    projectPath.value = "";
    keep.clear();
    refresh();
  };
  const readProject = btn("Read", null, async () => {
    const res = await metaCall("meta_model_ossie_read", { path: projectPath.value.trim() }, "Not read");
    if (!res) return;
    Object.assign(picked, { text: res.text, name: res.path });
    input.value = "";
    keep.clear();
    refresh();
  }, "tool outline small");
  folder.onchange = () => { keep.clear(); refresh(); };
  go.onclick = async () => {
    const res = last;
    if (!res) return;
    if (res.exists) {
      const needs = res.changes.filter((r) => r.change === "needs_dax").map((r) => r.object);
      const n = (k) => res.changes.filter((r) => r.change === k).length;
      const FILE_CHANGE = { new: ["New", "pass"], changed: ["Changed", "info"], same: ["Same", "neu"], removed: ["Removed", "fail"] };
      const files = h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "File"), h("th", {}, "Change"))),
        h("tbody", {}, (res.file_changes || []).map((f) => h("tr", {}, h("td", { class: "mono" }, f.path),
          h("td", {}, h("span", { class: `pill-s ${(FILE_CHANGE[f.change] || [f.change, "neu"])[1]}` }, (FILE_CHANGE[f.change] || [f.change])[0]))))));
      const ok = await confirmDialog(`Replace TMDL in ${res.name}?`,
        h("div", {}, h("p", {}, `${res.folder}/ is already in this project. Writing replaces the files below, with ${n("add")} added, ${n("change")} changed and ${keep.size} removed, and keeps the ${res.removable.length - keep.size} the Ossie file doesn't have, as you ticked.${needs.length ? ` ${needs.length} measure${needs.length === 1 ? " has" : "s have"} no expression yet (${needs.join(", ")}).` : ""} Nothing in Fabric changes, and you can undo it in Source control before you commit.`),
          h("div", { class: "scroll" }, files)),
        "Replace TMDL", { danger: true });
      if (!ok) return;
    }
    const done = await metaCall("meta_model_ossie_to_tmdl", { text: picked.text, name: folder.value.trim(), remove: [...keep.keys()], confirmed: true, token: res.token }, "TMDL not written");
    if (!done) return;
    closeDialog();
    const needs = done.changes.filter((r) => r.change === "needs_dax").map((r) => r.object);
    const n = (k) => done.changes.filter((r) => r.change === k).length;
    fill(result, h("div", { class: "ossie-done" },
      h("div", { class: "pass-box", role: "status" }, icon("ok", 16),
        `Wrote ${done.folder}/ from ${picked.name}${done.exists ? `, with ${n("add")} added, ${n("change")} changed and ${done.removable.length - keep.size} kept` : ""}. It's in Source control, ready to commit.`),
      needs.length ? h("div", { class: "card pad warn-box" }, `${needs.length} measure${needs.length === 1 ? " has" : "s have"} no expression yet, so ${needs.length === 1 ? "it shows" : "they show"} blank until you add DAX: `,
        h("span", { class: "mono" }, needs.join(", "))) : null,
      h("p", {}, h("b", {}, "Round-trip check")), ossieRoundTrip(done)));
  };
  showDialog("Ossie file to TMDL", h("div", { class: "scroll ossie-import ossie-to-tmdl" },
    h("div", { class: "row wrap" }, h("label", { class: "field grow" }, "Ossie file on this PC", input),
      h("label", { class: "field grow" }, "Or in the project", h("span", { class: "row tight" }, projectPath, readProject))),
    h("div", { class: "row wrap" }, h("label", { class: "field grow" }, "Model folder name", folder), where),
    out,
    h("p", { class: "muted-text" }, "Writes TMDL files under semantic/ on this PC. Nothing in Fabric changes, and nothing is committed until you commit in Source control.")),
  [btn("Cancel", null, closeDialog, "tool outline"), go]);
}

function ossieImportResult(res, file) {
  const n = res.counts || {};
  const saveTmdl = btn("Save TMDL to Git", "save", async () => {
    const out = await metaCall("meta_model_tmdl_save", { env: state.meta.env, name: res.name }, "TMDL not saved");
    if (out) toast(`Saved ${out.files} file${out.files === 1 ? "" : "s"} to ${out.folder}${out.removed.length ? `, removed ${out.removed.length} Fabric no longer has` : ""}. It's in Source control, ready to commit.`);
  }, "tool primary small");
  return h("div", { class: "ossie-done" },
    h("div", { class: "pass-box" }, icon("ok", 16),
      `${res.created ? "Created" : res.mode === "replace" ? "Replaced" : "Updated"} ${res.name} from ${file}: ${[n.add && `${n.add} added`, n.change && `${n.change} changed`, n.remove && `${n.remove} removed`].filter(Boolean).join(", ") || "no changes"}.`,
      res.url ? btn("Open in Fabric", "open", () => openInFabric(res.url), "tool link small") : null),
    res.needs_dax.length ? h("div", { class: "card pad warn-box" },
      `${res.needs_dax.length} measure${res.needs_dax.length === 1 ? " needs" : "s need"} DAX before reports use ${res.needs_dax.length === 1 ? "it" : "them"}; ${res.needs_dax.length === 1 ? "it shows" : "they show"} blank until then: `,
      h("span", { class: "mono" }, res.needs_dax.join(", "))) : null,
    h("div", { class: "row wrap" }, h("span", { class: "muted-text grow" }, `Keep Git in step: the model's TMDL, read back from Fabric, goes to semantic/${res.name}.SemanticModel/ next to the Ossie file.`), saveTmdl));
}

// ---------- Fabric Semantic View: the publish steps (OSSIE-005b, model_ossie_service.py) ----------

// KratosForge never publishes a view or calls Fabric from this card: it reads the files saved in the project's
// folder (meta_model_semantic_view_status), and its only buttons copy text, open that folder on this PC
// (meta_model_semantic_view_reveal, audited), or save
// the view's files there (meta_model_semantic_view_save, previewed and confirmed). Each step links the Learn
// section it follows (docs/semantic-model-ossie.md#publish-the-view-in-fabric-preview).
const SV_LEARN = {
  create: "https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-create#create-a-view-with-the-starter-file",
  upload: "https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-create#upload-existing-yaml-files",
  save: "https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-create#edit-and-save",
  publish: "https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-publish#publish-a-view",
  check: "https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-publish#check-the-result",
  createAccess: "https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-create#prerequisites",
  publishAccess: "https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-publish#prerequisites",
  overview: "https://learn.microsoft.com/en-us/fabric/fundamentals/semantic-views-overview",
};
const SV_MAX_FILES = 20;  // ossie_profiles.FABRIC_MAX_FILES
// What the files leave out, by construct (semantic_view.STRIPPED), for the "left out" line.
const SV_LEFT_OUT = { primary_key: ["primary key", "primary keys"], unique_keys: ["unique key list", "unique key lists"],
  ai_context: ["ai_context note", "ai_context notes"], fields: ["dataset field", "dataset fields"],
  custom_extensions: ["Power BI extension item", "Power BI extension items"], datatype: ["metric datatype", "metric datatypes"],
  query_source: ["query source", "query sources"] };

function svLearn(url, label, name) {
  return h("a", { href: "#", class: "learn-more sv-learn", title: url, "aria-label": name || null,
    onclick: (e) => { e.preventDefault(); openLink(url); } }, label, h("span", { "aria-hidden": "true" }, " ↗"));
}

function svSize(bytes) {
  return bytes < 1000 ? `${bytes} bytes` : bytes < 1e6 ? `${Math.round(bytes / 1000)} KB` : `${(bytes / 1e6).toFixed(1)} MB`;
}

const svCount = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// "4 datasets, 3 relationships" or "9 metrics, all DAX": what one file holds.
// "a", "a and b", "a, b and c"
function svList(items) {
  return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function svHolds(f) {
  if (f.unreadable) return "Not readable as one YAML object. The check above says why.";
  const parts = [];
  if (f.datasets) parts.push(svCount(f.datasets, "dataset", "datasets"));
  if (f.relationships) parts.push(svCount(f.relationships, "relationship", "relationships"));
  if (f.metrics) parts.push(svCount(f.metrics, "metric", "metrics"), f.dax_metrics === f.metrics ? "all DAX" : `${f.dax_metrics} with DAX`);
  return parts.join(", ") || "Version and name only";
}

// "2 primary keys and 1 ai_context note", from the report's rows the files leave out.
function svLeftOutText(rows) {
  const counts = new Map();
  for (const r of rows) {
    const key = r.excluded ? (r.kind === "relationship" ? "relationship" : r.kind === "measure" ? "metric" : "excluded") : (SV_LEFT_OUT[r.construct] ? r.construct : "other");
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const names = { ...SV_LEFT_OUT, relationship: ["multi-column relationship", "multi-column relationships"],
    metric: ["metric without DAX", "metrics without DAX"], excluded: ["other object", "other objects"], other: ["other key", "other keys"] };
  const parts = [...counts].map(([k, n]) => svCount(n, ...names[k]));
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}` : parts[0];
}

function svReportDialog(view, rows) {
  showDialog(`What the ${view} view leaves out`, h("div", { class: "scroll sv-report" },
    h("p", { class: "muted-text" }, "Each row names the documented rule it follows. Nothing here is sent to Fabric."),
    h("table", { class: "mini" }, h("thead", {}, h("tr", {}, h("th", {}, "Object"), h("th", {}, "Why"))),
      h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono" }, r.object),
        h("td", {}, r.reason, r.source ? [" ", svLearn(r.source, "Learn", `Learn: ${r.rule}`)] : null)))))),
    [btn("Close", null, closeDialog, "tool outline")]);
}

// Save view files (OSSIE-005a's engine call): previews, lists every file written and removed in the confirm, and
// writes only with the preview's token. A removal makes it a danger confirm.
async function svSaveFiles(view, modelPath, split, done) {
  const params = { view_name: view, path: modelPath, split };
  const res = await metaCall("meta_model_semantic_view_save", params, "View files not saved");
  if (!res) return;
  const c = res.confirm;
  const names = c.files.map((f) => f.split("/").pop());
  const removed = c.removed.map((f) => f.slice(c.folder.length + 1));
  const ok = await confirmDialog(`Save the ${view} view's files?`,
    `Writes ${svList(names)} to ${c.folder}/ in the project's folder, made on this PC from ${modelPath}. `
    + (removed.length ? `Deletes ${svList(removed)} there, because Fabric merges every .yaml file in a view's folder into the view. ` : "")
    + "Nothing is sent to Fabric.", removed.length ? "Save and delete" : "Save view files", { danger: removed.length > 0 });
  if (!ok) return;
  const out = await metaCall("meta_model_semantic_view_save", { ...params, confirmed: true, token: c.token }, "View files not saved");
  if (!out) return;
  toast(`Saved the ${view} view's files in ${out.folder}/. Commit them from Source control.`);
  done(view);
}

// State (a): nothing saved yet, so only "Save the view's files first", with Save view files.
function svNoFiles(folders, plan, done) {
  if (!folders.length) {
    return h("div", { class: "sv-first" }, h("b", {}, "Save the view's files first"),
      h("p", { class: "muted-text" }, "A view's files are made on this PC from a model kept in the project's folder, under semantic/. Save the model's TMDL to Git first (Import from Ossie or Ossie file to TMDL on the Apache Ossie card), then save the view's files here."));
  }
  const pick = h("select", { "aria-label": "Model to make the view from" },
    folders.map((f) => h("option", { value: f.path, selected: f.name === plan.name ? true : null }, `${f.name} (${f.path})`)));
  const save = btn("Save view files", "save", () => {
    const f = folders.find((x) => x.path === pick.value) || folders[0];
    svSaveFiles(f.name, f.path, "schema_metrics", done);
  }, "tool primary small");
  return h("div", { class: "sv-first" }, h("b", {}, "Save the view's files first"),
    h("p", { class: "muted-text" }, "KratosForge makes the view's .yaml files on this PC from a model in the project's folder and saves them in semantic-views\\<view>\\. Then this card shows the steps to publish them in Fabric."),
    h("div", { class: "row wrap" }, h("label", { class: "field" }, "Model", pick), save));
}

function svChecks(st) {
  const v = st.validation;
  if (st.valid) {
    const bytes = v.counts.yaml_bytes;
    return h("div", { class: "sv-check" }, h("span", { class: "pill-s pass" }, "Ready"),
      h("span", {}, `Passed Fabric's documented checks: version 0.2.0.dev0, DAX on every metric, single-column relationships, ${v.counts.yaml_files} of ${SV_MAX_FILES} files and ${svSize(bytes)} of 16 MB.`));
  }
  const problems = [...(v?.diagnostics || []).map((d) => ["", d]),
    ...Object.entries(v?.files || {}).flatMap(([file, r]) => r.diagnostics.map((d) => [file, d]))];
  return h("div", { class: "sv-check-fail" },
    h("div", { class: "sv-check" }, h("span", { class: "pill-s fail" }, "Failed"),
      h("span", {}, problems.length ? `${svCount(problems.length, "check", "checks")} failed, so Fabric would refuse to publish these files. Fix each one, or save the view's files again.`
        : "The files weren't checked, so Fabric may refuse them. Bring the view to 20 files and 16 MB or less.")),
    problems.length ? h("ul", { class: "gate-list" }, problems.slice(0, 12).map(([file, d]) => h("li", {},
      file ? h("span", { class: "mono" }, `${file} `) : null, d.path && d.path !== "$" ? h("span", { class: "mono" }, `${d.path} `) : null,
      `${d.message}. `, h("span", { class: "muted-text" }, `Fix: ${d.fix}`)))) : null,
    problems.length > 12 ? h("p", { class: "muted-text" }, `And ${problems.length - 12} more.`) : null);
}

function svWarnings(st) {
  const v = st.validation;
  const all = [...(v?.warnings || []), ...Object.values(v?.files || {}).flatMap((r) => r.warnings)];
  return all.length ? h("ul", { class: "gate-list sv-notes" }, all.map((d) => h("li", {}, h("span", { class: "pill-s wrn" }, "Note"), `${d.message}. `,
    h("span", { class: "muted-text" }, d.fix)))) : null;
}

function svSteps(view, lh, ok) {
  const wait = ok ? null : h("span", { class: "sv-wait" }, "Fix the files first");
  const step = (n, text, link, label, dim) => h("li", { class: dim ? "sv-dim" : null },
    h("span", { class: "sv-num", "aria-hidden": "true" }, String(n)),
    h("div", {}, h("div", {}, text), dim ? wait.cloneNode(true) : null, svLearn(link, `Learn: ${label}`)));
  const b = (t) => h("b", {}, t);
  const m = (t) => h("span", { class: "mono" }, t);
  return h("ol", { class: "sv-steps" },
    step(1, [`Open ${lh || "the lakehouse"} in Fabric, in the Lakehouse view rather than the SQL analytics endpoint. If `, b("Context > Semantic Views"), " has no ", b(view),
      " folder, select ", b("New semantic view"), ` on the ribbon, enter ${view} as the name, then select `, b("Create"), "."], SV_LEARN.create, "Create a view", false),
    step(2, [b("Upload."), " In the Explorer pane, right-click the ", b(view), " folder, select ", b("Upload > Upload files"),
      `, and choose the ${ok ? svCount(ok, "file", "files") : "files"} above. Uploaded files are saved right away. If Fabric made a starter `, m(".yaml"),
      " file, delete it, because every ", m(".yaml"), " file in the folder joins the view."], SV_LEARN.upload, "Upload files", !ok),
    step(3, [b("Save."), " Only if you change a file in Fabric. Open it, edit it, then select ", b("Save"), ". Saving writes the file and doesn't check it or update the model."],
      SV_LEARN.save, "Edit and save", !ok),
    step(4, [b("Publish."), " Right-click the ", b(view), " folder, select ", b("Publish"), ", then select ", b("Publish"), " in the dialog. Only saved changes are published."],
      SV_LEARN.publish, "Publish a view", !ok),
    step(5, [b("Check the result."), ` Open the ${view} model Fabric made, and read any warnings the publish gave.`], SV_LEARN.check, "Check the result", !ok));
}

function svBody(st, plan, folders, reload) {
  const view = st.view;
  const ok = st.valid ? st.files_summary.length : 0;
  const source = folders.find((f) => f.name === view);
  // The lakehouse and workspace only when this layer's model is the view's source: plan names what that model reads.
  const lh = source && source.name === plan.name && plan.lakehouse ? plan.lakehouse : "";
  const ws = lh ? plan.workspace_name || "" : "";
  const where = `${lh ? `${lh} > ` : ""}Context/Semantic Views/${view}/`;
  const other = view.toLowerCase() !== view ? view.toLowerCase() : view.toUpperCase();
  const s = st.source;
  const left = s?.same ? s.left_out : [];
  const resave = source ? btn("Save view files", "save", () => svSaveFiles(view, source.path, s?.split || "schema_metrics", reload), "tool outline small") : null;
  const open = btn("Open folder", "folderOpen", async () => {
    const res = await metaCall("meta_model_semantic_view_reveal", { view_name: view }, "Couldn't open the folder");
    if (res?.shown) toast(`Opened ${st.folder} in File Explorer.`);
  }, "tool outline small");
  const copy = btn("Copy path", "copyArrowRight", () => copyText(where, "Path"), "tool outline small");
  return [
    h("p", { class: "muted-text" }, `KratosForge doesn't publish semantic views, and nothing on this card writes to Fabric. You upload the files below to the lakehouse yourself, then publish the view there. Fabric then makes a read-only Direct Lake model named ${view}, and the view's files stay the source of truth.`),
    h("h3", { class: "sv-h" }, "1. The files"),
    svChecks(st),
    h("table", { class: "mini sv-files" }, h("thead", {}, h("tr", {}, h("th", {}, "File"), h("th", {}, "Holds"), h("th", {}, "Size"))),
      h("tbody", {}, st.files_summary.map((f) => h("tr", {}, h("td", { class: "mono" }, f.path), h("td", {}, svHolds(f)), h("td", { class: "n" }, svSize(f.bytes)))))),
    svWarnings(st),
    h("div", { class: "row wrap sv-folder" }, h("span", { class: "mono muted-text grow" }, `${st.folder.replace(/\//g, "\\")}\\ in the project folder`), open),
    s && !s.same ? h("div", { class: "row wrap sv-line" }, h("span", { class: "pill-s info" }, "Changed"),
      h("span", { class: "grow" }, `These files aren't what ${source.name} in ${source.path} gives now. Save the view's files again to bring them in step.`), resave) : null,
    left.length ? h("p", { class: "sv-line" }, h("span", { class: "pill-s wrn" }, `${left.length} left out`),
      ` The view publishes without ${svLeftOutText(left)}, because Fabric's YAML reference omits them or doesn't support them, so the files leave them out. `,
      h("a", { href: "#", class: "learn-more", onclick: (e) => { e.preventDefault(); svReportDialog(view, left); } }, "See the report")) : null,
    h("h3", { class: "sv-h" }, "2. Where they go"),
    h("div", { class: "sv-path" }, h("span", { class: "mono grow" }, where), copy),
    h("p", { class: "muted-text sv-small" }, lh ? `Lakehouse ${lh}${ws ? ` in workspace ${ws}` : ""}, which the view's datasets read. ` : "In the lakehouse the view's datasets read. ",
      `The folder sits right under Semantic Views, and its name becomes the view's name and the model's name. OneLake folder names are case-sensitive but model names aren't, so a ${view} and a ${other} folder can't both publish.`),
    h("h3", { class: "sv-h" }, "3. The steps in Fabric"),
    svSteps(view, lh, ok),
    h("div", { class: "sv-grid" },
      h("div", {}, h("h3", { class: "sv-h" }, "Permissions you need"),
        h("ul", { class: "sv-list" },
          h("li", {}, "To create the folder and upload or save files, Write on the lakehouse. ", svLearn(SV_LEARN.createAccess, "Learn", "Learn: permissions to create and upload")),
          h("li", {}, "To publish, Write on the lakehouse and the Contributor role or higher on the workspace. ", svLearn(SV_LEARN.publishAccess, "Learn", "Learn: permissions to publish")))),
      h("div", {}, h("h3", { class: "sv-h" }, "Before you publish (Preview)"),
        h("ul", { class: "sv-list" },
          h("li", {}, "Semantic views are in Preview in Fabric."),
          h("li", {}, "A workspace connected to Git, deployment pipelines or Power Platform solutions, or with workspace-level private links, can't publish a view."),
          h("li", {}, h("b", {}, "Last write wins."), " If two people publish the same view at the same time, the later publish is the one that counts. Don't edit the files while a publish runs, because publish doesn't lock them. ", svLearn(SV_LEARN.overview, "Learn", "Learn: last write wins")),
          h("li", {}, "The folder and the model aren't kept in step. A renamed folder can stop updating the model, and deleting either one leaves the other in place. ", svLearn(SV_LEARN.overview, "Learn", "Learn: the folder and the model")),
          h("li", {}, "If a publish fails to compile, the model from the last publish stays as it was.")))),
    h("div", { class: "sv-note" }, h("b", {}, "Published model:"), " not read. KratosForge doesn't read the model Fabric makes yet, so it can't compare it with these files."),
  ];
}

// The publish-steps card under the Apache Ossie one; null when preview features are hidden.
function semanticViewCard(plan) {
  const name = h("span", {}, "Publish the semantic view in Fabric");
  const title = preview("semanticView", h("b", { class: "sv-title" }, name, " "));
  if (!title) return null;
  const pick = h("select", { "aria-label": "Semantic view", hidden: true });
  const body = h("div", { class: "sv-body" }, h("p", { class: "muted-text" }, "Reading the view's files on this PC..."));
  const load = async (want) => {
    const [list, git] = await Promise.all([
      metaCall("meta_model_semantic_view_status", {}, "Semantic views not read"),
      rpc("meta_model_ossie_folders", {}).catch(() => ({ folders: [] })),
    ]);
    if (!list) { fill(body, h("p", { class: "muted-text" }, "Couldn't read semantic-views\\ in the project's folder.")); return; }
    const folders = git.folders || [];
    const view = list.views.includes(want) ? want : list.views.includes(plan.name) ? plan.name : list.views[0];
    pick.hidden = list.views.length < 2;
    pick.replaceChildren(...list.views.map((v) => h("option", { value: v, selected: v === view ? true : null }, v)));
    if (!view) { name.textContent = "Publish the semantic view in Fabric"; fill(body, svNoFiles(folders, plan, load)); return; }
    name.textContent = `Publish the ${view} semantic view in Fabric`;
    const source = folders.find((f) => f.name === view);
    // Compared with its model in the project when there is one; a model that can't be read only loses the comparison.
    let st = source ? await rpc("meta_model_semantic_view_status", { view_name: view, path: source.path }).catch(() => null) : null;
    st = st || await metaCall("meta_model_semantic_view_status", { view_name: view }, "View files not read");
    if (!st) { fill(body, h("p", { class: "muted-text" }, `Couldn't read semantic-views\\${view}\\ in the project's folder.`)); return; }
    fill(body, st.files_summary.length ? svBody(st, plan, folders, load) : svNoFiles(folders, plan, load));
  };
  pick.onchange = () => load(pick.value);
  load();
  return h("div", { class: "card pad sv-card" },
    h("div", { class: "row wrap" }, title, h("span", { class: "spacer" }), pick), body);
}

// ---------- profiling ----------

// Profile what an entity reads, on this PC, and turn what it finds into [[clean]] and [[check]] blocks.
async function profileEntity(name, source = true) {
  const m = state.meta;
  const overlay = showDialog(`Profile ${name}`, h("p", { class: "muted-text" }, `Reading ${source ? "what it reads" : "its table"} in ${ENV_LABEL[m.env]}...`));
  overlay.querySelector(".dialog").classList.add("wide");
  const prof = await metaCall("meta_profile", { name, env: m.env, source }, "Profile failed");
  if (!prof) return;
  const picked = new Set();
  const preview = h("div", {});
  const addBtn = btn("Add to entity", "add", async () => {
    const blocks = prof.suggestions.filter((s) => picked.has(s.id)).map((s) => s.toml);
    const res = await metaCall("meta_add_rules", { name, blocks }, "Not added");
    if (!res) return;
    closeDialog();
    toast(res.errors.length ? `Added, with ${res.errors.length} problem${res.errors.length === 1 ? "" : "s"} to fix` : `Added ${blocks.length} rule${blocks.length === 1 ? "" : "s"} to ${name}. Run it to apply them.`);
    render();
  }, "tool primary", { disabled: true });
  const sync = () => {
    addBtn.disabled = !picked.size;
    addBtn.lastChild.textContent = picked.size ? `Add ${picked.size} to entity` : "Add to entity";
  };
  const showPreview = async () => {
    const rules = prof.suggestions.filter((s) => picked.has(s.id) && s.kind === "clean").map((s) => s.rule);
    if (!rules.length) { preview.replaceChildren(h("p", { class: "muted-text" }, "Tick a cleaning rule to see what it changes.")); return; }
    preview.replaceChildren(h("p", { class: "muted-text" }, "Applying the rules on this PC..."));
    const res = await metaCall("meta_profile_preview", { name, env: m.env, rules });
    if (!res) return;
    const cols = res.columns.flatMap((c) => [c, `${c} (after)`]);
    preview.replaceChildren(h("b", {}, `Before and after: ${res.changed} value${res.changed === 1 ? "" : "s"} change`),
      res.rows.length ? dataTable({ columns: cols, rows: res.rows }) : h("p", { class: "muted-text" }, "Nothing changes."));
  };
  const kindPill = (k) => h("span", { class: `pill-s ${k === "clean" ? "cus" : k === "check" ? "info" : "neu"}` }, k === "info" ? "note" : k);
  const suggestions = prof.suggestions.length ? h("table", { class: "mini" }, h("tbody", {}, prof.suggestions.map((sg) => h("tr", {},
    h("td", {}, sg.toml ? h("input", { type: "checkbox", "aria-label": sg.id, onchange: (e) => {
      if (e.target.checked) picked.add(sg.id); else picked.delete(sg.id);
      sync();
      if (sg.kind === "clean") showPreview();
    } }) : null),
    h("td", {}, kindPill(sg.kind)), h("td", { class: "mono" }, sg.column), h("td", {}, sg.why),
    h("td", {}, sg.toml ? h("pre", { class: "sql-block" }, sg.toml.trim()) : null)))))
    : h("p", { class: "muted-text" }, "No suggestions: the rules in place already cover what the profile found.");
  const columns = h("table", { class: "mini" },
    h("thead", {}, h("tr", {}, ["Column", "Type", "Nulls", "Distinct", "Range or length", "Most common", "Found"].map((c) => h("th", {}, c)))),
    h("tbody", {}, prof.columns.map((c) => h("tr", {},
      h("td", { class: "mono" }, c.name), h("td", { class: "muted-text mono" }, c.type),
      h("td", { class: "n" }, `${c.nulls} (${c.null_pct}%)`), h("td", { class: "n" }, c.distinct),
      h("td", { class: "mono small" }, c.min !== undefined ? `${c.min} to ${c.max}` : c.min_length !== undefined ? `${c.min_length} to ${c.max_length} chars` : ""),
      h("td", { class: "small" }, c.top.slice(0, 3).map((t) => `${t.value} (${t.count})`).join(", ")),
      h("td", {}, c.issues.length ? c.issues.map((i) => h("div", { class: "small" }, i)) : h("span", { class: "pill-s pass" }, "clean"))))));
  showDialog(`Profile ${name}`, h("div", { class: "scroll" },
    h("div", { class: "row wrap" },
      h("span", { class: "muted-text" }, `${prof.rows} rows ${source ? "as read, before cleaning" : "in its table"} · ${ENV_LABEL[m.env]} · ${prof.seconds}s on this PC`),
      h("span", { class: "spacer" }),
      btn(source ? "Profile its table instead" : "Profile what it reads", null, () => profileEntity(name, !source), "tool link small")),
    h("b", {}, "Suggestions"), suggestions, preview, h("b", {}, "Columns"), columns),
  [btn("Close", null, closeDialog, "tool outline"), addBtn]).querySelector(".dialog").classList.add("wide");
}

// ---------- Best practices check of the model, run in Fabric (model_bpa_service.py) ----------

const BPA_SEVERITY = { Error: ["Error", "fail"], Warning: ["Warning", "wrn"], Info: ["Info", "info"] };
// A rule's link comes from a results file any workspace contributor can write, so only these open.
const BPA_LINK_HOSTS = ["learn.microsoft.com", "docs.microsoft.com", "github.com", "www.sqlbi.com", "sqlbi.com"];
const BPA_POLL_MS = 30000;

function bpaRuleLink(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "https:" || !BPA_LINK_HOSTS.includes(u.hostname)) return null;
  } catch { return null; }
  return h("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); openLink(url); } }, "About this rule");
}

// The card under the Direct Lake one; null when preview features are hidden.
function modelBpaCard(plan) {
  const title = preview("modelBpa", h("b", {}, "Best practices check "));
  if (!title) return null;
  const m = state.meta;
  const target = { env: m.env, layer: plan.layer, name: plan.name };
  const result = h("div", { class: "bpa-result" });
  const checkBtn = btn("Check in Fabric", "play", () => start(), "tool primary small");
  const card = h("div", { class: "card pad bpa-card" },
    h("div", { class: "row wrap" }, title, h("span", { class: "spacer" }), plan.existing ? checkBtn : null),
    h("p", { class: "muted-text" }, "Checks the model against Best Practice Analyzer rules with Microsoft's semantic-link-labs. It runs as a Python notebook in Fabric, because the library only runs there, so every check uses the workspace's capacity. It reads the model and never changes it."),
    result,
    howItWorks("modelBpa", {
      reads: "The model, through its XMLA endpoint, from a notebook in the workspace. The app then reads the findings the notebook saved in LH_Meta.",
      changes: "Publishes the notebook nb_fabric_desktop_bpa to the workspace once, and runs it once for each check. The model, its reports and its data stay as they are.",
      results: "LH_Meta/Files/_fabric_desktop/results, and the last result for each model on this PC. Each check is listed in Monitor.",
      more: [["Where the rules come from", "semantic-link-labs (MIT), which ports Microsoft's BPA rules. The check installs a fixed version in the notebook's own session."]],
      anchor: "semantic-model-best-practices",
    }));
  if (!plan.existing) {
    fill(result, h("p", { class: "muted-text" }, `${plan.name} isn't in the workspace yet. Create the model first, then check it.`));
    return card;
  }
  let timer = null;
  let version = "";  // the semantic-link-labs version a check installs, from the status
  const view = { category: "", serious: true };  // the findings filter, kept across refreshes
  const load = async () => {
    clearTimeout(timer);
    const status = await metaCall("model_bpa_status", target, "Last check not read");
    if (!card.isConnected && timer !== null) return;  // the tab was left: stop asking
    if (!status) { fill(result); return; }
    version = status.version || version;
    fill(result, bpaStatus(status, view));
    checkBtn.disabled = !!status.running;
    if (status.running) timer = setTimeout(() => { if (card.isConnected) load(); }, BPA_POLL_MS);
  };
  const start = async () => {
    checkBtn.disabled = true;
    try {
      const risk = await rpc("model_bpa_risk", { ...target, refresh: true }).catch(riskCheckFailed);
      if (!(await bpaConfirm(plan, risk, version))) return;
      const res = await metaCall("model_bpa_check", { ...target, risk_id: risk.failed ? "" : risk.id || "" }, "Check not started");
      if (!res) return;
      toast(res.message);
      timer = 0;
      await load();
    } finally {
      if (!result.querySelector(".bpa-running")) checkBtn.disabled = false;
    }
  };
  fill(result, h("p", { class: "muted-text" }, `Reading ${plan.name}'s last check...`));
  timer = 0;
  load();
  return card;
}

// The confirm before a check: what it writes, what it doesn't change, and the risk check. Resolves true to go ahead.
function bpaConfirm(plan, risk, version) {
  return new Promise((resolve) => {
    const done = (v) => { closeDialog(); resolve(v); };
    const box = riskAckBox(risk);
    const go = async () => {
      if (risk?.result === "warning" && box && !box.querySelector("input").checked) {
        toast("Tick \"I've looked at these\" first, or cancel.", true);
        return;
      }
      done(true);
    };
    const blocked = risk?.result === "blocked";
    showDialog(`Check ${plan.name} in Fabric?`, h("div", { class: "wl-confirm" },
      h("b", {}, "What it writes"),
      h("ul", { class: "gate-list" },
        h("li", {}, `Publishes or updates the notebook nb_fabric_desktop_bpa in ${plan.workspace_name || "the workspace"}`),
        h("li", {}, `Installs semantic-link-labs${version ? ` ${version}` : ""} into that notebook's session only`),
        h("li", {}, "Saves the findings as one file in LH_Meta/Files/_fabric_desktop/results")),
      h("b", {}, "What it doesn't change"),
      h("p", {}, `${plan.name}, its reports and its data. The model is read through its XMLA endpoint, which needs XMLA read on the capacity (on by default).`),
      riskPanel(risk, { box }),
      risk?.note ? h("p", { class: "muted-text small" }, risk.note) : null),
      blocked ? [btn("Close", null, () => done(false), "tool primary")]
        : [btn("Cancel", null, () => done(false), "tool outline"), btn("Check in Fabric", null, go, "tool primary")]);
  });
}

// semantic-link-labs' rule categories, as the filter names them.
const BPA_CATEGORIES = ["Performance", "DAX expressions", "Error prevention", "Formatting", "Maintenance", "Naming conventions"];

function bpaFindingsTable(findings, view) {
  const shown = findings.filter((f) => (!view.serious || f.severity === "Error" || f.severity === "Warning")
    && (!view.category || f.category.toLowerCase() === view.category.toLowerCase()));
  if (!shown.length) return h("p", { class: "muted-text" }, "No findings match the filter.");
  return h("table", { class: "mini" },
    h("thead", {}, h("tr", {}, ["Severity", "Rule", "Object", "Category", "What the rule asks"].map((x) => h("th", {}, x)))),
    h("tbody", {}, shown.map((f) => h("tr", {},
      h("td", {}, h("span", { class: `pill-s ${(BPA_SEVERITY[f.severity] || BPA_SEVERITY.Info)[1]}` }, f.severity)),
      h("td", {}, f.rule),
      h("td", {}, h("span", { class: "muted-text" }, f.object_type ? `${f.object_type} ` : ""), h("span", { class: "mono" }, f.object)),
      h("td", {}, f.category),
      h("td", {}, f.description, f.url ? [" ", bpaRuleLink(f.url)] : null)))));
}

function bpaStatus(s, view = { category: "", serious: true }) {
  const parts = [];
  if (s.running) {
    parts.push(h("div", { class: "info-box bpa-running" }, icon("info", 16),
      ` Checking ${s.model} in Fabric, started ${relTime(s.running.started_at)}. This card looks again every 30 seconds.`,
      s.running.url ? btn("Open the run in Fabric", "open", () => openInFabric(s.running.url), "tool link small") : null));
  }
  if (s.failed) {
    const f = s.failed;
    parts.push(h("div", { class: "card pad warn-box bpa-failed" },
      h("b", {}, `The last check didn't finish (${f.status || "unknown"})`),
      h("p", {}, f.message || "Fabric gave no reason. Check again, and if it fails again, open the run in Fabric."),
      f.traceback ? h("details", {}, h("summary", {}, "Traceback"), h("pre", { class: "mono small" }, f.traceback)) : null,
      f.pip ? h("details", {}, h("summary", {}, "pip output"), h("pre", { class: "mono small" }, f.pip)) : null,
      f.url ? btn("Open the run in Fabric", "open", () => openInFabric(f.url), "tool link small") : null));
  }
  const last = s.last;
  if (!last) {
    if (!s.running && !s.failed) parts.push(h("p", { class: "muted-text" }, `${s.model} hasn't been checked yet.`));
    return h("div", {}, parts);
  }
  const c = last.counts || {};
  const tile = (sev) => h("div", { class: "dl-tile" }, h("span", { class: "muted-text" }, BPA_SEVERITY[sev][0]),
    h("b", { class: `${BPA_SEVERITY[sev][1]}-text` }, String(c[sev] || 0)));
  const table = h("div", { class: "bpa-table" });
  const redraw = () => fill(table, bpaFindingsTable(last.findings, view));
  const category = h("select", { "aria-label": "Category", onchange: (e) => { view.category = e.target.value; redraw(); } },
    h("option", { value: "" }, "All categories"),
    BPA_CATEGORIES.map((x) => h("option", { value: x, selected: x === view.category ? true : null }, x)));
  const serious = h("input", { type: "checkbox", checked: view.serious ? true : null,
    onchange: (e) => { view.serious = e.target.checked; redraw(); } });
  redraw();
  const when = `Checked ${relTime(last.checked_at)} with ${last.package || "semantic-link-labs"} ${last.version || ""}`.trim()
    + (last.rules ? `, ${last.rules} rules.` : ".");
  parts.push(h("div", { class: "bpa-out" },
    h("div", { class: "dl-tiles" }, ["Error", "Warning", "Info"].map(tile)),
    last.no_tables ? h("p", { class: "muted-text" }, "The model has no tables yet, so most rules had nothing to check.") : null,
    last.findings.length ? [
      h("div", { class: "row wrap bpa-filters" }, h("label", { class: "field" }, "Show", category),
        h("label", { class: "check" }, serious, " Errors and warnings only")),
      table,
      h("p", { class: "muted-text" }, "Findings fixed by KratosForge's generator are fixed by the next Update model, not by editing in Fabric.")]
      : h("div", { class: "pass-box" }, icon("ok", 16), " No rule found anything."),
    h("p", { class: "muted-text" }, when, " ",
      h("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); monitorPage.panel = "runs"; go("monitor"); } }, "Open the run in Monitor"))));
  return h("div", {}, parts);
}
