// Monitor > Databricks (Preview, dbx-monitor.js): job runs and Lakeflow updates from a Databricks
// connection, newest first, filters that ask the engine again, and one run's tasks with the failed
// task's error. Reading starts nothing, and Open in Databricks opens only the run's own page.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const HOST = "https://adb-1234567890123456.7.azuredatabricks.net";
const NOW = Date.now();

const ROWS = [
  { kind: "job", id: "11", job_id: "1", name: "nightly_sales", started: NOW - 3_600_000, took_s: 840, outcome: "failed",
    result: "Failed at task gold_kpis", trigger: "Periodic", compute: "Job cluster", creator: "dev@contoso.com", url: `${HOST}/#job/1/run/11` },
  { kind: "pipeline", id: "u2", pipeline_id: "pl-1", name: "orders_cdc", started: NOW - 7_200_000, took_s: 0, outcome: "ok",
    result: "Succeeded", trigger: "Scheduled", compute: "Serverless", creator: "dev@contoso.com", url: "" },
];
const DETAIL = { ...ROWS[0], parameters: { run_date: "2026-11-02" }, tasks: [
  { key: "bronze", run_id: "101", outcome: "ok", result: "Succeeded", took_s: 180, compute: "Job cluster", notebook: "/Repos/sales/bronze", error: "" },
  { key: "gold_kpis", run_id: "102", outcome: "failed", result: "Failed", took_s: 0, compute: "Job cluster", notebook: "/Repos/sales/gold_kpis",
    error: "AnalysisException: column revenue_usd cannot be resolved." }] };

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 900 },
    engine: ["app_settings"],
    mocks: {
      engine_connections: () => ({ cli_config: "x", connections: [
        { key: "cli:dev", label: "dev (Databricks CLI)", source: "cli", host: HOST, warehouse_id: "", cluster_id: "", catalog: "", schema: "", remote_compute: "ask" }] }),
      databricks_runs: (p) => ({ schema: "databricks-runs@1", host: HOST, signed_in: true, days: p.days, user: p.mine ? "dev@contoso.com" : "",
        jobs: { ok: true }, pipelines: p.days === 7 ? { ok: false, error: "Databricks refused: 403" } : { ok: true },
        rows: p.days === 7 ? [ROWS[0]] : ROWS }),
      databricks_run: () => DETAIL,
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof databricksRunsPanel === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("the Databricks tab lists job runs and Lakeflow updates, mine for the last 24 hours", { skip }, async () => {
  await page().evaluate(() => { monitorPage.panel = "databricks"; go("monitor"); });
  await page().locator(".dbx-runs").waitFor({ timeout: 10000 });
  assert.match(await page().locator('.monitor-tabs [role="tab"][aria-selected="true"]').textContent(), /Databricks/);
  const call = app.calls.find((c) => c.method === "databricks_runs");
  assert.deepStrictEqual(call.params, { connection: "cli:dev", days: 1, mine: true });
  const rows = await page().locator(".dbx-runs tbody tr").allTextContents();
  assert.strictEqual(rows.length, 2);
  assert.match(rows[0], /nightly_sales.*1 h ago.*Failed at task gold_kpis.*14 min.*Job cluster.*Periodic/);
  assert.match(rows[1], /orders_cdc · Lakeflow.*Succeeded.*Serverless/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "monitor-databricks.png") });
});

test("filters ask again, and a list that can't be read says why", { skip }, async () => {
  await page().locator('.seg[aria-label="Last"] button', { hasText: "7 days" }).click();
  await page().locator(".dbx-note").waitFor();
  assert.match(await page().locator(".dbx-note").textContent(), /Lakeflow pipelines not shown: Databricks refused: 403/);
  await page().locator('.seg[aria-label="Whose"] button', { hasText: "Everyone" }).click();
  await page().locator(".dbx-runs").waitFor();
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "databricks_runs").at(-1).params, { connection: "cli:dev", days: 7, mine: false });
  await page().locator('.seg[aria-label="Last"] button', { hasText: "24 hours" }).click();
  await page().locator(".dbx-runs tbody tr >> nth=1").waitFor();
  await page().locator('.seg[aria-label="Show"] button', { hasText: "Lakeflow pipelines" }).click();
  await page().locator(".dbx-runs").waitFor();
  for (let i = 0; i < 40 && (await page().locator(".dbx-runs tbody tr").count()) !== 1; i++) await page().waitForTimeout(50);
  assert.match(await page().locator(".dbx-runs tbody tr").textContent(), /orders_cdc/);
  await page().locator('.seg[aria-label="Show"] button', { hasText: "All" }).click();
  await page().locator(".dbx-runs tbody tr >> nth=1").waitFor();
});

test("a job run opens its tasks with the failed task's error, and Open in Databricks opens its page", { skip }, async () => {
  await page().locator(".dbx-runs button", { hasText: "nightly_sales" }).click();
  await page().locator(".dbx-detail .dbx-tasks").waitFor();
  assert.deepStrictEqual(app.calls.find((c) => c.method === "databricks_run").params, { connection: "cli:dev", run_id: "11" });
  const tasks = await page().locator(".dbx-tasks li").allTextContents();
  assert.match(tasks[0], /bronze: Succeeded, 3 min\/Repos\/sales\/bronze/);
  assert.match(tasks[1], /gold_kpis: Failed.*AnalysisException: column revenue_usd cannot be resolved\./);
  assert.match(await page().locator(".dbx-detail").textContent(), /run_date2026-11-02/);
  await page().locator(".dbx-detail button", { hasText: "Open in Databricks" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_link"));
  assert.strictEqual(await page().evaluate(() => window.__fdShellCalls.find((c) => c.cmd === "open_link").args.url), `${HOST}/#job/1/run/11`);
});

test("Monitor > Databricks has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  assert.deepStrictEqual(await axeFindings(page(), "#view .page.monitor"), []);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
