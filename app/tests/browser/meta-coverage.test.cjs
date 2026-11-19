// app/ui/meta.js, the Data mesh project editor, beyond what the other mesh tests drive: setting up a
// project, the Layers editor, New entity (from the landing zone too), the entity page's Delete, Save and
// key notebook, the Runs tab and a run's detail, a run that needs a decision, a run in Fabric followed to
// its end, Environments (workspace, new workspace, where tables live and where runs run), Surrogate keys,
// Source systems (with and without the platform's list), Table health and approved exceptions.
// The engine's calls are mocked (see harness.cjs for how to run it); each case drives the page as a person
// would and checks what they see and exactly what the app asked the engine for.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();

const layer = (name, entities, extra = {}) => ({ name, lakehouse: `LH_${name[0].toUpperCase()}${name.slice(1)}`, entities, drift: "evolve",
  required_checks: ["key_unique"], reads_from: [], description: "", retention_days: 7, ...extra });
const entityRow = (name, l) => ({ name, layer: l, source: "landing/customers/*.csv", owner: "ana@contoso.com", checks: 1, clean: 0,
  derive: 0, custom: [], mode: "full", watermark: "" });
// meta_service.project: the summary the Data mesh page reads.
const PROJECT = {
  path: "C:/work/sales", name: "Sales", version: "7",
  templates: { files: "From files", landing: "From the landing zone", custom: "Custom code", fact: "Fact" },
  layers: [layer("bronze", 1, { description: "Raw files" }), layer("silver", 1), layer("gold", 0)],
  entities: [entityRow("bronze.customers", "bronze"), entityRow("silver.customers", "silver")],
  products: [], links: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
  exception_notices: [
    { entity: "silver.customers", rule: "owner", owner: "ana@contoso.com", reason: "Legacy feed", approver: "cdo@contoso.com",
      expires: "2026-11-25", status: "expiring", notice: "silver.customers's Needs an owner exception ends on 2026-11-25" },
    { entity: "gold.sales", rule: "key_unique", owner: "", reason: "", approver: "", expires: "permanent", status: "permanent" },
    { entity: "bronze.legacy", rule: "custom_rule", owner: "raj@contoso.com", reason: "Old", approver: "cdo@contoso.com",
      expires: "2026-01-01", status: "expired", notice: "bronze.legacy's exception ended on 2026-01-01" },
  ],
  environments: {
    dev: { target: "local" },
    test: { target: "onelake", workspace_id: "w-test", workspace_name: "Sales Test", compute: "local" },
    prod: { target: "onelake", workspace_id: "w-prod", workspace_name: "Sales Prod", compute: "fabric" },
  },
  project: { name: "Sales", domain: "sales" },
};
const SETUP = { path: null, default_path: "C:/Users/chris/Documents/metadata",
  presets: { 2: [{ name: "bronze" }, { name: "gold" }], 3: [{ name: "bronze" }, { name: "silver" }, { name: "gold" }] } };
// meta_service.entity, as entity-editor.test.cjs has it, with surrogate keys and their MERGE.
const ENTITY = {
  name: "silver.customers", layer: "silver", mode: "merge", watermark: "updated_at", owner: "ana@contoso.com",
  path: "metadata/entities/silver.customers.toml", text: "[entity]\nname = \"silver.customers\"\n",
  errors: [], columns: { id: "bigint", customer_sk: "int" }, keys: ["id"], derived: [], hook_adds: [],
  surrogate_keys: [{ column: "customer_sk", table: "customer", business_key: ["id"] }], keys_sql: "MERGE INTO sk.customer AS t USING ...",
  runs: [], custom: [], checks: 2, reads: ["bronze.customers"], impact: [], source: null,
};
const keyTable = (name, extra = {}) => ({ name, table: `sk.${name}`, sk: `${name}_sk`, bk: `${name}_bk`, created: true, keys: 1200, next: 1201,
  used_by: [], description: "", owner: "", columns: [{ name: `${name}_sk`, type: "INT", rule: "Surrogate key" }], ...extra });
// meta_service.keys: Surrogate keys and Source systems both read it.
const KEYS = {
  env: "dev", lakehouse: "LH_Keys", local: false, workspace_id: null, workspace_name: null, errors: [], problem: null,
  key_tables: [keyTable("customer", { used_by: ["silver.customers"], description: "A customer", owner: "ana" }), keyTable("product", { created: false })],
  source_systems: [{ id: 1, code: "CRM", name: "Dynamics", system_type: "SQL Server", owner: "", description: "", active: true,
    connections: { dev: { server_host: "crm-dev" }, test: { server_host: "" }, prod: { server_host: "" } } }],
  connection_fields: ["server_host"], system_types: ["SQL Server", "SAP"], platform_sources: null, source_problems: [],
};
// The same, with the platform's list of sources (Preview): CRM comes from it, LEG is only this project's.
const PLATFORM_KEYS = {
  ...KEYS,
  source_systems: [
    { ...KEYS.source_systems[0], platform: { name: "crm" } },
    { id: 5, code: "LEG", name: "Legacy", system_type: "", owner: "", description: "", active: false,
      connections: { dev: { server_host: "" }, test: { server_host: "" }, prod: { server_host: "" } } },
  ],
  platform_sources: [
    { name: "crm", code: "CRM", title: "Dynamics", read: true, source_id: 1 },
    { name: "sap", code: "SAP", title: "SAP ERP", read: false, source_id: 3, system_type: "SAP", owner: "ops@contoso.com" },
  ],
};
// meta_service.health: each table's Delta log.
const HEALTH = {
  local: false, optimize_after_run: false,
  tables: [
    { entity: "silver.customers", layer: "silver", exists: true, files: 40, bytes: 1048576, avg_file_bytes: 26214, v_ordered: false,
      versions_since_vacuum: 12, last_optimize: null, advice: ["40 small files: compact them"], status: "needs_maintenance" },
    { entity: "bronze.customers", layer: "bronze", exists: false, message: null, status: "missing" },
  ],
  maintenance: [{ status: "Completed", table: "silver.customers", started_at: ago(600), message: "Optimized" }],
};
const RUNS = [{ run_id: "r1", entity: "silver.customers", status: "failed", message: "Key isn't unique", started_at: ago(60), rows_read: 10,
  rows_written: 0, quarantined: 2, seconds: 3, metadata: "7", backfill: "none" }];
