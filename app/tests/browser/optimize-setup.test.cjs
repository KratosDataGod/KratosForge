// #1469: setting up Optimize's shared lakehouse (app/ui/optimize.js), the writes the Optimize page and
// Settings > Optimize lakehouses send (see harness.cjs for how to run it). Create a lakehouse sends
// optimize_create with the workspace and name picked; Connect sends optimize_connect for the lakehouse
// picked and, when the engine says the lakehouse needs Optimize's tables first, sends initialize:true
// only after the person confirms; Switch to sends optimize_switch for that lakehouse; Check sends
// optimize_connect for the connection without initialize; New installation ID asks first, then sends
// optimize_new_installation_id; Name this installation sends optimize_set_label with the trimmed
// name. Cancel at every dialog sends nothing, an engine refusal reads as a sentence (no Python
// exception name), opening the page and Settings sends no write, and axe finds nothing serious on the
// Settings section and the set-up confirmation. Settings still loading when the person moves on (as
// after the redraw a Check or Switch to makes) doesn't come back over the page they went to.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const READS = ["optimize_status", "optimize_discover", "optimize_findings", "optimize_recommendations", "list_workspaces"];
const WRITES = ["optimize_create", "optimize_connect", "optimize_switch", "optimize_disconnect", "optimize_new_installation_id",
  "optimize_set_label", "optimize_set_recommendation", "optimize_share_local_runs"];

const WORKSPACES = [{ id: "w1", displayName: "Platform - Prod" }, { id: "w2", displayName: "Sales" }];

// optimize/setup.py MESSAGES and check_access(): what the engine says about a lakehouse.
const MSG = {
  ready: "Ready.",
  empty: "This lakehouse is empty. Optimize can create its tables here.",
  unrelated: "This lakehouse has other tables. Optimize can add its own beside them without touching them.",
  upgrade: "This lakehouse uses an older Optimize schema. Upgrading adds tables and columns and keeps every row.",
  conflict: "Some tables named fdopt_ here aren't Optimize's, so Optimize won't write to this lakehouse. Pick another lakehouse, or create a new one.",
};
const WRITE_OK = "You can read and write this lakehouse.";
const POLICY_OFF = "PermissionError: Your organization has turned off shared optimization lakehouses (allow_optimize_lakehouse in its policy file).";

// optimize/setup.py inspect(): the answer for a lakehouse in a given state.
function inspection(state, adds = []) {
  return { state, message: MSG[state], family: "fdopt_", schema_version: state === "upgrade" ? 1 : state === "ready" ? 2 : null,
    app_schema_version: 2, missing: adds, adds, conflicts: state === "conflict" ? [{ table: "fdopt_findings", columns: ["severity"] }] : [],
    other_tables: state === "unrelated" ? 3 : 0, writable: state !== "conflict" };
}

// What each lakehouse in each workspace holds, as optimize_service.optimize_discover lists them (ours first).
const LAKES = {
  w1: [
    { lakehouse_id: "lh-ready", lakehouse_name: "FabricDesktop_Central", optimize: true, other_tables: 0, state: "ready" },
    { lakehouse_id: "lh-sales", lakehouse_name: "SalesLake", optimize: false, other_tables: 3, state: "unrelated",
      adds: ["fdopt_audit", "fdopt_findings", "fdopt_installations", "fdopt_schema_version"] },
    { lakehouse_id: "lh-bad", lakehouse_name: "Clash", optimize: false, other_tables: 1, state: "conflict" },
    { lakehouse_id: "lh-locked", lakehouse_name: "Locked", optimize: false, error: "No access" },
  ],
  w2: [],
};

let db;
let usageHeld = null;  // a promise Settings' telemetry_status read waits on
const fail = {};  // method -> the engine's error text, for the next calls to it
const copy = (x) => JSON.parse(JSON.stringify(x));
const conn = (workspace_id, workspace_name, lakehouse_id, lakehouse_name, access = "write") =>
  ({ workspace_id, workspace_name, lakehouse_id, lakehouse_name, schemas: true, schema_version: 2, access,
    checked_at: "2026-10-30T09:00:00.000000+00:00", added_at: "2026-10-30T09:00:00.000000+00:00" });

