// The publish-steps card for a Fabric Semantic View (OSSIE-005b, docs/semantic-model-ossie.md): it reads the view's
// saved files, links each step's Learn section, and has no button that writes to Fabric. The engine is mocked.
// axe covers dialogs: meta.js:svReportDialog
const { test, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings, settleForAxe } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const skipAxe = skip || (!axeSource && "axe-core is not installed");
const SHOT = process.env.SV_CARD_SHOT;  // a .png path: the Ready card at deviceScaleFactor 2

const PLAN = { layer: "gold", layers: ["gold"], name: "Sales", workspace_name: "Sales Dev", workspace_id: "ws",
  lakehouse: "lh_sales_gold", existing: null, tables: [], relationships: [] };
const LEARN = "https://learn.microsoft.com/en-us/fabric/fundamentals/";
const VALID = { valid: true, status: "Valid", diagnostics: [], warnings: [], files_checked: true,
  files: { "metrics.yaml": { valid: true, diagnostics: [], warnings: [] }, "schema.yaml": { valid: true, diagnostics: [], warnings: [] } },
  counts: { yaml_files: 2, yaml_bytes: 16000, tmdl_bytes: null, ignored: [] } };
const LEFT = [
  { object: "dataset 'fact_sales' primary_key", kind: "primary_key", construct: "primary_key", excluded: false, rule: "fabric/omitted",
    reason: "left out of the view: Dataset primary_key is omitted with warnings.", source: `${LEARN}semantic-views-yaml-reference` },
  { object: "dataset 'dim_customer' primary_key", kind: "primary_key", construct: "primary_key", excluded: false, rule: "fabric/omitted",
    reason: "left out of the view: Dataset primary_key is omitted with warnings.", source: `${LEARN}semantic-views-yaml-reference` },
  { object: "model ai_context", kind: "ai_context", construct: "ai_context", excluded: false, rule: "fabric/omitted",
    reason: "left out of the view: All ai_context is omitted with warnings.", source: `${LEARN}semantic-views-yaml-reference` },
];
const READY = { views: ["Sales"], view: "Sales", folder: "semantic-views/Sales", exists: true, files: ["metrics.yaml", "schema.yaml"],
  files_summary: [
    { path: "schema.yaml", bytes: 12000, datasets: 4, relationships: 3, metrics: 0, dax_metrics: 0 },
    { path: "metrics.yaml", bytes: 4000, datasets: 0, relationships: 0, metrics: 9, dax_metrics: 9 }],
  validation: VALID, valid: true, published: false,
  source: { path: "semantic/Sales.SemanticModel", split: "schema_metrics", same: true, left_out: LEFT, warnings: [] } };
const FAILED = { ...READY, valid: false, source: null,
  files_summary: [{ path: "metrics.yaml", bytes: 4000, datasets: 0, relationships: 0, metrics: 9, dax_metrics: 8 }, READY.files_summary[0]],
  validation: { ...VALID, valid: false, status: "Invalid", files: { ...VALID.files, "metrics.yaml": { valid: false, warnings: [], diagnostics: [
    { path: "$.metrics[3].expression", rule: "fabric/metric-dax", value: "", message: "has no DAX expression, and Fabric publishes only DAX",
      fix: "Add a DAX expression for this metric." }] } } } };

