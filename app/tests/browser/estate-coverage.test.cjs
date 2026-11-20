// app/ui/estate.js beyond the access actions (estate-access.test.cjs): a table's History with restore,
// the Tests tab that gates promote, the VL_Sources variable library on Source systems, each
// environment's capacity line, Cost's capacity strip, a figure's "how this was worked out" dialog,
// the Prices dialog, and adding a workspace to the access review. The engine's calls are mocked
// (see harness.cjs for how to run it); each case drives the page as a person would and checks what
// they see and what the app asked the engine for.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();

const layer = (name, entities) => ({ name, lakehouse: `LH_${name[0].toUpperCase()}${name.slice(1)}`, entities, drift: "evolve",
  required_checks: ["key_unique"], reads_from: [], description: "", retention_days: 7 });
const entityRow = (name, l) => ({ name, layer: l, source: "landing/customers/*.csv", owner: "ana@contoso.com", checks: 1, clean: 0,
  derive: 0, custom: [], mode: "full", watermark: "" });
// meta_service.project: the summary the Data mesh page reads.
const PROJECT = {
  path: "C:/work/sales", name: "Sales", version: "7", templates: { files: "From files" },
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
// meta_service.entity for the entity page (as entity-editor.test.cjs).
const ENTITY = {
  name: "silver.customers", layer: "silver", mode: "merge", watermark: "updated_at", owner: "ana@contoso.com",
  path: "metadata/entities/silver.customers.toml", text: "[entity]\nname = \"silver.customers\"\n",
  errors: [], columns: { id: "bigint", name: "string" }, keys: ["id"], surrogate_keys: [], derived: [], hook_adds: [],
  runs: [], custom: [], checks: 2, reads: ["bronze.customers"], impact: ["gold.sales"], source: null,
};
// estate_service.table_history: Delta versions newest first, with what can be restored.
const HISTORY = {
  retention_days: 7, layer: "silver", incremental: true, downstream: ["gold.sales"],
  versions: [
    { version: 5, at: ago(30), operation: "MERGE", run_id: "r5", rows: 120, current: true, restorable: true, restored_to: null },
    { version: 4, at: ago(90), operation: "OPTIMIZE", rows: null, maintenance: true, restorable: true, restored_to: null },
    { version: 3, at: ago(600), operation: "WRITE", run_id: "r3", rows: 100, restorable: true, restored_to: null },
    { version: 1, at: ago(20000), operation: "CREATE TABLE", rows: 0, restorable: false, why_not: "VACUUM removed its files", restored_to: null },
  ],
  restores: [{ at: ago(4000), to_version: 2, by: "raj@contoso.com", reason: "Duplicate load" }],
};
// meta_service.tests: pytest results for the project's tests folder.
const TESTS_RUN = {
  at: ago(0), seconds: 1.2, passed: 1, failed: 1, stale: false, files: ["tests/test_clean.py"],
  missing: ["transforms/score.py names tests/test_score.py, which doesn't exist"],
  tests: [
    { name: "test_trims_names", file: "tests/test_clean.py", status: "passed", seconds: 0.1, used_by: ["silver.customers"], message: "" },
    { name: "test_drops_blank_ids", file: "tests/test_clean.py", status: "failed", seconds: 0.2, used_by: [], message: "assert 3 == 2" },
  ],
};
const TESTS_GREEN = { ...TESTS_RUN, failed: 0, passed: 2, missing: [],
  tests: TESTS_RUN.tests.map((t) => ({ ...t, status: "passed", message: "" })) };
// meta_service.varlib: VL_Sources' variables per environment.
const VARLIB = {
  library: "VL_Sources", published: { test: ago(600) },
  environments: { dev: { workspace_id: null, workspace_name: null, value_set: "DEV" },
    test: { workspace_id: "w-test", workspace_name: "Sales Test", value_set: "TEST" },
    prod: { workspace_id: "w-prod", workspace_name: "Sales Prod", value_set: "PROD" } },
  variables: [
    { name: "ENVIRONMENT", note: "Which environment this is", dev: "dev", test: "test", prod: "prod" },
    { name: "CRM_server_host", note: "CRM's SQL server", dev: "crm-dev.contoso.com", test: "crm-dev.contoso.com", prod: "" },
  ],
};
// meta_service.keys: what the Source systems tab reads.
const KEYS = {
  env: "dev", lakehouse: "LH_Keys", local: true, path: "C:/work/sales/.local/LH_Keys", key_tables: [], errors: [], problem: null,
  source_systems: [{ id: 1, code: "CRM", name: "Dynamics", system_type: "SQL Server", owner: "", description: "", active: true,
    connections: { dev: { server_host: "crm-dev" }, test: { server_host: "" }, prod: { server_host: "" } } }],
  connection_fields: ["server_host"], system_types: ["SQL Server"], platform_sources: null, source_problems: [],
};
// estate_service.capacity_health: each environment's capacity, read only.
const CAPACITIES = {
  observed_at: ago(90), partial: false,
  environments: [
    { env: "test", access: "ok", status: "running", status_text: "Active", workspace_name: "Sales Test", observed_at: ago(90), stale: true, cached: false,
      capacity: { id: "cap-1", name: "F64 West", sku: "F64", region: "West Europe" } },
    { env: "prod", access: "denied", access_text: "Can't read", action: "Ask a capacity admin for read access", workspace_name: "Sales Prod",
      capacity_id: "cap-2", observed_at: ago(90), cached: true, read_error: "Unreachable", stale: false },
    { env: "dev", access: "no_workspace" },
  ],
};
const estimated = (value, unit, extra = {}) => ({ classification: "estimated", value, unit, metric: unit === "currency" ? "fabric_cost" : "fabric_job_cu",
  currency: unit === "currency" ? "USD" : undefined, complete: true, methods: ["run time × the CUs of what it ran on"], sources: ["the app's run log"],
  period: { start: ago(43200), end: null }, parts: 4, unknown_parts: 1, assumptions: ["A Python notebook uses 1 CU"], ...extra });
// estate_service.cost_report, as estate-access.test.cjs has it, with capacities and a billed capacity.
function costReport() {
  const since = ago(43200), now = ago(0);
  return {
    days: 30, since, settings: { price_per_cu_hour: 0.18, currency: "USD", python_cu: 1.0, spark_cu: 8.0 },
    total: { runs: 4, seconds: 400, fabric_runs: 4, local_runs: 0, unknown_runs: 0, uncalibrated_runs: 0,
      evidence: { fabric_job_cu: estimated(1.5, "cu_hours"), fabric_cost: estimated(0.27, "currency"),
        avoided_cu: estimated(0, "cu_hours"), avoided_cost: estimated(0, "currency") } },
    by_env: [], by_item: [], by_day: [], unavailable: {}, capacities: CAPACITIES,
    usage: {
      period: { start: since, end: now }, imports: 1, rows: 0, imported: null,
      matched: { rows: 0, runs: 0, measured: null, estimated: null }, unmatched: { rows: 0, measured: null, by_reason: {} },
      runs: { total: 0, matched: 0, no_row: 0, not_covered: 0, no_item_name: 0 }, by_run: [],
      billed: [{ capacity: "F64 West", environments: ["Test"], days: 2, first_day: "2026-10-01", last_day: "2026-10-02",
        cu: { classification: "measured", value: 30, unit: "cu_hours", metric: "billed_cu", sources: ["cost.csv"] },
        cost: [{ classification: "measured", value: 5.4, unit: "currency", currency: "EUR", metric: "billed_cost" }] }],
      imports_list: [{ id: "u1", file_name: "cost.csv", provider_label: "Azure Cost Management", layout_label: "Cost details",
        period: { start: "2026-10-01T00:00:00Z", end: "2026-10-03T00:00:00Z" }, rows: 2, imported_at: ago(60) }],
    },
  };
}
const REVIEW = { workspaces: [{ key: "test", label: "Test", env: "test", workspace_id: "w-test", workspace_name: "Sales Test", error: null }],
  people: [], flagged: 0, roles: ["Admin", "Member", "Contributor", "Viewer"] };

let db;
const fail = {};
const NEVER_RUN = { at: null, seconds: 0, passed: 0, failed: 0, stale: false, files: ["tests/test_clean.py"], missing: [], tests: [] };
const fresh = () => ({ history: structuredClone(HISTORY), tests: [], first: NEVER_RUN, varlib: structuredClone(VARLIB) });
// A refusal the test sets with fail[method] = message.
const guard = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };

