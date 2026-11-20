// The Best practices check card on the Semantic model tab (docs/semantic-model-best-practices.md): it shows the
// last findings worst first, a saved error as text, and starts a check in Fabric only after the risk check and a
// confirm; a blocked risk check offers only Close. Fabric and the engine are mocked.
// axe covers dialogs: meta.js:bpaConfirm
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const PLAN = { layer: "gold", layers: ["gold"], name: "SM_Gold", workspace_name: "Sales Test", workspace_id: "ws",
  lakehouse: "LH_Gold", existing: { id: "m1", url: "https://app.fabric.microsoft.com/x" }, tables: [], relationships: [] };
const LAST = { request_id: "bpa-1", checked_at: new Date().toISOString(), package: "semantic-link-labs", version: "0.17.1",
  no_tables: false, counts: { Error: 1, Warning: 1, Info: 0 },
  findings: [
    { severity: "Error", category: "Performance", rule: "Hide foreign keys", object_type: "Column", object: "'FactSales'[customer_sk]",
      description: "Foreign keys should be hidden.", url: "https://learn.microsoft.com/x" },
    { severity: "Warning", category: "Formatting", rule: "Mark primary keys", object_type: "Column", object: "'DimCustomer'[customer_sk]",
      description: "Set IsKey on the primary key column.", url: "javascript:alert(1)" }] };
const STATUS = { model: "SM_Gold", model_id: "m1", env: "test", workspace_id: "ws", workspace_name: "Sales Test",
  last: LAST, running: null, failed: null };
const RISK_WARN = { id: "r1", result: "warning", label: "Warning", summary: "The capacity is busy",
  findings: [{ check: "capacity", outcome: "warning", title: "Capacity at 85%" }], note: "Going ahead is recorded." };

const apps = [];
async function open(mocks) {
  const app = await openApp({ mocks: { meta_model_plan: () => PLAN,
    meta_model_ossie_models: () => ({ env: "test", models: [{ id: "m1", name: "SM_Gold" }] }),
    meta_model_directlake_setup: () => ({ env: "test", sku: "F32", capacity_sku: "F32", skus: ["F32"] }), ...mocks } });
  apps.push(app);
  await app.page.setViewportSize({ width: 1280, height: 1100 });
  await app.page.waitForFunction(() => typeof renderModelTab === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async () => {
    await loadSettings();
    state.user = { name: "Chris" };
    Object.assign(state.meta, { env: "test", tab: "model" });
    state.view = "metadata";
    const body = document.createElement("div");
    body.id = "model-under-test";
    document.querySelector("main, #main, body").prepend(body);
    await renderModelTab(body);
  });
  return app;
}

after(async () => { for (const app of apps) await app.close(); });

test("the card shows the last findings worst first, and only safe rule links", { skip }, async () => {
  const calls = [];
  const { page, errors } = await open({ model_bpa_status: (p) => { calls.push(["status", p]); return STATUS; } });
  const card = page.locator("#model-under-test .bpa-card");
  await card.locator(".bpa-out table").waitFor();
  assert.deepStrictEqual(calls[0], ["status", { env: "test", layer: "gold", name: "SM_Gold" }]);
  const rows = await card.locator(".bpa-out tbody tr").allTextContents();
  assert.match(rows[0], /^Error.*Hide foreign keys/);
  assert.match(rows[0], /About this rule/);
  assert.doesNotMatch(rows[1], /About this rule/, "a javascript: link from the results file isn't offered");
  assert.match(await card.textContent(), /semantic-link-labs 0\.17\.1/);
  assert.deepStrictEqual(errors, []);
});

test("a failed check shows the saved error as text", { skip }, async () => {
  const failed = { status: "Failed", message: "The check failed in Fabric: ConnectionError: XMLA <b>off</b>",
    url: "", traceback: "Traceback (most recent call last): ...", pip: "Successfully installed semantic-link-labs" };
  const { page } = await open({ model_bpa_status: () => ({ ...STATUS, last: null, failed }) });
  const box = page.locator("#model-under-test .bpa-failed");
  await box.waitFor();
  assert.match(await box.textContent(), /XMLA <b>off<\/b>/, "shown as text, not markup");
  assert.strictEqual(await box.locator("details").count(), 2);
});

test("Check in Fabric runs the risk check, confirms, and starts one check with the risk id", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const calls = [];
  let running = false;
  const { page, errors } = await open({
    model_bpa_status: () => (running ? { ...STATUS, running: { started_at: new Date().toISOString(), url: "" } } : STATUS),
    model_bpa_risk: (p) => { calls.push(["risk", p]); return RISK_WARN; },
    model_bpa_check: (p) => { calls.push(["check", p]); running = true; return { message: "Checking SM_Gold in Fabric" }; },
  });
  const card = page.locator("#model-under-test .bpa-card");
  await card.locator(".bpa-out table").waitFor();
  await card.locator("button:has-text('Check in Fabric')").click();
  const dialog = page.locator(".dialog");
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /Capacity at 85%/);
  assert.deepStrictEqual(await axeFindings(page, ".dialog"), []);
  assert.deepStrictEqual(calls, [["risk", { env: "test", layer: "gold", name: "SM_Gold", refresh: true }]], "nothing starts before the confirm");
  await dialog.locator("button:has-text('Check in Fabric')").click();
  await card.locator(".bpa-running").waitFor();
  assert.deepStrictEqual(calls[1], ["check", { env: "test", layer: "gold", name: "SM_Gold", risk_id: "r1" }]);
  assert.ok(await card.locator("button:has-text('Check in Fabric')").isDisabled(), "no second check while one runs");
  assert.deepStrictEqual(errors, []);
});

test("a blocked risk check offers only Close and starts nothing", { skip }, async () => {
  const calls = [];
  const { page } = await open({ model_bpa_status: () => STATUS,
    model_bpa_risk: () => ({ ...RISK_WARN, id: "r2", result: "blocked", label: "Blocked", summary: "The capacity is paused",
      findings: [{ check: "capacity", outcome: "blocked", title: "Capacity paused" }] }),
    model_bpa_check: (p) => { calls.push(p); return {}; } });
  const card = page.locator("#model-under-test .bpa-card");
  await card.locator(".bpa-out table").waitFor();
  await card.locator("button:has-text('Check in Fabric')").click();
  const dialog = page.locator(".dialog");
  await dialog.waitFor();
  assert.deepStrictEqual(await dialog.locator(".dialog-actions button").allTextContents(), ["Close"]);
  await dialog.locator("button:has-text('Close')").click();
  assert.deepStrictEqual(calls, []);
  assert.ok(!(await card.locator("button:has-text('Check in Fabric')").isDisabled()));
});

test("with preview features hidden the card isn't there", { skip }, async () => {
  const { page } = await open({ app_settings: () => ({ show_preview: { value: false } }), model_bpa_status: () => STATUS });
  assert.strictEqual(await page.locator("#model-under-test .bpa-card").count(), 0);
});
