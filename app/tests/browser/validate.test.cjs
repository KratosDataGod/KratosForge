// Validate notebook (NB-017, NB-018): the panel's two tabs against the real engine. Problems shows
// the code checks, then a clean run in a fresh kernel; Clean up applies ticked tidy-ups and Undo
// puts the cells back. No lakehouse is attached, so nothing reaches OneLake.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "p1", source: "run_date = '2026-10-18'", meta: { tags: ["parameters"] } },
  { type: "code", cell_id: "c2", source: "import os\nimport json\ntotal = later + 1   \n\n\n\n", meta: {} },
  { type: "code", cell_id: "c3", source: "later = 41\nprint(json.dumps({'d': '2026-10-18'}))", meta: {} },
  { type: "code", cell_id: "c4", source: "", meta: {} },
];

let app;
const saved = [];
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    engine: ["notebook_validate_check", "notebook_validate_start", "notebook_validate_status", "notebook_validate_list",
      "notebook_validate_cancel", "notebook_cleanup_plan", "notebook_cleanup_apply"],
    mocks: { save_notebook: (p) => { saved.push(p); return { saved: true }; } },
  });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Daily sales", cells });
    state.ribbonTab = "run";
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

test("Problems shows the code checks, then a clean run that finds the cell defined later", { skip }, async () => {
  await page().getByRole("button", { name: /^Validate/ }).click();
  const pane = page().locator(".validatepane");
  await pane.getByText("Not ready: 1 error").waitFor();
  assert.match(await pane.innerText(), /Cell 2 uses a name defined later/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "validate-checks.png") });
  await pane.getByRole("button", { name: "Run clean check" }).click();
  await pane.getByText(/The clean run stopped at cell 2/).waitFor({ timeout: 60000 });
  const text = await pane.innerText();
  assert.match(text, /Found by the clean run and by reading the code/);
  assert.match(text, /Cell 2\s+Failed/);
  assert.match(text, /Cell 3\s+Didn't run/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "validate-clean-run.png") });
  await pane.getByRole("button", { name: "Go to cell 2" }).first().click();
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.indexOf(selectedCell()) + 1), 2);
});

test("Clean up applies the ticked tidy-ups, and Undo puts the cells back", { skip }, async () => {
  const pane = page().locator(".validatepane");
  await pane.getByRole("tab", { name: /Clean up/ }).click();
  await pane.getByText("Remove 1 unused import").waitFor();
  const text = await pane.innerText();
  assert.match(text, /- import os/);
  assert.match(text, /Cell 3 repeats the run_date value/);
  assert.strictEqual(await pane.getByRole("checkbox", { name: /Remove 1 print line/ }).isChecked(), false);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "validate-cleanup.png") });
  await pane.getByRole("button", { name: /^Apply 3 tidy-ups/ }).click();
  await page().waitForFunction(() => state.notebook.cells.length === 3);
  const sources = await page().evaluate(() => state.notebook.cells.map((c) => c.source));
  assert.deepStrictEqual(sources, ["run_date = '2026-10-18'", "import json\ntotal = later + 1", CELLS[2].source]);
  // The result is out of date now.
  await pane.getByRole("tab", { name: /Problems/ }).click();
  await pane.getByText(/Out of date/).waitFor();
  await pane.getByRole("tab", { name: /Clean up/ }).click();
  await pane.getByRole("button", { name: "Undo" }).click();
  await page().waitForFunction(() => state.notebook.cells.length === 4);
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => c.source)), CELLS.map((c) => c.source));
  assert.deepStrictEqual(app.errors, []);
});

test("Undo isn't offered after a later edit, so it can't lose that edit", { skip }, async () => {
  const pane = page().locator(".validatepane");
  await pane.getByRole("button", { name: /^Apply 3 tidy-ups/ }).click();
  await page().waitForFunction(() => state.notebook.cells.length === 3);
  const undo = pane.getByRole("button", { name: "Undo" });
  await undo.waitFor();
  await page().evaluate(() => { setSource(state.notebook.cells[1], "import json\ntotal = 2"); markDirty(); });
  await undo.click();  // still on screen from before the edit: it refuses
  await page().locator("#toast").getByText(/Undo would lose those edits/).waitFor();
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => c.source)),
    ["run_date = '2026-10-18'", "import json\ntotal = 2", CELLS[2].source]);
  assert.strictEqual(await undo.count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
