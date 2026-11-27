// Data mesh > Home (mesh-home.js), the paths mesh-home.test leaves out: My team with a project that
// publishes nothing yet, a product with no readers and nothing needed, no data depended on and no
// following, and the cards' links to Requests and an access review; On call with no project open,
// nobody to wake, late landing sources, broken shortcuts and owner gaps, each opening its page; and
// Organization with no domains and nothing waiting long. mesh_home is mocked; what the person sees
// and where each link leads is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const hoursAgo = (n) => new Date(Date.now() - n * 36e5).toISOString();

const TEAM = () => ({
  project: { name: "orders" },
  products: [{ id: "sales/orders", name: "orders", version: "", status: "published", owners: ["olu@contoso.com"], readers: [], needs: [] }],
  waiting: [], asks: [], depends: [], follows_known: false, reviews_due: 2, reviews_known: true,
});
const ONCALL = () => ({
  incidents: [], wake: [],
  late: [{ name: "erp", title: "SAP ERP", newest: hoursAgo(30) }, { name: "shop", title: "", newest: "" }],
  landing_problem: "Test's LH_Landing couldn't be listed",
  broken: [{ name: "sales_orders", workspace_name: "Finance Dev", lakehouse_name: "LH_Finance" }],
  owner_gaps: { total: 3, no_owner: 1 },
});
const ORG = () => ({ totals: { domains: 0, products: 0, two_owners: 0, reviews_due: 0, waiting: 0, stale: 0 }, domains: [], stale: [], stale_days: 7 });

const db = {};
let app;
const page = () => app.page;
const view = () => page().locator("#view");
const card = (title) => view().locator(".mh-card", { has: page().locator(`h2:text-is("${title}")`) });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    meta_project: () => ({ path: "", default_path: "C:/work/new", presets: {} }),
    mesh_home: ({ view: v }) => ({ view: v, me: { user: "chris@contoso.com", admin: true }, project: db.project, incidents_read: db.incidentsRead, problems: [],
      team: db.team, oncall: db.oncall, org: db.org }),
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, { project: { name: "orders", domain: "sales", team: "Orders team" }, incidentsRead: false, team: TEAM(), oncall: ONCALL(), org: ORG() });
  app.calls.length = 0;
});

async function openHome(v, ready) {
  await page().evaluate((v) => { closeDialog(); meshHome.view = v; state.meta.tab = "home"; go("metadata"); render(); }, v);
  await view().locator(ready).first().waitFor();
}

test("My team: a product with no readers and nothing needed, nothing depended on, and no following yet", { skip }, async () => {
  await openHome("team", ".mh-card");
  assert.match(await page().locator(".mesh-page").innerText(), /Project orders · domain sales · Orders team/);
  assert.match(await card("Your products").locator("tbody tr").innerText(), /^orders\s+None yet\s+1\s+Nothing$/);
  assert.match(await card("Data you depend on").innerText(), /Nothing yet\.\s+Products your project links to\. Following isn't on this platform yet\./);
  assert.match(await card("Waiting on you").innerText(), /Nothing is waiting on you\.\s+2 grants to your products are due an access review\./);
  assert.match(await card("Your asks").innerText(), /You haven't asked for anything yet\./);
  await card("Waiting on you").getByRole("button", { name: "Review" }).click();
  await page().waitForFunction(() => state.meta.tab === "catalog" && productsPage.reviewDue === true);
  await openHome("team", ".mh-card");
  await card("Your asks").getByRole("button", { name: "Everything you sent" }).click();
  await page().waitForFunction(() => state.meta.tab === "requests" && meshRequests.show === "mine");
  assert.deepStrictEqual(app.errors, []);
});

test("My team with a project that publishes nothing, and none open at all", { skip }, async () => {
  db.team = { ...TEAM(), products: [] };
  await openHome("team", ".mh-card");
  assert.match(await card("Your products").innerText(), /orders hasn't published a data product yet\. Add one on Project > Products and links\./);
  db.team = { ...TEAM(), products: [], project: null };
  db.project = null;
  await openHome("team", ".mh-card");
  assert.match(await card("Your products").innerText(), /You don't own a data product yet\. Open your project to see the ones it publishes\./);
  assert.match(await page().locator(".mesh-page").innerText(), /No metadata project open on this PC\./);
  assert.deepStrictEqual(app.errors, []);
});

test("On call: no project open, nobody to wake, late sources, broken shortcuts and owner gaps, each opening its page", { skip }, async () => {
  await openHome("oncall", ".mh-card");
  assert.match(await card("Incidents that reach data products").innerText(), /Open a metadata project to see its incidents here/);
  assert.match(await card("Who to wake").innerText(), /No one: nothing upstream of a product is broken\./);
  const late = await card("Late landing sources in Prod").locator("li").allInnerTexts();
  assert.match(late[0], /^SAP ERP\s+last file \S/);
  assert.match(late[1], /^shop\s+No files yet$/);
  assert.match(await card("Late landing sources in Prod").innerText(), /Test's LH_Landing couldn't be listed/);
  assert.match(await card("Broken shortcuts").locator("li").innerText(), /^sales_orders\s+Finance Dev \/ LH_Finance$/);
  assert.match(await card("Owner gaps").innerText(), /3 things without cover, 1 with no owner at all\./);

  await card("Owner gaps").getByRole("button", { name: "Owners" }).click();
  await page().waitForFunction(() => state.view === "platform" && platformPage.tab === "overview");
  await openHome("oncall", ".mh-card");
  await card("Broken shortcuts").getByRole("button", { name: "Shortcuts" }).click();
  await page().waitForFunction(() => state.view === "onelake" && catalogPage.tab === "shortcuts" && scPage.view === "health");
  assert.deepStrictEqual(app.errors, []);
});

test("On call with an open project and no incident; every owner covered", { skip }, async () => {
  db.incidentsRead = true;
  db.oncall = { ...ONCALL(), late: [], landing_problem: null, broken: [], owner_gaps: { total: 0, no_owner: 0 },
    wake: [{ product: "orders", owners: [] }] };
  await openHome("oncall", ".mh-card");
  assert.match(await card("Incidents that reach data products").innerText(), /No active incident reaches a data product\./);
  assert.match(await card("Who to wake").locator("li").innerText(), /^orders\s+No owner$/);
  assert.match(await card("Late landing sources in Prod").innerText(), /Every source arrived on time\./);
  assert.match(await card("Broken shortcuts").innerText(), /No shortcut was broken at its last check\./);
  assert.match(await card("Owner gaps").innerText(), /Every domain, product and source has two owners\./);
  assert.deepStrictEqual(app.errors, []);
});

test("Organization with no domains offers Domains; nothing has waited long; Open Requests shows all", { skip }, async () => {
  await openHome("org", ".mh-tiles");
  assert.deepStrictEqual(await view().locator(".mh-tile").allInnerTexts().then((t) => t.map((x) => x.replace(/\s+/g, " ").trim())),
    ["0 domains", "0 data products", "0 with two owners", "0 access reviews due", "0 requests waiting"]);
  assert.match(await card("Waiting over 7 days").innerText(), /No request has waited that long\./);
  assert.match(await card("Domains").innerText(), /No domains yet/);
  await card("Waiting over 7 days").getByRole("button", { name: "Open Requests" }).click();
  await page().waitForFunction(() => state.meta.tab === "requests" && meshRequests.show === "all");
  await openHome("org", ".mh-tiles");
  await card("Domains").getByRole("button", { name: "Open Domains" }).click();
  await page().waitForFunction(() => state.meta.tab === "domains");
  assert.deepStrictEqual(app.errors, []);
});