const RUN_DETAIL = { run_id: "r1", entity: "silver.customers", status: "failed", started_at: ago(60), metadata: "7", seconds: 3, attempt: 2,
  job_id: "j7", message: "Key isn't unique", steps: [{ name: "Read", rows: 10, seconds: 1, detail: "10 rows" }, { name: "Checks", rows: 10, seconds: 1, detail: "1 failed" }],
  dq: [{ check: "key_unique", failed: 2, action: "stop" }, { check: "not_null(id)", failed: 0, action: "warn" }] };
const WORKSPACES = [{ id: "w-test", displayName: "Sales Test" }, { id: "w-prod", displayName: "Sales Prod" }, { id: "w-ops", displayName: "Ops" }];

let db;
const fail = {};
const fresh = () => ({ project: structuredClone(PROJECT), setup: false, keys: structuredClone(KEYS), health: structuredClone(HEALTH),
  runQueue: [], fabric: [], optimize: [], lookup: null, capacities: [{ id: "cap-1", displayName: "F64 West", sku: "F64", state: "Active" },
    { id: "cap-2", displayName: "Trial", state: "Paused" }] });
// A refusal the test sets with fail[method] = message.
const guard = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };
const project = () => structuredClone(db.project);

const mocks = {
  meta_project: () => (db.setup ? structuredClone(SETUP) : project()),
  meta_init: guard("meta_init", () => { db.setup = false; return project(); }),
  meta_open: guard("meta_open", () => { db.setup = false; return project(); }),
  meta_runs: () => structuredClone(RUNS),
  meta_runs_all: () => ({ runs: [] }),
  meta_run_detail: () => structuredClone(RUN_DETAIL),
  meta_entity: () => structuredClone(ENTITY),
  meta_entity_shortcuts: () => [],
  meta_save_entity: guard("meta_save_entity", ({ name }) => ({ name, errors: ["Needs an owner"] })),
  meta_delete_entity: guard("meta_delete_entity", () => ({ deleted: true })),
  meta_key_notebook: guard("meta_key_notebook", () => ({ name: "keys_silver_customers" })),
  meta_set_layers: guard("meta_set_layers", ({ layers }) => { db.project.layers = layers.map((l) => ({ ...l, lakehouse: "LH_X", entities: 0 })); return project(); }),
  meta_new_entity: guard("meta_new_entity", ({ name }) => ({ name })),
  platform_landing: () => ({ sources: [{ name: "sap", title: "SAP ERP", entities: [{ name: "customer", files: 2 }, { name: "material", files: 1 }] },
    { name: "empty", title: "empty", entities: [{ name: "", files: 0 }] }] }),
  meta_run: guard("meta_run", ({ names }) => db.runQueue.shift() || (names || ["bronze.customers"]).map((n) => ({ entity: n, status: "ok", rows_written: 5, run_id: "r2" }))),
  meta_fabric_result: guard("meta_fabric_result", () => db.fabric.shift() || { status: "InProgress" }),
  meta_set_environment: guard("meta_set_environment", () => project()),
  meta_set_compute: guard("meta_set_compute", () => project()),
  meta_create_workspace: guard("meta_create_workspace", ({ name }) => ({ ...project(), created: { displayName: name } })),
  list_capacities: () => structuredClone(db.capacities),
  list_workspaces: () => structuredClone(WORKSPACES),
  meta_promote_check: () => ({ errors: [], version: "7", notes: [] }),
  meta_keys: () => structuredClone(db.keys),
  meta_varlib: () => ({ library: "VL_Sources", published: {}, environments: { dev: {}, test: {}, prod: {} }, variables: [] }),
  meta_key_lookup: () => db.lookup,
  meta_delete_key_table: guard("meta_delete_key_table", () => ({ deleted: true })),
  meta_keys_sync: guard("meta_keys_sync", () => ({ created: ["product"], config: { source_system: 2 }, missing: [] })),
  meta_new_key_table: guard("meta_new_key_table", ({ name }) => ({ name })),
  meta_set_keys_workspace: guard("meta_set_keys_workspace", () => ({ ok: true })),
  meta_save_source_system: guard("meta_save_source_system", ({ system }) => system),
  meta_delete_source_system: guard("meta_delete_source_system", () => ({ deleted: true })),
  meta_source_read: guard("meta_source_read", () => ({ ok: true })),
  meta_sources_move: guard("meta_sources_move", () => ({ moved: [], conflicts: ["LEG: id 5 is already the platform's HR source"] })),
  meta_health: () => structuredClone(db.health),
  meta_optimize: guard("meta_optimize", ({ entities }) => db.optimize.shift() || (entities || ["a", "b"]).map(() => ({ status: "NotStarted" }))),
  meta_set_maintenance: guard("meta_set_maintenance", (p) => p),
};

before(async () => {
  if (skip) return;
  db = fresh();
  app = await openApp({ mocks });
  await startupReads(app);
  await page().evaluate((me) => { state.user = me; }, ME);
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => {
    closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    Object.assign(state.meta, { env: "dev", layersDraft: null, keyTable: null, source: null, sourceEnv: "dev", sourceDraft: null, project: null });
    state.workspaces = null; go("home");
  });
});

