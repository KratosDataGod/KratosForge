// AF-5 (docs/ux/action-first.md): before a shared lakehouse is connected, Optimize's Overview acts on
// this PC's last Estate X-ray scan, most severe first with the X-ray's own fix, and reads the saved scan
// without starting one. Creating a lakehouse is no longer the page's solid button (see harness.cjs).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const where = { workspace_id: "w1", workspace_name: "Sales", lakehouse_id: "lh1", lakehouse_name: "LH_Silver" };
const finding = (id, severity, kind, action, title, extra = {}) =>
  ({ id, kind, severity, action, title, detail: `${title}, from the Delta log.`, fixed: false, fix: null, ...where, table: id, ...extra });
let scan;
const fresh = () => ({ running: false, result: { scanned: "2026-10-26T08:00:00+00:00", workspaces: 1, lakehouses: 1, tables: 9, bytes: 1,
  findings: [
    finding("events", "low", "vacuum", "vacuum", "events keeps old files"),
    finding("orders", "high", "small_files", "optimize", "orders has many small files"),
    finding("daily", "medium", "cost", "run_local", "daily could run on this PC", { notebook: "Daily", lakehouse_name: null }),
    finding("gone", "high", "small_files", "optimize", "gone was fixed", { fixed: true }),
  ] } });

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["cost_report", "app_settings", "save_app_settings"], mocks: {
    optimize_status: () => ({ allowed: true, default_name: "FabricDesktop_Central", active: null, connections: [] }),
    xray: () => scan,
    xray_vacuum_preview: () => ({ files: 12, bytes: 2048, keep_days: 7, why: "the default" }),
    xray_vacuum: () => ({ job_id: "j1", keep_days: 7 }),
  } });
  await page().waitForFunction(() => typeof renderOptimize === "function" && !!state.settings);
});
after(async () => { await app?.close(); });

async function overview(user = "chris@contoso.com") {
  await page().evaluate((u) => { state.user = u; optimizePage.tab = "overview"; go("optimize"); }, user);
  await page().waitForSelector(".optimize-setup");
}

test("Overview lists this PC's open X-ray findings, most severe first, each with its fix", { skip }, async () => {
  scan = fresh();
  app.calls.length = 0;
  await overview();
  await page().waitForSelector(".optimize-local tbody tr");
  const rows = await page().$$eval(".optimize-local tbody tr", (trs) => trs.map((tr) => [...tr.children].map((td) => td.textContent.trim())));
  const titles = await page().$$eval(".optimize-local tbody tr td:first-child b", (bs) => bs.map((b) => b.textContent));
  assert.deepStrictEqual(titles, ["orders has many small files", "daily could run on this PC", "events keeps old files"]);
  assert.deepStrictEqual(rows.map((r) => r[1]), ["High", "Medium", "Low"]);
  assert.deepStrictEqual(rows.map((r) => r[3]), ["Optimize", "Open notebook", "Vacuum"]);
  assert.match(await page().textContent(".optimize-local"), /From this PC's last Estate X-ray scan/);
  // Reads the saved scan only.
  assert.ok(app.calls.filter((c) => c.method === "xray").every((c) => !c.params.refresh && !c.params.auto));
  // Creating a lakehouse is still there, outlined.
  const solid = await page().$$eval(".page.optimize button.primary", (bs) => bs.map((b) => b.textContent.trim()));
  assert.deepStrictEqual(solid, ["Optimize"]);
  assert.ok(await page().$('.optimize-setup button.outline:has-text("Create a lakehouse")'));
});

test("a fix started from Optimize shows as submitted in its row", { skip }, async () => {
  scan = fresh();
  await overview();
  await page().waitForSelector('.optimize-local tr[data-finding="events"]');
  await page().click('.optimize-local tr[data-finding="events"] button:has-text("Vacuum")');
  await page().waitForSelector('.dialog button:has-text("Vacuum"):not([disabled])');
  await page().click('.dialog button:has-text("Vacuum"):not([disabled])');
  await page().waitForFunction(() => document.querySelector('.optimize-local tr[data-finding="events"]')?.textContent.includes("Fix submitted"));
  assert.strictEqual(await page().$('.optimize-local tr[data-finding="events"] button'), null);
  assert.ok(app.calls.some((c) => c.method === "xray_vacuum" && c.params.table === "events"));
  assert.ok(await page().$(".page.optimize"), "the X-ray card replaced the page");
  assert.deepStrictEqual(app.errors, []);
});

test("signed out or never scanned, it says how to get findings and starts nothing", { skip }, async () => {
  app.calls.length = 0;
  await overview(null);
  assert.match(await page().textContent(".optimize-local"), /Sign in and scan with the Estate X-ray on Home/);
  assert.ok(!app.calls.some((c) => c.method === "xray"));
  scan = { running: false, result: null };
  await overview();
  await page().waitForFunction(() => /No Estate X-ray scan on this PC yet/.test(document.querySelector(".optimize-local")?.textContent || ""));
  assert.ok(await page().$('.optimize-local button:has-text("Scan on Home")'));
});
