// Platform in five tabs (Cleaner KratosForge): Overview, Requests with its waiting count, Set up and
// Govern as menus that name the page you're on, and Settings. Every page is still one click from the menu.
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

test("five tabs, and Requests shows how many are waiting", { skip }, async () => {
  await page().evaluate(() => { state.user = "chris@contoso.com"; goPlatform("overview"); });
  await page().waitForFunction(() => document.querySelector(".subtabs .tab-count")?.textContent === "2");
  assert.deepStrictEqual((await tabs().allTextContents()).map((t) => t.trim()), ["Overview", "Requests2", "Set up", "Govern", "Settings"]);
  assert.deepStrictEqual(asked, [], "Overview's own counts give the number: no second read of the requests");
  assert.strictEqual(await tabs().nth(0).getAttribute("aria-current"), "page");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "platform-tabs.png") });
});

test("Set up opens a menu of its three pages, and the tab then names the page", { skip }, async () => {
  await page().locator(".subtabs .tab-menu").first().click();
  const menu = page().getByRole("menu");
  assert.deepStrictEqual(await menu.getByRole("menuitem").allTextContents(), ["Landing zone", "Business keys", "Glossary"]);
  await menu.getByRole("menuitem", { name: "Glossary" }).click();
  await page().waitForFunction(() => platformPage.tab === "glossary");
  assert.deepStrictEqual(asked, [], "the count is kept from Overview");
  const setUp = page().locator(".subtabs .tab-menu").first();
  assert.match(await setUp.textContent(), /Set up: Glossary/);
  assert.strictEqual(await setUp.getAttribute("aria-current"), "page");
});

test("Govern holds Governance and Audit; old links to any page still open it", { skip }, async () => {
  await page().locator(".subtabs .tab-menu").nth(1).click();
  assert.deepStrictEqual(await page().getByRole("menu").getByRole("menuitem").allTextContents(), ["Governance", "Audit"]);
  await page().keyboard.press("Escape");
  await page().evaluate(() => goPlatform("glossary"));
  await page().waitForFunction(() => /Set up: Glossary/.test(document.querySelector(".subtabs").textContent));
  // Opened on another tab with no count yet, the bar reads the waiting requests once.
  await page().evaluate(() => { platformPage.waiting = undefined; goPlatform("audit"); });
  await page().waitForFunction(() => document.querySelector(".subtabs .tab-count")?.textContent === "2");
  assert.deepStrictEqual(asked, [{ status: "waiting" }]);
  assert.deepStrictEqual(app.errors, []);
});
