// Lineage > Impact on a Data mesh entity page (dataops.js impactDialog), and the check before a save
// that would break something (confirmContractChange). Both are Preview.
// axe covers dialogs: dataops.js:impactDialog, dataops.js:confirmContractChange, dataops.js:openLineageDialog
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = (!playwright && "Playwright is not installed") || (!axeSource && "axe-core is not installed");

const TEXT = "name = \"silver.orders\"\n\n[contract.columns]\norder_id = \"int64\"\ncustomer_id = \"int64\"\n";
const ENTITY = {
  name: "silver.orders", layer: "silver", mode: "incremental", watermark: "modified_at", owner: "ana@contoso.com",
  path: "metadata/entities/silver.orders.toml", text: TEXT, errors: [],
  columns: { order_id: "int64", customer_id: "int64" }, keys: ["order_id"], surrogate_keys: [], derived: [], hook_adds: [],
  runs: [], custom: [], checks: 1, reads: ["bronze.orders"], impact: ["gold.fact_sales"], source: null,
};
const IMPACT = {
  entity: "silver.orders", column: "customer_id", change: "drop", new_name: null, new_type: null, type: "int64",
  items: [
    { kind: "table", name: "gold.fact_sales", severity: "breaks", detail: "table", entity: "gold.fact_sales",
      why: ["matches customer_sk on customer_id; once it's dropped, building gold.fact_sales fails"],
      chain: ["silver.orders.customer_id", "gold.fact_sales.customer_sk"] },
    { kind: "model", name: "SM_Gold", severity: "breaks", detail: "semantic model", entity: null,
      why: ["its relationship fact_sales[customer_sk] → dim_customer[customer_sk] loses a side"], chain: [] },
    { kind: "python", name: "transforms.churn:run", severity: "check", detail: "your Python, hook after_clean of gold.churn",
      entity: "gold.churn", why: ["reads silver.orders without naming customer_id; it may still use every column"], chain: [] },
  ],
  counts: { breaks: 2, check: 1, table_only: 4 }, table_only: ["a", "b", "c", "d"], model_layer: "gold",
};

let app;
const page = () => app.page;
const calls = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      meta_project: () => ({ path: "C:/mesh", name: "Mesh", environments: {} }),
      meta_entity: () => structuredClone(ENTITY),
      meta_runs_all: () => ({ runs: [] }),
      meta_entity_shortcuts: () => [],
      meta_lineage: () => ({ entity: "silver.orders", columns: { order_id: { how: "source", label: "inferred", inputs: [] },
        customer_id: { how: "source", label: "inferred", inputs: [] } } }),
      meta_impact: (p) => { calls.push(["meta_impact", p]); return { ...structuredClone(IMPACT), column: p.column, change: p.change }; },
      meta_edit_impact: (p) => {
        calls.push(["meta_edit_impact", p]);
        const renamed = structuredClone(IMPACT);
        renamed.items[0].why = ["matches customer_sk on customer_id; once it's renamed to cust_id, building gold.fact_sales fails"];
        return { entity: p.name, changes: p.text.includes("customer_id") ? [] : [{ ...renamed, change: "rename", new_name: "cust_id" }] };
      },
      meta_openlineage_preview: (p) => ({ env: p.env, namespace: "kratosforge://crm/dev", events: 2,
        left_out: [{ entity: "bronze.orders", why: "hasn't run in Dev" }] }),
      meta_openlineage_export: (p) => { calls.push(["meta_openlineage_export", p]);
        return { path: "C:/Users/ana/Downloads/crm-dev-openlineage.jsonl", events: 2, left_out: [] }; },
      meta_save_entity: (p) => { calls.push(["meta_save_entity", p]); return { ...structuredClone(ENTITY), text: p.text }; },
    },
  });
  await page().evaluate(() => openEntity("silver.orders"));
  await page().waitForSelector("#entity-toml");
});
after(async () => { await app?.close(); });

test("Impact lists what breaks and what to check, with counts and the path to the first", { skip }, async () => {
  await page().getByRole("button", { name: /^Impact/ }).click();
  await page().waitForSelector(".impact-row");
  const text = await page().locator("#dialog-root .dialog").innerText();
  assert.match(text, /Impact of a change: silver\.orders/);
  assert.match(text, /Breaks \(2\)/);
  assert.match(text, /Check \(1\)/);
  assert.match(text, /Will break/);
  assert.match(text, /Read the table, not this column/);
  assert.match(text, /loses a side/);
  assert.match(text, /How .*fact_sales.* is reached/);
  assert.deepStrictEqual(calls.at(-1), ["meta_impact", { name: "silver.orders", column: "order_id", change: "drop" }]);
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
});

test("changing the column or the change asks again", { skip }, async () => {
  await page().getByRole("combobox", { name: "Column" }).selectOption("customer_id");
  await page().getByRole("combobox", { name: "Change" }).selectOption("rename");
  await page().waitForFunction(() => document.querySelector(".impact-row"));
  const last = calls.filter((c) => c[0] === "meta_impact").at(-1);
  assert.deepStrictEqual(last, ["meta_impact", { name: "silver.orders", column: "customer_id", change: "rename" }]);
  await page().getByRole("button", { name: "Close" }).last().click();
});

test("a save that breaks something asks first; Cancel saves nothing", { skip }, async () => {
  const box = page().locator("#entity-toml");
  await box.fill(TEXT.replace("customer_id", "cust_id"));
  await page().getByRole("button", { name: "Save", exact: true }).click();
  await page().waitForSelector("#dialog-root .dialog .impact-row");
  const text = await page().locator("#dialog-root .dialog").innerText();
  assert.match(text, /This change breaks 2 things/);
  assert.match(text, /You're renaming customer_id to cust_id in silver\.orders\. These read it by its old name:/);
  assert.match(text, /once it's renamed to cust_id/);
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  await page().getByRole("button", { name: "Cancel" }).click();
  assert.ok(!calls.some((c) => c[0] === "meta_save_entity"), "nothing was saved");
});

test("Save anyway saves", { skip }, async () => {
  await page().getByRole("button", { name: "Save", exact: true }).click();
  await page().getByRole("button", { name: "Save anyway" }).click();
  await page().waitForTimeout(100);
  assert.strictEqual(calls.filter((c) => c[0] === "meta_save_entity").length, 1);
});

test("Export OpenLineage says what it writes and leaves out, then writes it", { skip }, async () => {
  await page().getByRole("button", { name: /^Impact/ }).click();
  await page().waitForSelector(".impact-row");
  await page().getByRole("button", { name: "Export OpenLineage..." }).click();
  await page().waitForSelector("#dialog-root .dialog .gate-list");
  const text = await page().locator("#dialog-root .dialog").innerText();
  assert.match(text, /Export lineage as OpenLineage/);
  assert.match(text, /Writes 2 OpenLineage run events/);
  assert.match(text, /Left out \(1\)/);
  assert.match(text, /bronze\.orders: hasn't run in Dev/);
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  await page().getByRole("button", { name: "Export", exact: true }).click();
  await page().waitForFunction(() => !document.querySelector("#dialog-root .dialog"));
  assert.deepStrictEqual(calls.at(-1), ["meta_openlineage_export", { env: "dev" }]);
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