function fresh(connected = false) {
  const a = conn("w1", "Platform - Prod", "lh-ready", "FabricDesktop_Central");
  const b = conn("w2", "Sales", "lh-sales2", "Sales_Optimization");
  return { installation_id: "3b1f6c2e-0000-4000-8000-000000009a40", label: "", n: 0,
    connections: connected ? [a, b] : [], active: connected ? "lh-ready" : null, lakes: copy(LAKES) };
}

const refuse = (method) => { if (fail[method]) throw new Error(fail[method]); };

// optimize_service.py _optimize_register(): what a finished create or connect answers, after
// registry.add() remembered (and activated) the connection.
function register(workspace_id, workspace_name, lakehouse_id, lakehouse_name, created = false) {
  const c = conn(workspace_id, workspace_name, lakehouse_id, lakehouse_name);
  db.connections = db.connections.filter((x) => x.lakehouse_id !== lakehouse_id).concat(c);
  db.active = lakehouse_id;
  return { connected: true, created, connection: copy(c), access: { read: true, write: true, state: inspection("ready"), message: WRITE_OK } };
}

const mocks = {
  list_workspaces: () => WORKSPACES,
  xray: () => ({ result: null }),
  // Settings' Usage data read (settings.js usageSection), which the last test holds. Unheld it fails at
  // once, as an unmocked call does.
  telemetry_status: async () => { await usageHeld; throw new Error("telemetry_status isn't available in this test"); },
  // optimize_service.py optimize_status()
  optimize_status: () => ({ allowed: true, default_name: "FabricDesktop_Central", installation_id: db.installation_id, label: db.label,
    active: db.connections.find((c) => c.lakehouse_id === db.active) || null, connections: copy(db.connections) }),
  optimize_findings: () => [],
  optimize_recommendations: () => [],
  optimize_discover: ({ workspace_id }) => (db.lakes[workspace_id] || []).map(({ state, adds, ...l }) => l),
  // optimize_service.py optimize_create(): a name already used is refused.
  optimize_create: ({ workspace_id, name, workspace_name }) => {
    refuse("optimize_create");
    if ((db.lakes[workspace_id] || []).some((l) => l.lakehouse_name === name)) {
      throw new Error(`ValueError: This workspace already has a lakehouse called ${name}. Connect to it instead, or pick another name.`);
    }
    const id = `lh-new${++db.n}`;
    db.lakes[workspace_id].push({ lakehouse_id: id, lakehouse_name: name, optimize: true, other_tables: 0, state: "ready" });
    return register(workspace_id, workspace_name, id, name, true);
  },
  // optimize_service.py optimize_connect(): a conflict is refused; a lakehouse in setup.CAN_INITIALIZE
  // answers needs_confirmation unless the call says initialize.
  optimize_connect: ({ workspace_id, lakehouse_id, workspace_name = "", lakehouse_name = "", initialize = false }) => {
    refuse("optimize_connect");
    const lake = (db.lakes[workspace_id] || []).find((l) => l.lakehouse_id === lakehouse_id) || { state: "ready" };
    if (lake.state === "conflict") throw new Error(`ValueError: ${MSG.conflict}`);
    if (["empty", "unrelated", "incomplete", "upgrade"].includes(lake.state)) {
      if (!initialize) return { connected: false, needs_confirmation: true, inspection: inspection(lake.state, lake.adds) };
      lake.state = "ready";
      lake.optimize = true;
    }
    return register(workspace_id, workspace_name, lakehouse_id, lakehouse_name);
  },
  // optimize_service.py optimize_switch() -> registry.activate(): LookupError for one this PC doesn't know.
  optimize_switch: ({ lakehouse_id }) => {
    refuse("optimize_switch");
    const c = db.connections.find((x) => x.lakehouse_id === lakehouse_id);
    if (!c) throw new Error("LookupError: This PC isn't connected to that lakehouse. Connect to it first.");
    db.active = lakehouse_id;
    return { active: copy(c) };
  },
  optimize_disconnect: ({ lakehouse_id }) => {
    db.connections = db.connections.filter((c) => c.lakehouse_id !== lakehouse_id);
    if (db.active === lakehouse_id) db.active = db.connections[0]?.lakehouse_id || null;
    return { disconnected: lakehouse_id, active: db.connections.find((c) => c.lakehouse_id === db.active) || null };
  },
  // optimize_service.py optimize_new_installation_id() -> installation.reset(): a new uuid4.
  optimize_new_installation_id: () => {
    refuse("optimize_new_installation_id");
    db.installation_id = "9c4d2a71-1111-4222-8333-0000000000b7";
    return { installation_id: db.installation_id };
  },
  // optimize_service.py optimize_set_label() -> installation.set_label(): stripped, at most 80 characters.
  optimize_set_label: ({ label }) => {
    refuse("optimize_set_label");
    db.label = String(label || "").trim().slice(0, 80);
    return { label: db.label };
  },
};

