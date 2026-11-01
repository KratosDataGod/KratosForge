// #1475: the writes on a workload's page (app/ui/workloads.js) that change what this PC keeps about it
// (see harness.cjs for how to run it). Edit under Ownership sends workload_assign with only the roles,
// criticality and tags that changed, never a role the project's files declare; Link on a notebook matched
// by name sends workload_link with that item; More > Unlink sends workload_unlink and, when the link is
// one this PC keeps (so it decides which item Run in Fabric runs), asks first. Cancel sends nothing.
// An engine refusal reads as a sentence, without the exception's name. Opening the pages sends no write,
// and axe finds nothing serious on a workload page and on each dialog.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const READS = ["workload_detail"];
const WRITES = ["workload_assign", "workload_link", "workload_unlink", "workload_act", "risk_ack"];
const ROLES = ["team", "technical_owner", "business_contact", "support_contact"];  // workloads/model.py ROLES
const ROLE_LABELS = { team: "Team", technical_owner: "Technical owner", business_contact: "Business contact",
  support_contact: "Support contact" };  // workloads/model.py ROLE_LABELS
const CRITICALITY = ["critical", "high", "standard", "low"];  // workloads/model.py CRITICALITY

const at = (s) => new Date(Date.now() + s * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
const copy = (x) => JSON.parse(JSON.stringify(x));

// Two notebooks on this PC and one metadata entity. daily_sales is linked by hand to its Fabric item;
// orders_clean has no kept link and one notebook of its name in its publish workspace, so it is matched
// by name (inventory.notebook_link); silver.orders declares its team and technical owner in Git.
const DAILY = "notebook:daily_sales", ORDERS = "notebook:orders_clean", ENTITY = "meta:sales:silver.orders";
const WS = { workspace_id: "0b6f3c2a-5d1e-4f7a-9c8b-1a2b3c4d5e6f", workspace_name: "Sales Dev" };
const DAILY_ITEM = "7d1c0e9a-2b3f-4a5c-8d6e-0f1a2b3c4d5e", ORDERS_ITEM = "9e8d7c6b-5a4f-4e3d-2c1b-0a9f8e7d6c5b";

function fresh() {
  return {
    // workloads/store.py: assignment rows, {wid: {field: {value, set_by, set_at}}}; tags are a list.
    assignments: {
      [DAILY]: { team: { value: "Sales data team", set_by: ME, set_at: at(-86400) }, tags: { value: ["daily"], set_by: ME, set_at: at(-86400) } },
      [ENTITY]: { business_contact: { value: "erin@contoso.com", set_by: ME, set_at: at(-86400) },
        criticality: { value: "high", set_by: ME, set_at: at(-86400) } },
    },
    // workloads/store.py: link rows by notebook name.
    links: { daily_sales: { notebook: "daily_sales", ...WS, item_id: DAILY_ITEM, how: "manual", by: ME, at: at(-3600) } },
    // inventory.meta_workloads: {role: (value, where)} from project.toml and the entity file.
    declared: { team: ["Sales data team", "project.toml"], technical_owner: ["dana@contoso.com", "entity file"] },
    history: [],
    notebooks: ["daily_sales", "orders_clean"],  // the files NotebookStore.list finds
    inventory: [DAILY, ORDERS, ENTITY],
  };
}
let db = fresh();
const fail = {};  // method -> the engine's error text, for the next calls to it

const live = (source) => ({ source, state: "live", label: "Live", collected_at: at(0).replace("Z", "+00:00"), age_seconds: 0, detail: "" });

// inventory.owners: a declared role wins; otherwise the assigned one.
function owners(declared, assigned) {
  const out = {};
  for (const role of ROLES) {
    const [value, where] = declared[role] || ["", ""];
    if (value) out[role] = { value, source: "declared", where, placeholder: false };
    else if (assigned[role]) out[role] = { value: assigned[role].value, source: "assigned", where: "on this PC",
      set_by: assigned[role].set_by, set_at: assigned[role].set_at, placeholder: false };
  }
  return out;
}

// inventory.notebook_link: a kept link first, then the same name in its publish workspace.
const notebookLink = (name) => db.links[name] ? { ...WS, item_id: db.links[name].item_id, how: db.links[name].how, at: db.links[name].at }
  : name === "orders_clean" ? { ...WS, item_id: ORDERS_ITEM, how: "name match", at: null } : null;

// workload_service.workload_detail, built the way inventory.py and health.py build it.
function detail(id) {
  if (!db.inventory.includes(id)) {
    throw new Error("NotInInventory: That workload isn't in the inventory any more. It may have been renamed, deleted, or be in a workspace this app no longer reads.");
  }
  const assigned = db.assignments[id] || {};
  const crit = assigned.criticality ? { value: assigned.criticality.value, source: "assigned", where: "on this PC" } : null;
  const tags = assigned.tags?.value || [];
  const findings = [];
  const add = (level, kind, title, text, evidence, next) => findings.push({ level, kind, title, text, evidence, next, env: null });
  let w;
  if (id === ENTITY) {
    w = { id, kind: "meta_entity", kind_label: "Metadata entity", name: "silver.orders", description: "Cleaned orders",
      group: { type: "project", id: "sales", label: "sales" },
      definition: { project: "sales", layer: "silver", path: "C:\\repo\\sales\\entities\\silver\\orders.toml" },
      capabilities: ["discoverable", "observable", "runnable", "deployable"], owners: owners(db.declared, assigned),
      deployments: [], link: null };
  } else {
    const name = id.slice("notebook:".length);
    const link = notebookLink(name);
    const deployments = [{ env: null, env_label: "", location: { kind: "this_pc", label: "This PC" },
      identity: { kind: "you", label: `You (${ME}), signed in on this PC` }, tables_in: "lh_sales", version: null, schedules: [],
      latest_run: null, freshness: null, observation: live("Schedules on this PC"), actions: [] }];
    if (link) {
      deployments.push({ env: null, env_label: "",
        location: { kind: "fabric", label: `Fabric · ${link.workspace_name}`, workspace_id: link.workspace_id, workspace_name: link.workspace_name,
          item_id: link.item_id, url: `https://app.fabric.microsoft.com/groups/${link.workspace_id}/synapsenotebooks/${link.item_id}` },
        identity: { kind: "you_or_schedule_owner", label: "You, for runs started here; the schedule's owner for Fabric schedules" },
        tables_in: "Sales Dev", version: null, link, schedules: [], latest_run: null, freshness: null,
        observation: link.how === "name match" ? live("Fabric items in your workspaces") : live("Fabric jobs started from this app"),
        actions: ["run_in_fabric"] });
      if (link.how === "name match") {
        add("notice", "identity", "Linked to its Fabric item by name only",
          "Publish or run it in Fabric from this app once, or link it by hand, so a rename can't break the link.",
          { source: "name match", item_id: link.item_id }, [{ action: "link", label: "Link" }]);
      }
    }
    w = { id, kind: "notebook", kind_label: "Notebook", name, description: "",
      group: { type: "notebooks", id: "notebooks", label: "Notebooks on this PC" },
      definition: { name, modified: Date.now() / 1000 - 7200, compute: "local", lakehouse: { name: "lh_sales" }, workspace: WS },
      capabilities: ["discoverable", "observable", "runnable", "schedulable", "deployable"].concat(link ? ["cancelable"] : []),
      owners: owners({}, assigned), deployments, link };
  }
  if (!Object.values(w.owners).some((o) => o.value && !o.placeholder)) {
    add("notice", "ownership", "No owner", "Nobody is accountable for it, so nobody is told when it fails.", {}, [{ action: "assign", label: "Assign" }]);
  }
  return { ...w, criticality: crit, tags, findings, health: "idle", health_label: "No recorded runs", health_rank: 5,
    readiness: { state: w.deployments.length ? "ready" : "unknown", label: w.deployments.length ? "Ready" : "Not deployed anywhere" },
    unowned: findings.some((f) => f.kind === "ownership"), partial: false, errors: [],
    dependencies: { upstream: [], downstream: [], products: [] }, observation: live("Notebooks on this PC"),
    runs: [], actions: [], assignment_history: db.history.filter((h) => h.wid === id),
    platform_audit: { platform: "none", waiting: 0, error: "" }, timeline: [], consumers: [],
    // workload_service._wl_available: one per deployment action.
    available_actions: w.deployments.flatMap((d) => d.actions.map((a) => ({ action: a, label: "Run in Fabric", env: null, where: d.location.label }))) };
}

const refuse = (method) => { if (fail[method]) throw new Error(fail[method]); };

const mocks = {
  readiness_check: () => [],
  workload_detail: ({ id }) => detail(id),
  workload_capacity_use: ({ id }) => ({ id, days: 30, fabric_runs: 0, pc_runs: 0, measured_runs: 0, measured: null, total: null, problem: "" }),
  incidents_summary: () => ({ envs: [], user: ME }),
  // workload_service.workload_assign and WorkloadStore.assign: a declared role is refused; an empty value clears.
  workload_assign: ({ id, changes }) => {
    refuse("workload_assign");
    const w = detail(id);
    const declared = Object.keys(changes).find((r) => w.owners[r]?.source === "declared");
    if (declared) {
      throw new Error(`ValueError: ${ROLE_LABELS[declared]} is set in the ${w.owners[declared].where}, so change it there. What's in the project's files wins over this PC.`);
    }
    for (const [field, value] of Object.entries(changes)) {
      if (![...ROLES, "criticality", "tags"].includes(field)) throw new Error(`ValueError: ${field} can't be assigned`);
      if (field === "criticality" && value && !CRITICALITY.includes(value)) throw new Error(`ValueError: criticality is one of ${CRITICALITY.join(", ")}`);
      const row = (db.assignments[id] ||= {});
      const v = field === "tags" ? [...new Set(value.map((t) => String(t).trim()).filter(Boolean))].sort() : String(value || "").trim();
      if (field === "tags" ? v.length : v) row[field] = { value: v, set_by: ME, set_at: at(0) };
      else delete row[field];
      db.history.push({ wid: id, field, by: ME, at: at(0) });
    }
    return detail(id);
  },
  // workload_service.workload_link: the notebook must exist on this PC (NotebookStore.load), then a hand-made link.
  workload_link: ({ notebook, workspace_id, item_id, workspace_name = "" }) => {
    refuse("workload_link");
    if (!db.notebooks.includes(notebook)) {
      throw new Error(`FileNotFoundError: [Errno 2] No such file or directory: '~\\AppData\\Local\\KratosForge\\notebooks\\${notebook}.ipynb'`);
    }
    db.links[notebook] = { notebook, workspace_id, workspace_name, item_id, how: "manual", by: ME, at: at(0) };
    return detail(`notebook:${notebook}`);
  },
  // workload_service.workload_unlink: forgets a kept link (nothing to forget for a name match).
  workload_unlink: ({ notebook }) => {
    refuse("workload_unlink");
    delete db.links[notebook];
    return { unlinked: notebook };
  },
};

let baseline;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
  baseline = new Set(app.calls.map((c) => c.method));
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => { closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; go("home"); });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

