// SQL-003: managed runs on the SQL query page, against the real engine (see harness.cjs for how to
// run it). Queries use DuckDB's range(), so no lakehouse, sign-in or Fabric is needed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const SHOTS = process.env.SCREENSHOT_DIR;
const SLOW = "SELECT sum(a.range * b.range) AS s FROM range(200000000) a, range(1000) b";

let app;
const skip = !playwright && "Playwright is not installed";

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_start", "sql_status", "sql_page", "sql_cancel",
      "sql_export", "sql_export_status", "sql_export_cancel", "app_settings", "save_app_settings"],
    mocks: { list_tables: () => ({ dbo: ["sales"] }), table_columns: () => [] },
  });
  await app.page.evaluate((lh) => openSqlQuery(lh), LH);
  await app.page.waitForSelector(".sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const page = () => app.page;
async function setDoc(text) {
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
    v.focus();
  }, text);
}
const pill = () => page().locator(".sql-pill").textContent();
const waitPill = (text, timeout = 60000) =>
  page().waitForFunction((t) => document.querySelector(".sql-pill")?.textContent === t
    && (t === "Running" || !sqlPage.tabs[sqlPage.active].running), text, { timeout });
const band = () => page().locator(".sql-band").textContent();
const toastText = async () => (await page().locator(".toast").allTextContents()).join(" | ");
const runs = () => app.calls.filter((c) => c.method === "sql_start").map((c) => c.params);

test("a run goes through the engine's managed lifecycle and shows its first page", { skip }, async () => {
  await setDoc("SELECT range AS n, CASE WHEN range % 2 = 0 THEN NULL ELSE 'odd' END AS label, range AS n FROM range(2500)");
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  const call = runs().at(-1);
  assert.deepStrictEqual(call.lakehouse_ref, LH);
  assert.deepStrictEqual(call.allow, []);
  assert.match(await band(), /2,500 rows · [\d.]+ s on this PC · rows 1 to 1,000/);
  const heads = await page().locator(".sql-grid th").allTextContents();
  assert.deepStrictEqual(heads, ["", "n", "label", "n"], "repeated column names are kept");
  assert.strictEqual(await page().locator(".sql-grid tbody tr").count(), 1000);
  assert.strictEqual(await page().locator(".sql-grid tbody tr").first().locator("td.null").count(), 1);
  assert.ok(!app.calls.some((c) => c.method === "run_sql"), "the page no longer loads whole results");
});

test("Next and Previous page through the result without running it again", { skip }, async () => {
  const before = runs().length;
  await page().getByRole("button", { name: "Next page" }).click();
  await page().waitForFunction(() => document.querySelector(".sql-grid tbody td.i")?.textContent === "1001");
  await page().getByRole("button", { name: "Next page" }).click();
  await page().waitForFunction(() => document.querySelector(".sql-grid tbody td.i")?.textContent === "2001");
  assert.strictEqual(await page().locator(".sql-grid tbody tr").count(), 500);
  assert.ok(await page().getByRole("button", { name: "Next page" }).isDisabled());
  assert.match(await band(), /rows 2,001 to 2,500/);
  await page().getByRole("button", { name: "Previous page" }).click();
  await page().waitForFunction(() => document.querySelector(".sql-grid tbody td.i")?.textContent === "1001");
  assert.strictEqual(runs().length, before, "paging doesn't rerun the query");
});

test("Export ▾ CSV streams the result to Downloads", { skip }, async () => {
  await page().locator(".sql-export > summary").click();
  await page().getByRole("menuitem", { name: "CSV" }).click();
  await page().waitForFunction(() => /Saved 2,500 rows to/.test(document.querySelector(".toast")?.textContent || ""));
  const saved = (await toastText()).match(/Saved 2,500 rows to (.+\.csv)/)[1];
  const lines = fs.readFileSync(saved, "utf8").trim().split("\n");
  assert.strictEqual(lines.length, 2501);
  assert.strictEqual(lines[0], '"n","label","n"');
  assert.ok(!fs.readdirSync(path.dirname(saved)).some((f) => f.endsWith(".partial")));
});

test("Export ▾ Parquet never replaces the CSV and asks nothing", { skip }, async () => {
  await page().locator(".sql-export > summary").click();
  await page().getByRole("menuitem", { name: "Parquet" }).click();
  await page().waitForFunction(() => /\.parquet/.test(document.querySelector(".toast")?.textContent || ""));
  const saved = (await toastText()).match(/to (.+\.parquet)/)[1];
  assert.ok(fs.statSync(saved).size > 0);
});

