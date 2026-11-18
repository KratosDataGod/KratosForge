// SQL-003: managed runs on the SQL query page, against the real engine (see harness.cjs for how to
// run it). Queries use DuckDB's range(), so no lakehouse, sign-in or Fabric is needed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");
// axe covers dialogs (#1673, ui.test.cjs): the S38 case below runs axe while this is open.
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
      "sql_export", "sql_export_status", "sql_export_cancel", "sql_column_stats", "app_settings", "save_app_settings"],
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
  await page().getByRole("menuitem", { name: "CSV", exact: true }).click();
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

test("headers sort and Filter's boxes filter every row of the result, on this PC, without running it again", { skip }, async () => {
  await setDoc("SELECT range AS n, CASE range % 3 WHEN 0 THEN 'West' WHEN 1 THEN 'North' ELSE 'Central' END AS region FROM range(1200)");
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  const before = runs().length;
  const first = () => page().locator(".sql-grid tbody tr").first().locator("td").nth(1).textContent();
  const nHead = page().locator(".sql-grid th").nth(1);
  await nHead.getByRole("button").click();
  await page().waitForFunction(() => document.querySelector(".sql-grid th:nth-child(2)")?.getAttribute("aria-sort") === "ascending");
  await nHead.getByRole("button").click();
  await page().waitForFunction(() => document.querySelector(".sql-grid th:nth-child(2)")?.getAttribute("aria-sort") === "descending");
  assert.strictEqual(await first(), "1,199", "the largest of every row, not of the page");
  assert.match(await band(), /1,200 rows · sorted by n/);
  await page().getByRole("button", { name: "Filter", exact: true }).click();
  assert.strictEqual(await page().getByRole("button", { name: "Filter", exact: true }).getAttribute("aria-pressed"), "true");
  const region = page().getByRole("textbox", { name: "Filter region" });
  await region.fill("!= central");
  await region.press("Enter");
  await page().waitForFunction(() => /800 of 1,200 rows \(filtered\)/.test(document.querySelector(".sql-band")?.textContent || ""));
  const n = page().getByRole("textbox", { name: "Filter n" });
  await n.fill("< 10");  // applies as you stop typing
  await page().waitForFunction(() => /7 of 1,200 rows/.test(document.querySelector(".sql-band")?.textContent || ""));
  assert.ok(await n.evaluate((el) => el === document.activeElement), "the box keeps focus through the redraw");
  assert.deepStrictEqual(await page().locator(".sql-grid tbody tr td:nth-child(2)").allTextContents(), ["9", "7", "6", "4", "3", "1", "0"]);
  await n.fill("< ten");
  await n.press("Enter");
  await page().waitForSelector(".sql-grid-error");
  assert.match(await page().locator(".sql-grid-error").textContent(), /Write a number after < in the filter on n/);
  assert.strictEqual(await n.getAttribute("aria-invalid"), "true");
  assert.strictEqual(await page().locator(".sql-grid tbody tr").count(), 7, "the rows on screen stay");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "grid-sort-filter.png") });
  // Text typed while a page is still loading stays in the box through the redraw, and is applied after it.
  await page().evaluate(() => {
    const core = window.__TAURI__.core;
    const real = core.invoke;
    window.__held = [];
    window.__hold = true;
    core.invoke = (cmd, args) => (cmd === "rpc" && args?.method === "sql_page" && window.__hold
      ? new Promise((resolve, reject) => window.__held.push(() => real(cmd, args).then(resolve, reject))) : real(cmd, args));
  });
  await n.fill("< 1");
  await n.press("Enter");
  await page().waitForFunction(() => window.__held.length === 1);
  await page().keyboard.type("00");
  await page().evaluate(() => { window.__hold = false; window.__held.splice(0).forEach((go) => go()); });
  await page().waitForFunction(() => /[^\d,]1 of 1,200 rows/.test(document.querySelector(".sql-band")?.textContent || ""));
  assert.strictEqual(await n.inputValue(), "< 100", "the box kept what was typed while the page loaded");
  await page().waitForFunction(() => /67 of 1,200 rows/.test(document.querySelector(".sql-band")?.textContent || ""));
  assert.strictEqual(await n.inputValue(), "< 100");
  await page().getByRole("button", { name: "Filter", exact: true }).click();  // off: the filters go
  await page().waitForFunction(() => /1,200 rows · sorted by n/.test(document.querySelector(".sql-band")?.textContent || ""));
  assert.strictEqual(runs().length, before, "nothing ran again");
  const pages = app.calls.filter((c) => c.method === "sql_page").at(-1).params;
  assert.deepStrictEqual(pages.sort, [{ column: 0, desc: true }]);
  assert.strictEqual(pages.filters, undefined);
});

