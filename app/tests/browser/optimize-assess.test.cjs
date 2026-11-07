// OE-003: Optimize > Assessment, driven by the real engine's demonstration fixture
// (optimization_assess(demo=True) makes no Fabric call). See harness.cjs for how to run it.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

// axe covers dialogs (#1673, ui.test.cjs): the evidence case below runs axe while this is open.
// axe covers dialogs: optimize-assess.js:assessEvidence

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

let app;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["optimization_assess", "app_settings", "save_app_settings"],
    mocks: { optimize_status: () => ({ allowed: true, active: null, connections: [], default_name: "FabricDesktop_Central" }) },
  });
  await app.page.waitForFunction(() => typeof optimizeAssessPanel === "function" && !!state.settings);
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const page = () => app.page;
const shot = async (name) => { if (SHOTS) await page().screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true }); };

test("Assessment is a tab of Optimize and starts with what it reads", { skip }, async () => {
  await page().evaluate(() => { optimizePage.tab = "assessment"; go("optimize"); });
  await page().waitForSelector(".assess-empty");
  const tabs = (await page().locator(".page.optimize .subtabs .tab").allTextContents());
  assert.ok(tabs.includes("Assessment"), tabs.join(", "));
  const text = await page().locator(".page.optimize").textContent();
  assert.match(text, /Reads only\. No Fabric job starts/);
  assert.match(text, /Code is read, never run/);
  assert.ok(!app.calls.some((c) => c.method === "optimization_assess"), "nothing is assessed until asked");
  await shot("assess-before");
});

test("the demonstration fixture is labelled everywhere and its totals say incomplete", { skip }, async () => {
  await page().getByRole("button", { name: "See how it works with the demonstration fixture" }).click();
  await page().waitForSelector(".assess-table");
  const call = app.calls.find((c) => c.method === "optimization_assess");
  assert.deepStrictEqual(call.params, { demo: true });
  const text = await page().locator(".assess-view").textContent();
  assert.match(text, /DEMONSTRATION FIXTURE/);
  assert.ok((await page().locator(".fx-chip").count()) >= 3, "chip on next action, totals and opportunities");
  assert.match(text, /Incomplete:/);
  assert.match(text, /This is capacity freed, not money saved/);
  assert.equal(await page().locator(".assess-table tbody tr").count(), 6);
  assert.ok((await page().locator(".assess-dim").count()) >= 1, "uncounted rows are dimmed");
  assert.match(text, /Not counted: same workload as #1/);
  assert.match(text, /Not counted: saves maintenance, not capacity/);
  assert.match(await page().locator(".assess-scope").textContent(), /Leave the demonstration/);
  assert.deepStrictEqual(app.errors, []);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".page.optimize"), []);
  await shot("assess-demo");
});

test("a row opens its evidence, validation and rollback, with nothing to open in Fabric for a fixture", { skip }, async () => {
  await page().locator(".assess-table tbody tr").first().click();
  const sheet = page().locator(".dialog.assess-sheet");
  await sheet.waitFor();
  const text = await sheet.textContent();
  for (const part of ["Evidence", "Check before you change it", "How to undo it", "Recommendation", "Fabric job history"]) {
    assert.ok(text.includes(part), part);
  }
  assert.match(text, /It is not a saving until a capacity is resized, paused or retired/);
  assert.match(text, /Demonstration items don't exist in Fabric/);
  assert.equal(await sheet.getByRole("button", { name: /in Fabric$/ }).count(), 0);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);
  await page().waitForTimeout(400); // the dialog's fade-in, for the screenshot
  await shot("assess-evidence");
  await page().keyboard.press("Escape");
  await sheet.waitFor({ state: "detached" });
});

test("with preview features hidden, the tab isn't offered", { skip }, async () => {
  await page().evaluate(() => { state.settings.show_preview = { value: false }; optimizePage.tab = "assessment"; go("optimize"); });
  await page().waitForSelector(".page.optimize .subtabs");
  const tabs = await page().locator(".page.optimize .subtabs .tab").allTextContents();
  assert.ok(!tabs.includes("Assessment"), tabs.join(", "));
  await page().evaluate(() => { state.settings.show_preview = { value: true }; });
});
