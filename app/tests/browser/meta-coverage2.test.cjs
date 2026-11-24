// app/ui/meta.js beyond meta-coverage.test.cjs and the other Data mesh tests: Profile on the entity page
// (suggestions, the before and after of a cleaning rule, Add to entity), the entity file editor's keys
// (Tab, Esc then Tab, Ctrl+S, a save that renames, Run saving first) and what the page shows of a richer
// entity, the problems banner, a run's maintenance results, Optimize all when a capacity has a warning,
// Optimize after every run, the Semantic model tab's own states and a build's notes, the promote gate's
// findings and the Prod promote confirmed with Enter, Surrogate keys on this PC and Sync's answers,
// Source systems with the platform's problems, and New entity from a landing zone where nothing landed.
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
const copy = (x) => JSON.parse(JSON.stringify(x));

const layer = (name, entities) => ({ name, lakehouse: `LH_${name[0].toUpperCase()}${name.slice(1)}`, entities, drift: "evolve",
  required_checks: ["key_unique"], reads_from: [], description: "", retention_days: 7 });
const entityRow = (name, l) => ({ name, layer: l, source: "landing/customers/*.csv", owner: "ana@contoso.com", checks: 1, clean: 0,
  derive: 0, custom: [], mode: "full", watermark: "" });
// meta_service.project: the summary the Data mesh page reads.
const PROJECT = {
  path: "C:/work/sales", name: "Sales", version: "7",
  templates: { files: "From files", landing: "From the landing zone", custom: "Custom code" },
  layers: [layer("bronze", 1), layer("silver", 1)],
  entities: [entityRow("bronze.customers", "bronze"), entityRow("silver.customers", "silver")],
  products: [], links: [], exception_notices: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
  environments: {
    dev: { target: "local" },
    test: { target: "onelake", workspace_id: "w-test", workspace_name: "Sales Test", compute: "local" },
    prod: { target: "onelake", workspace_id: "w-prod", workspace_name: "Sales Prod", compute: "local" },
  },
  project: { name: "Sales", domain: "sales" },
};
// meta_service.entity with cleaning, derived columns, custom code, keys from another entity and a preview.
const ENTITY = {
  name: "silver.customers", layer: "silver", mode: "scd2", watermark: "", owner: "", path: "metadata/entities/silver.customers.toml",
  text: "[entity]\nname = \"silver.customers\"\n", errors: [], columns: { id: "bigint", full_name: "string", region: "string", customer_sk: "int" },
  keys: ["id"], derived: ["full_name"], hook_adds: ["region"], clean: 2, derive: 1, custom: ["score.py"], checks: 3,
  surrogate_keys: [{ column: "customer_sk", table: "customer", business_key: ["id"] }], keys_sql: "", keys_from: "silver.dim_customer",
  runs: [{ run_id: "", message: "Never run in Dev" }], reads: ["bronze.customers"], impact: [], source: null,
  preview: { total: 2, columns: ["id", "full_name"], rows: [{ id: 1, full_name: "Ana Lopez" }, { id: 2, full_name: null }] },
};
// meta_service.profile: what the source holds and the rules it suggests.
const PROFILE = {
  rows: 1200, seconds: 0.4,
  suggestions: [
    { id: "trim_name", kind: "clean", column: "name", rule: { trim: "name" }, why: "12 values start or end with a space", toml: "[[clean]]\ntrim = \"name\"\n" },
    { id: "id_not_null", kind: "check", column: "id", rule: null, why: "id is never empty", toml: "[[check]]\nnot_null = \"id\"\n" },
    { id: "wide", kind: "info", column: "notes", rule: null, why: "notes is mostly empty", toml: "" },
  ],
  columns: [
    { name: "id", type: "BIGINT", nulls: 0, null_pct: 0, distinct: 1200, min: 1, max: 1200, issues: [], top: [] },
    { name: "name", type: "VARCHAR", nulls: 3, null_pct: 0.3, distinct: 1100, min_length: 2, max_length: 40,
      issues: ["12 values have spaces around them"], top: [{ value: "Ana", count: 4 }, { value: "Bo", count: 2 }] },
  ],
};
// meta_service.health: tables in OneLake, one of them the run metadata.
const HEALTH = {
  local: false, optimize_after_run: false, maintenance: [],
  tables: [
    { entity: "silver.customers", layer: "silver", exists: true, files: 40, bytes: 1048576, avg_file_bytes: 26214, v_ordered: true,
      versions_since_vacuum: 3, last_optimize: ago(600), advice: [], status: "ok" },
    { entity: "meta.runs", layer: null, exists: true, files: 90, bytes: 2048, avg_file_bytes: 22, v_ordered: false,
      versions_since_vacuum: 90, last_optimize: null, advice: ["90 small files"], status: "needs_maintenance" },
  ],
};
// meta_service.optimize_check: one workspace's capacity has a warning.
const WARN_CHECK = {
  env: "test", local: false,
  workspaces: [
    { workspace_id: "ws-a", workspace_name: "Sales Test", result: "warning", risk_id: "risk-1", title: "F64 West is 92% used",
      detail: "Maintenance now slows the other jobs", tables: ["silver.customers", "meta.runs"] },
    { workspace_id: "ws-b", workspace_name: "Ops", result: "unknown", tables: ["bronze.customers"] },
    { workspace_id: "ws-c", workspace_name: "Blocked", result: "blocked", tables: ["gold.x"] },
  ],
};
// meta_service.model_plan for the Semantic model tab.
const PLAN = { layer: "silver", layers: ["bronze", "silver"], name: "SM_Silver", workspace_name: "Sales Test", workspace_id: "w-test",
  lakehouse: "LH_Silver", existing: null,
  tables: [{ name: "customers", entity: "silver.customers", source: "silver.customers", kind: "dimension" }],
  relationships: [{ from_table: "orders", from_column: "customer_sk", to_table: "customers", to_column: "customer_sk", active: false, why: "a second path to customers" }] };
