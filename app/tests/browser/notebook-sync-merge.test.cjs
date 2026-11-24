// A notebook saved in VS Code and edited here, against the real engine (Preview, WR-506): changes on one
// side merge on their own; a cell edited on both sides, deleted on one side and edited on the other, or
// cells moved on both, are each picked side by side, and only a pick for every one saves (vscode.js nbSyncMerge).
// axe covers dialogs: vscode.js:nbSyncMerge
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, axeFindings } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const ENGINE = ["list_notebooks", "open_notebook", "save_notebook", "notebook_sync_merge", "notebook_disk_stamp", "notebook_places",
  "compute_check", "kernel_variables", "notebook_versions", "notebook_version"];
const NAME = "Sales clean";
const CHOOSER = "This notebook changed in both places";
const OLD = "This notebook changed outside KratosForge";
const CELLS = ["# Load\ndf = load()", "# Parameters\nday = 1", "# Clean\ndf = df.dropna()", "# Join regions\ndf = join(df)", "# Summary chart\nplot(df)"];

let app;
const page = () => app.page;

function notebookFile() {
  const found = fs.readdirSync(app.engineHome, { recursive: true }).find((p) => path.basename(p) === `${NAME}.ipynb`
    && !p.split(path.sep).includes("notebook-versions"));
  return path.join(app.engineHome, found);
}
const onDisk = () => JSON.parse(fs.readFileSync(notebookFile(), "utf8")).cells.map((c) => [].concat(c.source).join(""));

// What VS Code does: rewrites the file.
function saveOutside(change) {
  const file = notebookFile();
  const nb = JSON.parse(fs.readFileSync(file, "utf8"));
  change(nb.cells);
  fs.writeFileSync(file, JSON.stringify(nb, null, 1));
}

// Each test starts from the five cells saved and open here, with preview features shown.
async function startClean(preview = true) {
  await page().evaluate(async ({ name, cells, preview }) => {
    closeDialog();
    state.settings = { ...(state.settings || {}), show_preview: { value: preview } };
    if (state.notebook) { state.notebook.dirty = false; state.notebook.diskConflict = false; }
    await rpc("save_notebook", { name, cells: cells.map((source) => ({ type: "code", source })) });
    await openNotebook(name);
  }, { name: NAME, cells: CELLS, preview });
  await page().waitForFunction(() => state.notebook && !state.notebook.dirty && !state.notebook.diskConflict);
}

// Edits here, then saves, as autosave would.
async function editHere(change) {
  await page().evaluate((change) => { new Function("cells", change)(state.notebook.cells); state.notebook.dirty = true; saveNotebook(); },
    change.toString().replace(/^\(?cells\)? => \{?|\}$/g, ""));
}

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, engine: ENGINE });
});

after(async () => { if (app) await app.close(); });

