// Data mesh > Home (mesh-home.js) beyond mesh-home and mesh-home-coverage: where each card's row and link
// leads (a product, Requests waiting, All products, a product depended on, an incident, the landing zone,
// Overview), a request of a kind and status the page has no name for, needs and states drawn in their
// own colours, an incident with no priority, shortcuts that couldn't be read, a domain with no products,
// the first choice falling to Organization, the catalog that couldn't be read, Preview off, and a store
// that refuses the remembered view. mesh_home and platform_products are mocked with the engine's reply shapes.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const hoursAgo = (n) => new Date(Date.now() - n * 36e5).toISOString();

const PRODUCT = { id: "sales/orders", name: "orders", version: "2", status: "published", owners: ["olu@contoso.com"], readers: ["bi"], needs: ["Two owners", "Incident upstream"] };
const TEAM = () => ({
  project: { name: "orders" }, products: [PRODUCT],
  waiting: [{ id: "r1", kind: "access", target: "sales/orders", project: "bi", requested_at: hoursAgo(2) }],
  asks: [{ id: "r2", kind: "retention", target: "", status: "parked", requested_at: hoursAgo(5) },
    { id: "r3", kind: "data", target: "sales/returns", status: "declined", decided_at: hoursAgo(1), requested_at: hoursAgo(9) }],
  depends: [{ id: "sales/orders", name: "orders", project: "sales", state: "Deprecated, ends 2027-01-01", incident: false },
    { id: "sales/returns", name: "returns", project: "sales", state: "Published", incident: true }],
  follows_known: true, reviews_due: 1, reviews_known: true,
});
const ONCALL = () => ({
  incidents: [{ id: "inc1", env: "prod", title: "Orders load failed", priority: "", products: [{ id: "sales/orders", name: "orders" }], readers: ["bi"], owner: "" }],
  wake: [{ product: "orders", owners: [] }],
  late: [], landing_problem: "", broken: null, owner_gaps: { total: 0, no_owner: 0 },
});
const ORG = () => ({ totals: { domains: 1, products: 0, two_owners: 0, reviews_due: null, waiting: 0, stale: 0 },
  domains: [{ name: "Sales", products: 0, readers: 0, two_owners: 0, needs: [] }], stale: [], stale_days: 7 });

const db = {};
let app;
const page = () => app.page;
const view = () => page().locator("#view");
const card = (title) => view().locator(".mh-card", { has: page().locator(`h2:text-is("${title}")`) });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    meta_project: () => ({ path: "", default_path: "C:/work/new", presets: {} }),
    platform_products: () => ({ project: null, products: [{ id: "sales/orders", project: "sales", name: "orders", description: "", owner: "olu@contoso.com",
      access: "public", status: "published", version: "2", ports: [], consumers: [] }] }),
    mesh_home: ({ view: v }) => {
      if (db.fail) throw new Error(db.fail);
      return { view: v, me: { user: "chris@contoso.com", admin: db.admin }, project: db.project, incidents_read: true, problems: [],
        team: db.team, oncall: db.oncall, org: db.org };
    },
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, { fail: null, admin: true, project: { name: "orders" }, team: TEAM(), oncall: ONCALL(), org: ORG() });
  await page().evaluate(() => { closeDialog(); meshHome.view = null; try { localStorage.removeItem("fd.meshHome"); } catch { /* none */ } });
});

async function openHome(v, ready) {
  await page().evaluate((v) => { closeDialog(); meshHome.view = v; state.meta.tab = "home"; go("metadata"); render(); }, v);
  await view().locator(ready).first().waitFor();
}
const where = () => page().evaluate(() => ({ view: state.view, tab: state.meta.tab, show: meshRequests.show, open: productsPage.open?.id || null,
  panel: monitorPage.panel, platform: platformPage.tab, incident: incPage.id }));

test("My team: unnamed kinds and statuses, needs and states in their colours, and where each link leads", { skip }, async () => {
  await openHome("team", ".mh-card");
  assert.deepStrictEqual(await card("Your products").locator("tbody .pill-s").evaluateAll((els) => els.map((e) => e.className)),
    ["pill-s wrn", "pill-s fail"]);
  assert.match(await card("Your asks").innerText(), /retention:\s*parked · /);
  assert.deepStrictEqual(await card("Your asks").locator("li > span.small").evaluateAll((els) => els.map((e) => e.className)),
    ["small muted-text", "small muted-text"]);
  assert.deepStrictEqual(await card("Data you depend on").locator("li > span.grow").evaluateAll((els) => els.map((e) => [e.className, e.textContent])),
    [["small grow ", "Deprecated, ends 2027-01-01"], ["small grow inc-unowned", "Incident upstream"]]);
  assert.match(await card("Data you depend on").innerText(), /Products your project links to, and products you follow\./);
  assert.match(await card("Waiting on you").innerText(), /1 grant to your products is due an access review\./);

  await card("Waiting on you").getByRole("button", { name: "Decide" }).click();
  assert.deepStrictEqual(await where().then((w) => [w.tab, w.show]), ["requests", "waiting"]);
  await openHome("team", ".mh-card");
  await card("Waiting on you").getByRole("button", { name: "Open Requests" }).click();
  assert.deepStrictEqual(await where().then((w) => [w.tab, w.show]), ["requests", "waiting"]);
  await openHome("team", ".mh-card");
  await card("Your products").getByRole("button", { name: "All products" }).click();
  assert.deepStrictEqual(await where().then((w) => [w.tab, w.open]), ["catalog", null]);
  await openHome("team", ".mh-card");
  await card("Your products").locator("tbody tr").click();
  await page().waitForFunction(() => productsPage.open?.id === "sales/orders");
  await page().evaluate(() => { productsPage.open = null; });
  await openHome("team", ".mh-card");
  await card("Data you depend on").getByRole("button", { name: "orders" }).click();
  await page().waitForFunction(() => productsPage.open?.id === "sales/orders");
  await page().evaluate(() => { productsPage.open = null; });
  assert.deepStrictEqual(app.errors, []);
});

