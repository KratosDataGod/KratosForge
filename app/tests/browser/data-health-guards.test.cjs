// Data health: the guard paths the main test file doesn't reach. In an open table, Stop watching and
// Check again that the engine refuses show the reason as an error and stay on the table; when they
// work, the list comes back. A changed schema lists what was added, removed and retyped. Mocks only.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const RULE = { baseline: 20, minimum: 10, spreads: 3, floor: 0.1, text: "Usual is the median rows written by the last 20 loads." };
const loads = (rows) => rows.map((n, i) => ({ version: 10 + i, at: "2026-11-17T08:00:00Z", operation: "WRITE", mode: "Append", rows: n, run_id: null }));
const orders = {
  entity: "silver.orders", env: "dev", version: 17,
  freshness: { state: "fresh", within_hours: 24, age_hours: 1, last_version: 17 },
  volume: { state: "normal", loads: loads(Array(12).fill(100)), latest: { version: 17, rows: 100 },
    usual: { enough: true, have: 12, need: 10, median: 100, spread: 2, band: 6 }, unread: 0, rule: RULE },
  schema: { state: "changed", added: ["region"], removed: ["legacy_code"], retyped: [{ name: "amount", from: "int", to: "decimal(18,2)" }],
    version: 17, since_version: 16 },
  alerts: [],
};
const full = { env: "dev", tables: [orders], entities: ["silver.orders"], rule: RULE };
let fail = false;
const sent = [];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    meta_data_health_list: () => full,
    meta_data_health_watch: (p) => { sent.push(["watch", p]); if (fail) throw new Error("The metadata project is read-only"); return p; },
    meta_data_health: (p) => { sent.push(["check", p]); if (fail) throw new Error("Couldn't read the table's Delta log"); return orders; },
  } });
  await startupReads(app);
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

async function openOrders() {
  await page().evaluate(() => { dhPage.open = null; dhPage.env = "dev"; monitorPage.panel = "data_health"; go("home"); go("monitor"); });
  await page().waitForSelector(".page.monitor .dh-panel tbody tr");
  await page().locator(".page.monitor .dh-panel tbody tr", { hasText: "silver.orders" }).click();
  await page().waitForSelector(".page.monitor .dh-tiles");
}
const button = (name) => page().locator(".page.monitor .monitor-filters button", { hasText: name });

test("a changed schema lists what was added, removed and retyped", { skip }, async () => {
  await openOrders();
  const text = await page().textContent(".page.monitor");
  for (const part of ["Added: region.", "Removed: legacy_code.", "Retyped: amount."]) assert.ok(text.includes(part), part);
});

test("Stop watching the engine refuses says why and stays on the table", { skip }, async () => {
  fail = true;
  sent.length = 0;
  await openOrders();
  await button("Stop watching").click();
  await page().waitForFunction(() => document.getElementById("toast")?.textContent.includes("read-only"));
  assert.deepStrictEqual(sent, [["watch", { name: "silver.orders", env: "dev", watch: false }]]);
  assert.ok(await page().locator(".page.monitor .dh-tiles").isVisible(), "the table stays open");
  assert.strictEqual(await page().evaluate(() => dhPage.open !== null), true);
  fail = false;
});

test("Check again the engine refuses says why and stays on the table", { skip }, async () => {
  fail = true;
  sent.length = 0;
  await openOrders();
  await button("Check again").click();
  await page().waitForFunction(() => document.getElementById("toast")?.textContent.includes("Delta log"));
  assert.deepStrictEqual(sent, [["check", { name: "silver.orders", env: "dev" }]]);
  assert.ok(await page().locator(".page.monitor .dh-tiles").isVisible());
  fail = false;
});

test("Stop watching that works goes back to the list", { skip }, async () => {
  sent.length = 0;
  await openOrders();
  await button("Stop watching").click();
  await page().waitForFunction(() => dhPage.open === null);
  await page().waitForSelector(".page.monitor .dh-panel tbody tr");
  assert.deepStrictEqual(sent, [["watch", { name: "silver.orders", env: "dev", watch: false }]]);
});

test("Check again that works reads the table again and redraws the list", { skip }, async () => {
  sent.length = 0;
  await openOrders();
  await button("Check again").click();
  await page().waitForFunction(() => document.querySelector(".page.monitor .dh-panel"));
  assert.deepStrictEqual(sent, [["check", { name: "silver.orders", env: "dev" }]]);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