test("a change on each side, in different cells, merges and saves without asking", { skip }, async () => {
  await startClean();
  saveOutside((cells) => { cells[0].source = ["# Load\ndf = load(fast=True)"]; });
  await editHere("cells[4].source = '# Summary chart\\nplot(df, title=\"Sales\")';");
  await page().getByText(`Merged the changes saved in VS Code with yours in ${NAME}. Both versions are under Versions.`, { exact: true }).first().waitFor();
  assert.deepStrictEqual(onDisk(), ["# Load\ndf = load(fast=True)", CELLS[1], CELLS[2], CELLS[3], '# Summary chart\nplot(df, title="Sales")']);
  assert.ok(await page().evaluate(() => !state.notebook.dirty && !state.notebook.diskConflict));
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].source), "# Load\ndf = load(fast=True)");  // the screen shows the merge
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a cell edited on both sides is shown side by side, and Save waits for a pick", { skip }, async () => {
  await startClean();
  saveOutside((cells) => { cells[2].source = ["# Clean\ndf = df.dropna().drop_duplicates()"]; });
  await editHere("cells[2].source = '# Clean\\ndf = df.dropna(subset=[\"order_id\"])';");
  const dialog = page().getByRole("dialog", { name: CHOOSER });
  await dialog.waitFor();
  await dialog.getByText("Edited on both sides").waitFor();
  assert.ok(await dialog.getByRole("button", { name: "Save" }).isDisabled());
  await dialog.getByText("Pick 1 more to save.").waitFor();
  assert.ok(fs.readFileSync(notebookFile(), "utf8").includes("drop_duplicates"), "nothing saved over VS Code's edit yet");
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-sync-merge.png") });
  await dialog.getByRole("radio", { name: "Keep both" }).check();
  await dialog.getByRole("button", { name: "Save" }).click();
  await page().waitForFunction(() => !state.notebook.dirty && !state.notebook.diskConflict);
  const cells = JSON.parse(fs.readFileSync(notebookFile(), "utf8")).cells;
  assert.deepStrictEqual(cells.slice(2, 4).map((c) => [].concat(c.source).join("")),
    ['# Clean\ndf = df.dropna(subset=["order_id"])', "# Clean\ndf = df.dropna().drop_duplicates()"]);  // mine, then VS Code's
  assert.strictEqual(cells[3].metadata["application/vnd.databricks.v1+cell"]?.title, "From VS Code");
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a cell deleted in VS Code and edited here, and cells moved on both sides, each get a pick", { skip }, async () => {
  await startClean();
  saveOutside((cells) => { cells.splice(4, 1); cells.unshift(cells.splice(1, 1)[0]); });  // deleted Summary chart; Parameters to the top
  await editHere("cells[4].source = '# Summary chart\\nplot(df, x=\"region\")'; cells.splice(2, 0, cells.splice(3, 1)[0]);");
  const dialog = page().getByRole("dialog", { name: CHOOSER });
  await dialog.getByText("Deleted in VS Code, edited here").waitFor();
  await dialog.getByText("Reordered on both sides").waitFor();
  await dialog.getByText("This cell was deleted in VS Code.").waitFor();
  await dialog.getByText("Pick 2 more to save.").waitFor();
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  await dialog.getByRole("button", { name: "Use all VS Code's" }).click();
  await dialog.getByText("Every difference has a pick.").waitFor();
  await dialog.getByRole("button", { name: "Save" }).click();
  await page().waitForFunction(() => !state.notebook.dirty && !state.notebook.diskConflict);
  assert.deepStrictEqual(onDisk(), [CELLS[1], CELLS[0], CELLS[2], CELLS[3]]);  // VS Code's order, the chart deleted
  assert.deepStrictEqual(app.errors, []);
});

test("Cancel changes nothing: the edits stay on screen, unsaved", { skip }, async () => {
  await startClean();
  saveOutside((cells) => { cells[1].source = ["# Parameters\nday = 2"]; });
  await editHere("cells[1].source = '# Parameters\\nday = 3';");
  const dialog = page().getByRole("dialog", { name: CHOOSER });
  await dialog.waitFor();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.ok(await page().evaluate(() => state.notebook.dirty && state.notebook.diskConflict));
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[1].source), "# Parameters\nday = 3");
  assert.strictEqual(onDisk()[1], "# Parameters\nday = 2");
  const kinds = await page().evaluate(() => rpc("notebook_versions", { name: "Sales clean" }).then((l) => l.versions.map((v) => v.kind)));
  assert.ok(kinds.includes("changed outside") && kinds.includes("not saved"), kinds.join(","));
});

test("with preview features hidden, the whole-notebook question is asked as before", { skip }, async () => {
  await startClean(false);
  saveOutside((cells) => { cells[0].source = ["# Load\ndf = load(2)"]; });
  await editHere("cells[4].source = '# Summary chart\\nplot(df, 2)';");
  await page().getByRole("dialog", { name: OLD }).waitFor();
  assert.strictEqual(await page().getByRole("dialog", { name: CHOOSER }).count(), 0);
});
