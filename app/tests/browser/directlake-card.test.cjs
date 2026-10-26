// The Direct Lake check on the Semantic model tab (docs/direct-lake-check.md): it starts on the workspace's
// capacity, checks only when asked, shows the worst first with a fix that opens where the change is made, and
// writes nothing. Fabric and the engine are mocked.
const { test, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.DIRECTLAKE_SHOTS;  // a folder: save screenshots of each step there

const PLAN = { layer: "gold", layers: ["gold"], name: "SM_Gold", workspace_name: "Sales Test", workspace_id: "ws",
  lakehouse: "LH_Gold", existing: { id: "m1", url: "https://app.fabric.microsoft.com/x" },
  tables: [{ name: "FactSales", entity: "gold.fact_sales", source: "gold.fact_sales", kind: "fact" },
           { name: "DimCustomer", entity: "gold.dim_customer", source: "gold.dim_customer", kind: "dimension" }],
  relationships: [] };
const LIMITS = { sku: "F32", files: 1000, row_groups: 1000, rows: 300000000, model_bytes: 40 * 1024 ** 3 };
const CHECK = { env: "test", name: "SM_Gold", workspace_name: "Sales Test", checked_at: new Date().toISOString(), sku: "F32",
  direct_lake: true, behavior: "automatic", storage: "sql", limits: LIMITS, model_bytes: 26 * 1024 ** 3, keys_checked: true,
  counts: { fail: 2, warn: 2, info: 1 }, unread: [],
  findings: [
    { severity: "fail", table: "DimCustomer", check: "Key not unique", column: "customer_sk", fix: "show_rows",
      message: "customer_sk has 3 values that appear more than once, so the relationship from FactSales fails at query time." },
    { severity: "fail", table: "FactSales", check: "Column missing", column: "discount_pct", fix: "update_model",
      message: "The model reads discount_pct, which gold.fact_sales no longer has. Visuals that use it show an error." },
    { severity: "warn", table: "FactSales", check: "Row groups", column: "", fix: "compact",
      message: "1,240 row groups; F32 allows 1,000 a table. Queries on it fall back to DirectQuery and run slower." },
    { severity: "warn", table: "FactWeb", check: "Parquet files", column: "", fix: "compact",
      message: "940 Parquet files; F32 allows 1,000 a table, so a few more loads will pass it." },
    { severity: "info", table: "FactSales", check: "New column", column: "channel", fix: "update_model",
      message: "gold.fact_sales has channel, which the model doesn't have yet." }],
  tables: [
    { table: "FactSales", delta_table: "gold.fact_sales", exists: true, rows: 212400000, files: 610, row_groups: 1240,
      row_groups_estimated: true, bytes: 18.2 * 1024 ** 3 },
    { table: "FactWeb", delta_table: "gold.fact_web", exists: true, rows: 88000000, files: 940, row_groups: 940,
      row_groups_estimated: false, bytes: 6.1 * 1024 ** 3 },
    { table: "DimCustomer", delta_table: "gold.dim_customer", exists: true, rows: 1200000, files: 4, row_groups: 4,
      row_groups_estimated: false, bytes: 96 * 1024 ** 2 }] };

const apps = [];
async function open(calls) {
  const app = await openApp({ mocks: {
    meta_model_plan: () => PLAN,
    meta_model_ossie_models: () => ({ env: "test", models: [{ id: "m1", name: "SM_Gold" }, { id: "m2", name: "Finance KPIs" }] }),
    meta_model_directlake_setup: (p) => { calls.push(["setup", p]); return { env: "test", sku: "F32", capacity_sku: "F32", skus: ["F2", "F32", "F64", "P1"] }; },
    meta_model_directlake_check: (p) => { calls.push(["check", p]); return CHECK; },
    meta_build_model: (p) => { calls.push(["build", p]); return null; },
  } });
  apps.push(app);
  await app.page.setViewportSize({ width: 1280, height: 1100 });
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

test("Check now lists the worst first against the workspace's capacity, with fixes, and writes nothing", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open(calls);
  const card = page.locator("#model-under-test .dl-card");
  await card.waitFor();
  await page.waitForFunction(() => document.querySelector(".dl-card select[aria-label='Capacity limits']")?.value === "F32");
  assert.deepStrictEqual(calls.filter(([n]) => n === "check"), [], "nothing is checked until asked");
  await shot(page, "1-card", "#model-under-test .dl-card");
  await card.locator("label:has-text('relationship keys') input").check();
  await card.locator("button:has-text('Check now')").click();
  await card.locator(".dl-out table").first().waitFor();
  assert.deepStrictEqual(calls.find(([n]) => n === "check")[1], { env: "test", name: "SM_Gold", sku: "F32", keys: true });
  const text = await card.textContent();
  assert.match(text, /Would fail2/);
  assert.match(text, /Past a limit, queries go to DirectQuery/);
  const rows = await card.locator(".dl-out > table tbody tr").allTextContents();
  assert.match(rows[0], /^Would fail/);
  assert.match(rows[0], /Open the table/);
  assert.match(rows[2], /Compact in Table health/);
  await card.locator("details summary:has-text('Every table')").click();
  assert.match(await card.locator("details").first().textContent(), /about 1,240/);
  assert.strictEqual(await card.locator("td.n.over").count(), 1);
  await shot(page, "2-findings", "#model-under-test .dl-card");
  assert.deepStrictEqual(calls.filter(([n]) => n === "build"), [], "checking builds nothing");
  await card.locator("button:has-text('Update model')").first().click();
  await page.waitForFunction(() => true);
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
    return body.querySelectorAll(".dl-card").length;
  });
  assert.strictEqual(count, 0);
});
