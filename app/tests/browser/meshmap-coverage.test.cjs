// Home's mesh map (meshmap.js) beyond what other tests draw: a domain that follows Fabric, a domain
// with no projects, projects in no domain, readers counted on a product, and the open project's own
// box going to Data mesh > Workspaces. mesh_map is mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const MAP = {
  me: "orders", problem: "",
  domains: [{ id: "sales", name: "Sales", parent_id: null, fabric_domain: "f-sales", owners: "olu@contoso.com, ana@contoso.com" },
    { id: "hr", name: "HR", parent_id: null, owners: "" }],
  projects: [{ name: "orders", domain_id: "sales", team: "Sales data team" }, { name: "scratch", domain_id: null, team: "" },
    { name: "forecast", domain_id: null, team: "Planning" }],
  products: [{ id: "orders/fact_orders", project: "orders", name: "fact_orders", access: "public", status: "published" },
    { id: "orders/old_orders", project: "orders", name: "old_orders", access: "domain", status: "deprecated" }],
  links: [{ from: "scratch", product: "orders/fact_orders", to: "orders" }, { from: "forecast", product: "orders/fact_orders", to: "orders" }],
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { mesh_map: () => MAP } });
});
after(async () => { await app?.close(); });

// Each test opens Home itself: the last one leaves it.
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => go("home"));
  await page().locator(".mesh-map").waitFor();
});

const domain = (name) => page().locator(".mesh-domain", { has: page().locator(".mesh-domain-head b", { hasText: name }) });

test("a domain from Fabric is badged and names its first owner, and an empty one says it has no projects", { skip }, async () => {
  assert.strictEqual(await domain("Sales").locator(".mesh-domain-head .badge").textContent(), "Fabric");
  await domain("Sales").getByText("Owner: olu@contoso.com +1").waitFor();
  assert.strictEqual(await domain("HR").locator(".mesh-domain-head .badge").count(), 0);
  await domain("HR").getByText("No projects yet").waitFor();
  assert.strictEqual(await domain("Sales").getByText("No projects yet").count(), 0);
});

test("projects in no domain are drawn together under No domain", { skip }, async () => {
  assert.deepStrictEqual(await domain("No domain").locator(".mesh-node b").allTextContents(), ["scratch", "forecast"]);
  assert.strictEqual(await domain("No domain").locator('[data-project="forecast"]').getAttribute("title"), "forecast · Planning");
  assert.strictEqual(await domain("No domain").locator('[data-project="scratch"]').getAttribute("title"), "scratch");
});

test("a product counts its readers and says when it's deprecated; the open project's box goes to its workspaces", { skip }, async () => {
  const fact = page().locator('[data-product="orders/fact_orders"]');
  assert.strictEqual(await fact.locator(".muted-text").textContent(), "2 readers");
  assert.match(await page().locator('[data-product="orders/old_orders"]').getAttribute("title"), / · deprecated$/);
  await page().waitForFunction(() => document.querySelectorAll(".mesh-links path[marker-end]").length === 2);
  await page().locator('.mesh-node.me[data-project="orders"]').getByText("This project").click();
  await page().waitForFunction(() => state.view === "metadata");
  assert.strictEqual(await page().evaluate(() => state.meta.tab), "workspaces");
});
