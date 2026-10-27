// Mesh wave 1, C3: Platform > Overview lists what has no owner, no second owner, or an owner who
// hasn't used the platform lately (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let coverage;
const overview = (p) => ({ env: p.env, counts: { domains: 2, subdomains: 0, projects: 0, teams: 0, key_tables: 0, keys: 0, products: 1, published: 1, requests: 0 },
  landing: { sources: [] }, problems: [], me: { admin: true }, needs_you: [], mine: [],
  status: { source: "settings", default_names: {}, environments: { dev: { kind: "pc" }, test: { kind: "pc" }, prod: { kind: "pc" } } }, recent: [] });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { platform_whoami: () => ({ admin: true }), platform_overview: overview, readiness_check: () => [],
    landing_status: () => ({ sources: [] }), list_capacities: () => [], platform_owner_coverage: () => coverage } });
});
after(async () => { if (app) await app.close(); });

const open = async () => {
  await page().evaluate(() => { state.user = "chris@contoso.com"; goPlatform("overview"); render(); });
  await page().waitForSelector(".owner-coverage table, .owner-coverage p");
};

test("each gap says what it is, who owns it and why it's listed, with nobody owning it first", { skip }, async () => {
  coverage = { quiet_days: 60, checked: 4, covered: 1, no_owner: 1, gaps: [
    { kind: "domain", id: "finance", name: "Finance", owners: [], problems: ["No owner"], unchecked: [], opens: "domains" },
    { kind: "product", id: "crm/orders", name: "orders", owners: ["sales team"], problems: ["No second owner to cover"], unchecked: ["sales team"], opens: "products" },
    { kind: "domain", id: "sales", name: "Sales", owners: ["dee@contoso.com", "ed@contoso.com"], problems: ["ed@contoso.com hasn't used the platform for 61 days"], unchecked: [], opens: "domains" }] };
  await open();
  assert.match(await page().textContent(".owner-coverage"), /1 of 4 covered/);
  const rows = await page().$$eval(".owner-coverage tbody tr", (rs) => rs.map((r) => r.dataset.owned));
  assert.deepStrictEqual(rows, ["domain:finance", "product:crm/orders", "domain:sales"]);
  assert.ok(await page().$('.owner-coverage tr[data-owned="domain:finance"] .fail-text'));
  assert.match(await page().textContent('.owner-coverage tr[data-owned="product:crm/orders"]'), /Names, not sign-ins, can't be checked/);
  assert.match(await page().textContent('.owner-coverage tr[data-owned="domain:sales"]'), /hasn't used the platform for 61 days/);
  await page().click('.owner-coverage tr[data-owned="domain:finance"] button:text-is("Open")');
  await page().waitForFunction(() => platformPage.tab === "domains");
});

test("everything covered says so, and an older platform says what to do", { skip }, async () => {
  coverage = { quiet_days: 60, checked: 3, covered: 3, no_owner: 0, gaps: [] };
  await open();
  assert.match(await page().textContent(".owner-coverage"), /Every domain, data product and source has two owners/);
  coverage = { available: false, problem: "The platform can't check owners yet: UDF_Platform is older than this app.", gaps: [] };
  await open();
  assert.match(await page().textContent(".owner-coverage"), /older than this app/);
  assert.match(await page().textContent(".how-it-works"), /records that you used it today \(catalog\.person\)/);
});