const dialog = () => page().locator("#dialog-root .dialog");
const actions = () => dialog().locator(".dialog-actions");
const callsFrom = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
async function waitCall(from, method, n = 1) {
  for (const end = Date.now() + 8000; callsFrom(from, method).length < n; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error(`${method} was never called`);
  }
  return callsFrom(from, method);
}
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source);
async function openTab(tab, env = "dev") {
  await page().evaluate(([t, e]) => { state.meta.env = e; state.meta.tab = t; go("metadata"); }, [tab, env]);
}

// ---------- setting up a project ----------

test("with no project, Set up creates one with the layers picked, or opens a folder", { skip }, async () => {
  db.setup = true;
  await openTab("entities");
  await page().getByRole("heading", { name: "Set up a metadata project" }).waitFor();
  assert.strictEqual(await page().locator("#meta-path").inputValue(), "C:/Users/chris/Documents/metadata");
  await page().locator('[data-preset="2"]').click();
  assert.ok(await page().locator("#meta-sample").isDisabled(), "the sample tables need three layers");
  assert.match(await page().locator('[data-preset="2"]').getAttribute("class"), /selected/);
  await page().locator("#meta-path").fill("  D:/mesh  ");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Create project" }).click();
  await toastSays(/Project created/);
  assert.deepStrictEqual(callsFrom(from, "meta_init"), [{ path: "D:/mesh", preset: "2", sample: false }]);
  await page().locator(".meta-project-groups").waitFor();

  db.setup = true;
  await openTab("entities");
  await page().locator("#meta-open-path").fill("E:/existing");
  fail.meta_open = "FileNotFoundError: E:/existing has no layers.toml";
  await page().getByRole("button", { name: "Open", exact: true }).click();
  await toastSays(/layers\.toml/);
  assert.ok(await page().getByRole("heading", { name: "Set up a metadata project" }).isVisible(), "a refusal keeps the setup page");
  delete fail.meta_open;
  await page().getByRole("button", { name: "Open", exact: true }).click();
  await page().locator(".meta-project-groups").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_open").at(-1), { path: "E:/existing" });
  assert.deepStrictEqual(app.errors, []);
});

// ---------- layers ----------

test("Layers edits a draft: reorder, policy, remove, add, discard and save", { skip }, async () => {
  await openTab("layers");
  const rows = page().locator(".meta-body table.list tbody tr");
  await rows.first().waitFor();
  assert.strictEqual(await rows.count(), 3);
  const save = page().getByRole("button", { name: "Save layers" });
  assert.ok(await save.isDisabled(), "nothing changed yet");
  assert.match(await rows.first().textContent(), /bronze.*Raw files.*LH_Bronze.*Files and sources/);
  assert.ok(await rows.first().getByRole("button", { name: "Remove" }).isDisabled(), "a layer with entities can't be removed");

  await rows.first().getByTitle("Move down").click();
  assert.deepStrictEqual(await page().locator(".meta-body .flow .layer-chip").allTextContents(), ["silver", "bronze", "gold"]);
  await page().getByRole("button", { name: "Discard changes" }).click();
  assert.deepStrictEqual(await page().locator(".meta-body .flow .layer-chip").allTextContents(), ["bronze", "silver", "gold"]);
  assert.ok(await save.isDisabled());

  await rows.nth(1).getByLabel("Schema drift").selectOption("strict");
  await rows.nth(1).getByLabel("Can read from").selectOption("bronze");
  await rows.nth(1).locator("label", { hasText: "Key not null" }).locator("input").check();
  await rows.nth(1).locator("label", { hasText: "Key unique" }).locator("input").uncheck();
  await rows.nth(1).locator("label", { hasText: "Required" }).locator("input").check();
  await rows.nth(1).getByLabel("Days of history").fill("30");
  await rows.nth(1).getByLabel("Days of history").dispatchEvent("change");
  await rows.nth(2).getByRole("button", { name: "Remove" }).click();
  assert.strictEqual(await rows.count(), 2);

  await page().getByRole("button", { name: "Add layer" }).click();
  await dialog().getByText("Add a layer").waitFor();
  await dialog().locator("input").fill("Platinum");
  await actions().getByRole("button", { name: "Add" }).click();
  await rows.nth(2).waitFor();
  assert.match(await rows.nth(2).textContent(), /platinum.*LH_Platinum/);

  const from = app.calls.length;
  await save.click();
  await toastSays(/Layers saved/);
  const [{ layers }] = callsFrom(from, "meta_set_layers");
  assert.deepStrictEqual(layers.map((l) => l.name), ["bronze", "silver", "platinum"]);
  assert.deepStrictEqual(layers[1], { name: "silver", drift: "strict", required_checks: ["key_not_null"], reads_from: ["bronze"],
    description: "", require_keys: true, retention_days: 30, workspace: "" });
  assert.deepStrictEqual(layers[2], { name: "platinum", drift: "strict", required_checks: ["key_unique"], reads_from: [],
    description: "", require_keys: false, retention_days: 7, workspace: "" });
  assert.deepStrictEqual(app.errors, []);
});

test("Layers refuses to remove the last layer, and a refused save keeps the draft", { skip }, async () => {
  db.project.layers = [layer("bronze", 0)];
  db.project.entities = [];
  await openTab("layers");
  const rows = page().locator(".meta-body table.list tbody tr");
  await rows.first().getByRole("button", { name: "Remove" }).click();
  await toastSays(/A project needs at least one layer/);
  assert.strictEqual(await rows.count(), 1);
  await rows.first().getByLabel("Schema drift").selectOption("ignore_new");
  fail.meta_set_layers = "Blocked by the save gate:\n- silver.orders would read a later layer";
  await page().getByRole("button", { name: "Save layers" }).click();
  await dialog().getByText("Layers not saved").waitFor();
  assert.deepStrictEqual(await dialog().locator(".gate-list li").allTextContents(), ["silver.orders would read a later layer"]);
  await actions().getByRole("button", { name: "Close" }).click();
  assert.ok(await page().getByRole("button", { name: "Discard changes" }).isVisible(), "the draft is kept");
});