const mocks = {
  meta_project: () => structuredClone(PROJECT),
  meta_runs: () => [],
  meta_runs_all: () => ({ runs: [] }),
  meta_entity: () => structuredClone(ENTITY),
  meta_entity_shortcuts: () => [],
  meta_table_history: guard("meta_table_history", () => structuredClone(db.history)),
  meta_restore: guard("meta_restore", ({ version }) => ({ new_version: 6, restored_from: version, incremental: true,
    watermark: "2026-10-01T00:00:00", downstream: ["gold.sales"] })),
  meta_run: guard("meta_run", ({ names }) => names.map((n) => ({ entity: n, status: "ok", rows_written: 10, run_id: "r9" }))),
  meta_tests: guard("meta_tests", ({ run }) => (run ? structuredClone(db.tests.shift() || TESTS_GREEN)
    : structuredClone(db.first))),
  meta_keys: () => structuredClone(KEYS),
  meta_varlib: guard("meta_varlib", () => structuredClone(db.varlib)),
  meta_publish_varlib: guard("meta_publish_varlib", ({ env }) => ({ created: false, workspace_name: VARLIB.environments[env].workspace_name,
    value_set: VARLIB.environments[env].value_set })),
  capacity_health: () => structuredClone(CAPACITIES),
  meta_promote_check: () => ({ errors: [], version: "7", notes: [], tests: { passed: 2 } }),
  cost_report: () => costReport(),
  risk_settings: guard("risk_settings", () => ({ warn_run_cu_hours: 2, warn_failed_runs: 3, schedule_window_minutes: 30, require_acknowledgement: false })),
  set_cost_settings: guard("set_cost_settings", (p) => p),
  set_risk_settings: (p) => p,
  access_review: () => structuredClone(REVIEW),
  list_workspaces: () => [{ id: "w-test", displayName: "Sales Test" }, { id: "w-ops", displayName: "Ops" }],
};

