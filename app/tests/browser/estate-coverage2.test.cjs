// app/ui/estate.js beyond estate-coverage.test.cjs and estate-access.test.cjs: Cost with runs in it (a run
// per item, a capacity's share, runs left out and why, an environment that couldn't be read, a currency
// Intl can't format), the Measured usage card when runs matched, when it can't be read and before any
// import, the import preview that asks for a period and lists what it skips, a restored version in
// History and a restore nothing reads, a lineage through a OneLake shortcut, an out of date test run,
// and the Access views' edges: no workspace yet, a flagged review with a workspace whose roles can't be
// read, Give access with nothing to give it on, a lakehouse without OneLake security, switching
// lakehouse, a partial Who can read, no roles, no lakehouses, and a role kept for an item permission.
// The engine's calls are mocked (see harness.cjs for how to run it); each case drives the page as a
// person would and checks what they see and what the app asked the engine for.
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
  path: "C:/work/sales", name: "Sales", version: "7", templates: { files: "From files" },
  layers: [layer("bronze", 1), layer("silver", 1)],
  entities: [entityRow("bronze.customers", "bronze"), entityRow("silver.customers", "silver")],
  products: [], links: [], exception_notices: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
  environments: { dev: { target: "local" }, test: { target: "onelake", workspace_id: "w-test", workspace_name: "Sales Test", compute: "local" },
    prod: { target: "onelake", workspace_id: "w-prod", workspace_name: "Sales Prod", compute: "local" } },
  project: { name: "Sales", domain: "sales" },
};
// meta_service.entity: bronze.customers reads files, not another entity, so its lineage asks for shortcuts.
const ENTITY = {
  name: "bronze.customers", layer: "bronze", mode: "full", watermark: "", owner: "ana@contoso.com",
  path: "metadata/entities/bronze.customers.toml", text: "[entity]\nname = \"bronze.customers\"\n",
  errors: [], columns: { id: "bigint" }, keys: ["id"], surrogate_keys: [], derived: [], hook_adds: [],
  runs: [], custom: [], checks: 1, reads: [], impact: [], source: "landing/customers/*.csv",
};
// estate_service.table_history: one version is a restore, and nothing reads the table.
const HISTORY = {
  retention_days: 7, layer: "bronze", incremental: false, downstream: [],
  versions: [
    { version: 3, at: ago(30), operation: "RESTORE", rows: 100, current: true, restorable: true, restored_to: 1 },
    { version: 2, at: ago(90), operation: "WRITE", run_id: null, rows: 90, restorable: true, restored_to: null },
    { version: 1, at: ago(600), operation: "SOMETHING NEW", rows: 80, restorable: true, restored_to: null },
  ],
  restores: [{ at: ago(30), to_version: 1, by: "", reason: "" }],
};
// meta_service.tests: a run from before the project changed.
const STALE_TESTS = { at: ago(120), seconds: 0.4, passed: 1, failed: 0, stale: true, files: ["tests/test_clean.py"], missing: [],
  tests: [{ name: "test_trims_names", file: "tests/test_clean.py", status: "passed", seconds: 0.1, used_by: ["silver.customers"], message: "" }] };

// A classified measurement (sidecar measurement.py).
const fig = (classification, value, unit, extra = {}) => ({ classification, value, unit, currency: unit === "currency" ? "USD" : undefined,
  complete: true, methods: [], sources: [], ...extra });
