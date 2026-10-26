// The data dictionary on the Semantic model tab (docs/data-dictionary.md): it lists the model's descriptions,
// exports a CSV, and an import shows every change before saving, then saves only from that preview. The engine
// is mocked.
const { test, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.DICTIONARY_SHOTS;  // a folder: save screenshots of each step there

const PLAN = { layer: "gold", layers: ["gold"], name: "SM_Gold", workspace_name: "Sales Test", workspace_id: "ws",
  lakehouse: "LH_Gold", existing: { id: "m1", url: "https://app.fabric.microsoft.com/x" },
  tables: [{ name: "fact_sales", entity: "gold.fact_sales", source: "gold.fact_sales", kind: "fact" }], relationships: [] };
const item = (table, name, kind, type, description, classification = "", hidden = false) =>
  ({ table, name, kind, type, description, classification, hidden, entity: `gold.${table}` });
const ITEMS = [
  item("fact_sales", "", "table", "", "One row per order line, loaded daily from the ERP.", "internal"),
  item("fact_sales", "fact_sales rows", "measure", "", "Rows in fact_sales. Written by KratosForge."),
  item("fact_sales", "fact_sales total net_amount", "measure", "", "Sum of net_amount. Written by KratosForge."),
  item("fact_sales", "order_id", "column", "int64", "The order's number in the ERP."),
  item("fact_sales", "net_amount", "column", "float64", ""),
  item("dim_customer", "", "table", "", "One row per customer."),
  item("dim_customer", "customer_sk", "column", "int64", "", "", true),
  item("dim_customer", "email", "column", "string", "Customer's contact email.", "confidential")];
const DICT = { layer: "gold", model: "SM_Gold", items: ITEMS, problem: null,
  counts: { tables: 2, tables_described: 2, columns: 4, columns_described: 2, measures: 2 } };
const PREVIEW = { layer: "gold", problem: null, unchanged: 5,
  changes: [{ entity: "gold.fact_sales", column: "net_amount", object: "fact_sales.net_amount", field: "description", now: "", new: "Amount after discount, before tax." },
            { entity: "gold.dim_customer", column: "customer_sk", object: "dim_customer.customer_sk", field: "classification", now: "", new: "internal" }],
  skipped: [{ row: 10, object: "fact_sales.old_discount", reason: "old_discount isn't in gold.fact_sales any more" }] };

const apps = [];
async function open(calls) {
  const app = await openApp({ mocks: {
    meta_model_plan: () => PLAN,
    meta_model_ossie_models: () => ({ env: "test", models: [{ id: "m1", name: "SM_Gold" }] }),
    meta_model_dictionary: (p) => { calls.push(["dictionary", p]); return DICT; },
    meta_model_dictionary_export: (p) => { calls.push(["export", p]); return { path: "C:\\Users\\me\\Downloads\\SM_Gold data dictionary.csv", rows: 8 }; },
    meta_model_dictionary_save: (p) => { calls.push(["save", p]); return { path: "semantic/SM_Gold.dictionary.csv", rows: 8 }; },
    meta_model_dictionary_preview: (p) => { calls.push(["preview", p]); return PREVIEW; },
    meta_model_dictionary_import: (p) => { calls.push(["import", p]); return { saved: 2, changes: PREVIEW.changes, skipped: PREVIEW.skipped }; },
  } });
  apps.push(app);
  await app.page.setViewportSize({ width: 1280, height: 1000 });
  await app.page.waitForFunction(() => typeof renderModelTab === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async () => {
    await loadSettings();
    state.user = { name: "Chris" };
    state.meta.env = "test";
    state.meta.tab = "model";
    state.view = "metadata";
    const body = document.createElement("div");
    body.id = "model-under-test";
    document.querySelector("main, #main, body").prepend(body);
    await renderModelTab(body);
  });
  return app;
}

const shot = async (page, name, el) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await (el ? page.locator(el).first() : page).screenshot({ path: path.join(SHOTS, `${name}.png`) });
};

after(async () => { for (const app of apps) await app.close(); });

test("The card lists descriptions and what's missing, and exports or saves the CSV", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open(calls);
  const card = page.locator("#model-under-test .dict-card");
  await card.locator(".dict-out table").waitFor();
  const text = await card.textContent();
  assert.match(text, /2 of 4 columns described/);
  assert.match(text, /2 columns have no description/);
  assert.match(text, /No description/);
  await shot(page, "1-dictionary", "#model-under-test .dict-card");
  await card.locator("button:has-text('Export CSV')").click();
  await card.locator("button:has-text('Save to the project')").click();
  await page.waitForFunction(() => document.body.textContent.includes("ready to commit"));
  assert.deepStrictEqual(calls.filter(([n]) => n === "export" || n === "save").map(([n, p]) => [n, p]),
    [["export", { layer: "gold" }], ["save", { layer: "gold" }]]);
  assert.deepStrictEqual(errors, []);
});

test("Import shows every change and skipped row, and saves only from the preview", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open(calls);
  await page.locator("#model-under-test button:has-text('Import descriptions')").click();
  const dialog = page.locator(".dialog");
  assert.strictEqual(await dialog.locator("button:has-text('Save changes')").isDisabled(), true, "nothing to save before a file");
  await dialog.locator("input[type=file]").setInputFiles({ name: "SM_Gold data dictionary.csv", mimeType: "text/csv",
    buffer: Buffer.from("Table,Column or measure,Kind,Type,Description,Classification,Hidden,Entity\r\n") });
  await dialog.locator(".dict-preview table").waitFor();
  const text = await dialog.textContent();
  assert.match(text, /2 to change/);
  assert.match(text, /5 unchanged/);
  assert.match(text, /Skipped \(1\)/);
  assert.match(text, /old_discount isn't in gold\.fact_sales any more/);
  assert.ok(calls.every(([n]) => n !== "import"), "nothing is saved before the button");
  await shot(page, "2-import-preview", ".dialog");
  await dialog.locator("button:has-text('Save 2 changes')").click();
  await page.waitForFunction(() => !document.querySelector(".dialog"));
  const save = calls.find(([n]) => n === "import")[1];
  assert.strictEqual(save.confirmed, true);
  assert.strictEqual(save.layer, "gold");
  assert.deepStrictEqual(errors, []);
});

test("With preview features hidden the card isn't there", { skip }, async () => {
  const app = await openApp({ mocks: { meta_model_plan: () => PLAN, app_settings: () => ({ show_preview: { value: false } }) } });
  apps.push(app);
  await app.page.waitForFunction(() => typeof renderModelTab === "function" && !!document.querySelector(".rail-item"));
  const count = await app.page.evaluate(async () => {
    await loadSettings();
    state.user = { name: "Chris" };
    Object.assign(state.meta, { env: "test", tab: "model" });
    state.view = "metadata";
    const body = document.createElement("div");
    document.body.prepend(body);
    await renderModelTab(body);
    return body.querySelectorAll(".dict-card").length;
  });
  assert.strictEqual(count, 0);
});
