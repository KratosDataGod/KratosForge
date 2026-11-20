// Settings > Environment (Preview, CP-010): the KratosForge Environment this PC uses, built as the
// approved mockup shows it (/mnt/project-files/ux/cp-010/, approved 2026-11-20). One status card with
// the one status and its next step; Create, Connect and Adopt; Upgrade; Switch and Disconnect; and the
// providers. Every change goes through the engine's plan-and-confirm: the engine shows a plan (or the
// manifest it would write) under a single-use token, this page shows it, and confirmDialog asks before
// cp_create, cp_adopt or cp_upgrade runs it by that token. A Prod workspace asks for its exact name.
// Disconnect only makes this PC forget an environment. With allow_central_environment off, only
// Disconnect stays.

// mode: status | create | connect | adopt | upgrade | list. The rest is what the current mode shows.
const kfe = { mode: "status", status: null, check: null, checkedAt: null, plan: null, form: null, workspaces: null,
  capacities: null, lakehouses: null, loadingWs: false, loadingCaps: false, planBox: null, upgrade: null, inside: null, filter: "", adopt: null, adoptName: "", done: null, error: "",
  lastKnown: {} };

const KFE_TONE = { Connected: "pass", "Working offline": "wrn", Synchronizing: "info", "Upgrade required": "wrn",
  "Limited permissions": "wrn", "Connection failed": "fail" };
const KFE_LEAD = "Where KratosForge keeps what your team shares: optimization history, savings and settings. Work still runs on this PC, and results wait here until the environment can be reached.";
const KFE_NO_OPERATIONAL = "None yet. Added when a feature that needs it is turned on.";
const KFE_DEFAULT_WS = "KratosForge";
const KFE_DEFAULT_LH = "KratosForgeLakehouse";

async function renderEnvironmentTab(el) {
  el.replaceChildren(h("p", { class: "muted-text small" }, "Reading this PC's environments…"));
  const status = await attempt(() => rpc("cp_status"), "Couldn't read this PC's environments");
  if (!status) {
    el.replaceChildren(h("p", { class: "muted-text small" }, "This PC's environments couldn't be read."),
      btn("Try again", "sync", () => renderEnvironmentTab(el), "tool outline"));
    return;
  }
  kfe.status = status;
  if (!status.allowed && kfe.mode !== "list") kfe.mode = "status";
  kfeDraw(el);
  if (status.allowed && status.active && kfe.mode === "status" && !kfe.check) kfeValidate(el);
}

function kfeGo(el, mode) {
  kfe.mode = mode;
  kfe.error = "";
  kfe.done = null;
  if (mode !== "create") kfe.plan = null;
  if (mode !== "adopt") kfe.adopt = null;
  renderEnvironmentTab(el);
}

function kfeDraw(el) {
  const s = kfe.status;
  const body = !s.allowed ? kfePolicyOff(el)
    : kfe.mode === "create" ? kfeCreate(el)
    : kfe.mode === "connect" ? kfeConnect(el)
    : kfe.mode === "adopt" ? kfeAdopt(el)
    : kfe.mode === "upgrade" ? kfeUpgrade(el)
    : kfe.mode === "list" ? kfeList(el)
    : [kfe.done ? kfeDoneCard() : null, s.active ? kfeStatusCard(el, s.active) : kfeNoneCard(el)];
  el.replaceChildren(h("div", { class: "kfe-page" },
    h("h2", { class: "kfe-title" }, "KratosForge Environment ", previewTag()),
    h("p", { class: "muted-text" }, KFE_LEAD, " ", learnMore("kratosforge-environment-preview")),
    body, kfeProviders()));
}

// ---------- status ----------

function kfeStatusLine(tone, label, text, extra) {
  return h("div", { class: `kfe-status ${tone}`, role: tone === "fail" ? "alert" : "status" },
    h("span", { class: `pill-s ${tone}` }, label), h("span", {}, text), extra || null);
}

async function kfeValidate(el) {
  kfe.check = { checking: true };
  kfeDraw(el);
  const got = await attempt(() => rpc("cp_check"), "Couldn't check the environment");
  kfe.check = got || null;
  kfe.checkedAt = got ? new Date() : kfe.checkedAt;
  const id = kfe.status?.active?.environment_id;
  if (got && id) kfe.lastKnown[id] = { status: got.status, at: kfe.checkedAt };
  if (el.isConnected) kfeDraw(el);
}

function kfeWhen(d) {
  if (!d) return "Not checked yet";
  const today = new Date().toDateString() === d.toDateString();
  return today ? `Today ${timeText(d)}` : dateText(d);
}

