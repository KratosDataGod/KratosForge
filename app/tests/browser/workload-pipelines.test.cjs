// Monitor > Operations > Workloads: local pipelines are one Preview group, a mocked success says so,
// and a pipeline run opens in the run view (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

const dep = (run, actions = []) => [{ env: null, env_label: "", location: { kind: "this_pc", label: "This PC" }, latest_run: run,
  observation: { source: "Pipelines on this PC", state: "live", label: "Live" }, actions }];
const run = (state, mocked = 0) => ({ id: "r1", state, mocked, started_at: new Date().toISOString(), where: "this_pc",
  open: { kind: "pipeline_run", run_id: "11111111-2222-3333-4444-555555555555" } });
const row = (name, health, label, r, actions) => ({ id: `pipeline:C:\\repo\\${name}.DataPipeline`, kind: "pipeline", kind_label: "Pipeline",
  name, preview: true, description: "repo · " + name + ".DataPipeline", group: { type: "pipelines", id: "pipelines", label: "Local pipelines" },
  owners: {}, tags: [], health, health_label: label, readiness: { state: "ready" }, unowned: true, partial: false,
  capabilities: ["runnable"], headline: null, last_run: r, deployments: dep(r, actions), group_key: "pipelines" });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    workloads_list: () => ({
      rows: [row("pl_orders_daily", "failing", "Failing", run("failed"), ["run_schedule"]),
        row("pl_reference_local", "attention", "Needs attention", run("ok", 2)),
        row("pl_customers_load", "idle", "No recorded runs", null)],
      groups: [{ id: "pipelines", label: "Local pipelines", count: 3, health: { failing: 1, attention: 1 } }],
      sources: [{ source: "Pipelines on this PC", state: "live", label: "Live" }],
      views: [{ id: "all", label: "All", count: 3 }], total: 3, offset: 0, limit: 100, scanning: false, user: "" }),
  } });
  app.errors.length = 0;
  await page().evaluate(() => { monitorPage.panel = "workloads"; monitorPage.opsView = "workloads"; go("monitor"); });
  await page().locator("tr.wl-group").first().waitFor();
});
after(async () => { if (app) await app.close(); });

test("local pipelines are one group tagged Preview, each row tagged too", { skip }, async () => {
  const group = await page().locator("tr.wl-group").first().textContent();
  assert.match(group, /^Local pipelines/);
  assert.match(group, /Preview/);
  const rows = page().locator("table.wl-list tbody tr:not(.wl-group)");
  assert.strictEqual(await rows.count(), 3);
  for (const t of await rows.allTextContents()) assert.match(t, /Pipeline.*Preview/);
  if (SHOTS) await page().locator("table.wl-list").screenshot({ path: path.join(SHOTS, "workload-pipelines.png") });
});

test("a mocked success shows Needs attention and says it used mocks", { skip }, async () => {
  const t = await page().locator("table.wl-list tbody tr", { hasText: "pl_reference_local" }).textContent();
  assert.match(t, /^Needs attention/);
  assert.match(t, /OK with mocks/);
  const never = await page().locator("table.wl-list tbody tr", { hasText: "pl_customers_load" }).textContent();
  assert.match(never, /No recorded runs/);
});

test("a pipeline run opens in the run view", { skip }, async () => {
  const opened = await page().evaluate(() => {
    window.openPipelineRun = (id) => { window.openedRun = id; };
    openRun({ kind: "pipeline_run", run_id: "abc" });
    return window.openedRun;
  });
  assert.strictEqual(opened, "abc");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