// ---------- new entity ----------

test("New entity from the landing zone fills the path and name from what landed, and Create sends them", { skip }, async () => {
  await openTab("entities");
  await page().locator(".page-head").getByRole("button", { name: "New entity" }).click();
  await dialog().locator("#ne-template").selectOption("landing");
  await dialog().getByLabel("Landing zone source").locator("option").first().waitFor({ state: "attached" });
  assert.deepStrictEqual(await dialog().getByLabel("Landing zone source").locator("option").allTextContents(), ["sap (SAP ERP)"],
    "a source with nothing landed isn't offered");
  assert.strictEqual(await dialog().locator("#ne-source").inputValue(), "sap/customer/*.csv");
  assert.strictEqual(await dialog().locator("#ne-table").inputValue(), "sap_customer");
  await dialog().getByLabel("Landing zone entity").selectOption("material");
  assert.strictEqual(await dialog().locator("#ne-source").inputValue(), "sap/material/*.csv");
  assert.strictEqual(await dialog().locator("#ne-layer").inputValue(), "bronze");
  assert.match(await dialog().textContent(), /Reads the raw files your central team lands/);
  await dialog().locator("#ne-keys").fill("id, code,");
  const from = app.calls.length;
  await actions().getByRole("button", { name: "Create" }).click();
  await toastSays(/Created bronze\.sap_material/);
  assert.deepStrictEqual(callsFrom(from, "meta_new_entity"), [{ template: "landing", name: "bronze.sap_material", source: "sap/material/*.csv", keys: ["id", "code"] }]);
  await page().waitForFunction(() => state.view === "entity");
  assert.deepStrictEqual(app.errors, []);
});

test("New entity asks for a name, and a save-gate refusal lists what to fix", { skip }, async () => {
  await openTab("entities");
  await page().locator(".page-head").getByRole("button", { name: "New entity" }).click();
  await dialog().locator("#ne-template").selectOption("custom");
  assert.match(await dialog().textContent(), /Creates transforms\/<table>\.py and a test for it/);
  await dialog().locator("#ne-template").selectOption("fact");
  assert.strictEqual(await dialog().locator("#ne-table").getAttribute("placeholder"), "fact_sales");
  const from = app.calls.length;
  await actions().getByRole("button", { name: "Create" }).click();
  await toastSays(/Give the table a name/);
  assert.deepStrictEqual(callsFrom(from, "meta_new_entity"), []);
  await dialog().locator("#ne-table").fill("fact_orders");
  await dialog().locator("#ne-layer").selectOption("gold");
  fail.meta_new_entity = "Blocked by the save gate:\n- Needs an owner\n- Key must be unique";
  await actions().getByRole("button", { name: "Create" }).click();
  await dialog().getByText("Couldn't create the entity").waitFor();
  assert.deepStrictEqual(await dialog().locator(".gate-list li").allTextContents(), ["Needs an owner", "Key must be unique"]);
  assert.strictEqual(callsFrom(from, "meta_new_entity")[0].name, "gold.fact_orders");
});

// ---------- the entity page ----------

test("the entity page's Delete asks in a danger confirm; Save and the key notebook send the entity", { skip }, async () => {
  await page().evaluate(() => openEntity("silver.customers"));
  await page().locator("#entity-toml").waitFor();
  const from = app.calls.length;
  await page().locator(".page-head").getByRole("button", { name: "Delete" }).click();
  await dialog().getByText("Delete silver.customers?").waitFor();
  assert.strictEqual(await page().evaluate(() => document.activeElement.textContent.trim()), "Cancel", "Cancel has focus");
  await actions().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(callsFrom(from, "meta_delete_entity"), []);

  await page().locator("#entity-toml").fill("[entity]\nname = \"silver.customers\"\nowner = \"\"\n");
  await page().locator(".card", { hasText: "Config" }).getByRole("button", { name: "Save" }).click();
  await toastSays(/Saved with 1 problem to fix/);
  assert.deepStrictEqual(callsFrom(from, "meta_save_entity"), [{ name: "silver.customers", text: "[entity]\nname = \"silver.customers\"\nowner = \"\"\n" }]);

  await page().locator("#entity-toml").waitFor();
  const keys = page().locator(".card", { hasText: "Cell 1: surrogate keys" });
  assert.match(await keys.textContent(), /customer_sk from sk\.customer on id.*MERGE INTO sk\.customer/);
  await keys.getByRole("button", { name: "Create template notebook" }).click();
  await waitCall(from, "open_notebook");
  assert.deepStrictEqual(callsFrom(from, "meta_key_notebook"), [{ name: "silver.customers", env: "dev" }]);
  assert.deepStrictEqual(callsFrom(from, "open_notebook"), [{ name: "keys_silver_customers" }]);

  await page().evaluate(() => openEntity("silver.customers"));
  await page().locator(".page-head").getByRole("button", { name: "Delete" }).click();
  await actions().getByRole("button", { name: "Delete" }).click();
  await toastSays(/Deleted silver\.customers/);
  assert.deepStrictEqual(callsFrom(from, "meta_delete_entity"), [{ name: "silver.customers" }]);
  await page().waitForFunction(() => state.view === "metadata" && state.meta.tab === "entities");
});

// ---------- runs ----------

