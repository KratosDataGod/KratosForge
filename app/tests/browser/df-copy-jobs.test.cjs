// Data Factory copy jobs (DF-6, Preview): the Copy jobs tab lists a workspace's copy jobs and shows the
// one picked: source, destination, each table, whether this PC could run it, and its runs. Run in Fabric
// goes through the Workloads confirm (workload_preflight, then workload_act); nothing here reaches Fabric.
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

const acted = [];
const pinned = [];
const PRE = { allowed: true, label: "Run in Fabric", workload: "cj_bronze_orders", env_label: "", where: "Fabric · Sales Dev",
  identity: "You (chris@contoso.com): a run started here runs as the person who starts it", target: "cj_bronze_orders in Sales Dev",
  consequence: "Starts copy job cj_bronze_orders in Fabric now, on its workspace's capacity, exactly as Fabric's own Run does. It moves data.",
  warnings: [], risk: null, recorded: "Recorded on this workload as submitted, then as succeeded or failed when the run finishes." };

let app;
const page = () => app.page;
async function until(ok, ms = 5000) {
  for (const end = Date.now() + ms; !ok(); await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("timed out waiting");
  }
}

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
      workload_preflight: (p) => {
        if (p.id === `fabric:${WS}:${CJ}`) return PRE;
        return { allowed: false, code: "not_in_inventory", reason: "That workload isn't in the inventory any more." };
      },
      workload_act: (p) => { acted.push(p); return { action: { status: "submitted" }, result: { job_id: "j3" } }; },
      set_workspace_mark: (p) => { pinned.push(p); return { favorites: [], pins: [p.workspace_id] }; },
      workloads_list: () => ({ rows: [], scanning: true }),
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
  assert.ok(!app.calls.some((c) => /workload_act|run_item|job_start/.test(c.method)), "nothing runs");
});

test("Run in Fabric asks first through the Workloads confirm, then starts it and reads the runs again", { skip }, async () => {
  await page().locator(".df-flow-row", { hasText: "cj_bronze_orders" }).click();
  await page().locator(".df-copy-table").waitFor();
  const runsBefore = app.calls.filter((c) => c.method === "fabric_copy_job_runs").length;
  await page().locator(".df-detail-head button", { hasText: "Run in Fabric…" }).click();
  const dialog = page().locator(".wl-confirm");
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /Runs onFabric · Sales Dev.*AsYou.*Targetcj_bronze_orders in Sales Dev.*What happensStarts copy job cj_bronze_orders.*It moves data/);
  assert.strictEqual(acted.length, 0, "nothing starts before the confirm");
  await page().locator(".dialog button", { hasText: /^Run in Fabric$/ }).click();
  await page().waitForFunction(() => document.querySelectorAll(".wl-confirm").length === 0);
  assert.deepStrictEqual(acted, [{ id: `fabric:${WS}:${CJ}`, action: "run_item", env: null, schedule_id: "", job_id: "" }]);
  await until(() => app.calls.filter((c) => c.method === "fabric_copy_job_runs").length > runsBefore);  // the runs are read again
});

test("a copy job Workloads doesn't list yet offers to pin its workspace, and starts nothing", { skip }, async () => {
  await page().locator(".df-flow-row", { hasText: "cj_erp_customers" }).click();
  await page().locator(".df-copy-table tbody", { hasText: "dbo.customers" }).waitFor();
  await page().locator(".df-detail-head button", { hasText: "Run in Fabric…" }).click();
  await page().locator(".dialog", { hasText: "doesn't list cj_erp_customers yet" }).waitFor();
  await page().locator(".dialog button", { hasText: "Pin and read again" }).click();
  await until(() => app.calls.some((c) => c.method === "workloads_list"));
  assert.deepStrictEqual(pinned, [{ workspace_id: WS, mark: "pins", on: true }]);
  assert.ok(app.calls.some((c) => c.method === "workloads_list" && c.params.refresh === true), "it reads the items again");
  assert.strictEqual(acted.length, 1, "nothing more started");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
