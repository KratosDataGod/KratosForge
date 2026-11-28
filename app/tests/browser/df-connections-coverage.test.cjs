// Data Factory connections (df-connections.js) beyond df-connections.test.cjs: a scan that fails, a
// scan that stopped at its limit and couldn't read gateway names, a missing connection used by two
// items, and a scan that found no connections at all. fabric_connections is mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const DEV = "11111111-2222-3333-4444-555555555555";
let app;
const page = () => app.page;
let reply;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { fabric_connections: () => reply() } });
  await page().waitForFunction(() => typeof renderDfConnections === "function" && !!document.querySelector(".rail-item"));
});
after(async () => { await app?.close(); });

const body = () => page().locator(".df-conn-body");
const openTab = async () => {
  await page().evaluate(() => { plPage.area = "connections"; plPage.pipeline = null; go("pipelines"); render(); });
  await page().waitForFunction(() => !/Reading connections/.test(document.querySelector(".df-conn-body")?.textContent ?? "Reading connections"));
};
const done = (extra) => ({ pending: false, scanned: new Date().toISOString(), items: 0, missed: 0, skipped: 0,
  workspaces: [{ id: DEV, name: "Sales Dev", env: "dev" }], missing: [], connections: [], ...extra });

test("a scan Fabric refuses says the connections couldn't be read", { skip }, async () => {
  reply = () => { throw new Error("Fabric answered 403 Forbidden"); };
  await openTab();
  assert.match(await body().locator(".error-text").textContent(), /^Could not read connections: /);
  assert.strictEqual(await page().locator(".df-conn-table").count(), 0);
});

test("an empty scan says nothing names a connection, and the notes say what wasn't read", { skip }, async () => {
  reply = () => done({ items: 250, skipped: 40, gateways_error: "Forbidden" });
  await openTab();
  const note = await body().locator("p.small").textContent();
  assert.match(note, /250 pipelines, copy jobs and dataflows read in Sales Dev \(Dev\)/);
  assert.match(note, / · 40 more not read \(limit reached\) · Gateway names couldn't be read \(they need permission to read gateways\)$/);
  await body().getByText("No connections you can use, and nothing here names one.").waitFor();
});

test("a missing connection used by two items says they both fail, and the scan's error is shown when it has one", { skip }, async () => {
  const use = (name, type) => ({ workspace_id: DEV, workspace_name: "Sales Dev", env: "dev", item_id: `${name}-id`, item_name: name, item_type: type });
  reply = () => done({ items: 2, missing: [{ id: "9f00aa11-0000-4000-8000-000000000001",
    used_by: [use("pl_orders", "pipeline"), use("df_orders", "dataflow")] }] });
  await openTab();
  assert.strictEqual(await body().getByRole("alert").textContent(),
    "1 connection is missing. pl_orders, df_orders in Dev use a connection you can't use (deleted, or not shared with you), so they fail when you run them.");
  assert.strictEqual(await page().locator(".df-conn-table tbody tr.df-missing td").first().locator("b").textContent(), "Unknown connection 9f00aa11");

  reply = () => done({ error: "The scan stopped: the workspace was deleted." });
  await page().getByRole("button", { name: "Refresh" }).click();
  await body().locator(".error-text", { hasText: "Could not read connections: The scan stopped: the workspace was deleted." }).waitFor();
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "fabric_connections").at(-1).params, { refresh: true });
});
