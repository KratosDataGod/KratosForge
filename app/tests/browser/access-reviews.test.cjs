// Mesh wave 1, C4: access reviews. Owners see which grants on their products are due, confirm the
// ones still needed, and nothing is taken away by itself (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const PID = "crm/c360";
const old = new Date(Date.now() - 100 * 86400e3).toISOString();
let grants = [
  { product_id: PID, product_name: "c360", project: "billing", granted_at: old, granted_by: "bo@contoso.com", reviewed_at: "", reviewed_by: "", days: 100, due: true },
  { product_id: PID, product_name: "c360", project: "ledger", granted_at: old, granted_by: "bo@contoso.com", reviewed_at: new Date(Date.now() - 5 * 86400e3).toISOString(), reviewed_by: "bo@contoso.com", days: 5, due: false },
];
let available = true;
const reviews = () => (available ? { every_days: 90, grants, due: grants.filter((g) => g.due).length }
  : { available: false, problem: "The platform can't review access yet: UDF_Platform is older than this app.", grants: [], due: 0, every_days: 90 });
const product = (id, name) => ({ id, project: "crm", name, description: "", owner: "bo@contoso.com", access: "private", status: "published",
  version: "1", domain_id: "sales", ports: [{ port: "p", env: "prod" }], consumers: [], granted: ["billing", "ledger"],
  can_link: false, why: "Your own product", linked: false });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_whoami: () => ({ admin: false, user: "bo@contoso.com" }),
    platform_products: () => ({ project: null, products: [product(PID, "c360"), product("crm/orders", "orders")] }),
    platform_product_trust: () => ({ product_id: PID, name: "c360", owner: "", admin: false, can_edit_terms: false, items: [], terms: [] }),
    platform_access_reviews: reviews,
    platform_confirm_access: ({ projects }) => {
      grants = grants.map((g) => (projects.includes(g.project) ? { ...g, due: false, days: 0, reviewed_at: new Date().toISOString(), reviewed_by: "bo@contoso.com" } : g));
      return reviews();
    },
  } });
});
after(async () => { if (app) await app.close(); });

const openList = async () => {
  await page().evaluate(() => { productsPage.open = null; productsPage.reviewDue = false; catalogPage.tab = "products"; go("onelake"); render(); });
  await page().waitForSelector('a.product-open:has-text("c360")');
};

test("the list says how many grants are due and filters to their products", { skip }, async () => {
  await openList();
  assert.match(await page().textContent(".access-review-band"), /Access review: 1 grant on your products hasn't been confirmed for 90 days/);
  await page().click(".review-chip");
  assert.ok(await page().$('a.product-open:has-text("c360")'));
  assert.strictEqual(await page().$('a.product-open:has-text("orders")'), null);
});

test("the product's page shows each grant's review and Still needed confirms it", { skip }, async () => {
  await openList();
  await page().click('a.product-open:has-text("c360")');
  await page().waitForSelector('.product-access tr[data-project="billing"] .access-review');
  assert.match(await page().textContent('.product-access tr[data-project="billing"]'), /Review due.*never confirmed/s);
  assert.match(await page().textContent('.product-access tr[data-project="ledger"]'), /Confirmed/);
  assert.doesNotMatch(await page().textContent('.product-access tr[data-project="ledger"]'), /Review due/);
  await page().click('.product-access tr[data-project="billing"] button:text-is("Still needed")');
  await page().waitForFunction(() => !document.querySelector('.product-access tr[data-project="billing"]').textContent.includes("Review due"));
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "platform_confirm_access").map((c) => JSON.parse(JSON.stringify(c.params))),
    [{ product_id: PID, projects: ["billing"] }]);
  assert.match(await page().textContent(".product-access"), /Nothing is taken away by itself/);
  assert.match(await page().textContent(".how-it-works"), /Still needed records your confirmation in catalog\.grant_review/);
});

test("a platform older than access reviews says so and keeps Take access away", { skip }, async () => {
  available = false;
  try {
    await openList();
    assert.strictEqual(await page().$(".access-review-band"), null);
    await page().click('a.product-open:has-text("c360")');
    await page().waitForSelector('.product-access tr[data-project="billing"]');
    assert.strictEqual(await page().$('.product-access button:text-is("Still needed")'), null);
    assert.ok(await page().$('.product-access button:text-is("Take access away")'));
    assert.match(await page().textContent(".product-access"), /older than this app/);
  } finally {
    available = true;
  }
});