// The workload's page, read afresh, once drawn; `until` (a page text) waits for it too.
async function openPage(id, until = null) {
  await page().evaluate((id) => openWorkload(id), id);
  return pageNow(id, until);
}
async function pageNow(id, until = null, gone = null) {
  const handle = await page().waitForFunction(([id, until, gone]) => {
    const pg = document.querySelector(".page.wl-page");
    if (state.view !== "workload" || wlPage.detail?.id !== id || !pg?.querySelector(".wl-grid")) return false;
    const text = pg.innerText;
    if (until && !text.includes(until)) return false;
    if (gone && text.includes(gone)) return false;
    return { text, subtitle: pg.querySelector(".sub").innerText,
      ownership: [...pg.querySelectorAll(".wl-grid table.mini tr")].map((tr) => [...tr.children].map((td) => td.innerText.trim())),
      deployments: [...pg.querySelectorAll("section.card")].find((s) => s.querySelector("h2")?.textContent === "Deployments").innerText };
  }, [id, until, gone]);
  return handle.jsonValue();
}

async function toastText() {
  const handle = await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
  return (await handle.jsonValue());
}

async function editOwnership(name) {
  await page().locator(".page.wl-page section.card", { has: page().locator("h2", { hasText: "Ownership" }) })
    .getByRole("button", { name: "Edit" }).click();
  const dialog = page().getByRole("dialog", { name: `Ownership of ${name}` });
  await dialog.waitFor();
  return dialog;
}

