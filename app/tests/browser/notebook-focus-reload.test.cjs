// Coming back to the window after another program (VS Code, a Git pull) saved the open notebook, against
// the real engine: a notebook with nothing unsaved shows the file as it is now; one with unsaved edits,
// a running cell or an open conflict is never replaced, so nothing typed here is lost (nbReloadIfChangedOutside).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const ENGINE = ["list_notebooks", "open_notebook", "save_notebook", "notebook_disk_stamp", "notebook_places", "compute_check",
  "kernel_variables", "notebook_versions", "notebook_version"];
const NAME = "Weekly orders";
const CELLS = 1000;

let app;
const page = () => app.page;

function notebookFile() {
  const found = fs.readdirSync(app.engineHome, { recursive: true }).find((p) => path.basename(p) === `${NAME}.ipynb`
    && !p.split(path.sep).includes("notebook-versions"));
  return path.join(app.engineHome, found);
}

// What VS Code does: rewrites the file, changing what `change` changes.
function saveOutside(change) {
  const file = notebookFile();
  const nb = JSON.parse(fs.readFileSync(file, "utf8"));
  change(nb);
  fs.writeFileSync(file, JSON.stringify(nb, null, 1));
}

const firstSource = () => page().evaluate(() => state.notebook.cells[0].source);
const backToTheWindow = () => page().evaluate(() => nbReloadIfChangedOutside());

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, engine: ENGINE });
  await page().evaluate(async ({ name, n }) => {
    const cells = Array.from({ length: n }, (_, i) => ({ type: i % 4 ? "code" : "markdown", source: `# cell ${i}\n` + "x = 1\n".repeat(40) }));
    await rpc("save_notebook", { name, cells });
    await openNotebook(name);
  }, { name: NAME, n: CELLS });
});

after(async () => { if (app) await app.close(); });

test("with nothing unsaved, coming back shows the large notebook as VS Code saved it", { skip }, async () => {
  saveOutside((nb) => { nb.cells[0].source = ["# from VS Code"]; nb.cells.splice(500, 1); });
  await page().evaluate(() => window.dispatchEvent(new Event("focus")));
  await page().waitForFunction(() => state.notebook.cells[0].source === "# from VS Code");
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.length), CELLS - 1);
  await page().getByText(`${NAME} changed outside KratosForge, for example in VS Code. Showing the file as it is now.`).waitFor();
  assert.ok(await page().evaluate(() => !state.notebook.dirty && !state.notebook.diskConflict));
  assert.deepStrictEqual(app.errors, []);
});

test("outputs VS Code saved are no change: coming back doesn't reload", { skip }, async () => {
  const before = await page().evaluate(() => state.notebook.diskStamp);
  saveOutside((nb) => { nb.cells[1].outputs = [{ output_type: "stream", name: "stdout", text: ["ran in VS Code\n"] }]; });
  const calls = app.calls.length;
  await backToTheWindow();
  assert.strictEqual(await page().evaluate(() => state.notebook.diskStamp), before);
  const asked = app.calls.slice(calls).map((c) => c.method);
  assert.ok(asked.includes("notebook_disk_stamp") && !asked.includes("open_notebook"), asked.join(","));
});

test("unsaved edits are never replaced by coming back, and the save then asks", { skip }, async () => {
  await page().evaluate(() => { state.notebook.cells[0].source = "# typed here, not saved yet"; state.notebook.dirty = true; });
  saveOutside((nb) => { nb.cells[0].source = ["# from VS Code again"]; });
  await backToTheWindow();
  assert.strictEqual(await firstSource(), "# typed here, not saved yet");
  await page().evaluate(() => saveNotebook());
  const dialog = page().getByRole("dialog", { name: "This notebook changed outside KratosForge" });
  await dialog.waitFor();
  await backToTheWindow();  // with the question open, coming back changes nothing either
  assert.strictEqual(await firstSource(), "# typed here, not saved yet");
  await dialog.getByRole("button", { name: "Keep mine" }).click();
  await page().waitForFunction(() => !state.notebook.dirty && !state.notebook.diskConflict);
  assert.ok(fs.readFileSync(notebookFile(), "utf8").includes("typed here, not saved yet"));
  assert.deepStrictEqual(app.errors, []);
});

test("a running cell's notebook isn't replaced by coming back", { skip }, async () => {
  saveOutside((nb) => { nb.cells[0].source = ["# from VS Code while a cell ran"]; });
  await page().evaluate(() => { state.notebook.cells[2].status = "running"; });
  await backToTheWindow();
  assert.strictEqual(await firstSource(), "# typed here, not saved yet");
  await page().evaluate(() => { state.notebook.cells[2].status = null; });
  await backToTheWindow();  // once it has finished, it does
  await page().waitForFunction(() => state.notebook.cells[0].source === "# from VS Code while a cell ran");
  assert.deepStrictEqual(app.errors, []);
});