test("cells select with the mouse and keys, right-click copies them, a value opens in full, and a column's statistics cover every row", { skip }, async () => {
  await setDoc(`SELECT range AS n, CASE WHEN range = 1 THEN '{"tier":"gold","tags":[1,2]}' ELSE 'r' || range END AS note FROM range(1200)`);
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  const before = runs().length;
  await page().evaluate(() => { window.__copied = []; Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied.push(t); } } }); });
  const cell = (r, c) => page().locator(`.sql-grid tbody tr[data-r="${r}"] td[data-c="${c}"]`);
  await cell(0, 0).click();
  await cell(2, 1).click({ modifiers: ["Shift"] });
  assert.match(await band(), /3 × 2 cells selected/);
  assert.strictEqual(await page().locator(".sql-grid td.sel").count(), 6);
  await page().keyboard.press("Control+c");
  await page().waitForFunction(() => window.__copied.length === 1);
  assert.strictEqual(await page().evaluate(() => window.__copied[0]), 'r0\n1\t{"tier":"gold","tags":[1,2]}\n2\tr2'.replace(/^r0/, "0\tr0"));
  await page().keyboard.press("Control+Shift+C");
  await page().waitForFunction(() => window.__copied.length === 2);
  assert.match(await page().evaluate(() => window.__copied[1]), /^n\tnote\n0\tr0/, "with headers");
  // Right-click inside the selection keeps it; the menu is the mockup's.
  await cell(1, 1).click({ button: "right" });
  const items = await page().locator("#menu [role=menuitem]").evaluateAll((els) => els.map((e) => e.firstChild.textContent));
  assert.deepStrictEqual(items, ["Copy", "Copy with headers", "Copy as CSV", "Copy as JSON", "Copy as INSERT statements", "Open value", "Column statistics"]);
  await page().getByRole("menuitem", { name: "Copy as JSON" }).click();
  await page().waitForFunction(() => window.__copied.length === 3);
  assert.strictEqual(JSON.parse(await page().evaluate(() => window.__copied[2])).length, 3);
  assert.ok(await page().locator(".sql-grid table").evaluate((t) => t === document.activeElement), "focus goes back to the grid");
  // Arrow keys move one cell from the focused one (the block's last, row 3); Enter opens the value in full, formatted.
  await page().keyboard.press("ArrowUp");
  assert.doesNotMatch(await band(), /cells selected/);
  await page().keyboard.press("Enter");
  const side = page().locator(".sql-side");
  await side.waitFor();
  assert.strictEqual(await side.locator("h3").textContent(), "note, row 2");
  assert.strictEqual(await side.locator("pre").textContent(), '{\n  "tier": "gold",\n  "tags": [\n    1,\n    2\n  ]\n}');
  assert.match(await side.textContent(), /Length28 characters/);
  await side.getByRole("button", { name: "Copy" }).click();
  await page().waitForFunction(() => window.__copied.length === 4);
  assert.strictEqual(await page().evaluate(() => window.__copied[3]), '{"tier":"gold","tags":[1,2]}', "the value as it is");
  await page().locator(".sql-grid table").focus();
  await page().keyboard.press("Escape");
  await page().waitForFunction(() => !document.querySelector(".sql-side"));
  // Column statistics: over all 1,200 rows, not the page.
  await cell(0, 0).click({ button: "right" });
  await page().getByRole("menuitem", { name: "Column statistics" }).click();
  await page().waitForFunction(() => /Mean/.test(document.querySelector(".sql-side")?.textContent || ""));
  const stats = await side.textContent();
  assert.match(stats, /Rows1,200/);
  assert.match(stats, /Max1,199/);
  assert.match(stats, /Over the whole result snapshot/);
  assert.strictEqual(await side.locator(".hist span").count() > 0, true);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "grid-stats.png") });
  await side.getByRole("button", { name: "Close" }).click();
  await page().waitForFunction(() => !document.querySelector(".sql-side"));
  assert.strictEqual(runs().length, before, "nothing ran again");
  assert.strictEqual(app.calls.filter((c) => c.method === "sql_column_stats").at(-1).params.column, 0);
});