before(async () => {
  if (skip) return;
  db = fresh();
  app = await openApp({ engine: ["app_settings", "save_app_settings", "cost_report"], mocks });
  await app.page.waitForFunction(() => typeof renderOptimize === "function" && !!state.settings);
  await startupReads(app);
  app.errors.length = 0;
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => {
    closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.user = "chris@contoso.com"; state.workspaces = null; go("home");
  });
});

const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const dialog = () => page().locator(".dialog");
const dialogButton = (name) => page().locator(".dialog .dialog-actions button", { hasText: new RegExp(`^${name}$`) });

async function dialogTitled(title) {
  await page().waitForFunction((t) => document.querySelector(".dialog")?.getAttribute("aria-label") === t, title);
}
async function noDialog() {
  await page().waitForFunction(() => !document.querySelector(".dialog"));
}
async function toastSays(re) {
  const h = await page().waitForFunction((src) => {
    const t = document.getElementById("toast");
    return !t.hidden && new RegExp(src).test(t.textContent) && t.textContent;
  }, re.source);
  return h.jsonValue();
}

// The Optimize page before any lakehouse is connected, with its set-up buttons drawn.
async function optimizeNotConnected() {
  db = fresh(false);
  await page().evaluate(() => { optimizePage.tab = "overview"; go("optimize"); });
  await page().waitForSelector(".page.optimize .head-actions.optimize-setup button");
}
const headButton = (name) => page().locator(".page.optimize .head-actions button", { hasText: name });

// Settings > Optimize lakehouses, with two lakehouses connected (the first active).
async function settingsSection() {
  db = fresh(true);
  // From another page, so the section found is the one drawn now and not the last one.
  await page().evaluate(() => go("home"));
  await page().waitForFunction(() => !document.querySelector(".page.settings"));
  await page().evaluate(() => { state.settingsTab = "general"; go("settings"); });
  const section = page().locator("section", { has: page().locator("h2:has-text('Optimize lakehouses')") });
  await section.waitFor();
  return section;
}
const lakeLine = (section, name) => section.locator(".setting", { has: page().locator(".setting-text b", { hasText: name }) });

async function pickWorkspace(id) {
  await dialog().locator("select").selectOption(id);
  await dialogButton("Next").click();
}

// The lakehouse list's Connect button for one lakehouse (by its name cell, never the row's middle).
async function connectRow(name) {
  const row = dialog().locator("tr", { has: page().locator("td b", { hasText: new RegExp(`^${name}$`) }) });
  if (!(await row.isVisible())) await dialog().locator("details summary").click();
  return row;
}

