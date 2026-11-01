// Run stale cells (deps.js, #1295), against the real engine: notebook_dependencies reads the graph and
// the kernel runs the cells. With the View tab's Dependencies switch on, each code cell says what it
// uses, and editing a cell after a run marks the cells below it Stale. Run stale cells runs them in
// order with no dialog when none of them changes anything outside the notebook; when one writes a
// file or table, the dialog names it, "Run ... only" runs just the cells before it and Cancel runs
// nothing. With nothing stale the toast says so. axe finds nothing serious in the dialog.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const ENGINE = ["start_cell", "poll_cell", "kernel_variables", "kernel_complete", "interrupt_cell", "notebook_dependencies"];

// No side effects: 1 -> 2 -> 3; cell 4 stands alone.
const PLAIN = [
  { type: "code", cell_id: "p1", source: "base = 1", meta: {} },
  { type: "code", cell_id: "p2", source: "total = base + 1", meta: {} },
  { type: "code", cell_id: "p3", source: "report = total * 2\nprint(report)", meta: {} },
  { type: "code", cell_id: "p4", source: "other = 5", meta: {} },
];

// Cell 3 writes a file and cell 5 a DuckDB table (in memory); both use cell 2. Only local writes.
let outFile;
const effectCells = () => [
  { type: "code", cell_id: "e1", source: "base = 1", meta: {} },
  { type: "code", cell_id: "e2", source: "total = base + 1", meta: {} },
  { type: "code", cell_id: "e3", source: `open(${JSON.stringify(outFile)}, "w").write(str(total))`, meta: {} },
  { type: "code", cell_id: "e4", source: "done = total + 1\nprint(done)", meta: {} },
  { type: "code", cell_id: "e5", source: "import duckdb\nduckdb.sql(\"CREATE OR REPLACE TABLE daily AS SELECT 1 AS n\")\nprint(total)", meta: {} },
];

let app;
const page = () => app.page;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// The code of each cell the kernel was asked to run since `from`.
const ranSince = (from) => app.calls.slice(from).filter((c) => c.method === "start_cell").map((c) => c.params.code);
const codeOf = (cells, ids) => ids.map((id) => cells.find((c) => c.cell_id === id).source);

// Opens a notebook and turns on Dependencies from the View tab, then goes to the Run tab.
async function openNotebook(name, cells) {
  await page().evaluate(({ name, cells }) => {
    loadIntoState({ name, cells });
    state.showDeps = false;
    state.ribbonTab = "view";
    go("notebook");
  }, { name, cells });
  const toggle = page().getByRole("button", { name: /^Dependencies/ });
  await toggle.click();
  await page().waitForFunction(() => state.showDeps && [...document.querySelectorAll("button[aria-pressed='true']")]
    .some((b) => b.textContent.startsWith("Dependencies")));
  await page().getByRole("tab", { name: "Run" }).click();
  await page().getByRole("button", { name: /^Run stale cells/ }).waitFor();
}

// Runs every cell and waits until each one has run.
async function runAllCells(n) {
  await page().evaluate(() => runAll());
  await page().waitForFunction((n) => state.kernel !== "busy"
    && state.notebook.cells.filter((c) => c.ran).length === n, n, { timeout: 60000 });
}

// Each code cell's Stale tag ("" when it has none), once the count and the tags match `want`.
async function waitForStale(want) {
  const got = await page().waitForFunction((want) => {
    const tags = [...document.querySelectorAll(".cell")].map((el) => el.querySelector(".deps-stale")?.textContent || "");
    return tags.length === want.length && tags.every((t, i) => t === want[i]) && tags;
  }, want, { timeout: 15000 }).catch(() => null);
  const tags = await page().evaluate(() => [...document.querySelectorAll(".cell")].map((el) => el.querySelector(".deps-stale")?.textContent || ""));
  assert.deepStrictEqual(tags, want, got ? "" : "the Stale tags never matched");
}

// Types new code into a cell's editor, as a person would.
async function editCell(index, code) {
  const editor = page().locator(".cell .cm-content").nth(index);
  await editor.click();
  await page().keyboard.press("ControlOrMeta+a");
  await page().keyboard.type(code);
  await page().keyboard.press("Escape");
  await page().waitForFunction(({ index, code }) => state.notebook.cells[index].source === code, { index, code });
}

// Waits until the run started by a click is over: `n` cells asked for and the kernel idle again.
async function waitForRun(from, n) {
  for (const end = Date.now() + 60000; ranSince(from).length < n; await sleep(25)) {
    if (Date.now() > end) throw new Error(`only ${ranSince(from).length} of ${n} cells ran`);
  }
  await page().waitForFunction(() => state.kernel !== "busy", null, { timeout: 60000 });
}

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE });
  outFile = path.join(app.engineHome, "stale-cells-out.txt");
});

after(async () => { if (app) await app.close(); });

test("with Dependencies on, each cell says what it uses; editing one after a run marks the cells below it Stale", { skip }, async () => {
  await openNotebook("Stale plain", PLAIN);
  // What each cell uses, read from its code.
  const strips = await page().waitForFunction(() => {
    const s = [...document.querySelectorAll(".cell .deps-strip")].map((el) => el.innerText.replace(/\s+/g, " ").trim());
    return s.length === 4 && s[1].includes("Uses base from 1") && s;
  }, null, { timeout: 15000 }).then((h) => h.jsonValue());
  assert.match(strips[0], /Defines base/);
  assert.match(strips[0], /Used by 2/);
  assert.match(strips[1], /Uses base from 1/);
  assert.match(strips[2], /Uses total from 2/);
  assert.match(strips[3], /Defines other/);
  assert.doesNotMatch(strips[3], /Uses/);
  await waitForStale(["", "", "", ""]);  // nothing has run yet, so nothing is stale

  await runAllCells(4);
  await waitForStale(["", "", "", ""]);
  await editCell(0, "base = 10");
  await waitForStale(["Stale: code changed", "Stale: cell 1 is out of date", "Stale: cell 2 is out of date", ""]);
  await page().waitForFunction(() => /^Run stale cells \(3\)/.test(document.querySelector("button[data-deps-run]")?.textContent || ""));
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "stale-cells-tags.png") });
  assert.deepStrictEqual(app.errors, []);
});