test("Runs lists the runs and a row opens its steps and checks", { skip }, async () => {
  await openTab("runs");
  const row = page().locator(".meta-body table.list tbody tr").first();
  await row.waitFor();
  assert.match(await row.textContent(), /Failed.*silver\.customers.*backfill: none.*Key isn't unique.*This PC/);
  const from = app.calls.length;
  await row.click();
  await dialog().getByText("Run r1").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_run_detail"), [{ run_id: "r1", env: "dev" }]);
  const text = await dialog().textContent();
  assert.match(text, /job j7 · try 2 · metadata 7 · 3s/);
  assert.match(text, /Read.*10 rows/);
  assert.match(text, /key_unique2 failedstop/);
  assert.match(text, /not_null\(id\)passedwarn/);
  assert.strictEqual(await dialog().locator(".pill-s.fail", { hasText: "2 failed" }).count(), 1, "a stopping check is red");
  assert.deepStrictEqual(app.errors, []);
});

test("a table that needs a decision offers each way on, and each one runs it that way", { skip }, async () => {
  const decision = () => [{ entity: "silver.customers", status: "needs_decision", options: ["none", "full", "migrate"], message: "The derive logic changed" },
    { entity: "bronze.customers", status: "ok", rows_written: 5, quarantined: 1, maintenance: "queued", run_id: "r2" }];
  await openTab("entities");
  const from = app.calls.length;
  for (const [choice, want] of [["Apply to new rows only", { backfill: "none", cascade: false }],
    ["Rebuild it and everything downstream", { backfill: null, cascade: true }], ["Migrate to Type 2", { backfill: "migrate", cascade: false }]]) {
    db.runQueue.push(decision());
    await page().locator(".page-head").getByRole("button", { name: "Run all" }).click();
    await dialog().getByText("One table needs a decision").waitFor();
    assert.match(await dialog().textContent(), /5 rows written, 1 quarantined.*Optimize queued/);
    assert.match(await dialog().textContent(), /silver\.customers needs a decision.*The derive logic changed/);
    await dialog().getByRole("button", { name: new RegExp(choice) }).click();
    await dialog().getByText("1 of 1 succeeded").waitFor();
    assert.deepStrictEqual(callsFrom(from, "meta_run").at(-1), { names: ["silver.customers"], env: "dev", ...want });
    await actions().getByRole("button", { name: "Close" }).click();
  }
  assert.deepStrictEqual(callsFrom(from, "meta_run").filter((p) => p.names === null).length, 3, "Run all sends no names");
  assert.deepStrictEqual(app.errors, []);
});

// Fabric runs are followed every 10 s or more; the page's long waits are made immediate for these cases.
async function fastWaits() {
  await page().evaluate(() => {
    window.__slowTimeout = window.setTimeout;
    window.setTimeout = (fn, ms, ...a) => window.__slowTimeout(fn, ms >= 10000 ? 0 : ms, ...a);
  });
}
async function slowWaits() {
  await page().evaluate(() => { if (window.__slowTimeout) window.setTimeout = window.__slowTimeout; });
}

test("a run in Fabric is followed until it ends, then shows its results; a failed one says so", { skip }, async () => {
  db.project.environments.prod.compute = "fabric";
  await openTab("entities", "prod");
  await fastWaits();
  try {
    db.runQueue.push([{ status: "submitted", job_id: "j1", request_id: "q1", env: "prod" }]);
    db.fabric.push({ status: "InProgress" }, { status: "Completed", results: [{ entity: "silver.customers", status: "ok", rows_written: 3, run_id: "r5" }] });
    const from = app.calls.length;
    await page().locator(".page-head").getByRole("button", { name: "Run all in Fabric" }).click();
    await dialog().getByText("1 of 1 succeeded").waitFor();
    assert.deepStrictEqual(callsFrom(from, "meta_fabric_result"), [{ request_id: "q1" }, { request_id: "q1" }]);
    const item = await page().evaluate(() => activity.items.find((i) => i.id === "fabric:j1"));
    assert.strictEqual(item.status, "ok");
    assert.match(item.text, /1 of 1 succeeded in Fabric/);
    await actions().getByRole("button", { name: "Close" }).click();

    db.runQueue.push([{ status: "submitted", job_id: "j2", request_id: "q2", env: "prod" }]);
    db.fabric.push({ status: "Failed", message: "The runner notebook crashed" });
    await page().locator(".page-head").getByRole("button", { name: "Run all in Fabric" }).click();
    await dialog().getByText("The Fabric run failed").waitFor();
    assert.match(await dialog().textContent(), /The runner notebook crashed.*Open it from Monitor/);
    assert.strictEqual(await page().evaluate(() => activity.items.find((i) => i.id === "fabric:j2").status), "failed");
    await actions().getByRole("button", { name: "Close" }).click();

    db.runQueue.push([{ status: "submitted", job_id: "j3", request_id: "q3", env: "prod" }]);
    fail.meta_fabric_result = "ConnectionError: The engine lost the connection";
    await page().locator(".page-head").getByRole("button", { name: "Run all in Fabric" }).click();
    await page().waitForFunction(() => activity.items.find((i) => i.id === "fabric:j3")?.status === "info");
    assert.match(await page().evaluate(() => activity.items.find((i) => i.id === "fabric:j3").text), /stopped following this run/);
  } finally {
    await slowWaits();
  }
  assert.deepStrictEqual(app.errors, []);
});

// ---------- environments ----------

test("Change picks another workspace for an environment and sends it", { skip }, async () => {
  await openTab("environments");
  const testCard = page().locator(".env-card", { hasText: "Sales Test" });
  await testCard.getByRole("button", { name: "Change" }).click();
  await dialog().getByText("Workspace for Test").waitFor();
  const prod = dialog().getByRole("button", { name: /Sales Prod/ });
  assert.match(await prod.textContent(), /Also used by Prod/);
  assert.match(await dialog().getByRole("button", { name: /Sales Test/ }).textContent(), /Current/);
  const from = app.calls.length;
  await dialog().getByRole("button", { name: /Ops/ }).click();
  await toastSays(/Test now uses Ops\./);
  assert.deepStrictEqual(callsFrom(from, "meta_set_environment"), [{ env: "test", workspace_id: "w-ops", workspace_name: "Ops" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Create a new workspace suggests a name, lists capacities and sends the one picked", { skip }, async () => {
  await openTab("environments");
  await page().locator(".env-card").first().getByRole("button", { name: "Assign workspace" }).click();
  await dialog().getByRole("button", { name: /Create a new workspace/ }).click();
  await dialog().getByText("New workspace for Dev").waitFor();
  assert.strictEqual(await dialog().locator("input").inputValue(), "sales - Dev");
  const capacity = dialog().locator("select");
  await capacity.locator("option", { hasText: "F64 West" }).waitFor({ state: "attached" });
  assert.deepStrictEqual(await capacity.locator("option").allTextContents(), ["F64 West (F64)", "Trial - paused", "No capacity (assign one in Fabric later)"]);

  // Back returns to the list; an empty name isn't sent.
  await actions().getByRole("button", { name: "Back" }).click();
  await dialog().getByText("Workspace for Dev").waitFor();
  await dialog().getByRole("button", { name: /Create a new workspace/ }).click();
  await dialog().locator("select option", { hasText: "F64 West" }).waitFor({ state: "attached" });
  await dialog().locator("input").fill("  ");
  const from = app.calls.length;
  await actions().getByRole("button", { name: /Create workspace/ }).click();
  assert.deepStrictEqual(callsFrom(from, "meta_create_workspace"), []);
  await dialog().locator("input").fill("Sales Dev");
  await dialog().locator("select").selectOption("cap-1");
  await actions().getByRole("button", { name: /Create workspace/ }).click();
  await toastSays(/Created Sales Dev and assigned it to Dev\. Its lakehouses are created on the first run\./);
  assert.deepStrictEqual(callsFrom(from, "meta_create_workspace"), [{ env: "dev", name: "Sales Dev", capacity_id: "cap-1" }]);

  db.capacities = [];
  await page().locator(".env-card").first().getByRole("button", { name: "Assign workspace" }).click();
  await dialog().getByRole("button", { name: /Create a new workspace/ }).click();
  await dialog().getByText("No Fabric capacity found for your account").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Tables in and Runs on send where an environment's tables live and where it runs", { skip }, async () => {
  await openTab("environments");
  const testCard = page().locator(".env-card", { hasText: "Sales Test" });
  await testCard.waitFor();
  const devCard = page().locator(".env-card").first();
  assert.ok(await devCard.getByRole("button", { name: "Workspace", exact: true }).isDisabled(), "Dev has no workspace to keep tables in");
  assert.ok(await devCard.getByRole("button", { name: /^Fabric/ }).isDisabled(), "tables on this PC can't run in Fabric");
  const from = app.calls.length;
  await testCard.getByRole("button", { name: /^Fabric/ }).click();
  await toastSays(/Test now runs in Fabric\. The first run uploads the project/);
  assert.deepStrictEqual(callsFrom(from, "meta_set_compute"), [{ env: "test", compute: "fabric" }]);
  await page().locator(".env-card", { hasText: "Sales Prod" }).getByRole("button", { name: "This PC", exact: true }).click();
  await toastSays(/Prod now runs on this PC\./);
  assert.deepStrictEqual(callsFrom(from, "meta_set_compute").at(-1), { env: "prod", compute: "local" });
  await page().locator(".env-card", { hasText: "Sales Test" }).getByRole("button", { name: "This PC only" }).click();
  await toastSays(/Test now keeps its tables on this PC only\./);
  assert.deepStrictEqual(callsFrom(from, "meta_set_environment"), [{ env: "test", target: "local" }]);

  db.project.environments.dev = { target: "local", workspace_id: "w-ops", workspace_name: "Ops" };
  await openTab("environments");
  await page().locator(".env-card").first().getByText(/Offline: tables stay in the project's \.local folder/).waitFor();
  await page().locator(".env-card").first().getByRole("button", { name: "Workspace", exact: true }).click();
  await toastSays(/Dev now reads and writes its workspace's lakehouses\./);
  assert.deepStrictEqual(callsFrom(from, "meta_set_environment").at(-1), { env: "dev", target: "onelake" });
  assert.deepStrictEqual(app.errors, []);
});

// ---------- surrogate keys ----------

test("Surrogate keys: pick a table, look up a key, delete an unused table, sync and pick the Keys workspace", { skip }, async () => {
  await openTab("keys");
  const detail = page().locator(".side-panel");
  await detail.waitFor();
  assert.match(await page().locator(".keys-head").textContent(), /LH_Keys in Dev.*No Keys workspace yet/);
  assert.match(await detail.textContent(), /sk\.customer.*A customer.*owner ana/);
  assert.ok(await detail.getByRole("button", { name: "Delete" }).isDisabled(), "a key table in use can't be deleted");
  const rows = await page().locator(".keys-grid table.list tbody tr").allTextContents();
  assert.match(rows[0], /sk\.customer.*1,200.*1,201.*1 table/);
  assert.match(rows[1], /sk\.product.*not created.*none/);

  const from = app.calls.length;
  db.lookup = { bk: "ABC", sk: 17, create_date: "2026-10-01T09:00:00Z" };
  await detail.getByLabel("Business key").fill("ABC");
  await detail.getByLabel("Business key").press("Enter");
  await detail.getByText("is 17", { exact: false }).waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_key_lookup"), [{ table: "customer", value: "ABC", env: "dev" }]);
  db.lookup = null;
  await detail.getByLabel("Business key").fill("ZZZ");
  await detail.getByRole("button", { name: "Find" }).click();
  await detail.getByText("No customer_bk ZZZ yet.", { exact: false }).waitFor();

  await page().locator(".keys-grid tbody tr", { hasText: "sk.product" }).click();
  await page().locator(".side-panel h2", { hasText: "sk.product" }).waitFor();
  await page().locator(".side-panel").getByRole("button", { name: "Delete" }).click();
  await dialog().getByText("Delete key table product?").waitFor();
  await actions().getByRole("button", { name: "Delete" }).click();
  await toastSays(/Deleted product/);
  assert.deepStrictEqual(callsFrom(from, "meta_delete_key_table"), [{ name: "product", env: "dev" }]);

  await page().getByRole("button", { name: /Sync to LH_Keys/ }).click();
  await toastSays(/Synced: 2 source systems, created sk\.product/);
  assert.deepStrictEqual(callsFrom(from, "meta_keys_sync"), [{ env: "dev" }]);

  await page().getByRole("button", { name: "Pick Keys workspace" }).click();
  await dialog().getByText("Keys workspace for Dev").waitFor();
  await dialog().getByRole("button", { name: /Ops/ }).click();
  assert.deepStrictEqual(await waitCall(from, "meta_set_keys_workspace"), [{ env: "dev", workspace_id: "w-ops", workspace_name: "Ops" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("New key table names what it creates; Use a key table offers only the platform's unused ones", { skip }, async () => {
  db.keys.key_tables = [];
  await openTab("keys");
  await page().getByText("No key tables yet").waitFor();
  await page().locator(".keys-head").getByRole("button", { name: "New key table" }).click();
  await dialog().getByText("Creates sk.customer in LH_Keys").waitFor();
  const from = app.calls.length;
  await actions().getByRole("button", { name: "Create" }).click();
  await toastSays(/Give the key table a name/);
  assert.deepStrictEqual(callsFrom(from, "meta_new_key_table"), []);
  await dialog().getByPlaceholder("customer", { exact: true }).fill("Store");
  assert.match(await dialog().textContent(), /Creates sk\.store in LH_Keys with store_sk \(INT\), store_bk \(unique\) and create_date\./);
  await dialog().getByPlaceholder("A customer, from any source").fill("A shop");
  await actions().getByRole("button", { name: "Create" }).click();
  await toastSays(/Created sk\.store/);
  assert.deepStrictEqual(callsFrom(from, "meta_new_key_table"), [{ name: "Store", description: "A shop", env: "dev" }]);

  db.keys.key_tables = [keyTable("customer")];
  db.keys.central_key_tables = [{ name: "customer" }, { name: "supplier", description: "A supplier" }];
  await openTab("keys");
  await page().locator(".keys-head").getByRole("button", { name: "Use a key table" }).click();
  assert.deepStrictEqual(await dialog().locator("select option").allTextContents(), ["sk.supplier: A supplier"]);
  await actions().getByRole("button", { name: "Use it" }).click();
  await toastSays(/This project now uses sk\.supplier/);
  assert.deepStrictEqual(callsFrom(from, "meta_new_key_table").at(-1), { name: "supplier", description: "A supplier", env: "dev" });

  db.keys.central_key_tables = [{ name: "customer" }];
  await openTab("keys");
  await page().locator(".keys-head").getByRole("button", { name: "Use a key table" }).click();
  await dialog().getByText("This project already uses every key table the platform has.").waitFor();
  assert.strictEqual(await actions().getByRole("button", { name: "Use it" }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

// ---------- source systems ----------

test("Source systems saves the edits with each environment's connection, and Delete asks first", { skip }, async () => {
  db.keys.platform_sources = null;
  await openTab("sources");
  const form = page().locator(".source-form");
  await form.waitFor();
  await form.getByLabel("Name").fill("Dynamics 365 Sales");
  await form.getByRole("group", { name: "Environment" }).getByRole("button", { name: "Test" }).click();
  await page().locator(".source-form").getByLabel("Server or host").fill("crm-test.contoso.com");
  await page().locator(".source-form").getByRole("group", { name: "Environment" }).getByRole("button", { name: "Dev" }).click();
  assert.strictEqual(await page().locator(".source-form").getByLabel("Name").inputValue(), "Dynamics 365 Sales", "edits survive switching environment");
  const from = app.calls.length;
  await page().locator(".source-form").getByRole("button", { name: "Save" }).click();
  await toastSays(/Saved CRM/);
  const [saved] = callsFrom(from, "meta_save_source_system");
  assert.strictEqual(saved.original_id, 1);
  assert.strictEqual(saved.env, "dev");
  assert.strictEqual(saved.system.name, "Dynamics 365 Sales");
  assert.deepStrictEqual(saved.system.connections.test, { server_host: "crm-test.contoso.com" });

  await page().getByRole("button", { name: "New source system" }).click();
  await page().locator(".source-form h2", { hasText: "New source system" }).waitFor();
  await page().locator(".source-form").getByRole("button", { name: "Save" }).click();
  await toastSays(/Give the source system a code, like CRM/);
  await page().locator(".source-form").getByLabel("Code").fill("ERP");
  await page().locator(".source-form").getByLabel("System type").selectOption("SAP");
  await page().locator(".source-form").getByRole("button", { name: "Save" }).click();
  await toastSays(/Saved ERP/);
  const created = callsFrom(from, "meta_save_source_system").at(-1);
  assert.strictEqual(created.original_id, null);
  assert.deepStrictEqual([created.system.id, created.system.code, created.system.system_type], [2, "ERP", "SAP"]);

  await page().evaluate(() => { state.meta.source = 1; state.meta.sourceDraft = null; });
  await openTab("sources");
  await page().locator(".source-form").getByRole("button", { name: "Delete" }).click();
  await dialog().getByText("Delete CRM?").waitFor();
  assert.strictEqual(await page().evaluate(() => document.activeElement.textContent.trim()), "Cancel");
  await actions().getByRole("button", { name: "Delete" }).click();
  await toastSays(/Deleted CRM/);
  assert.deepStrictEqual(callsFrom(from, "meta_delete_source_system"), [{ id: 1, env: "dev" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("with the platform's list, a project reads a source from it, and Move sources reports what it couldn't move", { skip }, async () => {
  db.keys = structuredClone(PLATFORM_KEYS);
  await openTab("sources");
  await page().getByText("From the platform").waitFor();
  assert.match(await page().locator(".card.pad", { hasText: "only in this project" }).first().textContent(), /LEG is only in this project/);
  const form = page().locator(".source-form");
  assert.ok(await form.getByLabel("Code").getAttribute("readonly") !== null, "a platform source's code is kept by the platform");
  assert.ok(await form.getByRole("button", { name: "Save connection" }).isVisible());

  await page().getByRole("button", { name: "Add a source this project reads" }).click();
  const rows = await dialog().locator("tbody tr").allTextContents();
  assert.strictEqual(rows.length, 1, "a source already read isn't offered");
  assert.match(rows[0], /SAP.*SAP ERP.*SAP.*ops@contoso\.com.*3/);
  const from = app.calls.length;
  await dialog().getByRole("button", { name: "Read" }).click();
  await toastSays(/This project now reads SAP/);
  assert.deepStrictEqual(callsFrom(from, "meta_source_read"), [{ name: "sap", env: "dev" }]);

  await page().getByRole("button", { name: "Move sources to the platform" }).click();
  await dialog().getByText("Move sources to the platform?").waitFor();
  assert.match(await dialog().textContent(), /Copies LEG \(id 5\) to the platform's list/);
  await actions().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(callsFrom(from, "meta_sources_move"), []);
  await page().getByRole("button", { name: "Move sources to the platform" }).click();
  await actions().getByRole("button", { name: "Move" }).click();
  await dialog().getByText("Some sources weren't moved").waitFor();
  assert.match(await dialog().textContent(), /LEG: id 5 is already the platform's HR source.*nothing was renumbered/);
  assert.deepStrictEqual(callsFrom(from, "meta_sources_move"), [{ env: "dev" }]);
  await actions().getByRole("button", { name: "Close" }).click();

  await page().locator(".source-form").getByRole("button", { name: "Stop reading" }).click();
  await dialog().getByText("Stop reading CRM?").waitFor();
  assert.match(await dialog().textContent(), /the platform keeps the source/);
  await actions().getByRole("button", { name: "Stop reading" }).click();
  assert.deepStrictEqual(await waitCall(from, "meta_delete_source_system"), [{ id: 1, env: "dev" }]);
  assert.deepStrictEqual(app.errors, []);
});

// ---------- table health ----------

test("Table health shows what needs maintenance and Optimize sends one table or all", { skip }, async () => {
  await openTab("health", "test");
  const rows = page().locator(".meta-body table.list tbody tr");
  await rows.first().waitFor();
  assert.match(await page().locator(".meta-body .card.pad").first().textContent(), /Test tables in OneLake.*1 of 2 would benefit from maintenance/);
  assert.match(await rows.first().textContent(), /silver\.customers.*40.*No.*12.*never.*40 small files: compact them/);
  assert.match(await rows.nth(1).textContent(), /Not written in this environment yet/);
  assert.match(await page().locator(".card", { hasText: "Recent maintenance jobs" }).textContent(), /Succeeded.*silver\.customers.*Optimized/);

  const from = app.calls.length;
  await rows.first().getByRole("button", { name: "Optimize" }).click();
  await toastSays(/Queued 1 maintenance job in Fabric/);
  assert.deepStrictEqual(callsFrom(from, "meta_optimize"), [{ env: "test", entities: ["silver.customers"] }]);

  db.optimize.push([{ status: "Failed", message: "The capacity is paused" }, { status: "NotStarted" }]);
  await page().getByRole("button", { name: "Optimize all in Fabric" }).click();
  await toastSays(/1 of 2 didn't start: The capacity is paused/);
  assert.deepStrictEqual(callsFrom(from, "meta_optimize").at(-1), { env: "test", entities: null });

  await page().getByLabel(/Optimize every table a Test run writes/).check();
  await toastSays(/Every table a run writes gets optimized in Fabric afterwards/);
  assert.deepStrictEqual(callsFrom(from, "meta_set_maintenance"), [{ env: "test", optimize_after_run: true }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Table health on this PC compacts instead and has no V-Order or after-run option", { skip }, async () => {
  db.health.local = true;
  await openTab("health", "dev");
  await page().getByText("Dev tables on this PC").waitFor();
  assert.strictEqual(await page().getByLabel(/Optimize every table/).count(), 0);
  assert.doesNotMatch(await page().locator(".meta-body table.list thead").textContent(), /V-Order/);
  await page().getByRole("button", { name: "Compact all" }).click();
  await toastSays(/Compacted 2 tables/);
});

// ---------- approved exceptions ----------

test("an exception about to end shows on every tab and in the bell; Review lists them all", { skip }, async () => {
  await openTab("runs");
  const banner = page().locator(".mesh-page > .problems", { hasText: "about to end" });
  await banner.waitFor();
  assert.match(await banner.textContent(), /1 approved exception is about to end.*Needs an owner exception ends on 2026-11-25/);
  const items = await page().evaluate(() => activity.items.filter((i) => i.id.startsWith("exception:")).map((i) => [i.status, i.title]));
  assert.deepStrictEqual(items.sort(), [["failed", "An approved exception needs attention"], ["info", "An approved exception is about to end"]]);
  await banner.getByRole("button", { name: "Review" }).click();
  const rows = page().locator(".meta-body table.list tbody tr");
  await rows.first().waitFor();
  assert.match(await rows.nth(0).textContent(), /silver\.customers.*Needs an owner.*ana@contoso\.com.*Legacy feed.*cdo@contoso\.com.*2026-11-25.*About to end/);
  assert.match(await rows.nth(1).textContent(), /gold\.sales.*Key must be unique.*No owner.*No reason.*No approver.*No end date/);
  assert.match(await rows.nth(2).textContent(), /bronze\.legacy.*custom_rule.*Ended/);

  db.project.exception_notices = [];
  await openTab("exceptions");
  await page().getByText("No approved exceptions").waitFor();
  assert.strictEqual(await page().locator(".mesh-page > .problems").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