const KEYS = {
  env: "dev", lakehouse: "LH_Keys", local: true, path: "C:/work/sales/.local/LH_Keys", workspace_id: null, workspace_name: null,
  errors: ["sk.product has no business key"], problem: "LH_Keys can't be opened", key_tables: [], central_key_tables: true,
  source_systems: [], connection_fields: ["server_host"], system_types: ["SQL Server"], platform_sources: null, source_problems: [],
};
const PLATFORM_KEYS = {
  ...KEYS, errors: [], problem: null,
  source_systems: [{ id: 1, code: "CRM", name: "Dynamics", system_type: "SQL Server", owner: "", description: "", active: true,
    connections: { dev: { server_host: "crm-dev" }, test: { server_host: "" }, prod: { server_host: "" } } }],
  platform_sources: [{ name: "crm", code: "CRM", title: "Dynamics", read: true, source_id: 1 }],
  source_problems: ["CRM has no Prod connection"], platform_problem: "The platform's list of sources couldn't be read in full.",
};

let db;
const fail = {};
const fresh = () => ({ project: copy(PROJECT), entity: copy(ENTITY), health: copy(HEALTH), check: copy(WARN_CHECK), keys: copy(KEYS),
  runQueue: [], optimize: [], sync: [], promoteCheck: {}, plan: copy(PLAN), saveName: null });
const guard = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };

