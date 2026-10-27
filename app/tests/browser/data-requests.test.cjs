// Mesh wave 1, B1: ask another team for data, a new data product from a domain or a change to a
// product, and the owner accepting it with a reply (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const sent = [];
const decided = [];
const product = { id: "crm/c360", project: "crm", name: "c360", description: "", owner: "bo@contoso.com", access: "public",
  status: "published", version: "1", domain_id: "sales", ports: [{ port: "customers", env: "prod" }], consumers: [], granted: [],
  can_link: false, why: "Open a metadata project to link", linked: false };
const requests = [
  { id: "d1", kind: "data", target: "sales", status: "waiting", can_decide: true, requested_by: "lee@contoso.com",
    requested_at: "2026-10-26T09:00:00Z", project: "ledger", reason: "Churn by region for the retention dashboard",
    detail: { what: "new", domain_id: "sales", domain_name: "Sales", needed_by: "2026-11-30" } },
  { id: "d2", kind: "data", target: "crm/c360", status: "waiting", can_decide: true, requested_by: "lee@contoso.com",
    requested_at: "2026-10-26T10:00:00Z", project: "ledger", reason: "Add a region column",
    detail: { what: "change", product_name: "c360", domain_id: "sales" } },
];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_whoami: () => ({ admin: false, user: "bo@contoso.com" }),
    platform_requests: () => requests,
    platform_request: (p) => { sent.push(p); return { id: "new", status: "waiting", ...p }; },
    platform_decide: (p) => { decided.push(p); return { ...requests.find((r) => r.id === p.id), status: "approved" }; },
    platform_domains: () => [{ id: "sales", name: "Sales" }, { id: "emea", name: "EMEA", parent_id: "sales" }],
    platform_products: () => ({ project: null, products: [product, { ...product, id: "crm/draft", name: "draft", status: "draft" }] }),
    platform_product_trust: () => ({ product_id: product.id, name: "c360", owner: "", admin: false, can_edit_terms: false, items: [], terms: [] }),
  } });
});
after(async () => { if (app) await app.close(); });

const waitFor = async (list) => { for (let i = 0; i < 50 && !list.length; i++) await page().waitForTimeout(20); };

test("a data request row says what is asked for and by when, and the owner accepts it with a reply", { skip }, async () => {
  await page().evaluate(() => { state.user = "bo@contoso.com"; meshRequests.show = "all"; goPlatform("requests"); });
  await page().waitForSelector('tr[data-request="d1"]');
  assert.match(await page().textContent('tr[data-request="d1"]'), /Data.*New data product from Sales.*Needed by 2026-11-30.*for ledger.*Churn by region/s);
  assert.match(await page().textContent('tr[data-request="d2"]'), /Change to c360.*Add a region column/s);
  await page().click('tr[data-request="d1"] >> button:text-is("Accept")');
  await page().waitForSelector("text=Accept the data request from lee@contoso.com?");
  assert.match(await page().textContent("[role=dialog]"), /Nothing is built or changed now/);
  await page().click('[role=dialog] >> button:text-is("Accept")');
  assert.deepStrictEqual(decided, [], "a reply is needed");
  await page().fill('[aria-label="Reply"]', "In Prod by 30 November");
  await page().click('[role=dialog] >> button:text-is("Accept")');
  await waitFor(decided);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(decided[0])), { id: "d1", approve: true, note: "In Prod by 30 November" });
});

test("New request > Data from another team sends a new product request to a domain", { skip }, async () => {
  await page().evaluate(() => newRequestDialog(null, "data"));
  await page().waitForSelector('[aria-label="What the data is for"]');
  await page().selectOption('[aria-label="Domain"]', "emea");
  await page().click('[role=dialog] >> button:text-is("Send request")');
  assert.strictEqual(sent.length, 0, "what you need is required");
  await page().fill('[aria-label="What the data is for"]', "Pipeline by rep");
  await page().fill('[role=dialog] input[aria-label="Needed by"]', "2026-12-01");
  await page().click('[role=dialog] >> button:text-is("Send request")');
  await waitFor(sent);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(sent[0])), { kind: "data", target: "emea", reason: "Pipeline by rep", detail: { what: "new", needed_by: "2026-12-01" } });
});

test("Ask for a change on a product's page picks that product, and drafts aren't offered", { skip }, async () => {
  await page().evaluate(() => { productsPage.open = null; catalogPage.tab = "products"; go("onelake"); render(); });
  await page().click('a.product-open:has-text("c360")');
  await page().click('button:text-is("Ask for a change")');
  await page().waitForSelector('[aria-label="Data product"]');
  assert.strictEqual(await page().inputValue('[aria-label="Data product"]'), "crm/c360");
  assert.deepStrictEqual(await page().$$eval('[aria-label="Data product"] option', (os) => os.map((o) => o.value)), ["crm/c360"]);
  await page().fill('[aria-label="What the data is for"]', "Add a region column");
  await page().click('[role=dialog] >> button:text-is("Send request")');
  for (let i = 0; i < 50 && sent.length < 2; i++) await page().waitForTimeout(20);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(sent[1])), { kind: "data", target: "crm/c360", reason: "Add a region column", detail: { what: "change", needed_by: "" } });
});