test("Create a lakehouse sends the workspace and name picked, and the page then shares through it", { skip }, async () => {
  await optimizeNotConnected();
  const from = app.calls.length;
  await headButton("Create a lakehouse").click();
  await dialogTitled("Create an optimization lakehouse");
  assert.match(await dialog().textContent(), /Pick a workspace everyone who shares results can reach/);
  await pickWorkspace("w2");
  await page().waitForSelector(".dialog input");
  // The name starts as the engine's default (optimize_service.py DEFAULT_NAME).
  assert.strictEqual(await dialog().locator("input").inputValue(), "FabricDesktop_Central");
  assert.match(await dialog().textContent(), /creates a lakehouse in Sales/);
  await dialog().locator("input").fill("  Opt_Central  ");
  await dialogButton("Create").click();
  assert.match(await toastSays(/is ready/), /^Opt_Central is ready: You can read and write this lakehouse\.$/);
  assert.deepStrictEqual(writes(from), [{ method: "optimize_create", params: { workspace_id: "w2", name: "Opt_Central", workspace_name: "Sales" } }]);
  await page().waitForFunction(() => /Shared through lakehouse Opt_Central in Sales · Read and write/.test(document.querySelector(".optimize-band")?.textContent || ""));
  assert.deepStrictEqual(app.errors, []);
});

test("Cancel at either Create dialog sends nothing", { skip }, async () => {
  await optimizeNotConnected();
  const from = app.calls.length;
  await headButton("Create a lakehouse").click();
  await dialogTitled("Create an optimization lakehouse");
  await dialogButton("Cancel").click();
  await noDialog();
  await headButton("Create a lakehouse").click();
  await dialogTitled("Create an optimization lakehouse");
  await pickWorkspace("w1");
  await page().waitForSelector(".dialog input");
  await dialogButton("Cancel").click();
  await noDialog();
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  assert.match(await page().locator(".optimize-band").textContent(), /not shared with your team yet/);
});

