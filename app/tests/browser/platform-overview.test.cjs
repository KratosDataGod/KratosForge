// Testing feedback item 23: Platform > Overview fits its cards, and the environment picker shows only
// where things are kept per environment (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const long = "chris.wagner@expertpcpros.onmicrosoft.com";
const overview = (p) => ({ env: p.env, counts: { domains: 9, subdomains: 1, projects: 0, teams: 0, key_tables: 0, keys: 0, products: 0, published: 0, requests: 0 },
  landing: { sources: [] }, problems: [], me: { admin: true }, needs_you: [], mine: [],
  status: { environments: { dev: { kind: "pc" }, test: { kind: "pc" }, prod: { kind: "fabric", workspace_name: "Platform - Prod" } } },
  recent: [{ at: "2026-10-18T10:00:00Z", by_user: long, action: "import domains from Fabric", target: long }] });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { platform_whoami: () => ({ admin: true }), platform_overview: overview, readiness_check: () => [],
    landing_status: () => ({ sources: [] }), platform_domains: () => [] } });
});
after(async () => { if (app) await app.close(); });

const open = async (tab) => {
  await page().evaluate((t) => { state.user = "chris@contoso.com"; goPlatform(t); }, tab);
  await page().waitForFunction(() => platformPage.me);
};
const headPicker = () => page().$('.page-head [aria-label="Environment"]');

test("Overview's recent changes stay inside their card", { skip }, async () => {
  await open("overview");
  await page().waitForSelector(".audit-row");
  const fits = await page().$$eval(".plat-two > .card", (cards) => cards.every((c) => c.scrollWidth <= c.clientWidth));
  assert.ok(fits);
  assert.strictEqual(await page().getAttribute(".audit-row .mono", "title"), long);
});

test("Overview picks the environment on the landing zone card only", { skip }, async () => {
  assert.strictEqual(await headPicker(), null);
  assert.ok(await page().$('[aria-label="Landing zone environment"]'));
});

test("an environment still on this PC offers to set it up in Fabric", { skip }, async () => {
  const links = await page().$$('text="Set up in Fabric"');
  assert.strictEqual(links.length, 2);
});

test("only Landing zone and Business keys show the environment picker", { skip }, async () => {
  await open("domains");
  assert.strictEqual(await headPicker(), null);
  await open("landing");
  assert.ok(await headPicker());
});

test("Overview says how it works, beside its content", { skip }, async () => {
  await open("overview");
  await page().waitForSelector(".how-it-works");
  const text = await page().textContent(".how-it-works");
  for (const part of ["What it reads", "What it changes", "Where results go", "Learn more"]) assert.ok(text.includes(part), part);
  const fits = await page().$eval(".with-how-main", (m) => m.scrollWidth <= m.clientWidth);
  assert.ok(fits);
});
