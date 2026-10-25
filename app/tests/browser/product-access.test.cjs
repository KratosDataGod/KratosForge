// PB-054: a data product's page lists the projects given access, and its owner or a platform admin
// takes access away behind a danger confirm (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

const PID = "crm/customer_360";
let me = { user: "bo@example.com", admin: false };
let granted = ["ledger", "billing"];
const product = () => ({ id: PID, project: "crm", name: "customer_360", description: "", owner: "Ana@example.com; cy@example.com",
  access: "private", status: "published", version: "1", domain_id: "sales", ports: [{ port: "customers", env: "prod" }],
  consumers: [{ consumer_project: "ledger", product_id: PID, port: "customers", reads: {} }], granted: [...granted],
  can_link: false, why: "Your own product", linked: false });
const mocks = {
  platform_whoami: () => me,
  platform_products: () => ({ project: null, products: [product()] }),
  platform_product_trust: () => ({ product_id: PID, name: "customer_360", owner: "", admin: false, can_edit_terms: false, items: [], terms: [] }),
  platform_revoke: ({ project }) => { granted = granted.filter((g) => g !== project); return granted.map((g) => ({ id: `${PID}|${g}`, product_id: PID, project: g })); },
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
});
after(async () => { if (app) await app.close(); });

const openProduct = async () => {
  await page().evaluate(() => { productsPage.open = null; catalogPage.tab = "products"; go("onelake"); render(); });
  await page().click('a.product-open:has-text("customer_360")');
  await page().waitForSelector(".product-access table, .product-access p");
};
const callsOf = (method) => app.calls.filter((c) => c.method === method).map((c) => JSON.parse(JSON.stringify(c.params)));

test("someone who isn't the owner or an admin sees who has access, with no way to take it away", { skip }, async () => {
  await openProduct();
  const text = await page().textContent(".product-access");
  assert.match(text, /ledger/);
  assert.match(text, /billing/);
  assert.match(await page().textContent('.product-access tr[data-project="ledger"]'), /Linked/);
  assert.match(await page().textContent('.product-access tr[data-project="billing"]'), /Not yet/);
  assert.strictEqual(await page().$('.product-access button:has-text("Take access away")'), null);
  assert.match(text, /Only the product's owner or a platform admin can take access away/);
});

test("the owner takes a linked project's access away after a danger confirm that says the link stays", { skip }, async () => {
  me = { user: "ana@example.com", admin: false };
  await openProduct();
  await page().click('.product-access tr[data-project="ledger"] button:has-text("Take access away")');
  const dialog = await page().textContent("[role=dialog]");
  assert.match(dialog, /Take ledger's access to customer_360 away\?/);
  assert.match(dialog, /that link stays and its runs keep reading/);
  assert.ok(await page().$("[role=dialog] button.danger-solid"));
  await page().click('[role=dialog] button:has-text("Take access away")');
  await page().waitForSelector('.product-access tr[data-project="ledger"]', { state: "detached" });
  assert.deepStrictEqual(callsOf("platform_revoke"), [{ product_id: PID, project: "ledger" }]);
  assert.ok(await page().$('.product-access tr[data-project="billing"]'));
});

test("Cancel takes nothing away, and the page names the write", { skip }, async () => {
  me = { user: "chris@example.com", admin: true };
  await openProduct();
  await page().click('.product-access tr[data-project="billing"] button:has-text("Take access away")');
  await page().click('[role=dialog] button:has-text("Cancel")');
  assert.strictEqual(callsOf("platform_revoke").length, 1);
  assert.match(await page().textContent(".how-it-works"), /Take access away deletes that project's row in catalog\.grant/);
});