function kfeStatusCard(el, env) {
  const c = kfe.check;
  const label = c && !c.checking ? c.status : "";
  const upgrade = label === "Upgrade required" && c.compatibility?.can_upgrade;
  const waiting = Number(c?.waiting || 0);
  const tone = KFE_TONE[label] || "neu";
  return h("section", { class: "card pad kfe-card", "aria-labelledby": "kfe-name" },
    h("div", { class: "row" }, h("h3", { class: "grow", id: "kfe-name" }, env.name || env.environment_id),
      btn("Validate connection", null, () => kfeValidate(el), "tool outline", { disabled: c?.checking ? true : null }),
      upgrade ? btn("Upgrade...", null, () => kfeUpgradePlan(el), "tool primary") : null,
      btn("Switch or disconnect...", null, () => kfeGo(el, "list"), "tool outline")),
    h("dl", { class: "kfe-kv" },
      h("dt", {}, "Provider"), h("dd", {}, kfeProviderLabel(env.provider), " ", previewTag()),
      h("dt", {}, "Workspace"), h("dd", {}, env.workspace_name || env.workspace_id),
      h("dt", {}, "Lakehouse"), h("dd", {}, env.lakehouse_name || env.lakehouse_id),
      h("dt", {}, "Operational database"),
      h("dd", { class: env.has_operational_store ? null : "muted-text" }, env.has_operational_store ? env.operational_name || "Linked" : KFE_NO_OPERATIONAL),
      h("dt", {}, "Last checked"), h("dd", {}, c?.checking ? "Checking now…" : kfeWhen(kfe.checkedAt))),
    c?.checking ? h("p", { class: "muted-text small" }, "Checking the environment…")
      : label ? kfeStatusLine(tone, label, c.next || "",
        h("span", { class: "small kfe-waiting" }, waiting
          ? [h("b", {}, `${waiting} result${waiting === 1 ? "" : "s"}`), " wait on this PC."]
          : "No results wait on this PC.")) : null,
    label === "Connection failed" && c.detail ? h("p", { class: "small muted-text" }, c.detail) : null,
    upgrade ? h("p", { class: "small muted-text" }, "The shared tables use an older version. Upgrade runs the same plan and confirm as Create, so you see each step before you confirm. KratosForge on other PCs can only read the tables until it's updated.") : null);
}

function kfeNoneCard(el) {
  return h("section", { class: "card pad kfe-card" },
    h("h3", {}, "This PC isn't connected to an environment"),
    h("p", { class: "muted-text" }, "Create an environment, or connect to one your team already has. Until then, work runs on this PC as it does today."),
    h("div", { class: "row" },
      btn("Create an environment...", null, () => kfeGo(el, "create"), "tool primary"),
      btn("Connect to one...", null, () => kfeGo(el, "connect"), "tool outline"),
      btn("Adopt this PC's central lakehouse...", null, () => kfeGo(el, "adopt"), "tool outline")));
}

function kfeDoneCard() {
  const d = kfe.done;
  return h("section", { class: "card pad kfe-card" }, kfeStatusLine(d.ok ? "pass" : "fail", d.label, d.text));
}

function kfeProviderLabel(id) {
  return (kfe.status?.providers || []).find((p) => p.id === id)?.label || id || "";
}

// The providers setup can offer, from the engine's registry: one that can't be used yet says so.
function kfeProviders() {
  const shown = (kfe.status?.providers || []).filter((p) => p.id !== "local");
  if (!shown.length) return null;
  return h("section", { class: "card pad kfe-card" }, h("h3", {}, "Providers"),
    h("table", { class: "kfe-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Provider"), h("th", {}, "State"), h("th", {}, "What it uses"))),
      h("tbody", {}, shown.map((p) => h("tr", {},
        h("td", {}, p.label),
        h("td", {}, p.available ? (p.preview ? previewTag() : h("span", { class: "pill-s pass" }, "Available"))
          : h("span", { class: "pill-s neu" }, "Not available yet")),
        h("td", { class: p.available ? null : "muted-text" }, KFE_USES[p.id] || p.planned || ""))))),
    h("p", { class: "small muted-text" }, "A provider becomes available after its tests and a live run pass."));
}

const KFE_USES = {
  fabric: "A workspace and a lakehouse with schemas. A Fabric SQL database is added only when a feature needs it.",
  databricks: "Planned: a Unity Catalog catalog and schema, with Delta tables.",
  snowflake: "Planned: a database and schemas, with roles and grants.",
};

// ---------- create ----------