test("Stop ends a running query and the page says it stopped", { skip }, async () => {
  await setDoc(SLOW);
  await page().keyboard.press("Control+Enter");
  await waitPill("Running");
  await page().waitForSelector(".sql-band button:has-text('Stop')");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-003-running.png") });
  await page().locator(".sql-band button:has-text('Stop')").click();
  await waitPill("Stopped");
  assert.match(await page().locator(".sql-grid .output.error").textContent(), /Stopped/);
  const status = await page().evaluate(() => rpc("sql_status", { execution: sqlPage.tabs[sqlPage.active].exec.id }));
  assert.strictEqual(status.state, "cancelled");
  assert.ok(!JSON.stringify(status).includes("range(200000000)"), "run status never carries the SQL");
});

test("a query that changes its own session asks first, then runs with that allowed", { skip }, async () => {
  await setDoc("CREATE TEMP VIEW odd AS SELECT range AS n FROM range(10) WHERE range % 2 = 1; SELECT * FROM odd");
  await page().keyboard.press("Control+Enter");
  await page().waitForSelector(".dialog");
  assert.match(await page().locator(".dialog").textContent(), /Let this query change its own session\?.*Nothing has run yet/s);
  await page().locator(".dialog-actions button:has-text('Run')").click();
  await waitPill("Succeeded");
  assert.deepStrictEqual(runs().at(-1).allow, ["local_mutation"]);
  assert.strictEqual(await page().locator(".sql-grid tbody tr").count(), 5);
});

test("a lakehouse write is refused with the reason, and nothing is asked", { skip }, async () => {
  await setDoc("DELETE FROM sales WHERE id = 1");
  await page().keyboard.press("Control+Enter");
  await waitPill("Failed");
  assert.match(await page().locator(".sql-grid .output.error").textContent(),
    /would change a lakehouse table in OneLake.*nothing ran.*Save as table/s);
  assert.strictEqual(await page().locator(".dialog").count(), 0);
});

test("reading a file on this PC is blocked inside the run", { skip }, async () => {
  await page().locator("select.sql-dialect").selectOption("duckdb");
  await setDoc("SELECT * FROM read_csv('/etc/hostname')");
  await page().keyboard.press("Control+Enter");
  await waitPill("Failed");
  assert.match(await page().locator(".sql-grid .output.error").textContent(), /disabled by configuration/);
  await page().locator("select.sql-dialect").selectOption("spark");
});

test("Settings › Advanced › SQL queries shows the limits and saves them", { skip }, async () => {
  await page().evaluate(() => go("settings"));
  await page().click("button.fold-toggle:has-text('Advanced')");  // folded by default (UX-006)
  await page().waitForSelector("text=SQL queries");
  const rows = page().getByRole("spinbutton", { name: "Rows per page" });
  assert.strictEqual(await rows.inputValue(), "1000");
  assert.strictEqual(await page().getByRole("spinbutton", { name: "Time limit" }).inputValue(), "10");
  assert.strictEqual(await page().getByRole("spinbutton", { name: "Memory" }).inputValue(), "60");
  assert.strictEqual(await page().getByRole("spinbutton", { name: "Queries at once" }).inputValue(), "2");
  await rows.fill("500");
  await page().locator(".advanced-group:has(h3:text('SQL queries')) button:has-text('Save')").click();
  await page().waitForFunction(() => /New SQL queries use these limits/.test(document.querySelector(".toast")?.textContent || ""));
  const settings = await page().evaluate(() => rpc("app_settings"));
  assert.strictEqual(settings.sql_page_rows.value, 500);
  if (SHOTS) {
    await page().locator(".advanced-group:has(h3:text('SQL queries'))").scrollIntoViewIfNeeded();
    await page().screenshot({ path: path.join(SHOTS, "sql-003-settings.png") });
  }
});

test("the next run pages by the new page size", { skip }, async () => {
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  await setDoc("SELECT range AS n FROM range(1200)");
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  assert.strictEqual(await page().locator(".sql-grid tbody tr").count(), 500);
  assert.match(await band(), /rows 1 to 500/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-003-paging.png") });
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
