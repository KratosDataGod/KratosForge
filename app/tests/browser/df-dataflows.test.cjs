// Data Factory dataflows (DF-7, Preview): the Dataflows tab lists a workspace's dataflows and shows the
// one picked: its M queries (hidden ones folded away), where each loads, and its refreshes. Reads only.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const WS = "11111111-2222-3333-4444-555555555555";
const DF = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

const DETAIL = {
  ok: true, problems: [], mashup: "", connections: [{ kind: "SQL", path: "erp.contoso.local;erp", connection_id: "c1" }],
  queries: [
    { name: "Products", m: "let\n  Source = Sql.Database(\"erp\", \"erp\")\nin\n  Source", load_enabled: true, hidden: false,
      destination: { query: "Products_DataDestination", kind: "Lakehouse", workspace_id: WS, item_id: "lh", schema: "sales", table: "products",
        update_method: "Replace", new_table: false } },
    { name: "Helper", m: "1", load_enabled: false, hidden: false, destination: null },
    { name: "Products_DataDestination", m: "let Pattern = Lakehouse.Contents(null) in Pattern", load_enabled: false, hidden: true, destination: null },
  ],
};

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    mocks: {
      sign_in: () => ({ user: "chris@contoso.com", tenant: "contoso" }),
      list_workspaces: () => [{ id: WS, displayName: "Sales Dev" }],
      workspace_dataflows: () => ({ workspace_id: WS, dataflows: [
        { workspace_id: WS, item_id: DF, name: "df_products_clean", description: "Cleans products", folder_id: "" },
        { workspace_id: WS, item_id: "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee", name: "df_broken", description: "", folder_id: "" }] }),
      workspace_dataflow_definition: (p) => {
        if (p.item_id !== DF) throw new Error("GET https://api.fabric.microsoft.com/... failed with 403: {}");
        return { workspace_id: WS, item_id: DF, name: p.name, ...DETAIL };
      },
      fabric_dataflow_refreshes: () => ({ workspace_id: WS, item_id: DF, refreshes: [
        { job_id: "j2", state: "failed", native_status: "Failed", started: "2026-10-25T06:00:00Z", seconds: 134, invoke: "Scheduled",
          message: "Credentials expired" },
        { job_id: "j1", state: "succeeded", native_status: "Completed", started: "2026-10-24T06:00:00Z", seconds: 122, invoke: "Manual", message: "" }] }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof renderDfDataflows === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("signed out, the tab offers sign-in, then lists a workspace's dataflows", { skip }, async () => {
  await page().evaluate(() => { state.user = null; state.workspaces = null; plPage.area = "pipelines"; go("pipelines"); });
  await page().locator('.df-areas [role="tab"]', { hasText: "Dataflows" }).click();
  await page().locator(".df-flow-body button", { hasText: "Sign in to list dataflows" }).click();
  await page().selectOption('.df-ws select[aria-label="Workspace"]', { label: "Sales Dev" });
  await page().locator(".df-flow-row", { hasText: "df_products_clean" }).waitFor();
  assert.deepStrictEqual(await page().locator(".df-flow-row b").allTextContents(), ["df_products_clean", "df_broken"]);
});

test("a dataflow shows its queries, hidden ones folded, and where each loads", { skip }, async () => {
  await page().locator(".df-flow-row", { hasText: "df_products_clean" }).click();
  await page().locator(".df-query", { hasText: "Products" }).first().waitFor();
  const call = app.calls.find((c) => c.method === "workspace_dataflow_definition").params;
  assert.deepStrictEqual(call, { workspace_id: WS, item_id: DF, name: "df_products_clean" });
  const shown = await page().locator(".df-queries > .df-query summary").allTextContents();
  assert.deepStrictEqual(shown, ["Products Loads to Lakehouse · sales.products (replace)", "Helper Not loaded"]);
  assert.match(await page().locator(".df-hidden > summary").textContent(), /Hidden queries \(1\)/);
  await page().locator(".df-queries > .df-query summary", { hasText: "Products" }).click();
  assert.match(await page().locator(".df-queries > .df-query[open] .df-m").textContent(), /Sql\.Database/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "df-dataflows.png") });
  await page().locator('[role="tab"]', { hasText: "Destinations" }).click();
  assert.match(await page().locator(".df-dest-table tbody").textContent(), /ProductsLakehouseSales Devsales\.productsReplace/);
  assert.match(await page().locator(".df-conns").textContent(), /SQL erp\.contoso\.local;erp/);
});

test("Refresh history lists Fabric's refreshes, newest first", { skip }, async () => {
  await page().locator('[role="tab"]', { hasText: "Refresh history" }).click();
  const rows = await page().locator(".df-refresh-table tbody tr").allTextContents();
  assert.match(rows[0], /^Failed.*2m 14s.*scheduled.*Credentials expired$/);
  assert.match(rows[1], /^Succeeded.*2m 2s.*manual/);
});

test("a dataflow Fabric won't give says why; Open in Fabric goes to its workspace", { skip }, async () => {
  await page().locator('[role="tab"]', { hasText: "Queries (M)" }).click();
  await page().locator(".df-flow-row", { hasText: "df_broken" }).click();
  await page().locator(".df-detail .error-text", { hasText: "Could not read df_broken" }).waitFor();
  await page().evaluate(() => { window.openInFabric = (url) => { window.openedUrl = url; }; });
  await page().locator(".df-detail-head button", { hasText: "Open in Fabric" }).click();
  assert.match(await page().evaluate(() => window.openedUrl), new RegExp(`/groups/${WS}/`));
  assert.ok(!app.calls.some((c) => /refresh_start|run_item|dataflow_refresh$/.test(c.method)), "nothing refreshes");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
