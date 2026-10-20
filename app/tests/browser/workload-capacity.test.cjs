// Cost allocation on the workload page: Capacity use of its runs, measured where imported usage
// matched, else estimated, and never a figure for runs on this PC (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const m = (classification, value, extra = {}) => ({ classification, metric: "fabric_job_cu", unit: "cu_hours", value, ...extra });
let use;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { readiness_check: () => [], workload_capacity_use: () => use } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

const card = async () => {
  await page().evaluate(() => {
    document.querySelector(".wl-capacity")?.remove();
    document.querySelector(".page").append(workloadCapacityCard({ id: "fabric:ws1:pl1" }));
  });
  await page().waitForFunction(() => !document.querySelector(".wl-capacity").textContent.includes("Adding up"));
  return page().textContent(".wl-capacity");
};

test("measured and estimated runs, and runs on this PC that aren't counted", { skip }, async () => {
  use = { fabric_runs: 3, pc_runs: 2, measured_runs: 1, problem: "",
    measured: m("measured", 2), total: m("estimated", 3, { complete: false, unknown_parts: 1, parts: 3 }) };
  const text = await card();
  for (const part of ["3 runs in Fabric used 3.00 CU h", "Estimated", "Incomplete", "1 of 3 matched imported usage", "2.00 CU h",
    "left out, not counted as 0", "2 runs on this PC started no Fabric job", "isn't counted here", "likely, not proven"]) {
    assert.ok(text.includes(part), part);
  }
  if (SHOTS) await page().locator(".wl-capacity").screenshot({ path: path.join(SHOTS, "workload-capacity-use.png") });
});

test("nothing matched says the figure is an estimate and where to import usage", { skip }, async () => {
  use = { fabric_runs: 1, pc_runs: 0, measured_runs: 0, problem: "", measured: null, total: m("estimated", 0.5) };
  const text = await card();
  assert.ok(text.includes("None matched imported usage, so the figure is the app's estimate"));
  assert.ok(text.includes("Optimize > Cost and savings"));
});

test("no runs says so, with no figure", { skip }, async () => {
  use = { fabric_runs: 0, pc_runs: 0, measured_runs: 0, problem: "", measured: null, total: null };
  assert.match(await card(), /No runs in the last 30 days\.$/);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
