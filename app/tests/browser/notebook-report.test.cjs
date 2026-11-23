// Notebook > Share > Open as report (nbreport.js, parity item 18, Preview): the main window sends the
// notebook to the real engine and opens the report's window; the window shows the Markdown and outputs
// without the code, says when the notebook changed after its run, and exports the same page.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const REPORT = ["notebook_report_open", "notebook_report_page", "notebook_report_export"];
let main, win;
const NB = { name: "Weekly sales review", cells: [
  { type: "markdown", cell_id: "m1", source: "Revenue by region since 1 October.", meta: {} },
  { type: "code", cell_id: "c1", source: "secret_code = spark.read.table('gold.fact_sales')", meta: {} },
  { type: "code", cell_id: "c2", source: "show(df)", meta: {} }] };

before(async () => {
  if (skip) return;
  [main, win] = await Promise.all([
    openApp({ engine: REPORT, mocks: { save_notebook: () => ({ saved: true }), notebook_syntax: () => [] } }),
    openApp({ engine: REPORT, search: "?popout=report", viewport: { width: 920, height: 640 } }),
  ]);
});
after(async () => { await Promise.all([main, win].map((a) => a?.close())); });

test("Share > Open as report sends the notebook without running anything and opens the report's window", { skip }, async () => {
  const p = main.page;
  await p.evaluate((nb) => {
    loadIntoState(nb);
    const [, c1, c2] = state.notebook.cells;
    c2.outputs = [{ type: "table", columns: ["region", "revenue"], rows: [["West", 1204330.5], ["North", 998120]] }];
    c2.count = 2;
    c2.finishedAtMs = Date.UTC(2026, 10, 14, 10, 42);
    c1.ran = { source: c1.source };
    go("notebook");
  }, NB);
  await p.locator(".cell .cm-content").first().waitFor();
  await p.getByRole("button", { name: "Share" }).click();
  await p.getByRole("menuitem", { name: /Open as report/ }).click();
  await p.waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "pop_out_open" && c.args.section === "report"));
  const sent = main.calls.find((c) => c.method === "notebook_report_open").params;
  assert.strictEqual(sent.stale, false);
  assert.match(sent.ran, /^Outputs from the run at .*, on this PC\.$/);
  assert.ok(!main.calls.some((c) => /^(start_cell|run_cell|sql_start)$/.test(c.method)), "nothing runs");
  const page = await p.evaluate(() => rpc("notebook_report_page"));
  assert.ok(!page.html.includes("secret_code") && page.html.includes("Revenue by region") && page.html.includes("<td>West</td>"));

  // An edit after the run: the report says it's out of date.
  await p.evaluate(() => { state.notebook.cells[1].source = "secret_code = 2"; });
  await p.getByRole("button", { name: "Share" }).click();
  await p.getByRole("menuitem", { name: /Open as report/ }).click();
  await p.waitForFunction(() => window.__fdShellCalls.filter((c) => c.cmd === "pop_out_open").length === 2);
  assert.strictEqual(main.calls.filter((c) => c.method === "notebook_report_open").at(-1).params.stale, true);
});

test("the report's window shows the outputs without the code, then the out-of-date note, and exports the page", { skip }, async () => {
  const p = win.page;
  await p.getByText("There's no report to show.", { exact: false }).waitFor();
  const sent = main.calls.filter((c) => c.method === "notebook_report_open");
  // The two windows share one engine in the app; here each has its own, so the report is sent to this one.
  await p.evaluate((params) => rpc("notebook_report_open", params), sent[0].params);
  await p.evaluate(() => dispatchEvent(new Event("focus")));
  const report = p.getByRole("article", { name: "Weekly sales review, report" });
  await report.getByText("Revenue by region since 1 October.").waitFor();
  assert.strictEqual(await report.getByRole("cell", { name: "West" }).count(), 1);
  assert.strictEqual(await report.getByText("secret_code").count(), 0);
  assert.match(await report.textContent(), /Outputs from the run at .*, on this PC\. Code is hidden\. 3 cells, 1 of them Markdown\./);
  assert.strictEqual(await p.title(), "Weekly sales review · report");
  if (axeSource) assert.deepStrictEqual(await axeFindings(p, ".nb-report"), []);

  await p.evaluate((params) => rpc("notebook_report_open", params), sent[1].params);
  await p.evaluate(() => dispatchEvent(new Event("focus")));
  await p.getByText("The notebook changed after this run.").waitFor();

  await p.getByRole("button", { name: "Export as HTML" }).click();
  const saved = (await p.locator("#toast").filter({ hasText: /Saved to .*\.html/ }).textContent()).match(/Saved to (.*\.html)/)[1];
  assert.match(saved, /Weekly sales review report\.html$/);
  assert.strictEqual(fs.readFileSync(saved, "utf8"), (await p.evaluate(() => rpc("notebook_report_page"))).html);

  await p.getByRole("button", { name: "Open the notebook" }).click();
  await p.waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "pop_out_close" && c.args.section === "report"));
  assert.strictEqual(await p.getByRole("button", { name: "Dock back" }).count(), 0, "a report has nothing to dock back");
});
