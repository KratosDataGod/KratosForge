// Product groups (Preview): a data product's tables, semantic models and reports on Data mesh > Products,
// adding a model or report picked from Fabric (only its id is kept), and finding products by what they
// offer (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

const PID = "crm/customer_360";
const SALES_WS = "eeeeeeee-0000-0000-0000-000000000005";
const SM = "dddddddd-0000-0000-0000-000000000004";
const SM2 = "dddddddd-0000-0000-0000-000000000009";
const RPT = "ffffffff-0000-0000-0000-000000000006";
const port = (table) => ({ port: table, env: "prod", table_name: table, lakehouse_name: "LH_Gold", workspace_name: "CRM Prod" });
const base = { project: "crm", description: "", owner: "ana@example.com", access: "public", status: "published", version: "1",
  domain_id: "sales", consumers: [], granted: [], can_link: false, why: "Your own product", linked: false, following: false };
const item = (type, id, name, endorsement) => ({ product_id: PID, item_id: id, item_type: type, workspace_id: SALES_WS,
  workspace_name: "Sales Prod", item_name: name, added_by: "ana", added_at: "2026-10-20T10:00:00Z", endorsement, certified_by: "", scanned_at: "" });

let items = [item("SemanticModel", SM, "SM_Customer360", "Certified"), item("Report", RPT, "Customer health", "Promoted"),
  item("Report", "ffffffff-0000-0000-0000-000000000007", "Churn watch", null)];
let admin = false;
let ownerEdits = false; // the server says this person owns the product (its rule, not the app's)
let older = false;
let lateLists = 0; // lists of another workspace that have answered
const mocks = {
  platform_products: () => ({ project: null, can_follow: true, can_group: true, products: [
    { ...base, id: PID, name: "customer_360", ports: ["dim_customer", "fact_orders", "dim_region"].map(port), items },
    { ...base, id: "ops/orders", name: "orders", project: "ops", ports: ["fact_orders", "dim_store"].map(port),
      items: [{ ...item("SemanticModel", SM2, "SM_Orders", "Promoted"), product_id: "ops/orders" }] },
    { ...base, id: "ops/inventory", name: "inventory", project: "ops", ports: ["a", "b", "c", "d"].map(port), items: [] }] }),
  platform_access_reviews: () => ({ every_days: 90, grants: [], due: 0 }),
  platform_product_trust: () => ({ product_id: PID, name: "customer_360", owner: "ana@example.com", admin, can_edit_terms: admin || ownerEdits, terms: [],
    items: [{ item_id: "bbbbbbbb-0000-0000-0000-000000000002", workspace_id: "w", item_type: "Lakehouse", item_name: "LH_Gold", env: "prod", endorsement: null }] }),
  platform_whoami: () => ({ user: "chris@example.com", admin }),
  platform_product_items: () => {
    if (older) throw new Error("LookupError: The platform can't group a product's semantic models and reports yet: UDF_Platform is older than this app. After an update, a platform admin runs Platform > Settings > Finish or repair once.");
    return { product_id: PID, items };
  },
  platform_add_product_item: (a) => {
    items = [...items, item(a.item_type, a.item_id, a.item_name, null)];
    return { product_id: PID, items };
  },
  platform_remove_product_item: ({ item_id }) => { items = items.filter((i) => i.item_id !== item_id); return { product_id: PID, items }; },
  list_workspaces: () => [{ id: "aaaaaaaa-0000-0000-0000-000000000001", displayName: "CRM Prod" }, { id: SALES_WS, displayName: "Sales Prod" }],
  // Any other workspace's list answers late, after the workspace picked next (see the Add a model test).
  list_items: async ({ workspace_id }) => (workspace_id === SALES_WS ? [
    { id: SM, type: "SemanticModel", displayName: "SM_Customer360" },
    { id: "dddddddd-0000-0000-0000-00000000000a", type: "SemanticModel", displayName: "SM_Sales_Daily" },
    { id: "cccccccc-0000-0000-0000-00000000000b", type: "Report", displayName: "Sales report" },
    { id: "cccccccc-0000-0000-0000-00000000000c", type: "Lakehouse", displayName: "LH_Sales" }]
    : await new Promise((r) => setTimeout(() => { lateLists += 1; r([]); }, 300))),
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
});
after(async () => { if (app) await app.close(); });