function kfeForm() {
  if (!kfe.form) kfe.form = { name: "", provider: "fabric", workspace: "new", workspaceName: KFE_DEFAULT_WS, capacity: "",
    lakehouse: "new", lakehouseName: KFE_DEFAULT_LH };
  return kfe.form;
}

// The workspaces this person can open, once per visit; with capacities too for Create. A capacity
// list this sign-in can't read leaves a box to paste the capacity's id.
async function kfeLoadChoices(el, capacities = false) {
  const jobs = [];
  if (kfe.workspaces === null && !kfe.loadingWs) {
    kfe.loadingWs = true;
    jobs.push(attempt(() => rpc("cp_discover"), "Couldn't list your workspaces").then((ns) => { kfe.workspaces = ns?.namespaces || []; })
      .finally(() => { kfe.loadingWs = false; }));
  }
  if (capacities && kfe.capacities === null && !kfe.loadingCaps) {
    kfe.loadingCaps = true;
    jobs.push(rpc("list_capacities").catch(() => []).then((caps) => {
      kfe.capacities = caps || [];
      if (kfe.capacities.length && !kfeForm().capacity) kfeForm().capacity = kfe.capacities[0].id;
    }).finally(() => { kfe.loadingCaps = false; }));
  }
  if (!jobs.length) return;
  await Promise.all(jobs);
  if (el.isConnected) kfeDraw(el);
}

function kfeRequest(f) {
  const req = { name: f.name.trim() };
  if (f.workspace === "new") {
    req.workspace_name = f.workspaceName.trim();
    if (f.capacity) req.capacity_id = f.capacity;
  } else req.workspace_id = f.workspace;
  if (f.lakehouse === "new") req.lakehouse_name = f.lakehouseName.trim();
  else req.lakehouse_id = f.lakehouse;
  return req;
}

function kfeCreate(el) {
  const f = kfeForm();
  kfeLoadChoices(el, true);
  const changed = (key, redraw = false) => (e) => { f[key] = e.target.value; kfe.plan = null; kfe.error = "";
    if (redraw) kfeDraw(el); else kfeShowPlanState(el); };
  // A select inside its label would otherwise take its options' text into its name.
  const field = (label, control, help) => {
    control.setAttribute("aria-label", label);
    return h("label", { class: "field kfe-field" }, label, control, help ? h("small", { class: "muted-text" }, help) : null);
  };
  const providers = (kfe.status.providers || []).filter((p) => p.id !== "local");
  const wsChoice = h("select", { onchange: async (e) => {
    f.workspace = e.target.value; f.lakehouse = "new"; kfe.plan = null; kfe.lakehouses = null; kfeDraw(el);
    if (f.workspace !== "new") {
      kfe.lakehouses = await rpc("list_lakehouses", { workspace_id: f.workspace }).catch(() => []);
      if (el.isConnected) kfeDraw(el);
    }
  } }, h("option", { value: "new", selected: f.workspace === "new" ? true : null }, `New: ${f.workspaceName || KFE_DEFAULT_WS}`),
  (kfe.workspaces || []).map((w) => h("option", { value: w.id, selected: f.workspace === w.id ? true : null }, `Existing: ${w.display_name}`)));
  const caps = kfe.capacities;
  const fields = h("div", { class: "kfe-fields" },
    field("Name", h("input", { value: f.name, maxlength: "120", oninput: changed("name") }),
      "Shown in KratosForge only. Fabric items keep their own names."),
    field("Provider", h("select", { onchange: changed("provider") }, providers.map((p) => h("option", {
      value: p.id, disabled: p.available ? null : true, selected: f.provider === p.id ? true : null },
    `${p.label} (${p.available ? (p.preview ? "Preview" : "available") : "not available yet"})`)))),
    field("Workspace", wsChoice),
    f.workspace === "new" ? field("New workspace name", h("input", { value: f.workspaceName, maxlength: "256", oninput: changed("workspaceName") })) : null,
    f.workspace === "new" ? field("Capacity for the new workspace", !caps?.length
      ? h("input", { value: f.capacity, placeholder: "Capacity id", spellcheck: "false", oninput: changed("capacity") })
      : h("select", { onchange: changed("capacity") }, caps.map((c) => h("option", { value: c.id, selected: f.capacity === c.id ? true : null },
        [c.sku, c.displayName, c.region ? `(${c.region})` : ""].filter(Boolean).join(" · ").replace(" · (", " ("))))) : null,
    field("Lakehouse", h("select", { onchange: changed("lakehouse", true) },
      h("option", { value: "new", selected: f.lakehouse === "new" ? true : null }, `New: ${f.lakehouseName || KFE_DEFAULT_LH}`),
      f.workspace === "new" ? null : (kfe.lakehouses || []).map((lh) => h("option", { value: lh.id, selected: f.lakehouse === lh.id ? true : null }, `Existing: ${lh.displayName}`)))),
    f.lakehouse === "new" ? field("New lakehouse name", h("input", { value: f.lakehouseName, maxlength: "256", oninput: changed("lakehouseName") })) : null);
  const planBox = h("div", { class: "kfe-plan-box" });
  kfe.planBox = planBox;
  const card = h("section", { class: "card pad kfe-card", "aria-labelledby": "kfe-create" },
    h("h3", { id: "kfe-create" }, "Create an environment"), fields, planBox);
  kfeShowPlanState(el);
  return card;
}

