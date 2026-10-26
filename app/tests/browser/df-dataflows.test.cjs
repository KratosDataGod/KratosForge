// Data Factory dataflows (DF-7, Preview): the Dataflows tab lists a workspace's dataflows and shows the
// one picked: its M queries (hidden ones folded away), where each loads, and its refreshes. Refresh in
// Fabric goes through the Workloads confirm; nothing here reaches Fabric.
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

const acted = [];
let risk = null;
let signedOut = false;
const PRE = { allowed: true, label: "Run in Fabric", workload: "df_products_clean", env_label: "", where: "Fabric · Sales Dev",
  identity: "You (chris@contoso.com): a run started here runs as the person who starts it", target: "df_products_clean in Sales Dev",
  consequence: "Starts dataflow Gen2 df_products_clean in Fabric now, on its workspace's capacity, exactly as Fabric's own Run does. "
    + "The refresh writes its results to every destination the dataflow has.",
  warnings: [], recorded: "Recorded on this workload as submitted, then as succeeded or failed when the run finishes." };

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    mocks: {
      sign_in: () => { signedOut = false; return { user: "chris@contoso.com", tenant: "contoso" }; },
      list_workspaces: () => [{ id: WS, displayName: "Sales Dev" }],
      workspace_dataflows: () => ({ workspace_id: WS, dataflows: [
        { workspace_id: WS, item_id: DF, name: "df_products_clean", description: "Cleans products", folder_id: "" },
        { workspace_id: WS, item_id: "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee", name: "df_broken", description: "", folder_id: "" }] }),
      workspace_dataflow_definition: (p) => {
        if (p.item_id !== DF) throw new Error("GET https://api.fabric.microsoft.com/... failed with 403: {}");
        return { workspace_id: WS, item_id: DF, name: p.name, ...DETAIL };
      },
      workload_preflight: () => (signedOut ? { allowed: false, reason: "Sign in first: this action uses Fabric." } : { ...PRE, risk }),
      workload_act: (p) => { acted.push(p); return { action: { status: "submitted" }, result: { job_id: "j3" } }; },
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

test("Refresh in Fabric asks first, shows the risk check, and a blocked check starts nothing", { skip }, async () => {
  risk = { id: "r-blocked", result: "blocked", label: "Blocked", summary: "This run can't start: fabcapdev is paused", can_proceed: false,
    needs_acknowledgement: false, note: "Read only: nothing was started.", reads: [],
    findings: [{ check: "capacity", outcome: "blocked", label: "Blocked", title: "The capacity is paused", detail: "Fabric refuses new jobs until an admin resumes it.",
      evidence: [], remedy: "", source: "", observed_at: null, essential: false, measurement: null, can_override: false }] };
  await page().locator(".df-detail-head button", { hasText: "Refresh in Fabric…" }).click();
  const dialog = page().locator(".dialog", { hasText: "Refresh in Fabric df_products_clean?" });
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /What happensStarts dataflow Gen2 df_products_clean.*every destination the dataflow has/);
  assert.match(await dialog.textContent(), /The capacity is paused/);
  assert.strictEqual(await dialog.locator("button", { hasText: /^Refresh in Fabric$/ }).count(), 0, "a blocked check offers no start");
  await dialog.locator("button", { hasText: "Close" }).last().click();
  assert.strictEqual(acted.length, 0);
});

test("with the check clear, Refresh in Fabric starts the refresh once", { skip }, async () => {
  risk = null;
  await page().locator(".df-detail-head button", { hasText: "Refresh in Fabric…" }).click();
  await page().locator(".wl-confirm").waitFor();
  await page().locator(".dialog button", { hasText: /^Refresh in Fabric$/ }).click();
  await page().waitForFunction(() => !document.querySelector(".wl-confirm"));
  assert.deepStrictEqual(acted, [{ id: `fabric:${WS}:${DF}`, action: "run_item", env: null, schedule_id: "", job_id: "" }]);
});

test("signing in from the confirm carries on to the same refresh, and reads the refreshes after", { skip }, async () => {
  signedOut = true;
  const before = app.calls.filter((c) => c.method === "fabric_dataflow_refreshes").length;
  await page().locator(".df-detail-head button", { hasText: "Refresh in Fabric…" }).click();
  await page().locator(".dialog button", { hasText: "Sign in" }).click();
  await page().locator(".wl-confirm").waitFor();
  assert.ok(await page().locator(".dialog button", { hasText: /^Refresh in Fabric$/ }).count(), "the verb is kept");
  await page().locator(".dialog button", { hasText: /^Refresh in Fabric$/ }).click();
  await page().waitForFunction(() => !document.querySelector(".wl-confirm"));
  assert.strictEqual(acted.length, 2);
  for (const end = Date.now() + 5000; app.calls.filter((c) => c.method === "fabric_dataflow_refreshes").length <= before;) {
    if (Date.now() > end) throw new Error("the refreshes weren't read again");
    await new Promise((r) => setTimeout(r, 25));
  }
});

test("a dataflow Fabric won't give says why; Open in Fabric goes to its workspace", { skip }, async () => {
  await page().locator('[role="tab"]', { hasText: "Queries (M)" }).click();
  await page().locator(".df-flow-row", { hasText: "df_broken" }).click();
  await page().locator(".df-detail .error-text", { hasText: "Could not read df_broken" }).waitFor();
  await page().evaluate(() => { window.openInFabric = (url) => { window.openedUrl = url; }; });
  await page().locator(".df-detail-head button", { hasText: "Open in Fabric" }).click();
  assert.match(await page().evaluate(() => window.openedUrl), new RegExp(`/groups/${WS}/`));
  assert.strictEqual(app.calls.filter((c) => c.method === "workload_act").length, 2, "only the refreshes asked for");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
