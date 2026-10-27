// Testing feedback item 23: Platform > Overview fits its cards, and the environment picker shows only
// where things are kept per environment (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const long = "chris.wagner@expertpcpros.onmicrosoft.com";
let source = "settings";
const overview = (p) => ({ env: p.env, counts: { domains: 9, subdomains: 1, projects: 0, teams: 0, key_tables: 0, keys: 0, products: 0, published: 0, requests: 0 },
  landing: { sources: [] }, problems: [], me: { admin: true }, needs_you: [], mine: [],
  status: { source, default_names: { dev: "Platform - Dev", test: "Platform - Test", prod: "Platform - Prod" }, environments: { dev: { kind: "pc" }, test: { kind: "pc" }, prod: { kind: "fabric", workspace_name: "Platform - Prod" } } },
  recent: [{ at: "2026-10-18T10:00:00Z", by_user: long, action: "import domains from Fabric", target: long }] });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { platform_whoami: () => ({ admin: true }), platform_overview: overview, readiness_check: () => [],
    landing_status: () => ({ sources: [] }), list_capacities: () => [],
    platform_domains: () => [{ id: "sales", name: "Sales", fabric_domain: "f-sales", owners: "" },
      { id: "reseller", name: "Reseller", parent_id: "sales", fabric_domain: "f-res", owners: "" },
      { id: "local", name: "Local", owners: "" }] } });
  // These check the panel beside the page, the layout people get from Settings > Appearance > Explanations.
  await app.page.evaluate(() => { state.settings = { ...(state.settings || {}), ui_explanations: { value: "beside" } }; applyAppearance(); });
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

test("Set up in Fabric opens the setup itself, and Projects says it counts joined projects", { skip }, async () => {
  assert.match(await page().textContent(".plat-stats"), /Projects joined/);
  await page().click('text="Set up in Fabric"');
  await page().waitForSelector('[role=dialog] >> text="Create the Dev platform"');
  assert.strictEqual(await page().inputValue("[role=dialog] input"), "Platform - Dev");
  await page().evaluate(() => closeDialog());
});

test("a platform set by policy offers no setup on Overview, as on Settings", { skip }, async () => {
  source = "policy";
  try {
    await open("glossary");
    await open("overview");
    await page().waitForSelector("text=Set by your organization's policy");
    assert.strictEqual((await page().$$('text="Set up in Fabric"')).length, 0);
  } finally {
    source = "settings";
  }
});

test("Domains: subdomains are indented without a tree glyph, and Fabric domains edit owners only", { skip }, async () => {
  await open("domains");
  await page().waitForSelector("text=Reseller");
  const rows = await page().$$eval("table.list tbody tr", (trs) => trs.map((tr) => tr.textContent));
  assert.ok(rows.every((t) => !t.includes("↳")));
  assert.ok(await page().$("td.plat-subdomain >> text=Reseller"));
  assert.strictEqual((await page().$$('button:text-is("Edit owners")')).length, 2);
  assert.strictEqual((await page().$$('button:text-is("Edit")')).length, 1);
  await page().click('tr:has-text("Sales") >> nth=0 >> button:text-is("Delete")');
  await page().waitForSelector("text=Delete Sales?");
  assert.match(await page().textContent("[role=dialog]"), /next Import from Fabric brings it back/);
  await page().evaluate(() => closeDialog());
});

test("only Landing zone and Business keys show the environment picker", { skip }, async () => {
  await open("glossary");
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
