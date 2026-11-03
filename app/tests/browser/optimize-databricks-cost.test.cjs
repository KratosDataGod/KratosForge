// Optimize > Cost: Databricks DBUs (Preview, dbx-cost.js). The last read shows without reading
// again; Read Databricks billing asks first, naming the warehouse and its state, and runs the query
// only after the confirm; Cancel runs nothing.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const HOST = "https://adb-1234567890123456.7.azuredatabricks.net";
const LAST = { ok: true, schema: "databricks-billing@1", days: 7, warehouse_id: "wh-run", read_at: new Date(Date.now() - 3_600_000).toISOString(),
  total_dbu: 84.6, rows: [{ kind: "warehouse", id: "wh-run", sku: "PREMIUM_SQL_PRO", dbu: 51 },
    { kind: "job", id: "1", sku: "PREMIUM_JOBS_COMPUTE", dbu: 22.4 }, { kind: "pipeline", id: "pl-1", sku: "PREMIUM_DLT", dbu: 11.2 }] };

let app;
let saved = null;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 1000 },
    engine: ["app_settings", "cost_report", "optimize_status"],
    mocks: {
      engine_connections: () => ({ connections: [{ key: "cli:dev", label: "dev (Databricks CLI)", source: "cli", host: HOST, warehouse_id: "wh-run",
        cluster_id: "", catalog: "", schema: "", remote_compute: "ask" }] }),
      databricks_billing_last: () => saved || {},
      databricks_billing: (p) => {
        if (!p.confirmed) return { needs_confirm: true, warehouse: { id: "wh-run", name: "Shared SQL", state: "STOPPED", auto_stop_mins: 10 } };
        saved = LAST;
        return LAST;
      },
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof databricksCostSection === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

const billingCalls = () => app.calls.filter((c) => c.method === "databricks_billing").map((c) => c.params);

test("the Databricks section says it hasn't been read and offers the connection's warehouse", { skip }, async () => {
  await page().evaluate(() => { optimizePage.tab = "cost"; go("optimize"); });
  await page().locator(".dbx-cost", { hasText: "Not read yet on this PC" }).waitFor({ timeout: 10000 });
  assert.strictEqual(await page().locator('.dbx-cost input[aria-label="SQL warehouse id"]').inputValue(), "wh-run");
  assert.match(await page().locator(".dbx-cost").textContent(), /never added to Fabric CU or Snowflake credits/);
});

test("Read billing asks first, and Cancel runs nothing", { skip }, async () => {
  await page().locator(".dbx-cost button", { hasText: "Read Databricks billing" }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(),
    /Read billing on Shared SQL\?.*one query on the SQL warehouse Shared SQL\. If it is stopped it starts, and it bills DBUs while it runs\. It is stopped now and stops after 10 minutes idle\./);
  await page().locator("#dialog-root button", { hasText: "Cancel" }).click();
  assert.deepStrictEqual(billingCalls(), [{ connection: "cli:dev", warehouse_id: "wh-run", days: 7 }]);
});

test("after the confirm the query runs once and the DBUs show by what used them", { skip }, async () => {
  await page().locator(".dbx-cost button", { hasText: "Read Databricks billing" }).click();
  await page().locator("#dialog-root .dialog button", { hasText: "Run the query" }).click();
  await page().locator(".dbx-dbu").waitFor({ timeout: 10000 });
  assert.deepStrictEqual(billingCalls().filter((p) => p.confirmed), [{ connection: "cli:dev", warehouse_id: "wh-run", days: 7, confirmed: true }]);
  assert.match(await page().locator(".dbx-cost-result").textContent(), /84\.6 DBU in the last 7 days · read 1 h ago on wh-run/);
  const rows = await page().locator(".dbx-dbu tbody tr").allTextContents();
  assert.deepStrictEqual(rows, ["SQL warehouse wh-runPREMIUM_SQL_PRO51", "Job 1PREMIUM_JOBS_COMPUTE22.4", "Lakeflow pipeline pl-1PREMIUM_DLT11.2"]);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "optimize-databricks-cost.png"), fullPage: true });
});

test("the Databricks DBU section has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  assert.deepStrictEqual(await axeFindings(page(), "#view .dbx-cost"), []);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
