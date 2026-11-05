// Mesh wave 2, S3: a product's promise to readers on its page, and whether it holds now for its own team
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let mine = null;
let incidents = [];
let saved = null;
const promise = { within_hours: 24, by: "06:00", max_rejected_pct: 1, envs: ["prod"] };
const base = { project: "crm", description: "", owner: "bo@contoso.com", access: "public", status: "published", version: "1",
  domain_id: "sales", ports: [{ port: "customers", entity: "silver.customers", env: "prod" }], consumers: [], granted: [],
  can_link: true, why: "", linked: false };
const products = () => [{ ...base, id: "crm/c360", name: "c360", promise }, { ...base, id: "crm/orders", name: "orders", promise: null }];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_products: () => ({ project: mine ? { name: mine } : null, products: products(), can_follow: true }),
    platform_product_trust: ({ product_id }) => ({ product_id, name: product_id.split("/")[1], owner: "", admin: false,
      can_edit: false, items: [] }),
    incidents_list: ({ env }) => ({ env, incidents, observation: { state: "live" } }),
    mesh_save_product: (params) => { saved = params; return {}; },
    meta_project: () => ({ path: "C:/work/crm", name: "crm", version: "3", templates: {}, layers: [], products: [], links: [],
      entities: [{ name: "silver.customers", layer: "silver" }], environments: {}, errors: [], run_order: [],
      project: { name: "crm", domain: "sales" } }),
    mesh_overview: () => ({ project: { name: "crm" }, published: [], platform: {}, products: [{ name: "c360", title: "C360", description: "",
      owner: "bo@contoso.com", access: "public", status: "published", version: "1", ports: [{ name: "customers", entity: "silver.customers" }],
      promise: { within_hours: 24, by: "06:00", max_rejected_pct: 1 } }] }),
  } });
});
after(async () => { if (app) await app.close(); });

const openProduct = async (name) => {
  await page().evaluate(() => { productsPage.open = null; catalogPage.tab = "products"; go("onelake"); render(); });
  await page().waitForSelector(`a.product-open:has-text("${name}")`);
  await page().click(`a.product-open:has-text("${name}")`);
  await page().waitForSelector(".product-promise");
};

test("a reader sees what the product promises, and no Now column", { skip }, async () => {
  mine = null;
  await openProduct("c360");
  const rows = await page().$$eval(".product-promise tbody tr", (trs) => trs.map((t) => t.textContent.trim()));
  assert.deepStrictEqual(rows, ["Loaded at least every 24 h", "Loaded by 06:00 each day", "Under 1 % of rows rejected"]);
  assert.match(await page().textContent(".product-promise"), /In Prod, from the product's file/);
  assert.strictEqual(await page().$(".product-promise .promise-now"), null);
  assert.strictEqual(await page().$('.product-promise button:has-text("Edit promise")'), null);
});

test("a product that promises nothing says so", { skip }, async () => {
  mine = null;
  await openProduct("orders");
  assert.match(await page().textContent(".product-promise"), /promises nothing/);
});

test("its own team sees each promise kept or broken, from the open incidents", { skip }, async () => {
  mine = "crm";
  incidents = [
    { id: "i1", kind: "promise", state: "open", name: "silver.customers", env: "prod",
      evidence: { products: ["c360"], reasons: ["c360: 2.4 % of rows rejected in the newest load, promised under 1 %"] } },
    { id: "i2", kind: "promise", state: "resolved", name: "silver.customers", env: "prod",
      evidence: { products: ["c360"], reasons: ["c360: last updated 30 h ago, promised every 24 h"] } },
  ];
  try {
    await openProduct("c360");
    await page().waitForSelector('.product-promise tr[data-promise="rejected"] .pill-s');
    const marks = await page().$$eval(".product-promise tbody tr", (trs) => trs.map((t) => [t.dataset.promise, t.querySelector(".pill-s")?.textContent]));
    assert.deepStrictEqual(marks, [["fresh", "Kept"], ["fresh", "Kept"], ["rejected", "Broken"]]);
    assert.ok(await page().$('.product-promise button:has-text("Edit promise")'));
    assert.ok(await page().$('.product-promise tr[data-promise="rejected"] button:has-text("Open the incident")'));
    assert.match(await page().textContent('.product-promise tr[data-promise="rejected"] .promise-why'), /^2\.4 % of rows rejected/);
    if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".product-promise"), []);
    // Edit promise opens the product's own dialog, at its promise.
    await page().click('.product-promise button:has-text("Edit promise")');
    await page().waitForSelector(".dialog .product-promise-fields");
    assert.strictEqual(await page().inputValue('.product-promise-fields input[type="time"]'), "06:00");
    if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
    await page().click('.dialog button:has-text("Cancel")');
  } finally {
    mine = null;
    incidents = [];
  }
});

