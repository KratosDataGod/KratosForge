// Data mesh > Domains > Map (meshflow.js), the paths mesh-overview leaves out: a catalog that can't be
// read, a platform with no domains yet (Open Domains), domains that don't feed each other, another
// project's row (its tables aren't on this PC, what it reads), a domain no project has joined, a
// layer with no tables, a table with nothing before or after it, and going back from a layer to its
// domain. The platform and project are mocked; what the person sees is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const entity = (name, layer, source, reads = []) => ({ name, layer, source, reads, owner: "", checks: 0, clean: 0, derive: 0, custom: [], mode: "full", watermark: "", keys: [], columns: [] });
const MAP = {
  me: "Orders", problem: "",
  domains: [{ id: "sales", name: "Sales", parent_id: null }, { id: "finance", name: "Finance", parent_id: null }, { id: "hr", name: "People", parent_id: null }],
  projects: [{ name: "Orders", domain_id: "sales", subdomain_id: null, team: "" }, { name: "Month-end close", domain_id: "finance", subdomain_id: null, team: "Finance BI" }],
  products: [{ id: "fin.close", project: "Month-end close", name: "Close", domain_id: "finance" }],
  links: [{ from: "Orders", product: "fin.close", to: "Month-end close" }],
};

// Set before the app opens: Home reads the map at start-up too.
const db = { fail: null, map: MAP };
let app;
const page = () => app.page;
const mf = () => page().locator(".mf");

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    meta_project: () => ({
      path: "C:/work/orders", name: "Orders", templates: {},
      layers: [{ name: "bronze", lakehouse: "LH_Bronze", entities: 1 }, { name: "silver", lakehouse: "LH_Silver", entities: 1 }, { name: "platinum", lakehouse: "LH_Plat", entities: 0 }],
      entities: [entity("erp_orders", "bronze", "landing erp/orders"), entity("lonely", "silver", "")],
      products: [], links: [], environments: {}, exceptions: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
      project: { name: "Orders", domain: "sales", team: "" },
    }),
    mesh_map: () => { if (db.fail) throw new Error(db.fail); return db.map; },
    meta_runs: () => [],
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, { fail: null, map: structuredClone(MAP) });
  app.errors.length = 0;
});

const show = async (sel) => {
  await page().evaluate(() => { Object.assign(meshFlow, { domain: null, sub: null, layer: null, table: null, dir: "both" }); goMeta("overview"); });
  await page().locator(sel).first().waitFor();
};

test("a catalog that can't be read says why", { skip }, async () => {
  db.fail = "The platform's catalog is locked by another writer.";
  await show("text=The platform's catalog couldn't be read:");
  assert.match(await page().locator("p", { hasText: "couldn't be read" }).innerText(), /locked by another writer/);
  assert.deepStrictEqual(app.errors, []);
});

test("with no domains yet it says where they come from, and Open Domains goes there", { skip }, async () => {
  db.map = { me: "Orders", problem: "", domains: [], projects: [], products: [], links: [] };
  await show("text=No domains yet");
  await page().evaluate(() => { window.__goMeta = window.goMeta; window.__went = []; window.goMeta = (t) => window.__went.push(t); });
  await page().getByRole("button", { name: "Open Domains" }).click();
  assert.deepStrictEqual(await page().evaluate(() => { window.goMeta = window.__goMeta; return window.__went; }), ["domains"]);
  assert.deepStrictEqual(app.errors, []);
});

test("domains that don't feed each other say so", { skip }, async () => {
  db.map.links = [];
  await show(".mf-domains");
  await mf().getByText("No project reads another's products yet.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("another project's row says what it reads and that its tables aren't here; a domain nobody joined says so", { skip }, async () => {
  await show(".mf-domains");
  await page().locator(".mf-domain", { hasText: "Sales" }).click();
  const orders = mf().locator(".mf-projects tbody tr", { hasText: "Orders" });
  assert.match(await orders.locator("td").nth(4).innerText(), /^Close from Month-end close$/);
  await mf().locator(".mf-crumbs").getByRole("button", { name: "Domains" }).click();
  await page().locator(".mf-domain", { hasText: "Finance" }).click();
  const close = mf().locator(".mf-projects tbody tr", { hasText: "Month-end close" });
  assert.strictEqual(await close.locator("td").nth(1).innerText(), "Open this project's folder on this PC to see its tables");
  assert.match(await close.locator("td").nth(3).innerText(), /^Orders \(Close\)$/);
  await mf().locator(".mf-crumbs").getByRole("button", { name: "Domains" }).click();
  await page().locator(".mf-domain", { hasText: "People" }).click();
  await mf().getByText("No projects have joined this domain yet. A project joins one on Domains > This project.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("an empty layer says so; a table with nothing before or after says so; the crumb goes back to the domain", { skip }, async () => {
  await show(".mf-domains");
  await page().locator(".mf-domain", { hasText: "Sales" }).click();
  await mf().getByRole("button", { name: /^platinum/ }).click();
  await mf().getByText("No tables in this layer yet.").waitFor();
  await mf().locator(".mf-crumbs").getByRole("button", { name: "Sales" }).click();
  await mf().getByRole("button", { name: /^silver/ }).click();
  await mf().getByRole("button", { name: "lonely" }).click();
  await mf().getByText("Nothing in this project reads it, and it reads nothing here.").waitFor();
  assert.deepStrictEqual(await mf().locator(".mf-lineage > div > b").allInnerTexts(), ["This table"]);
  await mf().locator(".mf-crumbs").getByRole("button", { name: "Sales" }).click();
  await mf().locator(".mf-projects").waitFor();
  assert.deepStrictEqual(await page().evaluate(() => [meshFlow.domain, meshFlow.layer, meshFlow.table]), ["sales", null, null]);
  assert.deepStrictEqual(app.errors, []);
});