async function moreItem(label) {
  await page().locator(".page.wl-page .wl-head").getByRole("button", { name: "More" }).click();
  return page().getByRole("menuitem", { name: label });
}

// ---------- workload_assign

test("Edit ownership sends only the boxes that changed, and the page shows them", { skip }, async () => {
  await openPage(DAILY);
  const from = app.calls.length;
  const dialog = await editOwnership("daily_sales");
  await dialog.getByRole("textbox", { name: "Technical owner" }).fill(ME);
  await dialog.getByRole("textbox", { name: "Tags, separated by commas" }).fill("daily, finance");
  await dialog.getByRole("combobox", { name: "Criticality" }).selectOption("high");
  await dialog.getByRole("button", { name: "Save" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "workload_assign",
    params: { id: DAILY, changes: { technical_owner: ME, criticality: "high", tags: ["daily", "finance"] } } }]);
  const shown = await pageNow(DAILY, "finance");
  assert.deepStrictEqual(shown.ownership, [
    ["Team", "Sales data team", "on this PC"], ["Technical owner", ME, "on this PC"], ["Business contact", "Not set", ""],
    ["Support contact", "Not set", ""], ["Criticality", "high", "on this PC"], ["Tags", "daily, finance", ""]]);
  assert.match((await toastText()), /^Saved on this PC\./);
});

