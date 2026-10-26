// Data Factory copy jobs (DF-6, Preview): the Copy jobs tab lists a workspace's copy jobs and shows the
// one picked: source, destination, each table, whether this PC could run it, and its runs. Reads only.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const WS = "11111111-2222-3333-4444-555555555555";
const CJ = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const SQL = "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee";
const lakehouse = (item) => ({ type: "LakehouseTable", connection_type: "Lakehouse", connection_id: "", workspace_id: WS, item_id: item,
  root_folder: "Tables", database: "" });

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    mocks: {
      sign_in: () => ({ user: "chris@contoso.com", tenant: "contoso" }),
      list_workspaces: () => [{ id: WS, displayName: "Sales Dev" }],
      workspace_copy_jobs: () => ({ workspace_id: WS, copy_jobs: [
        { workspace_id: WS, item_id: CJ, name: "cj_bronze_orders", description: "Landing to bronze", folder_id: "" },
        { workspace_id: WS, item_id: SQL, name: "cj_erp_customers", description: "", folder_id: "" },
        { workspace_id: WS, item_id: "dddddddd-bbbb-cccc-dddd-eeeeeeeeeeee", name: "cj_broken", description: "", folder_id: "" }] }),
      workspace_copy_job_definition: (p) => {
        const base = { workspace_id: WS, item_id: p.item_id, name: p.name, ok: true, problems: [], timeout: "0.12:00:00", retry: null };
        if (p.item_id === CJ) {
          return { ...base, job_mode: "Batch", source: lakehouse("lh-landing"), destination: lakehouse("lh-bronze"),
            local_check: { possible: true, reasons: [] },
            tables: [{ id: "t1", source: { schema: "", table: "orders", folder: "" }, destination: { schema: "sales", table: "orders", folder: "" },
              write_behavior: "Append", keys: [], table_option: "autoCreate", read_method: "", incremental_columns: ["modified_at"],
              mapped_columns: 0, staging: false }] };
        }
        if (p.item_id === SQL) {
          return { ...base, job_mode: "CDC",
            source: { type: "AzureSqlTable", connection_type: "AzureSqlDatabase", connection_id: "c1", workspace_id: "", item_id: "", root_folder: "", database: "erp" },
            destination: lakehouse("lh-bronze"),
            local_check: { possible: false, reasons: ["The source is AzureSqlDatabase, not a lakehouse: it needs Fabric's connection, so it runs in Fabric only.",
              "Change data capture (CDC) jobs run in Fabric only."] },
            tables: [{ id: "t1", source: { schema: "dbo", table: "customers", folder: "" }, destination: { schema: "", table: "customers", folder: "" },
              write_behavior: "Upsert", keys: ["customer_id"], table_option: "", read_method: "SnapshotPlusIncremental", incremental_columns: ["id"],
              mapped_columns: 3, staging: false }] };
        }
        throw new Error("GET https://api.fabric.microsoft.com/... failed with 403: {}");
      },
      fabric_copy_job_runs: () => ({ workspace_id: WS, item_id: CJ, runs: [
        { job_id: "j2", state: "failed", native_status: "Failed", started: "2026-10-25T06:00:00Z", seconds: 75, invoke: "Scheduled", message: "Source not found" },
        { job_id: "j1", state: "succeeded", native_status: "Completed", started: "2026-10-24T06:00:00Z", seconds: 30, invoke: "Manual", message: "" }] }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof renderDfCopyJobs === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("signed out, the tab offers sign-in, then lists a workspace's copy jobs", { skip }, async () => {
  await page().evaluate(() => { state.user = null; state.workspaces = null; plPage.area = "pipelines"; go("pipelines"); });
  assert.deepStrictEqual(await page().locator('.df-areas [role="tab"]').allTextContents(), ["Pipelines", "Copy jobs", "Dataflows", "Connections"]);
  await page().locator('.df-areas [role="tab"]', { hasText: "Copy jobs" }).click();
  await page().locator(".df-copy-body button", { hasText: "Sign in to list copy jobs" }).click();
  await page().selectOption('.df-ws select[aria-label="Workspace"]', { label: "Sales Dev" });
  await page().locator(".df-flow-row", { hasText: "cj_bronze_orders" }).waitFor();
  assert.deepStrictEqual(await page().locator(".df-flow-row b").allTextContents(), ["cj_bronze_orders", "cj_erp_customers", "cj_broken"]);
});

test("a OneLake copy job shows its ends, its tables and that this PC could run it", { skip }, async () => {
  await page().locator(".df-flow-row", { hasText: "cj_bronze_orders" }).click();
  await page().locator(".df-copy-table").waitFor();
  assert.deepStrictEqual(app.calls.find((c) => c.method === "workspace_copy_job_definition").params,
    { workspace_id: WS, item_id: CJ, name: "cj_bronze_orders" });
  assert.strictEqual(await page().locator(".df-detail-head .pill-s").textContent(), "OneLake only");
  assert.match(await page().locator(".df-kv").textContent(), /SourceLakehouse in Sales Dev \(Tables\)DestinationLakehouse in Sales Dev \(Tables\)ModeBatch.*ConnectionsNone \(OneLake only\)/);
  assert.match(await page().locator(".df-local-check").textContent(), /sandbox, never the real table/);
  assert.match(await page().locator(".df-copy-table tbody").textContent(), /orderssales\.ordersCreates the tableAppendIncrementalOn modified_at/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "df-copy-jobs.png") });
});

test("Run history lists Fabric's runs, newest first", { skip }, async () => {
  await page().locator('[role="tab"]', { hasText: "Run history" }).click();
  const rows = await page().locator(".df-copy-runs tbody tr").allTextContents();
  assert.match(rows[0], /^Failed.*1m 15s.*scheduled.*Source not found$/);
  assert.match(rows[1], /^Succeeded.*30s.*manual/);
});

test("an incremental SQL job lists its keys and says why it runs in Fabric only", { skip }, async () => {
  await page().locator('[role="tab"]', { hasText: "Tables" }).click();
  await page().locator(".df-flow-row", { hasText: "cj_erp_customers" }).click();
  await page().locator(".df-copy-table tbody", { hasText: "dbo.customers" }).waitFor();
  assert.strictEqual(await page().locator(".df-detail-head .pill-s").textContent(), "Runs in Fabric");
  assert.match(await page().locator(".df-kv").textContent(), /SourceAzureSqlDatabase · erp.*ModeChange data capture \(CDC\).*Connections1 \(see the Connections tab\)/);
  assert.match(await page().locator(".df-local-check").textContent(), /not a lakehouse.*CDC/);
  assert.match(await page().locator(".df-copy-table tbody").textContent(), /UpsertKeys: customer_idSnapshotPlusIncrementalOn id/);
});

test("a copy job Fabric won't give says why; Open in Fabric goes to its workspace; nothing runs", { skip }, async () => {
  await page().locator(".df-flow-row", { hasText: "cj_broken" }).click();
  await page().locator(".df-detail .error-text", { hasText: "Could not read cj_broken" }).waitFor();
  await page().evaluate(() => { window.openInFabric = (url) => { window.openedUrl = url; }; });
  await page().locator(".df-detail-head button", { hasText: "Open in Fabric" }).click();
  assert.match(await page().evaluate(() => window.openedUrl), new RegExp(`/groups/${WS}/`));
  assert.ok(!app.calls.some((c) => /run_item|copy_job_run$|job_start/.test(c.method)), "nothing runs");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
