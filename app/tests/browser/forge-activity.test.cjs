// Monitor > Runs, the Forge Activity view (Preview, forge-activity.js, LX-05): today's counts in the
// header, a lane for each of the three most recent running runs with only real progress, the band
// on by default from Refined, the outcome of a finished run once, and nothing animating in Professional.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
let level = 2;
let meta = [];
let cloud = [];

const RUNNING_META = [
  { run_id: "m1", env: "dev", entity: "bronze.orders", status: "running", started_at: ago(3), seconds: null, rows_written: null },
  { run_id: "m2", env: "dev", entity: "silver.orders", status: "running", started_at: ago(5), seconds: null, rows_written: null },
  { run_id: "m3", env: "dev", entity: "gold.sales", status: "running", started_at: ago(7), seconds: null, rows_written: null },
];
const DONE_META = [
  { run_id: "d1", env: "dev", entity: "silver.customers", status: "ok", started_at: ago(30), seconds: 72, rows_written: 48210 },
  { run_id: "d2", env: "dev", entity: "gold.dim_customer", status: "failed", started_at: ago(40), seconds: 22, rows_written: 0, message: "table not found" },
];
const FABRIC_RUNNING = [{ job_id: "j1", name: "Daily close", kind: "Pipeline", status: "InProgress", started_at: ago(6), env: "prod" }];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    mocks: {
      readiness_check: () => [],
      app_settings: () => ({ motion_level: { value: level }, show_preview: { value: true } }),
      history: () => [],
      meta_runs_all: () => ({ runs: meta, unavailable: {} }),
      fabric_runs: () => cloud,
      pc_schedule_runs: () => [],
      pipeline_runs: () => ({ runs: [] }),
      fabric_pipeline_runs: () => ({ runs: [], pending: false }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof forgeActivityFill === "function" && !!document.querySelector(".rail-item"));
});
after(async () => { if (app) await app.close(); });

async function open() {
  await page().evaluate(() => { monitorPage.panel = "runs"; go("home"); go("monitor"); });
  await page().locator(".forge-strip").waitFor({ timeout: 10000 });
}

test("the strip counts today's runs and the band is on by default at Refined, with lanes for the three newest", { skip }, async () => {
  meta = [...RUNNING_META, ...DONE_META];
  cloud = FABRIC_RUNNING;
  await open();
  const strip = await page().locator(".forge-strip").innerText();
  assert.match(strip, /Running now\s*4/);
  assert.match(strip, /Succeeded today\s*1/);
  assert.match(strip, /Failed today\s*1/);
  assert.match(strip, /Ran on this PC \(24 h\)\s*5 of 6/);
  assert.ok(await page().getByRole("checkbox", { name: "Forge view" }).isChecked());
  const band = page().locator(".forge-band");
  assert.strictEqual(await band.getAttribute("data-state"), "active");
  assert.match(await band.locator(".forge-title").innerText(), /THE FORGE IS ACTIVE/);
  const lanes = band.locator(".forge-lane");
  assert.strictEqual(await lanes.count(), 3);
  assert.match(await lanes.nth(0).innerText(), /bronze\.orders[\s\S]*Running, total not known yet/);
  assert.match(await lanes.nth(1).innerText(), /silver\.orders/);
  assert.match(await lanes.nth(2).innerText(), /Daily close[\s\S]*Fabric[\s\S]*Waiting on Fabric/);
  // Only a known step count gets a bar: neither an unknown total nor Fabric does.
  assert.strictEqual(await band.locator(".forge-bar").count(), 0);
  assert.match(await band.locator(".forge-more").innerText(), /1 more running, see the table below/);
  // The table stays the full record.
  assert.strictEqual(await page().locator(".monitor-scroll tbody tr").count(), 6);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "forge-activity.png") });
  assert.deepStrictEqual(app.errors, []);
});

