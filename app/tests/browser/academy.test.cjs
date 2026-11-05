// Academy (academy.js, ACD-007/008): the approved mockup with the real engine, so the certification,
// readiness and the exercise workspace (start, Run, Check my work, hints, reset) are the ones that ship.
// Also axe on Home, Path, Readiness and an open exercise.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const noAxe = skip || (!axeSource && "axe-core is not installed");
const ENGINE = ["app_settings", "save_app_settings", "academy_catalog", "academy_exercise", "academy_start", "academy_save",
  "academy_run", "academy_validate", "academy_hint", "academy_explanation", "academy_reset", "academy_readiness",
  "profile_get", "learn_catalog"];
const SOLUTION = `CREATE OR REPLACE TABLE silver_orders AS
SELECT order_id, status, amount, loaded_at
FROM (
    SELECT *, row_number() OVER (PARTITION BY order_id ORDER BY loaded_at DESC) AS load_rank
    FROM silver_orders
) AS ranked
WHERE load_rank = 1;

SELECT order_id, status, amount FROM silver_orders ORDER BY order_id;`;

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, mocks: { readiness_check: () => [] } });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

async function openTab(tab) {
  await page().evaluate((t) => { academyPage.open = null; academyPage.tab = t; go("academy"); }, tab);
}

test("Academy takes Learn's spot in the rail, with six tabs and the next step first", { skip }, async () => {
  const bottom = await page().locator("#rail-bottom .rail-item").allTextContents();
  assert.deepStrictEqual(bottom.map((t) => t.trim()), ["Academy", "Source control", "Settings"]);
  assert.ok(await page().locator("#rail-main .rail-item").count() <= 8);
  await page().locator("#rail-bottom .rail-item", { hasText: "Academy" }).click();
  await page().locator(".academy-next h2").waitFor();
  assert.match(await page().locator(".page.academy h1").textContent(), /Academy\s*Preview/);
  assert.deepStrictEqual((await page().locator(".subtabs [role=tab]").allTextContents()).map((t) => t.trim()),
    ["Home", "Path", "Practice", "Assessments", "Readiness", "Portfolio"]);
  const text = await page().locator(".academy-body").textContent();
  assert.match(text, /Your Learn lessons and rank moved here/);
  assert.match(text, /objectives proved/);
  assert.doesNotMatch(text, /\bshown\b/);
  assert.match(text, /never read or write OneLake/);
  assert.match(await page().locator(".academy-next").textContent(), /LOCAL VERIFIED/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "academy-home.png") });
});

test("Path lists every objective in real tables, and an objective that needs Fabric says so", { skip }, async () => {
  await openTab("path");
  await page().locator(".academy-table").first().waitFor();
  assert.strictEqual(await page().locator(".academy-acc-head > button[aria-expanded]").count(), 3, "each domain is a heading with a button");
  assert.ok(await page().locator(".academy-table th[scope=col]").count() >= 5);
  const all = await page().evaluate(async () => {
    for (const k of ["1", "2", "3"]) academyPage.open2[k] = true;
    await renderAcademy();
    return document.querySelectorAll(".academy-table tbody tr").length;
  });
  assert.strictEqual(all, 49);
  assert.match(await page().locator(".academy-body").textContent(), /Needs Fabric/);
  assert.match(await page().locator(".academy-body").textContent(), /use your capacity/);
});

test("Path, Readiness and Home have no serious axe findings", { skip: noAxe }, async () => {
  for (const tab of ["home", "path", "readiness"]) {
    await openTab(tab);
    await page().locator(".academy-body .card").first().waitFor();
    assert.deepStrictEqual(await axeFindings(page(), "#view"), [], tab);
  }
});

test("an exercise starts on this PC, runs SQL, fails the starter and passes a right answer", { skip }, async () => {
  await page().evaluate(() => academyOpen("E37"));
  await page().locator("button", { hasText: "Start the exercise" }).click();
  await page().locator("textarea.learn-editor").waitFor();
  assert.match(await page().locator(".academy-crumb").textContent(), /Academy/);
  assert.deepStrictEqual((await page().locator(".academy-panel [role=tab]").allTextContents()).map((t) => t.replace(/\s*\(.*$/, "").trim()),
    ["Task", "Hints", "Check"]);
  assert.strictEqual(await page().locator("[role=toolbar] .tool.primary").count(), 1, "Run is the only primary");
  await page().locator("textarea.learn-editor").fill("SELECT order_id, status FROM silver_orders ORDER BY order_id");
  await page().locator("[role=toolbar] button", { hasText: "Run" }).click();
  await page().locator(".learn-result table").waitFor();
  assert.deepStrictEqual(await page().locator(".learn-result th").allTextContents(), ["order_id", "status"]);

  await page().locator("[role=toolbar] button", { hasText: "Check my work" }).click();
  await page().locator(".academy-status").waitFor();
  assert.match(await page().locator(".academy-status").textContent(), /Not yet/);
  assert.strictEqual(await page().locator(".academy-panel button", { hasText: /^Next/ }).count(), 0, "Next only after a pass");

  await page().locator(".academy-panel [role=tab]", { hasText: "Hints" }).click();
  await page().locator(".academy-panel button", { hasText: "Show a hint" }).click();
  await page().locator(".learn-hint").waitFor();
  assert.match(await page().locator(".learn-hint").textContent(), /Hint 1 of 3/);

  await page().locator("textarea.learn-editor").fill(SOLUTION);
  await page().locator("[role=toolbar] button", { hasText: "Check my work" }).click();
  await page().locator(".academy-status.ok").waitFor();
  const panel = await page().locator(".academy-panel").textContent();
  assert.match(panel, /Passed: 2 of 2 checks/);
  assert.match(panel, /What it doesn't/);
  assert.match(panel, /Fabric lakehouse/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "academy-exercise.png") });
});

test("an open exercise has no serious axe findings", { skip: noAxe }, async () => {
  assert.deepStrictEqual(await axeFindings(page(), "#view"), []);
});

test("work survives leaving, and Reset asks first and puts the starter back", { skip }, async () => {
  await openTab("home");
  await page().evaluate(() => academyOpen("E37"));
  await page().waitForFunction((want) => document.querySelector("textarea.learn-editor")?.value === want, SOLUTION);
  await page().locator("[role=toolbar] button[aria-label='More actions']").click();
  await page().locator("#menu [role=menuitem]", { hasText: "Reset the exercise" }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /your answer included/);
  await page().locator("#dialog-root button", { hasText: "Reset exercise" }).click();
  await page().waitForFunction(() => document.querySelector("textarea.learn-editor")?.value.includes("SELECT DISTINCT"));
  assert.strictEqual(await page().locator(".academy-panel [role=tab][aria-selected=true]").textContent(), "Task");
});

test("Readiness keeps six measures apart and says it isn't the vendor's score", { skip }, async () => {
  await openTab("readiness");
  await page().locator(".academy-note").waitFor();
  assert.match(await page().locator(".academy-note").textContent(), /isn't Microsoft's score/);
  const heads = (await page().locator(".academy-table").first().locator("th[scope=col]").allTextContents()).map((t) => t.trim());
  assert.deepStrictEqual(heads, ["Objective", "Knowledge", "Guided", "Independent", "Troubleshooting", "Fabric verified", "Retention"]);
  assert.match(await page().locator(".academy-body").textContent(), /Not yet/);
  assert.doesNotMatch(await page().locator(".academy-table").first().textContent(), /—/);
});
