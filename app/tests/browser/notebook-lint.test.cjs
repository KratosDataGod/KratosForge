// Lint underlines in Python cells (nbintel.js, nblint.py; parity N09) against the real engine: a name no
// cell defines and a best-practice finding get amber (warning) underlines at their exact code, with the
// reason on hover, while names from other cells, the kernel's spark and %%sql cells get none; and nothing
// is checked or drawn while Present covers the notebook.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Lint", meta: {} },
  { type: "code", cell_id: "c1", source: "import pandas as pd\nx = 1", meta: {} },
  { type: "code", cell_id: "c2", source: "print(x, pd, spark, display)\nprint(totl)\nrows = spark.range(3).collect()", meta: {} },
  { type: "code", cell_id: "c3", source: "%%sql\nSELECT nothing_defined FROM t", meta: {} },
];

let app;
const page = () => app.page;
const cell = (i) => page().locator(".cell").nth(i);

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["notebook_syntax", "notebook_print_page"],
    mocks: { save_notebook: () => ({ saved: true }), notebook_dependencies: () => ({ cells: [] }),
      governance_practice_settings: () => ({ settings: {}, policies: [] }) },
  });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Lint", cells });
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

const marks = (i) => page().evaluate((i) => [...document.querySelectorAll(".cell")[i].querySelectorAll(".cm-lintRange")]
  .map((el) => [el.className.match(/cm-lintRange-(\w+)/)[1], el.textContent]), i);

test("a name no cell defines and a best-practice finding are amber underlines at their code", { skip }, async () => {
  await cell(2).locator(".cm-lintRange-warning").first().waitFor({ timeout: 8000 });
  await page().waitForFunction(() => document.querySelectorAll(".cell")[2].querySelectorAll(".cm-lintRange-warning").length >= 2, null, { timeout: 5000 });
  assert.deepStrictEqual(await marks(2), [["warning", "totl"], ["warning", "spark.range(3).collect()"]]);
  assert.deepStrictEqual(await marks(1), [], "the cell that defines x and pd has nothing");
  assert.deepStrictEqual(await marks(3), [], "a %%sql cell isn't read as Python");
  // The reason, in plain words, on hover.
  const box = await cell(2).locator(".cm-lintRange-warning", { hasText: "totl" }).boundingBox();
  await page().mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  const tip = page().locator(".cm-tooltip-lint");
  await tip.waitFor({ timeout: 5000 });
  assert.match(await tip.innerText(), /totl isn't defined: no cell of this notebook sets it/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-lint.png") });
  await page().mouse.move(0, 0);
  // The engine was given every code cell and which one this is.
  const call = app.calls.filter((c) => c.method === "notebook_syntax").find((c) => c.params.cell === 1);
  assert.strictEqual(call.params.cells.length, 3);
});

test("defining the name clears its underline, and a syntax error is still red", { skip }, async () => {
  await cell(2).locator(".cm-content").click();
  await page().keyboard.press("Control+a");
  await page().keyboard.type("totl = 2\nprint(totl, x)");
  await page().waitForFunction(() => !document.querySelectorAll(".cell")[2].querySelector(".cm-lintRange"), null, { timeout: 5000 });
  await page().keyboard.press("Control+a");
  await page().keyboard.type("totl = = 1");
  await cell(2).locator(".cm-lintRange-error").first().waitFor({ timeout: 5000 });
  assert.strictEqual(await cell(2).locator(".cm-lintRange-warning").count(), 0);
  await page().keyboard.press("Control+a");
  await page().keyboard.type("print(nope)");
  await cell(2).locator(".cm-lintRange-warning").first().waitFor({ timeout: 5000 });
  assert.deepStrictEqual(app.errors, []);
});

test("nothing is checked or drawn while Present covers the notebook", { skip }, async () => {
  await page().evaluate(() => openNotebookPresent());  // View > Present (nbpresent.js)
  const present = page().getByRole("dialog", { name: /presenting$/ });
  await present.waitFor();
  const before = app.calls.filter((c) => c.method === "notebook_syntax").length;
  await page().evaluate(() => state.notebook.cells.forEach((c) => { const v = editors.get(c.id); if (v) CM.forceLinting(v); }));
  await page().waitForTimeout(1200);
  assert.strictEqual(app.calls.filter((c) => c.method === "notebook_syntax").length, before, "no lint call while presenting");
  assert.strictEqual(await present.locator(".cm-lintRange, .cm-tooltip-lint").count(), 0);
  await page().keyboard.press("Escape");
  await present.waitFor({ state: "detached" });
  assert.ok(await cell(2).locator(".cm-lintRange-warning").count() >= 1, "the marks are back after Present");
  assert.deepStrictEqual(app.errors, []);
});