test("Run all on this PC shows cell N of M with a bar that follows the cells, and the wolf at Refined", { skip }, async () => {
  meta = [];
  cloud = [];
  await page().evaluate(() => {
    openNotebooks.set("TPC-H data", { name: "TPC-H data", cells: [], forgeRun: { done: 3, total: 6, started: Date.now() - 134000, result: null, ended: null } });
  });
  await open();
  const lane = page().locator(".forge-lane.steps");
  assert.match(await lane.innerText(), /TPC-H data[\s\S]*Notebook · This PC[\s\S]*Running, cell 4 of 6[\s\S]*2:1\d/);
  const bar = lane.getByRole("progressbar");
  assert.strictEqual(await bar.getAttribute("aria-valuenow"), "3");
  assert.strictEqual(await bar.getAttribute("aria-valuemax"), "6");
  assert.ok(await lane.locator(".forge-wolf").isVisible(), "no wolf at Refined");
  await page().evaluate(() => { openNotebooks.get("TPC-H data").forgeRun.done = 4; });
  await page().waitForFunction(() => /cell 5 of 6/.test(document.querySelector(".forge-lane.steps").textContent));
  assert.strictEqual(await bar.getAttribute("aria-valuenow"), "4");
  assert.match(await page().locator(".forge-strip").innerText(), /Running now\s*1/);
  // It finishes: the band shows the outcome and the anvil strikes once.
  await page().evaluate(() => Object.assign(openNotebooks.get("TPC-H data").forgeRun, { done: 6, result: "ok", ended: Date.now() }));
  await page().waitForFunction(() => document.querySelector(".forge-band")?.dataset.state === "done");
  assert.match(await page().locator(".forge-band").innerText(), /FORGED CLEAN[\s\S]*Succeeded, 6 cells/);
  await page().waitForFunction(() => (motionDiagnostics().events["forge.run.succeeded"] || 0) === 1);
  await open();  // drawn again: it doesn't strike twice
  assert.strictEqual(await page().evaluate(() => motionDiagnostics().events["forge.run.succeeded"]), 1);
  await page().evaluate(() => openNotebooks.delete("TPC-H data"));
  assert.deepStrictEqual(app.errors, []);
});

test("a failure cools the forge; with nothing recent it is quiet", { skip }, async () => {
  meta = [{ ...DONE_META[1], started_at: ago(2) }];
  cloud = [];
  await open();
  assert.strictEqual(await page().locator(".forge-band").getAttribute("data-state"), "failed");
  assert.match(await page().locator(".forge-band").innerText(), /THE FORGE COOLED[\s\S]*Failed: table not found/);
  meta = [DONE_META[0]];  // finished half an hour ago
  await open();
  assert.strictEqual(await page().locator(".forge-band").getAttribute("data-state"), "idle");
  assert.match(await page().locator(".forge-band").innerText(), /THE FORGE IS QUIET[\s\S]*Nothing is running/);
});

test("the switch hides the band for the session, and the strip stays", { skip }, async () => {
  await open();
  await page().getByRole("checkbox", { name: "Forge view" }).uncheck();
  await page().waitForFunction(() => !document.querySelector(".forge-band"));
  await open();
  assert.strictEqual(await page().locator(".forge-band").count(), 0);
  assert.ok(await page().locator(".forge-strip").isVisible());
  await page().evaluate(() => { forgeActivity.on = null; });
});

test("Professional keeps the text and the bar, without the wolf or any animation, and the band is off by default", { skip }, async () => {
  level = 0;
  await page().evaluate(async () => { await loadSettings(); applyMotion(); });
  meta = [];
  await page().evaluate(() => {
    openNotebooks.set("Quiet", { name: "Quiet", cells: [], forgeRun: { done: 1, total: 4, started: Date.now(), result: null, ended: null } });
  });
  await open();
  assert.strictEqual(await page().locator(".forge-band").count(), 0, "the band was on below Refined");
  await page().getByRole("checkbox", { name: "Forge view" }).check();
  const lane = page().locator(".forge-lane.steps");
  await lane.waitFor();
  assert.match(await lane.innerText(), /Running, cell 2 of 4/);
  assert.ok(await lane.getByRole("progressbar").isVisible());
  assert.strictEqual(await lane.locator(".forge-wolf").isVisible(), false, "the wolf ran in Professional");
  const moving = await page().evaluate(() => document.getAnimations().filter((a) => a.effect?.target?.closest?.(".forge-band")).length);
  assert.strictEqual(moving, 0, "something in the band animates in Professional");
  level = 2;
  await page().evaluate(async () => { openNotebooks.delete("Quiet"); forgeActivity.on = null; await loadSettings(); applyMotion(); });
});

test("fifty running runs still draw three lanes; the rest are in the table only", { skip }, async () => {
  meta = Array.from({ length: 50 }, (_, i) => ({ run_id: `r${i}`, env: "dev", entity: `t${i}`, status: "running", started_at: ago(i + 1) }));
  cloud = [];
  await open();
  assert.strictEqual(await page().locator(".forge-lane").count(), 3);
  assert.match(await page().locator(".forge-more").innerText(), /47 more running/);
  const moving = await page().evaluate(() => new Set(document.getAnimations().map((a) => a.effect?.target?.closest?.(".forge-lane")).filter(Boolean)).size);
  assert.ok(moving <= 3, `${moving} lanes animate`);
});