// estate_service.cost_report with runs in it, a capacity's share, runs matched to an import, and Prod left out.
function costReport(over = {}) {
  const since = ago(43200), now = ago(0);
  return {
    days: 30, since, settings: { price_per_cu_hour: 0.18, currency: "USD", python_cu: 1.0, spark_cu: 8.0 },
    total: { runs: 6, seconds: 900, fabric_runs: 3, local_runs: 3, unknown_runs: 2, uncalibrated_runs: 1,
      evidence: { fabric_job_cu: fig("estimated", 1.5, "cu_hours"), fabric_cost: fig("estimated", 0.27, "currency"),
        avoided_cu: fig("estimated", 0.5, "cu_hours"), avoided_cost: fig("estimated", 0.09, "currency") } },
    by_env: [
      { env: "test", capacity: "F64 West", sku: "F64", fabric_runs: 3, capacity_share_evidence: fig("estimated", 0.05, "percent", { metric: "capacity_share" }) },
      { env: "dev", capacity: null, sku: null, fabric_runs: 0, capacity_share_evidence: fig("unknown", null, "percent") },
    ],
    by_item: [
      { name: "silver.customers", kind: "Data mesh entity", env: "test", runs: 3, seconds: 600, fabric_runs: 3, local_runs: 0,
        evidence: { fabric_cost: fig("estimated", 0.2, "currency", { complete: false, reason: "1 run is still running" }) } },
      { name: null, kind: "Notebook", env: "dev", runs: 3, seconds: 0, fabric_runs: 0, local_runs: 3,
        evidence: { avoided_cost: fig("scenario", 0.004, "currency") } },
    ],
    by_day: [], unavailable: { prod: "sign-in expired" }, capacities: { observed_at: null, environments: [], partial: false },
    usage: {
      period: { start: since, end: now }, imports: 1, rows: 40, imported: fig("measured", 2.5, "cu_hours"),
      matched: { rows: 3, runs: 2, measured: fig("measured", 1.8, "cu_hours"), estimated: fig("estimated", 1.2, "cu_hours"), measured_to_estimate: 1.5 },
      unmatched: { rows: 37, measured: fig("measured", 0.7, "cu_hours"), by_reason: { "No run of this item started from this app": 37 } },
      runs: { total: 5, matched: 2, no_row: 1, not_covered: 1, no_item_name: 1 },
      by_run: [
        { state: "matched", name: "nb_sales", item: "nb_sales", kind: "Notebook", workspace: "Sales Test", started_at: ago(300),
          measured: fig("measured", 5400, "cu_seconds"), estimated: fig("estimated", 0.9, "cu_hours") },
        { state: "no_row", name: "nb_other", item: "nb_other", kind: "Notebook", workspace: "Sales Test", started_at: ago(200) },
      ],
      billed: [{ capacity: "F2 Trial", environments: [], days: 1, first_day: "2026-10-01", last_day: "2026-10-01", cu: null,
        cost: [{ classification: "measured", value: 1.2, unit: "currency", currency: "EUR", metric: "billed_cost" }] }],
      imports_list: [{ id: "u1", file_name: "metrics.csv", provider_label: "Capacity Metrics app export", layout_label: "Operations (Timepoint page)",
        period: { start: "2026-10-01T00:00:00Z", end: "2026-10-03T00:00:00Z" }, rows: 40, imported_at: ago(60) }],
    },
    ...over,
  };
}

// usage_service.usage_preview: an export without its period asks for one; with it, the rows it reads and skips.
function usagePreview({ options }) {
  const base = { provider: { id: "cost_management", label: "Azure Cost Management" }, layout: { id: "details", label: "Cost details" },
    columns: { used: { capacity: "ResourceName", cost: "CostInBillingCurrency" }, ignored: ["Tags", "MeterId"], not_stored: ["Tags"], missing: [] }, notes: [] };
  if (!options.period_start) {
    return { ...base, ready: false, needs: { option: "period" }, problem: "The file doesn't say which days it covers. Give the period it was exported for." };
  }
  return { ...base, ready: true, problem: "Some rows were skipped.", needs: null, rows_read: 10, to_import: 6, already_imported: 1,
    replaces_earlier: 2, duplicates_in_file: 1, period: { start: "2026-10-01T00:00:00Z", end: "2026-10-03T00:00:00Z" },
    workspaces: [], capacities: ["F64 West"], total: fig("measured", 12, "cu_hours", { metric: "billed_cu" }),
    cost: [{ classification: "measured", value: 5.4, unit: "currency", currency: "EUR", metric: "billed_cost" }],
    skipped: [{ reason: "Not a Fabric meter", count: 4, lines: [3, 5, 7] }, { reason: "No date", count: 1, lines: [9] }],
    notes: ["Costs are in the billing currency."] };
}