before(async () => {
  if (skip) return;
  db = fresh();
  app = await openApp({ mocks });
  await startupReads(app);
  await page().evaluate((me) => { state.user = me; state.workspaces = null; }, ME);
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => {
    closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.meta.env = "dev"; accessPage.data = null; accessPage.extra = []; go("home");
  });
});

const dialog = () => page().locator("#dialog-root .dialog");
const callsFrom = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
async function toastText() {
  const handle = await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
  return handle.jsonValue();
}
async function openTab(tab) {
  await page().evaluate((t) => { state.meta.tab = t; go("metadata"); }, tab);
}

// ---------- History and restore ----------

async function openHistory() {
  await page().evaluate(() => openEntity("silver.customers"));
  await page().locator(".page-head").getByRole("button", { name: /^History/ }).click();
  await dialog().locator("table.list").waitFor();
}

test("History lists the versions; Restore asks, sends the version and reason, and offers to run what reads it", { skip }, async () => {
  await openHistory();
  assert.match(await dialog().locator(".dialog-head").textContent(), /History of silver\.customers in Dev/);
  const rows = await dialog().locator("tbody tr").allTextContents();
  assert.strictEqual(rows.length, 4);
  assert.match(rows[0], /Merge · run r5.*120.*Current/);
  assert.match(rows[1], /Optimize/);
  assert.match(rows[3], /Created.*Files removed/);
  assert.strictEqual(await dialog().locator("tbody tr").nth(1).getAttribute("class"), "quiet", "maintenance is quiet");
  assert.match(await dialog().textContent(), /last 7 days can be restored \(the silver layer's Keep history\)/);
  assert.match(await dialog().textContent(), /back to version 2 by raj@contoso\.com \(Duplicate load\)/);

  // Cancel goes back to the history and restores nothing.
  await dialog().locator("tbody tr").nth(2).getByRole("button", { name: "Restore" }).click();
  await dialog().getByText("Restore silver.customers to version 3?").waitFor();
  const text = await dialog().textContent();
  assert.match(text, /loads incrementally, so its watermark moves back too/);
  assert.match(text, /Tables that read it aren't changed: gold\.sales/);
  const from = app.calls.length;
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().getByText("History of silver.customers in Dev").waitFor();
  await dialog().locator("table.list").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_restore"), []);

  await dialog().locator("tbody tr").nth(2).getByRole("button", { name: "Restore" }).click();
  await dialog().getByPlaceholder("Bad export from CRM on Monday").fill("  Bad export  ");
  await dialog().getByRole("button", { name: "Restore" }).click();
  await dialog().getByText("Version 6 now holds what version 3 had.").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_restore"), [{ name: "silver.customers", env: "dev", version: 3, reason: "Bad export" }]);
  assert.match(await dialog().textContent(), /watermark moved back to 2026-10-01T00:00:00/);
  assert.strictEqual(await dialog().locator("button.node").count(), 1, "the one table that reads it is offered");

  await dialog().getByRole("button", { name: "Run downstream" }).click();
  await dialog().getByText("1 of 1 succeeded").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_run").map((p) => p.names), [["gold.sales"]]);
  assert.deepStrictEqual(app.errors, []);
});

test("History says why when it can't be read, and when the table was never written", { skip }, async () => {
  fail.meta_table_history = "PermissionError: The lakehouse can't be read";
  await page().evaluate(() => openEntity("silver.customers"));
  await page().locator(".page-head").getByRole("button", { name: /^History/ }).click();
  await dialog().getByText("Could not read the history").waitFor();
  assert.doesNotMatch(await dialog().textContent(), /Reading the table's history/);

  delete fail.meta_table_history;
  db.history.versions = [];
  await page().evaluate(() => { closeDialog(); historyDialog("silver.customers"); });
  await dialog().getByText("silver.customers hasn't been written in Dev yet.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a refused restore says so and opens no Restored dialog", { skip }, async () => {
  fail.meta_restore = "ValueError: Version 3's files were removed by VACUUM";
  await openHistory();
  await dialog().locator("tbody tr").nth(2).getByRole("button", { name: "Restore" }).click();
  await dialog().getByRole("button", { name: "Restore" }).click();
  assert.match(await toastText(), /Restoring silver\.customers to version 3|files were removed/);
  await page().waitForFunction(() => /files were removed/.test(document.getElementById("toast").textContent));
  assert.strictEqual(await page().getByText("now holds what version").count(), 0);
});

// ---------- Tests tab ----------

test("Tests runs them when they've never run, shows what fails, and Run tests runs them again", { skip }, async () => {
  db.tests = [structuredClone(TESTS_RUN)];
  const from = app.calls.length;
  await openTab("tests");
  await page().locator(".tests-summary").getByText("1 failing").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_tests"), [{ run: false }, { run: true }]);
  const rows = await page().locator(".meta-body table.list tbody tr").allTextContents();
  assert.match(rows[0], /Passed.*test_trims_names.*silver\.customers/);
  assert.match(rows[1], /Failed.*test_drops_blank_ids.*assert 3 == 2.*Shared helpers/);
  assert.match(await page().locator(".meta-body .problems").textContent(), /test_score\.py, which doesn't exist/);
  assert.match(await page().locator(".tests-summary").textContent(), /1 passed, 1 failed/);

  await page().getByRole("button", { name: "Run tests" }).click();
  await page().locator(".tests-summary").getByText("All passing").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_tests").at(-1), { run: true });
  assert.strictEqual(await page().locator(".meta-body .problems").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a refused test run says why and the summary stops saying it's running", { skip }, async () => {
  db.tests = [structuredClone(TESTS_RUN)];
  await openTab("tests");
  await page().locator(".tests-summary").getByText("1 failing").waitFor();
  fail.meta_tests = "RuntimeError: pytest couldn't import transforms";
  await page().getByRole("button", { name: "Run tests" }).click();
  assert.match(await toastText(), /pytest couldn't import transforms/);
  await page().locator(".tests-summary").getByText("1 failing").waitFor();
  assert.strictEqual(await page().getByText("Running the tests...").count(), 0);
});

test("with no test files, Tests isn't run and says how to add one", { skip }, async () => {
  db.first = { ...NEVER_RUN, files: [] };
  const from = app.calls.length;
  await openTab("tests");
  await page().getByText("No tests yet. Put test_*.py files").waitFor();
  assert.deepStrictEqual(callsFrom(from, "meta_tests"), [{ run: false }]);
  assert.match(await page().locator(".tests-summary").textContent(), /Not run yet/);
  assert.deepStrictEqual(app.errors, []);
});

// ---------- VL_Sources on Source systems ----------

test("VL_Sources shows each environment's values and Publish sends that environment", { skip }, async () => {
  await openTab("sources");
  const card = page().locator(".card.estate", { hasText: "Variable library VL_Sources" });
  await card.locator("table").waitFor();
  assert.match(await card.locator("pre").textContent(), /getLibrary\("VL_Sources"\)\nlib\.CRM_server_host/);
  const crm = await card.locator("tbody tr", { hasText: "CRM_server_host" }).textContent();
  assert.match(crm, /crm-dev\.contoso\.com.*same as Dev.*empty/);
  const dev = card.getByRole("button", { name: /Publish to Dev/ });
  assert.ok(await dev.isDisabled());
  assert.strictEqual(await dev.getAttribute("title"), "Assign a workspace to Dev first");
  assert.match(await card.getByRole("button", { name: /Publish to Test/ }).getAttribute("title"), /Sales Test, with TEST active · last published/);

  const from = app.calls.length;
  await card.getByRole("button", { name: /Publish to Test/ }).click();
  await page().waitForFunction(() => /VL_Sources updated in Sales Test, with TEST active/.test(document.getElementById("toast").textContent));
  assert.deepStrictEqual(callsFrom(from, "meta_publish_varlib"), [{ env: "test" }]);
  for (const end = Date.now() + 5000; !callsFrom(from, "meta_varlib").length; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("the library wasn't read again after publishing");
  }

  fail.meta_publish_varlib = "PermissionError: You need Contributor on Sales Prod";
  await card.getByRole("button", { name: /Publish to Prod/ }).click();
  assert.match(await toastText(), /Publishing VL_Sources to Prod|Contributor on Sales Prod/);
  await page().waitForFunction(() => /Contributor on Sales Prod/.test(document.getElementById("toast").textContent));
  assert.deepStrictEqual(app.errors, []);
});

test("VL_Sources says plainly when the library can't be read", { skip }, async () => {
  fail.meta_varlib = "HTTPError: 403 Client Error: Forbidden for url: https://api.fabric.microsoft.com/v1/workspaces";
  await openTab("sources");
  const card = page().locator(".card.estate", { hasText: "Variable library VL_Sources" });
  await page().waitForFunction(() => [...document.querySelectorAll(".card.estate")].some((c) => /VL_Sources/.test(c.textContent) && !/Reading the source systems/.test(c.textContent)));
  const text = await card.textContent();
  assert.doesNotMatch(text, /Forbidden for url|HTTPError/);
  assert.match(text, /permission/i);
});

// ---------- capacities ----------

test("Environments shows each workspace's capacity, running or why it couldn't be read", { skip }, async () => {
  await openTab("environments");
  await page().locator(".capacity-line").first().waitFor();
  const test_ = await page().locator(".env-card", { hasText: "Sales Test" }).locator(".capacity-line").textContent();
  assert.match(test_, /Capacity.*F64 West · F64 · West Europe.*Running.*Read .*Stale/);
  const prod = await page().locator(".env-card", { hasText: "Sales Prod" }).locator(".capacity-line").textContent();
  assert.match(prod, /cap-2.*Can't read.*\(unreachable now\)/);
  assert.strictEqual(await page().locator(".env-card").first().locator(".capacity-line").count(), 0, "Dev has no workspace");
  assert.deepStrictEqual(app.errors, []);
});

async function openCost() {
  await page().evaluate(() => { optimizePage.tab = "cost"; costPage.days = 30; go("optimize"); });
  await page().locator(".capacity-strip").waitFor();
}

test("Cost's capacity strip lists the capacities and Refresh reads them again", { skip }, async () => {
  await openCost();
  const rows = await page().locator(".capacity-strip tbody tr").allTextContents();
  assert.strictEqual(rows.length, 2, "an environment without a workspace isn't listed");
  assert.match(rows[0], /Test.*Sales Test.*F64 West.*Running.*Active/);
  assert.match(rows[1], /Prod.*Sales Prod.*cap-2.*Can't read.*Ask a capacity admin/);
  const from = app.calls.length;
  await page().locator(".capacity-strip").getByRole("button", { name: "Refresh" }).click();
  for (const end = Date.now() + 5000; !callsFrom(from, "cost_report").length; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("Refresh didn't redraw Cost");
  }
  assert.deepStrictEqual(callsFrom(from, "capacity_health"), [{ refresh: true }]);
  assert.deepStrictEqual(app.errors, []);
});

test("a figure opens how it was worked out; a billed capacity shows per meter and day", { skip }, async () => {
  await openCost();
  await page().locator(".cmp-stat", { hasText: "Fabric capacity used" }).locator(".measure-fig").first().click();
  await dialog().getByText("Worked out as").waitFor();
  const text = await dialog().textContent();
  assert.match(await dialog().locator(".dialog-head").textContent(), /Fabric capacity used/);
  assert.match(text, /Estimated/);
  assert.match(text, /Worked out from the assumptions below/);
  assert.match(text, /Money \(USD\), a price applied to CU use/);
  assert.match(text, /run time × the CUs of what it ran on/);
  assert.match(text, /Runs counted3 of 4/);
  assert.match(text, /A Python notebook uses 1 CU/);
  assert.match(text, /to now/);
  assert.match(text, /A price is not a charge/);
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();

  const billed = page().locator(".usage-billed");
  assert.match(await billed.textContent(), /F64 West.*Used by Test.*2 days/);
  await billed.locator(".measure-fig").last().click();
  await dialog().getByText("Money (EUR), as billed").waitFor();
  assert.match(await dialog().textContent(), /Measured.*can't be split into single runs/s);
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("Prices saves the rates and the warnings; a refusal keeps the dialog open", { skip }, async () => {
  await openCost();
  await page().getByRole("button", { name: "Prices" }).click();
  await dialog().getByText("Prices and warnings").waitFor();
  await dialog().getByLabel("Price of one CU hour").fill("0.2");
  await dialog().getByLabel("Currency").fill("EUR");
  await dialog().getByLabel("Warn when this many of the last 5 runs failed").fill("2");
  fail.set_cost_settings = "ValueError: The price must be above 0";
  const from = app.calls.length;
  await dialog().getByRole("button", { name: "Save" }).click();
  await page().waitForFunction(() => /price must be above 0/.test(document.getElementById("toast").textContent));
  assert.ok(await dialog().isVisible(), "the dialog stays open");
  assert.deepStrictEqual(callsFrom(from, "set_risk_settings"), [], "warnings aren't saved when the prices aren't");

  delete fail.set_cost_settings;
  await dialog().getByRole("button", { name: "Save" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(callsFrom(from, "set_cost_settings").at(-1), { price_per_cu_hour: "0.2", currency: "EUR", python_cu: "1", spark_cu: "8" });
  assert.deepStrictEqual(callsFrom(from, "set_risk_settings"), [{ warn_run_cu_hours: 2, warn_failed_runs: 2, schedule_window_minutes: 30, require_acknowledgement: false }]);

  // Without the risk settings, the dialog is only the cost estimate.
  fail.risk_settings = "unavailable";
  await page().getByRole("button", { name: "Prices" }).click();
  await dialog().getByText("Cost estimate").waitFor();
  assert.strictEqual(await dialog().getByText("Before a run").count(), 0);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

// ---------- the access review ----------

test("Add a workspace puts the picked workspace in the review and reads it again", { skip }, async () => {
  await page().evaluate(() => { catalogPage.tab = "access"; accessPage.view = "roles"; go("onelake"); });
  await page().getByRole("button", { name: "Add a workspace" }).click();
  await dialog().getByText("Add a workspace to the review").waitFor();
  await dialog().locator("select").selectOption({ label: "Ops" });
  const from = app.calls.length;
  await dialog().getByRole("button", { name: "Add" }).click();
  for (const end = Date.now() + 5000; !callsFrom(from, "access_review").length; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("the review wasn't read again");
  }
  assert.deepStrictEqual(callsFrom(from, "access_review"), [{ extra: [{ workspace_id: "w-ops", workspace_name: "Ops" }] }]);

  // Cancel adds nothing.
  await page().getByRole("button", { name: "Add a workspace" }).click();
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(await page().evaluate(() => accessPage.extra.length), 1);
  assert.deepStrictEqual(app.errors, []);
});