test("On call: an incident with no priority opens it; nobody to wake; the landing zone link; shortcuts that couldn't be read", { skip }, async () => {
  await openHome("oncall", ".mh-card");
  const row = card("Incidents that reach data products").locator("tbody tr");
  assert.strictEqual(await row.locator(".pill-s").count(), 0);
  assert.match(await row.innerText(), /Orders load failed[\s\S]*No owner/);
  assert.match(await card("Who to wake").innerText(), /orders\s*No owner/);
  assert.match(await card("Broken shortcuts").innerText(), /Shortcuts couldn't be read\./);
  assert.match(await card("Owner gaps").innerText(), /Every domain, product and source has two owners\./);
  await card("Late landing sources in Prod").getByRole("button", { name: "Landing zone" }).click();
  assert.deepStrictEqual(await where().then((w) => [w.view, w.platform]), ["platform", "landing"]);
  await openHome("oncall", ".mh-card");
  await row.click();
  assert.deepStrictEqual(await where().then((w) => [w.view, w.incident]), ["incident", "inc1"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Organization: a domain with no products, an unknown count, and Open Overview", { skip }, async () => {
  await openHome("org", ".mh-tiles");
  assert.deepStrictEqual(await card("Domains").locator("tbody td").allInnerTexts(), ["Sales", "0", "0", "—", "Nothing"]);
  assert.deepStrictEqual(await view().locator(".mh-tile b").allInnerTexts(), ["1", "0", "0", "—", "0"]);
  await card("Domains").getByRole("button", { name: "Open Overview" }).click();
  assert.deepStrictEqual(await where().then((w) => [w.view, w.tab]), ["metadata", "overview"]);
  assert.deepStrictEqual(app.errors, []);
});

test("with no product, no project and no admin the first choice is Organization; a catalog that can't be read says why", { skip }, async () => {
  Object.assign(db, { admin: false, project: null, team: { ...TEAM(), products: [] } });
  await openHome(null, ".mh-tiles");
  assert.strictEqual(await page().evaluate(() => meshHome.view), "org");
  assert.match(await page().locator(".mesh-page").innerText(), /No metadata project open on this PC\./);
  const asked = app.calls.filter((c) => c.method === "mesh_home").map((c) => c.params.view);
  assert.ok(asked.includes("team") && asked.at(-1) === "org", asked.join());
  db.fail = "The catalog database is locked.";
  await openHome("team", ".mh p");
  assert.match(await view().locator(".mh").innerText(), /The platform's catalog couldn't be read: (Error: )?The catalog database is locked\./);
  assert.deepStrictEqual(app.errors, []);
});

test("Preview off drops Ask for data, and a store that refuses the view keeps it for this session", { skip }, async () => {
  // Drawn again with Preview off: the tab's own button goes (the Data mesh page itself needs a project then).
  await openHome("team", ".mh-card");
  assert.strictEqual(await page().getByRole("button", { name: "Ask for data" }).count(), 1);
  await page().evaluate(async () => {
    const shown = state.settings;
    state.settings = { ...(shown || {}), show_preview: { value: false } };
    try { await renderMeshHomeTab(document.querySelector(".with-how").parentElement, null); } finally { state.settings = shown; }
  });
  assert.strictEqual(await page().getByRole("button", { name: "Ask for data" }).count(), 0);
  assert.strictEqual(await card("Your products").count(), 1);
  const got = await page().evaluate(() => {
    const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
    Storage.prototype.getItem = () => { throw new Error("blocked"); };
    Storage.prototype.setItem = () => { throw new Error("blocked"); };
    try { meshHomeSave("oncall"); return meshHomeSaved(); } finally { Storage.prototype.getItem = get; Storage.prototype.setItem = set; }
  });
  assert.strictEqual(got, null);
  await view().locator(".seg").getByRole("button", { name: "On call" }).click();
  await card("Who to wake").waitFor();
  assert.strictEqual(await page().evaluate(() => meshHome.view), "oncall");
  assert.deepStrictEqual(app.errors, []);
});