// The plan part of Create: a button to see the plan, then the plan with Create, or what's blocked.
function kfeShowPlanState(el) {
  const box = kfe.planBox;
  if (!box) return;
  const f = kfeForm();
  const cancel = btn("Cancel", null, () => { kfe.form = null; kfeGo(el, "status"); }, "tool outline");
  if (!kfe.plan) {
    box.replaceChildren(kfe.error ? kfeStatusLine("fail", "Not created", kfe.error) : null,
      h("div", { class: "row kfe-buttons" }, cancel,
        btn("Show what will happen", null, () => kfePlan(el), "tool primary", { disabled: f.name.trim() ? null : true })));
    return;
  }
  const p = kfe.plan;
  box.replaceChildren(h("h4", {}, "What will happen"), kfePlanTable(p.steps, kfeNames()),
    p.token ? h("p", { class: "small muted-text" }, "Nothing is created until you confirm.") : null,
    h("div", { class: "row kfe-buttons" }, cancel,
      btn("Create environment...", null, () => kfeCreateRun(el), "tool primary", { disabled: p.token ? null : true })));
}

const KFE_VERB = { create: "Create", initialize: "Create", upgrade: "Upgrade", find: "Use", write_manifest: "Write", refuse: "Can't use" };

// Lakehouses the plan names by id, by their names, for the plan's rows.
function kfeNames() {
  return Object.fromEntries((kfe.lakehouses || []).map((lh) => [lh.id, `lakehouse ${lh.displayName}`]));
}

function kfeStepTitle(s, names = {}) {
  if (s.action === "write_manifest") return [h("b", {}, "Write the manifest"), " ", h("span", { class: "mono" }, s.target)];
  if (s.action === "refuse" && s.target === "environment") return h("b", {}, "This environment");
  return h("b", {}, `${KFE_VERB[s.action] || s.action} ${names[s.target] || s.target}`);
}

// One row per step the engine planned. A step after one that blocks is dimmed: it can't run either.
// A table or manifest step after a costed step costs nothing more than that step's storage.
function kfePlanTable(steps, names = {}) {
  let blocked = false;
  let costed = false;
  return h("table", { class: "kfe-table kfe-plan" },
    h("thead", {}, h("tr", {}, h("th", {}, "Step"), h("th", {}, "Permission it needs"), h("th", {}, "Cost"), h("th", {}, "Note"))),
    h("tbody", {}, steps.map((s) => {
      const dim = blocked;
      if (s.blocked) blocked = true;
      if (s.cost) costed = true;
      const cost = s.cost || (costed && ["initialize", "upgrade", "write_manifest"].includes(s.action) ? "Included above." : "");
      return h("tr", { class: dim ? "dim" : null },
        h("td", {}, kfeStepTitle(s, names), s.blocked ? h("div", { class: "fail-text small" }, s.blocked) : null),
        h("td", {}, s.permission || ""), h("td", {}, cost), h("td", {}, s.note || ""));
    })));
}

async function kfePlan(el) {
  const req = kfeRequest(kfeForm());
  const got = await attempt(() => rpc("cp_plan", { request: req }), "Couldn't make the plan");
  if (!got) return;
  kfe.plan = got;
  kfeShowPlanState(el);
}

function kfeCreatesText(steps) {
  const made = steps.filter((s) => s.action === "create").map((s) => s.target.split(" ")[0]);
  const what = made.length === 2 ? "a workspace and a lakehouse" : made.length === 1 ? `a ${made[0]}` : "";
  return what ? `This creates ${what} in Fabric, the shared tables and the manifest. Nothing is deleted.`
    : "This sets up the shared tables and writes the manifest. Nothing is deleted.";
}

