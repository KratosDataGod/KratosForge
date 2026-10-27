// Data mesh > Home: My team, On call and Organization, with or without a project open
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const views = [];
let admin = false;

const team = {
  project: null,
  products: [{ id: "sales/orders", name: "orders", version: "3", status: "published", owners: ["olu@contoso.com", "ana@contoso.com"],
    readers: ["finance", "hr"], needs: ["Incident upstream"], incident: "i1", incident_env: "prod" }],
  waiting: [{ id: "r1", kind: "access", target: "sales/orders", project: "finance", status: "waiting", requested_at: "2026-10-26T09:00:00+00:00", can_decide: true }],
  asks: [{ id: "r2", kind: "data", target: "inventory", status: "approved", requested_at: "2026-10-25T09:00:00+00:00", mine: true }],
  depends: [{ id: "ops/stores", name: "stores", project: "ops", state: "Deprecated, stops 2026-11-30", how: "following", incident: null }],
  follows_known: true, reviews_due: 1, reviews_known: true,
};
const oncall = {
  incidents: [{ id: "i1", env: "prod", title: "Orders late", state: "open", priority: "P2", owner: "", products: [{ id: "sales/orders", name: "orders", owners: ["olu@contoso.com"] }],
    readers: ["finance", "hr"], people: 2 }],
  wake: [{ product: "orders", owners: ["olu@contoso.com"] }],
  late: null, landing_problem: null, broken: [], owner_gaps: null,
};
const org = {
  totals: { domains: 1, products: 2, two_owners: 1, reviews_due: null, grants: null, waiting: 1, stale: 1 },
  domains: [{ id: "sales", name: "Sales", products: 2, readers: 2, two_owners: 1, reviews_due: null, incidents: 1, needs: ["1 without a second owner"] }],
  stale: [{ id: "r9", kind: "key_table", target: "promotion", status: "waiting", days: 9 }], stale_days: 5,
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    meta_project: () => ({ path: "", default_path: "C:/work/new", presets: { 3: [{ name: "bronze" }] } }),
    mesh_home: ({ view }) => {
      views.push(view);
      const out = { view, me: { user: "chris@contoso.com", admin }, project: null, incidents_read: false, problems: [] };
      if (view === "team") return { ...out, team: admin ? { ...team, products: [] } : team };
      if (view === "oncall") return { ...out, incidents_read: true, oncall, problems: ["Landing zone: couldn't reach OneLake"] };
      return { ...out, org };
    },
  } });
});
after(async () => { if (app) await app.close(); });

const open = async (sel) => {
  await page().evaluate(() => { state.meta.tab = "home"; go("metadata"); });
  await page().waitForSelector(sel);
};

test("with no project open, Data mesh opens on Home in My team for someone who owns a product", { skip }, async () => {
  await open(".mh-card");
  assert.ok(views.length && views.every((v) => v === "team"), views.join());  // the app may render twice while it starts
  assert.ok(await page().$('button:has-text("Set up a project")'));
  assert.strictEqual(await page().getAttribute('.seg button:has-text("My team")', "aria-pressed"), "true");
  assert.match(await page().textContent('.mh-card:has-text("Waiting on you")'), /Access: sales\/orders for finance/);
  assert.match(await page().textContent('.mh-card:has(h2:text-is("Your products"))'), /orders v3\s*2 projects\s*2\s*Incident upstream/);
  assert.match(await page().textContent('.mh-card:has-text("Data you depend on")'), /stores.*Deprecated, stops 2026-11-30/);
  assert.match(await page().textContent(".how-it-works"), /Nothing\. Each card opens the page that acts/);
  assert.ok(await page().$('.meta-groups .tab.active:has-text("Home") .preview-tag'), "the tab shows it is in Preview");
});

test("On call lists incidents that reach products, who to wake, and names what it couldn't read", { skip }, async () => {
  await page().click('.seg button:has-text("On call")');
  await page().waitForSelector('.mh-card:has-text("Who to wake")');
  assert.match(await page().textContent('.mh-card:has-text("Incidents that reach")'), /P2\s*Orders late.*orders.*2 projects.*No owner/);
  assert.match(await page().textContent('.mh-card:has-text("Who to wake")'), /orders\s*olu@contoso\.com/);
  assert.match(await page().textContent(".problems"), /Landing zone: couldn't reach OneLake/);
  assert.match(await page().textContent('.mh-card:has-text("Late landing sources in Prod")'), /The landing zone couldn't be read/);
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.meshHome")), "oncall");
});

test("Organization shows totals and one row per domain, with unknown counts as a dash", { skip }, async () => {
  await page().click('.seg button:has-text("Organization")');
  await page().waitForSelector(".mh-tiles");
  const tiles = await page().$$eval(".mh-tile", (t) => t.map((x) => x.innerText.replace(/\s+/g, " ").trim()));
  assert.deepStrictEqual(tiles, ["1 domains", "2 data products", "1 of 2 with two owners", "— access reviews due", "1 requests waiting"]);
  assert.match(await page().textContent('.mh-card:has-text("Domains") tbody'), /Sales\s*2\s*2\s*1 of 2\s*1 without a second owner/);
  assert.match(await page().textContent('.mh-card:has-text("Waiting over 5 days")'), /Key table: promotion\s*9 days/);
});

test("an admin with no product of their own starts on On call", { skip }, async () => {
  admin = true;
  views.length = 0;
  await page().evaluate(() => { localStorage.removeItem("fd.meshHome"); meshHome.view = null; });
  await open('.mh-card:has-text("Who to wake")');
  assert.deepStrictEqual([...new Set(views)], ["team", "oncall"]);
  assert.strictEqual(await page().getAttribute('.seg button:has-text("On call")', "aria-pressed"), "true");
  assert.deepStrictEqual(app.errors, []);
});
