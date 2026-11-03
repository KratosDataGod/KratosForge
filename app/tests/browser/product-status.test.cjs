// Data mesh Wave 2, P5: the Status view on Data mesh > Products. Each team's incident check reports Red,
// Amber or Green per product and environment; readers see it here (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let canStatus = true;
const hoursAgo = (n) => new Date(Date.now() - n * 36e5).toISOString();
const base = { project: "crm", description: "", owner: "bo@contoso.com", access: "public", status: "published", version: "1",
  domain_id: "sales", ports: [{ port: "customers", entity: "silver.customers", env: "prod" }], consumers: [], granted: [],
  can_link: true, why: "", linked: false, following: false, promise: null, items: [] };
const products = () => [
  { ...base, id: "crm/finance_gl", name: "finance_gl", health: { prod: { status: "green", why: "", latest: "", latest_at: "", checked_at: hoursAgo(0.1) } } },
  { ...base, id: "crm/customer_360", name: "customer_360", following: true,
    health: { prod: { status: "amber", why: "Promise broken: silver.customers", latest: "Acknowledged by dana@contoso.com", latest_at: hoursAgo(1), checked_at: hoursAgo(0.1) } } },
  { ...base, id: "crm/sales_orders", name: "sales_orders", consumers: [{ consumer_project: "a" }, { consumer_project: "b" }, { consumer_project: "c" }],
    health: { prod: { status: "red", why: "Load failed: silver.orders", latest: "", latest_at: "", checked_at: hoursAgo(30) },
      test: { status: "green", why: "", latest: "", latest_at: "", checked_at: hoursAgo(1) } } },
  { ...base, id: "crm/new_one", name: "new_one", health: {} },
];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_products: () => ({ project: { name: "crm" }, products: products(), can_follow: true, can_status: canStatus }),
    platform_access_reviews: () => ({ grants: [], due: 0 }),
  } });
});
after(async () => { if (app) await app.close(); });

const openProducts = async (status = true) => {
  await page().evaluate((on) => { Object.assign(productsPage, { open: null, following: false, status: on, statusEnv: "prod" }); catalogPage.tab = "products"; go("onelake"); render(); }, status);
};

const rows = () => page().$$eval(".product-status tbody tr", (trs) => trs.map((t) => [...t.children].map((td) => td.textContent.trim())));

test("Status lists Red first, then Amber, Green and not checked, each with why and the latest update", { skip }, async () => {
  canStatus = true;
  await openProducts(false);
  await page().waitForSelector('button.status-chip');
  await page().click('button.status-chip');
  await page().waitForSelector(".product-status tbody tr");
  const got = await rows();
  assert.deepStrictEqual(got.map((r) => r[0].split("crm")[0]), ["sales_orders", "customer_360", "finance_gl", "new_one"]);
  assert.match(got[0][1], /^Red\s*Not checked since/);
  assert.strictEqual(got[0][2], "Load failed: silver.orders, 3 readers affected");
  assert.strictEqual(got[1][1], "Amber");
  assert.strictEqual(got[1][2], "Promise broken: silver.customers");
  assert.match(got[1][3], /^Acknowledged by dana@contoso\.com, /);
  assert.deepStrictEqual(got[2].slice(1), ["Green", "Nothing open", "-"]);
  assert.deepStrictEqual(got[3].slice(1, 3), ["Not checked", "Its team's app or agent hasn't checked it here"]);
  assert.strictEqual(await page().getAttribute("button.status-chip", "aria-pressed"), "true");
});

test("another environment shows its own status", { skip }, async () => {
  await openProducts(true);
  await page().waitForSelector(".product-status tbody tr");
  await page().selectOption('.product-status select[aria-label="Environment"]', "test");
  await page().waitForFunction(() => document.querySelector('.product-status tr[data-product="crm/sales_orders"]')?.textContent.includes("Green"));
  const got = await rows();
  assert.deepStrictEqual(got.map((r) => r[1]), ["Green", "Not checked", "Not checked", "Not checked"]);
});

test("the search narrows the Status view too", { skip }, async () => {
  await openProducts(true);
  await page().waitForSelector(".product-status tbody tr");
  await page().fill(".catalog-search", "sales_orders");
  await page().dispatchEvent(".catalog-search", "input");
  await page().waitForFunction(() => document.querySelectorAll(".product-status tbody tr").length === 1);
  await page().fill(".catalog-search", "");
  await page().dispatchEvent(".catalog-search", "input");
});

test("a platform older than the Status view offers no Status button", { skip }, async () => {
  canStatus = false;
  await openProducts(true);
  await page().waitForSelector("button.follow-chip");
  assert.strictEqual(await page().$("button.status-chip"), null);
  assert.strictEqual(await page().$(".product-status"), null);
  canStatus = true;
});
