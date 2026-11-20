// The notebook's Engine picker (Preview, engines.js), with every engine call mocked: planned engines
// behind More, a Databricks engine with no connection or several to pick from, the remote confirm
// for a stopped warehouse and for Fabric Spark, the switch dialog (Keep both, Cancel, and from the
// kernel with its variables), a cell on an engine that isn't ready, a poll that fails, Cancel with
// nothing running, and the command that opens the picker.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const engine = (id, label, location, platform, extra = {}) => ({ id, label, location, platform, adapter: true, kernel: false,
  bills: location === "remote" ? (platform === "fabric" ? "uses Fabric capacity" : "bills DBUs") : "", planned: "", limitations: [], ...extra });
const CATALOG = { version: "9", engines: [
  engine("local-python", "Python (this PC)", "local", "local", { kernel: true }),
  engine("local-duckdb", "DuckDB (this PC)", "local", "local", { limitations: ["Not Fabric's or Databricks' Spark: results can differ where engines differ (use Prove to compare)."] }),
  engine("local-spark", "Apache Spark (this PC)", "local", "local", { adapter: false, planned: "Phase 2: run cells on Apache Spark installed on this PC (PySpark and Java)." }),
  engine("fabric-spark", "Fabric Spark (remote)", "remote", "fabric"),
  engine("databricks-sql", "Databricks SQL warehouse (remote)", "remote", "databricks"),
  engine("snowflake-sql", "Snowflake SQL (remote)", "remote", "snowflake", { adapter: false, planned: "SF-07: SQL cells on a Snowflake warehouse through Snowflake's SQL API." }),
] };
const CONN_DEV = { key: "cli:dev", label: "dev (Databricks CLI)", host: "https://adb-1.azuredatabricks.net", catalog: "main", schema: "sales", warning: "" };
const CONN_OLD = { key: "cli:old", label: "old (Databricks CLI)", host: "https://adb-2.azuredatabricks.net", catalog: "hive", schema: "", warning: "Keeps a personal access token in plain text." };
const plain = (p) => ({ engine: p.engine, transition: { needed: false }, confirm: false, grant_minutes: 60, cells: [], risk: { outcome: "pass", lines: [] } });

const next = {};
let app;
const page = () => app.page;
const callsOf = (m) => app.calls.filter((c) => c.method === m);

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 },
    mocks: {
      app_settings: () => ({ show_preview: { value: true } }),
      list_notebooks: () => [],
      open_notebook: ({ name }) => ({ name, cells: [{ type: "code", source: "select 1" }, { type: "code", source: "select 2" }], metadata: {} }),
      save_notebook: () => ({ saved: true }),
      restart_kernel: () => ({ restarted: true }),
      kernel_variables: () => [],
      notebook_runs: () => ({ runs: [] }),
      engine_catalog: () => CATALOG,
      engine_connections: () => ({ connections: next.conns }),
      engine_prepare: (p) => (next.plan || plain)(p),
      engine_select: (p) => ({ engine: p.engine, status: p.engine === "local-python" ? "ready" : "starting" }),
      engine_state: () => next.state,
      engine_close: () => ({ engine: "local-python", status: "ready" }),
      engine_start_cell: () => ({ status: "running" }),
      engine_poll_cell: () => { throw new Error("ConnectionError: the engine session went away"); },
      engine_cancel: () => ({ cancelled: false }),
    } });
  await page().waitForFunction(() => typeof openNotebook === "function" && typeof openEngineMenu === "function");
  await page().evaluate(() => openNotebook("Engines"));
  await page().locator(".engine-btn").first().waitFor();
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(next, { conns: [CONN_DEV], plan: null, state: null });
  await page().evaluate(() => {
    closeMenu(); closeDialog();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    engineStates.delete("Engines");
    delete state.notebook.engineConnection; delete state.notebook.lakehouse;
    state.notebook.engineRunning = false;
    state.variables = [];
    renderNotebook();
  });
});

const toast = async (re) => {
  await page().waitForFunction((src) => { const t = document.getElementById("toast"); return !t.hidden && new RegExp(src).test(t.textContent); }, re.source);
  return page().locator("#toast").textContent();
};
const pick = async (name) => {
  await page().locator(".engine-btn").first().click();
  await page().locator("#menu [role^=menuitem]", { hasText: name }).first().click();
};

test("More lists the planned engines, which can't be picked", { skip }, async () => {
  await page().locator(".engine-btn").first().click();
  await page().locator("#menu [role^=menuitem]", { hasText: "More" }).click();
  await page().locator("#menu", { hasText: "Planned, not available in this version" }).waitFor();
  const text = await page().locator("#menu").textContent();
  assert.match(text, /Apache Spark \(this PC\): Phase 2: run cells on Apache Spark/);
  assert.match(text, /Snowflake SQL \(remote\): SF-07/);
  assert.strictEqual(await page().locator("#menu [role^=menuitem]", { hasText: "Apache Spark" }).count(), 0);
});

