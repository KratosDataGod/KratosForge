// The Apache Ossie card on the Semantic model tab (docs/semantic-model-ossie.md): Export shows the report and
// saves the file; Import previews every change, asks before writing, and writes only after the confirm. Fabric
// and the engine are mocked.
const { test, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.OSSIE_SHOTS;  // a folder: save screenshots of each step there

const PLAN = { layer: "gold", layers: ["gold"], name: "SM_Gold", workspace_name: "Sales Test", workspace_id: "ws",
  lakehouse: "LH_Gold", existing: { id: "m1", url: "https://app.fabric.microsoft.com/x" },
  tables: [{ name: "fact_sales", entity: "gold.fact_sales", source: "dbo.fact_sales", kind: "fact" }], relationships: [] };
const YAML = "version: 0.2.0.dev0\nname: SM_Gold\ndatasets:\n- name: fact_sales\n  source: dbo.fact_sales\n";
const EXPORT = { yaml: YAML, model: "SM_Gold", name: "SM_Gold", version: "0.2.0.dev0", path: "semantic/SM_Gold.ossie.yaml",
  counts: { datasets: 6, fields: 61, metrics: 22, portable_metrics: 16, relationships: 5 },
  report: [{ object: "model", kind: "kept", message: "row-level security roles ('roles') has no Apache Ossie counterpart; preserved in the Power BI stash" },
           { object: "table 'dim_customer'", kind: "kept", message: "hierarchies ('hierarchies') has no Apache Ossie counterpart; preserved" }] };
const PREVIEW = { env: "test", name: "SM_Gold", mode: "update", exists: true, workspace_name: "Sales Test", removable: ["fact_sales[fact_sales rows]"],
  changes: [{ change: "add", kind: "table", object: "dim_channel", detail: "2 columns, 0 measures" },
            { change: "add", kind: "measure", object: "fact_sales[net revenue]", detail: "SUM('fact_sales'[amount])" },
            { change: "needs_dax", kind: "measure", object: "fact_sales[repeat rate]", detail: "Not translated" },
            { change: "remove", kind: "measure", object: "fact_sales[fact_sales rows]", detail: "" }],
  report: [{ object: "relationship 'orders_to_returns'", kind: "lost", message: "joins on 2 columns; Power BI joins on one" }] };

const apps = [];
async function open(calls, extra = {}) {
  const log = (name) => (p) => { calls.push([name, p]); return null; };
  const app = await openApp({ mocks: {
    meta_model_plan: () => PLAN,
    meta_model_ossie_models: () => ({ env: "test", models: [{ id: "m1", name: "SM_Gold" }, { id: "m2", name: "Finance KPIs" }] }),
    meta_model_ossie_export: (p) => { calls.push(["export", p]); return EXPORT; },
    meta_model_ossie_folders: () => ({ folders: [{ path: "semantic/Finance KPIs.SemanticModel", name: "Finance KPIs" }] }),
    meta_model_ossie_export_folder: (p) => { calls.push(["export_folder", p]); return { ...EXPORT, model: "Finance KPIs", path: "semantic/Finance KPIs.ossie.yaml" }; },
    meta_model_ossie_save: (p) => { calls.push(["save", p]); return { path: p.path }; },
    meta_model_ossie_read: (p) => { calls.push(["read", p]); return { path: p.path, text: YAML }; },
    meta_model_ossie_preview: (p) => { calls.push(["preview", p]); return PREVIEW; },
    meta_model_ossie_import: (p) => {
      calls.push(["import", p]);
      return p.confirmed
        ? { name: "SM_Gold", id: "m1", created: false, mode: "update", counts: { add: 2, needs_dax: 1 }, changes: PREVIEW.changes,
            report: PREVIEW.report, needs_dax: ["fact_sales[repeat rate]"], url: "https://app.fabric.microsoft.com/x" }
        : { confirm: { name: "SM_Gold", mode: "update", workspace_name: "Sales Test", counts: { add: 2, needs_dax: 1 } } };
    },
    meta_model_tmdl_save: log("tmdl"),
    ...extra,
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

test("Export shows what's portable and what only Fabric keeps, then saves the file into the project", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open(calls);
  const card = page.locator("#model-under-test .ossie-card");
  await card.waitFor();
  await page.waitForFunction(() => document.querySelectorAll(".ossie-card select option").length === 3);
  await card.locator("button:has-text('Export to Ossie')").click();
  await card.locator(".pass-box").waitFor();
  assert.match(await card.locator(".pass-box").textContent(), /Converted SM_Gold: 6 datasets, 61 fields, 22 measures \(16 also as SQL\), 5 relationships\. 2 things only Power BI understands are kept/);
  assert.match(await card.textContent(), /Kept for Fabric only \(2\)/);
  assert.match(await card.textContent(), /Not converted \(0\)/);
  await shot(page, "1-export", "#model-under-test .ossie-card");
  await card.locator("button:has-text('Save to Git')").click();
  await page.waitForFunction(() => document.body.textContent.includes("ready to commit"));
  assert.deepStrictEqual(calls.find(([n]) => n === "export")[1], { env: "test", name: "SM_Gold", translate_dax: true });
  assert.deepStrictEqual(calls.find(([n]) => n === "save")[1], { path: "semantic/SM_Gold.ossie.yaml", text: YAML });
  assert.deepStrictEqual(errors, []);
});

test("Import previews every change and writes only after the confirm, keeping what's ticked", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open(calls);
  await page.locator("#model-under-test button:has-text('Import from Ossie')").click();
  const dialog = page.locator(".dialog");
  await dialog.locator("input[aria-label='Ossie file in the project']").fill("semantic/orders.ossie.yaml");
  await dialog.locator("button:has-text('Read')").click();
  await dialog.locator(".ossie-preview table").waitFor();
  const text = await dialog.textContent();
  assert.match(text, /What changes in SM_Gold/);
  assert.match(text, /Needs DAX/);
  assert.match(text, /Can't carry over \(1\)/);
  assert.strictEqual(await dialog.locator("button:has-text('Update SM_Gold...')").count(), 1);
  await shot(page, "2-import-preview", ".dialog");
  // Untick "Keep it": the measure goes in remove.
  await dialog.locator("label:has-text('Keep it') input").uncheck();
  await dialog.locator("button:has-text('Update SM_Gold...')").click();
  await page.locator(".dialog:has-text('Update SM_Gold?')").waitFor();
  assert.ok(calls.filter(([n]) => n === "import").every(([, p]) => !p.confirmed), "nothing is written before the confirm");
  assert.match(await page.locator(".dialog").textContent(), /1 measure is BLANK\(\) until someone writes it in DAX\. No refresh runs\./);
  await shot(page, "3-confirm", ".dialog");
  await page.locator(".dialog button:has-text('Update SM_Gold')").last().click();
  await page.locator("#model-under-test .ossie-done").waitFor();
  const write = calls.filter(([n]) => n === "import").pop()[1];
  assert.strictEqual(write.confirmed, true);
  assert.deepStrictEqual(write.remove, ["fact_sales[fact_sales rows]"]);
  assert.strictEqual(write.mode, "update");
  const done = await page.locator("#model-under-test .ossie-done").textContent();
  assert.match(done, /Updated SM_Gold from semantic\/orders\.ossie\.yaml: 2 added\./);
  assert.match(done, /1 measure needs DAX before reports use it/);
  if (SHOTS) await page.waitForTimeout(800);  // the dialog's exit animation
  await shot(page, "4-result", "#model-under-test .ossie-card");
  await page.locator("#model-under-test button:has-text('Save TMDL to Git')").click();
  await page.waitForFunction(() => true);
  assert.deepStrictEqual(errors, []);
});

test("A model kept in Git exports on this PC, and Fabric's answer shows when it can't list models", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open(calls, { meta_model_ossie_models: () => { throw new Error("Semantic models live in a Fabric workspace. Switch to Test or Prod."); } });
  const card = page.locator("#model-under-test .ossie-card");
  await page.waitForFunction(() => document.querySelectorAll(".ossie-card optgroup").length === 2);
  assert.strictEqual(await card.locator("select").inputValue(), "git:semantic/Finance KPIs.SemanticModel");
  assert.match(await card.locator("optgroup[label='In Fabric'] option").textContent(), /Switch to Test or Prod/);
  await shot(page, "5-git-model", "#model-under-test .ossie-card");
  await card.locator("button:has-text('Export to Ossie')").click();
  await card.locator(".pass-box").waitFor();
  assert.deepStrictEqual(calls.find(([n]) => n === "export_folder")[1], { path: "semantic/Finance KPIs.SemanticModel", translate_dax: true });
  assert.ok(!calls.some(([n]) => n === "export"), "nothing is read from Fabric");
  assert.strictEqual(await card.locator("input[aria-label='Save in the project as']").inputValue(), "semantic/Finance KPIs.ossie.yaml");
  assert.deepStrictEqual(errors, []);
});

test("With preview features hidden the card isn't there", { skip }, async () => {
  const calls = [];
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
    return body.querySelectorAll(".ossie-card").length;
  });
  assert.strictEqual(count, 0);
  assert.deepStrictEqual(calls, []);
});