async function kfeCreateRun(el) {
  const p = kfe.plan;
  const name = p.request.name;
  const typed = p.typed_name;
  const ok = typed
    ? await confirmDialog(`Create ${name} in ${typed}?`,
      `${typed} is a Prod workspace, so this runs only after you type its name exactly to confirm. It adds a lakehouse, the shared tables and the manifest. Nothing is deleted.`,
      "Create", { typeToConfirm: typed })
    : await confirmDialog(`Create ${name} in Fabric?`, kfeCreatesText(p.steps), "Create");
  if (!ok) return;
  let out;
  try {
    out = await rpc("cp_create", { token: p.token, confirmed: true, confirm_name: typed || "" });
  } catch (err) {
    kfe.plan = null;
    kfe.error = friendlyError(err).text;
    kfeShowPlanState(el);
    return;
  }
  kfe.plan = null;
  kfe.form = null;
  const details = (out.steps || []).map((s) => s.detail).filter(Boolean).join(", ");
  kfe.done = out.ok
    ? { ok: true, label: "Created", text: `${name} is ready, and this PC is connected to it. ${details}.` }
    : { ok: false, label: "Stopped", text: `${kfeFailed(out.steps)} Nothing after it ran. Make the plan again to finish. What was already made is used, not made twice.` };
  kfe.check = null;
  kfe.mode = "status";
  await renderEnvironmentTab(el);
}

function kfeFailed(steps) {
  const f = (steps || []).find((s) => s.status === "failed");
  return f ? `${f.step} didn't finish. ${f.detail}${/[.!?]$/.test(f.detail || "") ? "" : "."}` : "It didn't finish.";
}

// ---------- connect ----------

function kfeConnect(el) {
  kfeLoadChoices(el);
  if (kfe.inside) return kfeInside(el);
  const q = kfe.filter.trim().toLowerCase();
  const rows = (kfe.workspaces || []).filter((w) => !q || (w.display_name || "").toLowerCase().includes(q));
  return h("section", { class: "card pad kfe-card", "aria-labelledby": "kfe-connect" },
    h("div", { class: "row" }, h("h3", { class: "grow", id: "kfe-connect" }, "Connect to an environment"),
      btn("Back", null, () => kfeGo(el, "status"), "tool outline")),
    kfe.error ? kfeStatusLine("fail", "Not connected", kfe.error) : null,
    h("label", { class: "field kfe-field kfe-narrow" }, "Find a workspace",
      h("input", { value: kfe.filter, placeholder: "Workspace name", oninput: (e) => {
        kfe.filter = e.target.value;
        const at = e.target.selectionStart;
        kfeDraw(el);
        const box = el.querySelector(".kfe-narrow input");
        if (box) { box.focus(); box.setSelectionRange(at, at); }
      } })),
    kfe.workspaces === null ? h("p", { class: "muted-text small" }, "Listing your workspaces…")
      : rows.length ? h("table", { class: "kfe-table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Workspace"), h("th", {}, ""))),
        h("tbody", {}, rows.map((w) => h("tr", {}, h("td", {}, w.display_name),
          h("td", { class: "kfe-act" }, btn("Look inside", null, () => kfeLookInside(el, w), "tool outline small",
            { "aria-label": `Look inside ${w.display_name}` }))))))
        : h("p", { class: "muted-text small" }, q ? "No workspace has that name." : "No workspaces to show."),
    h("p", { class: "small muted-text" }, "Workspaces you can open in Fabric. Reading them changes nothing."));
}

async function kfeLookInside(el, w) {
  kfe.inside = { workspace: w, found: null };
  kfeDraw(el);
  const got = await attempt(() => rpc("cp_discover", { workspace_id: w.id }), `Couldn't look inside ${w.display_name}`);
  const central = await rpc("optimize_status").catch(() => null);
  if (!kfe.inside || kfe.inside.workspace !== w) return;
  kfe.inside.found = got ? got.found : [];
  kfe.inside.truncated = !!got?.truncated;
  kfe.inside.central = central?.active?.lakehouse_id || "";
  if (el.isConnected) kfeDraw(el);
}