test("Create's refusals read as sentences: a name already used, and the policy turning it off", { skip }, async () => {
  await optimizeNotConnected();
  const create = async (name) => {
    await headButton("Create a lakehouse").click();
    await dialogTitled("Create an optimization lakehouse");
    await pickWorkspace("w1");
    await page().waitForSelector(".dialog input");
    await dialog().locator("input").fill(name);
    await dialogButton("Create").click();
  };
  await create("SalesLake");
  const text = await toastSays(/^Could not create the lakehouse/);
  assert.strictEqual(text, "Could not create the lakehouse: This workspace already has a lakehouse called SalesLake. Connect to it instead, or pick another name.\nDetails are in Activity (the bell).");
  await page().evaluate(() => { document.getElementById("toast").hidden = true; });
  fail.optimize_create = POLICY_OFF;
  await create("Another");
  const off = await toastSays(/^Could not create the lakehouse/);
  assert.match(off, /^Could not create the lakehouse: Your organization has turned off shared optimization lakehouses/);
  assert.ok(!/Error|\[object/.test(off), off);
  assert.match(await page().locator(".optimize-band").textContent(), /not shared with your team yet/);
});

test("Connect to a lakehouse that already holds Optimize's tables sends one connect, without initialize", { skip }, async () => {
  await optimizeNotConnected();
  const from = app.calls.length;
  await headButton("Connect to a lakehouse").click();
  await dialogTitled("Connect to an optimization lakehouse");
  await pickWorkspace("w1");
  await dialogTitled("Lakehouses in Platform - Prod");
  // optimize_discover's ours-first order: Optimize's own lakehouse on top, the others folded away,
  // and one that can't be read offers no Connect.
  const row = await connectRow("FabricDesktop_Central");
  const locked = await connectRow("Locked");
  assert.match(await locked.textContent(), /No access/);
  assert.strictEqual(await locked.locator("button").count(), 0);
  await row.locator("button", { hasText: "Connect" }).click();
  assert.strictEqual(await toastSays(/./), WRITE_OK);
  assert.deepStrictEqual(writes(from), [{ method: "optimize_connect",
    params: { workspace_id: "w1", lakehouse_id: "lh-ready", workspace_name: "Platform - Prod", lakehouse_name: "FabricDesktop_Central" } }]);
  assert.ok(app.calls.slice(from).some((c) => c.method === "optimize_discover" && c.params.workspace_id === "w1"));
  await page().waitForFunction(() => /Shared through lakehouse FabricDesktop_Central/.test(document.querySelector(".optimize-band")?.textContent || ""));
});

test("Connect to another lakehouse asks before setting it up, and sends initialize only after OK", { skip }, async () => {
  await optimizeNotConnected();
  const args = { workspace_id: "w1", lakehouse_id: "lh-sales", workspace_name: "Platform - Prod", lakehouse_name: "SalesLake" };
  const open = async () => {
    await headButton("Connect to a lakehouse").click();
    await dialogTitled("Connect to an optimization lakehouse");
    await pickWorkspace("w1");
    await dialogTitled("Lakehouses in Platform - Prod");
    const row = await connectRow("SalesLake");
    assert.match(await row.textContent(), /3 other tables, left untouched/);
    await row.locator("button", { hasText: "Connect" }).click();
    await dialogTitled("Set up Optimize in SalesLake?");
  };
  // Cancel: only the first, asking connect went; nothing initialized.
  let from = app.calls.length;
  await open();
  assert.strictEqual(await dialog().locator("p.text").textContent(),
    `${MSG.unrelated} This adds 4 tables named fdopt_ and changes nothing else.`);
  await dialogButton("Cancel").click();
  await noDialog();
  await pause(300);
  assert.deepStrictEqual(writes(from), [{ method: "optimize_connect", params: args }]);
  assert.match(await page().locator(".optimize-band").textContent(), /not shared with your team yet/);
  // OK: the same lakehouse again, with initialize.
  from = app.calls.length;
  await open();
  await dialogButton("Set up and connect").click();
  assert.strictEqual(await toastSays(/./), WRITE_OK);
  assert.deepStrictEqual(writes(from), [{ method: "optimize_connect", params: args },
    { method: "optimize_connect", params: { ...args, initialize: true } }]);
  await page().waitForFunction(() => /Shared through lakehouse SalesLake/.test(document.querySelector(".optimize-band")?.textContent || ""));
});

test("Cancel at the workspace and lakehouse lists sends nothing, and a conflict reads as a sentence", { skip }, async () => {
  await optimizeNotConnected();
  const from = app.calls.length;
  await headButton("Connect to a lakehouse").click();
  await dialogTitled("Connect to an optimization lakehouse");
  await dialogButton("Cancel").click();
  await noDialog();
  await headButton("Connect to a lakehouse").click();
  await dialogTitled("Connect to an optimization lakehouse");
  await pickWorkspace("w2");
  await dialogTitled("Lakehouses in Sales");
  assert.match(await dialog().textContent(), /No optimization lakehouse in this workspace yet/);
  await dialogButton("Cancel").click();
  await noDialog();
  // A list with lakehouses in it, Cancelled too.
  await headButton("Connect to a lakehouse").click();
  await dialogTitled("Connect to an optimization lakehouse");
  await pickWorkspace("w1");
  await dialogTitled("Lakehouses in Platform - Prod");
  await dialogButton("Cancel").click();
  await noDialog();
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  // A lakehouse with someone else's fdopt_ tables: the engine refuses, and says why.
  await headButton("Connect to a lakehouse").click();
  await dialogTitled("Connect to an optimization lakehouse");
  await pickWorkspace("w1");
  await dialogTitled("Lakehouses in Platform - Prod");
  await (await connectRow("Clash")).locator("button", { hasText: "Connect" }).click();
  const text = await toastSays(/^Could not connect/);
  assert.strictEqual(text, `Could not connect: ${MSG.conflict}\nDetails are in Activity (the bell).`);
  assert.match(await page().locator(".optimize-band").textContent(), /not shared with your team yet/);
});

test("Switch to sends that lakehouse's id, and the other one is then active", { skip }, async () => {
  const section = await settingsSection();
  const other = lakeLine(section, "Sales_Optimization");
  assert.strictEqual(await lakeLine(section, "FabricDesktop_Central").locator("button", { hasText: "Switch to" }).count(), 0, "the active one offers no Switch to");
  const from = app.calls.length;
  await other.locator("button", { hasText: "Switch to" }).click();
  await page().waitForFunction(() => [...document.querySelectorAll(".setting-text b")].some((b) => b.textContent === "Sales_Optimization (active)"));
  assert.deepStrictEqual(writes(from), [{ method: "optimize_switch", params: { lakehouse_id: "lh-sales2" } }]);
  // The engine's refusal (registry.activate) reads as a sentence.
  fail.optimize_switch = "LookupError: This PC isn't connected to that lakehouse. Connect to it first.";
  await lakeLine(section, "FabricDesktop_Central").locator("button", { hasText: "Switch to" }).click();
  assert.strictEqual(await toastSays(/^Could not switch/),
    "Could not switch: This PC isn't connected to that lakehouse. Connect to it first.\nDetails are in Activity (the bell).");
});

test("Check sends that connection to optimize_connect without initialize, and says what it found", { skip }, async () => {
  const section = await settingsSection();
  let from = app.calls.length;
  await lakeLine(section, "FabricDesktop_Central").locator("button", { hasText: "Check" }).click();
  assert.strictEqual(await toastSays(/./), WRITE_OK);
  assert.deepStrictEqual(writes(from), [{ method: "optimize_connect",
    params: { workspace_id: "w1", lakehouse_id: "lh-ready", workspace_name: "Platform - Prod", lakehouse_name: "FabricDesktop_Central" } }]);
  // One that now needs an upgrade: Check says so, and never initializes it.
  await settingsSection();
  db.lakes.w2.push({ lakehouse_id: "lh-sales2", lakehouse_name: "Sales_Optimization", optimize: true, state: "upgrade", adds: ["fdopt_costs"] });
  await page().evaluate(() => { document.getElementById("toast").hidden = true; });
  from = app.calls.length;
  await lakeLine(section, "Sales_Optimization").locator("button", { hasText: "Check" }).click();
  assert.strictEqual(await toastSays(/./), MSG.upgrade);
  assert.deepStrictEqual(writes(from), [{ method: "optimize_connect",
    params: { workspace_id: "w2", lakehouse_id: "lh-sales2", workspace_name: "Sales", lakehouse_name: "Sales_Optimization" } }]);
  fail.optimize_connect = POLICY_OFF;
  await lakeLine(section, "Sales_Optimization").locator("button", { hasText: "Check" }).click();
  assert.match(await toastSays(/^Could not check the lakehouse/), /^Could not check the lakehouse: Your organization has turned off shared optimization lakehouses \(allow_optimize_lakehouse in its policy file\)\./);
});

test("New installation ID asks first: Cancel sends nothing, OK sends it and shows the new id", { skip }, async () => {
  const section = await settingsSection();
  await section.locator("details summary", { hasText: "Advanced" }).click();
  assert.match(await section.textContent(), /3b1f6c2e-0000-4000-8000-000000009a40/);
  const from = app.calls.length;
  await section.locator("button", { hasText: "New installation ID" }).click();
  await dialogTitled("Use a new installation ID?");
  assert.match(await dialog().textContent(), /Rows already written keep the old ID/);
  await dialogButton("Cancel").click();
  await noDialog();
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  await section.locator("button", { hasText: "New installation ID" }).click();
  await dialogTitled("Use a new installation ID?");
  await dialogButton("Use a new ID").click();
  await page().waitForFunction(() => /9c4d2a71-1111-4222-8333-0000000000b7/.test(document.querySelector(".page.settings")?.textContent || ""));
  assert.deepStrictEqual(writes(from), [{ method: "optimize_new_installation_id", params: {} }]);
  // A refusal reads as a sentence.
  fail.optimize_new_installation_id = "PermissionError: [Errno 13] Permission denied: 'C:\\\\Users\\\\chris\\\\AppData\\\\Local\\\\FabricDesktop\\\\optimize\\\\installation.json'";
  const again = await settingsSection();
  await again.locator("details summary", { hasText: "Advanced" }).click();
  await again.locator("button", { hasText: "New installation ID" }).click();
  await dialogButton("Use a new ID").click();
  const text = await toastSays(/^Could not reset the ID/);
  assert.match(text, /^Could not reset the ID: This PC didn't allow access to C:\\Users\\chris\\AppData\\Local\\FabricDesktop\\optimize\\installation\.json\./);
  assert.ok(!/Errno|PermissionError/.test(text), text);
});

test("Name this installation sends the trimmed name, and a refusal reads as a sentence", { skip }, async () => {
  const section = await settingsSection();
  await section.locator("details summary", { hasText: "Advanced" }).click();
  const input = section.locator("input[placeholder='For example: Build server']");
  assert.strictEqual(await input.getAttribute("maxlength"), "80");  // installation.set_label keeps 80
  const from = app.calls.length;
  await input.fill("  Build server  ");
  await section.locator("button", { hasText: /^Save$/ }).click();
  assert.strictEqual(await toastSays(/./), "Saved");
  assert.deepStrictEqual(writes(from), [{ method: "optimize_set_label", params: { label: "Build server" } }]);
  fail.optimize_set_label = POLICY_OFF;
  await page().evaluate(() => { document.getElementById("toast").hidden = true; });
  await section.locator("button", { hasText: /^Save$/ }).click();
  assert.match(await toastSays(/^Could not save the name/), /^Could not save the name: Your organization has turned off shared optimization lakehouses/);
});

test("opening Optimize and Settings > Optimize lakehouses sends no write", { skip }, async () => {
  const from = app.calls.length;
  await optimizeNotConnected();
  await page().evaluate(() => { optimizePage.tab = "opportunities"; render(); });
  await page().waitForSelector(".page.optimize .optimize-setup");
  const section = await settingsSection();
  await section.locator("details summary", { hasText: "Advanced" }).click();
  await page().evaluate(() => { optimizePage.tab = "overview"; go("optimize"); });
  await page().waitForSelector(".optimize-band b");
  await pause(300);
  const made = app.calls.slice(from).map((c) => c.method);
  assert.ok(made.includes("optimize_status"), made.join(", "));
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.ok(made.filter((m) => m.startsWith("optimize_")).every((m) => READS.includes(m)), made.join(", "));
  assert.deepStrictEqual(app.errors, []);
});

test("axe finds nothing serious on Settings > Optimize lakehouses and the set-up confirmation", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const section = await settingsSection();
  await section.locator("details summary", { hasText: "Advanced" }).click();
  await page().evaluate(() => {
    const s = [...document.querySelectorAll("section")].find((x) => x.querySelector("h2")?.textContent.startsWith("Optimize lakehouses"));
    s.id = "optimize-lakehouses";
  });
  assert.deepStrictEqual(await axeFindings(page(), "#optimize-lakehouses"), [], "Settings section");
  await optimizeNotConnected();
  await headButton("Connect to a lakehouse").click();
  await dialogTitled("Connect to an optimization lakehouse");
  await pickWorkspace("w1");
  await dialogTitled("Lakehouses in Platform - Prod");
  await (await connectRow("SalesLake")).locator("button", { hasText: "Connect" }).click();
  await dialogTitled("Set up Optimize in SalesLake?");
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), [], "set-up confirmation");
  await dialogButton("Cancel").click();
});

test("Settings still loading when the person moves on doesn't come back over the next page", { skip }, async () => {
  db = fresh(true);
  let release;
  usageHeld = new Promise((r) => { release = r; });
  try {
    const from = app.calls.length;
    await page().evaluate(() => { state.settingsTab = "general"; go("settings"); });
    for (const end = Date.now() + 10000; !app.calls.slice(from).some((c) => c.method === "telemetry_status"); await pause(25)) {
      if (Date.now() > end) throw new Error("Settings never read Usage data");
    }
    await page().evaluate(() => go("home"));
  } finally { release(); usageHeld = null; }
  await pause(500);
  assert.deepStrictEqual(await page().evaluate(() => ({ view: state.view, settings: !!document.querySelector(".page.settings") })),
    { view: "home", settings: false });
});
