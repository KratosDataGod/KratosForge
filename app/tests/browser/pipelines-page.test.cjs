// Pipelines (Preview, pipelines plan P4): Create > Pipeline opens the reference pipeline from a JSON
// file, the page shows what runs here, the Run dialog gives the Copy and the notebook mocks, and the
// run view follows the run to the end against the real engine. Nothing reaches Fabric.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const REFERENCE = path.join(__dirname, "..", "..", "..", "sidecar", "tests", "pipelines", "reference_local.json");

let app;
const page = () => app.page;
const shot = (name) => (SHOTS ? page().screenshot({ path: path.join(SHOTS, `${name}.png`) }) : null);

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    engine: ["pipeline_list", "pipeline_import", "pipeline_compatibility", "pipeline_run_check", "pipeline_run_start",
      "pipeline_run_status", "pipeline_run_cancel", "pipeline_runs", "pipeline_export"],
    mocks: { list_notebooks: () => [{ name: "Transform orders" }], list_workspaces: () => [] },
  });
});

after(async () => { if (app) await app.close(); });

test("Create > Pipeline opens a pipeline file and shows what runs here", { skip }, async () => {
  // openApp waits for an earlier script; app.js (state, go) loads after it, and a cold start can be slow.
  await page().waitForFunction(() => typeof go === "function" && typeof pipelineOpenDialog === "function" && !!document.querySelector(".rail-item"));
  await page().evaluate(() => go("create"));
  await page().click("text=Open a Fabric pipeline");
  await page().waitForSelector(".pl-open");
  await page().waitForSelector("text=No .DataPipeline folders");
  await page().setInputFiles('input[aria-label="Choose a pipeline JSON file"]', REFERENCE);
  await page().waitForSelector(".pl-graph");
  await shot("pipelines-page");
  const summary = await page().textContent(".pl-summary");
  assert.match(summary, /11 activities: 10 run here1 needs a mock · Parameters/);
  assert.match(summary, /regions \(array\), min_rows \(int\)/);
  await page().click('.pl-node[data-path="$.properties.activities[1]"]');
  await page().waitForSelector(".pl-detail");
  assert.match(await page().textContent(".pl-detail"), /Ingest orders.*Needs a mock.*Give a mock for it/s);
  await page().click("text=Compatibility");
  await page().waitForSelector(".pl-compat");
  assert.match(await page().textContent(".pl-compat"), /Per region › Mark region/);
  await page().click('[role="tab"]:has-text("Definition")');
  await page().waitForSelector(".pl-json");
  assert.match(await page().textContent(".pl-json"), /"name": "Ingest orders"/);
  assert.deepStrictEqual(app.errors, []);
});

test("the Run dialog mocks what can't run here, then the run view follows the run", { skip }, async () => {
  await page().click('[role="tab"]:has-text("Graph")');
  await page().click(".pl-actions >> text=Run on this PC");
  await page().waitForSelector(".pl-run-dialog");
  // Copy has a mock already; the notebook step waits for a notebook or a mock.
  await page().waitForSelector(".pl-check >> text=Transform runs notebook");
  assert.ok(await page().isDisabled(".dialog-actions >> text=Run"));
  assert.match(await page().textContent(".pl-mocks"), /Ingest orders/);
  await page().fill('textarea[aria-label="Ingest orders output"]', '{"rowsCopied": 120}');
  await page().selectOption('select[aria-label="Transform runs notebook"]', { label: "Give it a mock instead" });
  await page().waitForSelector(".pl-check >> text=Every activity can run here or has a mock");
  await shot("pipelines-run-dialog");
  await page().click(".dialog-actions >> text=Run");
  await page().waitForSelector(".pl-timeline");
  await page().waitForFunction(() => /Succeeded/.test(document.querySelector(".pl-head h1")?.textContent || ""), null, { timeout: 30000 });
  // Two activities were mocked, so the run never reads as a plain success.
  assert.match(await page().textContent(".pl-head h1"), /Succeeded with mocks/);
  assert.doesNotMatch(await page().textContent(".pl-timeline"), /no capacity/);
  await shot("pipelines-run");
  const rows = await page().$$eval(".pl-row:not(.head) .pl-row-name", (els) => els.map((e) => e.textContent));
  assert.ok(rows.includes("Ingest orders"), rows.join(", "));
  // A loop's rows go by iteration: each iteration's steps together.
  const loop = rows.filter((r) => / · iteration /.test(r));
  assert.deepStrictEqual(loop, ["Settle · iteration 0", "Mark region · iteration 0", "Settle · iteration 1",
    "Mark region · iteration 1", "Settle · iteration 2", "Mark region · iteration 2"]);
  await page().click('.pl-row:has-text("Ingest orders")');
  assert.match(await page().textContent(".pl-detail"), /Mocked[\s\S]*"rowsCopied": 120/);
  assert.ok(!(await page().isDisabled("text=Run again with the same settings")));
  // The run is listed under the pipeline's Runs tab.
  await page().click("text=Back to the pipeline");
  await page().click('[role="tab"]:has-text("Runs")');
  await page().waitForSelector(".list >> text=Succeeded with mocks");
  assert.deepStrictEqual(app.errors, []);
});
