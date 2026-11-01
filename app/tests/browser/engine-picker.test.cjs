// The notebook engine picker (Preview): Engine sits beside Run all in place of Runs on. DuckDB on
// this PC runs a SQL cell for real and flags a Python cell before running it, with Run this cell on
// Python for that cell only. A remote engine asks first, says what it bills and what the risk check
// couldn't run, and Cancel starts nothing.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const plan = {
  engine: "databricks-sql", transition: { needed: false }, confirm: true, grant_minutes: 60, cells: [],
  target: { kind: "warehouse", id: "wh1", label: "Shared SQL" },
  risk: { outcome: "unknown", lines: [{ outcome: "unknown", text: "Couldn't run: couldn't read the warehouse (HTTP 403)" }] },
};

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 },
    engine: ["list_notebooks", "open_notebook", "save_notebook", "notebook_places", "compute_check", "kernel_variables",
      "start_cell", "poll_cell", "interrupt_cell", "restart_kernel",
      "engine_catalog", "engine_select", "engine_state", "engine_start_cell", "engine_poll_cell", "engine_cancel", "engine_close", "learn_patterns"],
    mocks: {
      notebook_runs: () => ({ runs: [] }),
      engine_connections: () => ({ connections: [{ key: "cli:dev", label: "dev (Databricks CLI)", host: "https://adb-1.azuredatabricks.net",
        warehouse_id: "wh1", cluster_id: null, catalog: "main", schema: "sales" }] }),
      engine_prepare: (p) => p.engine === "databricks-sql" ? plan
        : { engine: p.engine, transition: { needed: false }, confirm: false, grant_minutes: 60, cells: [], risk: { outcome: "pass", lines: [] } },
    } });
  await page().evaluate(async () => {
    await rpc("save_notebook", { name: "Engines", cells: [{ type: "code", source: "%%sql\nselect 42 as answer" },
      { type: "code", source: "n = 7\nprint(n * 6)" }] });
    await openNotebook("Engines");
  });
});
after(async () => { if (app) await app.close(); });

test("Engine replaces Runs on beside Run all, and says This PC", { skip }, async () => {
  const engine = page().locator(".engine-btn").first();
  await engine.waitFor();
  assert.match(await engine.textContent(), /Engine\s*Python \(this PC\)/);
  assert.strictEqual(await page().locator(".engine-where").first().textContent(), "This PC");
  assert.strictEqual(await page().getByRole("button", { name: /^Runs on/ }).count(), 0);
  await engine.click();
  await page().locator("#menu [role^=menuitem]", { hasText: "More" }).waitFor();  // the menu opens once the catalog is read
  const rows = await page().locator("#menu [role^=menuitem]").allTextContents();
  for (const name of ["Python (this PC)", "DuckDB", "Databricks SQL warehouse", "More"]) {
    assert.ok(rows.some((r) => r.startsWith(name)), `${name} is in the menu: ${rows.join(" | ")}`);
  }
  assert.ok(!rows.some((r) => r.startsWith("Apache Spark")), "planned engines sit behind More");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "engine-menu.png") });
  await page().keyboard.press("Escape");
});

test("How to choose opens the DuckDB or Spark decision pattern (GD-05)", { skip }, async () => {
  await page().locator(".engine-btn").first().click();
  await page().locator("#menu [role^=menuitem]", { hasText: "This PC or Fabric? How to choose" }).click();
  const dialog = page().getByRole("dialog", { name: "DuckDB on this PC or Spark in Fabric" });
  await dialog.waitFor();
  const said = await dialog.innerText();
  assert.match(said, /DuckDB on this PC works when:[\s\S]*Spark in Fabric works when:[\s\S]*Each one fails when:/);
  assert.match(said, /Cost:[\s\S]*Reliability:[\s\S]*How to decide:[\s\S]*Try it on this PC/);
  assert.match(await dialog.locator("pre").innerText(), /^# Runs on this PC with DuckDB/);
  if (SHOTS) { await page().waitForTimeout(400); await page().screenshot({ path: path.join(SHOTS, "decision-pattern.png") }); }  // after the fade-in
  await dialog.getByRole("link", { name: "Open in the guide" }).click();
  const opened = await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").map((c) => c.args.page));
  assert.deepStrictEqual(opened.slice(-1), ["decision-patterns.md#dp-02"]);
  await dialog.getByRole("button", { name: "Close" }).last().click();
});

test("a remote engine asks first, and Cancel starts nothing", { skip }, async () => {
  await page().locator(".engine-btn").first().click();
  await page().locator("#menu [role^=menuitem]", { hasText: "Databricks SQL warehouse" }).click();
  const dialog = page().getByRole("dialog");
  await dialog.waitFor();
  const text = await dialog.textContent();
  assert.match(text, /Each start bills DBUs until its auto-stop/);
  assert.match(text, /Couldn't run: couldn't read the warehouse \(HTTP 403\)/);
  assert.match(text, /never falls back to remote compute on its own/);
  assert.doesNotMatch(text, /no Fabric capacity/i);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "engine-remote-confirm.png") });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.ok(!app.calls.some((c) => c.method === "engine_select"), "nothing was selected");
});

test("DuckDB runs the SQL cell and flags the Python cell, which runs on Python for that cell only", { skip }, async () => {
  await page().locator(".engine-btn").first().click();
  await page().locator("#menu [role^=menuitem]", { hasText: /^DuckDB/ }).click();
  await page().waitForFunction(() => engineStates.get("Engines")?.status === "ready", null, { timeout: 20000 });
  assert.match(await page().locator(".engine-btn").first().textContent(), /DuckDB/);
  assert.strictEqual(await page().getByRole("button", { name: /^Connection/ }).count(), 0, "the Databricks connection shows only beside a Databricks engine");
  await page().evaluate(() => runCell(state.notebook.cells[0]));
  const first = page().locator(".cell").nth(0);
  await first.locator("td", { hasText: /^42$/ }).waitFor({ timeout: 20000 });
  assert.match(await first.textContent(), /DuckDB/, "the status line names the engine that ran it");
  await page().evaluate(() => runCell(state.notebook.cells[1]));
  const second = page().locator(".cell").nth(1);
  const flag = second.locator(".engine-flag");
  await flag.waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "engine-cell-flag.png") });
  await flag.getByRole("button", { name: "Run this cell on Python" }).click();
  await second.locator(".output, pre", { hasText: /^42\s*$/ }).first().waitFor({ timeout: 30000 });
  assert.match(await second.textContent(), /Python \(this PC\), this cell only/);
  assert.strictEqual(await page().evaluate(() => engineStates.get("Engines").engine), "local-duckdb", "the notebook stays on DuckDB");
});
