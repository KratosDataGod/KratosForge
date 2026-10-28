// Testing item 21: Data mesh opens on the organization's domains and drills down to tables
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

const entity = (name, layer, source, reads = []) => ({ name, layer, source, reads, owner: "", checks: 1, clean: 0, derive: 0, custom: [], mode: "full", watermark: "", keys: [], columns: [] });
const mocks = {
  meta_project: () => ({
    path: "C:/work/orders", name: "Orders", templates: {},
    layers: [{ name: "bronze", lakehouse: "LH_Bronze", entities: 1 }, { name: "silver", lakehouse: "LH_Silver", entities: 1 }, { name: "gold", lakehouse: "LH_Gold", entities: 1 }],
    entities: [entity("erp_orders", "bronze", "landing erp/orders"), entity("orders", "silver", "erp_orders", ["erp_orders"]), entity("fact_orders", "gold", "orders", ["orders"])],
    products: [{ name: "orders", title: "Orders", access: "public", status: "published", ports: [{ name: "main", entity: "fact_orders" }] }],
    links: [], environments: {}, exceptions: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
    project: { name: "Orders", domain: "sales", subdomain: "reseller", team: "Sales data team" },
  }),
  mesh_map: () => ({
    me: "Orders", problem: "",
    domains: [{ id: "sales", name: "Sales", parent_id: null }, { id: "reseller", name: "Reseller", parent_id: "sales" }, { id: "finance", name: "Finance", parent_id: null }],
    projects: [{ name: "Orders", domain_id: "sales", subdomain_id: "reseller", team: "Sales data team" }, { name: "Month-end close", domain_id: "finance", subdomain_id: null, team: "Finance BI" }],
    products: [{ id: "sales.orders", project: "Orders", name: "Orders", domain_id: "sales", access: "public", status: "published" }],
    links: [{ from: "Month-end close", product: "sales.orders", to: "Orders" }],
  }),
  meta_runs: () => [],
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await page().evaluate(() => goMeta("overview"));
  await page().waitForSelector(".mf-domains");
});
after(async () => { if (app) await app.close(); });

test("Data mesh opens on the organization's domains, with how they feed each other", { skip }, async () => {
  const cards = await page().$$eval(".mf-domain b", (b) => b.map((x) => x.textContent));
  assert.deepStrictEqual(cards, ["Sales", "Finance"]);
  assert.match(await page().textContent(".mf-domain.me"), /This project's domain/);
  assert.match(await page().textContent(".mf-feeds"), /Sales\s*→\s*Finance\s*Orders/);
  assert.ok(await page().$(".how-it-works"));
  assert.ok(await page().$('.meta-groups .tab.active:has-text("Domains") .preview-tag'), "the tab shows it is in Preview");
});

test("a domain lists its projects, their layers, products and readers", { skip }, async () => {
  await page().click('.mf-domain:has-text("Sales")');
  const row = await page().textContent(".mf-projects tbody tr");
  assert.match(row, /Orders/);
  assert.match(row, /Month-end close \(Orders\)/);
  assert.deepStrictEqual(await page().$$eval(".mf-layers .layer-chip", (c) => c.map((x) => x.firstChild.textContent)), ["bronze", "silver", "gold"]);
  assert.ok(await page().$('.mf button:has-text("All domains")'));
  assert.ok(await page().$('.mf button:has-text("Glossary")'));
  await page().click('.seg button:has-text("Reseller")');
  assert.ok(await page().$('.mf-projects:has-text("Orders")'));
});

test("a layer's table shows its whole lineage, both ways, out to the projects reading its products", { skip }, async () => {
  await page().click('.mf-layers .layer-chip:has-text("silver")');
  await page().click('.mf-layer button:has-text("orders")');
  const cols = await page().$$eval(".mf-lineage > div", (d) => d.map((x) => x.innerText.replace(/\s+/g, " ").trim()));
  assert.deepStrictEqual(cols, ["Source landing erp/orders", "It reads erp_orders", "This table orders", "Reads it fact_orders",
    "Data products Product Orders", "Other projects Month-end close reads Orders"]);
  await page().click('button[aria-pressed="false"]:has-text("Only what it reads")');
  assert.deepStrictEqual(await page().$$eval(".mf-lineage > div > b", (b) => b.map((x) => x.textContent)), ["Source", "It reads", "This table"]);
  await page().click('button:has-text("Whole lineage")');
  await page().click('.mf-lineage button:has-text("fact_orders")');
  assert.match(await page().textContent(".mf-table h2"), /fact_orders/);
  assert.match(await page().textContent(".mf-layer h2"), /gold tables/);
  assert.match(await page().textContent(".mf-lineage"), /Before that\s*erp_orders/);
  assert.ok(await page().$('.mf-table button:has-text("Columns")'));
  await page().click('.mf-crumbs button:has-text("Domains")');
  assert.ok(await page().$(".mf-domains"));
  assert.deepStrictEqual(app.errors, []);
});

test("the Preview tag stays in view on every level, once, on the tab", { skip }, async () => {
  await page().click('.mf-domain:has-text("Sales")');
  assert.ok(await page().$('.meta-groups .tab.active:has-text("Domains") .preview-tag'));
  await page().click('.mf-layers .layer-chip:has-text("gold")');
  assert.ok(await page().$('.meta-groups .tab.active:has-text("Domains") .preview-tag'));
  assert.strictEqual(await page().$(".mf-crumbs .preview-tag"), null);
});
