// Monitor > Runs lists pipeline runs on this PC (Preview), from the real engine: a mocked run says
// so, and a run opens to its activities, read from its record when you open it (see harness.cjs).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const REFERENCE = fs.readFileSync(path.join(__dirname, "..", "..", "..", "sidecar", "tests", "pipelines", "reference_local.json"), "utf8");
const MOCKS = { "Ingest orders": { output: { rowsCopied: 120 } }, Transform: { output: { exitValue: "ok" } } };

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["pipeline_run_start", "pipeline_run_status", "pipeline_runs"],
    mocks: { history: () => [], meta_runs_all: () => ({ runs: [] }), fabric_runs: () => [], pc_schedule_runs: () => [] },
  });
  app.errors.length = 0;
  for (const rows of [120, 0]) {
    const started = await page().evaluate(([text, mocks]) => rpc("pipeline_run_start", { text, mocks, wait_scale: 0 }),
      [REFERENCE, { ...MOCKS, "Ingest orders": { output: { rowsCopied: rows } } }]);
    assert.ok(started.ok, JSON.stringify(started));
    await page().evaluate(async (id) => {  // waitForFunction takes a returned promise as true at once
      while (!["succeeded", "failed"].includes((await rpc("pipeline_run_status", { run_id: id, values: false })).state)) {
        await new Promise((done) => setTimeout(done, 50));
      }
    }, started.run_id);
  }
  await page().evaluate(() => { monitorPage.panel = "runs"; go("monitor"); });
  await page().locator("tr.pl-run").nth(1).waitFor();
});
after(async () => { if (app) await app.close(); });

test("a mocked run never reads as plain Succeeded", { skip }, async () => {
  const rows = page().locator("tr.pl-run");
  assert.strictEqual(await rows.count(), 2);
  const texts = await rows.allTextContents();
  const ok = texts.find((t) => t.includes("Succeeded with mocks"));
  const bad = texts.find((t) => t.startsWith("Failed"));
  assert.ok(ok && bad, texts.join("\n"));
  for (const t of texts) {
    assert.match(t, /pl_reference_local/);
    assert.match(t, /Pipeline run 2 mocked ?Preview/);
    assert.match(t, /No environment/);
    assert.match(t, /This PC/);
  }
});

test("Problems first puts the failed run on top", { skip }, async () => {
  await page().locator("label.check", { hasText: "Problems first" }).locator("input").check();
  await page().locator("tr.pl-run").nth(1).waitFor();
  assert.match(await page().locator("tr.pl-run").first().textContent(), /^Failed/);
});

test("a run opens to its activities, with mocks marked and the inner failure named", { skip }, async () => {
  const failed = page().locator("tr.pl-run").first();
  const toggle = failed.locator(".pl-toggle");
  assert.strictEqual(await toggle.getAttribute("aria-expanded"), "false");
  await toggle.click();
  await page().locator("tr.pl-child").nth(6).waitFor();
  assert.strictEqual(await failed.locator(".pl-toggle").getAttribute("aria-expanded"), "true");
  assert.match(await failed.textContent(), /6 of 7 activities ran/);
  const kids = await page().locator("tr.pl-child").allTextContents();
  assert.strictEqual(kids.length, 7, kids.join("\n"));
  assert.ok(kids.some((t) => /^MockedIngest orders · Copy data/.test(t)), kids.join("\n"));
  assert.ok(kids.some((t) => /^MockedTransform · Notebook/.test(t)), kids.join("\n"));
  assert.ok(kids.some((t) => /^SucceededPer region · ForEach · 3 iterations/.test(t)), kids.join("\n"));
  assert.ok(kids.some((t) => /^FailedValidate · If condition › Too few rows · FailOnly 0 rows/.test(t)), kids.join("\n"));
  assert.ok(kids.some((t) => /^SkippedRecord · Set variable—/.test(t)), kids.join("\n"));
  // Drawing Runs again (here, hiding the Fabric half) reads the open run's activities again.
  await page().locator(".monitor-fabric button", { hasText: "Hide" }).click();
  await page().waitForFunction(() => !document.querySelector(".monitor-fabric .fabric-slot"));
  await page().locator("tr.pl-child").nth(6).waitFor();
  assert.strictEqual(await page().locator("tr.pl-child").count(), 7);
  if (SHOTS) await page().locator(".monitor-local").screenshot({ path: path.join(SHOTS, "monitor-pipeline-runs.png") });
  await page().locator("tr.pl-run").first().click();
  assert.strictEqual(await page().locator("tr.pl-child").count(), 0);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