function kfeInside(el) {
  const { workspace: w, found } = kfe.inside;
  const head = h("div", { class: "row" }, h("h3", { class: "grow" }, `In ${w.display_name}`),
    btn("Back to workspaces", null, () => { kfe.inside = null; kfe.error = ""; kfeDraw(el); }, "tool outline"));
  if (found === null) return h("section", { class: "card pad kfe-card" }, head, h("p", { class: "muted-text small" }, "Looking inside…"));
  const envs = found.filter((f) => f.state === "environment");
  const adoptable = found.filter((f) => f.state === "adoptable");
  const unread = found.filter((f) => f.state === "unreadable");
  return h("section", { class: "card pad kfe-card" }, head,
    kfe.error ? kfeStatusLine("fail", "Not connected", kfe.error) : null,
    h("h4", {}, "Environments"),
    envs.length ? h("table", { class: "kfe-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Environment"), h("th", {}, "Lakehouse"), h("th", {}, "Last changed"), h("th", {}, ""))),
      h("tbody", {}, envs.map((f) => h("tr", {},
        h("td", {}, h("b", {}, f.environment?.name || f.display_name), f.environment?.writable === false
          ? [" ", h("span", { class: "pill-s wrn" }, "Read only"),
            h("div", { class: "small muted-text" }, "A newer KratosForge wrote this environment. Update KratosForge to change it.")] : null),
        h("td", {}, f.display_name || f.lakehouse_id), h("td", {}, dateText(f.environment?.updated_at)),
        h("td", { class: "kfe-act" }, btn("Connect", null, () => kfeConnectTo(el, f), envs.length === 1 ? "tool primary small" : "tool outline small",
          { "aria-label": `Connect to ${f.environment?.name || f.display_name}` }))))))
      : h("p", { class: "muted-text small" }, "No environments in this workspace."),
    adoptable.length ? [h("h4", {}, "Central lakehouses this PC can adopt"),
      h("table", { class: "kfe-table" }, h("tbody", {}, adoptable.map((f) => h("tr", {},
        h("td", {}, h("b", {}, f.display_name || f.lakehouse_id),
          h("div", { class: "small muted-text" }, f.lakehouse_id === kfe.inside.central
            ? "Holds KratosForge's central tables but no manifest yet."
            : "Holds KratosForge's central tables but no manifest yet. To adopt it, connect this PC to it in Optimize lakehouses first.")),
        h("td", { class: "kfe-act" }, f.lakehouse_id === kfe.inside.central
          ? btn("Adopt...", null, () => kfeGo(el, "adopt"), "tool outline small", { "aria-label": `Adopt ${f.display_name}` }) : null)))))] : null,
    unread.length ? [h("h4", {}, "Couldn't read"),
      h("table", { class: "kfe-table" }, h("tbody", {}, unread.map((f) => h("tr", {},
        h("td", {}, f.display_name || f.lakehouse_id), h("td", { class: "muted-text" }, f.reason)))))] : null,
    kfe.inside.truncated ? h("p", { class: "small muted-text" }, "This workspace has more lakehouses than were looked at.") : null);
}

async function kfeConnectTo(el, f) {
  try {
    const out = await rpc("cp_connect", { workspace_id: f.workspace_id, lakehouse_id: f.lakehouse_id });
    toast(`Connected to ${out.environment.name}.`);
  } catch (err) {
    kfe.error = friendlyError(err).text;
    kfeDraw(el);
    return;
  }
  kfe.inside = null;
  kfe.check = null;
  kfeGo(el, "status");
}

// ---------- adopt ----------

function kfeAdopt(el) {
  const a = kfe.adopt;
  const nameBox = h("input", { value: kfe.adoptName, maxlength: "120", oninput: (e) => {
    kfe.adoptName = e.target.value;
    if (kfe.adopt?.token) { kfe.adopt = null; kfeDraw(el); el.querySelector(".kfe-adopt-name input")?.focus(); return; }
    const show = el.querySelector(".kfe-show-manifest");
    if (show) show.disabled = !kfe.adoptName.trim();
  } });
  if (a?.nothing) {
    return h("section", { class: "card pad kfe-card" }, h("div", { class: "row" }, h("h3", { class: "grow" }, "Adopt this PC's central lakehouse"),
      btn("Back", null, () => kfeGo(el, "status"), "tool outline")),
    kfeStatusLine("neu", "Nothing to adopt", a.nothing));
  }
  const m = a?.manifest;
  const ids = (id, label) => [h("span", {}, id), label ? h("span", { class: "muted-text" }, ` (${label})`) : null];
  return h("section", { class: "card pad kfe-card" },
    h("div", { class: "row" }, h("h3", { class: "grow" }, m ? `Adopt ${a.environment.lakehouse_name || "this lakehouse"} as an environment` : "Adopt this PC's central lakehouse"),
      btn("Back", null, () => kfeGo(el, "status"), "tool outline")),
    h("p", { class: "muted-text" }, "This PC already sends optimization history to this lakehouse. Adopting it writes one file, the manifest, so other PCs can find and connect to it. No table is created or changed."),
    kfe.error ? kfeStatusLine("fail", "Not adopted", kfe.error) : null,
    h("label", { class: "field kfe-field kfe-narrow kfe-adopt-name" }, "Name", nameBox),
    m ? [h("h4", {}, "The manifest it would write"),
      h("dl", { class: "kfe-kv mono" },
        h("dt", {}, "environment_id"), h("dd", {}, m.environment_id),
        h("dt", {}, "provider"), h("dd", {}, m.provider),
        h("dt", {}, "workspace_id"), h("dd", {}, ids(m.namespace.workspace_id, m.namespace.display_name)),
        h("dt", {}, "lakehouse_id"), h("dd", {}, ids(m.analytical_store.lakehouse_id, m.analytical_store.display_name)),
        h("dt", {}, "operational_store"), h("dd", {}, m.operational_store ? m.operational_store.display_name || "Linked" : "none"),
        m.services?.length ? [h("dt", {}, "services"), h("dd", {}, m.services.join(", "))] : null,
        h("dt", {}, "owner"), h("dd", {}, m.owner ? "your Entra object id" : "none")),
      h("p", { class: "small muted-text" }, "Ids and versions only. No secret, email or name of a person.")] : null,
    h("div", { class: "row kfe-buttons" },
      btn("Cancel", null, () => kfeGo(el, "status"), "tool outline"),
      m ? btn("Adopt...", null, () => kfeAdoptRun(el), "tool primary")
        : btn("Show the manifest", null, () => kfeAdoptPreview(el), "tool primary kfe-show-manifest", { disabled: kfe.adoptName.trim() ? null : true })));
}

