// Data Factory connections (DF-5, Preview): the Connections tab beside Pipelines lists the connections
// and what uses each one per environment, a missing connection first, and only reads.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const DEV = "11111111-2222-3333-4444-555555555555";
const TEST = "22222222-2222-3333-4444-555555555555";
const use = (ws, env, name, type = "pipeline") => ({ workspace_id: ws, workspace_name: env === "dev" ? "Sales Dev" : "Sales Test", env,
  item_id: `${name}-id`, item_name: name, item_type: type });

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  let scans = 0;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    mocks: {
      fabric_connections: () => {
        scans += 1;
        if (scans === 1) return { pending: true, connections: [], missing: [] };
        return { pending: false, scanned: new Date().toISOString(), items: 4, missed: 1, skipped: 0,
          workspaces: [{ id: DEV, name: "Sales Dev", env: "dev" }, { id: TEST, name: "Sales Test", env: "test" }],
          missing: [{ id: "5a1c7d2e-0000-4000-8000-0000000000ff", used_by: [use(TEST, "test", "pl_customer_load")] }],
          connections: [
            { id: "s1", name: "sql-erp-prod", type: "SQL", path: "erp.contoso.local", connectivity: "OnPremisesGateway", gateway_id: "gw-1",
              used_by: [use(DEV, "dev", "pl_customer_load"), use(DEV, "dev", "cj_erp_customers", "copy job")] },
            { id: "w1", name: "web-fx-rates", type: "Web", path: "", connectivity: "ShareableCloud", gateway_id: "", used_by: [] }] };
      },
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof renderDfConnections === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("the Connections tab says it's reading, then lists what uses each connection per environment", { skip }, async () => {
  await page().evaluate(() => { plPage.area = "pipelines"; plPage.pipeline = null; go("pipelines"); });
  await page().locator('.df-areas [role="tab"]', { hasText: "Connections" }).click();
  await page().locator(".df-conn-body", { hasText: "Reading connections" }).waitFor();
  await page().locator(".df-conn-table").waitFor({ timeout: 10000 });
  assert.deepStrictEqual(await page().locator(".df-conn-table th").allTextContents(), ["Connection", "Type", "Gateway", "Used by", "Dev", "Test"]);
  const rows = await page().locator(".df-conn-table tbody tr").allTextContents();
  assert.strictEqual(rows.length, 3);
  assert.match(rows[0], /^Unknown connection 5a1c7d2e.*pl_customer_load · pipeline—Missing$/);  // missing first
  assert.match(rows[1], /sql-erp-prod.*On-premises gateway.*cj_erp_customers · copy jobUsed by 2—$/);
  assert.match(rows[2], /web-fx-rates.*Cloud.*Nothing in these workspaces——$/);
  assert.match(await page().locator(".df-alert").textContent(), /1 connection is missing\. pl_customer_load in Test uses a connection you can't use/);
  assert.match(await page().locator(".df-conn-body").textContent(), /4 pipelines and copy jobs read in Sales Dev \(Dev\), Sales Test \(Test\) · 1 couldn't be read/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "df-connections.png") });
});

test("Manage connections and the item links open Fabric; Refresh asks for a new scan", { skip }, async () => {
  await page().evaluate(() => { window.openInFabric = (url) => { window.openedUrl = url; }; });
  await page().locator(".df-conn-head button", { hasText: "Manage connections in Fabric" }).click();
  assert.match(await page().evaluate(() => window.openedUrl), /app\.fabric\.microsoft\.com\/groups\/me\/gateways/);
  await page().locator(".df-conn-table button", { hasText: "cj_erp_customers" }).click();
  // No confirmed address for a copy job page yet, so its workspace opens.
  assert.match(await page().evaluate(() => window.openedUrl), new RegExp(`/groups/${DEV}/list`));
  await page().locator(".df-conn-head button", { hasText: "Refresh" }).click();
  for (let i = 0; i < 100 && !app.calls.some((c) => c.method === "fabric_connections" && c.params?.refresh === true); i++) await page().waitForTimeout(50);
  assert.ok(app.calls.some((c) => c.method === "fabric_connections" && c.params?.refresh === true));
});

test("the Pipelines tab goes back to the pipeline page", { skip }, async () => {
  await page().locator('.df-areas [role="tab"]', { hasText: "Pipelines" }).click();
  await page().locator("h1", { hasText: "Pipelines" }).waitFor();
  assert.strictEqual(await page().evaluate(() => plPage.area), "pipelines");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