test("Columns... hides and reorders the grid's columns, a header's edge resizes one, and a new run keeps the layout", { skip }, async () => {
  const q = "SELECT range AS n, 'r' || range AS note, range * 2 AS twice FROM range(50)";
  await setDoc(q);
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  const heads = () => page().locator(".sql-grid thead tr:first-child th[data-col] .sql-sort").evaluateAll((els) => els.map((e) => e.firstChild.textContent));
  await page().getByRole("button", { name: "Columns...", exact: true }).click();
  const dialog = page().getByRole("dialog", { name: "Columns" });
  await dialog.getByRole("checkbox", { name: /note/ }).uncheck();
  await dialog.getByRole("button", { name: "Move twice up" }).click();
  assert.ok(await dialog.getByRole("button", { name: "Move twice up" }).evaluate((b) => b === document.activeElement), "focus stays on the moved column");
  assert.deepStrictEqual(await heads(), ["n", "twice"]);
  await dialog.getByRole("button", { name: "Move twice up" }).click();
  assert.deepStrictEqual(await heads(), ["twice", "n"]);
  // axe covers dialogs: sqlgrid.js:sqlColumnsDialog
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), []);  // CI installs axe-core
  await dialog.getByRole("button", { name: "Done" }).click();
  assert.match(await page().locator(".sql-grid-hidden").textContent(), /1 column is hidden/);
  assert.deepStrictEqual(await page().locator(".sql-grid tbody tr").first().locator("td").allTextContents(), ["1", "0", "0"]);
  // Copy is of the columns on screen, in their order.
  await page().evaluate(() => { window.__copied = []; Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied.push(t); } } }); });
  await page().locator('.sql-grid tbody tr[data-r="2"] td[data-c="0"]').click();
  await page().keyboard.press("Control+Shift+C");
  await page().waitForFunction(() => window.__copied.length === 1);
  assert.strictEqual(await page().evaluate(() => window.__copied[0]), "twice\n4");
  // The handle at a header's edge: arrow keys resize, double-click fits again.
  const th = page().locator('.sql-grid th[data-col="2"]');
  const w0 = (await th.boundingBox()).width;
  await th.locator(".sql-col-resize").focus();
  for (let k = 0; k < 4; k++) await page().keyboard.press("ArrowRight");
  const widthIs = (w) => page().waitForFunction((w) => Math.abs(document.querySelector('.sql-grid th[data-col="2"]').getBoundingClientRect().width - w) <= 2, w);
  await widthIs(w0 + 64);  // wider by 64 px
  const w1 = (await th.boundingBox()).width;
  // Run it again: the same columns, so the layout stays.
  await page().locator(".sql-edit .cm-content").click();
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  await page().waitForSelector(".sql-grid tbody tr");
  assert.deepStrictEqual(await heads(), ["twice", "n"]);
  await widthIs(w1);  // the width too
  await th.locator(".sql-col-resize").dblclick();
  await page().waitForFunction((w) => document.querySelector('.sql-grid th[data-col="2"]').getBoundingClientRect().width < w - 8, w1);  // fits again
  // A run that ends while Columns... is open carries the layout to a new grid state: changes go to that one.
  await page().getByRole("button", { name: "Columns...", exact: true }).click();
  const old = await page().evaluate(() => { const t = sqlPage.tabs[sqlPage.active]; const g = t.grid; t.grid = sqlGridCarry(g); window.__oldGrid = g; return g.hidden.length; });
  await dialog.getByRole("checkbox", { name: /^n\b/ }).uncheck();
  assert.deepStrictEqual(await heads(), ["twice"], "the grid on screen changed");
  assert.deepStrictEqual(await page().evaluate(() => window.__oldGrid.hidden.length), old, "not the state the run replaced");
  await dialog.getByRole("button", { name: "Show all" }).click();
  await dialog.getByRole("button", { name: "Done" }).click();
  assert.deepStrictEqual(await heads(), ["n", "note", "twice"]);
  // Opened on a state kept from other columns, it starts again for these.
  await page().evaluate(() => { Object.assign(sqlPage.tabs[sqlPage.active].grid, { names: ["gone"], hidden: [0] }); });
  await page().getByRole("button", { name: "Columns...", exact: true }).click();
  assert.strictEqual(await dialog.getByRole("checkbox").count(), 3);
  await dialog.getByRole("button", { name: "Done" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("each statement's result keeps its own sort, and the grid reads that statement's rows (S14 with the grid)", { skip }, async () => {
  await setDoc("SELECT range AS n FROM range(5);\nSELECT range AS m FROM range(4)");
  await page().keyboard.press("Control+Enter");
  await waitPill("Succeeded");
  const head = page().locator(".sql-grid th").nth(1);
  const firstCell = () => page().locator(".sql-grid tbody tr").first().locator("td").nth(1).textContent();
  const aria = () => page().evaluate(() => document.querySelector(".sql-grid th:nth-child(2)")?.getAttribute("aria-sort"));
  await head.getByRole("button").click();
  await head.getByRole("button").click();
  await page().waitForFunction(() => document.querySelector(".sql-grid th:nth-child(2)")?.getAttribute("aria-sort") === "descending");
  assert.strictEqual(await firstCell(), "3", "statement 2 sorted, largest first");
  const sorted = app.calls.filter((c) => c.method === "sql_page").at(-1).params;
  assert.strictEqual(sorted.part, 2);
  assert.deepStrictEqual(sorted.sort, [{ column: 0, desc: true }]);
  await page().locator(".sql-rtab", { hasText: "1 SELECT" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".sql-grid tbody tr").length === 5);
  assert.notStrictEqual(await aria(), "descending", "statement 1 has a sort of its own");
  assert.strictEqual(await firstCell(), "0");
  await page().locator(".sql-rtab", { hasText: "2 SELECT" }).click();
  await page().waitForFunction(() => document.querySelector(".sql-grid th:nth-child(2)")?.getAttribute("aria-sort") === "descending");
  assert.strictEqual(await firstCell(), "3", "statement 2's sort came back with it");
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