async function kfeAdoptPreview(el) {
  kfe.error = "";
  try {
    kfe.adopt = await rpc("cp_adopt_preview", { name: kfe.adoptName.trim() });
  } catch (err) {
    const text = friendlyError(err).text;
    if (/Connect this PC to a central lakehouse first/.test(text)) kfe.adopt = { nothing: text };
    else kfe.error = text;
  }
  kfeDraw(el);
}

async function kfeAdoptRun(el) {
  const a = kfe.adopt;
  const where = a.environment.lakehouse_name || "this lakehouse";
  const typed = a.typed_name;
  const ok = typed
    ? await confirmDialog(`Adopt ${where}?`, `${typed} is a Prod workspace, so this runs only after you type its name exactly to confirm. This writes Files/_kratosforge/environment.json in ${where} and connects this PC to ${a.environment.name}. No table is created or changed.`,
      "Adopt", { typeToConfirm: typed })
    : await confirmDialog(`Adopt ${where}?`, `This writes Files/_kratosforge/environment.json in ${where} and connects this PC to ${a.environment.name}. No table is created or changed.`, "Adopt");
  if (!ok) return;
  try {
    const out = await rpc("cp_adopt", { token: a.token, confirmed: true, confirm_name: typed || "" });
    toast(`Adopted ${where}. This PC is connected to ${out.environment.name}.`);
  } catch (err) {
    kfe.adopt = null;
    kfe.error = friendlyError(err).text;
    kfeDraw(el);
    return;
  }
  kfe.adoptName = "";
  kfe.check = null;
  kfeGo(el, "status");
}

// ---------- upgrade ----------

async function kfeUpgradePlan(el) {
  const got = await attempt(() => rpc("cp_upgrade_plan"), "Couldn't make the upgrade plan");
  if (!got) return;
  kfe.mode = "upgrade";
  kfe.upgrade = got;
  kfe.error = "";
  kfeDraw(el);
}

function kfeUpgrade(el) {
  const u = kfe.upgrade;
  if (!u) { kfe.mode = "status"; return kfeStatusCard(el, kfe.status.active); }
  return h("section", { class: "card pad kfe-card", "aria-labelledby": "kfe-upgrade" },
    h("h3", { id: "kfe-upgrade" }, `Upgrade ${u.environment.name}`),
    h("p", { class: "muted-text" }, "The shared tables use an older version. Upgrading adds tables and columns and keeps every record. KratosForge on other PCs can only read the tables until it's updated."),
    kfe.error ? kfeStatusLine("fail", "Not upgraded", kfe.error) : null,
    h("h4", {}, "What will happen"), kfePlanTable(u.steps),
    u.token ? h("p", { class: "small muted-text" }, "Nothing changes until you confirm.") : null,
    h("div", { class: "row kfe-buttons" },
      btn("Cancel", null, () => { kfe.upgrade = null; kfeGo(el, "status"); }, "tool outline"),
      btn("Upgrade...", null, () => kfeUpgradeRun(el), "tool primary", { disabled: u.token ? null : true })));
}

