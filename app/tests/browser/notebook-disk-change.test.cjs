// A notebook changed on disk by another program (VS Code, a Git pull) while it's open here, against
// the real engine: the save doesn't write over it, and the person picks Keep mine or Load its version.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const ENGINE = ["list_notebooks", "open_notebook", "save_notebook", "notebook_places", "compute_check", "kernel_variables",
  "notebook_versions", "notebook_version"];
const TITLE = "This notebook changed outside KratosForge";

let app;
const page = () => app.page;

function notebookFile() {
  const found = fs.readdirSync(app.engineHome, { recursive: true }).find((p) => path.basename(p) === "Daily sales.ipynb"
    && !p.split(path.sep).includes("notebook-versions"));
  return path.join(app.engineHome, found);
}

// What VS Code does: rewrites the file with a different first cell.
function saveOutside(source) {
  const file = notebookFile();
  const nb = JSON.parse(fs.readFileSync(file, "utf8"));
  nb.cells[0].source = [source];
  fs.writeFileSync(file, JSON.stringify(nb));
}

async function editHere(source) {
  await page().evaluate((source) => { state.notebook.cells[0].source = source; state.notebook.dirty = true; return saveNotebook(); }, source);
}

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, engine: ENGINE });
  await page().evaluate(async () => {
    await rpc("save_notebook", { name: "Daily sales", cells: [{ type: "code", source: "orders = 1" }] });
    await openNotebook("Daily sales");
  });
});

after(async () => { if (app) await app.close(); });

test("Keep mine saves over the outside change, and both versions are kept", { skip }, async () => {
  saveOutside("orders = 2  # from VS Code");
  await editHere("orders = 3  # mine");
  const dialog = page().getByRole("dialog", { name: TITLE });
  await dialog.waitFor();
  assert.ok(fs.readFileSync(notebookFile(), "utf8").includes("from VS Code"), "nothing was saved over it");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-disk-change.png") });
  await dialog.getByRole("button", { name: "Keep mine" }).click();
  await page().waitForFunction(() => state.notebook && !state.notebook.dirty && !state.notebook.diskConflict);
  assert.ok(fs.readFileSync(notebookFile(), "utf8").includes("# mine"));
  const kinds = await page().evaluate(() => rpc("notebook_versions", { name: "Daily sales" }).then((l) => l.versions.map((v) => v.kind)));
  assert.ok(kinds.includes("changed outside") && kinds.includes("not saved"), kinds.join(","));
  assert.deepStrictEqual(app.errors, []);
});

test("Load its version can't replace the screen while a cell runs", { skip }, async () => {
  saveOutside("orders = 4  # from VS Code");
  await page().evaluate(() => { state.notebook.cells[0].status = "running"; });
  await editHere("orders = 5  # mine");
  const dialog = page().getByRole("dialog", { name: TITLE });
  await dialog.getByText("A cell is running. Stop it to load the other version.").waitFor();
  assert.ok(await dialog.getByRole("button", { name: "Load its version" }).isDisabled());
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page().evaluate(() => { state.notebook.cells[0].status = null; });
  assert.ok(await page().evaluate(() => state.notebook.dirty && state.notebook.diskConflict), "edits stay on screen, unsaved");
  assert.deepStrictEqual(app.errors, []);
});

test("Load its version shows the file as the other program saved it", { skip }, async () => {
  await editHere("orders = 6  # mine");  // still changed outside: asked again
  const dialog = page().getByRole("dialog", { name: TITLE });
  await dialog.getByRole("button", { name: "Load its version" }).click();
  await page().waitForFunction(() => state.notebook?.cells[0]?.source === "orders = 4  # from VS Code" && !state.notebook.dirty);
  // The edits that weren't saved are under Versions
  const sources = await page().evaluate(async () => {
    const list = await rpc("notebook_versions", { name: "Daily sales" });
    const unsaved = list.versions.filter((v) => v.kind === "not saved");
    return Promise.all(unsaved.map((v) => rpc("notebook_version", { name: "Daily sales", version_id: v.id }).then((r) => r.cells[0].source)));
  });
  assert.ok(sources.includes("orders = 6  # mine"), sources.join(" | "));
  await editHere("orders = 7");  // the next save goes through with the loaded file's stamp
  await page().waitForFunction(() => !state.notebook.dirty);
  assert.ok(fs.readFileSync(notebookFile(), "utf8").includes("orders = 7"));
  assert.deepStrictEqual(app.errors, []);
});
