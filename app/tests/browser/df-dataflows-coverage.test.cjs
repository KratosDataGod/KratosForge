// Data Factory dataflows (DF-7, Preview) beyond df-dataflows.test.cjs: no workspaces, a remembered
// workspace listed at once, a list Fabric won't give, a workspace with no dataflows, a dataflow with
// no queries, no loads and no connections, one loading into another workspace, refreshes Fabric
// won't give or has none of, a state the app doesn't know, and Refresh in Fabric for a workspace
// Workloads doesn't list yet (Pin and read again). The engine's replies are mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WS = { id: "ws-a", displayName: "Sales Dev" };
const EMPTY_WS = { id: "ws-b", displayName: "Sandbox" };
const BROKEN_WS = { id: "ws-c", displayName: "Finance" };
const BARE = { workspace_id: WS.id, item_id: "df-bare", name: "df_bare", description: "", folder_id: "" };
const ELSEWHERE = { workspace_id: WS.id, item_id: "df-else", name: "df_elsewhere", description: "Feeds Finance", folder_id: "" };

const db = { workspaces: [] };
let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const body = () => page().locator(".df-flow-body");

before(async () => {
  if (skip) return;
  app = await openApp({
    storage: { "fd.pipelines.workspace": WS.id },
    mocks: {
      readiness_check: () => [],
      list_workspaces: () => db.workspaces,
      workspace_dataflows: ({ workspace_id }) => {
        if (workspace_id === BROKEN_WS.id) throw new Error("GET https://api.fabric.microsoft.com/... failed with 403: {}");
        return { workspace_id, dataflows: workspace_id === WS.id ? [BARE, ELSEWHERE] : [] };
      },
      workspace_dataflow_definition: ({ item_id }) => (item_id === BARE.item_id
        ? { ok: true, problems: ["The mashup names a parameter that has no value: Region."], mashup: "", connections: [],
          queries: [{ name: "Region_Helper", m: "\"East\"", load_enabled: false, hidden: true, destination: null }] }
        : { ok: true, problems: [], mashup: "", connections: [{ kind: "", path: "https://contoso.sharepoint.com/sites/fin" }],
          queries: [
            { name: "Budget", m: "let S = SharePoint.Files(\"x\") in S", load_enabled: true, hidden: false,
              destination: { kind: "Warehouse", workspace_id: "ws-fin", item_id: "wh", schema: "", table: "budget", update_method: "Append", new_table: true } },
            { name: "Staging", m: "1", load_enabled: true, hidden: false, destination: null },
            { name: "Loose", m: "2", load_enabled: false, hidden: false,
              destination: { kind: "", workspace_id: "", item_id: "", schema: "", table: "", update_method: "", new_table: false } }] }),
      fabric_dataflow_refreshes: ({ item_id }) => {
        if (item_id === BARE.item_id) throw new Error("GET https://api.fabric.microsoft.com/... failed with 429: Too many requests");
        return { refreshes: [{ job_id: "j1", state: "deduped", native_status: "Deduped", started: "2026-10-25T06:00:00Z", seconds: null, invoke: "", message: "" }] };
      },
      workload_preflight: () => ({ allowed: false, code: "not_in_inventory", reason: "Workloads doesn't list it." }),
      set_workspace_mark: () => ({ ok: true }),
      workloads_list: () => ({ workloads: [] }),
    },
  });
  await page().waitForFunction(() => typeof renderDfDataflows === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

const openTab = () => page().evaluate(() => { dfFlowPage.workspace = null; dfFlowPage.list = null; plPage.area = "dataflows"; go("pipelines"); });

test("signed in with no workspaces, the tab says there are none", { skip }, async () => {
  await page().evaluate(() => { state.user = "chris@contoso.com"; state.workspaces = null; });
  await openTab();
  await body().getByText("No workspaces to list.").waitFor();
});

test("the remembered workspace lists at once; a dataflow with nothing in it says so on every tab", { skip }, async () => {
  db.workspaces = [WS, EMPTY_WS, BROKEN_WS];
  await page().evaluate(() => { state.workspaces = null; });
  await openTab();
  await page().locator(".df-flow-row", { hasText: "df_bare" }).waitFor();
  assert.deepStrictEqual(calls("workspace_dataflows"), [{ workspace_id: WS.id }]);
  assert.strictEqual(await page().locator('.df-ws select[aria-label="Workspace"]').inputValue(), WS.id);
  assert.match(await page().locator(".df-flow-main").innerText(), /Pick a dataflow to see its queries\./);
  await page().locator(".df-flow-row", { hasText: "df_bare" }).click();
  await page().locator(".df-queries").waitFor();
  const queries = await page().locator(".df-flow-main").innerText();
  assert.match(queries, /The mashup names a parameter that has no value: Region\.\s+No queries\.\s+Hidden queries \(1\)/);
  await page().locator('[role="tab"]', { hasText: "Destinations" }).click();
  assert.match(await page().locator(".df-flow-main").innerText(),
    /No query loads anywhere: this dataflow only feeds other queries or items\.\s+Connections\s+No connections named\./);
  await page().locator('[role="tab"]', { hasText: "Refresh history" }).click();
  await page().locator(".df-flow-main .error-text", { hasText: /^Could not read refreshes: / }).waitFor();
  assert.strictEqual(await page().locator(".df-flow-main .error-text").innerText(),
    "Could not read refreshes: Fabric is busy and asked the app to slow down. Try again in a minute.");
});

test("a dataflow loading into another workspace, an unnamed connection and a refresh state the app doesn't know", { skip }, async () => {
  await page().evaluate(() => { dfFlowPage.tab = "queries"; });
  await page().locator(".df-flow-row", { hasText: "df_elsewhere" }).click();
  await page().locator(".df-queries").waitFor();
  assert.deepStrictEqual(await page().locator(".df-queries > .df-query summary").allTextContents(), [
    "Budget Loads to Warehouse · budget (append, new table)",
    "Staging Loads (no destination set)",
    "Loose Loads to Unknown destination"]);
  assert.match(await page().locator(".df-flow-main").innerText(), /Feeds Finance/);
  await page().locator('[role="tab"]', { hasText: "Destinations" }).click();
  const rows = await page().locator(".df-dest-table tbody tr").allInnerTexts();
  assert.deepStrictEqual(rows.map((r) => r.replace(/\s+/g, " ").trim()), [
    "Budget Warehouse Another workspace budget Append · new table", "Loose Unknown — —"]);
  assert.strictEqual((await page().locator(".df-conns li").innerText()).trim(), "Connection https://contoso.sharepoint.com/sites/fin");
  await page().locator('[role="tab"]', { hasText: "Refresh history" }).click();
  const cells = await page().locator(".df-refresh-table tbody td").allInnerTexts();
  assert.deepStrictEqual([cells[0], cells[2], cells[3], cells[4]], ["Deduped", "—", "—", ""]);
});

test("Refresh in Fabric for a workspace Workloads doesn't list offers Pin and read again", { skip }, async () => {
  await page().locator(".df-detail-head button", { hasText: "Refresh in Fabric" }).click();
  const dialog = page().getByRole("dialog", { name: "Refresh in Fabric df_elsewhere?" });
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /It doesn't list df_elsewhere yet: Sales Dev isn't one of the workspaces this app works in/);
  await dialog.getByRole("button", { name: "Pin and read again" }).click();
  await page().waitForFunction(() => document.getElementById("toast").textContent === "Reading the items in Sales Dev. Try Refresh in Fabric again in a minute.");
  assert.deepStrictEqual(calls("workload_preflight"), [{ id: `fabric:${WS.id}:${ELSEWHERE.item_id}`, action: "run_item", env: null }]);
  assert.deepStrictEqual(calls("set_workspace_mark"), [{ workspace_id: WS.id, mark: "pins", on: true }]);
  assert.ok(calls("workloads_list").some((c) => c.limit === 1 && c.refresh === true), "the items are read again");
  assert.ok(!app.calls.some((c) => c.method === "workload_act"), "nothing started");
});

test("a workspace with no dataflows, and one Fabric won't list", { skip }, async () => {
  await page().selectOption('.df-ws select[aria-label="Workspace"]', { label: "Sandbox" });
  await body().getByText("Sandbox has no dataflows.").waitFor();
  await page().selectOption('.df-ws select[aria-label="Workspace"]', { label: "Finance" });
  await body().locator(".error-text", { hasText: /^Could not list dataflows: / }).waitFor();
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.pipelines.workspace")), BROKEN_WS.id);
  assert.deepStrictEqual(app.errors, []);
});
