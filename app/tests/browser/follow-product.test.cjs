// Mesh wave 1, S2: follow a data product from its page, and filter the list to what you follow
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let follows = ["crm/orders"];
let older = false;
const base = { project: "crm", description: "", owner: "bo@contoso.com", access: "public", status: "published", version: "1",
  domain_id: "sales", ports: [{ port: "p", env: "prod" }], consumers: [], granted: [], can_link: false, why: "Your own product", linked: false };
const products = () => [{ ...base, id: "crm/c360", name: "c360" }, { ...base, id: "crm/orders", name: "orders" }]
  .map((p) => (older ? p : { ...p, following: follows.includes(p.id) }));

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_products: () => ({ project: null, products: products(), can_follow: !older }),
    platform_product_trust: () => ({ product_id: "crm/c360", name: "c360", owner: "", admin: false, can_edit_terms: false, items: [], terms: [] }),
    platform_follow: ({ product_id, follow }) => {
      follows = follow ? [...new Set([...follows, product_id])] : follows.filter((x) => x !== product_id);
      return { following: follows };
    },
  } });
});
after(async () => { if (app) await app.close(); });

const openList = async () => {
  await page().evaluate(() => { productsPage.open = null; productsPage.following = false; catalogPage.tab = "products"; go("onelake"); render(); });
  await page().waitForSelector('a.product-open:has-text("c360")');
};

test("Following filters the list to the products you follow", { skip }, async () => {
  await openList();
  assert.strictEqual((await page().textContent(".follow-chip")).trim(), "Following (1)");
  await page().click(".follow-chip");
  assert.strictEqual(await page().getAttribute(".follow-chip", "aria-pressed"), "true");
  assert.strictEqual(await page().$('a.product-open:has-text("c360")'), null);
  assert.ok(await page().$('a.product-open:has-text("orders")'));
});

test("Follow on a product's page follows it, and Following stops", { skip }, async () => {
  await openList();
  await page().click('a.product-open:has-text("c360")');
  await page().waitForSelector(".follow-button");
  assert.strictEqual((await page().textContent(".follow-button")).trim(), "Follow");
  await page().click(".follow-button");
  await page().waitForSelector('.follow-button[aria-pressed="true"]');
  assert.strictEqual((await page().textContent(".follow-button")).trim(), "Following");
  assert.deepStrictEqual(follows, ["crm/orders", "crm/c360"]);
  await page().click(".follow-button");
  await page().waitForSelector('.follow-button[aria-pressed="false"]');
  assert.deepStrictEqual(follows, ["crm/orders"]);
  assert.match(await page().textContent(".how-it-works"), /Follow adds you to catalog\.follow/);
});

test("a platform older than following shows no Follow button or filter", { skip }, async () => {
  older = true;
  try {
    await openList();
    assert.strictEqual(await page().$(".follow-chip"), null);
    await page().click('a.product-open:has-text("c360")');
    await page().waitForSelector(".trust-endorsement");
    assert.strictEqual(await page().$(".follow-button"), null);
  } finally {
    older = false;
  }
});
