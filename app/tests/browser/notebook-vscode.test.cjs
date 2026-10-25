// VS Code alongside the app, against the real engine: Open in VS Code saves the notebook and asks
// the shell to open its file (or folder), and coming back into focus shows edits saved there.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const ENGINE = ["list_notebooks", "open_notebook", "save_notebook", "notebook_places", "compute_check", "kernel_variables",
  "notebook_file_path", "notebook_disk_stamp"];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, engine: ENGINE });
  await page().evaluate(async () => {
    await rpc("save_notebook", { name: "Daily sales", cells: [{ type: "code", source: "orders = 1" }, { type: "code", source: "total = 2" }] });
    await openNotebook("Daily sales");
  });
});

after(async () => { if (app) await app.close(); });

test("Open in VS Code saves first, then opens the notebook's file", { skip }, async () => {
  await page().evaluate(() => { state.notebook.cells[0].source = "orders = 10"; markDirty(); });
  await page().getByRole("button", { name: "Open in VS Code" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_in_vscode"));
  const call = await page().evaluate(() => window.__fdShellCalls.find((c) => c.cmd === "open_in_vscode").args);
  assert.match(call.path, /Daily sales\.ipynb$/);
  assert.strictEqual(call.insiders, false);
  assert.ok(fs.readFileSync(call.path, "utf8").includes("orders = 10"), "saved before opening");
  await page().getByRole("button", { name: "More VS Code options" }).click();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-vscode-menu.png") });
  await page().getByRole("menuitem", { name: "Open the notebooks folder in VS Code" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.filter((c) => c.cmd === "open_in_vscode").length === 2);
  const folder = await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_in_vscode")[1].args.path);
  assert.strictEqual(folder, path.dirname(call.path));
  assert.deepStrictEqual(app.errors, []);
});

test("Coming back into focus shows edits saved in VS Code", { skip }, async () => {
  const file = await page().evaluate(() => rpc("notebook_file_path", { name: "Daily sales" }).then((w) => w.path));
  const nb = JSON.parse(fs.readFileSync(file, "utf8"));
  nb.cells[1].source = ["total = orders * 3  # from VS Code"];
  fs.writeFileSync(file, JSON.stringify(nb));
  await page().evaluate(() => window.dispatchEvent(new Event("focus")));
  await page().waitForFunction(() => state.notebook?.cells[1]?.source === "total = orders * 3  # from VS Code");
  assert.strictEqual(await page().evaluate(() => state.notebook.dirty), false);
  // With unsaved edits here, focus leaves them alone: the save asks instead (nbDiskConflict).
  await page().evaluate(() => { state.notebook.cells[0].source = "orders = 11"; state.notebook.dirty = true; });
  nb.cells[1].source = ["total = 0"];
  fs.writeFileSync(file, JSON.stringify(nb));
  await page().evaluate(() => window.dispatchEvent(new Event("focus")));
  await page().waitForTimeout(300);
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].source), "orders = 11");
  assert.deepStrictEqual(app.errors, []);
});