const apps = [];
async function open(calls, status, extra = {}, scale = 1) {
  const app = await openApp({ scale, mocks: {
    meta_model_plan: () => PLAN,
    meta_model_ossie_models: () => ({ env: "test", models: [] }),
    meta_model_ossie_folders: () => ({ folders: [{ path: "semantic/Sales.SemanticModel", name: "Sales" }], unavailable: "" }),
    meta_model_semantic_view_status: (p) => {
      calls.push(["status", p]);
      const now = typeof status === "function" ? status() : status;
      if (!p.view_name) return { views: now.views, folder: "semantic-views" };
      return now;
    },
    meta_model_semantic_view_reveal: (p) => {
      calls.push(["reveal", p]);
      return { folder: "semantic-views/Sales", shown: "C:\\proj\\semantic-views\\Sales" };
    },
    ...extra,
  } });
  apps.push(app);
  await app.page.setViewportSize({ width: 1180, height: 1000 });
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

after(async () => { for (const app of apps) await app.close(); });

test("Ready: the files, where they go and every step with its Learn link; only copy and open-folder buttons", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open(calls, READY, {}, SHOT ? 2 : 1);
  const card = page.locator("#model-under-test .sv-card");
  await card.locator(".sv-files").waitFor();
  // Under the Apache Ossie card.
  assert.ok(await page.evaluate(() => {
    const o = document.querySelector("#model-under-test .ossie-card");
    return o && o.nextElementSibling === document.querySelector("#model-under-test .sv-card");
  }), "the card sits right under the Apache Ossie card");
  const text = await card.textContent();
  assert.match(text, /Publish the Sales semantic view in Fabric/);
  assert.match(text, /KratosForge doesn't publish semantic views, and nothing on this card writes to Fabric\./);
  assert.match(text, /Passed Fabric's documented checks: version 0\.2\.0\.dev0, DAX on every metric, single-column relationships, 2 of 20 files and 16 KB of 16 MB\./);
  const rows = await card.locator(".sv-files tbody tr").allTextContents();
  assert.deepStrictEqual(rows, ["schema.yaml4 datasets, 3 relationships12 KB", "metrics.yaml9 metrics, all DAX4 KB"]);
  assert.match(text, /semantic-views\\Sales\\ in the project folder/);
  assert.match(text, /3 left out The view publishes without 2 primary keys and 1 ai_context note/);
  assert.match(await card.locator(".sv-path").textContent(), /^lh_sales_gold > Context\/Semantic Views\/Sales\//);
  assert.match(text, /Lakehouse lh_sales_gold in workspace Sales Dev, which the view's datasets read\./);
  assert.match(text, /so a Sales and a sales folder can't both publish/);
  assert.match(text, /Published model: not read\./);
  // Every step links its Learn section; nothing is dimmed.
  const learn = await card.locator(".sv-steps a").evaluateAll((as) => as.map((a) => [a.textContent, a.title]));
  assert.deepStrictEqual(learn, [
    ["Learn: Create a view ↗", `${LEARN}semantic-views-create#create-a-view-with-the-starter-file`],
    ["Learn: Upload files ↗", `${LEARN}semantic-views-create#upload-existing-yaml-files`],
    ["Learn: Edit and save ↗", `${LEARN}semantic-views-create#edit-and-save`],
    ["Learn: Publish a view ↗", `${LEARN}semantic-views-publish#publish-a-view`],
    ["Learn: Check the result ↗", `${LEARN}semantic-views-publish#check-the-result`]]);
  assert.strictEqual(await card.locator(".sv-dim").count(), 0);
  // The only buttons copy text or open the folder on this PC.
  assert.deepStrictEqual((await card.locator("button").allTextContents()).map((t) => t.trim()), ["Open folder", "Copy path"]);
  await card.locator(".sv-steps a").first().click();
  const opened = await page.evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url));
  assert.deepStrictEqual(opened, [`${LEARN}semantic-views-create#create-a-view-with-the-starter-file`]);
  await card.locator("button:has-text('Open folder')").click();
  await page.waitForFunction(() => document.body.textContent.includes("in File Explorer"));
  assert.deepStrictEqual(calls.filter(([kind]) => kind === "reveal").map(([, p]) => p), [{ view_name: "Sales" }]);
  assert.deepStrictEqual(calls.find(([, p]) => p.path)[1], { view_name: "Sales", path: "semantic/Sales.SemanticModel" });
  assert.ok(!calls.some(([n]) => n !== "status" && n !== "reveal"), "the card only reads the saved files and opens their folder");
  if (SHOT) {
    await page.evaluate(() => document.getElementById("toast")?.remove());
    fs.mkdirSync(path.dirname(SHOT), { recursive: true });
    await card.screenshot({ path: SHOT });
  }
  assert.deepStrictEqual(errors, []);
});

test("Ready card and its report pass axe", { skip: skipAxe }, async () => {
  const { page } = await open([], READY);
  const card = page.locator("#model-under-test .sv-card");
  await card.locator(".sv-files").waitFor();
  await settleForAxe(page);
  assert.deepStrictEqual(await axeFindings(page, "#model-under-test .sv-card"), []);
  await card.locator("a:has-text('See the report')").click();
  const dialog = page.locator(".dialog");
  await dialog.waitFor();
  assert.strictEqual(await dialog.locator("tbody tr").count(), 3);
  assert.match(await dialog.textContent(), /dataset 'fact_sales' primary_key/);
  await settleForAxe(page);
  assert.deepStrictEqual(await axeFindings(page, ".dialog"), []);
});

test("A failed check shows its fix, and steps 2 to 5 wait with Fix the files first", { skip }, async () => {
  const { page, errors } = await open([], FAILED);
  const card = page.locator("#model-under-test .sv-card");
  await card.locator(".sv-files").waitFor();
  const text = await card.textContent();
  assert.match(text, /Failed1 check failed, so Fabric would refuse to publish these files/);
  assert.match(text, /metrics\.yaml \$\.metrics\[3\]\.expression has no DAX expression, and Fabric publishes only DAX\. Fix: Add a DAX expression for this metric\./);
  assert.match(text, /9 metrics, 8 with DAX/);
  assert.deepStrictEqual(await card.locator(".sv-steps li").evaluateAll((lis) => lis.map((li) => li.classList.contains("sv-dim"))),
    [false, true, true, true, true]);
  assert.strictEqual(await card.locator(".sv-steps .sv-wait:has-text('Fix the files first')").count(), 4);
  assert.strictEqual(await card.locator(".sv-steps li a").count(), 5, "the Learn links stay");
  assert.doesNotMatch(text, /left out/, "what's left out shows only when the files match the model");
  assert.deepStrictEqual(errors, []);
});

test("No files yet: only Save the view's files first, which previews, confirms, then saves", { skip }, async () => {
  const calls = [];
  let saved = false;
  const { page, errors } = await open(calls, () => (saved ? READY : { views: [] }), {
    meta_model_semantic_view_save: (p) => {
      calls.push(["save", p]);
      if (!p.confirmed) {
        return { confirm: { view: "Sales", folder: "semantic-views/Sales", files: ["semantic-views/Sales/metrics.yaml", "semantic-views/Sales/schema.yaml"],
          removed: [], token: "t0k", report: {} } };
      }
      saved = true;
      return { view: "Sales", folder: "semantic-views/Sales", files: [], removed: [], report: {} };
    },
  });
  const card = page.locator("#model-under-test .sv-card");
  await card.locator(".sv-first").waitFor();
  assert.match(await card.textContent(), /Save the view's files first/);
  assert.strictEqual(await card.locator(".sv-steps").count(), 0, "no steps until there are files");
  await card.locator("button:has-text('Save view files')").click();
  const confirm = page.locator(".dialog:has-text('Save the Sales view')");
  await confirm.waitFor();
  assert.match(await confirm.textContent(), /Writes metrics\.yaml and schema\.yaml to semantic-views\/Sales\/ in the project's folder, made on this PC from semantic\/Sales\.SemanticModel\. Nothing is sent to Fabric\./);
  assert.ok(calls.filter(([n]) => n === "save").every(([, p]) => !p.confirmed), "nothing is written before the confirm");
  await confirm.locator("button:has-text('Save view files')").click();
  await page.waitForFunction(() => document.body.textContent.includes("Commit them from Source control"));
  const writes = calls.filter(([n, p]) => n === "save" && p.confirmed).map(([, p]) => p);
  assert.deepStrictEqual(writes, [{ view_name: "Sales", path: "semantic/Sales.SemanticModel", split: "schema_metrics", confirmed: true, token: "t0k" }]);
  assert.ok(calls.some(([n, p]) => n === "status" && p.view_name === "Sales"), "the card reads the saved view after the save");
  assert.deepStrictEqual(errors, []);
});

test("With preview features hidden the card isn't there", { skip }, async () => {
  const app = await openApp({ mocks: { meta_model_plan: () => PLAN, app_settings: () => ({ show_preview: { value: false } }) } });
  apps.push(app);
  await app.page.waitForFunction(() => typeof renderModelTab === "function");
  const n = await app.page.evaluate(async () => {
    await loadSettings();
    state.user = { name: "Chris" };
    state.meta.env = "test";
    state.meta.tab = "model";
    state.view = "metadata";
    const body = document.createElement("div");
    document.body.prepend(body);
    await renderModelTab(body);
    return body.querySelectorAll(".sv-card").length;
  });
  assert.strictEqual(n, 0);
});