const callsOf = (method) => app.calls.filter((c) => c.method === method).map((c) => JSON.parse(JSON.stringify(c.params)));
const openList = async () => {
  await page().evaluate(() => { productsPage.open = null; productsPage.offer = "all"; goMeta("catalog"); });
  await page().waitForSelector('.mesh-products a.product-open:has-text("inventory")');
};
const openProduct = async () => {
  await openList();
  await page().click('a.product-open:has-text("customer_360")');
  await page().waitForSelector('.product-offer [data-group="Report"]');
};
async function until(ok, ms = 5000) {
  for (const end = Date.now() + ms; !ok(); await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("timed out waiting");
  }
}
const shownProducts = () => page().$$eval(".mesh-products a.product-open", (as) => as.map((a) => a.textContent.trim()));

test("the products list says what each offers, badges it with its model's endorsement and filters by it", { skip }, async () => {
  await openList();
  assert.deepStrictEqual(await page().$$eval(".product-offer-filters .seg button", (bs) => bs.map((b) => b.textContent)),
    ["All", "Has tables", "Has models", "Has reports"]);
  assert.ok(await page().$(".product-offer-filters .preview-tag"), "the filters are tagged Preview");
  const row = (name) => `.mesh-products tr:has(a.product-open:has-text("${name}"))`;
  assert.strictEqual((await page().textContent(`${row("customer_360")} .product-offer-summary`)).trim(), "3 tables, 1 model, 2 reports");
  assert.strictEqual((await page().textContent(`${row("orders")} .product-offer-summary`)).trim(), "2 tables, 1 model");
  assert.strictEqual((await page().textContent(`${row("inventory")} .product-offer-summary`)).trim(), "4 tables");
  assert.strictEqual((await page().textContent(`${row("customer_360")} .product-offer-badge`)).trim(), "Certified");
  assert.strictEqual((await page().textContent(`${row("orders")} .product-offer-badge`)).trim(), "Promoted");
  assert.strictEqual((await page().textContent(`${row("inventory")} .product-offer-badge`)).trim(), "Endorsement unknown");
  await page().click('.product-offer-filters button[data-offer="models"]');
  assert.deepStrictEqual(await shownProducts(), ["customer_360", "orders"]);
  await page().click('.product-offer-filters button[data-offer="reports"]');
  assert.deepStrictEqual(await shownProducts(), ["customer_360"]);
  await page().click('.product-offer-filters button[data-offer="tables"]');
  assert.deepStrictEqual(await shownProducts(), ["customer_360", "orders", "inventory"]);
  await page().click('.product-offer-filters button[data-offer="all"]');
  assert.strictEqual((await shownProducts()).length, 3);
});

test("a product's page groups its tables, semantic models and reports, each item with its own badge", { skip }, async () => {
  admin = false;
  await openProduct();
  const group = (g) => `.product-offer [data-group="${g}"]`;
  assert.ok(await page().$(".product-offer .preview-tag"));
  assert.match(await page().textContent(`${group("tables")} .product-offer-head`), /Tables\s*3/);
  assert.match(await page().textContent(`${group("SemanticModel")} .product-offer-head`), /Semantic models\s*1/);
  assert.match(await page().textContent(`${group("Report")} .product-offer-head`), /Reports\s*2/);
  assert.match(await page().textContent(`${group("tables")} .product-offer-item:has-text("dim_customer")`), /LH_Gold, Prod/);
  assert.strictEqual(await page().$(`${group("tables")} .endorse-badge`), null, "tables have no badge of their own");
  const badge = async (g, name) => (await page().textContent(`${group(g)} .product-offer-item:has-text("${name}") .endorse-badge`)).trim();
  assert.strictEqual(await badge("SemanticModel", "SM_Customer360"), "Certified");
  assert.strictEqual(await badge("Report", "Customer health"), "Promoted");
  assert.strictEqual(await badge("Report", "Churn watch"), "Endorsement unknown");
  await page().waitForSelector('.endorse-head:has-text("Certified")');
  assert.strictEqual((await page().textContent(".endorse-head")).trim(), "Certified", "the product's badge is its model's, not its lakehouse's");
  assert.strictEqual(await page().$('.product-offer button:has-text("Add a model")'), null, "only the owner or an admin adds");
  assert.strictEqual(await page().$('.product-offer button:has-text("Remove")'), null);
});