test("a Databricks engine with no connection on this PC says how to add one and prepares nothing", { skip }, async () => {
  next.conns = [];
  const n = callsOf("engine_prepare").length;
  await pick("Databricks SQL warehouse");
  assert.match(await toast(/no Databricks connection/), /Sign in with the Databricks CLI \(databricks auth login --profile <name>\), then choose the engine again\./);
  assert.strictEqual(callsOf("engine_prepare").length, n);
});

test("several connections are asked for; a stopped warehouse says it will start; Allow starts it on the one picked", { skip }, async () => {
  next.conns = [CONN_DEV, CONN_OLD];
  next.plan = (p) => ({ engine: p.engine, transition: { needed: false }, confirm: true, grant_minutes: 45, cells: ["c1", "c2"],
    policy: { profile: "Sales dev" }, target: { kind: "warehouse", id: "wh9", name: "Big SQL", state: "STOPPED", host: "adb-2.azuredatabricks.net" },
    risk: { outcome: "warning", lines: [{ outcome: "warning", text: "No auto-stop on Big SQL" }] } });
  next.state = { engine: "databricks-sql", label: "Databricks SQL warehouse (remote)", status: "ready", remote: true, bills: "bills DBUs" };
  await pick("Databricks SQL warehouse");
  await page().locator("#menu", { hasText: "Databricks connection" }).waitFor();
  assert.deepStrictEqual(await page().locator("#menu [role^=menuitem]").allTextContents(),
    ["dev (Databricks CLI)", "old (Databricks CLI) (keeps a token in plain text)"]);
  await page().locator("#menu [role^=menuitem]", { hasText: "old (Databricks CLI)" }).click();
  const dialog = page().getByRole("dialog");
  await dialog.waitFor();
  const text = await dialog.textContent();
  assert.match(text, /Use Databricks SQL warehouse \(remote\)\?/);
  assert.match(text, /Big SQL · stopped adb-2\.azuredatabricks\.net/);
  assert.match(text, /Starts billable compute \(Databricks DBU\)\. It will start\./);
  assert.match(text, /Sales dev · asks before remote compute/);
  assert.match(text, /This notebook to start Big SQL for 45 minutes\./);
  assert.match(text, /Worth a look:No auto-stop on Big SQL/);
  assert.strictEqual(callsOf("engine_prepare").at(-1).params.connection, "cli:old");
  await dialog.getByRole("button", { name: "Allow for 45 minutes" }).click();
  assert.match(await toast(/now runs on/), /^Engines now runs on Databricks SQL warehouse \(remote\)\. 2 cells can't run on it or run translated; they're flagged before running\.$/);
  const sel = callsOf("engine_select").at(-1).params;
  assert.deepStrictEqual([sel.engine, sel.connection, sel.confirmed, sel.decision], ["databricks-sql", "cli:old", true, null]);
  // The toolbar now shows the connection and its catalog beside the engine, and the connection can be changed.
  const conn = page().getByRole("button", { name: /^Connection/ }).first();
  assert.match(await conn.textContent(), /old \(Databricks CLI\)/);
  assert.strictEqual(await page().locator(".engine-scope").first().textContent(), "hive");
  assert.match(await page().locator(".engine-where").first().textContent(), /Remote · bills DBUs/);
  await conn.click();
  await page().locator("#menu [role^=menuitem]", { hasText: "dev (Databricks CLI)" }).click();
  await page().waitForFunction(() => document.querySelector(".engine-scope")?.textContent === "main.sales");
  assert.match(await page().getByRole("button", { name: /^Connection/ }).first().textContent(), /dev \(Databricks CLI\)/);
});

test("switching from one engine to another asks what to do with the old session: Cancel, then Keep both", { skip }, async () => {
  await page().evaluate(() => { engineStates.set("Engines", { engine: "databricks-sql", label: "Databricks SQL warehouse (remote)", status: "ready", remote: true, bills: "bills DBUs" });
    state.notebook.engineConnection = { key: "cli:dev", label: "dev", catalog: "main", schema: "" }; renderNotebook(); });
  next.plan = (p) => ({ ...plain(p), transition: { needed: true, decisions: ["close", "keep"] } });
  next.state = { engine: "local-duckdb", label: "DuckDB (this PC)", status: "ready" };
  const n = callsOf("engine_select").length;
  await pick(/^DuckDB/);
  let dialog = page().getByRole("dialog");
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /Switch to DuckDB \(this PC\)\?.*can't move to it\..*Variables and tables in the Databricks SQL warehouse \(remote\) session.*Keep both leaves the Databricks SQL warehouse \(remote\) session open/);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.strictEqual(callsOf("engine_select").length, n);
  await pick(/^DuckDB/);
  dialog = page().getByRole("dialog");
  await dialog.getByRole("button", { name: "Keep both" }).click();
  await toast(/now runs on DuckDB/);
  assert.strictEqual(callsOf("engine_select").at(-1).params.decision, "keep");
});

