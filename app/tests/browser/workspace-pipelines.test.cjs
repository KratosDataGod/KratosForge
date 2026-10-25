// Open a pipeline from a Fabric workspace (Data Factory DF-2, Preview): the open dialog lists a
// workspace's pipelines, opening one reads its definition and shows it on the Pipelines page with Open
// in Fabric, and the definition's SecureString default never reaches localStorage or the console.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const WS = "11111111-2222-3333-4444-555555555555";
const PL = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const BROKEN = "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee";
const DEFINITION = fs.readFileSync(path.join(__dirname, "..", "..", "..", "sidecar", "tests", "pipelines",
  "orders_daily.item-definition.json"), "utf8");
// The definition's parts as Fabric sends them (base64), and the SecureString default inside one.
const PAYLOADS = JSON.parse(DEFINITION).definition.parts.map((p) => p.payload);
assert.ok(PAYLOADS.some((b) => Buffer.from(b, "base64").toString().includes("not-a-real-secret")));
const leaks = (text) => text.includes("not-a-real-secret") || PAYLOADS.some((b) => text.includes(b));

let app;
const page = () => app.page;
const logs = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    engine: ["pipeline_list", "pipeline_import", "pipeline_compatibility", "pipeline_run_check", "pipeline_runs"],
    mocks: {
      sign_in: () => ({ user: "chris@contoso.com", tenant: "contoso" }),
      list_workspaces: () => [{ id: WS, displayName: "Sales Dev" }, { id: "22222222-2222-3333-4444-555555555555", displayName: "Empty" }],
      workspace_pipelines: (p) => ({ workspace_id: p.workspace_id, pipelines: p.workspace_id !== WS ? [] : [
        { workspace_id: WS, item_id: BROKEN, name: "pl_broken", description: "", folder_id: "" },
        { workspace_id: WS, item_id: PL, name: "pl_orders_daily", description: "Loads orders", folder_id: "" }] }),
      workspace_pipeline_definition: (p) => {
        if (p.item_id === BROKEN) throw new Error("GET https://api.fabric.microsoft.com/... failed with 403: {}");
        return { ok: true, workspace_id: p.workspace_id, item_id: p.item_id, name: p.name, text: DEFINITION, pipeline: {} };
      },
    },
  });
  page().on("console", (m) => logs.push(m.text()));
  await page().waitForFunction(() => typeof go === "function" && typeof pipelineOpenDialog === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("signed out, the dialog offers sign-in, then lists the chosen workspace's pipelines", { skip }, async () => {
  await page().evaluate(() => { state.user = null; state.workspaces = null; go("pipelines"); pipelineOpenDialog(); });
  await page().locator(".pl-open-fabric button", { hasText: "Sign in to list workspace pipelines" }).click();
  await page().selectOption('.pl-open-fabric select[aria-label="Workspace"]', { label: "Empty" });
  await page().locator(".pl-open-fabric", { hasText: "Empty has no pipelines." }).waitFor();
  await page().selectOption('.pl-open-fabric select[aria-label="Workspace"]', { label: "Sales Dev" });
  await page().locator(".pl-open-fabric .pl-open-row", { hasText: "pl_orders_daily" }).waitFor();
  const rows = await page().locator(".pl-open-fabric .pl-open-row").allTextContents();
  assert.deepStrictEqual(rows.map((r) => r.replace(/In Fabric$/, "")), ["pl_brokenSales Dev", "pl_orders_dailyLoads orders"]);
});

test("a definition Fabric won't give says why and keeps the dialog open", { skip }, async () => {
  await page().locator(".pl-open-fabric .pl-open-row", { hasText: "pl_broken" }).click();
  await page().locator(".pl-open .error-text", { hasText: "Could not read pl_broken from Fabric" }).waitFor();
  assert.ok(await page().locator(".pl-open").isVisible());
});

test("opening one shows it on the Pipelines page with Open in Fabric", { skip }, async () => {
  await page().locator(".pl-open-fabric .pl-open-row", { hasText: "pl_orders_daily" }).click();
  await page().waitForSelector(".pl-graph");
  const call = app.calls.find((c) => c.method === "workspace_pipeline_definition" && c.params.item_id === PL).params;
  assert.deepStrictEqual(call, { workspace_id: WS, item_id: PL, name: "pl_orders_daily" });
  assert.match(await page().textContent(".pl-head"), /pl_orders_daily[\s\S]*From Sales Dev · Fabric workspace/);
  const graph = await page().textContent(".pl-graph");
  assert.match(graph, /Case "emea":[\s\S]*Case "amer":/, "Switch cases read as their values, as in Fabric");
  assert.doesNotMatch(graph, /cases\[/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "pipelines-from-workspace.png") });
  await page().evaluate(() => { window.openInFabric = (url) => { window.openedUrl = url; }; });
  await page().locator(".pl-actions button", { hasText: "Open in Fabric" }).click();
  assert.match(await page().evaluate(() => window.openedUrl), new RegExp(`/groups/${WS}/pipelines/${PL}`));
});

test("the definition stays in memory: never in localStorage or the console", { skip }, async () => {
  const stored = await page().evaluate(() => Object.keys(localStorage).map((k) => `${k}=${localStorage.getItem(k)}`).join("\n"));
  assert.ok(!leaks(stored), "no definition in localStorage");
  assert.match(stored, new RegExp(`fd\\.pipelines\\.workspace=${WS}`));
  assert.ok(!logs.some(leaks), "no definition in the console");
  // The next open goes straight to the workspace picked last time.
  await page().evaluate(() => pipelineOpenDialog());
  await page().locator(".pl-open-fabric .pl-open-row", { hasText: "pl_orders_daily" }).waitFor();
  await page().evaluate(() => closeDialog());
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