test("who may add or remove comes from the server's rule, not the owner field split in the app", { skip }, async () => {
  ownerEdits = true; // whoami's user isn't in the owner field, but the server says this person may edit
  try {
    await openProduct();
    await page().waitForSelector('.product-offer button:has-text("Add a model")');
    assert.ok(await page().$('.product-offer button:has-text("Remove")'));
  } finally {
    ownerEdits = false;
  }
});

test("How this works names every write: the catalog row and the audit entry", { skip }, async () => {
  const how = await page().textContent(".how-it-works");
  assert.match(how, /Add a model or Add a report writes one row to catalog\.product_item \(the item's id, type, workspace id and display name\) and an add product item entry to catalog\.audit/);
  assert.match(how, /Remove deletes that row and logs remove product item there/);
  assert.match(how, /Nothing changes in Fabric/);
});

test("Add a model picks a workspace, then a semantic model in it, and records only the item", { skip }, async () => {
  admin = true;
  await openProduct();
  const from = app.calls.length;
  await page().click('.product-offer button:has-text("Add a model")');
  await page().waitForSelector('[role=dialog][aria-label="Add a semantic model"] select:not([disabled])');
  assert.match(await page().textContent("[role=dialog]"), /Only items you can see in Fabric are listed\. The product records the item's id; nothing in Fabric changes\./);
  await page().selectOption('[role=dialog] select[aria-label="Workspace"]', SALES_WS);
  await page().waitForSelector('[role=dialog] label:has-text("SM_Sales_Daily")');
  assert.deepStrictEqual(await page().$$eval("[role=dialog] .product-item-pick label", (ls) => ls.map((l) => l.textContent)),
    ["SM_Customer360", "SM_Sales_Daily"], "only semantic models are offered");
  // The first workspace's list answers after this one: the dialog must still add from the workspace picked.
  await until(() => lateLists > 0);
  await page().evaluate(() => new Promise((r) => setTimeout(r, 50)));
  await page().click('[role=dialog] label:has-text("SM_Sales_Daily")');
  await page().click('[role=dialog] button:has-text("Add")');
  await page().waitForSelector('.product-offer [data-group="SemanticModel"] .product-offer-item:has-text("SM_Sales_Daily")');
  assert.deepStrictEqual(callsOf("platform_add_product_item"), [{ product_id: PID, item_type: "SemanticModel",
    item_id: "dddddddd-0000-0000-0000-00000000000a", workspace_id: SALES_WS, workspace_name: "Sales Prod", item_name: "SM_Sales_Daily" }]);
  const fabricCalls = [...new Set(app.calls.slice(from).map((c) => c.method).filter((m) => !m.startsWith("platform_")))];
  assert.deepStrictEqual(fabricCalls, ["list_workspaces", "list_items"], "the dialog only lists from Fabric");
  assert.match(await page().textContent('.product-offer [data-group="SemanticModel"] .product-offer-head'), /Semantic models\s*2/);
});

test("removing an item asks first, drawn as a danger", { skip }, async () => {
  const remove = '.product-offer [data-group="Report"] .product-offer-item:has-text("Churn watch") button:has-text("Remove")';
  await page().click(remove);
  await page().waitForSelector('[role=dialog] >> text="Remove Churn watch from customer_360?"');
  assert.ok(await page().$(".dialog-actions .danger-solid"));
  await page().click('.dialog-actions button:has-text("Cancel")');
  assert.deepStrictEqual(callsOf("platform_remove_product_item"), []);
  await page().click(remove);
  await page().click(".dialog-actions .danger-solid");
  await page().waitForFunction(() => !document.querySelector('.product-offer [data-group="Report"]').textContent.includes("Churn watch"));
  assert.deepStrictEqual(callsOf("platform_remove_product_item"), [{ product_id: PID, item_id: "ffffffff-0000-0000-0000-000000000007" }]);
});

test("an older UDF_Platform says to run Finish or repair", { skip }, async () => {
  older = true;
  try {
    await openList();
    await page().click('a.product-open:has-text("customer_360")');
    await page().waitForSelector(".product-offer .fail-text");
    assert.match(await page().textContent(".product-offer .fail-text"), /After an update, a platform admin runs Platform > Settings > Finish or repair once\./);
    assert.doesNotMatch(await page().textContent(".product-offer"), /LookupError/);
  } finally {
    older = false;
  }
});

test("no page errors", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