test("a late incident counts only when it says it breaks this product's promise", { skip }, async () => {
  mine = "crm";
  incidents = [{ id: "i3", kind: "late", state: "open", name: "silver.customers", env: "prod", evidence: { promise_products: ["other"] } }];
  try {
    await openProduct("c360");
    await page().waitForSelector('.product-promise tr[data-promise="fresh"] .pill-s');
    assert.strictEqual(await page().textContent('.product-promise tr[data-promise="fresh"] .pill-s'), "Kept");
    incidents[0].evidence.promise_products = ["c360"];
    await openProduct("c360");
    await page().waitForSelector('.product-promise tr[data-promise="fresh"] .pill-s.wrn');
  } finally {
    mine = null;
    incidents = [];
  }
});

test("the product dialog keeps the promise and edits it", { skip }, async () => {
  const x = { name: "c360", title: "C360", description: "d", owner: "bo@contoso.com", access: "public", status: "published",
    version: "2", ports: [{ name: "customers", entity: "silver.customers" }], promise: { within_hours: 24, max_rejected_pct: 0, envs: ["prod"] } };
  const open = async (item) => {
    await page().evaluate((item) => productDialog({ entities: [{ name: "silver.customers", layer: "silver" }] }, item, () => {}), item);
    await page().waitForSelector(".product-promise-fields");
  };
  await open(x);
  await page().click('.dialog button:has-text("Save")');
  await page().waitForFunction(() => !document.querySelector(".product-promise-fields"));
  assert.deepStrictEqual(saved.product.promise, { within_hours: 24, max_rejected_pct: 0 });  // an unchanged save keeps it
  await open(x);
  await page().fill('.product-promise-fields input[type="time"]', "06:30");
  await page().selectOption(".product-promise-fields select", "test,prod");
  await page().click('.dialog button:has-text("Save")');
  await page().waitForFunction(() => !document.querySelector(".product-promise-fields"));
  assert.deepStrictEqual(saved.product.promise, { within_hours: 24, by: "06:30", max_rejected_pct: 0, envs: ["test", "prod"] });
  await open({ ...x, promise: null });
  await page().click('.dialog button:has-text("Save")');
  await page().waitForFunction(() => !document.querySelector(".product-promise-fields"));
  assert.strictEqual(saved.product.promise, null);
});

test("saving the dialog keeps envs the select has no preset for", { skip }, async () => {
  for (const envs of [["dev", "test", "prod"], ["test"], ["dev", "prod"]]) {
    const x = { name: "c360", title: "C360", description: "d", owner: "bo@contoso.com", access: "public", status: "published",
      version: "2", ports: [{ name: "customers", entity: "silver.customers" }], promise: { within_hours: 24, envs } };
    await page().evaluate((item) => productDialog({ entities: [{ name: "silver.customers", layer: "silver" }] }, item, () => {}), x);
    await page().waitForSelector(".product-promise-fields");
    await page().fill('.dialog label.field:has-text("Title") input', "Customer 360");
    await page().click('.dialog button:has-text("Save")');
    await page().waitForFunction(() => !document.querySelector(".product-promise-fields"));
    assert.strictEqual(saved.product.title, "Customer 360");
    assert.deepStrictEqual(saved.product.promise, { within_hours: 24, envs }, envs.join(","));
  }
});
