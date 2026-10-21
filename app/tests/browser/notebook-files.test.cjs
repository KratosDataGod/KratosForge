// Notebook files (NB-001, NB-021) against the real engine: All notebooks and search inside them,
// Save a copy, Delete to Recently deleted and Restore, and Open a notebook file. The check before
// Publish runs against a mocked Fabric answer, since nothing here reaches a tenant.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const ENGINE = ["list_notebooks", "open_notebook", "save_notebook", "rename_notebook", "notebook_places", "notebook_listing",
  "notebook_search", "notebook_save_copy", "notebook_delete_check", "notebook_delete", "notebook_deleted", "notebook_restore",
  "notebook_import_check", "notebook_import", "pc_schedules_for", "compute_check", "kernel_variables"];

let app;
const published = [];
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    engine: ENGINE,
    mocks: {
      notebook_publish_check: () => ({ changed: true, known: true, item_id: "item-1", published_at: "2026-10-20T09:20:00Z",
        fabric: [{ kind: "changed", position: 2, diff: ["-b = 2", "+b = 3"] }, { kind: "added", position: 3, diff: ["+c = 4"] }],
        mine: [{ kind: "changed", position: 1, diff: [] }] }),
      publish_notebook: (p) => { published.push(p); return { name: p.name, workspace_id: "ws1", workspace_name: "Sales WS", id: "item-1" }; },
    },
  });
  await page().evaluate(async () => {
    const save = (name, ...sources) => rpc("save_notebook", { name, cells: sources.map((source) => ({ type: "code", source })) });
    await save("Daily sales", "orders = 1", "by_region = orders.join(region_map)", "region_map = 2");
    await save("Region map load", "lakehouse.write_table(df, 'dbo.region_map')");
    await save("Other", "print('nothing here')");
    go("notebooks");
  });
});

after(async () => { if (app) await app.close(); });

test("All notebooks lists every notebook, and search finds code inside cells and jumps to the cell", { skip }, async () => {
  await page().getByRole("heading", { name: "All notebooks" }).waitFor();
  await page().getByRole("button", { name: "Daily sales", exact: true }).waitFor();
  assert.strictEqual(await page().locator(".nbf-results tbody tr").count(), 3);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-files-list.png") });
  await page().getByRole("searchbox", { name: "Search notebook names and code" }).fill("region_map");
  await page().getByText("3 matches in 2 of 3 notebooks").waitFor();
  const text = await page().locator(".nbf-found").innerText();
  assert.match(text, /by_region = orders\.join\(region_map\)/);
  assert.doesNotMatch(text, /nothing here/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-files-search.png") });
  await page().getByRole("button", { name: "Go to cell 3" }).click();
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Daily sales");
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.indexOf(selectedCell()) + 1), 3);
  assert.deepStrictEqual(app.errors, []);
});

test("Save a copy keeps the original; Delete asks, keeps it in Recently deleted, and Restore puts it back", { skip }, async () => {
  await page().getByRole("button", { name: "Save a copy", exact: true }).click();
  const dialog = page().getByRole("dialog", { name: "Save a copy of Daily sales" });
  assert.strictEqual(await dialog.getByRole("textbox", { name: "Name" }).inputValue(), "Daily sales 2");
  await dialog.getByRole("button", { name: "Save copy" }).click();
  await page().waitForFunction(() => state.notebook?.name === "Daily sales 2");
  await page().evaluate(() => { nbFiles.query = ""; go("notebooks"); });
  await page().getByRole("button", { name: "More actions for Daily sales 2" }).click();
  await page().getByRole("menuitem", { name: "Delete…" }).click();
  const confirm = page().getByRole("dialog", { name: "Delete Daily sales 2?" });
  assert.match(await confirm.innerText(), /goes to Recently deleted on this PC\. You can restore it for 30 days/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-files-delete.png") });
  await confirm.getByRole("button", { name: "Delete" }).click();
  await page().getByRole("button", { name: "Restore Daily sales 2" }).waitFor();
  assert.strictEqual(await page().evaluate(() => state.notebook), null);  // it was open: it's closed
  const names = await page().evaluate(() => rpc("list_notebooks").then((l) => l.map((n) => n.name).sort()));
  assert.deepStrictEqual(names, ["Daily sales", "Other", "Region map load"]);
  await page().getByRole("button", { name: "Restore Daily sales 2" }).click();
  await page().getByRole("button", { name: "Daily sales 2", exact: true }).waitFor();
  assert.strictEqual(await page().evaluate(() => rpc("notebook_deleted").then((l) => l.length)), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Open a notebook file adds a copy and opens it", { skip }, async () => {
  const content = JSON.stringify({ nbformat: 4, nbformat_minor: 5, metadata: { kernelspec: { name: "python3" } },
    cells: [{ cell_type: "code", id: "a", metadata: {}, source: ["x = 1\n", "x + 1"], outputs: [{ output_type: "stream", text: "2" }] },
      { cell_type: "markdown", id: "b", metadata: {}, source: "# Notes" }] });
  const chooser = page().waitForEvent("filechooser");
  await page().getByRole("button", { name: "Open a notebook file…" }).click();
  await (await chooser).setFiles({ name: "finance_close.ipynb", mimeType: "application/json", buffer: Buffer.from(content) });
  const dialog = page().getByRole("dialog", { name: "Open a notebook file" });
  await dialog.getByText("2 cells: 1 code, 1 Markdown.", { exact: false }).waitFor();
  assert.strictEqual(await dialog.getByRole("textbox", { name: "Name" }).inputValue(), "finance_close");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-files-open.png") });
  await dialog.getByRole("button", { name: "Add and open" }).click();
  await page().waitForFunction(() => state.notebook?.name === "finance_close");
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => c.source)), ["x = 1\nx + 1", "# Notes"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Publish shows what changed in Fabric and publishes only after the person chooses", { skip }, async () => {
  await page().evaluate(() => { state.user = "chris@example.com"; state.notebook.workspace = { workspace_id: "ws1", workspace_name: "Sales WS" }; publishNotebook(); });
  const dialog = page().getByRole("dialog", { name: "finance_close changed in Fabric" });
  await dialog.getByText("Cell 2: changed").waitFor();
  assert.match(await dialog.innerText(), /\+b = 3/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-files-fabric-changed.png") });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(published.length, 0);
  await page().evaluate(() => { publishNotebook(); });
  await page().getByRole("dialog", { name: "finance_close changed in Fabric" }).getByRole("button", { name: "Replace with mine" }).click();
  await page().getByRole("dialog", { name: "Replace the changes in Fabric?" }).getByRole("button", { name: "Replace with mine" }).click();
  await page().waitForFunction(() => document.getElementById("toast").textContent.startsWith("Published"));
  assert.strictEqual(published.length, 1);
  assert.strictEqual(published[0].replace_fabric_changes, true);
  assert.deepStrictEqual(app.errors, []);
});
