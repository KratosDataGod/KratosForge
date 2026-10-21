// Version history (lifecycle L1) against the real engine: saves keep versions, a checkpoint, Compare
// shows the cells that changed, and Restore puts old cells back while keeping the current ones.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const ENGINE = ["list_notebooks", "open_notebook", "save_notebook", "notebook_places", "compute_check", "kernel_variables",
  "notebook_versions", "notebook_version", "notebook_version_compare", "notebook_checkpoint", "notebook_version_restore",
  "notebook_version_delete"];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, engine: ENGINE });
  await page().evaluate(async () => {
    await rpc("save_notebook", { name: "Daily sales", cells: [{ type: "code", source: "orders = 1" }, { type: "code", source: "total = orders * 2" }] });
    await openNotebook("Daily sales");
  });
});

after(async () => { if (app) await app.close(); });

test("Versions lists the saved version, and a checkpoint is kept by name", { skip }, async () => {
  await page().getByRole("button", { name: "Versions", exact: true }).click();
  const dialog = page().getByRole("dialog", { name: "Versions of Daily sales" });
  await dialog.getByText("Same as now").waitFor();
  await dialog.getByRole("button", { name: "Create checkpoint…" }).click();
  const ask = page().getByRole("dialog", { name: "Create a checkpoint" });
  await ask.getByRole("textbox").fill("Before the refactor");
  await ask.getByRole("button", { name: "Create" }).click();
  await page().getByRole("dialog", { name: "Versions of Daily sales" }).getByText("Before the refactor", { exact: true }).waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-versions-list.png") });
  await page().getByRole("dialog").getByRole("button", { name: "Close", exact: true }).last().click();
  assert.deepStrictEqual(app.errors, []);
});

test("Compare shows what changed since the checkpoint, and Restore brings it back", { skip }, async () => {
  await page().evaluate(() => { state.notebook.cells[1].source = "total = orders * 3"; markDirty(); });
  await page().getByRole("button", { name: "Versions", exact: true }).click();
  const dialog = page().getByRole("dialog", { name: "Versions of Daily sales" });
  await dialog.getByRole("button", { name: /^Compare with Before the refactor/ }).click();
  const cmp = page().getByRole("dialog", { name: /^Now compared with Before the refactor/ });
  await cmp.getByText("1 cell changed.").waitFor();
  assert.match(await cmp.innerText(), /\+total = orders \* 3/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-versions-compare.png") });
  await cmp.getByRole("button", { name: "Restore this version" }).click();
  await page().getByRole("dialog", { name: "Restore Before the refactor?" }).getByRole("button", { name: "Restore" }).click();
  await page().waitForFunction(() => state.notebook?.cells[1]?.source === "total = orders * 2");
  const kinds = await page().evaluate(() => rpc("notebook_versions", { name: "Daily sales" }).then((l) => l.versions.map((v) => v.kind)));
  assert.ok(kinds.includes("before restore") && kinds.includes("checkpoint"), kinds.join(","));
  assert.deepStrictEqual(app.errors, []);
});

test("Deleting a version asks first, in red", { skip }, async () => {
  await page().getByRole("button", { name: "Versions", exact: true }).click();
  await page().getByRole("dialog", { name: "Versions of Daily sales" }).getByRole("button", { name: /^Delete Before the refactor/ }).click();
  const confirm = page().getByRole("dialog", { name: 'Delete the checkpoint "Before the refactor"?' });
  assert.match(await confirm.getByRole("button", { name: "Delete" }).getAttribute("class"), /danger/);
  await confirm.getByRole("button", { name: "Delete" }).click();
  await page().getByRole("dialog", { name: "Versions of Daily sales" }).waitFor();
  const kinds = await page().evaluate(() => rpc("notebook_versions", { name: "Daily sales" }).then((l) => l.versions.map((v) => v.kind)));
  assert.ok(!kinds.includes("checkpoint"));
  assert.deepStrictEqual(app.errors, []);
});