test("clearing a box sends it empty, which removes it, and nothing else", { skip }, async () => {
  await openPage(DAILY);
  const from = app.calls.length;
  const dialog = await editOwnership("daily_sales");
  await dialog.getByRole("textbox", { name: "Team" }).fill("");
  await dialog.getByRole("textbox", { name: "Tags, separated by commas" }).fill("");
  await dialog.getByRole("button", { name: "Save" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "workload_assign", params: { id: DAILY, changes: { team: "", tags: [] } } }]);
  const shown = await pageNow(DAILY, "No owner");
  assert.deepStrictEqual(shown.ownership.slice(0, 1), [["Team", "Not set", ""]]);
  assert.ok(!shown.ownership.some((r) => r[0] === "Tags"), "no tags row once they're all removed");
});

test("a role the project's files declare can't be edited and is never sent", { skip }, async () => {
  await openPage(ENTITY);
  const from = app.calls.length;
  const dialog = await editOwnership("silver.orders");
  for (const [label, where] of [["Team", "project.toml"], ["Technical owner", "entity file"]]) {
    assert.ok(await dialog.getByRole("textbox", { name: label }).isDisabled(), label);
    assert.match(await dialog.innerText(), new RegExp(`Set in the ${where.replace(".", "\\.")}\\. Change it there\\.`));
  }
  await dialog.getByRole("textbox", { name: "Support contact" }).fill("  oncall@contoso.com ");
  await dialog.getByRole("button", { name: "Save" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "workload_assign",
    params: { id: ENTITY, changes: { support_contact: "oncall@contoso.com" } } }]);
  await pageNow(ENTITY, "oncall@contoso.com");
});

test("Save with nothing changed, and Cancel after typing, send nothing", { skip }, async () => {
  await openPage(ENTITY);
  const before = JSON.stringify(db);
  const from = app.calls.length;
  let dialog = await editOwnership("silver.orders");
  await dialog.getByRole("button", { name: "Save" }).click();
  await dialog.waitFor({ state: "detached" });
  dialog = await editOwnership("silver.orders");
  await dialog.getByRole("textbox", { name: "Business contact" }).fill("frank@contoso.com");
  await dialog.getByRole("combobox", { name: "Criticality" }).selectOption("");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(300);  // nothing should happen: give it the chance
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual(JSON.stringify(db), before);
});

