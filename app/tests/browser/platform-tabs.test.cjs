// Platform in four tabs, laid out as Data mesh is: Overview, Set up, Govern and Settings. Set up and Govern
// hold several pages, picked on the view row's switcher; no menus.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const overview = (p) => ({ env: p.env, counts: { domains: 0, subdomains: 0, projects: 0, teams: 0, key_tables: 0, keys: 0, products: 0, published: 0, requests: 2 },
  landing: { sources: [] }, problems: [], me: { admin: true }, needs_you: [], mine: [],
  status: { source: "settings", default_names: {}, environments: { dev: { kind: "pc" }, test: { kind: "pc" }, prod: { kind: "pc" } } }, recent: [] });
const asked = [];

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, mocks: {
    platform_whoami: () => ({ admin: true }), platform_overview: overview, readiness_check: () => [],
    platform_requests: (p) => { asked.push(p); return [{ id: 1, status: "waiting" }, { id: 2, status: "waiting" }]; },
    platform_domains: () => [], platform_glossary: () => [], landing_status: () => ({ sources: [] }), list_capacities: () => [] } });
});
after(async () => { if (app) await app.close(); });

const tabs = () => page().locator(".page > .subtabs > .tab");

test("four tabs: requests are on Data mesh now, and the old link opens them there", { skip }, async () => {
  await page().evaluate(() => { state.user = "chris@contoso.com"; goPlatform("overview"); });
  await page().waitForSelector(".plat-stats");
  assert.deepStrictEqual((await tabs().allTextContents()).map((t) => t.trim()), ["Overview", "Set up", "Govern", "Settings"]);
  assert.deepStrictEqual(asked, [], "Overview's own counts give the number: no second read of the requests");
  assert.strictEqual(await tabs().nth(0).getAttribute("aria-selected"), "true");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "platform-tabs.png") });
  await page().click('.plat-stat:has-text("Requests waiting")');
  await page().waitForFunction(() => state.view === "metadata" && state.meta.tab === "requests");
  await page().evaluate(() => goPlatform("requests"));
  await page().waitForFunction(() => state.view === "metadata" && state.meta.tab === "requests");
  asked.length = 0;
  await page().evaluate(() => goPlatform("overview"));
  await page().waitForSelector(".plat-stats");
});

const pages = () => page().locator(".mesh-view-row .seg.platform-pages button");

test("Set up opens its first page, with its three pages on the view row; no menus", { skip }, async () => {
  await tabs().nth(1).click();
  await page().waitForFunction(() => platformPage.tab === "landing");
  assert.strictEqual(await page().$('[aria-haspopup="menu"]'), null, "no tab opens a menu");
  assert.deepStrictEqual((await pages().allTextContents()).map((t) => t.trim()), ["Landing zone", "Business keys", "Glossary"]);
  await pages().nth(2).click();
  await page().waitForFunction(() => platformPage.tab === "glossary");
  assert.deepStrictEqual(asked, [], "no tab reads the requests now");
  assert.strictEqual(await tabs().nth(1).getAttribute("aria-selected"), "true");
  assert.strictEqual(await pages().nth(2).getAttribute("aria-pressed"), "true");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "platform-set-up.png") });
});

test("Govern holds Governance and Audit; Overview and Settings have no switcher; old links to any page still open it", { skip }, async () => {
  await tabs().nth(2).click();
  await page().waitForFunction(() => platformPage.tab === "governance");
  assert.deepStrictEqual((await pages().allTextContents()).map((t) => t.trim()), ["Governance", "Audit"]);
  await page().evaluate(() => goPlatform("glossary"));
  await page().waitForFunction(() => document.querySelector(".page > .subtabs .tab.active")?.textContent === "Set up");
  await page().evaluate(() => goPlatform("audit"));
  await page().waitForFunction(() => document.querySelector(".page > .subtabs .tab.active")?.textContent === "Govern"
    && document.querySelector(".platform-pages button.on")?.textContent === "Audit");
  await tabs().nth(3).click();
  await page().waitForFunction(() => platformPage.tab === "settings");
  assert.strictEqual(await pages().count(), 0);
  assert.deepStrictEqual(asked, []);
  assert.deepStrictEqual(app.errors, []);
});
