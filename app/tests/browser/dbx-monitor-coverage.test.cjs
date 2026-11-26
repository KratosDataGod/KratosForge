// Monitor > Databricks (dbx-monitor.js), the paths monitor-databricks.test leaves out: no Databricks
// connection yet (and connections that can't be read) with its way to Settings, picking another
// connection, runs that can't be read, a workspace you aren't signed in to, no runs for the filters
// picked, a run over an hour long, and closing a run's detail. Every engine call is mocked; what the
// person sees and which connection, days and whose runs are asked for is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const HOST = "https://adb-1234567890123456.7.azuredatabricks.net";
const conn = (key, label) => ({ key, label, source: "cli", host: HOST, warehouse_id: "", cluster_id: "", catalog: "", schema: "", remote_compute: "ask" });
const RUN = { kind: "job", id: "21", job_id: "2", name: "monthly_close", started: Date.now() - 3 * 3_600_000, took_s: 7500, outcome: "ok",
  result: "Succeeded", trigger: "Manual", compute: "Job cluster", creator: "dev@contoso.com", url: `${HOST}/#job/2/run/21` };

const db = {};
let app;
const page = () => app.page;
const panel = () => page().locator(".dbx-mon");
const runsAsked = () => app.calls.filter((c) => c.method === "databricks_runs").map((c) => c.params);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    engine_connections: () => { if (db.conns instanceof Error) throw db.conns; return { connections: db.conns }; },
    databricks_runs: (p) => { if (db.runs instanceof Error) throw db.runs; return { host: HOST, days: p.days, ...db.runs }; },
    databricks_run: () => ({ ...RUN, parameters: {}, tasks: [{ key: "close", outcome: "ok", result: "Succeeded", took_s: 45, notebook: "", error: "" }] }),
  } });
  await page().waitForFunction(() => typeof databricksRunsPanel === "function");
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db.conns = [conn("cli:dev", "dev (Databricks CLI)"), conn("cli:prod", "prod (Databricks CLI)")];
  db.runs = { signed_in: true, jobs: { ok: true }, pipelines: { ok: true }, rows: [RUN] };
  await page().evaluate(() => {
    state.settings = { ...(state.settings || {}), show_preview: { value: true } };
    Object.assign(dbxMon, { connection: "", mine: true, days: 1, kind: "all", selected: null, detail: null });
    go("home");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

async function openTab(ready) {
  await page().evaluate(() => { monitorPage.panel = "databricks"; go("monitor"); });
  await panel().locator(ready).first().waitFor();
}

test("with no Databricks connection the tab says how to add one and opens Settings > Connections", { skip }, async () => {
  db.conns = [{ key: "env:x", label: "No host", host: "" }];
  await openTab("text=There's no Databricks connection on this PC yet.");
  assert.match(await panel().innerText(), /Sign in with the Databricks CLI, then add it on Settings > Connections\./);
  assert.deepStrictEqual(runsAsked(), []);
  // Where it leads, without drawing Settings (its own tests cover that page).
  await page().evaluate(() => { window.__go = window.go; window.__went = []; window.go = (v) => window.__went.push(v); });
  await panel().getByRole("button", { name: "Open Settings > Connections" }).click();
  assert.deepStrictEqual(await page().evaluate(() => { window.go = window.__go; return [window.__went, state.settingsTab]; }), [["settings"], "connections"]);

  db.conns = new Error("PermissionError: databricks.cfg can't be read");
  await openTab("p");
  await panel().getByText("databricks.cfg can't be read", { exact: false }).waitFor();
  assert.strictEqual(await panel().getByRole("button", { name: "Open Settings > Connections" }).count(), 1);
  assert.deepStrictEqual(app.errors, []);
});

test("picking another connection asks for its runs; a run over an hour reads in hours; Close puts the detail away", { skip }, async () => {
  await openTab(".dbx-runs");
  assert.match(await panel().locator(".dbx-runs tbody tr").innerText(), /monthly_close\s+.+\s+Succeeded\s+2 h 5 min\s+Job cluster\s+Manual/);
  await page().getByRole("combobox", { name: "Databricks connection" }).selectOption("cli:prod");
  await page().waitForFunction(() => dbxMon.connection === "cli:prod");
  await panel().locator(".dbx-runs").waitFor();
  assert.deepStrictEqual(runsAsked().at(-1), { connection: "cli:prod", days: 1, mine: true });
  await panel().getByRole("button", { name: "monthly_close" }).click();
  const detail = panel().getByRole("region", { name: "Run detail" });
  await detail.locator("h2", { hasText: "monthly_close" }).waitFor();
  assert.match(await detail.innerText(), /Succeeded · 2 h 5 min/);
  assert.match(await detail.locator(".dbx-tasks").innerText(), /close: Succeeded, 45 s/);
  await detail.getByRole("button", { name: "Close" }).click();
  await detail.waitFor({ state: "detached" });
  assert.strictEqual(await page().evaluate(() => [dbxMon.detail, dbxMon.selected].join()), ",");
  assert.deepStrictEqual(app.errors, []);
});

test("runs that can't be read, a workspace you aren't signed in to, and one that refuses with nothing listed", { skip }, async () => {
  db.runs = new Error("ConnectionError: the workspace didn't answer");
  await openTab("text=The runs couldn't be read.");

  db.runs = { signed_in: false, rows: [] };
  await openTab('[role="alert"]');
  assert.strictEqual(await panel().getByRole("alert").innerText(), "You aren't signed in to this workspace.");

  db.runs = { signed_in: true, error: "Databricks refused: 403 for /api/2.1/jobs/runs/list", rows: [] };
  await openTab('[role="alert"]');
  assert.strictEqual(await panel().getByRole("alert").innerText(), "Databricks refused: 403 for /api/2.1/jobs/runs/list");
  assert.deepStrictEqual(app.errors, []);
});

test("no runs says for which period and whose", { skip }, async () => {
  db.runs = { signed_in: true, jobs: { ok: true }, pipelines: { ok: true }, rows: [] };
  await openTab("text=No runs in the last 24 hours started by you.");
  await page().getByRole("group", { name: "Whose" }).getByRole("button", { name: "Everyone" }).click();
  await page().getByRole("group", { name: "Last" }).getByRole("button", { name: "7 days" }).click();
  await panel().getByText("No runs in the last 7 days.", { exact: true }).waitFor();
  assert.deepStrictEqual(runsAsked().at(-1), { connection: "cli:dev", days: 7, mine: false });
  // A filter that hides every row says so too.
  db.runs = { signed_in: true, jobs: { ok: true }, pipelines: { ok: true }, rows: [RUN] };
  await page().getByRole("group", { name: "Show" }).getByRole("button", { name: "Lakeflow pipelines" }).click();
  await panel().getByText("No runs in the last 7 days.", { exact: true }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});