test("assign refusals read as a sentence and keep the dialog with what was typed", { skip }, async () => {
  await openPage(ENTITY);
  // The project file now declares the business contact too (it changed after the page was read).
  db.declared.business_contact = ["erin@contoso.com", "project.toml"];
  const from = app.calls.length;
  const dialog = await editOwnership("silver.orders");
  await dialog.getByRole("textbox", { name: "Business contact" }).fill("frank@contoso.com");
  await dialog.getByRole("button", { name: "Save" }).click();
  let said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Not saved: Business contact is set in the project.toml, so change it there. What's in the project's files wins over this PC.");
  assert.strictEqual(await dialog.getByRole("textbox", { name: "Business contact" }).inputValue(), "frank@contoso.com");
  // The workload left the inventory (renamed or deleted since the page was read): NotInInventory.
  db.inventory = db.inventory.filter((x) => x !== ENTITY);
  await page().evaluate(() => { const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
  await dialog.getByRole("button", { name: "Save" }).click();
  said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Not saved: That workload isn't in the inventory any more. It may have been renamed, deleted, or be in a workspace this app no longer reads.");
  assert.doesNotMatch(said, /NotInInventory|ValueError/);
  assert.strictEqual(await dialog.count(), 1);
  assert.deepStrictEqual(writes(from).map((c) => c.params), [
    { id: ENTITY, changes: { business_contact: "frank@contoso.com" } }, { id: ENTITY, changes: { business_contact: "frank@contoso.com" } }]);
  await dialog.getByRole("button", { name: "Cancel" }).click();
});

// ---------- workload_link

async function startLink() {
  await openPage(ORDERS, "Linked to its Fabric item by name only");
  await page().locator(".page.wl-page .wl-finding", { hasText: "Linked to its Fabric item by name only" }).getByRole("button", { name: "Link" }).click();
  const dialog = page().getByRole("dialog", { name: "Link orders_clean to its Fabric item?" });
  await dialog.waitFor();
  return dialog;
}

test("Link on a notebook matched by name sends that item, and the page shows it linked by hand", { skip }, async () => {
  const from = app.calls.length;
  const dialog = await startLink();
  assert.match(await dialog.innerText(), /found a notebook named orders_clean in Sales Dev and matched it by name/);
  assert.match(await dialog.innerText(), /Nothing changes in Fabric\. The link is kept on this PC\./);
  await dialog.getByRole("button", { name: "Link", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "workload_link",
    params: { notebook: "orders_clean", workspace_id: WS.workspace_id, item_id: ORDERS_ITEM, workspace_name: "Sales Dev" } }]);
  const shown = await pageNow(ORDERS, "linked to its Fabric item (manual)", "by name only");
  assert.match(shown.deployments, /Fabric · Sales Dev/);
});

test("Link: Cancel sends nothing and the name match stays", { skip }, async () => {
  const before = JSON.stringify(db);
  const from = app.calls.length;
  const dialog = await startLink();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual(JSON.stringify(db), before);
  assert.match((await pageNow(ORDERS)).subtitle, /linked to its Fabric item \(name match\)/);
});

test("a link refusal reads as a sentence and the dialog stays", { skip }, async () => {
  const dialog = await startLink();
  db.notebooks = ["daily_sales"];  // the notebook's file was deleted after the page was read
  await dialog.getByRole("button", { name: "Link", exact: true }).click();
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Not linked: There is no file or folder at ~\\AppData\\Local\\KratosForge\\notebooks\\orders_clean.ipynb on this PC. Check the path, or pick the file again.");
  assert.doesNotMatch(said.split("\n")[0], /FileNotFoundError|Errno/);
  assert.strictEqual(await dialog.count(), 1);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(Object.keys(db.links), ["daily_sales"]);
});

// ---------- workload_unlink

async function startUnlink() {
  await openPage(DAILY, "Fabric · Sales Dev");
  await (await moreItem("Unlink from its Fabric item")).click();
  const dialog = page().getByRole("dialog", { name: "Unlink daily_sales from its Fabric item?" });
  await dialog.waitFor();
  return dialog;
}

test("Unlink of a kept link asks first, saying which item Run in Fabric runs now; Unlink sends that notebook", { skip }, async () => {
  const from = app.calls.length;
  const dialog = await startUnlink();
  const text = await dialog.innerText();
  assert.match(text, new RegExp(`Run in Fabric runs the notebook in Sales Dev \\(item ${DAILY_ITEM}\\) now`));
  assert.match(text, /Run in Fabric isn't offered until you publish or link it again/);
  const ok = dialog.getByRole("button", { name: "Unlink", exact: true });
  assert.match(await ok.getAttribute("class"), /danger-solid/);
  assert.ok(await dialog.getByRole("button", { name: "Cancel" }).evaluate((b) => b === document.activeElement), "Cancel has focus");
  assert.deepStrictEqual(writes(from), [], "nothing is sent while it asks");
  await ok.click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "workload_unlink", params: { notebook: "daily_sales" } }]);
  const shown = await pageNow(DAILY, "not linked to a Fabric item");
  assert.doesNotMatch(shown.deployments, /Fabric · Sales Dev/);
  assert.match(await toastText(), /^daily_sales is no longer linked/);
  await page().locator(".page.wl-page .wl-head").getByRole("button", { name: "More" }).click();
  assert.strictEqual(await page().getByRole("menuitem", { name: "Unlink from its Fabric item" }).count(), 0);
});