test("with no side effects, Run stale cells runs the stale cells in order with no dialog", { skip }, async () => {
  // Goes on from the test above: cells 1-3 are stale, cell 4 isn't.
  const from = app.calls.length;
  await page().getByRole("button", { name: /^Run stale cells/ }).click();
  await waitForRun(from, 3);
  assert.strictEqual(await page().locator("#dialog-root .dialog").count(), 0, "no dialog");
  assert.deepStrictEqual(ranSince(from), ["base = 10", "total = base + 1", "report = total * 2\nprint(report)"]);
  // The kernel saw the new value: (10 + 1) * 2.
  await page().waitForFunction(() => document.querySelectorAll(".cell")[2].querySelector(".output")?.innerText.trim() === "22");
  await waitForStale(["", "", "", ""]);
  assert.deepStrictEqual(app.errors, []);
});

test("when nothing is stale, the toast says so and nothing runs", { skip }, async () => {
  // Nothing has run in a fresh notebook; cells that haven't run aren't stale either.
  await openNotebook("Stale none", PLAIN.map((c) => ({ ...c })));
  for (const step of ["before a run", "after a run"]) {
    if (step === "after a run") await runAllCells(4);
    await page().evaluate(() => { document.getElementById("toast").hidden = true; });
    const from = app.calls.length;
    await page().getByRole("button", { name: /^Run stale cells/ }).click();
    await page().waitForFunction(() => {
      const t = document.getElementById("toast");
      return !t.hidden && t.textContent.startsWith("No outputs are out of date.");
    });
    assert.match(await page().locator("#toast").innerText(), /Cells that haven't run yet aren't counted; use Run all for those\./, step);
    await sleep(300);
    assert.deepStrictEqual(ranSince(from), [], `nothing runs ${step}`);
    assert.strictEqual(await page().locator("#dialog-root .dialog").count(), 0);
  }
  assert.deepStrictEqual(app.errors, []);
});

test("a stale cell that writes a file or table: the dialog names it; Cancel runs nothing, \"Run 1 and 2 only\" runs the cells before it", { skip }, async () => {
  const cells = effectCells();
  await openNotebook("Stale effects", cells);
  await runAllCells(5);
  assert.strictEqual(fs.readFileSync(outFile, "utf8"), "2", "cell 3 wrote the file");
  await page().waitForFunction(() => document.querySelectorAll(".cell")[4].querySelector(".deps-fx")?.textContent === "Writes a table");
  await editCell(0, "base = 10");
  await waitForStale(["Stale: code changed", "Stale: cell 1 is out of date", "Stale: cell 2 is out of date",
    "Stale: cell 2 is out of date", "Stale: cell 2 is out of date"]);

  // Cancel: the dialog names the cells that write, and nothing runs.
  let from = app.calls.length;
  await page().getByRole("button", { name: /^Run stale cells/ }).click();
  const dialog = page().locator("#dialog-root .dialog");
  await dialog.waitFor();
  const text = (await dialog.innerText()).replace(/\s+/g, " ");
  assert.match(text, /Run 5 stale cells\?/);
  assert.match(text, /Cells 1, 2, 3, 4 and 5 are out of date\. Running them again also does this:/);
  assert.deepStrictEqual(await dialog.locator(".deps-fx-list li").allInnerTexts(),
    [`Cell 3 writes the file ${outFile}.`, "Cell 5 writes the table dbo.daily."]);
  assert.deepStrictEqual(await dialog.locator(".dialog-actions button").allInnerTexts(), ["Run 1, 2, 3, 4 and 5", "Run 1 and 2 only", "Cancel"]);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "stale-cells-dialog.png") });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await sleep(500);
  assert.deepStrictEqual(ranSince(from), [], "Cancel runs nothing");
  assert.strictEqual(await page().evaluate(() => state.kernel), "idle");

  // "Run 1 and 2 only": cells 1 and 2 run; cell 3 doesn't write and the cells from 3 on stay stale.
  from = app.calls.length;
  await page().getByRole("button", { name: /^Run stale cells/ }).click();
  await dialog.waitFor();
  await dialog.getByRole("button", { name: "Run 1 and 2 only" }).click();
  await waitForRun(from, 2);
  await sleep(300);
  assert.deepStrictEqual(ranSince(from), codeOf(cells, ["e1", "e2"]).map((s, i) => (i === 0 ? "base = 10" : s)));
  assert.strictEqual(fs.readFileSync(outFile, "utf8"), "2", "the file wasn't written again");
  await waitForStale(["", "", "Stale: cell 2 ran again", "Stale: cell 2 ran again", "Stale: cell 2 ran again"]);
  assert.deepStrictEqual(app.errors, []);
});

test("the Run stale cells dialog has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  // Goes on from the test above: cells 3-5 are stale and cell 3 writes a file.
  await page().getByRole("button", { name: /^Run stale cells/ }).click();
  const dialog = page().locator("#dialog-root .dialog");
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /Run 3 stale cells\?/);
  const found = await axeFindings(page(), "#dialog-root .dialog");
  assert.deepStrictEqual(found, []);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
});