const mocks = {
  meta_project: () => copy(db.project),
  meta_runs: () => [],
  meta_runs_all: () => ({ runs: [{ run_id: "r7", entity: "silver.customers", env: "test", status: "ok", started_at: ago(30), rows_written: 12, seconds: 2 },
    { run_id: "r8", entity: "bronze.orders", env: "dev", status: "ok", started_at: ago(40), rows_written: 1, seconds: 1 }] }),
  meta_run_detail: () => ({ run_id: "r7", entity: "silver.customers", status: "ok", started_at: ago(30), metadata: "7", seconds: 2, attempt: 1,
    steps: [], dq: [] }),
  meta_entity: ({ name }) => ({ ...copy(db.entity), name }),
  meta_entity_shortcuts: () => [],
  meta_save_entity: guard("meta_save_entity", ({ name }) => ({ name: db.saveName || name, errors: [] })),
  meta_profile: guard("meta_profile", () => copy(PROFILE)),
  meta_profile_preview: ({ rules }) => (rules.length ? { changed: 2, columns: ["name"], rows: [{ name: " Ana ", "name (after)": "Ana" }] }
    : { changed: 0, columns: [], rows: [] }),
  meta_add_rules: guard("meta_add_rules", () => ({ errors: [] })),
  meta_run: guard("meta_run", ({ names }) => db.runQueue.shift() || (names || ["silver.customers"]).map((n) => ({ entity: n, status: "ok", rows_written: 5, run_id: "r2" }))),
  meta_risk_gate: () => ({ checks: [], held: {}, run: [] }),
  meta_health: () => copy(db.health),
  meta_optimize_check: guard("meta_optimize_check", () => copy(db.check)),
  meta_optimize: guard("meta_optimize", ({ entities }) => db.optimize.shift() || (entities || ["a"]).map(() => ({ status: "NotStarted" }))),
  meta_set_maintenance: (p) => p,
  meta_model_plan: guard("meta_model_plan", ({ layer }) => ({ ...copy(db.plan), layer: layer || db.plan.layer })),
  meta_build_model: guard("meta_build_model", ({ name }) => ({ created: true, name, tables: ["customers"], relationships: 0, skipped: ["silver.orders"],
    columns_left_out: [{ table: "customers", column: "photo", type: "BINARY" }], relationships_left_out: ["orders.customer_sk"],
    endpoint_refreshed: false, url: "https://app.fabric.microsoft.com/groups/w-test/semanticmodels/m9" })),
  meta_promote_check: ({ target }) => db.promoteCheck[target] || { errors: [], version: "7", notes: [] },
  meta_promote: guard("meta_promote", () => ({ version: "7" })),
  capacity_health: () => ({ observed_at: null, environments: [], partial: false }),
  meta_keys: () => copy(db.keys),
  meta_keys_sync: guard("meta_keys_sync", () => db.sync.shift()),
  meta_varlib: () => ({ library: "VL_Sources", published: {}, environments: { dev: {}, test: {}, prod: {} }, variables: [] }),
  meta_sources_move: () => ({ moved: ["LEG"], conflicts: [] }),
  platform_landing: () => ({ sources: [{ name: "sap", title: "sap", entities: [{ name: "", files: 0 }] }] }),
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
    Object.assign(state.meta, { env: "dev", layersDraft: null, keyTable: null, source: null, sourceEnv: "dev", sourceDraft: null, project: null,
      modelLayer: null, modelName: null });
    go("home");
  });
});

const dialog = () => page().locator("#dialog-root .dialog");
const callsFrom = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
// Waits for the app to have asked the engine; fails with the method's name if it never does.
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
async function openEntityPage() {
  await page().evaluate(() => openEntity("silver.customers"));
  await page().locator("#entity-toml").waitFor();
}

// ---------- the entity page ----------

