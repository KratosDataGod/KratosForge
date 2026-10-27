// Data mesh > Products: every team's data products, moved from OneLake, with Edit definition for
// the open project's own (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let project = null;
const base = { description: "", owner: "bo@contoso.com", access: "public", status: "published", version: "1", domain_id: "sales",
  ports: [{ port: "p", env: "prod" }], consumers: [], granted: [], can_link: false, linked: false, following: false };

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    meta_project: () => (project ? { path: "C:/work/crm", name: "crm", version: "3", templates: {}, layers: [], entities: [], products: [], links: [],
      environments: {}, exceptions: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [], project: { name: "crm" } }
      : { path: "", default_path: "C:/work/new", presets: { 3: [{ name: "bronze" }] } }),
    meta_runs: () => [],
    platform_products: () => ({ project: project && { name: "crm" }, can_follow: true, products: [
      { ...base, id: "crm/c360", name: "c360", project: "crm", why: "Your own product" },
      { ...base, id: "ops/stores", name: "stores", project: "ops", why: "Open a metadata project to link" }] }),
    platform_access_reviews: () => ({ every_days: 90, grants: [], due: 0 }),
    platform_product_trust: ({ product_id }) => ({ product_id, name: product_id, owner: "", admin: false, can_edit_terms: false, items: [], terms: [] }),
    platform_whoami: () => ({ user: "chris@contoso.com", admin: false }),
  } });
});
after(async () => { if (app) await app.close(); });

test("with no project open, Products lists every team's products in Data mesh", { skip }, async () => {
  await page().evaluate(() => goMeta("catalog"));
  await page().waitForSelector('.mesh-products a.product-open:has-text("stores")');
  assert.ok(await page().$('.meta-groups .tab.active:has-text("Products")'));
  assert.ok(await page().$(".mesh-products .preview-tag"));
  await page().click('a.product-open:has-text("c360")');
  await page().waitForSelector(".trust-name");
  assert.strictEqual(await page().$('button:has-text("Edit definition")'), null, "no project open, so nothing to edit here");
});

test("OneLake's Data products tab and older links open Data mesh > Products", { skip }, async () => {
  await page().evaluate(() => { productsPage.open = null; catalogPage.tab = "catalog"; go("onelake"); });
  await page().waitForSelector('.onelake-tabs button:has-text("Data products")');
  await page().click('.onelake-tabs button:has-text("Data products")');
  await page().waitForSelector('.mesh-products a.product-open:has-text("c360")');
  assert.strictEqual(await page().evaluate(() => state.view), "metadata");
  await page().evaluate(() => { catalogPage.tab = "products"; go("onelake"); });
  await page().waitForSelector('.mesh-products a.product-open:has-text("c360")');
  assert.strictEqual(await page().evaluate(() => [state.view, catalogPage.tab].join()), "metadata,catalog");
});

test("the open project's own product has Edit definition, which opens Project > Products and links", { skip }, async () => {
  project = true;
  await page().evaluate(() => { productsPage.open = null; goMeta("catalog"); });
  await page().waitForFunction(() => productsPage.mine === "crm" && document.querySelector(".mesh-products a.product-open"));
  await page().click('a.product-open:has-text("c360")');
  await page().waitForSelector('button:has-text("Edit definition")');
  await page().click('button:has-text("Edit definition")');
  await page().waitForFunction(() => state.meta.tab === "products");
  assert.ok(await page().$('.meta-project-groups .tab.active:has-text("Products and links")'));
  assert.deepStrictEqual(app.errors, []);
});