test("Unlink: Cancel sends nothing and the link stays", { skip }, async () => {
  const before = JSON.stringify(db);
  const from = app.calls.length;
  const dialog = await startUnlink();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual(JSON.stringify(db), before);
  assert.match((await pageNow(DAILY)).subtitle, /linked to its Fabric item \(manual\)/);
});

test("Unlink of a name match changes nothing Run in Fabric targets, so it doesn't ask", { skip }, async () => {
  await openPage(ORDERS, "Fabric · Sales Dev");
  const from = app.calls.length;
  await (await moreItem("Unlink from its Fabric item")).click();
  for (const end = Date.now() + 5000; !writes(from).length; await pause(25)) if (Date.now() > end) break;
  assert.deepStrictEqual(writes(from), [{ method: "workload_unlink", params: { notebook: "orders_clean" } }]);
  assert.strictEqual(await page().locator("#dialog-root [role=dialog]").count(), 0);
  // Still matched by name, so Run in Fabric still runs the same item.
  const shown = await pageNow(ORDERS, "linked to its Fabric item (name match)");
  assert.match(shown.deployments, /Fabric · Sales Dev/);
});

test("an unlink refusal reads as a sentence and the link stays", { skip }, async () => {
  fail.workload_unlink = "OperationalError: database is locked";  // workloads.sqlite busy past its 30 s timeout (store.py)
  const dialog = await startUnlink();
  await dialog.getByRole("button", { name: "Unlink", exact: true }).click();
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Not unlinked: Something went wrong inside the app. The details below help with a bug report.");
  assert.ok(db.links.daily_sales, "still linked");
  assert.match((await pageNow(DAILY)).subtitle, /linked to its Fabric item \(manual\)/);
});

// ---------- reading

test("opening the workload pages sends no write", { skip }, async () => {
  const from = app.calls.length;
  await openPage(DAILY, "Fabric · Sales Dev");
  await openPage(ORDERS, "Linked to its Fabric item by name only");
  await openPage(ENTITY, "dana@contoso.com");
  await (await moreItem("Read again")).click();
  for (const end = Date.now() + 5000; !app.calls.slice(from).some((c) => c.params?.refresh); await pause(25)) if (Date.now() > end) break;
  await pageNow(ENTITY);
  await pause(300);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  const reads = new Set([...READS, "workload_capacity_use", "incidents_summary"]);
  assert.deepStrictEqual([...new Set(made.filter((m) => !reads.has(m) && !baseline.has(m)))], []);
});

test("axe finds nothing serious on a workload page and its dialogs", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const check = async (where, selector) => assert.deepStrictEqual(await axeFindings(page(), selector), [], where);
  await openPage(ORDERS, "Linked to its Fabric item by name only");
  await check("workload page", ".page.wl-page");
  await editOwnership("orders_clean");
  await check("ownership dialog", "#dialog-root .dialog");
  await page().evaluate(() => closeDialog());
  await startLink();
  await check("link dialog", "#dialog-root .dialog");
  await page().evaluate(() => closeDialog());
  await startUnlink();
  await check("unlink dialog", "#dialog-root .dialog");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