test("Profile suggests rules, shows what a cleaning rule changes, and Add sends the ticked rules", { skip }, async () => {
  await openEntityPage();
  const from = app.calls.length;
  await page().locator(".page-head").getByRole("button", { name: "Profile" }).click();
  await dialog().getByText("1200 rows as read, before cleaning · Dev").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_profile"), [{ name: "silver.customers", env: "dev", source: true }]);
  const cols = await dialog().locator("table.mini").last().locator("tbody tr").allTextContents();
  assert.match(cols[0], /id.*BIGINT.*0 \(0%\).*1200.*1 to 1200.*clean/);
  assert.match(cols[1], /name.*VARCHAR.*3 \(0\.3%\).*2 to 40 chars.*Ana \(4\), Bo \(2\).*12 values have spaces/);
  assert.strictEqual(await dialog().getByRole("checkbox").count(), 2, "a note has nothing to add");
  assert.match(await dialog().locator("tbody tr", { hasText: "notes is mostly empty" }).textContent(), /note/);
  const add = dialog().getByRole("button", { name: /Add.*to entity/ });
  assert.ok(await add.isDisabled());

  await dialog().getByRole("checkbox", { name: "trim_name" }).check();
  await dialog().getByText("Before and after: 2 values change").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_profile_preview"), [{ name: "silver.customers", env: "dev", rules: [{ trim: "name" }] }]);
  assert.match(await dialog().locator(".output").textContent(), /name \(after\).* Ana .*Ana/);
  await dialog().getByRole("checkbox", { name: "id_not_null" }).check();
  assert.strictEqual((await add.textContent()).trim(), "Add 2 to entity");
  // Unticking the cleaning rule leaves nothing to preview.
  await dialog().getByRole("checkbox", { name: "trim_name" }).uncheck();
  await dialog().getByText("Tick a cleaning rule to see what it changes.").waitFor();
  await dialog().getByRole("checkbox", { name: "trim_name" }).check();
  await dialog().getByText("Before and after: 2 values change").waitFor();

  await add.click();
  await toastSays(/^Added 2 rules to silver\.customers\. Run it to apply them\./);
  assert.deepStrictEqual(callsFrom(from, "meta_add_rules"), [{ name: "silver.customers",
    blocks: ["[[clean]]\ntrim = \"name\"\n", "[[check]]\nnot_null = \"id\"\n"] }]);

  // Profile its table instead reads the table rather than its source.
  await page().locator(".page-head").getByRole("button", { name: "Profile" }).click();
  await dialog().getByRole("button", { name: "Profile its table instead" }).click();
  await dialog().getByText("1200 rows in its table").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_profile").at(-1), { name: "silver.customers", env: "dev", source: false });
  assert.ok(await dialog().getByRole("button", { name: "Profile what it reads" }).isVisible());
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("a refused profile or Add says why, and Add keeps the dialog", { skip }, async () => {
  await openEntityPage();
  fail.meta_profile = "ValueError: landing/customers/*.csv matches no file";
  await page().locator(".page-head").getByRole("button", { name: "Profile" }).click();
  await toastSays(/matches no file/);
  delete fail.meta_profile;
  await page().evaluate(() => closeDialog());
  fail.meta_add_rules = "ValueError: [[check]] needs a column";
  await page().locator(".page-head").getByRole("button", { name: "Profile" }).click();
  await dialog().getByRole("checkbox", { name: "id_not_null" }).check();
  await dialog().getByRole("button", { name: "Add 1 to entity" }).click();
  await toastSays(/needs a column/);
  assert.ok(await dialog().getByText("Suggestions").isVisible(), "the dialog stays open");
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
});

test("the entity page shows its steps, keys, columns, runs and data; Tab, Esc then Tab, and Ctrl+S work in the editor", { skip }, async () => {
  const from = app.calls.length;
  // With no project read yet, the entity page reads it.
  await page().evaluate(() => { state.meta.project = null; openEntity("silver.customers"); });
  await page().locator("#entity-toml").waitFor();
  await waitCall(from, "meta_project");
  const steps = await page().locator(".steps .step").allTextContents();
  assert.deepStrictEqual(steps.map((s) => s.replace(/(framework|expression|your code)$/, "|$1")), [
    "Read|framework", "Contract|framework", "Surrogate keys · 1|framework", "Clean · 2|framework", "Derive · 1|expression",
    "score.py|your code", "Checks · 3|framework", "Write · Type 2 dimension|framework", "Log|framework"]);
  assert.match(await page().locator(".page-head .sub").textContent(), /Type 2 dimension · owner not set/);
  assert.match(await page().getByText(/^Keys come from silver\.dim_customer/).textContent(), /no MERGE into LH_Keys to copy/);
  assert.strictEqual(await page().getByRole("button", { name: "Create template notebook" }).count(), 0);
  const cols = await page().locator(".cols .col-row").allTextContents();
  assert.match(cols[1], /full_name.*derived/);
  assert.match(cols[2], /region.*hook/);
  assert.match(cols[3], /customer_sk.*LH_Keys/);
  // Recent runs fill in from every environment: only this table's.
  const recent = page().locator(".card", { hasText: "Recent runs" });
  await recent.locator(".run-line").waitFor();
  assert.strictEqual(await recent.locator(".run-line").count(), 1);
  assert.match(await recent.textContent(), /Test · .*12 rows written/);
  await recent.locator(".run-line").getByRole("button", { name: "Details" }).click();
  await dialog().getByText(/^silver\.customers · /).waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_run_detail").at(-1), { run_id: "r7", env: "test" });
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  const data = page().locator(".card", { hasText: "Data in Dev · 2 rows" });
  assert.match(await data.locator("tbody").textContent(), /1Ana Lopez2null/);

  // Tab types four spaces; Esc, then Tab, moves on.
  const editor = page().locator("#entity-toml");
  await editor.click();
  await editor.press("End");
  await editor.press("Tab");
  assert.match(await editor.inputValue(), / {4}$/);
  assert.strictEqual(await page().evaluate(() => document.activeElement.id), "entity-toml");
  await editor.press("Escape");
  await editor.press("Tab");
  assert.notStrictEqual(await page().evaluate(() => document.activeElement.id), "entity-toml", "Tab after Esc leaves the editor");
  assert.doesNotMatch(await editor.inputValue(), / {8}$/);

  // Ctrl+S saves what's in the editor; a save that renames opens the new name.
  db.saveName = "silver.clients";
  const text = await editor.inputValue();
  await editor.focus();
  await editor.press("Control+s");
  await toastSays(/^Saved\. It passes the save gate\./);
  assert.deepStrictEqual(callsFrom(from, "meta_save_entity"), [{ name: "silver.customers", text }]);
  await page().locator("h1 .mono", { hasText: "silver.clients" }).waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_entity").at(-1), { name: "silver.clients", env: "dev" });
  assert.deepStrictEqual(app.errors, []);
});

test("Run with unsaved edits saves the file first, then runs the table", { skip }, async () => {
  await openEntityPage();
  const from = app.calls.length;
  await page().locator("#entity-toml").fill("[entity]\nname = \"silver.customers\"\nowner = \"ana\"\n");
  await page().locator(".page-head").getByRole("button", { name: "Run in Dev" }).click();
  await dialog().getByText("1 of 1 succeeded").waitFor();
  const order = app.calls.slice(from).map((c) => c.method).filter((m) => m === "meta_save_entity" || m === "meta_run");
  assert.deepStrictEqual(order, ["meta_save_entity", "meta_run"]);
  assert.deepStrictEqual(callsFrom(from, "meta_save_entity")[0].text, "[entity]\nname = \"silver.customers\"\nowner = \"ana\"\n");
  assert.deepStrictEqual(callsFrom(from, "meta_run")[0].names, ["silver.customers"]);
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
});

// ---------- the project page ----------

test("problems in the project show on every tab and keep Run all off", { skip }, async () => {
  db.project.errors = ["silver.customers: Needs an owner", "gold.sales reads a later layer"];
  await openTab("runs");
  const banner = page().locator(".problems", { hasText: "problems to fix" });
  await banner.waitFor();
  assert.match(await banner.textContent(), /2 problems to fix.*Runs and promote stay blocked.*Needs an owner.*reads a later layer/);
  assert.ok(await page().locator(".page-head").getByRole("button", { name: "Run all" }).isDisabled());
  await openTab("layers");
  await page().locator(".problems", { hasText: "2 problems to fix" }).waitFor();
});

test("a run's results say which tables were optimized afterwards and which weren't", { skip }, async () => {
  db.runQueue.push([
    { entity: "bronze.customers", status: "ok", rows_written: 5, run_id: "r1", maintenance: "queued" },
    { entity: "silver.customers", status: "ok", rows_written: 4, quarantined: 1, run_id: "r2", maintenance: "Failed", maintenance_message: "The capacity is paused" },
    { entity: "gold.sales", status: "ok", rows_written: 3, run_id: "r3", maintenance: "skipped", maintenance_message: "F64 West is 92% used" },
  ]);
  await openTab("entities");
  await page().locator(".page-head").getByRole("button", { name: "Run all" }).click();
  await dialog().getByText("3 of 3 succeeded").waitFor();
  const rows = dialog().locator(".result-row");
  assert.match(await rows.nth(0).textContent(), /bronze\.customers.*5 rows written.*Optimize queued/);
  assert.match(await rows.nth(1).textContent(), /4 rows written, 1 quarantined.*Optimize not started/);
  assert.strictEqual(await rows.nth(1).locator(".pill-s.wrn").getAttribute("title"), "The capacity is paused");
  assert.match(await rows.nth(2).textContent(), /Not optimized \(capacity warning\)/);
  assert.strictEqual(await rows.nth(2).locator(".pill-s.wrn").getAttribute("title"), "F64 West is 92% used");
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  assert.deepStrictEqual(app.errors, []);
});

// ---------- Table health ----------

test("Optimize all with a capacity warning lists its tables; without the tick only the others start", { skip }, async () => {
  db.optimize.push([{ status: "NotStarted" }, { status: "Failed", warning: true }, { status: "Failed", warning: true }]);
  await openTab("health", "test");
  const row = page().locator("tbody tr", { hasText: "meta.runs" });
  await row.waitFor();
  assert.match(await row.textContent(), /run metadata.*90 small files/);
  const from = app.calls.length;
  await page().getByRole("button", { name: "Optimize all in Fabric" }).click();
  await dialog().getByText("Optimize 3 tables in Fabric?").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_optimize_check"), [{ env: "test", entities: null }]);
  const text = await dialog().textContent();
  assert.match(text, /Sales Test: 2 tables.*F64 West is 92% used\. Maintenance now slows the other jobs/);
  assert.match(text, /Ops: 1 table, capacity check not known\./);
  assert.doesNotMatch(text, /Blocked/, "a blocked workspace isn't offered");
  const every = dialog().getByRole("button", { name: "Optimize all 3" });
  assert.ok(await every.isDisabled(), "all waits for the tick");
  await dialog().getByRole("button", { name: "Optimize the 1 without warnings" }).click();
  await toastSays(/Queued 1 maintenance job in Fabric\. The 2 tables on Sales Test weren't optimized, because its capacity has a warning\./);
  assert.deepStrictEqual(callsFrom(from, "meta_optimize"), [{ env: "test", entities: null, risk_ids: null }]);

  // Ticking the box starts all of them, with the warning's id.
  await page().getByRole("button", { name: "Optimize all in Fabric" }).click();
  await dialog().getByRole("checkbox").check();
  await every.click();
  assert.deepStrictEqual((await waitCall(from, "meta_optimize", 2)).at(-1), { env: "test", entities: null, risk_ids: { "ws-a": "risk-1" } });
  await toastSays(/^Queued 1 maintenance job in Fabric$/);
  assert.deepStrictEqual(app.errors, []);
});

test("skipped tables on several warned workspaces, or one, are named in the toast", { skip }, async () => {
  db.check.workspaces = [
    { workspace_id: "ws-a", workspace_name: "Sales Test", result: "warning", risk_id: "r1", title: "Busy", tables: ["silver.customers"] },
    { workspace_id: "ws-b", workspace_name: "Ops", result: "warning", risk_id: "r2", title: "Busy too", tables: ["meta.runs"] },
  ];
  db.optimize.push([{ status: "Failed", warning: true }, { status: "Failed", warning: true }, { status: "Failed", message: "Table is locked" }]);
  await openTab("health", "test");
  await page().locator("tbody tr", { hasText: "meta.runs" }).waitFor();
  await page().getByRole("button", { name: "Optimize all in Fabric" }).click();
  await dialog().getByText("Optimize 2 tables in Fabric?").waitFor();
  assert.strictEqual(await dialog().getByRole("button", { name: /without warnings/ }).count(), 0, "nothing is clear of a warning");
  await dialog().getByRole("checkbox").check();
  await dialog().getByRole("button", { name: "Optimize all 2" }).click();
  await toastSays(/^The 2 tables on 2 workspaces weren't optimized, because their capacities have a warning\. 1 didn't start: Table is locked$/);

  // One table on one workspace.
  db.check.workspaces = [db.check.workspaces[0]];
  db.optimize.push([{ status: "Failed", warning: true }]);
  await page().locator("tbody tr", { hasText: "silver.customers" }).getByRole("button", { name: "Optimize" }).click();
  await dialog().getByRole("checkbox").check();
  await dialog().getByRole("button", { name: "Optimize all 1" }).click();
  await toastSays(/^The table on Sales Test wasn't optimized, because its capacity has a warning\.$/);
});

test("Optimize after every run asks first while a capacity has a warning; Cancel leaves it off", { skip }, async () => {
  await openTab("health", "test");
  const box = page().getByRole("checkbox", { name: /Optimize every table a Test run writes/ });
  await box.waitFor();
  const from = app.calls.length;
  await box.check();
  await dialog().getByText("Optimize after every Test run?").waitFor();
  assert.match(await dialog().textContent(), /F64 West is 92% used\. After each run, Optimize starts only while the capacity check passes/);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await page().waitForFunction(() => ![...document.querySelectorAll("input[type=checkbox]")].some((b) => b.checked && /Optimize every table/.test(b.parentElement.textContent)));
  assert.deepStrictEqual(callsFrom(from, "meta_set_maintenance"), []);

  await box.check();
  await dialog().getByRole("button", { name: "Turn on" }).click();
  await toastSays(/^Every table a run writes gets optimized in Fabric afterwards/);
  assert.deepStrictEqual(callsFrom(from, "meta_set_maintenance"), [{ env: "test", optimize_after_run: true }]);
  await box.uncheck();
  await toastSays(/^Runs no longer optimize afterwards/);
  assert.deepStrictEqual(callsFrom(from, "meta_set_maintenance").at(-1), { env: "test", optimize_after_run: false });
});

// ---------- Semantic model ----------

test("the Semantic model tab asks for Test or Prod in Dev, and says so when the plan can't be read", { skip }, async () => {
  await openTab("model", "dev");
  await page().getByText("Pick Test or Prod").waitFor();
  fail.meta_model_plan = "ConnectionError: Fabric can't be reached";
  await openTab("model", "test");
  await page().getByText(/Couldn't check the semantic model in Fabric, so Create and Update are off/).waitFor();
  assert.strictEqual(await page().getByRole("button", { name: /Create model|Update model/ }).count(), 0);
});

test("the Semantic model tab reads another layer when picked, and a build says what it left out", { skip }, async () => {
  const from = app.calls.length;
  await openTab("model", "test");
  await page().getByRole("button", { name: "Create model" }).waitFor();
  assert.match(await page().locator(".rel-cell").textContent(), /orders\.customer_sk.*→ customers\.customer_sk.*inactive: a second path to customers/);
  await page().getByLabel("Layer").selectOption("bronze");
  await page().waitForFunction(() => [...document.querySelectorAll("select")].some((s) => s.value === "bronze" && s.closest(".card")));
  assert.deepStrictEqual((await waitCall(from, "meta_model_plan", 2)).at(-1), { env: "test", layer: "bronze" });
  await page().getByLabel("Model name").fill("SM_Raw");
  await page().getByRole("button", { name: "Create model" }).click();
  const done = page().locator(".pass-box", { hasText: "Created SM_Raw" });
  await done.waitFor();
  const text = await done.textContent();
  assert.match(text, /1 tables, 0 relationships\./);
  assert.match(text, /Not written yet, so left out: silver\.orders\./);
  assert.match(text, /can't read these column types, so they were left out: customers\.photo \(BINARY\)\./);
  assert.match(text, /Relationships left out because their column isn't in the model: orders\.customer_sk\./);
  assert.match(text, /The SQL endpoint refresh didn't run/);
  assert.deepStrictEqual(callsFrom(from, "meta_build_model"), [{ env: "test", layer: "bronze", name: "SM_Raw", replace: false }]);
  await done.getByRole("button", { name: "Open in Fabric" }).click();
  assert.strictEqual(await page().evaluate(() => fabricPage.page), "https://app.fabric.microsoft.com/groups/w-test/semanticmodels/m9");
  assert.deepStrictEqual(app.errors, []);
});

// ---------- Environments and promote ----------

test("the promote gate lists what to do first and its notes; Prod's promote goes once its name is typed and Enter pressed", { skip }, async () => {
  db.promoteCheck.test = { errors: ["silver.customers's last Dev run failed"], version: "7", notes: [] };
  db.promoteCheck.prod = { errors: [], version: "7", tests: { passed: 1 }, notes: ["The catalog couldn't be reached"] };
  await openTab("environments");
  const toTest = page().locator(".card", { hasText: "Promote Dev to Test" });
  await toTest.getByText("1 thing to do first").waitFor();
  assert.match(await toTest.textContent(), /last Dev run failed/);
  const toProd = page().locator(".card", { hasText: "Promote Test to Prod" });
  await toProd.locator(".warn-box").waitFor();
  assert.match(await toProd.textContent(), /Ready: metadata 7 passed every check in Test, and all 1 test passed\..*The catalog couldn't be reached/);

  const from = app.calls.length;
  await toProd.getByRole("button", { name: "Promote to Prod" }).click();
  await dialog().getByText("Promote to Prod?").waitFor();
  const name = dialog().getByRole("textbox", { name: "Type Sales Prod to confirm" });
  await name.fill("Sales Pro");
  await name.press("Enter");
  assert.ok(await dialog().isVisible(), "Enter does nothing until the name matches");
  await name.fill("Sales Prod");
  await name.press("Enter");
  await toastSays(/^Prod is now on metadata 7/);
  assert.deepStrictEqual(callsFrom(from, "meta_promote"), [{ source: "test", target: "prod", confirm_name: "Sales Prod" }]);
  assert.deepStrictEqual(app.errors, []);
});

// ---------- Surrogate keys and Source systems ----------

test("Surrogate keys on this PC says where, lists what to fix, and Sync says what the platform is missing or has", { skip }, async () => {
  db.sync.push({ created: [], missing: ["product", "store"], config: {} }, { platform: true, created: ["customer"], config: {} },
    { platform: true, created: [], config: {} });
  await openTab("keys");
  await page().getByText("C:/work/sales/.local/LH_Keys").waitFor();
  assert.match(await page().locator(".keys-head").textContent(), /This PC, C:\/work\/sales\/\.local\/LH_Keys/);
  assert.strictEqual(await page().getByRole("button", { name: /Keys workspace/ }).count(), 0, "a Keys workspace is only for OneLake");
  assert.match(await page().locator(".problems", { hasText: "Fix these first" }).textContent(), /no business key.*LH_Keys can't be opened/);
  assert.match(await page().locator(".meta-body").textContent(), /Use the platform's key table for each business key/);
  const from = app.calls.length;
  await page().getByRole("button", { name: "Sync to LH_Keys" }).click();
  await toastSays(/^Not in the platform yet: sk\.product, sk\.store\. Request them in Platform > Set up > Business keys\./);
  assert.deepStrictEqual(callsFrom(from, "meta_keys_sync"), [{ env: "dev" }]);
  await page().getByRole("button", { name: "Sync to LH_Keys" }).click();
  await toastSays(/^Every key table is in the platform; created sk\.customer/);
  await page().getByRole("button", { name: "Sync to LH_Keys" }).click();
  await toastSays(/^Every key table is in the platform$/);
});

test("Source systems with the platform shows its problems; Add says when every source is read; Move reports what moved", { skip }, async () => {
  db.keys = copy(PLATFORM_KEYS);
  db.keys.source_systems.push({ id: 5, code: "LEG", name: "Legacy", system_type: "", owner: "", description: "", active: true,
    connections: { dev: { server_host: "" }, test: { server_host: "" }, prod: { server_host: "" } } });
  db.keys.source_systems[0].platform = { name: "crm" };
  await openTab("sources");
  await page().locator(".problems", { hasText: "Fix these first" }).getByText("CRM has no Prod connection").waitFor();
  assert.match(await page().locator(".fail-text").textContent(), /couldn't be read in full/);

  await page().getByRole("button", { name: "Add a source this project reads" }).click();
  await dialog().getByText("This project already reads every source in the platform.").waitFor();
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();

  const from = app.calls.length;
  await page().getByRole("button", { name: "Move sources to the platform" }).click();
  await dialog().getByRole("button", { name: "Move" }).click();
  await toastSays(/^Moved LEG to the platform/);
  assert.deepStrictEqual(callsFrom(from, "meta_sources_move"), [{ env: "dev" }]);

  // Edit in Platform opens the platform's sources at this one.
  await page().locator(".source-item", { hasText: "CRM" }).click();
  await page().getByRole("button", { name: "Edit in Platform" }).click();
  await page().waitForFunction(() => state.view === "platform" && platformPage.source === "crm");
  assert.deepStrictEqual(app.errors, []);
});

test("New entity from the landing zone says so when nothing has landed yet", { skip }, async () => {
  await openTab("entities");
  await page().locator(".page-head").getByRole("button", { name: "New entity" }).waitFor();
  await page().evaluate(() => newEntityDialog("landing"));
  await dialog().getByText(/^Nothing has landed yet\. Type the path, like sap\/customer\/\*\.csv/).waitFor();
  assert.strictEqual(await dialog().locator("#ne-template").inputValue(), "landing");
  assert.strictEqual(await dialog().getByLabel("Landing zone source").locator("option").count(), 0, "a source with nothing landed isn't offered");
  await dialog().getByRole("button", { name: "Cancel" }).click();
});
