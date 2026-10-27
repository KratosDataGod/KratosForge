// Data mesh's tabs: the mesh for everyone on top, the open project's editor behind Project
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const entity = (name, layer) => ({ name, layer, source: "landing erp/orders", reads: [], owner: "", checks: 1, clean: 0, derive: 0, custom: [], mode: "full", watermark: "", keys: [], columns: [] });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    meta_project: () => ({
      path: "C:/work/orders", name: "Orders", version: "3", templates: {},
      layers: [{ name: "bronze", lakehouse: "LH_Bronze", entities: 1 }], entities: [entity("erp_orders", "bronze")],
      products: [], links: [], environments: {}, exceptions: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
      project: { name: "Orders", domain: "sales" },
    }),
    meta_runs: () => [],
    mesh_home: ({ view }) => ({ view, me: { user: "chris@contoso.com", admin: false }, project: { name: "Orders" }, incidents_read: true, problems: [],
      team: { project: { name: "Orders" }, products: [], waiting: [], asks: [], depends: [], follows_known: true, reviews_due: 0, reviews_known: true } }),
  } });
  await page().evaluate(() => { state.meta.tab = "home"; go("metadata"); });
  await page().waitForSelector(".mh-card");
});
after(async () => { if (app) await app.close(); });

const tabs = (sel) => page().$$eval(`${sel} > .tab`, (b) => b.map((x) => x.textContent));

test("the top tabs are the mesh's, and Project holds the editor", { skip }, async () => {
  assert.deepStrictEqual(await tabs(".meta-groups"), ["Home", "Requests", "Domains", "Project"]);
  assert.strictEqual(await page().$(".meta-project-groups"), null);
  assert.strictEqual(await page().$('.head-actions button:has-text("Run all")'), null, "Run all is a project action");
  await page().click('.meta-groups .tab:has-text("Project")');
  await page().waitForSelector(".meta-project-groups");
  assert.deepStrictEqual(await tabs(".meta-project-groups"), ["Design", "Runs", "Quality", "Products and links", "Deploy"]);
  assert.ok(await page().$('.meta-project-groups .tab.active:has-text("Design")'));
  assert.ok(await page().$('.head-actions button:has-text("Run all")'));
  assert.match(await page().textContent(".page-head .sub"), /Every table is a file in C:\/work\/orders/);
});

test("Project opens again on the tab last used there", { skip }, async () => {
  await page().click('.meta-project-groups .tab:has-text("Runs")');
  await page().waitForSelector('.meta-project-groups .tab.active:has-text("Runs")');
  await page().click('.meta-groups .tab:has-text("Home")');
  await page().waitForSelector(".mh-card");
  await page().click('.meta-groups .tab:has-text("Project")');
  await page().waitForSelector('.meta-project-groups .tab.active:has-text("Runs")');
  await page().click('.meta-groups .tab:has-text("Domains")');
  await page().waitForSelector('.meta-tabs button:has-text("Domain and workspaces")');
  assert.ok(await page().$('.meta-tabs button.on:has-text("Map")'));
  assert.deepStrictEqual(app.errors, []);
});