async function kfeUpgradeRun(el) {
  const u = kfe.upgrade;
  const name = u.environment.name;
  const typed = u.typed_name;
  const text = "This upgrades the shared tables and records their new versions in the manifest. Every record is kept, and nothing is deleted.";
  const ok = typed
    ? await confirmDialog(`Upgrade ${name} in ${typed}?`, `${typed} is a Prod workspace, so this runs only after you type its name exactly to confirm. ${text}`,
      "Upgrade", { typeToConfirm: typed })
    : await confirmDialog(`Upgrade ${name}?`, text, "Upgrade");
  if (!ok) return;
  let out;
  try {
    out = await rpc("cp_upgrade", { token: u.token, confirmed: true, confirm_name: typed || "" });
  } catch (err) {
    kfe.upgrade = { ...u, token: "" };
    kfe.error = `${friendlyError(err).text}`;
    kfeDraw(el);
    return;
  }
  kfe.upgrade = null;
  kfe.check = null;
  kfe.mode = "status";
  const done = out.ok ? { ok: true, label: "Upgraded", text: `${name}'s shared tables are up to date. Every record was kept.` }
    : { ok: false, label: "Stopped", text: `${kfeFailed(out.steps)} Nothing after it ran. Choose Upgrade again to finish.` };
  kfe.done = done;
  await renderEnvironmentTab(el);
}

// ---------- environments on this PC ----------

function kfeList(el) {
  const s = kfe.status;
  const activeId = s.active?.environment_id;
  const known = (e) => {
    const k = kfe.lastKnown[e.environment_id];
    if (k) return `${k.status}, ${kfeWhen(k.at).replace(/^Today/, "today")}`;
    return e.writable === false ? "Read only" : "Not checked yet";
  };
  return h("section", { class: "card pad kfe-card", "aria-labelledby": "kfe-list" },
    h("div", { class: "row" }, h("h3", { class: "grow", id: "kfe-list" }, "Environments on this PC"),
      btn("Back", null, () => kfeGo(el, "status"), "tool outline")),
    s.environments.length ? h("table", { class: "kfe-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Environment"), h("th", {}, "Workspace"), h("th", {}, "Last known status"), h("th", {}, ""))),
      h("tbody", {}, s.environments.map((e) => h("tr", {},
        h("td", {}, h("b", {}, e.name), e.environment_id === activeId ? [" ", h("span", { class: "pill-s pass" }, "Active")] : null),
        h("td", {}, e.workspace_name || e.workspace_id), h("td", {}, known(e)),
        h("td", { class: "kfe-act" },
          e.environment_id === activeId || !s.allowed ? null : btn("Switch to it", null, () => kfeSwitch(el, e), "tool outline small",
            { "aria-label": `Switch to ${e.name}` }),
          btn("Disconnect...", null, () => kfeDisconnect(el, e), "tool outline small", { "aria-label": `Disconnect ${e.name}` }))))))
      : h("p", { class: "muted-text small" }, "This PC isn't connected to any environment."));
}

async function kfeSwitch(el, e) {
  if (!(await attempt(() => rpc("cp_switch", { environment_id: e.environment_id }), "Couldn't switch"))) return;
  toast(`This PC now uses ${e.name}.`);
  kfe.check = null;
  kfe.checkedAt = null;
  kfeGo(el, "status");
}

async function kfeDisconnect(el, e) {
  const waiting = e.environment_id === kfe.status.active?.environment_id ? Number(kfe.check?.waiting || 0) : 0;
  const ok = await confirmDialog(`Disconnect this PC from ${e.name}?`,
    `This PC forgets ${e.name}. Nothing in Fabric is changed or deleted, and you can connect again later.${waiting
      ? ` ${waiting} result${waiting === 1 ? "" : "s"} still wait on this PC. They stay on this PC, and nothing in the environment changes.` : ""}`,
    "Disconnect", { danger: true });
  if (!ok) return;
  if (!(await attempt(() => rpc("cp_disconnect", { environment_id: e.environment_id }), "Couldn't disconnect"))) return;
  toast(`Disconnected this PC from ${e.name}.`);
  delete kfe.lastKnown[e.environment_id];
  if (e.environment_id === kfe.status.active?.environment_id) { kfe.check = null; kfe.checkedAt = null; }
  renderEnvironmentTab(el);
}

// ---------- turned off by policy ----------

function kfePolicyOff(el) {
  const active = kfe.status.active;
  return h("section", { class: "card pad kfe-card" },
    kfeStatusLine("neu", "Turned off", "Your organization has turned off KratosForge Environments. Ask IT to change allow_central_environment in Group Policy or the policy file if you need them."),
    h("p", { class: "small muted-text" }, "Work still runs on this PC. ",
      active ? btn("Disconnect this PC...", null, () => kfeDisconnect(el, active), "tool outline small") : null));
}