test("from the kernel, the switch names its variables, and closing it restarts the kernel", { skip }, async () => {
  await page().evaluate(() => { state.variables = ["orders", "customers", "n", "df"].map((name) => ({ name, type: "int", value: "1" })); });
  next.plan = (p) => ({ ...plain(p), transition: { needed: true, decisions: ["close"] } });
  next.state = { engine: "local-duckdb", label: "DuckDB (this PC)", status: "ready" };
  const restarts = callsOf("restart_kernel").length;
  await pick(/^DuckDB/);
  const dialog = page().getByRole("dialog");
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /Variables in this session: orders, customers, n and 1 more/);
  assert.strictEqual(await dialog.getByRole("button", { name: "Keep both" }).count(), 0);
  await dialog.getByRole("button", { name: "Switch and close session" }).click();
  await toast(/now runs on DuckDB/);
  assert.strictEqual(callsOf("restart_kernel").length, restarts + 1);
  assert.strictEqual(callsOf("engine_select").at(-1).params.decision, null, "the kernel's own restart closes it");
});

test("Fabric Spark sends the attached lakehouse and says it uses Fabric capacity; Cancel starts nothing", { skip }, async () => {
  await page().evaluate(() => { state.notebook.lakehouse = { workspace_id: "ws-1", lakehouse_id: "lh-1", name: "LH_Sales" }; });
  next.plan = (p) => ({ ...plain(p), confirm: true, grant_minutes: 30, target: { id: "pool-1", name: "Starter pool" } });
  const n = callsOf("engine_select").length;
  await pick("Fabric Spark");
  const dialog = page().getByRole("dialog");
  await dialog.waitFor();
  const text = await dialog.textContent();
  assert.match(text, /Uses Fabric capacity while the Spark session runs\./);
  assert.match(text, /start a Spark session on Starter pool for 30 minutes\. It uses Fabric capacity until it closes\./);
  assert.match(text, /Couldn't run part of it:|No issues found\./);
  const prep = callsOf("engine_prepare").at(-1).params;
  assert.deepStrictEqual([prep.engine, prep.workspace_id, prep.lakehouse_id, prep.lakehouse_name], ["fabric-spark", "ws-1", "lh-1", "LH_Sales"]);
  assert.deepStrictEqual(prep.cells, ["select 1", "select 2"]);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.strictEqual(callsOf("engine_select").length, n);
});

test("a cell on an engine that is starting or failed isn't sent to it", { skip }, async () => {
  const n = callsOf("engine_start_cell").length;
  await page().evaluate(() => { engineStates.set("Engines", { engine: "local-duckdb", label: "DuckDB (this PC)", status: "starting" }); });
  assert.strictEqual(await page().evaluate(() => runCell(state.notebook.cells[0])), false);
  await toast(/^DuckDB \(this PC\) is still starting\.$/);
  await page().evaluate(() => { engineStates.set("Engines", { engine: "local-duckdb", label: "DuckDB (this PC)", status: "error", error: "the file is locked" }); });
  assert.strictEqual(await page().evaluate(() => runCell(state.notebook.cells[0])), false);
  await toast(/^DuckDB \(this PC\) isn't ready: the file is locked\.$/);
  assert.strictEqual(callsOf("engine_start_cell").length, n);
});

test("a poll that fails ends the cell with the engine's error", { skip }, async () => {
  await page().evaluate(() => { engineStates.set("Engines", { engine: "local-duckdb", label: "DuckDB (this PC)", status: "ready" }); renderNotebook(); });
  assert.strictEqual(await page().evaluate(() => runCell(state.notebook.cells[1])), false);
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[1].status), "error");
  await page().locator(".cell").nth(1).getByText(/the engine session went away/).first().waitFor();
  assert.strictEqual(await page().evaluate(() => state.notebook.engineRunning), false);
});

test("Cancel on the engine with nothing running says so", { skip }, async () => {
  await page().evaluate(() => { state.notebook.engineRunning = true; cancelRun(); });
  await toast(/^Nothing was running on the engine\.$/);
  assert.deepStrictEqual(callsOf("engine_cancel").at(-1).params, { notebook: "Engines" });
});

test("the command opens the picker, or asks for a notebook first", { skip }, async () => {
  const run = () => page().evaluate(() => features.commands.find((c) => c.label === "Choose the notebook's engine").run());
  await run();
  await page().locator("#menu", { hasText: "Engine for this notebook" }).waitFor();
  await page().evaluate(() => { closeMenu(); go("home"); });
  await page().waitForFunction(() => !document.querySelector(".engine-btn"));
  await run();
  await toast(/^Open a notebook first\.$/);
  await page().evaluate(() => openNotebook("Engines"));
  await page().locator(".engine-btn").first().waitFor();
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
