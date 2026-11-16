// SQL-003: managed runs on the SQL query page, against the real engine (see harness.cjs for how to
// run it). Queries use DuckDB's range(), so no lakehouse, sign-in or Fabric is needed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");
// axe covers dialogs (#1673, ui.test.cjs): the cases below run axe while these are open.
// axe covers dialogs: query.js:renderQuery

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const SHOTS = process.env.SCREENSHOT_DIR;
const SLOW = "SELECT sum(a.range * b.range) AS s FROM range(200000000) a, range(1000) b";

let app;
const skip = !playwright && "Playwright is not installed";

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "sql_start", "sql_status", "sql_page", "sql_cancel", "sql_pin", "learn_fix_compare",
      "sql_file_info", "sql_file_read", "sql_file_write",
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

test("a script of several statements gives a result tab for each and stops at the first error (S14)", { skip }, async () => {
  await setDoc("SELECT range AS n FROM range(3);\nSELECT 'x' AS word;\nSELECT oops FROM range(2) AS r;\nSELECT 4 AS never");
  await page().keyboard.press("Control+Enter");
  await waitPill("Failed");
  const call = runs().at(-1);
  assert.deepStrictEqual(call.statements, ["SELECT range AS n FROM range(3);", "SELECT 'x' AS word;", "SELECT oops FROM range(2) AS r;", "SELECT 4 AS never"]);
  await page().waitForSelector(".sql-rtab");
  const tabs = await page().locator(".sql-rtab").allTextContents();
  assert.deepStrictEqual(tabs, ["1 SELECT · 3 rows", "2 SELECT · 1 row", "3 Error", "4 Not run"]);
  assert.strictEqual(await page().locator(".sql-rtab[aria-selected=true]").textContent(), "3 Error");
  assert.match(await page().locator(".sql-stop-note").textContent(), /Statement 3 failed, so statement 4 didn't run\. Binder Error: Referenced column "oops" not found in FROM clause \(line 3\)\. Statements 1 and 2 finished/);
  assert.match(await page().locator(".sql-grid pre").textContent(), /Candidate bindings/, "the whole error on its own tab");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-s14-stopped.png") });
  await page().locator(".sql-rtab", { hasText: "1 SELECT" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".sql-grid tbody tr").length === 3);
  assert.deepStrictEqual(await page().locator(".sql-grid th").allTextContents(), ["", "n"]);
  await page().getByRole("button", { name: "Go to line 3" }).click();
  const picked = await page().evaluate(() => { const s = sqlPage.editor.selection(); return sqlPage.editor.text().slice(s.from, s.to); });
  assert.strictEqual(picked, "SELECT oops FROM range(2) AS r;");
  await setDoc("SELECT 1 AS a;\nSELECT 2 AS b");
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  assert.deepStrictEqual(await page().locator(".sql-rtab").allTextContents(), ["1 SELECT · 1 row", "2 SELECT · 1 row"]);
  assert.strictEqual(await page().locator(".sql-rtab[aria-selected=true]").textContent(), "2 SELECT · 1 row");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-s14-all-ran.png") });
  await setDoc("SELECT 1 AS a;\nSELECT 2 AS b");
  await page().evaluate(() => { const v = sqlPage.editor.view; v.dispatch({ selection: { anchor: 3 } }); });
  await page().keyboard.press("Control+Shift+Enter");
  await waitPill("Succeeded");
  assert.ok(!runs().at(-1).statements, "Ctrl+Shift+Enter runs one statement");
  assert.ok(await page().locator(".sql-rtabs-row").isHidden());
});

test("History lists the tab's runs; an old result opens again without running, and can be pinned (S27)", { skip }, async () => {
  await setDoc("SELECT range AS n FROM range(4)");
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  await setDoc("SELECT range AS n FROM range(2)");
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  const before = runs().length;
  await page().getByRole("tab", { name: /^History/ }).click();
  const rows = page().locator(".sql-history-list tbody tr");
  assert.match(await rows.first().textContent(), /SELECT range AS n FROM range\(2\)Latest2/);
  await rows.nth(1).getByRole("button", { name: "Pin" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".sql-history-list .sql-pinned").length === 1);
  await rows.nth(1).locator(".link-btn").click();
  await page().waitForFunction(() => document.querySelectorAll(".sql-grid tbody tr").length === 4);
  assert.match(await page().locator(".sql-old-note").textContent(), /not the latest run\. Read from the snapshot on this PC; nothing ran\./);
  assert.strictEqual(runs().length, before, "opening an old result runs nothing");
  await page().getByRole("button", { name: "Compare with latest" }).click();
  await page().waitForFunction(() => /Different from the latest run: 4 rows then, 2 rows in the latest/.test(document.querySelector(".toast")?.textContent || ""));
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-s27-old-result.png") });
  await page().getByRole("button", { name: "Back to the latest" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".sql-grid tbody tr").length === 2);
  assert.ok(await page().locator(".sql-old-note").count() === 0);
  await page().getByRole("tab", { name: /^History/ }).click();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-s27-history.png") });
  await page().getByRole("tab", { name: /^Results/ }).click();
});

test("a query with parameters asks for their values once, then keeps them with the tab (S17)", { skip }, async () => {
  await setDoc("SELECT :word AS w,\n  {{n}} + 1 AS n, ':not' AS s");
  await page().keyboard.press("Control+Enter");
  await page().waitForSelector(".sql-params:not([hidden])");
  assert.match(await page().locator("#sql-params-h").textContent(), /This query has 2 parameters/);
  const before = runs().length;
  await page().getByLabel("Value of word").fill("x' OR '1'='1");
  await page().getByLabel("Type of n").selectOption("number");
  await page().getByLabel("Value of n").fill("4,2");
  await page().getByRole("button", { name: "Run with these values" }).click();
  assert.match(await page().locator(".sql-params").textContent(), /Write a number, such as 42 or 3\.5\./);
  assert.strictEqual(runs().length, before, "nothing runs while a value doesn't fit");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-s17-ask.png") });
  await page().getByLabel("Value of n").fill("41");
  await page().getByRole("button", { name: "Run with these values" }).click();
  await waitPill("Succeeded");
  const call = runs().at(-1);
  assert.deepStrictEqual(call.params, [{ name: "word", type: "text", value: "x' OR '1'='1" }, { name: "n", type: "number", value: "41" }]);
  assert.ok(call.query.includes(":word") && !call.query.includes("OR '1'"), "the values never go into the SQL");
  assert.deepStrictEqual(await page().locator(".sql-grid tbody td:not(.i)").allTextContents(), ["x' OR '1'='1", "42", ":not"]);
  assert.match(await band(), /with word = x' OR '1'='1, n = 41/);
  assert.match(await page().locator(".sql-param-chip").textContent(), /2 parameters: word = x' OR '1'='1, n = 41/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-s17-kept.png") });
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  assert.ok(await page().locator(".sql-params").isHidden(), "kept values don't ask again");
  const kept = await page().evaluate(() => JSON.parse(localStorage.getItem(queryTabsKey(sqlPage.ref)))[sqlPage.active].params);
  assert.deepStrictEqual(kept, { word: { type: "text", value: "x' OR '1'='1" }, n: { type: "number", value: "41" } });
});

test("a .sql file opens in a tab, saves back to itself and says when another program changed it (S38)", { skip }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kf-sqlfile-"));
  const file = path.join(dir, "orders.sql");
  fs.writeFileSync(file, '-- KratosForge parameters: {"w":{"type":"text","value":"West"}}\nSELECT :w AS w\n');
  await page().evaluate(() => {
    const invoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => (cmd === "pick_sql_file" ? window.__fdPick(args) : invoke(cmd, args));
  });
  await page().evaluate((file) => { window.__fdPick = () => file; }, file);
  await page().getByRole("button", { name: "Open .sql..." }).click();
  await page().waitForSelector(".sql-file-chip");
  assert.strictEqual(await page().locator(".sql-tabs [aria-selected=true]").textContent(), "orders.sql");
  assert.strictEqual(await page().locator(".sql-file-chip").textContent(), `File on this PC: ${file}`);
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), "SELECT :w AS w\n", "the parameter line stays out of the editor");
  assert.deepStrictEqual(await page().evaluate(() => sqlPage.tabs[sqlPage.active].params), { w: { type: "text", value: "West" } });
  const dot = page().locator(".sql-tab.on .sql-dirty");
  assert.ok(await dot.isHidden());

  await setDoc("SELECT :w AS w, 2 AS two\n");
  assert.ok(await dot.isVisible(), "a change the file doesn't have shows a dot");
  await page().keyboard.press("Control+s");
  await page().waitForFunction(() => /Saved orders\.sql/.test(document.querySelector(".toast")?.textContent || ""));
  assert.strictEqual(fs.readFileSync(file, "utf8"), '-- KratosForge parameters: {"w":{"type":"text","value":"West"}}\nSELECT :w AS w, 2 AS two\n',
    "saved back with its parameter line and its own LF line endings");
  assert.ok(await page().locator(".sql-tab.on .sql-dirty").isHidden());

  fs.writeFileSync(file, "SELECT 'from another editor' AS s\n");
  fs.utimesSync(file, new Date(), new Date(Date.now() + 5000));
  await page().evaluate(() => window.dispatchEvent(new Event("focus")));
  await page().waitForSelector(".sql-disk-note:not([hidden])");
  assert.match(await page().locator(".sql-disk-note").textContent(), /orders\.sql changed on this PC.*Your tab has no unsaved changes\./);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-s38-changed.png") });
  await page().getByRole("button", { name: "Load the new version" }).click();
  await page().waitForFunction(() => sqlPage.editor?.text() === "SELECT 'from another editor' AS s\n");
  assert.ok(await page().locator(".sql-disk-note").isHidden());

  await setDoc("SELECT 'mine' AS s\n");
  await page().locator(".sql-tab.on .close").click();
  await page().waitForSelector("#dialog-root .dialog");
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /Save changes to orders\.sql\?.*file on this PC stays as it was/);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "sql-s38-close.png") });
  await page().getByRole("button", { name: "Don't save" }).click();
  await page().waitForFunction(() => !sqlPage.tabs.some((t) => t.disk));
  assert.strictEqual(fs.readFileSync(file, "utf8"), "SELECT 'from another editor' AS s\n", "Don't save leaves the file as it was");

  const copy = path.join(dir, "copy.sql");
  await page().evaluate((copy) => { window.__fdPick = (args) => (args.saveName ? copy : null); }, copy);
  await setDoc("SELECT 1 AS one\nSELECT 2 AS two");
  await page().getByRole("button", { name: "Save as .sql..." }).click();
  await page().waitForSelector(".sql-file-chip");
  assert.strictEqual(fs.readFileSync(copy, "utf8"), "SELECT 1 AS one\r\nSELECT 2 AS two", "a new file has Windows line endings");
  assert.strictEqual(await page().locator(".sql-tabs [aria-selected=true]").textContent(), "copy.sql");
  const shell = await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "pick_sql_file").length);
  assert.strictEqual(shell, 0, "the test's picker stood in for the shell's");
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