// estate_service.access_review: a flagged person, and a workspace whose roles can't be listed.
const REVIEW = {
  workspaces: [
    { key: "test", label: "Test", env: "test", workspace_id: "w-test", workspace_name: "Sales Test", error: null },
    { key: "prod", label: "Prod", env: "prod", workspace_id: "w-prod", workspace_name: "Sales Prod", error: "Forbidden" },
  ],
  people: [{ id: "p1", name: "Guest Bo", type: "User", email: "bo@fabrikam.com", flags: ["A guest with Admin"], roles: { test: { role: "Admin", assignment_id: "a1" } } },
    { id: "p2", name: "etl-app", type: "ServicePrincipal", email: "", flags: [], roles: {} }],
  flagged: 1, roles: ["Admin", "Member", "Contributor", "Viewer"],
};
const CATALOG = {
  workspaces: [{ id: "w-test", displayName: "Sales Test" }, { id: "w-ops", displayName: "Ops" }],
  items: [
    { workspace_id: "w-test", workspace_name: "Sales Test", item_id: "lh-1", name: "LH_Silver", type: "Lakehouse" },
    { workspace_id: "w-ops", workspace_name: "Ops", item_id: "lh-2", name: "LH_Ops", type: "Lakehouse" },
    { workspace_id: "w-ops", workspace_name: "Ops", item_id: "nb-1", name: "nb_ops", type: "Notebook" },
  ],
  skipped: [], favorites: [],
};
// estate_service.access_lakehouse: LH_Silver without OneLake security, LH_Ops with it and no roles yet.
const LAKEHOUSES = {
  "lh-1": { workspace_id: "w-test", item_id: "lh-1", enabled: false, problem: "OneLake security isn't enabled on this item.", people: [], schemas: [], tables: ["/Tables/dbo/customers"], roles: [] },
  "lh-2": { workspace_id: "w-ops", item_id: "lh-2", enabled: true, problem: null, people: [{ id: "p1", name: "Guest Bo", role: "Viewer" }],
    schemas: ["/Tables/dbo"], tables: ["/Tables/dbo/jobs"], roles: [] },
};

let db;
const fail = {};
const fresh = () => ({ report: costReport(), history: copy(HISTORY), review: copy(REVIEW), catalog: copy(CATALOG), lakehouses: copy(LAKEHOUSES) });
const guard = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };

const mocks = {
  meta_project: () => copy(PROJECT),
  meta_runs: () => [],
  meta_runs_all: () => ({ runs: [] }),
  meta_entity: () => copy(ENTITY),
  meta_entity_shortcuts: () => [{ name: "sc_crm", full_path: "Files/landing/customers", target_type: "AdlsGen2", target: "https://crm.dfs.core.windows.net/raw" }],
  meta_table_history: () => copy(db.history),
  meta_restore: ({ version }) => ({ new_version: 4, restored_from: version, incremental: false, downstream: [] }),
  meta_tests: () => copy(STALE_TESTS),
  meta_promote_check: () => ({ errors: [], version: "7", notes: [] }),
  capacity_health: () => ({ observed_at: null, environments: [], partial: false }),
  cost_report: guard("cost_report", () => copy(db.report)),
  usage_preview: guard("usage_preview", usagePreview),
  access_review: () => copy(db.review),
  catalog: () => copy(db.catalog),
  access_lakehouse: ({ item_id }) => copy(db.lakehouses[item_id]),
  access_who_can_read: () => ({ readers: [], partial: true, problem: "The data access roles couldn't be read." }),
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
    state.meta.env = "dev"; costPage.days = 30;
    Object.assign(accessPage, { data: null, extra: [], lh: null, lakehouse: null, view: "all" }); catalogPage.data = null; go("home");
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
async function openCost() {
  await page().evaluate(() => { optimizePage.tab = "cost"; go("optimize"); });
  await page().locator(".cost-head").waitFor();
}
async function openAccess(view) {
  await page().evaluate((v) => { catalogPage.tab = "access"; accessPage.view = v; go("onelake"); }, view);
}

// ---------- Cost ----------

test("Cost lists each item's runs, a capacity's share, and the runs it leaves out and why", { skip }, async () => {
  await openCost();
  const stats = page().locator(".cost .cmp-stats").first();
  await stats.getByText("of F64 West capacity (F64)").waitFor();
  assert.match(await stats.textContent(), /<0\.1%.*Estimated.*Test's Fabric runs/s);
  assert.doesNotMatch(await stats.textContent(), /Dev's Fabric runs/, "an unknown share isn't shown");
  const cost = await page().locator(".cost").textContent();
  assert.match(cost, /1 run on this PC had no comparable Fabric run of the same table/);
  assert.match(cost, /2 runs' capacity use isn't known/);
  assert.match(cost, /Left out: Prod \(sign-in expired\)\./);
  const rows = await page().locator(".cost > table.list tbody tr").allTextContents();
  assert.strictEqual(rows.length, 2);
  assert.match(rows[0], /silver\.customers.*Data mesh entity.*Test.*3.*Estimated.*Incomplete/);
  assert.match(rows[1], /Notebook.*Dev.*3.*Not known.*under .*0\.01.*Scenario/);
  // The Incomplete badge says why.
  assert.strictEqual(await page().locator(".cost > table.list tbody tr").first().locator(".pill-s", { hasText: "Incomplete" }).getAttribute("title"), "1 run is still running");

  // The share opens how it was worked out, in percent of a capacity.
  await stats.locator(".cmp-stat", { hasText: "F64 West" }).locator(".measure-fig").click();
  await dialog().getByText("Percent of a capacity over the period").waitFor();
  assert.match(await dialog().locator(".dialog-head").textContent(), /Share of F64 West capacity/);
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();

  // 7 days reads the report again for that many days.
  const from = app.calls.length;
  await page().getByRole("button", { name: "7 days" }).click();
  assert.deepStrictEqual((await waitCall(from, "cost_report")).at(-1), { days: 7 });
  await page().waitForFunction(() => document.querySelector(".cost-head .chip.on")?.textContent === "7 days");
  assert.deepStrictEqual(app.errors, []);
});

test("Measured usage compares the matched runs with their estimate and says why the others didn't match", { skip }, async () => {
  await openCost();
  const card = page().locator(".usage-card");
  await card.locator(".cmp-stats").waitFor();
  const text = await card.textContent();
  assert.match(text, /2\.50 CU h.*Measured CU in your imports.*40 rows in the last 30 days/s);
  assert.match(text, /1\.80 CU h.*This app's runs, measured.*2 of 5 Fabric runs matched/s);
  assert.match(text, /The same runs, estimated.*measured is 1\.5× the estimate/s);
  assert.match(text, /0\.70 CU h.*Other use in the imports.*37 rows not from this app's runs/s);
  assert.match(text, /1 run inside an import had no matching row.*1 run aren't covered by any import\. 1 run can't be matched\./s);
  assert.strictEqual(await card.locator(".cmp-stat", { hasText: "Other use" }).locator("span[title]").last().getAttribute("title"),
    "No run of this item started from this app: 37");
  // Only the matched run is listed, with its CU-seconds.
  const runs = await card.locator(":scope > table.list tbody tr").allTextContents();
  assert.strictEqual(runs.length, 1);
  assert.match(runs[0], /nb_sales.*Notebook · Sales Test.*5,400 CU-s.*0\.90 CU h/);
  await card.locator(":scope > table.list tbody tr").locator(".measure-fig").first().click();
  await dialog().getByText("CU-seconds (capacity use)").waitFor();
  assert.match(await dialog().locator(".dialog-head").textContent(), /nb_sales: measured/);
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  // A billed capacity none of the environments uses, with no compute meter.
  const billed = await page().locator(".usage-billed tbody tr").textContent();
  assert.match(billed, /F2 Trial.*Not one of your environments' capacities.*1 day.*None/);
  assert.deepStrictEqual(app.errors, []);
});

test("Measured usage says why it can't be read, and how to get a file before any import", { skip }, async () => {
  db.report.usage = { ...db.report.usage, error: "The usage store on this PC can't be opened." };
  await openCost();
  await page().locator(".usage-card .error-text").getByText("The usage store on this PC can't be opened.").waitFor();
  assert.strictEqual(await page().locator(".usage-card .cmp-stats").count(), 0);

  db.report = costReport({ usage: { ...costReport().usage, imports_list: [] }, settings: { price_per_cu_hour: 0.18, currency: "EURO", python_cu: 1, spark_cu: 8 } });
  await page().getByRole("button", { name: "30 days" }).click();
  await page().locator(".usage-card").getByText(/a capacity admin exports the operations table/).waitFor();
  // A currency Intl can't format is still shown, as the number and the code.
  assert.match(await page().locator(".cost-head").textContent(), /0\.18 EURO per CU hour/);

  // Cost that can't be added up says so in the panel.
  fail.cost_report = "PermissionError: The run log can't be read";
  await page().getByRole("button", { name: "90 days" }).click();
  await page().locator(".cost .monitor-empty").getByText(/run log can't be read/).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("an export without its period asks for one; Use this period previews again with it and lists the skipped rows", { skip }, async () => {
  await openCost();
  await page().getByRole("button", { name: "Import usage file" }).click();
  const from = app.calls.length;
  await dialog().getByLabel("File").setInputFiles({ name: "cost.csv", mimeType: "text/csv", buffer: Buffer.from("Date,Cost\n") });
  await dialog().getByText("The file doesn't say which days it covers.").waitFor();
  const go = dialog().getByRole("button", { name: "Import", exact: true });
  assert.ok(await go.isDisabled(), "nothing to import until the period is given");
  await dialog().getByLabel("From").fill("2026-10-01");
  await dialog().getByLabel("To", { exact: true }).fill("2026-10-03");
  await dialog().getByRole("button", { name: "Use this period" }).click();
  await dialog().getByText("Recognized as").waitFor();
  assert.deepStrictEqual((await waitCall(from, "usage_preview", 2)).map((p) => p.options),
    [{ tz: "UTC" }, { tz: "UTC", period_start: "2026-10-01", period_end: "2026-10-03" }]);
  const text = await dialog().textContent();
  assert.match(text, /Azure Cost Management, Cost details/);
  assert.match(text, /Not storedTags/);
  assert.match(text, /Not usedMeterId/);
  assert.match(text, /10 read · 6 to import · 1 already imported · 2 for days an earlier import also has .* · 1 repeated in the file/);
  assert.match(text, /CapacitiesF64 West/);
  assert.match(text, /Compute billed12 CU h/);
  assert.match(text, /Billed cost/);
  assert.match(text, /5 rows skipped/);
  assert.match(text, /Not a Fabric meter: 4 \(lines 3, 5, 7, \.\.\.\)/);
  assert.match(text, /No date: 1 \(line 9\)/);
  assert.match(text, /Costs are in the billing currency\./);
  assert.strictEqual(await dialog().locator("p.muted-text", { hasText: "Some rows were skipped." }).count(), 1, "a ready file's note isn't an error");
  assert.ok(!(await go.isDisabled()));

  // A preview the engine refuses leaves the dialog empty, with Import off.
  fail.usage_preview = "ValueError: The file isn't UTF-8";
  await dialog().getByLabel("Times are in").fill("+01:00");
  await dialog().getByLabel("Times are in").dispatchEvent("change");
  await toastSays(/isn't UTF-8/);
  await page().waitForFunction(() => !document.querySelector(".usage-preview").textContent);
  assert.ok(await go.isDisabled());
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

// ---------- History, lineage and tests ----------

test("History names a restore by the version it went back to; a restore nothing reads offers no run", { skip }, async () => {
  await page().evaluate(() => openEntity("bronze.customers"));
  // Its files come through a OneLake shortcut, so the lineage says so.
  const via = page().locator(".lineage3 .node.plain", { hasText: "via shortcut sc_crm" });
  await via.waitFor();
  assert.match(await via.textContent(), /→ AdlsGen2/);
  assert.strictEqual(await via.getAttribute("title"), "Files/landing/customers points at AdlsGen2: https://crm.dfs.core.windows.net/raw");

  await page().locator(".page-head").getByRole("button", { name: /^History/ }).click();
  await dialog().locator("table.list").waitFor();
  const rows = await dialog().locator("tbody tr").allTextContents();
  assert.match(rows[0], /Restored to version 1.*Current/);
  assert.match(rows[1], /^2.*Write90Restore$/);
  assert.match(rows[2], /SOMETHING NEW/);
  assert.match(await dialog().textContent(), /back to version 1 by someone/);

  await dialog().locator("tbody tr").nth(2).getByRole("button", { name: "Restore" }).click();
  await dialog().getByText("Restore bronze.customers to version 1?").waitFor();
  assert.doesNotMatch(await dialog().textContent(), /loads incrementally|Tables that read it/);
  await dialog().getByRole("button", { name: "Restore" }).click();
  await dialog().getByText("Version 4 now holds what version 1 had.").waitFor();
  assert.match(await dialog().textContent(), /Nothing reads this table\./);
  assert.strictEqual(await dialog().getByRole("button", { name: "Run downstream" }).count(), 0);
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("a test run from before the project changed is marked Out of date", { skip }, async () => {
  await page().evaluate(() => { state.meta.tab = "tests"; go("metadata"); });
  const pill = page().locator(".tests-summary .pill-s.wrn");
  await pill.waitFor();
  assert.strictEqual(await pill.textContent(), "Out of date");
  assert.strictEqual(await pill.getAttribute("title"), "The project changed since this run");
  assert.match(await page().locator(".tests-summary").textContent(), /All passing.*1 passed, 0 failed/);
});

// ---------- Access ----------

test("Environments flags who to look at and says which workspaces' roles can't be listed", { skip }, async () => {
  await openAccess("roles");
  await page().getByLabel("Guest Bo in Test").waitFor();
  assert.match(await page().locator(".problems").textContent(), /1 to look at.*Flags are listed next to each person/);
  assert.match(await page().locator("tbody tr", { hasText: "Guest Bo" }).textContent(), /User · bo@fabrikam\.com.*A guest with Admin/);
  assert.ok(await page().getByLabel("Guest Bo in Prod").isDisabled(), "roles in a workspace that can't be read can't be changed");
  assert.match(await page().locator("thead").textContent(), /Prod.*Can't read roles/);
  assert.match(await page().locator(".page").textContent(), /Listing roles needs Member or Admin on the workspace: Prod\./);
  assert.strictEqual(await page().getByLabel("etl-app in Test").inputValue(), "", "an app with no role shows No access");

  // Give access offers only the workspaces it can change.
  await page().getByRole("button", { name: "Give access" }).click();
  const options = await dialog().getByLabel("Workspace").locator("option").allTextContents();
  assert.deepStrictEqual(options, ["Test · Sales Test"]);
  assert.strictEqual(await dialog().getByLabel("Role").inputValue(), "Viewer");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("with no environment workspace, Environments says how to add one and Give access asks for one first", { skip }, async () => {
  db.review = { workspaces: [], people: [], flagged: 0, roles: REVIEW.roles };
  await openAccess("roles");
  await page().getByText("No environment has a workspace yet.").waitFor();
  await page().getByRole("button", { name: "Give access" }).click();
  await toastSays(/^Add a workspace to the review first/);
  assert.strictEqual(await dialog().count(), 0, "no dialog opens");
});

test("Table access reads the first lakehouse, says when OneLake security is off on another, and shows a partial answer", { skip }, async () => {
  const from = app.calls.length;
  await openAccess("tables");
  await page().getByText("No roles yet.").waitFor();
  assert.deepStrictEqual(callsFrom(from, "access_lakehouse"), [{ workspace_id: "w-ops", item_id: "lh-2" }]);
  assert.deepStrictEqual(await page().getByLabel("Lakehouse").locator("option").allTextContents(), ["LH_Ops · Ops", "LH_Silver · Sales Test"],
    "only lakehouses are listed, by workspace and name");

  await page().getByLabel("Lakehouse").selectOption("lh-1");
  await page().getByText("OneLake security isn't on for this lakehouse, or you can't manage it.").waitFor();
  assert.deepStrictEqual(callsFrom(from, "access_lakehouse").at(-1), { workspace_id: "w-test", item_id: "lh-1" });
  await page().locator("details summary", { hasText: "Details" }).click();
  assert.match(await page().locator("details pre").textContent(), /OneLake security isn't enabled on this item/);

  // Who can read without the data access roles says the list isn't complete.
  await page().getByLabel("Table", { exact: true }).selectOption("/Tables/dbo/customers");
  await page().getByText("No one found so far.").waitFor();
  assert.match(await page().locator(".who-reads .warn-text").textContent(), /Not complete: .* The data access roles couldn't be read\./);
  assert.deepStrictEqual(callsFrom(from, "access_who_can_read"), [{ workspace_id: "w-test", item_id: "lh-1", table: "/Tables/dbo/customers" }]);
  // Picking no table empties the answer.
  await page().getByLabel("Table", { exact: true }).selectOption("");
  await page().waitForFunction(() => !document.querySelector(".who-reads .warn-text"));

  // Open the lakehouse in Fabric goes to its page there.
  await page().getByRole("button", { name: "Open the lakehouse in Fabric" }).click();
  assert.match(await page().evaluate(() => fabricPage.page), /groups\/w-test\/.*lh-1/);
  assert.deepStrictEqual(app.errors, []);
});

test("Table access with no lakehouses says so, and a role kept for an item permission says so when edited", { skip }, async () => {
  db.catalog.items = [];
  await openAccess("tables");
  await page().getByText("No lakehouses yet.").waitFor();

  db.catalog = copy(CATALOG);
  db.lakehouses["lh-2"].roles = [{ name: "Readers", paths: ["/Tables/dbo/jobs"], entra: ["x-unknown"], item_members: ["ReadAll", "ReadData"] }];
  await page().evaluate(() => { catalogPage.data = null; accessPage.lakehouse = null; accessPage.lh = null; render(); });
  const row = page().locator("tbody tr", { hasText: "Readers" });
  await row.waitFor();
  assert.match(await row.textContent(), /dbo\.jobs.*x-unknown.*Everyone with ReadAll permission.*Everyone with ReadData permission/);
  await row.getByRole("button", { name: "Edit" }).click();
  await dialog().getByText("Kept as is: everyone with ReadAll, ReadData permission.").waitFor();
  // A member who isn't one of the workspace's people is still listed, ticked.
  assert.ok(await dialog().getByLabel("x-unknown").isChecked());
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(app.errors, []);
});
